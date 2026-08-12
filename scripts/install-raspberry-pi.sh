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
rm -f /etc/sudoers.d/trainmeet-server-update
install -d -o trainmeet-server -g trainmeet-server -m 0750 "$STATE_DIR"

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
