#!/bin/sh
set -eu

REPOSITORY="beahead-ab/trainmeet-server"
SOURCE_URL="https://github.com/${REPOSITORY}/archive/refs/heads/main.tar.gz"
TEMP_DIR=$(mktemp -d)
ARCHIVE_PATH="$TEMP_DIR/trainmeet-server.tar.gz"

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT INT TERM

case "$(uname -s)" in
  Darwin)
    INSTALLER="scripts/install-mac.command"
    # The Mac installation is per user: it writes to Application Support and
    # registers a launchd agent in the logged-in user's own domain. Running it
    # through sudo would install everything for root instead.
    if [ "$(id -u)" -eq 0 ]; then
      echo "Kör installationen utan sudo på Mac:"
      echo "  curl -fsSL https://raw.githubusercontent.com/${REPOSITORY}/main/install.sh | sh"
      exit 1
    fi
    ;;
  *)
    INSTALLER="scripts/install-raspberry-pi.sh"
    ;;
esac

echo "Hämtar TrainMeet Server …"
if ! curl -fsSL "$SOURCE_URL" -o "$ARCHIVE_PATH"; then
  echo
  echo "Kunde inte hämta TrainMeet Server från GitHub."
  echo "Kontrollera internetanslutningen:"
  echo "  https://github.com/${REPOSITORY}"
  exit 1
fi

tar -xzf "$ARCHIVE_PATH" -C "$TEMP_DIR"
if [ -f "$TEMP_DIR/$INSTALLER" ]; then
  SOURCE_DIR="$TEMP_DIR"
else
  SOURCE_DIR=$(find "$TEMP_DIR" -maxdepth 1 -type d -name 'trainmeet-server-*' | head -n 1)
fi

if [ -z "${SOURCE_DIR:-}" ] || [ ! -f "$SOURCE_DIR/$INSTALLER" ]; then
  echo "Installationspaketet saknar installationen för den här plattformen."
  exit 1
fi

chmod +x "$SOURCE_DIR/$INSTALLER"
exec "$SOURCE_DIR/$INSTALLER"
