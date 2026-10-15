"""Progreso de los trabajos: etapa y porcentaje leídos de la salida real de cada herramienta.

Las muestras de salida son las que imprimen las herramientas reales (no los
simuladores), con cada actualización de progreso en su propia línea: así llegan
al lector de procesos, que separa por \\n y por \\r.
"""
import time

import pytest

from app.progress import tracker_for
from app.sample_firmware import esp_idf_build


def feed_all(tracker, text):
    """Pasa la salida línea por línea y devuelve las fotos del estado cuando cambia."""
    shots = []
    for line in text.strip("\n").split("\n"):
        changed, _ = tracker.feed(line)
        if changed:
            shots.append(tracker.snapshot())
    return shots


def stages_seen(shots):
    out = []
    for s in shots:
        if not out or out[-1] != s["stage"]:
            out.append(s["stage"])
    return out


# ------------------------------------------------------------------- esptool

ESPTOOL_FLASH_3 = """
esptool.py v4.12.0
Serial port /dev/ttyACM0
Connecting....
Chip is ESP32-P4 (revision v1.0)
Uploading stub...
Running stub...
Stub running...
Changing baud rate to 921600
Changed.
Configuring flash size...
Flash will be erased from 0x00002000 to 0x00007fff...
Flash will be erased from 0x0000a000 to 0x0000afff...
Flash will be erased from 0x00020000 to 0x0011cfff...
Compressed 22992 bytes to 14275...
Writing at 0x00002000... (100 %)
Wrote 22992 bytes (14275 compressed) at 0x00002000 in 0.5 seconds (effective 368.0 kbit/s)...
Hash of data verified.
Compressed 3072 bytes to 103...
Writing at 0x0000a000... (100 %)
Wrote 3072 bytes (103 compressed) at 0x0000a000 in 0.1 seconds (effective 343.2 kbit/s)...
Hash of data verified.
Compressed 1000000 bytes to 612345...
Writing at 0x00020000... (2 %)
Writing at 0x0008f000... (50 %)
Writing at 0x0011c000... (100 %)
Wrote 1000000 bytes (612345 compressed) at 0x00020000 in 14.2 seconds (effective 563.4 kbit/s)...
Hash of data verified.

Leaving...
Hard resetting via RTS pin...
"""


def test_esptool_several_files_weighted_by_size():
    t = tracker_for("esptool", "flash", sizes=[22992, 3072, 1000000])
    assert t.snapshot() == {"stages": ["connect", "write", "reset"], "stage": "connect", "pct": None,
                            "detail": None}
    shots = feed_all(t, ESPTOOL_FLASH_3)
    assert stages_seen(shots) == ["write", "reset"]
    pcts = [s["pct"] for s in shots if s["stage"] == "write"]
    assert pcts == sorted(pcts), "el porcentaje nunca retrocede"
    total = 22992 + 3072 + 1000000
    assert any(abs(p - 100 * 22992 / total) < 0.2 for p in pcts)            # terminó el bootloader
    assert any(abs(p - 100 * (22992 + 3072 + 500000) / total) < 0.2 for p in pcts)  # mitad del programa
    assert pcts[-1] == 100
    mid = next(s for s in shots if s["stage"] == "write" and 50 < s["pct"] < 99)
    assert mid["detail"] == "archivo 3 de 3"


def test_esptool_without_sizes_uses_the_erase_ranges():
    t = tracker_for("esptool", "flash")
    shots = feed_all(t, ESPTOOL_FLASH_3)
    pcts = [s["pct"] for s in shots if s["stage"] == "write"]
    assert pcts == sorted(pcts) and pcts[-1] == 100
    # Pesos de los rangos de borrado: 0x6000, 0x1000 y 0xFD000 bytes.
    assert any(abs(p - 100 * 0x6000 / (0x6000 + 0x1000 + 0xFD000)) < 0.2 for p in pcts)


def test_esptool_progress_lines_are_marked():
    t = tracker_for("esptool", "flash", sizes=[1000])
    assert t.feed("Writing at 0x00010000... (37 %)")[1] is True
    assert t.feed("Hash of data verified.")[1] is False


def test_esptool_verify_and_erase():
    t = tracker_for("esptool", "verify", sizes=[22992, 3072])
    shots = feed_all(t, """
Connecting....
Verifying 0x59d0 (22992) bytes @ 0x00002000 in flash against bootloader.bin...
-- verify OK (digest matched)
Verifying 0xc00 (3072) bytes @ 0x0000a000 in flash against partition-table.bin...
-- verify OK (digest matched)
""")
    assert stages_seen(shots) == ["verify"] and shots[-1]["pct"] == 100
    t = tracker_for("esptool", "erase")
    shots = feed_all(t, """
Connecting....
Erasing flash (this may take a while)...
Chip erase completed successfully in 3.1s
""")
    assert t.snapshot()["stages"] == ["connect", "erase"]
    assert stages_seen(shots) == ["erase"] and shots[-1]["pct"] == 100


