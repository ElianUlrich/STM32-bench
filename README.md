# Banco remoto — programación y depuración de microcontroladores sobre BeagleBoard-xM

Aplicación web que convierte una **BeagleBoard-xM** (headless, sin pantalla ni teclado) en un banco
de programación y depuración remota de microcontroladores **STM32**, **ESP32/ESP8266** y
**Arduino (AVR)**. Desde el navegador de tu PC (conectada por **Tailscale**) podés:

- subir firmware (`.elf`, `.hex`, `.bin`) indicando **para qué placa es**; la app revisa el contenido
  y no deja grabarlo en otra (ver [Binario ↔ placa](#binario--placa-no-grabar-el-micro-equivocado));
- flashear, verificar, borrar, resetear, detener y reanudar el chip por **ST-Link (SWD) con OpenOCD**;
- flashear, verificar y borrar por **USB DFU** (bootloader de ST, con `dfu-util`), entrando al
  bootloader en forma manual, con GPIO o con un comando por puerto serie;
- grabar, verificar, borrar, resetear e identificar **ESP32 / ESP8266** por puerto serie con `esptool`;
- grabar, verificar, resetear e identificar **Arduino** (Uno, Nano, Mega, Leonardo, Micro, Pro Mini…)
  con `avrdude` por su bootloader, o cualquier ATmega con un programador **ISP** (USBasp/USBtiny);
- **depurar el STM32 desde la misma web** (pestaña *Depurar*): código fuente con breakpoints,
  continuar/pausar/paso a paso, variables locales y globales editables, expresiones, pila y registros
  (ver [Depurar en la web](#depurar-en-la-web-pestaña-depurar));
- o levantar un **servidor GDB** para depurar desde `arm-none-eabi-gdb`, VS Code o STM32CubeIDE;
- ver en vivo la salida de OpenOCD/dfu-util y un **monitor serie**, que también muestra el
  **RTT** del programa (el `printf` por el mismo ST-Link, sin adaptador USB-serie);
- ver qué hay enchufado por USB y **cortar o reiniciar la energía de un puerto** (con un hub que lo
  soporte, vía `uhubctl`) para reiniciar en frío una placa o un ST-Link colgado;
- ver el estado de la placa y **apagarla o reiniciarla** con seguridad.

```
 Tu PC (navegador, GDB) ──Tailscale──► BeagleBoard-xM (app web :8080, OpenOCD GDB :3333)
                                          │ USB host
                                          ├── ST-Link (Nucleo o externo) ──SWD──► STM32
                                          ├── USB del propio STM32 (PA11/PA12) ──► STM32 (DFU)
                                          ├── USB-serie (CP210x/CH340/USB-JTAG) ──► ESP32 (esptool)
                                          └── USB del Arduino / USBasp ──► ATmega (avrdude)
```

## Estado del proyecto (30/09/2026)

- ✔ Aplicación instalada y funcionando en la BeagleBoard-xM (Ubuntu 16.04 + Python 3.9 compilado).
- ✔ Acceso remoto por Tailscale y servicio systemd que arranca solo.
- ✔ OpenOCD (fork de ST) compilado en la placa; conexión SWD con un **STM32H503CBT6** mediante un
  ST-LINK/V2 original verificada.
- ✔ Soporte de **ESP32/ESP8266** (esptool) y **Arduino/AVR** (avrdude): 28 placas en `chips.yaml`,
  vínculo binario ↔ placa y verificación del ID del chip en STM32. Probado en modo mock; falta en
  hardware real.
- ⏳ **Depurador en la web** (pestaña *Depurar*): hecho y probado con el simulador de GDB; falta
  compilar GDB 12 en la placa (`scripts/build_gdb.sh`, en curso) y probarlo con el H503.
- ✔ USB DFU del H503: la BeagleBoard no lo detectaba por un problema físico, **el cable USB-C o el
  puerto** (cambiando los dos aparece `0483:df11`; falta saber cuál de los dos). Falta grabar por DFU
  desde la web.
- ✔ **Depuración remota con GDB probada** (30/09/2026): con el contador binario
  ([firmware/h503_leds](firmware/h503_leds/)) en el H503, desde `arm-none-eabi-gdb` en la PC por
  Tailscale: halt, leer y escribir variables, continue, Ctrl+C, breakpoints, desconexión y reconexión.
  "Identificar" leyó el ID del chip por SWD.
- 📘 Guías en PDF: [cómo funciona](docs/Guia_1_Como_funciona.pdf) y
  [requisitos, instalación (placa y PC) y trabajo remoto desde PowerShell](docs/Guia_2_Instalacion_y_trabajo_remoto.pdf)
  (se generan con `docs/generar_guias.py`).
- 📄 Documentación completa, con diagramas de bloques y la bitácora de la puesta en marcha:
  [docs/Banco_STM32_Documentacion.pdf](docs/Banco_STM32_Documentacion.pdf) (se genera con
  `docs/generar_documentacion.py`).

Contenido: [Hardware](#hardware-y-conexiones) · [Seguridad](#seguridad) ·
[Instalación](#instalación-en-la-beagleboard-xm) · [Tailscale](#tailscale) · [Uso](#uso-de-la-interfaz) ·
[Configuración](#configuración) · [ESP32 y Arduino](#esp32-y-arduino) ·
[Binario ↔ placa](#binario--placa-no-grabar-el-micro-equivocado) · [DFU y BootControl](#usb-dfu-y-bootcontrol) ·
[Depurar en la web](#depurar-en-la-web-pestaña-depurar) · [Depurar con GDB](#depurar-con-gdb) · [Syncthing](#syncthing-alternativa-para-la-carpeta-de-proyectos) ·
[SD en solo lectura](#opcional-sistema-de-archivos-en-solo-lectura) · [Desarrollo](#desarrollo-en-la-pc-modo-mock) ·
[Problemas](#solución-de-problemas) · [Pendiente de verificar](#pendiente-de-verificar-en-hardware)

---

## Hardware y conexiones

- **BeagleBoard-xM** (OMAP3/DM3730, Cortex-A8, 512 MB, armhf) con Debian 11/12 en la microSD.
  Solo necesita 5 V y cable de red.
- **ST-Link** (V2, V2-1, V3; integrado en una Nucleo/Discovery o externo) en un USB de la placa,
  conectado al STM32 por **SWD** (SWDIO, SWCLK, GND, y NRST recomendado).
- Para **DFU**: el **USB del propio STM32** (no el del ST-Link) en otro USB de la placa. En una
  Nucleo suelen ser dos conectores distintos (el del ST-Link y el "USB USER"). Con los dos cables
  conectados podés elegir el método desde la interfaz sin tocar nada.

Tener en cuenta:

- El dispositivo DFU (`0483:df11`) **solo existe mientras el chip está en el bootloader**. Con tu
  firmware corriendo, ese USB no aparece (o aparece como tu dispositivo, p. ej. un CDC `/dev/ttyACM*`).
- Varias placas en DFU tienen el mismo VID:PID; se distinguen por **número de serie** (el bootloader
  de ST lo deriva del ID único del chip). La app lo pasa a `dfu-util -S`.
- **Alimentación**: los 4 USB de la xM comparten poca corriente. Si alimentás varias placas o una que
  consume mucho, usá un **hub USB con fuente propia**; si no, el USB se cae a mitad de un flasheo.
- **Condiciones del bootloader DFU**: algunas familias sólo hacen USB DFU con un cristal externo de
  ciertas frecuencias. Revisá la nota de aplicación **AN2606** de ST para tu chip.
- **ESP32 / ESP8266**: el USB de la placa de desarrollo (conversor CP210x/CH340 o el USB-JTAG
  interno de S3/C3/C6) aparece como `/dev/ttyUSB*` o `/dev/ttyACM*`. esptool lo pone en modo
  descarga solo, con DTR/RTS; si tu placa no tiene ese circuito, mantené BOOT apretado al grabar.
- **Arduino**: el USB de la placa (`/dev/ttyACM*` en Uno/Mega/Leonardo, `/dev/ttyUSB*` en clones con
  CH340). Para un ATmega sin bootloader, un **USBasp/USBtiny** en otro USB (ISP).

## Seguridad

La app puede ejecutar comandos en la placa, así que:

- **Accedé solo por Tailscale. No abras puertos en el router** ni hagas port-forwarding del 8080
  ni del 3333. La BeagleBoard no debe ser accesible desde internet.
- Además de Tailscale, la app **rechaza conexiones que no vengan de la red de Tailscale**
  (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) o de localhost (`security.allowed_networks` en `bench.yaml`).
  Así, ni siquiera alguien en la LAN del laboratorio puede entrar.
- **Login** con usuario y contraseña. La contraseña se guarda como hash **scrypt** (biblioteca
  estándar de Python, KDF recomendado por OWASP; se eligió en lugar de bcrypt/argon2 para no
  compilar nada en armhf). Si Python se compiló con OpenSSL < 1.1 (Ubuntu 16.04), `hashlib.scrypt`
  no existe y se usa **PBKDF2-HMAC-SHA256 con 600.000 iteraciones** (el login tarda ~2-3 s en la xM). La sesión es una cookie firmada `HttpOnly`, `SameSite=Strict`, con
  vencimiento. Hay límite de intentos fallidos por IP.
- **Nada de credenciales en el código**: van en `/etc/stm32-bench/bench.env` (permisos 640).
- Los pedidos que modifican algo exigen el header `X-Bench` (protección CSRF adicional).
- **Ningún comando se arma concatenando texto con la shell**: todo es `subprocess` con lista de
  argumentos y sin `shell=True`. Chips, interfaces y backends salen de listas blancas en los YAML;
  las rutas se resuelven dentro del directorio de proyectos (sin `..` ni symlinks hacia afuera) y los
  nombres de archivo tienen una lista blanca de caracteres. Esto último es importante porque los
  argumentos `-c` de OpenOCD son **Tcl**: una ruta con `[`, `]`, `{`, `}` o `$` podría inyectar
  código Tcl.
- El servicio corre con un **usuario sin privilegios** (`stm32bench`). Sudo le permite **solo**
  `poweroff` y `reboot` **sin argumentos** (`/etc/sudoers.d/stm32-bench`, validado con `visudo -c`).
- **El puerto GDB (3333) no tiene autenticación**: cualquiera que llegue a ese puerto controla el
  chip. Por eso escucha por defecto **solo en la IP de Tailscale**, y OpenOCD se lanza con los puertos
  telnet y Tcl deshabilitados. Si en tu tailnet hay otras personas, restringí los puertos con las
  **ACL de Tailscale** (ver [Tailscale](#tailscale)).

## Instalación en la BeagleBoard-xM

### En un solo comando, desde la PC (recomendado)

`desplegar.bat` (raíz del repo) arma un paquete con el código de tu PC (incluidos los cambios sin
commit), lo copia a la placa por `scp`, entra por SSH y corre `scripts/instalar_todo.sh`, que hace
todo solo. Necesita `ssh`/`scp` (cliente OpenSSH de Windows) y Git. En PowerShell, en la carpeta del repo:

```powershell
.\desplegar.bat -ConfigurarClave                 # una vez: clave SSH, después no pide la contraseña
.\desplegar.bat -Completo -Placa 192.168.0.27    # placa recién instalada: todo (5-9 h, corre en tmux)
.\desplegar.bat                                  # actualizar la app (2-5 min)
.\desplegar.bat -ActualizarConfig                # ídem, y usa los chips.yaml / bench.yaml nuevos
.\desplegar.bat -Simular                         # muestra lo que haría
```

`-Completo` agranda la partición, instala Tailscale (pide autorizarlo con un enlace), Python 3.9 en
Ubuntu 16.04, OpenOCD, la app y GDB. Pide al principio la contraseña de sudo y la de la web, y después
sigue solo en la sesión `instalar` de tmux (`tmux attach -t instalar` para mirarla). Todos los pasos son
idempotentes: si algo falla, se corrige y se vuelve a correr. Más detalle en la
[guía de instalación](docs/Guia_2_Instalacion_y_trabajo_remoto.pdf).

### Paso a paso

Resumen (los detalles de cada paso siguen abajo):

```bash
sudo apt update && sudo apt install -y git tmux
git clone <url-de-este-repo> ~/stm32-bench && cd ~/stm32-bench
tmux new -s openocd              # la compilación tarda: que no dependa de la sesión SSH
sudo bash scripts/build_openocd.sh
sudo bash scripts/install.sh
```

Si clonás o copiás el repo desde Windows, asegurate de que los scripts tengan fin de línea **LF**
(el `.gitattributes` del repo lo fuerza al clonar con git).

#### 0. Si la placa tiene la imagen vieja Ubuntu 16.04 (rcn-ee 2017)

Esa imagen trae Python 3.5 y dfu-util 0.8, y su partición ocupa solo 1,6 GB aunque la microSD sea más
grande. Antes de los pasos siguientes:

```bash
sudo dpkg --configure -a && sudo apt install -y apt-transport-https ca-certificates cloud-guest-utils
sudo growpart /dev/mmcblk0 2 && sudo resize2fs /dev/mmcblk0p2      # usar toda la microSD
tmux new -s prep
sudo bash scripts/prepare_ubuntu1604.sh    # Python 3.9 en /opt/python3.9 + dfu-util 0.11 (~1-2 h)
```

`install.sh` detecta `/opt/python3.9` solo. En 16.04 no hay `python3-libgpiod` y el kernel 4.9 no
soporta la API v2 de GPIO, así que el backend `gpio` de BootControl queda para más adelante (los
backends `manual` y `serial_command` funcionan). systemd 229 muestra avisos por algunas opciones de
endurecimiento del servicio que no conoce; se ignoran sin problema. Con una microSD de 4 GB el espacio
alcanza justo (los scripts borran lo que compilan); para la partición de datos y el overlay de solo
lectura conviene una de 8 GB o más.

#### 1. OpenOCD (fork de ST)

El OpenOCD de Debian es demasiado viejo para H5/H7. `scripts/build_openocd.sh` compila e instala en
`/usr/local` el fork oficial de ST (<https://github.com/STMicroelectronics/OpenOCD>, por defecto el tag
`openocd-cubeide-v2.2.0`, el que usa STM32CubeIDE):

- instala las dependencias (`build-essential`, `libtool`, `autoconf`, `automake`, `texinfo`,
  `pkg-config`, `libusb-1.0-0-dev`, `libhidapi-dev`...);
- clona o actualiza el código, compila con soporte ST-Link y jimtcl interno;
- crea un **swap temporal de 1 GB** si RAM+swap no alcanzan (con 512 MB, `-j2` se queda sin memoria);
- al final muestra `openocd --version` y verifica que estén `target/stm32h5x.cfg`,
  `target/stm32h7x.cfg`, las interfaces, etc.

**Es lento: en la xM tarda aproximadamente entre 45 y 90 minutos.** Correlo dentro de `tmux`
(si se corta el SSH, volvés con `tmux attach -t openocd`). Es **idempotente**: si el commit pedido
ya está instalado no recompila (`FORCE=1` para forzar; `OPENOCD_REF=<tag>` para otra versión).

#### 1b. GDB para la pestaña *Depurar* (opcional, tarda horas)

La pestaña *Depurar* maneja GDB en la placa. El de Ubuntu 16.04 (`gdb-multiarch` 7.11) no lee el
formato DWARF 5 que genera el GCC de STM32CubeIDE, así que se compila GDB 12 sólo para ARM:

```bash
tmux new -s gdb
sudo bash scripts/build_gdb.sh        # 3 a 6 horas en la xM; queda /usr/local/bin/arm-none-eabi-gdb
```

Es idempotente y usa un swap temporal de 1 GB. Sin este paso funciona todo lo demás (y el servidor
GDB para depurar desde la PC); la pestaña *Depurar* avisa que falta el GDB.

#### 2. La app

`sudo bash scripts/install.sh`:

1. instala `python3-venv`, `dfu-util`, `python3-libgpiod`, etc.;
2. crea el usuario `stm32bench` (grupos `plugdev`, `dialout`, `gpio`);
3. copia la app a `/opt/stm32-bench` y crea el venv con las dependencias, más esptool (sin
   `cryptography`); con `USE_PIWHEELS=1` agrega piwheels como índice extra (sólo para Debian recientes);
4. crea `/etc/stm32-bench` (config), `/var/lib/stm32-bench` (estado e historial) y
   `/srv/stm32-bench/proyectos`;
5. **pregunta usuario y contraseña** de la web y genera `/etc/stm32-bench/bench.env` con el hash y
   una clave de sesión aleatoria (si ya existe, no lo toca);
6. instala las **reglas udev** (`/etc/udev/rules.d/60-stm32-bench.rules`) para ST-Link
   (`0483:3748, 374b, 374e, 374f, 3752, 3753, 3754, 3757`...), bootloader DFU (`0483:df11`) y `gpiochip`;
7. genera `/etc/sudoers.d/stm32-bench`, lo **valida con `visudo -cf`** y recién entonces lo instala;
8. registra y arranca el servicio **systemd** `stm32-bench` (arranque automático y reinicio ante fallos).

Se puede volver a correr para actualizar la app: no pisa `bench.env` ni tus YAML (si cambiaron,
deja la versión nueva como `*.yaml.dist`).

Comandos útiles:

```bash
systemctl status stm32-bench
journalctl -u stm32-bench -f
sudo systemctl restart stm32-bench      # después de editar /etc/stm32-bench/*.yaml
```

Para cambiar la contraseña: generá un hash nuevo y reemplazá `BENCH_PASSWORD_HASH` en `bench.env`:

```bash
sudo /opt/stm32-bench/.venv/bin/python /opt/stm32-bench/scripts/hash_password.py
```

## Tailscale

En la BeagleBoard (Debian armhf está soportado):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=banco-stm32
tailscale ip -4                           # la IP 100.x.y.z del banco
```

Seguí el enlace que muestra `tailscale up` para autorizar el equipo. En la consola de administración de
Tailscale conviene **desactivar el vencimiento de la clave** de este equipo (key expiry): si vence,
el banco queda inaccesible hasta que alguien entre físicamente.

Luego, desde tu PC (con Tailscale activo): `http://banco-stm32:8080` (MagicDNS) o `http://100.x.y.z:8080`.

Opcional:

- **SSH por Tailscale**: `sudo tailscale up --ssh --hostname=banco-stm32`.
- **HTTPS**: `sudo tailscale serve --bg 8080` publica la app en `https://banco-stm32.<tu-tailnet>.ts.net`
  (solo dentro de la tailnet). En ese caso poné `BENCH_COOKIE_SECURE=1` en `bench.env`.
- **ACL** para que solo tu usuario llegue a la web y al GDB: etiquetá la placa
  (`sudo tailscale up --hostname=banco-stm32 --advertise-tags=tag:banco`, con `tag:banco` declarado
  en `tagOwners`) y en la política de la tailnet:

  ```json
  "grants": [
    { "src": ["tu-usuario@ejemplo.com"], "dst": ["tag:banco"], "ip": ["tcp:8080", "tcp:3333", "tcp:22"] }
  ]
  ```

## Uso de la interfaz

- **Placa**: elegí el chip, el **método** (ST-Link/SWD o USB DFU; solo se habilitan los que el chip
  declara en `chips.yaml`), la interfaz de OpenOCD y el **ST-Link** (se detectan todos los conectados
  por número de serie; con más de uno hay que elegir). Con DFU, se listan los dispositivos en modo
  DFU (`dfu-util -l`) o se deja en "Automático". Con **Listo**, la tarjeta se pliega a un resumen de
  dos líneas (placa, método y programador, con aviso si falta algo); **Cambiar** la despliega.
- **Columna plegable**: el ícono de la tarjeta Placa pliega la columna izquierda a un riel con la
  placa y los botones Flashear, Verificar y Reset, para dar más lugar a la lista, el código o las
  consolas. Los botones de acción quedan siempre visibles abajo de la columna.
- **Archivos**: **Subir firmware** o **Carpeta ESP-IDF**, o soltá archivos y carpetas en cualquier
  parte de la pestaña (si arrastrás sobre otra pestaña, se cambia sola a Archivos). Los subidos van a
  `proyectos/_subidos/<proyecto>/`. Los proyectos se pliegan con un clic; el buscador filtra por
  nombre o placa y **Sólo esta placa** deja los de la placa elegida. La placa de cada archivo es una
  etiqueta: con un clic se cambia. Al elegir un archivo, la placa se selecciona sola, y el archivo
  elegido queda a la vista. Solo se pueden **borrar** los subidos desde la web.
- **ESP32 / Arduino**: elegí el **puerto serie** de la placa (se listan con su VID:PID). Si el monitor
  serie tiene ese puerto abierto, la app lo cierra mientras graba y lo vuelve a abrir al terminar.
- **Acciones**: Flashear (program + verify + reset), Verificar, Borrar flash, Reset, Halt, Run e
  **Identificar** (lee el ID del chip / la firma / el tipo de ESP sin grabar nada).
  Para un `.bin` se pide la dirección de carga (por defecto la base de flash del chip). Con DFU, los
  botones de Reset/Halt/Run y el servidor GDB se deshabilitan y se explica por qué.
- **Un solo trabajo a la vez**: si el programador está ocupado, el pedido se rechaza (no se encola).
  Se puede **cancelar** el trabajo en curso.
- **Progreso**: debajo de las pestañas aparece la operación en curso con su **etapa** (Conectar,
  Borrar, Escribir, Verificar, Reiniciar), el **porcentaje** cuando la herramienta lo informa (esptool,
  dfu-util, avrdude; OpenOCD sólo informa etapas), el tiempo y **Cancelar**. El avance también se ve en
  el indicador de arriba y en el título de la pestaña del navegador. Al terminar queda el resultado
  (verde o rojo, con la duración y **Ver log**); si falló, dice el motivo que dio la herramienta (por
  ejemplo `openocd falló: Verify Failed`). Lo lee `app/progress.py` de la salida de cada herramienta.
- **Servidor GDB** (pestaña *Depurar*, sección plegable "Servidor GDB para VS Code o CubeIDE"):
  Iniciar/Detener; muestra `IP:puerto` para conectarse. Si querés flashear por SWD con el servidor
  activo, la app avisa y ofrece detenerlo.
- **Log en vivo** (Server-Sent Events): plegado a una línea con el último mensaje y un contador de
  errores; se abre solo si una operación falla. Abierto, permite buscar, ver sólo errores y avisos,
  copiar y fijarlo abierto. Las líneas de avance (`Writing at ... (45 %)`) se pisan en vez de apilarse.
  El **historial** guarda cada operación con su resultado.
- **Monitor serie**: puerto USB-serie (p. ej. el VCP de la Nucleo, `/dev/ttyACM0`), baudrate
  configurable y envío de texto con LF/CRLF/CR.
- **Sistema**: IP, uptime, temperatura (si el kernel la expone), RAM, disco, versiones de OpenOCD y
  dfu-util, y botones **Apagar** / **Reiniciar** con confirmación. Después de apagar:
  **esperá ~30 s antes de cortar la alimentación; la placa no se puede volver a encender por software.**

## Configuración

Archivos en `/etc/stm32-bench/` (en el repo, `config/`).

### `chips.yaml`: agregar un chip = agregar una entrada

```yaml
chips:
  - id: stm32u575zi                 # [a-z0-9_-], único
    name: STM32U575ZI
    family: U5 (Cortex-M33)
    flash: { base: 0x08000000, size_kb: 2048, sector_kb: 8 }
    default_method: swd
    methods:
      swd:
        target_cfg: target/stm32u5x.cfg       # relativo a los scripts de OpenOCD
        interface: stlink-dap                 # id de bench.yaml -> interfaces
        adapter_speed_khz: 4000
        flash_banks: [0]                      # bancos a borrar en "Borrar flash"
        # tcl_vars: { DUAL_BANK: 1 }          # variables antes del target (p. ej. H7 dual bank)
        # erase_cmds: ["stm32l4x mass_erase 0"]   # reemplaza el borrado genérico
      dfu:
        vid_pid: "0483:df11"
        alt: 0                                # "Internal Flash"
        # address: 0x08000000                 # por defecto flash.base
```

Incluye STM32H503CB (`target/stm32h5x.cfg`), STM32H723VG (`target/stm32h7x.cfg`) y ejemplos F1, F4,
L4, G0 y G4. Al arrancar, la app **verifica que los `.cfg` existan** en el OpenOCD instalado y avisa
en la interfaz si falta alguno.

Nota H5: la CPU del H5 está en el AP 1 del DAP, así que **no funciona con la interfaz HLA**
(`stlink.cfg`); hace falta `stlink-dap` (ST-Link V2 con firmware ≥ J24, V2-1 o V3).

### `bench.yaml`

- `openocd.binary`, `openocd.scripts_dir`, `dfu_util.binary` y los timeouts.
- `debugger.gdb_binary`: el GDB que usa la pestaña *Depurar* (`/usr/local/bin/arm-none-eabi-gdb`).
- `esptool.binary` (vacío: usa el esptool del venv, `python -m esptool`) y `esptool.timeout_s`;
  `avrdude.binary` y `avrdude.timeout_s`.
- `interfaces`: `stlink` (`interface/stlink.cfg`, `hla_swd`) y `stlink-dap`
  (`interface/stlink-dap.cfg`, `dapdirect_swd`). El transporte se pasa explícitamente porque
  `stlink-dap.cfg` no lo elige solo.
- El ST-Link elegido se pasa como `adapter serial <serie>` (OpenOCD ≥ 0.12 y fork de ST); la app
  detecta al arrancar si el OpenOCD instalado usa `adapter serial` o el viejo `hla_serial`.
- `gdb.port` (3333) y `gdb.bind`: `tailscale` (recomendado), `all`, `localhost` o una IP.
- `uploads.max_size_mb`, `scan.max_depth`, `serial.bauds`, `history.max_entries`.
- `security.allowed_networks` y `security.session_hours`.
- `boot_control`: ver la sección siguiente.

## ESP32 y Arduino

Cada placa de `chips.yaml` declara uno o más métodos: `swd` y `dfu` (STM32), `esptool` (Espressif) o
`avrdude` (AVR). La interfaz agrupa las placas por familia y muestra solo los botones que el método
soporta; los que no, quedan deshabilitados con el motivo.

| Método | Placas incluidas | Archivo | Acciones |
|---|---|---|---|
| `esptool` | ESP8266, ESP32, S2, S3, C2, C3, C5, C6, C61, H2, P4 | `.bin` | grabar, verificar, borrar, reset, identificar |
| `avrdude` (bootloader) | Uno, Nano (y bootloader viejo), Pro Mini, Duemilanove, Mega 2560/1280, Leonardo, Micro | `.hex` | grabar, verificar, reset, identificar |
| `avrdude` (ISP) | ATmega328P con USBasp (agregá otros copiando la entrada) | `.hex` | ídem + borrar |

```yaml
  - id: esp32-c3
    name: ESP32-C3
    default_method: esptool
    methods:
      esptool: { chip: esp32c3, baud: 460800 }     # app_address por defecto 0x10000
  - id: arduino-leonardo
    name: Arduino Leonardo
    default_method: avrdude
    methods:
      avrdude: { part: atmega32u4, programmer: avr109, baud: 57600,
                 flash_kb: 32, bootloader_kb: 4, touch_1200: true }
```

- **ESP, qué `.bin` subir**: desde Arduino IDE, *Sketch → Exportar binario compilado* genera
  `sketch.ino.bin` (solo la aplicación: se graba en `0x10000`) y `sketch.ino.merged.bin` (bootloader
  + particiones + app: se graba en `0x0`). La app reconoce cuál es y elige la dirección sola. El `.bin`
  se graba con `--flash_mode/--flash_freq/--flash_size keep` para no tocar el encabezado del bootloader.
- **Proyecto ESP-IDF (recomendado con ESP-IDF)**: en *Archivos*, arrastrá la **carpeta del proyecto**
  (o tocá *elegí la carpeta*). La web lee `build/flasher_args.json` y sube **solo los `.bin` que lista**
  (bootloader, tabla de particiones, programa y datos, si hay): unos KB o MB en vez de los cientos de MB
  de `build/`. Antes de subir muestra cada archivo con su dirección, tamaño y fecha. Cada `.bin` queda
  marcado con su dirección en la lista, y al grabar se elige **Sólo este archivo** (lo normal: el
  programa) o **Todo el proyecto**, con el modo, la frecuencia y el tamaño de flash del manifiesto.
  Una carpeta `build/` que llega por Syncthing funciona igual. Controles, al subir y otra vez al
  grabar: el chip del manifiesto y de cada imagen tiene que ser el de la placa; rutas de la lista
  blanca, solo `.bin` y nada cifrado; las imágenes no se pisan ni se salen de la flash; cada imagen cae
  dentro de una partición (y el programa entra en la suya); y **ninguna toca una partición NVS**,
  donde el programa guarda presets, ajustes y WiFi. Por eso no se usa un combinado en `0x0`: esptool
  rellena los huecos con `0xFF` y borraría NVS. En un proyecto así tampoco se elige la dirección a mano.
- **Cuidado con un `.bin` de ESP-IDF subido suelto**: sin `flasher_args.json` el banco lo grabaría en
  `0x10000`, que en proyectos con particiones propias puede caer dentro de NVS. Subí la carpeta.
- **Arduino, qué `.hex` subir**: *Sketch → Exportar binario compilado* genera `sketch.ino.hex` (sin
  bootloader, el que hay que usar) y `sketch.ino.with_bootloader.hex` (solo para ISP; por bootloader
  serie se rechaza porque pisa el bootloader).
- **Leonardo / Micro** (32U4): antes de grabar, la app abre el puerto a 1200 baudios para que el chip
  salte al bootloader y espera el puerto nuevo que aparece.
- **Borrar** en un Arduino por bootloader no existe (el bootloader no borra el chip completo): hace
  falta ISP. **Reset** pulsa DTR (o el toque de 1200 baudios en 32U4).
- **GDB, Halt y Run** solo están para STM32 por SWD. Un ESP32 se podría depurar por JTAG con
  OpenOCD-esp32, pero eso no está implementado.

## Binario ↔ placa: no grabar el micro equivocado

Cada archivo queda **vinculado a una placa** (se guarda en `firmware_meta.json`, en el directorio de
estado). Para grabar se exigen dos cosas: que el archivo tenga placa asignada **y** que esa placa
sea la elegida. Además, el **contenido** se revisa al subirlo, al reasignarlo y otra vez antes de
cada grabación (el archivo pudo cambiar):

| Familia | Qué se comprueba en el archivo | Qué se comprueba en el chip conectado |
|---|---|---|
| STM32 | `.elf` de arquitectura ARM; direcciones dentro de la flash del chip | OpenOCD lee `DBGMCU_IDCODE` antes de grabar y aborta si el ID no es el esperado (p. ej. `0x474` en H503) |
| ESP | encabezado `0xE9`, **ID de chip** del encabezado (ESP32 ≠ S3 ≠ C3…) y offset del bootloader en los combinados | esptool compara `--chip` con el chip que responde y aborta si no coincide |
| AVR | `.hex` válido, que entre en la flash y que no pise el bootloader | avrdude lee la **firma** del chip y aborta si no es la del `part` |

Limitación: un `.hex` de AVR **no dice para qué chip fue compilado**, así que un sketch de Uno y uno
de Nano (mismo ATmega328P) son indistinguibles; ahí la protección es la asignación que hacés al
subirlo más la firma que verifica avrdude. En ESP8266 el encabezado tampoco tiene ID de chip.

## USB DFU y BootControl

Con el método DFU:

1. Si el archivo es `.elf` o `.hex`, se **convierte a `.bin`** en un directorio temporal (con
   `pyelftools`/`intelhex`, sin necesitar el toolchain en la placa). Se usa la dirección de **carga**
   (LMA) de cada segmento, así los valores iniciales de `.data` quedan en flash. Si hay **segmentos
   discontinuos** se avisa (el hueco se rellena con 0xFF), y si hay datos **fuera de la flash
   interna** (QSPI, RAM sin dirección de carga, bytes de opción) la conversión se rechaza.
2. Si el chip no está ya en DFU, **BootControl** lo pone en bootloader y se espera a que aparezca
   `0483:df11` (con timeout).
3. `dfu-util -d 0483:df11 -a 0 -S <serie> -s 0x08000000:leave -D firmware.bin` (el `:leave` sale del
   bootloader y ejecuta; se puede desmarcar). Verificar lee la flash con `-U` y compara; borrar usa
   `:mass-erase:force`.
4. BootControl devuelve el chip a su aplicación (siempre, aunque la operación falle).

Backends (`boot_control.backend`):

- **`manual`**: la web muestra "Poné BOOT0 en alto y reseteá la placa" con una cuenta regresiva y
  sigue sola cuando aparece el dispositivo DFU. Al terminar recuerda volver BOOT0 a 0.
- **`gpio`**: la BeagleBoard maneja **BOOT0** y **NRST** con **libgpiod** (`/dev/gpiochipN`, nunca
  sysfs): BOOT0=1, pulso de NRST, espera DFU, y al final BOOT0=0 y pulso de NRST. Número de chip,
  línea y polaridad (`active_low`) configurables.
- **`serial_command`**: envía un comando configurable (p. ej. `"BOOTLOADER\n"`) por un puerto serie
  a tu firmware, que tiene que saltar al bootloader de sistema. La dirección de la System Memory de
  cada familia está en la AN2606. Esquema en el firmware:

  ```c
  /* Al recibir "BOOTLOADER\n": */
  __disable_irq();
  HAL_RCC_DeInit(); HAL_DeInit();                 /* periféricos y relojes a reset */
  SysTick->CTRL = 0; SysTick->LOAD = 0; SysTick->VAL = 0;
  for (int i = 0; i < 8; i++) { NVIC->ICER[i] = 0xFFFFFFFF; NVIC->ICPR[i] = 0xFFFFFFFF; }
  __enable_irq();
  uint32_t base = BOOTLOADER_ADDR;               /* ver AN2606 para tu chip */
  __set_MSP(*(volatile uint32_t *)base);
  ((void (*)(void))(*(volatile uint32_t *)(base + 4)))();
  ```

### ⚠️ Cableado del backend `gpio`: los GPIO de la BeagleBoard-xM son de 1,8 V

**Los GPIO del conector de expansión de la BeagleBoard-xM trabajan a 1,8 V y NO toleran 3,3 V.
No los conectes directo a BOOT0 ni a NRST del STM32 (3,3 V): hace falta adaptación de nivel.**

```
  NRST (open-drain con transistor NPN; config: nrst.active_low = false)

   GPIO NRST (1,8 V) ──[ 4k7 ]──┐ B
                                 │   2N3904 / BC547 / MMBT3904
                                 C ────────────────► NRST del STM32
                                 E ──┐                (tiene pull-up interno y
                                     GND común        el capacitor de la placa)

   GPIO en 1  -> transistor conduce -> NRST = 0 (reset)
   GPIO en 0  -> transistor abierto -> NRST lo sube el pull-up del STM32

  BOOT0 (traductor de nivel no inversor; config: boot0.active_low = false)

   VIO_1V8 de la xM ──► VCCA   SN74LVC1T45   VCCB ◄── 3,3 V del target
   GPIO BOOT0 ───────► A                    B ──[ 1k ]──► BOOT0 del STM32
                       DIR ── VCCA (A -> B)       GND común
```

- **NRST nunca debe manejarse en push-pull a 3,3 V**: el transistor sólo lo lleva a 0; el STM32 y
  la placa lo suben. Así también funciona el botón de reset.
- Para BOOT0 **evitá un NPN inversor con pull-up**: si la BeagleBoard se apaga, BOOT0 quedaría en 1
  y el STM32 arrancaría siempre en el bootloader. El traductor no inversor deja BOOT0 en 0 (por la
  resistencia de pull-down de la placa) cuando la xM no lo maneja. Si usás otra etapa que invierte,
  poné `active_low: true`.
- **GND común** entre la BeagleBoard y el target.
- Numeración: el SRM de la xM nombra los pines como `GPIO_n`; con libgpiod es el banco `n // 32`
  (gpiochip) y la línea `n % 32`. Confirmalo en la placa con `gpiodetect` y `gpioinfo`, y verificá que
  el **pinmux** del pin esté en modo GPIO. El ejemplo de `bench.yaml` (`gpiochip4`, líneas 11 y 12)
  es solo ilustrativo.
- Probá sin el STM32 conectado: `gpioset gpiochip4 11=1` y medí con el tester.

## RTT: `printf` por el ST-Link

RTT (*Real-Time Transfer*) lleva el texto del programa por los mismos cables del SWD: no hace falta
adaptador USB-serie ni pines extra, y el micro no se frena (sirve con audio o control). El programa
escribe en un buffer en RAM y OpenOCD lo lee mientras corre.

- **En el firmware**: usá SEGGER RTT (`SEGGER_RTT_printf`, viene con CubeIDE/X-CUBE) o la versión mínima
  de este repo, `firmware/h503_leds/src/rtt.c` (`rtt_init()`, `rtt_escribir()`, `rtt_leer()`). La app busca el
  bloque de control por el símbolo **`_SEGGER_RTT`** del `.elf`. Ejemplo: `05_contador_rtt`.
- **En la web**: elegí el `.elf` del programa en *Archivos*, y en *Monitor serie* el puerto
  **"RTT — por el ST-Link"** → *Abrir*. Lo que escribas en *Enviar* llega al programa (canal de bajada).
- Funciona junto con la depuración: al iniciar el servidor GDB o la pestaña *Depurar* con un `.elf` que
  tiene RTT, OpenOCD publica también el canal (puerto `rtt.port`, 9090, en la IP de Tailscale; se puede
  leer desde la PC con cualquier cliente TCP, p. ej. PuTTY en modo *Raw*).
- Si el programa todavía no había creado el bloque (por ejemplo, al "Grabar y parar en main"), la
  depuración vuelve a buscarlo en cada parada.

## USB: diagnóstico y energía por puerto

En *Sistema → USB* se ve todo lo que está enchufado (VID:PID, nombre, puerto serie, en qué hub y puerto).
Sirve para diagnosticar: un STM32 en modo DFU tiene que aparecer como `0483:df11`.

Con un **hub USB que permita cortar la energía por puerto** (los compatibles con
[uhubctl](https://github.com/mvp/uhubctl#compatible-usb-hubs)), cada puerto tiene botones **Reiniciar**
(apaga 3 s y prende), **Apagar** y **Prender**. Protecciones:

- nunca se corta un puerto que lleve a un adaptador de red (en la xM el Ethernet cuelga del mismo chip
  que los USB: cortarlo dejaría la placa inaccesible);
- en hubs que cortan todos los puertos juntos (*ganged*) sólo se permite Reiniciar: uhubctl corre en la
  placa y vuelve a dar energía solo, aunque la red se corte mientras tanto;
- no se corta nada mientras hay una operación en curso.

`install.sh` instala uhubctl (`scripts/build_uhubctl.sh`, compila en segundos en 16.04) y una regla
udev para que el servicio pueda manejar los hubs sin root. Si el hub de la placa no lo soporta, la
sección lo dice y sigue mostrando la lista de dispositivos.

## MAC fija (BeagleBoard-xM)

El chip de red de la xM (LAN9514) no tiene EEPROM: el driver inventa una MAC distinta en cada arranque y
el router le da otra IP local. `scripts/fijar_mac.sh` guarda la MAC actual en `/etc/stm32-bench/mac.conf`
y un servicio (`stm32-bench-mac`) la pone en cada arranque, antes de levantar la red. `instalar_todo.sh`
lo hace solo la primera vez; a mano:

```bash
sudo bash scripts/fijar_mac.sh                    # fija la MAC de ahora (la IP actual se conserva)
sudo bash scripts/fijar_mac.sh 02:5e:42:00:00:01  # otra MAC (se aplica al reiniciar)
sudo bash scripts/fijar_mac.sh --quitar
```

Conviene además reservar una IP para esa MAC en el router (DHCP estático). En *Sistema* se ve la MAC y
si está fija.

## Depurar en la web (pestaña *Depurar*)

Para depurar sin instalar nada en la PC. La app maneja GDB en la placa (en modo MI, la interfaz para
programas) y lo conecta al servidor GDB de OpenOCD:

```
navegador ──HTTP/SSE──► app (DebugSession) ──MI──► arm-none-eabi-gdb ──TCP──► OpenOCD :3333 ──SWD──► STM32
```

**Qué hace falta**

- Un `.elf` compilado con información de depuración (configuración *Debug*, `-g`), **asignado a su
  placa** como cualquier firmware.
- **Las fuentes** (`.c`, `.h`, `.s`) en la carpeta del proyecto. El `.elf` guarda las rutas de la PC
  donde se compiló (`C:/Users/.../Core/Src/main.c`); la app las busca dentro del proyecto: relativas a
  la carpeta del `.elf` y sus padres (CubeIDE: `../Core/Src/main.c`) o por nombre de archivo. Dos formas:
  - subir el proyecto como **`.zip`** en la pestaña Archivos (con la placa elegida). Se extrae en
    `_subidos/<proyecto>/<carpeta>/` sólo lo que sirve: `.elf/.hex/.bin`, `.c/.h/.s/.cpp/.ld`. Lo
    demás (`.o`, `.settings`...) se ignora. Subir de nuevo el mismo `.zip` reemplaza la carpeta;
  - o sincronizar la carpeta del proyecto con Syncthing.
- GDB 12 en la placa (`scripts/build_gdb.sh`, ver Instalación) y `debugger.gdb_binary` en `bench.yaml`.

**Uso**

1. Elegí el `.elf` en *Archivos* y abrí *Depurar*. Ya se ve el código: los números de línea en negrita
   tienen código; **clic en el número = breakpoint** (antes de iniciar quedan como `○` y se aplican al
   empezar).
2. Elegí cómo empezar y tocá **Iniciar depuración**:
   - *Grabar y parar en main*: graba el `.elf` (`load` de GDB), reinicia y frena al principio de `main()`;
   - *Reiniciar y parar en main*: igual pero sin grabar (el programa ya está en el chip);
   - *Conectarse sin reiniciar*: frena el programa donde esté, como el `target extended-remote` de antes.

   Si el servidor GDB no estaba iniciado, se inicia solo. Antes de grabar se lee el **ID del chip**
   (`DBGMCU_IDCODE`) y si no es el de la placa, no se toca nada.
3. Controles (y atajos, con la pestaña abierta): **Continuar** (F5), **Pausar** (F6), **Paso** (F10,
   ejecuta la línea sin entrar a las funciones), **Entrar** (F11), **Salir** (Shift+F11, termina la
   función actual), **Reset** (reinicia el micro y queda detenido).
   El selector **×1 / ×5 / ×10 / ×50 / ×100** repite Paso y Entrar esa cantidad de veces: frena antes
   si aparece un breakpoint o el programa termina, se corta con Pausar, y las variables se leen al final.
4. Al detenerse se marca la línea actual (y se abre el archivo que corresponda) y se actualizan:
   - **Variables locales** y **globales** del proyecto: clic en un valor para cambiarlo
     (números `123`, `0x7B`, `0b101`, `'a'`, `true/false`). Los que cambiaron desde la última parada
     se ven en rojo;
   - **Vigilar**: expresiones de C (`contador & 0xFF`, `buffer[3]`, `p->campo`, `$pc`). Por
     seguridad no se aceptan variables de GDB con `$` salvo registros;
   - **Pila de llamadas** (clic = ir a esa línea), **breakpoints** (con la cantidad de veces que se
     cumplieron) y **registros** (r0–r12, sp, lr, pc, xpsr, msp, psp...).
5. **Detener** desconecta GDB y el programa sigue corriendo en el chip. Los breakpoints quedan
   guardados (en el navegador) para la próxima sesión.

Notas:

- OpenOCD acepta **un solo cliente GDB**: mientras depurás en la web no se puede conectar un GDB
  externo, y al revés. Flashear desde *Acciones* detiene la depuración (pide confirmación).
- Agregar o sacar un breakpoint con el programa corriendo lo pausa un instante y lo reanuda.
- El STM32 tiene pocos breakpoints de hardware (8 en el H5, 6 en F1/F4); si se pasa del límite GDB
  avisa al continuar.
- Sólo STM32 por SWD. Todavía no hay vista de memoria, watchpoints ni periféricos por nombre (SVD).

## Depurar con GDB

En la pestaña **Servidor GDB**, "Iniciar" lanza OpenOCD en modo servidor con el chip, la interfaz y el
ST-Link elegidos, y muestra la dirección para conectarse (p. ej. `100.101.102.103:3333`).

### `arm-none-eabi-gdb`

```text
$ arm-none-eabi-gdb build/Debug/firmware.elf
(gdb) target extended-remote 100.101.102.103:3333
(gdb) monitor reset halt
(gdb) load
(gdb) break main
(gdb) continue
```

Con el servidor activo, los botones Reset/Halt/Run de la web se reemplazan por `monitor reset halt`,
`monitor halt` y `monitor resume` desde GDB.

En Windows, `arm-none-eabi-gdb.exe` viene con STM32CubeIDE (en
`C:\ST\STM32CubeIDE_x\STM32CubeIDE\plugins\...gnu-tools-for-stm32...\tools\bin\`).
[firmware/h503_leds/depurar.bat](firmware/h503_leds/depurar.bat) lo lanza ya conectado al banco.

- **No abras `IP:3333` en el navegador**: no es una página web. OpenOCD recibe texto HTTP
  (`ignoring character 0x..`) y el navegador ocupa la **única** conexión GDB permitida; las
  siguientes se rechazan (`rejected 'gdb' connection, no more connections allowed`).
- Por Tailscale conviene `set remotetimeout 10` para evitar el aviso de `keep_alive`.
- Al conectarse, GDB detiene el núcleo; al salir con `quit` se desconecta y el programa sigue.

### VS Code (Cortex-Debug, `servertype: "external"`)

`.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Banco remoto (STM32)",
      "type": "cortex-debug",
      "request": "launch",
      "servertype": "external",
      "gdbTarget": "100.101.102.103:3333",
      "executable": "${workspaceFolder}/build/Debug/firmware.elf",
      "gdbPath": "arm-none-eabi-gdb",
      "svdFile": "${workspaceFolder}/STM32H723.svd",
      "runToEntryPoint": "main",
      "overrideLaunchCommands": ["monitor reset halt", "load", "monitor reset halt"],
      "overrideRestartCommands": ["monitor reset halt"]
    },
    {
      "name": "Banco remoto (adjuntar sin cargar)",
      "type": "cortex-debug",
      "request": "attach",
      "servertype": "external",
      "gdbTarget": "100.101.102.103:3333",
      "executable": "${workspaceFolder}/build/Debug/firmware.elf",
      "gdbPath": "arm-none-eabi-gdb"
    }
  ]
}
```

(Si usás MagicDNS: `"gdbTarget": "banco-stm32:3333"`.)

### STM32CubeIDE

*Debug Configurations* → *STM32 C/C++ Application* → pestaña *Debugger* → *Debug probe*
"ST-LINK (OpenOCD)" → marcar **Connect to remote GDB server**, *Host name* = IP de Tailscale,
*Port* = 3333.

## Syncthing (alternativa para la carpeta de proyectos)

En lugar de subir por la web, podés sincronizar la carpeta de compilación de tu PC con
`/srv/stm32-bench/proyectos` (la app la escanea y muestra cada subcarpeta como proyecto; ignora
`.stfolder`, `.stversions` y los temporales de Syncthing).

```bash
sudo apt install -y syncthing
sudo adduser --system --group --home /var/lib/syncthing-bench syncthing-bench
sudo usermod -a -G stm32bench syncthing-bench      # puede escribir en proyectos (2775)
sudo systemctl enable --now syncthing@syncthing-bench
```

- La GUI de Syncthing escucha en `127.0.0.1:8384`: accedé con un túnel
  (`ssh -L 8384:127.0.0.1:8384 usuario@banco-stm32`) o cambiala a la IP de Tailscale.
- En la placa, compartí la carpeta como **"Receive Only"** (solo recibir): así nada de lo que pase en el
  banco se propaga a tu PC.
- Agregá `_subidos` al `.stignore` para que las subidas por web no se mezclen.
- Consumo: Syncthing usa unos 50–100 MB de RAM en la xM; con 512 MB alcanza, pero no lo dejes
  escaneando carpetas enormes (compartí solo `build/` o `Debug/`).

## Opcional: sistema de archivos en solo lectura

La microSD se puede corromper si se corta la energía mientras escribe. Se puede montar la raíz en
**solo lectura con overlay** (los cambios van a RAM y se pierden al reiniciar) y dejar los datos que
deben persistir en una **partición aparte o en un pendrive**. Es un cambio delicado: hacelo cuando el
banco ya funcione y con una copia de la SD.

1. **Partición de datos** (en la SD o un pendrive USB), ext4, montada en `/srv/stm32-bench`:

   ```text
   # /etc/fstab
   LABEL=benchdata  /srv/stm32-bench  ext4  defaults,noatime,commit=30  0  2
   ```

   Mové ahí lo que debe persistir y montalo con bind:
   - `/var/lib/stm32-bench` (historial) → `/srv/stm32-bench/state` y `BENCH_STATE_DIR` en `bench.env`;
   - **`/var/lib/tailscale`** (¡si no, cada reinicio pide volver a autorizar la placa!);
   - la configuración de Syncthing, si lo usás.

   ```text
   /srv/stm32-bench/tailscale  /var/lib/tailscale  none  bind  0  0
   ```

2. **Raíz con overlay** usando `overlayroot`:

   ```bash
   sudo apt install -y overlayroot
   echo 'overlayroot="tmpfs:recurse=0"' | sudo tee /etc/overlayroot.conf
   sudo update-initramfs -u
   ```

   `recurse=0` hace que sólo la raíz vaya en RAM: la partición de datos sigue siendo de escritura.
   La xM tiene que arrancar **con initramfs** (revisá que `uEnv.txt`/el bootloader cargue el initrd).
   Después de reiniciar, `mount | grep overlay` debe mostrar la raíz como overlay.

3. **Logs en RAM**: `Storage=volatile` en `/etc/systemd/journald.conf`.

4. **Para hacer cambios** (actualizar la app, `apt`): `sudo overlayroot-chroot` y trabajar ahí, o
   desactivarlo temporalmente con `overlayroot=disabled` en la línea de comandos del kernel.

Si `overlayroot` no está disponible para tu versión de Debian, la alternativa es montar la raíz `ro`
en `fstab` y poner `/var/log`, `/tmp` y `/var/tmp` en `tmpfs`.

## Desarrollo en la PC (modo mock)

Con `BENCH_MOCK=1` la app **simula OpenOCD, dfu-util, los ST-Link, el dispositivo DFU, el flujo de
BootControl, el puerto serie y la información del sistema**, así se puede desarrollar y probar la
interfaz sin hardware. Los simuladores (`scripts/mock_openocd.py`, `scripts/mock_dfu_util.py`) se
ejecutan como subprocesos reales, así que el camino de ejecución y el log en vivo son los mismos que
en la placa.

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\dev_run.ps1
```

```bash
# Linux / macOS
scripts/dev_run.sh
```

Abrí `http://127.0.0.1:8080`. El usuario es `dev` y la **contraseña se genera al azar en cada
arranque** y se muestra en la consola (o definí `BENCH_PASSWORD_HASH`). En el primer arranque se crean
proyectos de ejemplo en `dev-data/proyectos/`:

- `demo-blink/Debug/` (`.elf`, `.hex`, `.bin`): todo funciona;
- `casos-dfu/con-hueco.hex`: DFU avisa de segmentos discontinuos;
- `casos-dfu/flash-externa.hex`: DFU rechaza datos fuera de la flash;
- `casos-dfu/demo-falla.elf`: OpenOCD simula una verificación fallida;
- `h503-contador/` (el contador binario real, `.elf` + fuentes): para la pestaña *Depurar*, con
  `scripts/mock_gdb.py`, que simula GDB ejecutando ese programa con sus líneas reales.

Otras fallas simuladas: `BENCH_MOCK_FAIL=connect` (OpenOCD no conecta al target) y
`BENCH_MOCK_DFU_LEAVE_ERROR=1` (el error típico de dfu-util al salir con `:leave`).

### Tests

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt    # Windows (.venv/bin/... en Linux)
.venv/Scripts/python -m pytest
node --test "tests/js/*.test.js"                                  # funciones de la web (Node 18+)
```

Cubren: validación de archivos y path traversal, construcción de comandos de OpenOCD y dfu-util
(listas de argumentos, inyección Tcl), carga y validación de `chips.yaml`/`bench.yaml`, selección de
método, conversión elf/hex→bin, flujo de BootControl (gpio con driver falso, manual, serial, mock y
DFU completo con el simulador), detección de ST-Link por sysfs, autenticación y la API, análisis de
`.bin` de ESP y `.hex` de AVR, comandos de esptool/avrdude, vínculo archivo ↔ placa y flujos completos
de ESP32, Uno, Leonardo, ISP e identificación de STM32 con los simuladores (`mock_esptool.py`,
`mock_avrdude.py`, `mock_openocd.py`). Para el depurador: el protocolo GDB/MI, la validación de
expresiones y asignaciones, la lectura de fuentes y líneas del `.elf` (DWARF 3 y 5, rutas de Windows),
la subida de proyectos `.zip` (rutas maliciosas, límites) y sesiones completas con `mock_gdb.py`
(breakpoints, paso a paso, variables, pausa, reset, chip equivocado). El progreso de los trabajos se
prueba con la salida real de OpenOCD, esptool, dfu-util y avrdude (`tests/test_progress.py`), y las
funciones puras de la web (`web/ui-utils.js`: fechas relativas, nombres cortos de placa, textos de
progreso, filtro de archivos) con `node --test`.

## Estructura del repositorio

```
app/
  main.py            FastAPI, middleware de seguridad, sesiones, estáticos
  api.py             rutas /api/...
  bench.py           servicio central: valida pedidos y arma los trabajos SWD/DFU/ESP/AVR
  settings.py        variables de entorno y bench.yaml
  chips.py           chips.yaml y verificación de .cfg
  compat.py          ¿este archivo es para esta placa? (ARM/ESP/AVR)
  elfinfo.py         fuentes y líneas con código de un .elf (DWARF), búsqueda de fuentes en el proyecto
  rtt.py             cliente del canal RTT de OpenOCD (como un puerto más del monitor serie)
  usb.py             dispositivos USB (sysfs) y energía por puerto (uhubctl)
  debugger/          pestaña Depurar: mi.py (protocolo GDB/MI), session.py (sesión de GDB)
  firmware_meta.py   vínculo archivo -> placa (firmware_meta.json)
  files.py           validación, subida, escaneo, borrado
  convert.py         .elf/.hex -> .bin (DFU)
  probe.py           ST-Link por sysfs y dispositivos DFU
  programmers/       base (capacidades), openocd.py, dfu.py, esptool.py, avrdude.py (comandos puros),
                     runner.py (un trabajo a la vez), gdbserver.py
  bootcontrol/       manual, gpio (libgpiod), serial_cmd, mock
  serial_monitor.py  monitor serie (pyserial)
  system.py          estado del sistema, apagar/reiniciar
  events.py          bus para Server-Sent Events
  history.py         historial (JSON Lines)
web/                 index.html, app.js, style.css, login (sin build, sin dependencias JS)
config/              chips.yaml, bench.yaml, bench.example.env
scripts/             desplegar.ps1, instalar_todo.sh, build_openocd.sh, build_gdb.sh, install.sh, hash_password.py, dev_run.*, simuladores
deploy/              systemd, udev, sudoers
firmware/h503_leds/  firmware de prueba para STM32H503 (LEDs en PA0..PA7), fuentes y compilados
docs/                documentación PDF y su generador
tests/               pytest
```

## Solución de problemas

- **"No se detectó ningún ST-Link"**: `lsusb | grep 0483`. Si aparece pero la web dice "sin permisos",
  revisá las reglas udev (`udevadm control --reload-rules && udevadm trigger`, o desenchufar/enchufar)
  y que `stm32bench` esté en `plugdev` (`id stm32bench`).
- **`Error: open failed` / `unable to find a matching ST-Link`**: el número de serie no coincide.
  Con ST-Link/V2 viejos (serie binaria) actualizá el firmware del ST-Link (≥ V2.J21.S4).
- **H5 no conecta con `stlink`**: usá `stlink-dap` (el H5 necesita dapdirect).
- **Error con `stlink-dap`**: ST-Link V2 con firmware anterior a J24 no soporta dapdirect: actualizalo o
  usá `stlink` (HLA) en chips que lo permitan.
- **DFU "No apareció el dispositivo"**: mirá *Sistema → USB* (y el log, que al fallar lista lo que ve la
  placa por USB). Si no aparece `0483:df11`: cable sólo de carga (probalo en la PC con STM32CubeProgrammer
  en modo USB), cable en el USB del ST-Link en lugar del del STM32, BOOT0 no quedó en 1 al resetear, algo
  conectado a los pines de otra interfaz del bootloader (un adaptador serie en PA9/PA10 puede hacer que
  elija la USART), poca energía en los USB de la xM, o el chip necesita cristal externo para USB DFU (AN2606).
- **RTT: "no se encontró el bloque de control"**: el programa que corre en el micro no es el `.elf` elegido
  o no llamó todavía a `rtt_init()`. Grabalo y volvé a abrir RTT.
- **dfu-util devuelve error después de "File downloaded successfully"** con `:leave`: es habitual
  (el dispositivo se desconecta antes de responder); la app lo trata como éxito y lo avisa en el log.
- **La web no carga desde tu PC**: ¿Tailscale activo en ambos lados? ¿Tu IP está en
  `security.allowed_networks`? `journalctl -u stm32-bench -n 50`.
- **Apagar/Reiniciar da error de sudo**: `sudo -l -U stm32bench` debe listar solo poweroff y reboot.
- **ESP: `Failed to connect ... No serial data received`**: la placa no entró en modo descarga.
  Mantené BOOT apretado, tocá EN/RST y soltá BOOT al empezar a grabar; o bajá `baud` en `chips.yaml`.
- **ESP: `This chip is ESP32-S3, not ESP32`**: la placa conectada no es la elegida (la protección
  funcionó). Elegí la placa correcta y subí el binario compilado para ella.
- **Arduino: `stk500_getsync() not in sync`**: puerto equivocado, placa con bootloader viejo (probá
  "Arduino Nano (bootloader viejo)", 57600 baud) o el monitor serie de otra app tiene el puerto.
- **Arduino: `Expected signature for ...`**: el chip conectado no es el de la placa elegida.
- **"Este archivo no tiene placa asignada"**: asignale la placa en su fila de la lista de archivos.

## Pendiente de verificar en hardware

Todo lo siguiente se probó solamente con los simuladores del modo mock o con tests unitarios.
Verificalo en la BeagleBoard:

1. `scripts/build_openocd.sh` completo en la xM (tiempo, swap temporal, jimtcl) y que el OpenOCD
   instalado traiga `stm32h5x.cfg`, `stm32h7x.cfg` y `stlink-dap.cfg`.
2. Que las wheels de `pydantic-core` (FastAPI) se instalen en armv7 sin compilar (PyPI/piwheels) con
   tu versión de Debian/Python.
3. `scripts/install.sh`: creación de usuario/grupos, udev, `visudo -cf` del sudoers generado y
   arranque del servicio systemd.
4. Flashear/verificar/borrar/reset/halt/run reales en **STM32H503** (con `stlink-dap`) y **STM32H723**,
   con la salida real de OpenOCD. En particular: borrado genérico `flash erase_sector 0 0 last`,
   `verify_image` con el núcleo detenido y que halt/run por separado se comporten como se espera.
5. Selección por `adapter serial` con dos ST-Link conectados, y la conversión a hex de la serie de un
   ST-Link/V2 viejo (que coincida con la que espera OpenOCD).
6. ~~Servidor GDB y `arm-none-eabi-gdb` por Tailscale~~ ✔ probado con el H503. Falta VS Code
   (Cortex-Debug) y STM32CubeIDE como cliente.
7. DFU real: parseo de `dfu-util -l` de tu versión, `:leave`, lectura `-U` para verificar y
   `:mass-erase:force` **sin** `-D` (depende de la versión de dfu-util: en algunas hace falta un
   archivo). H503 y H723 en DFU (condiciones de AN2606).
8. Backend `gpio`: API de `python3-libgpiod` (1.x de Debian o 2.x), numeración gpiochip/línea, pinmux,
   niveles con el adaptador de 1,8 V → 3,3 V y tiempos de reset.
9. Backend `serial_command` con tu firmware.
10. Monitor serie con el VCP de la Nucleo (`/dev/ttyACM0`), desconexión en caliente.
11. Datos del sistema en la xM: IP de `tailscale0` por ioctl, temperatura (el DM3730 puede no exponer
    ninguna zona térmica), RAM y disco.
12. Apagar y reiniciar vía sudo, y el tiempo real hasta poder cortar la energía.
13. Consumo de RAM/CPU de la app en la xM (objetivo: < 80 MB) y fluidez del log en vivo.
14. La sección opcional de overlay de solo lectura (`overlayroot`, initramfs en la xM).
15. **Identificar** en STM32: el Tcl `read_memory` sobre `DBGMCU_IDCODE` con el OpenOCD de ST y el ID
    real de cada familia (sobre todo G0, cuyo DBGMCU está en `0x40015800`).
16. esptool 4.12 instalado `--no-deps` en el venv de la xM (sin `cryptography`) y grabación real de
    un ESP32 y un ESP32-S3/C3 (USB-JTAG nativo), incluido el reset automático por DTR/RTS.
17. avrdude de Ubuntu 16.04 (6.2) con Uno, Nano con bootloader viejo, Mega 2560 (`wiring`, `-D`) y
    Leonardo (toque de 1200 baudios y cambio de puerto), y un USBasp por ISP.
18. Que cerrar y reabrir el monitor serie durante una grabación funcione con los drivers reales
    (cdc_acm, cp210x, ch341) del kernel 4.9.
19. **Pestaña Depurar** con el H503: compilación de GDB 12 en la xM (`build_gdb.sh`), los comandos
    MI contra el OpenOCD real (`-target-download`, `monitor reset halt`, breakpoints de hardware en
    flash, `-exec-interrupt` con `mi-async`), los nombres de registros que da OpenOCD, y el tiempo que
    tarda pyelftools en leer un proyecto grande de CubeIDE (con HAL) en la xM.
20. **RTT** con el OpenOCD de ST y `stlink-dap`: `rtt setup/start/server start`, `rtt polling_interval`
    (puede no existir en esa versión: va dentro de `catch`), lectura con el micro corriendo y canal de bajada.
21. **Energía por puerto**: qué informa `uhubctl` del hub LAN9514 de la xM (¿ppps o ganged?) y de un hub
    externo; permisos con la regla udev; que Reiniciar devuelva el dispositivo.
22. **MAC fija**: que `stm32-bench-mac.service` corra antes de que la red tome la interfaz en la imagen
    rcn-ee (ifupdown/connman) y que la IP local se mantenga después de reiniciar.
23. **Proyecto ESP-IDF desde la web** con el ESP32-P4 (Waveshare ESP32-P4-WIFI6-Touch-LCD-7B, CH343
    → `/dev/ttyACM0`): arrastrar la carpeta en Chrome/Edge/Firefox, la confirmación del navegador al
    elegir una carpeta con el botón, grabar solo el programa y todo el proyecto con esptool 4.12, y
    que los presets en NVS sobrevivan.

## Licencia

[MIT](LICENSE) © 2026 Elian Ulrich. Podés usar, modificar y distribuir el código citando al autor.
