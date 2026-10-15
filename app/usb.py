"""Dispositivos USB de la placa y control de alimentación por puerto.

* list_devices(): lo que hay enchufado, leído de /sys/bus/usb/devices (sin
  ejecutar nada): VID:PID, nombre, velocidad, en qué hub y puerto está, si es
  un adaptador de red y qué puerto serie creó. Sirve para diagnosticar (por
  ejemplo, si el STM32 en modo DFU, 0483:df11, aparece o no).

* Alimentación por puerto con uhubctl (https://github.com/mvp/uhubctl): apaga,
  prende o reinicia (apaga unos segundos y prende) un puerto de un hub que lo
  soporte. Sirve para reiniciar en frío una placa o un ST-Link colgado.

  Protecciones: nunca se corta un puerto que lleve a un adaptador de red (en
  la BeagleBoard-xM el Ethernet cuelga del mismo chip que los USB: cortarlo
  deja la placa inaccesible). En hubs "ganged" (un solo interruptor para todos
  los puertos) sólo se permite reiniciar: uhubctl corre en la placa y vuelve a
  dar energía solo, aunque la red se corte mientras tanto.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .procs import run_capture

SYSFS = Path("/sys/bus/usb/devices")
DEVICE_RE = re.compile(r"^\d+-\d+(\.\d+)*$")
LOCATION_RE = re.compile(r"^\d+(-\d+(\.\d+)*)?$")
ACTIONS = ("on", "off", "cycle")
CYCLE_DELAY_S = 3

# Dispositivos conocidos: ayuda a leer la lista.
KNOWN = {
    "0483:3744": "ST-Link V1", "0483:3748": "ST-Link V2", "0483:374b": "ST-Link V2-1",
    "0483:374d": "ST-Link V3 loader", "0483:374e": "ST-Link V3", "0483:374f": "ST-Link V3",
    "0483:3752": "ST-Link V2-1", "0483:3753": "ST-Link V3", "0483:3754": "ST-Link V3",
    "0483:df11": "STM32 en modo DFU (bootloader de ST)",
    "0483:5740": "STM32 como puerto serie (CDC)",
    "10c4:ea60": "CP210x (USB-serie)", "1a86:7523": "CH340 (USB-serie)", "1a86:55d4": "CH9102 (USB-serie)", "1a86:55d3": "CH343 (USB-serie)",
    "0403:6001": "FT232 (USB-serie)", "0403:6010": "FT2232", "0403:6015": "FT-X (USB-serie)",
    "303a:1001": "ESP32-S3/C3/C6 (USB-JTAG/serie)", "303a:0002": "ESP32-S2 (USB)",
    "16c0:05dc": "USBasp (programador AVR)", "1781:0c9f": "USBtiny (programador AVR)",
    "0424:ec00": "Ethernet de la placa (LAN9514)", "0424:9514": "Hub de la placa (LAN9514)",
}
KNOWN_VENDORS = {"2341": "Arduino", "2a03": "Arduino", "303a": "Espressif", "0483": "STMicroelectronics"}


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def describe(vid_pid: str, product: Optional[str] = None) -> str:
    if vid_pid in KNOWN:
        return KNOWN[vid_pid]
    vendor = KNOWN_VENDORS.get(vid_pid.split(":")[0])
    return " ".join(x for x in (vendor, product) if x) or "Dispositivo USB"


@dataclass
class UsbDevice:
    path: str                    # "1-2.3" (bus 1, hub en el puerto 2, puerto 3 de ese hub)
    vid_pid: str
    product: Optional[str]
    manufacturer: Optional[str]
    serial: Optional[str]
    speed: Optional[str]
    is_hub: bool
    network: List[str] = field(default_factory=list)      # interfaces de red (eth0...)
    serial_ports: List[str] = field(default_factory=list)  # /dev/ttyUSB0, /dev/ttyACM0...

    @property
    def hub(self) -> str:
        return self.path.rsplit(".", 1)[0] if "." in self.path else self.path.split("-")[0]

    @property
    def port(self) -> int:
        return int(self.path.rsplit(".", 1)[-1].split("-")[-1])

    def to_public(self) -> Dict:
        return {"path": self.path, "hub": self.hub, "port": self.port, "vid_pid": self.vid_pid,
                "product": self.product, "manufacturer": self.manufacturer, "serial": self.serial,
                "speed_mbps": self.speed, "is_hub": self.is_hub, "network": self.network,
                "serial_ports": self.serial_ports, "description": describe(self.vid_pid, self.product)}


def list_devices(root: Path = SYSFS) -> List[UsbDevice]:
    if sys.platform == "win32":
        from .winusb import present_devices
        return devices_from_windows(present_devices(), windows_serial_ports())
    devices = []
    if not root.is_dir():
        return devices
    for entry in sorted(root.iterdir(), key=lambda p: [int(x) for x in re.split(r"[-.]", p.name) if x.isdigit()]):
        if not DEVICE_RE.match(entry.name):
            continue
        vid, pid = _read(entry / "idVendor"), _read(entry / "idProduct")
        if not vid or not pid:
            continue
        net, ttys = [], []
        for sub in entry.glob("*:*"):                        # interfaces del dispositivo
            net += [p.name for p in (sub / "net").glob("*")] if (sub / "net").is_dir() else []
            ttys += [f"/dev/{p.name}" for p in sub.glob("tty?*") if p.name != "tty"]
            ttys += [f"/dev/{p.name}" for p in (sub / "tty").glob("tty*")] if (sub / "tty").is_dir() else []
        devices.append(UsbDevice(
            path=entry.name, vid_pid=f"{vid}:{pid}".lower(), product=_read(entry / "product"),
            manufacturer=_read(entry / "manufacturer"), serial=_read(entry / "serial"),
            speed=_read(entry / "speed"), is_hub=_read(entry / "bDeviceClass") == "09",
            network=sorted(set(net)), serial_ports=sorted(set(ttys))))
    return devices


_WIN_LOCATION_RE = re.compile(r"Port_#0*(\d+)\.Hub_#0*(\d+)")


def devices_from_windows(devices, serial_ports: Sequence[Dict]) -> List[UsbDevice]:
    """La lista de USB de Windows (winusb.present_devices) con el COM de cada adaptador serie.

    `serial_ports`: [{"device": "COM5", "vid": 0x10C4, "pid": 0xEA60, "serial": "0001"}] (pyserial)."""
    out = []
    for n, d in enumerate(devices):
        m = _WIN_LOCATION_RE.search(d.location or "")
        path = f"{m.group(2)}-{m.group(1)}" if m else f"0-{n + 1}"
        coms = [p["device"] for p in serial_ports
                if p.get("vid") is not None and f"{p['vid']:04x}" == d.vid and f"{p['pid']:04x}" == d.pid
                and (not d.serial or not p.get("serial") or p["serial"] == d.serial)]
        out.append(UsbDevice(path=path, vid_pid=f"{d.vid}:{d.pid}", product=d.name, manufacturer=None,
                             serial=d.serial, speed=None, is_hub=False, serial_ports=sorted(coms)))
    return out


def windows_serial_ports() -> List[Dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"device": p.device, "vid": p.vid, "pid": p.pid, "serial": p.serial_number}
            for p in list_ports.comports() if p.vid is not None]


def mock_devices() -> List[UsbDevice]:
    return [
        UsbDevice("1-2", "0424:9514", None, None, None, "480", True),
        UsbDevice("1-2.1", "0424:ec00", None, None, None, "480", False, network=["eth0"]),
        UsbDevice("1-2.2", "0483:3748", "STM32 STLink", "STMicroelectronics", "066DFF485550755187121832", "12", False),
        UsbDevice("1-2.3", "10c4:ea60", "CP2102 USB to UART", "Silicon Labs", "0001", "12", False,
                  serial_ports=["/dev/ttyUSB0"]),
        UsbDevice("1-2.4", "2341:0043", "Arduino Uno", "Arduino (www.arduino.cc)", "7553", "12", False,
                  serial_ports=["/dev/ttyACM1"]),
    ]


# ---------------------------------------------------------------------------
# Alimentación por puerto (uhubctl)
# ---------------------------------------------------------------------------

class PowerError(RuntimeError):
    """Pedido de alimentación inválido o fallido (mensaje para el usuario)."""


HUB_RE = re.compile(r"^Current status for hub (\S+) \[([0-9a-fA-F]{4}:[0-9a-fA-F]{4})[^\]]*?,\s*"
                    r"USB [\d.]+,\s*(\d+) ports?,\s*(\w+)\]")
PORT_RE = re.compile(r"^\s+Port (\d+): ([0-9a-fA-F]{4})((?: [A-Za-z0-9]+)*)(?: \[(.*)\])?\s*$")


@dataclass
class HubPort:
    number: int
    powered: bool
    connected: bool
    device: Optional[str]


@dataclass
class Hub:
    location: str
    vid_pid: str
    ports: List[HubPort]
    switching: str               # ppps (por puerto) | ganged (todos juntos) | nops (no se puede)


def parse_uhubctl(text: str) -> List[Hub]:
    hubs: List[Hub] = []
    for line in text.splitlines():
        m = HUB_RE.match(line)
        if m:
            hubs.append(Hub(location=m.group(1), vid_pid=m.group(2).lower(), ports=[], switching=m.group(4).lower()))
            continue
        m = PORT_RE.match(line)
        if m and hubs:
            words = m.group(3).split()
            hubs[-1].ports.append(HubPort(number=int(m.group(1)), powered="power" in words and "off" not in words,
                                          connected="connect" in words, device=(m.group(4) or None)))
    return hubs


MOCK_UHUBCTL = """Current status for hub 1-2 [0424:9514, USB 2.00, 5 ports, ppps]
  Port 1: 0503 power highspeed enable connect [0424:ec00]
  Port 2: 0103 power enable connect [0483:3748 STMicroelectronics STM32 STLink 066DFF485550755187121832]
  Port 3: 0103 power enable connect [10c4:ea60 Silicon Labs CP2102 USB to UART 0001]
  Port 4: 0103 power enable connect [2341:0043 Arduino (www.arduino.cc) Arduino Uno 7553]
  Port 5: 0100 power
