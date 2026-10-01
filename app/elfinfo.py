"""Información de depuración de un .elf: archivos fuente y líneas con código.

La pestaña "Depurar" la usa para:
  * listar los archivos fuente del programa y encontrarlos en la carpeta del
    proyecto (el .elf guarda las rutas de la PC donde se compiló, por ejemplo
    C:/Users/.../src/placa.c o ../Core/Src/main.c);
  * marcar en qué líneas se puede poner un breakpoint.

Se lee la tabla de líneas DWARF con pyelftools (versiones 2 a 5). El navegador
nunca pide un archivo por ruta: pide "la fuente número N de este .elf" y la
ruta la resuelve el servidor, siempre dentro del proyecto.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

from .settings import UPLOADS_DIRNAME

SOURCE_EXTENSIONS = (".c", ".h", ".s", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".inc")
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_INDEX_FILES = 20000
_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


class DebugInfoError(ValueError):
    """El .elf no sirve para depurar (mensaje para el usuario)."""


@dataclass
class SourceFile:
    id: int
    name: str                   # como lo guardó el compilador, con "/" (p. ej. "src/placa.c")
    base: str                   # nombre del archivo
    lines: List[int]            # líneas con código (donde se puede poner un breakpoint)
    path: Optional[Path] = None  # en disco, dentro del proyecto
    rel: Optional[str] = None    # relativa al directorio de proyectos (para mostrar)

    def to_public(self) -> Dict:
        return {"id": self.id, "name": self.name, "base": self.base, "rel": self.rel,
                "found": self.path is not None, "lines": self.lines}


@dataclass
class DebugInfo:
    elf: Path
    sources: List[SourceFile]
    has_main: bool
    missing: List[str] = field(default_factory=list)   # fuentes del proyecto que no se encontraron

    def by_id(self, source_id: int) -> SourceFile:
        if not 0 <= source_id < len(self.sources):
            raise DebugInfoError("Archivo fuente inexistente")
        return self.sources[source_id]

    def find(self, file: Optional[str], fullname: Optional[str] = None) -> Optional[SourceFile]:
        """Fuente que corresponde a lo que informa GDB en un frame o breakpoint."""
        for text in (file, fullname):
            if not text:
                continue
            norm = text.replace("\\", "/")
            for src in self.sources:
                if src.name == norm or norm.endswith("/" + src.name.lstrip("./")) or src.name.endswith("/" + norm):
                    return src
        base = (file or fullname or "").replace("\\", "/").rsplit("/", 1)[-1]
        matches = [s for s in self.sources if s.base == base]
        return matches[0] if len(matches) == 1 else None

    def location(self, src: SourceFile, line: int) -> str:
        """Ubicación para -break-insert: el nombre corto si no es ambiguo."""
        same = [s for s in self.sources if s.base == src.base]
        return f"{src.base if len(same) == 1 else src.name}:{int(line)}"

    def to_public(self) -> Dict:
        return {"sources": [s.to_public() for s in self.sources if s.path is not None],
                "missing": self.missing, "has_main": self.has_main}


# ---------------------------------------------------------------------------
# Lectura del DWARF
# ---------------------------------------------------------------------------

def _s(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")


def _is_abs(path: str) -> bool:
    return path.startswith("/") or bool(re.match(r"^[A-Za-z]:/", path))


def _join(directory: str, name: str) -> str:
    name = name.replace("\\", "/")
    directory = directory.replace("\\", "/").rstrip("/")
    if _is_abs(name) or not directory:
        return name
    return f"{directory}/{name}"


def _relative_to_comp_dir(path: str, comp_dir: str) -> str:
    comp = comp_dir.replace("\\", "/").rstrip("/")
    if comp and path.lower().startswith(comp.lower() + "/"):
        return path[len(comp) + 1:]
    return path


def _line_table_files(lp, comp_dir: str) -> Dict[int, str]:
    """Índice de archivo de la tabla de líneas -> ruta (relativa a comp_dir si se puede)."""
    hdr = lp.header
    version = hdr["version"]
    dirs = [_s(d) for d in hdr["include_directory"]]
    out: Dict[int, str] = {}
    for i, entry in enumerate(hdr["file_entry"]):
        di = entry.dir_index
        if version >= 5:                     # índices desde 0; el directorio 0 es comp_dir
            idx = i
            directory = dirs[di] if di < len(dirs) else ""
        else:                                # índices desde 1; el directorio 0 es comp_dir
            idx = i + 1
            directory = "" if di == 0 else (dirs[di - 1] if di - 1 < len(dirs) else "")
        path = _join(directory, _s(entry.name))
        out[idx] = _relative_to_comp_dir(path, comp_dir)
    return out


def read_line_info(elf: Path) -> Tuple[Dict[str, Set[int]], bool]:
    """{ruta del fuente: líneas con código} y si el programa tiene main()."""
    try:
        from elftools.common.exceptions import ELFError
        from elftools.elf.elffile import ELFFile
    except ImportError as exc:     # pragma: no cover
        raise DebugInfoError(f"Falta pyelftools: {exc}")
    lines: Dict[str, Set[int]] = {}
    try:
        with open(elf, "rb") as fh:
            ef = ELFFile(fh)
            if not ef.has_dwarf_info() or ef.get_section_by_name(".debug_line") is None:
                raise DebugInfoError(
                    "El .elf no tiene información de depuración: compilalo con -g (configuración Debug).")
            dwarf = ef.get_dwarf_info()
            for cu in dwarf.iter_CUs():
                top = cu.get_top_DIE()
                comp_attr = top.attributes.get("DW_AT_comp_dir")
                comp_dir = _s(comp_attr.value) if comp_attr else ""
                lp = dwarf.line_program_for_CU(cu)
                if lp is None:
                    continue
                files = _line_table_files(lp, comp_dir)
                for entry in lp.get_entries():
                    st = entry.state
                    if st is None or st.end_sequence or not st.is_stmt or st.line <= 0:
                        continue
                    name = files.get(st.file)
                    if name:
                        lines.setdefault(name, set()).add(st.line)
            has_main = False
            symtab = ef.get_section_by_name(".symtab")
            if symtab is not None:
                has_main = any(sym.name == "main" and sym["st_info"]["type"] == "STT_FUNC"
                               for sym in symtab.iter_symbols())
    except DebugInfoError:
        raise
    except (ELFError, OSError, KeyError, ValueError, IndexError) as exc:
        raise DebugInfoError(f"No se pudo leer la información de depuración del .elf: {exc}")
    if not lines:
        raise DebugInfoError("El .elf no tiene tabla de líneas: compilalo con -g (configuración Debug).")
    return lines, has_main


# ---------------------------------------------------------------------------
# Ubicación de los fuentes en el proyecto
# ---------------------------------------------------------------------------

def project_root(projects_dir: Path, elf: Path) -> Path:
    """Carpeta del proyecto al que pertenece el .elf (donde se buscan los fuentes)."""
    root = Path(os.path.realpath(projects_dir))
    parts = Path(os.path.realpath(elf)).relative_to(root).parts
    if parts and parts[0] == UPLOADS_DIRNAME:
        return root / UPLOADS_DIRNAME / parts[1] if len(parts) > 2 else root / UPLOADS_DIRNAME
    return root / parts[0] if len(parts) > 1 else root


def _index_sources(root: Path) -> Dict[str, List[Path]]:
    """nombre de archivo -> rutas, para los fuentes dentro del proyecto (sin seguir symlinks)."""
    index: Dict[str, List[Path]] = {}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if not name.lower().endswith(SOURCE_EXTENSIONS):
                continue
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            index.setdefault(name, []).append(path)
            count += 1
            if count >= MAX_INDEX_FILES:
                return index
    return index


def _components(name: str) -> List[str]:
    return [p for p in name.replace("\\", "/").split("/") if p and p not in (".", "..") and not _DRIVE_RE.match(p)]


def locate(name: str, elf_dir: Path, root: Path, index: Dict[str, List[Path]]) -> Optional[Path]:
    """Busca en el proyecto el archivo que el compilador llamó `name`.

    1) relativo a la carpeta del .elf o a sus padres (CubeIDE: ../Core/Src/main.c);
    2) el archivo del mismo nombre cuya ruta comparta más carpetas finales con `name`.
    """
    root_real = Path(os.path.realpath(root))
    norm = name.replace("\\", "/")
    if not _is_abs(norm):
        base = Path(os.path.realpath(elf_dir))
        while True:
            cand = Path(os.path.normpath(base / norm))
            if cand.is_file() and not cand.is_symlink():
                try:
                    Path(os.path.realpath(cand)).relative_to(root_real)
                    return cand
                except ValueError:
                    pass
            if base == root_real or root_real not in base.parents:
                break
            base = base.parent
    wanted = _components(norm)
    if not wanted:
        return None
    candidates = index.get(wanted[-1], [])
    best, best_score = None, -1
    for cand in candidates:
        have = list(cand.relative_to(root).parts)
        score = 0
        while score < min(len(have), len(wanted)) and have[-1 - score] == wanted[-1 - score]:
            score += 1
        if score > best_score:
            best, best_score = cand, score
    return best


# La lectura del DWARF es lo lento (pyelftools es Python puro): se guarda por tamaño y fecha
# del .elf. La búsqueda de los fuentes se rehace cada vez, porque se pueden subir después.
_lines_cache: Dict[Tuple[str, int, int], Tuple[Dict[str, Set[int]], bool]] = {}
_index_cache: Dict[str, Tuple[float, Dict[str, List[Path]]]] = {}
_cache_lock = threading.Lock()
INDEX_TTL_S = 3.0


def _cached_line_info(elf: Path) -> Tuple[Dict[str, Set[int]], bool]:
    st = elf.stat()
    key = (str(elf), st.st_size, int(st.st_mtime_ns))
    with _cache_lock:
        if key in _lines_cache:
            return _lines_cache[key]
    result = read_line_info(elf)
    with _cache_lock:
        if len(_lines_cache) > 32:
            _lines_cache.clear()
        _lines_cache[key] = result
    return result


def _cached_index(root: Path) -> Dict[str, List[Path]]:
    now = time.monotonic()
    with _cache_lock:
        hit = _index_cache.get(str(root))
        if hit and now - hit[0] < INDEX_TTL_S:
            return hit[1]
    index = _index_sources(root)
    with _cache_lock:
        _index_cache[str(root)] = (now, index)
    return index


def load_debug_info(projects_dir: Path, elf: Path) -> DebugInfo:
    """DebugInfo del .elf (bloqueante).

    Los números de fuente se asignan sobre TODOS los archivos de la tabla de
    líneas (se encuentren o no), así no cambian cuando después se suben las
    fuentes: la sesión de depuración y los breakpoints guardados siguen valiendo.
    """
    lines, has_main = _cached_line_info(elf)
    root = project_root(projects_dir, elf)
    index = _cached_index(root)
    projects_real = Path(os.path.realpath(projects_dir))
    sources: List[SourceFile] = []
    missing: List[str] = []
    for name in sorted(lines, key=lambda n: (n.rsplit("/", 1)[-1].lower().endswith(".h"), n.lower())):
        path = locate(name, elf.parent, root, index)
        if path is None and not name.lower().endswith(".h"):
            # Los encabezados (stdint.h del compilador...) no se suben con el proyecto: no es un error.
            missing.append(name)
        sources.append(SourceFile(
            id=len(sources), name=name, base=name.rsplit("/", 1)[-1], lines=sorted(lines[name]), path=path,
            rel=Path(os.path.realpath(path)).relative_to(projects_real).as_posix() if path else None))
    return DebugInfo(elf=elf, sources=sources, has_main=has_main, missing=missing)


def read_source(src: SourceFile) -> str:
    if src.path is None:
        raise DebugInfoError(f"No se encontró '{src.name}' en la carpeta del proyecto")
    if src.path.stat().st_size > MAX_SOURCE_BYTES:
        raise DebugInfoError(f"'{src.base}' es demasiado grande para mostrarlo")
    return src.path.read_bytes().decode("utf-8", "replace")
