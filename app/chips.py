"""Catálogo de placas y chips (config/chips.yaml).

Cada entrada declara qué métodos de programación soporta y sus parámetros:
  * swd      STM32 con ST-Link y OpenOCD
  * dfu      STM32 por el bootloader USB de ST (dfu-util)
  * esptool  ESP8266 / ESP32 por su puerto serie USB (esptool)
  * avrdude  Arduino AVR por el bootloader serie o un programador ISP (avrdude)

Todo lo que termina en un comando externo sale de acá (archivo de
configuración del administrador), nunca de texto libre del usuario.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from .settings import CFG_PATH_RE, ID_RE, ConfigError, _as_int, _get

METHODS = ("swd", "dfu", "esptool", "avrdude")
# Métodos que programan a través de un puerto serie (se elige el puerto en la web).
SERIAL_METHODS = ("esptool", "avrdude")

VID_PID_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$")
TCL_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
TCL_VAR_VALUE_RE = re.compile(r"^[A-Za-z0-9_.x]{1,32}$")
# Comandos Tcl de borrado definidos por el administrador: sin sustituciones.
TCL_SAFE_CMD_RE = re.compile(r"^[A-Za-z0-9_ .x-]{1,128}$")

# ESP: chip de esptool -> (IMAGE_CHIP_ID en la cabecera del .bin, offset del bootloader).
# Valores tomados del código de esptool 4.12 (esptool/targets/*.py).
ESP_CHIPS: Dict[str, Tuple[Optional[int], int]] = {
    "esp8266": (None, 0x0),
    "esp32": (0, 0x1000),
    "esp32s2": (2, 0x1000),
    "esp32s3": (9, 0x0),
    "esp32c2": (12, 0x0),
    "esp32c3": (5, 0x0),
    "esp32c5": (23, 0x2000),
    "esp32c6": (13, 0x0),
    "esp32c61": (20, 0x0),
    "esp32h2": (16, 0x0),
    "esp32p4": (18, 0x2000),
}
ESP_BEFORE = ("default_reset", "usb_reset", "no_reset")
AVR_PART_RE = re.compile(r"^[a-z0-9]{2,20}$")
AVR_PROGRAMMERS = ("arduino", "wiring", "avr109", "urclock", "stk500v1", "stk500v2", "usbasp", "usbtiny")
# Programadores ISP: no usan bootloader ni puerto serie, y sí permiten borrado total.
AVR_ISP_PROGRAMMERS = ("usbasp", "usbtiny")

# Grupo de cada método para agrupar placas en la interfaz.
GROUP_BY_METHOD = {"swd": "STM32", "dfu": "STM32", "esptool": "Espressif (ESP)", "avrdude": "Arduino / AVR"}


@dataclass(frozen=True)
class IdCode:
    """Registro de identificación del chip (DBGMCU_IDCODE en STM32)."""
    address: int
    mask: int
    value: int


@dataclass(frozen=True)
class SwdParams:
    target_cfg: str
    interface: Optional[str]
    adapter_speed_khz: int
    flash_banks: Tuple[int, ...] = (0,)
    tcl_vars: Tuple[Tuple[str, str], ...] = ()
    erase_cmds: Tuple[str, ...] = ()
    idcode: Optional[IdCode] = None
    uid: Optional[int] = None          # dirección del número de serie único de 96 bits


@dataclass(frozen=True)
class DfuParams:
    vid_pid: str
    alt: int
    address: int


@dataclass(frozen=True)
class EsptoolParams:
    chip: str                 # nombre de chip para "esptool --chip"
    baud: int
    app_address: int          # dirección de una aplicación suelta (.bin sin bootloader)
    before: str = "default_reset"

    @property
    def image_chip_id(self) -> Optional[int]:
        return ESP_CHIPS[self.chip][0]

    @property
    def bootloader_offset(self) -> int:
        return ESP_CHIPS[self.chip][1]


@dataclass(frozen=True)
class AvrdudeParams:
    part: str                 # -p (atmega328p, atmega2560, atmega32u4...)
    programmer: str           # -c (arduino, wiring, avr109, usbasp...)
    baud: Optional[int]       # -b (None para programadores ISP)
    flash_kb: int
    bootloader_kb: float      # espacio que ocupa el bootloader al final de la flash
    touch_1200: bool = False  # Leonardo/Micro: abrir el puerto a 1200 baud para entrar al bootloader
    no_auto_erase: bool = False   # -D (Mega 2560 con bootloader stk500v2)

    @property
    def is_isp(self) -> bool:
        return self.programmer in AVR_ISP_PROGRAMMERS

    @property
    def max_program_bytes(self) -> int:
        return int(self.flash_kb * 1024 - self.bootloader_kb * 1024)


@dataclass(frozen=True)
class Chip:
    id: str
    name: str
    family: str
    default_method: str
    flash_base: Optional[int] = None
    flash_size: Optional[int] = None      # en bytes
    sector_kb: Optional[int] = None
    group: str = ""
    swd: Optional[SwdParams] = None
    dfu: Optional[DfuParams] = None
    esptool: Optional[EsptoolParams] = None
    avrdude: Optional[AvrdudeParams] = None

    @property
    def methods(self) -> List[str]:
        return [m for m in METHODS if getattr(self, m) is not None]

    @property
    def flash_end(self) -> int:
        """Primera dirección fuera de la flash (sólo chips con flash declarada)."""
        if self.flash_base is None or self.flash_size is None:
            raise ValueError(f"{self.id} no declara 'flash'")
        return self.flash_base + self.flash_size

    def extensions(self) -> List[str]:
        """Tipos de archivo que acepta la placa (según sus métodos)."""
        ext = set()
        if self.swd or self.dfu:
            ext |= {".elf", ".hex", ".bin"}
        if self.esptool:
            ext |= {".bin"}
        if self.avrdude:
            ext |= {".hex"}
        return sorted(ext)

    def to_public(self) -> Dict[str, Any]:
        """Representación para la API/interfaz."""
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "group": self.group,
            "flash_base": None if self.flash_base is None else f"0x{self.flash_base:08X}",
            "flash_size_kb": None if self.flash_size is None else self.flash_size // 1024,
            "sector_kb": self.sector_kb,
            "default_method": self.default_method,
            "methods": self.methods,
            "extensions": self.extensions(),
            "swd": None if self.swd is None else {
                "target_cfg": self.swd.target_cfg,
                "interface": self.swd.interface,
                "adapter_speed_khz": self.swd.adapter_speed_khz,
                "idcode": None if self.swd.idcode is None else f"0x{self.swd.idcode.value:03X}",
            },
            "dfu": None if self.dfu is None else {
                "vid_pid": self.dfu.vid_pid,
                "alt": self.dfu.alt,
                "address": f"0x{self.dfu.address:08X}",
            },
            "esptool": None if self.esptool is None else {
                "chip": self.esptool.chip,
                "baud": self.esptool.baud,
                "app_address": f"0x{self.esptool.app_address:X}",
            },
            "avrdude": None if self.avrdude is None else {
                "part": self.avrdude.part,
                "programmer": self.avrdude.programmer,
                "baud": self.avrdude.baud,
                "isp": self.avrdude.is_isp,
                "touch_1200": self.avrdude.touch_1200,
                "max_program_bytes": self.avrdude.max_program_bytes,
            },
        }


def _obj(raw: Any, where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: se esperaba un objeto")
    return raw


def _parse_idcode(raw: Any, where: str) -> Optional[IdCode]:
    if raw is None:
        return None
    raw = _obj(raw, where)
    return IdCode(
        address=_as_int(_get(raw, "address"), f"{where}.address", 0, 0xFFFFFFFF),
        mask=_as_int(_get(raw, "mask", 0xFFF), f"{where}.mask", 1, 0xFFFFFFFF),
        value=_as_int(_get(raw, "value"), f"{where}.value", 0, 0xFFFFFFFF),
    )


def _parse_swd(raw: Any, where: str, interfaces: Optional[Mapping[str, Any]]) -> SwdParams:
    raw = _obj(raw, where)
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
        idcode=_parse_idcode(_get(raw, "idcode"), f"{where}.idcode"),
        uid=(None if _get(raw, "uid") is None
             else _as_int(_get(raw, "uid"), f"{where}.uid", 0, 0xFFFFFFF0)),
    )


def _parse_dfu(raw: Any, where: str, flash_base: int) -> DfuParams:
    raw = _obj(raw, where)
    vid_pid = str(_get(raw, "vid_pid", "0483:df11")).lower()
    if not VID_PID_RE.fullmatch(vid_pid):
        raise ConfigError(f"{where}.vid_pid: '{vid_pid}' no tiene formato VVVV:PPPP")
    return DfuParams(
        vid_pid=vid_pid,
        alt=_as_int(_get(raw, "alt", 0), f"{where}.alt", 0, 255),
        address=_as_int(_get(raw, "address", flash_base), f"{where}.address", 0, 0xFFFFFFFF),
    )


def _parse_esptool(raw: Any, where: str) -> EsptoolParams:
    raw = _obj(raw, where)
    chip = str(_get(raw, "chip", ""))
    if chip not in ESP_CHIPS:
        raise ConfigError(f"{where}.chip: '{chip}' no es un chip de esptool conocido "
                          f"({', '.join(ESP_CHIPS)})")
    before = str(_get(raw, "before", "default_reset"))
    if before not in ESP_BEFORE:
        raise ConfigError(f"{where}.before: '{before}' no válido ({', '.join(ESP_BEFORE)})")
    default_app = 0x0 if chip == "esp8266" else 0x10000
    return EsptoolParams(
        chip=chip,
        baud=_as_int(_get(raw, "baud", 460800), f"{where}.baud", 9600, 5_000_000),
        app_address=_as_int(_get(raw, "app_address", default_app), f"{where}.app_address", 0, 0x1000000),
        before=before,
    )


def _parse_avrdude(raw: Any, where: str) -> AvrdudeParams:
    raw = _obj(raw, where)
    part = str(_get(raw, "part", ""))
    if not AVR_PART_RE.fullmatch(part):
        raise ConfigError(f"{where}.part: '{part}' no es un nombre de parte válido")
    programmer = str(_get(raw, "programmer", "arduino"))
    if programmer not in AVR_PROGRAMMERS:
        raise ConfigError(f"{where}.programmer: '{programmer}' no permitido ({', '.join(AVR_PROGRAMMERS)})")
    baud = _get(raw, "baud")
    if programmer not in AVR_ISP_PROGRAMMERS:
        baud = _as_int(baud if baud is not None else 115200, f"{where}.baud", 300, 2_000_000)
    elif baud is not None:
        baud = _as_int(baud, f"{where}.baud", 300, 2_000_000)
    bootloader_kb = float(_get(raw, "bootloader_kb", 0.5 if programmer == "arduino" else 0))
    if programmer in AVR_ISP_PROGRAMMERS:
        bootloader_kb = float(_get(raw, "bootloader_kb", 0))
    if not 0 <= bootloader_kb <= 16:
        raise ConfigError(f"{where}.bootloader_kb: fuera de rango")
    return AvrdudeParams(
        part=part,
        programmer=programmer,
        baud=baud,
        flash_kb=_as_int(_get(raw, "flash_kb"), f"{where}.flash_kb", 1, 1024),
        bootloader_kb=bootloader_kb,
        touch_1200=bool(_get(raw, "touch_1200", False)),
        no_auto_erase=bool(_get(raw, "no_auto_erase", False)),
    )


STM32_FLASH_BASE = 0x08000000
STM32_DFU = {"vid_pid": "0483:df11", "alt": 0}


def _expand_stm32(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Las líneas de "stm32:" (familia + ID + flash) como entradas completas de "chips:".

    Lo común de cada familia (target_cfg, dónde está el ID, uid, interfaz, velocidad,
    si el bootloader tiene USB DFU...) está en "stm32_families:"; la línea puede pisarlo."""
    families = data.get("stm32_families") or {}
    lines = data.get("stm32") or []
    if not isinstance(families, dict) or not isinstance(lines, list):
        raise ConfigError("chips.yaml: 'stm32_families' es un objeto y 'stm32' una lista")
    out = []
    for index, line in enumerate(lines):
        line = _obj(line, f"stm32[{index}]")
        where = f"stm32[{index}] ({line.get('id', 'sin id')})"
        fam_name = str(line.get("family", ""))
        fam = families.get(fam_name)
        if not isinstance(fam, dict):
            raise ConfigError(f"{where}: la familia '{fam_name}' no está en stm32_families")
        for key in ("target_cfg", "idcode_address"):
            if key not in fam:
                raise ConfigError(f"stm32_families.{fam_name}: falta '{key}'")
        if "dev_id" not in line:
            raise ConfigError(f"{where}: falta dev_id (el ID del chip, DBGMCU_IDCODE & 0xFFF)")
        if "flash_kb" not in line:
            raise ConfigError(f"{where}: falta flash_kb")

        def pick(key: str, default: Any = None) -> Any:
            return line[key] if key in line else fam.get(key, default)

        swd: Dict[str, Any] = {
            "target_cfg": fam["target_cfg"],
            "adapter_speed_khz": pick("adapter_speed_khz", 4000),
            "flash_banks": pick("flash_banks", [0]),
            "idcode": {"address": fam["idcode_address"], "value": line["dev_id"]},
        }
        for key in ("interface", "tcl_vars", "uid"):
            if pick(key) is not None:
                swd[key] = pick(key)
        methods: Dict[str, Any] = {"swd": swd}
        if pick("dfu", False):
            methods["dfu"] = dict(STM32_DFU)
        flash: Dict[str, Any] = {"base": STM32_FLASH_BASE, "size_kb": line["flash_kb"]}
        if pick("sector_kb") is not None:
            flash["sector_kb"] = pick("sector_kb")
        core = fam.get("core")
        out.append({"id": line.get("id"), "name": line.get("name", line.get("id")),
                    "family": f"{fam_name} ({core})" if core else fam_name, "group": line.get("group", "STM32"),
                    "flash": flash, "default_method": "swd", "methods": methods})
    return out


