"""Modo PC: el banco corriendo en una computadora con Windows (sin BeagleBoard),
con los USB de la propia PC. Enumeración USB, datos del sistema y herramientas."""
import sys

import pytest

from app import winusb


# ------------------------------------------------------------ USB en Windows

def test_instance_ids():
    d = winusb.parse_instance_id(r"USB\VID_0483&PID_374B\066DFF485550755187121832")
    assert (d.vid, d.pid, d.serial) == ("0483", "374b", "066DFF485550755187121832")
    # Sin número de serie Windows inventa uno con "&": no es una serie.
    d = winusb.parse_instance_id(r"USB\VID_0483&PID_3748\5&2A3B1C0&0&2")
    assert (d.vid, d.pid, d.serial) == ("0483", "3748", None)
    # Las interfaces de un dispositivo compuesto (MI_xx) no son dispositivos aparte.
    assert winusb.parse_instance_id(r"USB\VID_0483&PID_374B&MI_02\6&1A2B3C&0&0002") is None
    assert winusb.parse_instance_id(r"HID\VID_046D&PID_C52B\7&1") is None
    assert winusb.parse_instance_id(r"USB\ROOT_HUB30\4&1") is None


@pytest.mark.skipif(sys.platform != "win32", reason="sólo en Windows")
def test_present_devices_on_this_pc():
    devices = winusb.present_devices()
    assert isinstance(devices, list)
    for d in devices:
        assert len(d.vid) == 4 and len(d.pid) == 4 and d.instance_id.upper().startswith("USB\\VID_")


def _dev(instance_id, name=None, location=None, problem=0):
    d = winusb.parse_instance_id(instance_id)
    return winusb.WinUsbDevice(d.instance_id, d.vid, d.pid, d.serial, name, location, problem)


WIN_DEVICES = [
    _dev(r"USB\VID_0483&PID_374B\066DFF485550755187121832", "ST-Link Debug", "Port_#0002.Hub_#0003"),
    _dev(r"USB\VID_0483&PID_3748\5&2A3B1C0&0&4", "STM32 STLink", "Port_#0004.Hub_#0003", problem=28),
    _dev(r"USB\VID_10C4&PID_EA60\0001", "CP2102 USB to UART Bridge Controller", "Port_#0001.Hub_#0003"),
    _dev(r"USB\VID_046D&PID_08E3\9586B420", "USB Composite Device", "Port_#0003.Hub_#0002"),
]


def test_stlinks_on_windows():
    from app import probe
    found = probe.stlinks_from_windows(WIN_DEVICES)
    assert [(p.serial, p.model, p.accessible) for p in found] == [
        ("066DFF485550755187121832", "ST-LINK/V2-1", True),
        ("", "ST-LINK/V2", False)]
    # Sin driver: el aviso dice qué instalar (no habla de udev).
    hint = found[1].to_public()["access_hint"]
    assert "driver" in hint and "udev" not in hint


def test_usb_list_on_windows():
    from app import usb
    ports = [{"device": "COM5", "vid": 0x10C4, "pid": 0xEA60, "serial": "0001"}]
    devices = usb.devices_from_windows(WIN_DEVICES, ports)
    by = {d.vid_pid: d for d in devices}
    assert by["10c4:ea60"].serial_ports == ["COM5"]
    assert by["0483:374b"].serial == "066DFF485550755187121832"
    assert by["0483:374b"].to_public()["description"] == "ST-Link V2-1"
    assert (by["0483:374b"].hub, by["0483:374b"].port) == ("3", 2)


# ------------------------------------------------------------------ sistema

@pytest.mark.skipif(sys.platform != "win32", reason="sólo en Windows")
def test_system_info_on_windows(tmp_path):
    from app import system
    info = system.collect(tmp_path, "tailscale0", mock=False)
    assert info["memory"]["total"] > 0 and info["uptime_s"] > 0
    assert info["disk_root"]["total"] > 0 and info["temperatures"] == []
    assert info["kernel"].startswith("Windows ")


