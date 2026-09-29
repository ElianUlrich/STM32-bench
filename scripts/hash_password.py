#!/usr/bin/env python3
"""Genera el hash de contraseña para BENCH_PASSWORD_HASH (scrypt).

Uso interactivo:   python3 scripts/hash_password.py
Desde un script:   printf '%s' "$PASS" | python3 scripts/hash_password.py --stdin
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.auth import hash_password  # noqa: E402

MIN_LENGTH = 10


def main() -> int:
    if "--stdin" in sys.argv:
        password = sys.stdin.read().rstrip("\r\n")
    else:
        password = getpass.getpass("Contraseña nueva: ")
        if password != getpass.getpass("Repetila: "):
            print("Las contraseñas no coinciden.", file=sys.stderr)
            return 1
    if len(password) < MIN_LENGTH:
        print(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres.", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
