"""Variables en vivo: qué variables de un .elf se pueden graficar y cómo leerlas.

Las variables globales y las static (también las de dentro de una función) tienen
una dirección fija: se leen con el micro corriendo, sin detenerlo, por el puerto
TCL de OpenOCD (`read_memory`, acceso a memoria por el bus de depuración).

* `list_variables(elf)`: variables numéricas del DWARF, con los campos de los
  struct ("sensor.temperatura") y los elementos de los arrays ("ultimas[3]").
* `read_command(vars)`: un solo comando TCL que lee todas.
* `decode(vars, reply)`: los valores numéricos de la respuesta.
"""
from __future__ import annotations

import asyncio
import re
import struct
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MAX_VARIABLES = 3000          # en un proyecto grande (HAL) no listar sin fin
MAX_ELEMENTS = 16             # de cada array se ofrecen los primeros
MAX_DEPTH = 4                 # struct dentro de struct...

# kind -> (bytes, formato de struct)
KINDS: Dict[str, Tuple[int, str]] = {
    "i8": (1, "<b"), "u8": (1, "<B"), "bool": (1, "<B"), "i16": (2, "<h"), "u16": (2, "<H"),
    "i32": (4, "<i"), "u32": (4, "<I"), "i64": (8, "<q"), "u64": (8, "<Q"), "f32": (4, "<f"), "f64": (8, "<d"),
}

# DW_ATE_*: codificación de los tipos base.
_ATE_FLOAT, _ATE_BOOL = 0x04, 0x02
_ATE_SIGNED = (0x05, 0x06)            # signed, signed_char
_ATE_UNSIGNED = (0x07, 0x08)          # unsigned, unsigned_char
_DW_OP_ADDR = 0x03
_DW_OP_PLUS_UCONST = 0x23


class LiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class Variable:
    name: str
    address: int
    kind: str

    @property
    def size(self) -> int:
        return KINDS[self.kind][0]

    def to_public(self) -> Dict:
        return {"name": self.name, "address": f"0x{self.address:08X}", "type": self.kind}


# --------------------------------------------------------------------- DWARF

def _attr(die, name):
    a = die.attributes.get(name)
    return a.value if a is not None else None


def _name(die) -> Optional[str]:
    v = _attr(die, "DW_AT_name")
    if v is None and "DW_AT_specification" in die.attributes:
        v = _attr(die.get_DIE_from_attribute("DW_AT_specification"), "DW_AT_name")
    if v is None and "DW_AT_abstract_origin" in die.attributes:
        v = _attr(die.get_DIE_from_attribute("DW_AT_abstract_origin"), "DW_AT_name")
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else v


def _type(die):
    """El tipo de un DIE, sin typedef/volatile/const."""
    if die is None or "DW_AT_type" not in die.attributes:
        return None
    t = die.get_DIE_from_attribute("DW_AT_type")
    while t is not None and t.tag in ("DW_TAG_typedef", "DW_TAG_volatile_type", "DW_TAG_const_type",
                                      "DW_TAG_restrict_type", "DW_TAG_atomic_type"):
        if "DW_AT_type" not in t.attributes:
            return None
        t = t.get_DIE_from_attribute("DW_AT_type")
    return t


def _is_const(die) -> bool:
    """¿La variable es const (no cambia nunca: no tiene sentido graficarla)?"""
    if "DW_AT_type" not in die.attributes:
        for ref in ("DW_AT_specification", "DW_AT_abstract_origin"):
            if ref in die.attributes:
                return _is_const(die.get_DIE_from_attribute(ref))
        return False
    t = die.get_DIE_from_attribute("DW_AT_type")
    while t is not None:
        if t.tag == "DW_TAG_const_type":
            return True
        if t.tag == "DW_TAG_array_type":
            return _is_const(t)               # const int tabla[4]: el const está en el elemento
        if t.tag not in ("DW_TAG_typedef", "DW_TAG_volatile_type", "DW_TAG_restrict_type",
                         "DW_TAG_atomic_type") or "DW_AT_type" not in t.attributes:
            return False
        t = t.get_DIE_from_attribute("DW_AT_type")
    return False


