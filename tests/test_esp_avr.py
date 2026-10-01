"""ESP32 (esptool) y Arduino (avrdude): análisis de binarios y construcción de comandos."""
from pathlib import Path

import pytest

from app.chips import AvrdudeParams, ConfigError, EsptoolParams, parse_chips
from app.programmers import avrdude as avr
from app.programmers import esptool as esp
from app.sample_firmware import avr_hex, esp_image, esp_merged, to_ihex

ESP32 = EsptoolParams(chip="esp32", baud=460800, app_address=0x10000)
ESP32S3 = EsptoolParams(chip="esp32s3", baud=460800, app_address=0x10000)
ESP8266 = EsptoolParams(chip="esp8266", baud=460800, app_address=0x0)
CMD = ("esptool.py",)


# --------------------------------------------------------------------- ESP: .bin

def test_esp_app_image():
    img = esp.analyze_bin(esp_image(0, 4096), ESP32)
    assert img.kind == "aplicación" and img.address == 0x10000 and img.chip_id == 0


def test_esp_merged_image_goes_to_zero():
    img = esp.analyze_bin(esp_merged(0, 0x1000), ESP32)
    assert img.kind == "combinada" and img.address == 0x0
    img = esp.analyze_bin(esp_merged(9, 0x0), ESP32S3)
    assert img.kind == "combinada" and img.address == 0x0


def test_esp_wrong_chip_rejected():
    with pytest.raises(esp.EspImageError, match="ESP32-S3, no para ESP32"):
        esp.analyze_bin(esp_image(9, 4096), ESP32)
    with pytest.raises(esp.EspImageError, match="ESP32-C3"):
        esp.analyze_bin(esp_image(5, 4096), ESP32S3)


def test_esp_merged_for_other_bootloader_offset():
    with pytest.raises(esp.EspImageError, match="0x1000"):
        esp.analyze_bin(esp_merged(0, 0x1000), ESP32S3)


def test_esp8266_has_no_chip_id():
    img = esp.analyze_bin(esp_image(0, 2048), ESP8266)
    assert img.address == 0x0 and img.chip_id is None


def test_esp_garbage_rejected():
    with pytest.raises(esp.EspImageError, match="0xE9"):
        esp.analyze_bin(b"\x00" * 4096, ESP32)
    with pytest.raises(esp.EspImageError, match="chico"):
        esp.analyze_bin(b"\xe9", ESP32)


def test_esp_explicit_address_warns():
    img = esp.analyze_bin(esp_image(0, 4096), ESP32, address=0x20000)
    assert img.address == 0x20000 and "0x20000" in img.warnings[0]


# ----------------------------------------------------------------- ESP: comandos

def test_esp_commands():
    fw = Path("/srv/p/app.bin")
    a = esp.flash_args(CMD, ESP32, "/dev/ttyUSB0", fw, 0x10000)
    assert a[:11] == ["esptool.py", "--chip", "esp32", "--port", "/dev/ttyUSB0", "--baud", "460800",
                      "--before", "default_reset", "--after", "hard_reset"]
    assert a[11:] == ["write_flash", "--flash_mode", "keep", "--flash_freq", "keep", "--flash_size", "keep",
                      "0x10000", str(fw)]
    assert esp.verify_args(CMD, ESP32, "/dev/ttyUSB0", fw, 0x0)[-3:] == ["verify_flash", "0x0", str(fw)]
    assert esp.erase_args(CMD, ESP32, "/dev/ttyUSB0")[-1] == "erase_flash"
    assert esp.reset_args(CMD, ESP32, "/dev/ttyUSB0")[-1] == "read_mac"
    info = esp.info_args(CMD, ESP32, "/dev/ttyUSB0")
    assert info[info.index("--chip") + 1] == "auto" and info[-1] == "flash_id"


@pytest.mark.parametrize("port", ["", "/dev/tty USB0", "/dev/ttyUSB0;reboot", "../../etc", "COM1000", "$(id)"])
def test_esp_bad_port(port):
    with pytest.raises(esp.EspImageError):
        esp.erase_args(CMD, ESP32, port)


# -------------------------------------------------------------------- AVR: .hex

UNO = AvrdudeParams(part="atmega328p", programmer="arduino", baud=115200, flash_kb=32, bootloader_kb=0.5)
MEGA = AvrdudeParams(part="atmega2560", programmer="wiring", baud=115200, flash_kb=256, bootloader_kb=8,
                     no_auto_erase=True)
ISP = AvrdudeParams(part="atmega328p", programmer="usbasp", baud=None, flash_kb=32, bootloader_kb=0)
LEO = AvrdudeParams(part="atmega32u4", programmer="avr109", baud=57600, flash_kb=32, bootloader_kb=4,
                    touch_1200=True)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_avr_hex_fits(tmp_path):
    img = avr.analyze_hex(write(tmp_path, "b.hex", avr_hex(1000)), UNO)
    assert img.size == 1000 and img.warnings == []


