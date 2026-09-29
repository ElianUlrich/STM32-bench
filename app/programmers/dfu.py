"""Construcción de comandos de dfu-util y parseo de `dfu-util -l`.

Funciones puras: devuelven listas de argumentos (sin shell). Los valores que
vienen del usuario (número de serie del dispositivo) se validan con lista
blanca y además se exige que sea uno de los dispositivos detectados.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

DFU_SERIAL_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
VID_PID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")

# Ejemplo de línea:
# Found DFU: [0483:df11] ver=0200, devnum=7, cfg=1, intf=0, path="1-1.3", alt=0,
#   name="@Internal Flash  /0x08000000/8*128Kg", serial="3957377B3432"
_FOUND_RE = re.compile(
    r'^Found DFU: \[(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})\]'
    r'.*?devnum=(?P<devnum>\d+)'
    r'.*?path="(?P<path>[^"]*)"'
    r'.*?alt=(?P<alt>\d+)'
    r'.*?name="(?P<name>[^"]*)"'
    r'(?:.*?serial="(?P<serial>[^"]*)")?')


class DfuCommandError(ValueError):
    pass


@dataclass
class DfuDevice:
    vid_pid: str
    serial: str
    path: str
    devnum: int
    alts: List[Dict[str, object]] = field(default_factory=list)

    def to_public(self) -> Dict[str, object]:
        return {"vid_pid": self.vid_pid, "serial": self.serial, "path": self.path,
                "devnum": self.devnum, "alts": self.alts}


def parse_list_output(text: str) -> List[DfuDevice]:
    """Agrupa las líneas 'Found DFU' por dispositivo (una línea por alt)."""
    devices: Dict[tuple, DfuDevice] = {}
    for raw in text.splitlines():
        line = raw.strip()
        match = _FOUND_RE.match(line)
        if not match:
            continue
        vid_pid = f"{match['vid'].lower()}:{match['pid'].lower()}"
        serial = match["serial"] or ""
        key = (vid_pid, match["path"], serial)
        dev = devices.get(key)
        if dev is None:
            dev = DfuDevice(vid_pid=vid_pid, serial=serial, path=match["path"], devnum=int(match["devnum"]))
            devices[key] = dev
        dev.alts.append({"alt": int(match["alt"]), "name": match["name"]})
    return list(devices.values())


@dataclass(frozen=True)
class DfuTarget:
    """Dispositivo DFU concreto sobre el que se opera."""
    command: tuple              # ("dfu-util",) o el simulador
    vid_pid: str
    alt: int
    serial: Optional[str] = None


def _common(target: DfuTarget) -> List[str]:
    if not VID_PID_RE.fullmatch(target.vid_pid):
        raise DfuCommandError("VID:PID inválido")
    if not 0 <= int(target.alt) <= 255:
        raise DfuCommandError("alt inválido")
    args = list(target.command) + ["-d", target.vid_pid, "-a", str(int(target.alt))]
    if target.serial:
        if not DFU_SERIAL_RE.fullmatch(target.serial):
            raise DfuCommandError("Número de serie DFU inválido")
        args += ["-S", target.serial]
    return args


def _addr(address: int) -> str:
    if not 0 <= address <= 0xFFFFFFFF:
        raise DfuCommandError("Dirección fuera de rango")
    return f"0x{address:08X}"


def list_args(command: Sequence[str]) -> List[str]:
    return list(command) + ["-l"]


def download_args(target: DfuTarget, address: int, firmware: Path, leave: bool) -> List[str]:
    """Escribe un .bin en `address`. Con leave, el bootloader salta a la aplicación."""
    spec = _addr(address) + (":leave" if leave else "")
    return _common(target) + ["-s", spec, "-D", str(firmware)]


def upload_args(target: DfuTarget, address: int, length: int, out_file: Path) -> List[str]:
    """Lee `length` bytes desde `address` a un archivo (para verificar)."""
    if length <= 0:
        raise DfuCommandError("Longitud inválida")
    return _common(target) + ["-s", f"{_addr(address)}:{int(length)}", "-U", str(out_file)]


def mass_erase_args(target: DfuTarget, address: int) -> List[str]:
    """Borrado completo de la flash (DfuSe mass-erase; requiere dfu-util >= 0.9)."""
    return _common(target) + ["-s", f"{_addr(address)}:mass-erase:force"]


def leave_args(target: DfuTarget, address: int) -> List[str]:
    """Solo salir del bootloader y saltar a `address` (sin escribir)."""
    return _common(target) + ["-s", f"{_addr(address)}:leave"]


def version_args(command: Sequence[str]) -> List[str]:
    return list(command) + ["-V"]


# Mensajes que indican que la descarga terminó aunque dfu-util devuelva error
# al salir con :leave (el dispositivo se desconecta antes de responder).
DOWNLOAD_DONE_MARKERS = ("File downloaded successfully", "Download done.")


def leave_error_is_benign(output: str) -> bool:
    """True si el error de salida ocurrió DESPUÉS de completar la descarga."""
    return any(marker in output for marker in DOWNLOAD_DONE_MARKERS)
