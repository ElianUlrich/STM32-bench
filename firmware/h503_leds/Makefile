# Firmware de prueba para STM32H503CBT6 con LEDs en PA0..PA7.
#
#   make                          compila los 4 programas en build/ (.elf .hex .bin .map)
#   make DEFS=-DLED_ACTIVO_ALTO=0 si los LEDs encienden con el pin en bajo
#   make clean
#
# Necesita arm-none-eabi-gcc en el PATH (por ejemplo el de STM32CubeIDE, ver README.md)
# o indicar el prefijo:  make PREFIX=/ruta/a/arm-none-eabi-

PREFIX  ?= arm-none-eabi-
CC      := $(PREFIX)gcc
OBJCOPY := $(PREFIX)objcopy
SIZE    := $(PREFIX)size

BUILD     := build
PROGRAMAS := 01_blink_todos 02_auto_fantastico 03_contador_binario 04_contador_serie
COMUNES   := src/startup_stm32h503.c src/placa.c
# Fuentes que usa sólo un programa.
EXTRA_04_contador_serie := src/serie.c

CPU     := -mcpu=cortex-m33 -mthumb -mfloat-abi=soft
CFLAGS  := $(CPU) -std=gnu11 -Og -g3 -Wall -Wextra -ffreestanding \
           -ffunction-sections -fdata-sections -fno-tree-loop-distribute-patterns \
           -Isrc $(DEFS)
LDFLAGS := $(CPU) -T stm32h503cb.ld -nostdlib -Wl,--gc-sections -Wl,--print-memory-usage

SALIDAS := $(foreach p,$(PROGRAMAS),$(BUILD)/$(p).elf $(BUILD)/$(p).hex $(BUILD)/$(p).bin)

all: $(SALIDAS)

$(BUILD)/%.elf: src/%.c $(COMUNES) src/placa.h src/serie.h src/serie.c stm32h503cb.ld | $(BUILD)
	$(CC) $(CFLAGS) $(LDFLAGS) -Wl,-Map=$(BUILD)/$*.map $< $(COMUNES) $(EXTRA_$*) -lgcc -o $@
	$(SIZE) $@

$(BUILD)/%.hex: $(BUILD)/%.elf
	$(OBJCOPY) -O ihex $< $@

$(BUILD)/%.bin: $(BUILD)/%.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)

.PHONY: all clean
.SECONDARY:
