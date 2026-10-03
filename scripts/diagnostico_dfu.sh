#!/usr/bin/env bash
# =============================================================================
# Diagnóstico: ¿por qué la placa no ve el STM32 en modo DFU?
#
#   sudo bash scripts/diagnostico_dfu.sh
#
# Toma una "foto" del USB, te pide que pongas el STM32 en modo DFU (o que lo
# enchufes), toma otra y compara. Todo queda en /tmp/diagnostico_dfu.txt para
# mandarlo. No cambia nada del sistema.
#
# Cómo leer el resultado:
#   * Aparece "new full-speed USB device" en el kernel -> Linux lo ve: el problema
#     sería de permisos/udev o de dfu-util.
#   * No aparece nada, pero uhubctl muestra "connect" en un puerto nuevo -> el hub
#     siente el cable pero la enumeración falla (energía, ruido, kernel).
#   * No aparece nada y uhubctl tampoco cambia -> el hub no siente nada: cable,
#     puerto, alimentación del puerto o el STM32 no activó el USB.
# =============================================================================
set -u

OUT=/tmp/diagnostico_dfu.txt
[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo con sudo."; exit 1; }

foto() {
    echo "----- lsusb"
    lsusb 2>&1
    echo "----- lsusb -t"
    lsusb -t 2>&1
    echo "----- uhubctl (estado de cada puerto del hub)"
    if command -v uhubctl >/dev/null; then uhubctl 2>&1; else echo "(uhubctl no instalado)"; fi
    echo "----- puertos (sysfs)"
    for p in /sys/bus/usb/devices/*/*-port*; do
        [ -e "$p" ] || continue
        printf '%s connect_type=%s\n' "$(basename "$p")" "$(cat "$p/connect_type" 2>/dev/null)"
    done
}

{
    echo "===== Diagnóstico DFU $(date -Iseconds)"
    echo "----- sistema"
    uname -a
    grep PRETTY_NAME /etc/os-release
    cat /proc/device-tree/model 2>/dev/null; echo
    echo "----- dfu-util"
    (dfu-util --version 2>&1 | head -1)
    echo "----- reglas udev de DFU"
    grep -h "df11" /etc/udev/rules.d/*.rules 2>/dev/null || echo "(ninguna)"
    echo "----- energía y errores USB del kernel (últimos)"
    dmesg | grep -iE "over-current|overcurrent|usb|ehci|ohci|hub" | tail -40
    echo
    echo "===== ANTES"
    foto
} > "$OUT" 2>&1

dmesg -c >/dev/null 2>&1 || true        # limpia el buffer para ver sólo lo nuevo
echo
echo "1) Conectá el USB del STM32 (el USB-C de la WeAct, no el ST-Link) a la BeagleBoard."
echo "2) Ponelo en modo DFU: mantené BOOT, tocá y soltá RESET, soltá BOOT."
read -r -p "3) Esperá 5 segundos y apretá Enter... " _

{
    echo
    echo "===== DESPUÉS"
    echo "----- kernel (todo lo nuevo)"
    dmesg 2>&1 || true
    foto
    echo "----- dfu-util -l"
    dfu-util -l 2>&1 | tail -15
} >> "$OUT" 2>&1

echo
if dmesg | grep -q "new full-speed USB device"; then
    echo ">> Linux SÍ vio un dispositivo nuevo (ver $OUT)."
elif dmesg | grep -qiE "error -71|error -110|unable to enumerate|device descriptor read"; then
    echo ">> El hub vio algo pero falló la enumeración (ver $OUT)."
else
    echo ">> Linux NO vio nada nuevo en el USB (ver $OUT)."
fi
echo "Resultado completo en $OUT. Para traerlo a la PC (en PowerShell):"
echo "   scp ubuntu@100.99.235.81:$OUT ."
