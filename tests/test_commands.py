"""Construcción de comandos de OpenOCD y dfu-util (siempre listas, nunca shell)."""
from pathlib import Path

import pytest

from app.chips import DfuParams, SwdParams
from app.files import FileValidationError
from app.programmers import dfu as d
from app.programmers import openocd as o
from app.settings import InterfaceConfig

DAP = InterfaceConfig("stlink-dap", "interface/stlink-dap.cfg", "dapdirect_swd")
HLA = InterfaceConfig("stlink", "interface/stlink.cfg", "hla_swd")
H7 = SwdParams(target_cfg="target/stm32h7x.cfg", interface="stlink-dap", adapter_speed_khz=4000)
ELF = Path("/srv/proyectos/demo blink/fw.elf")
BIN = Path("/srv/proyectos/p/fw.bin")


def setup(serial=None, iface=DAP, serial_command="adapter serial"):
    return o.OpenOcdSetup(command=("openocd",), scripts_dir="/usr/local/share/openocd/scripts",
                          interface=iface, serial=serial, serial_command=serial_command)


def pairs(args):
    """Lista de valores de -c en orden."""
    return [args[i + 1] for i, a in enumerate(args) if a == "-c"]


def test_base_order_and_types():
    args = o.base_args(setup(serial="066DFF485550755187121832"), H7)
    assert all(isinstance(a, str) for a in args)
    assert args[:3] == ["openocd", "-s", "/usr/local/share/openocd/scripts"]
    iface = args.index("interface/stlink-dap.cfg")
    transport = args.index("transport select dapdirect_swd")
    serial = args.index("adapter serial 066DFF485550755187121832")
    target = args.index("target/stm32h7x.cfg")
    speed = args.index("adapter speed 4000")
    # interfaz -> transporte -> serie -> target -> velocidad
    assert iface < transport < serial < target < speed


def test_hla_serial_for_old_openocd():
    args = o.base_args(setup(serial="ABC123", iface=HLA, serial_command="hla_serial"), H7)
    assert "hla_serial ABC123" in args and "transport select hla_swd" in args


@pytest.mark.parametrize("serial", ["abc def", "x;shutdown", "[exec id]", "\\xaa", "a" * 65, "é"])
def test_bad_probe_serial(serial):
    with pytest.raises(o.CommandError):
        o.base_args(setup(serial=serial), H7)


def test_tcl_vars_before_target():
    swd = SwdParams(target_cfg="target/stm32h7x.cfg", interface=None, adapter_speed_khz=1800,
                    tcl_vars=(("DUAL_BANK", "1"),))
    args = o.base_args(setup(), swd)
    assert args.index("set DUAL_BANK 1") < args.index("target/stm32h7x.cfg")


def test_flash_elf():
    args = o.flash_args(setup(), H7, ELF, None)
    assert pairs(args)[-1] == "program {/srv/proyectos/demo blink/fw.elf} verify reset exit"


def test_flash_bin_requires_address():
    with pytest.raises(o.CommandError):
        o.flash_args(setup(), H7, BIN, None)
    args = o.flash_args(setup(), H7, BIN, 0x08020000)
    assert pairs(args)[-1] == "program {/srv/proyectos/p/fw.bin} 0x08020000 verify reset exit"


def test_flash_path_injection_blocked():
    for bad in ("/srv/p/x}; exec rm -rf /; {.elf", "/srv/p/[exec id].elf", "/srv/p/$env.elf"):
        with pytest.raises(FileValidationError):
            o.flash_args(setup(), H7, Path(bad), None)


def test_verify():
    args = o.verify_args(setup(), H7, BIN, 0x08000000)
    assert pairs(args)[-5:] == ["init", "halt", "verify_image {/srv/proyectos/p/fw.bin} 0x08000000 bin",
                                "resume", "shutdown"]
    args = o.verify_args(setup(), H7, ELF, None)
    assert "verify_image {/srv/proyectos/demo blink/fw.elf}" in args


def test_erase_default_and_override():
    swd = SwdParams(target_cfg="target/stm32h7x.cfg", interface=None, adapter_speed_khz=4000, flash_banks=(0, 1))
    c = pairs(o.erase_args(setup(), swd))
    assert c[-5:] == ["init", "reset halt", "flash erase_sector 0 0 last", "flash erase_sector 1 0 last", "shutdown"]
    swd = SwdParams(target_cfg="target/stm32h7x.cfg", interface=None, adapter_speed_khz=4000,
                    erase_cmds=("stm32h7x mass_erase 0",))
    c = pairs(o.erase_args(setup(), swd))
    assert "stm32h7x mass_erase 0" in c and not any(x.startswith("flash erase_sector") for x in c)


