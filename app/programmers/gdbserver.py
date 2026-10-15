"""OpenOCD en modo servidor GDB (proceso persistente controlado por la app)."""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ..events import EventBus
from ..procs import StreamingProcess
from ..relay import TcpRelay
from .runner import failure_hint


class GdbServerError(RuntimeError):
    pass


class GdbServerBusy(GdbServerError):
    """El OpenOCD actual no sirve y no se puede reiniciar: alguien lo está usando."""


@dataclass(frozen=True)
class ServerTarget:
    """Lo que necesita quien pide el OpenOCD compartido (servidor GDB, Depurar o RTT)."""
    chip: str
    probe_serial: Optional[str]
    rtt_address: Optional[str] = None     # "0x20000430"; None = no hace falta RTT
    rtt_port: Optional[int] = None


class GdbServer:
    def __init__(self, bus: EventBus, env_extra: Optional[Dict[str, str]] = None) -> None:
        self.bus = bus
        self.env_extra = env_extra or {}
        self._proc: Optional[StreamingProcess] = None
        self._watch: Optional[asyncio.Task] = None
        self._relays: List[TcpRelay] = []
        self._stopping = False
        self.state = "stopped"          # stopped | starting | listening | error
        self.info: Dict[str, Any] = {}
        self.client_connected = False
        self.last_error: Optional[str] = None
        # RTT: None (sin RTT) | buscando | encontrado | no_encontrado
        self.rtt_state: Optional[str] = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.running

    def status(self) -> Dict[str, Any]:
        return {"state": self.state, "active": self.active, "client_connected": self.client_connected,
                "last_error": self.last_error, "rtt_state": self.rtt_state, **self.info}

    def _publish(self) -> None:
        self.bus.publish({"type": "gdb", "status": self.status()})

    def uses_probe(self, serial: Optional[str]) -> bool:
        """True si el servidor activo ocupa ese ST-Link (o no se puede saber)."""
        if not self.active:
            return False
        mine = self.info.get("probe_serial")
        return mine is None or serial is None or mine == serial

    def serves(self, want: ServerTarget) -> bool:
        """¿El OpenOCD que está corriendo sirve para este pedido?"""
        if not self.active or self.info.get("chip") != want.chip:
            return False
        if want.probe_serial and self.info.get("probe_serial") not in (None, want.probe_serial):
            return False
        if want.rtt_address is None:
            return True                       # no pide RTT: sirve con o sin RTT
        rtt = self.info.get("rtt") or {}
        return rtt.get("address") == want.rtt_address and rtt.get("port") == want.rtt_port

    async def ensure(self, want: ServerTarget, start: Callable[[], Awaitable[Any]], *,
                     used_by_debugger: bool, wait_s: float = 10.0) -> Dict[str, Any]:
        """Deja corriendo un OpenOCD que sirva para `want`.

        Reutiliza el actual si sirve. Si no, lo reinicia con `start`, salvo que
        alguien lo esté usando: reiniciarlo le cortaría la sesión a la pestaña
        Depurar o a un GDB externo (OpenOCD acepta un solo cliente GDB).
        Con RTT pedido pero sin encontrar el bloque, y sin nadie conectado, se
        reinicia para volver a buscarlo (el programa pudo haber arrancado después).
        """
        nobody_using = not used_by_debugger and not self.client_connected
        retry_rtt = bool(want.rtt_address) and self.rtt_state == "no_encontrado" and nobody_using
        if not (self.serves(want) and not retry_rtt):
            if self.active:
                if used_by_debugger:
                    raise GdbServerBusy("La depuración de la web está usando OpenOCD con otro chip o programa: "
                                        "detenela primero")
                if self.client_connected:
                    raise GdbServerBusy("Hay un GDB externo conectado al servidor y reiniciar OpenOCD le "
                                        "cortaría la sesión: desconectalo (quit) y probá de nuevo")
                await self.stop()
            await start()
        deadline = time.monotonic() + wait_s       # OpenOCD puede seguir arrancando
        while self.state != "listening" and self.active and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if self.state != "listening":
            raise GdbServerError(self.last_error or "OpenOCD no arrancó")
        return self.status()

    def local_endpoint(self) -> Tuple[str, int, Optional[int]]:
        """(IP, puerto GDB, puerto RTT) de OpenOCD, para conectarse desde la propia placa."""
        local = self.info.get("local") or {}
        rtt = self.info.get("rtt") or {}
        return "127.0.0.1", int(local.get("gdb") or self.info.get("port", 3333)), local.get("rtt") or rtt.get("port")

    def tcl_endpoint(self) -> Optional[Tuple[str, int]]:
        """Puerto TCL de OpenOCD (sólo en 127.0.0.1), o None."""
        tcl = (self.info.get("local") or {}).get("tcl")
        return ("127.0.0.1", int(tcl)) if tcl and self.active else None

    def _on_line(self, line: str) -> None:
        self.bus.publish({"type": "log", "source": "gdb", "level": "out", "line": line})
        changed = False
        if "Listening on port" in line and "gdb" in line:
            self.state = "listening"
            changed = True
        elif "accepting 'gdb' connection" in line:
            self.client_connected = True
            changed = True
        elif "dropped 'gdb' connection" in line:
            self.client_connected = False
            changed = True
        elif "rtt: Searching for control block" in line and self.rtt_state != "encontrado":
            self.rtt_state = "buscando"
            changed = True
        elif "rtt: Control block found" in line:
            self.rtt_state = "encontrado"
            changed = True
        elif "rtt: No control block found" in line:
            self.rtt_state = "no_encontrado"
            changed = True
        if changed:
            self.bus.publish({"type": "gdb", "status": self.status()})

    async def start(self, argv: List[str], info: Dict[str, Any], wait_s: float = 15.0,
                    relays: Optional[List[TcpRelay]] = None) -> Dict[str, Any]:
        """Arranca OpenOCD y los reenvíos de sus puertos a la red (`relays`)."""
        if self.active:
            raise GdbServerError("El servidor GDB ya está en marcha")
        for relay in relays or []:
            try:
                await relay.start()
                self._relays.append(relay)
            except OSError as exc:
                await self._close_relays()
                self.state = "error"
                self.last_error = f"No se pudo abrir el puerto {relay.port} en {relay.host}: {exc.strerror or exc}"
                self._publish()
                raise GdbServerError(self.last_error)
        self._stopping = False
        self.state = "starting"
        self.client_connected = False
        self.last_error = None
        self.rtt_state = "buscando" if info.get("rtt") else None
        self.info = dict(info, started=time.time())
        self.bus.publish({"type": "log", "source": "gdb", "level": "cmd", "line": "$ " + " ".join(argv)})
        proc = StreamingProcess(argv, on_line=self._on_line, env_extra=self.env_extra)
        try:
            proc.start()
        except FileNotFoundError:
            await self._close_relays()
            self.state = "error"
            self.last_error = f"No se encontró '{argv[0]}'"
            self._publish()
            raise GdbServerError(self.last_error)
        self._proc = proc
        self._publish()
        self._watch = asyncio.get_running_loop().create_task(self._watch_process(proc))

        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.state == "listening":
                return self.status()
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.1)
        if proc.poll() is not None:
            # Que _watch_process termine de leer la salida y registre el motivo.
            try:
                await asyncio.wait_for(asyncio.shield(self._watch), 5)
            except asyncio.TimeoutError:
                pass
            await self._close_relays()
            raise GdbServerError(self.last_error or "OpenOCD terminó al iniciar")
        return self.status()   # sigue arrancando; el estado llegará por SSE

    async def _watch_process(self, proc: StreamingProcess) -> None:
        while proc.poll() is None:
            await asyncio.sleep(0.5)
        await asyncio.get_running_loop().run_in_executor(None, proc.join_reader)
        if self._proc is not proc:
            return
        code = proc.poll()
        self._proc = None
        self.client_connected = False
        await self._close_relays()
        if self._stopping:
            self.state = "stopped"
        else:
            tail = [l for l in list(proc.lines)[-8:] if l.strip()]
            errors = [l for l in tail if "Error" in l or "error" in l]
            self.state = "error"
            self.last_error = f"OpenOCD terminó (código {code}). " + (errors[-1] if errors else (tail[-1] if tail else ""))
            hint = failure_hint(" ".join(errors))
            if hint:
                self.last_error += f". {hint}"
        self._publish()

    async def stop(self) -> Dict[str, Any]:
        proc = self._proc
        if proc is None:
            self.state = "stopped"
            self._publish()
            return self.status()
        self._stopping = True
        await asyncio.get_running_loop().run_in_executor(None, proc.terminate)
        if self._watch is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._watch), 5)
            except asyncio.TimeoutError:
                pass
        self._proc = None
        await self._close_relays()
        self.state = "stopped"
        self.client_connected = False
        self._publish()
        return self.status()

    async def _close_relays(self) -> None:
        relays, self._relays = self._relays, []
        for relay in relays:
            await relay.stop()

    def stop_sync(self) -> None:
        """Parada sincrónica (al apagar la app)."""
        if self._proc is not None:
            self._stopping = True
            threading.Thread(target=self._proc.terminate, daemon=True).start()
