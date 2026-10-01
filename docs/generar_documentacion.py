#!/usr/bin/env python3
"""Genera docs/Banco_STM32_Documentacion.pdf (documento de trabajo del proyecto).

Uso:
    .venv/Scripts/python -m pip install -r docs/requirements-docs.txt
    .venv/Scripts/python docs/generar_documentacion.py

Los diagramas de bloques se dibujan como vectores con reportlab.graphics, así
se ven nítidos a cualquier zoom. Para actualizar el documento a medida que
avanza la puesta en marcha, editar la sección "Bitácora" y ESTADO_PASOS.
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = Path(__file__).resolve().parent / "Banco_STM32_Documentacion.pdf"
FECHA = date(2026, 9, 30)

# ----------------------------------------------------------------------------- fuentes

FONT, FONT_B, FONT_I, MONO = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Courier"


def registrar_fuentes() -> None:
    """Fuentes TTF con acentos, flechas y ·; si no hay, quedan las estándar."""
    global FONT, FONT_B, FONT_I, MONO
    candidatas = {
        "Sans": ["C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
        "Sans-Bold": ["C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
        "Sans-Italic": ["C:/Windows/Fonts/segoeuii.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"],
        "Mono": ["C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
    }
    encontradas = {}
    for nombre, rutas in candidatas.items():
        for ruta in rutas:
            if Path(ruta).is_file():
                pdfmetrics.registerFont(TTFont(nombre, ruta))
                encontradas[nombre] = True
                break
    if len(encontradas) == len(candidatas):
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Italic", boldItalic="Sans-Bold")
        FONT, FONT_B, FONT_I, MONO = "Sans", "Sans-Bold", "Sans-Italic", "Mono"


registrar_fuentes()

# ----------------------------------------------------------------------------- colores

ACENTO = colors.HexColor("#2f7d6d")
ACENTO_OSC = colors.HexColor("#1f5a4e")
ACENTO_SUAVE = colors.HexColor("#e2f0ec")
TEXTO = colors.HexColor("#1d2320")
GRIS = colors.HexColor("#66706a")
GRIS_BORDE = colors.HexColor("#c9cec7")
GRIS_FONDO = colors.HexColor("#f3f4f1")
AZUL_SUAVE = colors.HexColor("#e6eef8")
AZUL = colors.HexColor("#3b6aa0")
AMBAR_SUAVE = colors.HexColor("#fdf1d6")
AMBAR = colors.HexColor("#a06f00")
ROJO_SUAVE = colors.HexColor("#fbe9e6")
ROJO = colors.HexColor("#b23a2e")
VIOLETA_SUAVE = colors.HexColor("#efe9f8")
VIOLETA = colors.HexColor("#6a4c9c")

# ----------------------------------------------------------------------------- estilos

base = getSampleStyleSheet()
ST = {
    "titulo": ParagraphStyle("titulo", fontName=FONT_B, fontSize=26, leading=31, textColor=TEXTO, spaceAfter=10),
    "subtitulo": ParagraphStyle("subtitulo", fontName=FONT, fontSize=13.5, leading=18, textColor=GRIS, spaceAfter=6),
    "h1": ParagraphStyle("h1", fontName=FONT_B, fontSize=16, leading=20, textColor=ACENTO_OSC, spaceBefore=6, spaceAfter=8),
    "h2": ParagraphStyle("h2", fontName=FONT_B, fontSize=12, leading=15, textColor=TEXTO, spaceBefore=10, spaceAfter=4),
    "cuerpo": ParagraphStyle("cuerpo", fontName=FONT, fontSize=10, leading=14.2, textColor=TEXTO, spaceAfter=6),
    "vineta": ParagraphStyle("vineta", fontName=FONT, fontSize=10, leading=14, textColor=TEXTO,
                             leftIndent=14, bulletIndent=4, spaceAfter=2.5),
    "leyenda": ParagraphStyle("leyenda", fontName=FONT_I, fontSize=8.8, leading=11.5, textColor=GRIS,
                              alignment=TA_CENTER, spaceBefore=4, spaceAfter=10),
    "celda": ParagraphStyle("celda", fontName=FONT, fontSize=8.8, leading=11.2, textColor=TEXTO),
    "celda_b": ParagraphStyle("celda_b", fontName=FONT_B, fontSize=8.8, leading=11.2, textColor=TEXTO),
    "codigo": ParagraphStyle("codigo", fontName=MONO, fontSize=8.2, leading=10.6, textColor=TEXTO),
    "nota": ParagraphStyle("nota", fontName=FONT, fontSize=9.3, leading=13, textColor=TEXTO),
}


def P(texto: str, estilo: str = "cuerpo") -> Paragraph:
    return Paragraph(texto, ST[estilo])


def c(texto: str) -> str:
    """Código en línea dentro de un párrafo."""
    texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<font name="{MONO}" size="8.8" color="#1f5a4e">{texto}</font>'


def vinetas(items) -> list:
    return [Paragraph(t, ST["vineta"], bulletText="•") for t in items]


def bloque_codigo(texto: str) -> Table:
    t = Table([[Preformatted(texto.strip("\n"), ST["codigo"])]], colWidths=[170 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GRIS_FONDO),
        ("BOX", (0, 0), (-1, -1), 0.5, GRIS_BORDE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def recuadro(texto: str, fondo=ACENTO_SUAVE, borde=ACENTO) -> Table:
    t = Table([[Paragraph(texto, ST["nota"])]], colWidths=[170 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fondo),
        ("LINEBEFORE", (0, 0), (0, -1), 3, borde),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def tabla(filas, anchos, encabezado=True) -> Table:
    datos = []
    for i, fila in enumerate(filas):
        estilo = "celda_b" if (encabezado and i == 0) else "celda"
        datos.append([Paragraph(str(celda), ST[estilo]) for celda in fila])
    t = Table(datos, colWidths=[a * mm for a in anchos], repeatRows=1 if encabezado else 0)
    estilo = [
        ("GRID", (0, 0), (-1, -1), 0.4, GRIS_BORDE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if encabezado:
        estilo.append(("BACKGROUND", (0, 0), (-1, 0), ACENTO_SUAVE))
    t.setStyle(TableStyle(estilo))
    return t


# ----------------------------------------------------------------------------- dibujo

def caja(d, x, y, w, h, lineas, fondo=colors.white, borde=ACENTO, tam=8.2, negrita=True,
         color_texto=TEXTO, radio=4, grosor=0.9):
    d.add(Rect(x, y, w, h, rx=radio, ry=radio, fillColor=fondo, strokeColor=borde, strokeWidth=grosor))
    alto_linea = tam * 1.28
    total = alto_linea * (len(lineas) - 1)
    base_y = y + h / 2 + total / 2 - tam * 0.35
    for i, texto in enumerate(lineas):
        fuente = FONT_B if (i == 0 and negrita) else FONT
        t = tam if i == 0 else tam - 0.9
        d.add(String(x + w / 2, base_y - i * alto_linea, texto, fontName=fuente, fontSize=t,
                     fillColor=color_texto if i == 0 else GRIS, textAnchor="middle"))


def titulo_grupo(d, x, y, texto, tam=9, color=TEXTO, anchor="start"):
    d.add(String(x, y, texto, fontName=FONT_B, fontSize=tam, fillColor=color, textAnchor=anchor))


def _punta(d, xa, ya, xb, yb, color, tam=5.5):
    ang = math.atan2(yb - ya, xb - xa)
    p1 = (xb - tam * math.cos(ang - 0.42), yb - tam * math.sin(ang - 0.42))
    p2 = (xb - tam * math.cos(ang + 0.42), yb - tam * math.sin(ang + 0.42))
    d.add(Polygon([xb, yb, p1[0], p1[1], p2[0], p2[1]], fillColor=color, strokeColor=color, strokeWidth=0.5))


def flecha(d, puntos, color=GRIS, grosor=1.1, doble=False, trazo=None):
    """Flecha por una lista de puntos [(x, y), ...] con punta al final (y al inicio si doble)."""
    for (x1, y1), (x2, y2) in zip(puntos, puntos[1:]):
        d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=grosor, strokeDashArray=trazo))
    _punta(d, *puntos[-2], *puntos[-1], color)
    if doble:
        _punta(d, *puntos[1], *puntos[0], color)


def etiqueta(d, x, y, texto, tam=7.2, color=GRIS, anchor="middle", negrita=False):
    d.add(String(x, y, texto, fontName=FONT_B if negrita else FONT, fontSize=tam, fillColor=color, textAnchor=anchor))


def diagrama_sistema() -> Drawing:
    """Diagrama 1: bloques del sistema completo (hardware y red)."""
    d = Drawing(495, 300)
    # PC
    caja(d, 0, 5, 115, 285, [], fondo=GRIS_FONDO, borde=GRIS_BORDE)
    titulo_grupo(d, 57.5, 276, "PC del usuario", anchor="middle")
    etiqueta(d, 57.5, 266, "(en otro lugar)")
    caja(d, 10, 204, 95, 36, ["Navegador", "interfaz web"], borde=AZUL)
    caja(d, 10, 152, 95, 36, ["GDB / VS Code", "STM32CubeIDE"], borde=AZUL)
    caja(d, 10, 17, 95, 36, ["Syncthing", "(opcional)"], borde=AZUL)
    # Túnel Tailscale
    caja(d, 130, 5, 80, 285, [], fondo=AZUL_SUAVE, borde=AZUL, grosor=0.7)
    titulo_grupo(d, 170, 276, "Tailscale", color=AZUL, anchor="middle")
    etiqueta(d, 170, 266, "VPN cifrada (100.x.y.z)", tam=6.6)
    etiqueta(d, 170, 14, "sin puertos abiertos", tam=6.4)
    etiqueta(d, 170, 7.5, "a internet", tam=6.4)
    # BeagleBoard-xM
    caja(d, 225, 5, 150, 285, [], fondo=ACENTO_SUAVE, borde=ACENTO)
    titulo_grupo(d, 300, 276, "BeagleBoard-xM", color=ACENTO_OSC, anchor="middle")
    etiqueta(d, 300, 266, "Cortex-A8 · 512 MB · headless", tam=6.6)
    filas = [(222, ["App web (FastAPI)", "puerto 8080 · login"]),
             (170, ["OpenOCD (fork de ST)", "SWD · servidor GDB :3333"]),
             (125, ["dfu-util 0.11", "USB DFU (bootloader ST)"]),
             (80, ["Monitor serie · BootControl", "ttyACM · GPIO"]),
             (35, ["Carpeta de proyectos", ".elf / .hex / .bin"])]
    for yc, lineas in filas:
        caja(d, 235, yc - 16, 130, 32, lineas, borde=ACENTO)
    # La app controla todo lo demás
    flecha(d, [(242, 206), (242, 186)], color=ACENTO, grosor=0.8, trazo=[2, 2])
    # PC <-> placa a través del túnel
    flecha(d, [(105, 222), (235, 222)], color=AZUL, doble=True)
    etiqueta(d, 170, 226, "HTTP + SSE", color=AZUL)
    flecha(d, [(105, 170), (235, 170)], color=AZUL, doble=True)
    etiqueta(d, 170, 174, "GDB remoto", color=AZUL)
    flecha(d, [(105, 35), (235, 35)], color=AZUL)
    etiqueta(d, 170, 39, "sincroniza firmware", color=AZUL)
    # Hardware de programación
    caja(d, 395, 198, 100, 44, ["ST-Link", "V2 / V2-1 / V3"], borde=VIOLETA, fondo=VIOLETA_SUAVE)
    caja(d, 395, 95, 100, 66, ["STM32 (target)", "H503 · H723 ·", "cualquier STM32"], borde=ROJO, fondo=ROJO_SUAVE)
    caja(d, 395, 15, 100, 44, ["Adaptador de nivel", "1,8 V → 3,3 V"], borde=AMBAR, fondo=AMBAR_SUAVE)
    flecha(d, [(365, 170), (381, 170), (381, 220), (395, 220)], color=VIOLETA)
    etiqueta(d, 388, 224, "USB", tam=6.2)
    flecha(d, [(445, 198), (445, 161)], color=VIOLETA)
    etiqueta(d, 450, 176, "SWD", anchor="start", color=VIOLETA, negrita=True)
    flecha(d, [(365, 125), (395, 125)], color=ROJO)
    etiqueta(d, 380, 128.5, "USB", tam=6.2)
    flecha(d, [(365, 80), (381, 80), (381, 37), (395, 37)], color=AMBAR)
    etiqueta(d, 373, 84, "GPIO", tam=6.2)
    flecha(d, [(445, 59), (445, 95)], color=AMBAR)
    etiqueta(d, 450, 72, "BOOT0 / NRST", anchor="start", color=AMBAR, tam=6.8, negrita=True)
    return d


def diagrama_software() -> Drawing:
    """Diagrama 2: bloques de software dentro de la placa."""
    d = Drawing(495, 372)
    ancho = 380
    caja(d, 0, 330, ancho, 34, ["Navegador — HTML + CSS + JavaScript (sin dependencias)",
                                "Target · Archivos · Acciones · GDB · Log en vivo · Serie · Historial · Sistema"],
         borde=AZUL, fondo=AZUL_SUAVE)
    flecha(d, [(120, 330), (120, 304)], color=AZUL)
    etiqueta(d, 124, 314, "pedidos HTTP (fetch)", anchor="start")
    flecha(d, [(260, 304), (260, 330)], color=AZUL)
    etiqueta(d, 264, 314, "eventos SSE (log, estado)", anchor="start")
    caja(d, 0, 270, ancho, 34, ["Middleware de seguridad",
                                "solo red Tailscale · cookie de sesión firmada · header X-Bench · límite de tamaño"],
         borde=ROJO, fondo=ROJO_SUAVE)
    flecha(d, [(190, 270), (190, 256)])
    caja(d, 0, 222, ancho, 34, ["API REST + SSE (FastAPI / uvicorn)",
                                "/api/jobs · /api/gdb · /api/files · /api/probes · /api/serial · /api/system · /api/events"])
    flecha(d, [(190, 222), (190, 208)])
    caja(d, 0, 174, ancho, 34, ["Servicio Bench — valida TODO con listas blancas",
                                "chip · método · interfaz · archivo dentro de proyectos · nº de serie detectado · dirección"],
         fondo=ACENTO_SUAVE)
    modulos = [["JobRunner", "1 trabajo a la vez", "cancelable"],
               ["GdbServer", "OpenOCD", "persistente"],
               ["BootControl", "manual · gpio", "· comando serie"],
               ["Monitor serie", "pyserial", "lectura en hilo"],
               ["Archivos", "validar · subir", "· elf/hex→bin"]]
    externos = [["openocd", "dfu-util"], ["openocd", ":3333"], ["gpiochip", "/ ttyACM"],
                ["/dev/ttyACM*", "VCP Nucleo"], ["proyectos/", "Syncthing"]]
    w, gap = 72, 5
    for i, (mod, ext) in enumerate(zip(modulos, externos)):
        x = i * (w + gap)
        flecha(d, [(x + w / 2, 174), (x + w / 2, 142)])
        caja(d, x, 96, w, 46, mod, tam=7.8)
        flecha(d, [(x + w / 2, 96), (x + w / 2, 70)], color=GRIS)
        caja(d, x, 28, w, 42, ext, fondo=GRIS_FONDO, borde=GRIS_BORDE, tam=7.6)
    etiqueta(d, 190, 14, "procesos externos (subprocess, lista de argumentos, sin shell) y dispositivos",
             tam=7.2)
    # Columna derecha
    caja(d, 395, 262, 100, 102, ["Configuración", "chips.yaml", "bench.yaml", "bench.env", "(secretos, 640)"],
         fondo=AMBAR_SUAVE, borde=AMBAR)
    flecha(d, [(395, 280), (388, 280), (388, 191), (380, 191)], color=AMBAR)
    caja(d, 395, 170, 100, 62, ["EventBus", "log · estado · serie", "→ SSE a la web"], fondo=VIOLETA_SUAVE,
         borde=VIOLETA)
    flecha(d, [(395, 214), (384, 214), (384, 239), (380, 239)], color=VIOLETA)
    caja(d, 395, 96, 100, 46, ["Historial", "history.jsonl"], fondo=GRIS_FONDO, borde=GRIS_BORDE)
    flecha(d, [(380, 119), (395, 119)], color=GRIS, trazo=[2, 2])
    caja(d, 395, 28, 100, 42, ["systemd + sudo", "solo poweroff / reboot"], fondo=GRIS_FONDO, borde=GRIS_BORDE)
    return d


def diagrama_vinculo() -> Drawing:
    """Diagrama 4: cómo se evita grabar un binario en la placa equivocada."""
    d = Drawing(495, 330)
    cols = [(0, ACENTO, ACENTO_SUAVE), (170, AZUL, AZUL_SUAVE), (340, AMBAR, AMBAR_SUAVE)]
    w = 155
    caja(d, 40, 292, 415, 34, ["1 · Subir el archivo eligiendo la placa (o asignarla después)",
                               "si el contenido no corresponde a esa placa, se rechaza explicando el motivo"],
         fondo=GRIS_FONDO, borde=GRIS_BORDE, tam=8.2)
    revisiones = [["2 · STM32 (.elf / .hex / .bin)", "ELF de arquitectura ARM", "direcciones dentro de la flash",
                   "tamaño ≤ flash del chip"],
                  ["2 · ESP (.bin)", "encabezado 0xE9", "ID de chip del encabezado", "offset del bootloader"],
                  ["2 · Arduino / AVR (.hex)", "Intel HEX válido", "entra en la flash del chip",
                   "no pisa el bootloader"]]
    for (x, borde, fondo), lineas in zip(cols, revisiones):
        flecha(d, [(x + w / 2, 292), (x + w / 2, 262)])
        caja(d, x, 200, w, 62, lineas, fondo=fondo, borde=borde, tam=8)
        flecha(d, [(x + w / 2, 200), (x + w / 2, 180)])
    caja(d, 40, 150, 415, 30, ["3 · Vínculo archivo → placa", "firmware_meta.json en /var/lib/stm32-bench"],
         tam=8.2)
    flecha(d, [(247.5, 150), (247.5, 132)])
    caja(d, 40, 98, 415, 34, ["4 · Grabar", "exige: placa asignada = placa elegida · se vuelve a revisar el contenido"],
         fondo=ACENTO_SUAVE, tam=8.2)
    chequeos = [["OpenOCD", "lee DBGMCU_IDCODE", "(0x474 = STM32H503)"],
                ["esptool --chip", "compara con el chip", "que responde"],
                ["avrdude -p", "lee la firma del chip", "(1E 95 0F = ATmega328P)"]]
    for (x, borde, fondo), lineas in zip(cols, chequeos):
        flecha(d, [(x + w / 2, 98), (x + w / 2, 74)])
        caja(d, x, 24, w, 50, lineas, fondo=fondo, borde=borde, tam=8)
    etiqueta(d, 247.5, 8, "5 · Antes de escribir, la herramienta confirma el chip conectado; si no coincide, "
             "aborta sin tocar la flash", tam=7.4, color=TEXTO)
    return d


def diagrama_dfu() -> Drawing:
    """Diagrama 3: flujo de una operación por USB DFU."""
    d = Drawing(495, 352)
    x, w = 125, 245
    cx = x + w / 2
    pasos = [
        (312, ["1 · Pedido desde la web", "chip + método DFU + archivo (+ dirección si es .bin)"], ACENTO_SUAVE),
        (264, ["2 · Validación", "el chip soporta DFU · archivo dentro de proyectos · dirección en la flash"], colors.white),
        (216, ["3 · Conversión .elf / .hex → .bin", "usa la LMA · avisa si hay huecos · rechaza datos fuera de flash"], colors.white),
    ]
    for y, lineas, fondo in pasos:
        caja(d, x, y, w, 34, lineas, fondo=fondo, tam=8)
    flecha(d, [(cx, 312), (cx, 298)])
    flecha(d, [(cx, 264), (cx, 250)])
    flecha(d, [(cx, 216), (cx, 196)])
    # Decisión
    yc = 174
    d.add(Polygon([cx, yc + 22, cx + 82, yc, cx, yc - 22, cx - 82, yc], fillColor=AMBAR_SUAVE,
                  strokeColor=AMBAR, strokeWidth=0.9))
    etiqueta(d, cx, yc + 2, "¿Hay un 0483:df11", tam=8, color=TEXTO, negrita=True)
    etiqueta(d, cx, yc - 8, "presente?", tam=8, color=TEXTO, negrita=True)
    # No -> BootControl
    caja(d, 392, 118, 103, 92, ["4 · BootControl.enter()", "manual: aviso BOOT0", "gpio: BOOT0=1 + NRST",
                                "serie: comando", "esperar DFU (timeout)"], fondo=AMBAR_SUAVE, borde=AMBAR, tam=7.8)
    flecha(d, [(cx + 82, yc), (392, yc)], color=AMBAR)
    etiqueta(d, cx + 96, yc + 4, "no", color=AMBAR, negrita=True)
    flecha(d, [(443, 118), (443, 106), (x + w, 106)], color=AMBAR)
    # Sí -> operación
    flecha(d, [(cx, yc - 22), (cx, 124)])
    etiqueta(d, cx + 6, 138, "sí", anchor="start", color=ACENTO, negrita=True)
    caja(d, x, 90, w, 34, ["5 · dfu-util", "flashear :leave · verificar (leer -U y comparar) · borrar mass-erase"],
         fondo=ACENTO_SUAVE, tam=8)
    flecha(d, [(cx, 90), (cx, 76)])
    caja(d, x, 42, w, 34, ["6 · BootControl.exit() — siempre (finally)", "gpio: BOOT0=0 + reset · manual: recordar bajar BOOT0"],
         fondo=AMBAR_SUAVE, borde=AMBAR, tam=8)
    flecha(d, [(cx, 42), (cx, 32)])
    caja(d, x, 0, w, 32, ["7 · Resultado", "log en vivo (SSE) + historial (ok / error / cancelado)"], tam=8)
    # Nota lateral
    caja(d, 0, 150, 110, 70, ["Un trabajo del JobRunner", "un solo trabajo a la vez", "cancelable desde la web",
                              "con tiempo máximo"], fondo=GRIS_FONDO, borde=GRIS_BORDE, tam=7.8)
    return d


# Estado de la puesta en marcha: (texto, estado) con estado = ok | curso | pendiente
ESTADO_PASOS = [
    (["1 · Imagen", "Ubuntu 16.04"], "ok"),
    (["2 · Reparar", "apt"], "ok"),
    (["3 · Agrandar", "partición"], "ok"),
    (["4 · Copiar", "el código"], "ok"),
    (["5 · Python 3.9", "+ dfu-util 0.11"], "ok"),
    (["6 · Compilar", "OpenOCD (ST)"], "ok"),
    (["7 · install.sh", "servicio + udev"], "ok"),
    (["8 · Tailscale", "acceso remoto"], "ok"),
    (["9 · Conexión SWD", "con el H503"], "ok"),
    (["10 · Flasheo y", "debug remoto"], "curso"),
]
COLORES_ESTADO = {"ok": (ACENTO_SUAVE, ACENTO, "hecho"), "curso": (AMBAR_SUAVE, AMBAR, "en curso"),
                  "pendiente": (GRIS_FONDO, GRIS_BORDE, "pendiente")}


def diagrama_estado() -> Drawing:
    """Diagrama 4: pasos de la puesta en marcha y su estado."""
    d = Drawing(495, 150)
    w, h, gap = 88, 42, 13.75
    for i, (lineas, estado) in enumerate(ESTADO_PASOS):
        fila, col = divmod(i, 5)
        x = col * (w + gap)
        y = 72 if fila == 0 else 8
        fondo, borde, _ = COLORES_ESTADO[estado]
        caja(d, x, y, w, h, lineas, fondo=fondo, borde=borde, tam=7.9, grosor=1.1 if estado != "pendiente" else 0.8)
        if col < 4:
            flecha(d, [(x + w, y + h / 2), (x + w + gap, y + h / 2)], grosor=0.9)
    # Salto de fila (del paso 5 al 6)
    x5 = 4 * (w + gap) + w / 2
    flecha(d, [(x5, 72), (x5, 61), (w / 2, 61), (w / 2, 50)], grosor=0.9)
    # Leyenda
    x = 0
    for estado in ("ok", "curso", "pendiente"):
        fondo, borde, texto = COLORES_ESTADO[estado]
        d.add(Rect(x, 132, 12, 9, fillColor=fondo, strokeColor=borde, strokeWidth=0.9))
        etiqueta(d, x + 16, 133.5, texto, anchor="start", tam=7.6, color=TEXTO)
        x += 80
    return d


def diagrama_conexion() -> Drawing:
    """Diagrama 5: cableado BeagleBoard -> ST-LINK/V2 -> STM32H503."""
    d = Drawing(495, 232)
    # Fuente y BeagleBoard
    caja(d, 0, 182, 120, 40, ["Fuente 5 V", "≥ 2,5 A (ideal 3 A)"], fondo=AMBAR_SUAVE, borde=AMBAR, tam=7.8)
    flecha(d, [(60, 182), (60, 168)], color=AMBAR)
    caja(d, 0, 40, 120, 128, [], fondo=ACENTO_SUAVE, borde=ACENTO)
    titulo_grupo(d, 60, 152, "BeagleBoard-xM", color=ACENTO_OSC, anchor="middle")
    for i, t in enumerate(["LAN9514:", "hub USB + Ethernet", "(comparten", "alimentación)"]):
        etiqueta(d, 60, 130 - i * 10, t, tam=7)
    caja(d, 12, 50, 96, 24, ["USB host"], borde=ACENTO, tam=7.8)
    flecha(d, [(108, 62), (160, 62)], color=VIOLETA, doble=True)
    etiqueta(d, 134, 66, "USB", tam=7)
    # ST-LINK/V2
    caja(d, 160, 40, 120, 172, [], fondo=VIOLETA_SUAVE, borde=VIOLETA)
    titulo_grupo(d, 220, 196, "ST-LINK/V2", color=VIOLETA, anchor="middle")
    etiqueta(d, 220, 186, "firmware V2J46S7 · 0483:3748", tam=6.6)
    etiqueta(d, 220, 176, "conector de 20 pines", tam=6.6)
    # STM32H503
    caja(d, 350, 40, 145, 172, [], fondo=ROJO_SUAVE, borde=ROJO)
    titulo_grupo(d, 422, 196, "STM32H503CBT6", color=ROJO, anchor="middle")
    etiqueta(d, 422, 186, "alimentada aparte (3,3 V)", tam=6.6)
    cables = [(155, "1 · VAPP", "3,3 V", AMBAR, None),
              (130, "7 · SWDIO", "PA13", AZUL, None),
              (105, "9 · SWCLK", "PA14", AZUL, None),
              (80, "4 · GND", "GND", TEXTO, None),
              (55, "15 · NRST", "NRST (opcional)", GRIS, [3, 2])]
    for y, izq, der, color, trazo in cables:
        d.add(Line(280, y, 350, y, strokeColor=color, strokeWidth=1.6, strokeDashArray=trazo))
        etiqueta(d, 274, y - 2.5, izq, anchor="end", tam=7.6, color=TEXTO, negrita=True)
        etiqueta(d, 356, y - 2.5, der, anchor="start", tam=7.6, color=TEXTO, negrita=True)
    caja(d, 440, 60, 48, 100, ["LEDs", "PA0", "…", "PA7"], fondo=colors.white, borde=ROJO, tam=7.4)
    etiqueta(d, 247, 22, "El pin 1 (VAPP) sólo mide la tensión: el ST-LINK/V2 no alimenta al micro.",
             tam=7.2, color=TEXTO)
    etiqueta(d, 247, 11, "Sin GND común el SWD falla aunque la tensión se lea bien.", tam=7.2, color=TEXTO)
    return d


def figura(dibujo: Drawing, leyenda: str):
    return KeepTogether([dibujo, Paragraph(leyenda, ST["leyenda"])])


# ----------------------------------------------------------------------------- contenido

def contenido() -> list:
    s = []
    fecha = FECHA.strftime("%d/%m/%Y")

    # ---- Portada
    s += [Spacer(1, 40 * mm),
          P("Banco de programación y debug remoto para STM32, ESP32 y Arduino", "titulo"),
          P("BeagleBoard-xM · OpenOCD (SWD) · USB DFU · esptool · avrdude · acceso por Tailscale", "subtitulo"),
          Spacer(1, 6 * mm),
          P(f"Documento de trabajo — actualizado al {fecha}", "cuerpo"),
          P("Estado: la aplicación está instalada y funcionando en la BeagleBoard-xM, con acceso remoto por "
            "Tailscale, y la conexión SWD con el STM32H503 está verificada. En curso: flasheo del firmware de "
            "prueba y depuración remota con GDB. Se agregó soporte para ESP32/ESP8266 y Arduino (AVR), con cada "
            "binario vinculado a su placa; está probado en simulación y falta probarlo con placas reales.", "cuerpo"),
          Spacer(1, 10 * mm),
          recuadro("<b>Resumen.</b> Una BeagleBoard-xM sin pantalla ni teclado, ubicada en un banco de laboratorio, "
                   "funciona como programador y depurador remoto de microcontroladores STM32, ESP32 y Arduino. Desde el navegador de "
                   "una PC en otro lugar, conectada por una VPN Tailscale, se sube el firmware, se programa el chip "
                   "por ST-Link (SWD), por el bootloader USB DFU de ST o por puerto serie (esptool para ESP32, avrdude "
                   "para Arduino), se depura con GDB, se ve la salida en vivo "
                   "y un monitor serie, y se controla la placa (apagar / reiniciar)."),
          Spacer(1, 12 * mm),
          P("<b>Contenido</b>", "h2")]
    indice = ["Resumen en siete pasos", "1. La idea", "2. Arquitectura del sistema (diagrama de bloques)",
              "3. Arquitectura de software (diagrama de bloques)", "4. Métodos de programación: STM32, ESP32 y Arduino",
              "5. Seguridad", "6. Configuración", "7. Desarrollo y pruebas sin hardware",
              "8. Puesta en marcha de la BeagleBoard-xM (bitácora)", "9. Firmware de prueba para el STM32H503",
              "10. Lecciones aprendidas", "11. Próximos pasos",
              "Anexo A. Estructura del repositorio", "Anexo B. Comandos útiles"]
    s += [P(t, "vineta") for t in indice]
    s.append(PageBreak())

    # ---- Resumen en siete pasos
    s += [P("Resumen en siete pasos", "h1"),
          P("Una versión corta de todo el proyecto (sirve también como guion para presentarlo)."),
          tabla([["Paso", "Qué se hizo"],
                 ["0 · La idea", "Programar y depurar un STM32 desde cualquier lugar: el micro queda en el banco de "
                                 "laboratorio conectado a una BeagleBoard y se controla desde el navegador de la PC."],
                 ["1 · Hardware", "BeagleBoard-xM sólo con alimentación y red, sin pantalla ni teclado. Un ST-LINK/V2 "
                                  "en su USB y cuatro cables al STM32H503: SWDIO, SWCLK, 3,3 V y GND."],
                 ["2 · Preparar la placa", "La placa tenía Ubuntu 16.04 (2017). Se agrandó la partición y se "
                                           "compiló en la placa lo que faltaba: Python 3.9, dfu-util y el OpenOCD "
                                           "de ST."],
                 ["3 · Acceso remoto", "Tailscale: red privada cifrada. La placa no queda expuesta a internet; "
                                       "sólo los equipos de la cuenta pueden entrar."],
                 ["4 · Aplicación web", "Corre en la placa: subir firmware, elegir chip y método, Flashear, "
                                        "Verificar, Borrar, Reset, con el log de OpenOCD en vivo; monitor serie, "
                                        "historial y apagado seguro."],
                 ["5 · Firmware de prueba", "Tres programas cortos para el H503 con LEDs en PA0–PA7: parpadeo, luz "
                                            "que va y viene y contador binario."],
                 ["6 · El problema real", "Desde la PC conectaba y desde la placa no: faltaba la masa común (GND) "
                                          "entre el ST-Link y la placa del micro. Con ese cable, conectó."],
                 ["7 · Resultado", "Flashear desde el navegador, iniciar el servidor GDB y, desde la PC, detener el "
                                   "micro, leer una variable, cambiarla y ver a los LEDs responder."]],
                [38, 132]),
          Spacer(1, 6),
          recuadro("El chip se elige desde un archivo de configuración, así que el banco sirve para cualquier STM32 y, "
                   "desde la última versión, también para ESP32/ESP8266 y Arduino (28 placas en el catálogo). "
                   "Todo el código, los scripts y este documento están versionados en GitHub "
                   "(repositorio privado ElianUlrich/STM32-bench).")]
    s.append(PageBreak())

    # ---- 1. La idea
    s += [P("1. La idea", "h1"),
          P("<b>El problema.</b> Los microcontroladores con los que se trabaja están conectados en un banco de "
            "pruebas de laboratorio, pero quien los programa y depura está en otro lugar. Sin una herramienta "
            "remota, cada cambio de firmware exige estar físicamente frente al banco."),
          P("<b>La propuesta.</b> Convertir una BeagleBoard-xM en un <i>banco de programación y debug remoto</i>: "
            "la placa queda conectada al programador (ST-Link) y al STM32, sólo con alimentación de 5 V y cable de "
            "red, y ofrece una aplicación web accesible únicamente a través de una VPN (Tailscale). Todo lo que se "
            "haría con el programador enchufado a la PC se hace desde el navegador."),
          P("Objetivos", "h2")]
    s += vinetas([
        "<b>Programar</b> cualquier STM32: flashear (program + verify + reset), sólo verificar, borrar la flash "
        "completa, reset, halt y run.",
        "<b>Dos métodos de conexión</b> seleccionables por chip o por operación: ST-Link por SWD con OpenOCD "
        "(programación y debug) y USB DFU con dfu-util (sólo programación, sin debug).",
        "<b>Otras familias</b>: ESP32/ESP8266 por puerto serie con esptool y Arduino (AVR) con avrdude, por "
        "su bootloader o con un programador ISP.",
        "<b>No grabar el micro equivocado</b>: cada binario se sube indicando para qué placa es; la app revisa "
        "el contenido y, antes de escribir, confirma el chip conectado.",
        "<b>Depurar remotamente</b> con GDB: arm-none-eabi-gdb, VS Code (Cortex-Debug) o STM32CubeIDE.",
        "<b>Gestionar el firmware</b>: subir .elf/.hex/.bin arrastrando al navegador o sincronizar una carpeta "
        "con Syncthing.",
        "<b>Ver en vivo</b> la salida de OpenOCD/dfu-util, un historial de operaciones y un monitor serie.",
        "<b>Controlar la placa</b>: estado del sistema, apagar y reiniciar con confirmación.",
        "<b>Soporte configurable</b>: agregar un chip nuevo es agregar una entrada a un archivo YAML.",
        "<b>Seguridad</b>: sin exposición a internet, login, y ningún comando armado con texto del usuario.",
    ])
    s += [P("Requisitos y restricciones", "h2"),
          tabla([["Aspecto", "Detalle"],
                 ["Placa", "BeagleBoard-xM: TI DM3730 (Cortex-A8, ARMv7, armhf), 512 MB de RAM, arranque desde "
                           "microSD, 4 puertos USB host, Ethernet. Sin pantalla, teclado ni mouse (headless)."],
                 ["Programador", "ST-Link V2 / V2-1 / V3, integrado en Nucleo/Discovery o externo, por USB."],
                 ["Chips actuales", "STM32H503CBT6 (Cortex-M33, serie H5) y STM32H723VGT6 (Cortex-M7, serie H7); "
                                    "además ESP32 (todas las variantes) y Arduino Uno, Nano, Mega, Leonardo, etc."],
                 ["Red", "La PC y la placa se conectan por Tailscale. No se abren puertos en el router."],
                 ["Liviandad", "Pocos recursos: backend Python (FastAPI), frontend sin compilación ni Node."],
                 ["Operación", "Servicios con systemd; arranque automático; usuario sin privilegios."]],
                [35, 135])]
    s.append(PageBreak())

    # ---- 2. Arquitectura del sistema
    s += [P("2. Arquitectura del sistema", "h1"),
          P("La figura 1 muestra los bloques del sistema completo. A la izquierda, la PC del usuario; en el "
            "medio, el túnel cifrado de Tailscale; a la derecha, la BeagleBoard-xM con los procesos que corren "
            "en ella y el hardware de programación conectado a sus puertos USB."),
          figura(diagrama_sistema(), "Figura 1 — Diagrama de bloques del sistema. Las flechas azules pasan por "
                 "la VPN; las de la derecha son conexiones físicas (USB, SWD y GPIO)."),
          P("Bloques", "h2")]
    s += vinetas([
        "<b>PC del usuario</b>: navegador para la interfaz web y cliente GDB (arm-none-eabi-gdb, VS Code o "
        "CubeIDE). Opcionalmente, Syncthing para sincronizar la carpeta de compilación.",
        "<b>Tailscale</b>: VPN de malla (WireGuard). La placa recibe una IP 100.x.y.z accesible sólo desde los "
        "equipos de la cuenta; no hay que abrir puertos en el router.",
        "<b>App web</b>: servicio systemd en el puerto 8080. Orquesta todo lo demás.",
        "<b>OpenOCD</b> (fork oficial de ST, el de STM32CubeIDE): programa por SWD y, en modo servidor, expone "
        "el puerto GDB 3333. Se compila en la placa porque el de los repositorios es viejo para H5/H7.",
        "<b>dfu-util</b>: programa por USB a través del bootloader de sistema del STM32 (VID:PID 0483:df11).",
        "<b>esptool y avrdude</b>: programan ESP32/ESP8266 y Arduino por el puerto USB-serie de cada placa "
        "(/dev/ttyUSB* o /dev/ttyACM*). No aparecen en la figura para no recargarla: se conectan igual que "
        "el monitor serie.",
        "<b>ST-Link</b>: conectado por USB a la placa y por SWD (SWDIO, SWCLK, GND, NRST) al STM32.",
        "<b>USB del propio STM32</b> (PA11/PA12): usado para DFU. En una Nucleo es un conector distinto al del "
        "ST-Link, así que pueden estar los dos enchufados.",
        "<b>Adaptador de nivel</b> (opcional, para BootControl por GPIO): los GPIO de la BeagleBoard-xM son de "
        "1,8 V y BOOT0/NRST del STM32 son de 3,3 V; no se pueden conectar directo.",
    ])
    s.append(PageBreak())

    # ---- 3. Arquitectura de software
    s += [P("3. Arquitectura de software", "h1"),
          P("Dentro de la placa, la aplicación está organizada en capas (figura 2). Cada pedido del navegador "
            "atraviesa el middleware de seguridad, llega a la API y se valida en el servicio central "
            "(<i>Bench</i>) antes de construir cualquier comando. Los procesos externos se lanzan siempre con una "
            "lista de argumentos, nunca a través de una shell."),
          figura(diagrama_software(), "Figura 2 — Bloques de software en la BeagleBoard-xM."),
          KeepTogether([P("Decisiones técnicas", "h2"),
          tabla([["Decisión", "Motivo"],
                 ["FastAPI + uvicorn (sin extras)", "Asincronismo nativo para SSE, procesos largos y el monitor "
                                                    "serie. ~40–60 MB de RAM. Sin extensiones que compilar en armhf."],
                 ["Server-Sent Events (SSE)", "El log es de una sola vía; SSE no necesita librerías y el "
                                              "navegador reconecta solo. El envío por serie usa POST."],
                 ["Frontend HTML + CSS + JS sin dependencias", "Sin Node ni compilación; se sirve desde el mismo "
                                                               "backend. Dimensionado para 1366×768."],
                 ["Contraseña con scrypt (hashlib)", "KDF recomendado por OWASP, en la biblioteca estándar: no hay "
                                                     "que compilar bcrypt/argon2 en armhf."],
                 ["Un trabajo a la vez (409 si está ocupado)", "Nunca se pisan dos sesiones del programador; "
                                                               "encolar flasheos a ciegas es más riesgoso."],
                 ["Procesos con Popen + hilo lector", "Mismo código en Linux (placa) y Windows (desarrollo), "
                                                      "salida línea a línea al log en vivo."],
                 ["Modo simulación (BENCH_MOCK=1)", "Simuladores de OpenOCD, dfu-util, ST-Link, DFU, serie y "
                                                    "sistema para desarrollar y probar sin hardware."]],
                [55, 115])]),
          Spacer(1, 8)]

    # ---- 4. Métodos
    s += [P("4. Métodos de programación: STM32, ESP32 y Arduino", "h1"),
          P("STM32: ST-Link (SWD) y USB DFU", "h2"),
          P("Cada chip declara en <b>chips.yaml</b> qué métodos soporta y sus parámetros. La interfaz sólo "
            "habilita los métodos disponibles y, con DFU, deshabilita lo que el bootloader no puede hacer, "
            "explicando el motivo."),
          tabla([["Acción", "ST-Link (SWD) — OpenOCD", "USB DFU — dfu-util"],
                 ["Flashear", "program … verify reset exit", "-s 0x08000000:leave -D firmware.bin"],
                 ["Verificar", "halt · verify_image · resume", "leer con -U y comparar"],
                 ["Borrar flash", "flash erase_sector &lt;banco&gt; 0 last", "-s 0x08000000:mass-erase:force"],
                 ["Reset / Halt / Run", "sí", "no (sin acceso al núcleo)"],
                 ["Servidor GDB", "sí (puerto 3333)", "no"],
                 [".elf / .hex", "directo", "se convierten a .bin en la placa"]],
                [32, 70, 68]),
          Spacer(1, 10),
          figura(diagrama_dfu(), "Figura 3 — Flujo de una operación por USB DFU."),
          P("Ejemplo de comando construido por la app para flashear un STM32H723 (lista de argumentos; los "
            "valores entre llaves son literales en Tcl):"),
          bloque_codigo("""
openocd -s /usr/local/share/openocd/scripts
        -f interface/stlink-dap.cfg  -c "transport select dapdirect_swd"
        -c "adapter serial 066DFF485550755187121832"
        -f target/stm32h7x.cfg       -c "adapter speed 4000"
        -c "program {/srv/stm32-bench/proyectos/demo/Debug/demo.elf} verify reset exit"
"""),
          Spacer(1, 4),
          recuadro("<b>Hallazgo al revisar el fork de ST (tag openocd-cubeide-v2.2.0):</b> la CPU del STM32H5 está "
                   "en el AP 1 del DAP, por lo que el modo HLA (" + c("interface/stlink.cfg") + ") no sirve; hace "
                   "falta " + c("stlink-dap.cfg") + " con " + c("transport select dapdirect_swd") + ", que ese "
                   "archivo no elige solo. El ST-Link se selecciona con " + c("adapter serial") + ".",
                   fondo=AZUL_SUAVE, borde=AZUL),
          P("Backends de BootControl (cómo se entra al bootloader)", "h2"),
          tabla([["Backend", "Cómo funciona", "Requisitos"],
                 ["manual", "La web muestra “Poné BOOT0 en alto y reseteá la placa” con cuenta regresiva y sigue "
                            "sola cuando aparece el dispositivo DFU.", "Una persona en el banco."],
                 ["gpio", "La placa maneja BOOT0 y NRST con libgpiod (/dev/gpiochipN, nunca sysfs): BOOT0=1, pulso "
                          "de NRST, espera DFU; al final BOOT0=0 y reset.",
                  "Adaptador 1,8 V → 3,3 V (NPN en NRST, traductor no inversor en BOOT0)."],
                 ["serial_command", "Envía un comando configurable (p. ej. “BOOTLOADER\\n”) por el puerto serie "
                                    "al firmware, que salta al bootloader de sistema.",
                  "Soporte en el firmware (dirección de System Memory según AN2606)."]],
                [26, 92, 52]),
          Spacer(1, 10)]

    s += [P("ESP32 / ESP8266 (esptool) y Arduino / AVR (avrdude)", "h2"),
          P("Se agregaron dos métodos más, que usan el puerto USB-serie de cada placa. Cada placa del catálogo "
            "declara su método y sus parámetros; la interfaz agrupa las placas por familia, pide el puerto serie y "
            "deshabilita (explicando el motivo) lo que el método no puede hacer."),
          tabla([["Acción", "ESP32 / ESP8266 — esptool", "Arduino — avrdude"],
                 ["Flashear", "write_flash en 0x10000 (app) o 0x0 (imagen combinada), modo/frecuencia/tamaño "
                              "“keep”", "-U flash:w:archivo.hex:i (con -D en el Mega)"],
                 ["Verificar", "verify_flash (compara el hash)", "-U flash:v:archivo.hex:i"],
                 ["Borrar", "erase_flash", "sólo con programador ISP (-e); el bootloader no borra"],
                 ["Reset", "reset por DTR/RTS", "pulso de DTR (o toque de 1200 baudios en 32U4)"],
                 ["Identificar", "flash_id con --chip auto", "lee la firma del chip"],
                 ["Halt / Run / GDB", "no (haría falta JTAG)", "no"]],
                [26, 76, 68]),
          Spacer(1, 6),
          *vinetas([
              "<b>Catálogo</b>: 28 placas en chips.yaml: 7 STM32, 11 Espressif (ESP8266, ESP32, S2, S3, C2, C3, C5, "
              "C6, C61, H2, P4) y 10 AVR (Uno, Nano con bootloader nuevo y viejo, Pro Mini, Duemilanove, Mega 2560 "
              "y 1280, Leonardo, Micro y un ATmega328P genérico con USBasp).",
              "<b>Qué archivo se sube</b>: en Arduino IDE, <i>Exportar binario compilado</i>. Para ESP32, el "
              ".ino.bin (sólo la aplicación) o el .ino.merged.bin (bootloader + particiones + app); la app "
              "reconoce cuál es y elige la dirección. Para AVR, el .ino.hex sin bootloader.",
              "<b>Leonardo y Micro</b> (ATmega32U4): la app abre el puerto a 1200 baudios para que el chip salte "
              "al bootloader, espera el puerto nuevo que aparece y graba ahí.",
              "<b>Monitor serie</b>: si tiene abierto el mismo puerto, se cierra durante la grabación y se vuelve "
              "a abrir al terminar.",
              "<b>Instalación</b>: avrdude desde apt; esptool 4.12 con pip --no-deps, porque su dependencia "
              "cryptography (sólo la usa espsecure) no tiene wheel para la placa.",
          ])]

    s += [P("Vínculo binario ↔ placa", "h2"),
          P("Con placas de tres familias en el mismo banco, grabar un binario en el micro equivocado es fácil. "
            "Por eso cada archivo se sube indicando para qué placa es, y la protección tiene tres capas "
            "(figura 4): se revisa el contenido del archivo, se exige que la placa elegida sea la del archivo y, "
            "antes de escribir, la herramienta confirma el chip conectado. En STM32 esta última capa es nueva: "
            "OpenOCD lee el registro DBGMCU_IDCODE y aborta con “CHIP EQUIVOCADO” si no coincide con el "
            "esperado."),
          figura(diagrama_vinculo(), "Figura 4 — Las tres capas que evitan grabar un binario en la placa equivocada."),
          recuadro("<b>Limitación.</b> Un .hex de AVR no dice para qué chip fue compilado: un sketch de Uno y uno de "
                   "Nano (el mismo ATmega328P) son indistinguibles. Ahí la protección es la asignación al subirlo "
                   "más la firma que verifica avrdude. En el ESP8266 el encabezado tampoco trae el ID de chip.",
                   fondo=AMBAR_SUAVE, borde=AMBAR),
          Spacer(1, 8)]

    # ---- 5. Seguridad
    s += [P("5. Seguridad", "h1"),
          P("La aplicación puede ejecutar comandos en la placa, así que la seguridad se diseñó en capas:")]
    s += vinetas([
        "<b>Red</b>: acceso sólo por Tailscale; no se abren puertos en el router. Además la app rechaza "
        "conexiones que no vengan de las redes de Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48) o de localhost.",
        "<b>Autenticación</b>: usuario y contraseña; hash scrypt (o PBKDF2-HMAC-SHA256 con 600.000 "
        "iteraciones si el Python no trae scrypt, como en la placa); cookie de sesión firmada, HttpOnly, "
        "SameSite=Strict, con vencimiento; límite de intentos fallidos por IP. Sin credenciales en el código: "
        "van en /etc/stm32-bench/bench.env (permisos 640).",
        "<b>CSRF</b>: toda petición que modifica algo exige el header X-Bench (un sitio ajeno no puede "
        "agregarlo sin CORS, que no está habilitado).",
        "<b>Inyección de comandos</b>: subprocess con lista de argumentos y sin shell; chips, interfaces y "
        "backends sólo desde los YAML; rutas resueltas dentro del directorio de proyectos (sin “..” ni "
        "symlinks hacia afuera); lista blanca de caracteres en los nombres, porque los argumentos -c de OpenOCD "
        "son código Tcl y una ruta con [ ] { } $ podría inyectar Tcl.",
        "<b>Privilegios</b>: el servicio corre como usuario stm32bench (grupos plugdev, dialout, gpio). sudo "
        "sólo permite poweroff y reboot sin argumentos, validado con visudo -c.",
        "<b>GDB</b>: el puerto 3333 no tiene autenticación, por eso escucha sólo en la IP de Tailscale y "
        "OpenOCD se lanza con telnet y Tcl deshabilitados. Se recomiendan ACL de Tailscale.",
        "<b>Cabeceras HTTP</b>: Content-Security-Policy estricta, X-Frame-Options DENY, nosniff, no-referrer.",
    ])

    # ---- 6. Configuración
    s += [PageBreak(),
          P("6. Configuración", "h1"),
          P("Los archivos viven en /etc/stm32-bench/ (en el repositorio, en config/). Agregar un chip es "
            "agregar una entrada:"),
          bloque_codigo("""
