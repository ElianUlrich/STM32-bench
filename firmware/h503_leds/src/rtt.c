/*
 * rtt.c — Implementación mínima de RTT con el formato de bloque de control de
 * SEGGER (lo que busca OpenOCD con "rtt setup ... SEGGER RTT").
 */
#include "rtt.h"

#define RTT_TAM_SUBIDA  1024U
#define RTT_TAM_BAJADA  32U

typedef struct {
    const char *nombre;
    char *buffer;
    uint32_t tamanio;
    volatile uint32_t escritura;    /* WrOff: lo mueve quien escribe */
    volatile uint32_t lectura;      /* RdOff: lo mueve quien lee */
    uint32_t flags;                 /* 0 = si no entra, se descarta (no bloquea) */
} rtt_buffer_t;

typedef struct {
    char id[16];                    /* "SEGGER RTT": lo que busca el depurador */
    int32_t max_subida;
    int32_t max_bajada;
    rtt_buffer_t subida[1];
    rtt_buffer_t bajada[1];
} rtt_control_t;

/* El nombre lo exige el formato: el banco lo busca en el .elf para saber dónde está. */
rtt_control_t _SEGGER_RTT;

static char buf_subida[RTT_TAM_SUBIDA];
static char buf_bajada[RTT_TAM_BAJADA];

void rtt_init(void)
{
    static const char id[] = "SEGGER RTT";
    rtt_control_t *cb = &_SEGGER_RTT;

    cb->max_subida = 1;
    cb->max_bajada = 1;
    cb->subida[0] = (rtt_buffer_t){"Terminal", buf_subida, RTT_TAM_SUBIDA, 0, 0, 0};
    cb->bajada[0] = (rtt_buffer_t){"Terminal", buf_bajada, RTT_TAM_BAJADA, 0, 0, 0};
    /* El texto de identificación va al final: el depurador nunca ve un bloque a medio armar. */
    __asm volatile ("dmb" ::: "memory");
    for (unsigned i = 0; i < sizeof(id); i++) {
        cb->id[i] = id[i];
    }
    __asm volatile ("dmb" ::: "memory");
}

void rtt_caracter(char c)
{
    rtt_buffer_t *b = &_SEGGER_RTT.subida[0];
    uint32_t w = b->escritura;
    uint32_t siguiente = (w + 1U == b->tamanio) ? 0U : w + 1U;
    if (siguiente == b->lectura) {
        return;                         /* lleno: nadie está leyendo; se descarta */
    }
    b->buffer[w] = c;
    __asm volatile ("dmb" ::: "memory");
    b->escritura = siguiente;
}

void rtt_escribir(const char *texto)
{
    while (*texto) {
        rtt_caracter(*texto++);
    }
}

void rtt_numero(uint32_t n)
{
    char tmp[10];
    int i = 0;
    do {
        tmp[i++] = (char)('0' + n % 10U);
        n /= 10U;
    } while (n != 0U);
    while (i > 0) {
        rtt_caracter(tmp[--i]);
    }
}

void rtt_hex8(uint8_t n)
{
    static const char dig[] = "0123456789ABCDEF";
    rtt_caracter(dig[n >> 4]);
    rtt_caracter(dig[n & 0xFU]);
}

int rtt_leer(void)
{
    rtt_buffer_t *b = &_SEGGER_RTT.bajada[0];
    uint32_t r = b->lectura;
    if (r == b->escritura) {
        return -1;
    }
    char c = b->buffer[r];
    __asm volatile ("dmb" ::: "memory");
    b->lectura = (r + 1U == b->tamanio) ? 0U : r + 1U;
    return (unsigned char)c;
}
