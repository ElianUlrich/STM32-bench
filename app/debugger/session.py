"""Sesión de depuración en la web: la app maneja GDB (modo MI) en la placa.

    navegador ──HTTP/SSE──► DebugSession ──stdin/stdout (MI)──► GDB ──TCP──► OpenOCD :3333 ──SWD──► STM32

Seguridad: la web nunca le manda texto libre a GDB. Cada acción arma un
comando MI concreto (paso, breakpoint en archivo:línea de la lista del .elf,
leer o cambiar una variable). Las expresiones se validan: sin caracteres de
control y sin variables de conveniencia de GDB ($...) salvo los registros.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..chips import IdCode
from ..elfinfo import DebugInfo, DebugInfoError
from ..events import EventBus
from .mi import MiParseError, MiRecord, parse_line, quote

MAX_EXPR = 200
MAX_WATCHES = 20
MAX_GLOBALS = 40
MAX_VALUE_CHARS = 400
REGISTERS = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12",
             "sp", "lr", "pc", "xpsr", "msp", "psp", "primask", "basepri", "faultmask", "control"]
_REG_NAMES = set(REGISTERS) | {"fp", "ip", "cpsr"}
_DOLLAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?")
LVALUE_RE = re.compile(r"^[A-Za-z_]\w*(\s*(\.|->)\s*[A-Za-z_]\w*|\s*\[\s*\d{1,6}\s*\])*$")
VALUE_RE = re.compile(r"^(-?(0[xX][0-9A-Fa-f]{1,16}|0[bB][01]{1,64}|\d{1,20}(\.\d+)?([eE][-+]?\d{1,3})?)[uUlLfF]{0,3}"
                      r"|'(\\.|[^'\\])'|true|false)$")
CONTROL_ACTIONS = ("continue", "pause", "next", "step", "finish", "reset")
STOP_REASONS = {
    "breakpoint-hit": "breakpoint", "end-stepping-range": "paso", "function-finished": "fin de función",
    "signal-received": "pausa", "location-reached": "llegó", "watchpoint-trigger": "watchpoint",
    "exited": "el programa terminó", "exited-normally": "el programa terminó",
}


class DebugError(RuntimeError):
    """Error de depuración con mensaje para el usuario."""


def check_expression(expr: str) -> str:
    expr = (expr or "").strip()
    if not expr:
        raise DebugError("Expresión vacía")
    if len(expr) > MAX_EXPR:
        raise DebugError(f"Expresión demasiado larga (máx. {MAX_EXPR} caracteres)")
    if any(ord(c) < 32 or ord(c) == 127 for c in expr):
        raise DebugError("La expresión tiene caracteres de control")
    for m in _DOLLAR_RE.finditer(expr):
        if (m.group(1) or "").lower() not in _REG_NAMES:
            raise DebugError("En las expresiones sólo se permiten registros con $ ($pc, $sp, $r0…)")
    return expr


def check_assignment(name: str, value: str) -> str:
    name, value = (name or "").strip(), (value or "").strip()
    if not LVALUE_RE.match(name) or len(name) > MAX_EXPR:
        raise DebugError(f"'{name}' no es una variable que se pueda modificar")
    if not VALUE_RE.match(value):
        raise DebugError("Valor no válido: usá un número (decimal, 0x.., 0b..), un carácter 'a' o true/false")
    return f"{name}={value}"


def _short(text: Any) -> str:
    text = str(text)
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "…"


class GdbMiProcess:
    """Proceso GDB con --interpreter=mi2: comandos numerados y registros asíncronos."""

    def __init__(self, argv: Sequence[str], env_extra: Dict[str, str],
                 on_record: Callable[[MiRecord], None], on_exit: Callable[[Optional[int], List[str]], None]) -> None:
        self.argv = [str(a) for a in argv]
        self.env_extra = env_extra
        self.on_record = on_record
        self.on_exit = on_exit
        self._proc: Optional[subprocess.Popen] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._token = 0
        self._tail: List[str] = []

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        env = os.environ.copy()
        env.update(self.env_extra)
        self._proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, env=env, shell=False, text=True,
                                      encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read_loop, name="gdb-mi-reader", daemon=True).start()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None and self._loop is not None
        loop = self._loop
        for raw in self._proc.stdout:
            line = raw.rstrip("\r\n")
            self._tail = (self._tail + [line])[-20:]
            try:
                rec = parse_line(line)
            except MiParseError:
                rec = MiRecord(kind="console", text=line + "\n")
            if rec is None or rec.kind == "prompt":
                continue
            try:
                loop.call_soon_threadsafe(self._dispatch, rec)
            except RuntimeError:
                return
        code = self._proc.wait()
        try:
            loop.call_soon_threadsafe(self._finish, code)
        except RuntimeError:
            pass

    def _dispatch(self, rec: MiRecord) -> None:
        if rec.kind == "result" and rec.token is not None and rec.token in self._pending:
            fut = self._pending.pop(rec.token)
            if not fut.done():
                fut.set_result(rec)
            return
        self.on_record(rec)

    def _finish(self, code: Optional[int]) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(DebugError("GDB terminó"))
        self._pending.clear()
        self.on_exit(code, self._tail)

    async def send(self, command: str, timeout: float = 10.0) -> MiRecord:
        if not self.running or self._proc is None or self._proc.stdin is None:
            raise DebugError("GDB no está corriendo")
        self._token += 1
        token = self._token
        fut = asyncio.get_running_loop().create_future()
        self._pending[token] = fut
        try:
            self._proc.stdin.write(f"{token}{command}\n")
            self._proc.stdin.flush()
        except (OSError, ValueError):
            self._pending.pop(token, None)
            raise DebugError("No se pudo hablar con GDB (terminó)")
        try:
            rec = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(token, None)
            raise DebugError(f"GDB no respondió a tiempo ({command.split()[0]})")
        if rec.cls == "error":
            raise DebugError(str(rec.results.get("msg", "error de GDB")))
        return rec

    def terminate(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()


class DebugSession:
    def __init__(self, bus: EventBus, env_extra: Optional[Dict[str, str]] = None) -> None:
        self.bus = bus
        self.env_extra = env_extra or {}
        self._proc: Optional[GdbMiProcess] = None
        self._lock = asyncio.Lock()
        self._stopped_evt: Optional[asyncio.Event] = None
        self._quiet = 0
        self._stopping = False
        self._reset_state()
        self.watches: List[str] = []

    def _reset_state(self) -> None:
        self.state = "off"                    # off | starting | stopped | running | error
        self.message: Optional[str] = None
        self.info: Dict[str, Any] = {}
        self.debuginfo: Optional[DebugInfo] = None
        self.frame: Optional[Dict[str, Any]] = None
        self.stack: List[Dict[str, Any]] = []
        self.locals: List[Dict[str, Any]] = []
        self.globals: List[Dict[str, Any]] = []
        self.watch_values: List[Dict[str, Any]] = []
        self.registers: List[Dict[str, Any]] = []
        self.breakpoints: Dict[int, Dict[str, Any]] = {}
        self.stop_reason: Optional[str] = None
        self._target_running = False          # estado real del chip (state se actualiza al leer variables)
        self._last_section: Optional[str] = None
        self._stop_seq = 0
        self._reg_index: Dict[str, int] = {}
        self._global_names: List[str] = []

    # ------------------------------------------------------------------ estado
    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.running

    def status(self) -> Dict[str, Any]:
        return {
            "state": self.state, "active": self.active, "message": self.message, **self.info,
            "stop_reason": self.stop_reason, "frame": self.frame, "stack": self.stack,
            "locals": self.locals, "globals": self.globals, "registers": self.registers,
            "watches": self.watch_values if self.watch_values else [{"expr": w} for w in self.watches],
            "breakpoints": sorted(self.breakpoints.values(), key=lambda b: b["number"]),
        }

    def publish(self) -> None:
        self.bus.publish({"type": "debug", "status": self.status()})

    def log(self, line: str, level: str = "out") -> None:
        self.bus.publish({"type": "log", "source": "depurador", "level": level, "line": line})

    # ------------------------------------------------------------- registros MI
    def _on_record(self, rec: MiRecord) -> None:
        if rec.kind in ("console", "target", "log"):
            for line in rec.text.splitlines():
                if line.strip():
                    self.log(line, "err" if rec.kind == "log" and "rror" in line else "out")
            return
        if rec.kind == "status" and rec.cls == "download":
            section = (rec.results.get("_") or {}).get("section")
            if section and section != self._last_section:
                self._last_section = section
                self.log(f"Grabando la sección {section}…")
            return
        if rec.kind == "exec" and rec.cls == "running":
            self._target_running = True
            self.state = "running"
            self.stop_reason = None
            if self._stopped_evt:
                self._stopped_evt.clear()
            if not self._quiet:
                self.publish()
        elif rec.kind == "exec" and rec.cls == "stopped":
            reason = str(rec.results.get("reason", ""))
            if reason.startswith("exited"):
                self.message = "El programa terminó"
            self._target_running = False
            self._stop_seq += 1
            self.stop_reason = STOP_REASONS.get(reason, reason or "detenido")
            bp = rec.results.get("bkptno")
            if bp is not None and reason == "breakpoint-hit":
                try:
                    num = int(bp)
                    if num in self.breakpoints:
                        self.breakpoints[num]["hits"] = self.breakpoints[num].get("hits", 0) + 1
                except ValueError:
                    pass
            if self._stopped_evt:
                self._stopped_evt.set()
            if self._quiet:
                self.state = "stopped"            # la operación en curso lee el estado al final
            else:
                asyncio.get_running_loop().create_task(self._refresh_task())
        elif rec.kind == "notify" and rec.cls == "thread-group-exited" and not self._stopping:
            self.state = "error"
            self.message = "Se perdió la conexión con el chip (OpenOCD). Detené la depuración y volvé a iniciarla."
            self.publish()
        elif rec.kind == "notify" and rec.cls == "breakpoint-deleted":
            try:
                self.breakpoints.pop(int(rec.results.get("id", "0")), None)
            except ValueError:
                pass

    def _on_exit(self, code: Optional[int], tail: List[str]) -> None:
        self._proc = None
        if self._stopping:
            return
        last = next((l for l in reversed(tail) if l.strip() and not l.startswith("(gdb)")), "")
        keep_info = dict(self.info)
        self._reset_state()
        self.info = keep_info
        self.state = "error"
        self.message = f"GDB terminó (código {code}). {last[:200]}"
        self.publish()

    async def _refresh_task(self) -> None:
        async with self._lock:
            if self._target_running or not self.active:
                return
            await self._refresh()
            self.state = "stopped"
        self.publish()

    # ------------------------------------------------------------------ inicio
    async def start(self, *, gdb_argv: List[str], elf: Path, elf_rel: str, debuginfo: DebugInfo,
                    host: str, port: int, chip_name: str, idcode: Optional[IdCode], mode: str) -> Dict[str, Any]:
        if self.active:
            raise DebugError("Ya hay una sesión de depuración en curso")
        if mode not in ("load", "reset", "attach"):
            raise DebugError("Modo de inicio inválido")
        self._reset_state()
        self._stopping = False
        self._stopped_evt = asyncio.Event()
        self.info = {"elf": elf_rel, "chip_name": chip_name, "mode": mode, "started": time.time()}
        self.debuginfo = debuginfo
        self.state = "starting"
        self.publish()
        self.log("$ " + " ".join(gdb_argv), "cmd")
        proc = GdbMiProcess(gdb_argv + [str(elf)], self.env_extra, self._on_record, self._on_exit)
        try:
            proc.start()
        except FileNotFoundError:
            self.state = "error"
            self.message = (f"No se encontró '{gdb_argv[0]}'. En la placa: sudo bash scripts/build_gdb.sh "
                            "(o debugger.gdb_binary en bench.yaml).")
            self.publish()
            raise DebugError(self.message)
        self._proc = proc
        try:
            async with self._lock:
                self._quiet += 1
                try:
                    await self._connect(host, port, idcode, mode, debuginfo)
                finally:
                    self._quiet -= 1
                self.state = "stopped"
                self.stop_reason = {"load": "inicio", "reset": "inicio", "attach": "conectado"}[mode]
                await self._refresh()
        except Exception as exc:
            msg = str(exc) if isinstance(exc, DebugError) else f"Error al iniciar la depuración: {exc}"
            await self._kill()
            self.state = "error"
            self.message = msg
            self.publish()
            raise DebugError(msg)
        self.publish()
        return self.status()

    async def _send(self, command: str, timeout: float = 10.0) -> MiRecord:
        if self._proc is None:
            raise DebugError("No hay una sesión de depuración")
        return await self._proc.send(command, timeout)

    async def _monitor(self, command: str, timeout: float = 15.0) -> None:
        await self._send(f"-interpreter-exec console {quote('monitor ' + command)}", timeout)

    async def _flush_registers(self) -> None:
        # Después de un "monitor reset" GDB no sabe que el chip cambió.
        try:
            await self._send(f"-interpreter-exec console {quote('maintenance flush register-cache')}")
        except DebugError:
            await self._send(f"-interpreter-exec console {quote('flushregs')}")

    async def _connect(self, host: str, port: int, idcode: Optional[IdCode], mode: str, info: DebugInfo) -> None:
        for cmd in ("-gdb-set confirm off", "-gdb-set pagination off", "-gdb-set print pretty off",
                    "-gdb-set remotetimeout 10"):
            await self._send(cmd)
        try:
            await self._send("-gdb-set mi-async on")       # para poder pausar con -exec-interrupt
        except DebugError:
            await self._send("-gdb-set target-async on")
        self.log(f"Conectando GDB a OpenOCD ({host}:{port})…")
        await self._send(f"-target-select extended-remote {host}:{int(port)}", timeout=25)
        if idcode is not None:
            await self._check_chip(idcode)
        if mode in ("load", "reset"):
            await self._monitor("reset halt")
        if mode == "load":
            self.log("Grabando el programa en la flash (load)…")
            rec = await self._send("-target-download", timeout=240)
            size = rec.results.get("load-size")
            self.log(f"Grabado: {size} bytes" if size else "Grabado")
            await self._monitor("reset halt")
        await self._flush_registers()
        names = (await self._send("-data-list-register-names")).results.get("register-names", [])
        self._reg_index = {str(n): i for i, n in enumerate(names) if str(n) in REGISTERS}
        self._global_names = await self._list_globals(info)
        if mode in ("load", "reset") and info.has_main:
            await self._send("-break-insert -t main")
            await self._run_and_wait("-exec-continue", 20)

    async def _check_chip(self, idcode: IdCode) -> None:
        rec = await self._send(f"-data-read-memory-bytes 0x{idcode.address:08X} 4")
        mem = rec.results.get("memory") or [{}]
        raw = bytes.fromhex(str(mem[0].get("contents", "")))
        if len(raw) < 4:
            raise DebugError("No se pudo leer el ID del chip")
        found = int.from_bytes(raw[:4], "little") & idcode.mask
        if found != idcode.value:
            raise DebugError(f"CHIP EQUIVOCADO: se esperaba el ID 0x{idcode.value:03X} y el chip conectado "
                             f"tiene 0x{found:03X}. No se graba nada.")
        self.log(f"Chip verificado: ID 0x{found:03X}")

    async def _list_globals(self, info: DebugInfo) -> List[str]:
        """Variables globales definidas en los fuentes del proyecto (no en bibliotecas)."""
        try:
            rec = await self._send("-symbol-info-variables", timeout=20)
        except DebugError:
            return []
        names: List[str] = []
        for group in (rec.results.get("symbols") or {}).get("debug", []):
            if info.find(group.get("filename"), group.get("fullname")) is None:
                continue
            for sym in group.get("symbols", []):
                name = str(sym.get("name", ""))
                if name and LVALUE_RE.match(name) and name not in names:
                    names.append(name)
        return names[:MAX_GLOBALS]

    async def _run_and_wait(self, command: str, timeout: float) -> None:
        assert self._stopped_evt is not None
        self._stopped_evt.clear()
        await self._send(command)
        try:
            await asyncio.wait_for(self._stopped_evt.wait(), timeout)
        except asyncio.TimeoutError:
            await self._send("-exec-interrupt")
            await asyncio.wait_for(self._stopped_evt.wait(), 5)

    # ------------------------------------------------------------ instantánea
    def _frame(self, f: Dict[str, Any]) -> Dict[str, Any]:
        src = self.debuginfo.find(f.get("file"), f.get("fullname")) if self.debuginfo else None
        line = f.get("line")
        return {"level": int(f.get("level", 0) or 0), "func": f.get("func") or "??", "addr": f.get("addr"),
                "file": src.base if src else (f.get("file") or None), "source_id": src.id if src and src.path else None,
                "line": int(line) if line else None}

    async def _evaluate(self, expr: str) -> Dict[str, Any]:
        try:
            rec = await self._send(f"-data-evaluate-expression {quote(expr)}")
            return {"value": _short(rec.results.get("value", ""))}
        except DebugError as exc:
            return {"error": str(exc)}

    async def _refresh(self) -> None:
        """Lee pila, variables, expresiones y registros (con el programa detenido)."""
        try:
            frames = (await self._send("-stack-list-frames 0 19")).results.get("stack", [])
        except DebugError:
            frames = []
        self.stack = [self._frame(f) for f in frames]
        self.frame = self.stack[0] if self.stack else None
        self.locals = []
        try:
            variables = (await self._send("-stack-list-variables --simple-values")).results.get("variables", [])
        except DebugError:
            variables = []
        for v in variables[:60]:
            entry = {"name": v.get("name"), "type": v.get("type", ""), "arg": v.get("arg") == "1"}
            if "value" in v:
                entry["value"] = _short(v["value"])
            else:                                  # estructuras y arreglos: valor completo
                entry.update(await self._evaluate(str(v.get("name"))))
            self.locals.append(entry)
        self.globals = []
        for name in self._global_names:
            self.globals.append(dict({"name": name}, **(await self._evaluate(name))))
        self.watch_values = [dict({"expr": w}, **(await self._evaluate(w))) for w in self.watches]
        self.registers = []
        if self._reg_index:
            idx = " ".join(str(i) for i in sorted(self._reg_index.values()))
            try:
                values = (await self._send(f"-data-list-register-values --skip-unavailable x {idx}")).results
                by_num = {int(r["number"]): r.get("value") for r in values.get("register-values", [])}
                self.registers = [{"name": n, "value": by_num.get(i)} for n, i in
                                  sorted(self._reg_index.items(), key=lambda kv: REGISTERS.index(kv[0]))
                                  if by_num.get(i) is not None]
            except DebugError:
                pass

    # ------------------------------------------------------------------ control
    def _require(self) -> None:
        if not self.active:
            raise DebugError("No hay una sesión de depuración activa")
        if self.state not in ("stopped", "running"):
            raise DebugError("La sesión no está lista")

    async def control(self, action: str) -> Dict[str, Any]:
        if action not in CONTROL_ACTIONS:
            raise DebugError("Acción desconocida")
        self._require()
        if action == "pause":
            if self._target_running:
                await self._send("-exec-interrupt")
            return self.status()
        if action == "reset":
            async with self._lock:
                self._quiet += 1
                try:
                    if self._target_running:
                        assert self._stopped_evt is not None
                        self._stopped_evt.clear()
                        await self._send("-exec-interrupt")
                        await asyncio.wait_for(self._stopped_evt.wait(), 5)
                    await self._monitor("reset halt")
                    await self._flush_registers()
                finally:
                    self._quiet -= 1
                self.state = "stopped"
                self.stop_reason = "reset"
                await self._refresh()
            self.publish()
            return self.status()
        if self._target_running:
            raise DebugError("El programa está corriendo: pausalo primero")
        async with self._lock:
            if self._stopped_evt:
                self._stopped_evt.clear()
            seq = self._stop_seq
            await self._send(f"-exec-{action}")
            if self._stop_seq == seq:             # ^running: todavía no volvió a detenerse
                self._target_running = True
                self.state = "running"
                self.stop_reason = None
        self.publish()
        return self.status()

    async def _while_paused(self, fn):
        """Ejecuta fn con el programa detenido; si estaba corriendo, lo pausa y lo reanuda."""
        async with self._lock:
            resume = self._target_running
            if resume:
                self._quiet += 1
                try:
                    assert self._stopped_evt is not None
                    self._stopped_evt.clear()
                    await self._send("-exec-interrupt")
                    await asyncio.wait_for(self._stopped_evt.wait(), 5)
                finally:
                    self._quiet -= 1
            try:
                return await fn()
            finally:
                if resume:
                    seq = self._stop_seq
                    await self._send("-exec-continue")
                    if self._stop_seq == seq:
                        self._target_running = True
                        self.state = "running"

    async def toggle_breakpoint(self, source_id: int, line: int) -> Dict[str, Any]:
        self._require()
        assert self.debuginfo is not None
        try:
            src = self.debuginfo.by_id(int(source_id))
        except DebugInfoError as exc:
            raise DebugError(str(exc))
        line = int(line)
        if not 1 <= line <= 1_000_000:
            raise DebugError("Línea inválida")
        existing = next((b for b in self.breakpoints.values()
                         if b.get("source_id") == src.id and b.get("line") == line), None)

        async def apply():
            if existing:
                await self._send(f"-break-delete {int(existing['number'])}")
                self.breakpoints.pop(existing["number"], None)
                return
            rec = await self._send(f"-break-insert {quote(self.debuginfo.location(src, line))}")
            bkpt = rec.results.get("bkpt") or {}
            real = self.debuginfo.find(bkpt.get("file"), bkpt.get("fullname")) or src
            num = int(bkpt.get("number", 0))
            self.breakpoints[num] = {"number": num, "source_id": real.id, "file": real.base,
                                     "line": int(bkpt.get("line") or line), "func": bkpt.get("func"), "hits": 0}

        await self._while_paused(apply)
        self.publish()
        return self.status()

    async def clear_breakpoints(self) -> Dict[str, Any]:
        self._require()

        async def apply():
            for num in list(self.breakpoints):
                await self._send(f"-break-delete {int(num)}")
                self.breakpoints.pop(num, None)

        await self._while_paused(apply)
        self.publish()
        return self.status()

    async def add_watch(self, expr: str) -> Dict[str, Any]:
        expr = check_expression(expr)
        if expr not in self.watches:
            if len(self.watches) >= MAX_WATCHES:
                raise DebugError(f"Máximo {MAX_WATCHES} expresiones")
            self.watches.append(expr)
        await self._update_watches()
        return self.status()

    async def remove_watch(self, index: int) -> Dict[str, Any]:
        if 0 <= index < len(self.watches):
            self.watches.pop(index)
        await self._update_watches()
        return self.status()

    async def _update_watches(self) -> None:
        if self.active and not self._target_running and self.state == "stopped":
            async with self._lock:
                self.watch_values = [dict({"expr": w}, **(await self._evaluate(w))) for w in self.watches]
        else:
            self.watch_values = []
        self.publish()

    async def set_variable(self, name: str, value: str) -> Dict[str, Any]:
        self._require()
        if self._target_running:
            raise DebugError("Pausá el programa para cambiar una variable")
        assignment = check_assignment(name, value)
        async with self._lock:
            await self._send(f"-data-evaluate-expression {quote(assignment)}")
            self.log(f"{assignment}")
            await self._refresh()
        self.publish()
        return self.status()

    # ------------------------------------------------------------------- final
    async def _kill(self) -> None:
        proc = self._proc
        self._stopping = True
        self._proc = None
        if proc is not None:
            await asyncio.get_running_loop().run_in_executor(None, proc.terminate)

    async def stop(self) -> Dict[str, Any]:
        """Desconecta GDB: el programa sigue corriendo en el chip."""
        proc = self._proc
        if proc is None:
            if self.state != "off":
                keep = list(self.watches)
                self._reset_state()
                self.watches = keep
                self.publish()
            return self.status()
        self._stopping = True
        try:
            if self._target_running:
                self._quiet += 1
                if self._stopped_evt:
                    self._stopped_evt.clear()
                await proc.send("-exec-interrupt", 3)
                if self._stopped_evt:
                    await asyncio.wait_for(self._stopped_evt.wait(), 3)
            await proc.send("-target-detach", 5)
            self.log("GDB desconectado: el programa sigue corriendo")
            await proc.send("-gdb-exit", 3)
        except (DebugError, asyncio.TimeoutError):
            pass
        finally:
            self._quiet = 0
        await self._kill()
        keep = list(self.watches)
        self._reset_state()
        self.watches = keep
        self.publish()
        return self.status()

    def stop_sync(self) -> None:
        proc = self._proc
        if proc is not None:
            self._stopping = True
            threading.Thread(target=proc.terminate, daemon=True).start()
