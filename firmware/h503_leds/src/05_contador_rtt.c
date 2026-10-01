/*
 * 05_contador_rtt — El contador binario (PA0 = bit 0 ... PA7 = bit 7) que manda cada
 * valor por RTT: por el mismo ST-Link (SWD), sin adaptador USB-serie ni cables extra.
 *
 * En la web: pestaña Monitor serie, puerto "RTT — por el ST-Link", Abrir.
 * Sirve también mientras se depura en la pestaña Depurar.
 *
 * Comandos (una letra; en el monitor serie, "Enviar"):
 *     p  pausa / sigue       r  vuelve a 0       +  más rápido       -  más lento
 *     ?  muestra la ayuda    s  muestra el estado
 */
#include "placa.h"
#include "rtt.h"

volatile uint32_t contador;
volatile uint32_t periodo_ms = 250;
volatile uint32_t pausado;

static void enviar_valor(void)
{
    uint8_t leds = (uint8_t)(contador & 0xFFU);
    rtt_escribir("contador = ");
    rtt_numero(contador);
    rtt_escribir("   LEDs = ");
    for (int bit = 7; bit >= 0; bit--) {
        rtt_caracter((leds >> bit) & 1U ? '1' : '0');
    }
    rtt_escribir("   (0x");
    rtt_hex8(leds);
    rtt_escribir(")\r\n");
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
    rtt_escribir(pausado ? "[pausado] " : "[contando] ");
    rtt_escribir("periodo = ");
    rtt_numero(periodo_ms);
    rtt_escribir(" ms   ");
    enviar_valor();
}

static void mostrar_ayuda(void)
{
    rtt_escribir("Comandos: p = pausa/sigue, r = volver a 0, + = mas rapido, - = mas lento, "
                   "s = estado, ? = ayuda\r\n");
}

static void atender(int c)
{
    switch (c) {
    case 'p': case 'P':
        pausado = !pausado;
        rtt_escribir(pausado ? "> Pausado\r\n" : "> Sigue\r\n");
        break;
    case 'r': case 'R':
        contador = 0;
        leds_mostrar(0);
        rtt_escribir("> Vuelve a 0\r\n");
        break;
    case '+':
        if (periodo_ms > 50U) {
            periodo_ms /= 2U;
        }
        rtt_escribir("> Periodo: ");
        rtt_numero(periodo_ms);
        rtt_escribir(" ms\r\n");
        break;
    case '-':
        if (periodo_ms < 4000U) {
            periodo_ms *= 2U;
        }
        rtt_escribir("> Periodo: ");
        rtt_numero(periodo_ms);
        rtt_escribir(" ms\r\n");
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
    rtt_init();
    leds_mostrar(0x00);

    rtt_escribir("\r\n=== 05_contador_rtt - STM32H503 (RTT) ===\r\n");
    mostrar_ayuda();

    uint32_t ultimo = ms_transcurridos;
    for (;;) {
        int c = rtt_leer();
        if (c >= 0) {
            atender(c);
        }
        if (!pausado && (uint32_t)(ms_transcurridos - ultimo) >= periodo_ms) {
            ultimo = ms_transcurridos;
            contador_paso();
        }
    }
}