"""


class PortPower:
    def __init__(self, binary: str = "uhubctl", mock: bool = False) -> None:
        self.binary = binary
        self.mock = mock
        self._mock_off: Dict[str, float] = {}       # "1-2:3" -> hora en que se apagó

    def _run(self, args: Sequence[str], timeout: float = 20.0) -> str:
        if sys.platform == "win32" and not self.mock:
            raise PowerError("Cortar la energía de un puerto USB no está disponible en Windows.")
        try:
            out = run_capture([self.binary] + list(args), timeout=timeout)
        except FileNotFoundError:
            raise PowerError("No está instalado uhubctl (sudo bash scripts/install.sh lo instala).")
        text = (out.stdout or "") + (out.stderr or "")
        if out.returncode != 0 and "No compatible" not in text:
            raise PowerError(f"uhubctl falló: {text.strip().splitlines()[-1] if text.strip() else out.returncode}")
        return text

    def hubs(self) -> List[Hub]:
        if self.mock:
            hubs = parse_uhubctl(MOCK_UHUBCTL)
            now = time.monotonic()
            for hub in hubs:
                for port in hub.ports:
                    off_at = self._mock_off.get(f"{hub.location}:{port.number}")
                    if off_at is not None and off_at > now - 3600:
                        port.powered = False
                        port.connected = False
            return hubs
        return parse_uhubctl(self._run([]))

    def status(self, devices: List[UsbDevice]) -> Dict:
        """Hubs con control de alimentación y, por puerto, qué hay y si se puede cortar."""
        try:
            hubs = self.hubs()
        except PowerError as exc:
            return {"available": False, "message": str(exc), "hubs": []}
        by_path = {d.path: d for d in devices}
        out = []
        for hub in hubs:
            ports = []
            for p in hub.ports:
                path = f"{hub.location}.{p.number}" if "-" in hub.location else f"{hub.location}-{p.number}"
                dev = by_path.get(path)
                reason = self.blocked_reason(hub, p.number, devices)
                ports.append({"port": p.number, "powered": p.powered, "connected": p.connected,
                              "path": path, "device": dev.to_public() if dev else None,
                              "raw": p.device, "blocked": reason,
                              "actions": [] if reason else (["on", "off", "cycle"] if hub.switching == "ppps"
                                                            else ["cycle"])})
            out.append({"location": hub.location, "vid_pid": hub.vid_pid, "switching": hub.switching,
                        "description": describe(hub.vid_pid), "ports": ports})
        message = None if out else ("No se encontró ningún hub con control de alimentación por puerto. "
                                    "Hace falta un hub compatible con uhubctl (ver la lista en su página).")
        return {"available": bool(out), "message": message, "hubs": out}

    @staticmethod
    def blocked_reason(hub: Hub, port: int, devices: List[UsbDevice]) -> Optional[str]:
        if hub.switching == "nops":
            return "Este hub no permite cortar la alimentación"
        prefix = f"{hub.location}.{port}" if "-" in hub.location else f"{hub.location}-{port}"
        for dev in devices:
            if (dev.path == prefix or dev.path.startswith(prefix + ".")) and dev.network:
                return f"Lleva a la red ({', '.join(dev.network)}): cortarlo dejaría la placa inaccesible"
        return None

    def act(self, location: str, port: int, action: str, devices: List[UsbDevice]) -> str:
        if not LOCATION_RE.match(location or ""):
            raise PowerError("Hub inválido")
        if action not in ACTIONS:
            raise PowerError("Acción inválida")
        port = int(port)
        hub = next((h for h in self.hubs() if h.location == location), None)
        if hub is None or not any(p.number == port for p in hub.ports):
            raise PowerError("Ese hub o puerto no existe (actualizá la lista)")
        reason = self.blocked_reason(hub, port, devices)
        if reason:
            raise PowerError(reason)
        if hub.switching != "ppps" and action != "cycle":
            raise PowerError("Este hub corta todos los puertos juntos: sólo se permite Reiniciar")
        if self.mock:
            key = f"{location}:{port}"
            if action == "off":
                self._mock_off[key] = time.monotonic()
            else:
                self._mock_off.pop(key, None)
            return f"simulado: uhubctl -l {location} -p {port} -a {action}"
        args = ["-l", location, "-p", str(port), "-a", action]
        if action == "cycle":
            args += ["-d", str(CYCLE_DELAY_S)]
        self._run(args, timeout=30)
        return "uhubctl " + " ".join(args)
