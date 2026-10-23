"""Carga y validación de la configuración de chips y del banco."""
import pytest

from app.chips import check_cfg_files, load_chips, parse_chips
from app.settings import REPO_ROOT, ConfigError, load_bench_config, parse_bench_config

INTERFACES = {"stlink": object(), "stlink-dap": object()}


def minimal(**overrides):
    chip = {
        "id": "chip1",
        "name": "Chip 1",
        "flash": {"base": 0x08000000, "size_kb": 128},
        "methods": {"swd": {"target_cfg": "target/stm32h5x.cfg"}},
    }
    chip.update(overrides)
    return {"chips": [chip]}


def test_repo_config_loads():
    bench = load_bench_config(REPO_ROOT / "config" / "bench.yaml")
    chips = load_chips(REPO_ROOT / "config" / "chips.yaml", bench.interfaces)
    ids = {c.id for c in chips}
    assert {"stm32h503cb", "stm32h723vg"} <= ids
    assert len(ids) == len(chips)
    for family in ("F1", "F4", "L4", "G0", "G4"):
        assert any(c.family.startswith(family) for c in chips), family


def test_h5_and_h7_entries():
    chips = {c.id: c for c in load_chips(REPO_ROOT / "config" / "chips.yaml")}
    h5, h7 = chips["stm32h503cb"], chips["stm32h723vg"]
    assert h5.swd.target_cfg == "target/stm32h5x.cfg"
    assert h7.swd.target_cfg == "target/stm32h7x.cfg"
    assert h5.flash_base == h7.flash_base == 0x08000000
    assert h5.flash_size == 128 * 1024 and h7.flash_size == 1024 * 1024
    assert h5.methods == ["swd", "dfu"] and h7.dfu.vid_pid == "0483:df11"
    assert h7.dfu.address == 0x08000000   # por defecto, la base de flash
    # El stm32h7x.cfg de ST necesita AP_NUM (núcleo Cortex-M7 en el AP 0): sin eso OpenOCD
    # termina con "can't read AP_NUM" al usar stlink-dap.
    assert ("AP_NUM", "0") in h7.swd.tcl_vars


def test_hex_strings_and_defaults():
    chips = parse_chips(minimal(flash={"base": "0x08000000", "size_kb": "64"}))
    chip = chips[0]
    assert chip.flash_base == 0x08000000 and chip.flash_size == 64 * 1024
    assert chip.default_method == "swd"
    assert chip.swd.flash_banks == (0,)
    assert chip.dfu is None


def test_swd_only_chip_has_no_dfu_method():
    chips = {c.id: c for c in load_chips(REPO_ROOT / "config" / "chips.yaml")}
    assert chips["stm32f103c8"].methods == ["swd"]
    assert chips["stm32g071rb"].methods == ["swd"]


@pytest.mark.parametrize("bad, fragment", [
    ({"id": "Mayus"}, "id"),
    ({"flash": {"base": 0x08000000}}, "size_kb"),
    ({"methods": {}}, "methods"),
    ({"methods": {"jtag": {}}}, "desconocidos"),
    ({"methods": {"swd": {"target_cfg": "../../etc/passwd.cfg"}}}, "target_cfg"),
    ({"methods": {"swd": {"target_cfg": "target/x.cfg; rm -rf /"}}}, "target_cfg"),
    ({"methods": {"swd": {"target_cfg": "target/x.cfg", "interface": "jlink"}}}, "interface"),
    ({"methods": {"dfu": {"vid_pid": "zzzz:df11"}}}, "vid_pid"),
    ({"default_method": "dfu"}, "default_method"),
    ({"methods": {"swd": {"target_cfg": "target/x.cfg", "tcl_vars": {"X": "[exec rm]"}}}}, "tcl_vars"),
    ({"methods": {"swd": {"target_cfg": "target/x.cfg", "erase_cmds": ["flash erase {x}"]}}}, "erase_cmds"),
])
def test_invalid_entries(bad, fragment):
    with pytest.raises(ConfigError) as exc:
        parse_chips(minimal(**bad), INTERFACES)
    assert fragment in str(exc.value)


def test_duplicate_ids():
    data = minimal()
    data["chips"].append(dict(data["chips"][0]))
    with pytest.raises(ConfigError, match="duplicado"):
        parse_chips(data)


def test_adding_a_chip_is_just_an_entry():
    data = minimal()
    data["chips"].append({
        "id": "stm32u575zi", "name": "STM32U575ZI", "family": "U5",
        "flash": {"base": 0x08000000, "size_kb": 2048},
        "default_method": "dfu",
        "methods": {"dfu": {"alt": 0}, "swd": {"target_cfg": "target/stm32u5x.cfg"}},
    })
    chips = parse_chips(data)
    assert chips[1].default_method == "dfu" and chips[1].methods == ["swd", "dfu"]


def test_check_cfg_files_reports_missing(tmp_path):
    (tmp_path / "target").mkdir()
    (tmp_path / "interface").mkdir()
    (tmp_path / "target" / "stm32h7x.cfg").write_text("#")
    (tmp_path / "interface" / "stlink.cfg").write_text("#")
    bench = load_bench_config(REPO_ROOT / "config" / "bench.yaml")
    chips = load_chips(REPO_ROOT / "config" / "chips.yaml", bench.interfaces)
    result = check_cfg_files(chips, bench.interfaces, tmp_path)
    assert result.checked
    missing = {(m["kind"], m["cfg"]) for m in result.missing}
    assert ("interface", "interface/stlink-dap.cfg") in missing
    assert ("target", "target/stm32h5x.cfg") in missing
    assert ("target", "target/stm32h7x.cfg") not in missing


