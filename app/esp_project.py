"""Proyectos ESP-IDF: qué grabar y dónde, según build/flasher_args.json.

`idf.py build` deja en build/ un flasher_args.json con la lista exacta de
imágenes (bootloader, tabla de particiones, programa y a veces datos) y la
dirección de cada una. La web lee ese archivo y sube sólo esos .bin (unos KB
o MB, contra cientos de MB de build/), y el banco graba cada uno donde dice.

Controles, antes de guardar el proyecto y otra vez antes de grabar:
  * el proyecto tiene que ser para el chip de la placa (chip del manifiesto y
    el ID dentro del bootloader y del programa);
  * rutas de la lista blanca (nada de "..", absolutas ni ocultas), sólo .bin,
    y nada cifrado (necesitaría --encrypt y claves del chip);
  * las imágenes no se pisan entre sí y entran en la flash declarada;
  * cada imagen que no sea el bootloader o la tabla cae dentro de una partición
    (y el programa entra en la suya), y **ninguna toca una partición NVS**: ahí
    el programa guarda sus datos (presets, ajustes, WiFi) y se perderían.
"""
from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple

from .chips import ESP_CHIPS, EsptoolParams
from .files import FileValidationError, is_safe_component

MANIFEST_NAME = "flasher_args.json"
MANIFEST_MAX_BYTES = 64 * 1024
MAX_IMAGES = 16
SECTOR = 0x1000
MAX_FLASH = 256 * 1024 * 1024
FLASH_MODES = ("qio", "qout", "dio", "dout", "opi", "keep")
FLASH_FREQ_RE = re.compile(r"^([0-9]{2,3}m|keep)$")
FLASH_SIZE_RE = re.compile(r"^([0-9]{1,3}MB|keep|detect)$")
ROLES = ("bootloader", "partition-table", "app")      # claves de flasher_args.json
ROLE_LABELS = {"bootloader": "gestor de arranque", "partition-table": "tabla de particiones",
               "app": "programa", "data": "datos"}
IMAGE_MAGIC = 0xE9
PART_MAGIC = b"\xaa\x50"
PART_MD5_MAGIC = b"\xeb\xeb"
PART_ENTRY = struct.Struct("<2sBBII16sI")
PART_TABLE_MAX = 0xC00
# Particiones de datos que nunca se escriben: NVS (subtipo 2) y sus claves (4).
PROTECTED_DATA_SUBTYPES = {0x02: "NVS", 0x04: "claves de NVS"}
PRETTY = {"esp8266": "ESP8266", "esp32": "ESP32", "esp32s2": "ESP32-S2", "esp32s3": "ESP32-S3",
          "esp32c2": "ESP32-C2", "esp32c3": "ESP32-C3", "esp32c5": "ESP32-C5", "esp32c6": "ESP32-C6",
          "esp32c61": "ESP32-C61", "esp32h2": "ESP32-H2", "esp32p4": "ESP32-P4"}
CHIP_BY_ID = {cid: name for name, (cid, _off) in ESP_CHIPS.items() if cid is not None}


class EspProjectError(FileValidationError):
    """Proyecto ESP-IDF inválido o peligroso de grabar (mensaje para el usuario)."""


@dataclass(frozen=True)
class Image:
    offset: int
    path: str          # relativa a la carpeta del manifiesto, con "/"
    role: str          # bootloader | partition-table | app | data

    @property
    def label(self) -> str:
        return ROLE_LABELS[self.role]


@dataclass(frozen=True)
class Manifest:
    chip: str
    flash_mode: str
    flash_freq: str
    flash_size: str
    images: Tuple[Image, ...]      # ordenadas por dirección

    def image_for(self, rel_path: str) -> Optional[Image]:
        return next((i for i in self.images if i.path == rel_path), None)

    def role(self, role: str) -> Optional[Image]:
        return next((i for i in self.images if i.role == role), None)

    @property
    def flash_bytes(self) -> Optional[int]:
        m = re.fullmatch(r"([0-9]+)MB", self.flash_size)
        return int(m.group(1)) * 1024 * 1024 if m else None


