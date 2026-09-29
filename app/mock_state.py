"""Estado compartido del modo mock (dispositivo DFU simulado).

Lo usan el BootControl simulado (proceso de la app) y scripts/mock_dfu_util.py
(proceso aparte), por eso vive en un archivo JSON del directorio de estado.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

STATE_FILE = "mock_dfu.json"
ENV_VAR = "BENCH_MOCK_STATE_DIR"
MOCK_DFU_SERIAL = "3957377B3432"


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / STATE_FILE


def set_dfu_present(state_dir: Path, delay_s: float = 0.0, serial: str = MOCK_DFU_SERIAL) -> None:
    """El dispositivo DFU simulado 'aparece' dentro de delay_s segundos."""
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    data = {"present_at": time.time() + delay_s, "serial": serial}
    tmp = _path(state_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, _path(state_dir))


def clear_dfu(state_dir: Path) -> None:
    try:
        _path(state_dir).unlink()
    except FileNotFoundError:
        pass


def dfu_present(state_dir: Path) -> Optional[str]:
    """Devuelve el número de serie si el dispositivo DFU simulado está presente."""
    try:
        data = json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("present_at") is not None and time.time() >= float(data["present_at"]):
        return str(data.get("serial", MOCK_DFU_SERIAL))
    return None
