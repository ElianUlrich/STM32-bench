"""Vínculo binario ↔ placa y flujos completos de ESP32 / Arduino / identificación en modo mock."""
import time

from app.sample_firmware import avr_hex, demo_images, esp_image

SERIAL = "066DFF485550755187121832"


def wait_job(client, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if client.get("/api/jobs/current").json()["job"] is None:
            return client.get("/api/history").json()["entries"][0]
        time.sleep(0.1)
    raise AssertionError("el trabajo no terminó")


def upload(client, name, data, target, project="t"):
    return client.post("/api/files/upload", files={"file": (name, data, "application/octet-stream")},
                       data={"project": project, "target": target})


# ------------------------------------------------------------------ subida

def test_upload_requires_target(client):
    r = client.post("/api/files/upload", files={"file": ("a.bin", esp_image(0), "application/octet-stream")})
    assert r.status_code == 422
    assert upload(client, "a.bin", esp_image(0), "no-existe").status_code == 400


def test_upload_rejects_binary_for_other_chip(client):
    r = upload(client, "s3.bin", esp_image(9), "esp32")
    assert r.status_code == 400 and "ESP32-S3" in r.json()["detail"]
    r = upload(client, "blink.hex", avr_hex(900), "esp32")
    assert r.status_code == 400 and ".bin" in r.json()["detail"]
    r = upload(client, "fw.elf", demo_images()["elf"], "arduino-uno")
    assert r.status_code == 400


def test_upload_assigns_target_and_listing_shows_it(client):
    r = upload(client, "esp.bin", esp_image(0), "esp32")
    assert r.status_code == 200 and r.json()["target"] == "esp32"
    files = [f for p in client.get("/api/files").json()["projects"] for f in p["files"]]
    assert next(f for f in files if f["path"] == r.json()["path"])["target"] == "esp32"


# ------------------------------------------------------------- asignación

def test_assign_and_unassign_existing_file(client):
    path = "esp32s3-demo/s3-demo.bin"
    r = client.put("/api/files/target", json={"path": path, "target": "esp32"})
    assert r.status_code == 400 and "ESP32-S3" in r.json()["detail"]      # contenido incompatible
    assert client.put("/api/files/target", json={"path": path, "target": None}).status_code == 200
    r = client.post("/api/jobs", json={"action": "flash", "chip": "esp32-s3", "file": path,
                                       "serial_port": "MOCK-USB0"})
    assert r.status_code == 400 and "no tiene placa asignada" in r.json()["detail"]
    assert client.put("/api/files/target", json={"path": path, "target": "esp32-s3"}).status_code == 200
    r = client.put("/api/files/target", json={"path": "../x.bin", "target": "esp32"})
    assert r.status_code == 400


def test_flash_refused_on_wrong_board(client):
    r = client.post("/api/jobs", json={"action": "flash", "chip": "esp32", "file": "esp32s3-demo/s3-demo.bin",
                                       "serial_port": "MOCK-USB0"})
    assert r.status_code == 400 and "es para ESP32-S3" in r.json()["detail"]
    r = client.post("/api/jobs", json={"action": "flash", "chip": "stm32h723vg", "probe_serial": SERIAL,
                                       "file": "demo-blink/Debug/demo-blink.elf"})
    assert r.status_code == 400 and "STM32H503" in r.json()["detail"]


# --------------------------------------------------------------- ESP32 (mock)

def test_esp32_flash_app_and_merged(client):
    for path, addr in (("esp32-blink/build/esp32-blink.ino.bin", "0x10000"),
                       ("esp32-blink/build/esp32-blink.ino.merged.bin", "0x0")):
        r = client.post("/api/jobs", json={"action": "flash", "chip": "esp32", "file": path,
                                           "serial_port": "MOCK-USB0"})
        assert r.status_code == 202, r.text
        assert f"@ {addr.upper().replace('0X', '0x')}" in r.json()["job"]["description"]
        assert wait_job(client)["state"] == "ok"


def test_esp32_erase_reset_info(client):
    for action in ("erase", "reset", "info"):
        r = client.post("/api/jobs", json={"action": action, "chip": "esp32", "serial_port": "MOCK-USB0"})
        assert r.status_code == 202, r.text
        assert wait_job(client)["state"] == "ok", action


def test_esp32_wrong_port_fails_and_gdb_not_allowed(client):
    r = client.post("/api/jobs", json={"action": "info", "chip": "esp32", "serial_port": "MOCK-ACM1"})
    assert r.status_code == 202
    assert wait_job(client)["state"] == "error"
    r = client.post("/api/jobs", json={"action": "halt", "chip": "esp32", "serial_port": "MOCK-USB0"})
    assert r.status_code == 400 and "JTAG" in r.json()["detail"]
    r = client.post("/api/jobs", json={"action": "info", "chip": "esp32", "serial_port": "/dev/ttyS0"})
    assert r.status_code == 400


def test_serial_monitor_released_and_reopened(client):
    assert client.post("/api/serial/open", json={"port": "MOCK-USB0", "baud": 115200}).json()["open"]
    r = client.post("/api/jobs", json={"action": "flash", "chip": "esp32",
                                       "file": "esp32-blink/build/esp32-blink.ino.bin", "serial_port": "MOCK-USB0"})
    assert r.status_code == 202
    assert wait_job(client)["state"] == "ok"
    status = client.get("/api/serial").json()
    assert status["open"] and status["port"] == "MOCK-USB0"


# -------------------------------------------------------------- Arduino (mock)

def test_arduino_uno_flash_verify_info_reset(client):
    for action in ("flash", "verify", "info", "reset"):
        body = {"action": action, "chip": "arduino-uno", "serial_port": "MOCK-ACM1"}
        if action in ("flash", "verify"):
            body["file"] = "arduino-blink/Blink.ino.hex"
        r = client.post("/api/jobs", json=body)
        assert r.status_code == 202, r.text
        assert wait_job(client)["state"] == "ok", action


def test_arduino_signature_mismatch(client):
    # Un .hex válido para Mega, pero el puerto tiene un Uno: avrdude compara la firma y aborta.
    r = upload(client, "mega.hex", avr_hex(2000), "arduino-mega2560")
    assert r.status_code == 200
    r = client.post("/api/jobs", json={"action": "flash", "chip": "arduino-mega2560", "file": r.json()["path"],
                                       "serial_port": "MOCK-ACM1"})
    assert r.status_code == 202
    entry = wait_job(client)
    assert entry["state"] == "error"


def test_arduino_erase_only_with_isp(client):
    r = client.post("/api/jobs", json={"action": "erase", "chip": "arduino-uno", "serial_port": "MOCK-ACM1"})
    assert r.status_code == 400 and "ISP" in r.json()["detail"]
    r = client.post("/api/jobs", json={"action": "erase", "chip": "avr-atmega328p-usbasp"})
    assert r.status_code == 202
    assert wait_job(client)["state"] == "ok"


def test_leonardo_touch_1200(client):
    r = client.post("/api/jobs", json={"action": "flash", "chip": "arduino-leonardo",
                                       "file": "arduino-leonardo/Teclado.ino.hex", "serial_port": "MOCK-ACM2"})
    assert r.status_code == 202
    assert wait_job(client)["state"] == "ok"


# --------------------------------------------------------- STM32: ID del chip

def test_stm32_identify_and_wrong_chip(client, monkeypatch):
    r = client.post("/api/jobs", json={"action": "info", "chip": "stm32h503cb", "probe_serial": SERIAL})
    assert r.status_code == 202
    assert wait_job(client)["state"] == "ok"
    monkeypatch.setenv("BENCH_MOCK_STM32_ID", "0x483")          # hay un H7 conectado, no un H5
    r = client.post("/api/jobs", json={"action": "flash", "chip": "stm32h503cb", "probe_serial": SERIAL,
                                       "file": "demo-blink/Debug/demo-blink.elf"})
    assert r.status_code == 202
    entry = wait_job(client)
    assert entry["state"] == "error"


def test_config_exposes_capabilities(client):
    cfg = client.get("/api/config").json()
    assert cfg["chip_capabilities"]["arduino-uno"]["avrdude"] == ["flash", "info", "reset", "verify"]
    assert "gdb" in cfg["chip_capabilities"]["stm32h503cb"]["swd"]
    assert cfg["unsupported_reasons"]["esptool"]
    assert len(cfg["chips"]) >= 25
