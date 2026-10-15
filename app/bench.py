"""Servicio central del banco: une configuración, detección y trabajos.

Toda la validación de pedidos de la interfaz ocurre acá, ANTES de construir
cualquier comando:
  * el chip debe existir en chips.yaml y soportar el método pedido;
  * la interfaz debe estar en bench.yaml;
  * el archivo debe resolverse dentro del directorio de proyectos;
  * el número de serie del ST-Link / dispositivo DFU debe estar entre los detectados;
  * el puerto serie (ESP32 / Arduino) debe estar entre los detectados;
  * las direcciones deben caer dentro de la flash del chip;
  * el archivo debe estar ASIGNADO a la placa elegida y su contenido debe ser
    compatible con ella (así no se graba un binario en el micro equivocado).
"""
from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import mock_state, probe, system, usb
from .bootcontrol import create_boot_control, wait_for_dfu
from .chips import CfgCheck, Chip, check_cfg_files, load_chips
from .compat import Compat, check_file_for_chip
from .convert import ConversionError, convert_to_bin
from .debugger.session import DebugError, DebugSession
from .elfinfo import DebugInfo, DebugInfoError, find_symbol, load_debug_info
from .rtt import RTT_PORT_NAME, RTT_SYMBOL, RttPort
from .events import EventBus
from .files import FileValidationError, resolve_firmware
from .firmware_meta import FirmwareMeta
from .history import History
from .procs import run_capture
from .programmers import avrdude as avr_cmd, base, dfu as dfu_cmd, esptool as esp_cmd, openocd as ocd_cmd
from .programmers.gdbserver import GdbServer, GdbServerBusy, GdbServerError, ServerTarget
from .programmers.runner import BusyError, Job, JobContext, JobFailed, JobRunner, failure_reason, new_job
from .progress import tracker_for
from .livevars import MAX_LIVE, MAX_RATE_HZ, LiveSampler, Variable, list_variables
from .reading import ChipReader
from .relay import TcpRelay, free_local_port
from .serial_monitor import SerialError, SerialManager
from .settings import BenchConfig, ConfigError, InterfaceConfig, Settings, load_bench_config


class RequestError(ValueError):
    """Pedido inválido (HTTP 400) con mensaje para el usuario."""


class GdbActiveError(RuntimeError):
    """Hay un servidor GDB usando el programador (HTTP 409, code=gdb_active)."""


@dataclass
class JobRequest:
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


def parse_address(text: Optional[str]) -> Optional[int]:
    if text is None or str(text).strip() == "":
        return None
    try:
        value = int(str(text).strip(), 0)
    except ValueError:
        raise RequestError(f"Dirección inválida: '{text}' (usar 0x08000000 o decimal)")
    if not 0 <= value <= 0xFFFFFFFF:
        raise RequestError("Dirección fuera del rango de 32 bits")
    return value


