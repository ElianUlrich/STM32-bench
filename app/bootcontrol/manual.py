"""Backend manual: la persona que está en el banco mueve BOOT0 y resetea."""
from __future__ import annotations

from ..programmers.runner import JobContext
from .base import BootControl

ENTER_MESSAGE = ("Poné BOOT0 en alto (1) y reseteá la placa (botón RESET o desconectar "
                 "y reconectar el USB del STM32). La operación sigue sola cuando aparezca "
                 "el dispositivo DFU.")
EXIT_MESSAGE = ("Volvé BOOT0 a bajo (0). Si no, el próximo reset vuelve a entrar al "
                "bootloader en lugar de arrancar tu programa.")


class ManualBootControl(BootControl):
    name = "manual"
    needs_dfu_leave = True

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s

    async def enter(self, ctx: JobContext) -> None:
        ctx.log("Esperando que se ponga el chip en modo bootloader manualmente")
        ctx.prompt("manual_boot", ENTER_MESSAGE, timeout_s=self.timeout_s)

    async def exit(self, ctx: JobContext, left_bootloader: bool) -> None:
        ctx.warn(EXIT_MESSAGE)

    def describe(self) -> str:
        return "manual (BOOT0 y reset a mano)"
