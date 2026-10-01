#!/usr/bin/env bash
# =============================================================================
# MAC fija para la red de la placa.
#
# La BeagleBoard-xM no tiene EEPROM para el chip de red (LAN9514): el driver
# smsc95xx inventa una MAC distinta en cada arranque, así que el router le da
# otra IP local cada vez. Este script guarda una MAC y un servicio de systemd
# la pone en cada arranque, antes de que se levante la red.
#
#   sudo bash scripts/fijar_mac.sh                     fija la MAC que tiene AHORA
#                                                      (así la IP actual se conserva)
#   sudo bash scripts/fijar_mac.sh 02:5e:42:00:00:01   fija esa MAC (se aplica al reiniciar)
#   sudo bash scripts/fijar_mac.sh --quitar            vuelve al comportamiento original
#
# Variable opcional: IFACE=eth0
# Recomendado además: en el router, reservar una IP para esa MAC (DHCP estático).
# =============================================================================
set -euo pipefail

IFACE="${IFACE:-eth0}"
CONF_DIR="/etc/stm32-bench"
CONF="$CONF_DIR/mac.conf"
UNIT="/etc/systemd/system/stm32-bench-mac.service"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log()  { echo -e "\033[1;32m[mac]\033[0m $*"; }
warn() { echo -e "\033[1;33m[mac]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[mac]\033[0m $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Ejecutalo con sudo."
[[ "$IFACE" =~ ^[a-zA-Z0-9_.-]{1,15}$ ]] || die "Nombre de interfaz inválido: $IFACE"

if [ "${1:-}" = "--quitar" ]; then
    systemctl disable --now stm32-bench-mac.service 2>/dev/null || true
    rm -f "$UNIT" "$CONF"
    systemctl daemon-reload
    log "Listo: desde el próximo arranque $IFACE vuelve a tener una MAC al azar."
    exit 0
fi

[ -e "/sys/class/net/$IFACE" ] || die "No existe la interfaz $IFACE (ip link para ver las que hay; IFACE=... para elegir otra)."
actual="$(cat "/sys/class/net/$IFACE/address")"
mac="$(echo "${1:-$actual}" | tr 'A-F' 'a-f')"
[[ "$mac" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] || die "MAC inválida: $mac (formato 02:5e:42:00:00:01)"
primer=$((16#${mac:0:2}))
(( primer & 1 )) && die "$mac es una dirección multicast: el primer byte tiene que ser par (p. ej. 02:...)."

install -d -m 0755 "$CONF_DIR"
printf 'IFACE=%s\nMAC=%s\n' "$IFACE" "$mac" > "$CONF"
chmod 0644 "$CONF"

IP_BIN="$(command -v ip)"
sed -e "s|@IFACE@|$IFACE|g" -e "s|@IP@|$IP_BIN|g" \
    "$SRC_DIR/deploy/systemd/stm32-bench-mac.service" > "$UNIT"
systemctl daemon-reload
systemctl enable stm32-bench-mac.service >/dev/null

if [ "$mac" = "$actual" ]; then
    log "MAC de $IFACE fijada en $mac (la actual): desde el próximo arranque ya no cambia."
else
    # No se aplica en caliente: cambiar la MAC corta la red (y la sesión SSH) y cambia la IP.
    warn "MAC de $IFACE fijada en $mac. Se aplica al reiniciar: la placa va a tener otra IP local."
fi
log "Sugerencia: en el router, reservá una IP para $mac (DHCP estático / reserva de dirección)."
