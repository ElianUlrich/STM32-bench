"""Leer el micro: un trabajo por método que lee la flash con la herramienta de ese
método, la analiza (app/readback.py) y guarda el informe para la pestaña Micro.

  * SWD (OpenOCD): `dump_image` de toda la flash, sin detener el programa, más el
    ID y el número de serie único del chip;
  * USB DFU: `dfu-util -U` de toda la flash (el flujo vive en Bench._prepare_dfu);
  * esptool: el arranque y la tabla de particiones, y de cada aplicación su
    descripción ("identify", rápido) o la partición entera ("full", copia);
  * avrdude: la flash (sin la zona del bootloader) y, con ISP, los fusibles.

La copia de respaldo se guarda en Archivos: un .bin (STM32), un .hex (AVR) o una
carpeta de proyecto ESP-IDF con su flasher_args.json, que se graba con las mismas
protecciones de siempre (NVS se guarda aparte y nunca se graba sola).
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from . import esp_project, readback
from . import files as fileops
from .compat import check_file_for_chip
from .convert import ConversionError, convert_to_bin, to_ihex
from .files import FileValidationError, resolve_firmware
from .programmers import avrdude as avr_cmd, base, esptool as esp_cmd, openocd as ocd_cmd
from .programmers.runner import JobContext, JobFailed, new_job
from .progress import tracker_for

if TYPE_CHECKING:  # pragma: no cover
    from .bench import Bench, JobRequest
    from .chips import Chip

BACKUP_PROJECT = "respaldos"
ESP_HEAD = 0x10000            # arranque + tabla de particiones
APP_HEAD = 0x1000             # comienzo de una aplicación: cabecera y descripción
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
READ_FAILED = ("No se pudo leer la flash: el chip tiene activada la protección de lectura (RDP) o no "
               "respondió. Con la protección activa sólo se puede borrar todo.")
ESP_DATA_KEEP = {0x00: "ota", 0x01: "phy"}     # datos chicos que se incluyen en la copia
ESP_SUBTYPES = {0x00: "ota", 0x01: "phy", 0x02: "nvs", 0x03: "coredump", 0x04: "nvs_keys", 0x05: "efuse",
                0x80: "esphttpd", 0x81: "fat", 0x82: "spiffs", 0x83: "littlefs"}


def _row(label: str, value: str) -> Dict[str, str]:
    return {"label": label, "value": value}


def _file(name: str, label: str, size: int, address: int) -> Dict:
    return {"name": name, "label": label, "size": size, "address": f"0x{address:X}"}


def _safe_name(text: str, fallback: str) -> str:
    name = UNSAFE_NAME.sub("_", text or "").strip("._-")[:60]
    return name if name and name[0].isalnum() else fallback


def _explain(message: str) -> str:
    if "failed to read memory" in message.lower() or "read protection" in message.lower():
        return f"{READ_FAILED} ({message})"
    return message


def _ocd_identity(output: str) -> List[Dict[str, str]]:
    rows = []
    m = re.search(r"(?:Chip verificado: ID|ID del chip conectado:) (0x[0-9A-Fa-f]+)", output)
    if m:
        rows.append(_row("ID del chip", m.group(1)))
    m = re.search(r"^UID: (.+)$", output, re.MULTILINE)
    if m:
        words = [int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b", m.group(1))][:3]
        if len(words) == 3:
            rows.append(_row("Número de serie (UID)", "-".join(f"{w:08X}" for w in words)))
    return rows


def _esp_identity(output: str) -> List[Dict[str, str]]:
    rows = []
    m = re.search(r"^Chip is (.+?) \(revision (v[\d.]+)\)", output, re.MULTILINE)
    if m:
        rows.append(_row("Chip conectado", f"{m.group(1)} (revisión {m.group(2)})"))
    m = re.search(r"^MAC: ([0-9a-fA-F:]{17})", output, re.MULTILINE)
    if m:
        rows.append(_row("MAC", m.group(1)))
    return rows


def _avr_identity(output: str, isp: bool) -> List[Dict[str, str]]:
    rows = []
    m = re.search(r"Device signature = (0x[0-9a-fA-F]+)", output)
    if m:
        rows.append(_row("Firma", m.group(1).lower()))
    fuses = re.findall(r"^(0x[0-9a-fA-F]{2})\s*$", output, re.MULTILINE)
    if isp and len(fuses) >= 3:
        rows.append(_row("Fusibles", f"bajo {fuses[0]} · alto {fuses[1]} · extendido {fuses[2]}"))
    return rows


class ChipReader:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self.store = readback.ReadbackStore(bench.settings.state_dir / "readback")

    # ------------------------------------------------------------------ trabajos
    async def prepare(self, chip: "Chip", req: "JobRequest", method: str):
        if method == "swd":
            return await self._swd(chip, req)
        if method == "dfu":
            return self.bench._prepare_dfu(chip, req, None)
        if method == "esptool":
            return self._esp(chip, req)
        return self._avr(chip, req)

    async def _swd(self, chip: "Chip", req: "JobRequest"):
        from .bench import GdbActiveError
        b = self.bench
        iface = b.interface_for(chip, req.interface)
        serial = b.resolve_probe_serial(req.probe_serial)
        if b.gdb.uses_probe(serial):
            if not req.stop_gdb:
                raise GdbActiveError("El servidor GDB está usando el programador. Detenelo para leer el micro.")
            await b.stop_gdb()
        setup = b.openocd_setup(iface, serial)
        swd = chip.swd
        job = new_job(base.READ, "swd", f"Leer el micro {chip.name} por SWD ({iface.id})", chip=chip.id)
        timeout = b.cfg.openocd_timeout_s

        async def fn(ctx: JobContext) -> str:
            ctx.track(tracker_for("openocd", "read"))
            rid, folder = self.store.create()
            path = folder / "flash.bin"
            try:
                argv = ocd_cmd.read_args(setup, swd, path, chip.flash_base, chip.flash_size)
                result = await ctx.run(argv, source="openocd", timeout_s=timeout)
                data = path.read_bytes()
            except JobFailed as exc:
                self.store.discard(rid)
                raise JobFailed(_explain(str(exc)))
            except BaseException:
                self.store.discard(rid)
                raise
            identity = ([_row("Chip", chip.name)] + _ocd_identity(result.output)
                        + [_row("Flash", f"{chip.flash_size // 1024} KB desde 0x{chip.flash_base:08X}")])
            report = self.flash_report(rid, chip, "swd", data, chip.flash_base, identity)
            ctx.job.result = {"readback": rid}
            return report["summary"]["text"]

        return job, fn

    def _avr(self, chip: "Chip", req: "JobRequest"):
        b = self.bench
        params = chip.avrdude
        port = None if params.is_isp else b.resolve_serial_port(req.serial_port)
        via = "programador ISP" if params.is_isp else f"bootloader ({port})"
        job = new_job(base.READ, "avrdude", f"Leer el micro {chip.name} por avrdude, {via}", chip=chip.id)
        timeout = b.cfg.avrdude_timeout_s
        app_size = params.flash_kb * 1024 - int(params.bootloader_kb * 1024)

        async def run(ctx: JobContext) -> str:
            rid, folder = self.store.create()
            path = folder / "flash.bin"
            try:
                target_port = port
                if params.touch_1200 and not params.is_isp:
                    target_port = await b._touch_1200(ctx, port)
                argv = avr_cmd.read_args(b.avrdude_cmd, params, target_port or "", path)
                result = await ctx.run(argv, source="avrdude", timeout_s=timeout)
                data = path.read_bytes()
            except BaseException:
                self.store.discard(rid)
                raise
            identity = [_row("Chip", chip.name)] + _avr_identity(result.output, params.is_isp)
            report = self.flash_report(rid, chip, "avrdude", data, 0, identity, app_size=app_size)
            ctx.job.result = {"readback": rid}
            return report["summary"]["text"]

        async def fn(ctx: JobContext) -> str:
            ctx.track(tracker_for("avrdude", "read"))
            return await b._run_releasing_port(ctx, port, lambda: run(ctx))

        return job, fn

    def _esp(self, chip: "Chip", req: "JobRequest"):
        from .bench import RequestError
        b = self.bench
        scope = req.read_scope or "identify"
        if scope not in ("identify", "full"):
            raise RequestError("Qué leer: 'identify' (rápido) o 'full' (copia completa)")
        port = b.resolve_serial_port(req.serial_port)
        full = scope == "full"
        job = new_job(base.READ, "esptool", f"Leer el micro {chip.name} por esptool ({port})"
                      + (" — copia completa" if full else ""), chip=chip.id)

        async def steps(ctx: JobContext) -> str:
            rid, folder = self.store.create()
            try:
                return await self._esp_steps(ctx, rid, folder, chip, port, full)
            except BaseException:
                self.store.discard(rid)
                raise

        async def fn(ctx: JobContext) -> str:
            return await b._run_releasing_port(ctx, port, lambda: steps(ctx))

        return job, fn

    async def _esp_steps(self, ctx: JobContext, rid: str, folder: Path, chip: "Chip", port: str, full: bool) -> str:
        b = self.bench
        params = chip.esptool

        async def read(address: int, size: int, name: str, detail: str) -> Tuple[bytes, str]:
            tracker = tracker_for("esptool", "read")
            tracker.detail = detail
            ctx.track(tracker)
            path = folder / name
            result = await ctx.run(esp_cmd.read_args(b.esptool_cmd, params, port, address, size, path),
                                   source="esptool", timeout_s=b.cfg.esptool_timeout_s)
            data = path.read_bytes()
            if name.endswith(".part"):
                path.unlink()
            return data, result.output

        head, output = await read(0, ESP_HEAD, "inicio.bin", "arranque y particiones")
        report = self._new_report(rid, chip, "esptool", "full" if full else "identify",
                                  [_row("Chip", chip.name)] + _esp_identity(output))
        report["files"].append(_file("inicio.bin", "Arranque y tabla de particiones (64 KB)", len(head), 0))
        table_at = readback.find_partition_table(head)
        apps: List[Dict] = []
        partitions: List[Dict] = []
        manifest: List[Dict] = []          # lo que la copia graba, con su dirección y rol
        extra: List[str] = []              # se guarda pero no se graba (NVS)
        heads: Dict[str, bytes] = {}       # lo leído de cada aplicación, para las que no tienen descripción
        cands, last = self.candidates(chip, 0, 0), self.last_flashed(chip.id)

        def keep(name: str, address: int, data: bytes, role: Optional[str], label: str) -> str:
            used = set(f["name"] for f in report["files"])
            stem, n = name[:-4], 2
            while name in used:
                name, n = f"{stem}-{n}.bin", n + 1
            (folder / name).write_bytes(data)
            report["files"].append(_file(name, label, len(data), address))
            if role:
                manifest.append({"name": name, "address": address, "role": role})
            else:
                extra.append(name)
            return name

        if table_at is not None:
            parts = esp_project.parse_partition_table(head[table_at:table_at + readback.PART_TABLE_SIZE])
            for p in parts:
                partitions.append({"name": p.name, "type": "app" if p.type == 0 else "data",
                                   "subtype": ESP_SUBTYPES.get(p.subtype, f"0x{p.subtype:02X}") if p.type else
                                   ("factory" if p.subtype == 0 else f"ota_{p.subtype - 0x10}"),
                                   "offset": f"0x{p.offset:X}", "size": p.size})
            if full:
                boot = head[params.bootloader_offset:table_at]
                if readback.used_length(boot):
                    keep("bootloader.bin", params.bootloader_offset, boot[:readback.image_length(boot)],
                         "bootloader", "Gestor de arranque")
                keep("partition-table.bin", table_at, head[table_at:table_at + readback.PART_TABLE_SIZE],
                     "partition-table", "Tabla de particiones")
            for p in (p for p in parts if p.type == 0):
                data, _ = await read(p.offset, p.size if full else min(APP_HEAD, p.size), f"{p.name}.part",
                                     f"aplicación {p.name}")
                desc = readback.parse_app_desc(data)
                heads[p.name] = data
                apps.append(dict({"partition": p.name, "offset": f"0x{p.offset:X}"},
                                 **(desc or {"project": None, "version": None, "date": None, "time": None,
                                             "idf": None, "sha256": None})))
                if full and readback.used_length(data):
                    role = "app" if not any(m["role"] == "app" for m in manifest) else "data"
                    found = [] if desc else readback.match_apps(apps[-1:], cands, last, heads)
                    title = desc["project"] if desc else (Path(found[0]["path"]).stem if found else "")
                    keep(_safe_name(title, p.name) + ".bin", p.offset,
                         data[:readback.image_length(data)], role, f"Aplicación ({p.name})")
            if full:
                for p in (p for p in parts if p.type == 1):
                    if p.subtype in ESP_DATA_KEEP or p.subtype == 0x02:
                        data, _ = await read(p.offset, p.size, f"{p.name}.part", f"datos {p.name}")
                        if not readback.used_length(data):
                            continue
                        if p.subtype == 0x02:
                            keep(_safe_name(p.name, "nvs") + ".bin", p.offset, data, None,
                                 f"Datos guardados NVS ({p.name}): no se graban con la copia")
                        else:
                            keep(_safe_name(p.name, "datos") + ".bin", p.offset,
                                 data[:readback.round_up(readback.used_length(data), 0x1000)], "data",
                                 f"Datos ({p.name})")
                    else:
                        report["warnings"].append(f"No se leyó la partición de datos '{p.name}' "
                                                  f"({p.size // 1024} KB): no hace falta para volver a grabar.")

        described = [a for a in apps if a.get("sha256")]
        matches = readback.match_apps(apps, cands, last, heads)
        if table_at is None:
            empty = readback.used_length(head) == 0
            summary = readback.summarize([], True) if empty else {
                "state": "unknown", "file": None,
                "text": "No se encontró la tabla de particiones: firmware desconocido o no es de ESP-IDF"}
        elif not matches and described:
            a = described[0]
            summary = {"state": "unknown", "file": None,
                       "text": f"Tiene grabado {a['project']} {a['version']}, que no está entre los archivos "
                               "de esta placa"}
        else:
            summary = readback.summarize(matches, empty=False)
        report.update(summary=summary, matches=matches,
                      esp={"partitions": partitions, "apps": apps,
                           "table": f"0x{table_at:X}" if table_at is not None else None},
                      can_save=full and any(m["role"] == "app" for m in manifest),
                      backup={"manifest": manifest, "extra": extra} if full else None)
        self.store.save(rid, report)
        ctx.job.result = {"readback": rid}
        return summary["text"]

    # ------------------------------------------------------------------ informe
    def _new_report(self, rid: str, chip: "Chip", method: str, scope: str, identity: List[Dict]) -> Dict:
        return {"id": rid, "chip": chip.id, "chip_name": chip.name, "method": method, "scope": scope,
                "time": time.time(), "identity": identity, "summary": None, "firmware": None, "matches": [],
                "esp": None, "files": [], "can_save": False, "warnings": []}

    def flash_report(self, rid: str, chip: "Chip", method: str, data: bytes, base_addr: int,
                     identity: List[Dict], app_size: Optional[int] = None) -> Dict:
        """Informe de una flash leída entera (STM32 por SWD o DFU, AVR)."""
        area = data[:app_size] if app_size else data
        info = readback.analyze_flash(area, base_addr, vectors=chip.avrdude is None)
        matches = readback.find_matches(area, base_addr, self.candidates(chip, base_addr, base_addr + len(area)),
                                        self.last_flashed(chip.id))
        report = self._new_report(rid, chip, method, "full", identity)
        report.update(firmware=info, matches=matches, summary=readback.summarize(matches, info["empty"]),
                      files=[_file("flash.bin", "Flash completa", len(data), base_addr)],
                      can_save=not info["empty"])
        self.store.save(rid, report)
        return report

    def last_flashed(self, chip_id: str) -> Optional[str]:
        """Lo último que se grabó bien en esta placa desde el banco."""
        for entry in self.bench.history.list():
            if entry.get("chip") == chip_id and entry.get("action") == base.FLASH and entry.get("state") == "ok":
                return entry.get("file")
        return None

    def candidates(self, chip: "Chip", base_addr: int, flash_end: int) -> List[readback.Candidate]:
        """Los archivos asignados a la placa, como quedarían en la flash."""
        out = []
        for rel, target in self.bench.meta.all().items():
            if target != chip.id:
                continue
            try:
                path = resolve_firmware(self.bench.settings.projects_dir, rel)
                ext = path.suffix.lower()
                if chip.esptool is not None:
                    if ext == ".bin":
                        data = path.read_bytes()
                        desc = readback.parse_app_desc(data[:APP_HEAD])
                        # Sin descripción se compara por bytes (un compilado viejo o de otra herramienta).
                        out.append(readback.Candidate(rel, 0, b"" if desc else data, desc))
                    continue
                if ext in (".elf", ".hex"):
                    image = convert_to_bin(path, base_addr, flash_end, ext=ext)
                    out.append(readback.Candidate(rel, image.address, image.data))
                elif ext == ".bin":
                    out.append(readback.Candidate(rel, base_addr, path.read_bytes()))
            except (FileValidationError, ConversionError, OSError):
                continue
        return out

    # ------------------------------------------------------------- copia de respaldo
    def save(self, rid: str) -> Dict:
        """Guarda la lectura en Archivos (proyecto "respaldos"), asignada a su placa."""
        report = self.store.get(rid)
        chip = self.bench.chip(report["chip"])
        if not report.get("can_save"):
            raise FileValidationError("Esta lectura no se puede guardar como copia"
                                      + (": hacé una copia completa." if report.get("scope") == "identify" else
                                         ": la flash está vacía."))
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(report["time"]))
        projects = self.bench.settings.projects_dir
        if chip.esptool is not None:
            return self._save_esp(rid, report, chip, stamp)
        data = self.store.file(rid, "flash.bin").read_bytes()
        if chip.avrdude is not None:
            app = data[:chip.avrdude.flash_kb * 1024 - int(chip.avrdude.bootloader_kb * 1024)]
            name = f"{chip.id}-{stamp}.hex"
            content = to_ihex([(0, app[:readback.round_up(readback.used_length(app))])]).encode()
        else:
            name = f"{chip.id}-{stamp}.bin"
            content = data[:readback.round_up(readback.used_length(data))]
        saved = fileops.save_upload(projects, BACKUP_PROJECT, name, io.BytesIO(content), max(len(content), 1),
                                    validator=lambda tmp: check_file_for_chip(tmp, chip, filename=name))
        self.bench.meta.assign(saved.rel_path, chip.id)
        return {"ok": True, "path": saved.rel_path}

    def _save_esp(self, rid: str, report: Dict, chip: "Chip", stamp: str) -> Dict:
        backup = report["backup"]
        images = tuple(sorted((esp_project.Image(m["address"], m["name"], m["role"]) for m in backup["manifest"]),
                              key=lambda i: i.offset))
        manifest = esp_project.Manifest(chip=chip.esptool.chip, flash_mode="keep", flash_freq="keep",
                                        flash_size="keep", images=images)
        app = next(i for i in images if i.role == "app")
        names = [m["name"] for m in backup["manifest"]] + list(backup["extra"])
        items = [(n, io.BytesIO(self.store.file(rid, n).read_bytes())) for n in names]
        folder = _safe_name(app.path[:-4], chip.id) + "-" + stamp
        biggest = max(f["size"] for f in report["files"])
        saved = fileops.save_file_set(
            self.bench.settings.projects_dir, BACKUP_PROJECT, folder, items, max(biggest, 1),
            extra={esp_project.MANIFEST_NAME: esp_project.dump_manifest(manifest)},
            validator=lambda tmp: esp_project.check(manifest, esp_project.reader(tmp), chip.esptool))
        for rel in saved.firmware:
            self.bench.meta.assign(rel, chip.id)
        return {"ok": True, "folder": saved.folder, "path": f"{saved.folder}/{app.path}"}
