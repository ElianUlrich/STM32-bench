"""Protocolo GDB/MI: lectura de las líneas que escribe GDB y armado de comandos.

Formato de salida (resumido):
    123^done,bkpt={number="1",line="19"}      resultado del comando 123
    *stopped,reason="breakpoint-hit",frame={...}   evento asíncrono
    =breakpoint-modified,bkpt={...}           notificación
    ~"texto"   @"texto"   &"texto"             consola / programa / log de GDB
    (gdb)                                      fin de un bloque de salida

Los valores son strings estilo C, tuplas {a=..,b=..} o listas [..].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_ESCAPES = {"n": b"\n", "t": b"\t", "r": b"\r", '"': b'"', "\\": b"\\", "a": b"\a", "b": b"\b",
            "f": b"\f", "v": b"\v", "e": b"\x1b"}
_RECORD_RE = re.compile(r"^(\d*)([\^*+=~@&])")


class MiParseError(ValueError):
    pass


@dataclass
class MiRecord:
    kind: str                      # result | exec | status | notify | console | target | log | prompt
    token: Optional[int] = None
    cls: str = ""                  # done, running, error, stopped... (vacío en streams)
    results: Dict[str, Any] = field(default_factory=dict)
    text: str = ""                 # para los streams


_KINDS = {"^": "result", "*": "exec", "+": "status", "=": "notify", "~": "console", "@": "target", "&": "log"}


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise MiParseError(f"se esperaba '{ch}' en la posición {self.i}: {self.s[:120]!r}")
        self.i += 1

    def cstring(self) -> str:
        self.expect('"')
        out = bytearray()
        while True:
            if self.i >= len(self.s):
                raise MiParseError("string sin cerrar")
            ch = self.s[self.i]
            if ch == '"':
                self.i += 1
                return out.decode("utf-8", "replace")
            if ch == "\\":
                nxt = self.s[self.i + 1:self.i + 2]
                if nxt in _ESCAPES:
                    out += _ESCAPES[nxt]
                    self.i += 2
                    continue
                octal = re.match(r"[0-7]{1,3}", self.s[self.i + 1:self.i + 4])
                if octal:                       # GDB escribe los bytes no ASCII en octal (\303\241 = á)
                    out.append(int(octal.group(0), 8) & 0xFF)
                    self.i += 1 + len(octal.group(0))
                    continue
                out += nxt.encode("utf-8")
                self.i += 2
                continue
            out += ch.encode("utf-8")
            self.i += 1

    def name(self) -> str:
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in "={}[],":
            self.i += 1
        return self.s[start:self.i]

    def value(self) -> Any:
        ch = self.peek()
        if ch == '"':
            return self.cstring()
        if ch == "{":
            self.i += 1
            out: Dict[str, Any] = {}
            if self.peek() == "}":
                self.i += 1
                return out
            while True:
                key, val = self.result()
                out[key] = val
                if self.peek() == ",":
                    self.i += 1
                    continue
                self.expect("}")
                return out
        if ch == "[":
            self.i += 1
            items: List[Any] = []
            if self.peek() == "]":
                self.i += 1
                return items
            while True:
                if self.peek() in '"{[':
                    items.append(self.value())
                else:                           # lista de resultados: se descartan los nombres
                    items.append(self.result()[1])
                if self.peek() == ",":
                    self.i += 1
                    continue
                self.expect("]")
                return items
        raise MiParseError(f"valor inesperado en la posición {self.i}: {self.s[:120]!r}")

    def result(self) -> Tuple[str, Any]:
        key = self.name()
        self.expect("=")
        return key, self.value()


def parse_line(line: str) -> Optional[MiRecord]:
    """Una línea de salida de GDB -> MiRecord (None si es una línea vacía)."""
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    if line.strip() == "(gdb)":
        return MiRecord(kind="prompt")
    m = _RECORD_RE.match(line)
    if not m:
        # Texto suelto (p. ej. salida del programa sin formato): se trata como consola.
        return MiRecord(kind="console", text=line + "\n")
    token = int(m.group(1)) if m.group(1) else None
    kind = _KINDS[m.group(2)]
    p = _Parser(line)
    p.i = m.end()
    if kind in ("console", "target", "log"):
        return MiRecord(kind=kind, token=token, text=p.cstring())
    cls = p.name()
    results: Dict[str, Any] = {}
    while p.peek() == ",":
        p.i += 1
        if p.peek() in "{[":                  # +download,{section=...}: valor sin nombre
            results["_"] = p.value()
            continue
        key, val = p.result()
        results[key] = val
    return MiRecord(kind=kind, token=token, cls=cls, results=results)


# ---------------------------------------------------------------------------
# Armado de comandos
# ---------------------------------------------------------------------------

def quote(text: str) -> str:
    """String estilo C para un argumento de comando MI. Sin caracteres de control."""
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("Caracteres de control no permitidos")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
