"""Gestión de archivos de firmware.

Reglas de seguridad:
  * Todo se resuelve con realpath y debe quedar DENTRO del directorio de
    proyectos (se cubren "..", rutas absolutas y symlinks que apunten afuera).
  * Los nombres se validan con una lista blanca de caracteres. Esto no es solo
    estética: las rutas terminan dentro de comandos Tcl de OpenOCD (-c "program
    {ruta}"), y caracteres como [ ] { } $ " \\ ; permitirían inyectar Tcl.
  * Solo se pueden borrar archivos subidos desde la web (carpeta _subidos/);
    lo que llega por Syncthing es de solo lectura en la interfaz.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Dict, List, Optional

from .settings import UPLOADS_DIRNAME

ALLOWED_EXTENSIONS = (".elf", ".hex", ".bin")
# Lo que se extrae de un proyecto en .zip: firmware + fuentes (para la pestaña Depurar).
SOURCE_EXTENSIONS = (".c", ".h", ".s", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".inc", ".ld")
ZIP_MAX_FILES = 3000
ZIP_MAX_TOTAL_BYTES = 128 * 1024 * 1024
# Un componente de ruta: empieza con alfanumérico, luego letras, números,
# espacio y . _ - + ( ). Máximo 128 caracteres.
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,127}$")
# Caracteres que nunca pueden aparecer en una ruta que se pasa a OpenOCD (Tcl).
TCL_UNSAFE_CHARS = set('{}[]$"\\;\n\r\t')
# Archivos/carpetas de Syncthing y temporales que se ignoran al escanear.
IGNORED_DIRS = {".stfolder", ".stversions", ".git", "__pycache__"}
IGNORED_FILE_PREFIXES = (".syncthing.", "~syncthing~", ".upload-")
IGNORED_FILE_SUFFIXES = (".tmp", ".part")


class FileValidationError(ValueError):
    """Error de validación de archivo con mensaje para el usuario."""


def is_safe_component(name: str) -> bool:
    return bool(SAFE_COMPONENT_RE.fullmatch(name)) and not name.endswith((" ", "."))


def validate_filename(name: str) -> str:
    """Valida un nombre de archivo de firmware (sin directorios)."""
    if not name or "/" in name or "\\" in name:
        raise FileValidationError("El nombre de archivo no puede contener directorios")
    if not is_safe_component(name):
        raise FileValidationError(
            f"Nombre no permitido: '{name}'. Usá letras, números, espacio y . _ - + ( ) "
            "(máx. 128 caracteres, sin empezar con punto)")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise FileValidationError(f"Extensión '{ext or '(ninguna)'}' no permitida. Válidas: .elf, .hex, .bin")
    return name


def validate_project_name(name: str) -> str:
    """Valida el nombre de la subcarpeta de proyecto para subidas."""
    name = (name or "").strip()
    if not name:
        return "general"
    if not is_safe_component(name) or "." in name[:1]:
        raise FileValidationError(
            f"Nombre de proyecto no permitido: '{name}'. Usá letras, números, espacio y _ - + ( )")
    return name


def file_kind(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def ensure_tcl_safe(path: Path) -> str:
    """Devuelve la ruta en formato POSIX si es segura para meterla entre {} en Tcl."""
    text = path.as_posix()
    bad = sorted(set(text) & TCL_UNSAFE_CHARS)
    if bad:
        raise FileValidationError(f"La ruta contiene caracteres no permitidos: {' '.join(bad)}")
    return text


def _is_within(child: Path, root: Path) -> bool:
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_in_root(root: Path, rel_path: str) -> Path:
    """Resuelve una ruta relativa dentro de root, o lanza FileValidationError.

    Acepta separadores '/' únicamente. Rechaza rutas absolutas, componentes
    vacíos, '.', '..' y cualquier componente fuera de la lista blanca.
    """
    if not rel_path or not isinstance(rel_path, str):
        raise FileValidationError("Ruta vacía")
    if "\\" in rel_path or "\x00" in rel_path:
        raise FileValidationError("Ruta inválida")
    if rel_path.startswith("/"):
        raise FileValidationError("No se permiten rutas absolutas")
    # Se valida el texto tal cual (sin normalizar): "a/./b" o "a//b" se rechazan.
    parts = rel_path.split("/")
    for part in parts:
        if part in (".", "..") or not (is_safe_component(part) or part == UPLOADS_DIRNAME):
            raise FileValidationError(f"Componente de ruta no permitido: '{part}'")
    root_real = Path(os.path.realpath(root))
    candidate = Path(os.path.realpath(root_real.joinpath(*parts)))
    if not _is_within(candidate, root_real):
        raise FileValidationError("La ruta sale del directorio de proyectos")
    return candidate


def resolve_firmware(root: Path, rel_path: str) -> Path:
    """Resuelve y valida un archivo de firmware existente para usarlo en una acción."""
    path = resolve_in_root(root, rel_path)
    validate_filename(path.name)
    if not path.is_file():
        raise FileValidationError(f"No existe el archivo '{rel_path}'")
    ensure_tcl_safe(path)
    return path


# ---------------------------------------------------------------------------
# Verificación de contenido
# ---------------------------------------------------------------------------

def check_content(kind: str, head: bytes, size: int) -> None:
    """Chequeo básico del contenido según el tipo (evita subir cualquier cosa)."""
    if size == 0:
        raise FileValidationError("El archivo está vacío")
    if kind == "elf":
        if len(head) < 20 or head[:4] != b"\x7fELF":
            raise FileValidationError("No es un ELF válido (falta la firma \\x7fELF)")
        if head[4] != 1:
            raise FileValidationError("El ELF no es de 32 bits (los microcontroladores soportados son de 32 bits)")
        machine = int.from_bytes(head[18:20], "little" if head[5] == 1 else "big")
        if machine not in (40, 83, 94, 243):  # ARM, AVR, Xtensa, RISC-V
            raise FileValidationError(f"El ELF no es para un microcontrolador soportado (e_machine={machine})")
    elif kind == "hex":
        stripped = head.lstrip(b"\xef\xbb\xbf \r\n\t")
        if not stripped.startswith(b":"):
            raise FileValidationError("No parece un Intel HEX (las líneas deben empezar con ':')")


# ---------------------------------------------------------------------------
# Subida
# ---------------------------------------------------------------------------

@dataclass
class SavedFile:
    rel_path: str
    size: int


def save_upload(projects_dir: Path, project: str, filename: str, stream: BinaryIO,
                max_bytes: int, chunk_size: int = 64 * 1024,
                validator: Optional[Callable[[Path], None]] = None) -> SavedFile:
    """Guarda un archivo subido en _subidos/<proyecto>/<nombre>.

    Se escribe en un temporal y se renombra al final (atómico), así Syncthing
    ni el escaneo ven nunca un archivo a medio escribir. `validator` recibe el
    temporal y puede rechazarlo (p. ej. si el binario no es para la placa elegida).
    """
    validate_filename(filename)
    project = validate_project_name(project)
    kind = file_kind(Path(filename))

    target_dir = projects_dir / UPLOADS_DIRNAME / project
    target_dir.mkdir(parents=True, exist_ok=True)
    root_real = Path(os.path.realpath(projects_dir))
    if not _is_within(Path(os.path.realpath(target_dir)), root_real):
        raise FileValidationError("El directorio de destino sale del directorio de proyectos")

    tmp = target_dir / f".upload-{secrets.token_hex(8)}.part"
    size = 0
    head = b""
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FileValidationError(
                        f"El archivo supera el máximo permitido ({max_bytes // (1024 * 1024)} MB)")
                if len(head) < 64:
                    head += chunk[:64 - len(head)]
                out.write(chunk)
        check_content(kind, head, size)
        if validator is not None:
            validator(tmp)
        final = target_dir / filename
        os.replace(tmp, final)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    rel = final.relative_to(projects_dir).as_posix()
    return SavedFile(rel_path=rel, size=size)


def _receive(stream: BinaryIO, tmp: Path, max_bytes: int, chunk_size: int = 64 * 1024) -> int:
    size = 0
    with open(tmp, "wb") as out:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise FileValidationError(
                    f"El archivo supera el máximo permitido ({max_bytes // (1024 * 1024)} MB)")
            out.write(chunk)
    return size


@dataclass
class SavedProject:
    folder: str                       # carpeta del proyecto, relativa al directorio de proyectos
    firmware: List[str] = field(default_factory=list)
    sources: int = 0
    skipped: int = 0


def _zip_entries(zf: zipfile.ZipFile, max_file_bytes: int):
    """(partes de la ruta, ZipInfo) de los archivos útiles; sin el prefijo común."""
    useful, skipped = [], 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            skipped += 1
            continue
        parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
        ext = Path(parts[-1]).suffix.lower() if parts else ""
        if (not parts or ext not in ALLOWED_EXTENSIONS + SOURCE_EXTENSIONS
                or any(p in (".", "..") or not is_safe_component(p) for p in parts)):
            skipped += 1          # carpetas ocultas (.settings), objetos .o, nombres raros...
            continue
        if info.file_size > max_file_bytes:
            raise FileValidationError(f"'{info.filename}' es demasiado grande")
        useful.append((parts, info))
    if len(useful) > ZIP_MAX_FILES:
        raise FileValidationError(f"El .zip tiene demasiados archivos (máx. {ZIP_MAX_FILES})")
    if sum(i.file_size for _, i in useful) > ZIP_MAX_TOTAL_BYTES:
        raise FileValidationError("El .zip descomprimido es demasiado grande")
    # Si todo está dentro de una sola carpeta, esa carpeta es el proyecto.
    top = {parts[0] for parts, _ in useful if len(parts) > 1}
    common = top.pop() if len(top) == 1 and all(len(p) > 1 for p, _ in useful) else None
    if common:
        useful = [(parts[1:], info) for parts, info in useful]
    return useful, skipped, common


def save_project_zip(projects_dir: Path, project: str, filename: str, stream: BinaryIO, max_bytes: int,
                     validator: Optional[Callable[[List[tuple]], None]] = None) -> SavedProject:
    """Extrae un proyecto (.elf/.hex/.bin + fuentes .c/.h/.s) en _subidos/<proyecto>/<carpeta>/.

    Protecciones: sólo extensiones de firmware y fuente, nombres de la lista
    blanca, sin "..", rutas absolutas ni symlinks, y límites de cantidad y de
    tamaño descomprimido (contra "bombas zip"). Si la carpeta ya existía, se reemplaza.
    """
    if not filename.lower().endswith(".zip") or "/" in filename or "\\" in filename:
        raise FileValidationError("Se esperaba un archivo .zip")
    project = validate_project_name(project)
    target_dir = projects_dir / UPLOADS_DIRNAME / project
    target_dir.mkdir(parents=True, exist_ok=True)
    root_real = Path(os.path.realpath(projects_dir))
    if not _is_within(Path(os.path.realpath(target_dir)), root_real):
        raise FileValidationError("El directorio de destino sale del directorio de proyectos")

    token = secrets.token_hex(8)
    tmp_zip = target_dir / f".upload-{token}.part"
    tmp_dir = target_dir / f".upload-{token}.d"
    try:
        _receive(stream, tmp_zip, max_bytes)
        try:
            zf = zipfile.ZipFile(tmp_zip)
        except zipfile.BadZipFile:
            raise FileValidationError("El .zip está dañado o no es un .zip")
        with zf:
            entries, skipped, common = _zip_entries(zf, max_bytes)
            if not entries:
                raise FileValidationError("El .zip no tiene archivos de firmware ni fuentes")
            stem = common or Path(filename).stem
            folder = stem if is_safe_component(stem) else "proyecto"
            result = SavedProject(folder="", skipped=skipped)
            firmware: List[Path] = []
            written = 0
            for parts, info in entries:
                dest = tmp_dir.joinpath(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > ZIP_MAX_TOTAL_BYTES or out.tell() + len(chunk) > info.file_size:
                            raise FileValidationError("El .zip no coincide con los tamaños que declara")
                        out.write(chunk)
                if dest.suffix.lower() in ALLOWED_EXTENSIONS:
                    check_content(file_kind(dest), dest.read_bytes()[:64], dest.stat().st_size)
                    ensure_tcl_safe(target_dir / folder / Path(*parts))
                    firmware.append(dest)
                else:
                    result.sources += 1
        if not firmware:
            raise FileValidationError("El .zip no tiene ningún .elf, .hex ni .bin")
        if validator is not None:            # recibe [(ruta relativa dentro del proyecto, archivo)]
            validator([(f.relative_to(tmp_dir).as_posix(), f) for f in firmware])
        final = target_dir / folder
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp_dir, final)
        result.folder = final.relative_to(projects_dir).as_posix()
        result.firmware = sorted((final / f.relative_to(tmp_dir)).relative_to(projects_dir).as_posix()
                                 for f in firmware)
        return result
    finally:
        try:
            tmp_zip.unlink()
        except FileNotFoundError:
            pass
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def save_file_set(projects_dir: Path, project: str, folder: str, items: List[tuple], max_bytes: int,
                  extra: Optional[Dict[str, bytes]] = None,
                  validator: Optional[Callable[[Path], None]] = None) -> SavedProject:
    """Guarda varios binarios con sus rutas relativas en _subidos/<proyecto>/<carpeta>/.

    `items` = [(ruta relativa con "/", stream)]. `extra` son archivos chicos que
    genera el banco (p. ej. un manifiesto) y van en la raíz de la carpeta.
    `validator` recibe la carpeta temporal completa antes de publicarla. Si la
    carpeta ya existía, se reemplaza entera (como al subir un .zip).
    """
    project = validate_project_name(project)
    if not is_safe_component(folder):
        raise FileValidationError(f"Nombre de carpeta no permitido: '{folder}'")
    target_dir = projects_dir / UPLOADS_DIRNAME / project
    target_dir.mkdir(parents=True, exist_ok=True)
    root_real = Path(os.path.realpath(projects_dir))
    if not _is_within(Path(os.path.realpath(target_dir)), root_real):
        raise FileValidationError("El directorio de destino sale del directorio de proyectos")

    tmp_dir = target_dir / f".upload-{secrets.token_hex(8)}.d"
    try:
        written: List[str] = []
        for rel, stream in items:
            parts = rel.split("/") if isinstance(rel, str) else []
            if (not parts or rel in written or "\\" in rel
                    or any(p in (".", "..") or not is_safe_component(p) for p in parts)):
                raise FileValidationError(f"Ruta no permitida: '{rel}'")
            validate_filename(parts[-1])
            dest = tmp_dir.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = _receive(stream, dest, max_bytes)
            check_content(file_kind(dest), dest.read_bytes()[:64], size)
            ensure_tcl_safe(target_dir / folder / Path(*parts))
            written.append(rel)
        for name, data in (extra or {}).items():
            if not is_safe_component(name) or not name.endswith(".json"):
                raise FileValidationError(f"Nombre no permitido: '{name}'")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            (tmp_dir / name).write_bytes(data)
        if validator is not None:
            validator(tmp_dir)
        final = target_dir / folder
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp_dir, final)
        folder_rel = final.relative_to(projects_dir).as_posix()
        return SavedProject(folder=folder_rel, firmware=sorted(f"{folder_rel}/{rel}" for rel in written))
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def delete_upload(projects_dir: Path, rel_path: str) -> None:
    """Borra un archivo subido. Solo se permite dentro de _subidos/."""
    path = resolve_in_root(projects_dir, rel_path)
    uploads_real = Path(os.path.realpath(projects_dir / UPLOADS_DIRNAME))
    if not _is_within(path, uploads_real):
        raise FileValidationError(
            "Solo se pueden borrar archivos subidos desde la web. Los archivos "
            "sincronizados (Syncthing) se gestionan desde tu PC.")
    validate_filename(path.name)
    if not path.is_file():
        raise FileValidationError("El archivo no existe")
    path.unlink()
    # Si la carpeta del proyecto quedó vacía, se elimina.
    parent = path.parent
    if parent != uploads_real and not any(parent.iterdir()):
        parent.rmdir()


# ---------------------------------------------------------------------------
# Escaneo
# ---------------------------------------------------------------------------

def _ignored_file(name: str) -> bool:
    return name.startswith(IGNORED_FILE_PREFIXES) or name.endswith(IGNORED_FILE_SUFFIXES)


def scan_projects(projects_dir: Path, max_depth: int = 5,
                  targets: Optional[Dict[str, str]] = None) -> List[Dict]:
    """Recorre el directorio de proyectos y agrupa el firmware por proyecto.

    El "proyecto" es la carpeta de primer nivel (o _subidos/<proyecto>). Los
    archivos con nombres fuera de la lista blanca se listan como no utilizables
    para que el usuario sepa que tiene que renombrarlos.
    """
    root = Path(os.path.realpath(projects_dir))
    if not root.is_dir():
        return []
    projects: Dict[str, Dict] = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        depth = len(rel_dir.parts)
        # Poda: carpetas ocultas, de Syncthing y más allá de la profundidad máxima.
        dirnames[:] = sorted(d for d in dirnames
                             if d not in IGNORED_DIRS and not d.startswith(".") and depth < max_depth)
        for name in sorted(filenames):
            if _ignored_file(name) or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            path = current / name
            if path.is_symlink():
                real = Path(os.path.realpath(path))
                if not _is_within(real, root):
                    continue
            try:
                st = path.stat()
            except OSError:
                continue
            rel = path.relative_to(root)
            parts = rel.parts
            uploaded = parts[0] == UPLOADS_DIRNAME
            if uploaded:
                project_name = parts[1] if len(parts) > 2 else "general"
                project_key = f"{UPLOADS_DIRNAME}/{project_name}"
            elif len(parts) > 1:
                project_name = parts[0]
                project_key = parts[0]
            else:
                project_name = "(raíz)"
                project_key = ""
            usable = all(is_safe_component(p) or p == UPLOADS_DIRNAME for p in parts)
            try:
                ensure_tcl_safe(path)
            except FileValidationError:
                usable = False
            entry = projects.setdefault(project_key, {
                "key": project_key,
                "name": project_name,
                "uploaded": uploaded,
                "files": [],
            })
            entry["files"].append({
                "path": rel.as_posix(),
                "name": name,
                # Subcarpeta dentro del proyecto (p. ej. "Debug"), sin repetir el proyecto.
                "subdir": "/".join(parts[2 if uploaded else 1:-1]),
                "kind": file_kind(path),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "deletable": uploaded,
                "usable": usable,
                # Placa a la que está asignado el archivo (None = sin asignar).
                "target": (targets or {}).get(rel.as_posix()),
            })

    result = list(projects.values())
    for project in result:
        project["files"].sort(key=lambda f: f["mtime"], reverse=True)
        project["latest"] = project["files"][0]["mtime"] if project["files"] else 0
    # Primero los subidos, luego por actividad reciente.
    result.sort(key=lambda p: (not p["uploaded"], -p["latest"]))
    return result
