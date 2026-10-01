#!/usr/bin/env python3
"""Genera las dos guías del banco en PDF:

    docs/Guia_1_Como_funciona.pdf                 qué es y cómo funciona por dentro
    docs/Guia_2_Instalacion_y_trabajo_remoto.pdf  requisitos, instalación (placa y PC) y
                                                  trabajo remoto desde PowerShell

Uso:
    .venv/Scripts/python docs/generar_guias.py

Reutiliza los estilos y diagramas de generar_documentacion.py (el documento de
trabajo con la bitácora completa).
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generar_documentacion as g  # noqa: E402
from generar_documentacion import (ACENTO, ACENTO_OSC, ACENTO_SUAVE, AMBAR, AMBAR_SUAVE, AZUL,  # noqa: E402
                                   AZUL_SUAVE, GRIS, GRIS_BORDE, GRIS_FONDO, ROJO, ROJO_SUAVE, VIOLETA,
                                   VIOLETA_SUAVE, P, bloque_codigo, c, caja, etiqueta, flecha,
                                   recuadro, tabla, titulo_grupo, vinetas)

# Los títulos nunca quedan solos al pie de una página.
g.ST["h1"].keepWithNext = 1
g.ST["h2"].keepWithNext = 1

DOCS = Path(__file__).resolve().parent
OUT_1 = DOCS / "Guia_1_Como_funciona.pdf"
OUT_2 = DOCS / "Guia_2_Instalacion_y_trabajo_remoto.pdf"
FECHA = "octubre de 2026"
IP = "100.99.235.81"            # IP de Tailscale del banco (cambiala si tu placa tiene otra)
REPO = "https://github.com/ElianUlrich/STM32-bench"


# ============================================================================ diagramas nuevos

def diagrama_operacion() -> Drawing:
    """Recorrido de un pedido "Flashear" desde el navegador hasta el chip."""
    d = Drawing(495, 330)
    x, w = 95, 300
    cx = x + w / 2
    pasos = [
        (290, ["1 · Clic en Flashear", "el navegador manda POST /api/jobs (chip, método, archivo, programador)"], AZUL_SUAVE, AZUL),
        (238, ["2 · Seguridad", "red de Tailscale · sesión iniciada · header X-Bench"], ROJO_SUAVE, ROJO),
        (186, ["3 · Validación (Bench)", "placa y método en chips.yaml · archivo asignado a esa placa y compatible"], ACENTO_SUAVE, ACENTO),
        (134, ["4 · Trabajo (JobRunner)", "uno solo a la vez · arma el comando como lista de argumentos"], ACENTO_SUAVE, ACENTO),
        (82, ["5 · Herramienta", "OpenOCD · dfu-util · esptool · avrdude (sin shell)"], VIOLETA_SUAVE, VIOLETA),
        (30, ["6 · Chip", "verifica el ID / la firma, graba, verifica y reinicia"], ROJO_SUAVE, ROJO),
    ]
    for y, lineas, fondo, borde in pasos:
        caja(d, x, y, w, 36, lineas, fondo=fondo, borde=borde, tam=8.2)
    for y in (290, 238, 186, 134, 82):
        flecha(d, [(cx, y), (cx, y - 16)])
    # Vuelta: log en vivo e historial
    caja(d, 410, 120, 85, 80, ["Log en vivo", "cada línea de la", "herramienta llega", "al navegador (SSE)", "+ historial"],
         fondo=GRIS_FONDO, borde=GRIS_BORDE, tam=7.6)
    flecha(d, [(395, 100), (452, 100), (452, 120)], color=GRIS, trazo=[2, 2])
    flecha(d, [(452, 200), (452, 308), (395, 308)], color=AZUL, trazo=[2, 2])
    caja(d, 0, 120, 80, 80, ["Rechazo", "si algo no", "corresponde:", "error con el", "motivo"],
         fondo=AMBAR_SUAVE, borde=AMBAR, tam=7.6)
    flecha(d, [(95, 204), (40, 204), (40, 200)], color=AMBAR, trazo=[2, 2])
    return d


def diagrama_remoto() -> Drawing:
    """Formas de trabajar desde la PC: web, SSH desde PowerShell y GDB."""
    d = Drawing(495, 230)
    caja(d, 0, 10, 150, 210, [], fondo=GRIS_FONDO, borde=GRIS_BORDE)
    titulo_grupo(d, 75, 206, "Tu PC (Windows)", anchor="middle")
    caja(d, 12, 140, 126, 46, ["Navegador", f"http://{IP}:8080"], borde=AZUL, tam=8)
    caja(d, 12, 80, 126, 46, ["PowerShell", f"ssh ubuntu@{IP}", "scp archivo ubuntu@…"], borde=AZUL, tam=8)
    caja(d, 12, 20, 126, 46, ["GDB / VS Code /", "CubeIDE (opcional)"], borde=AZUL, tam=8)
    caja(d, 168, 10, 92, 210, [], fondo=AZUL_SUAVE, borde=AZUL, grosor=0.7)
    titulo_grupo(d, 214, 206, "Tailscale", color=AZUL, anchor="middle")
    etiqueta(d, 214, 194, "túnel cifrado", tam=6.8)
    caja(d, 278, 10, 217, 210, [], fondo=ACENTO_SUAVE, borde=ACENTO)
    titulo_grupo(d, 386, 206, f"BeagleBoard-xM ({IP})", color=ACENTO_OSC, anchor="middle")
    caja(d, 292, 140, 190, 46, ["App web (puerto 8080)", "archivos · flashear · depurar · serie"], tam=8)
    caja(d, 292, 80, 190, 46, ["Servidor SSH (puerto 22)", "terminal de Linux: bash, tmux, systemctl"], tam=8)
    caja(d, 292, 20, 190, 46, ["OpenOCD servidor GDB (3333)", "sólo cuando lo iniciás en la web"], tam=8)
    for y, txt in ((163, "HTTP"), (103, "SSH"), (43, "GDB")):
        flecha(d, [(138, y), (292, y)], color=AZUL, doble=True)
        etiqueta(d, 214, y + 4, txt, color=AZUL, negrita=True)
    return d


ANCHO_UTIL = 170 * mm


def figura_partes(dibujo, leyenda: str) -> list:
    """Dibujo (achicado si es más ancho que la página) y su leyenda, sueltos para meterlos en un
    KeepTogether junto con el título (un KeepTogether dentro de otro calcula mal su alto)."""
    if isinstance(dibujo, Drawing) and dibujo.width > ANCHO_UTIL:
        f = ANCHO_UTIL / dibujo.width
        dibujo.scale(f, f)
        dibujo.width, dibujo.height = dibujo.width * f, dibujo.height * f
    return [dibujo, Paragraph(leyenda, g.ST["leyenda"])]


def figura(dibujo, leyenda: str):
    return KeepTogether(figura_partes(dibujo, leyenda))


def imagen_pestana() -> Image:
    return Image(str(DOCS / "img" / "pestana_depurar.jpg"), width=170 * mm, height=170 * mm * 397 / 800)


# ============================================================================ portada y pie

def portada(titulo: str, subtitulo: str, resumen: str, indice: list) -> list:
    s = [Spacer(1, 38 * mm), P(titulo, "titulo"), P(subtitulo, "subtitulo"), Spacer(1, 4 * mm),
         P(f"Banco de programación y depuración remota · {FECHA}"),
         P(f"Repositorio: {c(REPO)}"), Spacer(1, 8 * mm), recuadro(resumen), Spacer(1, 10 * mm),
         P("<b>Contenido</b>", "h2")]
    s += [P(t, "vineta") for t in indice]
    s.append(PageBreak())
    return s


def hacer_pie(texto: str):
    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont(g.FONT, 8)
        canvas.setFillColor(GRIS)
        if doc.page > 1:
            canvas.drawString(20 * mm, 12 * mm, texto)
            canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Página {doc.page}")
            canvas.setStrokeColor(GRIS_BORDE)
            canvas.setLineWidth(0.4)
            canvas.line(20 * mm, 15.5 * mm, A4[0] - 20 * mm, 15.5 * mm)
        canvas.restoreState()
    return pie


def construir(out: Path, titulo: str, contenido: list) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=22 * mm, title=titulo, author="Banco STM32")
    pie = hacer_pie(titulo)
    doc.build(contenido, onFirstPage=pie, onLaterPages=pie)
    print(f"PDF generado: {out}")


# ============================================================================ GUÍA 1

def guia_1() -> list:
    s = portada(
        "Cómo funciona el banco",
        "Programar y depurar microcontroladores a distancia con una BeagleBoard-xM",
        "<b>En pocas palabras.</b> Una BeagleBoard-xM queda en el banco del laboratorio, conectada por USB a "
        "los microcontroladores (STM32, ESP32, Arduino) y a sus programadores. Desde el navegador de cualquier "
        "PC conectada a la misma red privada (Tailscale) se sube el firmware, se graba, se depura línea por "
        "línea y se lee el puerto serie, sin estar físicamente frente al banco.",
        ["1. La idea", "2. Las piezas del sistema", "3. Qué pasa cuando tocás un botón",
         "4. Cómo se programa cada familia de microcontroladores", "5. Cómo se evita grabar el micro equivocado",
         "6. Depuración", "7. Monitor serie, historial y estado de la placa", "8. Seguridad",
         "9. Organización del software", "10. Desarrollo sin hardware y pruebas", "11. Límites actuales"])

    # 1
    s += [P("1. La idea", "h1"),
          P("Cuando el hardware está en un laboratorio y quien programa está en otro lugar, cada cambio de "
            "firmware exige ir hasta el banco. La solución es dejar en el banco una computadora chica (la "
            "BeagleBoard-xM, una placa con Linux) que hace de intermediaria: tiene enchufados los programadores "
            "y los microcontroladores, y ofrece una aplicación web para usarlos."),
          tabla([["Necesidad", "Cómo la resuelve el banco"],
                 ["Grabar firmware a distancia", "Subís el .elf/.hex/.bin a la web y tocás Flashear."],
                 ["Depurar (pausar, ver variables, paso a paso)", "Pestaña Depurar en la web, o GDB/VS Code/CubeIDE "
                                                                  "conectados al servidor GDB del banco."],
                 ["Ver lo que el micro manda por serie", "Monitor serie en la web, con envío de texto."],
                 ["Que nadie de afuera pueda entrar", "Acceso sólo por Tailscale (red privada cifrada) y login."],
                 ["Que funcione sin pantalla ni teclado", "La placa arranca sola el servicio; todo se maneja "
                                                         "desde la web o por SSH."]],
                [62, 108])]

    # 2
    s += [P("2. Las piezas del sistema", "h1"),
          P("La figura muestra los bloques. A la izquierda la PC del usuario; en el medio el túnel de "
            "Tailscale; a la derecha la BeagleBoard con los programas que corren en ella y el hardware "
            "conectado a sus puertos USB."),
          figura(g.diagrama_sistema(), "Figura 1 — Bloques del sistema (ejemplo con un STM32 por ST-Link y USB DFU)."),
          *vinetas([
              "<b>PC</b>: sólo necesita un navegador y Tailscale. Opcionalmente PowerShell para entrar a la "
              "terminal de la placa, y un depurador (GDB, VS Code, CubeIDE).",
              "<b>Tailscale</b>: red privada (VPN) que conecta la PC con la placa aunque estén en redes distintas. "
              "Cada equipo recibe una IP fija del tipo 100.x.y.z. No hay que abrir puertos en el router.",
              "<b>BeagleBoard-xM</b>: placa con procesador ARM Cortex-A8 y Linux (Ubuntu). Corre la aplicación "
              "web y las herramientas de programación.",
              "<b>Herramientas</b>: OpenOCD (fork de ST) para ST-Link, dfu-util para el bootloader USB de ST, "
              "esptool para ESP32/ESP8266, avrdude para Arduino, y GDB para depurar.",
              "<b>Programadores y placas</b>: ST-Link (SWD) al STM32; el USB de las placas ESP32 y Arduino; "
              "opcionalmente un adaptador USB-serie para el monitor.",
          ])]

    # 3
    s += [KeepTogether([P("3. Qué pasa cuando tocás un botón", "h1"),
          P("Todas las acciones siguen el mismo camino. Ejemplo: grabar un programa en un STM32."),
          *figura_partes(diagrama_operacion(), "Figura 2 — Recorrido de un pedido, desde el navegador hasta el chip y de vuelta.")]),
          *vinetas([
              "El navegador nunca manda comandos: manda <i>qué</i> quiere (placa, acción, archivo). El "
              "servidor decide <i>cómo</i> y arma el comando de la herramienta.",
              "Hay <b>un solo trabajo a la vez</b>: si el programador está ocupado, el pedido se rechaza en lugar "
              "de encolarse. Un trabajo largo se puede cancelar.",
              "Cada línea que escribe la herramienta aparece en el <b>log en vivo</b> (Server-Sent Events) y el "
              "resultado queda en el <b>historial</b>.",
          ])]

    # 4
    s += [P("4. Cómo se programa cada familia", "h1"),
          P("Cada placa del catálogo (" + c("config/chips.yaml") + ", 28 placas) declara uno o más métodos. La "
            "interfaz sólo muestra los botones que ese método soporta y explica por qué los demás están "
            "deshabilitados."),
          tabla([["Método", "Placas", "Conexión", "Acciones"],
                 ["ST-Link (SWD) · OpenOCD", "STM32 (H5, H7, F1, F4, L4, G0, G4…)", "ST-Link al conector SWD "
                  "(SWDIO, SWCLK, GND)", "grabar, verificar, borrar, reset, halt, run, identificar, depurar"],
                 ["USB DFU · dfu-util", "STM32 con bootloader USB", "USB del propio STM32 (PA11/PA12), con "
                  "BOOT0 en alto", "grabar, verificar, borrar"],
                 ["Serie · esptool", "ESP8266, ESP32, S2, S3, C2, C3, C5, C6, C61, H2, P4", "USB de la placa "
                  "(conversor o USB-JTAG)", "grabar, verificar, borrar, reset, identificar"],
                 ["Serie · avrdude", "Uno, Nano, Mega, Leonardo, Micro, Pro Mini…", "USB del Arduino (o "
                  "USBasp por ISP)", "grabar, verificar, reset, identificar (borrar sólo por ISP)"]],
                [36, 44, 44, 46]),
          Spacer(1, 6),
          *vinetas([
              "<b>STM32 por SWD</b>: OpenOCD habla con el ST-Link. Antes de grabar lee el registro de "
              "identificación del chip (DBGMCU_IDCODE) y aborta si no es el esperado.",
              "<b>STM32 por USB DFU</b>: el chip entra a su bootloader de fábrica (BOOT0 en alto + reset). La app "
              "convierte el .elf/.hex a .bin y lo graba con dfu-util. No permite depurar.",
              "<b>ESP32</b>: esptool pone el chip en modo descarga solo (líneas DTR/RTS del USB). La app reconoce "
              "si el .bin es sólo la aplicación (va en 0x10000) o la imagen combinada (va en 0x0).",
              "<b>Arduino</b>: avrdude habla con el bootloader por el puerto serie. En Leonardo/Micro la app abre "
              "el puerto a 1200 baudios para que el chip salte al bootloader.",
          ])]

    # 5
    s += [KeepTogether([P("5. Cómo se evita grabar el micro equivocado", "h1"),
          P("Con muchas placas en el mismo banco, grabar un binario en la placa que no corresponde es fácil y "
            "puede dañar hardware. Por eso cada archivo se sube diciendo para qué placa es, y hay tres controles:"),
          *figura_partes(g.diagrama_vinculo(), "Figura 3 — Las tres capas: contenido del archivo, asignación y chip conectado.")]),
          recuadro("<b>Ejemplo.</b> Un binario compilado para ESP32-S3 trae ese dato en su encabezado: si lo "
                   "asignás a un ESP32 común, la web lo rechaza al subirlo. Si igual hubiera otro chip conectado, "
                   "esptool lo detecta antes de escribir y aborta.")]

    # 6
    s += [P("6. Depuración", "h1"),
          P("Depurar es ejecutar el programa bajo control: frenarlo en una línea (breakpoint), avanzar de a una "
            "línea, ver y cambiar variables, y ver la pila de llamadas. Sirve para encontrar por qué el programa "
            "hace algo distinto de lo esperado, sin llenarlo de printf."),
          P("Pestaña Depurar (todo en la web)", "h2"),
          P("La aplicación corre GDB en la placa, lo conecta a OpenOCD y muestra el resultado. Se ve el código "
            "fuente, se marcan breakpoints con un clic en el número de línea y se usan los mismos atajos que en "
            "los IDE: F5 continuar, F6 pausar, F10 paso, F11 entrar, Shift+F11 salir."),
          figura(g.diagrama_depurador(), "Figura 4 — La pestaña Depurar: la app maneja GDB en la placa."),
          figura(imagen_pestana(), "Figura 5 — Pestaña Depurar detenida en un breakpoint (modo simulación)."),
          *vinetas([
              "Para ver el código hacen falta las fuentes: se sube el proyecto en .zip (el .elf y la carpeta con "
              "los .c/.h). La app busca cada archivo usando las rutas que guarda el .elf.",
              "Tres formas de empezar: grabar y parar en main, reiniciar y parar en main, o conectarse sin "
              "reiniciar (frena el programa donde esté).",
              "Pausar detiene todo el microcontrolador: en un sistema de audio o de control, mientras está "
              "frenado, la salida se corta.",
          ]),
          P("Servidor GDB (para usar tu propio depurador)", "h2"),
          P("También se puede iniciar sólo el servidor GDB y conectarse desde la PC con arm-none-eabi-gdb, VS Code "
            "(Cortex-Debug) o STM32CubeIDE a " + c(f"{IP}:3333") + ". OpenOCD acepta un único cliente: o la web, "
            "o un depurador externo.")]

    # 7
    s += [P("7. Monitor serie, historial y estado de la placa", "h1"),
          *vinetas([
              "<b>Monitor serie</b>: lista los puertos USB-serie de la placa (/dev/ttyUSB*, /dev/ttyACM*), con "
              "velocidad configurable, y permite enviar texto con el fin de línea elegido. Si se graba una placa "
              "por el mismo puerto, el monitor se cierra mientras graba y se vuelve a abrir solo.",
              "<b>Historial</b>: cada operación con su resultado, duración y archivo.",
              "<b>Sistema</b>: IP, tiempo encendida, memoria, disco, versiones de las herramientas, y los "
              "botones Apagar y Reiniciar (con confirmación). Después de apagar hay que esperar unos 30 segundos "
              "antes de cortar la alimentación; la placa no se puede volver a encender a distancia.",
          ])]

    # 8
    s += [P("8. Seguridad", "h1"),
          P("La aplicación puede ejecutar programas en la placa y grabar hardware, así que se protegió en capas:"),
          tabla([["Capa", "Qué hace"],
                 ["Red", "Sólo se aceptan conexiones desde la red de Tailscale (100.64.0.0/10) o desde la propia "
                         "placa. No se abren puertos en el router."],
                 ["Login", "Usuario y contraseña (guardada como hash, nunca en texto). Cookie de sesión firmada "
                           "que vence. Límite de intentos fallidos."],
                 ["Pedidos", "Toda acción exige un header especial (protección contra sitios ajenos)."],
                 ["Comandos", "Nunca se arma un comando con texto del usuario: listas de argumentos, sin shell. "
                              "Placas e interfaces sólo de los archivos de configuración; nombres de archivo con "
                              "caracteres permitidos; rutas siempre dentro de la carpeta de proyectos."],
                 ["Privilegios", "El servicio corre como un usuario sin privilegios. sudo sólo le permite apagar "
                                 "y reiniciar."],
                 ["Depurador", "El puerto GDB escucha sólo en la IP de Tailscale. La web nunca le manda texto "
                               "libre a GDB; las expresiones se validan."]],
                [28, 142])]

    # 9
    s += [KeepTogether([P("9. Organización del software", "h1"),
          P("El servidor está escrito en Python (FastAPI) y la interfaz en HTML, CSS y JavaScript sin "
            "dependencias externas, para que funcione liviano en la BeagleBoard."),
          *figura_partes(g.diagrama_software(), "Figura 6 — Capas de software dentro de la placa.")]),
          tabla([["Carpeta / archivo", "Qué contiene"],
                 ["app/", "servidor: API, validaciones, trabajos, depurador, monitor serie, sistema"],
                 ["web/", "la interfaz (index.html, app.js, style.css)"],
                 ["config/", "chips.yaml (placas), bench.yaml (herramientas, red, límites)"],
                 ["scripts/", "instalación, compilación de OpenOCD y GDB, simuladores para el modo mock"],
                 ["deploy/", "servicio systemd, reglas udev, permisos de sudo"],
                 ["firmware/h503_leds/", "programas de prueba para el STM32H503 (LEDs y puerto serie)"],
                 ["tests/", "pruebas automáticas (pytest)"],
                 ["docs/", "esta guía, la de instalación y el documento de trabajo con la bitácora"]],
                [40, 130])]

    # 10
    s += [P("10. Desarrollo sin hardware y pruebas", "h1"),
          P("Con la variable " + c("BENCH_MOCK=1") + " la aplicación corre en la PC y reemplaza OpenOCD, dfu-util, "
            "esptool, avrdude y GDB por simuladores que se ejecutan como programas reales. Así se desarrolla y se "
            "prueba toda la interfaz sin placa. El simulador de GDB ejecuta el contador binario real, con sus "
            "líneas y funciones."),
          P("Las pruebas automáticas (más de 220) cubren validación de archivos, armado de comandos, "
            "configuración, vínculo archivo-placa, el protocolo de GDB, la subida de proyectos .zip y sesiones "
            "de depuración completas.")]

    # 11
    s += [P("11. Límites actuales", "h1"),
          *vinetas([
              "La depuración en la web es sólo para STM32 por SWD. Todavía no hay vista de memoria, "
              "watchpoints, breakpoints condicionales ni periféricos por nombre.",
              "El USB DFU del STM32H503 todavía no fue probado con éxito en la placa (no enumera; posible cable "
              "sólo de carga).",
              "ESP32 y Arduino están probados en simulación; falta probarlos con placas reales.",
              "La BeagleBoard-xM tiene USB con poca corriente: varias placas a la vez requieren un hub con fuente.",
          ])]
    return s


# ============================================================================ GUÍA 2

def guia_2() -> list:
    s = portada(
        "Instalación y trabajo remoto",
        "Requisitos, puesta en marcha de la BeagleBoard y de la PC, y comandos para trabajar a distancia",
        "<b>Cómo usar esta guía.</b> Los comandos en recuadro gris se copian y pegan. Fijate siempre "
        "<b>dónde</b> se escriben: los que empiezan en la PC van en <b>PowerShell</b> (el prompt dice "
        + c("PS C:\\>") + "); los de la placa van en la <b>terminal de Linux</b>, a la que se entra por SSH "
        "(el prompt dice " + c("ubuntu@arm:~$") + "). Mezclarlos es el error más común.",
        ["1. Requisitos de hardware", "2. Requisitos de software y cuentas",
         "3. Instalación en un solo comando (recomendado)", "3b. Instalación en la BeagleBoard, paso a paso",
         "4. Instalación en la PC", "5. Trabajo remoto desde PowerShell", "6. Comandos útiles en la placa",
         "7. Actualizar a una versión nueva", "8. Problemas frecuentes"])

    # 1
    s += [P("1. Requisitos de hardware", "h1"),
          tabla([["Elemento", "Detalle", "¿Obligatorio?"],
                 ["BeagleBoard-xM", "ARM Cortex-A8, 512 MB de RAM. Otra placa Linux ARM (Raspberry Pi) también "
                                    "sirve con cambios mínimos.", "Sí"],
                 ["microSD", "4 GB como mínimo; 8 GB o más recomendado (las compilaciones ocupan lugar).", "Sí"],
                 ["Fuente de 5 V", "Al menos 2,5 A. Con poca corriente se caen los USB.", "Sí"],
                 ["Cable de red", "Ethernet al router del laboratorio (con acceso a internet).", "Sí"],
                 ["ST-Link", "V2, V2-1 o V3 (suelto o el de una Nucleo). Para STM32H5 hace falta firmware "
                             "J24 o posterior (modo dapdirect).", "Para STM32"],
                 ["Cables SWD", "SWDIO, SWCLK y GND (NRST recomendado). Sin GND común no conecta.", "Para STM32"],
                 ["Cable USB de datos", "Para DFU, ESP32 y Arduino. Ojo: algunos cables USB-C son sólo de carga.",
                  "Según la placa"],
                 ["Adaptador USB-serie 3,3 V", "CH340, CP2102 o FT232, para el monitor serie de un STM32 sin "
                                               "puerto serie por USB.", "Opcional"],
                 ["Hub USB con fuente", "Si se conectan varias placas a la vez.", "Recomendado"],
                 ["Teclado y monitor HDMI/DVI", "Sólo para el primer arranque si no se puede entrar por la red.",
                  "Opcional"]],
                [36, 104, 30])]

    # 2
    s += [P("2. Requisitos de software y cuentas", "h1"),
          tabla([["Dónde", "Qué", "Para qué"],
                 ["Placa", "Ubuntu 16.04 (imagen rcn-ee) o Debian 11/12 para ARM (armhf)", "sistema operativo"],
                 ["Placa", "Python 3.9 o superior (en 16.04 se compila con un script)", "la aplicación web"],
                 ["Placa", "OpenOCD de ST, GDB 12, dfu-util, esptool, avrdude", "programar y depurar (los "
                                                                               "instalan los scripts)"],
                 ["PC", "Windows 10/11 con PowerShell y el cliente OpenSSH (viene incluido)", "entrar a la "
                                                                                             "placa y copiar archivos"],
                 ["PC", "Tailscale para Windows", "llegar a la placa desde cualquier red"],
                 ["PC", "Un navegador (Edge, Chrome, Firefox)", "usar la aplicación"],
                 ["PC", "Git para Windows", "bajar el repositorio; lo usa desplegar.bat para armar el paquete"],
                 ["PC", "STM32CubeIDE (opcional)", "compilar firmware; trae arm-none-eabi-gcc y -gdb"],
                 ["Cuentas", "Tailscale (gratis para uso personal) y GitHub (el repositorio es privado)", "red "
                                                                                                        "privada y código"]],
                [20, 90, 60])]

    # 3
    s += [P("3. Instalación en un solo comando (recomendado)", "h1"),
          P("El script " + c("desplegar.bat") + ", en la carpeta del repositorio, hace todo desde la PC: arma un "
            "paquete con el código (incluidos los cambios sin commit), lo copia a la placa por la red, entra por "
            "SSH y la configura sola con " + c("scripts/instalar_todo.sh") + ". Sólo hace falta que la placa tenga "
            "Linux, red y SSH (pasos 3.1 de la sección siguiente), y Git en la PC."),
          tabla([["Comando (PowerShell, en la carpeta del repositorio)", "Qué hace", "Tiempo"],
                 [c(".\\desplegar.bat -ConfigurarClave"), "Una sola vez: crea una clave SSH y la instala en la placa, "
                  "para no escribir la contraseña de SSH cada vez.", "1 min"],
                 [c(".\\desplegar.bat -Completo -Placa 192.168.0.27"), "Placa recién instalada: agranda la partición, "
                  "instala Tailscale, Python 3.9, OpenOCD, la aplicación y GDB. Corre en tmux.", "5-9 h"],
                 [c(".\\desplegar.bat"), "Actualiza la aplicación con el código de la PC.", "2-5 min"],
                 [c(".\\desplegar.bat -ActualizarConfig"), "Igual, y además reemplaza chips.yaml y bench.yaml por los "
                  "nuevos (guarda los anteriores como .bak).", "2-5 min"],
                 [c(".\\desplegar.bat -Simular"), "Muestra lo que haría, sin tocar nada.", "segundos"]],
                [62, 88, 20]),
          Spacer(1, 6),
          *vinetas([
              "Para la instalación completa usá la <b>IP de la red local</b> (" + c("-Placa 192.168.x.y") + "): "
              "Tailscale todavía no está. Las siguientes veces alcanza con " + c(".\\desplegar.bat") + ", que usa "
              "la IP de Tailscale (" + c(IP) + ").",
              "Al principio pide la contraseña de sudo, el usuario y la contraseña de la web y la autorización de "
              "Tailscale (un enlace para abrir en la PC). Después ya no hace falta estar: corre sola varias horas.",
              "Corre dentro de tmux, en la sesión " + c("instalar") + ". Si se corta la conexión sigue sola; para "
              "volver a mirarla: " + c(f"ssh ubuntu@{IP}") + " y " + c("tmux attach -t instalar") + ".",
              "Todo es idempotente: si algo falla, se corrige y se vuelve a correr el mismo comando; lo que ya "
              "estaba instalado no se recompila.",
          ]),
          P("3b. Instalación en la BeagleBoard, paso a paso", "h1"),
          P("Lo mismo que hace " + c("desplegar.bat") + ", a mano, por si se quiere entender o hacer un paso solo."),
          recuadro("Los pasos 3.4 a 3.7 compilan programas en la placa y tardan mucho (de 1 a 6 horas cada "
                   "uno). Por eso se corren dentro de <b>tmux</b>: si se corta la conexión, la compilación sigue. "
                   "Todos los scripts se pueden volver a correr sin problema.", fondo=AMBAR_SUAVE, borde=AMBAR),
          P("3.1 Primer acceso por la red local", "h2"),
          P("Con la placa enchufada a la red, buscá su IP en la lista de equipos del router (o conectá monitor y "
            "teclado y escribí " + c("ip addr") + "). Desde PowerShell en una PC de la misma red:"),
          bloque_codigo("ssh ubuntu@192.168.0.27          # usá la IP local de tu placa"),
          P("La primera vez pregunta si confiás en el equipo: escribí " + c("yes") + ". Después pide la contraseña "
            "(la de la imagen; al escribirla no se ve nada, es normal). Cambiala enseguida:"),
          bloque_codigo("passwd"),
          P("3.2 Preparar el sistema (sólo Ubuntu 16.04)", "h2"),
          P("La imagen vieja trae Python 3.5 y su partición no usa toda la microSD. Primero se repara apt y se "
            "agranda la partición:"),
          bloque_codigo("""
