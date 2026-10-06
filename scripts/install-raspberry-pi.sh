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

# Run by the updater (TRAINMEET_INSTALL_BUILD is how it says so), keep what
# this script prints. The updater's status file only knows *that* installing
# failed; this log says why, and Programuppdatering shows its last lines under
# Teknisk information. It is written by the freshly downloaded source, so it
# reaches an installation whose updater script is still the old one - which
# is every installation on the first update after this change.
INSTALL_LOG="$STATE_DIR/update-install.log"
if [ -n "${TRAINMEET_INSTALL_BUILD:-}" ] && [ -z "${TRAINMEET_INSTALL_LOGGED:-}" ]; then
  TRAINMEET_INSTALL_LOGGED=1
  export TRAINMEET_INSTALL_LOGGED
  install -d -m 0750 "$STATE_DIR"
  RESULT_FILE=$(mktemp)
  # POSIX sh has no pipefail: the exit status travels through a file so the
  # updater sees the installer's, not tee's.
  {
    printf 'TrainMeet Server: installerar build %s, %s\n' \
      "$TRAINMEET_INSTALL_BUILD" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "$0" "$@" 2>&1 && echo 0 > "$RESULT_FILE" || echo $? > "$RESULT_FILE"
  } | tee "$INSTALL_LOG"
  RESULT=$(cat "$RESULT_FILE")
  rm -f "$RESULT_FILE"
  # The server reads the log, so it has to own it - also right after the
  # install that created the user.
  chown trainmeet-server:trainmeet-server "$INSTALL_LOG" 2>/dev/null || true
  chmod 0640 "$INSTALL_LOG"
  exit "${RESULT:-1}"
fi

echo "Installerar TrainMeet Server …"
export DEBIAN_FRONTEND=noninteractive
# apt only when something is actually missing. On an update every package is
# already there, and `apt-get update` is then a network round trip to a
# mirror that can be slow, down or locked by unattended-upgrades - each of
# which failed the whole update without saying so. A lock timeout covers the
# case where it does have to run while the system's own apt is busy.
MISSING=""
for PACKAGE in avahi-daemon avahi-utils mosquitto python3 python3-venv; do
  dpkg-query -W -f '${Status}' "$PACKAGE" 2>/dev/null | grep -q 'install ok installed' \
    || MISSING="$MISSING $PACKAGE"
done
if [ -n "$MISSING" ]; then
  echo "Installerar systempaket:$MISSING"
  apt-get -o DPkg::Lock::Timeout=120 update
  # shellcheck disable=SC2086
  apt-get -o DPkg::Lock::Timeout=120 install -y $MISSING
fi

if ! id trainmeet-server >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin trainmeet-server
fi

install -d -m 0755 "$INSTALL_DIR"
# Clear before copying. `cp -R` merges into what is already there, so a
# package that has been renamed leaves its old directory behind and both end
# up importable - which is how a box ran tambox_gateway for a day after
# tmbox_gateway had been installed over it. Only code lives here; the
# database is in STATE_DIR and is never touched by this.
rm -rf "$INSTALL_DIR/src"
cp -R "$SERVER_DIR/src" "$INSTALL_DIR/"
# Two files, because they answer two questions. VERSION is the SemVer a
# person reads and comes from the repo, so it cannot drift from what the code
# says about itself. BUILD is the commit, which identifies the exact tree.
if [ -f "$SERVER_DIR/VERSION" ]; then
  install -m 0644 "$SERVER_DIR/VERSION" "$INSTALL_DIR/VERSION"
  # A second copy beside the code. The updater script from before this change
  # overwrites $INSTALL_DIR/VERSION with the git sha right after we run, and
  # it knows nothing about this path - so the very first update already shows
  # a real version number instead of "okänd".
  install -m 0644 "$SERVER_DIR/VERSION" "$INSTALL_DIR/src/tmbox_gateway/VERSION"
else
  : > "$INSTALL_DIR/VERSION"
fi
printf '%s\n' "${TRAINMEET_INSTALL_BUILD:-${TRAINMEET_INSTALL_VERSION:-}}" > "$INSTALL_DIR/BUILD"
chmod 0644 "$INSTALL_DIR/VERSION" "$INSTALL_DIR/BUILD"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --disable-pip-version-check --quiet 'paho-mqtt>=2.1,<3'
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server.conf" /etc/mosquitto/conf.d/trainmeet-server.conf
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server.service" /etc/systemd/system/trainmeet-server.service

