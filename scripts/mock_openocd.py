#!/usr/bin/env python3
"""OpenOCD simulado para el modo mock (BENCH_MOCK=1).

Interpreta los mismos argumentos que arma la app (-f, -c, -s) y produce una
salida parecida a la real, con demoras, para probar la interfaz sin hardware.

Fallas simuladas:
  * archivo cuyo nombre contiene "falla"  -> error de verificación
  * BENCH_MOCK_FAIL=connect               -> no se puede conectar al target
"""
import os
import re
import sys
import time

KNOWN_SERIALS = {"066DFF485550755187121832", "002F00283232511639353236"}
CORES = {
    "stm32h7x": "Cortex-M7 r1p2", "stm32h5x": "Cortex-M33 r0p4", "stm32f1x": "Cortex-M3 r1p1",
    "stm32f4x": "Cortex-M4 r0p1", "stm32l4x": "Cortex-M4 r0p1", "stm32g0x": "Cortex-M0+ r0p1",
    "stm32g4x": "Cortex-M4 r0p1",
}


def out(line, delay=0.05):
    print(line, flush=True)
    time.sleep(delay)


def main(argv):
    if "--version" in argv:
        out("Open On-Chip Debugger 0.12.0+dev-mock (stm32-bench simulado)", 0)
        return 0
    cmds = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-c"]
    files = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-f"]
    if any(c.startswith("help adapter serial") for c in cmds):
        out("adapter serial serial_string", 0)
        return 0

    out("Open On-Chip Debugger 0.12.0+dev-mock (stm32-bench simulado)")
    out("Licensed under GNU GPL v2")
    target = files[-1] if files else "target/desconocido.cfg"
    family = os.path.splitext(os.path.basename(target))[0]
    cpu = f"{family}.cpu"
    for cmd in cmds:
        m = re.match(r"adapter serial (\S+)|hla_serial (\S+)", cmd)
        if m:
            serial = m.group(1) or m.group(2)
            if serial not in KNOWN_SERIALS:
                out(f"Error: unable to find a matching ST-Link (serial {serial})")
                return 1
    speed = next((c.split()[-1] for c in cmds if c.startswith("adapter speed")), "4000")
    out(f"Info : clock speed {speed} kHz")
    out("Info : STLINK V3J13M4 (API v3) VID:PID 0483:374E")
    out("Info : Target voltage: 3.287")
    if os.environ.get("BENCH_MOCK_FAIL") == "connect":
        out("Error: init mode failed (unable to connect to the target)", 0.3)
        return 1
    out(f"Info : [{cpu}] {CORES.get(family, 'Cortex-M')} processor detected", 0.2)
    out(f"Info : [{cpu}] target has 8 breakpoints, 4 watchpoints")

    if any(c.startswith("gdb_port") for c in cmds):
        port = next(c.split()[-1] for c in cmds if c.startswith("gdb_port"))
        out(f"Info : starting gdb server for {cpu} on {port}")
        out(f"Info : Listening on port {port} for gdb connections")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    for cmd in cmds:
        if cmd.startswith("program "):
            path = re.search(r"\{(.*?)\}", cmd).group(1)
            if not os.path.isfile(path):
                out(f"Error: couldn't open {path}")
                return 1
            size = os.path.getsize(path)
            out(f"[{cpu}] halted due to debug-request, current mode: Thread", 0.2)
            out("xPSR: 0x01000000 pc: 0x08000100 msp: 0x20020000")
            out("** Programming Started **", 0.3)
            out(f"Info : Device: STM32 (mock), flash size probed value 1024k")
            for pct in (25, 50, 75, 100):
                out(f"Info : writing... {pct}%", 0.3)
            out("** Programming Finished **")
            out("** Verify Started **", 0.4)
            if "falla" in os.path.basename(path):
                out("Error: checksum mismatch - attempting binary compare")
                out("diff 0 address 0x08000400. Was 0xff instead of 0x12")
                out("** Verify Failed **")
                return 1
            out(f"** Verified OK ** ({size} bytes)")
            out("** Resetting Target **")
            out("shutdown command invoked")
            return 0
        if cmd.startswith("verify_image "):
            path = re.search(r"\{(.*?)\}", cmd).group(1)
            if "falla" in os.path.basename(path):
                out("Error: checksum mismatch - attempting binary compare", 0.5)
                return 1
            out(f"verified {os.path.getsize(path)} bytes in 0.412s (mock)", 0.5)
        elif cmd.startswith("flash erase_sector"):
            bank = cmd.split()[2]
            out(f"erased sectors 0 through 7 on flash bank {bank} in 3.1s", 1.5)
        elif cmd in ("halt", "reset halt"):
            out(f"[{cpu}] halted due to debug-request, current mode: Thread")
            out("xPSR: 0x01000000 pc: 0x08000abc msp: 0x2001ffd8")
        elif cmd == "resume":
            out(f"[{cpu}] resumed")
        elif cmd == "shutdown":
            out("shutdown command invoked")
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