def _type_of_variable(die):
    t = _type(die)
    if t is None and "DW_AT_specification" in die.attributes:
        t = _type(die.get_DIE_from_attribute("DW_AT_specification"))
    if t is None and "DW_AT_abstract_origin" in die.attributes:
        t = _type(die.get_DIE_from_attribute("DW_AT_abstract_origin"))
    return t


def _scalar_kind(t) -> Optional[str]:
    size = _attr(t, "DW_AT_byte_size")
    if t.tag == "DW_TAG_enumeration_type":
        enc = _ATE_UNSIGNED[0]
        base = _type(t)
        if base is not None and base.tag == "DW_TAG_base_type":
            enc = _attr(base, "DW_AT_encoding")
        signed = enc in _ATE_SIGNED
    elif t.tag == "DW_TAG_base_type":
        enc = _attr(t, "DW_AT_encoding")
        if enc == _ATE_FLOAT:
            return {4: "f32", 8: "f64"}.get(size)
        if enc == _ATE_BOOL:
            return "bool" if size == 1 else None
        if enc not in _ATE_SIGNED + _ATE_UNSIGNED:
            return None
        signed = enc in _ATE_SIGNED
    elif t.tag == "DW_TAG_pointer_type":
        return {4: "u32", 8: "u64"}.get(size or 4)
    else:
        return None
    bits = {1: "8", 2: "16", 4: "32", 8: "64"}.get(size)
    return (("i" if signed else "u") + bits) if bits else None


def _member_offset(die) -> Optional[int]:
    loc = _attr(die, "DW_AT_data_member_location")
    if loc is None:
        return 0                                  # unión
    if isinstance(loc, int):
        return loc
    if isinstance(loc, list) and loc and loc[0] == _DW_OP_PLUS_UCONST:
        value, shift = 0, 0                       # ULEB128
        for b in loc[1:]:
            value |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return value
    return None


def _array_counts(t) -> List[int]:
    counts = []
    for sub in t.iter_children():
        if sub.tag != "DW_TAG_subrange_type":
            continue
        count = _attr(sub, "DW_AT_count")
        upper = _attr(sub, "DW_AT_upper_bound")
        if isinstance(count, int):
            counts.append(count)
        elif isinstance(upper, int):
            counts.append(upper + 1)
        else:
            return []                             # tamaño desconocido (extern int x[])
    return counts


def _type_size(t) -> Optional[int]:
    size = _attr(t, "DW_AT_byte_size")
    if isinstance(size, int):
        return size
    if t.tag == "DW_TAG_array_type":
        elem = _type(t)
        counts = _array_counts(t)
        esize = _type_size(elem) if elem is not None else None
        if esize and counts:
            n = 1
            for c in counts:
                n *= c
            return esize * n
    return None


def _expand(name: str, address: int, t, depth: int, out: List[Variable]) -> None:
    if t is None or depth > MAX_DEPTH or len(out) >= MAX_VARIABLES:
        return
    kind = _scalar_kind(t)
    if kind:
        out.append(Variable(name, address, kind))
        return
    if t.tag in ("DW_TAG_structure_type", "DW_TAG_union_type", "DW_TAG_class_type"):
        for m in t.iter_children():
            if m.tag != "DW_TAG_member" or "DW_AT_bit_size" in m.attributes or "DW_AT_data_bit_offset" in m.attributes:
                continue
            off = _member_offset(m)
            mname = _name(m)
            if off is None or not mname:
                continue
            _expand(f"{name}.{mname}", address + off, _type(m), depth + 1, out)
    elif t.tag == "DW_TAG_array_type":
        elem = _type(t)
        counts = _array_counts(t)
        esize = _type_size(elem) if elem is not None else None
        if not counts or not esize:
            return
        inner = 1
        for c in counts[1:]:
            inner *= c
        for i in range(min(counts[0], MAX_ELEMENTS)):
            if len(counts) == 1:
                _expand(f"{name}[{i}]", address + i * esize, elem, depth + 1, out)
            else:                                     # matriz: sólo la primera fila de cada dimensión
                _expand(f"{name}[{i}][0]", address + i * inner * esize, elem, depth + 1, out)


