#!/usr/bin/env bash
# =============================================================================
# Instalación automática del banco en la placa. Normalmente lo lanza
# scripts/desplegar.ps1 desde la PC (un solo comando); también se puede correr
# a mano:
#
#   sudo bash scripts/instalar_todo.sh app        sólo la aplicación (unos minutos):
#                                                 primera instalación o actualización
#   sudo bash scripts/instalar_todo.sh completo   todo desde una placa recién instalada (horas):
#                                                 sistema, Tailscale, Python 3.9, OpenOCD, app y GDB
#   ... --config   además reemplaza chips.yaml y bench.yaml por la versión nueva
#                  (la anterior queda como .bak)
#
# Cada paso es idempotente: lo que ya está instalado no se vuelve a compilar,
# así que se puede cortar y volver a correr.
# =============================================================================
set -euo pipefail

MODO="${1:-app}"
CONFIG=0
[ "${2:-}" = "--config" ] && CONFIG=1
DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="/etc/stm32-bench"
inicio=$(date +%s)

log()  { echo -e "\033[1;32m[instalar]\033[0m $*"; }
warn() { echo -e "\033[1;33m[instalar]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[instalar]\033[0m $*" >&2; exit 1; }
paso() { echo; echo -e "\033[1;36m==================== $* ====================\033[0m"; }

[ "$(id -u)" -eq 0 ] || die "Ejecutalo con sudo."
case "$MODO" in app|completo) ;; *) die "Modo desconocido: $MODO (app o completo)";; esac

