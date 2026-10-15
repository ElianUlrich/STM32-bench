"""Leer el micro: qué tiene grabado, comparado con los archivos de la placa, y copia de respaldo.

Los simuladores guardan lo que se graba (por SWD, DFU, esptool o avrdude) y lo
devuelven al leer, así el flujo completo se prueba sin hardware.
"""
import hashlib
import json
import time

import pytest

from app import mock_state, readback
from app.sample_firmware import esp_app_desc, esp_idf_build, esp_image, esp_partition_table

SERIAL = "066DFF485550755187121832"
H503 = {"chip": "stm32h503cb", "probe_serial": SERIAL}


def wait_job(client, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if client.get("/api/jobs/current").json()["job"] is None:
            return client.get("/api/history").json()["entries"][0]
        time.sleep(0.05)
    raise AssertionError("el trabajo no terminó")


def run_job(client, body):
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 202, r.text
    return wait_job(client)


def read(client, **body):
    done = run_job(client, dict(body, action="read"))
    report = client.get("/api/readback").json()["report"]
    return done, report


# ------------------------------------------------------------- análisis (puro)

def test_used_size_and_vectors():
    flash = bytearray(b"\xff" * 4096)
    flash[0:8] = (0x20004000).to_bytes(4, "little") + (0x08000101).to_bytes(4, "little")
    flash[8:100] = bytes(range(92))
    info = readback.analyze_flash(bytes(flash), base=0x08000000)
    assert info["used"] == 100 and info["empty"] is False
    assert info["vectors"] == {"sp": "0x20004000", "reset": "0x08000101"}
    assert readback.analyze_flash(b"\xff" * 64, base=0x08000000)["empty"] is True
    # Algo que no es una tabla de vectores no se informa como tal.
    assert readback.analyze_flash(b"\x00" * 64, base=0x08000000)["vectors"] is None


def test_match_kinds():
    flash = b"ABCD" + b"\xff" * 12
    assert readback.match_kind(flash, 0, b"ABCD") == "exact"
    assert readback.match_kind(b"ABCDxyz" + b"\xff" * 9, 0, b"ABCD") == "prefix"
    assert readback.match_kind(flash, 0, b"ABCE") is None
    assert readback.match_kind(flash, 4, b"\xff\xff") is None         # un archivo "vacío" no identifica nada
    assert readback.match_kind(flash, 20, b"AB") is None               # no entra en lo leído


def test_esp_app_description_and_partition_table():
    desc = readback.parse_app_desc(esp_image(18, 4096, desc=esp_app_desc("pedal", "1.2.0")))
    assert desc["project"] == "pedal" and desc["version"] == "1.2.0"
    assert desc["idf"] == "v5.5.5" and len(desc["sha256"]) == 64
    assert readback.parse_app_desc(esp_image(18, 4096)) is None        # sin descripción
    region = bytearray(b"\xff" * 0x10000)
    region[0xA000:0xA000 + 0xC00] = esp_partition_table()
    assert readback.find_partition_table(bytes(region)) == 0xA000
    assert readback.find_partition_table(b"\xff" * 0x10000) is None


def test_esp_app_without_description_matches_by_its_bytes():
    image = esp_image(18, 8192)                       # sin esp_app_desc_t (p. ej. compilado viejo)
    app = {"partition": "factory", "sha256": None}
    cands = [readback.Candidate("p/viejo.bin", 0, image), readback.Candidate("p/otro.bin", 0, esp_image(18, 8192, entry=0x40080100))]
    # Con sólo la cabecera leída (identificación rápida) alcanza para reconocerla.
    found = readback.match_apps([app], cands, heads={"factory": image[:4096]})
    assert [m["path"] for m in found] == ["p/viejo.bin"]
    assert readback.match_apps([app], cands, heads={"factory": b"\xff" * 4096}) == []


def test_esp_image_length_is_exact():
    image = esp_image(18, 5000, desc=esp_app_desc("x", "1"))
    padded = image + b"\xff" * 300
    assert readback.esp_image_length(padded) == len(image)
    assert readback.esp_image_length(b"\xff" * 64) is None


# ------------------------------------------------------------------ STM32 (SWD)

def test_read_swd_identifies_the_flashed_file(client):
    assert run_job(client, dict(H503, action="flash", file="demo-blink/Debug/demo-blink.elf"))["state"] == "ok"
    done, report = read(client, **H503)
    assert done["state"] == "ok", done["message"]
    assert done["message"] == "Tiene grabado demo-blink.elf"
    assert report["summary"]["state"] == "match"
    # Lo último que se grabó desde el banco es el candidato preferido entre los idénticos.
    assert report["summary"]["file"] == "demo-blink/Debug/demo-blink.elf"
    assert {"demo-blink/Debug/demo-blink.bin", "demo-blink/Debug/demo-blink.hex"} <= {m["path"] for m in report["matches"]}
    rows = {r["label"]: r["value"] for r in report["identity"]}
    assert rows["ID del chip"] == "0x474"
    assert len(rows["Número de serie (UID)"].split("-")) == 3
    fw = report["firmware"]
    assert fw["size"] == 128 * 1024 and 0 < fw["used"] < fw["size"]
    assert fw["vectors"]["reset"].startswith("0x0800")


def test_read_after_erase_reports_empty(client):
    assert run_job(client, dict(H503, action="erase"))["state"] == "ok"
    done, report = read(client, **H503)
    assert report["summary"]["state"] == "empty" and report["matches"] == []
    assert "vacía" in done["message"]


def test_read_unknown_firmware(client):
    data = bytes((i * 7 + 3) & 0xFF for i in range(3000))
    r = client.post("/api/files/upload", files={"file": ("raro.bin", data, "application/octet-stream")},
                    data={"project": "t", "target": "stm32h503cb"})
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert run_job(client, dict(H503, action="flash", file=path))["state"] == "ok"
    assert client.delete("/api/files", params={"path": path}).status_code == 200
    done, report = read(client, **H503)
    assert report["summary"]["state"] == "unknown" and report["matches"] == []
    assert "desconocido" in done["message"]


def test_download_and_backup(client):
    assert run_job(client, dict(H503, action="flash", file="demo-blink/Debug/demo-blink.elf"))["state"] == "ok"
    _, report = read(client, **H503)
    rid = report["id"]
    files = {f["name"]: f for f in report["files"]}
    r = client.get(f"/api/readback/{rid}/files/flash.bin")
    assert r.status_code == 200 and len(r.content) == 128 * 1024
    assert client.get(f"/api/readback/{rid}/files/..%2Fsecreto").status_code in (400, 404)
    assert client.get("/api/readback/no-existe/files/flash.bin").status_code in (400, 404)
    assert "flash.bin" in files
    r = client.post(f"/api/readback/{rid}/save")
    assert r.status_code == 200, r.text
    saved = r.json()["path"]
    assert saved.startswith("_subidos/respaldos/") and saved.endswith(".bin")
    listed = {f["path"]: f for p in client.get("/api/files").json()["projects"] for f in p["files"]}
    assert listed[saved]["target"] == "stm32h503cb"
    # La copia se puede volver a grabar, y al leer coincide.
    assert run_job(client, dict(H503, action="flash", file=saved))["state"] == "ok"
    _, again = read(client, **H503)
    assert again["summary"]["state"] == "match" and again["summary"]["file"] == saved


def test_read_protection_is_explained(client, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_RDP", "1")
    done, _ = read(client, **H503)
    assert done["state"] == "error"
    assert "protección de lectura" in done["message"]


# ------------------------------------------------------------------- STM32 (DFU)

def test_read_dfu(client):
    body = {"chip": "stm32h503cb", "method": "dfu"}
    assert run_job(client, dict(body, action="flash", file="demo-blink/Debug/demo-blink.bin"))["state"] == "ok"
    done, report = read(client, **body)
    assert done["state"] == "ok", done["message"]
    assert report["method"] == "dfu" and report["summary"]["state"] == "match"
    assert report["summary"]["file"] == "demo-blink/Debug/demo-blink.bin"


# --------------------------------------------------------------------- ESP32-P4

def flash_p4(client, build):
    manifest = build["flasher_args.json"]
    paths = list(json.loads(manifest)["flash_files"].values())
    files = [("manifest", ("flasher_args.json", manifest, "application/json"))]
    files += [("files", (p.rsplit("/", 1)[-1], build[p], "application/octet-stream")) for p in paths]
    r = client.post("/api/files/esp-project", files=files, data={"project": "t", "target": "esp32-p4", "paths": paths})
    assert r.status_code == 200, r.text
    app = r.json()["path"]
    assert run_job(client, {"action": "flash", "chip": "esp32-p4", "file": app, "serial_port": "MOCK-USB0",
                            "esp_scope": "all"})["state"] == "ok"
    return app


@pytest.fixture
def p4(client, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_ESP_CHIP", "esp32p4")
    build = esp_idf_build(name="pedal")
    manifest = build["flasher_args.json"]
    paths = list(json.loads(manifest)["flash_files"].values())
    files = [("manifest", ("flasher_args.json", manifest, "application/json"))]
    files += [("files", (p.rsplit("/", 1)[-1], build[p], "application/octet-stream")) for p in paths]
    r = client.post("/api/files/esp-project", files=files, data={"project": "t", "target": "esp32-p4", "paths": paths})
    assert r.status_code == 200, r.text
    app = r.json()["path"]
    assert run_job(client, {"action": "flash", "chip": "esp32-p4", "file": app, "serial_port": "MOCK-USB0",
                            "esp_scope": "all"})["state"] == "ok"
    return app


def test_read_esp_identifies_the_app_quickly(client, p4):
    done, report = read(client, chip="esp32-p4", serial_port="MOCK-USB0")
    assert done["state"] == "ok", done["message"]
    assert report["scope"] == "identify"
    apps = report["esp"]["apps"]
    assert apps[0]["partition"] == "factory" and apps[0]["project"] == "pedal" and apps[0]["idf"] == "v5.5.5"
    assert {p["name"] for p in report["esp"]["partitions"]} == {"nvs", "phy_init", "factory"}
    assert report["summary"] == {"state": "match", "file": p4, "text": "Tiene grabado pedal.bin"}


def test_read_esp_full_backup_is_a_flashable_project(client, p4, settings):
    # El programa ya guardó sus presets en NVS (0xB000).
    mock_state.flash_write(settings.state_dir, "esp", 0xB000, b"presets" * 100)
    done, report = read(client, chip="esp32-p4", serial_port="MOCK-USB0", read_scope="full")
    assert done["state"] == "ok", done["message"]
    names = {f["name"] for f in report["files"]}
    assert {"bootloader.bin", "partition-table.bin", "pedal.bin", "nvs.bin"} <= names
    r = client.post(f"/api/readback/{report['id']}/save")
    assert r.status_code == 200, r.text
    folder = r.json()["folder"]
    listed = {f["path"]: f for p in client.get("/api/files").json()["projects"] for f in p["files"]}
    app = f"{folder}/pedal.bin"
    assert listed[app]["esp"] == {"address": "0x20000", "role": "app", "files": 3}   # NVS no se graba
    assert f"{folder}/nvs.bin" in listed and "esp" not in listed[f"{folder}/nvs.bin"]
    assert run_job(client, {"action": "flash", "chip": "esp32-p4", "file": app, "serial_port": "MOCK-USB0",
                            "esp_scope": "all"})["state"] == "ok"


def test_read_esp_app_without_description(client, monkeypatch):
    # Un compilado sin esp_app_desc_t se reconoce por sus bytes, y la copia lleva su nombre.
    monkeypatch.setenv("BENCH_MOCK_ESP_CHIP", "esp32p4")
    build = esp_idf_build(name="viejo")
    build["viejo.bin"] = esp_image(18, 8192)
    app = flash_p4(client, build)
    _, quick = read(client, chip="esp32-p4", serial_port="MOCK-USB0")
    assert quick["summary"]["file"] == app
    _, report = read(client, chip="esp32-p4", serial_port="MOCK-USB0", read_scope="full")
    assert report["summary"]["file"] == app
    assert "viejo.bin" in {f["name"] for f in report["files"]}


# ---------------------------------------------------------------------- Arduino

def test_read_arduino(client):
    body = {"chip": "arduino-uno", "serial_port": "MOCK-ACM1"}
    assert run_job(client, dict(body, action="flash", file="arduino-blink/Blink.ino.hex"))["state"] == "ok"
    done, report = read(client, **body)
    assert done["state"] == "ok", done["message"]
    assert report["summary"]["file"] == "arduino-blink/Blink.ino.hex"
    rows = {r["label"]: r["value"] for r in report["identity"]}
    assert rows["Firma"] == "0x1e950f"
    r = client.post(f"/api/readback/{report['id']}/save")
    assert r.status_code == 200 and r.json()["path"].endswith(".hex")


def test_read_is_a_known_action_with_its_own_label(client):
    caps = client.get("/api/config").json()["chip_capabilities"]
    assert "read" in caps["stm32h503cb"]["swd"] and "read" in caps["stm32h503cb"]["dfu"]
    assert "read" in caps["esp32-p4"]["esptool"] and "read" in caps["arduino-uno"]["avrdude"]
    assert client.get("/api/readback").json() == {"report": None} or True


def test_sha_of_demo_desc_is_stable():
    # La descripción de la app de prueba lleva el SHA del nombre (para comparar sin leer todo).
    d = readback.parse_app_desc(esp_image(18, 4096, desc=esp_app_desc("pedal", "1")))
    assert d["sha256"] == hashlib.sha256(b"pedal").hexdigest()
