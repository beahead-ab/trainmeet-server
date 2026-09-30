#!/bin/bash
# Startar servern mot en separat mosquitto, som på Pi:n (--external-broker).
#   RIG_DIR=/tmp/rig tools/tmbox-rig/start.sh [timed]
# "timed" tar tid på gatewayens arbete till $RIG_DIR/timing.jsonl, och
# STALL_AT/STALL_SECONDS lägger in en konstgjord spärr på arbetstråden.
set -e
RIG_DIR=${RIG_DIR:?sätt RIG_DIR}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
mkdir -p "$RIG_DIR/state"
pgrep -f "[m]osquitto -p 18830" >/dev/null || (mosquitto -p 18830 > "$RIG_DIR/mosq.log" 2>&1 &)
cd "$ROOT"
ARGS="--external-broker --bind 127.0.0.1 --http-port 18787 --mqtt-port 18830 --state-dir $RIG_DIR/state --gateway-id ${GATEWAY_ID:-trainmeet}"
if [ "$1" = timed ]; then
  PYTHONPATH=src:$HERE setsid nohup python3 "$HERE/timed_server.py" "$RIG_DIR/timing.jsonl" $ARGS > "$RIG_DIR/server.log" 2>&1 < /dev/null &
else
  PYTHONPATH=src setsid nohup python3 -m tmbox_gateway.local_server $ARGS > "$RIG_DIR/server.log" 2>&1 < /dev/null &
fi
for i in $(seq 1 100); do curl -s -o /dev/null http://127.0.0.1:18787/v1/info && exit 0; sleep 0.1; done
echo "servern kom inte upp"; tail -20 "$RIG_DIR/server.log"; exit 1
