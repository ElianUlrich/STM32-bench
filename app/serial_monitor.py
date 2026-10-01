"""Monitor serie: lee un puerto USB-serie y lo transmite en vivo a la web.

Solo se permite abrir puertos que aparecen en el listado (lista blanca) y con
baudrates de la configuración.
"""
from __future__ import annotations

import codecs
import random
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from .events import EventBus

MOCK_PORT = "MOCK-ACM0"
# Puertos simulados: VCP de una Nucleo, un ESP32 (CP2102) y dos Arduino.
MOCK_PORTS = [
    {"device": "MOCK-ACM0", "description": "Puerto simulado: VCP de Nucleo (0483:374B)"},
    {"device": "MOCK-USB0", "description": "Puerto simulado: ESP32 DevKit, CP2102 (10C4:EA60)"},
    {"device": "MOCK-ACM1", "description": "Puerto simulado: Arduino Uno (2341:0043)"},
    {"device": "MOCK-ACM2", "description": "Puerto simulado: Arduino Leonardo (2341:8036)"},
]


class SerialError(RuntimeError):
    pass


class _MockPort:
    """Puerto simulado: emite una línea por segundo y hace eco de lo recibido."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._next = time.monotonic()
        self._count = 0
        self._lock = threading.Lock()
        self.is_open = True

    def read(self, size: int = 1) -> bytes:
        time.sleep(0.05)
        now = time.monotonic()
        with self._lock:
            if now >= self._next:
                self._count += 1
                self._next = now + 1.0
                adc = 2048 + random.randint(-40, 40)
                self._buffer += f"[mock] tick={self._count} adc={adc} temp={24 + random.random():.2f}C\r\n".encode()
            data = bytes(self._buffer[:size])
            del self._buffer[:size]
        return data

    def write(self, data: bytes) -> int:
        with self._lock:
            self._buffer += b"echo: " + data.replace(b"\r", b"").replace(b"\n", b"\\n") + b"\r\n"
        return len(data)

    def close(self) -> None:
        self.is_open = False


class SerialManager:
    def __init__(self, bus: EventBus, bauds: Sequence[int], default_baud: int, mock: bool = False) -> None:
        self.bus = bus
        self.bauds = tuple(bauds)
        self.default_baud = default_baud
        self.mock = mock
        self._port: Any = None
        self._port_name: Optional[str] = None
        self._baud: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._error: Optional[str] = None

    # --- listado ---------------------------------------------------------------
    def list_ports(self) -> List[Dict[str, str]]:
        if self.mock:
            return [dict(p) for p in MOCK_PORTS]
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        ports = []
        for info in list_ports.comports():
            # Solo puertos USB (los ttyS/ttyO internos de la placa no interesan).
            if info.vid is None:
                continue
            desc = info.description or ""
            desc += f" ({info.vid:04X}:{info.pid:04X})"
            if info.serial_number:
                desc += f" (S/N {info.serial_number})"
            ports.append({"device": info.device, "description": desc})
        ports.sort(key=lambda p: p["device"])
        return ports

    def status(self) -> Dict[str, Any]:
        return {"open": self._port is not None, "port": self._port_name, "baud": self._baud,
                "error": self._error, "bauds": list(self.bauds), "default_baud": self.default_baud}

    def _publish_status(self) -> None:
        self.bus.publish({"type": "serial_status", "status": self.status()})

    # --- abrir / cerrar --------------------------------------------------------
    def _check(self, port: str, baud: int) -> None:
        if baud not in self.bauds:
            raise SerialError(f"Baudrate no permitido: {baud}")
        allowed = {p["device"] for p in self.list_ports()}
        if port not in allowed:
            raise SerialError(f"Puerto no disponible: {port}")

    def _open_port(self, port: str, baud: int) -> Any:
        if self.mock:
            return _MockPort()
        import serial
        try:
            return serial.Serial(port, baudrate=baud, timeout=0.1, write_timeout=2)
        except serial.SerialException as exc:
            raise SerialError(f"No se pudo abrir {port}: {exc}")

    def open(self, port: str, baud: int) -> None:
        self._check(port, baud)
        self.close()
        with self._lock:
            self._port = self._open_port(port, baud)
            self._port_name = port
            self._baud = baud
            self._error = None
            self._stop.clear()
            self._thread = threading.Thread(target=self._read_loop, name="serial-reader", daemon=True)
            self._thread.start()
        self.bus.publish({"type": "serial", "data": f"\n--- abierto {port} @ {baud} ---\n", "meta": True})
        self._publish_status()

    def close(self) -> None:
        with self._lock:
            port = self._port
            if port is None:
                return
            self._stop.set()
            self._port = None
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        try:
            port.close()
        except Exception:
            pass
        self.bus.publish({"type": "serial", "data": f"\n--- cerrado {self._port_name} ---\n", "meta": True})
        self._port_name = None
        self._baud = None
        self._publish_status()

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        port = self._port
        while not self._stop.is_set() and port is not None:
            try:
                data = port.read(4096)
            except Exception as exc:  # p. ej. se desenchufó el USB
                self._error = f"Error de lectura: {exc}"
                self.bus.publish({"type": "serial", "data": f"\n--- {self._error} ---\n", "meta": True})
                threading.Thread(target=self.close, daemon=True).start()
                return
            if data:
                text = decoder.decode(data)
                if text:
                    self.bus.publish({"type": "serial", "data": text})

    # --- escritura -------------------------------------------------------------
    def write(self, data: bytes) -> None:
        with self._lock:
            if self._port is None:
                raise SerialError("El puerto serie no está abierto")
            try:
                self._port.write(data)
            except Exception as exc:
                raise SerialError(f"No se pudo escribir: {exc}")

    def touch_1200(self, port: str) -> None:
        """Abre y cierra el puerto a 1200 baud: los Arduino con USB nativo (Leonardo,
        Micro) lo interpretan como pedido de entrar al bootloader."""
        if self.mock:
            return
        import serial
        try:
            handle = serial.Serial(port, baudrate=1200)
            handle.dtr = False
            handle.close()
        except serial.SerialException as exc:
            raise SerialError(f"No se pudo hacer el reset a 1200 baud en {port}: {exc}")

    def pulse_reset(self, port: str) -> None:
        """Pulso en DTR: en los Arduino con USB-serie reinicia el micro (capacitor al RESET)."""
        if self.mock:
            return
        import serial
        try:
            handle = serial.Serial(port, baudrate=115200)
            handle.dtr = False
            time.sleep(0.1)
            handle.dtr = True
            time.sleep(0.1)
            handle.close()
        except serial.SerialException as exc:
            raise SerialError(f"No se pudo reiniciar por DTR en {port}: {exc}")

    def send_to_port(self, port: str, baud: int, data: bytes) -> None:
        """Envía datos a un puerto concreto (lo usa BootControl serial_command).

        Si el monitor tiene ese puerto abierto, se reutiliza; si no, se abre,
        escribe y cierra.
        """
        if self._port is not None and self._port_name == port:
            self.write(data)
            return
        handle = self._open_port(port, baud)
        try:
            handle.write(data)
            if hasattr(handle, "flush"):
                handle.flush()
        finally:
            handle.close()
