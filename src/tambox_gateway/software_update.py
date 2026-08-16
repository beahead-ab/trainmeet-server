from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


class SoftwareUpdateError(RuntimeError):
    pass


LINUX_INSTALL_DIR = Path("/opt/trainmeet-server")
LINUX_UPDATER = Path("/usr/local/sbin/trainmeet-server-update")


def mac_install_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "TrainMeet Server" / "server"


@dataclass(frozen=True)
class UpdateBackend:
    """How this installation replaces and restarts itself."""

    kind: str
    version_file: Path
    updater: Path


def update_backend() -> UpdateBackend | None:
    """Return the updater for the running installation, or None if unmanaged.

    Docker and Kubernetes deliberately have no backend. A container cannot
    replace its own image without being handed the host's Docker socket, so
    those installations keep updating through a new image instead.
    """
    if sys.platform.startswith("linux") and LINUX_UPDATER.exists():
        # The Pi runs the server as its own unprivileged user. Updating is a
        # root-owned systemd unit that the web server may only ask to start.
        return UpdateBackend("systemd", LINUX_INSTALL_DIR / "VERSION", LINUX_UPDATER)
    if sys.platform == "darwin":
        install_dir = mac_install_dir()
        updater = install_dir / "scripts" / "trainmeet-server-update"
        if updater.exists():
            # Everything belongs to the logged-in user, so the update runs
            # unprivileged and needs no helper service at all.
            return UpdateBackend("launchd", install_dir / "VERSION", updater)
    return None


def supports_updates() -> bool:
    return update_backend() is not None


def installed_version() -> str:
    backend = update_backend()
    version_file = backend.version_file if backend else LINUX_INSTALL_DIR / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "okänd"
    except OSError:
        return "utvecklingsversion"


def latest_version(channel: str) -> dict[str, str]:
    urls = {
        # GitHubs vanliga webb- och patchadresser saknar API:ts anonyma
        # gräns på 60 anrop/timme, som ofta delas av en hel driftleverantör.
        "stable": "https://github.com/beahead-ab/trainmeet-server/releases/latest",
        "test": "https://github.com/beahead-ab/trainmeet-server/commit/main.patch",
    }
    if channel not in urls:
        raise SoftwareUpdateError("Okänd uppdateringskanal")
    request = Request(urls[channel], headers={"Accept": "text/html, text/plain", "User-Agent": "TrainMeet-Server/0.7"})
    try:
        with urlopen(request, timeout=10) as response:
            final_url = response.geturl()
            payload = response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise SoftwareUpdateError("GitHub kunde inte nås") from error
    if channel == "stable":
        path = unquote(urlparse(final_url).path).rstrip("/")
        marker = "/releases/tag/"
        if marker not in path:
            raise SoftwareUpdateError("Ingen stabil TrainMeet-version är publicerad")
        return {"version": path.split(marker, 1)[1], "published_at": ""}
    match = re.match(rb"From ([0-9a-f]{40}) ", payload)
    if match is None:
        raise SoftwareUpdateError("GitHub lämnade ingen giltig versionsinformation")
    return {"version": match.group(1).decode("ascii")[:8], "published_at": ""}


def read_update_status(state_dir: Path) -> dict[str, str]:
    try:
        value = json.loads((state_dir / "update-status.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"status": "idle"}
    except (OSError, json.JSONDecodeError):
        return {"status": "idle", "message": "Ingen uppdatering pågår"}


def start_update(channel: str) -> None:
    if channel not in {"stable", "test"}:
        raise SoftwareUpdateError("Okänd uppdateringskanal")
    backend = update_backend()
    if backend is None:
        raise SoftwareUpdateError("Den här installationen uppdateras inte från webbgränssnittet")
    try:
        if backend.kind == "systemd":
            subprocess.run(
                ["/bin/systemctl", "start", "--no-block", f"trainmeet-server-update@{channel}.service"],
                check=True,
                timeout=5,
            )
        else:
            # The updater restarts the service and therefore kills this very
            # process. Detach it so the update survives its own parent.
            subprocess.Popen(
                [str(backend.updater), channel],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise SoftwareUpdateError("Uppdateringstjänsten kunde inte startas") from error
