"""Rutas HTTP de la API (/api/...)."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import files as fileops
from . import system
from .auth import LoginLimiter, check_credentials
from .bench import Bench, JobRequest
from .compat import check_file_for_chip

router = APIRouter(prefix="/api")
limiter = LoginLimiter()


def get_bench(request: Request) -> Bench:
    return request.app.state.bench


def require_user(request: Request) -> str:
    bench = get_bench(request)
    user = request.session.get("user")
    if not user or user != bench.settings.username:
        raise HTTPException(status_code=401, detail="Sesión no iniciada o vencida")
    return user


# ----------------------------------------------------------------------- sesión

class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody, request: Request) -> Dict[str, Any]:
    bench = get_bench(request)
    ip = request.client.host if request.client else "?"
    wait = limiter.blocked_for(ip)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"Demasiados intentos. Probá de nuevo en {int(wait)} s")
    ok = await asyncio.get_running_loop().run_in_executor(
        None, check_credentials, body.username, body.password,
        bench.settings.username, bench.settings.password_hash)
    if not ok:
        limiter.failure(ip)
        await asyncio.sleep(0.5)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    limiter.success(ip)
    request.session.clear()
    request.session["user"] = bench.settings.username
    return {"ok": True, "user": bench.settings.username}


@router.post("/logout")
async def logout(request: Request) -> Dict[str, Any]:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
async def me(user: str = Depends(require_user)) -> Dict[str, Any]:
    return {"user": user}


# ------------------------------------------------------------------ configuración

@router.get("/config")
async def config(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return get_bench(request).public_config()


# ----------------------------------------------------------------------- archivos

@router.get("/files")
async def list_files(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    projects = await asyncio.get_running_loop().run_in_executor(
        None, fileops.scan_projects, bench.settings.projects_dir, bench.cfg.scan_max_depth,
        bench.meta.all())
    return {"projects_dir": str(bench.settings.projects_dir), "projects": projects}


@router.post("/files/upload")
async def upload(request: Request, file: UploadFile = File(...), project: str = Form("general"),
                 target: str = Form(...), _: str = Depends(require_user)) -> Dict[str, Any]:
    """Sube un binario y lo asigna a su placa (obligatorio): el contenido tiene
    que ser compatible con esa placa o se rechaza."""
    bench = get_bench(request)
    chip = bench.chip(target)
    max_bytes = bench.cfg.max_upload_mb * 1024 * 1024
    filename = file.filename or ""
    result: Dict[str, Any] = {}

    def validator(tmp):
        result["compat"] = check_file_for_chip(tmp, chip, filename=filename)

    saved = await asyncio.get_running_loop().run_in_executor(
        None, lambda: fileops.save_upload(bench.settings.projects_dir, project, filename,
                                          file.file, max_bytes, validator=validator))
    bench.meta.assign(saved.rel_path, chip.id)
    bench.bus.publish({"type": "files_changed"})
    compat = result.get("compat")
    return {"ok": True, "path": saved.rel_path, "size": saved.size, "target": chip.id,
            "warnings": compat.warnings if compat else []}


class TargetBody(BaseModel):
    path: str
    target: Optional[str] = None


@router.put("/files/target")
async def set_file_target(body: TargetBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    """Asigna (o quita, con target=null) la placa de un archivo existente, p. ej. de Syncthing."""
    bench = get_bench(request)
    if body.target:
        compat = await asyncio.get_running_loop().run_in_executor(
            None, bench.assign_target, body.path, body.target)
        warnings = compat.warnings
    else:
        fileops.resolve_in_root(bench.settings.projects_dir, body.path)
        bench.meta.forget(body.path)
        warnings = []
    bench.bus.publish({"type": "files_changed"})
    return {"ok": True, "path": body.path, "target": body.target, "warnings": warnings}


@router.delete("/files")
async def delete_file(request: Request, path: str, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    fileops.delete_upload(bench.settings.projects_dir, path)
    bench.meta.forget(path)
    bench.bus.publish({"type": "files_changed"})
    return {"ok": True}


# ---------------------------------------------------------------- programadores

@router.get("/probes")
async def probes(request: Request, dfu: bool = True, _: str = Depends(require_user)) -> Dict[str, Any]:
    """ST-Links (sysfs, barato) y, si se pide, dispositivos DFU (ejecuta dfu-util -l)."""
    bench = get_bench(request)
    stlinks = [p.to_public() for p in bench.list_stlinks()]
    dfu_devices, dfu_error = [], None
    if dfu:
        try:
            dfu_devices = [d.to_public() for d in await bench.list_dfu()]
        except Exception as exc:
            dfu_error = str(exc)
    ports = await asyncio.get_running_loop().run_in_executor(None, bench.serial.list_ports)
    return {"stlinks": stlinks, "dfu": dfu_devices, "dfu_error": dfu_error, "serial_ports": ports}


# ----------------------------------------------------------------------- trabajos

class JobBody(BaseModel):
    action: str
    chip: str
    method: Optional[str] = None
    interface: Optional[str] = None
    file: Optional[str] = None
    address: Optional[str] = None
    probe_serial: Optional[str] = None
    dfu_serial: Optional[str] = None
    serial_port: Optional[str] = None
    leave: bool = True
    stop_gdb: bool = False


@router.post("/jobs", status_code=202)
async def create_job(body: JobBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    job = await bench.start_job(JobRequest(**data))
    return {"job": job.to_public()}


@router.post("/jobs/cancel")
async def cancel_job(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return {"cancelled": get_bench(request).runner.cancel()}


@router.get("/jobs/current")
async def current_job(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    job = get_bench(request).runner.current
    return {"job": job.to_public() if job else None}


@router.get("/history")
async def history(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return {"entries": get_bench(request).history.list()}


# ------------------------------------------------------------------- servidor GDB

class GdbStartBody(BaseModel):
    chip: str
    interface: Optional[str] = None
    probe_serial: Optional[str] = None


@router.get("/gdb")
async def gdb_status(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return get_bench(request).gdb.status()


@router.post("/gdb/start")
async def gdb_start(body: GdbStartBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).start_gdb(body.chip, body.interface, body.probe_serial)


@router.post("/gdb/stop")
async def gdb_stop(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).gdb.stop()


# ------------------------------------------------------------------ monitor serie

class SerialOpenBody(BaseModel):
    port: str
    baud: int


class SerialSendBody(BaseModel):
    text: str
    eol: str = "\n"


EOLS = {"": "", "\n": "\n", "\r": "\r", "\r\n": "\r\n"}


@router.get("/serial")
async def serial_status(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    ports = await asyncio.get_running_loop().run_in_executor(None, bench.serial.list_ports)
    return {**bench.serial.status(), "ports": ports}


@router.post("/serial/open")
async def serial_open(body: SerialOpenBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    await asyncio.get_running_loop().run_in_executor(None, bench.serial.open, body.port, body.baud)
    return bench.serial.status()


@router.post("/serial/close")
async def serial_close(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    await asyncio.get_running_loop().run_in_executor(None, bench.serial.close)
    return bench.serial.status()


@router.post("/serial/send")
async def serial_send(body: SerialSendBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    if body.eol not in EOLS:
        raise HTTPException(status_code=400, detail="Fin de línea inválido")
    if len(body.text) > 4096:
        raise HTTPException(status_code=400, detail="Texto demasiado largo (máx. 4096)")
    bench = get_bench(request)
    bench.serial.write((body.text + EOLS[body.eol]).encode("utf-8"))
    return {"ok": True}


# ------------------------------------------------------------------------ sistema

class PowerBody(BaseModel):
    confirm: bool = False


@router.get("/system")
async def system_info(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    info = await asyncio.get_running_loop().run_in_executor(
        None, system.collect, bench.settings.projects_dir, bench.cfg.tailscale_interface, bench.settings.mock)
    info["versions"] = bench.versions
    info["mock"] = bench.settings.mock
    return info


async def _power(kind: str, body: PowerBody, request: Request) -> Dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Falta la confirmación")
    bench = get_bench(request)
    if bench.runner.busy:
        raise HTTPException(status_code=409, detail="Hay una operación en curso; esperá o cancelala antes")
    bench.shutdown()
    command = system.power_action(kind, bench.settings.mock)
    bench.bus.publish({"type": "log", "source": "app", "level": "warn", "line": f"Sistema: {command}"})
    return {"ok": True, "command": command}


@router.post("/system/poweroff")
async def poweroff(body: PowerBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await _power("poweroff", body, request)


@router.post("/system/reboot")
async def reboot(body: PowerBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await _power("reboot", body, request)


# ----------------------------------------------------------------- eventos (SSE)

def _sse(event: Dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@router.get("/events")
async def events(request: Request, _: str = Depends(require_user)) -> StreamingResponse:
    bench = get_bench(request)
    queue = bench.bus.subscribe()

    async def stream():
        try:
            yield "retry: 3000\n\n"
            job = bench.runner.current
            yield _sse({"type": "snapshot", "job": job.to_public() if job else None,
                        "gdb": bench.gdb.status(), "serial": bench.serial.status()})
            for past in bench.bus.replay():
                yield _sse(dict(past, replay=True))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield _sse(event)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bench.bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