def test_reset_halt_resume():
    assert pairs(o.reset_args(setup(), H7))[-3:] == ["init", "reset run", "shutdown"]
    assert pairs(o.halt_args(setup(), H7))[-3:] == ["init", "halt", "shutdown"]
    assert pairs(o.resume_args(setup(), H7))[-3:] == ["init", "resume", "shutdown"]


def test_gdb_server():
    args = o.gdb_server_args(setup(), H7, 3333, "100.64.1.2")
    c = pairs(args)
    assert c[:4] == ["bindto 100.64.1.2", "gdb_port 3333", "telnet_port disabled", "tcl_port disabled"]
    assert c[-1] == "init" and "shutdown" not in c
    # la configuración de puertos va antes del target
    assert args.index("bindto 100.64.1.2") < args.index("target/stm32h7x.cfg")
    with pytest.raises(ValueError):
        o.gdb_server_args(setup(), H7, 3333, "0.0.0.0; rm")
    with pytest.raises(o.CommandError):
        o.gdb_server_args(setup(), H7, 80, "127.0.0.1")


# --- dfu-util -------------------------------------------------------------------

TARGET = d.DfuTarget(command=("dfu-util",), vid_pid="0483:df11", alt=0, serial="3957377B3432")


def test_dfu_download():
    args = d.download_args(TARGET, 0x08000000, Path("/tmp/fw.bin"), leave=True)
    assert args == ["dfu-util", "-d", "0483:df11", "-a", "0", "-S", "3957377B3432",
                    "-s", "0x08000000:leave", "-D", str(Path("/tmp/fw.bin"))]
    args = d.download_args(TARGET, 0x08020000, Path("/tmp/fw.bin"), leave=False)
    assert args[args.index("-s") + 1] == "0x08020000"


def test_dfu_upload_erase_leave():
    up = d.upload_args(TARGET, 0x08000000, 2112, Path("/tmp/rb.bin"))
    assert up[up.index("-s") + 1] == "0x08000000:2112" and up[up.index("-U") + 1] == str(Path("/tmp/rb.bin"))
    er = d.mass_erase_args(TARGET, 0x08000000)
    assert er[er.index("-s") + 1] == "0x08000000:mass-erase:force" and "-D" not in er
    lv = d.leave_args(TARGET, 0x08000000)
    assert lv[lv.index("-s") + 1] == "0x08000000:leave"


def test_dfu_without_serial_and_validation():
    t = d.DfuTarget(command=("dfu-util",), vid_pid="0483:df11", alt=1)
    assert "-S" not in d.mass_erase_args(t, 0x08000000)
    with pytest.raises(d.DfuCommandError):
        d.mass_erase_args(d.DfuTarget(command=("dfu-util",), vid_pid="0483:df11", alt=0, serial="x y"), 0)
    with pytest.raises(d.DfuCommandError):
        d.mass_erase_args(d.DfuTarget(command=("dfu-util",), vid_pid="0483;df11", alt=0), 0)
    with pytest.raises(d.DfuCommandError):
        d.upload_args(TARGET, 0x08000000, 0, Path("x"))


LIST_OUTPUT = """dfu-util 0.11

Copyright 2005-2009 Weston Schmidt, Harald Welte and OpenMoko Inc.
Found Runtime: [0483:374e] ver=0100, devnum=4, cfg=1, intf=3, path="1-1.1", alt=0, name="ST-LINK", serial="002F00283232511639353236"
Found DFU: [0483:df11] ver=0200, devnum=9, cfg=1, intf=0, path="1-1.3", alt=1, name="@Option Bytes   /0x5200201C/01*128 e", serial="3957377B3432"
Found DFU: [0483:df11] ver=0200, devnum=9, cfg=1, intf=0, path="1-1.3", alt=0, name="@Internal Flash   /0x08000000/8*128Kg", serial="3957377B3432"
Found DFU: [0483:DF11] ver=0200, devnum=11, cfg=1, intf=0, path="1-1.4", alt=0, name="@Internal Flash   /0x08000000/64*2Kg", serial="205F31724D32"
"""


def test_parse_dfu_list():
    devices = d.parse_list_output(LIST_OUTPUT)
    assert [(x.vid_pid, x.serial, x.path) for x in devices] == [
        ("0483:df11", "3957377B3432", "1-1.3"), ("0483:df11", "205F31724D32", "1-1.4")]
    assert sorted(a["alt"] for a in devices[0].alts) == [0, 1]
    assert devices[0].devnum == 9


def test_parse_dfu_list_empty():
    assert d.parse_list_output("dfu-util 0.11\n\nNo DFU capable USB device available\n") == []


def test_leave_error_detection():
    assert d.leave_error_is_benign("Download done.\nFile downloaded successfully\ndfu-util: Error during download get_status")
    assert not d.leave_error_is_benign("dfu-util: Error during download")
