"""Ejecutor de trabajos de programación: UN solo trabajo a la vez.

Cualquier operación sobre hardware (OpenOCD, dfu-util, entrada/salida del
bootloader, conversión) corre como un "trabajo". Si ya hay uno en curso, el
siguiente se rechaza con BusyError (la API responde 409): encolar flasheos a
ciegas desde remoto es más peligroso que útil.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from ..events import EventBus
from ..history import History
from ..procs import StreamingProcess


class BusyError(RuntimeError):
    """Ya hay un trabajo de programación en curso."""


class JobFailed(RuntimeError):
    """El trabajo terminó con error (mensaje para el usuario)."""


class JobCancelled(RuntimeError):
    """El usuario canceló el trabajo."""


@dataclass
class Job:
    id: str
    action: str
    method: str
    chip: Optional[str] = None
    file: Optional[str] = None
    description: str = ""
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    state: str = "running"          # running | ok | error | cancelled
    message: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_public(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.finished:
            data["duration_s"] = round(self.finished - self.started, 2)
        return data


@dataclass
class ProcessResult:
    returncode: int
    output: str


class JobContext:
    """Lo que un trabajo puede usar: log, procesos cancelables, avisos al usuario."""

    def __init__(self, runner: "JobRunner", job: Job) -> None:
        self.runner = runner
        self.job = job
        self.cancel_event = threading.Event()
        self._process: Optional[StreamingProcess] = None

    # --- log y avisos -------------------------------------------------------
    def log(self, message: str, level: str = "info", source: str = "app") -> None:
        self.runner.bus.publish({"type": "log", "source": source, "level": level,
                                 "line": message, "job": self.job.id})

    def warn(self, message: str) -> None:
        self.job.warnings.append(message)
        self.log(message, level="warn")

    def prompt(self, kind: str, message: str, timeout_s: Optional[float] = None) -> None:
        """Muestra un diálogo en la interfaz (p. ej. 'poné BOOT0 en alto')."""
        self.runner.bus.publish({"type": "prompt", "kind": kind, "message": message,
                                 "timeout_s": timeout_s, "job": self.job.id, "open": True})

    def close_prompt(self) -> None:
        self.runner.bus.publish({"type": "prompt", "open": False, "job": self.job.id})

    # --- cancelación --------------------------------------------------------
    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled("Cancelado por el usuario")

    async def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self.check_cancelled()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.1, remaining))

    def cancel(self) -> None:
        self.cancel_event.set()
        if self._process is not None:
            threading.Thread(target=self._process.terminate, daemon=True).start()

    # --- procesos -----------------------------------------------------------
    async def run(self, argv: Sequence[str], source: str, timeout_s: float,
                  check: bool = True) -> ProcessResult:
        """Ejecuta un comando externo transmitiendo su salida al log en vivo."""
        self.check_cancelled()
        self.log("$ " + " ".join(_quote_for_log(a) for a in argv), level="cmd", source=source)
        proc = StreamingProcess(
            argv,
            on_line=lambda line: self.log(line, level="out", source=source),
            env_extra=self.runner.env_extra,
        )
        try:
            proc.start()
        except FileNotFoundError:
            raise JobFailed(f"No se encontró el programa '{argv[0]}'. ¿Está instalado?")
        self._process = proc
        deadline = time.monotonic() + timeout_s
        try:
            while proc.poll() is None:
                if self.cancel_event.is_set():
                    await asyncio.get_running_loop().run_in_executor(None, proc.terminate)
                    raise JobCancelled("Cancelado por el usuario")
                if time.monotonic() > deadline:
                    await asyncio.get_running_loop().run_in_executor(None, proc.terminate)
                    raise JobFailed(f"{source}: se superó el tiempo máximo ({timeout_s:.0f} s)")
                await asyncio.sleep(0.05)
            await asyncio.get_running_loop().run_in_executor(None, proc.join_reader)
        finally:
            self._process = None
        result = ProcessResult(returncode=proc.poll() or 0, output=proc.output())
        if check and result.returncode != 0:
            raise JobFailed(f"{source} terminó con código {result.returncode}")
        return result


def _quote_for_log(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


JobFunction = Callable[[JobContext], Awaitable[Optional[str]]]


class JobRunner:
    def __init__(self, bus: EventBus, history: History,
                 env_extra: Optional[Dict[str, str]] = None) -> None:
        self.bus = bus
        self.history = history
        self.env_extra = env_extra or {}
        self._current: Optional[Job] = None
        self._context: Optional[JobContext] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def busy(self) -> bool:
        return self._current is not None

    @property
    def current(self) -> Optional[Job]:
        return self._current

    def start(self, job: Job, fn: JobFunction) -> Job:
        """Arranca un trabajo en segundo plano. BusyError si ya hay uno.

        Se llama desde el event loop (un solo hilo), así que chequear y tomar
        el "lock" es atómico.
        """
        if self._current is not None:
            raise BusyError(f"El programador está ocupado ({self._current.description or self._current.action})")
        self._current = job
        self._context = JobContext(self, job)
        self.bus.publish({"type": "job", "job": job.to_public()})
        self._task = asyncio.get_running_loop().create_task(self._run(job, fn, self._context))
        return job

    async def _run(self, job: Job, fn: JobFunction, ctx: JobContext) -> None:
        ctx.log(f"▶ {job.description}")
        try:
            message = await fn(ctx)
            job.state = "ok"
            job.message = message or "Completado"
            ctx.log(f"✔ {job.message}", level="ok")
        except JobCancelled as exc:
            job.state = "cancelled"
            job.message = str(exc)
            ctx.log(f"■ {job.message}", level="warn")
        except JobFailed as exc:
            job.state = "error"
            job.message = str(exc)
            ctx.log(f"✘ {job.message}", level="error")
        except Exception as exc:  # error inesperado: se informa igual
            job.state = "error"
            job.message = f"Error interno: {exc}"
            ctx.log(f"✘ {job.message}", level="error")
        finally:
            ctx.close_prompt()
            job.finished = time.time()
            self.history.add(job.to_public())
            self._current = None
            self._context = None
            self.bus.publish({"type": "job", "job": job.to_public()})

    def cancel(self) -> bool:
        if self._context is None:
            return False
        self._context.cancel()
        return True

    async def wait_idle(self, timeout_s: float = 30.0) -> None:
        """Espera a que termine el trabajo actual (usado en tests)."""
        if self._task is not None:
            await asyncio.wait_for(asyncio.shield(self._task), timeout_s)


def new_job(action: str, method: str, description: str, chip: Optional[str] = None,
            file: Optional[str] = None) -> Job:
    return Job(id=uuid.uuid4().hex[:12], action=action, method=method,
               chip=chip, file=file, description=description)
