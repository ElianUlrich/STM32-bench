"""Estado del sistema (IP, uptime, temperatura, RAM, disco) y apagado/reinicio.

Apagar y reiniciar usan sudo con una regla de /etc/sudoers.d que permite
SOLO poweroff y reboot (ruta absoluta) sin argumentos.
"""
from __future__ import annotations

import ipaddress
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


TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
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


def host_ipv4() -> List[str]:
    """IPv4 de esta computadora sin saber de qué interfaz (Windows y otros: getaddrinfo)."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos if not i[4][0].startswith("127.")})


def pick_tailscale(addresses: List[str]) -> Optional[str]:
    """La IP de Tailscale (rango 100.64.0.0/10) entre las de la computadora."""
    for ip in addresses:
        if ipaddress.ip_address(ip) in TAILSCALE_NET:
            return ip
    return None


def _tailscale_cli_ip() -> Optional[str]:
    """`tailscale ip -4` (Windows y macOS: la IP del adaptador no siempre aparece por getaddrinfo)."""
    candidates = [shutil.which("tailscale")]
    if sys.platform == "win32":
        candidates.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe"))
    for exe in candidates:
        if not exe or not os.path.isfile(exe):
            continue
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5,
                                 stdin=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        ip = pick_tailscale([line.strip() for line in out.splitlines() if line.strip().count(".") == 3])
        if ip:
            return ip
    return None


def tailscale_ipv4(interface: str) -> Optional[str]:
    """IP de Tailscale: la de la interfaz (Linux) o la que informa Tailscale (Windows, macOS)."""
    if sys.platform.startswith("linux"):
        return interface_ipv4(interface)
    return _tailscale_cli_ip() or pick_tailscale(host_ipv4())


def os_name() -> str:
    """"Windows 11 (compilación 26200)": platform.release() dice "10" también en Windows 11."""
    if sys.platform == "win32":
        build = sys.getwindowsversion().build
        return f"Windows {'11' if build >= 22000 else platform.release()} (compilación {build})"
    return platform.platform(terse=True)


def list_ipv4() -> List[Dict[str, str]]:
    """Todas las interfaces con IPv4 (excepto loopback)."""
    if not sys.platform.startswith("linux"):
        ips = host_ipv4()
        ts = _tailscale_cli_ip()
        if ts and ts not in ips:
            ips.append(ts)
        return [{"interface": "Tailscale" if pick_tailscale([ip]) else "red", "ip": ip} for ip in ips]
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
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    line = _read_first_line(Path("/proc/uptime"))
    return float(line.split()[0]) if line else None


def _windows_memory() -> Optional[Dict[str, int]]:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return None
    return {"total": st.ullTotalPhys, "used": st.ullTotalPhys - st.ullAvailPhys, "available": st.ullAvailPhys}


def memory() -> Optional[Dict[str, int]]:
    if sys.platform == "win32":
        return _windows_memory()
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
            "mac": {"interface": "eth0", "address": "02:5e:42:00:00:01", "fixed": False},
        }
    try:
        load = list(os.getloadavg())
    except (OSError, AttributeError):
        load = None
    linux = sys.platform.startswith("linux")
    return {
        "hostname": socket.gethostname(),
        "kernel": platform.release() if linux else os_name(),
        "addresses": list_ipv4(),
        "tailscale_ip": tailscale_ipv4(tailscale_iface),
        "uptime_s": uptime_s(),
        "load": load,
        "memory": memory(),
        "temperatures": temperatures() if linux else [],
        "disk_root": disk(Path("/") if linux else Path(os.environ.get("SystemDrive", "C:") + "\\")),
        "disk_projects": disk(projects_dir),
        "mac": mac_info() if linux else None,
    }


def mac_info(interface: str = "eth0") -> Optional[Dict[str, object]]:
    """MAC de la red y si está fijada con scripts/fijar_mac.sh (la xM la cambia en cada arranque)."""
    address = _read_first_line(Path(f"/sys/class/net/{interface}/address"))
    if address is None:
        return None
    return {"interface": interface, "address": address,
            "fixed": Path("/etc/stm32-bench/mac.conf").is_file()}


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
