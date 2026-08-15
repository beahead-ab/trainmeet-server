from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


class SoftwareUpdateError(RuntimeError):
    pass


def installed_version() -> str:
    try:
        return Path("/opt/trainmeet-server/VERSION").read_text(encoding="utf-8").strip() or "okänd"
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
    try:
        subprocess.run(["/bin/systemctl", "start", "--no-block", f"trainmeet-server-update@{channel}.service"], check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise SoftwareUpdateError("Uppdateringstjänsten kunde inte startas") from error
