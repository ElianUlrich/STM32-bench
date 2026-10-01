#!/usr/bin/env bash
# =============================================================================
# Instalación del banco STM32 en la BeagleBoard-xM (Debian armhf).
#
#   sudo scripts/install.sh
#
# Hace, de forma idempotente (se puede volver a correr para actualizar):
#   1. Dependencias del sistema (python3-venv, dfu-util, avrdude, libgpiod...).
#   2. Usuario de servicio sin privilegios (stm32bench) y grupos.
#   3. Copia de la app a /opt/stm32-bench y venv con las dependencias Python.
#   4. Directorios: /etc/stm32-bench, /var/lib/stm32-bench, proyectos.
#   5. /etc/stm32-bench/bench.env con usuario, hash de contraseña y clave.
#   6. Reglas udev (ST-Link, DFU, USBasp/USBtiny, gpiochip).
#   7. /etc/sudoers.d/stm32-bench (solo poweroff/reboot), validado con visudo.
#   8. Servicio systemd stm32-bench (arranque automático).
#
# OpenOCD se compila aparte con scripts/build_openocd.sh (tarda mucho).
#
# Variables opcionales:
#   SERVICE_USER=stm32bench  APP_DIR=/opt/stm32-bench
#   PROJECTS_DIR=/srv/stm32-bench/proyectos  PORT=8080
#   ADMIN_USER=admin  ADMIN_PASSWORD=...   (si no, se pregunta)
#   PYTHON=/ruta/python3.9   (por defecto: /opt/python3.9 si existe, si no python3)
#   USE_PIWHEELS=0|1        (1: agrega piwheels; solo para Debian/Raspbian recientes)
#
# En Ubuntu 16.04 (Python 3.5) corré antes scripts/prepare_ubuntu1604.sh.
# =============================================================================
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-stm32bench}"
APP_DIR="${APP_DIR:-/opt/stm32-bench}"
CONF_DIR="/etc/stm32-bench"
STATE_DIR="/var/lib/stm32-bench"
PROJECTS_DIR="${PROJECTS_DIR:-/srv/stm32-bench/proyectos}"
PORT="${PORT:-8080}"
USE_PIWHEELS="${USE_PIWHEELS:-0}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log()  { echo -e "\033[1;32m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[install]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[install]\033[0m $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Ejecutalo con sudo."

# --- 1. Paquetes del sistema --------------------------------------------------
log "Instalando paquetes del sistema..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 ca-certificates rsync sudo usbutils libusb-1.0-0 libhidapi-hidraw0
# dfu-util: el de /usr/local (compilado por prepare_ubuntu1604.sh) tiene prioridad.
[ -x /usr/local/bin/dfu-util ] || apt-get install -y --no-install-recommends dfu-util
# avrdude: Arduino / AVR (bootloader serie o programador ISP).
apt-get install -y --no-install-recommends avrdude \
    || warn "No se pudo instalar avrdude (solo necesario para placas Arduino / AVR)."
# libgpiod solo hace falta para el backend "gpio" de BootControl.
apt-get install -y --no-install-recommends python3-libgpiod gpiod \
    || warn "No se pudo instalar python3-libgpiod (solo necesario para boot_control: gpio)."

# Python >= 3.9: /opt/python3.9 (prepare_ubuntu1604.sh) o el del sistema.
if [ -z "${PYTHON:-}" ]; then
    if [ -x /opt/python3.9/bin/python3.9 ]; then
        PYTHON=/opt/python3.9/bin/python3.9
    else
        PYTHON="$(command -v python3)"
    fi
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    die "Se necesita Python 3.9 o superior ($PYTHON es $("$PYTHON" --version 2>&1)).
En Ubuntu 16.04 corré primero: sudo bash scripts/prepare_ubuntu1604.sh"
fi
if [ "$PYTHON" = "$(command -v python3)" ]; then
    apt-get install -y --no-install-recommends python3-venv python3-pip
fi
log "Usando $PYTHON ($("$PYTHON" --version 2>&1))"

# --- 2. Usuario y grupos ------------------------------------------------------
getent group plugdev >/dev/null || groupadd --system plugdev
getent group gpio >/dev/null || groupadd --system gpio
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    log "Creando el usuario de servicio $SERVICE_USER..."
    useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -a -G plugdev,dialout,gpio "$SERVICE_USER"

# --- 3. Aplicación y venv -----------------------------------------------------
if [ "$SRC_DIR" != "$APP_DIR" ]; then
    log "Copiando la aplicación a $APP_DIR..."
    mkdir -p "$APP_DIR"
    rsync -a --delete \
        --exclude .git --exclude .venv --exclude dev-data --exclude tests \
        --exclude __pycache__ --exclude .pytest_cache --exclude .claude \
        "$SRC_DIR"/ "$APP_DIR"/
fi
chown -R root:root "$APP_DIR"
chmod -R go-w "$APP_DIR"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    log "Creando el entorno virtual..."
    # --system-site-packages: para ver python3-libgpiod instalado por apt.
    "$PYTHON" -m venv --system-site-packages "$APP_DIR/.venv"
fi
# --only-binary pydantic-core: nunca compilarlo (es Rust); pip elige la última
# versión con wheel manylinux armv7l para este Python.
PIP_ARGS=(--disable-pip-version-check --prefer-binary --only-binary pydantic-core)
if [ "$USE_PIWHEELS" = "1" ]; then
    # Las wheels de piwheels se compilan para Raspberry Pi OS reciente y pueden
    # no funcionar con una glibc vieja (Ubuntu 16.04).
    PIP_ARGS+=(--extra-index-url https://www.piwheels.org/simple)
fi
"$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
log "Instalando dependencias de Python (puede tardar unos minutos)..."
"$APP_DIR/.venv/bin/pip" install "${PIP_ARGS[@]}" -r "$APP_DIR/requirements.txt"

# esptool (ESP32 / ESP8266). Se instala --no-deps porque sus dependencias
# "cryptography" (solo la usa espsecure) y bitstring 4 (usa bitarray, en C) no
# tienen wheel armv7l para esta glibc. bitstring 3.1.9 es Python puro.
log "Instalando esptool..."
"$APP_DIR/.venv/bin/pip" install "${PIP_ARGS[@]}" "bitstring==3.1.9" "ecdsa>=0.16" "reedsolo>=1.5.3,<1.8" \
    && "$APP_DIR/.venv/bin/pip" install "${PIP_ARGS[@]}" --no-deps "esptool==4.12.0" \
    && "$APP_DIR/.venv/bin/python" -m esptool version >/dev/null \
    || warn "No se pudo instalar esptool (solo necesario para placas ESP)."

# --- 4. Directorios ------------------------------------------------------------
log "Creando directorios..."
install -d -m 0755 -o root -g root "$CONF_DIR"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"
install -d -m 2775 -o "$SERVICE_USER" -g "$SERVICE_USER" "$PROJECTS_DIR"
for f in chips.yaml bench.yaml; do
    if [ ! -f "$CONF_DIR/$f" ]; then
        install -m 0644 "$APP_DIR/config/$f" "$CONF_DIR/$f"
        log "  $CONF_DIR/$f instalado"
    elif ! cmp -s "$APP_DIR/config/$f" "$CONF_DIR/$f"; then
        install -m 0644 "$APP_DIR/config/$f" "$CONF_DIR/$f.dist"
        warn "  $CONF_DIR/$f ya existe (no se toca). La versión nueva quedó en $f.dist"
    fi
done

# --- 5. bench.env (secretos) -------------------------------------------------
ENV_FILE="$CONF_DIR/bench.env"
if [ ! -f "$ENV_FILE" ]; then
    log "Configurando el acceso a la web..."
    ADMIN_USER="${ADMIN_USER:-}"
    if [ -z "$ADMIN_USER" ]; then
        read -r -p "Usuario para la web [admin]: " ADMIN_USER
        ADMIN_USER="${ADMIN_USER:-admin}"
    fi
    if [ -z "${ADMIN_PASSWORD:-}" ]; then
        while true; do
            read -r -s -p "Contraseña (mín. 10 caracteres): " ADMIN_PASSWORD; echo
            read -r -s -p "Repetila: " ADMIN_PASSWORD2; echo
            [ "$ADMIN_PASSWORD" = "$ADMIN_PASSWORD2" ] && [ "${#ADMIN_PASSWORD}" -ge 10 ] && break
            warn "No coinciden o es muy corta. Probá de nuevo."
        done
    fi
    HASH="$(printf '%s' "$ADMIN_PASSWORD" | "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/hash_password.py" --stdin)"
    SECRET="$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    umask 027
    cat > "$ENV_FILE" <<EOF
# Generado por install.sh el $(date -Iseconds). Permisos 640 root:$SERVICE_USER.
BENCH_USER=$ADMIN_USER
BENCH_PASSWORD_HASH=$HASH
BENCH_SECRET_KEY=$SECRET
BENCH_CONFIG_DIR=$CONF_DIR
BENCH_PROJECTS_DIR=$PROJECTS_DIR
BENCH_STATE_DIR=$STATE_DIR
BENCH_HOST=0.0.0.0
BENCH_PORT=$PORT
BENCH_COOKIE_SECURE=0
BENCH_MOCK=0
EOF
    unset ADMIN_PASSWORD ADMIN_PASSWORD2
    umask 022
else
    log "$ENV_FILE ya existe: se conservan usuario y contraseña."
fi
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

# --- 6. udev ------------------------------------------------------------------
log "Instalando reglas udev..."
install -m 0644 "$APP_DIR/deploy/udev/60-stm32-bench.rules" /etc/udev/rules.d/60-stm32-bench.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --subsystem-match=gpio || udevadm trigger

# --- 7. sudoers ----------------------------------------------------------------
POWEROFF="$(command -v poweroff || true)"; REBOOT="$(command -v reboot || true)"
for p in /usr/sbin/poweroff /sbin/poweroff; do [ -e "$p" ] && POWEROFF="$p" && break; done
for p in /usr/sbin/reboot /sbin/reboot; do [ -e "$p" ] && REBOOT="$p" && break; done
[ -n "$POWEROFF" ] && [ -n "$REBOOT" ] || die "No se encontraron poweroff/reboot."
TMP_SUDOERS="$(mktemp)"
sed -e "s|@SERVICE_USER@|$SERVICE_USER|g" -e "s|@POWEROFF@|$POWEROFF|g" -e "s|@REBOOT@|$REBOOT|g" \
    "$APP_DIR/deploy/sudoers/stm32-bench" > "$TMP_SUDOERS"
if visudo -cf "$TMP_SUDOERS" >/dev/null; then
    install -m 0440 -o root -g root "$TMP_SUDOERS" /etc/sudoers.d/stm32-bench
    log "sudoers instalado y validado (solo $POWEROFF y $REBOOT)."
else
    rm -f "$TMP_SUDOERS"
    die "visudo rechazó el archivo sudoers generado; no se instaló."
fi
rm -f "$TMP_SUDOERS"

# --- 8. systemd ----------------------------------------------------------------
log "Instalando el servicio systemd..."
sed -e "s|@SERVICE_USER@|$SERVICE_USER|g" -e "s|@APP_DIR@|$APP_DIR|g" \
    "$APP_DIR/deploy/systemd/stm32-bench.service" > /etc/systemd/system/stm32-bench.service
systemctl daemon-reload
systemctl enable stm32-bench.service >/dev/null
systemctl restart stm32-bench.service

# --- Resumen ------------------------------------------------------------------
sleep 3
if systemctl is-active --quiet stm32-bench.service; then
    log "Servicio en marcha."
else
    warn "El servicio no arrancó. Mirá: journalctl -u stm32-bench -n 50"
fi
[ -x /usr/local/bin/openocd ] || warn "No hay /usr/local/bin/openocd todavía: corré sudo scripts/build_openocd.sh"
TS_IP="$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
if [ -n "$TS_IP" ]; then
    log "Abrí http://$TS_IP:$PORT desde tu PC (por Tailscale)."
else
    warn "No se detectó tailscale0. Instalá Tailscale (ver README) y abrí http://<IP-tailscale>:$PORT"
fi
