"""Ícono del Banco remoto (.ico) dibujado por código: un chip blanco sobre un cuadrado
redondeado verde azulado, el mismo motivo que el logo de la web. Sin dependencias.

    python -m installer.icono salida.ico
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

TEAL = (15, 118, 110)
WHITE = (255, 255, 255)


def _coverage(size: int, inside) -> list:
    """Imagen RGBA (filas de bytes) con antialias por supermuestreo 4x4."""
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(4):
                for sx in range(4):
                    u = (x + (sx + 0.5) / 4) / size
                    v = (y + (sy + 0.5) / 4) / size
                    color = inside(u, v)
                    if color:
                        acc[0] += color[0]
                        acc[1] += color[1]
                        acc[2] += color[2]
                        acc[3] += 255
            n = acc[3] / 255 or 1
            row += bytes((int(acc[0] / n), int(acc[1] / n), int(acc[2] / n), int(acc[3] / 16)))
        rows.append(bytes(row))
    return rows


def _rounded(u: float, v: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if not (x0 <= u <= x1 and y0 <= v <= y1):
        return False
    cx = min(max(u, x0 + r), x1 - r)
    cy = min(max(v, y0 + r), y1 - r)
    return (u - cx) ** 2 + (v - cy) ** 2 <= r * r


def _pixel(u: float, v: float):
    if not _rounded(u, v, 0.02, 0.02, 0.98, 0.98, 0.22):
        return None
    # Cuerpo del chip (contorno) y su "núcleo".
    body = _rounded(u, v, 0.30, 0.30, 0.70, 0.70, 0.06)
    hole = _rounded(u, v, 0.37, 0.37, 0.63, 0.63, 0.03)
    core = _rounded(u, v, 0.44, 0.44, 0.56, 0.56, 0.02)
    if (body and not hole) or core:
        return WHITE
    # Patas: tres por lado.
    for c in (0.40, 0.50, 0.60):
        if abs(u - c) <= 0.025 and (0.18 <= v <= 0.30 or 0.70 <= v <= 0.82):
            return WHITE
        if abs(v - c) <= 0.025 and (0.18 <= u <= 0.30 or 0.70 <= u <= 0.82):
            return WHITE
    return TEAL


def png(size: int) -> bytes:
    rows = _coverage(size, _pixel)
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    images = [png(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "banco-remoto.ico")
    out.write_bytes(ico())
    print(out)
