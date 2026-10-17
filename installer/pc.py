"""Instalar el Banco remoto en esta PC (modo PC, Windows), sin que haga falta nada más.

Todo queda en %LOCALAPPDATA%\\BancoRemoto (no pide administrador):
    app\\            la aplicación (iniciar_pc.bat la arranca)
    python\\         Python 3.12 embebido, privado del banco, con sus bibliotecas
    herramientas\\   avrdude, dfu-util, y OpenOCD/GDB de ST (si no hay STM32CubeIDE)
    proyectos\\, state\\, banco.env   (los crea la app; banco.env tiene sólo el hash de la contraseña)

Lo que necesita viene adentro del instalador (installer/preparar.py), así que no
baja nada de internet. La excepción es Tailscale, si se elige: se baja el
instalador oficial, se verifica su firma digital y se instala (pide permiso de
administrador una vez).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TAILSCALE_MSI = "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi"
TAILSCALE_SIGNER = "CN=Tailscale Inc.,"


class PcInstallError(RuntimeError):
    pass


def default_base() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "BancoRemoto"


def tailscale_exe() -> Optional[Path]:
    exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    if exe.is_file():
        return exe
    found = shutil.which("tailscale")
    return Path(found) if found else None


def tailscale_ip() -> Optional[str]:
    exe = tailscale_exe()
    if not exe:
        return None
    try:
        out = subprocess.run([str(exe), "ip", "-4"], capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    ips = [line.strip() for line in out.splitlines() if line.strip().startswith("100.")]
    return ips[0] if ips else None


def _run(cmd: List[str], log: Callable[[str], None], cwd: Optional[Path] = None,
         stdin_data: Optional[str] = None, env: Optional[dict] = None) -> None:
    proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdin=subprocess.PIPE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if stdin_data is not None:
        proc.stdin.write(stdin_data)
    proc.stdin.close()
    for line in proc.stdout:
        if line.strip():
            log(line.rstrip())
    if proc.wait() != 0:
        raise PcInstallError(f"Falló: {Path(cmd[0]).name} {' '.join(cmd[1:3])}…")


def _extract_app(package: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)                     # los datos (proyectos, state) están afuera de app\
    dest.mkdir(parents=True)
    with tarfile.open(package, "r:gz") as tar:
        root = dest.resolve()
        for member in tar.getmembers():
            if not str((dest / member.name).resolve()).startswith(str(root)):
                raise PcInstallError(f"Ruta inválida en el paquete: {member.name}")
        tar.extractall(dest)


def _install_python(runtime: Path, python_dir: Path, log: Callable[[str], None]) -> Path:
    """Python embebido: se descomprime y se le habilita site-packages (para pip y las bibliotecas)."""
    exe = python_dir / "python.exe"
    if python_dir.exists():
        shutil.rmtree(python_dir)
    with zipfile.ZipFile(runtime / "python-embed.zip") as z:
        z.extractall(python_dir)
    pth = next(python_dir.glob("python3*._pth"))
    stdlib = next(python_dir.glob("python3*.zip")).name
    pth.write_text(f"{stdlib}\n.\nLib\\site-packages\nimport site\n", encoding="utf-8")
    (python_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    out = subprocess.run([str(exe), "-c", "import sys; print(sys.version.split()[0])"], capture_output=True,
                         text=True, creationflags=NO_WINDOW)
    if out.returncode != 0:
        raise PcInstallError("El Python del banco no arranca: " + out.stderr.strip()[:200])
    log(f"Python {out.stdout.strip()} listo.")
    return exe


def install_tailscale(log: Callable[[str], None]) -> None:
    """Baja el instalador oficial, verifica que lo firmó Tailscale Inc. y lo instala (pide administrador)."""
    tmp = Path(tempfile.mkdtemp()) / "tailscale-setup.msi"
    log("Bajando Tailscale (instalador oficial)...")
    with urllib.request.urlopen(TAILSCALE_MSI, timeout=300) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    check = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "$s = Get-AuthenticodeSignature -LiteralPath $env:BANCO_MSI; "
                            "\"$($s.Status)|$($s.SignerCertificate.Subject)\""],
                           env=dict(os.environ, BANCO_MSI=str(tmp)), capture_output=True, text=True,
                           timeout=60, creationflags=NO_WINDOW).stdout.strip()
    status, _, subject = check.partition("|")
    if status != "Valid" or not subject.startswith(TAILSCALE_SIGNER):
        tmp.unlink(missing_ok=True)
        raise PcInstallError(f"El instalador de Tailscale no tiene una firma válida de Tailscale Inc. ({check[:80]})")
    log("Firma de Tailscale Inc. verificada. Instalando (Windows va a pedir permiso)...")
    code = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                           "(Start-Process msiexec.exe -ArgumentList '/i', $env:BANCO_MSI, '/qb', '/norestart' "
                           "-Verb RunAs -Wait -PassThru).ExitCode"],
                          env=dict(os.environ, BANCO_MSI=str(tmp)), capture_output=True, text=True,
                          timeout=900, creationflags=NO_WINDOW)
    tmp.unlink(missing_ok=True)
    if code.returncode != 0 or code.stdout.strip() not in ("0", "3010"):
        raise PcInstallError("No se pudo instalar Tailscale (¿se canceló el permiso de administrador?).")
    log("Tailscale instalado.")


def login_tailscale(log: Callable[[str], None]) -> None:
    """`tailscale up`: abre en el navegador el inicio de sesión (si hace falta) sin esperar."""
    exe = tailscale_exe()
    if not exe or tailscale_ip():
        return
    proc = subprocess.Popen([str(exe), "up"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)

    def watch() -> None:
        for line in proc.stdout:
            m = re.search(r"https://login\.tailscale\.com/\S+", line)
            if m:
                webbrowser.open(m.group(0))
    threading.Thread(target=watch, daemon=True).start()
    log("Iniciá sesión en Tailscale en el navegador para que esta PC entre a tu red.")


def create_shortcut(target: Path, args: str, icon: Optional[Path], name: str = "Banco remoto") -> Path:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    lnk = desktop / f"{name}.lnk"
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BANCO_LNK); "
          "$s.TargetPath = $env:BANCO_TARGET; $s.Arguments = $env:BANCO_ARGS; $s.WorkingDirectory = $env:BANCO_DIR; "
          "$s.Description = 'Programación y depuración de microcontroladores'; "
          "if ($env:BANCO_ICON) { $s.IconLocation = $env:BANCO_ICON }; $s.Save()")
    env = dict(os.environ, BANCO_LNK=str(lnk), BANCO_TARGET=str(target), BANCO_ARGS=args,
               BANCO_DIR=str(target.parent), BANCO_ICON=str(icon) if icon else "")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   capture_output=True, timeout=60, creationflags=NO_WINDOW)
    return lnk


def install(package: Path, runtime: Path, base: Path, web_user: str, web_password: str, shortcut: bool,
            icon_bytes: Optional[bytes], tailscale: bool = False, remote_access: bool = False,
            log: Callable[[str], None] = print) -> Path:
    """Instala o actualiza. Devuelve la ruta de iniciar_pc.bat."""
    app_dir, python_dir, tools_dir = base / "app", base / "python", base / "herramientas"
    base.mkdir(parents=True, exist_ok=True)
    log(f"Copiando la aplicación a {app_dir}...")
    _extract_app(package, app_dir)

    log("Instalando Python 3.12 (privado del banco: no toca otros Python de la PC)...")
    python = _install_python(runtime, python_dir, log)

    log("Instalando las bibliotecas (FastAPI, pyserial, esptool...)...")
    wheels = runtime / "wheels"
    pip_wheel = next(wheels.glob("pip-*.whl"))
    # pip corre desde su .whl (el Python embebido no lo trae). Con "-c" y no con ".whl/pip": en
    # Windows pip no se deja instalar a sí mismo si el programa se llama "pip".
    bootstrap = ("import sys; sys.path.insert(0, sys.argv[1]); from pip._internal.cli.main import main; "
                 "sys.exit(main(sys.argv[2:]))")
    _run([str(python), "-c", bootstrap, str(pip_wheel), "install", "--no-index", "--find-links", str(wheels),
          "--disable-pip-version-check", "--no-warn-script-location", "-q",
          "-r", str(app_dir / "requirements.txt"), "esptool==4.12.0", "colorama"], log)

    log("Instalando avrdude, dfu-util, OpenOCD y GDB...")
    if tools_dir.exists():
        shutil.rmtree(tools_dir)
    shutil.copytree(runtime / "herramientas", tools_dir)
    if (runtime / "LICENCIAS.txt").is_file():
        shutil.copy2(runtime / "LICENCIAS.txt", base / "LICENCIAS.txt")

    env = dict(os.environ, BANCO_DATA_DIR=str(base), PYTHONIOENCODING="utf-8")
    if web_user and web_password:
        log("Guardando el usuario de la web (sólo el hash de la contraseña)...")
        _run([str(python), str(app_dir / "scripts" / "pc_run.py"), "--credenciales-stdin"], log,
             stdin_data=f"{web_user}\n{web_password}\n", env=env)

    if tailscale and not tailscale_exe():
        install_tailscale(log)
    if tailscale:
        login_tailscale(log)

    log("Herramientas que va a usar el banco:")
    check = subprocess.run([str(python), str(app_dir / "scripts" / "pc_run.py"), "--check"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env, creationflags=NO_WINDOW)
    for line in check.stdout.splitlines()[1:]:
        if line.strip():
            log(line.rstrip())

    bat = app_dir / "iniciar_pc.bat"
    if shortcut:
        icon = None
        if icon_bytes:
            icon = base / "banco-remoto.ico"
            icon.write_bytes(icon_bytes)
        lnk = create_shortcut(bat, "--tailscale" if remote_access else "", icon)
        log(f"Acceso directo: {lnk}")
    return bat


def launch(bat: Path, args: str = "") -> None:
    subprocess.Popen(["cmd", "/c", "start", "", str(bat)] + ([args] if args else []), cwd=str(bat.parent),
                     creationflags=NO_WINDOW)
