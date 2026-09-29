"""Backend serial_command: se le pide al firmware (por UART/VCP) que salte al bootloader.

Requiere que el firmware en ejecución implemente ese comando (por ejemplo,
al recibir "BOOTLOADER\\n" desactiva periféricos y salta a la System Memory).
"""
from __future__ import annotations

import asyncio

from ..programmers.runner import JobContext, JobFailed
from ..serial_monitor import SerialError, SerialManager
from ..settings import BootControlConfig
from .base import BootControl


class SerialCommandBootControl(BootControl):
    name = "serial_command"
    needs_dfu_leave = True

    def __init__(self, cfg: BootControlConfig, serial: SerialManager) -> None:
        self.cfg = cfg
        self.serial = serial

    async def enter(self, ctx: JobContext) -> None:
        command = self.cfg.serial_command.encode("utf-8")
        ctx.log(f"Enviando comando de bootloader por {self.cfg.serial_port} @ {self.cfg.serial_baud}: "
                f"{self.cfg.serial_command!r}")
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.serial.send_to_port,
                                       self.cfg.serial_port, self.cfg.serial_baud, command)
        except SerialError as exc:
            raise JobFailed(f"No se pudo enviar el comando de bootloader: {exc}")

    async def exit(self, ctx: JobContext, left_bootloader: bool) -> None:
        # dfu-util ya hizo (o intentó) el "leave"; no hay más que hacer.
        if not left_bootloader:
            ctx.warn("El chip puede seguir en el bootloader; reseteálo si no arranca la aplicación.")

    def describe(self) -> str:
        return f"serial_command ({self.cfg.serial_port})"
