"""Validación de archivos, protección contra path traversal y escaneo."""
import io
import os

import pytest

from app import files as f
from app.sample_firmware import demo_images
from app.settings import UPLOADS_DIRNAME

IMAGES = demo_images()


@pytest.mark.parametrize("name", [
    "firmware.elf", "Firmware_v1.2-rc1.HEX", "app (debug).bin", "a+b.bin", "demo blink.elf"])
def test_valid_names(name):
    assert f.validate_filename(name) == name


@pytest.mark.parametrize("name", [
    "", ".hidden.elf", "../x.elf", "dir/x.elf", "dir\\x.elf", "x.exe", "x", "x.elf.sh",
    "x{y}.elf", "x[1].bin", "$x.bin", "x;rm.bin", 'x".bin', "x`id`.bin", "x.bin ", "x" * 200 + ".bin",
    "firmware\n.bin",
])
def test_invalid_names(name):
    with pytest.raises(f.FileValidationError):
        f.validate_filename(name)


def test_project_names():
    assert f.validate_project_name("") == "general"
    assert f.validate_project_name("mi-proyecto") == "mi-proyecto"
    for bad in ("../x", ".oculto", "a/b", "x{"):
        with pytest.raises(f.FileValidationError):
            f.validate_project_name(bad)


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "proyectos"
    (root / "p1" / "build").mkdir(parents=True)
    (root / "p1" / "build" / "fw.elf").write_bytes(IMAGES["elf"])
    (root / "p1" / "fw.bin").write_bytes(IMAGES["bin"])
    (tmp_path / "secreto.bin").write_bytes(b"\x00" * 16)
    return root


@pytest.mark.parametrize("rel", [
    "../secreto.bin", "p1/../../secreto.bin", "/etc/passwd", "p1/./fw.bin", "p1//fw.bin",
    "p1\\fw.bin", "", "C:/Windows/win.ini", "p1/fw.bin\x00.elf",
])
def test_path_traversal_rejected(root, rel):
    with pytest.raises(f.FileValidationError):
        f.resolve_firmware(root, rel)


def test_resolve_ok(root):
    path = f.resolve_firmware(root, "p1/build/fw.elf")
    assert path.name == "fw.elf" and path.is_file()


def test_resolve_missing_and_bad_extension(root):
    with pytest.raises(f.FileValidationError, match="No existe"):
        f.resolve_firmware(root, "p1/nada.bin")
    (root / "p1" / "notas.txt").write_text("x")
    with pytest.raises(f.FileValidationError):
        f.resolve_firmware(root, "p1/notas.txt")


def test_symlink_outside_rejected(root, tmp_path):
    link = root / "p1" / "link.bin"
    try:
        os.symlink(tmp_path / "secreto.bin", link)
    except (OSError, NotImplementedError):
        pytest.skip("El sistema no permite crear symlinks")
    with pytest.raises(f.FileValidationError, match="sale del directorio"):
        f.resolve_firmware(root, "p1/link.bin")


def test_tcl_unsafe_paths():
    from pathlib import Path
    assert f.ensure_tcl_safe(Path("/srv/proyectos/a b/fw.elf")) == "/srv/proyectos/a b/fw.elf"
    for bad in ("/srv/{x}/fw.elf", "/srv/[exec]/fw.elf", "/srv/$x/fw.elf", '/srv/"/fw.elf'):
        with pytest.raises(f.FileValidationError):
            f.ensure_tcl_safe(Path(bad))


# --- subida -------------------------------------------------------------------

def test_upload_ok(tmp_path):
    saved = f.save_upload(tmp_path, "proy", "fw.elf", io.BytesIO(IMAGES["elf"]), max_bytes=1 << 20)
    assert saved.rel_path == f"{UPLOADS_DIRNAME}/proy/fw.elf"
    assert (tmp_path / saved.rel_path).read_bytes() == IMAGES["elf"]
    assert not list((tmp_path / UPLOADS_DIRNAME / "proy").glob(".upload-*"))


def test_upload_too_big_leaves_nothing(tmp_path):
    with pytest.raises(f.FileValidationError, match="máximo"):
        f.save_upload(tmp_path, "proy", "big.bin", io.BytesIO(b"\x00" * 5000), max_bytes=4096, chunk_size=1024)
    assert list((tmp_path / UPLOADS_DIRNAME / "proy").iterdir()) == []


