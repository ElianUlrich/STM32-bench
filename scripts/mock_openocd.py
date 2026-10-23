#!/usr/bin/env python3
"""OpenOCD simulado para el modo mock (BENCH_MOCK=1).

Interpreta los mismos argumentos que arma la app (-f, -c, -s) y produce una
salida parecida a la real, con demoras, para probar la interfaz sin hardware.

Fallas simuladas:
  * archivo cuyo nombre contiene "falla"  -> error de verificación
  * BENCH_MOCK_FAIL=connect               -> no se puede conectar al target
  * BENCH_MOCK_RDP=1                      -> la flash no se puede leer (protección de lectura)

Lo que se graba queda en la flash simulada compartida (app/mock_state.py).
"""
import os
import socket
import threading
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import mock_state  # noqa: E402
from app.convert import convert_to_bin  # noqa: E402

STATE = Path(os.environ.get(mock_state.ENV_VAR, "."))
FLASH_BASE = 0x08000000

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


# ------------------------------------------------- puerto TCL: muestrear variables

T0 = time.monotonic()


def valor_simulado(var, t):
    """Lo que valdría la variable del programa grabado a los t segundos (06_ondas y genéricos)."""
    import math
    ms = int(t * 1000)
    tri = lambda periodo: 1 - abs((t / periodo) % 1 * 2 - 1)            # 0..1..0
    nombre = var.name.split(" (")[0]
    fijos = {"frecuencia_hz": 1.0, "amplitud": 1.0, "periodo_ms": 250, "paso_ms": 80, "pausado": 0}
    if nombre in fijos:
        return fijos[nombre]
    if nombre == "seno":
        return math.sin(2 * math.pi * t)
    if nombre == "fase":
        return t % 1
    if nombre == "triangular":
        return 100 * tri(2.0)
    if nombre == "cuadrada":
        return 1000 if ms % 500 < 250 else -1000
    if nombre == "ms_transcurridos":
        return ms
    if nombre == "contador":
        return ms // (100 if "06_ondas" in str(mock_state.running_elf(STATE, "stm32")) else 250)
    if nombre == "sensor.temperatura":
        return 25 + 3 * math.sin(2 * math.pi * t / 20) + 0.2 * math.sin(2 * math.pi * t * 1.3)
    if nombre == "sensor.adc":
        return int(2048 + 2000 * math.sin(2 * math.pi * t / 5))
    if nombre == "sensor.estado":
        return 1 if math.cos(2 * math.pi * t / 20) > 0 else -1
    m = re.match(r"ultimas\[(\d)\]$", nombre)
    if m:
        return int(1000 * math.sin(2 * math.pi * (t - 0.1 * (int(m.group(1)) + 1))))
    # Cualquier otra: una onda según la dirección, del tipo de la variable.
    periodo = 1 + (var.address >> 2) % 7
    if var.kind.startswith("f"):
        return math.sin(2 * math.pi * t / periodo)
    return int(100 * tri(periodo))


def bytes_simulados(var, t):
    import struct as st
    from app.livevars import KINDS
    size, fmt = KINDS[var.kind]
    v = valor_simulado(var, t)
    if var.kind.startswith("f"):
        return st.pack(fmt, float(v))
    return (int(v) & ((1 << (8 * size)) - 1)).to_bytes(size, "little")


def leer_memoria(direccion, ancho, cantidad, variables, t):
    """Palabras que devuelve read_memory: de las variables del programa grabado; el resto, 0."""
    paso = ancho // 8
    memoria = {}
    for var in variables:
        for i, b in enumerate(bytes_simulados(var, t)):
            memoria[var.address + i] = b
    palabras = []
    for k in range(cantidad):
        base = direccion + k * paso
        palabras.append(int.from_bytes(bytes(memoria.get(base + i, 0) for i in range(paso)), "little"))
    return palabras


