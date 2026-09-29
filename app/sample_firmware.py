"""Generación de firmware sintético (ELF/HEX/BIN) para tests y modo mock.

No es un programa real: solo tiene la estructura correcta (tabla de vectores,
segmento .text en flash y segmento .data con VMA en RAM y LMA en flash).
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable, List, Tuple

from .settings import UPLOADS_DIRNAME

FLASH_BASE = 0x08000000
RAM_BASE = 0x20000000


def build_elf(segments: Iterable[Tuple[int, int, bytes]], entry: int = FLASH_BASE + 0x101,
              machine: int = 40) -> bytes:
    """ELF32 little-endian mínimo. segments = [(paddr, vaddr, datos), ...]."""
    segs: List[Tuple[int, int, bytes]] = list(segments)
    phoff = 52
    data_off = phoff + 32 * len(segs)
    header = struct.pack(
        "<16sHHIIIIIHHHHHH",
        b"\x7fELF\x01\x01\x01" + b"\x00" * 9,
        2, machine, 1, entry, phoff, 0, 0x05000200, 52, 32, len(segs), 40, 0, 0)
    phdrs = b""
    payload = b""
    offset = data_off
    for paddr, vaddr, data in segs:
        phdrs += struct.pack("<8I", 1, offset, vaddr, paddr, len(data), len(data), 5, 4)
        payload += data
        offset += len(data)
    return header + phdrs + payload


def vector_table(size: int = 1024) -> bytes:
    """Tabla de vectores + relleno: SP inicial y Reset_Handler plausibles."""
    head = struct.pack("<II", RAM_BASE + 0x20000, FLASH_BASE + 0x101)
    body = bytes((i * 7 + 3) & 0xFF for i in range(size - len(head)))
    return head + body


def to_ihex(blocks: Iterable[Tuple[int, bytes]]) -> str:
    """Intel HEX con registros de dirección extendida (tipo 04)."""
    lines: List[str] = []

    def record(rtype: int, addr: int, data: bytes) -> str:
        raw = bytes([len(data), (addr >> 8) & 0xFF, addr & 0xFF, rtype]) + data
        checksum = (-sum(raw)) & 0xFF
        return ":" + (raw + bytes([checksum])).hex().upper()

    upper = None
    for base, data in blocks:
        for i in range(0, len(data), 16):
            addr = base + i
            if (addr >> 16) != upper:
                upper = addr >> 16
                lines.append(record(0x04, 0, struct.pack(">H", upper)))
            lines.append(record(0x00, addr & 0xFFFF, data[i:i + 16]))
    lines.append(record(0x01, 0, b""))
    return "\n".join(lines) + "\n"


def demo_images() -> dict:
    text = vector_table(2048)
    data = bytes(range(64))
    elf = build_elf([(FLASH_BASE, FLASH_BASE, text),
                     (FLASH_BASE + len(text), RAM_BASE, data)])     # .data: LMA en flash
    flat = text + data
    return {
        "elf": elf,
        "hex": to_ihex([(FLASH_BASE, flat)]).encode(),
        "bin": flat,
        # Dos bloques separados por 64 KB: la conversión DFU avisa del hueco.
        "hex_gap": to_ihex([(FLASH_BASE, text), (FLASH_BASE + 0x10000, data)]).encode(),
        # Datos en flash externa (0x90000000): la conversión DFU debe fallar.
        "hex_ext": to_ihex([(FLASH_BASE, text), (0x90000000, data)]).encode(),
    }


def seed_projects(projects_dir: Path) -> None:
    """En modo mock, crea proyectos de ejemplo si el directorio está vacío."""
    projects_dir.mkdir(parents=True, exist_ok=True)
    if any(projects_dir.iterdir()):
        return
    images = demo_images()
    debug = projects_dir / "demo-blink" / "Debug"
    debug.mkdir(parents=True)
    (debug / "demo-blink.elf").write_bytes(images["elf"])
    (debug / "demo-blink.hex").write_bytes(images["hex"])
    (debug / "demo-blink.bin").write_bytes(images["bin"])
    casos = projects_dir / "casos-dfu"
    casos.mkdir()
    (casos / "con-hueco.hex").write_bytes(images["hex_gap"])
    (casos / "flash-externa.hex").write_bytes(images["hex_ext"])
    # El simulador de OpenOCD falla con archivos cuyo nombre contiene "falla".
    (casos / "demo-falla.elf").write_bytes(images["elf"])
    # Simula restos de Syncthing que el escaneo debe ignorar.
    (projects_dir / ".stfolder").mkdir()
    (casos / ".syncthing.demo.hex.tmp").write_bytes(b":00000001FF\n")
    uploads = projects_dir / UPLOADS_DIRNAME / "general"
    uploads.mkdir(parents=True)
    (uploads / "subido-ejemplo.bin").write_bytes(images["bin"])
