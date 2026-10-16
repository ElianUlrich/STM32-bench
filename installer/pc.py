"""Instalar el Banco remoto en esta PC (modo PC, Windows).

Queda en %LOCALAPPDATA%\\BancoRemoto\\app con su propio entorno de Python, un
acceso directo "Banco remoto" en el escritorio que abre iniciar_pc.bat, y el
usuario y la contraseña de la web (sólo el hash) en %LOCALAPPDATA%\\BancoRemoto.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Callable, List, Optional

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class PcInstallError(RuntimeError):
    pass


def default_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "BancoRemoto" / "app"


def find_python() -> Optional[List[str]]:
    """Un Python 3.9 o más nuevo de esta PC (no el del instalador: ése viene empaquetado)."""
    for cmd in (["py", "-3"], ["python"], ["python3"]):
        try:
            out = subprocess.run(cmd + ["-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                                 capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        parts = out.stdout.split()
        if out.returncode == 0 and len(parts) == 2 and (int(parts[0]), int(parts[1])) >= (3, 9):
            return cmd
    return None


def _run(cmd: List[str], log: Callable[[str], None], cwd: Optional[Path] = None,
         stdin_data: Optional[str] = None) -> None:
    proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if stdin_data is not None:
        proc.stdin.write(stdin_data)
    proc.stdin.close()
    for line in proc.stdout:
        if line.strip():
            log(line.rstrip())
    if proc.wait() != 0:
        raise PcInstallError(f"Falló: {' '.join(cmd[:4])}…")


def create_shortcut(target: Path, icon: Optional[Path], name: str = "Banco remoto") -> Path:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    lnk = desktop / f"{name}.lnk"
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BANCO_LNK); "
          "$s.TargetPath = $env:BANCO_TARGET; $s.WorkingDirectory = $env:BANCO_DIR; "
          "$s.Description = 'Programación y depuración de microcontroladores'; "
          "if ($env:BANCO_ICON) { $s.IconLocation = $env:BANCO_ICON }; $s.Save()")
    env = dict(os.environ, BANCO_LNK=str(lnk), BANCO_TARGET=str(target), BANCO_DIR=str(target.parent),
               BANCO_ICON=str(icon) if icon else "")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   capture_output=True, timeout=60, creationflags=NO_WINDOW)
    return lnk


def install(package: Path, dest: Path, web_user: str, web_password: str, shortcut: bool,
            icon_bytes: Optional[bytes], log: Callable[[str], None] = print) -> Path:
    """Instala o actualiza. Devuelve la ruta de iniciar_pc.bat."""
    python = find_python()
    if python is None:
        raise PcInstallError("No se encontró Python 3.9 o más nuevo. Instalalo desde https://www.python.org "
                             "(marcá \"Add python.exe to PATH\") y volvé a abrir el instalador.")
    log(f"Python: {' '.join(python)}")
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Copiando la aplicación a {dest}...")
    with tarfile.open(package, "r:gz") as tar:
        for member in tar.getmembers():                 # sólo rutas dentro de dest
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise PcInstallError(f"Ruta inválida en el paquete: {member.name}")
        tar.extractall(dest)
    venv_python = dest / ".venv" / "Scripts" / "python.exe"
    if not venv_python.is_file():
        log("Creando el entorno de Python...")
        _run(python + ["-m", "venv", str(dest / ".venv")], log)
    log("Instalando dependencias (unos minutos la primera vez)...")
    _run([str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "-q",
          "-r", str(dest / "requirements.txt"), "esptool>=4.7,<5"], log)
    if web_user and web_password:
        log("Guardando el usuario de la web (sólo el hash de la contraseña)...")
        _run([str(venv_python), str(dest / "scripts" / "pc_run.py"), "--credenciales-stdin"], log,
             stdin_data=f"{web_user}\n{web_password}\n")
    log("Buscando las herramientas (OpenOCD, GDB, avrdude, dfu-util)...")
    check = subprocess.run([str(venv_python), str(dest / "scripts" / "pc_run.py"), "--check"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    for line in check.stdout.splitlines():
        if line.strip():
            log(line.rstrip())
    if check.returncode != 0:
        log("Falta OpenOCD: instalá STM32CubeIDE para programar STM32 por ST-Link (después no hay que reinstalar).")
    bat = dest / "iniciar_pc.bat"
    if shortcut:
        icon = None
        if icon_bytes:
            icon = dest / "banco-remoto.ico"
            icon.write_bytes(icon_bytes)
        lnk = create_shortcut(bat, icon)
        log(f"Acceso directo: {lnk}")
    return bat


def launch(bat: Path) -> None:
    subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=str(bat.parent), creationflags=NO_WINDOW)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--python":
    print(find_python())