- id: stm32h723vg
  name: STM32H723VGT6
  family: H7 (Cortex-M7)
  flash: { base: 0x08000000, size_kb: 1024, sector_kb: 128 }
  default_method: swd
  methods:
    swd:
      target_cfg: target/stm32h7x.cfg      # relativo a los scripts de OpenOCD
      interface: stlink-dap                # definida en bench.yaml
      adapter_speed_khz: 4000
      flash_banks: [0]                     # bancos a borrar
    dfu:
      vid_pid: "0483:df11"
      alt: 0                               # "Internal Flash"
"""),
          Spacer(1, 6),
          tabla([["Archivo", "Contenido"],
                 ["chips.yaml", "Catálogo de 28 placas: STM32 (H503, H723 y ejemplos F1, F4, L4, G0, G4, cada uno con "
                                "su IDCODE), ESP32/ESP8266 y Arduino. Al arrancar se verifica que los .cfg "
                                "existan en el OpenOCD instalado."],
                 ["bench.yaml", "Binarios y timeouts de OpenOCD, dfu-util, esptool y avrdude, interfaces (stlink = HLA, stlink-dap = "
                                "dapdirect), puerto y bind del GDB, tamaño máximo de subida, baudrates, redes "
                                "permitidas, BootControl."],
                 ["bench.env", "Usuario, hash de la contraseña, clave de sesión y rutas. Lo genera install.sh."]],
                [30, 140])]
    s.append(PageBreak())

    # ---- 7. Desarrollo y pruebas
    s += [P("7. Desarrollo y pruebas sin hardware", "h1"),
          P("Para desarrollar en la PC (Windows) sin la placa, el <b>modo simulación</b> (BENCH_MOCK=1) reemplaza "
            "OpenOCD, dfu-util, esptool y avrdude por simuladores que se ejecutan como procesos reales, así el camino de "
            "ejecución y el log en vivo son los mismos que en la placa. También simula dos ST-Link, la aparición "
            "del dispositivo DFU al entrar al bootloader, un puerto serie y los datos del sistema. Se crean "
            "proyectos de ejemplo, incluidos casos de error (hueco entre segmentos, datos en flash externa, "
            "verificación fallida)."),
          bloque_codigo("powershell -ExecutionPolicy Bypass -File scripts\\dev_run.ps1   # http://127.0.0.1:8080"),
          Spacer(1, 6),
          P("Pruebas automáticas", "h2"),
          P("198 tests con pytest, todos pasando (uno se omite en Windows). Cubren:")]
    s += vinetas([
        "Validación de archivos: nombres, extensiones, tamaño, contenido (ELF ARM de 32 bits, Intel HEX), "
        "path traversal y symlinks.",
        "Construcción de comandos de OpenOCD y dfu-util: orden de argumentos, direcciones, bloqueo de "
        "inyección Tcl, parseo de dfu-util -l.",
        "Carga y validación de chips.yaml y bench.yaml; detección de .cfg faltantes.",
        "Selección de método según el chip y capacidades de cada método.",
        "Conversión .elf/.hex → .bin: uso de la LMA, huecos, datos fuera de flash, solapamientos.",
        "BootControl: secuencia de GPIO con un driver falso, manual, comando serie, timeout, cancelación y flujo "
        "DFU completo con el simulador.",
        "API: login, redes permitidas, CSRF, trabajos concurrentes (409), bloqueo por servidor GDB activo, "
        "monitor serie, apagado.",
        "Autenticación: hash scrypt y respaldo PBKDF2 cuando hashlib.scrypt no existe.",
        "ESP32 y Arduino: análisis de .bin (ID de chip, imagen combinada, offset del bootloader) y de .hex "
        "(tamaño, bootloader), comandos de esptool y avrdude, puertos inválidos, toque de 1200 baudios y "
        "liberación del monitor serie.",
        "Vínculo binario ↔ placa: subida sin placa, placa incompatible, reasignación, grabación rechazada en "
        "otra placa, firma AVR distinta y chip STM32 equivocado (IDCODE).",
    ])
    s += [P("Pruebas en el navegador", "h2"),
          P("Con la app en modo simulación se recorrieron los flujos principales: flasheo por SWD con log en vivo, "
            "flasheo por DFU con el diálogo “Poné BOOT0 en alto…” (que se cierra solo al aparecer el "
            "dispositivo) y el aviso de segmentos discontinuos, servidor GDB (muestra IP:3333) y el pedido de "
            "detenerlo al flashear, monitor serie, historial y estado del sistema, a 1366×678."),
          P("Problemas encontrados y corregidos en esas pruebas:"),
          *vinetas([
              "El diálogo de confirmación dependía del evento <i>close</i> del elemento &lt;dialog&gt;, que el "
              "navegador no emite si la pestaña no se está dibujando: ahora se resuelve desde los botones.",
              "El navegador usaba CSS/JS viejos de la caché: los archivos estáticos ahora se sirven con "
              "Cache-Control: no-cache (revalidación con ETag).",
              "La lista de archivos repetía el nombre del proyecto como subcarpeta.",
          ]),
          P("Con ESP32 y Arduino se probó: grabar un Uno desde la web, que al elegir un archivo se seleccione "
            "sola su placa, que un binario de ESP32-S3 no se pueda grabar en un ESP32 (el botón queda "
            "deshabilitado con el motivo) y grabar una imagen combinada de ESP32 en 0x0 con el hash verificado. "
            "Se corrigieron dos errores: la validación al subir usaba la extensión del archivo temporal (.part) "
            "y el puerto serie elegido para una placa se arrastraba a otra (ahora se recuerda por placa).")]
    s.append(PageBreak())

    # ---- 8. Bitácora
    s += [P("8. Puesta en marcha de la BeagleBoard-xM (bitácora)", "h1"),
          figura(diagrama_estado(), f"Figura 5 — Pasos de la puesta en marcha y su estado al {fecha}."),
          P("8.1 Estado inicial encontrado", "h2"),
          tabla([["Elemento", "Encontrado"],
                 ["Sistema", "Ubuntu 16.04 LTS, imagen de consola de rcn-ee (Robert C. Nelson) del 23/01/2017."],
                 ["Kernel", "4.9.5-armv7-x4 (armv7l)."],
                 ["Memoria", "483 MB utilizables, sin swap."],
                 ["Almacenamiento", "microSD de 3,7 GB, pero la partición raíz ocupaba sólo 1,6 GB (474 MB libres)."],
                 ["Python", "3.5.2 (la app necesita 3.9 o superior)."],
                 ["Red", "eth0 192.168.0.27/24 (LAN) y usb0 192.168.7.2 (red por USB de la imagen)."],
                 ["apt", "Instalación de paquetes interrumpida y un repositorio https sin soporte instalado."]],
                [32, 138]),
          Spacer(1, 6),
          P("8.2 Decisión: mantener Ubuntu 16.04", "h2"),
          P("Se evaluó instalar un sistema más nuevo, pero la página oficial de imágenes de BeagleBoard.org ya no "
            "publica imágenes para la xM; una Debian nueva habría que armarla a partir del sistema base de rcn-ee "
            "más kernel y bootloader. Actualizar en el lugar (16.04 → 18.04 → 20.04 → 22.04) es lento y riesgoso "
            "con un kernel no estándar. Como los repositorios de 16.04 siguen respondiendo, se decidió conservar "
            "el sistema y agregar un Python 3.9 compilado aparte (la última versión que compila con el OpenSSL "
            "1.0.2 de 16.04). El acceso sólo por Tailscale compensa la falta de actualizaciones de seguridad."),
          P("8.3 Acceso y reparación de apt", "h2"),
          P("Con monitor y teclado se ingresó con el usuario por defecto de la imagen (indicado en la pantalla de "
            "login) y luego se trabajó por SSH desde la PC, para copiar y pegar comandos. Se recomienda cambiar "
            "la contraseña de fábrica con " + c("passwd") + "."),
          bloque_codigo("""
