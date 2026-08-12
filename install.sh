#!/bin/sh
set -eu

REPOSITORY="beahead-ab/trainmeet-server"
ARCHIVE_URL="https://github.com/${REPOSITORY}/releases/latest/download/trainmeet-server.tar.gz"
SOURCE_URL="https://github.com/${REPOSITORY}/archive/refs/heads/main.tar.gz"
TEMP_DIR=$(mktemp -d)
ARCHIVE_PATH="$TEMP_DIR/trainmeet-server.tar.gz"

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT INT TERM

echo "Hämtar TrainMeet Server …"
if ! curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE_PATH"; then
  echo "Ingen paketerad release hittades; provar senaste main."
  if ! curl -fsSL "$SOURCE_URL" -o "$ARCHIVE_PATH"; then
    echo
    echo "Kunde inte hämta TrainMeet Server från GitHub."
    echo "Kontrollera internetanslutningen och att repot är publikt:"
    echo "  https://github.com/${REPOSITORY}"
    exit 1
  fi
fi

tar -xzf "$ARCHIVE_PATH" -C "$TEMP_DIR"
if [ -x "$TEMP_DIR/scripts/install-raspberry-pi.sh" ]; then
  SOURCE_DIR="$TEMP_DIR"
else
  SOURCE_DIR=$(find "$TEMP_DIR" -maxdepth 1 -type d -name 'trainmeet-server-*' | head -n 1)
fi

if [ -z "${SOURCE_DIR:-}" ] || [ ! -x "$SOURCE_DIR/scripts/install-raspberry-pi.sh" ]; then
  echo "Installationspaketet saknar Raspberry Pi-installationen."
  exit 1
fi

exec "$SOURCE_DIR/scripts/install-raspberry-pi.sh"