sudo dpkg --configure -a
sudo apt update
sudo apt install -y apt-transport-https ca-certificates cloud-guest-utils git tmux
lsblk                                             # ver el nombre del disco y la partición
sudo growpart /dev/mmcblk0 2 && sudo resize2fs /dev/mmcblk0p2
df -h /                                           # comprobar el espacio nuevo
"""),
          P("3.3 Copiar el código a la placa", "h2"),
          P("Opción A, desde la PC con Git (no hace falta cuenta de GitHub en la placa). En PowerShell, dentro de "
            "la carpeta del repositorio:"),
          bloque_codigo(f"""
git archive --format=tar.gz -o stm32-bench.tgz HEAD
scp stm32-bench.tgz ubuntu@{IP}:~
"""),
          P("y en la placa:"),
          bloque_codigo("mkdir -p ~/stm32-bench && tar -xzf ~/stm32-bench.tgz -C ~/stm32-bench && cd ~/stm32-bench"),
          P("Opción B, clonar en la placa (pide usuario y token de GitHub porque el repositorio es privado):"),
          bloque_codigo(f"git clone {REPO}.git ~/stm32-bench && cd ~/stm32-bench"),
          P("3.4 Python 3.9 y dfu-util (sólo Ubuntu 16.04, ~1-2 h)", "h2"),
          bloque_codigo("""
