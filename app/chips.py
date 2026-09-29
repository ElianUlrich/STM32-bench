"""Catálogo de chips (config/chips.yaml).

Cada chip declara qué métodos de programación soporta (swd, dfu) y los
parámetros de cada uno. Todo lo que termina en un comando externo sale de acá
(archivo de configuración del administrador), nunca de texto libre del usuario.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from .settings import CFG_PATH_RE, ID_RE, ConfigError, _as_int, _get

METHODS = ("swd", "dfu")
VID_PID_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$")
TCL_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
TCL_VAR_VALUE_RE = re.compile(r"^[A-Za-z0-9_.x]{1,32}$")
# Comandos Tcl de borrado definidos por el administrador: sin sustituciones.
TCL_SAFE_CMD_RE = re.compile(r"^[A-Za-z0-9_ .x-]{1,128}$")


@dataclass(frozen=True)
class SwdParams:
    target_cfg: str
    interface: Optional[str]
    adapter_speed_khz: int
    flash_banks: Tuple[int, ...] = (0,)
    tcl_vars: Tuple[Tuple[str, str], ...] = ()
    erase_cmds: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DfuParams:
    vid_pid: str
    alt: int
    address: int


@dataclass(frozen=True)
class Chip:
    id: str
    name: str
    family: str
    flash_base: int
    flash_size: int          # en bytes
    sector_kb: Optional[int]
    default_method: str
    swd: Optional[SwdParams] = None
    dfu: Optional[DfuParams] = None

    @property
    def methods(self) -> List[str]:
        return [m for m in METHODS if getattr(self, m) is not None]

    @property
    def flash_end(self) -> int:
        """Primera dirección fuera de la flash."""
        return self.flash_base + self.flash_size

    def to_public(self) -> Dict[str, Any]:
        """Representación para la API/interfaz."""
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "flash_base": f"0x{self.flash_base:08X}",
            "flash_size_kb": self.flash_size // 1024,
            "sector_kb": self.sector_kb,
            "default_method": self.default_method,
            "methods": self.methods,
            "swd": None if self.swd is None else {
                "target_cfg": self.swd.target_cfg,
                "interface": self.swd.interface,
                "adapter_speed_khz": self.swd.adapter_speed_khz,
            },
            "dfu": None if self.dfu is None else {
                "vid_pid": self.dfu.vid_pid,
                "alt": self.dfu.alt,
                "address": f"0x{self.dfu.address:08X}",
            },
        }


def _parse_swd(raw: Any, where: str, interfaces: Optional[Mapping[str, Any]]) -> SwdParams:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: se esperaba un objeto")
    target_cfg = str(_get(raw, "target_cfg", ""))
    if not CFG_PATH_RE.fullmatch(target_cfg) or ".." in target_cfg:
        raise ConfigError(f"{where}.target_cfg: ruta inválida '{target_cfg}'")
    interface = _get(raw, "interface")
    if interface is not None:
        interface = str(interface)
        if interfaces is not None and interface not in interfaces:
            raise ConfigError(f"{where}.interface: '{interface}' no está definida en bench.yaml")
    banks = _get(raw, "flash_banks", [0])
    if not isinstance(banks, list) or not banks:
        raise ConfigError(f"{where}.flash_banks: se esperaba una lista no vacía")
    tcl_vars = []
    for name, value in (_get(raw, "tcl_vars", {}) or {}).items():
        if not TCL_VAR_NAME_RE.fullmatch(str(name)) or not TCL_VAR_VALUE_RE.fullmatch(str(value)):
            raise ConfigError(f"{where}.tcl_vars: variable inválida {name}={value}")
        tcl_vars.append((str(name), str(value)))
    erase_cmds = []
    for cmd in _get(raw, "erase_cmds", []) or []:
        if not TCL_SAFE_CMD_RE.fullmatch(str(cmd)):
            raise ConfigError(f"{where}.erase_cmds: comando no permitido '{cmd}'")
        erase_cmds.append(str(cmd))
    return SwdParams(
        target_cfg=target_cfg,
        interface=interface,
        adapter_speed_khz=_as_int(_get(raw, "adapter_speed_khz", 1000), f"{where}.adapter_speed_khz", 5, 24000),
        flash_banks=tuple(_as_int(b, f"{where}.flash_banks", 0, 15) for b in banks),
        tcl_vars=tuple(tcl_vars),
        erase_cmds=tuple(erase_cmds),
    )


def _parse_dfu(raw: Any, where: str, flash_base: int) -> DfuParams:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: se esperaba un objeto")
    vid_pid = str(_get(raw, "vid_pid", "0483:df11")).lower()
    if not VID_PID_RE.fullmatch(vid_pid):
        raise ConfigError(f"{where}.vid_pid: '{vid_pid}' no tiene formato VVVV:PPPP")
    return DfuParams(
        vid_pid=vid_pid,
        alt=_as_int(_get(raw, "alt", 0), f"{where}.alt", 0, 255),
        address=_as_int(_get(raw, "address", flash_base), f"{where}.address", 0, 0xFFFFFFFF),
    )


def parse_chips(data: Any, interfaces: Optional[Mapping[str, Any]] = None) -> List[Chip]:
    """Valida el contenido de chips.yaml. `interfaces` permite verificar referencias."""
    if isinstance(data, dict):
        entries = data.get("chips")
    else:
        entries = data
    if not isinstance(entries, list) or not entries:
        raise ConfigError("chips.yaml: se esperaba una lista 'chips' con al menos un chip")

    chips: List[Chip] = []
    seen = set()
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ConfigError(f"chips[{index}]: se esperaba un objeto")
        cid = str(_get(raw, "id", ""))
        where = f"chips[{index}] ({cid or 'sin id'})"
        if not ID_RE.fullmatch(cid):
            raise ConfigError(f"{where}: 'id' inválido (usar minúsculas, números, - y _)")
        if cid in seen:
            raise ConfigError(f"{where}: id duplicado")
        seen.add(cid)

        flash = _get(raw, "flash", {})
        if not isinstance(flash, dict):
            raise ConfigError(f"{where}.flash: se esperaba un objeto {{base, size_kb}}")
        flash_base = _as_int(_get(flash, "base"), f"{where}.flash.base", 0, 0xFFFFFFFF)
        size_kb = _as_int(_get(flash, "size_kb"), f"{where}.flash.size_kb", 1, 1024 * 1024)
        sector_kb = _get(flash, "sector_kb")
        if sector_kb is not None:
            sector_kb = _as_int(sector_kb, f"{where}.flash.sector_kb", 1, 1024)

        methods = _get(raw, "methods", {})
        if not isinstance(methods, dict) or not methods:
            raise ConfigError(f"{where}: 'methods' debe declarar al menos swd o dfu")
        unknown = set(methods) - set(METHODS)
        if unknown:
            raise ConfigError(f"{where}: métodos desconocidos {sorted(unknown)} (válidos: swd, dfu)")

        swd = _parse_swd(methods["swd"], f"{where}.methods.swd", interfaces) if "swd" in methods else None
        dfu = _parse_dfu(methods["dfu"], f"{where}.methods.dfu", flash_base) if "dfu" in methods else None

        default_method = str(_get(raw, "default_method", "swd" if swd else "dfu"))
        if default_method not in methods:
            raise ConfigError(f"{where}: default_method '{default_method}' no está en 'methods'")

        chips.append(Chip(
            id=cid,
            name=str(_get(raw, "name", cid)),
            family=str(_get(raw, "family", "")),
            flash_base=flash_base,
            flash_size=size_kb * 1024,
            sector_kb=sector_kb,
            default_method=default_method,
            swd=swd,
            dfu=dfu,
        ))
    return chips


def load_chips(path: Path, interfaces: Optional[Mapping[str, Any]] = None) -> List[Chip]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise ConfigError(f"No se encontró {path}")
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML inválido: {exc}")
    return parse_chips(data, interfaces)


@dataclass
class CfgCheck:
    """Resultado de verificar los .cfg contra el OpenOCD instalado."""
    scripts_dir: Optional[str]
    missing: List[Dict[str, str]] = field(default_factory=list)
    checked: bool = False

    def to_public(self) -> Dict[str, Any]:
        return {"scripts_dir": self.scripts_dir, "missing": self.missing, "checked": self.checked}


def check_cfg_files(chips: List[Chip], interfaces: Mapping[str, Any], scripts_dir: Optional[Path]) -> CfgCheck:
    """Verifica que existan los .cfg de target e interfaz en los scripts de OpenOCD."""
    if scripts_dir is None or not Path(scripts_dir).is_dir():
        return CfgCheck(scripts_dir=str(scripts_dir) if scripts_dir else None, checked=False)
    result = CfgCheck(scripts_dir=str(scripts_dir), checked=True)
    for iface in interfaces.values():
        if not (Path(scripts_dir) / iface.cfg).is_file():
            result.missing.append({"kind": "interface", "id": iface.id, "cfg": iface.cfg})
    reported = set()
    for chip in chips:
        if chip.swd and not (Path(scripts_dir) / chip.swd.target_cfg).is_file():
            key = (chip.id, chip.swd.target_cfg)
            if key not in reported:
                reported.add(key)
                result.missing.append({"kind": "target", "id": chip.id, "cfg": chip.swd.target_cfg})
    return result
