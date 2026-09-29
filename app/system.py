"""Estado del sistema (IP, uptime, temperatura, RAM, disco) y apagado/reinicio.

Apagar y reiniciar usan sudo con una regla de /etc/sudoers.d que permite
SOLO poweroff y reboot (ruta absoluta) sin argumentos.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

def _sbin(name: str) -> str:
    """Ruta absoluta del comando (Debian con /usr fusionado: /usr/sbin; si no, /sbin).

    Debe coincidir exactamente con la ruta que install.sh pone en sudoers.
    """
    for base in ("/usr/sbin", "/sbin"):
        if os.path.exists(f"{base}/{name}"):
            return f"{base}/{name}"
    return f"/usr/sbin/{name}"


POWEROFF_CMD = ["sudo", "-n", _sbin("poweroff")]
REBOOT_CMD = ["sudo", "-n", _sbin("reboot")]


def interface_ipv4(name: str) -> Optional[str]:
    """IPv4 de una interfaz (Linux, ioctl SIOCGIFADDR). None si no existe."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = struct.pack("256s", name[:15].encode())
            result = fcntl.ioctl(sock.fileno(), 0x8915, packed)  # SIOCGIFADDR
            return socket.inet_ntoa(result[20:24])
    except (OSError, ImportError):
        return None


def list_ipv4() -> List[Dict[str, str]]:
    """Todas las interfaces con IPv4 (excepto loopback)."""
    addresses = []
    try:
        names = [name for _, name in socket.if_nameindex()]
    except (OSError, AttributeError):
        names = []
    for name in names:
        if name == "lo":
            continue
        ip = interface_ipv4(name)
        if ip:
            addresses.append({"interface": name, "ip": ip})
    return addresses


def _read_first_line(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return None


def uptime_s() -> Optional[float]:
    line = _read_first_line(Path("/proc/uptime"))
    return float(line.split()[0]) if line else None


def memory() -> Optional[Dict[str, int]]:
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.strip().split()[0]) * 1024
        total = info["MemTotal"]
        available = info.get("MemAvailable", info.get("MemFree", 0))
        return {"total": total, "used": total - available, "available": available}
    except (OSError, KeyError, ValueError):
        return None


def temperatures() -> List[Dict[str, object]]:
    """Sensores térmicos del kernel (el DM3730 puede no exponer ninguno)."""
    result = []
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        raw = _read_first_line(zone / "temp")
        if raw is None:
            continue
        try:
            celsius = int(raw) / 1000.0
        except ValueError:
            continue
        result.append({"name": _read_first_line(zone / "type") or zone.name, "celsius": round(celsius, 1)})
    return result


def disk(path: Path) -> Optional[Dict[str, int]]:
    try:
        usage = shutil.disk_usage(str(path))
        return {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        return None


def collect(projects_dir: Path, tailscale_iface: str, mock: bool) -> Dict[str, object]:
    """Resumen del estado del sistema para la interfaz."""
    if mock:
        return {
            "hostname": "beagleboard-mock",
            "kernel": "mock",
            "addresses": [{"interface": "eth0", "ip": "192.168.1.50"},
                          {"interface": tailscale_iface, "ip": "100.101.102.103"}],
            "tailscale_ip": "100.101.102.103",
            "uptime_s": time.monotonic(),
            "load": [0.12, 0.08, 0.05],
            "memory": {"total": 512 * 2**20, "used": 180 * 2**20, "available": 332 * 2**20},
            "temperatures": [{"name": "cpu (simulado)", "celsius": 47.5}],
            "disk_root": {"total": 16 * 2**30, "used": 5 * 2**30, "free": 11 * 2**30},
            "disk_projects": disk(projects_dir),
        }
    try:
        load = list(os.getloadavg())
    except (OSError, AttributeError):
        load = None
    return {
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "addresses": list_ipv4(),
        "tailscale_ip": interface_ipv4(tailscale_iface),
        "uptime_s": uptime_s(),
        "load": load,
        "memory": memory(),
        "temperatures": temperatures(),
        "disk_root": disk(Path("/")),
        "disk_projects": disk(projects_dir),
    }


class PowerError(RuntimeError):
    pass


def power_action(kind: str, mock: bool) -> str:
    """Lanza poweroff/reboot en segundo plano (el HTTP responde antes)."""
    cmd = {"poweroff": POWEROFF_CMD, "reboot": REBOOT_CMD}[kind]
    if mock:
        return "simulado: " + " ".join(cmd)
    # "sudo -n -l <cmd>" confirma que la regla de sudoers lo permite, sin ejecutarlo.
    check = subprocess.run(["sudo", "-n", "-l", cmd[-1]], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=10, shell=False)
    if check.returncode != 0:
        raise PowerError("sudo no permite ejecutar " + cmd[-1] +
                         ". Revisá /etc/sudoers.d/stm32-bench (ver README).")
    subprocess.run(["sync"], check=False, timeout=30)
    # Popen sin esperar: el sistema empieza a apagarse y esta respuesta ya salió.
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, shell=False, start_new_session=True)
    return " ".join(cmd)
