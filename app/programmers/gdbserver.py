"""OpenOCD en modo servidor GDB (proceso persistente controlado por la app)."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, List, Optional

from ..events import EventBus
from ..procs import StreamingProcess


class GdbServerError(RuntimeError):
    pass


class GdbServer:
    def __init__(self, bus: EventBus, env_extra: Optional[Dict[str, str]] = None) -> None:
        self.bus = bus
        self.env_extra = env_extra or {}
        self._proc: Optional[StreamingProcess] = None
        self._watch: Optional[asyncio.Task] = None
        self._stopping = False
        self.state = "stopped"          # stopped | starting | listening | error
        self.info: Dict[str, Any] = {}
        self.client_connected = False
        self.last_error: Optional[str] = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.running

    def status(self) -> Dict[str, Any]:
        return {"state": self.state, "active": self.active, "client_connected": self.client_connected,
                "last_error": self.last_error, **self.info}

    def _publish(self) -> None:
        self.bus.publish({"type": "gdb", "status": self.status()})

    def uses_probe(self, serial: Optional[str]) -> bool:
        """True si el servidor activo ocupa ese ST-Link (o no se puede saber)."""
        if not self.active:
            return False
        mine = self.info.get("probe_serial")
        return mine is None or serial is None or mine == serial

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
        if changed:
            self.bus.publish({"type": "gdb", "status": self.status()})

    async def start(self, argv: List[str], info: Dict[str, Any], wait_s: float = 15.0) -> Dict[str, Any]:
        if self.active:
            raise GdbServerError("El servidor GDB ya está en marcha")
        self._stopping = False
        self.state = "starting"
        self.client_connected = False
        self.last_error = None
        self.info = dict(info, started=time.time())
        self.bus.publish({"type": "log", "source": "gdb", "level": "cmd", "line": "$ " + " ".join(argv)})
        proc = StreamingProcess(argv, on_line=self._on_line, env_extra=self.env_extra)
        try:
            proc.start()
        except FileNotFoundError:
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
            await asyncio.sleep(0.2)   # deja que _watch_process registre el error
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
        if self._stopping:
            self.state = "stopped"
        else:
            tail = [l for l in list(proc.lines)[-8:] if l.strip()]
            errors = [l for l in tail if "Error" in l or "error" in l]
            self.state = "error"
            self.last_error = f"OpenOCD terminó (código {code}). " + (errors[-1] if errors else (tail[-1] if tail else ""))
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
        self.state = "stopped"
        self.client_connected = False
        self._publish()
        return self.status()

    def stop_sync(self) -> None:
        """Parada sincrónica (al apagar la app)."""
        if self._proc is not None:
            self._stopping = True
            threading.Thread(target=self._proc.terminate, daemon=True).start()