ssh ubuntu@192.168.0.27
sudo dpkg --configure -a
sudo apt install -y apt-transport-https ca-certificates
sudo apt update          # OK: ports.ubuntu.com (xenial) y repos.rcn-ee.com responden
"""),
          P("8.4 Agrandar la partición a toda la microSD", "h2"),
          bloque_codigo("""
sudo apt install -y cloud-guest-utils tmux
sudo growpart /dev/mmcblk0 2        # CHANGED: partition=2 ... new: size=7690207
sudo resize2fs /dev/mmcblk0p2       # redimensionado en línea, sin reiniciar
df -h /                             # 3,6 GB de tamaño, 2,4 GB libres
"""),
          P("8.5 Copia del código a la placa", "h2"),
          P("El repositorio todavía no está publicado; se empaquetó en la PC y se copió por SSH (219 KB)."),
          bloque_codigo("""
# En la PC (PowerShell)
tar --exclude=.venv --exclude=dev-data --exclude=.git -czf $env:USERPROFILE\\Desktop\\stm32-bench.tgz
    -C $env:USERPROFILE\\Desktop\\Debugger .
scp $env:USERPROFILE\\Desktop\\stm32-bench.tgz ubuntu@192.168.0.27:~
# En la placa
mkdir -p ~/stm32-bench && tar -xzf ~/stm32-bench.tgz -C ~/stm32-bench && cd ~/stm32-bench
"""),
          P("8.6 Preparación del sistema (completada)", "h2"),
          P("Se agregó el script " + c("scripts/prepare_ubuntu1604.sh") + ", que se ejecuta dentro de tmux "
            "(si se corta el SSH, el trabajo sigue; se retoma con " + c("tmux attach -t prep") + "):"),
          *vinetas([
              "Instala las dependencias de compilación y crea un swap temporal de 512 MB.",
              "Descarga Python 3.9.25, verifica su SHA-256, lo compila e instala en /opt/python3.9 sin tocar el "
              "Python del sistema (1 a 2 horas en la xM).",
              "Compila dfu-util 0.11 en /usr/local (el de 16.04 es 0.8 y no tiene mass-erase), verificando su MD5.",
              "Borra los archivos de compilación para ahorrar espacio.",
          ]),
          bloque_codigo("""