def parse_chips(data: Any, interfaces: Optional[Mapping[str, Any]] = None) -> List[Chip]:
    """Valida el contenido de chips.yaml. `interfaces` permite verificar referencias."""
    if isinstance(data, dict):
        entries = data.get("chips")
        if isinstance(entries, list):
            entries = entries + _expand_stm32(data)
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

        methods = _get(raw, "methods", {})
        if not isinstance(methods, dict) or not methods:
            raise ConfigError(f"{where}: 'methods' debe declarar al menos un método "
                              f"({', '.join(METHODS)})")
        unknown = set(methods) - set(METHODS)
        if unknown:
            raise ConfigError(f"{where}: métodos desconocidos {sorted(unknown)} (válidos: {', '.join(METHODS)})")

        # La flash (base y tamaño) es obligatoria para los métodos de STM32.
        flash = _get(raw, "flash")
        flash_base = size_kb = sector_kb = None
        if flash is not None or "swd" in methods or "dfu" in methods:
            if not isinstance(flash, dict):
                raise ConfigError(f"{where}.flash: se esperaba un objeto {{base, size_kb}}")
            flash_base = _as_int(_get(flash, "base"), f"{where}.flash.base", 0, 0xFFFFFFFF)
            size_kb = _as_int(_get(flash, "size_kb"), f"{where}.flash.size_kb", 1, 1024 * 1024)
            sector_kb = _get(flash, "sector_kb")
            if sector_kb is not None:
                sector_kb = _as_int(sector_kb, f"{where}.flash.sector_kb", 1, 1024)

        swd = _parse_swd(methods["swd"], f"{where}.methods.swd", interfaces) if "swd" in methods else None
        dfu = _parse_dfu(methods["dfu"], f"{where}.methods.dfu", flash_base) if "dfu" in methods else None
        esp = _parse_esptool(methods["esptool"], f"{where}.methods.esptool") if "esptool" in methods else None
        avr = _parse_avrdude(methods["avrdude"], f"{where}.methods.avrdude") if "avrdude" in methods else None

        first = next(m for m in METHODS if m in methods)
        default_method = str(_get(raw, "default_method", first))
        if default_method not in methods:
            raise ConfigError(f"{where}: default_method '{default_method}' no está en 'methods'")

        chips.append(Chip(
            id=cid,
            name=str(_get(raw, "name", cid)),
            family=str(_get(raw, "family", "")),
            group=str(_get(raw, "group", GROUP_BY_METHOD[first])),
            default_method=default_method,
            flash_base=flash_base,
            flash_size=None if size_kb is None else size_kb * 1024,
            sector_kb=sector_kb,
            swd=swd,
            dfu=dfu,
            esptool=esp,
            avrdude=avr,
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
