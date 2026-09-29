"""Conversión de .elf / .hex a imagen binaria plana para dfu-util.

dfu-util (DfuSe) solo carga binarios planos en una dirección. Para .elf y .hex
se extraen los bloques con datos, se validan contra la flash del chip y se
arma una imagen contigua rellenando los huecos con 0xFF (valor de flash
borrada).

Detalles importantes:
  * En un ELF se usa la dirección FÍSICA de cada segmento (p_paddr = LMA), no
    la virtual. La sección .data se ejecuta en RAM (VMA) pero sus valores
    iniciales se guardan en flash (LMA): usar la VMA daría una imagen inválida.
  * Si hay datos fuera de la flash interna (flash externa QSPI, RAM sin AT>,
    bytes de opción, OTP...) la conversión falla: el .bin resultante sería
    gigante o escribiría donde no corresponde.
  * Huecos grandes entre bloques generan un aviso: la imagen igual es válida
    pero se escriben muchos 0xFF (más lento y más desgaste).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

DEFAULT_GAP_WARN_BYTES = 4096
FILL_BYTE = 0xFF


class ConversionError(ValueError):
    """La imagen no se puede convertir de forma segura."""


@dataclass
class Segment:
    address: int
    data: bytes

    @property
    def end(self) -> int:
        return self.address + len(self.data)


@dataclass
class BinImage:
    address: int
    data: bytes
    segments: List[Tuple[int, int]] = field(default_factory=list)   # (inicio, fin)
    warnings: List[str] = field(default_factory=list)


def load_elf_segments(path: Path) -> List[Segment]:
    from elftools.elf.elffile import ELFFile
    from elftools.common.exceptions import ELFError

    try:
        with open(path, "rb") as fh:
            elf = ELFFile(fh)
            if elf.elfclass != 32:
                raise ConversionError("El ELF no es de 32 bits")
            if elf["e_machine"] != "EM_ARM":
                raise ConversionError(f"El ELF no es para ARM ({elf['e_machine']})")
            segments = []
            for seg in elf.iter_segments():
                if seg["p_type"] != "PT_LOAD" or seg["p_filesz"] == 0:
                    continue
                data = seg.data()[: seg["p_filesz"]]
                segments.append(Segment(address=seg["p_paddr"], data=bytes(data)))
    except ELFError as exc:
        raise ConversionError(f"ELF inválido: {exc}")
    if not segments:
        raise ConversionError("El ELF no tiene segmentos cargables con datos")
    return segments


def load_hex_segments(path: Path) -> List[Segment]:
    from intelhex import IntelHex, IntelHexError

    ih = IntelHex()
    try:
        ih.loadhex(str(path))
    except (IntelHexError, ValueError) as exc:
        raise ConversionError(f"Intel HEX inválido: {exc}")
    segments = []
    for start, stop in ih.segments():
        segments.append(Segment(address=start, data=bytes(ih.tobinarray(start=start, size=stop - start))))
    if not segments:
        raise ConversionError("El HEX no contiene datos")
    return segments


def merge_segments(segments: List[Segment]) -> List[Segment]:
    """Ordena y une segmentos contiguos. Error si se solapan con datos distintos."""
    ordered = sorted((s for s in segments if s.data), key=lambda s: s.address)
    merged: List[Segment] = []
    for seg in ordered:
        if merged and seg.address < merged[-1].end:
            raise ConversionError(
                f"Segmentos solapados en 0x{seg.address:08X}; la imagen es ambigua")
        if merged and seg.address == merged[-1].end:
            merged[-1] = Segment(merged[-1].address, merged[-1].data + seg.data)
        else:
            merged.append(Segment(seg.address, seg.data))
    return merged


def build_bin(segments: List[Segment], flash_base: int, flash_end: int,
              gap_warn_bytes: int = DEFAULT_GAP_WARN_BYTES) -> BinImage:
    """Arma la imagen binaria plana y valida que todo esté dentro de la flash."""
    merged = merge_segments(segments)
    if not merged:
        raise ConversionError("No hay datos para programar")

    outside = [s for s in merged if s.address < flash_base or s.end > flash_end]
    if outside:
        detail = ", ".join(f"0x{s.address:08X}-0x{s.end - 1:08X}" for s in outside)
        raise ConversionError(
            f"Hay datos fuera de la flash interna del chip (0x{flash_base:08X}-0x{flash_end - 1:08X}): "
            f"{detail}. Puede ser flash externa, RAM sin dirección de carga o bytes de opción. "
            "Programalo por SWD o generá un .bin solo con la flash interna.")

    warnings: List[str] = []
    for prev, cur in zip(merged, merged[1:]):
        gap = cur.address - prev.end
        if gap > gap_warn_bytes:
            warnings.append(
                f"Segmentos discontinuos: hueco de {gap} bytes entre 0x{prev.end:08X} y "
                f"0x{cur.address:08X}; se rellena con 0xFF. Verificá que sea lo esperado.")

    start = merged[0].address
    image = bytearray([FILL_BYTE]) * (merged[-1].end - start)
    for seg in merged:
        offset = seg.address - start
        image[offset: offset + len(seg.data)] = seg.data
    if start != flash_base:
        warnings.append(f"La imagen empieza en 0x{start:08X} (no en la base de flash 0x{flash_base:08X}).")
    return BinImage(address=start, data=bytes(image),
                    segments=[(s.address, s.end) for s in merged], warnings=warnings)


def convert_to_bin(path: Path, flash_base: int, flash_end: int,
                   gap_warn_bytes: int = DEFAULT_GAP_WARN_BYTES) -> BinImage:
    """Convierte un .elf o .hex en BinImage."""
    ext = path.suffix.lower()
    if ext == ".elf":
        segments = load_elf_segments(path)
    elif ext == ".hex":
        segments = load_hex_segments(path)
    else:
        raise ConversionError(f"No se puede convertir '{ext}' (solo .elf y .hex)")
    return build_bin(segments, flash_base, flash_end, gap_warn_bytes)