tmux new -s prep
sudo bash scripts/prepare_ubuntu1604.sh
"""),
          P("<b>Resultado:</b> terminó en unos 72 minutos (20:02 → 21:14, hora de la placa). Quedaron "
            "Python 3.9.25 con OpenSSL 1.0.2g (todos los módulos necesarios cargan) y dfu-util 0.11. Durante la "
            "compilación sólo aparecieron advertencias del compilador (variables sin usar, strict-aliasing), "
            "sin errores; las advertencias de pip por ejecutarse como root son esperables porque la app usa su "
            "propio entorno virtual."),
          P("8.7 Tailscale (hecho en paralelo a la compilación)", "h2"),
          P("Desde otra sesión SSH, mientras compilaba Python. La imagen no traía " + c("curl") + " (se agregó al "
            "script de preparación). El repositorio de Tailscale todavía publica paquetes para Ubuntu 16.04 "
            "armhf (versión 1.102.4)."),
          bloque_codigo("""
sudo apt install -y curl
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=banco-stm32     # se autoriza abriendo el enlace en la PC
"""),
          P("En la consola de Tailscale (Machines → banco-stm32 → ⋯ → Disable key expiry) se desactivó el "
            "vencimiento de la clave, para que la placa no quede fuera de la red. La placa obtuvo la IP "
            "<b>100.99.235.81</b> y se verificó el acceso remoto desde la PC con " + c("ssh ubuntu@100.99.235.81") +
            ". Desde ahí se usa esa IP (o el nombre banco-stm32) en lugar de la IP de la red local."),
          P("Otros ajustes hechos para 16.04: " + c("install.sh") + " detecta /opt/python3.9, instala "
            "pydantic-core sólo como paquete precompilado (se verificó que existe para armv7 y Python 3.9 hasta "
            "la versión 2.46.5) y ya no usa piwheels; " + c("build_openocd.sh") + " clona sólo la versión "
            "necesaria y borra el código al terminar. En 16.04 el backend GPIO de BootControl queda pendiente "
            "(no hay python3-libgpiod y el kernel 4.9 no tiene la API v2 de GPIO)."),
          P("8.8 Compilación de OpenOCD", "h2"),
          P("Con " + c("sudo bash scripts/build_openocd.sh") + " dentro de tmux: dependencias, clon superficial del "
            "fork de ST (tag openocd-cubeide-v2.2.0), swap temporal de 1 GB y compilación. <b>Tardó 35 minutos</b> "
            "y quedó instalado " + c("Open On-Chip Debugger 0.12.0+dev-gc8d973b") + ". El script terminó antes de "
            "la verificación final por un error propio: " + c("openocd --version | head -n 1") + " con "
            + c("set -o pipefail") + ": " + c("head") + " cierra la tubería, OpenOCD recibe SIGPIPE y el script "
            "aborta. Se corrigió (" + c("sed -n 1p") + " y " + c("grep ... >/dev/null") + " en lugar de "
            + c("head") + " y " + c("grep -q") + ") y los .cfg se verificaron a mano."),
          P("8.9 Dos problemas detectados antes de instalar", "h2"),
          *vinetas([
              "<b>Fin de línea de Windows (CRLF).</b> Algunos archivos editados en la PC quedaron con CRLF y bash "
              "falló con " + c("set: pipefail: invalid option") + ". Se convirtieron en la placa con sed, y desde "
              "entonces el paquete para la placa se genera con " + c("git archive") + " a partir del repositorio "
              "(siempre LF, sin archivos temporales).",
              "<b>hashlib.scrypt no existe con OpenSSL 1.0.2.</b> El Python compilado en la placa no trae scrypt, "
              "que se usaba para el hash de la contraseña de la web. Se agregó un respaldo con "
              "PBKDF2-HMAC-SHA256 de 600.000 iteraciones (recomendación de OWASP) y tests para ese caso.",
          ]),
          P("8.10 Instalación de la aplicación", "h2"),
          P(c("sudo bash scripts/install.sh") + " terminó sin errores en unos 15 minutos:"),
          *vinetas([
              "python3-libgpiod no existe para 16.04: el script lo avisa y sigue (sólo afecta al backend GPIO).",
              "pydantic-core 2.46.5 se instaló como paquete precompilado para armv7 (sin compilar Rust); FastAPI "
              "0.128.8, uvicorn 0.39.0 y starlette 0.49.3; PyYAML se compiló en la placa en segundos.",
              "sudoers validado con visudo: sólo /sbin/poweroff y /sbin/reboot (en 16.04 están en /sbin).",
              "Servicio systemd stm32-bench activo y habilitado para arrancar con la placa.",
          ]),
          P("La web quedó accesible en <b>http://100.99.235.81:8080</b>, sin avisos de archivos .cfg faltantes. "
            "La pestaña Sistema mostró: temperatura del procesador de 62 a 65 °C, unos 130 MB de RAM usados de "
            "483 MB, 1,4 GB de 3,6 GB de disco, y las versiones de OpenOCD y dfu-util."),
          recuadro("Buena práctica: la contraseña de la web se ingresa sólo cuando el script la pide. Si por error "
                   "se escribe como comando, queda en el historial de bash; se borra con "
                   + c("history -c && history -w") + ".", fondo=AMBAR_SUAVE, borde=AMBAR),
          P("8.11 Primer reinicio de la placa", "h2"),
          P("Después de apagar y encender, el servicio de la web arrancó solo, pero la placa no era accesible: "
            + c("ip link show eth0") + " mostraba " + c("NO-CARRIER") + " (el cable de red estaba flojo) y "
            "Tailscale quedaba en " + c("NoState") + " por no tener red. Con el cable bien conectado bastó "
            + c("sudo systemctl restart tailscaled") + ". También se observó que la xM genera una dirección "
            "MAC aleatoria en cada arranque (por ejemplo 2a:62:23:2a:63:0c): queda pendiente fijarla para que "
            "el router le asigne siempre la misma IP."),
          PageBreak(),
          P("8.12 Primera conexión con el STM32H503 (diagnóstico)", "h2"),
          figura(diagrama_conexion(), "Figura 6 — Cableado del banco: BeagleBoard-xM, ST-LINK/V2 (conector de "
                 "20 pines) y placa del STM32H503."),
          P("El programador es un <b>ST-LINK/V2 original</b> (firmware V2J46S7, compatible con el modo dapdirect "
            "que necesita el H5). Su número de serie es binario, como en todos los V2 viejos; la app lo convierte "
            "a hexadecimal y OpenOCD lo aceptó con " + c("adapter serial") + ". La primera conexión falló y se "
            "diagnosticó por pasos:"),
          tabla([["Prueba", "Resultado", "Conclusión"],
                 ["Reset desde la web (4000 kHz)", "ST-Link encontrado y 3,21 V, pero “init mode failed (unable "
                  "to connect to the target)”", "Programador y alimentación del micro bien; falla el SWD"],
                 ["Por SSH a 200 kHz y conectando bajo reset", "Mismo error", "No es la velocidad"],
                 ["ST-Link en la PC con STM32CubeProgrammer", "Conecta: STM32H50x, ID 0x474, 128 KB, AP 1",
                  "El cableado SWD y el chip están bien"],
                 ["ST-Link en la PC con el OpenOCD de CubeIDE (con el .cfg de CubeIDE y con el de la placa)",
                  "“Examination succeed” en los dos casos", "La configuración de OpenOCD no es el problema"],
                 ["De vuelta en la placa, con cable de GND", "Avanza más: “Fail reading CTRL/STAT register”",
                  "Faltaba la masa común"],
                 ["dmesg en la placa", "El Ethernet se cae y vuelve (eth0 link down / link up)",
                  "Después se vio que era el cable de red flojo, no la alimentación"],
                 ["Con GND conectado y menos carga en los USB", "“Examination succeed” y Reset enviado",
                  "Conexión SWD funcionando desde la BeagleBoard"]],
                [52, 62, 56]),
          Spacer(1, 6),
          P("<b>Causa:</b> principalmente la falta de un cable de masa entre el ST-Link y la placa del micro "
            "(desde la PC funcionaba porque todo compartía la masa de sus USB). Las caídas del Ethernet que "
            "mostraba dmesg resultaron ser un cable de red flojo y no un problema de alimentación. Igual conviene "
            "revisar la capacidad de la fuente de 5 V de la placa y, si hace falta, usar un hub USB con fuente "
            "propia. La velocidad SWD del H503 se bajó a 1000 kHz en "
            "/etc/stm32-bench/chips.yaml, un valor más tolerante con cables largos."),
          P("Queda un aviso no bloqueante: al conectar, el script " + c("target/stm32h5x.cfg") + " de esta versión "
            "de OpenOCD intenta configurar DBGMCU (depuración en bajo consumo y watchdogs detenidos) y esa "
            "escritura falla. No afecta a los programas de prueba; se corregirá usando el archivo de CubeIDE, "
            "que conecta sin ese error."),
          Spacer(1, 8)]

    # ---- 9. Firmware de prueba
    s += [P("9. Firmware de prueba para el STM32H503", "h1"),
          P("Para probar el banco se escribieron tres programas mínimos en C, sin HAL, para una placa con el "
            "STM32H503CBT6 y LEDs en PA0–PA7. Están en " + c("firmware/h503_leds/") + " del repositorio, ya "
            "compilados (.elf, .hex y .bin)."),
          tabla([["Programa", "Qué hace", "Qué prueba del banco"],
                 ["01_blink_todos", "Enciende los LEDs de a uno (PA0 → PA7) y después parpadean todos a 1 Hz",
                  "Flashear, Verificar y el orden de los LEDs"],
                 ["02_auto_fantastico", "Una luz que va y viene de PA0 a PA7",
                  "Halt congela la luz, Run la reanuda, Reset reinicia; variable en .data para la conversión DFU"],
                 ["03_contador_binario", "Cuenta de 0 a 255 en binario cada 250 ms",
                  "Servidor GDB: leer y modificar la variable contador, breakpoints"]],
                [36, 66, 68]),
          Spacer(1, 6),
          *vinetas([
              "<b>Registros</b>: direcciones tomadas del encabezado oficial de ST (stm32h503xx.h): RCC en "
              "0x44020C00 (AHB2ENR en +0x8C, bit 0 = GPIOA) y GPIOA en 0x42020000. Sólo se tocan PA0–PA7; PA13 y "
              "PA14 quedan libres para el SWD.",
              "<b>Reloj</b>: el código lee el divisor HSIDIV del HSI de reset para calcular los tiempos, así "
              "funciona sea cual sea su valor inicial. Base de tiempo de 1 ms con SysTick.",
              "<b>Memoria</b>: flash de 128 KB en 0x08000000 y sólo SRAM1 (16 KB), para no depender de que SRAM2 "
              "esté habilitada tras el reset. Arranque y tabla de vectores (16 + 134 entradas) propios, en C.",
              "<b>Compilación</b>: con el GCC 13.3 incluido en STM32CubeIDE, sin advertencias; unos 920 bytes cada "
              "programa. La conversión .elf/.hex → .bin de la app coincide byte a byte con la de objcopy.",
              "<b>Polaridad</b>: por defecto el LED enciende con el pin en alto; si es al revés se compila con "
              + c("make DEFS=-DLED_ACTIVO_ALTO=0") + ".",
          ]),
          P("Depuración remota con el contador binario", "h2"),
          P("Con el programa flasheado y el servidor GDB iniciado desde la web, en la PC:"),
          bloque_codigo("""
