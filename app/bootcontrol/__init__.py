"""Backends de BootControl y fábrica según la configuración."""
from __future__ import annotations

from pathlib import Path

from ..serial_monitor import SerialManager
from ..settings import BootControlConfig
from .base import BootControl, wait_for_dfu
from .gpio import GpioBootControl
from .manual import ManualBootControl
from .mock import MockBootControl
from .serial_cmd import SerialCommandBootControl

__all__ = ["BootControl", "wait_for_dfu", "create_boot_control"]


def create_boot_control(cfg: BootControlConfig, serial: SerialManager, mock: bool,
                        state_dir: Path) -> BootControl:
    if mock:
        return MockBootControl(cfg.backend, state_dir)
    if cfg.backend == "gpio":
        return GpioBootControl(cfg)
    if cfg.backend == "serial_command":
        return SerialCommandBootControl(cfg, serial)
    return ManualBootControl(cfg.timeout_s)
