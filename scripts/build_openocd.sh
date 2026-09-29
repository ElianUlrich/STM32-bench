#!/usr/bin/env bash
# =============================================================================
# Compila e instala el fork oficial de ST de OpenOCD en /usr/local.
#
# Por qué: el OpenOCD de Debian es demasiado viejo para STM32H5/H7 (faltan
# target/stm32h5x.cfg y mejoras de stm32h7x). El fork de ST
# (https://github.com/STMicroelectronics/OpenOCD) es el que usa STM32CubeIDE.
#
# ¡LENTO! En la BeagleBoard-xM (Cortex-A8 1 GHz, 512 MB) la compilación tarda
# aproximadamente entre 45 y 90 minutos. Correlo dentro de tmux o screen para
# que un corte de SSH no lo interrumpa:
#     sudo apt install tmux && tmux new -s openocd
#     sudo scripts/build_openocd.sh
#
# Es idempotente: si ya está instalada la misma versión (commit), no recompila.
# Variables opcionales:
#     OPENOCD_REF=openocd-cubeide-v2.2.0   tag o rama del fork de ST
#     JOBS=2                               compilaciones en paralelo
#     FORCE=1                              recompilar aunque ya esté instalado
#     SRC_DIR=/usr/local/src/openocd-st    dónde se clona el código
#     KEEP_SRC=1                           no borrar el código al terminar
# Necesita ~1,5 GB libres (clon superficial + compilación + swap temporal).
# =============================================================================
set -euo pipefail

OPENOCD_REPO="${OPENOCD_REPO:-https://github.com/STMicroelectronics/OpenOCD.git}"
OPENOCD_REF="${OPENOCD_REF:-openocd-cubeide-v2.2.0}"
PREFIX="/usr/local"
SRC_DIR="${SRC_DIR:-/usr/local/src/openocd-st}"
JOBS="${JOBS:-2}"
FORCE="${FORCE:-0}"
STAMP="$PREFIX/share/openocd/.stm32-bench-build"
SWAPFILE="/var/tmp/openocd-build.swap"

log()  { echo -e "\033[1;32m[build_openocd]\033[0m $*"; }
warn() { echo -e "\033[1;33m[build_openocd]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[build_openocd]\033[0m $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Ejecutalo con sudo."

# --- Dependencias -------------------------------------------------------------
log "Instalando dependencias de compilación (apt)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    git ca-certificates build-essential make gcc pkg-config \
    libtool autoconf automake texinfo \
    libusb-1.0-0-dev libhidapi-dev

# --- Versión pedida (sin clonar todavía) ----------------------------------------
# ls-remote da el commit del tag/rama; "^{}" es el commit de un tag anotado.
REFS="$(git ls-remote "$OPENOCD_REPO" "refs/tags/$OPENOCD_REF" "refs/tags/$OPENOCD_REF^{}" "refs/heads/$OPENOCD_REF")"
[ -n "$REFS" ] || die "No existe el tag o rama '$OPENOCD_REF' en $OPENOCD_REPO"
COMMIT="$(echo "$REFS" | awk 'index($2, "^{}") {print $1; exit}')"
[ -n "$COMMIT" ] || COMMIT="$(echo "$REFS" | awk 'NR==1 {print $1}')"
log "Versión elegida: $OPENOCD_REF ($COMMIT)"

# --- ¿Ya está instalado? -----------------------------------------------------
if [ "$FORCE" != "1" ] && [ -x "$PREFIX/bin/openocd" ] && [ -f "$STAMP" ] \
   && [ "$(cat "$STAMP")" = "$COMMIT" ]; then
    log "OpenOCD $OPENOCD_REF ya está instalado (commit $COMMIT). Nada que hacer."
    log "Usá FORCE=1 para recompilar."
    "$PREFIX/bin/openocd" --version 2>&1 | head -n 1
    exit 0
fi

# --- Swap temporal (512 MB de RAM no alcanzan con -j2) -----------------------
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
        warn "Poca RAM+swap y poco disco para swap temporal: compilando con JOBS=1."
        JOBS=1
    fi
fi

# --- Código fuente ------------------------------------------------------------
# Clon superficial (solo esa versión): ~10 veces menos disco que el historial completo.
rm -rf "$SRC_DIR"
mkdir -p "$(dirname "$SRC_DIR")"
log "Clonando $OPENOCD_REF en $SRC_DIR..."
git clone -q --depth 1 --branch "$OPENOCD_REF" "$OPENOCD_REPO" "$SRC_DIR"

# --- Compilación ---------------------------------------------------------------
cd "$SRC_DIR"
# Solo jimtcl: libjaylink (J-Link) no hace falta y su servidor git a veces no responde.
if grep -q 'path = jimtcl' .gitmodules 2>/dev/null; then
    log "Inicializando el submódulo jimtcl..."
    git submodule update --init jimtcl
fi

log "bootstrap..."
./bootstrap nosubmodule

CONF_ARGS=(--prefix="$PREFIX" --enable-stlink --disable-werror --disable-doxygen-html --disable-doxygen-pdf)
# Versiones nuevas quitaron el jimtcl interno: en ese caso hace falta libjim-dev.
if ./configure --help | grep -q -- "--disable-internal-jimtcl"; then
    CONF_ARGS+=(--enable-internal-jimtcl)
else
    log "Esta versión usa jimtcl del sistema: instalando libjim-dev..."
    apt-get install -y --no-install-recommends libjim-dev
fi

log "configure ${CONF_ARGS[*]}"
./configure "${CONF_ARGS[@]}"

start=$(date +%s)
log "Compilando con $JOBS trabajo(s) en paralelo. Esto tarda (~45-90 min en la BeagleBoard-xM)..."
make -j"$JOBS"
log "Instalando en $PREFIX..."
make install
echo "$COMMIT" > "$STAMP"
log "Compilación e instalación: $(( ($(date +%s) - start) / 60 )) min."
cd /
if [ "${KEEP_SRC:-0}" != "1" ]; then
    rm -rf "$SRC_DIR"
    log "Código fuente borrado para liberar espacio (KEEP_SRC=1 para conservarlo)."
fi

# --- Verificación --------------------------------------------------------------
log "Verificando la instalación:"
"$PREFIX/bin/openocd" --version 2>&1 | head -n 1
SCRIPTS="$PREFIX/share/openocd/scripts"
missing=0
for cfg in interface/stlink.cfg interface/stlink-dap.cfg target/stm32h5x.cfg target/stm32h7x.cfg \
           target/stm32f1x.cfg target/stm32f4x.cfg target/stm32l4x.cfg target/stm32g0x.cfg target/stm32g4x.cfg; do
    if [ -f "$SCRIPTS/$cfg" ]; then
        echo "  ok      $cfg"
    else
        echo "  FALTA   $cfg"
        missing=1
    fi
done
if "$PREFIX/bin/openocd" -c "help adapter serial" -c shutdown 2>&1 | grep -q "adapter serial"; then
    echo "  ok      comando 'adapter serial'"
fi
[ "$missing" -eq 0 ] || warn "Faltan algunos .cfg: la app avisará en la interfaz."
if command -v openocd >/dev/null && [ "$(command -v openocd)" != "$PREFIX/bin/openocd" ]; then
    warn "Hay otro openocd en el PATH ($(command -v openocd)). La app usa $PREFIX/bin/openocd (bench.yaml)."
fi
log "Listo."
