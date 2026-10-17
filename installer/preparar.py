"""Junta lo que el instalador lleva adentro para que la PC no necesite nada más.

    python -m installer.preparar            (lo llama installer/build.ps1)

Deja en installer/build/runtime/:
  python-embed.zip     Python 3.12 oficial "embebido" (python.org): privado del banco,
                       no toca ningún Python de la PC y no pide administrador.
  wheels/              FastAPI, pyserial, esptool... y pip, para Windows 64 bits: se instalan
                       sin internet.
  herramientas/
    avrdude/           avrdude 6.3.0-arduino17 (el del IDE de Arduino) con su avrdude.conf
    dfu-util/          dfu-util 0.11 (el del IDE de Arduino)
    stm32/openocd/     OpenOCD de ST (soporta STM32H5/H7) y sus scripts  } copiados de un
    stm32/gdb/         arm-none-eabi-gdb de ST                             } STM32CubeIDE
  LICENCIAS.txt        de dónde sale cada cosa y dónde está su código fuente

Las descargas tienen el SHA-256 fijo: si el archivo cambia, no se usa.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "installer" / "build" / "runtime"
CACHE = REPO / "installer" / "build" / "descargas"

PYTHON_VERSION = "3.12.10"
PYTHON_TAG = "cp312"
DOWNLOADS = {
    "python": (f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embeddable-amd64.zip",
               "156c7eea90d58cd7e91a23f28a0056616b13e9f4cf4901b7b99b837b7848c6da"),
    "avrdude": ("https://downloads.arduino.cc/tools/avrdude-6.3.0-arduino17-i686-w64-mingw32.zip",
                "e99188873c7c5ad8f8f906f068c33600e758b2e36cce3adbd518a21bd266749d"),
    "dfu-util": ("https://downloads.arduino.cc/tools/dfu-util-0.11-arduino5-windows_386.tar.gz",
                 "6451e16bf77600fe2436c8708ab4b75077c49997cf8bedf03221d9d6726bb641"),
}

LICENSES = """\
Banco remoto: componentes de terceros que instala este instalador
===================================================================

Python {python} (embebido para Windows) - Python Software Foundation License
  https://www.python.org/downloads/release/python-{python_nodots}/

Bibliotecas de Python (carpeta wheels): cada una con su licencia (MIT, BSD, Apache 2.0, GPL en el
caso de esptool). Lista y versiones: requirements.txt y la carpeta site-packages instalada.

avrdude 6.3.0-arduino17 - GPL-2.0. Compilado por Arduino (https://downloads.arduino.cc/tools/).
  Código fuente: https://github.com/arduino/avrdude-build-script y https://github.com/avrdudes/avrdude

dfu-util 0.11 - GPL-2.0. Compilado por Arduino. Código fuente: https://dfu-util.sourceforge.net/

OpenOCD (fork de STMicroelectronics, {openocd}) - GPL-2.0, con libusb, hidapi y libjaylink (LGPL).
  Copiado de STM32CubeIDE. Código fuente: https://github.com/STMicroelectronics/OpenOCD

arm-none-eabi-gdb (GNU Tools for STM32, {gdb}) - GPL-3.0. Copiado de STM32CubeIDE.
  Código fuente: https://github.com/STMicroelectronics/gnu-tools-for-stm32
"""


def fetch(name: str) -> bytes:
    url, sha = DOWNLOADS[name]
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / url.rsplit("/", 1)[-1]
    if cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == sha:
        return cached.read_bytes()
    print(f"Bajando {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        raise SystemExit(f"El SHA-256 de {url} no coincide ({got}): no se usa.")
    cached.write_bytes(data)
    return data


def _strip_first(names, member: str) -> str:
    """Saca la carpeta raíz común de los archivos ("avrdude/bin/x" -> "bin/x")."""
    return member.split("/", 1)[1] if "/" in member else member


def prepare_python() -> None:
    (OUT / "python-embed.zip").write_bytes(fetch("python"))


def prepare_wheels() -> None:
    """Las mismas versiones que el entorno donde corrieron los tests (pip freeze), para Windows 64 bits."""
    wheels = OUT / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    wheels.mkdir(parents=True)
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check", "-q"]
    # esptool 4.x y reedsolo se publican sólo como código fuente (Python puro): se arman acá.
    subprocess.run(pip + ["wheel", "--no-deps", "-w", str(wheels), "esptool==4.12.0", "reedsolo==1.7.0"], check=True)
    freeze = subprocess.run(pip + ["freeze"], capture_output=True, text=True, check=True).stdout
    constraints = wheels.parent / "constraints.txt"
    constraints.write_text("\n".join(l for l in freeze.splitlines() if "==" in l and not l.startswith("-e")),
                           encoding="utf-8")
    print("Bajando las dependencias de Python para Windows 64 bits...")
    subprocess.run(pip + ["download", "--only-binary=:all:", "--platform", "win_amd64",
                          "--python-version", PYTHON_VERSION, "--implementation", "cp",
                          "--find-links", str(wheels), "-c", str(constraints), "-d", str(wheels),
                          "-r", str(REPO / "requirements.txt"), "esptool==4.12.0", "colorama", "pip"], check=True)
    constraints.unlink()


def prepare_avrdude() -> None:
    dest = OUT / "herramientas" / "avrdude"
    shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(fetch("avrdude"))) as z:
        for info in z.infolist():
            rel = _strip_first(None, info.filename)
            if not rel or info.is_dir():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    if not (dest / "bin" / "avrdude.exe").is_file() or not (dest / "etc" / "avrdude.conf").is_file():
        raise SystemExit("El zip de avrdude no tiene bin/avrdude.exe y etc/avrdude.conf")


def prepare_dfu_util() -> None:
    dest = OUT / "herramientas" / "dfu-util"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(fetch("dfu-util")), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile():
                (dest / Path(m.name).name).write_bytes(tar.extractfile(m).read())
    if not (dest / "dfu-util.exe").is_file():
        raise SystemExit("El paquete de dfu-util no trae dfu-util.exe")


def prepare_stm32() -> dict:
    """OpenOCD de ST y GDB, copiados del STM32CubeIDE de esta PC."""
    sys.path.insert(0, str(REPO))
    from app import tools
    found = tools.detect()
    ocd, gdb = found.get("openocd"), found.get("gdb")
    dest = OUT / "herramientas" / "stm32"
    shutil.rmtree(dest, ignore_errors=True)
    if not ocd or not ocd.source.startswith("STM32CubeIDE") or not gdb or not gdb.source.startswith("STM32CubeIDE"):
        print("AVISO: no hay STM32CubeIDE en esta PC: el instalador no va a traer OpenOCD ni GDB de ST "
              "(en la PC de destino se usan los de su STM32CubeIDE).")
        return {}
    shutil.copytree(Path(ocd.path).parent, dest / "openocd" / "bin")
    shutil.copytree(ocd.scripts_dir, dest / "openocd" / "scripts")
    (dest / "gdb").mkdir(parents=True)
    shutil.copy2(gdb.path, dest / "gdb" / "arm-none-eabi-gdb.exe")
    versions = {}
    for key, exe in (("openocd", dest / "openocd" / "bin" / "openocd.exe"),
                     ("gdb", dest / "gdb" / "arm-none-eabi-gdb.exe")):
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
        versions[key] = (out.stdout + out.stderr).splitlines()[0].strip()
        print(f"  {versions[key]}")
    return versions


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    prepare_python()
    prepare_wheels()
    prepare_avrdude()
    prepare_dfu_util()
    versions = prepare_stm32()
    text = LICENSES.format(python=PYTHON_VERSION, python_nodots=PYTHON_VERSION.replace(".", ""),
                           openocd=versions.get("openocd", "-"), gdb=versions.get("gdb", "-"))
    if not versions:                                   # sin OpenOCD/GDB de ST en el paquete
        text = text.split("\nOpenOCD (fork")[0] + "\n"
    (OUT / "LICENCIAS.txt").write_text(text, encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"Listo: {OUT} ({size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
