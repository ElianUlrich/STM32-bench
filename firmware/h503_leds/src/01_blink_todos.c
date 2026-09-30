/*
 * 01_blink_todos — Prueba básica: los 8 LEDs (PA0..PA7) parpadean juntos.
 *
 * Al arrancar hace una "prueba de lámparas": enciende los LEDs de a uno
 * (PA0 -> PA7) para confirmar el orden y que no haya ninguno quemado.
 * Después: 500 ms encendidos, 500 ms apagados (1 Hz).
 */
#include "placa.h"

int main(void)
{
    placa_init();

    for (int i = 0; i < 8; i++) {
        leds_mostrar((uint8_t)(1U << i));
        esperar_ms(150);
    }

    for (;;) {
        leds_mostrar(0xFF);
        esperar_ms(500);
        leds_mostrar(0x00);
        esperar_ms(500);
    }
}
