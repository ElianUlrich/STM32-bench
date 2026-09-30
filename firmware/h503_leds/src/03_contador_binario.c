/*
 * 03_contador_binario — Cuenta de 0 a 255 en binario sobre PA0 (bit 0) .. PA7 (bit 7).
 *
 * Pensado para probar el servidor GDB del banco:
 *     (gdb) target extended-remote 100.99.235.81:3333
 *     (gdb) monitor halt
 *     (gdb) print contador            -> el valor coincide con los LEDs
 *     (gdb) set var contador = 0xAA   -> LEDs alternados 10101010
 *     (gdb) break contador_paso       -> se detiene en cada incremento
 *     (gdb) continue
 */
#include "placa.h"

volatile uint32_t contador;

/* Función aparte (sin inline) para poder poner un breakpoint. */
__attribute__((noinline)) void contador_paso(void)
{
    contador++;
    leds_mostrar((uint8_t)(contador & 0xFFU));
}

int main(void)
{
    placa_init();
    leds_mostrar(0x00);

    for (;;) {
        esperar_ms(250);
        contador_paso();
    }
}