def test_avr_hex_overlaps_bootloader(tmp_path):
    with pytest.raises(avr.AvrHexError, match="bootloader"):
        avr.analyze_hex(write(tmp_path, "b.hex", avr_hex(32 * 1024 - 100)), UNO)


def test_avr_hex_for_bigger_chip(tmp_path):
    with pytest.raises(avr.AvrHexError, match="chip más grande"):
        avr.analyze_hex(write(tmp_path, "b.hex", avr_hex(40 * 1024)), UNO)


def test_avr_hex_invalid(tmp_path):
    with pytest.raises(avr.AvrHexError):
        avr.analyze_hex(write(tmp_path, "b.hex", b":zz\n"), UNO)


def test_avr_hex_not_at_zero_warns(tmp_path):
    img = avr.analyze_hex(write(tmp_path, "b.hex", to_ihex([(0x100, b"\x01" * 16)]).encode()), UNO)
    assert "0x100" in img.warnings[0]


# ----------------------------------------------------------------- AVR: comandos

def test_avr_commands():
    fw = Path("/srv/p/Blink.ino.hex")
    assert avr.flash_args(("avrdude",), UNO, "/dev/ttyACM0", fw) == [
        "avrdude", "-p", "atmega328p", "-c", "arduino", "-P", "/dev/ttyACM0", "-b", "115200",
        "-U", f"flash:w:{fw}:i"]
    mega = avr.flash_args(("avrdude",), MEGA, "/dev/ttyACM0", fw)
    assert "-D" in mega and mega[mega.index("-c") + 1] == "wiring"
    assert avr.verify_args(("avrdude",), UNO, "/dev/ttyACM0", fw)[-1] == f"flash:v:{fw}:i"
    info = avr.info_args(("avrdude",), UNO, "/dev/ttyACM0")
    assert "-U" not in info


def test_avr_isp_uses_usb_and_can_erase():
    args = avr.erase_args(("avrdude",), ISP, "")
    assert args == ["avrdude", "-p", "atmega328p", "-c", "usbasp", "-P", "usb", "-e"]
    assert ISP.is_isp and not UNO.is_isp


def test_avr_bad_port():
    with pytest.raises(avr.AvrHexError):
        avr.info_args(("avrdude",), UNO, "/dev/ttyACM0 -U flash:w:/etc/passwd")


# --------------------------------------------------------------- catálogo nuevo

def test_catalog_new_methods_validation():
    base = {"id": "x", "name": "X"}
    with pytest.raises(ConfigError, match="esptool conocido"):
        parse_chips([dict(base, methods={"esptool": {"chip": "esp99"}})])
    with pytest.raises(ConfigError, match="programmer"):
        parse_chips([dict(base, methods={"avrdude": {"part": "atmega328p", "programmer": "rm", "flash_kb": 32}})])
    with pytest.raises(ConfigError, match="part"):
        parse_chips([dict(base, methods={"avrdude": {"part": "atmega 328p; x", "flash_kb": 32}})])
    with pytest.raises(ConfigError, match="flash"):
        parse_chips([dict(base, methods={"swd": {"target_cfg": "target/x.cfg"}})])   # swd exige flash
    ok = parse_chips([dict(base, methods={"esptool": {"chip": "esp32c3"}})])[0]
    assert ok.methods == ["esptool"] and ok.group.startswith("Espressif") and ok.extensions() == [".bin"]
    assert ok.esptool.app_address == 0x10000


def test_catalog_idcode():
    chip = parse_chips([{"id": "h5", "flash": {"base": 0x08000000, "size_kb": 128},
                         "methods": {"swd": {"target_cfg": "target/stm32h5x.cfg",
                                             "idcode": {"address": "0x44024000", "value": "0x474"}}}}])[0]
    assert chip.swd.idcode.address == 0x44024000 and chip.swd.idcode.value == 0x474 and chip.swd.idcode.mask == 0xFFF


def test_repo_catalog_has_all_families():
    from app.chips import load_chips
    from app.settings import REPO_ROOT, load_bench_config
    bench = load_bench_config(REPO_ROOT / "config" / "bench.yaml")
    chips = {c.id: c for c in load_chips(REPO_ROOT / "config" / "chips.yaml", bench.interfaces)}
    groups = {c.group for c in chips.values()}
    assert groups == {"STM32", "Espressif (ESP)", "Arduino / AVR"}
    for cid in ("esp32", "esp32-s3", "esp32-c3", "esp8266", "arduino-uno", "arduino-mega2560", "arduino-leonardo"):
        assert cid in chips, cid
    assert all(c.swd.idcode is not None for c in chips.values() if c.swd)
