"""Ejecución de procesos externos con lectura de salida línea a línea.

Se usa subprocess.Popen con una lista de argumentos (NUNCA shell=True) y un
hilo lector. Así funciona igual en Linux (placa) y en Windows (desarrollo en
modo mock), sin depender del tipo de event loop de asyncio.
"""
from __future__ import annotations

import os
import subprocess
import threading
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Sequence

LineCallback = Callable[[str], None]


class StreamingProcess:
    def __init__(self, argv: Sequence[str], on_line: Optional[LineCallback] = None,
                 env_extra: Optional[Dict[str, str]] = None, keep_lines: int = 2000) -> None:
        if isinstance(argv, str):
            raise TypeError("argv debe ser una lista de argumentos, no un string")
        self.argv: List[str] = [str(a) for a in argv]
        self._on_line = on_line
        self._env_extra = env_extra or {}
        self.lines: Deque[str] = deque(maxlen=keep_lines)
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None

    def start(self) -> None:
        env = os.environ.copy()
        env.update(self._env_extra)
        self._proc = subprocess.Popen(
            self.argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, name="proc-reader", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            line = raw.rstrip("\r\n")
            self.lines.append(line)
            if self._on_line:
                try:
                    self._on_line(line)
                except Exception:
                    pass
        self._proc.stdout.close()

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc else None

    def poll(self) -> Optional[int]:
        return self._proc.poll() if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def join_reader(self, timeout: float = 2.0) -> None:
        """Espera a que el hilo lector termine de volcar la salida."""
        if self._reader:
            self._reader.join(timeout)

    def terminate(self, grace_s: float = 3.0) -> None:
        """SIGTERM y, si no termina a tiempo, SIGKILL."""
        if not self._proc or self._proc.poll() is not None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=grace_s)

    def output(self) -> str:
        return "\n".join(self.lines)


def run_capture(argv: Sequence[str], timeout: float = 10.0,
                env_extra: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Ejecución corta y sincrónica (listados, versiones). Sin shell."""
    if isinstance(argv, str):
        raise TypeError("argv debe ser una lista de argumentos")
    env = os.environ.copy()
    env.update(env_extra or {})
    return subprocess.run([str(a) for a in argv], stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          env=env, shell=False)
