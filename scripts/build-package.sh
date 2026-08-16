#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GATEWAY_DIR=$(dirname "$SCRIPT_DIR")
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$GATEWAY_DIR/pyproject.toml")
DIST_DIR="$GATEWAY_DIR/dist"
ARCHIVE="$DIST_DIR/trainmeet-server-${VERSION}.tar.gz"
LATEST_ARCHIVE="$DIST_DIR/trainmeet-server.tar.gz"

mkdir -p "$DIST_DIR"
cd "$GATEWAY_DIR"
tar \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.egg-info' \
  --exclude='.pio' \
  -czf "$ARCHIVE" \
    README.md .dockerignore Dockerfile compose.yaml deploy \
    install.sh pyproject.toml src scripts packaging

cp "$ARCHIVE" "$LATEST_ARCHIVE"

echo "$ARCHIVE"
