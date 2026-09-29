"""API HTTP en modo mock: autenticación, protecciones y flujo de trabajos."""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.sample_firmware import demo_images

from .conftest import TEST_PASSWORD, TEST_USER

SERIAL = "066DFF485550755187121832"


def wait_job(client, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if client.get("/api/jobs/current").json()["job"] is None:
            return client.get("/api/history").json()["entries"][0]
        time.sleep(0.1)
    raise AssertionError("el trabajo no terminó")


def test_requires_login(settings):
    with TestClient(create_app(settings), client=("127.0.0.1", 1)) as c:
        assert c.get("/api/config").status_code == 401
        assert c.get("/api/files").status_code == 401
        assert c.get("/").status_code == 200            # la página sí se sirve
        r = c.post("/api/login", json={"username": TEST_USER, "password": "mal"}, headers={"X-Bench": "1"})
        assert r.status_code == 401


def test_network_allowlist(settings):
    with TestClient(create_app(settings), client=("192.168.1.20", 1)) as c:
        assert c.get("/").status_code == 403
        assert c.get("/api/me").status_code == 403


def test_csrf_header_required(client):
    r = client.post("/api/jobs/cancel", headers={"X-Bench": ""})
    assert r.status_code == 403
    assert client.post("/api/jobs/cancel").status_code == 200


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_config(client):
    data = client.get("/api/config").json()
    assert data["mock"] is True
    ids = [c["id"] for c in data["chips"]]
    assert "stm32h503cb" in ids and "stm32h723vg" in ids
    assert data["capabilities"]["dfu"] == ["erase", "flash", "verify"]


def test_upload_scan_delete(client):
    files = {"file": ("mi fw.elf", demo_images()["elf"], "application/octet-stream")}
    r = client.post("/api/files/upload", files=files, data={"project": "banco"})
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    listing = client.get("/api/files").json()["projects"]
    assert any(f["path"] == path for p in listing for f in p["files"])
    r = client.post("/api/files/upload", files={"file": ("../evil.elf", b"x", "application/octet-stream")})
    assert r.status_code == 400
    r = client.post("/api/files/upload", files={"file": ("x.exe", b"x", "application/octet-stream")})
    assert r.status_code == 400
    assert client.delete("/api/files", params={"path": "demo-blink/Debug/demo-blink.bin"}).status_code == 400
    assert client.delete("/api/files", params={"path": path}).status_code == 200


def test_upload_too_big_rejected_early(client):
    big = b"\x00" * (17 * 1024 * 1024)
    r = client.post("/api/files/upload", files={"file": ("big.bin", big, "application/octet-stream")})
    assert r.status_code == 413


def test_probes(client):
    data = client.get("/api/probes").json()
    assert len(data["stlinks"]) == 2 and data["dfu"] == []


def test_job_validation(client):
    r = client.post("/api/jobs", json={"action": "flash", "chip": "no-existe", "file": "x.bin"})
    assert r.status_code == 400
    r = client.post("/api/jobs", json={"action": "reset", "chip": "stm32h723vg", "method": "dfu"})
    assert r.status_code == 400 and "USB DFU" in r.json()["detail"]
    r = client.post("/api/jobs", json={"action": "flash", "chip": "stm32h723vg", "probe_serial": SERIAL,
                                       "file": "../../etc/passwd"})
    assert r.status_code == 400
    # varios ST-Link conectados y ninguno elegido
    r = client.post("/api/jobs", json={"action": "reset", "chip": "stm32h723vg"})
    assert r.status_code == 400 and "elegí uno" in r.json()["detail"]
    r = client.post("/api/jobs", json={"action": "reset", "chip": "stm32h723vg", "probe_serial": "NOEXISTE"})
    assert r.status_code == 400
    # .bin fuera de la flash
    r = client.post("/api/jobs", json={"action": "flash", "chip": "stm32h503cb", "probe_serial": SERIAL,
                                       "file": "demo-blink/Debug/demo-blink.bin", "address": "0x0801FF00"})
    assert r.status_code == 400 and "no entra" in r.json()["detail"]


def test_flash_busy_and_history(client):
    body = {"action": "flash", "chip": "stm32h723vg", "probe_serial": SERIAL,
            "file": "demo-blink/Debug/demo-blink.elf"}
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 202
    r2 = client.post("/api/jobs", json=dict(body, action="reset"))
    assert r2.status_code == 409 and r2.json()["code"] == "busy"
    entry = wait_job(client)
    assert entry["state"] == "ok" and entry["action"] == "flash"


def test_gdb_blocks_flash_until_confirmed(client):
    r = client.post("/api/gdb/start", json={"chip": "stm32h723vg", "probe_serial": SERIAL})
    assert r.status_code == 200, r.text
    status = r.json()
    assert status["state"] == "listening" and status["connect"].endswith(":3333")
    body = {"action": "flash", "chip": "stm32h723vg", "probe_serial": SERIAL,
            "file": "demo-blink/Debug/demo-blink.hex"}
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 409 and r.json()["code"] == "gdb_active"
    r = client.post("/api/gdb/start", json={"chip": "stm32h723vg", "probe_serial": SERIAL})
    assert r.status_code == 400   # ya está en marcha
    r = client.post("/api/jobs", json=dict(body, stop_gdb=True))
    assert r.status_code == 202
    assert client.get("/api/gdb").json()["active"] is False
    assert wait_job(client)["state"] == "ok"


def test_gdb_unknown_chip(client):
    r = client.post("/api/gdb/start", json={"chip": "no-existe"})
    assert r.status_code == 400


def test_serial_monitor(client):
    data = client.get("/api/serial").json()
    port = data["ports"][0]["device"]
    assert client.post("/api/serial/open", json={"port": "/dev/ttyS0", "baud": 115200}).status_code == 400
    assert client.post("/api/serial/open", json={"port": port, "baud": 12345}).status_code == 400
    assert client.post("/api/serial/open", json={"port": port, "baud": 115200}).json()["open"] is True
    assert client.post("/api/serial/send", json={"text": "hola", "eol": "\r\n"}).status_code == 200
    assert client.post("/api/serial/send", json={"text": "x", "eol": "; rm"}).status_code == 400
    assert client.post("/api/serial/close").json()["open"] is False


def test_system_and_power(client):
    info = client.get("/api/system").json()
    assert info["memory"]["total"] > 0 and info["tailscale_ip"]
    assert client.post("/api/system/poweroff", json={}).status_code == 400
    r = client.post("/api/system/reboot", json={"confirm": True})
    assert r.status_code == 200 and "simulado" in r.json()["command"]


def test_logout(client):
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401