tmux new -s prep
sudo bash ~/stm32-bench/scripts/prepare_ubuntu1604.sh
"""),
          P("3.5 OpenOCD de ST (~45-90 min)", "h2"),
          bloque_codigo("""
tmux new -s openocd
sudo bash ~/stm32-bench/scripts/build_openocd.sh
"""),
          P("3.6 Tailscale", "h2"),
          bloque_codigo("""
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=banco-stm32
tailscale ip -4                                   # anotá la IP 100.x.y.z
"""),
          P(c("tailscale up") + " muestra un enlace: abrilo en la PC e iniciá sesión con tu cuenta. Después, en la "
            "consola web de Tailscale (Machines → banco-stm32 → ⋯), elegí <b>Disable key expiry</b>: si la clave "
            "vence, nadie puede entrar a la placa sin ir hasta ella."),
          P("3.7 La aplicación", "h2"),
          bloque_codigo("sudo bash ~/stm32-bench/scripts/install.sh"),
          P("Pregunta el <b>usuario y la contraseña de la web</b> (mínimo 10 caracteres; usá una distinta de la de "
            "Linux). Instala las dependencias, esptool y avrdude, crea el usuario del servicio, las reglas USB y el "
            "servicio que arranca solo. Al final dice " + c("Servicio en marcha.") + " y la dirección para abrir."),
          P("3.8 GDB para la pestaña Depurar (opcional, 3-6 h)", "h2"),
          bloque_codigo("""
