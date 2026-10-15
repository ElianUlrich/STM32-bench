"""Hash de contraseñas, límite de intentos y detección de ST-Link por sysfs."""
from app.auth import LoginLimiter, check_credentials, hash_password, verify_password
from app.probe import normalize_serial, stlinks_from_sysfs
from app.settings import client_allowed, parse_bench_config


def test_hash_roundtrip():
    encoded = hash_password("secreto")
    assert encoded.startswith("scrypt:") and "$" not in encoded
    assert verify_password("secreto", encoded)
    assert not verify_password("Secreto", encoded)
    assert hash_password("secreto") != encoded   # sal aleatoria


def test_pbkdf2_fallback_without_scrypt(monkeypatch):
    """Python con OpenSSL 1.0.2 (Ubuntu 16.04) no tiene hashlib.scrypt."""
    import app.auth as auth
    monkeypatch.setattr(auth, "HAS_SCRYPT", False)
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1000)   # rápido para el test
    encoded = auth.hash_password("secreto")
    assert encoded.startswith("pbkdf2_sha256:1000:") and "$" not in encoded
    assert auth.verify_password("secreto", encoded)
    assert not auth.verify_password("otro", encoded)
    # Un hash scrypt no se puede verificar sin scrypt: se rechaza, no revienta.
    assert not auth.verify_password("secreto", "scrypt:16384:8:1:c2FsdA:aGFzaA")


def test_pbkdf2_explicit_scheme():
    import app.auth as auth
    encoded = auth.hash_password("clave", scheme="pbkdf2_sha256")
    assert encoded.split(":")[1] == str(auth.PBKDF2_ITERATIONS)
    assert auth.verify_password("clave", encoded)


def test_verify_garbage():
    assert not verify_password("x", None)
    assert not verify_password("x", "no-es-un-hash")
    assert not verify_password("x", "bcrypt:1:2:3:aa:bb")


def test_check_credentials():
    encoded = hash_password("pw")
    assert check_credentials("admin", "pw", "admin", encoded)
    assert not check_credentials("root", "pw", "admin", encoded)
    assert not check_credentials("admin", "nop", "admin", encoded)


def test_login_limiter():
    limiter = LoginLimiter(max_failures=3, window_s=60, lockout_s=60)
    for _ in range(2):
        limiter.failure("1.2.3.4")
    assert limiter.blocked_for("1.2.3.4") == 0
    limiter.failure("1.2.3.4")
    assert limiter.blocked_for("1.2.3.4") > 0
    assert limiter.blocked_for("5.6.7.8") == 0
    limiter.success("1.2.3.4")
    assert limiter.blocked_for("1.2.3.4") == 0


def test_allowed_networks():
    cfg = parse_bench_config({"interfaces": {"s": {"cfg": "interface/stlink.cfg"}},
                              "security": {"allowed_networks": ["127.0.0.0/8", "100.64.0.0/10", "fd7a:115c:a1e0::/48"]}})
    nets = cfg.allowed_networks
    assert client_allowed("100.101.1.2", nets)
    assert client_allowed("127.0.0.1", nets)
    assert client_allowed("fd7a:115c:a1e0::1", nets)
    assert client_allowed("::ffff:100.64.0.1", nets)
    assert not client_allowed("192.168.1.10", nets)
    assert not client_allowed("8.8.8.8", nets)
    assert not client_allowed(None, nets)
    assert not client_allowed("testclient", nets)


def test_normalize_serial():
    assert normalize_serial(b"066DFF485550755187121832\n") == ("066DFF485550755187121832", False)
    # ST-Link/V2 viejo: bytes binarios expuestos por el kernel como Latin-1 en UTF-8
    raw = "".join(chr(b) for b in bytes.fromhex("AABC6E065075FF5517421 93F".replace(" ", ""))).encode("utf-8")
    serial, was_binary = normalize_serial(raw + b"\n")
    assert was_binary and serial == "AABC6E065075FF551742193F"


def _fake_usb(root, name, vid, pid, serial=b"SER123\n", busnum=1, devnum=5):
    dev = root / name
    dev.mkdir(parents=True)
    (dev / "idVendor").write_text(vid + "\n")
    (dev / "idProduct").write_text(pid + "\n")
    (dev / "serial").write_bytes(serial)
    (dev / "product").write_text("STM32 STLink\n")
    (dev / "busnum").write_text(f"{busnum}\n")
    (dev / "devnum").write_text(f"{devnum}\n")


def test_list_stlinks_from_sysfs(tmp_path):
    sysfs = tmp_path / "sys"
    _fake_usb(sysfs, "1-1.1", "0483", "374b", b"066DFF485550755187121832\n", devnum=4)
    _fake_usb(sysfs, "1-1.2", "0483", "3748", "ª¼n\x06PuÿU\x17B\x19?".encode("utf-8"), devnum=7)
    _fake_usb(sysfs, "1-1.3", "0483", "df11", b"3957377B3432\n")          # DFU: no es ST-Link
    _fake_usb(sysfs, "1-1.4", "046d", "c52b", b"X\n")                     # otro fabricante
    (sysfs / "usb1").mkdir()                                              # hub sin idVendor
    devfs = tmp_path / "dev"
    (devfs / "001").mkdir(parents=True)
    (devfs / "001" / "004").write_text("")
    probes = stlinks_from_sysfs(sysfs, devfs)
    assert [(p.pid, p.model) for p in probes] == [("374b", "ST-LINK/V2-1"), ("3748", "ST-LINK/V2")]
    assert probes[0].serial == "066DFF485550755187121832" and not probes[0].raw_serial_was_binary
    assert probes[1].serial == "AABC6E065075FF551742193F" and probes[1].raw_serial_was_binary
    assert probes[0].accessible is True and probes[1].accessible is False
