/*
 * placa.c — Inicialización de PA0..PA7 y base de tiempo con SysTick.
 */
#include "placa.h"

volatile uint32_t ms_transcurridos;

uint32_t reloj_hz(void)
{
    /*
     * Tras el reset el núcleo corre con el HSI (64 MHz) dividido por HSIDIV
     * (RCC_CR bits 4:3: 00 = /1, 01 = /2, 10 = /4, 11 = /8). Se lee en lugar de
     * suponerlo, así los tiempos son correctos sea cual sea el valor de reset.
     */
    uint32_t hsidiv = (RCC_CR >> 3) & 0x3U;
    return 64000000UL >> hsidiv;
}

void placa_init(void)
{
    /* 1) Reloj del puerto A. La lectura posterior asegura que ya está activo. */
    RCC_AHB2ENR |= RCC_AHB2ENR_GPIOAEN;
    (void)RCC_AHB2ENR;

    /* 2) Estado inicial: todos apagados, antes de pasar los pines a salida. */
    leds_mostrar(0x00);

    /* 3) PA0..PA7: salida push-pull, velocidad baja, sin pull-up/down.
     *    Sólo se tocan los bits de esos 8 pines: PA13/PA14 son SWD. */
    uint32_t moder = GPIOA_MODER;
    moder &= ~0x0000FFFFUL;          /* 2 bits por pin */
    moder |=  0x00005555UL;          /* 01 = salida */
    GPIOA_MODER = moder;
    GPIOA_OTYPER  &= ~0x000000FFUL;  /* push-pull */
    GPIOA_OSPEEDR &= ~0x0000FFFFUL;  /* baja velocidad (menos ruido) */
    GPIOA_PUPDR   &= ~0x0000FFFFUL;  /* sin resistencias internas */

    /* 4) SysTick: una interrupción por milisegundo con el reloj del núcleo. */
    SYST_RVR = reloj_hz() / 1000U - 1U;
    SYST_CVR = 0;
    SYST_CSR = 0x7;                  /* CLKSOURCE = núcleo, TICKINT, ENABLE */
}

void leds_mostrar(uint8_t patron)
{
#if !LED_ACTIVO_ALTO
    patron = (uint8_t)~patron;
#endif
    /* BSRR: 16 bits bajos encienden (set), 16 altos apagan (reset); atómico. */
    GPIOA_BSRR = (uint32_t)patron | ((uint32_t)(uint8_t)~patron << 16);
}

void esperar_ms(uint32_t ms)
{
    uint32_t inicio = ms_transcurridos;
    while ((uint32_t)(ms_transcurridos - inicio) < ms) {
        /* espera activa (sin WFI, para no complicar la depuración) */
    }
}

void SysTick_Handler(void)
{
    ms_transcurridos++;
}