tmux new -s gdb
sudo bash ~/stm32-bench/scripts/build_gdb.sh
arm-none-eabi-gdb --version                       # al terminar: GNU gdb (GDB) 12.1
"""),
          P("3.9 Verificar", "h2"),
          bloque_codigo("""
systemctl status stm32-bench --no-pager           # tiene que decir active (running)
tailscale status                                  # la placa y tu PC conectadas
"""),
          P("Desde la PC, con Tailscale activo, abrí " + c(f"http://{IP}:8080") + " (o "
            + c("http://banco-stm32:8080") + ") e ingresá con el usuario de la web.")]

    # 4
    s += [P("4. Instalación en la PC", "h1"),
          P("4.1 Tailscale", "h2"),
          P("Descargalo de " + c("https://tailscale.com/download/windows") + ", instalalo e iniciá sesión con la "
            "<b>misma cuenta</b> que usaste en la placa. En el ícono de la bandeja tiene que figurar banco-stm32 "
            "como conectado. Comprobación en PowerShell:"),
          bloque_codigo(f"""
tailscale status
ping {IP}
"""),
          P("4.2 Cliente SSH", "h2"),
          P("Windows 10 y 11 lo traen. Comprobalo:"),
          bloque_codigo("ssh -V"),
          P("Si dice que no existe, instalalo desde Configuración → Aplicaciones → Características opcionales → "
            "Cliente OpenSSH, o en una PowerShell abierta como administrador:"),
          bloque_codigo("Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0"),
          P("4.3 Git y el repositorio", "h2"),
          P("Instalá Git para Windows (" + c("https://git-scm.com") + ") y bajá el repositorio:"),
          bloque_codigo(f"""
