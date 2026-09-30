/*
 * stm32h503cb.ld — Script de enlace para STM32H503CBT6.
 * Flash 128 KB en 0x08000000. SRAM1 (16 KB) en 0x20000000: se usa sólo SRAM1
 * para no depender de que SRAM2 (0x20004000) esté habilitada tras el reset.
 */
ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 128K
  RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 16K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
  .isr_vector :
  {
    KEEP(*(.isr_vector))
  } > FLASH

  .text :
  {
    *(.text*)
    *(.rodata*)
    . = ALIGN(4);
  } > FLASH

  /* .data se ejecuta en RAM pero sus valores iniciales se guardan en flash:
     VMA en RAM, LMA en FLASH (el conversor a .bin de la app usa la LMA). */
  _sidata = LOADADDR(.data);
  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } > RAM AT > FLASH

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > RAM
}
