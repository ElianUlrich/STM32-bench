#!/usr/bin/env python3
"""Banco remoto en MODO PC: la app en tu computadora, con sus propios USB (sin BeagleBoard).

La primera vez pide un usuario y una contraseña para entrar a la web y los guarda
(sólo el hash) en %LOCALAPPDATA%\\BancoRemoto\\banco.env. Ahí quedan también los
proyectos y el estado. Después busca las herramientas (OpenOCD, GDB, dfu-util,
avrdude), arranca el servidor y abre el navegador.

    python scripts/pc_run.py                 sólo esta PC: http://127.0.0.1:8080
    python scripts/pc_run.py --tailscale     también desde otras PC de tu Tailscale
    python scripts/pc_run.py --password      cambiar el usuario o la contraseña
    python scripts/pc_run.py --check         sólo mostrar las herramientas encontradas

iniciar_pc.bat hace lo mismo (y la primera vez crea el entorno de Python).
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app import system, tools  # noqa: E402
from app.auth import hash_password  # noqa: E402

MIN_PASSWORD = 10


def data_dir() -> Path:
    if os.environ.get("BANCO_DATA_DIR"):
        return Path(os.environ["BANCO_DATA_DIR"])
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "BancoRemoto"
    return Path.home() / ".local" / "share" / "banco-remoto"


def ask_credentials(env_file: Path) -> None:
    print("Usuario y contraseña para entrar a la web del banco.")
    default_user = os.environ.get("USERNAME") or os.environ.get("USER") or "admin"
    user = input(f"Usuario [{default_user}]: ").strip() or default_user
    while True:
        password = getpass.getpass(f"Contraseña (mínimo {MIN_PASSWORD} caracteres): ")
        if len(password) < MIN_PASSWORD:
            print("Muy corta.")
            continue
        if password != getpass.getpass("Repetila: "):
            print("No coinciden.")
            continue
        break
    save_credentials(env_file, user, password)


def save_credentials(env_file: Path, user: str, password: str) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Banco remoto en modo PC. Lo generó scripts/pc_run.py: la contraseña no se guarda, sólo su hash.",
        f"BENCH_USER={user}",
        f"BENCH_PASSWORD_HASH={hash_password(password)}",
        f"BENCH_SECRET_KEY={secrets.token_urlsafe(48)}",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Guardado en {env_file}\n")


def show_tools() -> bool:
    found = tools.detect()
    names = {"openocd": "OpenOCD (ST-Link)", "gdb": "GDB (Depurar)", "dfu-util": "dfu-util (USB DFU)",
             "avrdude": "avrdude (Arduino)"}
    print("Herramientas:")
    for key, label in names.items():
        f = found.get(key)
        print(f"  {label:<20} " + (f"{f.source}" if f else "no encontrada"))
    try:
        import esptool  # noqa: F401
        print(f"  {'esptool (ESP32)':<20} entorno de Python")
    except ImportError:
        print(f"  {'esptool (ESP32)':<20} no instalado (pip install \"esptool<5\")")
    if not found.get("openocd"):
        print("\n  Sin OpenOCD no se puede programar ni depurar STM32 por ST-Link: instalá STM32CubeIDE")
        print("  (https://www.st.com/stm32cubeide) o el OpenOCD de xPack.")
    print()
    return bool(found.get("openocd"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Banco remoto en modo PC")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--tailscale", action="store_true", help="escuchar en la IP de Tailscale (otras PC de tu red)")
    ap.add_argument("--password", action="store_true", help="cambiar usuario y contraseña")
    ap.add_argument("--check", action="store_true", help="sólo mostrar las herramientas")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--credenciales-stdin", action="store_true",
                    help="guardar usuario y contraseña leídos de la entrada (dos líneas) y salir")
    args = ap.parse_args()

    if args.check:
        return 0 if show_tools() else 1
    if args.credenciales_stdin:                      # lo usa el instalador (sin consola)
        user, password = sys.stdin.readline().rstrip("\r\n"), sys.stdin.readline().rstrip("\r\n")
        if not user or len(password) < MIN_PASSWORD:
            print(f"Hace falta un usuario y una contraseña de al menos {MIN_PASSWORD} caracteres.", file=sys.stderr)
            return 1
        save_credentials(data_dir() / "banco.env", user, password)
        return 0

    base = data_dir()
    env_file = base / "banco.env"
    if args.password or not env_file.is_file():
        ask_credentials(env_file)
    show_tools()

    host = "127.0.0.1"
    if args.tailscale:
        ts = system.tailscale_ipv4("tailscale0")
        if not ts:
            print("No se encontró la IP de Tailscale: ¿está abierto Tailscale en esta PC?")
            return 1
        host = ts
    os.environ.update({
        "BENCH_ENV_FILE": str(env_file),
        "BENCH_MODE": "pc",
        "BENCH_MOCK": "0",
        "BENCH_CONFIG_DIR": str(REPO / "config"),
        "BENCH_BENCH_FILE": str(REPO / "config" / "bench.pc.yaml"),
        "BENCH_PROJECTS_DIR": str(base / "proyectos"),
        "BENCH_STATE_DIR": str(base / "state"),
        "PYTHONUNBUFFERED": "1",
    })
    (base / "proyectos").mkdir(parents=True, exist_ok=True)
    (base / "state").mkdir(parents=True, exist_ok=True)

    url = f"http://{host}:{args.port}/"
    print(f"Banco remoto en {url}   (Ctrl+C para cerrar)")
    print(f"Proyectos: {base / 'proyectos'}\n")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(2), webbrowser.open(url)), daemon=True).start()
    import uvicorn
    uvicorn.run("app.main:create_app", factory=True, host=host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
