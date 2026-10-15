"""Construcción de comandos de OpenOCD (funciones puras, sin efectos).

Siempre se devuelve una LISTA de argumentos para subprocess (sin shell). Ojo:
los argumentos de "-c" son código Tcl que interpreta OpenOCD, por eso:
  * las rutas pasan por ensure_tcl_safe() y van entre llaves {...} (en Tcl el
    contenido entre llaves es literal, sin sustituciones);
  * los números se formatean acá (nunca se copia texto del usuario);
  * el número de serie del ST-Link se valida con una lista blanca.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..chips import IdCode, SwdParams
from ..files import ensure_tcl_safe
from ..settings import InterfaceConfig

# Números de serie de ST-Link: alfanuméricos (los V2 con serie binaria se
# convierten a hex en probe.py, igual que hace OpenOCD).
PROBE_SERIAL_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
# Comando para elegir el adaptador por número de serie según la versión:
# OpenOCD >= 0.12 (y el fork de ST) usan "adapter serial"; antes, "hla_serial".
SERIAL_COMMANDS = ("adapter serial", "hla_serial")


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class OpenOcdSetup:
    """Todo lo necesario para invocar OpenOCD con un adaptador concreto."""
    command: Tuple[str, ...]            # ("openocd",) o el simulador en modo mock
    scripts_dir: Optional[str]
    interface: InterfaceConfig
    serial: Optional[str] = None
    serial_command: str = "adapter serial"


def _hex(value: int) -> str:
    if not 0 <= value <= 0xFFFFFFFF:
        raise CommandError(f"Dirección fuera de rango: {value}")
    return f"0x{value:08X}"


def _tcl_path(path: Path) -> str:
    return "{" + ensure_tcl_safe(path) + "}"


def base_args(setup: OpenOcdSetup, swd: SwdParams, extra_config: Sequence[str] = ()) -> List[str]:
    """Parte común: interfaz, transporte, serie, variables, target y velocidad.

    El orden importa: el serie va después del .cfg de la interfaz (que elige el
    driver) y antes del target; la velocidad va después del target porque el
    .cfg del target define su propia velocidad por defecto.
    """
    if setup.serial_command not in SERIAL_COMMANDS:
        raise CommandError(f"Comando de serie no soportado: {setup.serial_command}")
    args: List[str] = list(setup.command)
    if setup.scripts_dir:
        args += ["-s", str(setup.scripts_dir)]
    for cmd in extra_config:
        args += ["-c", cmd]
    args += ["-f", setup.interface.cfg]
    if setup.interface.transport:
        args += ["-c", f"transport select {setup.interface.transport}"]
    if setup.serial:
        if not PROBE_SERIAL_RE.fullmatch(setup.serial):
            raise CommandError("Número de serie de ST-Link inválido")
        args += ["-c", f"{setup.serial_command} {setup.serial}"]
    for name, value in swd.tcl_vars:
        args += ["-c", f"set {name} {value}"]
    args += ["-f", swd.target_cfg]
    args += ["-c", f"adapter speed {int(swd.adapter_speed_khz)}"]
    return args


def _needs_address(path: Path) -> bool:
    return path.suffix.lower() == ".bin"


def idcode_check_cmd(idcode: IdCode, strict: bool = True) -> str:
    """Tcl que lee el ID del chip conectado y lo compara con el esperado.

    Sólo usa números de chips.yaml (validados), nunca texto del usuario. Con
    strict=True, si el ID no coincide termina OpenOCD con error antes de tocar
    la flash ("shutdown error"): así no se graba un binario en el chip equivocado.
    """
    addr, mask, value = _hex(idcode.address), f"0x{idcode.mask:X}", f"0x{idcode.value:X}"
    leido = f"set _id [expr {{[lindex [read_memory {addr} 32 1] 0] & {mask}}}]"
    if not strict:
        return (leido + f"; echo [format \"ID del chip conectado: 0x%03X (esperado para esta placa: 0x%03X)\" "
                f"$_id {value}]")
    return (leido + f"; if {{$_id != {value}}} {{ echo [format \"CHIP EQUIVOCADO: se esperaba el ID 0x%03X y el "
            f"chip conectado tiene 0x%03X. No se toca la flash.\" {value} $_id]; shutdown error }} else "
            f"{{ echo [format \"Chip verificado: ID 0x%03X\" $_id] }}")


def _init_checked(setup: OpenOcdSetup, swd: SwdParams) -> List[str]:
    """init + verificación del ID del chip (si chips.yaml lo declara)."""
    args = base_args(setup, swd) + ["-c", "init"]
    if swd.idcode is not None:
        args += ["-c", idcode_check_cmd(swd.idcode)]
    return args


def flash_args(setup: OpenOcdSetup, swd: SwdParams, firmware: Path, address: Optional[int]) -> List[str]:
    """program + verify + reset. Para .bin la dirección es obligatoria."""
    program = f"program {_tcl_path(firmware)}"
    if _needs_address(firmware):
        if address is None:
            raise CommandError("Para un .bin hace falta la dirección de carga")
        program += f" {_hex(address)}"
    program += " verify reset exit"
    return _init_checked(setup, swd) + ["-c", program]


def verify_args(setup: OpenOcdSetup, swd: SwdParams, firmware: Path, address: Optional[int]) -> List[str]:
    """Solo verificar: halt, verify_image, resume (no reprograma nada)."""
    verify = f"verify_image {_tcl_path(firmware)}"
    if _needs_address(firmware):
        if address is None:
            raise CommandError("Para un .bin hace falta la dirección de carga")
        verify += f" {_hex(address)} bin"
    return _init_checked(setup, swd) + ["-c", "halt", "-c", verify, "-c", "resume", "-c", "shutdown"]


def erase_args(setup: OpenOcdSetup, swd: SwdParams) -> List[str]:
    """Borrado completo. Por defecto borra todos los sectores de cada banco."""
    args = _init_checked(setup, swd) + ["-c", "reset halt"]
    if swd.erase_cmds:
        for cmd in swd.erase_cmds:
            args += ["-c", cmd]
    else:
        for bank in swd.flash_banks:
            args += ["-c", f"flash erase_sector {int(bank)} 0 last"]
    return args + ["-c", "shutdown"]


def read_args(setup: OpenOcdSetup, swd: SwdParams, output: Path, address: int, size: int) -> List[str]:
    """Lee la flash a un archivo sin detener el programa (el acceso de depuración de los
    Cortex-M lee la memoria con el núcleo corriendo). Si chips.yaml lo dice, informa
    también el número de serie único del chip."""
    if not 0 <= address <= 0xFFFFFFFF or not 0 < size <= 0x10000000:
        raise CommandError("Rango de lectura inválido")
    args = _init_checked(setup, swd)
    if swd.uid is not None:
        args += ["-c", f"echo \"UID: [read_memory {_hex(swd.uid)} 32 3]\""]
    return args + ["-c", f"dump_image {_tcl_path(output)} {_hex(address)} {int(size)}", "-c", "shutdown"]


def reset_args(setup: OpenOcdSetup, swd: SwdParams) -> List[str]:
    return base_args(setup, swd) + ["-c", "init", "-c", "reset run", "-c", "shutdown"]


def halt_args(setup: OpenOcdSetup, swd: SwdParams) -> List[str]:
    return base_args(setup, swd) + ["-c", "init", "-c", "halt", "-c", "shutdown"]


def resume_args(setup: OpenOcdSetup, swd: SwdParams) -> List[str]:
    return base_args(setup, swd) + ["-c", "init", "-c", "resume", "-c", "shutdown"]


def info_args(setup: OpenOcdSetup, swd: SwdParams) -> List[str]:
    """Identificar: conecta, informa el ID del chip (sin cortar si no coincide) y sale."""
    args = base_args(setup, swd) + ["-c", "init"]
    if swd.idcode is not None:
        args += ["-c", idcode_check_cmd(swd.idcode, strict=False)]
    return args + ["-c", "shutdown"]


@dataclass(frozen=True)
class RttConfig:
    """Bloque de control de RTT del programa (símbolo _SEGGER_RTT del .elf) y puerto TCP."""
    address: int
    size: int
    port: int


def gdb_server_args(setup: OpenOcdSetup, swd: SwdParams, port: int, bind_ip: str,
                    rtt: Optional[RttConfig] = None, tcl_port: Optional[int] = None) -> List[str]:
    """OpenOCD como servidor GDB persistente (y, si el programa lo tiene, servidor RTT).

    telnet se deshabilita. El puerto TCL (con él la app muestrea variables con el
    micro corriendo) acepta cualquier comando de OpenOCD, y como "bindto" aplica a
    todos los puertos, sólo se habilita si OpenOCD escucha en 127.0.0.1: a la red
    salen el puerto GDB y el del canal RTT, reenviados por la app (relay.py).
    """
    if not 1024 <= int(port) <= 65535:
        raise CommandError("Puerto GDB fuera de rango")
    import ipaddress
    ipaddress.ip_address(bind_ip)  # lanza ValueError si no es una IP
    if tcl_port is not None:
        if bind_ip != "127.0.0.1":
            raise CommandError("El puerto TCL de OpenOCD sólo puede escuchar en 127.0.0.1")
        if not 1024 <= int(tcl_port) <= 65535 or int(tcl_port) == int(port):
            raise CommandError("Puerto TCL inválido")
    config = [
        f"bindto {bind_ip}",
        f"gdb_port {int(port)}",
        "telnet_port disabled",
        f"tcl_port {int(tcl_port)}" if tcl_port is not None else "tcl_port disabled",
    ]
    after_init: List[str] = []
    if rtt is not None:
        if not 1024 <= int(rtt.port) <= 65535 or int(rtt.port) == int(port):
            raise CommandError("Puerto RTT inválido")
        if not (0 <= rtt.address <= 0xFFFFFFFF and 16 <= rtt.size <= 0x10000):
            raise CommandError("Bloque de control RTT inválido")
        # Se busca justo en la dirección del símbolo: rápido y sin falsos positivos.
        config += [f"rtt setup {_hex(rtt.address)} {int(rtt.size)} {{SEGGER RTT}}",
                   "catch {rtt polling_interval 10}"]
        # Con el target ya examinado: buscar el bloque y publicar el canal 0 por TCP.
        after_init = ["-c", "rtt start", "-c", f"rtt server start {int(rtt.port)} 0"]
    return base_args(setup, swd, extra_config=config) + ["-c", "init"] + after_init


def version_args(command: Sequence[str]) -> List[str]:
    return list(command) + ["--version"]


def serial_probe_args(command: Sequence[str], scripts_dir: Optional[str]) -> List[str]:
    """Pregunta a OpenOCD si conoce 'adapter serial' (para elegir el comando)."""
    args = list(command)
    if scripts_dir:
        args += ["-s", str(scripts_dir)]
    return args + ["-c", "help adapter serial", "-c", "shutdown"]