# ------------------------------------------------------------------- OpenOCD

OPENOCD_FLASH = """
Open On-Chip Debugger 0.12.0+dev-00573-g0ecc3d5c7
Licensed under GNU GPL v2
Info : clock speed 8000 kHz
Info : STLINK V2J45S7 (API v2) VID:PID 0483:3748
Info : Target voltage: 3.243
Info : [stm32h5x.cpu] Cortex-M33 r0p4 processor detected
Info : [stm32h5x.cpu] target has 8 breakpoints, 4 watchpoints
Chip verificado: ID 0x474
[stm32h5x.cpu] halted due to debug-request, current mode: Thread
xPSR: 0x01000000 pc: 0x08000254 msp: 0x20004000
** Programming Started **
Info : Device: STM32H503
Info : flash size probed value 128k
** Programming Finished **
** Verify Started **
** Verified OK **
** Resetting Target **
shutdown command invoked
"""


def test_openocd_flash_goes_through_its_stages():
    t = tracker_for("openocd", "flash")
    assert t.snapshot()["stages"] == ["connect", "write", "verify", "reset"]
    shots = feed_all(t, OPENOCD_FLASH)
    assert stages_seen(shots) == ["write", "verify", "reset"]
    # OpenOCD no informa porcentaje: la etapa en curso es indeterminada.
    assert all(s["pct"] is None for s in shots)


def test_openocd_verify_and_erase():
    t = tracker_for("openocd", "verify")
    shots = feed_all(t, """
Chip verificado: ID 0x474
[stm32h5x.cpu] halted due to debug-request, current mode: Thread
verified 924 bytes in 0.120s (7.520 KiB/s)
""")
    assert t.snapshot()["stages"] == ["connect", "verify"]
    assert stages_seen(shots) == ["verify"] and shots[-1]["pct"] == 100
    t = tracker_for("openocd", "erase")
    shots = feed_all(t, """
Chip verificado: ID 0x474
erased sectors 0 through 15 on flash bank 0 in 1.234s
""")
    assert stages_seen(shots) == ["erase"] and shots[-1]["pct"] == 100


def test_quick_actions_have_no_tracker():
    for tool in ("openocd", "esptool", "avrdude", "dfu-util"):
        for action in ("reset", "halt", "resume", "info"):
            assert tracker_for(tool, action) is None


# ------------------------------------------------------------------ dfu-util

DFU_DOWNLOAD = """
dfu-util 0.11
Opening DFU capable USB device...
Device ID 0483:df11
Claiming USB DFU Interface...
Setting Alternate Interface #0 ...
Determining device status...
DFU state(2) = dfuIDLE, status(0) = No error condition is present
DfuSe interface name: "Internal Flash   "
Downloading element to address = 0x08000000, size = 2412
Erase   	[                         ]   0%            0 bytes
Erase   	[=========================] 100%         2412 bytes
Erase    done.
Download	[                         ]   0%            0 bytes
Download	[============             ]  48%         1024 bytes
Download	[=========================] 100%         2412 bytes
Download done.
File downloaded successfully
Submitting leave request...
Transitioning to dfuMANIFEST state
"""


def test_dfu_download_erase_then_write():
    t = tracker_for("dfu-util", "flash")
    assert t.snapshot()["stages"] == ["connect", "erase", "write"]
    shots = feed_all(t, DFU_DOWNLOAD)
    assert stages_seen(shots) == ["erase", "write"]
    writes = [s["pct"] for s in shots if s["stage"] == "write"]
    assert 48 in writes and writes[-1] == 100
    assert t.feed("Download\t[=====      ]  20%   512 bytes")[1] is True
    assert t.feed("Download done.")[1] is False


def test_dfu_upload_is_verify():
    t = tracker_for("dfu-util", "verify")
    shots = feed_all(t, """
Opening DFU capable USB device...
Uploading from address = 0x08000000, size = 2412
Upload	[============             ]  50%         1206 bytes
Upload	[=========================] 100%         2412 bytes
Upload done.
""")
    assert stages_seen(shots) == ["verify"] and shots[-1]["pct"] == 100


# ------------------------------------------------------------------- avrdude

AVRDUDE_FLASH = """
avrdude: AVR device initialized and ready to accept instructions

Reading | ################################################## | 100% 0.00s

avrdude: Device signature = 0x1e950f (probably m328p)
avrdude: reading input file "Blink.ino.hex"
avrdude: writing flash (924 bytes):

Writing | ################################################## | 100% 0.17s

avrdude: 924 bytes of flash written
avrdude: verifying flash memory against Blink.ino.hex:
avrdude: load data flash data from input file Blink.ino.hex:
avrdude: input file Blink.ino.hex contains 924 bytes
avrdude: reading on-chip flash data:

Reading | ################################################## | 100% 0.15s

avrdude: verifying ...
avrdude: 924 bytes of flash verified

avrdude done.  Thank you.
"""


