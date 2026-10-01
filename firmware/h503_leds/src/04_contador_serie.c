/*
 * 04_contador_serie — El contador binario de 03 (PA0 = bit 0 ... PA7 = bit 7) que
 * además manda cada valor por el puerto serie y acepta comandos.
 *
 * Puerto serie: USART1, TX = PA9, RX = PA10, 115200 baudios, 8N1.
 * Con un adaptador USB-serie de 3,3 V en un USB de la BeagleBoard:
 *     PA9 (TX)  -> RX del adaptador
 *     PA10 (RX) <- TX del adaptador
 *     GND       -- GND
 * y en la web: Monitor serie, /dev/ttyUSB0, 115200.
 *
 * Comandos (una letra; en el monitor serie, "Enviar"):
 *     p  pausa / sigue       r  vuelve a 0       +  más rápido       -  más lento
 *     ?  muestra la ayuda    s  muestra el estado
 */
#include "placa.h"
#include "serie.h"

volatile uint32_t contador;
volatile uint32_t periodo_ms = 250;
volatile uint32_t pausado;

static void enviar_valor(void)
{
    uint8_t leds = (uint8_t)(contador & 0xFFU);
    serie_escribir("contador = ");
    serie_numero(contador);
    serie_escribir("   LEDs = ");
    for (int bit = 7; bit >= 0; bit--) {
        serie_caracter((leds >> bit) & 1U ? '1' : '0');
    }
    serie_escribir("   (0x");
    serie_hex8(leds);
    serie_escribir(")\r\n");
}

/* Función aparte (sin inline) para poder poner un breakpoint, como en 03. */
__attribute__((noinline)) void contador_paso(void)
{
    contador++;
    leds_mostrar((uint8_t)(contador & 0xFFU));
    enviar_valor();
}

static void mostrar_estado(void)
{
    serie_escribir(pausado ? "[pausado] " : "[contando] ");
    serie_escribir("periodo = ");
    serie_numero(periodo_ms);
    serie_escribir(" ms   ");
    enviar_valor();
}

static void mostrar_ayuda(void)
{
    serie_escribir("Comandos: p = pausa/sigue, r = volver a 0, + = mas rapido, - = mas lento, "
                   "s = estado, ? = ayuda\r\n");
}

static void atender(int c)
{
    switch (c) {
    case 'p': case 'P':
        pausado = !pausado;
        serie_escribir(pausado ? "> Pausado\r\n" : "> Sigue\r\n");
        break;
    case 'r': case 'R':
        contador = 0;
        leds_mostrar(0);
        serie_escribir("> Vuelve a 0\r\n");
        break;
    case '+':
        if (periodo_ms > 50U) {
            periodo_ms /= 2U;
        }
        serie_escribir("> Periodo: ");
        serie_numero(periodo_ms);
        serie_escribir(" ms\r\n");
        break;
    case '-':
        if (periodo_ms < 4000U) {
            periodo_ms *= 2U;
        }
        serie_escribir("> Periodo: ");
        serie_numero(periodo_ms);
        serie_escribir(" ms\r\n");
        break;
    case 's': case 'S':
        mostrar_estado();
        break;
    case '?': case 'h': case 'H':
        mostrar_ayuda();
        break;
    default:                                   /* fin de línea u otra cosa: se ignora */
        break;
    }
}

int main(void)
{
    placa_init();
    serie_init(115200U);
    leds_mostrar(0x00);

    serie_escribir("\r\n=== 04_contador_serie - STM32H503 ===\r\n");
    mostrar_ayuda();

    uint32_t ultimo = ms_transcurridos;
    for (;;) {
        int c = serie_leer();
        if (c >= 0) {
            atender(c);
        }
        if (!pausado && (uint32_t)(ms_transcurridos - ultimo) >= periodo_ms) {
            ultimo = ms_transcurridos;
            contador_paso();
        }
    }
}
