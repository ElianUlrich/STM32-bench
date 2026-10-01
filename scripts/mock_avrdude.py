#!/usr/bin/env python3
"""avrdude simulado para el modo mock (BENCH_MOCK=1).

Chip "conectado" según el puerto:
  MOCK-ACM1 -> ATmega328P (Arduino Uno)
  MOCK-ACM2 -> ATmega32U4 (Leonardo)
  usb       -> ATmega328P (programador ISP)
  otros     -> no responde.
Si la firma no coincide con -p, falla igual que avrdude real.
"""
import os
import sys
import time

SIGNATURES = {"atmega328p": "1e 95 0f", "atmega168": "1e 94 06", "atmega2560": "1e 98 01",
              "atmega1280": "1e 97 03", "atmega32u4": "1e 95 87"}
CONNECTED = {"MOCK-ACM1": "atmega328p", "MOCK-ACM2": "atmega32u4", "usb": "atmega328p"}


def out(line, delay=0.04):
    print(line, flush=True)
    time.sleep(delay)


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def main(argv):
    if "-?" in argv:
        out("Usage: avrdude [options]", 0)
        out("avrdude version 6.2 (simulado), URL: <http://savannah.nongnu.org/projects/avrdude/>", 0)
        return 0
    part = opt(argv, "-p")
    port = opt(argv, "-P")
    ops = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-U"]
    connected = os.environ.get("BENCH_MOCK_AVR_PART") or CONNECTED.get(port)
    out("")
    out(f"avrdude: AVR device initialized and ready to accept instructions", 0.3)
    if connected is None:
        out(f"avrdude: stk500_recv(): programmer is not responding")
        out(f"avrdude: stk500_getsync() attempt 10 of 10: not in sync: resp=0x00")
        return 1
    out("Reading | ################################################## | 100% 0.01s")
    out(f"avrdude: Device signature = 0x{SIGNATURES[connected].replace(' ', '')}")
    if connected != part:
        out(f"avrdude: Expected signature for {part.upper()} is {SIGNATURES.get(part, '?? ?? ??').upper()}")
        out("         Double check chip, or use -F to override this check.")
        return 1
    if "-e" in argv:
        out("avrdude: erasing chip", 0.8)
    for op in ops:
        # memoria:modo:archivo:formato (el archivo puede tener ":" en Windows, p. ej. C:\...)
        mem, mode, rest = op.split(":", 2)
        path, fmt = rest.rsplit(":", 1)
        size = os.path.getsize(path) * 10 // 28   # aproximado: bytes reales de un .hex
        if mode == "w":
            if "-D" not in argv:
                out("avrdude: erasing chip")
            out(f"avrdude: reading input file \"{path}\"")
            out(f"avrdude: writing flash ({size} bytes):")
            out("Writing | ################################################## | 100% 1.20s", 0.6)
            out(f"avrdude: {size} bytes of flash written")
        out(f"avrdude: verifying flash memory against {path}:")
        out("Reading | ################################################## | 100% 0.90s", 0.4)
        if "falla" in os.path.basename(path):
            out("avrdude: verification error, first mismatch at byte 0x0040")
            out("         0x0c != 0xff")
            return 1
        out(f"avrdude: {size} bytes of flash verified")
    out("")
    out("avrdude done.  Thank you.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
