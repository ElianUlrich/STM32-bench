"""Cuando una herramienta falla, el resultado dice por qué (la línea de error de su salida),
no sólo "terminó con código 1"."""
import time

from app.programmers.runner import failure_reason

SERIAL = "066DFF485550755187121832"


def wait_job(client, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if client.get("/api/jobs/current").json()["job"] is None:
            return client.get("/api/history").json()["entries"][0]
        time.sleep(0.1)
    raise AssertionError("el trabajo no terminó")


def test_failure_reason_picks_the_last_error_line():
    out = "\n".join([
        "Info : clock speed 8000 kHz",
        "** Programming Started **",
        "Error: checksum mismatch - attempting binary compare",
        "diff 0 address 0x08000400. Was 0xff instead of 0x12",
        "** Verify Failed **",
        "shutdown command invoked",
    ])
    assert failure_reason(out) == "Verify Failed"
    assert failure_reason("A fatal error occurred: Failed to connect to Espressif device: No serial data received.") \
        == "A fatal error occurred: Failed to connect to Espressif device: No serial data received."
    assert failure_reason("Error: unable to find a matching ST-Link") == "unable to find a matching ST-Link"
    assert failure_reason("todo bien\notra línea") is None
    assert failure_reason("") is None
    long = "Error: " + "x" * 400
    assert len(failure_reason(long)) <= 200


def test_failed_verify_says_why(client):
    r = client.post("/api/jobs", json={"action": "flash", "chip": "stm32h503cb", "probe_serial": SERIAL,
                                       "file": "casos-dfu/demo-falla.elf"})
    assert r.status_code == 202, r.text
    done = wait_job(client)
    assert done["state"] == "error"
    assert done["message"] == "openocd falló: Verify Failed"


def test_esp_without_board_says_why(client):
    r = client.post("/api/jobs", json={"action": "info", "chip": "esp32", "serial_port": "MOCK-ACM1"})
    assert r.status_code == 202, r.text
    done = wait_job(client)
    assert done["state"] == "error"
    assert done["message"].startswith("esptool falló: A fatal error occurred: Failed to connect")


def test_failure_hints():
    from app.programmers.runner import failure_hint
    assert "ST-Link" in failure_hint("open failed")
    assert "ST-Link" in failure_hint("unable to find a matching ST-Link")
    assert failure_hint("Verify Failed") is None


def test_failure_reason_keeps_the_wrong_chip_message():
    out = ("Info : [stm32f4x.cpu] Cortex-M4 r0p1 processor detected\n"
           "CHIP EQUIVOCADO: se esperaba el ID 0x431 y el chip conectado tiene 0x433. No se toca la flash.\n"
           "shutdown command invoked\n")
    assert failure_reason(out).startswith("CHIP EQUIVOCADO: se esperaba el ID 0x431")