def test_check_cfg_files_without_openocd():
    result = check_cfg_files([], {}, None)
    assert result.checked is False


def test_bench_config_validation():
    base = {"interfaces": {"stlink": {"cfg": "interface/stlink.cfg"}}}
    cfg = parse_bench_config(base)
    assert cfg.gdb_port == 3333 and cfg.gdb_bind == "tailscale"
    with pytest.raises(ConfigError):
        parse_bench_config(dict(base, gdb={"bind": "no-es-ip"}))
    with pytest.raises(ConfigError):
        parse_bench_config(dict(base, boot_control={"backend": "gpio"}))
    with pytest.raises(ConfigError):
        parse_bench_config(dict(base, boot_control={"backend": "serial_command", "serial_command": {"port": "/dev/ttyACM0"}}))
    with pytest.raises(ConfigError):
        parse_bench_config(dict(base, boot_control={"backend": "gpio", "gpio": {
            "boot0": {"chip": "../gpiochip0", "line": 1}, "nrst": {"chip": "gpiochip0", "line": 2}}}))
    with pytest.raises(ConfigError):
        parse_bench_config({"interfaces": {"x": {"cfg": "../../evil.cfg"}}})


# ------------------------------------------------- STM32: una línea por chip

FAMILIES = {"F4": {"core": "Cortex-M4", "target_cfg": "target/stm32f4x.cfg", "idcode_address": 0xE0042000,
                   "uid": 0x1FFF7A10, "interface": "stlink", "adapter_speed_khz": 2000, "dfu": True}}


def test_stm32_in_one_line_with_its_family():
    data = minimal()
    data["stm32_families"] = FAMILIES
    data["stm32"] = [
        {"id": "stm32f401re", "name": "STM32F401RE (Nucleo-F401RE)", "family": "F4", "dev_id": 0x433,
         "flash_kb": 512},
        {"id": "stm32f410rb", "name": "STM32F410RB", "family": "F4", "dev_id": 0x458, "flash_kb": 128,
         "dfu": False, "adapter_speed_khz": 1000},
    ]
    chips = {c.id: c for c in parse_chips(data, INTERFACES)}
    c = chips["stm32f401re"]
    assert c.family == "F4 (Cortex-M4)" and c.group == "STM32" and c.default_method == "swd"
    assert c.flash_base == 0x08000000 and c.flash_size == 512 * 1024
    assert c.swd.target_cfg == "target/stm32f4x.cfg" and c.swd.interface == "stlink"
    assert c.swd.adapter_speed_khz == 2000 and c.swd.uid == 0x1FFF7A10
    assert (c.swd.idcode.address, c.swd.idcode.value) == (0xE0042000, 0x433)
    assert c.methods == ["swd", "dfu"] and c.dfu.vid_pid == "0483:df11"
    # Lo de la línea pisa lo de la familia.
    f410 = chips["stm32f410rb"]
    assert f410.methods == ["swd"] and f410.swd.adapter_speed_khz == 1000


@pytest.mark.parametrize("line, fragment", [
    ({"id": "x1", "family": "Z9", "dev_id": 0x1, "flash_kb": 64}, "familia"),
    ({"id": "x1", "family": "F4", "flash_kb": 64}, "dev_id"),
    ({"id": "x1", "family": "F4", "dev_id": 0x433}, "flash_kb"),
    ({"id": "chip1", "family": "F4", "dev_id": 0x433, "flash_kb": 64}, "duplicado"),
])
def test_stm32_line_errors(line, fragment):
    data = minimal()
    data["stm32_families"] = FAMILIES
    data["stm32"] = [line]
    with pytest.raises(ConfigError) as exc:
        parse_chips(data, INTERFACES)
    assert fragment in str(exc.value)


def test_repo_catalog_covers_most_stm32_families():
    chips = load_chips(REPO_ROOT / "config" / "chips.yaml")
    stm32 = [c for c in chips if c.swd is not None]
    families = {c.family.split(" ")[0] for c in stm32}
    assert {"C0", "F0", "F1", "F2", "F3", "F4", "F7", "G0", "G4", "H5", "H7", "L0", "L1", "L4", "L4+", "L5",
            "U0", "U5", "WB", "WL"} <= families
    assert len(stm32) >= 50
    assert all(c.swd.idcode is not None for c in stm32)       # nunca se graba un chip sin verificar su ID
    by_id = {c.id: c for c in stm32}
    # Las entradas probadas en el banco conservan sus ajustes.
    assert by_id["stm32h503cb"].swd.adapter_speed_khz == 1000
    assert ("AP_NUM", "0") in by_id["stm32h723vg"].swd.tcl_vars


def test_catalog_cfg_files_exist_in_the_st_openocd():
    """Todos los target_cfg del catálogo existen en los scripts del OpenOCD de ST (si está CubeIDE)."""
    from pathlib import Path
    from app import tools
    ocd = tools.detect().get("openocd")
    if not ocd or not ocd.scripts_dir:
        pytest.skip("no hay OpenOCD de ST en esta PC")
    bench = load_bench_config(REPO_ROOT / "config" / "bench.yaml")
    chips = load_chips(REPO_ROOT / "config" / "chips.yaml", bench.interfaces)
    check = check_cfg_files(chips, bench.interfaces, Path(ocd.scripts_dir))
    assert check.missing == []
