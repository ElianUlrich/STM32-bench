"""El paquete con la aplicación (banco-remoto.tgz) que copia el instalador.

En el .exe viene adentro (build.ps1 lo arma con `git archive`). Corriendo desde
el repositorio se arma en el momento, con los cambios sin commit incluidos.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

NAME = "banco-remoto.tgz"
REPO = Path(__file__).resolve().parent.parent


def resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", REPO / "installer"))
    return base / name


def runtime_path() -> Path:
    """Python embebido, bibliotecas y herramientas (installer/preparar.py)."""
    frozen = getattr(sys, "_MEIPASS", None)
    path = Path(frozen) / "runtime" if frozen else REPO / "installer" / "build" / "runtime"
    if not (path / "python-embed.zip").is_file():
        raise FileNotFoundError(f"Falta {path}: corré python -m installer.preparar")
    return path


def package_path() -> Path:
    bundled = resource(NAME)
    if bundled.is_file():
        return bundled
    return build(Path(tempfile.gettempdir()) / NAME)


def build(out: Path) -> Path:
    """git archive del repositorio (con los cambios sin commit, como desplegar.ps1)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    ref = subprocess.run(["git", "stash", "create"], cwd=REPO, capture_output=True, text=True,
                         creationflags=flags).stdout.strip() or "HEAD"
    subprocess.run(["git", "archive", "--format=tar.gz", "-o", str(out), ref], cwd=REPO, check=True,
                   creationflags=flags)
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "installer" / NAME))