arm-none-eabi-gdb 03_contador_binario.elf
(gdb) target extended-remote 100.99.235.81:3333
(gdb) monitor halt                 # los LEDs se congelan
(gdb) print contador               # coincide con los LEDs
(gdb) set var contador = 0xAA      # LEDs 10101010
(gdb) break contador_paso
(gdb) continue
""")]
    s.append(PageBreak())

    # ---- 10. Lecciones aprendidas
    s += [P("10. Lecciones aprendidas", "h1")]
    s += vinetas([
        "<b>Verificar las versiones antes de diseñar sobre ellas.</b> La placa tenía Ubuntu 16.04 con Python 3.5 "
        "y OpenSSL 1.0.2: hubo que compilar Python 3.9 y reemplazar scrypt por PBKDF2.",
        "<b>Windows y Linux no escriben igual los fines de línea.</b> Un script con CRLF no corre en bash. "
        "Solución: .gitattributes con eol=lf y paquetes generados con git archive.",
        "<b>set -o pipefail tiene trampas.</b> head o grep -q cierran la tubería antes de tiempo y el comando "
        "anterior termina con SIGPIPE, lo que aborta el script.",
        "<b>Leer bien la tensión del target no prueba que el SWD funcione.</b> El pin VAPP sólo mide; SWDIO y "
        "SWCLK necesitan una masa común con el programador.",
        "<b>Aislar el problema cambiando una sola cosa.</b> Probar el mismo ST-Link y los mismos cables desde la "
        "PC separó en minutos el cableado, la configuración de OpenOCD y la placa.",
        "<b>La BeagleBoard-xM tiene particularidades de hardware:</b> el Ethernet y los USB salen del mismo chip "
        "(LAN9514) y comparten alimentación; la MAC es aleatoria en cada arranque; no tiene pila para el reloj.",
        "<b>El STM32H5 necesita el modo dapdirect (stlink-dap)</b>, porque su CPU está en el AP 1; un ST-LINK/V2 "
        "original con firmware reciente lo soporta.",
        "<b>Una causa a la vez.</b> Las caídas de Ethernet parecían un problema de alimentación y eran un "
        "cable flojo; el SWD se arregló con la masa. Mezclar síntomas lleva a conclusiones equivocadas.",
        "<b>Con varias familias en el banco, el archivo tiene que saber para qué placa es.</b> Los .bin de ESP "
        "traen el ID del chip y los STM32 exponen su IDCODE, pero un .hex de AVR no dice nada: la asignación "
        "explícita al subir cubre ese hueco.",
        "<b>Dependencias en una placa vieja</b>: esptool trae cryptography, que no compila en armhf con "
        "OpenSSL 1.0.2; como sólo la usa espsecure, se instala con --no-deps.",
        "<b>Documentar mientras se trabaja</b> (este documento se genera desde un script y se actualiza en cada "
        "paso) evita reconstruir de memoria lo que pasó.",
    ])

    # ---- 11. Próximos pasos
    s += [P("11. Próximos pasos", "h1"),
          tabla([["Paso", "Detalle"],
                 ["Flasheo y GDB (en curso)", "Flashear 01_blink_todos y 03_contador_binario desde la web; sesión "
                                              "GDB remota; probar Halt, Run y Reset con 02_auto_fantastico."],
                 ["Alimentación", "Confirmar que la fuente de la xM sea de 5 V y al menos 2,5 A; si el SWD vuelve "
                                  "a fallar, usar un hub USB con fuente propia para el ST-Link."],
                 ["Script del H5", "Usar el stm32h5x.cfg de CubeIDE para eliminar el error de examine-end "
                                   "(configuración de DBGMCU)."],
                 ["MAC fija", "Fijar la dirección MAC de eth0 para que la IP de la red local no cambie."],
                 ["Despliegue por git", "Clonar el repositorio desde GitHub en la placa (con un token de sólo "
                                        "lectura) para actualizar con git pull."],
                 ["USB DFU", "El H503 entra al bootloader pero la BeagleBoard no lo enumera: probar el mismo "
                             "cable USB-C en la PC (puede ser sólo de carga) y otro puerto de la xM."],
                 ["ESP32 y Arduino", "Instalar la versión nueva en la placa y probar Identificar en el H503 "
                                     "(IDCODE real), un ESP32 y un Arduino Uno reales."],
                 ["microSD más grande", "Con 8 GB o más: partición de datos y sistema en solo lectura."]],
                [38, 132]),
          Spacer(1, 6),
          P("Verificaciones pendientes en hardware", "h2")]
    s += vinetas([
        "Flashear, verificar, borrar, halt y run en el STM32H503 y, más adelante, en el STM32H723.",
        "Servidor GDB en la IP de tailscale0; conexión desde arm-none-eabi-gdb y VS Code (Cortex-Debug).",
        "DFU real: dfu-util -l, :leave, verificación con -U y mass-erase sin -D.",
        "Monitor serie con un VCP; apagar y reiniciar desde la web.",
        "Dos ST-Link conectados a la vez (selección por número de serie).",
        "Identificar en STM32: el Tcl read_memory sobre DBGMCU_IDCODE con el OpenOCD de ST.",
        "esptool 4.12 sin cryptography en la xM; ESP32 y ESP32-S3/C3 (USB-JTAG nativo) reales.",
        "avrdude 6.2 de Ubuntu 16.04 con Uno, Nano con bootloader viejo, Mega 2560 y Leonardo (1200 baudios).",
    ])

    # ---- Anexos
    s += [KeepTogether([P("Anexo A. Estructura del repositorio", "h1"),
          bloque_codigo("""
