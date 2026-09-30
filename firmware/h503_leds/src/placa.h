/*
 * placa.h — Acceso mínimo al hardware del STM32H503CBT6 (sin HAL ni CMSIS).
 *
 * LEDs en PA0..PA7. Las direcciones salen del encabezado oficial de ST
 * (cmsis-device-h5, stm32h503xx.h), alias no seguros:
 *   RCC   = 0x44020C00   (RCC_CR en +0x00, RCC_AHB2ENR en +0x8C, bit 0 = GPIOA)
 *   GPIOA = 0x42020000
 */
#ifndef PLACA_H
#define PLACA_H

#include <stdint.h>

#define REG32(dir)      (*(volatile uint32_t *)(dir))

#define RCC_BASE        0x44020C00UL
#define RCC_CR          REG32(RCC_BASE + 0x00)
#define RCC_AHB2ENR     REG32(RCC_BASE + 0x8C)
#define RCC_AHB2ENR_GPIOAEN (1UL << 0)

#define GPIOA_BASE      0x42020000UL
#define GPIOA_MODER     REG32(GPIOA_BASE + 0x00)
#define GPIOA_OTYPER    REG32(GPIOA_BASE + 0x04)
#define GPIOA_OSPEEDR   REG32(GPIOA_BASE + 0x08)
#define GPIOA_PUPDR     REG32(GPIOA_BASE + 0x0C)
#define GPIOA_ODR       REG32(GPIOA_BASE + 0x14)
#define GPIOA_BSRR      REG32(GPIOA_BASE + 0x18)

/* SysTick (núcleo Cortex-M33) */
#define SYST_CSR        REG32(0xE000E010UL)
#define SYST_RVR        REG32(0xE000E014UL)
#define SYST_CVR        REG32(0xE000E018UL)

/*
 * Polaridad de los LEDs:
 *   1 = el LED enciende con el pin en ALTO (pin -> resistencia -> LED -> GND)
 *   0 = el LED enciende con el pin en BAJO (3,3 V -> resistencia -> LED -> pin)
 * Se puede cambiar al compilar:  make DEFS=-DLED_ACTIVO_ALTO=0
 */
#ifndef LED_ACTIVO_ALTO
#define LED_ACTIVO_ALTO 1
#endif

/* Milisegundos desde el arranque (lo incrementa la interrupción de SysTick). */
extern volatile uint32_t ms_transcurridos;

void     placa_init(void);              /* reloj de GPIOA, PA0..PA7 como salida, SysTick 1 ms */
void     leds_mostrar(uint8_t patron);  /* bit 0 -> PA0 ... bit 7 -> PA7 (1 = encendido) */
void     esperar_ms(uint32_t ms);
uint32_t reloj_hz(void);                /* frecuencia del núcleo (HSI tras el reset) */

#endif /* PLACA_H */
