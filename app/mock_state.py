"""Estado compartido del modo mock: dispositivo DFU simulado y flash simulada.

Lo usan el BootControl simulado (proceso de la app) y los simuladores de
scripts/ (procesos aparte), por eso vive en archivos del directorio de estado.

La flash simulada de cada "dispositivo" (stm32, esp, avr) guarda lo que se
grabó como segmentos (dirección + archivo); al leer, lo no grabado es 0xFF.
Así lo que se graba por SWD se puede leer por DFU, y al revés.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional

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


# --------------------------------------------------------------- flash simulada

def _flash_dir(state_dir: Path, device: str) -> Path:
    return Path(state_dir) / f"mock_flash_{device}"


def _segments(folder: Path) -> List[list]:
    try:
        return json.loads((folder / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def flash_write(state_dir: Path, device: str, address: int, data: bytes) -> None:
    folder = _flash_dir(state_dir, device)
    folder.mkdir(parents=True, exist_ok=True)
    items = _segments(folder)
    name = f"{len(items):04d}.bin"
    (folder / name).write_bytes(data)
    items.append([int(address), name])
    tmp = folder / "index.tmp"
    tmp.write_text(json.dumps(items), encoding="utf-8")
    os.replace(tmp, folder / "index.json")


def flash_erase(state_dir: Path, device: str) -> None:
    shutil.rmtree(_flash_dir(state_dir, device), ignore_errors=True)


def flash_read(state_dir: Path, device: str, address: int, size: int) -> bytes:
    folder = _flash_dir(state_dir, device)
    out = bytearray(b"\xff" * size)
    for addr, name in _segments(folder):
        try:
            data = (folder / name).read_bytes()
        except OSError:
            continue
        start, end = max(addr, address), min(addr + len(data), address + size)
        if start < end:
            out[start - address:end - address] = data[start - addr:end - addr]
    return bytes(out)


# ------------------------------------------------------- programa que "corre"

def set_running_elf(state_dir: Path, device: str, elf: Optional[str]) -> None:
    """El .elf grabado (o None si se borró o se grabó un .bin/.hex): de ahí salen
    las variables que el OpenOCD simulado devuelve al muestrear."""
    path = Path(state_dir) / f"mock_program_{device}.json"
    if elf is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(json.dumps({"elf": str(elf)}), encoding="utf-8")


def running_elf(state_dir: Path, device: str) -> Optional[str]:
    try:
        return json.loads((Path(state_dir) / f"mock_program_{device}.json").read_text(encoding="utf-8"))["elf"]
    except (OSError, ValueError, KeyError):
        return None
