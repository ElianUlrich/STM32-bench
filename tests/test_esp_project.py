"""Proyectos ESP-IDF: se elige la carpeta del proyecto, la web lee build/flasher_args.json
y sólo se suben (y se graban) los .bin que hacen falta, en sus direcciones.

Protección central: nada puede pisar una partición NVS (ahí el programa guarda presets
y ajustes) y el proyecto tiene que ser para el chip de la placa elegida.
"""
import json
import time

import pytest

from app.sample_firmware import esp_idf_build, esp_image

APP = "_subidos/t/demo-idf/demo-idf.bin"


@pytest.fixture(autouse=True)
def p4_connected(monkeypatch):
    # El esptool simulado responde como un ESP32-P4.
    monkeypatch.setenv("BENCH_MOCK_ESP_CHIP", "esp32p4")


def wait_job(client, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if client.get("/api/jobs/current").json()["job"] is None:
            return client.get("/api/history").json()["entries"][0]
        time.sleep(0.1)
    raise AssertionError("el trabajo no terminó")


def upload_project(client, build, target="esp32-p4", project="t", paths=None):
    """Lo que hace la web: el manifiesto + sólo los .bin que lista."""
    manifest = build["flasher_args.json"]
    listed = list(json.loads(manifest)["flash_files"].values())
    paths = listed if paths is None else paths
    files = [("manifest", ("flasher_args.json", manifest, "application/json"))]
    files += [("files", (p.rsplit("/", 1)[-1], build.get(p, b"\xe9"), "application/octet-stream")) for p in paths]
    return client.post("/api/files/esp-project", files=files,
                       data={"project": project, "target": target, "paths": paths})


def listed_files(client):
    return {f["path"]: f for p in client.get("/api/files").json()["projects"] for f in p["files"]}


def flash(client, path, scope=None, action="flash"):
    body = {"action": action, "chip": "esp32-p4", "file": path, "serial_port": "MOCK-USB0"}
    if scope:
        body["esp_scope"] = scope
    return client.post("/api/jobs", json=body)


# ------------------------------------------------------------------ subida

def test_upload_keeps_only_flash_files_and_marks_them(client):
    r = upload_project(client, esp_idf_build())
    assert r.status_code == 200, r.text
    assert r.json()["path"] == APP and r.json()["target"] == "esp32-p4"
    files = listed_files(client)
    project = {p: f for p, f in files.items() if p.startswith("_subidos/t/demo-idf/")}
    assert sorted(project) == ["_subidos/t/demo-idf/bootloader/bootloader.bin", APP,
                               "_subidos/t/demo-idf/partition_table/partition-table.bin"]
    assert all(f["target"] == "esp32-p4" for f in project.values())
    assert files[APP]["esp"] == {"address": "0x20000", "role": "app", "files": 3}
    assert files["_subidos/t/demo-idf/bootloader/bootloader.bin"]["esp"]["address"] == "0x2000"


def test_upload_real_size_program(client):
    # El programa de TONE MASTER pesa ~977 KB: más que el límite de las peticiones comunes.
    r = upload_project(client, esp_idf_build(app_size=977 * 1024))
    assert r.status_code == 200, r.text


def test_upload_rejects_image_over_nvs(client):
    build = esp_idf_build(extra=[(0xB000, "nvs.bin", b"\x00" * 4096)])
    r = upload_project(client, build)
    assert r.status_code == 400 and "NVS" in r.json()["detail"]
    assert not any(p.startswith("_subidos/t/") for p in listed_files(client))


def test_upload_rejects_project_for_other_chip(client):
    r = upload_project(client, esp_idf_build(), target="esp32-s3")
    assert r.status_code == 400 and "ESP32-P4" in r.json()["detail"]
    # flasher_args.json dice esp32p4 pero el programa está compilado para un ESP32-S3.
    build = esp_idf_build()
    build["demo-idf.bin"] = esp_image(9, 8192)
    r = upload_project(client, build)
    assert r.status_code == 400 and "ESP32-S3" in r.json()["detail"]


def test_upload_rejects_app_bigger_than_its_partition(client):
    build = esp_idf_build(parts=(("nvs", 1, 2, 0xB000, 0x10000), ("factory", 0, 0, 0x20000, 0x1000)))
    r = upload_project(client, build)
    assert r.status_code == 400 and "no entra en la partición 'factory'" in r.json()["detail"]


def test_upload_needs_exactly_the_listed_files(client):
    build = esp_idf_build()
    r = upload_project(client, build, paths=["demo-idf.bin"])
    assert r.status_code == 400 and "bootloader/bootloader.bin" in r.json()["detail"]
    r = upload_project(client, build, paths=list(json.loads(build["flasher_args.json"])["flash_files"].values())
                       + ["otro.bin"])
    assert r.status_code == 400 and "otro.bin" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["../x.bin", "/etc/x.bin", "a\\b.bin", "build/.x.bin", "x.elf"])
