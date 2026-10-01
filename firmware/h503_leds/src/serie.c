/*
 * serie.c — USART1 por encuesta (sin interrupciones): TX = PA9, RX = PA10.
 */
#include "placa.h"
#include "serie.h"

#define RCC_CFGR2       REG32(RCC_BASE + 0x20)
#define RCC_APB2ENR     REG32(RCC_BASE + 0xA4)
#define RCC_APB2ENR_USART1EN (1UL << 14)

#define GPIOA_AFRH      REG32(GPIOA_BASE + 0x24)

#define USART1_BASE     0x40013800UL
#define USART1_CR1      REG32(USART1_BASE + 0x00)
#define USART1_BRR      REG32(USART1_BASE + 0x0C)
#define USART1_ISR      REG32(USART1_BASE + 0x1C)
#define USART1_ICR      REG32(USART1_BASE + 0x20)
#define USART1_RDR      REG32(USART1_BASE + 0x24)
#define USART1_TDR      REG32(USART1_BASE + 0x28)

#define CR1_UE          (1UL << 0)
#define CR1_RE          (1UL << 2)
#define CR1_TE          (1UL << 3)
#define ISR_FE          (1UL << 1)
#define ISR_NE          (1UL << 2)
#define ISR_ORE         (1UL << 3)
#define ISR_RXNE        (1UL << 5)
#define ISR_TXE         (1UL << 7)

/* Frecuencia de PCLK2 = núcleo / HPRE / PPRE2 (se leen, como el HSIDIV). */
static uint32_t pclk2_hz(void)
{
    static const uint16_t div_ahb[8] = {2, 4, 8, 16, 64, 128, 256, 512};
    uint32_t cfgr2 = RCC_CFGR2;
    uint32_t hz = reloj_hz();
    uint32_t hpre = cfgr2 & 0xFU;              /* 0xxx = /1, 1000 = /2 ... 1111 = /512 */
    uint32_t ppre2 = (cfgr2 >> 8) & 0x7U;      /* 0xx = /1, 100 = /2 ... 111 = /16 */
    if (hpre & 0x8U) {
        hz /= div_ahb[hpre & 0x7U];
    }
    if (ppre2 & 0x4U) {
        hz >>= (ppre2 & 0x3U) + 1U;
    }
    return hz;
}

void serie_init(uint32_t baudios)
{
    RCC_AHB2ENR |= RCC_AHB2ENR_GPIOAEN;
    RCC_APB2ENR |= RCC_APB2ENR_USART1EN;
    (void)RCC_APB2ENR;                         /* espera a que el reloj esté activo */

    /* PA9 y PA10: primero la función alternativa 7 y después el modo, así no hay glitch.
     * Sólo se tocan los bits de esos dos pines (PA0..PA7 son LEDs, PA13/PA14 son SWD). */
    GPIOA_AFRH = (GPIOA_AFRH & ~((0xFUL << 4) | (0xFUL << 8))) | (7UL << 4) | (7UL << 8);
    GPIOA_PUPDR = (GPIOA_PUPDR & ~(3UL << 20)) | (1UL << 20);       /* pull-up en RX */
    GPIOA_MODER = (GPIOA_MODER & ~((3UL << 18) | (3UL << 20))) | (2UL << 18) | (2UL << 20);

    USART1_CR1 = 0;                            /* 8 bits, sin paridad, sobremuestreo x16 */
    USART1_BRR = (pclk2_hz() + baudios / 2U) / baudios;
    USART1_CR1 = CR1_TE | CR1_RE | CR1_UE;
}

void serie_caracter(char c)
{
    while ((USART1_ISR & ISR_TXE) == 0) {
    }
    USART1_TDR = (uint8_t)c;
}

void serie_escribir(const char *texto)
{
    while (*texto) {
        serie_caracter(*texto++);
    }
}

void serie_numero(uint32_t n)
{
    char buf[10];
    int i = 0;
    do {
        buf[i++] = (char)('0' + n % 10U);
        n /= 10U;
    } while (n != 0U);
    while (i > 0) {
        serie_caracter(buf[--i]);
    }
}

void serie_hex8(uint8_t n)
{
    static const char dig[] = "0123456789ABCDEF";
    serie_caracter(dig[n >> 4]);
    serie_caracter(dig[n & 0xFU]);
}

int serie_leer(void)
{
    uint32_t isr = USART1_ISR;
    if (isr & (ISR_ORE | ISR_FE | ISR_NE)) {
        USART1_ICR = ISR_ORE | ISR_FE | ISR_NE;  /* mismas posiciones en ICR: limpia el error */
    }
    if (isr & ISR_RXNE) {
        return (int)(USART1_RDR & 0xFFU);
    }
    return -1;
}