cd $env:USERPROFILE\\Desktop
git clone {REPO}.git Debugger
"""),
          P("4.4 STM32CubeIDE (opcional)", "h2"),
          P("Sirve para compilar firmware y trae el GDB para ARM. El GDB queda en una carpeta como "
            + c("C:\\ST\\STM32CubeIDE_1.19.0\\STM32CubeIDE\\plugins\\…gnu-tools-for-stm32…\\tools\\bin")
            + ". El script " + c("firmware\\h503_leds\\depurar.bat") + " ya lo busca ahí."),
          P("4.5 Probar la aplicación en la PC, sin placa (opcional)", "h2"),
          bloque_codigo("powershell -ExecutionPolicy Bypass -File scripts\\dev_run.ps1"),
          P("Abre la app en modo simulación en " + c("http://127.0.0.1:8080") + "; la contraseña aparece en la "
            "consola.")]

    # 5
    s += [KeepTogether([P("5. Trabajo remoto desde PowerShell", "h1"),
          *figura_partes(diagrama_remoto(), "Figura 1 — Las tres formas de trabajar con el banco desde la PC.")]),
          P("5.1 Entrar a la terminal de Linux de la placa", "h2"),
          bloque_codigo(f"""
ssh ubuntu@{IP}              # o:  ssh ubuntu@banco-stm32
"""),
          P("Cuando el prompt cambia a " + c("ubuntu@arm:~$") + " ya estás en la placa: todo lo que escribas corre "
            "en Linux. Para volver a PowerShell: " + c("exit") + " (o Ctrl+D)."),
          recuadro("<b>PowerShell no es Linux.</b> En PowerShell 5 no existe " + c("&&") + " para encadenar comandos "
                   "(usá " + c(";") + "), ni " + c("df") + ", " + c("grep") + " o " + c("sudo") + ". Si un "
                   "comando de esta guía da error de “no se reconoce”, seguramente lo escribiste en la PC en lugar "
                   "de en la placa.", fondo=AZUL_SUAVE, borde=AZUL),
          P("5.2 Entrar sin escribir la contraseña cada vez (clave SSH)", "h2"),
          P("Se hace una sola vez. En PowerShell, creá la clave (Enter a todas las preguntas) y copiala a la placa:"),
          bloque_codigo(f"""
