"""Arduino / AVR: comandos de avrdude y verificación de archivos .hex.

avrdude compara la firma del chip conectado con la parte indicada (-p) y
aborta si no coincide, así que no puede grabar un Uno con el firmware de un
Mega. Además, acá se verifica que el .hex entre en la flash disponible (sin
pisar el bootloader).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence

from ..chips import AvrdudeParams
from .esptool import PORT_RE


class AvrHexError(ValueError):
    """El .hex no es válido para la placa elegida."""


@dataclass
class AvrImage:
    size: int                  # bytes de programa (última dirección + 1)
    warnings: List[str] = field(default_factory=list)


def analyze_hex(path: Path, params: AvrdudeParams) -> AvrImage:
    from intelhex import IntelHex, IntelHexError

    ih = IntelHex()
    try:
        ih.loadhex(str(path))
    except (IntelHexError, ValueError) as exc:
        raise AvrHexError(f"Intel HEX inválido: {exc}")
    if ih.minaddr() is None:
        raise AvrHexError("El .hex no contiene datos")
    end = ih.maxaddr() + 1
    limit = params.max_program_bytes
    if end > params.flash_kb * 1024:
        raise AvrHexError(
            f"El programa llega hasta 0x{end:X} ({end} bytes) y la flash del {params.part} tiene "
            f"{params.flash_kb} KB: este .hex es para un chip más grande.")
    if end > limit:
        raise AvrHexError(
            f"El programa ocupa {end} bytes y el máximo con el bootloader es {limit} bytes: "
            "pisaría el bootloader.")
    warnings = []
    if ih.minaddr() != 0:
        warnings.append(f"El programa no empieza en 0x0 (empieza en 0x{ih.minaddr():X}).")
    return AvrImage(size=end, warnings=warnings)


def _base(command: Sequence[str], params: AvrdudeParams, port: str) -> List[str]:
    args = list(command) + ["-p", params.part, "-c", params.programmer]
    if params.is_isp:
        args += ["-P", "usb"]
    else:
        if not PORT_RE.fullmatch(port or ""):
            raise AvrHexError(f"Puerto serie inválido: '{port}'")
        args += ["-P", port]
    if params.baud:
        args += ["-b", str(int(params.baud))]
    return args


def flash_args(command: Sequence[str], params: AvrdudeParams, port: str, firmware: Path) -> List[str]:
    """Graba y verifica (avrdude verifica por defecto después de escribir)."""
    args = _base(command, params, port)
    if params.no_auto_erase:
        args.append("-D")
    return args + ["-U", f"flash:w:{firmware}:i"]


def verify_args(command: Sequence[str], params: AvrdudeParams, port: str, firmware: Path) -> List[str]:
    return _base(command, params, port) + ["-U", f"flash:v:{firmware}:i"]


def erase_args(command: Sequence[str], params: AvrdudeParams, port: str) -> List[str]:
    """Borrado total (sólo con programador ISP; el bootloader no lo permite)."""
    return _base(command, params, port) + ["-e"]


def info_args(command: Sequence[str], params: AvrdudeParams, port: str) -> List[str]:
    """Sin -U: avrdude se conecta, lee la firma del chip y la compara con -p."""
    return _base(command, params, port)
