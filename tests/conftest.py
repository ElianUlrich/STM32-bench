import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.auth import hash_password  # noqa: E402
from app.settings import REPO_ROOT, Settings  # noqa: E402

TEST_USER = "tester"
TEST_PASSWORD = "clave-de-prueba-123"
_HASH = hash_password(TEST_PASSWORD)


def run(coro):
    """Ejecuta una corrutina en un loop nuevo (sin depender de pytest-asyncio)."""
    return asyncio.run(coro)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        mock=True,
        username=TEST_USER,
        password_hash=_HASH,
        secret_key="k" * 48,
        config_dir=REPO_ROOT / "config",
        projects_dir=tmp_path / "proyectos",
        state_dir=tmp_path / "state",
        cookie_secure=False,
    )


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app(settings)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        c.headers.update({"X-Bench": "1"})
        resp = c.post("/api/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
        assert resp.status_code == 200, resp.text
        yield c