app/                 backend (FastAPI)
  main.py            aplicación, middleware de seguridad, sesiones, estáticos
  api.py             rutas /api/...
  bench.py           servicio central: valida pedidos y arma trabajos SWD/DFU/ESP/AVR
  settings.py        variables de entorno y bench.yaml
  chips.py           chips.yaml (28 placas) y verificación de .cfg
  compat.py          ¿este archivo es para esta placa? (ARM / ESP / AVR)
  firmware_meta.py   vínculo archivo -> placa
  files.py           validación, subida, escaneo y borrado de firmware
  convert.py         .elf/.hex -> .bin para DFU
  probe.py           ST-Link por sysfs y dispositivos DFU
  programmers/       capacidades; comandos de OpenOCD, dfu-util, esptool y avrdude;
                     JobRunner, GdbServer
  bootcontrol/       manual, gpio (libgpiod), comando serie, simulado
  serial_monitor.py  monitor serie (pyserial)
  system.py          estado del sistema, apagar/reiniciar
  events.py          bus de eventos para SSE
  history.py         historial (JSON Lines)
web/                 interfaz (HTML, CSS, JS sin dependencias)
config/              chips.yaml, bench.yaml, bench.example.env
scripts/             build_openocd.sh, prepare_ubuntu1604.sh, install.sh, hash_password.py,
                     dev_run.ps1/.sh, simuladores de OpenOCD, dfu-util, esptool y avrdude
