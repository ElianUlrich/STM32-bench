"""Autenticación: hash de contraseña (biblioteca estándar) y sesión.

Formatos del hash (sin '$' para no chocar con EnvironmentFile de systemd ni
con shells):
    scrypt:<n>:<r>:<p>:<salt_b64>:<hash_b64>
    pbkdf2_sha256:<iteraciones>:<salt_b64>:<hash_b64>

scrypt es un KDF resistente a GPU recomendado por OWASP, igual que bcrypt o
argon2, y viene en hashlib: no hay que compilar nada en la placa armhf. Pero
hashlib.scrypt solo existe si Python se compiló con OpenSSL >= 1.1; en Ubuntu
16.04 (OpenSSL 1.0.2) se usa PBKDF2-HMAC-SHA256 con 600.000 iteraciones
(recomendación de OWASP para PBKDF2). Se verifican los dos formatos.
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
PBKDF2_ITERATIONS = 600_000
HAS_SCRYPT = hasattr(hashlib, "scrypt")


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, scheme: Optional[str] = None) -> str:
    """Hash con scrypt si está disponible; si no, PBKDF2-HMAC-SHA256."""
    if not password:
        raise ValueError("La contraseña no puede estar vacía")
    scheme = scheme or ("scrypt" if HAS_SCRYPT else "pbkdf2_sha256")
    salt = secrets.token_bytes(16)
    if scheme == "scrypt":
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                                dklen=KEY_LEN, maxmem=MAXMEM)
        return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{_b64e(salt)}:{_b64e(digest)}"
    if scheme == "pbkdf2_sha256":
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, KEY_LEN)
        return f"pbkdf2_sha256:{PBKDF2_ITERATIONS}:{_b64e(salt)}:{_b64e(digest)}"
    raise ValueError(f"Esquema de hash desconocido: {scheme}")


def verify_password(password: str, encoded: Optional[str]) -> bool:
    if not encoded or not password:
        return False
    try:
        parts = encoded.split(":")
        if parts[0] == "scrypt" and len(parts) == 6:
            if not HAS_SCRYPT:
                return False   # este Python no puede verificar scrypt (OpenSSL < 1.1)
            _, n, r, p, salt_b64, hash_b64 = parts
            expected = _b64d(hash_b64)
            digest = hashlib.scrypt(password.encode("utf-8"), salt=_b64d(salt_b64), n=int(n), r=int(r),
                                    p=int(p), dklen=len(expected), maxmem=MAXMEM)
        elif parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            _, iterations, salt_b64, hash_b64 = parts
            expected = _b64d(hash_b64)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64d(salt_b64),
                                         int(iterations), len(expected))
        else:
            return False
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