# A drop-in overrides the unit we just wrote, and we never install one - so an
# operator's own file, holding real settings like the bind address and the
# external broker, silently kept starting the package we renamed away from.
# Rewrite our own module path inside it rather than deleting someone's config.
DROPIN_DIR=/etc/systemd/system/trainmeet-server.service.d
if [ -d "$DROPIN_DIR" ]; then
  for DROPIN in "$DROPIN_DIR"/*.conf; do
    [ -f "$DROPIN" ] || continue
    if grep -q 'tambox_gateway\.' "$DROPIN"; then
      sed -i 's/tambox_gateway\./tmbox_gateway./g' "$DROPIN"
      echo "Rättade paketnamnet i $DROPIN (tambox_gateway -> tmbox_gateway)."
    fi
  done
fi
install -m 0755 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server-update" /usr/local/sbin/trainmeet-server-update
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server-update.service" /etc/systemd/system/trainmeet-server-update.service
rm -f /etc/systemd/system/trainmeet-server-update@.service
install -m 0644 "$SERVER_DIR/packaging/raspberry-pi/50-trainmeet-server-update.rules" /etc/polkit-1/rules.d/50-trainmeet-server-update.rules
install -m 0755 "$SERVER_DIR/packaging/raspberry-pi/trainmeet-server-browser" /usr/local/bin/trainmeet-server-browser
rm -f /etc/sudoers.d/trainmeet-server-update
install -d -o trainmeet-server -g trainmeet-server -m 0750 "$STATE_DIR"
# The server writes its own safety copy here before a meet reset (#129). The
# updater creates the folder as root to back up before installing, so hand the
# folder and what it holds to the server, also on an installation updated before.
install -d -o trainmeet-server -g trainmeet-server -m 0750 "$STATE_DIR/backups"
chown -R trainmeet-server:trainmeet-server "$STATE_DIR/backups"

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
# `enable --now` starts the service only when it is not already running, so
# on an update it does nothing at all and the old process keeps serving. That
# is why an update could report success while the running code was stale.
# mosquitto above has always had its explicit restart; this one was missed.
systemctl enable trainmeet-server.service
systemctl restart trainmeet-server.service

# Wait for the server to answer, not for two seconds. `Restart=always` means a
# server that crashes on start is "activating" again two seconds later, so a
# single `is-active` after a fixed sleep said yes to a crashing server and no
# to a slow one. What settles it is the health endpoint answering, and the
# port comes from the effective ExecStart because a drop-in may override it.
RUNNING_EXEC=$(systemctl show trainmeet-server.service -p ExecStart --value)
HEALTH_PORT=$(printf '%s' "$RUNNING_EXEC" | sed -n 's/.*--http-port \([0-9]\{1,5\}\).*/\1/p')
[ -n "$HEALTH_PORT" ] || HEALTH_PORT=8787
# A server bound to one address answers only there; the usual 0.0.0.0 answers
# on loopback like everything else.
HEALTH_HOST=$(printf '%s' "$RUNNING_EXEC" | sed -n 's/.*--bind \([^ ]*\).*/\1/p')
case "$HEALTH_HOST" in ""|0.0.0.0|::|'[::]') HEALTH_HOST=127.0.0.1 ;; esac
HEALTH_URL="http://$HEALTH_HOST:$HEALTH_PORT/healthz"
healthy() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1
  else
    "$VENV_DIR/bin/python" -c "import sys, urllib.request
sys.exit(0 if urllib.request.urlopen('$HEALTH_URL', timeout=3).status == 200 else 1)" \
      >/dev/null 2>&1
  fi
}
ATTEMPT=0
until healthy; do
  ATTEMPT=$((ATTEMPT + 1))
  if [ "$ATTEMPT" -ge 30 ] || systemctl is-failed --quiet trainmeet-server.service; then
    echo "TrainMeet Server svarar inte efter starten. Så här ser tjänsten och dess logg ut:"
    systemctl status trainmeet-server.service --no-pager -l 2>&1 | head -n 15 || true
    journalctl -u trainmeet-server.service -n 40 --no-pager 2>&1 || true
    exit 1
  fi
  sleep 2
done

# A server that answers is not yet a server running the right code: a
# drop-in can start another package just as happily, so the effective
# command is checked too.
case "$RUNNING_EXEC" in
  *tmbox_gateway.local_server*) ;;
  *)
    echo "TrainMeet Server startade fel paket. Effektivt startkommando:"
    echo "  $RUNNING_EXEC"
    echo "Leta efter en åsidosättning under /etc/systemd/system/trainmeet-server.service.d/"
    exit 1
    ;;
esac

# Everything from here on is information. None of it may fail the install:
# the server is already running the new code, and a failure here would make
# the updater roll a working installation back over a missing file.
PI_ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}') || PI_ADDRESS=""
CONNECTION_CODE=""
if [ -r "$STATE_DIR/connection-code.txt" ]; then
  CONNECTION_CODE=$(tr -d '[:space:]' < "$STATE_DIR/connection-code.txt") || CONNECTION_CODE=""
fi
echo
echo "TrainMeet Server är installerad och startar automatiskt."
echo "Öppna: http://${PI_ADDRESS:-trainmeet.local}:${HEALTH_PORT}"
[ -z "$CONNECTION_CODE" ] || echo "Anslutningskod: ${CONNECTION_CODE}"
if [ "$BROWSER_ENABLED" = true ]; then
  echo "Chromium öppnar TrainMeet Server automatiskt efter nästa omstart."
  echo "Genvägen 'Starta TrainMeet Server' finns också på skrivbordet."
else
  echo "Ingen Raspberry Pi Desktop hittades; servern körs utan lokal webbläsare."
fi
