"""Muchos STM32: cuando el chip conectado no es el elegido, el banco dice cuál es
(sin tocar la flash), y "Identificar" nombra el chip conectado."""
import time

SERIAL = "066DFF485550755187121832"
F411 = {"chip": "stm32f411ce", "probe_serial": SERIAL}


def run_job(client, body):
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 202, r.text
    end = time.time() + 30
    while client.get("/api/jobs/current").json()["job"] is not None:
        assert time.time() < end
        time.sleep(0.05)
    return client.get("/api/history").json()["entries"][0]


def test_wrong_chip_names_the_connected_one(client, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_STM32_ID", "0x433")            # un STM32F401xD/E
    done = run_job(client, dict(F411, action="erase"))
    assert done["state"] == "error"
    assert "CHIP EQUIVOCADO" in done["message"]
    assert "STM32F401CE" in done["message"] and "STM32F401RE" in done["message"]


def test_identify_names_the_connected_chip(client, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_STM32_ID", "0x433")
    done = run_job(client, dict(F411, action="info"))
    assert done["state"] == "ok"
    assert "0x433" in done["message"] and "STM32F401RE" in done["message"]
    monkeypatch.setenv("BENCH_MOCK_STM32_ID", "0x431")
    assert "coincide" in run_job(client, dict(F411, action="info"))["message"]


def test_unknown_id_says_how_to_add_it(client, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_STM32_ID", "0x6AB")
    done = run_job(client, dict(F411, action="erase"))
    assert done["state"] == "error" and "chips.yaml" in done["message"]


def test_catalog_chip_works_end_to_end(client):
    """Un chip del catálogo nuevo (una línea en chips.yaml) se graba como cualquier otro."""
    caps = client.get("/api/config").json()
    chips = {c["id"]: c for c in caps["chips"]}
    assert chips["stm32u575zi"]["family"] == "U5 (Cortex-M33)" and "dfu" in chips["stm32u575zi"]["methods"]
    done = run_job(client, {"chip": "stm32u575zi", "probe_serial": SERIAL, "action": "info"})
    assert done["state"] == "ok", done["message"]
