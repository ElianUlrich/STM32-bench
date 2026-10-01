# Firmware de prueba — STM32H503CBT6 con LEDs en PA0..PA7

Cuatro programas mínimos (C sin HAL, 1-2 KB cada uno) para probar el banco. Ya vienen compilados en
`build/` (`.elf`, `.hex`, `.bin`); sólo hay que subirlos desde la web.

| Programa | Qué hace | Para probar |
|---|---|---|
| `01_blink_todos` | Enciende los LEDs de a uno (PA0 → PA7) y después parpadean todos a 1 Hz | Flashear, Verificar, orden de los LEDs |
| `02_auto_fantastico` | Una luz que va y viene de PA0 a PA7 | **Halt** congela la luz, **Run** la reanuda, **Reset** reinicia el recorrido. Tiene una variable en `.data` (prueba la conversión .elf → .bin de DFU) |
| `03_contador_binario` | Cuenta 0..255 en binario (PA0 = bit 0) cada 250 ms | Servidor GDB: `print contador`, `set var contador = 0xAA`, breakpoints |
| `04_contador_serie` | El mismo contador, pero manda cada valor por **USART1** (PA9/PA10, 115200 8N1) y acepta comandos | **Monitor serie** de la web (recibir y enviar) y la pestaña Depurar |

## Puerto serie (04_contador_serie)

Hace falta un **adaptador USB-serie de 3,3 V** (CH340, CP2102, FT232...) en un USB de la BeagleBoard.
El ST-LINK/V2 original no tiene puerto serie virtual (los de Nucleo/V3 sí, pero van a otros pines).

| STM32H503 | Adaptador USB-serie |
|---|---|
| PA9 (USART1_TX) | RX |
| PA10 (USART1_RX) | TX |
| GND | GND |

En la web: pestaña **Monitor serie**, puerto `/dev/ttyUSB0` (CH340/CP2102/FT232) a **115200**, *Abrir*.
Cada cuenta llega como:

```text
contador = 171   LEDs = 10101011   (0xAB)
```

Comandos (escribilos en el campo de texto y tocá *Enviar*): `p` pausa/sigue, `r` vuelve a 0,
`+` más rápido, `-` más lento (de 50 ms a 4 s), `s` estado, `?` ayuda.

Con la pestaña **Depurar** se puede poner un breakpoint en `contador_paso()` y ver que la línea del
valor llega al monitor serie recién al pasar por `enviar_valor()`.

Todos: salida push-pull en PA0..PA7 sin tocar PA13/PA14 (SWD), base de tiempo de 1 ms con SysTick.
El reloj es el HSI de reset; el código lee el divisor `HSIDIV` para calcular los tiempos.

## Polaridad de los LEDs

Por defecto un LED **enciende con el pin en alto** (pin → resistencia → LED → GND). Si en tu placa
encienden con el pin en bajo (el blink se ve invertido: los LEDs "encendidos" quedan apagados en la
prueba inicial), recompilá con:

```bash
make clean && make DEFS=-DLED_ACTIVO_ALTO=0
```

## Probar con el banco

En la web: chip **STM32H503CBT6**, método **ST-Link (SWD)**, interfaz **stlink-dap** (el H5 no
funciona con HLA). Subí el `.elf` y usá Flashear.

- **Verificar**: con el mismo archivo tiene que dar "Verificación OK"; con otro programa, error.
- **Halt / Run / Reset**: con `02_auto_fantastico`.
- **GDB** (Iniciar servidor GDB en la web, después en tu PC):

  ```text
  arm-none-eabi-gdb build/03_contador_binario.elf
  (gdb) target extended-remote 100.99.235.81:3333
  (gdb) monitor halt
  (gdb) print contador
  (gdb) set var contador = 0xAA
  (gdb) break contador_paso
  (gdb) continue
  ```

- **USB DFU**: método USB DFU con el `.bin` (dirección 0x08000000), o el `.elf` / `.hex`, que la app
  convierte sola. Requiere el USB del propio STM32 conectado a la BeagleBoard.

## Compilar

Con el compilador que trae STM32CubeIDE (Git Bash en Windows):

```bash
T=/c/ST/STM32CubeIDE_1.19.0/STM32CubeIDE/plugins
export PATH="$T/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.13.3.rel1.win32_1.0.0.202411081344/tools/bin:$T/com.st.stm32cube.ide.mcu.externaltools.make.win32_2.2.0.202409170845/tools/bin:$PATH"
make
```

(En otra versión de CubeIDE cambian los números de las carpetas de `plugins`.)

## Archivos

- `src/placa.h`, `src/placa.c`: registros (direcciones del encabezado oficial de ST `stm32h503xx.h`),
  inicialización de PA0..PA7, `leds_mostrar()`, `esperar_ms()` con SysTick.
- `src/serie.h`, `src/serie.c` (sólo 04): USART1 por encuesta, sin interrupciones; el divisor de
  baudios se calcula con el reloj real (HSI, HSIDIV, HPRE y PPRE2 leídos de los registros).
- `src/startup_stm32h503.c`: tabla de vectores (16 del núcleo + 134 IRQ) y arranque en C.
- `stm32h503cb.ld`: flash 128 KB en 0x08000000, RAM: SRAM1 16 KB en 0x20000000.
