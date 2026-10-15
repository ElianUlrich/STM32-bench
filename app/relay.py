"""Reenvío TCP: publica en la red un puerto que OpenOCD abre sólo en 127.0.0.1.

OpenOCD tiene un único "bindto" para todos sus puertos. Para muestrear variables
hace falta su puerto TCL (acepta cualquier comando de OpenOCD) y ése no tiene que
quedar en la red. Entonces OpenOCD escucha sólo en 127.0.0.1 y la app reenvía a
Tailscale (o a donde diga gdb.bind) únicamente el puerto GDB y el del canal RTT.
"""
from __future__ import annotations

import asyncio
import socket
from typing import Optional, Set


def free_local_port() -> int:
    """Un puerto TCP libre en 127.0.0.1 (para los puertos internos de OpenOCD)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TcpRelay:
    """Escucha en (host, port) y conecta cada cliente con 127.0.0.1:target_port."""

    def __init__(self, host: str, port: int, target_port: int) -> None:
        self.host, self.port, self.target_port = host, int(port), int(target_port)
        self._server: Optional[asyncio.base_events.Server] = None
        self._writers: Set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        """Lanza OSError si el puerto está ocupado."""
        self._server = await asyncio.start_server(self._client, self.host, self.port, reuse_address=True)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.target_port)
        except OSError:
            writer.close()                  # OpenOCD todavía no escucha o ya terminó
            return
        self._writers.update((writer, up_writer))
        try:
            await asyncio.gather(self._pipe(reader, up_writer), self._pipe(up_reader, writer))
        finally:
            self._writers.difference_update((writer, up_writer))

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            writer.close()                  # cerrar un lado corta también el otro

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for w in list(self._writers):
                w.close()
            await self._server.wait_closed()
            self._server = None
