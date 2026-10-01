/*
 * serie.h — Puerto serie mínimo con USART1 (sin HAL): TX = PA9, RX = PA10, 8N1.
 *
 * Direcciones del encabezado oficial de ST (stm32h503xx.h) y pines de la base
 * de datos de CubeMX (PA9 = USART1_TX, PA10 = USART1_RX, función alternativa 7):
 *   USART1      = 0x40013800  (BRR +0x0C, ISR +0x1C, ICR +0x20, RDR +0x24, TDR +0x28)
 *   RCC_CFGR2   = RCC + 0x20  (prescalers HPRE y PPRE2)
 *   RCC_APB2ENR = RCC + 0xA4  (bit 14 = USART1EN)
 *   GPIOA_AFRH  = GPIOA + 0x24
 * El reloj de USART1 es PCLK2 (valor de reset de RCC_CCIPR1.USART1SEL).
 */
#ifndef SERIE_H
#define SERIE_H

#include <stdint.h>

void serie_init(uint32_t baudios);
void serie_caracter(char c);
void serie_escribir(const char *texto);
void serie_numero(uint32_t n);          /* en decimal */
void serie_hex8(uint8_t n);             /* dos dígitos hexadecimales */
int  serie_leer(void);                  /* el byte recibido, o -1 si no llegó nada */

#endif /* SERIE_H */
