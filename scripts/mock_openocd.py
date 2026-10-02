#!/usr/bin/env python3
"""OpenOCD simulado para el modo mock (BENCH_MOCK=1).

Interpreta los mismos argumentos que arma la app (-f, -c, -s) y produce una
salida parecida a la real, con demoras, para probar la interfaz sin hardware.

Fallas simuladas:
  * archivo cuyo nombre contiene "falla"  -> error de verificación
  * BENCH_MOCK_FAIL=connect               -> no se puede conectar al target
"""
import os
import socket
import threading
import re
import sys
import time

KNOWN_SERIALS = {"066DFF485550755187121832", "002F00283232511639353236"}
# ID (DBGMCU_IDCODE & 0xFFF) del chip simulado según el .cfg de target.
IDS = {"stm32h5x": 0x474, "stm32h7x": 0x483, "stm32f1x": 0x410, "stm32f4x": 0x431,
       "stm32l4x": 0x415, "stm32g0x": 0x460, "stm32g4x": 0x469}
CORES = {
    "stm32h7x": "Cortex-M7 r1p2", "stm32h5x": "Cortex-M33 r0p4", "stm32f1x": "Cortex-M3 r1p1",
    "stm32f4x": "Cortex-M4 r0p1", "stm32l4x": "Cortex-M4 r0p1", "stm32g0x": "Cortex-M0+ r0p1",
    "stm32g4x": "Cortex-M4 r0p1",
}


def out(line, delay=0.05):
    print(line, flush=True)
    time.sleep(delay)


def gdb_server(host, port):
    """Puerto GDB simulado: acepta una conexión (como OpenOCD) y avisa al conectar y desconectar.
    No habla el protocolo de GDB: alcanza para que la app vea "cliente conectado"."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((host, port))
    except OSError:
        return                               # puerto ocupado (otro simulador): sin clientes simulados
    srv.listen(1)
    while True:
        conn, _ = srv.accept()
        out(f"Info : accepting 'gdb' connection on tcp/{port}", 0)
        try:
            while conn.recv(1024):
                pass
        except OSError:
            pass
        conn.close()
        out("Info : dropped 'gdb' connection", 0)


def rtt_server(host, port):
    """Canal RTT simulado: el programa 05_contador_rtt (cuenta cada 250 ms y acepta comandos)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=rtt_cliente, args=(conn,), daemon=True).start()


def rtt_cliente(conn):
    estado = {"contador": 0, "periodo": 0.25, "pausado": False}
    try:
        conn.sendall(b"\r\n=== 05_contador_rtt - STM32H503 (RTT) ===\r\n"
                     b"Comandos: p = pausa/sigue, r = volver a 0, + = mas rapido, - = mas lento, "
                     b"s = estado, ? = ayuda\r\n")
        conn.settimeout(0.05)
        proximo = time.monotonic()
        while True:
            try:
                datos = conn.recv(64)
                if not datos:
                    return
                for c in datos.decode(errors="ignore"):
                    if c in "pP":
                        estado["pausado"] = not estado["pausado"]
                        conn.sendall(b"> Pausado\r\n" if estado["pausado"] else b"> Sigue\r\n")
                    elif c in "rR":
                        estado["contador"] = 0
                        conn.sendall(b"> Vuelve a 0\r\n")
                    elif c == "+":
                        estado["periodo"] = max(0.05, estado["periodo"] / 2)
                    elif c == "-":
                        estado["periodo"] = min(4.0, estado["periodo"] * 2)
            except socket.timeout:
                pass
            if not estado["pausado"] and time.monotonic() >= proximo:
                proximo = time.monotonic() + estado["periodo"]
                estado["contador"] += 1
                leds = estado["contador"] & 0xFF
                conn.sendall(f"contador = {estado['contador']}   LEDs = {leds:08b}   (0x{leds:02X})\r\n".encode())
    except OSError:
        return
    finally:
        conn.close()


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
        bind = next((c.split()[1] for c in cmds if c.startswith("bindto ")), "127.0.0.1")
        threading.Thread(target=gdb_server, args=(bind, int(port)), daemon=True).start()
        out(f"Info : Listening on port {port} for gdb connections")
        setup = next((c for c in cmds if c.startswith("rtt setup ")), None)
        server = next((c for c in cmds if c.startswith("rtt server start ")), None)
        if setup and "rtt start" in cmds:
            out("Info : rtt: Searching for control block 'SEGGER RTT'")
            out(f"Info : rtt: Control block found at {setup.split()[2].lower()}")
        if server:
            rtt_port = int(server.split()[3])
            threading.Thread(target=rtt_server, args=(bind, rtt_port), daemon=True).start()
            out(f"Info : Listening on port {rtt_port} for rtt connections")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    for cmd in cmds:
        if "read_memory" in cmd:
            # Verificación del ID del chip (DBGMCU_IDCODE).
            connected = int(os.environ.get("BENCH_MOCK_STM32_ID", "0") or "0", 0) or IDS.get(family, 0x000)
            expected = re.search(r"!= (0x[0-9A-Fa-f]+)", cmd) or re.search(r"\$_id (0x[0-9A-Fa-f]+)\]", cmd)
            expected_id = int(expected.group(1), 16) if expected else connected
            if "shutdown error" in cmd:
                if connected != expected_id:
                    out(f"CHIP EQUIVOCADO: se esperaba el ID 0x{expected_id:03X} y el chip conectado tiene "
                        f"0x{connected:03X}. No se toca la flash.")
                    return 1
                out(f"Chip verificado: ID 0x{connected:03X}")
            else:
                out(f"ID del chip conectado: 0x{connected:03X} (esperado para esta placa: 0x{expected_id:03X})")
            continue
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
