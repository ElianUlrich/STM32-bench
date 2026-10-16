"""Qué placa y qué sistema hay del otro lado del SSH, y qué hace falta instalar.

`PROBE_SCRIPT` corre en la placa (sólo lee: no instala ni cambia nada) y devuelve
líneas clave=valor. `analyze()` las convierte en un plan: placa, sistema, pasos
con su tiempo estimado y avisos. El usuario puede corregir la placa o el sistema
detectados (`board_id`, `os_key`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PROBE_SCRIPT = r"""
v() { printf '%s=%s\n' "$1" "$2"; }
v model "$(tr -d '\000' < /proc/device-tree/model 2>/dev/null)"
[ -r /etc/os-release ] && . /etc/os-release
v os_id "${ID:-}"
v os_version "${VERSION_ID:-}"
v os_pretty "${PRETTY_NAME:-}"
v arch "$(uname -m)"
v mem_kb "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
v disk_free_kb "$(df -Pk / | awk 'NR==2 {print $4}')"
if [ -x /opt/python3.9/bin/python3.9 ]; then py=/opt/python3.9/bin/python3.9; else py=python3; fi
v python "$($py -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
v glibc "$(ldd --version 2>&1 | head -n 1 | awk '{print $NF}')"
v tailscale_ip "$(tailscale ip -4 2>/dev/null | head -n 1)"
v openocd "$([ -x /usr/local/bin/openocd ] && /usr/local/bin/openocd --version 2>&1 | head -n 1)"
v openocd_h5 "$([ -f /usr/local/share/openocd/scripts/target/stm32h5x.cfg ] && echo 1 || echo 0)"
v gdb "$([ -x /usr/local/bin/arm-none-eabi-gdb ] && /usr/local/bin/arm-none-eabi-gdb --version 2>&1 | head -n 1)"
if [ -f /etc/systemd/system/stm32-bench.service ]; then v bench "$(systemctl is-active stm32-bench 2>/dev/null)"; else v bench none; fi
v user "$(id -un)"
v home "$HOME"
"""


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    match: str                     # expresión sobre /proc/device-tree/model
    openocd_min: int               # compilar OpenOCD en la placa
    gdb_min: int                   # compilar GDB en la placa
    python_min: int                # compilar Python 3.9 (sistemas viejos)
    notes: Tuple[str, ...] = ()


BOARDS: Tuple[Board, ...] = (
    Board("beagleboard-xm", "BeagleBoard-xM", r"BeagleBoard[- ]?xM", 60, 300, 90,
          ("Los GPIO de la BeagleBoard-xM son de 1,8 V: para manejar BOOT0/NRST hace falta un adaptador de "
           "nivel (ver README).", "La xM no tiene MAC fija: el instalador la fija para que no cambie la IP.")),
    Board("beaglebone-black", "BeagleBone Black / Green", r"BeagleBone (Black|Green|Blue)|AM335x BeagleBone",
          60, 300, 90, ("Los GPIO de la BeagleBone son de 3,3 V (sin adaptador para BOOT0/NRST).",)),
    Board("pocketbeagle", "PocketBeagle", r"PocketBeagle", 60, 300, 90),
    Board("beagle-64", "BeagleBone AI-64 / BeaglePlay / BeagleY-AI", r"AI-64|BeaglePlay|BeagleY|BeagleV",
          15, 90, 20),
    Board("raspberry-pi", "Raspberry Pi", r"Raspberry Pi", 20, 120, 30),
    Board("pc-linux", "PC con Linux", r"^$", 5, 25, 10),
    Board("generica", "Otra placa con Linux", r"", 45, 240, 60),
)


@dataclass(frozen=True)
class OsProfile:
    key: str
    label: str
    os_id: str
    version: str
    supported: bool = True
    note: Optional[str] = None


OSES: Tuple[OsProfile, ...] = (
    OsProfile("ubuntu-16.04", "Ubuntu 16.04 (imagen de rcn-ee de 2017)", "ubuntu", "16.04",
              note="Sistema viejo: se compila Python 3.9 (probado en la BeagleBoard-xM)."),
    OsProfile("ubuntu-18.04", "Ubuntu 18.04", "ubuntu", "18.04"),
    OsProfile("ubuntu-20.04", "Ubuntu 20.04", "ubuntu", "20.04"),
    OsProfile("ubuntu-22.04", "Ubuntu 22.04", "ubuntu", "22.04"),
    OsProfile("ubuntu-24.04", "Ubuntu 24.04", "ubuntu", "24.04"),
    OsProfile("debian-10", "Debian 10 (Buster)", "debian", "10",
              note="Trae Python 3.7: se compila Python 3.9 (no probado en este sistema)."),
    OsProfile("debian-11", "Debian 11 (Bullseye)", "debian", "11"),
    OsProfile("debian-12", "Debian 12 (Bookworm)", "debian", "12"),
    OsProfile("debian-13", "Debian 13 (Trixie)", "debian", "13"),
    OsProfile("otro", "Otro Linux (no probado)", "", "", note="Sistema no probado: puede fallar algún paso."),
)

ARCHES = {"armv7l": "ARM de 32 bits", "aarch64": "ARM de 64 bits", "x86_64": "PC (x86 de 64 bits)"}
# Binarios precompilados (Releases de GitHub): arquitectura -> glibc mínima con la que se compilaron.
PREBUILT = {"armv7l": ("armhf", (2, 23)), "aarch64": ("arm64", (2, 31))}


@dataclass(frozen=True)
class Step:
    name: str
    minutes: int
    detail: str = ""


@dataclass
class Analysis:
    info: Dict[str, str]
    board: Board
    os: OsProfile
    arch: str
    supported: bool
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    prebuilt: Optional[str] = None             # "armhf"/"arm64" si se usan precompilados
    prebuilt_available: Optional[str] = None
    mode: str = "completo"                     # "completo" (desde cero) o "app" (actualizar)

    def step(self, name: str) -> Step:
        return next(s for s in self.steps if s.name == name)

    @property
    def total_minutes(self) -> int:
        return sum(s.minutes for s in self.steps)


def parse(text: str) -> Dict[str, str]:
    info = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[a-z0-9_]+", key):
            info[key] = value.strip()
    return info


def _version(text: str) -> Tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text or "")[:3])


def detect_board(info: Dict[str, str]) -> Board:
    model = info.get("model", "")
    if not model:
        return board_by_id("pc-linux" if info.get("arch") == "x86_64" else "generica")
    for b in BOARDS:
        if b.match and re.search(b.match, model, re.I):
            return b
    return board_by_id("generica")


def board_by_id(board_id: str) -> Board:
    return next(b for b in BOARDS if b.id == board_id)


def detect_os(info: Dict[str, str]) -> OsProfile:
    os_id, version = info.get("os_id", ""), info.get("os_version", "")
    for o in OSES:
        if o.os_id and o.os_id == os_id and version.split(".")[:2] == o.version.split(".")[:2]:
            return o
    if os_id == "raspbian":                                 # Raspberry Pi OS = Debian
        return detect_os(dict(info, os_id="debian"))
    return os_by_key("otro")


def os_by_key(key: str) -> OsProfile:
    return next(o for o in OSES if o.key == key)


def analyze(info: Dict[str, str], board_id: Optional[str] = None, os_key: Optional[str] = None,
            prebuilt: bool = True, with_gdb: bool = True) -> Analysis:
    board = board_by_id(board_id) if board_id else detect_board(info)
    os_ = os_by_key(os_key) if os_key else detect_os(info)
    arch = info.get("arch", "")
    a = Analysis(info=info, board=board, os=os_, arch=arch, supported=True)

    # --- ¿Se puede? ------------------------------------------------------------
    os_id, version = info.get("os_id", ""), _version(info.get("os_version", ""))
    if os_id == "ubuntu" and version and version < (16, 4):
        a.blockers.append(f"Ubuntu {info.get('os_version')} es demasiado viejo: hace falta 16.04 o más nuevo.")
    if os_id == "debian" and version and version < (10,):
        a.blockers.append(f"Debian {info.get('os_version')} es demasiado viejo: hace falta 10 o más nuevo.")
    if arch not in ARCHES:
        a.blockers.append(f"Procesador no soportado ({arch or 'desconocido'}): hace falta ARM o x86 de 64 bits.")
    a.supported = not a.blockers

    # --- Binarios precompilados ---------------------------------------------------
    target = PREBUILT.get(arch)
    if target and _version(info.get("glibc", "")) >= target[1]:
        a.prebuilt_available = target[0]
    a.prebuilt = a.prebuilt_available if prebuilt else None

    # --- Qué ya está --------------------------------------------------------------
    has_openocd = bool(info.get("openocd")) and info.get("openocd_h5") == "1"
    has_bench = info.get("bench", "none") not in ("none", "")
    a.mode = "app" if (has_openocd and has_bench) else "completo"

    # --- Pasos ---------------------------------------------------------------------
    if a.mode == "completo":
        a.steps.append(Step("Sistema base", 5, "paquetes, partición en toda la microSD"))
        if not info.get("tailscale_ip"):
            a.steps.append(Step("Tailscale", 3, "hay que autorizar la placa en el navegador"))
        if _version(info.get("python", "")) < (3, 9):
            a.steps.append(Step("Python 3.9", board.python_min, "se compila (el sistema trae uno viejo)"))
        if not has_openocd:
            a.steps.append(Step("OpenOCD de ST", 2 if a.prebuilt else board.openocd_min,
                                "precompilado" if a.prebuilt else "se compila en la placa"))
    a.steps.append(Step("Aplicación", 10 if board.python_min >= 60 else 4, "servicio web y configuración"))
    if a.mode == "completo" and with_gdb and not info.get("gdb"):
        a.steps.append(Step("GDB para Depurar", 3 if a.prebuilt else board.gdb_min,
                            "precompilado" if a.prebuilt else "se compila en la placa"))

    # --- Avisos -------------------------------------------------------------------
    if os_.note:
        a.warnings.append(os_.note)
    a.warnings.extend(board.notes)
    try:
        free_gb = int(info.get("disk_free_kb", "0")) / 1024 / 1024
        mem_mb = int(info.get("mem_kb", "0")) / 1024
    except ValueError:
        free_gb, mem_mb = 0.0, 0.0
    if free_gb and free_gb < 2:
        a.warnings.append(f"Poco espacio libre en la microSD ({free_gb:.1f} GB): el instalador agranda la "
                          "partición si la tarjeta es más grande; si no, usá una de 8 GB o más.")
    compiles = a.mode == "completo" and not a.prebuilt
    if compiles and mem_mb and mem_mb < 1024:
        a.warnings.append(f"Con {mem_mb:.0f} MB de RAM la compilación usa swap temporal en la microSD.")
    if target and not a.prebuilt_available:
        a.warnings.append("No hay binarios precompilados para este sistema (glibc vieja): se compila en la placa.")
    return a
