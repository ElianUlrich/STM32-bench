#!/usr/bin/env python3
"""esptool simulado para el modo mock (BENCH_MOCK=1).

Chip "conectado" según el puerto:
  MOCK-USB0 -> ESP32 (se puede cambiar con BENCH_MOCK_ESP_CHIP=esp32s3, etc.)
  otros     -> no responde (como un puerto sin ESP).
Si --chip no coincide con el chip conectado, falla igual que esptool real.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import mock_state  # noqa: E402

STATE = Path(os.environ.get(mock_state.ENV_VAR, "."))

PRETTY = {"esp8266": "ESP8266", "esp32": "ESP32", "esp32s2": "ESP32-S2", "esp32s3": "ESP32-S3",
          "esp32c2": "ESP32-C2", "esp32c3": "ESP32-C3", "esp32c5": "ESP32-C5", "esp32c6": "ESP32-C6",
          "esp32c61": "ESP32-C61", "esp32h2": "ESP32-H2", "esp32p4": "ESP32-P4"}


def out(line, delay=0.04):
    print(line, flush=True)
    time.sleep(delay)


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def file_pairs(argv, cmd):
    """Pares (dirección, archivo) después de write_flash/verify_flash, salteando opciones."""
    rest = argv[argv.index(cmd) + 1:]
    args = []
    i = 0
    while i < len(rest):
        if rest[i].startswith("--"):
            i += 2 if rest[i] in ("--flash_mode", "--flash_freq", "--flash_size") else 1
            continue
        args.append(rest[i])
        i += 1
    return [(args[k], args[k + 1]) for k in range(0, len(args) - 1, 2)]


def main(argv):
    if argv[:1] == ["version"] or argv[-1:] == ["version"]:
        out("esptool.py v4.12.0 (simulado)", 0)
        return 0
    chip = opt(argv, "--chip", "auto")
    port = opt(argv, "--port", "?")
    baud = opt(argv, "--baud", "115200")
    commands = [a for a in argv if a in ("write_flash", "verify_flash", "erase_flash", "read_mac", "flash_id",
                                         "read_flash")]
    cmd = commands[0] if commands else None
    out(f"esptool.py v4.12.0 (simulado)")
    out(f"Serial port {port}")
    out("Connecting....", 0.4)
    if port != "MOCK-USB0":
        out("A fatal error occurred: Failed to connect to Espressif device: No serial data received.")
        return 2
    connected = os.environ.get("BENCH_MOCK_ESP_CHIP", "esp32")
    if chip == "auto":
        out(f"Detecting chip type... {PRETTY[connected]}")
    elif chip != connected:
        out(f"A fatal error occurred: This chip is {PRETTY[connected]}, not {PRETTY.get(chip, chip)}. "
            "Wrong --chip argument?")
        return 2
    out(f"Chip is {PRETTY[connected]}-D0WD-V3 (revision v3.1)")
    out("Features: WiFi, BT, Dual Core, 240MHz, VRef calibration in efuse")
    out("Crystal is 40MHz")
    out("MAC: 24:6f:28:aa:bb:cc")
    out("Uploading stub...")
    out("Running stub...")
    out("Stub running...")
    if baud != "115200":
        out(f"Changing baud rate to {baud}")
        out("Changed.")
    if cmd == "write_flash":
        out("Configuring flash size...")
        for addr, path in file_pairs(argv, cmd):
            size = os.path.getsize(path)
            out(f"Flash will be erased from {int(addr, 0):#010x} to {int(addr, 0) + size - 1:#010x}...")
            out(f"Compressed {size} bytes to {max(size // 2, 1)}...")
            for pct in (25, 50, 75, 100):
                out(f"Writing at {int(addr, 0) + size * pct // 100:#010x}... ({pct} %)", 0.15)
            mock_state.flash_write(STATE, "esp", int(addr, 0), Path(path).read_bytes())
            out(f"Wrote {size} bytes ({max(size // 2, 1)} compressed) at {int(addr, 0):#010x} in 1.2 seconds")
            if "falla" in os.path.basename(path):
                out("A fatal error occurred: MD5 of file does not match data in flash!")
                return 2
            out("Hash of data verified.")
    elif cmd == "verify_flash":
        for addr, path in file_pairs(argv, cmd):
            size = os.path.getsize(path)
            out(f"Verifying {size:#x} ({size}) bytes @ {int(addr, 0):#010x} in flash against {path}...", 0.3)
            if "falla" in os.path.basename(path):
                out("-- verify FAILED (digest mismatch)")
                return 2
            out("-- verify OK (digest matched)")
    elif cmd == "read_flash":
        i = argv.index("read_flash")
        addr, size, path = int(argv[i + 1], 0), int(argv[i + 2], 0), argv[i + 3]
        Path(path).write_bytes(mock_state.flash_read(STATE, "esp", addr, size))
        for pct in (25, 50, 75, 100):
            out(f"{size * pct // 100} ({pct} %)", 0.05)
        out(f"Read {size} bytes at {addr:#010x} in 0.5 seconds (500.0 kbit/s)...")
    elif cmd == "erase_flash":
        mock_state.flash_erase(STATE, "esp")
        out("Erasing flash (this may take a while)...", 1.5)
        out("Chip erase completed successfully in 3.1s")
    elif cmd == "read_mac":
        out("MAC: 24:6f:28:aa:bb:cc")
    elif cmd == "flash_id":
        out("Manufacturer: 20")
        out("Device: 4016")
        out("Detected flash size: 4MB")
    out("Hard resetting via RTS pin...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
