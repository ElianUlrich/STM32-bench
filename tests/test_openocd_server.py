"""Un solo OpenOCD compartido por el servidor GDB, la pestaña Depurar y RTT:
cuándo se reutiliza, cuándo se reinicia y cuándo no se puede tocar."""
import dataclasses
import socket
import time

import pytest

SERIAL = "066DFF485550755187121832"
RTT_ELF = "h503-contador/build/05_contador_rtt.elf"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def use_free_ports(client):
    bench = client.app.state.bench
    bench.cfg = dataclasses.replace(bench.cfg, gdb_port=free_port(), rtt_port=free_port())
    return bench.cfg


def wait_gdb(client, pred, timeout=5):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = client.get("/api/gdb").json()
        if pred(st):
            return st
        time.sleep(0.05)
    raise AssertionError(f"estado del servidor GDB inesperado: {st}")


def test_rtt_does_not_cut_an_external_gdb(client):
    """Con un GDB de la PC conectado, abrir RTT no puede reiniciar OpenOCD (le cortaría la sesión)."""
    cfg = use_free_ports(client)
    started = client.post("/api/gdb/start", json={"chip": "stm32h503cb", "probe_serial": SERIAL}).json()
    externo = socket.create_connection(("127.0.0.1", cfg.gdb_port))
    try:
        wait_gdb(client, lambda s: s["client_connected"])
        r = client.post("/api/rtt/open", json={"chip": "stm32h503cb", "probe_serial": SERIAL, "file": RTT_ELF})
        assert r.status_code == 409, r.text
        assert "GDB" in r.json()["detail"] and "conectado" in r.json()["detail"]
        st = client.get("/api/gdb").json()
        assert st["active"] and st["client_connected"] and st["started"] == started["started"]
    finally:
        externo.close()
        client.post("/api/gdb/stop")


def test_debug_gets_rtt_when_server_was_started_without_it(client):
    """El servidor estaba iniciado sin RTT (otro .elf): al depurar un programa con RTT se reinicia con RTT."""
    use_free_ports(client)
    assert client.post("/api/gdb/start", json={"chip": "stm32h503cb", "probe_serial": SERIAL}).json()["rtt_state"] is None
    r = client.post("/api/debug/start", json={"chip": "stm32h503cb", "probe_serial": SERIAL, "file": RTT_ELF,
                                              "mode": "attach"})
    assert r.status_code == 200, r.text
    # OpenOCD avisa que encontró el bloque RTT un instante después de arrancar.
    st = wait_gdb(client, lambda s: s["rtt_state"] == "encontrado")
    assert st["rtt"]["address"] == "0x20000430"
    client.post("/api/debug/stop")
    client.post("/api/gdb/stop")


def test_rtt_reuses_the_server_of_the_debug_session(client):
    """Depurando un programa con RTT, abrir RTT usa el mismo OpenOCD (no corta la depuración)."""
    use_free_ports(client)
    assert client.post("/api/debug/start", json={"chip": "stm32h503cb", "probe_serial": SERIAL, "file": RTT_ELF,
                                                 "mode": "attach"}).status_code == 200
    started = client.get("/api/gdb").json()["started"]
    r = client.post("/api/rtt/open", json={"chip": "stm32h503cb", "probe_serial": SERIAL, "file": RTT_ELF})
    assert r.status_code == 200, r.text
    assert client.get("/api/gdb").json()["started"] == started
    assert client.get("/api/debug").json()["state"] == "stopped"
    client.post("/api/serial/close")
    client.post("/api/debug/stop")
    client.post("/api/gdb/stop")


def test_other_chip_is_refused_while_debugging(client):
    use_free_ports(client)
    assert client.post("/api/debug/start", json={"chip": "stm32h503cb", "probe_serial": SERIAL, "file": RTT_ELF,
                                                 "mode": "attach"}).status_code == 200
    r = client.post("/api/rtt/open", json={"chip": "stm32h723vg", "probe_serial": SERIAL, "file": RTT_ELF})
    assert r.status_code == 409 and "depuración" in r.json()["detail"]
    client.post("/api/debug/stop")
    client.post("/api/gdb/stop")


def test_network_ports_are_relayed_and_openocd_listens_only_locally(client):
    """OpenOCD escucha en 127.0.0.1 (con el puerto TCL); a la red salen GDB y RTT reenviados."""
    cfg = use_free_ports(client)
    st = client.post("/api/gdb/start", json={"chip": "stm32h503cb", "probe_serial": SERIAL, "file": RTT_ELF}).json()
    try:
        local = st["local"]
        assert len({cfg.gdb_port, cfg.rtt_port, local["gdb"], local["rtt"], local["tcl"]}) == 5
        # Un cliente externo recibe el canal RTT por el puerto público.
        with socket.create_connection(("127.0.0.1", cfg.rtt_port), timeout=5) as rtt:
            data = b""
            while b"05_contador_rtt" not in data:
                data += rtt.recv(256)
        # Y un GDB externo por el puerto público llega a OpenOCD.
        with socket.create_connection(("127.0.0.1", cfg.gdb_port)):
            wait_gdb(client, lambda s: s["client_connected"])
        wait_gdb(client, lambda s: not s["client_connected"])
    finally:
        client.post("/api/gdb/stop")
    # Al detenerlo se cierran también los puertos públicos.
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", cfg.gdb_port), timeout=1).close()