@pytest.mark.parametrize("name, content, fragment", [
    ("x.elf", b"no soy un elf" * 4, "ELF"),
    ("x.elf", b"\x7fELF\x02\x01\x01" + b"\x00" * 60, "32 bits"),
    ("x.hex", b"hola", "Intel HEX"),
    ("x.bin", b"", "vacío"),
])
def test_upload_content_checks(tmp_path, name, content, fragment):
    with pytest.raises(f.FileValidationError, match=fragment):
        f.save_upload(tmp_path, "p", name, io.BytesIO(content), max_bytes=1 << 20)


def test_upload_x86_elf_rejected(tmp_path):
    from app.sample_firmware import build_elf
    elf = build_elf([(0x1000, 0x1000, b"\x90" * 16)], machine=3)   # EM_386
    with pytest.raises(f.FileValidationError, match="microcontrolador"):
        f.save_upload(tmp_path, "p", "x.elf", io.BytesIO(elf), max_bytes=1 << 20)


def test_upload_bad_name_or_project(tmp_path):
    with pytest.raises(f.FileValidationError):
        f.save_upload(tmp_path, "p", "../../x.bin", io.BytesIO(b"1"), max_bytes=10)
    with pytest.raises(f.FileValidationError):
        f.save_upload(tmp_path, "../..", "x.bin", io.BytesIO(b"1"), max_bytes=10)


# --- borrado ------------------------------------------------------------------

def test_delete_only_uploads(root):
    saved = f.save_upload(root, "proy", "fw.bin", io.BytesIO(b"\x01\x02"), max_bytes=100)
    with pytest.raises(f.FileValidationError, match="Solo se pueden borrar"):
        f.delete_upload(root, "p1/fw.bin")
    assert (root / "p1" / "fw.bin").exists()
    f.delete_upload(root, saved.rel_path)
    assert not (root / saved.rel_path).exists()
    assert not (root / UPLOADS_DIRNAME / "proy").exists()   # carpeta vacía eliminada
    with pytest.raises(f.FileValidationError):
        f.delete_upload(root, f"{UPLOADS_DIRNAME}/../p1/fw.bin")


# --- escaneo ------------------------------------------------------------------

def test_scan_groups_and_ignores_syncthing(root):
    (root / ".stfolder").mkdir()
    (root / ".stversions").mkdir()
    (root / ".stversions" / "viejo.bin").write_bytes(b"1")
    (root / "p1" / ".syncthing.fw.bin.tmp").write_bytes(b"1")
    (root / "p1" / "~syncthing~fw.bin.tmp").write_bytes(b"1")
    (root / "p1" / "readme.txt").write_text("x")
    (root / "p2").mkdir()
    (root / "p2" / "raro{1}.bin").write_bytes(b"1")
    f.save_upload(root, "subido", "up.hex", io.BytesIO(IMAGES["hex"]), max_bytes=1 << 20)

    projects = {p["key"]: p for p in f.scan_projects(root)}
    assert set(projects) == {"p1", "p2", f"{UPLOADS_DIRNAME}/subido"}
    p1_files = {x["path"] for x in projects["p1"]["files"]}
    assert p1_files == {"p1/build/fw.elf", "p1/fw.bin"}
    assert all(not x["deletable"] for x in projects["p1"]["files"])
    up = projects[f"{UPLOADS_DIRNAME}/subido"]
    assert up["uploaded"] and up["files"][0]["deletable"] and up["files"][0]["kind"] == "hex"
    # Nombres fuera de la lista blanca se listan, pero no se pueden usar.
    assert projects["p2"]["files"][0]["usable"] is False
    # Los subidos van primero.
    assert f.scan_projects(root)[0]["uploaded"] is True


def test_scan_max_depth(root):
    deep = root / "p3" / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "fw.bin").write_bytes(b"1")
    keys = {p["key"] for p in f.scan_projects(root, max_depth=3)}
    assert "p3" not in keys
    keys = {p["key"] for p in f.scan_projects(root, max_depth=6)}
    assert "p3" in keys


def test_scan_missing_dir(tmp_path):
    assert f.scan_projects(tmp_path / "no-existe") == []