deploy/              unidad systemd, reglas udev, plantilla de sudoers
firmware/h503_leds/  firmware de prueba para STM32H503 (fuentes, Makefile, compilados)
docs/                este documento y su generador
tests/               198 tests (pytest)
""")]),
          KeepTogether([P("Anexo B. Comandos útiles", "h1"),
          bloque_codigo("""
ssh ubuntu@100.99.235.81             # entrar a la placa por Tailscale (o ubuntu@banco-stm32)
tmux attach -t prep                  # volver a una sesión de tmux
systemctl status stm32-bench         # estado del servicio
journalctl -u stm32-bench -f         # log del servicio
sudo systemctl restart stm32-bench   # reiniciar tras editar /etc/stm32-bench/*.yaml
lsusb | grep 0483                    # ST-Link / DFU conectados
ls /dev/ttyUSB* /dev/ttyACM*         # puertos serie (ESP32, Arduino, VCP)
df -h / ; free -m                    # espacio y memoria
ip link show eth0                    # NO-CARRIER = cable de red desconectado
sudo systemctl restart tailscaled    # reconectar Tailscale
dmesg | tail -25                     # mensajes del kernel (USB, red)
openocd -f interface/stlink-dap.cfg -c "transport select dapdirect_swd" -f target/stm32h5x.cfg \\
        -c "adapter speed 1000" -c init -c "reset halt" -c shutdown     # prueba SWD manual
git archive --format=tar.gz -o stm32-bench.tgz HEAD   # (en la PC) paquete limpio para la placa
""")]),
          ]
    return s


def pie(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(GRIS)
    if doc.page > 1:
        canvas.drawString(20 * mm, 12 * mm, "Banco STM32 — documentación del proyecto")
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Página {doc.page}")
        canvas.setStrokeColor(GRIS_BORDE)
        canvas.setLineWidth(0.4)
        canvas.line(20 * mm, 15.5 * mm, A4[0] - 20 * mm, 15.5 * mm)
    canvas.restoreState()


def main() -> None:
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=22 * mm,
                            title="Banco STM32 — documentación del proyecto",
                            subject="Banco de programación y debug remoto para STM32 sobre BeagleBoard-xM")
    doc.build(contenido(), onFirstPage=pie, onLaterPages=pie)
    print(f"PDF generado: {OUT}")


if __name__ == "__main__":
    main()