def test_upload_rejects_bad_paths_in_manifest(client, bad):
    build = esp_idf_build(extra=[(0x220000, bad, b"\x00" * 16)])
    r = upload_project(client, build)
    assert r.status_code == 400


def test_upload_rejects_encrypted_projects(client):
    build = esp_idf_build()
    manifest = json.loads(build["flasher_args.json"])
    manifest["app"]["encrypted"] = "true"
    build["flasher_args.json"] = json.dumps(manifest).encode()
    r = upload_project(client, build)
    assert r.status_code == 400 and "cifrad" in r.json()["detail"]


# --------------------------------------------------------------- grabación

def test_flash_only_the_chosen_file_by_default(client):
    assert upload_project(client, esp_idf_build()).status_code == 200
    r = flash(client, APP)
    assert r.status_code == 202, r.text
    desc = r.json()["job"]["description"]
    assert "@ 0x20000" in desc and "0x2000," not in desc and "0xA000" not in desc
    assert wait_job(client)["state"] == "ok"


def test_flash_whole_project(client):
    assert upload_project(client, esp_idf_build()).status_code == 200
    r = flash(client, APP, scope="all")
    assert r.status_code == 202, r.text
    assert "@ 0x2000, 0xA000, 0x20000" in r.json()["job"]["description"]
    assert wait_job(client)["state"] == "ok"
    r = flash(client, APP, scope="all", action="verify")
    assert r.status_code == 202 and wait_job(client)["state"] == "ok"


def test_flash_refuses_manual_address_for_project_files(client):
    assert upload_project(client, esp_idf_build()).status_code == 200
    body = {"action": "flash", "chip": "esp32-p4", "file": APP, "serial_port": "MOCK-USB0", "address": "0x10000"}
    r = client.post("/api/jobs", json=body)
    assert r.status_code == 400 and "flasher_args.json" in r.json()["detail"]


def test_synced_build_folder_uses_its_manifest(client, settings):
    """Una carpeta build/ que llega por Syncthing (sin subirla) también usa flasher_args.json."""
    build_dir = settings.projects_dir / "pedal" / "build"
    for rel, data in esp_idf_build(name="pedal").items():
        (build_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (build_dir / rel).write_bytes(data)
    app = "pedal/build/pedal.bin"
    assert client.put("/api/files/target", json={"path": app, "target": "esp32-p4"}).status_code == 200
    assert listed_files(client)[app]["esp"]["address"] == "0x20000"
    r = flash(client, app)
    assert r.status_code == 202 and "@ 0x20000" in r.json()["job"]["description"]
    assert wait_job(client)["state"] == "ok"
    # Si alguien agrega a mano una imagen sobre NVS, no se graba.
    manifest = json.loads((build_dir / "flasher_args.json").read_text())
    manifest["flash_files"]["0xb000"] = "nvs.bin"
    (build_dir / "nvs.bin").write_bytes(b"\x00" * 4096)
    (build_dir / "flasher_args.json").write_text(json.dumps(manifest))
    r = flash(client, app)
    assert r.status_code == 400 and "NVS" in r.json()["detail"]
