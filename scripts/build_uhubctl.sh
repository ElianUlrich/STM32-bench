#!/usr/bin/env bash
# =============================================================================
# Instala uhubctl (https://github.com/mvp/uhubctl): corta y da energía a los
# puertos de un hub USB que lo soporte. Lo usa la sección "Alimentación USB"
# de la pestaña Sistema.
#
#   sudo bash scripts/build_uhubctl.sh
#
# En Debian 10+ / Ubuntu 19.04+ viene empaquetado; en Ubuntu 16.04 no, así que
# se compila (es un solo archivo C: tarda segundos). Idempotente.
# =============================================================================
set -euo pipefail

VERSION="${UHUBCTL_VERSION:-v2.6.0}"
SRC_DIR="/usr/local/src/uhubctl"

log()  { echo -e "\033[1;32m[uhubctl]\033[0m $*"; }
die()  { echo -e "\033[1;31m[uhubctl]\033[0m $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Ejecutalo con sudo."

if command -v uhubctl >/dev/null; then
    log "Ya está instalado: $(command -v uhubctl) ($(uhubctl -v 2>/dev/null | sed -n 1p))"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
if apt-get install -y --no-install-recommends uhubctl >/dev/null 2>&1; then
    log "Instalado desde apt: $(command -v uhubctl)"
    exit 0
fi

log "No está en apt: se compila $VERSION..."
apt-get install -y --no-install-recommends git ca-certificates build-essential pkg-config libusb-1.0-0-dev
rm -rf "$SRC_DIR"
git clone -q --depth 1 --branch "$VERSION" https://github.com/mvp/uhubctl.git "$SRC_DIR"
make -C "$SRC_DIR"
make -C "$SRC_DIR" install
rm -rf "$SRC_DIR"
log "Instalado: $(command -v uhubctl) ($(uhubctl -v 2>/dev/null | sed -n 1p))"
