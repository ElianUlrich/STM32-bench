"""ESP8266 / ESP32: comandos de esptool y análisis de binarios .bin.

Comandos: funciones puras que devuelven listas de argumentos (sin shell). El
puerto serie se valida contra los puertos detectados antes de llegar acá.

Análisis del .bin: las imágenes de ESP empiezan con el byte mágico 0xE9 y,
salvo en el ESP8266, su cabecera extendida trae el ID del chip para el que se
compilaron (offset 12). Eso permite rechazar un binario de ESP32-S3 que se
intenta grabar en un ESP32. También se detecta si el archivo es una imagen
"combinada" (bootloader + tabla de particiones + aplicación, se graba en 0x0)
o sólo la aplicación (se graba en la dirección de la app, normalmente 0x10000).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..chips import ESP_CHIPS, EsptoolParams

PORT_RE = re.compile(r"^(/dev/[A-Za-z0-9_./-]{1,64}|COM[0-9]{1,3}|MOCK-[A-Z0-9]{1,16})$")
IMAGE_MAGIC = 0xE9
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_MAGIC = b"\xaa\x50"

# ID de chip -> nombre legible (para mensajes de error).
CHIP_NAMES = {cid: name for name, (cid, _off) in ESP_CHIPS.items() if cid is not None}
PRETTY = {"esp8266": "ESP8266", "esp32": "ESP32", "esp32s2": "ESP32-S2", "esp32s3": "ESP32-S3",
          "esp32c2": "ESP32-C2", "esp32c3": "ESP32-C3", "esp32c5": "ESP32-C5", "esp32c6": "ESP32-C6",
          "esp32c61": "ESP32-C61", "esp32h2": "ESP32-H2", "esp32p4": "ESP32-P4"}


class EspImageError(ValueError):
    """El .bin no es válido para el chip elegido."""


@dataclass
class EspImage:
    address: int
    kind: str                     # "combinada" | "aplicación"
    chip_id: Optional[int]
    warnings: List[str] = field(default_factory=list)


def _chip_id_at(data: bytes, offset: int) -> Optional[int]:
    if len(data) < offset + 14 or data[offset] != IMAGE_MAGIC:
        return None
    return data[offset + 12] | (data[offset + 13] << 8)


def analyze_bin(data: bytes, params: EsptoolParams, address: Optional[int] = None) -> EspImage:
    """Determina dónde grabar el .bin y verifica que sea para este chip."""
    if len(data) < 32:
        raise EspImageError("El archivo es demasiado chico para ser un binario de ESP")
    expected = params.image_chip_id
    boot = params.bootloader_offset
    warnings: List[str] = []

    is_merged = (len(data) > PARTITION_TABLE_OFFSET + 2
                 and data[PARTITION_TABLE_OFFSET:PARTITION_TABLE_OFFSET + 2] == PARTITION_MAGIC
                 and len(data) > boot and data[boot] == IMAGE_MAGIC)
    if is_merged:
        kind, header_at, auto_address = "combinada", boot, 0x0
    elif data[0] == IMAGE_MAGIC:
        kind, header_at, auto_address = "aplicación", 0, params.app_address
    else:
        # ¿Es una imagen combinada para otro chip (bootloader en otro offset)?
        for other, (cid, off) in ESP_CHIPS.items():
            if off != boot and len(data) > off and data[off] == IMAGE_MAGIC:
                raise EspImageError(
                    f"Parece un binario combinado para un chip con el bootloader en 0x{off:X} "
                    f"(como {PRETTY.get(other, other)}), no para {PRETTY[params.chip]}.")
        raise EspImageError("No parece un binario de ESP: falta la firma 0xE9 al comienzo de la imagen.")

    chip_id = None
    if expected is not None:
        chip_id = _chip_id_at(data, header_at)
        if chip_id != expected:
            found = PRETTY.get(CHIP_NAMES.get(chip_id, ""), f"ID {chip_id}")
            raise EspImageError(
                f"Este binario fue compilado para {found}, no para {PRETTY[params.chip]}. "
                "Elegí la placa correcta o recompilá para este chip.")

    final_address = auto_address
    if address is not None:
        if address != auto_address:
            warnings.append(f"Se usa la dirección indicada 0x{address:X} en lugar de la detectada "
                            f"0x{auto_address:X} (imagen {kind}).")
        final_address = address
    return EspImage(address=final_address, kind=kind, chip_id=chip_id, warnings=warnings)


# --------------------------------------------------------------------------- comandos

def _check_port(port: str) -> str:
    if not PORT_RE.fullmatch(port or ""):
        raise EspImageError(f"Puerto serie inválido: '{port}'")
    return port


def _base(command: Sequence[str], params: EsptoolParams, port: str, chip: Optional[str] = None,
          after: str = "hard_reset") -> List[str]:
    return list(command) + [
        "--chip", chip or params.chip,
        "--port", _check_port(port),
        "--baud", str(int(params.baud)),
        "--before", params.before,
        "--after", after,
    ]


def flash_args(command: Sequence[str], params: EsptoolParams, port: str, firmware: Path, address: int) -> List[str]:
    """Graba el .bin (esptool verifica el hash al terminar) y resetea el chip."""
    return _base(command, params, port) + [
        "write_flash", "--flash_mode", "keep", "--flash_freq", "keep", "--flash_size", "keep",
        f"0x{address:X}", str(firmware)]


def verify_args(command: Sequence[str], params: EsptoolParams, port: str, firmware: Path, address: int) -> List[str]:
    return _base(command, params, port) + ["verify_flash", f"0x{address:X}", str(firmware)]


FLASH_SETTING_RE = re.compile(r"^(qio|qout|dio|dout|opi|keep|detect|[0-9]{2,3}m|[0-9]{1,3}MB)$")


def _pairs(pairs: Sequence[Tuple[int, Path]]) -> List[str]:
    if not pairs:
        raise EspImageError("No hay archivos para grabar")
    out: List[str] = []
    for address, path in pairs:
        out += [f"0x{address:X}", str(path)]
    return out


def write_files_args(command: Sequence[str], params: EsptoolParams, port: str,
                     pairs: Sequence[Tuple[int, Path]], flash_mode: str = "keep",
                     flash_freq: str = "keep", flash_size: str = "keep") -> List[str]:
    """Varias imágenes en una sola pasada (proyecto ESP-IDF), con los parámetros de flash
    de flasher_args.json. esptool verifica el hash de cada una y resetea al final."""
    for value in (flash_mode, flash_freq, flash_size):
        if not FLASH_SETTING_RE.fullmatch(value):
            raise EspImageError(f"Parámetro de flash inválido: '{value}'")
    return _base(command, params, port) + [
        "write_flash", "--flash_mode", flash_mode, "--flash_freq", flash_freq, "--flash_size", flash_size,
    ] + _pairs(pairs)


def verify_files_args(command: Sequence[str], params: EsptoolParams, port: str,
                      pairs: Sequence[Tuple[int, Path]]) -> List[str]:
    return _base(command, params, port) + ["verify_flash"] + _pairs(pairs)


def erase_args(command: Sequence[str], params: EsptoolParams, port: str) -> List[str]:
    return _base(command, params, port) + ["erase_flash"]


def reset_args(command: Sequence[str], params: EsptoolParams, port: str) -> List[str]:
    """Entra al bootloader, lee la MAC y hace un reset por hardware (RTS)."""
    return _base(command, params, port) + ["read_mac"]


def info_args(command: Sequence[str], params: EsptoolParams, port: str) -> List[str]:
    """Identifica el chip conectado (--chip auto: muestra lo que realmente hay)."""
    return _base(command, params, port, chip="auto") + ["flash_id"]
