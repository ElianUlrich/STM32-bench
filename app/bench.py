"""Servicio central del banco: une configuración, detección y trabajos.

Toda la validación de pedidos de la interfaz ocurre acá, ANTES de construir
cualquier comando:
  * el chip debe existir en chips.yaml y soportar el método pedido;
  * la interfaz debe estar en bench.yaml;
  * el archivo debe resolverse dentro del directorio de proyectos;
  * el número de serie del ST-Link / dispositivo DFU debe estar entre los detectados;
  * las direcciones deben caer dentro de la flash del chip.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import mock_state, probe, system
from .bootcontrol import create_boot_control, wait_for_dfu
from .chips import CfgCheck, Chip, check_cfg_files, load_chips
from .convert import ConversionError, convert_to_bin
from .events import EventBus
from .files import FileValidationError, resolve_firmware
from .history import History
from .procs import run_capture
from .programmers import base, dfu as dfu_cmd, openocd as ocd_cmd
from .programmers.gdbserver import GdbServer, GdbServerError
from .programmers.runner import BusyError, Job, JobContext, JobFailed, JobRunner, new_job
from .serial_monitor import SerialManager
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
    leave: bool = True
    stop_gdb: bool = False


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
            self.env_extra = {"PYTHONUNBUFFERED": "1", mock_state.ENV_VAR: str(settings.state_dir)}
            self.scripts_dir: Optional[str] = None
        else:
            self.openocd_cmd = (self.cfg.openocd_binary,)
            self.dfu_cmd = (self.cfg.dfu_util_binary,)
            self.env_extra = {}
            scripts = self.cfg.openocd_scripts()
            self.scripts_dir = str(scripts) if scripts else None

        self.bus = EventBus()
        self.history = History(settings.state_dir / "history.jsonl", self.cfg.history_max_entries)
        self.runner = JobRunner(self.bus, self.history, self.env_extra)
        self.gdb = GdbServer(self.bus, self.env_extra)
        self.serial = SerialManager(self.bus, self.cfg.serial_bauds, self.cfg.serial_default_baud, settings.mock)
        self.boot = create_boot_control(self.cfg.boot_control, self.serial, settings.mock, settings.state_dir)
        self.serial_command = "adapter serial"
        self.versions: Dict[str, Optional[str]] = {"openocd": None, "dfu_util": None}
        self.cfg_check = CfgCheck(scripts_dir=self.scripts_dir)
        if settings.mock:
            mock_state.clear_dfu(settings.state_dir)

    # ------------------------------------------------------------------ inicio
    def detect_tools(self) -> None:
        """Versiones y comando de serie de OpenOCD; chequeo de .cfg. (bloqueante)"""
        self.versions["openocd"] = _first_line_of(ocd_cmd.version_args(self.openocd_cmd), self.env_extra)
        self.versions["dfu_util"] = _first_line_of(dfu_cmd.version_args(self.dfu_cmd), self.env_extra)
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
            base.check_action(method, req.action)
        except base.MethodError as exc:
            raise RequestError(str(exc))
        if self.runner.busy:
            raise BusyError(f"El programador está ocupado ({self.runner.current.description})")

        firmware: Optional[Path] = None
        if req.action in base.NEEDS_FILE:
            if not req.file:
                raise RequestError("Elegí un archivo de firmware")
            try:
                firmware = resolve_firmware(self.settings.projects_dir, req.file)
            except FileValidationError as exc:
                raise RequestError(str(exc))

        if method == "swd":
            job, fn = await self._prepare_swd(chip, req, firmware)
        else:
            job, fn = self._prepare_dfu(chip, req, firmware)
        return self.runner.start(job, fn)

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
            await self.gdb.stop()

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
                    base.HALT: "Núcleo detenido (halt)", base.RESUME: "Núcleo en ejecución (run)"}

        async def fn(ctx: JobContext) -> str:
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
                                raise JobFailed(f"dfu-util terminó con código {result.returncode}")
                        left = leave
                        message = "Flasheado por DFU" + (" y en ejecución" if leave else "")
                    elif action == base.VERIFY:
                        size = bin_path.stat().st_size
                        readback = tmpdir / "readback.bin"
                        await ctx.run(dfu_cmd.upload_args(target, bin_addr, size, readback),
                                      source="dfu-util", timeout_s=timeout)
                        _compare(bin_path.read_bytes(), readback.read_bytes() if readback.exists() else b"", bin_addr)
                        message = "Verificación OK"
                    else:
                        await ctx.run(dfu_cmd.mass_erase_args(target, dfu.address),
                                      source="dfu-util", timeout_s=timeout)
                        message = "Flash borrada"

                    if not left and boot.needs_dfu_leave and action == base.VERIFY:
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

    # --------------------------------------------------------------- servidor GDB
    def gdb_bind_ip(self) -> Tuple[str, Optional[str]]:
        """(IP donde escucha OpenOCD, IP que se muestra para conectarse)."""
        bind = self.cfg.gdb_bind
        ts_ip = "100.101.102.103" if self.settings.mock else system.interface_ipv4(self.cfg.tailscale_interface)
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

    async def start_gdb(self, chip_id: str, interface: Optional[str], probe_serial: Optional[str]) -> Dict[str, Any]:
        chip = self.chip(chip_id)
        if chip.swd is None:
            raise RequestError(base.DFU_UNSUPPORTED_REASON)
        if self.runner.busy:
            raise BusyError("Hay una operación en curso; esperá a que termine")
        iface = self.interface_for(chip, interface)
        serial = self.resolve_probe_serial(probe_serial)
        bind_ip, show_ip = self.gdb_bind_ip()
        port = self.cfg.gdb_port
        try:
            argv = ocd_cmd.gdb_server_args(self.openocd_setup(iface, serial), chip.swd, port, bind_ip)
        except (ocd_cmd.CommandError, ValueError) as exc:
            raise RequestError(str(exc))
        info = {"chip": chip.id, "chip_name": chip.name, "interface": iface.id, "probe_serial": serial,
                "port": port, "bind_ip": bind_ip,
                "connect": f"{show_ip}:{port}" if show_ip else f"<IP de la placa>:{port}"}
        try:
            return await self.gdb.start(argv, info)
        except GdbServerError as exc:
            raise RequestError(str(exc))

    # ------------------------------------------------------------------ estado
    def public_config(self) -> Dict[str, Any]:
        bc = self.cfg.boot_control
        return {
            "mock": self.settings.mock,
            "config_errors": self.config_errors,
            "chips": [c.to_public() for c in self.chips],
            "interfaces": [{"id": i.id, "cfg": i.cfg, "transport": i.transport, "description": i.description}
                           for i in self.cfg.interfaces.values()],
            "default_interface": self.cfg.openocd_default_interface,
            "capabilities": {m: sorted(c) for m, c in base.CAPABILITIES.items()},
            "dfu_unsupported_reason": base.DFU_UNSUPPORTED_REASON,
            "boot_control": {"backend": bc.backend, "description": self.boot.describe(), "timeout_s": bc.timeout_s},
            "gdb": {"port": self.cfg.gdb_port, "bind": self.cfg.gdb_bind},
            "uploads": {"max_size_mb": self.cfg.max_upload_mb, "extensions": [".elf", ".hex", ".bin"]},
            "cfg_check": self.cfg_check.to_public(),
            "versions": self.versions,
            "serial_command": self.serial_command,
        }

    def shutdown(self) -> None:
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


def _first_line_of(argv: List[str], env_extra: Dict[str, str]) -> Optional[str]:
    try:
        out = run_capture(argv, timeout=15, env_extra=env_extra)
    except Exception:
        return None
    for line in (out.stdout + "\n" + out.stderr).splitlines():
        if line.strip():
            return line.strip()
    return None
