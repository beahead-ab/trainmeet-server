from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .update_contract import normalise
from .version import build_identifier, product_version, user_agent


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
    """The SemVer a person should see. See `version.py` for where it lives."""
    backend = update_backend()
    install_root = backend.version_file.parent if backend else LINUX_INSTALL_DIR
    return product_version(install_root)


def installed_build() -> str:
    """The git commit this code was built from - technical information."""
    backend = update_backend()
    install_root = backend.version_file.parent if backend else LINUX_INSTALL_DIR
    return build_identifier(install_root)


def latest_version() -> dict[str, str]:
    """What is on `main` right now.

    GitHub can tell us the commit cheaply; the SemVer lives in the repo's
    VERSION file, so both are fetched. A build that differs is what decides
    whether an update exists - the version number can stay put across several
    fixes, and an operator still wants to be able to take them.
    """
    # GitHubs vanliga patchadress saknar API:ts anonyma gräns på 60 anrop/timme,
    # som ofta delas av en hel driftleverantör.
    url = "https://github.com/beahead-ab/trainmeet-server/commit/main.patch"
    request = Request(url, headers={"Accept": "text/plain", "User-Agent": user_agent()})
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise SoftwareUpdateError("GitHub kunde inte nås") from error
    match = re.match(rb"From ([0-9a-f]{40}) ", payload)
    if match is None:
        raise SoftwareUpdateError("GitHub lämnade ingen giltig versionsinformation")
    build = match.group(1).decode("ascii")[:8]
    return {"version": _latest_product_version(), "build": build, "published_at": ""}


def _latest_product_version() -> str:
    """`main`'s VERSION file, read raw so it needs no API token."""
    url = "https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/VERSION"
    request = Request(url, headers={"Accept": "text/plain", "User-Agent": user_agent()})
    try:
        with urlopen(request, timeout=10) as response:
            value = response.read().decode("utf-8").strip()
    except (HTTPError, URLError, TimeoutError):
        # Not fatal: the build tells us whether an update exists. A missing
        # version number makes the offer vaguer, not wrong.
        return ""
    return value


def read_update_status(state_dir: Path) -> dict[str, Any]:
    """What the updater script last wrote, in the shared contract's shape."""
    try:
        value = json.loads((state_dir / "update-status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {"status": "idle", "message": "Ingen uppdatering pågår"}
    return normalise(value if isinstance(value, dict) else None)


def start_update() -> None:
    backend = update_backend()
    if backend is None:
        raise SoftwareUpdateError("Den här installationen uppdateras inte från webbgränssnittet")
    try:
        if backend.kind == "systemd":
            subprocess.run(
                ["/bin/systemctl", "start", "--no-block", "trainmeet-server-update.service"],
                check=True,
                timeout=5,
            )
        else:
            # The updater restarts the service and therefore kills this very
            # process. Detach it so the update survives its own parent.
            subprocess.Popen(
                [str(backend.updater)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise SoftwareUpdateError("Uppdateringstjänsten kunde inte startas") from error
