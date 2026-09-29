"""Backend gpio: la BeagleBoard maneja BOOT0 y NRST del STM32 con libgpiod.

¡ATENCIÓN! Los GPIO de la BeagleBoard-xM son de 1,8 V. Nunca conectarlos
directo a BOOT0/NRST (3,3 V): usar un transistor o un adaptador de nivel
(ver README). La polaridad de cada línea se configura con active_low, por
ejemplo si un transistor NPN invierte la señal.

Se usa el character device (/dev/gpiochipN) vía el módulo Python "gpiod",
compatible con la API 1.x (python3-libgpiod de Debian 11/12) y la 2.x (PyPI).
Nunca sysfs (/sys/class/gpio, obsoleto).
"""
from __future__ import annotations

import asyncio
from typing import Callable, Dict, Optional, Protocol

from ..programmers.runner import JobContext, JobFailed
from ..settings import BootControlConfig, GpioLineConfig
from .base import BootControl

CONSUMER = "stm32-bench"


class GpioDriver(Protocol):
    def set(self, name: str, active: bool) -> None: ...
    def release(self) -> None: ...


class GpiodDriver:
    """Adaptador sobre el módulo gpiod (API v1 o v2). Las líneas arrancan inactivas."""

    def __init__(self, lines: Dict[str, GpioLineConfig]) -> None:
        try:
            import gpiod  # type: ignore
        except ImportError:
            raise JobFailed("Falta el módulo Python 'gpiod' (apt install python3-libgpiod)")
        self._gpiod = gpiod
        self._v2 = hasattr(gpiod, "request_lines")
        self._handles: Dict[str, tuple] = {}
        try:
            for name, cfg in lines.items():
                self._handles[name] = self._request(cfg)
        except Exception as exc:
            self.release()
            raise JobFailed(f"No se pudieron tomar las líneas GPIO: {exc}")

    def _request(self, cfg: GpioLineConfig) -> tuple:
        gpiod = self._gpiod
        if self._v2:
            from gpiod.line import Direction, Value  # type: ignore
            path = cfg.chip if cfg.chip.startswith("/dev/") else f"/dev/{cfg.chip}"
            settings = gpiod.LineSettings(direction=Direction.OUTPUT, active_low=cfg.active_low,
                                          output_value=Value.INACTIVE)
            request = gpiod.request_lines(path, consumer=CONSUMER, config={cfg.line: settings})
            return ("v2", request, cfg.line)
        chip = gpiod.Chip(cfg.chip.replace("/dev/", ""))
        line = chip.get_line(cfg.line)
        flags = gpiod.LINE_REQ_FLAG_ACTIVE_LOW if cfg.active_low else 0
        line.request(consumer=CONSUMER, type=gpiod.LINE_REQ_DIR_OUT, flags=flags, default_val=0)
        return ("v1", line, chip)

    def set(self, name: str, active: bool) -> None:
        handle = self._handles[name]
        if handle[0] == "v2":
            from gpiod.line import Value  # type: ignore
            handle[1].set_value(handle[2], Value.ACTIVE if active else Value.INACTIVE)
        else:
            handle[1].set_value(1 if active else 0)

    def release(self) -> None:
        for handle in self._handles.values():
            try:
                if handle[0] == "v2":
                    handle[1].release()
                else:
                    handle[1].release()
                    handle[2].close()
            except Exception:
                pass
        self._handles.clear()


DriverFactory = Callable[[Dict[str, GpioLineConfig]], GpioDriver]


class GpioBootControl(BootControl):
    name = "gpio"
    needs_dfu_leave = False   # al salir se resetea con NRST

    def __init__(self, cfg: BootControlConfig, driver_factory: Optional[DriverFactory] = None) -> None:
        if cfg.boot0 is None or cfg.nrst is None:
            raise ValueError("GpioBootControl necesita boot0 y nrst")
        self.cfg = cfg
        self._factory = driver_factory or GpiodDriver
        self._driver: Optional[GpioDriver] = None

    def _lines(self) -> Dict[str, GpioLineConfig]:
        return {"boot0": self.cfg.boot0, "nrst": self.cfg.nrst}

    async def _reset_pulse(self, ctx: JobContext) -> None:
        assert self._driver is not None
        self._driver.set("nrst", True)            # NRST activo = chip en reset
        await ctx.sleep(self.cfg.reset_pulse_ms / 1000)
        self._driver.set("nrst", False)
        await ctx.sleep(self.cfg.settle_ms / 1000)

    async def enter(self, ctx: JobContext) -> None:
        self._driver = self._factory(self._lines())
        ctx.log(f"GPIO: BOOT0=1 ({self.cfg.boot0.chip}:{self.cfg.boot0.line})")
        self._driver.set("boot0", True)
        ctx.log(f"GPIO: pulso de NRST de {self.cfg.reset_pulse_ms} ms "
                f"({self.cfg.nrst.chip}:{self.cfg.nrst.line})")
        await self._reset_pulse(ctx)

    async def exit(self, ctx: JobContext, left_bootloader: bool) -> None:
        """BOOT0=0 y reset: el chip arranca su aplicación. Siempre libera las líneas."""
        if self._driver is None:
            self._driver = self._factory(self._lines())
        try:
            ctx.log("GPIO: BOOT0=0 y pulso de NRST para volver a la aplicación")
            self._driver.set("boot0", False)
            self._driver.set("nrst", True)
            # Sin ctx.sleep: esta parte no debe poder cancelarse a mitad.
            await asyncio.sleep(self.cfg.reset_pulse_ms / 1000)
            self._driver.set("nrst", False)
        finally:
            self._driver.release()
            self._driver = None

    def describe(self) -> str:
        b, n = self.cfg.boot0, self.cfg.nrst
        return f"gpio (BOOT0={b.chip}:{b.line}, NRST={n.chip}:{n.line})"
