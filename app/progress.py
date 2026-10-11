"""Progreso de un trabajo leído de la salida de la herramienta: etapa y porcentaje.

Cada herramienta avisa a su manera en qué anda:
  * OpenOCD (`program ... verify reset`) imprime marcas de etapa
    ("** Programming Started **", "** Verify Started **"...) pero no porcentaje;
  * esptool imprime "Writing at 0x... (37 %)" por cada archivo, uno tras otro;
  * dfu-util dibuja barras "Erase [====] 20%" y "Download [===] 48%";
  * avrdude dibuja "Writing | ### | 100%" al terminar cada fase.

`tracker_for(herramienta, acción)` devuelve un seguidor con las etapas de esa
operación (connect, erase, write, verify, reset). Se le pasa cada línea con
`feed()`, que dice si cambió algo que valga la pena mostrar (otra etapa, otro
porcentaje entero u otro detalle) y si la línea es sólo una actualización de
avance (la interfaz las pisa en lugar de apilarlas). `snapshot()` es lo que se
publica. Un porcentaje None significa "esta etapa no informa cuánto falta".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

Stage = str


@dataclass(frozen=True)
class Rule:
    """Si la línea coincide: pasa a `stage`, fija `pct` o lo lee del grupo "pct"."""
    pattern: Pattern
    stage: Optional[Stage] = None
    pct: Optional[float] = None
    read_pct: bool = False
    when: Tuple[Stage, ...] = ()       # sólo si la etapa actual es una de estas
    progress: bool = False             # la línea es una actualización de avance


def _rule(regex: str, **kw) -> Rule:
    return Rule(re.compile(regex), **kw)


class Tracker:
    stages: Tuple[Stage, ...] = ()
    rules: Sequence[Rule] = ()

    def __init__(self) -> None:
        self.stage: Stage = self.stages[0]
        self.pct: Optional[float] = None
        self.detail: Optional[str] = None
        self._seen = self._state()

    # ---------------------------------------------------------------- interfaz
    def feed(self, line: str) -> Tuple[bool, bool]:
        """(cambió algo visible, la línea es una actualización de avance)."""
        progress = self._line(line.strip())
        state = self._state()
        changed = state != self._seen
        self._seen = state
        return changed, progress

    def snapshot(self) -> Dict:
        pct = None if self.pct is None else round(self.pct, 1)
        return {"stages": list(self.stages), "stage": self.stage, "pct": pct, "detail": self.detail}

    # ----------------------------------------------------------------- interno
    def _state(self) -> tuple:
        return self.stage, None if self.pct is None else int(self.pct), self.detail

    def _go(self, stage: Stage) -> None:
        if stage != self.stage:
            self.stage = stage
            self.pct = None

    def _line(self, text: str) -> bool:
        for rule in self.rules:
            m = rule.pattern.search(text)
            if not m or (rule.when and self.stage not in rule.when):
                continue
            if rule.stage:
                self._go(rule.stage)
            if rule.read_pct:
                self.pct = float(min(100, int(m.group("pct"))))
            elif rule.pct is not None:
                self.pct = rule.pct
            return rule.progress
        return False


# --------------------------------------------------------------------- OpenOCD
# Antes de operar, el banco lee el ID del chip ("Chip verificado...") y detiene
# el núcleo ("halted due to..."): ahí termina la conexión.
_OCD_READY = r"Chip verificado|ID del chip conectado|halted due to"


class OpenOcdFlash(Tracker):
    stages = ("connect", "write", "verify", "reset")
    rules = (
        _rule(r"\*\* Programming Started \*\*", stage="write"),
        _rule(r"\*\* Verify Started \*\*", stage="verify"),
        _rule(r"\*\* Resetting Target \*\*", stage="reset"),
    )


class OpenOcdVerify(Tracker):
    stages = ("connect", "verify")
    rules = (
        _rule(_OCD_READY, stage="verify", when=("connect",)),
        _rule(r"^verified \d+ bytes", stage="verify", pct=100),
    )


class OpenOcdErase(Tracker):
    stages = ("connect", "erase")
    rules = (
        _rule(_OCD_READY, stage="erase", when=("connect",)),
        _rule(r"erased sectors|mass erase complete", stage="erase", pct=100),
    )


# --------------------------------------------------------------------- esptool
_ESP_WRITING = re.compile(r"^Writing at 0x[0-9a-fA-F]+\.*\s*\(\s*(?P<pct>\d+)\s*%\)")
_ESP_COMPRESSED = re.compile(r"^Compressed \d+ bytes")
_ESP_WROTE = re.compile(r"^Wrote \d+ bytes")
_ESP_ERASE_RANGE = re.compile(r"^Flash will be erased from 0x(?P<a>[0-9a-fA-F]+) to 0x(?P<b>[0-9a-fA-F]+)")
_ESP_RESET = re.compile(r"^(Hard resetting|Leaving\.\.\.)")


class _Files(Tracker):
    """Varios archivos seguidos: el porcentaje total se pondera por tamaño."""

    def __init__(self, sizes: Optional[Sequence[int]]) -> None:
        self.sizes: List[int] = list(sizes or [])
        self.file = -1                 # archivo en curso (índice)
        super().__init__()

    def _weights(self) -> List[int]:
        return self.sizes

    def _set_file_pct(self, file_pct: float) -> None:
        weights = self._weights()
        total_files = len(weights)
        if 0 <= self.file < total_files:
            done = sum(weights[:self.file]) + weights[self.file] * file_pct / 100
            self.pct = 100 * done / sum(weights)
        else:
            self.pct = file_pct        # sin pesos: el avance del archivo en curso
        if total_files > 1 and self.file < total_files:
            self.detail = f"archivo {self.file + 1} de {total_files}"
        elif self.file >= 1:
            self.detail = f"archivo {self.file + 1}"


class EsptoolWrite(_Files):
    stages = ("connect", "write", "reset")

    def __init__(self, sizes: Optional[Sequence[int]]) -> None:
        self.erase_ranges: List[int] = []
        self._next_file = True
        super().__init__(sizes)

    def _weights(self) -> List[int]:
        return self.sizes or self.erase_ranges

    def _start_file(self) -> None:
        self._go("write")
        self.file += 1
        self._next_file = False
        self._set_file_pct(0)

    def _line(self, text: str) -> bool:
        m = _ESP_ERASE_RANGE.match(text)
        if m:
            self.erase_ranges.append(int(m.group("b"), 16) - int(m.group("a"), 16) + 1)
            return False
        if _ESP_COMPRESSED.match(text):
            if self._next_file:
                self._start_file()
            return False
        m = _ESP_WRITING.match(text)
        if m:
            if self._next_file:
                self._start_file()
            self._set_file_pct(min(100, int(m.group("pct"))))
            return True
        if _ESP_WROTE.match(text):
            self._set_file_pct(100)
            self._next_file = True
            return False
        if _ESP_RESET.match(text):
            self._go("reset")
        return False


class EsptoolVerify(_Files):
    stages = ("connect", "verify")
    _start = re.compile(r"^Verifying 0x[0-9a-fA-F]+ \(\d+\) bytes")
    _ok = re.compile(r"^-- verify OK")

    def _line(self, text: str) -> bool:
        if self._start.match(text):
            self._go("verify")
            self.file += 1
            if self.sizes:
                self._set_file_pct(0)
        elif self._ok.match(text) and self.sizes:
            self._set_file_pct(100)
        return False


class EsptoolErase(Tracker):
    stages = ("connect", "erase")
    rules = (
        _rule(r"^Erasing flash", stage="erase"),
        _rule(r"^Chip erase completed", stage="erase", pct=100),
    )


# -------------------------------------------------------------------- dfu-util
_DFU_BAR = r"\[[= ]*\]\s*(?P<pct>\d+)%"


class DfuDownload(Tracker):
    stages = ("connect", "erase", "write")
    rules = (
        _rule(r"^Erase\s*" + _DFU_BAR, stage="erase", read_pct=True, progress=True),
        _rule(r"^Performing mass erase", stage="erase"),
        _rule(r"^Download\s*" + _DFU_BAR, stage="write", read_pct=True, progress=True),
        _rule(r"^Download done", stage="write", pct=100),
    )


class DfuUpload(Tracker):
    stages = ("connect", "verify")
    rules = (
        _rule(r"^Upload\s*" + _DFU_BAR, stage="verify", read_pct=True, progress=True),
        _rule(r"^Upload done", stage="verify", pct=100),
    )


class DfuErase(Tracker):
    stages = ("connect", "erase")
    rules = (
        _rule(r"^Performing mass erase", stage="erase"),
        _rule(r"^Done!", stage="erase", pct=100, when=("erase",)),
    )


# --------------------------------------------------------------------- avrdude
# Las barras "Reading | ### | 100%" aparecen también al leer la firma del chip
# (conexión): sólo cuentan como verificación si ya empezó a verificar.
_AVR_BAR = r"\|\s*#*\s*\|\s*(?P<pct>\d+)%"


class AvrFlash(Tracker):
    stages = ("connect", "write", "verify")
    rules = (
        _rule(r"writing flash", stage="write"),
        _rule(r"^Writing\s*" + _AVR_BAR, read_pct=True, when=("write",), progress=True),
        _rule(r"verifying flash memory|reading on-chip flash data", stage="verify", when=("write", "verify")),
        _rule(r"^Reading\s*" + _AVR_BAR, read_pct=True, when=("verify",), progress=True),
        _rule(r"bytes of flash verified", stage="verify", pct=100),
        _rule(r"^(Reading|Writing)\s*\|", progress=True),
    )


class AvrVerify(Tracker):
    stages = ("connect", "verify")
    rules = (
        _rule(r"verifying flash memory|reading on-chip flash data", stage="verify"),
        _rule(r"^Reading\s*" + _AVR_BAR, read_pct=True, when=("verify",), progress=True),
        _rule(r"bytes of flash verified", stage="verify", pct=100),
        _rule(r"^(Reading|Writing)\s*\|", progress=True),
    )


class AvrErase(Tracker):
    stages = ("connect", "erase")
    rules = (
        _rule(r"erasing chip", stage="erase"),
        _rule(r"avrdude done", stage="erase", pct=100, when=("erase",)),
        _rule(r"^(Reading|Writing)\s*\|", progress=True),
    )


# ---------------------------------------------------------------------- fábrica
_SIMPLE = {
    ("openocd", "flash"): OpenOcdFlash, ("openocd", "verify"): OpenOcdVerify, ("openocd", "erase"): OpenOcdErase,
    ("esptool", "erase"): EsptoolErase,
    ("dfu-util", "flash"): DfuDownload, ("dfu-util", "verify"): DfuUpload, ("dfu-util", "erase"): DfuErase,
    ("avrdude", "flash"): AvrFlash, ("avrdude", "verify"): AvrVerify, ("avrdude", "erase"): AvrErase,
}
_BY_SIZE = {("esptool", "flash"): EsptoolWrite, ("esptool", "verify"): EsptoolVerify}


def tracker_for(tool: str, action: str, sizes: Optional[Sequence[int]] = None) -> Optional[Tracker]:
    """Seguidor de progreso para esa herramienta y acción, o None si no aplica
    (reset, halt, run e identificar son instantáneos). `sizes`: tamaño de cada
    archivo, en el orden en que se graban (esptool con varios archivos)."""
    if sizes is not None and any(int(s) <= 0 for s in sizes):
        raise ValueError("Los tamaños de archivo tienen que ser positivos")
    key = (tool, action)
    if key in _BY_SIZE:
        return _BY_SIZE[key](sizes)
    if key in _SIMPLE:
        return _SIMPLE[key]()
    return None
