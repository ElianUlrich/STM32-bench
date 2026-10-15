"""Leer el micro: qué tiene grabado y si coincide con algún archivo de la placa.

Dos partes:
  * análisis (funciones puras): cuánto de la flash está usado, la tabla de
    vectores de un Cortex-M, si el contenido coincide con un archivo, y en los
    ESP32 la tabla de particiones y la descripción de cada aplicación
    (esp_app_desc_t: proyecto, versión, fecha de compilación, versión de IDF);
  * almacén: cada lectura queda en el directorio de estado (informe + los .bin
    leídos) para mostrarla, descargarla o guardarla como copia de respaldo.

Comparar contra los archivos de la placa permite decir "tiene grabado
04_contador_serie.elf" sin que el micro sepa nada de nombres de archivo.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import esp_project
from .files import FileValidationError

ERASED = b"\xff"
APP_DESC_MAGIC = 0xABCD5432
APP_DESC_OFFSET = 0x20            # cabecera de imagen (24) + cabecera del primer segmento (8)
APP_DESC_SIZE = 256
ESP_IMAGE_MAGIC = 0xE9
PART_TABLE_SIZE = 0xC00
RID_RE = re.compile(r"^[0-9a-f]{12}$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
KIND_ORDER = {".elf": 0, ".hex": 1, ".bin": 2}


# ------------------------------------------------------------------ análisis

def used_length(data: bytes) -> int:
    """Bytes hasta el último que no está borrado (0xFF)."""
    return len(data.rstrip(ERASED))


def round_up(n: int, step: int = 16) -> int:
    return (n + step - 1) // step * step


def analyze_flash(data: bytes, base: int, vectors: bool = True) -> Dict:
    """Tamaño leído, cuánto está usado y (Cortex-M) la tabla de vectores, si es creíble."""
    used = used_length(data)
    table = None
    if vectors and used >= 8:
        sp, reset = struct.unpack_from("<II", data, 0)
        # Pila inicial en RAM y reset dentro de la flash leída, en modo Thumb (impar).
        if 0x10000000 <= sp <= 0x3FFFFFFF and sp % 4 == 0 and base <= reset < base + len(data) and reset & 1:
            table = {"sp": f"0x{sp:08X}", "reset": f"0x{reset:08X}"}
    return {"size": len(data), "used": used, "empty": used == 0, "base": f"0x{base:08X}", "vectors": table}


def match_kind(flash: bytes, offset: int, content: bytes) -> Optional[str]:
    """"exact": la flash es el archivo y el resto está borrado; "prefix": el archivo está,
    pero hay más datos en otra parte; None: no coincide (o el archivo no dice nada)."""
    if not content or not content.strip(ERASED) or offset < 0 or offset + len(content) > len(flash):
        return None
    if flash[offset:offset + len(content)] != content:
        return None
    rest = used_length(flash[:offset]) + used_length(flash[offset + len(content):])
    return "exact" if rest == 0 else "prefix"


def parse_app_desc(image: bytes) -> Optional[Dict]:
    """esp_app_desc_t al comienzo del primer segmento de una aplicación de ESP-IDF."""
    end = APP_DESC_OFFSET + APP_DESC_SIZE
    if len(image) < end or image[0] != ESP_IMAGE_MAGIC:
        return None
    if struct.unpack_from("<I", image, APP_DESC_OFFSET)[0] != APP_DESC_MAGIC:
        return None

    def text(start: int, size: int) -> str:
        raw = image[APP_DESC_OFFSET + start:APP_DESC_OFFSET + start + size]
        return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()

    return {"version": text(16, 32), "project": text(48, 32), "time": text(80, 16), "date": text(96, 16),
            "idf": text(112, 32), "sha256": image[APP_DESC_OFFSET + 144:APP_DESC_OFFSET + 176].hex()}


def find_partition_table(region: bytes) -> Optional[int]:
    """Dirección de la tabla de particiones dentro de los primeros KB leídos (alineada a 4 KB)."""
    for offset in range(0x1000, len(region) - PART_TABLE_SIZE + 1, 0x1000):
        if region[offset:offset + 2] != esp_project.PART_MAGIC:
            continue
        try:
            esp_project.parse_partition_table(region[offset:offset + PART_TABLE_SIZE])
        except esp_project.EspProjectError:
            continue
        return offset
    return None


def esp_image_length(data: bytes) -> Optional[int]:
    """Largo exacto de una imagen de ESP (segmentos + suma de control + SHA-256), o None."""
    if len(data) < 24 or data[0] != ESP_IMAGE_MAGIC:
        return None
    pos = 24
    for _ in range(data[1]):
        if pos + 8 > len(data):
            return None
        length = struct.unpack_from("<I", data, pos + 4)[0]
        pos += 8 + length
        if pos > len(data):
            return None
    pos = round_up(pos + 1)                     # la suma de control cierra el relleno a 16 bytes
    if data[23] == 1:                           # hash_appended
        pos += 32
    return pos if pos <= len(data) else None


def image_length(data: bytes) -> int:
    """Cuánto guardar de una región leída: la imagen exacta si se reconoce, si no lo usado."""
    exact = esp_image_length(data)
    return exact if exact is not None else min(len(data), round_up(used_length(data)))


# ---------------------------------------------------------- coincidencias

@dataclass(frozen=True)
class Candidate:
    """Un archivo de la placa, ya convertido a lo que quedaría en la flash."""
    path: str                   # relativa al directorio de proyectos
    address: int                # dirección absoluta donde se graba
    data: bytes
    desc: Optional[Dict] = None  # ESP: descripción de la aplicación


def _preference(path: str, last_flashed: Optional[str]) -> tuple:
    return (0 if path == last_flashed else 1, KIND_ORDER.get(Path(path).suffix.lower(), 3), path)


def find_matches(flash: bytes, base: int, candidates: Sequence[Candidate],
                 last_flashed: Optional[str] = None) -> List[Dict]:
    """Archivos que están en la flash leída: primero los exactos; dentro de cada grupo,
    lo último que se grabó desde el banco, después .elf, .hex y .bin."""
    found = []
    for c in candidates:
        kind = match_kind(flash, c.address - base, c.data)
        if kind:
            found.append({"path": c.path, "kind": kind})
    found.sort(key=lambda m: (m["kind"] != "exact",) + _preference(m["path"], last_flashed))
    return found


def match_apps(apps: Sequence[Dict], candidates: Sequence[Candidate],
               last_flashed: Optional[str] = None, heads: Optional[Dict[str, bytes]] = None) -> List[Dict]:
    """ESP: aplicaciones de la flash cuya descripción (SHA del .elf, proyecto, versión,
    fecha) coincide con la de un archivo: se identifica sin leer el programa entero.
    Una aplicación sin descripción se compara por los bytes leídos (`heads`, por partición)."""
    found = []
    for app in apps:
        head = (heads or {}).get(app["partition"], b"")
        n = min(len(head), used_length(head))
        for c in candidates:
            d = c.desc
            if app.get("sha256"):
                same = bool(d) and all(d[k] == app[k] for k in ("sha256", "project", "version", "date", "time"))
            else:
                same = not d and n >= 64 and c.data[:n] == head[:n]
            if same:
                found.append({"path": c.path, "kind": "app", "partition": app["partition"]})
    found.sort(key=lambda m: _preference(m["path"], last_flashed))
    return found


def summarize(matches: Sequence[Dict], empty: bool) -> Dict:
    if empty:
        return {"state": "empty", "file": None, "text": "La flash está vacía"}
    if not matches:
        return {"state": "unknown", "file": None,
                "text": "Firmware desconocido: no coincide con ningún archivo de esta placa"}
    best = matches[0]
    name = Path(best["path"]).name
    if best["kind"] == "prefix":
        return {"state": "partial", "file": best["path"],
                "text": f"Coincide con {name}, y hay más datos en la flash"}
    return {"state": "match", "file": best["path"], "text": f"Tiene grabado {name}"}


# --------------------------------------------------------------------- almacén

class ReadbackStore:
    """Una carpeta por lectura con report.json y los archivos leídos; se guardan las últimas."""

    def __init__(self, root: Path, keep: int = 4) -> None:
        self.root = Path(root)
        self.keep = keep

    def create(self) -> tuple:
        self.root.mkdir(parents=True, exist_ok=True)
        rid = secrets.token_hex(6)
        folder = self.root / rid
        folder.mkdir()
        return rid, folder

    def save(self, rid: str, report: Dict) -> None:
        folder = self._folder(rid)
        tmp = folder / "report.json.tmp"
        tmp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, folder / "report.json")
        self._prune()

    def discard(self, rid: str) -> None:
        shutil.rmtree(self._folder(rid), ignore_errors=True)

    def latest(self) -> Optional[Dict]:
        reports = sorted(self.root.glob("*/report.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in reports:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        return None

    def get(self, rid: str) -> Dict:
        path = self._folder(rid) / "report.json"
        if not path.is_file():
            raise FileValidationError("No existe esa lectura (puede haberse borrado una más vieja)")
        return json.loads(path.read_text(encoding="utf-8"))

    def file(self, rid: str, name: str) -> Path:
        """Un archivo de la lectura: sólo los que figuran en su informe."""
        report = self.get(rid)
        if not FILE_NAME_RE.fullmatch(name or "") or name not in {f["name"] for f in report.get("files", [])}:
            raise FileValidationError("Ese archivo no es parte de la lectura")
        path = self._folder(rid) / name
        if not path.is_file():
            raise FileValidationError("El archivo de la lectura ya no está")
        return path

    def _folder(self, rid: str) -> Path:
        if not RID_RE.fullmatch(rid or ""):
            raise FileValidationError("Lectura inválida")
        return self.root / rid

    def _prune(self) -> None:
        folders = sorted((p for p in self.root.iterdir() if p.is_dir() and RID_RE.fullmatch(p.name)),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for old in folders[self.keep:]:
            shutil.rmtree(old, ignore_errors=True)
