#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
SERVER_DIR=${SCRIPT_DIR:h}
NATIVE_LABEL="com.beahead.trainmeet-server"
NATIVE_PLIST="$HOME/Library/LaunchAgents/$NATIVE_LABEL.plist"
NATIVE_DISABLED_PLIST="$NATIVE_PLIST.disabled"

if ! command -v colima >/dev/null 2>&1; then
  echo "Colima saknas. Installera först med: brew install colima docker docker-compose"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI saknas. Installera först med: brew install docker docker-compose"
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Docker Compose saknas. Installera med: brew install docker-compose"
  exit 1
fi

echo "Gör Docker till TrainMeet-miljö på denna Mac …"
launchctl bootout "gui/$(id -u)/$NATIVE_LABEL" >/dev/null 2>&1 || true
if [[ -f "$NATIVE_PLIST" ]]; then
  mv "$NATIVE_PLIST" "$NATIVE_DISABLED_PLIST"
fi

brew services start colima >/dev/null
if ! colima status >/dev/null 2>&1; then
  colima start
fi

cd "$SERVER_DIR"
"${COMPOSE[@]}" up -d --build --force-recreate

for attempt in {1..30}; do
  if curl --silent --fail --max-time 2 http://127.0.0.1:8787/v1/info >/dev/null; then
    break
  fi
  if [[ $attempt -eq 30 ]]; then
    echo "TrainMeet Server blev inte klar. Visa status med: ${COMPOSE[*]} ps"
    exit 1
  fi
  sleep 1
done

LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "din-macs-ip")
echo
echo "TrainMeet Docker är installerad och igång."
echo "På denna Mac: http://127.0.0.1:8787"
echo "Från iPhone:  http://${LOCAL_IP}:8787"
echo "MQTT:         ${LOCAL_IP}:1883"
echo "Colima och containrarna startar automatiskt efter inloggning."
