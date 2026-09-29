"""BootControl simulado: reproduce el flujo del backend configurado sin hardware.

Al "entrar" programa la aparición del dispositivo DFU simulado (que ve
scripts/mock_dfu_util.py) después de `delay_s` segundos.
"""
from __future__ import annotations

from pathlib import Path

from .. import mock_state
from ..programmers.runner import JobContext
from .base import BootControl
from .manual import ENTER_MESSAGE, EXIT_MESSAGE


class MockBootControl(BootControl):
    def __init__(self, backend: str, state_dir: Path, delay_s: float = 2.0,
                 never_appears: bool = False) -> None:
        self.name = backend
        self.needs_dfu_leave = backend != "gpio"
        self.state_dir = state_dir
        self.delay_s = delay_s
        self.never_appears = never_appears

    async def enter(self, ctx: JobContext) -> None:
        if self.name == "gpio":
            ctx.log("[mock] GPIO: BOOT0=1")
            ctx.log("[mock] GPIO: pulso de NRST")
        elif self.name == "serial_command":
            ctx.log("[mock] Enviando comando de bootloader por el puerto serie")
        else:
            ctx.prompt("manual_boot", ENTER_MESSAGE + " (simulado: aparece solo)", timeout_s=None)
        if not self.never_appears:
            mock_state.set_dfu_present(self.state_dir, delay_s=self.delay_s)

    async def exit(self, ctx: JobContext, left_bootloader: bool) -> None:
        if self.name == "gpio":
            ctx.log("[mock] GPIO: BOOT0=0 y pulso de NRST")
        elif self.name == "manual":
            ctx.warn(EXIT_MESSAGE)
        mock_state.clear_dfu(self.state_dir)

    def describe(self) -> str:
        return f"{self.name} (simulado)"
