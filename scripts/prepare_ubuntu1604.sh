#!/usr/bin/env bash
# =============================================================================
# Preparación para sistemas viejos (p. ej. la imagen rcn-ee Ubuntu 16.04 de la
# BeagleBoard-xM), que traen Python 3.5 y dfu-util 0.8:
#
#   * compila Python 3.9 en /opt/python3.9 (sin tocar el python3 del sistema);
#     3.9 es la última versión que compila con el OpenSSL 1.0.2 de 16.04;
#   * compila dfu-util 0.11 en /usr/local (0.8 no tiene :mass-erase).
#
#   sudo bash scripts/prepare_ubuntu1604.sh
#
# Después: sudo bash scripts/build_openocd.sh  y  sudo bash scripts/install.sh
#
# Tarda ~1 a 2 h en la xM (casi todo es Python). Correlo dentro de tmux.
# Es idempotente: lo ya instalado no se recompila (FORCE=1 para forzar).
# =============================================================================
set -euo pipefail

PY_VERSION="3.9.25"
PY_SHA256="00e07d7c0f2f0cc002432d1ee84d2a40dae404a99303e3f97701c10966c91834"
PY_PREFIX="/opt/python3.9"
DFU_VERSION="0.11"
DFU_MD5="31c983543a1fe8f03260ca4d56ad4f43"
BUILD_DIR="/var/tmp/stm32-bench-build"
SWAPFILE="/var/tmp/stm32-bench-build.swap"
JOBS="${JOBS:-2}"
FORCE="${FORCE:-0}"

log()  { echo -e "\033[1;32m[prepare]\033[0m $*"; }
warn() { echo -e "\033[1;33m[prepare]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[prepare]\033[0m $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Ejecutalo con sudo."

# --- Espacio en disco ---------------------------------------------------------
free_mb=$(df -Pm /var/tmp | awk 'NR==2 {print $4}')
if [ "$free_mb" -lt 1200 ]; then
    die "Hay solo ${free_mb} MB libres. Hacen falta ~1,2 GB. Si la microSD es más grande que la
partición, agrandala:  sudo apt install cloud-guest-utils && sudo growpart /dev/mmcblk0 2 && sudo resize2fs /dev/mmcblk0p2"
fi

# --- Dependencias -------------------------------------------------------------
log "Instalando dependencias de compilación (apt)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    build-essential pkg-config wget curl ca-certificates xz-utils \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev uuid-dev libgdbm-dev libncurses5-dev \
    libusb-1.0-0-dev autoconf automake libtool

# --- Swap temporal ------------------------------------------------------------
cleanup() {
    if [ -f "$SWAPFILE" ]; then
        swapoff "$SWAPFILE" 2>/dev/null || true
        rm -f "$SWAPFILE"
    fi
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
if [ $((mem_kb + swap_kb)) -lt 900000 ] && [ "$free_mb" -gt 1500 ]; then
    log "Creando swap temporal de 512 MB..."
    dd if=/dev/zero of="$SWAPFILE" bs=1M count=512 status=none
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE" >/dev/null
    swapon "$SWAPFILE"
fi
mkdir -p "$BUILD_DIR"

# --- Python 3.9 -----------------------------------------------------------------
PY_BIN="$PY_PREFIX/bin/python3.9"
if [ "$FORCE" != "1" ] && [ -x "$PY_BIN" ] && "$PY_BIN" -c "import sys; sys.exit(0 if sys.version.split()[0] == '$PY_VERSION' else 1)"; then
    log "Python $PY_VERSION ya está instalado en $PY_PREFIX."
else
    log "Descargando Python $PY_VERSION..."
    cd "$BUILD_DIR"
    wget -q "https://www.python.org/ftp/python/$PY_VERSION/Python-$PY_VERSION.tar.xz"
    echo "$PY_SHA256  Python-$PY_VERSION.tar.xz" | sha256sum -c - >/dev/null \
        || die "La suma SHA256 de Python-$PY_VERSION.tar.xz no coincide."
    tar -xf "Python-$PY_VERSION.tar.xz"
    rm "Python-$PY_VERSION.tar.xz"
    cd "Python-$PY_VERSION"
    log "Compilando Python $PY_VERSION (1 a 2 h en la xM)..."
    ./configure --prefix="$PY_PREFIX" --with-ensurepip=install >/dev/null
    make -j"$JOBS" >/dev/null
    make altinstall >/dev/null
    cd "$BUILD_DIR" && rm -rf "Python-$PY_VERSION"
fi
"$PY_BIN" -c "import ssl, sqlite3, ctypes, lzma, bz2, zlib, uuid; print('  módulos ok:', ssl.OPENSSL_VERSION)" \
    || die "A Python le faltan módulos (revisá los paquetes -dev de arriba)."
"$PY_BIN" --version

# --- dfu-util 0.11 ------------------------------------------------------------
if [ "$FORCE" != "1" ] && /usr/local/bin/dfu-util -V 2>/dev/null | grep -q "dfu-util $DFU_VERSION"; then
    log "dfu-util $DFU_VERSION ya está instalado en /usr/local."
else
    log "Compilando dfu-util $DFU_VERSION..."
    cd "$BUILD_DIR"
    wget -q "https://dfu-util.sourceforge.net/releases/dfu-util-$DFU_VERSION.tar.gz"
    echo "$DFU_MD5  dfu-util-$DFU_VERSION.tar.gz" | md5sum -c - >/dev/null \
        || die "La suma MD5 de dfu-util-$DFU_VERSION.tar.gz no coincide."
    tar -xzf "dfu-util-$DFU_VERSION.tar.gz"
    cd "dfu-util-$DFU_VERSION"
    ./configure --prefix=/usr/local >/dev/null
    make -j"$JOBS" >/dev/null
    make install >/dev/null
fi
/usr/local/bin/dfu-util -V | head -n 1
if dpkg -s dfu-util >/dev/null 2>&1; then
    warn "También está el dfu-util de apt (/usr/bin). La app usa el primero del PATH: /usr/local/bin."
fi

log "Listo. Siguiente paso: sudo bash scripts/build_openocd.sh"
