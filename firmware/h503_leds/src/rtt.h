/*
 * rtt.h — "printf" por el ST-Link (SWD), sin puerto serie: RTT.
 *
 * El programa escribe en un buffer en RAM; el depurador (OpenOCD) lo lee por
 * SWD mientras el micro sigue corriendo. Usa el mismo formato que SEGGER RTT
 * (bloque de control "_SEGGER_RTT" con el texto "SEGGER RTT"), así que sirve
 * con OpenOCD, con la web del banco o con cualquier visor de RTT.
 *
 * Una sola vía de subida (micro -> PC, canal 0) y una de bajada (PC -> micro).
 * Si nadie lee y el buffer se llena, el texto que no entra se descarta: el
 * programa nunca se queda esperando.
 */
#ifndef RTT_H
#define RTT_H

#include <stdint.h>

void rtt_init(void);
void rtt_caracter(char c);
void rtt_escribir(const char *texto);
void rtt_numero(uint32_t n);            /* en decimal */
void rtt_hex8(uint8_t n);               /* dos dígitos hexadecimales */
int  rtt_leer(void);                    /* el byte recibido, o -1 si no llegó nada */

#endif /* RTT_H */