def test_tailscale_ip_from_the_host_addresses():
    from app import system
    assert system.pick_tailscale(["192.168.1.20", "100.99.235.81", "10.0.0.2"]) == "100.99.235.81"
    assert system.pick_tailscale(["192.168.1.20"]) is None


def test_pc_mode_does_not_power_off_the_computer(client, monkeypatch):
    bench = client.app.state.bench
    monkeypatch.setattr(bench.settings, "host", "pc")
    assert client.get("/api/config").json()["host"] == "pc"
    r = client.post("/api/system/poweroff", json={"confirm": True})
    assert r.status_code == 400 and "PC" in r.json()["detail"]


# ------------------------------------------------------------- herramientas

def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def fake_installs(tmp_path):
    """Lo que dejan instalado STM32CubeIDE y el IDE de Arduino en Windows."""
    from app.tools import EXE
    plugins = tmp_path / "ST" / "STM32CubeIDE_1.19.0" / "STM32CubeIDE" / "plugins"
    ocd = _touch(plugins / "com.st.stm32cube.ide.mcu.externaltools.openocd.win32_2.4"
                 / "tools" / "bin" / f"openocd{EXE}")
    scripts = plugins / "com.st.stm32cube.ide.mcu.debug.openocd_2.3" / "resources" / "openocd" / "st_scripts"
    _touch(scripts / "target" / "stm32h5x.cfg")
    gdb = _touch(plugins / "com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.13.3"
                 / "tools" / "bin" / f"arm-none-eabi-gdb{EXE}")
    # Una versión vieja de CubeIDE al lado: se elige la más nueva.
    _touch(tmp_path / "ST" / "STM32CubeIDE_1.9.0" / "STM32CubeIDE" / "plugins"
           / "com.st.stm32cube.ide.mcu.externaltools.openocd.win32_2.0.0.1" / "tools" / "bin" / f"openocd{EXE}")
    tools = tmp_path / "Local" / "Arduino15" / "packages" / "arduino" / "tools"
    avr = _touch(tools / "avrdude" / "6.3.0-arduino17" / "bin" / f"avrdude{EXE}")
    conf = _touch(tools / "avrdude" / "6.3.0-arduino17" / "etc" / "avrdude.conf")
    dfu = _touch(tools / "dfu-util" / "0.11.0-arduino5" / f"dfu-util{EXE}")
    return {"openocd": ocd, "scripts": scripts, "gdb": gdb, "avrdude": avr, "conf": conf, "dfu": dfu}


def test_tools_found_in_cubeide_and_arduino(tmp_path):
    from app import tools
    want = fake_installs(tmp_path)
    roots = tools.SearchRoots(st=[tmp_path / "ST"], local_appdata=tmp_path / "Local",
                              appdata=tmp_path / "Roaming", program_files=[], path_lookup=lambda name: None)
    found = tools.detect(roots)
    assert found["openocd"].path == str(want["openocd"]) and found["openocd"].source == "STM32CubeIDE 1.19.0"
    assert found["openocd"].scripts_dir == str(want["scripts"])
    assert found["gdb"].path == str(want["gdb"])
    assert found["avrdude"].path == str(want["avrdude"]) and found["avrdude"].config == str(want["conf"])
    assert found["dfu-util"].path == str(want["dfu"]) and found["dfu-util"].source == "Arduino IDE"


def test_tools_in_the_path_win(tmp_path):
    from app import tools
    roots = tools.SearchRoots(st=[], local_appdata=tmp_path, appdata=tmp_path, program_files=[],
                              path_lookup=lambda name: f"C:/bin/{name}.exe" if name == "openocd" else None)
    found = tools.detect(roots)
    assert found["openocd"].path == "C:/bin/openocd.exe" and found["openocd"].source == "PATH"
    assert found["gdb"] is None and found["avrdude"] is None


