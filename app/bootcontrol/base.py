"""Abstracción BootControl: cómo poner el STM32 en modo bootloader (DFU) y sacarlo.

Flujo general (lo orquesta app/jobs.py):
    1. Si el dispositivo DFU ya está presente, no se hace nada.
    2. boot.enter(ctx)          -> p. ej. BOOT0=1 + pulso de NRST
    3. wait_for_dfu(...)        -> espera a que aparezca 0483:df11 (con timeout)
    4. operación con dfu-util
    5. boot.exit(ctx, left)     -> p. ej. BOOT0=0 + pulso de NRST
       (se ejecuta SIEMPRE en un finally, aunque la operación falle)
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, List

from ..programmers.dfu import DfuDevice
from ..programmers.runner import JobContext, JobFailed


class BootControl(ABC):
    #: nombre del backend (manual, gpio, serial_command)
    name: str = "base"
    #: True si al salir hay que pedirle a dfu-util que salte a la aplicación
    #: (el backend no puede resetear el chip por su cuenta).
    needs_dfu_leave: bool = True

    @abstractmethod
    async def enter(self, ctx: JobContext) -> None:
        """Pone el chip en modo bootloader (o le pide al usuario que lo haga)."""

    @abstractmethod
    async def exit(self, ctx: JobContext, left_bootloader: bool) -> None:
        """Devuelve el chip a su aplicación. Debe ser tolerante a errores."""

    def describe(self) -> str:
        return self.name


ListFn = Callable[[], Awaitable[List[DfuDevice]]]
MatchFn = Callable[[DfuDevice], bool]


async def wait_for_dfu(ctx: JobContext, list_fn: ListFn, match: MatchFn,
                       timeout_s: float, poll_s: float = 1.0) -> DfuDevice:
    """Espera (cancelable) a que aparezca un dispositivo DFU que cumpla `match`."""
    deadline = time.monotonic() + timeout_s
    announced = False
    while True:
        ctx.check_cancelled()
        devices = await list_fn()
        for dev in devices:
            if match(dev):
                return dev
        if time.monotonic() >= deadline:
            raise JobFailed(
                f"No apareció el dispositivo DFU en {timeout_s:.0f} s. Revisá el cable USB "
                "del STM32 (no el del ST-Link), que BOOT0 esté en alto y que se haya reseteado.")
        if not announced:
            ctx.log(f"Esperando dispositivo DFU (hasta {timeout_s:.0f} s)...")
            announced = True
        await ctx.sleep(poll_s)
