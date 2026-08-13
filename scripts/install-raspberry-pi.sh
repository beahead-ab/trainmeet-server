#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Kör installationen med sudo: sudo ./scripts/install-raspberry-pi.sh"
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERVER_DIR=$(dirname "$SCRIPT_DIR")
INSTALL_DIR=/opt/trainmeet-server
STATE_DIR=/var/lib/trainmeet-server
VENV_DIR="$INSTALL_DIR/venv"
DESKTOP_USER=${TRAINMEET_SERVER_DESKTOP_USER:-${SUDO_USER:-}}

# This script normally runs from the complete source package downloaded by
# install.sh. If somebody pipes this low-level script directly into sh, $0 is
# only "sh" and SERVER_DIR points at the current directory. Bootstrap through
# the public installer instead of failing later with a confusing missing-src
# message.
if [ ! -d "$SERVER_DIR/src" ] \
  || [ ! -f "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server.service" ]; then
  echo "Hämtar det fullständiga installationspaketet …"
  exec sh -c 'curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sh'
fi

echo "Installerar TrainMeet Server …"
apt-get update
export DEBIAN_FRONTEND=noninteractive
apt-get install -y avahi-daemon avahi-utils mosquitto python3 python3-venv

if ! id trainmeet-server >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin trainmeet-server
fi

install -d -m 0755 "$INSTALL_DIR"
cp -R "$SERVER_DIR/src" "$INSTALL_DIR/"
printf '%s\n' "${TRAINMEET_INSTALL_VERSION:-main}" > "$INSTALL_DIR/VERSION"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --disable-pip-version-check --quiet 'paho-mqtt>=2.1,<3'
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server.conf" /etc/mosquitto/conf.d/trainmeet-server.conf
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server.service" /etc/systemd/system/trainmeet-server.service
install -m 0755 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server-update" /usr/local/sbin/trainmeet-server-update
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server-update@.service" /etc/systemd/system/trainmeet-server-update@.service
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/50-trainmeet-server-update.rules" /etc/polkit-1/rules.d/50-trainmeet-server-update.rules
install -m 0755 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server-browser" /usr/local/bin/trainmeet-server-browser
rm -f /etc/sudoers.d/trainmeet-server-update
install -d -o trainmeet-server -g trainmeet-server -m 0750 "$STATE_DIR"

BROWSER_ENABLED=false
if [ -z "$DESKTOP_USER" ] || [ "$DESKTOP_USER" = root ]; then
  DESKTOP_USER=$(getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 {print $1; exit}')
fi
if command -v labwc >/dev/null 2>&1 \
  && [ -n "${DESKTOP_USER:-}" ] \
  && id "$DESKTOP_USER" >/dev/null 2>&1; then
  DESKTOP_HOME=$(getent passwd "$DESKTOP_USER" | cut -d: -f6)
  apt-get install -y chromium curl util-linux
  AUTOSTART_DIR="$DESKTOP_HOME/.config/labwc"
  AUTOSTART_FILE="$AUTOSTART_DIR/autostart"
  install -d -o "$DESKTOP_USER" -g "$DESKTOP_USER" -m 0755 "$AUTOSTART_DIR"
  touch "$AUTOSTART_FILE"
  if ! grep -q 'trainmeet-server-browser' "$AUTOSTART_FILE"; then
    printf '\n# TrainMeet Server\n/usr/local/bin/trainmeet-server-browser &\n' >> "$AUTOSTART_FILE"
  fi
  chown "$DESKTOP_USER:$DESKTOP_USER" "$AUTOSTART_FILE"
  chmod 0644 "$AUTOSTART_FILE"
  if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR=$(runuser -u "$DESKTOP_USER" -- xdg-user-dir DESKTOP 2>/dev/null || true)
  fi
  DESKTOP_DIR=${DESKTOP_DIR:-$DESKTOP_HOME/Desktop}
  install -d -o "$DESKTOP_USER" -g "$DESKTOP_USER" -m 0755 "$DESKTOP_DIR"
  install -o "$DESKTOP_USER" -g "$DESKTOP_USER" -m 0755 \
    "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server.desktop" \
    "$DESKTOP_DIR/Starta-TrainMeet-Server.desktop"
  systemctl set-default graphical.target
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_wayland W2 || true
    raspi-config nonint do_boot_behaviour B4 || true
    raspi-config nonint do_boot_wait 0 || true
    raspi-config nonint do_blanking 1 || true
  fi
  BROWSER_ENABLED=true
fi

systemctl daemon-reload
systemctl enable --now avahi-daemon.service
systemctl enable --now mosquitto.service
systemctl restart mosquitto.service
systemctl enable --now trainmeet-server.service

sleep 2
if ! systemctl is-active --quiet trainmeet-server.service; then
  echo "TrainMeet Server kunde inte starta. Visa loggen med:"
  echo "  journalctl -u trainmeet-server.service -n 50"
  exit 1
fi

PI_ADDRESS=$(hostname -I | awk '{print $1}')
CONNECTION_CODE=$(tr -d '[:space:]' < "$STATE_DIR/connection-code.txt")
echo
echo "TrainMeet Server är installerad och startar automatiskt."
echo "Öppna: http://${PI_ADDRESS}:8787"
echo "Anslutningskod: ${CONNECTION_CODE}"
if [ "$BROWSER_ENABLED" = true ]; then
  echo "Chromium öppnar TrainMeet Server automatiskt efter nästa omstart."
  echo "Genvägen 'Starta TrainMeet Server' finns också på skrivbordet."
else
  echo "Ingen Raspberry Pi Desktop hittades; servern körs utan lokal webbläsare."
fi