def test_auto_in_bench_yaml_uses_the_detected_tools(tmp_path, monkeypatch):
    from app import settings as st, tools
    want = fake_installs(tmp_path)
    roots = tools.SearchRoots(st=[tmp_path / "ST"], local_appdata=tmp_path / "Local",
                              appdata=tmp_path / "Roaming", program_files=[], path_lookup=lambda name: None)
    monkeypatch.setattr(tools, "default_roots", lambda: roots)
    tools.clear_cache()
    cfg = st.parse_bench_config({
        "openocd": {"binary": "auto", "scripts_dir": "auto"}, "dfu_util": {"binary": "auto"},
        "avrdude": {"binary": "auto", "config": "auto"}, "debugger": {"gdb_binary": "auto"},
        "interfaces": {"stlink": {"cfg": "interface/stlink.cfg", "transport": "hla_swd"}}})
    assert cfg.openocd_binary == str(want["openocd"]) and cfg.openocd_scripts_dir == str(want["scripts"])
    assert cfg.debugger_gdb_binary == str(want["gdb"]) and cfg.dfu_util_binary == str(want["dfu"])
    assert cfg.avrdude_binary == str(want["avrdude"]) and cfg.avrdude_config == str(want["conf"])
    tools.clear_cache()


def test_mode_setting_does_not_clash_with_the_web_host(monkeypatch):
    from app.settings import settings_from_env
    monkeypatch.setenv("BENCH_MOCK", "1")
    monkeypatch.setenv("BENCH_HOST", "0.0.0.0")           # dirección del servidor web (bench.env de la placa)
    monkeypatch.delenv("BENCH_MODE", raising=False)
    monkeypatch.delenv("BENCH_ENV_FILE", raising=False)
    assert settings_from_env().host == "board"
    monkeypatch.setenv("BENCH_MODE", "pc")
    monkeypatch.setenv("BENCH_BENCH_FILE", "config/bench.pc.yaml")
    s = settings_from_env()
    assert s.host == "pc" and s.bench_file.name == "bench.pc.yaml"


def fake_bundled(base):
    """La carpeta herramientas que deja el instalador (%LOCALAPPDATA%\BancoRemoto\herramientas)."""
    from app.tools import EXE
    return {
        "openocd": _touch(base / "stm32" / "openocd" / "bin" / f"openocd{EXE}"),
        "scripts": _touch(base / "stm32" / "openocd" / "scripts" / "target" / "stm32h5x.cfg").parents[1],
        "gdb": _touch(base / "stm32" / "gdb" / f"arm-none-eabi-gdb{EXE}"),
        "avrdude": _touch(base / "avrdude" / "bin" / f"avrdude{EXE}"),
        "conf": _touch(base / "avrdude" / "etc" / "avrdude.conf"),
        "dfu": _touch(base / "dfu-util" / f"dfu-util{EXE}"),
    }


def test_tools_installed_with_the_bench(tmp_path):
    from app import tools
    own = fake_bundled(tmp_path / "herramientas")
    roots = tools.SearchRoots(st=[], local_appdata=tmp_path, appdata=tmp_path, program_files=[],
                              path_lookup=lambda name: None, bundled=tmp_path / "herramientas")
    found = tools.detect(roots)
    assert found["openocd"].path == str(own["openocd"]) and found["openocd"].scripts_dir == str(own["scripts"])
    assert found["openocd"].source == "instalado con el banco"
    assert found["gdb"].path == str(own["gdb"])
    assert found["avrdude"].path == str(own["avrdude"]) and found["avrdude"].config == str(own["conf"])
    assert found["dfu-util"].path == str(own["dfu"])


def test_cubeide_goes_first_for_stm32_but_not_for_arduino(tmp_path):
    from app import tools
    ide = fake_installs(tmp_path)
    own = fake_bundled(tmp_path / "herramientas")
    roots = tools.SearchRoots(st=[tmp_path / "ST"], local_appdata=tmp_path / "Local", appdata=tmp_path,
                              program_files=[], path_lookup=lambda name: None, bundled=tmp_path / "herramientas")
    found = tools.detect(roots)
    assert found["openocd"].path == str(ide["openocd"]) and found["gdb"].path == str(ide["gdb"])
    assert found["avrdude"].path == str(own["avrdude"]) and found["dfu-util"].path == str(own["dfu"])
