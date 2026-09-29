"""Configuración de la aplicación.

Hay dos fuentes:
  * Variables de entorno (BENCH_*): secretos, rutas y modo mock.
  * config/bench.yaml: parámetros del banco (OpenOCD, interfaces, GDB, serie,
    BootControl...). Se valida al cargar y cualquier error se reporta con un
    mensaje claro.
"""
from __future__ import annotations

import ipaddress
import os
import re
import secrets
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Identificadores (interfaces, chips, backends): minúsculas, números, - y _.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Rutas relativas a los scripts de OpenOCD (interface/x.cfg, target/y.cfg).
CFG_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)*\.cfg$")
# Nombres de transporte de OpenOCD.
TRANSPORT_RE = re.compile(r"^[a-z_]{2,32}$")
# Chips de gpiod: gpiochipN o una ruta /dev/gpiochipN.
GPIOCHIP_RE = re.compile(r"^(/dev/)?gpiochip[0-9]{1,3}$")


class ConfigError(Exception):
    """Error de configuración con mensaje pensado para el usuario."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


# ---------------------------------------------------------------------------
# bench.yaml
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InterfaceConfig:
    id: str
    cfg: str
    transport: Optional[str]
    description: str = ""


@dataclass(frozen=True)
class GpioLineConfig:
    chip: str
    line: int
    active_low: bool = False


@dataclass(frozen=True)
class BootControlConfig:
    backend: str = "manual"
    timeout_s: float = 20.0
    boot0: Optional[GpioLineConfig] = None
    nrst: Optional[GpioLineConfig] = None
    reset_pulse_ms: int = 50
    settle_ms: int = 200
    serial_port: Optional[str] = None
    serial_baud: int = 115200
    serial_command: str = ""


@dataclass(frozen=True)
class BenchConfig:
    openocd_binary: str = "/usr/local/bin/openocd"
    openocd_scripts_dir: Optional[str] = None
    openocd_default_interface: str = "stlink-dap"
    openocd_timeout_s: float = 180.0
    dfu_util_binary: str = "/usr/bin/dfu-util"
    dfu_util_timeout_s: float = 300.0
    interfaces: Dict[str, InterfaceConfig] = field(default_factory=dict)
    gdb_port: int = 3333
    gdb_bind: str = "tailscale"
    tailscale_interface: str = "tailscale0"
    max_upload_mb: int = 16
    scan_max_depth: int = 5
    serial_default_baud: int = 115200
    serial_bauds: Tuple[int, ...] = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
    history_max_entries: int = 200
    allowed_networks: Tuple[Any, ...] = ()
    session_hours: float = 12.0
    boot_control: BootControlConfig = field(default_factory=BootControlConfig)

    def openocd_scripts(self) -> Optional[Path]:
        """Directorio de scripts de OpenOCD (explícito o deducido del binario)."""
        if self.openocd_scripts_dir:
            return Path(self.openocd_scripts_dir)
        binary = shutil.which(self.openocd_binary) or self.openocd_binary
        prefix = Path(binary).resolve().parent.parent
        candidate = prefix / "share" / "openocd" / "scripts"
        return candidate if candidate.is_dir() else None


def _get(d: Dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    value = d.get(key, default)
    return default if value is None else value


def _as_int(value: Any, where: str, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        if isinstance(value, str):
            number = int(value.strip(), 0)
        elif isinstance(value, bool):
            raise ValueError
        else:
            number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: se esperaba un número entero, llegó {value!r}")
    if lo is not None and number < lo:
        raise ConfigError(f"{where}: {number} es menor que el mínimo {lo}")
    if hi is not None and number > hi:
        raise ConfigError(f"{where}: {number} es mayor que el máximo {hi}")
    return number


def _gpio_line(raw: Any, where: str) -> Optional[GpioLineConfig]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: se esperaba un objeto {{chip, line, active_low}}")
    chip = str(_get(raw, "chip", ""))
    if not GPIOCHIP_RE.fullmatch(chip):
        raise ConfigError(f"{where}.chip: '{chip}' no es un gpiochip válido (ej. gpiochip4)")
    return GpioLineConfig(
        chip=chip,
        line=_as_int(_get(raw, "line"), f"{where}.line", 0, 1023),
        active_low=bool(_get(raw, "active_low", False)),
    )


def parse_bench_config(data: Dict[str, Any]) -> BenchConfig:
    """Valida el contenido de bench.yaml y devuelve un BenchConfig."""
    if not isinstance(data, dict):
        raise ConfigError("bench.yaml: el archivo debe ser un objeto YAML")

    openocd = _get(data, "openocd", {})
    dfu = _get(data, "dfu_util", {})

    interfaces: Dict[str, InterfaceConfig] = {}
    for iid, raw in (_get(data, "interfaces", {}) or {}).items():
        where = f"interfaces.{iid}"
        if not ID_RE.fullmatch(str(iid)):
            raise ConfigError(f"{where}: id inválido")
        cfg = str(_get(raw, "cfg", ""))
        if not CFG_PATH_RE.fullmatch(cfg) or ".." in cfg:
            raise ConfigError(f"{where}.cfg: ruta inválida '{cfg}'")
        transport = _get(raw, "transport")
        if transport is not None and not TRANSPORT_RE.fullmatch(str(transport)):
            raise ConfigError(f"{where}.transport: valor inválido '{transport}'")
        interfaces[str(iid)] = InterfaceConfig(
            id=str(iid), cfg=cfg,
            transport=str(transport) if transport else None,
            description=str(_get(raw, "description", "")),
        )
    if not interfaces:
        raise ConfigError("bench.yaml: no hay ninguna interfaz definida en 'interfaces'")

    default_iface = str(_get(openocd, "default_interface", next(iter(interfaces))))
    if default_iface not in interfaces:
        raise ConfigError(f"openocd.default_interface: '{default_iface}' no está en 'interfaces'")

    gdb = _get(data, "gdb", {})
    bind = str(_get(gdb, "bind", "tailscale"))
    if bind not in ("tailscale", "all", "localhost"):
        try:
            ipaddress.ip_address(bind)
        except ValueError:
            raise ConfigError(f"gdb.bind: '{bind}' no es tailscale/all/localhost ni una IP")

    serial = _get(data, "serial", {})
    bauds = tuple(_as_int(b, "serial.bauds", 50, 12_000_000) for b in _get(serial, "bauds", [115200]))

    security = _get(data, "security", {})
    networks = []
    for net in _get(security, "allowed_networks", ["0.0.0.0/0", "::/0"]):
        try:
            networks.append(ipaddress.ip_network(str(net), strict=False))
        except ValueError:
            raise ConfigError(f"security.allowed_networks: red inválida '{net}'")

    bc = _get(data, "boot_control", {})
    backend = str(_get(bc, "backend", "manual"))
    if backend not in ("manual", "gpio", "serial_command"):
        raise ConfigError(f"boot_control.backend: '{backend}' no es manual/gpio/serial_command")
    gpio = _get(bc, "gpio", {})
    sc = _get(bc, "serial_command", {})
    boot_control = BootControlConfig(
        backend=backend,
        timeout_s=float(_get(bc, "timeout_s", 20)),
        boot0=_gpio_line(_get(gpio, "boot0"), "boot_control.gpio.boot0"),
        nrst=_gpio_line(_get(gpio, "nrst"), "boot_control.gpio.nrst"),
        reset_pulse_ms=_as_int(_get(gpio, "reset_pulse_ms", 50), "boot_control.gpio.reset_pulse_ms", 1, 5000),
        settle_ms=_as_int(_get(gpio, "settle_ms", 200), "boot_control.gpio.settle_ms", 0, 10000),
        serial_port=_get(sc, "port"),
        serial_baud=_as_int(_get(sc, "baud", 115200), "boot_control.serial_command.baud", 50, 12_000_000),
        serial_command=str(_get(sc, "command", "")),
    )
    if backend == "gpio" and (boot_control.boot0 is None or boot_control.nrst is None):
        raise ConfigError("boot_control: el backend gpio necesita 'gpio.boot0' y 'gpio.nrst'")
    if backend == "serial_command" and (not boot_control.serial_port or not boot_control.serial_command):
        raise ConfigError("boot_control: el backend serial_command necesita 'port' y 'command'")

    return BenchConfig(
        openocd_binary=str(_get(openocd, "binary", "openocd")),
        openocd_scripts_dir=_get(openocd, "scripts_dir"),
        openocd_default_interface=default_iface,
        openocd_timeout_s=float(_get(openocd, "timeout_s", 180)),
        dfu_util_binary=str(_get(dfu, "binary", "dfu-util")),
        dfu_util_timeout_s=float(_get(dfu, "timeout_s", 300)),
        interfaces=interfaces,
        gdb_port=_as_int(_get(gdb, "port", 3333), "gdb.port", 1024, 65535),
        gdb_bind=bind,
        tailscale_interface=str(_get(gdb, "tailscale_interface", "tailscale0")),
        max_upload_mb=_as_int(_get(_get(data, "uploads", {}), "max_size_mb", 16), "uploads.max_size_mb", 1, 512),
        scan_max_depth=_as_int(_get(_get(data, "scan", {}), "max_depth", 5), "scan.max_depth", 1, 20),
        serial_default_baud=_as_int(_get(serial, "default_baud", 115200), "serial.default_baud", 50, 12_000_000),
        serial_bauds=bauds,
        history_max_entries=_as_int(_get(_get(data, "history", {}), "max_entries", 200), "history.max_entries", 10, 10000),
        allowed_networks=tuple(networks),
        session_hours=float(_get(security, "session_hours", 12)),
        boot_control=boot_control,
    )


def load_bench_config(path: Path) -> BenchConfig:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        raise ConfigError(f"No se encontró {path}")
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML inválido: {exc}")
    return parse_bench_config(data)


# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    mock: bool
    username: str
    password_hash: Optional[str]
    secret_key: str
    config_dir: Path
    projects_dir: Path
    state_dir: Path
    cookie_secure: bool
    # Contraseña generada al vuelo en modo mock cuando no hay hash configurado.
    generated_password: Optional[str] = None

    @property
    def chips_file(self) -> Path:
        return self.config_dir / "chips.yaml"

    @property
    def bench_file(self) -> Path:
        return self.config_dir / "bench.yaml"

    @property
    def uploads_dir(self) -> Path:
        return self.projects_dir / UPLOADS_DIRNAME

    def mock_command(self, script: str) -> List[str]:
        """Comando para lanzar uno de los simuladores de scripts/."""
        return [sys.executable, str(REPO_ROOT / "scripts" / script)]


UPLOADS_DIRNAME = "_subidos"


def load_env_file(path: Path) -> None:
    """Carga un archivo KEY=VALUE en os.environ (sin pisar lo ya definido).

    Pensado para desarrollo (BENCH_ENV_FILE); en la placa el EnvironmentFile
    de systemd ya define las variables.
    """
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


def settings_from_env() -> Settings:
    """Construye Settings desde el entorno. Falla si faltan secretos (fuera de mock)."""
    env_file = os.environ.get("BENCH_ENV_FILE")
    if env_file:
        load_env_file(Path(env_file))

    mock = _env_bool("BENCH_MOCK")
    username = os.environ.get("BENCH_USER", "").strip() or ("dev" if mock else "")
    password_hash = os.environ.get("BENCH_PASSWORD_HASH", "").strip() or None
    secret_key = os.environ.get("BENCH_SECRET_KEY", "").strip()
    generated_password = None

    if mock:
        default_base = REPO_ROOT / "dev-data"
        default_config = REPO_ROOT / "config"
        if not password_hash:
            # Sin credenciales fijas: se genera una contraseña aleatoria por arranque.
            from .auth import hash_password
            generated_password = secrets.token_urlsafe(9)
            password_hash = hash_password(generated_password)
        if not secret_key:
            secret_key = secrets.token_urlsafe(48)
    else:
        default_base = Path("/var/lib/stm32-bench")
        default_config = Path("/etc/stm32-bench")
        missing = [name for name, val in (("BENCH_USER", username),
                                          ("BENCH_PASSWORD_HASH", password_hash),
                                          ("BENCH_SECRET_KEY", secret_key)) if not val]
        if missing:
            raise ConfigError("Faltan variables de entorno obligatorias: " + ", ".join(missing))
        if len(secret_key) < 32:
            raise ConfigError("BENCH_SECRET_KEY es demasiado corta (mínimo 32 caracteres)")

    projects_default = (default_base / "proyectos") if mock else Path("/srv/stm32-bench/proyectos")
    state_default = (default_base / "state") if mock else default_base

    return Settings(
        mock=mock,
        username=username,
        password_hash=password_hash,
        secret_key=secret_key,
        config_dir=Path(os.environ.get("BENCH_CONFIG_DIR") or default_config),
        projects_dir=Path(os.environ.get("BENCH_PROJECTS_DIR") or projects_default),
        state_dir=Path(os.environ.get("BENCH_STATE_DIR") or state_default),
        cookie_secure=_env_bool("BENCH_COOKIE_SECURE"),
        generated_password=generated_password,
    )


def client_allowed(host: Optional[str], networks: Sequence[Any]) -> bool:
    """True si la IP del cliente pertenece a alguna de las redes permitidas."""
    if not networks:
        return True
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # "testclient" u otros nombres no-IP: solo se aceptan si todo está permitido.
        return any(net.prefixlen == 0 for net in networks)
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return any(addr.version == net.version and addr in net for net in networks)
