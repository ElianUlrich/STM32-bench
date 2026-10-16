#!/usr/bin/env bash
# =============================================================================
# Compila OpenOCD o GDB dentro de un contenedor ARM (GitHub Actions, ver
# .github/workflows/binarios.yml) con los mismos build_openocd.sh / build_gdb.sh
# que usa la placa, y empaqueta lo que instalaron en /usr/local:
#
#   out/<herramienta>-<arch>.tar.gz  y  .sha256     (los baja scripts/binarios.sh)
#
#   bash scripts/ci_binarios.sh openocd armhf
# =============================================================================
set -euo pipefail

HERRAMIENTA="$1"
ARCH="$2"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$DIR/out"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git >/dev/null

find /usr/local -xdev | sort > /tmp/antes
JOBS="$(nproc)" KEEP_SRC=0 bash "$DIR/scripts/build_$HERRAMIENTA.sh"
find /usr/local -xdev | sort > /tmp/despues
comm -13 /tmp/antes /tmp/despues | sed 's|^/||' > /tmp/nuevos
[ -s /tmp/nuevos ] || { echo "No se instaló nada en /usr/local" >&2; exit 1; }

mkdir -p "$OUT"
ARCHIVO="$HERRAMIENTA-$ARCH.tar.gz"
tar -czf "$OUT/$ARCHIVO" --no-recursion -C / -T /tmp/nuevos
(cd "$OUT" && sha256sum "$ARCHIVO" > "$ARCHIVO.sha256")
echo "Listo: $ARCHIVO ($(du -h "$OUT/$ARCHIVO" | cut -f1)), $(wc -l < /tmp/nuevos) archivos"
