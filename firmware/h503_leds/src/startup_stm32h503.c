/*
 * startup_stm32h503.c — Tabla de vectores y arranque en C (sin ensamblador).
 *
 * El STM32H503 tiene 134 interrupciones de periféricos (0..133, según
 * stm32h503xx.h) más las 16 entradas del núcleo Cortex-M33.
 */
#include <stdint.h>

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss, _estack;
extern int main(void);

void Reset_Handler(void);
void Default_Handler(void);

/* Manejadores débiles: si el programa no los define, van a Default_Handler. */
#define DEBIL __attribute__((weak, alias("Default_Handler")))
void NMI_Handler(void) DEBIL;
void HardFault_Handler(void) DEBIL;
void MemManage_Handler(void) DEBIL;
void BusFault_Handler(void) DEBIL;
void UsageFault_Handler(void) DEBIL;
void SecureFault_Handler(void) DEBIL;
void SVC_Handler(void) DEBIL;
void DebugMon_Handler(void) DEBIL;
void PendSV_Handler(void) DEBIL;
void SysTick_Handler(void) DEBIL;

#define N_IRQ 134

__attribute__((section(".isr_vector"), used))
void (*const tabla_vectores[16 + N_IRQ])(void) = {
    (void (*)(void))(&_estack),   /* puntero de pila inicial */
    Reset_Handler,
    NMI_Handler,
    HardFault_Handler,
    MemManage_Handler,
    BusFault_Handler,
    UsageFault_Handler,
    SecureFault_Handler,
    0, 0, 0,                      /* reservados */
    SVC_Handler,
    DebugMon_Handler,
    0,                            /* reservado */
    PendSV_Handler,
    SysTick_Handler,
    [16 ... (16 + N_IRQ - 1)] = Default_Handler,
};

void Reset_Handler(void)
{
    /* Copia .data de la flash (LMA) a la RAM (VMA) y pone .bss en cero. */
    uint32_t *origen = &_sidata;
    for (uint32_t *p = &_sdata; p < &_edata; p++) {
        *p = *origen++;
    }
    for (uint32_t *p = &_sbss; p < &_ebss; p++) {
        *p = 0;
    }
    main();
    for (;;) {
    }
}

void Default_Handler(void)
{
    /* Interrupción sin manejador o falla: queda acá (visible con GDB). */
    for (;;) {
    }
}
