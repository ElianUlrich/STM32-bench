/*
 * 02_auto_fantastico — Una luz que recorre PA0 -> PA7 y vuelve.
 *
 * Sirve para probar Halt / Run desde la web: al hacer Halt la luz se congela
 * en su posición, con Run sigue desde ahí.
 *
 * "paso_ms" es una variable inicializada (va a la sección .data): su valor
 * inicial se guarda en flash y el arranque lo copia a RAM. Así este programa
 * también prueba que la conversión .elf -> .bin para DFU use la dirección
 * de carga (LMA) correcta. Se puede cambiar en vivo desde GDB:
 *     (gdb) set var paso_ms = 20
 */
#include "placa.h"

volatile uint32_t paso_ms = 80;

int main(void)
{
    placa_init();

    int posicion = 0;
    int direccion = 1;
    for (;;) {
        leds_mostrar((uint8_t)(1U << posicion));
        esperar_ms(paso_ms);
        if (posicion == 7) {
            direccion = -1;
        } else if (posicion == 0) {
            direccion = 1;
        }
        posicion += direccion;
    }
}