@dataclass(frozen=True)
class Partition:
    name: str
    type: int
    subtype: int
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def protected(self) -> Optional[str]:
        return PROTECTED_DATA_SUBTYPES.get(self.subtype) if self.type == 0x01 else None


# --------------------------------------------------------------------- manifiesto

def _check_path(path: object) -> str:
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        raise EspProjectError(f"Ruta no permitida en {MANIFEST_NAME}: '{path}'")
    parts = path.split("/")
    if len(parts) > 4 or any(p in (".", "..") or not is_safe_component(p) for p in parts):
        raise EspProjectError(f"Ruta no permitida en {MANIFEST_NAME}: '{path}'")
    if PurePosixPath(path).suffix.lower() != ".bin":
        raise EspProjectError(f"Sólo se graban archivos .bin: '{path}'")
    return path


def _offset(text: object) -> int:
    try:
        value = int(str(text), 0)
    except ValueError:
        raise EspProjectError(f"Dirección inválida en {MANIFEST_NAME}: '{text}'")
    if not 0 <= value < MAX_FLASH or value % SECTOR:
        raise EspProjectError(f"Dirección fuera de rango o no alineada a 4 KB: '{text}'")
    return value


def parse_manifest(data: bytes) -> Manifest:
    """Lee y valida un flasher_args.json de ESP-IDF."""
    if len(data) > MANIFEST_MAX_BYTES:
        raise EspProjectError(f"{MANIFEST_NAME} es demasiado grande")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise EspProjectError(f"{MANIFEST_NAME} no es un JSON válido")
    if not isinstance(doc, dict):
        raise EspProjectError(f"{MANIFEST_NAME} no tiene el formato de ESP-IDF")
    flash_files = doc.get("flash_files")
    if not isinstance(flash_files, dict) or not flash_files:
        raise EspProjectError(f"{MANIFEST_NAME} no lista archivos para grabar (falta flash_files)")
    if len(flash_files) > MAX_IMAGES:
        raise EspProjectError(f"{MANIFEST_NAME} lista demasiados archivos")

    extra = doc.get("extra_esptool_args") or {}
    chip = extra.get("chip") if isinstance(extra, dict) else None
    if chip not in ESP_CHIPS:
        raise EspProjectError(f"{MANIFEST_NAME} no dice para qué chip es (extra_esptool_args.chip)")
    settings = doc.get("flash_settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    mode = str(settings.get("flash_mode", "keep"))
    freq = str(settings.get("flash_freq", "keep"))
    size = str(settings.get("flash_size", "keep"))
    if mode not in FLASH_MODES or not FLASH_FREQ_RE.fullmatch(freq) or not FLASH_SIZE_RE.fullmatch(size):
        raise EspProjectError(f"Parámetros de flash inválidos en {MANIFEST_NAME}: {mode} {freq} {size}")

    roles: Dict[str, str] = {}
    for role in ROLES:
        entry = doc.get(role)
        if isinstance(entry, dict) and entry.get("file"):
            if str(entry.get("encrypted", "false")).lower() == "true":
                raise EspProjectError("El proyecto usa flash cifrada: el banco no graba imágenes cifradas")
            roles[str(entry["file"])] = role
    if str(doc.get("encrypted", "false")).lower() == "true":
        raise EspProjectError("El proyecto usa flash cifrada: el banco no graba imágenes cifradas")

    images: List[Image] = []
    seen = set()
    for key, path in flash_files.items():
        path = _check_path(path)
        if path in seen:
            raise EspProjectError(f"'{path}' aparece dos veces en {MANIFEST_NAME}")
        seen.add(path)
        images.append(Image(offset=_offset(key), path=path, role=roles.get(path, "data")))
    images.sort(key=lambda i: i.offset)
    return Manifest(chip=chip, flash_mode=mode, flash_freq=freq, flash_size=size, images=tuple(images))


def dump_manifest(m: Manifest) -> bytes:
    """Manifiesto normalizado (mismo formato que ESP-IDF, sólo con lo que se usa)."""
    doc: Dict[str, object] = {
        "flash_settings": {"flash_mode": m.flash_mode, "flash_size": m.flash_size, "flash_freq": m.flash_freq},
        "flash_files": {hex(i.offset): i.path for i in m.images},
        "extra_esptool_args": {"chip": m.chip},
    }
    for i in m.images:
        if i.role != "data":
            doc[i.role] = {"offset": hex(i.offset), "file": i.path, "encrypted": "false"}
    return (json.dumps(doc, indent=4) + "\n").encode("utf-8")


# ------------------------------------------------------------ tabla de particiones

def parse_partition_table(data: bytes) -> List[Partition]:
    parts: List[Partition] = []
    for pos in range(0, min(len(data), PART_TABLE_MAX) - PART_ENTRY.size + 1, PART_ENTRY.size):
        magic, ptype, subtype, offset, size, name, _flags = PART_ENTRY.unpack_from(data, pos)
        if magic == PART_MD5_MAGIC or magic == b"\xff\xff":
            break
        if magic != PART_MAGIC:
            raise EspProjectError("La tabla de particiones está dañada (firma inválida)")
        label = name.split(b"\0", 1)[0].decode("ascii", "replace")
        parts.append(Partition(label, ptype, subtype, offset, size))
    if not parts:
        raise EspProjectError("La tabla de particiones está vacía o no es una tabla de ESP-IDF")
    return parts


# -------------------------------------------------------------------- verificación

def _chip_id(data: bytes) -> Optional[int]:
    if len(data) < 14 or data[0] != IMAGE_MAGIC:
        return None
    return data[12] | (data[13] << 8)


def check(m: Manifest, read: Callable[[str], bytes], params: EsptoolParams) -> List[Partition]:
    """Valida el proyecto para esta placa. `read(ruta)` devuelve el contenido de cada .bin.

    Devuelve la tabla de particiones. Lanza EspProjectError con un mensaje claro.
    """
    board = PRETTY.get(params.chip, params.chip)
    if m.chip != params.chip:
        raise EspProjectError(f"El proyecto es para {PRETTY.get(m.chip, m.chip)} ({MANIFEST_NAME}), "
                              f"no para {board}. Elegí la placa correcta o recompilá.")
    table = m.role("partition-table")
    if table is None:
        raise EspProjectError(f"{MANIFEST_NAME} no incluye la tabla de particiones")
    boot = m.role("bootloader")
    if boot is not None and boot.offset != params.bootloader_offset:
        raise EspProjectError(f"El bootloader va en 0x{boot.offset:X}, pero en {board} va en "
                              f"0x{params.bootloader_offset:X}: el proyecto no es para esta placa.")

    sizes: Dict[str, int] = {}
    partitions: List[Partition] = []
    for img in m.images:
        try:
            data = read(img.path)
        except (FileNotFoundError, KeyError):
            raise EspProjectError(f"Falta {img.path} (lo lista {MANIFEST_NAME}). Volvé a compilar.")
        if not data:
            raise EspProjectError(f"{img.path} está vacío")
        sizes[img.path] = len(data)
        if img.role in ("bootloader", "app"):
            cid = _chip_id(data)
            if cid is None:
                raise EspProjectError(f"{img.path} no es una imagen de ESP (falta la firma 0xE9)")
            if params.image_chip_id is not None and cid != params.image_chip_id:
                found = PRETTY.get(CHIP_BY_ID.get(cid, ""), f"ID {cid}")
                raise EspProjectError(f"{img.path} fue compilado para {found}, no para {board}.")
        elif img.role == "partition-table":
            partitions = parse_partition_table(data)

    flash = m.flash_bytes
    previous: Optional[Image] = None
    for img in m.images:                                   # ordenadas por dirección
        end = img.offset + sizes[img.path]
        if flash is not None and end > flash:
            raise EspProjectError(f"{img.path} (0x{img.offset:X}) no entra en la flash de {m.flash_size}")
        if previous is not None and img.offset < previous.offset + sizes[previous.path]:
            raise EspProjectError(f"{img.path} se superpone con {previous.path}")
        previous = img
        if img.role in ("bootloader", "partition-table"):
            continue
        for p in partitions:
            if p.protected and img.offset < p.end and end > p.offset:
                raise EspProjectError(
                    f"{img.path} (0x{img.offset:X}) pisaría la partición {p.protected} '{p.name}' "
                    f"(0x{p.offset:X}-0x{p.end - 1:X}), donde el programa guarda sus datos "
                    "(presets, ajustes, WiFi). No se graba.")
        home = next((p for p in partitions if p.offset <= img.offset < p.end), None)
        if home is None:
            raise EspProjectError(f"{img.path} (0x{img.offset:X}) no cae dentro de ninguna partición")
        if end > home.end:
            raise EspProjectError(f"El {img.label} {img.path} ({sizes[img.path] // 1024} KB) no entra en la "
                                  f"partición '{home.name}' ({home.size // 1024} KB)")
        if img.role == "app" and home.type != 0x00:
            raise EspProjectError(f"El programa cae en '{home.name}', que no es una partición de aplicación")
    return partitions


# ------------------------------------------------------------- en el disco

def load(folder: Path) -> Manifest:
    path = folder / MANIFEST_NAME
    if path.stat().st_size > MANIFEST_MAX_BYTES:
        raise EspProjectError(f"{MANIFEST_NAME} es demasiado grande")
    return parse_manifest(path.read_bytes())


def reader(folder: Path) -> Callable[[str], bytes]:
    """Lee los .bin del proyecto sin salir de su carpeta (ni por symlinks)."""
    root = Path(os.path.realpath(folder))

    def read(rel: str) -> bytes:
        target = Path(os.path.realpath(root.joinpath(*rel.split("/"))))
        try:
            target.relative_to(root)
        except ValueError:
            raise EspProjectError(f"{rel} sale de la carpeta del proyecto")
        return target.read_bytes()

    return read


def find(projects_dir: Path, firmware: Path) -> Optional[Tuple[Path, Manifest, Image]]:
    """¿El archivo es parte de un proyecto ESP-IDF? Busca flasher_args.json hacia arriba.

    Si la carpeta tiene un manifiesto que no lista el archivo, no es parte del
    proyecto (None). Si el manifiesto es inválido, se lanza el error: grabar
    "a ciegas" un archivo de un proyecto con particiones propias puede pisar NVS.
    """
    root = Path(os.path.realpath(projects_dir))
    path = Path(os.path.realpath(firmware))
    current = path.parent
    while True:
        try:
            current.relative_to(root)
        except ValueError:
            return None
        if (current / MANIFEST_NAME).is_file():
            manifest = load(current)
            image = manifest.image_for(path.relative_to(current).as_posix())
            return (current, manifest, image) if image else None
        if current == root:
            return None
        current = current.parent


def annotate(projects_dir: Path, projects: List[Dict]) -> None:
    """Marca en el listado los .bin que son parte de un proyecto ESP-IDF (dirección y rol)."""
    for project in projects:
        for f in project["files"]:
            if f["kind"] != "bin":
                continue
            try:
                found = find(projects_dir, projects_dir / f["path"])
            except (EspProjectError, OSError):
                continue
            if found:
                _folder, manifest, image = found
                f["esp"] = {"address": hex(image.offset), "role": image.role, "files": len(manifest.images)}
