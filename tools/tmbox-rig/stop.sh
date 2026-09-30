#!/bin/bash
# Kör som eget kommando: mönstret matchar annars skalet som anropar det.
pkill -f "[t]mbox_gateway.local_server" ; pkill -f "[t]imed_server.py"
for i in $(seq 1 50); do pgrep -f "[t]mbox_gateway.local_server|[t]imed_server.py" >/dev/null || exit 0; sleep 0.1; done
pkill -9 -f "[t]mbox_gateway.local_server|[t]imed_server.py"
