#!/usr/bin/env bash
# =============================================================================
# Instala OpenOCD o GDB PRECOMPILADOS (Releases de GitHub) en vez de compilarlos
# en la placa: minutos en lugar de horas.
#
#   sudo bash scripts/binarios.sh openocd armhf
#   sudo bash scripts/binarios.sh gdb arm64
#
# Los compila .github/workflows/binarios.yml con los mismos build_openocd.sh y
# build_gdb.sh (armhf sobre Ubuntu 16.04: sirve para sistemas con glibc >= 2.23;
# arm64 sobre Debian 11). Se verifica el SHA-256 y que no falten bibliotecas;
# si algo falla, sale con código 1 y instalar_todo.sh compila como siempre.
#
# BINARIOS_URL cambia de dónde se bajan (por defecto, el release binarios-v1).
# =============================================================================
set -euo pipefail

HERRAMIENTA="${1:-}"
ARCH="${2:-}"
BASE="${BINARIOS_URL:-https://github.com/ElianUlrich/STM32-bench/releases/download/binarios-v1}"

log()  { echo -e "\033[1;32m[binarios]\033[0m $*"; }
warn() { echo -e "\033[1;33m[binarios]\033[0m $*" >&2; }

[ "$(id -u)" -eq 0 ] || { warn "Ejecutalo con sudo."; exit 1; }
case "$HERRAMIENTA" in
    openocd) BIN=/usr/local/bin/openocd; LIBS="libusb-1.0-0 libhidapi-hidraw0 libhidapi-libusb0" ;;
    gdb)     BIN=/usr/local/bin/arm-none-eabi-gdb; LIBS="libexpat1 zlib1g" ;;
    *) warn "Uso: binarios.sh openocd|gdb armhf|arm64"; exit 1 ;;
esac
case "$ARCH" in armhf|arm64) ;; *) warn "Arquitectura sin binarios: $ARCH"; exit 1 ;; esac

ARCHIVO="$HERRAMIENTA-$ARCH.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log "Bajando $ARCHIVO..."
if ! curl -fsSL --retry 3 -o "$TMP/$ARCHIVO" "$BASE/$ARCHIVO" \
   || ! curl -fsSL --retry 3 -o "$TMP/$ARCHIVO.sha256" "$BASE/$ARCHIVO.sha256"; then
    warn "No se pudo bajar $BASE/$ARCHIVO."
    exit 1
fi
if ! (cd "$TMP" && sha256sum -c "$ARCHIVO.sha256" >/dev/null); then
    warn "El SHA-256 de $ARCHIVO no coincide: no se instala."
    exit 1
fi

# Bibliotecas que necesitan los binarios (las de ncurses cambian de nombre entre versiones).
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends $LIBS >/dev/null 2>&1 || true
if [ "$HERRAMIENTA" = gdb ]; then
    apt-get install -y --no-install-recommends libgmp10 >/dev/null 2>&1 || true
    apt-get install -y --no-install-recommends libtinfo6 >/dev/null 2>&1 \
        || apt-get install -y --no-install-recommends libtinfo5 >/dev/null 2>&1 || true
fi

# Se prueba en una carpeta aparte antes de tocar /usr/local.
mkdir -p "$TMP/raiz"
tar -xzf "$TMP/$ARCHIVO" -C "$TMP/raiz"
faltan="$(ldd "$TMP/raiz$BIN" 2>/dev/null | awk '/not found/ {print $1}' | tr '\n' ' ')"
if [ -n "$faltan" ]; then
    warn "A $HERRAMIENTA le faltan bibliotecas en este sistema: $faltan"
    exit 1
fi
if ! LD_LIBRARY_PATH= "$TMP/raiz$BIN" --version >/dev/null 2>&1; then
    warn "$HERRAMIENTA precompilado no arranca en este sistema."
    exit 1
fi

tar -xzf "$TMP/$ARCHIVO" -C /
log "Instalado: $("$BIN" --version 2>&1 | sed -n 1p)"
