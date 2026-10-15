"""Acciones, capacidades por método y selección de método."""
from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional

from ..chips import Chip

# Acciones que se pueden pedir sobre un chip.
FLASH = "flash"
VERIFY = "verify"
ERASE = "erase"
RESET = "reset"
HALT = "halt"
RESUME = "resume"
INFO = "info"
READ = "read"
GDB = "gdb"

ACTIONS = (FLASH, VERIFY, ERASE, RESET, HALT, RESUME, INFO, READ)

ACTION_LABELS: Dict[str, str] = {
    FLASH: "Flashear",
    VERIFY: "Verificar",
    ERASE: "Borrar flash",
    RESET: "Reset",
    HALT: "Halt",
    RESUME: "Run",
    INFO: "Identificar",
    READ: "Leer el micro",
    GDB: "Servidor GDB",
}

# Qué puede hacer cada método.
CAPABILITIES: Dict[str, FrozenSet[str]] = {
    # ST-Link: acceso completo al núcleo.
    "swd": frozenset({FLASH, VERIFY, ERASE, RESET, HALT, RESUME, INFO, READ, GDB}),
    # Bootloader USB de ST: sólo escribe, lee y borra la flash.
    "dfu": frozenset({FLASH, VERIFY, ERASE, READ}),
    # esptool por el puerto serie: el bootloader de ROM del ESP graba, verifica y borra;
    # el reset se hace con las líneas DTR/RTS del adaptador USB-serie.
    "esptool": frozenset({FLASH, VERIFY, ERASE, RESET, INFO, READ}),
    # avrdude: el bootloader de Arduino graba y verifica; borrar sólo con programador ISP.
    "avrdude": frozenset({FLASH, VERIFY, ERASE, RESET, INFO, READ}),
}

UNSUPPORTED_REASON: Dict[str, str] = {
    "dfu": ("No disponible por USB DFU: el bootloader de ST solo permite escribir, leer y "
            "borrar la flash; no da acceso al núcleo (sin debug, reset, halt ni run). Usá SWD."),
    "esptool": ("No disponible con esptool: por el puerto serie no hay acceso de depuración al "
                "núcleo del ESP (halt, run y GDB necesitan JTAG)."),
    "avrdude": ("No disponible en Arduino AVR: el bootloader sólo graba y verifica, y los AVR "
                "no tienen depuración por USB."),
}
AVR_ERASE_REASON = ("El bootloader de Arduino no permite borrar la flash completa (la borra al "
                    "grabar). El borrado total sólo está disponible con un programador ISP.")
DFU_UNSUPPORTED_REASON = UNSUPPORTED_REASON["dfu"]

# Acciones que necesitan un archivo de firmware.
NEEDS_FILE = frozenset({FLASH, VERIFY})


class MethodError(ValueError):
    """Método o acción no válidos para el chip."""


def select_method(chip: Chip, requested: Optional[str]) -> str:
    """Devuelve el método a usar: el pedido (si el chip lo soporta) o el default."""
    if requested is None or requested == "":
        return chip.default_method
    if requested not in CAPABILITIES:
        raise MethodError(f"Método desconocido '{requested}' (válidos: {', '.join(CAPABILITIES)})")
    if requested not in chip.methods:
        raise MethodError(
            f"{chip.name} no tiene configurado el método '{requested}' "
            f"(soporta: {', '.join(chip.methods)})")
    return requested


def chip_capabilities(chip: Chip, method: str) -> FrozenSet[str]:
    """Capacidades del método para este chip concreto."""
    caps = set(CAPABILITIES[method])
    if method == "avrdude" and chip.avrdude is not None and not chip.avrdude.is_isp:
        caps.discard(ERASE)
    return frozenset(caps)


def unsupported_reason(chip: Chip, method: str, action: str) -> str:
    if method == "avrdude" and action == ERASE:
        return AVR_ERASE_REASON
    return UNSUPPORTED_REASON.get(method, f"La acción '{action}' no está disponible con {method}")


def check_action(method: str, action: str, chip: Optional[Chip] = None) -> None:
    """Lanza MethodError si el método (para ese chip) no soporta la acción."""
    if action not in ACTION_LABELS:
        raise MethodError(f"Acción desconocida '{action}'")
    caps = chip_capabilities(chip, method) if chip is not None else CAPABILITIES[method]
    if action not in caps:
        if chip is not None:
            raise MethodError(unsupported_reason(chip, method, action))
        raise MethodError(UNSUPPORTED_REASON.get(method, f"La acción '{action}' no está disponible con {method}"))


def public_capabilities(chip: Chip) -> Dict[str, List[str]]:
    return {m: sorted(chip_capabilities(chip, m)) for m in chip.methods}
