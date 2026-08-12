from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
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
        "stable": "https://api.github.com/repos/beahead-ab/trainmeet-server/releases/latest",
        "test": "https://api.github.com/repos/beahead-ab/trainmeet-server/commits/main",
    }
    if channel not in urls:
        raise SoftwareUpdateError("Okänd uppdateringskanal")
    request = Request(urls[channel], headers={"Accept": "application/vnd.github+json", "User-Agent": "TrainMeet-Server/0.7"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise SoftwareUpdateError("GitHub kunde inte nås") from error
    if channel == "stable":
        return {"version": str(payload["tag_name"]), "published_at": str(payload.get("published_at") or "")}
    return {"version": str(payload["sha"])[:8], "published_at": str(payload.get("commit", {}).get("author", {}).get("date") or "")}


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