def test_avrdude_signature_read_is_not_the_verify():
    t = tracker_for("avrdude", "flash")
    assert t.snapshot()["stages"] == ["connect", "write", "verify"]
    shots = feed_all(t, AVRDUDE_FLASH)
    assert stages_seen(shots) == ["write", "verify"]
    assert next(s for s in shots if s["stage"] == "write" and s["pct"] == 100)
    assert shots[-1] == {"stages": ["connect", "write", "verify"], "stage": "verify", "pct": 100, "detail": None}


def test_unknown_tool_or_action():
    assert tracker_for("otra", "flash") is None
    with pytest.raises(ValueError):
        tracker_for("esptool", "flash", sizes=[0, -1])


# ------------------------------------------------ en un trabajo (banco simulado)

def watch_job(client, timeout=30):
    """Fotos del trabajo en curso (vía /api/jobs/current) hasta que termina."""
    shots = []
    end = time.time() + timeout
    while time.time() < end:
        job = client.get("/api/jobs/current").json()["job"]
        if job is None:
            return shots, client.get("/api/history").json()["entries"][0]
        shots.append(job)
        time.sleep(0.02)
    raise AssertionError("el trabajo no terminó")


def test_flash_job_publishes_its_progress(client, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_ESP_CHIP", "esp32p4")
    build = esp_idf_build(app_size=64 * 1024)
    import json
    manifest = build["flasher_args.json"]
    paths = list(json.loads(manifest)["flash_files"].values())
    files = [("manifest", ("flasher_args.json", manifest, "application/json"))]
    files += [("files", (p.rsplit("/", 1)[-1], build[p], "application/octet-stream")) for p in paths]
    r = client.post("/api/files/esp-project", files=files,
                    data={"project": "t", "target": "esp32-p4", "paths": paths})
    assert r.status_code == 200, r.text
    r = client.post("/api/jobs", json={"action": "flash", "chip": "esp32-p4", "file": r.json()["path"],
                                       "serial_port": "MOCK-USB0", "esp_scope": "all"})
    assert r.status_code == 202, r.text
    shots, done = watch_job(client)
    progress = [s["progress"] for s in shots if s.get("progress")]
    assert progress, "el trabajo en curso informa su progreso"
    assert progress[0]["stages"] == ["connect", "write", "reset"]
    writing = [p["pct"] for p in progress if p["stage"] == "write" and p["pct"] is not None]
    assert writing and writing == sorted(writing) and 0 < max(writing) <= 100
    assert done["state"] == "ok" and "progress" not in done        # el historial no guarda el progreso


def test_progress_lines_are_flagged_in_the_log(client):
    r = client.post("/api/jobs", json={"action": "flash", "chip": "esp32",
                                       "file": "esp32-blink/build/esp32-blink.ino.bin",
                                       "serial_port": "MOCK-USB0"})
    assert r.status_code == 202, r.text
    watch_job(client)
    logs = [e for e in client.app.state.bench.bus.replay() if e["type"] == "log" and e.get("source") == "esptool"]
    writing = [e for e in logs if "Writing at" in e["line"]]
    assert writing and all(e.get("progress") is True for e in writing)
    assert all("progress" not in e for e in logs if "Writing at" not in e["line"])


# ----------------------------------------------------------------------- lectura

def test_read_trackers():
    t = tracker_for("openocd", "read")
    assert t.snapshot()["stages"] == ["connect", "read"]
    shots = feed_all(t, """
Chip verificado: ID 0x474
dumped 131072 bytes in 2.412s (53.068 KiB/s)
""")
    assert stages_seen(shots) == ["read"] and shots[-1]["pct"] == 100

    t = tracker_for("esptool", "read")
    shots = feed_all(t, """
Connecting....
Chip is ESP32-P4 (revision v1.0)
16384 (25 %)
65536 (100 %)
Read 65536 bytes at 0x00000000 in 1.5 seconds (349.5 kbit/s)...
Hard resetting via RTS pin...
""")
    assert stages_seen(shots) == ["read"] and [s["pct"] for s in shots] == [25, 100]
    assert t.feed("32768 (50 %)")[1] is True

    t = tracker_for("dfu-util", "read")
    shots = feed_all(t, """
Uploading from address = 0x08000000, size = 131072
Upload	[============             ]  50%        65536 bytes
Upload	[=========================] 100%       131072 bytes
Upload done.
""")
    assert stages_seen(shots) == ["read"] and shots[-1]["pct"] == 100

    t = tracker_for("avrdude", "read")
    shots = feed_all(t, """
avrdude: AVR device initialized and ready to accept instructions
Reading | ################################################## | 100% 0.00s
avrdude: Device signature = 0x1e950f (probably m328p)
avrdude: reading flash memory:
Reading | ################################################## | 100% 4.21s
avrdude: writing output file "flash.bin"
""")
    assert stages_seen(shots) == ["read"] and shots[-1]["pct"] == 100
