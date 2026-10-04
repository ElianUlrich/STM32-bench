"""¿Este archivo de firmware es para esta placa?

Se usa al subir un archivo (para aceptar la asignación de placa) y otra vez
antes de grabarlo (el archivo pudo cambiar, por ejemplo por Syncthing).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import esp_project
from .chips import Chip
from .convert import ConversionError, convert_to_bin
from .files import FileValidationError
from .programmers.avrdude import AvrHexError, analyze_hex
from .programmers.esptool import EspImage, EspImageError, analyze_bin

ELF_MACHINES = {40: "ARM", 83: "AVR", 94: "Xtensa (ESP32)", 243: "RISC-V"}


@dataclass
class Compat:
    warnings: List[str] = field(default_factory=list)
    esp: Optional[EspImage] = None
    # Si el .bin es parte de un proyecto ESP-IDF: (carpeta, manifiesto, imagen).
    esp_project: Optional[Tuple[Path, "esp_project.Manifest", "esp_project.Image"]] = None


def elf_machine(path: Path) -> Optional[int]:
    head = path.read_bytes()[:20]
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return None
    return int.from_bytes(head[18:20], "little" if head[5] == 1 else "big")


def check_file_for_chip(path: Path, chip: Chip, filename: Optional[str] = None,
                        esp_address: Optional[int] = None, projects_dir: Optional[Path] = None) -> Compat:
    """Lanza FileValidationError si el archivo no corresponde a la placa.

    Con `projects_dir`, un .bin que es parte de un proyecto ESP-IDF (hay un
    flasher_args.json que lo lista) se valida con todo el proyecto y va en la
    dirección que dice el manifiesto.
    """
    name = filename or path.name
    ext = Path(name).suffix.lower()
    if ext not in chip.extensions():
        raise FileValidationError(
            f"{chip.name} acepta {', '.join(chip.extensions())}; '{name}' no es de ese tipo.")
    result = Compat()

    if chip.swd or chip.dfu:                     # STM32 (ARM)
        if ext == ".elf":
            machine = elf_machine(path)
            if machine != 40:
                raise FileValidationError(
                    f"Este .elf es para {ELF_MACHINES.get(machine, 'otra arquitectura')}, no para un STM32 (ARM).")
        if ext in (".elf", ".hex"):
            try:
                image = convert_to_bin(path, chip.flash_base, chip.flash_end, ext=ext)
            except ConversionError as exc:
                raise FileValidationError(str(exc))
            result.warnings += image.warnings
        else:
            size = path.stat().st_size
            if size > chip.flash_size:
                raise FileValidationError(
                    f"El .bin ({size} bytes) es más grande que la flash de {chip.name} ({chip.flash_size} bytes).")
        return result

    if chip.esptool:
        found = esp_project.find(projects_dir, path) if projects_dir is not None else None
        if found:
            folder, manifest, image = found
            esp_project.check(manifest, esp_project.reader(folder), chip.esptool)
            if esp_address is not None and esp_address != image.offset:
                raise FileValidationError(
                    f"{image.path} va en 0x{image.offset:X} según {esp_project.MANIFEST_NAME}: "
                    "en un proyecto ESP-IDF no se elige la dirección a mano.")
            result.esp = EspImage(address=image.offset, kind=image.label, chip_id=chip.esptool.image_chip_id)
            result.esp_project = found
            return result
        try:
            result.esp = analyze_bin(path.read_bytes(), chip.esptool, esp_address)
        except EspImageError as exc:
            raise FileValidationError(str(exc))
        result.warnings += result.esp.warnings
        return result

    if chip.avrdude:
        try:
            result.warnings += analyze_hex(path, chip.avrdude).warnings
        except AvrHexError as exc:
            raise FileValidationError(str(exc))
        return result

    return result
