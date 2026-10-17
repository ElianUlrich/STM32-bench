"""Dónde están las herramientas en una PC (modo PC): OpenOCD, GDB, dfu-util y avrdude.

En la BeagleBoard las instala `instalar_todo.sh` en rutas fijas. En una PC con
Windows ya suelen estar, porque las traen los IDE:

* STM32CubeIDE: el OpenOCD de ST (soporta STM32H5/H7) con sus scripts, y
  arm-none-eabi-gdb.  C:\\ST\\STM32CubeIDE_x.y.z\\STM32CubeIDE\\plugins\\...
* IDE de Arduino 2.x: avrdude (con su avrdude.conf) y dfu-util.
  %LOCALAPPDATA%\\Arduino15\\packages\\arduino\\tools\\...
* xPack OpenOCD:  %APPDATA%\\xPacks\\@xpack-dev-tools\\openocd\\<versión>\\.content\\
* o el PATH.
* Las que deja el instalador del banco (%LOCALAPPDATA%\\BancoRemoto\\herramientas):
  avrdude y dfu-util se usan siempre de ahí (son las versiones probadas); OpenOCD y
  GDB de ST, sólo si no hay STM32CubeIDE.

En bench.yaml, "auto" en vez de una ruta usa lo que se encuentre acá
(config/bench.pc.yaml lo trae así). `python -m app.tools` muestra qué encontró.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

AUTO = "auto"
EXE = ".exe" if sys.platform == "win32" else ""


@dataclass(frozen=True)
class Found:
    path: str
    source: str                         # "STM32CubeIDE 1.19.0", "Arduino IDE", "xPack 0.12.0", "PATH"
    scripts_dir: Optional[str] = None   # OpenOCD
    config: Optional[str] = None        # avrdude.conf

    def to_public(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class SearchRoots:
    st: List[Path]                      # C:\ST (instalaciones de STM32CubeIDE)
    local_appdata: Optional[Path]
    appdata: Optional[Path]
    program_files: List[Path]
    path_lookup: Callable[[str], Optional[str]] = field(default=shutil.which)
    bundled: Optional[Path] = None      # herramientas que instaló el banco


def default_roots() -> SearchRoots:
    env = os.environ.get
    pf = [Path(p) for p in (env("ProgramFiles"), env("ProgramFiles(x86)")) if p]
    st = [Path(env("SystemDrive", "C:") + "\\ST")] + [p / "STMicroelectronics" / "STM32Cube" for p in pf]
    # El instalador deja la app en BancoRemoto/app y las herramientas en BancoRemoto/herramientas.
    bundled = Path(env("BANCO_HERRAMIENTAS") or Path(__file__).resolve().parents[2] / "herramientas")
    return SearchRoots(st=st, local_appdata=Path(env("LOCALAPPDATA")) if env("LOCALAPPDATA") else None,
                       appdata=Path(env("APPDATA")) if env("APPDATA") else None, program_files=pf,
                       bundled=bundled if bundled.is_dir() else None)


BUNDLED = "instalado con el banco"


def _version_key(text: str) -> List[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


def _newest(paths: Iterable[Path], key: Callable[[Path], str]) -> Optional[Path]:
    paths = [p for p in paths if p.is_file()]
    return max(paths, key=lambda p: _version_key(key(p))) if paths else None


def _cubeide_plugins(roots: SearchRoots) -> List[Path]:
    """Carpetas plugins de cada STM32CubeIDE instalado (la más nueva primero)."""
    found = []
    for root in roots.st:
        found += [d / "STM32CubeIDE" / "plugins" for d in root.glob("STM32CubeIDE*") if d.is_dir()]
    found = [p for p in found if p.is_dir()]
    return sorted(found, key=lambda p: _version_key(p.parent.parent.name), reverse=True)


def _ide_version(plugins: Path) -> str:
    m = re.search(r"(\d+\.\d+\.\d+)", plugins.parent.parent.name)
    return f"STM32CubeIDE {m.group(1)}" if m else "STM32CubeIDE"


def find_openocd(roots: SearchRoots) -> Optional[Found]:
    for plugins in _cubeide_plugins(roots):
        exe = _newest(plugins.glob(f"com.st.stm32cube.ide.mcu.externaltools.openocd.*/tools/bin/openocd{EXE}"),
                      lambda p: p.parents[2].name)
        scripts = sorted(plugins.glob("com.st.stm32cube.ide.mcu.debug.openocd_*/resources/openocd/st_scripts"),
                         key=lambda p: _version_key(p.parents[2].name), reverse=True)
        if exe and scripts:
            return Found(str(exe), _ide_version(plugins), scripts_dir=str(scripts[0]))
    if roots.bundled:
        exe = roots.bundled / "stm32" / "openocd" / "bin" / f"openocd{EXE}"
        scripts = roots.bundled / "stm32" / "openocd" / "scripts"
        if exe.is_file() and scripts.is_dir():
            return Found(str(exe), BUNDLED, scripts_dir=str(scripts))
    if roots.appdata:
        base = roots.appdata / "xPacks" / "@xpack-dev-tools" / "openocd"
        exe = _newest(base.glob(f"*/.content/bin/openocd{EXE}"), lambda p: p.parents[2].name)
        if exe:
            scripts = exe.parents[1] / "openocd" / "scripts"
            return Found(str(exe), f"xPack {exe.parents[2].name}",
                         scripts_dir=str(scripts) if scripts.is_dir() else None)
    path = roots.path_lookup("openocd")
    return Found(path, "PATH") if path else None


def find_gdb(roots: SearchRoots) -> Optional[Found]:
    for plugins in _cubeide_plugins(roots):
        exe = _newest(plugins.glob(f"com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/tools/bin/"
                                   f"arm-none-eabi-gdb{EXE}"), lambda p: p.parents[2].name)
        if exe:
            return Found(str(exe), _ide_version(plugins))
    if roots.bundled and (roots.bundled / "stm32" / "gdb" / f"arm-none-eabi-gdb{EXE}").is_file():
        return Found(str(roots.bundled / "stm32" / "gdb" / f"arm-none-eabi-gdb{EXE}"), BUNDLED)
    path = roots.path_lookup("arm-none-eabi-gdb")
    return Found(path, "PATH") if path else None


def _arduino_tools(roots: SearchRoots) -> Optional[Path]:
    if not roots.local_appdata:
        return None
    tools = roots.local_appdata / "Arduino15" / "packages" / "arduino" / "tools"
    return tools if tools.is_dir() else None


def find_avrdude(roots: SearchRoots) -> Optional[Found]:
    if roots.bundled and (roots.bundled / "avrdude" / "bin" / f"avrdude{EXE}").is_file():
        conf = roots.bundled / "avrdude" / "etc" / "avrdude.conf"
        return Found(str(roots.bundled / "avrdude" / "bin" / f"avrdude{EXE}"), BUNDLED,
                     config=str(conf) if conf.is_file() else None)
    tools = _arduino_tools(roots)
    if tools:
        exe = _newest(tools.glob(f"avrdude/*/bin/avrdude{EXE}"), lambda p: p.parents[1].name)
        if exe:
            conf = exe.parents[1] / "etc" / "avrdude.conf"
            return Found(str(exe), "Arduino IDE", config=str(conf) if conf.is_file() else None)
    for pf in roots.program_files:                       # IDE de Arduino 1.x
        exe = pf / "Arduino" / "hardware" / "tools" / "avr" / "bin" / f"avrdude{EXE}"
        if exe.is_file():
            conf = exe.parents[1] / "etc" / "avrdude.conf"
            return Found(str(exe), "Arduino IDE 1.x", config=str(conf) if conf.is_file() else None)
    path = roots.path_lookup("avrdude")
    return Found(path, "PATH") if path else None


def find_dfu_util(roots: SearchRoots) -> Optional[Found]:
    if roots.bundled and (roots.bundled / "dfu-util" / f"dfu-util{EXE}").is_file():
        return Found(str(roots.bundled / "dfu-util" / f"dfu-util{EXE}"), BUNDLED)
    path = roots.path_lookup("dfu-util")
    if path:
        return Found(path, "PATH")
    tools = _arduino_tools(roots)
    if tools:
        exe = _newest(tools.glob(f"dfu-util/*/dfu-util{EXE}"), lambda p: p.parent.name)
        if exe:
            return Found(str(exe), "Arduino IDE")
    return None


def detect(roots: Optional[SearchRoots] = None) -> Dict[str, Optional[Found]]:
    roots = roots or default_roots()
    return {"openocd": find_openocd(roots), "gdb": find_gdb(roots), "dfu-util": find_dfu_util(roots),
            "avrdude": find_avrdude(roots)}


_cache: Optional[Dict[str, Optional[Found]]] = None


def detected() -> Dict[str, Optional[Found]]:
    """detect() una sola vez por proceso (buscar en los IDE recorre carpetas)."""
    global _cache
    if _cache is None:
        _cache = detect(default_roots())
    return _cache


def clear_cache() -> None:
    global _cache
    _cache = None


def resolve(value, tool: str, attr: str = "path", fallback: Optional[str] = None) -> Optional[str]:
    """Para bench.yaml: el valor tal cual, o lo detectado si es "auto" (fallback si no se encontró)."""
    if value != AUTO:
        return value
    found = detected().get(tool)
    return (getattr(found, attr) if found else None) or fallback


def main() -> int:
    found = detect()
    width = max(len(k) for k in found)
    for name, f in found.items():
        if f is None:
            print(f"{name:<{width}}  no encontrado")
            continue
        print(f"{name:<{width}}  {f.path}   ({f.source})")
        if f.scripts_dir:
            print(f"{'':<{width}}  scripts: {f.scripts_dir}")
        if f.config:
            print(f"{'':<{width}}  config:  {f.config}")
    return 0 if found["openocd"] else 1


if __name__ == "__main__":
    sys.exit(main())