def _static_address(die) -> Optional[int]:
    loc = _attr(die, "DW_AT_location")
    if isinstance(loc, list) and len(loc) in (5, 9) and loc[0] == _DW_OP_ADDR:
        return int.from_bytes(bytes(loc[1:]), "little")
    return None


def list_variables(elf: Path) -> List[Variable]:
    """Variables numéricas con dirección fija del .elf ([] si no tiene información de depuración)."""
    try:
        from elftools.elf.elffile import ELFFile
        with open(elf, "rb") as fh:
            ef = ELFFile(fh)
            if not ef.has_dwarf_info():
                return []
            out: List[Variable] = []
            for cu in ef.get_dwarf_info().iter_CUs():
                _walk(cu.get_top_DIE(), None, out)
                if len(out) >= MAX_VARIABLES:
                    break
    except Exception:          # .elf corrupto o DWARF que pyelftools no entiende: no hay variables
        return []
    seen, result = set(), []
    natural = lambda v: ([int(p) if p.isdigit() else p for p in re.split(r"(\d+)", v.name.lower())], v.address)
    for v in sorted(out, key=natural):
        if v.name not in seen and v.address:
            seen.add(v.name)
            result.append(v)
    return result


def _walk(die, function: Optional[str], out: List[Variable]) -> None:
    for child in die.iter_children():
        if child.tag == "DW_TAG_variable":
            address = _static_address(child)
            name = _name(child)
            if address is not None and name and not _is_const(child):
                label = f"{name} ({function})" if function else name
                _expand(label, address, _type_of_variable(child), 0, out)
        elif child.tag in ("DW_TAG_subprogram", "DW_TAG_lexical_block", "DW_TAG_inlined_subroutine"):
            fname = function
            if child.tag == "DW_TAG_subprogram":
                fname = _name(child) or function
            _walk(child, fname, out)
        elif child.tag in ("DW_TAG_namespace",):
            _walk(child, function, out)


# ----------------------------------------------------------------- lectura TCL

def _reads(v: Variable) -> Tuple[int, int]:
    """(ancho en bits, cantidad) para read_memory: el tamaño natural si está alineada; si no, de a bytes."""
    if v.address % v.size == 0:
        return v.size * 8, 1
    return 8, v.size


def read_command(variables: List[Variable]) -> str:
    """Un comando TCL que lee todas las variables; si algo falla devuelve "ERROR <mensaje>"."""
    reads = " ".join(f"[read_memory 0x{v.address:08x} {w} {n}]" for v in variables for w, n in [_reads(v)])
    return f"if {{[catch {{concat {reads}}} r]}} {{return \"ERROR $r\"}} else {{return $r}}"


_HEX = re.compile(r"0x[0-9a-fA-F]+|\d+")


def decode(variables: List[Variable], reply: str) -> List[float]:
    reply = reply.strip()
    if reply.startswith("ERROR") or "rror" in reply[:40]:
        raise LiveError(reply[6:].strip() if reply.startswith("ERROR") else reply)
    words = [int(w, 0) for w in _HEX.findall(reply)]
    values, i = [], 0
    for v in variables:
        width, n = _reads(v)
        if i + n > len(words):
            raise LiveError(f"Respuesta incompleta de OpenOCD: {reply[:80]}")
        if n == 1:
            raw = words[i].to_bytes(v.size, "little")
        else:
            raw = bytes(w & 0xFF for w in words[i:i + n])
        i += n
        value = struct.unpack(KINDS[v.kind][1], raw)[0]
        values.append(float(value) if v.kind in ("f32", "f64") else value)
    return values