# Los scripts tienen que tener fin de línea LF (si vinieron de Windows sin git, se arreglan).
for f in "$DIR"/scripts/*.sh; do
    if grep -q $'\r' "$f"; then sed -i 's/\r$//' "$f"; warn "Fin de línea corregido en $(basename "$f")"; fi
done

# --- Usuario y contraseña de la web: se piden AL PRINCIPIO -----------------------
# (en la instalación completa install.sh corre horas después; así no queda esperando)
if [ ! -f "$CONF_DIR/bench.env" ] && [ -z "${ADMIN_PASSWORD:-}" ]; then
    paso "Acceso a la web"
    read -r -p "Usuario para la web [admin]: " ADMIN_USER
    ADMIN_USER="${ADMIN_USER:-admin}"
    while true; do
        read -r -s -p "Contraseña para la web (mín. 10 caracteres): " ADMIN_PASSWORD; echo
        read -r -s -p "Repetila: " ADMIN_PASSWORD2; echo
        [ "$ADMIN_PASSWORD" = "$ADMIN_PASSWORD2" ] && [ "${#ADMIN_PASSWORD}" -ge 10 ] && break
        warn "No coinciden o es muy corta. Probá de nuevo."
    done
    unset ADMIN_PASSWORD2
    export ADMIN_USER ADMIN_PASSWORD
fi

if [ "$MODO" = "completo" ]; then
    # --- 1. Sistema --------------------------------------------------------------
    paso "1/6 Sistema base"
    export DEBIAN_FRONTEND=noninteractive
    dpkg --configure -a || true                  # la imagen rcn-ee viene con apt a medio configurar
    apt-get update -qq
    apt-get install -y --no-install-recommends ca-certificates curl git tmux cloud-guest-utils \
        || apt-get install -y --no-install-recommends ca-certificates curl git tmux

    # Partición raíz en toda la microSD (las imágenes vienen con 1,6 GB).
    raiz="$(findmnt -n -o SOURCE /)"
    if [[ "$raiz" =~ ^(/dev/mmcblk[0-9]+)p([0-9]+)$ ]] && command -v growpart >/dev/null; then
        if growpart "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" >/dev/null 2>&1; then
            resize2fs "$raiz" >/dev/null && log "Partición agrandada a toda la microSD."
        else
            log "La partición ya ocupa toda la microSD."
        fi
    fi
    df -h / | sed -n 2p

    # --- 2. Tailscale ---------------------------------------------------------------
    paso "2/6 Tailscale"
    if ! command -v tailscale >/dev/null; then
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
    if tailscale ip -4 >/dev/null 2>&1; then
        log "Tailscale ya está conectado: $(tailscale ip -4 | sed -n 1p)"
    else
        log "Abrí en la PC el enlace que aparece abajo e iniciá sesión para autorizar la placa."
        tailscale up --hostname=banco-stm32
        log "Tailscale conectado: $(tailscale ip -4 | sed -n 1p)"
        warn "Acordate de desactivar el vencimiento de la clave (Disable key expiry) en la consola de Tailscale."
    fi

    # --- 3. Python 3.9 y dfu-util (sólo sistemas viejos) ------------------------------
    paso "3/6 Python 3.9 y dfu-util"
    if [ -x /opt/python3.9/bin/python3.9 ] || python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        log "Ya hay Python 3.9 o superior."
    else
        bash "$DIR/scripts/prepare_ubuntu1604.sh"
    fi

    # --- 4. OpenOCD ---------------------------------------------------------------------
    paso "4/6 OpenOCD de ST (45-90 min la primera vez)"
    bash "$DIR/scripts/build_openocd.sh"
fi

# --- Aplicación ------------------------------------------------------------------
paso "$([ "$MODO" = completo ] && echo '5/6 ')Aplicación"
bash "$DIR/scripts/install.sh"
unset ADMIN_PASSWORD

for f in chips.yaml bench.yaml; do
    [ -f "$CONF_DIR/$f.dist" ] || continue
    if [ "$CONFIG" = "1" ]; then
        cp "$CONF_DIR/$f" "$CONF_DIR/$f.bak"
        mv "$CONF_DIR/$f.dist" "$CONF_DIR/$f"
        log "$f actualizado (el anterior quedó en $f.bak)."
    else
        warn "Hay una versión nueva de $f ($f.dist). Para usarla: desplegar.bat -ActualizarConfig"
    fi
done
if [ "$CONFIG" = "1" ]; then
    systemctl restart stm32-bench
fi

# --- MAC fija (BeagleBoard-xM: sin EEPROM, la MAC cambia en cada arranque) -----------
if [ ! -f /etc/stm32-bench/mac.conf ] && [ -e /sys/class/net/eth0 ]; then
    bash "$DIR/scripts/fijar_mac.sh" || warn "No se pudo fijar la MAC (ver scripts/fijar_mac.sh)."
fi

# --- GDB para la pestaña Depurar --------------------------------------------------
if [ "$MODO" = "completo" ]; then
    paso "6/6 GDB 12 para la pestaña Depurar (3-6 h la primera vez)"
    bash "$DIR/scripts/build_gdb.sh"
fi

# --- Resumen -------------------------------------------------------------------------
paso "Resumen"
minutos=$(( ($(date +%s) - inicio) / 60 ))
if systemctl is-active --quiet stm32-bench; then
    log "Servicio en marcha ($minutos min)."
else
    warn "El servicio no está activo. Mirá: journalctl -u stm32-bench -n 50"
fi
if [ -x /usr/local/bin/openocd ]; then
    log "OpenOCD: $(/usr/local/bin/openocd --version 2>&1 | sed -n 1p)"
fi
if [ -x /usr/local/bin/arm-none-eabi-gdb ]; then
    log "GDB: $(/usr/local/bin/arm-none-eabi-gdb --version | sed -n 1p)"
fi
ts_ip="$(tailscale ip -4 2>/dev/null | sed -n 1p || true)"
if [ -n "$ts_ip" ]; then
    log "Abrí http://$ts_ip:8080 desde tu PC."
else
    warn "Sin Tailscale: instalalo con  sudo bash $DIR/scripts/instalar_todo.sh completo"
fi
# La instalación completa corre en tmux: se espera un Enter para que el resumen no desaparezca.
if [ "$MODO" = "completo" ] && [ -t 0 ]; then
    read -r -p "Listo. Enter para cerrar esta ventana... " _
fi
