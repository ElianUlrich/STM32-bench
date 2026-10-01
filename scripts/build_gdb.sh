#!/usr/bin/env bash
# =============================================================================
# Compila e instala GDB para ARM (arm-none-eabi-gdb) en /usr/local.
#
# Por qué: la pestaña "Depurar" de la web maneja GDB en la placa. El GDB de
# Ubuntu 16.04 (gdb-multiarch 7.11) no lee la información de depuración DWARF 5
# que genera el GCC 11+ de STM32CubeIDE ("wrong version in compilation unit
# header"). GDB 12 sí, y todavía compila con el GCC 5.4 de Ubuntu 16.04.
#
# Se compila solo GDB, para ARM, sin Python, TUI ni documentación, y sin
# símbolos de depuración (-O2): así entra en la RAM de la xM con swap.
#
# ¡MUY LENTO! En la BeagleBoard-xM puede tardar de 3 a 6 horas. Correlo en tmux:
#     tmux new -s gdb
#     sudo bash scripts/build_gdb.sh
# (si se corta el SSH: tmux attach -t gdb)
#
# Es idempotente: si ya está instalada la misma versión, no recompila.
# Variables opcionales:
#     GDB_VERSION=12.1   JOBS=1   FORCE=1   KEEP_SRC=1
#     SRC_DIR=/usr/local/src/gdb-build
# Necesita ~1,5 GB libres en el disco (código + compilación + swap temporal).
# =============================================================================
set -euo pipefail

GDB_VERSION="${GDB_VERSION:-12.1}"
URL="https://ftp.gnu.org/gnu/gdb/gdb-${GDB_VERSION}.tar.xz"
PREFIX="/usr/local"
SRC_DIR="${SRC_DIR:-/usr/local/src/gdb-build}"
JOBS="${JOBS:-1}"
FORCE="${FORCE:-0}"
BIN="$PREFIX/bin/arm-none-eabi-gdb"
STAMP="$PREFIX/share/.stm32-bench-gdb"
SWAPFILE="/var/tmp/gdb-build.swap"

log()  { echo -e "\033[1;32m[build_gdb]\033[0m $*"; }
warn() { echo -e "\033[1;33m[build_gdb]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[build_gdb]\033[0m $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Ejecutalo con sudo."

# --- ¿Ya está instalado? -----------------------------------------------------
if [ "$FORCE" != "1" ] && [ -x "$BIN" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$GDB_VERSION" ]; then
    log "arm-none-eabi-gdb $GDB_VERSION ya está instalado. Nada que hacer (FORCE=1 para recompilar)."
    "$BIN" --version | sed -n 1p   # sed lee todo: sin SIGPIPE con pipefail
    exit 0
fi

# --- Dependencias -------------------------------------------------------------
# expat: imprescindible, OpenOCD le describe a GDB los registros del Cortex-M en XML.
# gmp: obligatoria desde GDB 11. ncurses: la readline interna necesita termcap.
log "Instalando dependencias de compilación (apt)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils build-essential make gcc g++ bison flex \
    libexpat1-dev libgmp-dev zlib1g-dev libncurses5-dev

# --- Swap temporal ---------------------------------------------------------------
cleanup_swap() {
    if [ -f "$SWAPFILE" ]; then
        swapoff "$SWAPFILE" 2>/dev/null || true
        rm -f "$SWAPFILE"
        log "Swap temporal eliminado."
    fi
}
trap cleanup_swap EXIT
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
if [ $((mem_kb + swap_kb)) -lt 1400000 ]; then
    free_kb=$(df -Pk /var/tmp | awk 'NR==2 {print $4}')
    if [ "$free_kb" -gt 1300000 ]; then
        log "Creando swap temporal de 1 GB en $SWAPFILE (se borra al terminar)..."
        dd if=/dev/zero of="$SWAPFILE" bs=1M count=1024 status=none
        chmod 600 "$SWAPFILE"
        mkswap "$SWAPFILE" >/dev/null
        swapon "$SWAPFILE"
    else
        warn "Poca RAM+swap y poco disco para un swap temporal: la compilación puede fallar por memoria."
    fi
fi

# --- Código fuente ------------------------------------------------------------
rm -rf "$SRC_DIR"
mkdir -p "$SRC_DIR"
log "Descargando $URL ..."
curl -fL --retry 3 -o "$SRC_DIR/gdb.tar.xz" "$URL"
log "Descomprimiendo..."
tar -xJf "$SRC_DIR/gdb.tar.xz" -C "$SRC_DIR"
rm -f "$SRC_DIR/gdb.tar.xz"

# --- Compilación (fuera del árbol de fuentes, como pide GDB) ------------------
mkdir -p "$SRC_DIR/build"
cd "$SRC_DIR/build"
CONF_ARGS=(--prefix="$PREFIX" --target=arm-none-eabi
           --disable-nls --disable-werror --disable-sim --disable-tui
           --with-expat --with-python=no --without-guile --without-babeltrace
           --without-debuginfod --disable-source-highlight --without-lzma --with-system-zlib
           --disable-binutils --disable-ld --disable-gas --disable-gold --disable-gprof --disable-gprofng)
log "configure ${CONF_ARGS[*]}"
CFLAGS="-O2" CXXFLAGS="-O2" "../gdb-${GDB_VERSION}/configure" "${CONF_ARGS[@]}"

start=$(date +%s)
log "Compilando con $JOBS trabajo(s). Esto tarda varias horas en la BeagleBoard-xM..."
# MAKEINFO=true: no regenerar la documentación (no hace falta texinfo).
make -j"$JOBS" all-gdb MAKEINFO=true
log "Instalando en $PREFIX..."
make install-gdb MAKEINFO=true
mkdir -p "$(dirname "$STAMP")"
echo "$GDB_VERSION" > "$STAMP"
log "Compilación e instalación: $(( ($(date +%s) - start) / 60 )) min."
cd /
if [ "${KEEP_SRC:-0}" != "1" ]; then
    rm -rf "$SRC_DIR"
    log "Código fuente borrado para liberar espacio (KEEP_SRC=1 para conservarlo)."
fi

# --- Verificación --------------------------------------------------------------
"$BIN" --version | sed -n 1p
if "$BIN" -batch -ex "set architecture armv8-m.main" >/dev/null 2>&1; then
    log "OK: GDB conoce Cortex-M33 (armv8-m.main)."
else
    warn "Este GDB no reconoce armv8-m.main; revisá la versión."
fi
log "Listo. En /etc/stm32-bench/bench.yaml, debugger.gdb_binary: $BIN (es el valor por defecto)."
