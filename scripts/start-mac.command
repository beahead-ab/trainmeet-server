#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
GATEWAY_DIR=${SCRIPT_DIR:h}
cd "$GATEWAY_DIR"

if ! command -v mosquitto >/dev/null 2>&1; then
  echo "Mosquitto saknas. Installera det en gång med:"
  echo "  brew install mosquitto"
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Förbereder Tambox-servern första gången …"
  python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import paho.mqtt.client' >/dev/null 2>&1; then
  echo "Installerar den lokala MQTT-komponenten …"
  .venv/bin/pip install 'paho-mqtt>=2.1,<3'
fi

export PYTHONPATH="$GATEWAY_DIR/src"
exec .venv/bin/python -m tambox_gateway.local_server --bind 0.0.0.0 --state-dir data/local
