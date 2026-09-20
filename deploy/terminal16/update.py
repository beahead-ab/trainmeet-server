#!/usr/bin/env python3
"""Update only the isolated lab, retaining its previous release for rollback."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

from deploy import ROOT, UNIT, CADDY, CLOUD_SITE, health, run, runtime_identity


def update(payload, sha, previous):
    if not all(re.fullmatch("[a-f0-9]{40}", value) for value in (sha, previous)):
        raise RuntimeError("Pinned current and previous revisions required")
    metadata = ROOT / "deployment.json"
    old = json.loads(metadata.read_text())
    old_release = ROOT / "releases" / previous
    if old["sha"] != previous or old["release"] != str(old_release):
        raise RuntimeError("Installed version changed")
    original = UNIT.read_text()
    if original.count(str(old_release)) != 2:
        raise RuntimeError("Unexpected service definition")
    if (payload / "REVISION").read_text().strip() != sha:
        raise RuntimeError("Payload revision mismatch")
    manifest = json.loads((payload / "MANIFEST.json").read_text())
    actual = {str(p.relative_to(payload)) for p in payload.rglob("*") if p.is_file()} - {"MANIFEST.json", "REVISION"}
    if actual != set(manifest):
        raise RuntimeError("Unexpected payload files")
    for relative, digest in manifest.items():
        file = payload / relative
        if (Path(relative).is_absolute() or ".." in Path(relative).parts or file.is_symlink()
                or not file.resolve().is_relative_to(payload.resolve())
                or hashlib.sha256(file.read_bytes()).hexdigest() != digest):
            raise RuntimeError("Payload checksum mismatch")
    before = runtime_identity()
    proxy = CADDY.read_bytes(), CLOUD_SITE.read_bytes()
    release = ROOT / "releases" / sha
    shutil.copytree(payload, release)
    release.chmod(0o755)
    for path in release.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    backup = Path("/var/backups/trainmeet") / ("tmbox-lab-update-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(mode=0o700)
    shutil.copy2(UNIT, backup / UNIT.name)
    shutil.copy2(metadata, backup / "deployment.json")
    candidate = original.replace(str(old_release), str(release))
    if UNIT.read_text() != original:
        raise RuntimeError("Service changed during preparation")
    try:
        UNIT.write_text(candidate)
        run("systemd-analyze", "verify", str(UNIT))
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "trainmeet-tmbox-lab")
        ready = False
        for _ in range(15):
            try:
                ready = health(8797, "/tmbox-lab/healthz", "server.trainmeet.app").get("service") == "tmbox-lab"
            except OSError:
                pass
            if ready:
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError("Lab failed to start")
        public = json.loads(run("curl", "--fail", "--silent", "--show-error", "--max-time", "10",
                                "--resolve", "server.trainmeet.app:443:127.0.0.1",
                                "https://server.trainmeet.app/tmbox-lab/healthz"))
        if public.get("service") != "tmbox-lab" or runtime_identity() != before or proxy != (CADDY.read_bytes(), CLOUD_SITE.read_bytes()):
            raise RuntimeError("Service health/baseline mismatch")
        result = {**old, "sha": sha, "release": str(release), "backup": str(backup), "existing_services": before}
        metadata.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    except Exception:
        if UNIT.read_text() == candidate:
            UNIT.write_text(original)
            run("systemctl", "daemon-reload")
            run("systemctl", "restart", "trainmeet-tmbox-lab")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--previous", required=True)
    args = parser.parse_args()
    with open("/var/lock/trainmeet-tmbox-lab-install.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        update(args.payload.resolve(), args.sha, args.previous)
