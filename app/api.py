"""Rutas HTTP de la API (/api/...)."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import esp_project
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
    def scan():
        found = fileops.scan_projects(bench.settings.projects_dir, bench.cfg.scan_max_depth, bench.meta.all())
        esp_project.annotate(bench.settings.projects_dir, found)   # .bin de proyectos ESP-IDF
        return found

    projects = await asyncio.get_running_loop().run_in_executor(None, scan)
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
    if filename.lower().endswith(".zip"):
        return await _upload_project_zip(bench, chip, project, filename, file, max_bytes)
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


async def _upload_project_zip(bench: Bench, chip, project: str, filename: str, file: UploadFile,
                              max_bytes: int) -> Dict[str, Any]:
    """Proyecto completo en .zip (firmware + fuentes, para depurar). Se asigna la placa
    a los binarios compatibles con ella; si ninguno lo es, se rechaza el .zip."""
    ok: Dict[str, Any] = {}
    rejected: list = []

    def validator(items):
        for rel, path in items:
            try:
                ok[rel] = check_file_for_chip(path, chip)
            except fileops.FileValidationError as exc:
                rejected.append(f"{rel}: {exc}")
        if not ok:
            raise fileops.FileValidationError(
                f"Ningún binario del .zip corresponde a {chip.name}. " + " ".join(rejected[:3]))

    saved = await asyncio.get_running_loop().run_in_executor(
        None, lambda: fileops.save_project_zip(bench.settings.projects_dir, project, filename,
                                               file.file, max_bytes, validator=validator))
    warnings = []
    for rel, compat in ok.items():
        bench.meta.assign(f"{saved.folder}/{rel}", chip.id)
        warnings += compat.warnings
    warnings += [f"Sin asignar (no es para {chip.name}): {r}" for r in rejected]
    if saved.skipped:
        warnings.append(f"Se ignoraron {saved.skipped} archivos que no son firmware ni fuentes")
    bench.bus.publish({"type": "files_changed"})
    elfs = [f"{saved.folder}/{rel}" for rel in ok if rel.lower().endswith(".elf")]
    return {"ok": True, "folder": saved.folder, "path": (elfs or [f"{saved.folder}/{r}" for r in ok])[0],
            "firmware": saved.firmware, "sources": saved.sources, "target": chip.id, "warnings": warnings}


@router.post("/files/esp-project")
async def upload_esp_project(request: Request, manifest: UploadFile = File(...),
                             files: List[UploadFile] = File(...), paths: List[str] = Form(...),
                             project: str = Form("general"), target: str = Form(...),
                             _: str = Depends(require_user)) -> Dict[str, Any]:
    """Proyecto ESP-IDF: flasher_args.json + exactamente los .bin que lista (la web
    los elige de la carpeta build/). Se valida todo contra la placa antes de guardarlo."""
    bench = get_bench(request)
    chip = bench.chip(target)
    if chip.esptool is None:
        raise fileops.FileValidationError(f"{chip.name} no es una placa ESP: elegí la placa del proyecto")
    m = esp_project.parse_manifest(await manifest.read(esp_project.MANIFEST_MAX_BYTES + 1))
    if len(files) != len(paths):
        raise fileops.FileValidationError("Cada archivo tiene que llegar con su ruta")
    listed = {img.path for img in m.images}
    missing = sorted(listed - set(paths))
    unknown = sorted(set(paths) - listed)
    if missing:
        raise fileops.FileValidationError("Faltan archivos del proyecto: " + ", ".join(missing))
    if unknown or len(set(paths)) != len(paths):
        raise fileops.FileValidationError(
            f"Archivos que no lista {esp_project.MANIFEST_NAME}: " + ", ".join(unknown or paths))
    app = m.role("app")
    folder = app.path.rsplit("/", 1)[-1][:-4] if app else "proyecto-esp"
    max_bytes = bench.cfg.max_upload_mb * 1024 * 1024

    def validator(tmp_dir):
        esp_project.check(m, esp_project.reader(tmp_dir), chip.esptool)

    saved = await asyncio.get_running_loop().run_in_executor(
        None, lambda: fileops.save_file_set(
            bench.settings.projects_dir, project, folder, [(p, f.file) for p, f in zip(paths, files)],
            max_bytes, extra={esp_project.MANIFEST_NAME: esp_project.dump_manifest(m)}, validator=validator))
    for rel in saved.firmware:
        bench.meta.assign(rel, chip.id)
    bench.bus.publish({"type": "files_changed"})
    main = f"{saved.folder}/{app.path}" if app else saved.firmware[0]
    return {"ok": True, "folder": saved.folder, "path": main, "firmware": saved.firmware,
            "target": chip.id, "warnings": []}


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
    esp_scope: Optional[str] = None     # proyecto ESP-IDF: "file" (sólo el elegido) o "all"
    read_scope: Optional[str] = None    # leer un ESP: "identify" (rápido) o "full" (copia completa)


@router.post("/jobs", status_code=202)
async def create_job(body: JobBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    job = await bench.start_job(JobRequest(**data))
    return {"job": job.to_public()}


# ------------------------------------------------------------------- leer el micro

@router.get("/readback")
async def readback_latest(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    """La última lectura del micro (informe completo), o null."""
    return {"report": get_bench(request).reader.store.latest()}


@router.get("/readback/{rid}/files/{name}")
async def readback_file(rid: str, name: str, request: Request, _: str = Depends(require_user)) -> FileResponse:
    bench = get_bench(request)
    path = bench.reader.store.file(rid, name)
    report = bench.reader.store.get(rid)
    return FileResponse(path, media_type="application/octet-stream", filename=f"{report['chip']}-{name}")


@router.post("/readback/{rid}/save")
async def readback_save(rid: str, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    """Guarda la lectura como copia de respaldo en Archivos (proyecto "respaldos")."""
    bench = get_bench(request)
    result = await asyncio.get_running_loop().run_in_executor(None, bench.reader.save, rid)
    bench.bus.publish({"type": "files_changed"})
    return result


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
    file: Optional[str] = None       # .elf elegido: si usa RTT, OpenOCD publica también el canal RTT


@router.get("/gdb")
async def gdb_status(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return get_bench(request).gdb.status()


@router.post("/gdb/start")
async def gdb_start(body: GdbStartBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    rtt = await asyncio.get_running_loop().run_in_executor(None, bench.rtt_config, body.file)
    return await bench.start_gdb(body.chip, body.interface, body.probe_serial, rtt=rtt)


class RttOpenBody(BaseModel):
    chip: str
    file: str
    interface: Optional[str] = None
    probe_serial: Optional[str] = None


@router.post("/rtt/open")
async def rtt_open(body: RttOpenBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    """Abre el canal RTT del programa en el monitor serie (por el ST-Link, sin adaptador)."""
    return await get_bench(request).open_rtt(body.chip, body.interface, body.probe_serial, body.file)


@router.post("/gdb/stop")
async def gdb_stop(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).stop_gdb()


# ------------------------------------------------------------------- depurador web

class DebugStartBody(BaseModel):
    chip: str
    file: str
    interface: Optional[str] = None
    probe_serial: Optional[str] = None
    mode: str = "load"          # load = grabar y parar en main · reset = reiniciar · attach = donde esté


class DebugControlBody(BaseModel):
    action: str
    count: int = 1          # Paso / Entrar repetidos (×5, ×10, ×50, ×100)


class BreakpointBody(BaseModel):
    source_id: int
    line: int


class WatchBody(BaseModel):
    expr: str


class SetVarBody(BaseModel):
    name: str
    value: str


@router.get("/debug")
async def debug_status(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return get_bench(request).debug.status()


@router.get("/debug/sources")
async def debug_sources(request: Request, file: str, _: str = Depends(require_user)) -> Dict[str, Any]:
    """Archivos fuente del .elf encontrados en la carpeta del proyecto."""
    bench = get_bench(request)
    _, info = await asyncio.get_running_loop().run_in_executor(None, bench.debug_elf, file)
    if bench.debug.active and bench.debug.info.get("elf") == file:
        bench.debug.set_debuginfo(info)          # pudieron subirse las fuentes con la sesión iniciada
    return dict(info.to_public(), file=file)


@router.get("/debug/source")
async def debug_source(request: Request, file: str, id: int, _: str = Depends(require_user)) -> Dict[str, Any]:
    """Texto de un archivo fuente, elegido por número (nunca por ruta)."""
    from .elfinfo import DebugInfoError, read_source
    bench = get_bench(request)
    loop = asyncio.get_running_loop()
    _, info = await loop.run_in_executor(None, bench.debug_elf, file)
    try:
        src = info.by_id(id)
        text = await loop.run_in_executor(None, read_source, src)
    except DebugInfoError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"id": src.id, "name": src.name, "base": src.base, "rel": src.rel, "lines": src.lines, "text": text}


@router.post("/debug/start")
async def debug_start(body: DebugStartBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).start_debug(body.chip, body.interface, body.probe_serial, body.file, body.mode)


@router.post("/debug/stop")
async def debug_stop(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).debug.stop()


@router.post("/debug/control")
async def debug_control(body: DebugControlBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).debug.control(body.action, body.count)


@router.post("/debug/breakpoint")
async def debug_breakpoint(body: BreakpointBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    """Pone o saca un breakpoint en esa línea."""
    return await get_bench(request).debug.toggle_breakpoint(body.source_id, body.line)


@router.post("/debug/breakpoints/clear")
async def debug_clear_breakpoints(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).debug.clear_breakpoints()


@router.post("/debug/watch")
async def debug_add_watch(body: WatchBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).debug.add_watch(body.expr)


@router.delete("/debug/watch")
async def debug_remove_watch(request: Request, index: int, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).debug.remove_watch(index)


@router.post("/debug/set")
async def debug_set(body: SetVarBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    return await get_bench(request).debug.set_variable(body.name, body.value)


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


# ------------------------------------------------------------------------ USB

class UsbPowerBody(BaseModel):
    location: str
    port: int
    action: str            # on | off | cycle


@router.get("/usb")
async def usb_status(request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    """Lo que hay enchufado por USB y el control de alimentación por puerto (uhubctl)."""
    bench = get_bench(request)
    return await asyncio.get_running_loop().run_in_executor(None, bench.usb_status)


@router.post("/usb/power")
async def usb_power(body: UsbPowerBody, request: Request, _: str = Depends(require_user)) -> Dict[str, Any]:
    bench = get_bench(request)
    return await bench.usb_power_action(body.location, body.port, body.action)


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
                        "gdb": bench.gdb.status(), "serial": bench.serial.status(),
                        "debug": bench.debug.status()})
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
