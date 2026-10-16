"""Instalador: qué placa y qué sistema hay del otro lado del SSH, qué pasos hacen
falta, y el comando que instala en la placa (sin contraseñas a la vista)."""
from installer import perfil, remoto

XM_1604 = """\
model=TI OMAP3 BeagleBoard xM
os_id=ubuntu
os_version=16.04
os_pretty=Ubuntu 16.04.3 LTS
arch=armv7l
mem_kb=503572
disk_free_kb=5120000
python=3.5.2
glibc=2.23
tailscale_ip=
openocd=
openocd_h5=0
gdb=
bench=none
"""

BBB_DEBIAN11 = """\
model=TI AM335x BeagleBone Black
os_id=debian
os_version=11
os_pretty=Debian GNU/Linux 11 (bullseye)
arch=armv7l
mem_kb=496000
disk_free_kb=1200000
python=3.9.2
glibc=2.31
tailscale_ip=100.70.1.2
openocd=Open On-Chip Debugger 0.12.0+dev
openocd_h5=1
gdb=
bench=active
"""

PI4_DEBIAN12 = """\
model=Raspberry Pi 4 Model B Rev 1.4
os_id=debian
os_version=12
os_pretty=Debian GNU/Linux 12 (bookworm)
arch=aarch64
mem_kb=3900000
disk_free_kb=20000000
python=3.11.2
glibc=2.36
tailscale_ip=
openocd=
openocd_h5=0
gdb=
bench=none
"""


def test_parse_probe_output():
    info = perfil.parse(XM_1604 + "basura sin igual\n")
    assert info["model"] == "TI OMAP3 BeagleBoard xM" and info["mem_kb"] == "503572"
    assert info["openocd"] == ""


def test_beagleboard_xm_with_old_ubuntu():
    a = perfil.analyze(perfil.parse(XM_1604))
    assert a.board.id == "beagleboard-xm" and a.os.key == "ubuntu-16.04"
    assert a.supported and a.prebuilt == "armhf"
    names = [s.name for s in a.steps]
    assert names[:3] == ["Sistema base", "Tailscale", "Python 3.9"]
    assert "OpenOCD de ST" in names and "Aplicación" in names
    # Con binarios precompilados OpenOCD tarda minutos; compilado en la xM, más de media hora.
    assert a.step("OpenOCD de ST").minutes <= 5
    slow = perfil.analyze(perfil.parse(XM_1604), prebuilt=False)
    assert slow.step("OpenOCD de ST").minutes >= 45
    assert any("1,8 V" in w for w in a.warnings)                  # GPIO de la xM
    assert a.mode == "completo"


def test_beaglebone_already_installed_only_updates():
    a = perfil.analyze(perfil.parse(BBB_DEBIAN11))
    assert a.board.id == "beaglebone-black" and a.os.key == "debian-11"
    assert a.mode == "app"                                        # ya tiene OpenOCD con H5 y el banco
    assert [s.name for s in a.steps] == ["Aplicación"]
    assert any("espacio" in w for w in a.warnings)                # 1,2 GB libres


def test_raspberry_pi_arm64():
    a = perfil.analyze(perfil.parse(PI4_DEBIAN12))
    assert a.board.id == "raspberry-pi" and a.prebuilt == "arm64"
    assert "Python 3.9" not in [s.name for s in a.steps]          # ya trae 3.11


def test_user_can_correct_board_and_os():
    a = perfil.analyze(perfil.parse(PI4_DEBIAN12), board_id="beaglebone-black", os_key="debian-11")
    assert a.board.id == "beaglebone-black" and a.os.key == "debian-11"


def test_unsupported_systems_are_explained():
    info = perfil.parse(XM_1604.replace("os_version=16.04", "os_version=14.04"))
    a = perfil.analyze(info)
    assert not a.supported and "14.04" in a.blockers[0]
    a = perfil.analyze(perfil.parse(PI4_DEBIAN12.replace("arch=aarch64", "arch=mips")))
    assert not a.supported


def test_install_command_keeps_secrets_out_of_the_command_line():
    cmd = remoto.install_command(home="/home/ubuntu", mode="completo", prebuilt="armhf", with_gdb=True,
                                 update_config=False, credentials_file="/home/ubuntu/.banco-cred")
    assert cmd.startswith("sudo -S -p '' ")
    assert "instalar_todo.sh completo" in cmd and "--binarios armhf" in cmd and "--credenciales" in cmd
    assert "nohup" in cmd and "banco-instalar.log" in cmd
    # El archivo de credenciales: sólo usuario y contraseña de la web, para borrarse al leerlo.
    content = remoto.credentials_content("admin", "una clave 'rara' $x")
    assert "ADMIN_USER='admin'" in content and "ADMIN_PASSWORD='una clave '\\''rara'\\'' $x'" in content


def test_progress_from_the_install_log():
    log = ("\x1b[1;36m==================== 1/6 Sistema base ====================\x1b[0m\n"
           "algo\n"
           "To authenticate, visit:\n\n\thttps://login.tailscale.com/a/1a2b3c4d\n"
           "\x1b[1;36m==================== 4/6 OpenOCD de ST (45-90 min la primera vez) ====================\x1b[0m\n")
    p = remoto.parse_log(log)
    assert p.step == "4/6 OpenOCD de ST (45-90 min la primera vez)"
    assert p.tailscale_url == "https://login.tailscale.com/a/1a2b3c4d"
    assert p.finished is None
    assert remoto.parse_log(log + "[banco] FIN 0\n").finished == 0
