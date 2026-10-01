"""RTT: el "printf" del programa llega por el ST-Link (SWD), sin puerto serie.

OpenOCD lee el buffer RTT del micro mientras corre y publica el canal 0 en un
puerto TCP (rtt server start). Esta clase es un cliente de ese puerto con la
misma interfaz que un puerto serie (read/write/close), así el monitor serie de
la web lo muestra como un puerto más.
"""
from __future__ import annotations

import socket
import time

RTT_PORT_NAME = "RTT"
RTT_SYMBOL = "_SEGGER_RTT"


class RttPort:
    def __init__(self, host: str, port: int, wait_s: float = 6.0) -> None:
        # OpenOCD publica el puerto un momento después de "Listening ... gdb": se reintenta.
        deadline = time.monotonic() + wait_s
        while True:
            try:
                self._sock = socket.create_connection((host, int(port)), timeout=2)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        self._sock.settimeout(0.1)
        self.is_open = True

    def read(self, size: int = 1) -> bytes:
        try:
            data = self._sock.recv(size)
        except socket.timeout:
            return b""
        if not data:
            raise ConnectionError("OpenOCD cerró el canal RTT (¿se detuvo el servidor GDB?)")
        return data

    def write(self, data: bytes) -> int:
        self._sock.sendall(data)
        return len(data)

    def close(self) -> None:
        self.is_open = False
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
