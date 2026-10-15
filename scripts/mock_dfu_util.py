#!/usr/bin/env python3
"""dfu-util simulado para el modo mock (BENCH_MOCK=1).

El "dispositivo DFU" solo existe mientras el BootControl simulado lo hizo
aparecer (estado compartido en BENCH_MOCK_STATE_DIR/mock_dfu.json). La flash
simulada se guarda en mock_flash.bin para que "verificar" compare de verdad.

  BENCH_MOCK_DFU_LEAVE_ERROR=1  -> simula el error típico de dfu-util al salir
                                   con :leave (después de descargar bien).
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import mock_state  # noqa: E402

STATE = Path(os.environ.get(mock_state.ENV_VAR, "."))


def out(line, delay=0.03):
    print(line, flush=True)
    time.sleep(delay)


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def header():
    out("dfu-util 0.11 (simulado)", 0)
    out("", 0)


def main(argv):
    if "-V" in argv:
        out("dfu-util 0.11 (simulado)", 0)
        return 0
    header()
    serial = mock_state.dfu_present(STATE)
    if "-l" in argv:
        if serial:
            for alt, name in ((0, "@Internal Flash   /0x08000000/8*128Kg"), (1, "@Option Bytes   /0x5200201C/01*128 e")):
                out(f'Found DFU: [0483:df11] ver=0200, devnum=9, cfg=1, intf=0, path="1-1.3", alt={alt}, '
                    f'name="{name}", serial="{serial}"', 0)
        return 0

    if not serial:
        out("dfu-util: No DFU capable USB device available")
        return 74
    wanted = opt(argv, "-S")
    if wanted and wanted != serial:
        out(f"dfu-util: No DFU capable USB device available (serial {wanted})")
        return 74

    spec = opt(argv, "-s") or ""
    parts = spec.split(":")
    address = int(parts[0], 0) if parts and parts[0] else 0x08000000
    modifiers = parts[1:]
    out("Opening DFU capable USB device...")
    out("Device ID 0483:df11")
    out("Device DFU version 011a")
    out("Claiming USB DFU Interface...")
    out("Setting Alternate Interface #0 ...")
    out("Determining device status: state = dfuIDLE, status = 0")

    if "mass-erase" in modifiers:
        out("Performing mass erase, this can take a moment", 1.5)
        mock_state.flash_erase(STATE, "stm32")
        out("Done!")
        return 0

    download = opt(argv, "-D")
    upload = opt(argv, "-U")
    if download:
        data = Path(download).read_bytes()
        out(f"Downloading element to address = 0x{address:08x}, size = {len(data)}")
        for pct in (0, 20, 40, 60, 80, 100):
            bar = "=" * (pct // 4)
            out(f"Download\t[{bar:<25}] {pct:3d}%", 0.25)
        mock_state.flash_write(STATE, "stm32", address, data)
        out("Download done.")
        out("File downloaded successfully")
        if "leave" in modifiers:
            out("Submitting leave request...")
            out("Transitioning to dfuMANIFEST state")
            mock_state.clear_dfu(STATE)
            if os.environ.get("BENCH_MOCK_DFU_LEAVE_ERROR") == "1":
                out("dfu-util: Error during download get_status")
                return 74
        return 0

    if upload:
        length = int(modifiers[0]) if modifiers and modifiers[0].isdigit() else 0
        chunk = mock_state.flash_read(STATE, "stm32", address, length)
        out(f"Uploading from address = 0x{address:08x}, size = {length}", 0.5)
        Path(upload).write_bytes(chunk)
        out("Upload\t[============             ]  50%", 0.2)
        out("Upload\t[=========================] 100%")
        out(f"Received a total of {length} bytes")
        out("Upload done.")
        return 0

    if "leave" in modifiers:
        out("Submitting leave request...")
        mock_state.clear_dfu(STATE)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
