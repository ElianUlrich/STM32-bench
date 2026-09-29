"""Acciones, capacidades por método y selección de método."""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional

from ..chips import Chip

# Acciones que se pueden pedir sobre un chip.
FLASH = "flash"
VERIFY = "verify"
ERASE = "erase"
RESET = "reset"
HALT = "halt"
RESUME = "resume"
GDB = "gdb"

ACTIONS = (FLASH, VERIFY, ERASE, RESET, HALT, RESUME)

ACTION_LABELS: Dict[str, str] = {
    FLASH: "Flashear",
    VERIFY: "Verificar",
    ERASE: "Borrar flash",
    RESET: "Reset",
    HALT: "Halt",
    RESUME: "Run",
    GDB: "Servidor GDB",
}

# Qué puede hacer cada método. El bootloader DFU de ST solo escribe, lee y
# borra la flash: no hay acceso al núcleo, así que no hay debug ni reset/halt.
CAPABILITIES: Dict[str, FrozenSet[str]] = {
    "swd": frozenset({FLASH, VERIFY, ERASE, RESET, HALT, RESUME, GDB}),
    "dfu": frozenset({FLASH, VERIFY, ERASE}),
}

DFU_UNSUPPORTED_REASON = (
    "No disponible por USB DFU: el bootloader de ST solo permite escribir, leer y "
    "borrar la flash; no da acceso al núcleo (sin debug, reset, halt ni run). Usá SWD.")

# Acciones que necesitan un archivo de firmware.
NEEDS_FILE = frozenset({FLASH, VERIFY})


class MethodError(ValueError):
    """Método o acción no válidos para el chip."""


def select_method(chip: Chip, requested: Optional[str]) -> str:
    """Devuelve el método a usar: el pedido (si el chip lo soporta) o el default."""
    if requested is None or requested == "":
        return chip.default_method
    if requested not in CAPABILITIES:
        raise MethodError(f"Método desconocido '{requested}' (válidos: swd, dfu)")
    if requested not in chip.methods:
        raise MethodError(
            f"El chip {chip.name} no tiene configurado el método '{requested}' "
            f"(soporta: {', '.join(chip.methods)})")
    return requested


def check_action(method: str, action: str) -> None:
    """Lanza MethodError si el método no soporta la acción."""
    if action not in ACTION_LABELS:
        raise MethodError(f"Acción desconocida '{action}'")
    if action not in CAPABILITIES[method]:
        if method == "dfu":
            raise MethodError(DFU_UNSUPPORTED_REASON)
        raise MethodError(f"La acción '{action}' no está disponible con {method}")
