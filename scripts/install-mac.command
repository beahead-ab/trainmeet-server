#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
SERVER_DIR=${SCRIPT_DIR:h}
APP_DIR="$HOME/Library/Application Support/TrainMeet Server"
INSTALL_DIR="$APP_DIR/server"
STATE_DIR="$APP_DIR/state"
LOG_DIR="$APP_DIR/logs"
VENV_DIR="$APP_DIR/venv"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/com.beahead.trainmeet-server.plist"
LABEL="com.beahead.trainmeet-server"
LEGACY_LABEL="com.beahead.trainmeet-tambox"
LEGACY_PLIST="$LAUNCH_AGENTS_DIR/$LEGACY_LABEL.plist"
LEGACY_DISABLED_PLIST="$LEGACY_PLIST.disabled"

if ! command -v mosquitto >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installerar Mosquitto med Homebrew …"
    brew install mosquitto
  else
    echo "Mosquitto och Homebrew saknas."
    echo "Installera Homebrew från https://brew.sh och kör installationen igen."
    exit 1
  fi
fi

mkdir -p "$INSTALL_DIR" "$STATE_DIR" "$LOG_DIR" "$LAUNCH_AGENTS_DIR"
# Replace rather than merge, so modules removed upstream do not linger in an
# updated installation.
rm -rf "$INSTALL_DIR/src"
ditto "$SERVER_DIR/src" "$INSTALL_DIR/src"
cp "$SERVER_DIR/pyproject.toml" "$INSTALL_DIR/pyproject.toml"
cp "$SERVER_DIR/README.md" "$INSTALL_DIR/README.md"
# Two files: VERSION is the SemVer a person reads and comes from the repo, so
# it cannot drift from what the code says about itself. BUILD is the commit.
if [ -f "$SERVER_DIR/VERSION" ]; then
  cp "$SERVER_DIR/VERSION" "$INSTALL_DIR/VERSION"
else
  : > "$INSTALL_DIR/VERSION"
fi
printf '%s\n' "${TRAINMEET_INSTALL_BUILD:-${TRAINMEET_INSTALL_VERSION:-}}" > "$INSTALL_DIR/BUILD"

# The update button runs the updater from the installed copy, so it has to be
# reinstalled by every update as well.
mkdir -p "$INSTALL_DIR/scripts"
install -m 0755 "$SERVER_DIR/packaging/mac/trainmeet-server-update" \
  "$INSTALL_DIR/scripts/trainmeet-server-update"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check "${INSTALL_DIR}[mqtt]"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV_DIR/bin/python</string>
    <string>-m</string>
    <string>tmbox_gateway.local_server</string>
    <string>--bind</string>
    <string>0.0.0.0</string>
    <string>--state-dir</string>
    <string>$STATE_DIR</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$INSTALL_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/server.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/server-error.log</string>
</dict>
</plist>
PLIST

chmod 600 "$PLIST_PATH"

# Version 0.6 uses a new service name and a separate state directory. Stop the
# pre-release TMBox service so it cannot compete for HTTP/MQTT ports. Its
# complete Application Support directory is intentionally left untouched.
launchctl bootout "gui/$(id -u)/$LEGACY_LABEL" >/dev/null 2>&1 || true
if [[ -f "$LEGACY_PLIST" ]]; then
  mv "$LEGACY_PLIST" "$LEGACY_DISABLED_PLIST"
fi

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

# Give the service a real chance to come up before declaring failure: an update
# treats a non-zero exit here as the signal to roll back.
for attempt in {1..30}; do
  if curl --silent --fail --max-time 2 http://127.0.0.1:8787/v1/info >/dev/null 2>&1; then
    break
  fi
  if [[ $attempt -eq 30 ]]; then
    echo "Tjänsten startade inte. Loggen finns i: $LOG_DIR/server-error.log"
    exit 1
  fi
  sleep 1
done

LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "din-macs-ip")
CONNECTION_CODE=$(tr -cd '0-9' < "$STATE_DIR/connection-code.txt")
echo
echo "TrainMeet Server är installerad och startar automatiskt."
echo "På denna Mac: http://127.0.0.1:8787"
echo "Från iPhone:  http://${LOCAL_IP}:8787"
echo "Anslutningskod: ${CONNECTION_CODE}"
if [[ -d "$HOME/Library/Application Support/TrainMeet TMBox" ]]; then
  echo "Äldre testdata är bevarad i: $HOME/Library/Application Support/TrainMeet TMBox"
fi
