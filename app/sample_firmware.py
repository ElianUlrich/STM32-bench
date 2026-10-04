"""Generación de firmware sintético (ELF/HEX/BIN) para tests y modo mock.

No es un programa real: solo tiene la estructura correcta (tabla de vectores,
segmento .text en flash y segmento .data con VMA en RAM y LMA en flash).
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

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


def esp_image(chip_id: int, size: int = 4096, entry: int = 0x40080000) -> bytes:
    """Imagen de ESP mínima: cabecera (0xE9) + cabecera extendida con el ID de chip."""
    header = bytes([0xE9, 1, 2, 0x20]) + struct.pack("<I", entry)
    ext = bytes([0xEE, 0, 0, 0]) + struct.pack("<H", chip_id) + bytes(10)   # 16 bytes
    body = bytes((i * 13 + 7) & 0xFF for i in range(max(size - len(header) - len(ext), 16)))
    return header + ext + body


def esp_merged(chip_id: int, bootloader_offset: int, app_size: int = 4096) -> bytes:
    """Imagen combinada: bootloader en su offset, tabla de particiones en 0x8000, app en 0x10000."""
    image = bytearray([0xFF]) * 0x10000
    boot = esp_image(chip_id, 0x2000, entry=0x40078000)
    image[bootloader_offset:bootloader_offset + len(boot)] = boot
    image[0x8000:0x8002] = bytes([0xAA, 0x50])   # firma de la tabla de particiones
    return bytes(image) + esp_image(chip_id, app_size)


# Tabla de particiones de TONE MASTER (pedal con ESP32-P4): NVS grande antes del programa.
IDF_PARTITIONS = (("nvs", 1, 2, 0xB000, 0x10000), ("phy_init", 1, 1, 0x1B000, 0x1000),
                  ("factory", 0, 0, 0x20000, 0x200000))


def esp_partition_table(parts: Sequence[Tuple[str, int, int, int, int]] = IDF_PARTITIONS) -> bytes:
    """partition-table.bin como lo genera ESP-IDF: entradas de 32 bytes + MD5, relleno a 0xC00."""
    out = bytearray()
    for name, ptype, subtype, offset, size in parts:
        out += (b"\xaa\x50" + bytes([ptype, subtype]) + struct.pack("<II", offset, size)
                + name.encode().ljust(16, b"\0") + struct.pack("<I", 0))
    out += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(bytes(out)).digest()
    return bytes(out) + b"\xff" * (0xC00 - len(out))


def esp_idf_build(name: str = "demo-idf", chip: str = "esp32p4", chip_id: int = 18,
                  bootloader: int = 0x2000, table: int = 0xA000, app: int = 0x20000,
                  app_size: int = 8192, parts=IDF_PARTITIONS,
                  extra: Sequence[Tuple[int, str, bytes]] = ()) -> Dict[str, bytes]:
    """Carpeta build/ de ESP-IDF: flasher_args.json + los .bin que lista (ruta -> contenido)."""
    files = {
        "bootloader/bootloader.bin": esp_image(chip_id, 0x5000, entry=0x4FF00000),
        "partition_table/partition-table.bin": esp_partition_table(parts),
        f"{name}.bin": esp_image(chip_id, app_size),
    }
    flash_files = {hex(bootloader): "bootloader/bootloader.bin", hex(app): f"{name}.bin",
                   hex(table): "partition_table/partition-table.bin"}
    for offset, path, data in extra:
        files[path] = data
        flash_files[hex(offset)] = path
    manifest = {
        "write_flash_args": ["--flash_mode", "dio", "--flash_size", "32MB", "--flash_freq", "40m"],
        "flash_settings": {"flash_mode": "dio", "flash_size": "32MB", "flash_freq": "40m"},
        "flash_files": flash_files,
        "bootloader": {"offset": hex(bootloader), "file": "bootloader/bootloader.bin", "encrypted": "false"},
        "app": {"offset": hex(app), "file": f"{name}.bin", "encrypted": "false"},
        "partition-table": {"offset": hex(table), "file": "partition_table/partition-table.bin",
                            "encrypted": "false"},
        "extra_esptool_args": {"after": "hard_reset", "before": "default_reset", "stub": True, "chip": chip},
    }
    files["flasher_args.json"] = json.dumps(manifest, indent=4).encode()
    return files


def avr_hex(size: int = 1024) -> bytes:
    return to_ihex([(0, bytes((i * 5 + 1) & 0xFF for i in range(size)))]).encode()


# Archivos de ejemplo del modo mock y la placa a la que corresponden.
DEMO_TARGETS = {
    "demo-blink/Debug/demo-blink.elf": "stm32h503cb",
    "demo-blink/Debug/demo-blink.hex": "stm32h503cb",
    "demo-blink/Debug/demo-blink.bin": "stm32h503cb",
    "casos-dfu/con-hueco.hex": "stm32h723vg",
    "casos-dfu/demo-falla.elf": "stm32h503cb",
    f"{UPLOADS_DIRNAME}/general/subido-ejemplo.bin": "stm32h503cb",
    "esp32-blink/build/esp32-blink.ino.bin": "esp32",
    "esp32-blink/build/esp32-blink.ino.merged.bin": "esp32",
    "esp32s3-demo/s3-demo.bin": "esp32-s3",
    "pedal-p4/build/pedal-p4.bin": "esp32-p4",
    "arduino-blink/Blink.ino.hex": "arduino-uno",
    "arduino-leonardo/Teclado.ino.hex": "arduino-leonardo",
    "h503-contador/build/03_contador_binario.elf": "stm32h503cb",
    "h503-contador/build/04_contador_serie.elf": "stm32h503cb",
    "h503-contador/build/05_contador_rtt.elf": "stm32h503cb",
}


def seed_debug_project(projects_dir: Path) -> None:
    """Copia el contador binario real (.elf + fuentes) para probar la pestaña Depurar."""
    from .settings import REPO_ROOT
    src = REPO_ROOT / "firmware" / "h503_leds"
    dest = projects_dir / "h503-contador"
    elf = src / "build" / "03_contador_binario.elf"
    if dest.exists() or not elf.is_file():
        return
    (dest / "build").mkdir(parents=True)
    (dest / "src").mkdir()
    for programa in ("03_contador_binario", "04_contador_serie", "05_contador_rtt"):
        f = src / "build" / f"{programa}.elf"
        if f.is_file():
            (dest / "build" / f.name).write_bytes(f.read_bytes())
    for f in (src / "src").iterdir():
        if f.suffix in (".c", ".h"):
            (dest / "src" / f.name).write_bytes(f.read_bytes())


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
    (casos / ".syncthing.demo.hex.tmp").write_bytes(b":00000001FF" + bytes([10]))
    uploads = projects_dir / UPLOADS_DIRNAME / "general"
    uploads.mkdir(parents=True)
    (uploads / "subido-ejemplo.bin").write_bytes(images["bin"])
    # ESP32 (aplicación suelta e imagen combinada), ESP32-S3 y Arduino.
    esp = projects_dir / "esp32-blink" / "build"
    esp.mkdir(parents=True)
    (esp / "esp32-blink.ino.bin").write_bytes(esp_image(0, 8192))
    (esp / "esp32-blink.ino.merged.bin").write_bytes(esp_merged(0, 0x1000))
    s3 = projects_dir / "esp32s3-demo"
    s3.mkdir()
    (s3 / "s3-demo.bin").write_bytes(esp_image(9, 8192))
    # Proyecto ESP-IDF (ESP32-P4, con NVS antes del programa) como llega por Syncthing.
    for rel, data in esp_idf_build(name="pedal-p4").items():
        dest = projects_dir / "pedal-p4" / "build" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    ard = projects_dir / "arduino-blink"
    ard.mkdir()
    (ard / "Blink.ino.hex").write_bytes(avr_hex(924))
    leo = projects_dir / "arduino-leonardo"
    leo.mkdir()
    (leo / "Teclado.ino.hex").write_bytes(avr_hex(4800))
