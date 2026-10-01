"""RTT ("printf" por el ST-Link), lista de USB y alimentación por puerto (uhubctl)."""
import dataclasses
import socket
import sys
import time
from pathlib import Path

import pytest

from app import usb
from app.chips import SwdParams
from app.elfinfo import find_symbol
from app.programmers import openocd as ocd
from app.settings import REPO_ROOT, InterfaceConfig

SERIAL = "066DFF485550755187121832"
BUILD = REPO_ROOT / "firmware" / "h503_leds" / "build"
RTT_ELF = "h503-contador/build/05_contador_rtt.elf"


# ---------------------------------------------------------------------- RTT

def test_rtt_symbol_in_elf():
    addr, size = find_symbol(BUILD / "05_contador_rtt.elf", "_SEGGER_RTT")
    assert 0x20000000 <= addr < 0x20004000 and size == 0x48
    assert find_symbol(BUILD / "03_contador_binario.elf", "_SEGGER_RTT") is None


def test_gdb_server_args_with_rtt():
    setup = ocd.OpenOcdSetup(command=("openocd",), scripts_dir=None,
                             interface=InterfaceConfig("stlink-dap", "interface/stlink-dap.cfg", "dapdirect_swd"),
                             serial=SERIAL, serial_command="adapter serial")
    swd = SwdParams(target_cfg="target/stm32h5x.cfg", interface="stlink-dap", adapter_speed_khz=1000)
    args = ocd.gdb_server_args(setup, swd, 3333, "100.99.235.81", rtt=ocd.RttConfig(0x20000430, 0x48, 9090))
    cmds = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-c"]
    assert "rtt setup 0x20000430 72 {SEGGER RTT}" in cmds
    assert cmds.index("init") < cmds.index("rtt start") < cmds.index("rtt server start 9090 0")
    assert "tcl_port disabled" in cmds and "telnet_port disabled" in cmds
    with pytest.raises(ocd.CommandError):
        ocd.gdb_server_args(setup, swd, 3333, "100.99.235.81", rtt=ocd.RttConfig(0x20000430, 0x48, 3333))
    assert not any("rtt" in a for a in ocd.gdb_server_args(setup, swd, 3333, "100.99.235.81"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serial_text(client):
    return "".join(e.get("data", "") for e in client.app.state.bench.bus.replay() if e["type"] == "serial")


def test_rtt_open_in_serial_monitor(client):
    bench = client.app.state.bench
    bench.cfg = dataclasses.replace(bench.cfg, rtt_port=free_port())
    r = client.post("/api/rtt/open", json={"chip": "stm32h503cb", "file": RTT_ELF, "probe_serial": SERIAL})
    assert r.status_code == 200, r.text
    assert r.json()["serial"]["open"] and r.json()["serial"]["port"] == "RTT"
    assert r.json()["gdb"]["rtt_state"] == "encontrado"
    end = time.time() + 5
    while "contador = 2" not in serial_text(client) and time.time() < end:
        time.sleep(0.1)
    assert "contador = 2" in serial_text(client)
    # Lo que se escribe en el monitor llega al programa (comando "p" = pausa).
    assert client.post("/api/serial/send", json={"text": "p", "eol": ""}).status_code == 200
    end = time.time() + 3
    while "> Pausado" not in serial_text(client) and time.time() < end:
        time.sleep(0.1)
    assert "> Pausado" in serial_text(client)
    client.post("/api/serial/close")
    client.post("/api/gdb/stop")


def test_rtt_rejects_program_without_rtt(client):
    r = client.post("/api/rtt/open", json={"chip": "stm32h503cb", "probe_serial": SERIAL,
                                           "file": "h503-contador/build/03_contador_binario.elf"})
    assert r.status_code == 400 and "RTT" in r.json()["detail"]
    r = client.post("/api/rtt/open", json={"chip": "esp32", "file": RTT_ELF})
    assert r.status_code == 400


def test_gdb_start_with_rtt_elf(client):
    bench = client.app.state.bench
    bench.cfg = dataclasses.replace(bench.cfg, rtt_port=free_port())
    st = client.post("/api/gdb/start", json={"chip": "stm32h503cb", "probe_serial": SERIAL, "file": RTT_ELF}).json()
    assert st["rtt"]["address"] == "0x20000430" and st["rtt"]["port"] == bench.cfg.rtt_port
    client.post("/api/gdb/stop")


# ---------------------------------------------------------------------- USB

def make_sysfs(root: Path):
    def dev(name, vid, pid, product=None, cls="00", net=None, tty=None, acm=None):
        d = root / name
        d.mkdir(parents=True)
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")
        (d / "bDeviceClass").write_text(cls + "\n")
        (d / "speed").write_text("12\n")
        if product:
            (d / "product").write_text(product + "\n")
        intf = d / f"{name}:1.0"
        intf.mkdir()
        if net:
            (intf / "net" / net).mkdir(parents=True)
        if tty:
            (intf / tty).mkdir()
        if acm:
            (intf / "tty" / acm).mkdir(parents=True)
    dev("1-2", "0424", "9514", cls="09")
    dev("1-2.1", "0424", "ec00", net="eth0")
    dev("1-2.2", "0483", "df11", "STM32  BOOTLOADER")
    dev("1-2.3", "10c4", "ea60", "CP2102", tty="ttyUSB0")
    dev("1-2.4", "2341", "0043", "Arduino Uno", acm="ttyACM0")
    (root / "usb1").mkdir()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows no permite ':' en nombres de carpeta (sysfs sí)")
def test_list_devices_from_sysfs(tmp_path):
    make_sysfs(tmp_path)
    devs = {d.path: d for d in usb.list_devices(tmp_path)}
    assert set(devs) == {"1-2", "1-2.1", "1-2.2", "1-2.3", "1-2.4"}
    assert devs["1-2"].is_hub and devs["1-2.1"].network == ["eth0"]
    assert devs["1-2.2"].to_public()["description"].startswith("STM32 en modo DFU")
    assert devs["1-2.3"].serial_ports == ["/dev/ttyUSB0"] and devs["1-2.4"].serial_ports == ["/dev/ttyACM0"]
    assert devs["1-2.3"].hub == "1-2" and devs["1-2.3"].port == 3


def test_parse_uhubctl_and_protections(tmp_path):
    hubs = usb.parse_uhubctl(usb.MOCK_UHUBCTL + "Current status for hub 2 [1d6b:0002 Linux xhci, USB 2.00, 2 ports, ganged]\n"
                                                "  Port 1: 0100 power\n  Port 2: 0000 off\n")
    assert [h.location for h in hubs] == ["1-2", "2"] and hubs[0].switching == "ppps"
    assert hubs[0].ports[1].device.startswith("0483:3748") and hubs[1].ports[1].powered is False
    devices = usb.mock_devices()
    assert "red" in usb.PortPower.blocked_reason(hubs[0], 1, devices)       # el Ethernet nunca se corta
    assert usb.PortPower.blocked_reason(hubs[0], 3, devices) is None


def test_usb_api(client):
    data = client.get("/api/usb").json()
    assert any(d["vid_pid"] == "0483:3748" for d in data["devices"])
    hub = data["power"]["hubs"][0]
    eth = next(p for p in hub["ports"] if p["port"] == 1)
    assert eth["blocked"] and eth["actions"] == []
    r = client.post("/api/usb/power", json={"location": "1-2", "port": 3, "action": "off"})
    assert r.status_code == 200, r.text
    port3 = next(p for p in r.json()["power"]["hubs"][0]["ports"] if p["port"] == 3)
    assert port3["powered"] is False
    assert client.post("/api/usb/power", json={"location": "1-2", "port": 3, "action": "on"}).status_code == 200
    r = client.post("/api/usb/power", json={"location": "1-2", "port": 1, "action": "cycle"})
    assert r.status_code == 400 and "red" in r.json()["detail"]
    assert client.post("/api/usb/power", json={"location": "1-2", "port": 3, "action": "rm"}).status_code == 400
    assert client.post("/api/usb/power", json={"location": "1-2;reboot", "port": 3, "action": "on"}).status_code == 400
    assert client.post("/api/usb/power", json={"location": "1-2", "port": 9, "action": "on"}).status_code == 400


def test_system_reports_mac(client):
    mac = client.get("/api/system").json()["mac"]
    assert mac["interface"] == "eth0" and mac["fixed"] is False
