"""Punto de entrada: crea la aplicación FastAPI.

Uso:  uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import sample_firmware
from .api import router
from .bench import Bench, GdbActiveError, RequestError
from .debugger.session import DebugError
from .files import FileValidationError
from .programmers.runner import BusyError
from .serial_monitor import SerialError
from .settings import REPO_ROOT, Settings, client_allowed, settings_from_env
from .system import PowerError

log = logging.getLogger("stm32bench")
WEB_DIR = REPO_ROOT / "web"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SECURITY_HEADERS = [
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy",
     b"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
     b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
]


# Rutas que reciben firmware: límite de uploads.max_size_mb (+1 MB de margen).
UPLOAD_PATHS = ("/api/files/upload", "/api/files/esp-project")


class GuardMiddleware:
    """Middleware ASGI de seguridad (va por fuera de todo lo demás):

    * rechaza clientes fuera de security.allowed_networks (Tailscale/localhost);
    * exige el header "X-Bench: 1" en toda petición de la API que modifique
      algo: un sitio ajeno no puede agregar headers propios sin CORS (que no
      habilitamos), así que esto bloquea CSRF además de SameSite=Strict;
    * agrega headers de seguridad a todas las respuestas.
    """

    def __init__(self, app, networks, max_upload_bytes: int) -> None:
        self.app = app
        self.networks = networks
        # Margen para los encabezados multipart.
        self.max_upload_bytes = max_upload_bytes + 1024 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        client = scope.get("client")
        host = client[0] if client else None
        if not client_allowed(host, self.networks):
            return await _plain(send, 403, "Acceso solo por Tailscale")
        path = scope.get("path", "")
        if path.startswith("/api/") and scope.get("method") not in SAFE_METHODS:
            headers = dict(scope.get("headers") or [])
            if headers.get(b"x-bench") != b"1":
                return await _plain(send, 403, "Falta el header X-Bench")
            # Corta subidas demasiado grandes antes de recibir el cuerpo.
            limit = self.max_upload_bytes if path in UPLOAD_PATHS else 256 * 1024
            try:
                length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                length = 0
            if length > limit:
                return await _plain(send, 413, "La petición es demasiado grande")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or []) + SECURITY_HEADERS
                if not path.startswith("/api/"):
                    # Estáticos: siempre revalidar (ETag -> 304) para no usar JS/CSS viejos tras actualizar.
                    headers.append((b"cache-control", b"no-cache"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def _plain(send, status: int, text: str) -> None:
    body = json.dumps({"detail": text}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), *SECURITY_HEADERS]})
    await send({"type": "http.response.body", "body": body})


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or settings_from_env()
    bench = Bench(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        bench.bus.attach_loop(loop)
        if settings.mock:
            sample_firmware.seed_projects(settings.projects_dir)
            sample_firmware.seed_debug_project(settings.projects_dir)
            for rel, target in sample_firmware.DEMO_TARGETS.items():
                if (settings.projects_dir / rel).is_file() and bench.meta.target_of(rel) is None:
                    bench.meta.assign(rel, target)
            if settings.generated_password:
                print("\n" + "=" * 64)
                print(f"  MODO MOCK — usuario: {settings.username}   contraseña: {settings.generated_password}")
                print("  (contraseña aleatoria por arranque; definí BENCH_PASSWORD_HASH para fijarla)")
                print("=" * 64 + "\n", flush=True)
        for error in bench.config_errors:
            log.error("Error de configuración: %s", error)
        # La detección de herramientas puede tardar (OpenOCD en la placa): en segundo plano.
        loop.run_in_executor(None, bench.detect_tools)
        yield
        bench.shutdown()

    app = FastAPI(title="stm32-bench", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bench = bench

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="bench_session",
        max_age=int(bench.cfg.session_hours * 3600),
        same_site="strict",
        https_only=settings.cookie_secure,
    )
    app.add_middleware(GuardMiddleware, networks=bench.cfg.allowed_networks,
                       max_upload_bytes=bench.cfg.max_upload_mb * 1024 * 1024)

    def _error(status: int, detail: str, code: Optional[str] = None) -> JSONResponse:
        content = {"detail": detail}
        if code:
            content["code"] = code
        return JSONResponse(status_code=status, content=content)

    @app.exception_handler(RequestError)
    async def _request_error(_: Request, exc: RequestError):
        return _error(400, str(exc))

    @app.exception_handler(FileValidationError)
    async def _file_error(_: Request, exc: FileValidationError):
        return _error(400, str(exc))

    @app.exception_handler(DebugError)
    async def _debug_error(_: Request, exc: DebugError):
        return _error(400, str(exc))

    @app.exception_handler(SerialError)
    async def _serial_error(_: Request, exc: SerialError):
        return _error(400, str(exc))

    @app.exception_handler(BusyError)
    async def _busy(_: Request, exc: BusyError):
        return _error(409, str(exc), "busy")

    @app.exception_handler(GdbActiveError)
    async def _gdb_active(_: Request, exc: GdbActiveError):
        return _error(409, str(exc), "gdb_active")

    @app.exception_handler(PowerError)
    async def _power(_: Request, exc: PowerError):
        return _error(500, str(exc))

    app.include_router(router)
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
