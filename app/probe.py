"""Detección de programadores ST-Link (por sysfs en Linux, SetupAPI en Windows) y
dispositivos DFU (dfu-util -l)."""
from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .procs import run_capture
from .programmers import dfu as dfu_cmd
from .winusb import WinUsbDevice, present_devices

ST_VID = "0483"
# PIDs de ST-Link conocidos -> nombre legible.
STLINK_PIDS: Dict[str, str] = {
    "3744": "ST-LINK/V1",
    "3748": "ST-LINK/V2",
    "374b": "ST-LINK/V2-1",
    "374d": "STLINK-V3 (loader)",
    "374e": "STLINK-V3E",
    "374f": "STLINK-V3",
    "3752": "ST-LINK/V2-1 (sin MSD)",
    "3753": "STLINK-V3 (2 VCP)",
    "3754": "STLINK-V3 (sin MSD)",
    "3755": "STLINK-V3 (loader)",
    "3757": "STLINK-V3PWR",
}
_ALNUM = set(string.ascii_letters + string.digits)


@dataclass
class StLink:
    serial: str
    pid: str
    model: str
    product: str
    bus: int
    devnum: int
    accessible: bool
    raw_serial_was_binary: bool = False
    # Qué hacer si no es accesible (permisos en Linux, driver en Windows).
    access_hint: str = "Sin permiso sobre el USB: revisá las reglas udev (ver README)."

    def to_public(self) -> Dict[str, object]:
        return {
            "serial": self.serial,
            "vid_pid": f"{ST_VID}:{self.pid}",
            "model": self.model,
            "product": self.product,
            "bus": self.bus,
            "devnum": self.devnum,
            "accessible": self.accessible,
            "serial_was_binary": self.raw_serial_was_binary,
            "access_hint": None if self.accessible else self.access_hint,
        }


def normalize_serial(raw: bytes) -> Tuple[str, bool]:
    """Convierte el número de serie de sysfs a un texto alfanumérico.

    Los ST-Link/V2 viejos reportan 12 bytes binarios como serie. El kernel los
    expone como caracteres Latin-1 codificados en UTF-8. OpenOCD (>= 0.11) los
    identifica por su representación hexadecimal, así que hacemos lo mismo.
    """
    text = raw.decode("utf-8", errors="surrogateescape").strip("\n\x00")
    if text and all(ch in _ALNUM for ch in text):
        return text, False
    data = bytearray()
    for ch in text:
        code = ord(ch)
        if 0xDC80 <= code <= 0xDCFF:   # byte inválido en UTF-8 (surrogateescape)
            code -= 0xDC00
        data.append(code & 0xFF)
    return data.hex().upper(), True


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def list_stlinks() -> List[StLink]:
    """Los ST-Link conectados: sysfs en Linux (no requiere lsusb ni root), SetupAPI en Windows."""
    if sys.platform == "win32":
        return stlinks_from_windows(present_devices())
    return stlinks_from_sysfs()


def stlinks_from_sysfs(sysfs_root: Path = Path("/sys/bus/usb/devices"),
                       devfs_root: Path = Path("/dev/bus/usb")) -> List[StLink]:
    found: List[StLink] = []
    if not sysfs_root.is_dir():
        return found
    for dev in sorted(sysfs_root.iterdir()):
        vid = _read(dev / "idVendor")
        pid = _read(dev / "idProduct")
        if vid is None or pid is None:
            continue
        vid_s = vid.decode(errors="replace").strip().lower()
        pid_s = pid.decode(errors="replace").strip().lower()
        if vid_s != ST_VID or pid_s not in STLINK_PIDS:
            continue
        raw_serial = _read(dev / "serial") or b""
        serial, was_binary = normalize_serial(raw_serial)
        product = (_read(dev / "product") or b"").decode(errors="replace").strip()
        try:
            bus = int((_read(dev / "busnum") or b"0").strip())
            devnum = int((_read(dev / "devnum") or b"0").strip())
        except ValueError:
            bus, devnum = 0, 0
        node = devfs_root / f"{bus:03d}" / f"{devnum:03d}"
        accessible = os.access(node, os.R_OK | os.W_OK) if node.exists() else False
        found.append(StLink(serial=serial, pid=pid_s, model=STLINK_PIDS[pid_s], product=product,
                            bus=bus, devnum=devnum, accessible=accessible,
                            raw_serial_was_binary=was_binary))
    return found


WINDOWS_DRIVER_HINT = ("Falta el driver del ST-Link en Windows: instalá STM32CubeProgrammer o STM32CubeIDE "
                       "(traen el driver STSW-LINK009) y volvé a conectarlo.")


def stlinks_from_windows(devices: Sequence[WinUsbDevice]) -> List[StLink]:
    """ST-Link de la lista de USB de Windows. Sin driver no se puede usar (OpenOCD no lo abre)."""
    found = []
    for n, d in enumerate(devices):
        if d.vid != ST_VID or d.pid not in STLINK_PIDS:
            continue
        found.append(StLink(serial=d.serial or "", pid=d.pid, model=STLINK_PIDS[d.pid], product=d.name or "",
                            bus=0, devnum=n, accessible=d.has_driver, access_hint=WINDOWS_DRIVER_HINT))
    return found


def mock_stlinks() -> List[StLink]:
    return [
        StLink(serial="066DFF485550755187121832", pid="374b", model=STLINK_PIDS["374b"],
               product="STM32 STLink", bus=1, devnum=4, accessible=True),
        StLink(serial="002F00283232511639353236", pid="374e", model=STLINK_PIDS["374e"],
               product="STLINK-V3", bus=1, devnum=6, accessible=True),
    ]


class DfuListError(RuntimeError):
    pass


def list_dfu_devices(command: Sequence[str], env_extra: Optional[Dict[str, str]] = None,
                     timeout_s: float = 8.0) -> List[dfu_cmd.DfuDevice]:
    """Ejecuta `dfu-util -l` y devuelve los dispositivos en modo DFU."""
    try:
        result = run_capture(dfu_cmd.list_args(command), timeout=timeout_s, env_extra=env_extra)
    except FileNotFoundError:
        raise DfuListError(f"No se encontró dfu-util ('{command[0]}'). Instalalo con apt install dfu-util")
    except Exception as exc:
        raise DfuListError(f"No se pudo ejecutar dfu-util: {exc}")
    return dfu_cmd.parse_list_output(result.stdout + "\n" + result.stderr)