ssh-keygen -t ed25519
scp $env:USERPROFILE\\.ssh\\id_ed25519.pub ubuntu@{IP}:~/clave.pub
"""),
          P("Después, en la placa (es la última vez que pide la contraseña):"),
          bloque_codigo("""
mkdir -p ~/.ssh && cat ~/clave.pub >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && rm ~/clave.pub
"""),
          P("5.3 Un nombre corto para la placa", "h2"),
          P("Creá el archivo " + c("C:\\Users\\<tu usuario>\\.ssh\\config") + " (sin extensión) con:"),
          bloque_codigo(f"""
Host banco
    HostName {IP}
    User ubuntu
"""),
          P("Desde ese momento alcanza con " + c("ssh banco") + ", " + c("scp archivo banco:~") + ", etc."),
          P("5.4 Copiar archivos entre la PC y la placa", "h2"),
          bloque_codigo(f"""
# de la PC a la placa (queda en la carpeta personal, ~)
scp C:\\ruta\\archivo.elf ubuntu@{IP}:~
# de la placa a la carpeta actual de la PC
scp ubuntu@{IP}:/var/lib/stm32-bench/history.jsonl .
# una carpeta entera
scp -r C:\\ruta\\carpeta ubuntu@{IP}:~
"""),
          P("Para el firmware conviene subirlo desde la web (pestaña Archivos), que además lo asigna a su placa."),
          P("5.5 Ejecutar un comando en la placa sin quedarse adentro", "h2"),
          bloque_codigo(f"""