# ------------------------------------------------------------------- muestreo

MAX_LIVE = 8                  # variables a la vez
MAX_RATE_HZ = 100
KEEP_SAMPLES = 3000           # lo último, para redibujar al volver a la pestaña
PUBLISH_EVERY_S = 0.1         # los valores van al navegador en tandas
GIVE_UP_AFTER_S = 2.0         # errores de lectura seguidos antes de abandonar


class LiveSampler:
    """Lee las variables elegidas a ritmo fijo por el puerto TCL de OpenOCD y publica
    los valores por el bus de eventos ({"type": "live", "samples": [[t, v1, v2...], ...]})."""

    def __init__(self, bus) -> None:
        self.bus = bus
        self.state = "stopped"                  # stopped | running | error
        self.variables: List[Variable] = []
        self.rate_hz = 0
        self.actual_hz: Optional[float] = None
        self.error: Optional[str] = None
        self.info: Dict = {}
        self._samples: "deque" = deque(maxlen=KEEP_SAMPLES)
        self._task: Optional[asyncio.Task] = None

    def status(self, samples: bool = True) -> Dict:
        st = {"state": self.state, "variables": [v.to_public() for v in self.variables], "rate_hz": self.rate_hz,
              "actual_hz": self.actual_hz, "error": self.error, **self.info}
        if samples:
            st["samples"] = list(self._samples)
        return st

    def _publish_status(self) -> None:
        self.bus.publish({"type": "live_status", "status": self.status(samples=False)})

    async def start(self, endpoint: Tuple[str, int], variables: List[Variable], rate_hz: int, info: Dict) -> Dict:
        await self.stop()
        self.variables, self.rate_hz, self.info = list(variables), int(rate_hz), dict(info, started=time.time())
        self.state, self.error, self.actual_hz = "running", None, None
        self._samples.clear()
        self._task = asyncio.get_running_loop().create_task(self._run(*endpoint))
        self._publish_status()
        return self.status()

    async def stop(self) -> Dict:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self.state == "running":
            self.state = "stopped"
            self._publish_status()
        return self.status(samples=False)

    def _fail(self, message: str) -> None:
        self.state, self.error = "error", message
        self._publish_status()

    async def _run(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
        except (OSError, asyncio.TimeoutError):
            self._fail("No se pudo conectar con OpenOCD para leer las variables")
            return
        cmd = (read_command(self.variables) + "\x1a").encode()
        period = 1.0 / self.rate_hz
        t0 = next_at = last_pub = loop.time()
        batch: List[list] = []
        failing_since: Optional[float] = None
        count, count_t = 0, t0
        try:
            while True:
                writer.write(cmd)
                await writer.drain()
                reply = (await asyncio.wait_for(reader.readuntil(b"\x1a"), 3))[:-1].decode("utf-8", "replace")
                now = loop.time()
                try:
                    values = decode(self.variables, reply)
                except LiveError as exc:
                    failing_since = failing_since or now
                    if now - failing_since >= GIVE_UP_AFTER_S:
                        self._fail(f"No se pueden leer las variables: {exc}")
                        return
                    await asyncio.sleep(0.2)
                    continue
                failing_since = None
                sample = [round(now - t0, 4)] + [round(v, 6) if isinstance(v, float) else v for v in values]
                self._samples.append(sample)
                batch.append(sample)
                count += 1
                if now - count_t >= 1.0:
                    self.actual_hz, count, count_t = round(count / (now - count_t), 1), 0, now
                if now - last_pub >= PUBLISH_EVERY_S:
                    self.bus.publish({"type": "live", "samples": batch})
                    batch, last_pub = [], now
                next_at += period
                delay = next_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_at = loop.time()                 # más lento que lo pedido: lo más rápido posible
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            self._fail("Se perdió la conexión con OpenOCD (¿se detuvo el servidor o se desconectó el ST-Link?)")
        finally:
            writer.close()
