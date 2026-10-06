"""Render startup: provision the first owner before opening HTTP, then run MQTT locally."""
import os
from pathlib import Path
import subprocess
import sys

from tmbox_gateway.identity import AdminAccessError, IdentityStore
from tmbox_gateway.local_server import _database_path, _wait_for_port, main


def provision_owner(state_dir: Path) -> None:
    identities = IdentityStore(_database_path(state_dir))
    try:
        if identities.admin_access_summary()["password_configured"]:
            return
        name = os.environ.get("TRAINMEET_BOOTSTRAP_ADMIN_NAME", "")
        email = os.environ.get("TRAINMEET_BOOTSTRAP_ADMIN_EMAIL", "")
        password = os.environ.get("TRAINMEET_BOOTSTRAP_ADMIN_PASSWORD", "")
        if not all((name, email, password)):
            raise SystemExit("Set TRAINMEET_BOOTSTRAP_ADMIN_NAME, TRAINMEET_BOOTSTRAP_ADMIN_EMAIL and TRAINMEET_BOOTSTRAP_ADMIN_PASSWORD before the first deploy.")
        try:
            identities.create_first_owner(name, email, password)
        except AdminAccessError:
            raise SystemExit("Invalid bootstrap admin values. Use a valid email, display name and password of 8–256 characters.") from None
    finally:
        identities.close()
        # These values are used only once and do not reset an existing account.
        for key in ("TRAINMEET_BOOTSTRAP_ADMIN_NAME", "TRAINMEET_BOOTSTRAP_ADMIN_EMAIL", "TRAINMEET_BOOTSTRAP_ADMIN_PASSWORD"):
            os.environ.pop(key, None)


def run() -> None:
    state_dir = Path(os.environ.get("TRAINMEET_STATE_DIR", "/var/lib/trainmeet-server"))
    state_dir.mkdir(parents=True, exist_ok=True)
    provision_owner(state_dir)
    origin = os.environ.get("TRAINMEET_PUBLIC_CLIENT_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL", "")
    if not origin.startswith("https://"):
        raise SystemExit("Set TRAINMEET_PUBLIC_CLIENT_ORIGIN to the public HTTPS service URL.")
    broker_dir = state_dir / "mosquitto"
    broker_dir.mkdir(parents=True, exist_ok=True)
    config_path = broker_dir / "mosquitto.conf"
    config_path.write_text(f"listener 1883 127.0.0.1\nallow_anonymous true\npersistence true\npersistence_location {broker_dir}/\nautosave_interval 10\nlog_dest stdout\nlog_type warning\nlog_type error\n", encoding="utf-8")
    broker = subprocess.Popen(["mosquitto", "-c", str(config_path)])
    try:
        if not _wait_for_port("127.0.0.1", 1883, 10):
            raise SystemExit("The local MQTT broker did not start.")
        sys.argv = ["trainmeet-server", "--external-broker", "--mqtt-host", "127.0.0.1", "--bind", "0.0.0.0", "--http-port", os.environ.get("PORT", "10000"), "--state-dir", str(state_dir), "--force-external-auth", "--public-client-origin", origin.rstrip("/"), "--gateway-id", "trainmeet-render"]
        main()
    finally:
        broker.terminate()
        try:
            broker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.wait()


if __name__ == "__main__":
    run()
