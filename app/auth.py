"""Autenticación: hash de contraseña con scrypt (biblioteca estándar) y sesión.

Formato del hash (sin '$' para no chocar con EnvironmentFile de systemd ni
con shells):  scrypt:<n>:<r>:<p>:<salt_b64>:<hash_b64>

scrypt es un KDF resistente a GPU recomendado por OWASP, igual que bcrypt o
argon2, pero viene en hashlib: no hay que compilar nada en la placa armhf.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, List, Optional

SCRYPT_N = 2 ** 14   # ~16 MB de RAM por verificación
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LEN = 32
MAXMEM = 64 * 1024 * 1024


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    if not password:
        raise ValueError("La contraseña no puede estar vacía")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                            dklen=KEY_LEN, maxmem=MAXMEM)
    return f"scrypt:{n}:{r}:{p}:{_b64e(salt)}:{_b64e(digest)}"


def verify_password(password: str, encoded: Optional[str]) -> bool:
    if not encoded or not password:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split(":")
        if scheme != "scrypt":
            return False
        expected = _b64d(hash_b64)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=_b64d(salt_b64), n=int(n), r=int(r),
                                p=int(p), dklen=len(expected), maxmem=MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def check_credentials(username: str, password: str, expected_user: str, password_hash: Optional[str]) -> bool:
    """Compara usuario y contraseña en tiempo constante (siempre calcula el hash)."""
    user_ok = hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
    pass_ok = verify_password(password, password_hash)
    return user_ok and pass_ok


class LoginLimiter:
    """Limita intentos fallidos por IP: tras `max_failures` en `window_s`, bloquea."""

    def __init__(self, max_failures: int = 5, window_s: float = 300, lockout_s: float = 300) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._failures: Dict[str, List[float]] = {}
        self._locked_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked_for(self, ip: str) -> float:
        with self._lock:
            until = self._locked_until.get(ip, 0)
            remaining = until - time.monotonic()
            return remaining if remaining > 0 else 0.0

    def failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(ip, []) if now - t < self.window_s]
            recent.append(now)
            self._failures[ip] = recent
            if len(recent) >= self.max_failures:
                self._locked_until[ip] = now + self.lockout_s
                self._failures[ip] = []

    def success(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)
