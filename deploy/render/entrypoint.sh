#!/bin/sh
set -eu
if [ -n "${RENDER_GIT_COMMIT:-}" ]; then
    printf '%s\n' "$RENDER_GIT_COMMIT" > /opt/trainmeet-server/BUILD
fi
state_dir="${TRAINMEET_STATE_DIR:-/var/lib/trainmeet-server}"
mkdir -p "$state_dir" "$state_dir/mosquitto"
# Render mounts the persistent disk at runtime, after the image is built.
chown trainmeet:trainmeet "$state_dir" "$state_dir/mosquitto"
exec gosu trainmeet python /app/deploy/render/start.py
