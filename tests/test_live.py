"""Gráficos de variables en vivo: qué variables tiene el .elf, cómo se leen por el
puerto TCL de OpenOCD con el micro corriendo, y el muestreo completo con el simulador."""
import struct
from pathlib import Path

from app import livevars

ONDAS = Path(__file__).resolve().parent.parent / "firmware" / "h503_leds" / "build" / "06_ondas.elf"


# ------------------------------------------------------------ variables del .elf

def test_globals_with_types_struct_fields_and_arrays():
    found = {v.name: v for v in livevars.list_variables(ONDAS)}
    assert found["seno"].kind == "f32" and found["frecuencia_hz"].kind == "f32"
    assert found["cuadrada"].kind == "i16" and found["contador"].kind == "u32"
    assert found["ms_transcurridos"].kind == "u32"                     # de placa.c
    # Campos de un struct, con su desplazamiento.
    t, adc, est = found["sensor.temperatura"], found["sensor.adc"], found["sensor.estado"]
    assert (t.kind, adc.kind, est.kind) == ("f32", "u16", "i8")
    assert adc.address == t.address + 4 and est.address == t.address + 6
    # Elementos de un array.
    assert [found[f"ultimas[{i}]"].address - found["ultimas[0]"].address for i in range(8)] == list(range(0, 16, 2))
    # Una variable static dentro de una función.
    assert found["fase (ondas_paso)"].kind == "f32"
    assert all(0x20000000 <= v.address < 0x20004000 for v in found.values())
    # Las constantes (en flash, no cambian) no se ofrecen.
    assert not any(n.startswith("tabla_vectores") for n in found)
    # Ordenadas por nombre (los índices como números) y sin repetir.
    names = [v.name for v in livevars.list_variables(ONDAS)]
    assert names[0] == "amplitud" and names.index("ultimas[1]") < names.index("ultimas[7]")
    assert len(names) == len(set(names))


def test_elf_without_debug_info_has_no_variables(tmp_path):
    bad = tmp_path / "x.elf"
    bad.write_bytes(b"no es un elf")
    assert livevars.list_variables(bad) == []


# ------------------------------------------------------- lectura por TCL (puro)

def test_read_command_and_decoding():
    vs = [livevars.Variable("f", 0x20000000, "f32"), livevars.Variable("s", 0x20000004, "i16"),
          livevars.Variable("b", 0x20000007, "u8"), livevars.Variable("d", 0x20000008, "f64"),
          livevars.Variable("x", 0x20000011, "u32")]                 # desalineada: se lee de a bytes
    cmd = livevars.read_command(vs)
    assert "read_memory 0x20000000 32 1" in cmd and "read_memory 0x20000004 16 1" in cmd
    assert "read_memory 0x20000008 64 1" in cmd and "read_memory 0x20000011 8 4" in cmd
    d = struct.unpack("<Q", struct.pack("<d", -2.5))[0]
    reply = f"0x3f800000 0xfc18 0xff 0x{d:x} 0x78 0x56 0x34 0x12"
    assert livevars.decode(vs, reply) == [1.0, -1000, 255, -2.5, 0x12345678]


def test_decoding_errors():
    vs = [livevars.Variable("a", 0x20000000, "u32")]
    try:
        livevars.decode(vs, "ERROR Failed to read memory at 0x20000000")
    except livevars.LiveError as exc:
        assert "0x20000000" in str(exc)
    else:
        raise AssertionError("debía fallar")


# ------------------------------------------------- muestreo (API + simulador)

import time  # noqa: E402

SERIAL = "066DFF485550755187121832"
ONDAS_REL = "h503-contador/build/06_ondas.elf"
H503 = {"chip": "stm32h503cb", "probe_serial": SERIAL}


def free_ports(client):
    import dataclasses
    import socket

    def free():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    bench = client.app.state.bench
    bench.cfg = dataclasses.replace(bench.cfg, gdb_port=free(), rtt_port=free())


def flash_ondas(client):
    r = client.post("/api/jobs", json=dict(H503, action="flash", file=ONDAS_REL))
    assert r.status_code == 202, r.text
    end = time.time() + 30
    while client.get("/api/jobs/current").json()["job"] is not None:
        assert time.time() < end
        time.sleep(0.05)


def wait_live(client, pred, timeout=10):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = client.get("/api/live").json()
        if pred(st):
            return st
        time.sleep(0.05)
    raise AssertionError(f"estado inesperado: {st}")


def start_live(client, names, rate_hz=50):
    return client.post("/api/live/start", json=dict(H503, file=ONDAS_REL, variables=names, rate_hz=rate_hz))


def test_variables_of_a_program(client):
    r = client.get("/api/live/variables", params={"file": ONDAS_REL})
    assert r.status_code == 200, r.text
    names = {v["name"]: v for v in r.json()["variables"]}
    assert names["seno"]["type"] == "f32" and names["seno"]["address"].startswith("0x2000")
    r = client.get("/api/live/variables", params={"file": "demo-blink/Debug/demo-blink.hex"})
    assert r.status_code == 400 and ".elf" in r.json()["detail"]


def test_live_sampling_with_the_micro_running(client):
    free_ports(client)
    flash_ondas(client)
    r = start_live(client, ["seno", "cuadrada", "sensor.temperatura"])
    assert r.status_code == 200, r.text
    st = wait_live(client, lambda s: len(s["samples"]) >= 25)
    assert st["state"] == "running" and st["rate_hz"] == 50
    assert [v["name"] for v in st["variables"]] == ["seno", "cuadrada", "sensor.temperatura"]
    times = [s[0] for s in st["samples"]]
    assert times == sorted(times) and times[-1] - times[0] > 0.3
    seno = [s[1] for s in st["samples"]]
    assert all(-1.01 <= v <= 1.01 for v in seno) and max(seno) - min(seno) > 0.3      # cambia
    assert {s[2] for s in st["samples"]} <= {1000, -1000}
    assert all(20 < s[3] < 30 for s in st["samples"])
    # Usa el OpenOCD compartido: el servidor GDB sigue disponible para la PC.
    assert client.get("/api/gdb").json()["state"] == "listening"
    assert client.post("/api/live/stop").json()["state"] == "stopped"
    client.post("/api/gdb/stop")


def test_stopping_openocd_stops_sampling(client):
    free_ports(client)
    flash_ondas(client)
    assert start_live(client, ["contador"]).status_code == 200
    wait_live(client, lambda s: len(s["samples"]) >= 3)
    client.post("/api/gdb/stop")
    assert client.get("/api/live").json()["state"] == "stopped"


def test_live_bad_requests(client):
    free_ports(client)
    assert start_live(client, ["no_existe"]).status_code == 400
    assert start_live(client, []).status_code == 400
    assert start_live(client, [f"ultimas[{i}]" for i in range(8)] + ["seno"]).status_code == 400   # máx. 8
    assert start_live(client, ["seno"], rate_hz=1000).status_code == 400
    r = client.post("/api/live/start", json=dict(H503, file="demo-blink/Debug/demo-blink.elf", variables=["x"]))
    assert r.status_code == 400


def test_read_errors_are_reported(client, monkeypatch):
    free_ports(client)
    flash_ondas(client)
    monkeypatch.setenv("BENCH_MOCK_RDP", "1")
    client.post("/api/gdb/stop")                    # que el simulador arranque con la protección
    assert start_live(client, ["seno"]).status_code == 200
    st = wait_live(client, lambda s: s["state"] == "error")
    assert "0x2000" in st["error"]
    client.post("/api/gdb/stop")