class Bench:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config_errors: List[str] = []
        try:
            self.cfg: BenchConfig = load_bench_config(settings.bench_file)
        except ConfigError as exc:
            # Sin bench.yaml válido no se puede operar el hardware, pero la
            # interfaz arranca igual para mostrar el error (la placa es headless).
            self.config_errors.append(str(exc))
            self.cfg = BenchConfig(interfaces={"stlink": InterfaceConfig("stlink", "interface/stlink.cfg", "hla_swd")},
                                   openocd_default_interface="stlink")
        try:
            self.chips: List[Chip] = load_chips(settings.chips_file, self.cfg.interfaces)
        except ConfigError as exc:
            self.config_errors.append(str(exc))
            self.chips = []
        self.chips_by_id: Dict[str, Chip] = {c.id: c for c in self.chips}

        settings.state_dir.mkdir(parents=True, exist_ok=True)
        settings.projects_dir.mkdir(parents=True, exist_ok=True)

        if settings.mock:
            self.openocd_cmd: Tuple[str, ...] = tuple(settings.mock_command("mock_openocd.py"))
            self.dfu_cmd: Tuple[str, ...] = tuple(settings.mock_command("mock_dfu_util.py"))
            self.esptool_cmd: Tuple[str, ...] = tuple(settings.mock_command("mock_esptool.py"))
            self.avrdude_cmd: Tuple[str, ...] = tuple(settings.mock_command("mock_avrdude.py"))
            self.gdb_client_cmd: Tuple[str, ...] = tuple(settings.mock_command("mock_gdb.py"))
            self.env_extra = {"PYTHONUNBUFFERED": "1", mock_state.ENV_VAR: str(settings.state_dir)}
            self.scripts_dir: Optional[str] = None
        else:
            self.openocd_cmd = (self.cfg.openocd_binary,)
            self.dfu_cmd = (self.cfg.dfu_util_binary,)
            # esptool se instala en el mismo entorno virtual que la app.
            self.esptool_cmd = ((self.cfg.esptool_binary,) if self.cfg.esptool_binary
                                else (sys.executable, "-m", "esptool"))
            self.avrdude_cmd = (self.cfg.avrdude_binary,) + (("-C", self.cfg.avrdude_config)
                                                              if self.cfg.avrdude_config else ())
            self.gdb_client_cmd = (self.cfg.debugger_gdb_binary,)
            self.env_extra = {}
            scripts = self.cfg.openocd_scripts()
            self.scripts_dir = str(scripts) if scripts else None

        self.bus = EventBus()
        self.history = History(settings.state_dir / "history.jsonl", self.cfg.history_max_entries)
        self.meta = FirmwareMeta(settings.state_dir / "firmware_meta.json")
        self.reader = ChipReader(self)
        self.runner = JobRunner(self.bus, self.history, self.env_extra)
        self.gdb = GdbServer(self.bus, self.env_extra)
        self.debug = DebugSession(self.bus, self.env_extra)
        self.live = LiveSampler(self.bus)
        self.port_power = usb.PortPower(mock=settings.mock)
        # Mientras se depura, si RTT todavía no encontró el bloque (el programa recién arranca), se reintenta.
        self.debug.rtt_retry = lambda: bool(self.gdb.active and self.gdb.info.get("rtt")
                                            and self.gdb.rtt_state != "encontrado")
        self.serial = SerialManager(self.bus, self.cfg.serial_bauds, self.cfg.serial_default_baud, settings.mock)
        self.boot = create_boot_control(self.cfg.boot_control, self.serial, settings.mock, settings.state_dir)
        self.serial_command = "adapter serial"
        self.versions: Dict[str, Optional[str]] = {"openocd": None, "dfu_util": None,
                                                   "esptool": None, "avrdude": None}
        self.cfg_check = CfgCheck(scripts_dir=self.scripts_dir)
        if settings.mock:
            mock_state.clear_dfu(settings.state_dir)

    # ------------------------------------------------------------------ inicio
    def detect_tools(self) -> None:
        """Versiones y comando de serie de OpenOCD; chequeo de .cfg. (bloqueante)"""
        self.versions["openocd"] = _first_line_of(ocd_cmd.version_args(self.openocd_cmd), self.env_extra)
        self.versions["dfu_util"] = _first_line_of(dfu_cmd.version_args(self.dfu_cmd), self.env_extra)
        self.versions["esptool"] = _first_line_of(list(self.esptool_cmd) + ["version"], self.env_extra,
                                                  contains="esptool")
        self.versions["avrdude"] = _first_line_of(list(self.avrdude_cmd) + ["-?"], self.env_extra,
                                                  contains="ersion")
        try:
            out = run_capture(ocd_cmd.serial_probe_args(self.openocd_cmd, self.scripts_dir),
                              timeout=15, env_extra=self.env_extra)
            text = out.stdout + out.stderr
            if "adapter serial" in text:
                self.serial_command = "adapter serial"
            elif "hla_serial" in text:
                self.serial_command = "hla_serial"
        except Exception:
            pass
        if self.settings.mock:
            self.cfg_check = CfgCheck(scripts_dir="(mock)", checked=True)
        else:
            self.cfg_check = check_cfg_files(self.chips, self.cfg.interfaces,
                                             Path(self.scripts_dir) if self.scripts_dir else None)

    # --------------------------------------------------------------- consultas
    def chip(self, chip_id: str) -> Chip:
        chip = self.chips_by_id.get(chip_id)
        if chip is None:
            raise RequestError(f"Chip desconocido: '{chip_id}'")
        return chip

    def interface_for(self, chip: Chip, requested: Optional[str]) -> InterfaceConfig:
        iid = requested or (chip.swd.interface if chip.swd else None) or self.cfg.openocd_default_interface
        iface = self.cfg.interfaces.get(iid)
        if iface is None:
            raise RequestError(f"Interfaz desconocida: '{iid}'")
        return iface

    def list_stlinks(self) -> List[probe.StLink]:
        return probe.mock_stlinks() if self.settings.mock else probe.list_stlinks()

    async def list_dfu(self) -> List[dfu_cmd.DfuDevice]:
        return await asyncio.get_running_loop().run_in_executor(
            None, probe.list_dfu_devices, self.dfu_cmd, self.env_extra)

    def resolve_probe_serial(self, requested: Optional[str]) -> Optional[str]:
        """Valida el ST-Link elegido contra los detectados."""
        probes = self.list_stlinks()
        serials = [p.serial for p in probes]
        if requested:
            if requested not in serials:
                raise RequestError("El ST-Link elegido no está conectado (actualizá la lista)")
            return requested
        if len(probes) > 1:
            raise RequestError(f"Hay {len(probes)} ST-Link conectados: elegí uno en la lista")
        return serials[0] if serials else None

    def openocd_setup(self, iface: InterfaceConfig, serial: Optional[str]) -> ocd_cmd.OpenOcdSetup:
        return ocd_cmd.OpenOcdSetup(command=self.openocd_cmd, scripts_dir=self.scripts_dir,
                                    interface=iface, serial=serial, serial_command=self.serial_command)

    # ------------------------------------------------------------------ trabajos
    async def start_job(self, req: JobRequest) -> Job:
        chip = self.chip(req.chip)
        try:
            method = base.select_method(chip, req.method)
            base.check_action(method, req.action, chip)
        except base.MethodError as exc:
            raise RequestError(str(exc))
        if self.runner.busy:
            raise BusyError(f"El programador está ocupado ({self.runner.current.description})")

        firmware: Optional[Path] = None
        compat: Optional[Compat] = None
        if req.action in base.NEEDS_FILE:
            if not req.file:
                raise RequestError("Elegí un archivo de firmware")
            try:
                firmware = resolve_firmware(self.settings.projects_dir, req.file)
            except FileValidationError as exc:
                raise RequestError(str(exc))
            compat = self.check_binding(req.file, firmware, chip,
                                        esp_address=parse_address(req.address) if chip.esptool else None)

        if req.action == base.READ:
            job, fn = await self.reader.prepare(chip, req, method)
        elif method == "swd":
            job, fn = await self._prepare_swd(chip, req, firmware)
        elif method == "dfu":
            job, fn = self._prepare_dfu(chip, req, firmware)
        elif method == "esptool":
            job, fn = self._prepare_esptool(chip, req, firmware, compat)
        else:
            job, fn = self._prepare_avrdude(chip, req, firmware)
        if compat is not None:
            job.warnings.extend(compat.warnings)
        return self.runner.start(job, fn)

    # ------------------------------------------------------- vínculo archivo/placa
    def chip_name(self, chip_id: Optional[str]) -> str:
        chip = self.chips_by_id.get(chip_id or "")
        return chip.name if chip else (chip_id or "?")

    def check_binding(self, rel_path: str, firmware: Path, chip: Chip,
                      esp_address: Optional[int] = None) -> Compat:
        """El archivo tiene que estar asignado a esta placa y ser compatible con ella."""
        assigned = self.meta.target_of(rel_path)
        if assigned is None:
            raise RequestError(
                "Este archivo no tiene placa asignada. Asignale su placa en la pestaña Archivos "
                "antes de grabarlo (así no se graba en el micro equivocado).")
        if assigned != chip.id:
            raise RequestError(
                f"Este archivo es para {self.chip_name(assigned)}, no para {chip.name}. "
                "Elegí esa placa o reasigná el archivo.")
        try:
            return check_file_for_chip(firmware, chip, esp_address=esp_address,
                                       projects_dir=self.settings.projects_dir)
        except FileValidationError as exc:
            raise RequestError(str(exc))

    def assign_target(self, rel_path: str, target: str) -> Compat:
        """Asigna un archivo existente a una placa (verificando que sea compatible)."""
        chip = self.chip(target)
        try:
            firmware = resolve_firmware(self.settings.projects_dir, rel_path)
            compat = check_file_for_chip(firmware, chip, projects_dir=self.settings.projects_dir)
        except FileValidationError as exc:
            raise RequestError(str(exc))
        self.meta.assign(rel_path, chip.id)
        return compat

    # ----------------------------------------------------------- puertos serie
    def resolve_serial_port(self, requested: Optional[str]) -> str:
        ports = [p["device"] for p in self.serial.list_ports()]
        if requested:
            if requested not in ports:
                raise RequestError("El puerto serie elegido no está conectado (actualizá la lista)")
            return requested
        if len(ports) == 1:
            return ports[0]
        if not ports:
            raise RequestError("No hay ningún puerto serie USB conectado a la placa")
        raise RequestError(f"Hay {len(ports)} puertos serie: elegí el de la placa")

    async def _run_releasing_port(self, ctx: JobContext, port: Optional[str], coro_factory):
        """Cierra el monitor serie si usa `port`, ejecuta y lo vuelve a abrir."""
        status = self.serial.status()
        reopen = status["open"] and port is not None and status["port"] == port
        if reopen:
            ctx.log(f"Se cierra el monitor serie ({port}) mientras se programa")
            await asyncio.get_running_loop().run_in_executor(None, self.serial.close)
        try:
            return await coro_factory()
        finally:
            if reopen:
                await asyncio.sleep(1.0)   # la placa se reinicia y el puerto puede tardar en volver
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, self.serial.open, port, status["baud"])
                    ctx.log(f"Monitor serie reabierto ({port} @ {status['baud']})")
                except SerialError as exc:
                    ctx.log(f"No se pudo reabrir el monitor serie: {exc}", level="warn")

    def _check_bin_range(self, chip: Chip, address: int, size: int) -> None:
        if address < chip.flash_base or address + size > chip.flash_end:
            raise RequestError(
                f"La imagen (0x{address:08X}, {size} bytes) no entra en la flash de {chip.name} "
                f"(0x{chip.flash_base:08X}-0x{chip.flash_end - 1:08X})")

    async def _prepare_swd(self, chip: Chip, req: JobRequest, firmware: Optional[Path]):
        assert chip.swd is not None
        iface = self.interface_for(chip, req.interface)
        serial = self.resolve_probe_serial(req.probe_serial)
        if self.gdb.uses_probe(serial):
            if not req.stop_gdb:
                raise GdbActiveError(
                    "El servidor GDB está usando el programador. Detenelo para continuar "
                    "(o, desde GDB, usá 'monitor reset halt' / 'monitor resume').")
            await self.stop_gdb()

        address = None
        if firmware is not None and firmware.suffix.lower() == ".bin":
            address = parse_address(req.address)
            if address is None:
                address = chip.flash_base
            self._check_bin_range(chip, address, firmware.stat().st_size)

        setup = self.openocd_setup(iface, serial)
        swd = chip.swd
        action = req.action
        try:
            if action == base.FLASH:
                argv = ocd_cmd.flash_args(setup, swd, firmware, address)
            elif action == base.VERIFY:
                argv = ocd_cmd.verify_args(setup, swd, firmware, address)
            elif action == base.ERASE:
                argv = ocd_cmd.erase_args(setup, swd)
            elif action == base.RESET:
                argv = ocd_cmd.reset_args(setup, swd)
            elif action == base.HALT:
                argv = ocd_cmd.halt_args(setup, swd)
            elif action == base.INFO:
                argv = ocd_cmd.info_args(setup, swd)
            else:
                argv = ocd_cmd.resume_args(setup, swd)
        except (ocd_cmd.CommandError, FileValidationError) as exc:
            raise RequestError(str(exc))

        rel = req.file if firmware else None
        desc = f"{base.ACTION_LABELS[action]} {chip.name} por SWD ({iface.id})"
        if rel:
            desc += f" — {Path(rel).name}"
        if address is not None:
            desc += f" @ 0x{address:08X}"
        job = new_job(action, "swd", desc, chip=chip.id, file=rel)
        timeout = self.cfg.openocd_timeout_s
        messages = {base.FLASH: "Flasheado, verificado y reseteado", base.VERIFY: "Verificación OK",
                    base.ERASE: "Flash borrada", base.RESET: "Reset enviado",
                    base.HALT: "Núcleo detenido (halt)", base.RESUME: "Núcleo en ejecución (run)",
                    base.INFO: "Chip identificado (ver el log)"}

        async def fn(ctx: JobContext) -> str:
            ctx.track(tracker_for("openocd", action))
            await ctx.run(argv, source="openocd", timeout_s=timeout)
            return messages[action]

        return job, fn

    def _prepare_dfu(self, chip: Chip, req: JobRequest, firmware: Optional[Path]):
        assert chip.dfu is not None
        dfu = chip.dfu
        action = req.action
        requested_serial = req.dfu_serial or None
        if requested_serial and not dfu_cmd.DFU_SERIAL_RE.fullmatch(requested_serial):
            raise RequestError("Número de serie DFU inválido")

        address = None
        if firmware is not None and firmware.suffix.lower() == ".bin":
            address = parse_address(req.address)
            if address is None:
                address = dfu.address
            self._check_bin_range(chip, address, firmware.stat().st_size)

        rel = req.file if firmware else None
        desc = f"{base.ACTION_LABELS[action]} {chip.name} por USB DFU"
        if rel:
            desc += f" — {Path(rel).name}"
        job = new_job(action, "dfu", desc, chip=chip.id, file=rel)
        boot = self.boot
        timeout = self.cfg.dfu_util_timeout_s
        leave = bool(req.leave)

        def match(dev: dfu_cmd.DfuDevice) -> bool:
            return dev.vid_pid == dfu.vid_pid and (requested_serial is None or dev.serial == requested_serial)

        async def fn(ctx: JobContext) -> str:
            ctx.track(tracker_for("dfu-util", action))
            with tempfile.TemporaryDirectory(prefix="stm32bench-") as tmp:
                tmpdir = Path(tmp)
                # 1) Imagen binaria
                bin_path, bin_addr = firmware, address
                if firmware is not None and firmware.suffix.lower() != ".bin":
                    ctx.log(f"Convirtiendo {firmware.name} a .bin para dfu-util")
                    try:
                        image = await asyncio.get_running_loop().run_in_executor(
                            None, convert_to_bin, firmware, chip.flash_base, chip.flash_end)
                    except ConversionError as exc:
                        raise JobFailed(str(exc))
                    for warning in image.warnings:
                        ctx.warn(warning)
                    bin_path = tmpdir / (firmware.stem + ".bin")
                    bin_path.write_bytes(image.data)
                    bin_addr = image.address
                    ctx.log(f"Imagen: {len(image.data)} bytes desde 0x{image.address:08X} "
                            f"({len(image.segments)} bloque(s))")

                # 2) Dispositivo DFU (entrar al bootloader si hace falta)
                touched = False
                left = False
                try:
                    present = [d for d in await self.list_dfu() if match(d)]
                    if len(present) > 1 and requested_serial is None:
                        raise JobFailed(f"Hay {len(present)} dispositivos DFU: elegí uno por número de serie")
                    if present:
                        device = present[0]
                        ctx.log("El dispositivo ya está en modo DFU")
                    else:
                        ctx.log(f"Entrando al bootloader: {boot.describe()}")
                        touched = True
                        await boot.enter(ctx)
                        device = await wait_for_dfu(ctx, self.list_dfu, match, self.cfg.boot_control.timeout_s)
                        ctx.close_prompt()
                    touched = True
                    ctx.log(f"Dispositivo DFU {device.vid_pid} serie {device.serial or '?'} en {device.path}")
                    target = dfu_cmd.DfuTarget(command=self.dfu_cmd, vid_pid=dfu.vid_pid, alt=dfu.alt,
                                               serial=device.serial or None)

                    # 3) Operación
                    if action == base.FLASH:
                        result = await ctx.run(dfu_cmd.download_args(target, bin_addr, bin_path, leave),
                                               source="dfu-util", timeout_s=timeout, check=False)
                        if result.returncode != 0:
                            if leave and dfu_cmd.leave_error_is_benign(result.output):
                                ctx.log("dfu-util devolvió error al salir del bootloader, pero la "
                                        "descarga se completó (normal con :leave)", level="warn")
                            else:
                                reason = failure_reason(result.output)
                                raise JobFailed(f"dfu-util falló: {reason}" if reason
                                                else f"dfu-util terminó con código {result.returncode}")
                        left = leave
                        message = "Flasheado por DFU" + (" y en ejecución" if leave else "")
                    elif action == base.VERIFY:
                        size = bin_path.stat().st_size
                        readback = tmpdir / "readback.bin"
                        await ctx.run(dfu_cmd.upload_args(target, bin_addr, size, readback),
                                      source="dfu-util", timeout_s=timeout)
                        _compare(bin_path.read_bytes(), readback.read_bytes() if readback.exists() else b"", bin_addr)
                        message = "Verificación OK"
                    elif action == base.READ:
                        rid, folder = self.reader.store.create()
                        flash_path = folder / "flash.bin"
                        try:
                            await ctx.run(dfu_cmd.upload_args(target, dfu.address, chip.flash_size, flash_path),
                                          source="dfu-util", timeout_s=timeout)
                            data = flash_path.read_bytes()
                        except BaseException:
                            self.reader.store.discard(rid)
                            raise
                        identity = [{"label": "Chip", "value": chip.name},
                                    {"label": "Dispositivo DFU",
                                     "value": f"{device.vid_pid} · serie {device.serial or '?'}"},
                                    {"label": "Flash",
                                     "value": f"{chip.flash_size // 1024} KB desde 0x{dfu.address:08X}"}]
                        report = self.reader.flash_report(rid, chip, "dfu", data, dfu.address, identity)
                        ctx.job.result = {"readback": rid}
                        message = report["summary"]["text"]
                    else:
                        await ctx.run(dfu_cmd.mass_erase_args(target, dfu.address),
                                      source="dfu-util", timeout_s=timeout)
                        message = "Flash borrada"

                    if not left and boot.needs_dfu_leave and action in (base.VERIFY, base.READ):
                        result = await ctx.run(dfu_cmd.leave_args(target, dfu.address),
                                               source="dfu-util", timeout_s=30, check=False)
                        left = result.returncode == 0
                    return message
                finally:
                    if touched:
                        try:
                            await boot.exit(ctx, left)
                        except Exception as exc:
                            ctx.log(f"No se pudo devolver el chip a la aplicación: {exc}", level="error")

        return job, fn

    # ------------------------------------------------------------ ESP (esptool)
    def _prepare_esptool(self, chip: Chip, req: JobRequest, firmware: Optional[Path],
                         compat: Optional[Compat]):
        assert chip.esptool is not None
        params = chip.esptool
        port = self.resolve_serial_port(req.serial_port)
        action = req.action
        cmd = self.esptool_cmd
        address = compat.esp.address if (compat is not None and compat.esp is not None) else None
        project = compat.esp_project if compat is not None else None
        scope = req.esp_scope or "file"
        if scope not in ("file", "all") or (scope == "all" and project is None):
            raise RequestError("Grabar todo el proyecto sólo se puede con un proyecto ESP-IDF (flasher_args.json)")
        pairs: List[Tuple[int, Path]] = []
        if project is not None:
            folder, manifest, image = project
            chosen = manifest.images if scope == "all" else (image,)
            pairs = [(i.offset, folder.joinpath(*i.path.split("/"))) for i in chosen]
        try:
            if project is not None and action in (base.FLASH, base.VERIFY):
                settings = (manifest.flash_mode, manifest.flash_freq, manifest.flash_size)
                argv = (esp_cmd.write_files_args(cmd, params, port, pairs, *settings) if action == base.FLASH
                        else esp_cmd.verify_files_args(cmd, params, port, pairs))
            elif action == base.FLASH:
                argv = esp_cmd.flash_args(cmd, params, port, firmware, address)
            elif action == base.VERIFY:
                argv = esp_cmd.verify_args(cmd, params, port, firmware, address)
            elif action == base.ERASE:
                argv = esp_cmd.erase_args(cmd, params, port)
            elif action == base.RESET:
                argv = esp_cmd.reset_args(cmd, params, port)
            else:
                argv = esp_cmd.info_args(cmd, params, port)
        except esp_cmd.EspImageError as exc:
            raise RequestError(str(exc))

        rel = req.file if firmware else None
        desc = f"{base.ACTION_LABELS[action]} {chip.name} por esptool ({port})"
        if rel:
            desc += f" — {Path(rel).name}"
        if pairs and action in (base.FLASH, base.VERIFY):
            if scope == "all":
                desc += f" (proyecto completo, {len(pairs)} archivos)"
            desc += " @ " + ", ".join(f"0x{a:X}" for a, _ in pairs)
        elif address is not None:
            desc += f" @ 0x{address:X}"
        job = new_job(action, "esptool", desc, chip=chip.id, file=rel)
        timeout = self.cfg.esptool_timeout_s
        messages = {base.FLASH: "Grabado y verificado (hash OK); el chip se reinició",
                    base.VERIFY: "Verificación OK", base.ERASE: "Flash borrada",
                    base.RESET: "Reset enviado", base.INFO: "Chip identificado (ver el log)"}
        kind = compat.esp.kind if (compat is not None and compat.esp is not None) else None

        if pairs:
            sizes: Optional[List[int]] = [path.stat().st_size for _, path in pairs]
        else:
            sizes = [firmware.stat().st_size] if firmware is not None else None

        async def fn(ctx: JobContext) -> str:
            ctx.track(tracker_for("esptool", action, sizes))
            if pairs and action in (base.FLASH, base.VERIFY):
                for a, path in pairs:
                    ctx.log(f"0x{a:06X}  {path.relative_to(folder).as_posix()}")
            elif kind:
                ctx.log(f"Imagen {kind}: se graba en 0x{address:X}")
            await self._run_releasing_port(
                ctx, port, lambda: ctx.run(argv, source="esptool", timeout_s=timeout))
            return messages[action]

        return job, fn

    # ---------------------------------------------------------- Arduino (avrdude)
    def _prepare_avrdude(self, chip: Chip, req: JobRequest, firmware: Optional[Path]):
        assert chip.avrdude is not None
        params = chip.avrdude
        port = None if params.is_isp else self.resolve_serial_port(req.serial_port)
        action = req.action
        cmd = self.avrdude_cmd

        def build(target_port: Optional[str]) -> List[str]:
            p = target_port or ""
            if action == base.FLASH:
                return avr_cmd.flash_args(cmd, params, p, firmware)
            if action == base.VERIFY:
                return avr_cmd.verify_args(cmd, params, p, firmware)
            if action == base.ERASE:
                return avr_cmd.erase_args(cmd, params, p)
            return avr_cmd.info_args(cmd, params, p)

        try:
            build(port)   # valida los argumentos antes de aceptar el trabajo
        except avr_cmd.AvrHexError as exc:
            raise RequestError(str(exc))

        via = "programador ISP" if params.is_isp else f"bootloader ({port})"
        rel = req.file if firmware else None
        desc = f"{base.ACTION_LABELS[action]} {chip.name} por avrdude, {via}"
        if rel:
            desc += f" — {Path(rel).name}"
        job = new_job(action, "avrdude", desc, chip=chip.id, file=rel)
        timeout = self.cfg.avrdude_timeout_s
        messages = {base.FLASH: "Grabado y verificado", base.VERIFY: "Verificación OK",
                    base.ERASE: "Flash borrada", base.RESET: "Reset enviado",
                    base.INFO: "Chip identificado: la firma coincide con la placa"}

        async def run(ctx: JobContext) -> None:
            if action == base.RESET and not params.is_isp:
                await self._avr_reset(ctx, port, params)
                return
            target_port = port
            if params.touch_1200 and not params.is_isp:
                target_port = await self._touch_1200(ctx, port)
            await ctx.run(build(target_port), source="avrdude", timeout_s=timeout)

        async def fn(ctx: JobContext) -> str:
            ctx.track(tracker_for("avrdude", action))
            await self._run_releasing_port(ctx, port, lambda: run(ctx))
            return messages[action]

        return job, fn

    async def _touch_1200(self, ctx: JobContext, port: str) -> str:
        """Leonardo/Micro: abrir el puerto a 1200 baud hace que entren al bootloader,
        que aparece como un puerto serie nuevo durante unos 8 segundos."""
        before = {p["device"] for p in self.serial.list_ports()}
        ctx.log(f"Reset por 1200 baud en {port} para entrar al bootloader")
        if self.settings.mock:
            await ctx.sleep(0.5)
            return port
        await asyncio.get_running_loop().run_in_executor(None, self.serial.touch_1200, port)
        deadline = time.monotonic() + 8
        now = before
        while time.monotonic() < deadline:
            await ctx.sleep(0.3)
            now = {p["device"] for p in self.serial.list_ports()}
            new = sorted(now - before)
            if new:
                await ctx.sleep(0.5)
                ctx.log(f"Bootloader en {new[0]}")
                return new[0]
        if port in now:
            return port
        raise JobFailed("No apareció el puerto del bootloader después del reset a 1200 baud")

    async def _avr_reset(self, ctx: JobContext, port: str, params) -> None:
        if params.touch_1200:
            ctx.log("Reset por 1200 baud: entra al bootloader y a los ~8 s arranca el programa")
            if not self.settings.mock:
                await asyncio.get_running_loop().run_in_executor(None, self.serial.touch_1200, port)
            return
        ctx.log(f"Pulso de DTR en {port} (reinicia el Arduino)")
        if not self.settings.mock:
            await asyncio.get_running_loop().run_in_executor(None, self.serial.pulse_reset, port)

    # --------------------------------------------------------------- servidor GDB
    def gdb_bind_ip(self) -> Tuple[str, Optional[str]]:
        """(IP donde escucha OpenOCD, IP que se muestra para conectarse)."""
        bind = self.cfg.gdb_bind
        ts_ip = "100.101.102.103" if self.settings.mock else system.tailscale_ipv4(self.cfg.tailscale_interface)
        if bind == "tailscale":
            if self.settings.mock:
                return "127.0.0.1", ts_ip
            if not ts_ip:
                raise RequestError(f"No se encontró la interfaz {self.cfg.tailscale_interface}: "
                                   "¿está Tailscale levantado? (o cambiá gdb.bind en bench.yaml)")
            return ts_ip, ts_ip
        if bind == "all":
            others = [a["ip"] for a in system.list_ipv4()]
            return "0.0.0.0", ts_ip or (others[0] if others else None)
        if bind == "localhost":
            return "127.0.0.1", "127.0.0.1"
        return bind, bind

    def rtt_config(self, rel_path: Optional[str]) -> Optional[ocd_cmd.RttConfig]:
        """RTT del .elf (si tiene el bloque _SEGGER_RTT); None si no es un .elf con RTT."""
        if not rel_path:
            return None
        try:
            elf = resolve_firmware(self.settings.projects_dir, rel_path)
        except FileValidationError:
            return None
        if elf.suffix.lower() != ".elf":
            return None
        sym = find_symbol(elf, RTT_SYMBOL)
        if sym is None:
            return None
        return ocd_cmd.RttConfig(address=sym[0], size=max(sym[1], 24), port=self.cfg.rtt_port)

    async def start_gdb(self, chip_id: str, interface: Optional[str], probe_serial: Optional[str],
                        rtt: Optional[ocd_cmd.RttConfig] = None) -> Dict[str, Any]:
        chip = self.chip(chip_id)
        if chip.swd is None:
            raise RequestError(base.DFU_UNSUPPORTED_REASON)
        if self.runner.busy:
            raise BusyError("Hay una operación en curso; esperá a que termine")
        iface = self.interface_for(chip, interface)
        serial = self.resolve_probe_serial(probe_serial)
        bind_ip, show_ip = self.gdb_bind_ip()
        port = self.cfg.gdb_port
        # OpenOCD escucha sólo en 127.0.0.1 (con su puerto TCL, para muestrear variables);
        # a la red salen el puerto GDB y el de RTT por reenvío.
        local = {"gdb": free_local_port(), "tcl": free_local_port()}
        local_rtt = None
        if rtt is not None:
            local["rtt"] = free_local_port()
            local_rtt = dataclasses.replace(rtt, port=local["rtt"])
        try:
            argv = ocd_cmd.gdb_server_args(self.openocd_setup(iface, serial), chip.swd, local["gdb"], "127.0.0.1",
                                           rtt=local_rtt, tcl_port=local["tcl"])
            ipaddress.ip_address(bind_ip)
        except (ocd_cmd.CommandError, ValueError) as exc:
            raise RequestError(str(exc))
        relays = [TcpRelay(bind_ip, port, local["gdb"])]
        info = {"chip": chip.id, "chip_name": chip.name, "interface": iface.id, "probe_serial": serial,
                "port": port, "bind_ip": bind_ip, "local": local,
                "connect": f"{show_ip}:{port}" if show_ip else f"<IP de la placa>:{port}"}
        if rtt is not None:
            relays.append(TcpRelay(bind_ip, rtt.port, local["rtt"]))
            info["rtt"] = {"address": f"0x{rtt.address:08X}", "port": rtt.port,
                           "connect": f"{show_ip}:{rtt.port}" if show_ip else None}
        try:
            return await self.gdb.start(argv, info, relays=relays)
        except GdbServerError as exc:
            raise RequestError(str(exc))

    async def stop_gdb(self) -> Dict[str, Any]:
        """Detiene el servidor GDB (y antes, la depuración y los gráficos de la web que lo usan)."""
        await self.live.stop()
        await self.debug.stop()
        return await self.gdb.stop()

    # ------------------------------------------------------ variables en vivo
    def live_variables(self, rel_path: str) -> Tuple[Path, List[Variable]]:
        """Las variables que se pueden graficar de un .elf (bloqueante)."""
        try:
            elf = resolve_firmware(self.settings.projects_dir, rel_path)
        except FileValidationError as exc:
            raise RequestError(str(exc))
        if elf.suffix.lower() != ".elf":
            raise RequestError("Para graficar variables hace falta el .elf (trae sus nombres, tipos y direcciones)")
        variables = list_variables(elf)
        if not variables:
            raise RequestError(f"{elf.name} no tiene variables globales con información de depuración "
                               "(compilá con -g)")
        return elf, variables

    async def start_live(self, chip_id: str, interface: Optional[str], probe_serial: Optional[str],
                         rel_path: str, names: List[str], rate_hz: int) -> Dict[str, Any]:
        """Empieza a muestrear variables con el micro corriendo, por el OpenOCD compartido."""
        chip = self.chip(chip_id)
        if chip.swd is None:
            raise RequestError("Los gráficos de variables son para STM32 por SWD (ST-Link)")
        if self.runner.busy:
            raise BusyError("Hay una operación en curso; esperá a que termine")
        if not names:
            raise RequestError("Elegí al menos una variable")
        if len(names) > MAX_LIVE:
            raise RequestError(f"Se pueden graficar hasta {MAX_LIVE} variables a la vez")
        if not 1 <= int(rate_hz) <= MAX_RATE_HZ:
            raise RequestError(f"Muestras por segundo: de 1 a {MAX_RATE_HZ}")
        loop = asyncio.get_running_loop()
        elf, variables = await loop.run_in_executor(None, self.live_variables, rel_path)
        self.check_binding(rel_path, elf, chip)
        by_name = {v.name: v for v in variables}
        missing = [n for n in names if n not in by_name]
        if missing:
            raise RequestError(f"{elf.name} no tiene la variable '{missing[0]}'")
        await self.live.stop()
        # Si hay que arrancar OpenOCD, con RTT si el programa lo usa (así abrir RTT después no lo reinicia).
        rtt = None if self.gdb.active else await loop.run_in_executor(None, self.rtt_config, rel_path)
        await self.ensure_server(chip, interface, probe_serial, rtt)
        endpoint = self.gdb.tcl_endpoint()
        if endpoint is None:
            raise RequestError("OpenOCD no tiene el puerto para leer variables: detené el servidor GDB y probá de nuevo")
        return await self.live.start(endpoint, [by_name[n] for n in names], int(rate_hz),
                                     {"chip": chip.id, "chip_name": chip.name, "elf": rel_path})

    # ------------------------------------------------------------- depurador web
    def debug_elf(self, rel_path: str) -> Tuple[Path, DebugInfo]:
        """Valida el .elf a depurar y lee sus fuentes (bloqueante)."""
        try:
            elf = resolve_firmware(self.settings.projects_dir, rel_path)
        except FileValidationError as exc:
            raise RequestError(str(exc))
        if elf.suffix.lower() != ".elf":
            raise RequestError("Para depurar hace falta el .elf (trae los símbolos y las líneas del código)")
        try:
            return elf, load_debug_info(self.settings.projects_dir, elf)
        except DebugInfoError as exc:
            raise RequestError(str(exc))

    async def start_debug(self, chip_id: str, interface: Optional[str], probe_serial: Optional[str],
                          rel_path: str, mode: str) -> Dict[str, Any]:
        chip = self.chip(chip_id)
        if chip.swd is None:
            raise RequestError("La depuración en la web es sólo para STM32 por SWD (ST-Link)")
        if self.runner.busy:
            raise BusyError("Hay una operación en curso; esperá a que termine")
        if self.debug.active:
            raise RequestError("Ya hay una sesión de depuración: detenela primero")
        loop = asyncio.get_running_loop()
        elf, info = await loop.run_in_executor(None, self.debug_elf, rel_path)
        # El .elf tiene que estar asignado a esta placa y ser compatible (sobre todo si se graba).
        self.check_binding(rel_path, elf, chip)
        rtt = await loop.run_in_executor(None, self.rtt_config, rel_path)
        await self.ensure_server(chip, interface, probe_serial, rtt)
        host, port, _ = self.gdb.local_endpoint()
        argv = list(self.gdb_client_cmd) + ["--interpreter=mi2", "--nx", "--quiet"]
        try:
            return await self.debug.start(gdb_argv=argv, elf=elf, elf_rel=rel_path, debuginfo=info,
                                          host=host, port=port,
                                          chip_name=chip.name, idcode=chip.swd.idcode, mode=mode)
        except DebugError as exc:
            raise RequestError(str(exc))

    async def ensure_server(self, chip: Chip, interface: Optional[str], probe_serial: Optional[str],
                            rtt: Optional[ocd_cmd.RttConfig]) -> Dict[str, Any]:
        """El OpenOCD compartido (servidor GDB, Depurar y RTT) listo para este chip, ST-Link y RTT.
        Lo reutiliza si sirve; si no, lo reinicia, salvo que alguien lo esté usando (409)."""
        want = ServerTarget(chip=chip.id, probe_serial=self.resolve_probe_serial(probe_serial),
                            rtt_address=f"0x{rtt.address:08X}" if rtt else None,
                            rtt_port=rtt.port if rtt else None)
        try:
            return await self.gdb.ensure(want, lambda: self.start_gdb(chip.id, interface, probe_serial, rtt=rtt),
                                         used_by_debugger=self.debug.active)
        except GdbServerBusy as exc:
            raise BusyError(str(exc))
        except GdbServerError as exc:
            raise RequestError(str(exc))

    # ---------------------------------------------------------------------- RTT
    async def open_rtt(self, chip_id: str, interface: Optional[str], probe_serial: Optional[str],
                       rel_path: str) -> Dict[str, Any]:
        """Abre el canal RTT del programa en el monitor serie (inicia OpenOCD con RTT si hace falta)."""
        chip = self.chip(chip_id)
        if chip.swd is None:
            raise RequestError("RTT es para STM32 por SWD (ST-Link)")
        if self.runner.busy:
            raise BusyError("Hay una operación en curso; esperá a que termine")
        loop = asyncio.get_running_loop()
        rtt = await loop.run_in_executor(None, self.rtt_config, rel_path)
        if rtt is None:
            raise RequestError("Este archivo no usa RTT: hace falta el .elf de un programa con RTT (el símbolo "
                               "_SEGGER_RTT, de SEGGER RTT o de firmware/h503_leds/src/rtt.c).")
        await self.ensure_server(chip, interface, probe_serial, rtt)
        host, _, rtt_port = self.gdb.local_endpoint()
        try:
            await loop.run_in_executor(None, self.serial.open_custom, RTT_PORT_NAME,
                                       lambda: RttPort(host, rtt_port), "RTT por el ST-Link")
        except SerialError as exc:
            raise RequestError(str(exc))
        if self.gdb.rtt_state == "no_encontrado":
            self.bus.publish({"type": "serial", "meta": True,
                              "data": "\n--- RTT: no se encontró el bloque de control. ¿Está corriendo este "
                                      "programa en el micro? Grabalo y volvé a abrir RTT. ---\n"})
        return {"serial": self.serial.status(), "gdb": self.gdb.status()}

    # ---------------------------------------------------------------------- USB
    def usb_devices(self) -> List[usb.UsbDevice]:
        return usb.mock_devices() if self.settings.mock else usb.list_devices()

    def usb_status(self) -> Dict[str, Any]:
        devices = self.usb_devices()
        return {"devices": [d.to_public() for d in devices], "power": self.port_power.status(devices)}

    async def usb_power_action(self, location: str, port: int, action: str) -> Dict[str, Any]:
        """Apaga, prende o reinicia un puerto USB (nunca uno que lleve a la red)."""
        if self.runner.busy:
            raise BusyError("Hay una operación en curso: esperá a que termine antes de cortar un USB")
        loop = asyncio.get_running_loop()
        devices = await loop.run_in_executor(None, self.usb_devices)
        names = {"on": "Prender", "off": "Apagar", "cycle": "Reiniciar"}
        try:
            cmd = await loop.run_in_executor(None, self.port_power.act, location, port, action, devices)
        except usb.PowerError as exc:
            raise RequestError(str(exc))
        self.bus.publish({"type": "log", "source": "usb", "level": "cmd",
                          "line": f"{names.get(action, action)} USB {location} puerto {port}: {cmd}"})
        if action != "off" and not self.settings.mock:
            await asyncio.sleep(1.5)            # que el dispositivo vuelva a aparecer
        return await loop.run_in_executor(None, self.usb_status)

    # ------------------------------------------------------------------ estado
    def public_config(self) -> Dict[str, Any]:
        bc = self.cfg.boot_control
        return {
            "mock": self.settings.mock,
            "host": self.settings.host,
            "config_errors": self.config_errors,
            "chips": [c.to_public() for c in self.chips],
            "interfaces": [{"id": i.id, "cfg": i.cfg, "transport": i.transport, "description": i.description}
                           for i in self.cfg.interfaces.values()],
            "default_interface": self.cfg.openocd_default_interface,
            "capabilities": {m: sorted(c) for m, c in base.CAPABILITIES.items()},
            "chip_capabilities": {c.id: base.public_capabilities(c) for c in self.chips},
            "unsupported_reasons": dict(base.UNSUPPORTED_REASON, avr_erase=base.AVR_ERASE_REASON),
            "serial_methods": ["esptool", "avrdude"],
            "dfu_unsupported_reason": base.DFU_UNSUPPORTED_REASON,
            "boot_control": {"backend": bc.backend, "description": self.boot.describe(), "timeout_s": bc.timeout_s},
            "gdb": {"port": self.cfg.gdb_port, "bind": self.cfg.gdb_bind},
            "uploads": {"max_size_mb": self.cfg.max_upload_mb, "extensions": [".elf", ".hex", ".bin"]},
            "cfg_check": self.cfg_check.to_public(),
            "versions": self.versions,
            "serial_command": self.serial_command,
        }

    def shutdown(self) -> None:
        self.debug.stop_sync()
        self.gdb.stop_sync()
        self.serial.close()
        self.runner.cancel()


def _compare(expected: bytes, actual: bytes, address: int) -> None:
    if len(actual) < len(expected):
        raise JobFailed(f"Verificación fallida: se leyeron {len(actual)} de {len(expected)} bytes")
    if actual[:len(expected)] == expected:
        return
    for offset, (a, b) in enumerate(zip(expected, actual)):
        if a != b:
            raise JobFailed(f"Verificación fallida en 0x{address + offset:08X}: "
                            f"esperado 0x{a:02X}, leído 0x{b:02X}")


def _first_line_of(argv: List[str], env_extra: Dict[str, str], contains: Optional[str] = None) -> Optional[str]:
    """Primera línea no vacía de la salida (o la primera que contenga `contains`)."""
    try:
        out = run_capture(argv, timeout=15, env_extra=env_extra)
    except Exception:
        return None
    for line in (out.stdout + "\n" + out.stderr).splitlines():
        if line.strip() and (contains is None or contains in line):
            return line.strip()
    return None
