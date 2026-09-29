"""Conversión de .elf / .hex a .bin para DFU."""
import pytest

from app.convert import ConversionError, build_bin, convert_to_bin, Segment
from app.sample_firmware import FLASH_BASE, RAM_BASE, build_elf, demo_images, to_ihex, vector_table

FLASH_END = FLASH_BASE + 128 * 1024


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_hex_contiguous(tmp_path):
    payload = bytes(range(256)) * 4
    path = write(tmp_path, "fw.hex", to_ihex([(FLASH_BASE, payload)]).encode())
    image = convert_to_bin(path, FLASH_BASE, FLASH_END)
    assert image.address == FLASH_BASE and image.data == payload and image.warnings == []


def test_hex_gap_is_filled_and_warned(tmp_path):
    a, b = b"\x11" * 32, b"\x22" * 16
    path = write(tmp_path, "gap.hex", to_ihex([(FLASH_BASE, a), (FLASH_BASE + 0x10000, b)]).encode())
    image = convert_to_bin(path, FLASH_BASE, FLASH_END)
    assert len(image.data) == 0x10000 + 16
    assert image.data[:32] == a and image.data[-16:] == b
    assert set(image.data[32:0x10000]) == {0xFF}
    assert any("discontinuos" in w for w in image.warnings)


def test_small_gap_no_warning(tmp_path):
    path = write(tmp_path, "g.hex", to_ihex([(FLASH_BASE, b"\x01" * 16), (FLASH_BASE + 64, b"\x02" * 16)]).encode())
    image = convert_to_bin(path, FLASH_BASE, FLASH_END)
    assert image.warnings == [] and image.data[16:64] == b"\xff" * 48


def test_hex_outside_flash_fails(tmp_path):
    path = write(tmp_path, "ext.hex", demo_images()["hex_ext"])
    with pytest.raises(ConversionError, match="fuera de la flash"):
        convert_to_bin(path, FLASH_BASE, FLASH_END)


def test_image_bigger_than_flash_fails(tmp_path):
    path = write(tmp_path, "big.hex", to_ihex([(FLASH_BASE, b"\x00" * 64)]).encode())
    with pytest.raises(ConversionError):
        convert_to_bin(path, FLASH_BASE, FLASH_BASE + 32)


def test_elf_uses_load_address(tmp_path):
    """.data se ejecuta en RAM (VMA) pero se guarda en flash (LMA): debe ir en flash."""
    text = vector_table(512)
    data = b"\xAB\xCD" * 8
    elf = build_elf([(FLASH_BASE, FLASH_BASE, text), (FLASH_BASE + len(text), RAM_BASE, data)])
    image = convert_to_bin(write(tmp_path, "fw.elf", elf), FLASH_BASE, FLASH_END)
    assert image.address == FLASH_BASE
    assert image.data == text + data
    assert image.warnings == []


def test_elf_ram_segment_without_lma_fails(tmp_path):
    elf = build_elf([(FLASH_BASE, FLASH_BASE, b"\x00" * 64), (RAM_BASE, RAM_BASE, b"\x01" * 16)])
    with pytest.raises(ConversionError, match="RAM"):
        convert_to_bin(write(tmp_path, "fw.elf", elf), FLASH_BASE, FLASH_END)


def test_elf_non_arm_fails(tmp_path):
    elf = build_elf([(FLASH_BASE, FLASH_BASE, b"\x00" * 64)], machine=62)   # x86-64 (pero 32 bits)
    with pytest.raises(ConversionError, match="ARM"):
        convert_to_bin(write(tmp_path, "fw.elf", elf), FLASH_BASE, FLASH_END)


def test_invalid_files(tmp_path):
    with pytest.raises(ConversionError):
        convert_to_bin(write(tmp_path, "x.hex", b":zz\n"), FLASH_BASE, FLASH_END)
    with pytest.raises(ConversionError):
        convert_to_bin(write(tmp_path, "x.elf", b"\x7fELF basura"), FLASH_BASE, FLASH_END)
    with pytest.raises(ConversionError):
        convert_to_bin(write(tmp_path, "x.bin", b"\x00"), FLASH_BASE, FLASH_END)


def test_image_not_at_flash_base_is_warned():
    image = build_bin([Segment(FLASH_BASE + 0x8000, b"\x01" * 8)], FLASH_BASE, FLASH_END)
    assert image.address == FLASH_BASE + 0x8000
    assert any("no en la base" in w for w in image.warnings)


def test_overlapping_segments_fail():
    with pytest.raises(ConversionError, match="solapados"):
        build_bin([Segment(FLASH_BASE, b"\x01" * 16), Segment(FLASH_BASE + 8, b"\x02" * 16)], FLASH_BASE, FLASH_END)


def test_elf_and_hex_of_demo_are_equivalent(tmp_path):
    images = demo_images()
    from_elf = convert_to_bin(write(tmp_path, "d.elf", images["elf"]), FLASH_BASE, FLASH_END)
    from_hex = convert_to_bin(write(tmp_path, "d.hex", images["hex"]), FLASH_BASE, FLASH_END)
    assert from_elf.data == from_hex.data == images["bin"]