def tcl_server(host, port):
    """Puerto TCL simulado: responde los read_memory con las variables del .elf grabado."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(2)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=tcl_cliente, args=(conn,), daemon=True).start()


def tcl_cliente(conn):
    from app.livevars import list_variables
    cache = {}
    buf = b""
    try:
        while True:
            datos = conn.recv(4096)
            if not datos:
                return
            buf += datos
            while b"\x1a" in buf:
                cmd, buf = buf.split(b"\x1a", 1)
                elf = mock_state.running_elf(STATE, "stm32")
                if elf not in cache:
                    cache[elf] = list_variables(Path(elf)) if elf and os.path.isfile(elf) else []
                lecturas = re.findall(r"read_memory (0x[0-9a-fA-F]+) (\d+) (\d+)", cmd.decode(errors="replace"))
                if os.environ.get("BENCH_MOCK_RDP") == "1" and lecturas:
                    resp = f"ERROR Failed to read memory at {lecturas[0][0]}"
                else:
                    t = time.monotonic() - T0
                    palabras = []
                    for d, w, n in lecturas:
                        palabras += leer_memoria(int(d, 16), int(w), int(n), cache[elf], t)
                    resp = " ".join(f"0x{p:x}" for p in palabras)
                conn.sendall(resp.encode() + b"\x1a")
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
        tcl = next((c.split()[1] for c in cmds if c.startswith("tcl_port ")), "disabled")
        if tcl != "disabled":
            threading.Thread(target=tcl_server, args=(bind, int(tcl)), daemon=True).start()
            out(f"Info : Listening on port {tcl} for tcl connections")
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
        if cmd.startswith('echo "UID:'):
            out("UID: 0x001F0035 0x3133510A 0x37383539")
            continue
        if cmd.startswith("dump_image "):
            m = re.match(r"dump_image \{(.*?)\} (0x[0-9A-Fa-f]+) (\d+)", cmd)
            path, address, size = m.group(1), int(m.group(2), 16), int(m.group(3))
            if os.environ.get("BENCH_MOCK_RDP") == "1":
                out(f"Error: Failed to read memory at 0x{address:08x}", 0.3)
                return 1
            Path(path).write_bytes(mock_state.flash_read(STATE, "stm32", address, size))
            out(f"dumped {size} bytes in 0.412s (mock)", 0.6)
            continue
        if "read_memory" in cmd:
            # Verificación del ID del chip (DBGMCU_IDCODE).
            expected = re.search(r"!= (0x[0-9A-Fa-f]+)", cmd) or re.search(r"\$_id (0x[0-9A-Fa-f]+)\]", cmd)
            expected_id = int(expected.group(1), 16) if expected else 0
            # Familias sin ID simulado propio: el chip conectado es el que se espera.
            connected = (int(os.environ.get("BENCH_MOCK_STM32_ID", "0") or "0", 0) or IDS.get(family)
                         or expected_id)
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
            # Como el OpenOCD real: marcas de etapa, sin porcentaje de avance.
            out("** Programming Started **", 0.3)
            if path.lower().endswith(".bin"):
                at = re.search(r"\} (0x[0-9A-Fa-f]+)", cmd)
                address = int(at.group(1), 16) if at else FLASH_BASE
                mock_state.flash_write(STATE, "stm32", address, Path(path).read_bytes())
            else:
                image = convert_to_bin(Path(path), FLASH_BASE, FLASH_BASE + 0x1000000)
                mock_state.flash_write(STATE, "stm32", image.address, image.data)
            mock_state.set_running_elf(STATE, "stm32", path if path.lower().endswith(".elf") else None)
            out("Info : Device: STM32 (mock)")
            out("Info : flash size probed value 1024k", 1.0)
            out("** Programming Finished **")
            out("** Verify Started **", 0.6)
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
        elif cmd.startswith("flash erase_sector") or "mass_erase" in cmd:
            mock_state.flash_erase(STATE, "stm32")
            mock_state.set_running_elf(STATE, "stm32", None)
            bank = cmd.split()[2] if cmd.startswith("flash erase_sector") else "0"
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