ssh ubuntu@{IP} "systemctl status stm32-bench --no-pager"
ssh ubuntu@{IP} "uptime; df -h /; free -m"
ssh -t ubuntu@{IP} "sudo systemctl restart stm32-bench"
"""),
          P("Con " + c("-t") + " se puede usar sudo, porque deja que la placa pida la contraseña."),
          P("5.6 Trabajos largos que sobreviven a un corte: tmux", "h2"),
          tabla([["Comando (en la placa)", "Qué hace"],
                 [c("tmux new -s nombre"), "abre una sesión nueva con ese nombre"],
                 ["Ctrl+B y después D", "sale de la sesión sin cortarla (sigue corriendo)"],
                 [c("tmux ls"), "lista las sesiones abiertas"],
                 [c("tmux attach -t nombre"), "vuelve a entrar a una sesión"],
                 [c("exit"), "dentro de la sesión: la cierra"]],
                [60, 110]),
          P("5.7 Depurar con GDB desde la PC", "h2"),
          P("Con el servidor GDB iniciado en la web (botón Iniciar, tiene que decir “Escuchando”):"),
          bloque_codigo(f"""
C:\\Users\\<tu usuario>\\Desktop\\Debugger\\firmware\\h503_leds\\depurar.bat
# o a mano:
arm-none-eabi-gdb programa.elf -ex "set remotetimeout 10" `
    -ex "target extended-remote {IP}:3333"
"""),
          P("No abras " + c(f"{IP}:3333") + " en el navegador: no es una página web y ocupa la única conexión GDB."),
          P("5.8 Túnel SSH (sólo si no podés usar Tailscale)", "h2"),
          P("Si estás en la misma red local, un túnel lleva la web a tu PC sin abrir puertos:"),
          bloque_codigo("""
ssh -L 8080:localhost:8080 ubuntu@192.168.0.27
# mientras esa ventana esté abierta:  http://localhost:8080
""")]

    # 6
    s += [P("6. Comandos útiles en la placa", "h1"),
          tabla([["Comando", "Para qué"],
                 [c("systemctl status stm32-bench"), "estado del servicio de la web"],
                 [c("sudo systemctl restart stm32-bench"), "reiniciarlo (por ejemplo después de editar la "
                                                           "configuración)"],
                 [c("journalctl -u stm32-bench -f"), "ver el log del servicio en vivo (Ctrl+C para salir)"],
                 [c("sudo nano /etc/stm32-bench/chips.yaml"), "editar las placas (Ctrl+O guarda, Ctrl+X sale)"],
                 [c("lsusb"), "ver qué hay conectado por USB (ST-Link 0483:3748, DFU 0483:df11…)"],
                 [c("ls /dev/ttyUSB* /dev/ttyACM*"), "puertos serie disponibles"],
                 [c("dmesg | tail -25"), "últimos mensajes del sistema (USB que entra y sale, red)"],
                 [c("tailscale status"), "equipos de la red privada"],
                 [c("sudo systemctl restart tailscaled"), "reconectar Tailscale"],
                 [c("ip link show eth0"), "estado del cable de red (NO-CARRIER = desconectado)"],
                 [c("uptime; df -h /; free -m"), "tiempo encendida, disco y memoria"],
                 [c("sudo poweroff"), "apagar (esperá unos 30 s antes de desenchufar)"],
                 [c("sudo reboot"), "reiniciar"]],
                [72, 98])]

    # 7
    s += [P("7. Actualizar a una versión nueva", "h1"),
          P("Lo más simple, en PowerShell, en la carpeta del repositorio:"),
          bloque_codigo("""
git pull
.\\desplegar.bat
"""),
          P("A mano, sin el script. En PowerShell:"),
          bloque_codigo(f"""
git pull
git archive --format=tar.gz -o stm32-bench.tgz HEAD
scp stm32-bench.tgz ubuntu@{IP}:~
"""),
          P("En la placa:"),
          bloque_codigo("""
rm -rf ~/stm32-bench && mkdir ~/stm32-bench
tar -xzf ~/stm32-bench.tgz -C ~/stm32-bench
cd ~/stm32-bench && sudo bash scripts/install.sh
"""),
          P("install.sh conserva el usuario, la contraseña y la configuración. Si la configuración del "
            "repositorio cambió, deja la nueva al lado como " + c("chips.yaml.dist") + " / " + c("bench.yaml.dist")
            + ". Para usarla:"),
          bloque_codigo("""
sudo cp /etc/stm32-bench/chips.yaml.dist /etc/stm32-bench/chips.yaml
sudo systemctl restart stm32-bench
"""),
          P("Después recargá la web con Ctrl+F5.")]

    # 8
    s += [P("8. Problemas frecuentes", "h1"),
          tabla([["Síntoma", "Causa probable y solución"],
                 [c("ssh: connect ... timed out"), "Tailscale apagado en la PC o en la placa, o cable de red flojo. "
                  "Revisá " + c("tailscale status") + " en la PC; si la placa no aparece, entrá por la red local "
                  "y corré " + c("sudo systemctl restart tailscaled") + "."],
                 [c("REMOTE HOST IDENTIFICATION HAS CHANGED"), "Se reinstaló el sistema de la placa. En PowerShell: "
                  + c(f"ssh-keygen -R {IP}") + " y volvé a entrar."],
                 [c("client_loop: send disconnect"), "Se cortó la conexión (la placa muy ocupada compilando, o la "
                  "red). Volvé a entrar; lo que estaba en tmux sigue corriendo."],
                 ["“df / grep / sudo no se reconoce”", "Lo escribiste en PowerShell: primero " + c("ssh ubuntu@…") + "."],
                 [c("&& no es un separador válido"), "PowerShell 5 no tiene &&: usá ; o un comando por línea."],
                 [c("Sorry, try again"), "Contraseña de sudo mal escrita (es la del usuario ubuntu)."],
                 ["La web no carga", "Tailscale activo en la PC; " + c("systemctl status stm32-bench") + " en la "
                  "placa; mirá " + c("journalctl -u stm32-bench -n 50") + "."],
                 ["El ST-Link no conecta al chip", "Falta el GND común, o el chip está sin alimentación. El H5 "
                  "necesita la interfaz stlink-dap."],
                 ["La pestaña Depurar no muestra el código", "Subiste sólo el .elf: subí el proyecto en .zip con "
                  "las fuentes."],
                 ["La placa no vuelve después de apagarla", "No se puede encender a distancia: hay que desenchufar "
                  "y volver a enchufar la alimentación."]],
                [52, 118])]
    return s


def main() -> None:
    construir(OUT_1, "Banco STM32 — Cómo funciona", guia_1())
    construir(OUT_2, "Banco STM32 — Instalación y trabajo remoto", guia_2())


if __name__ == "__main__":
    main()
