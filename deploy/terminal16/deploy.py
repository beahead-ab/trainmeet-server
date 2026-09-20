#!/usr/bin/env python3
"""First install of the isolated test bench; never restart Cloud/Server.

Run as root on the existing TrainMeet test host with a pinned git archive.
Only adds a loopback service and one route on server.trainmeet.app.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from urllib.request import Request, urlopen

ROOT = Path("/opt/trainmeet-tmbox-lab")
CADDY = Path("/etc/caddy/Caddyfile")
UNIT = Path("/etc/systemd/system/trainmeet-tmbox-lab.service")
CLOUD_SITE = Path("/etc/caddy/sites/trainmeet-cloud.caddy")
OLD_BLOCK = "server.trainmeet.app {\n    reverse_proxy 127.0.0.1:8787\n}"
NEW_BLOCK = """server.trainmeet.app {
    redir /tmbox-lab /tmbox-lab/ 308
    handle /tmbox-lab/* {
        reverse_proxy 127.0.0.1:8797 {
            flush_interval -1
        }
    }
    handle {
        reverse_proxy 127.0.0.1:8787
    }
}"""


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def health(port, path="/healthz", host=None):
    request = Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host} if host else {})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def runtime_identity():
    return {"server": health(8787), "cloud": health(8791),
            "server_start": run("systemctl", "show", "trainmeet-server", "-p", "ActiveEnterTimestamp", "-p", "NRestarts"),
            "cloud_start": run("docker", "inspect", "-f", "{{.State.StartedAt}} {{.Image}}", "trainmeet-cloud-cloud-1")}


def install(payload, sha):
    if not re.fullmatch("[a-f0-9]{40}", sha):
        raise RuntimeError("Full pinned source SHA required")
    if ROOT.exists() or UNIT.exists():
        raise RuntimeError("An existing lab installation needs a separate update, not first install")
    original = CADDY.read_text()
    cloud_original = CLOUD_SITE.read_bytes()
    if original.count(OLD_BLOCK) != 1 or "8797" in original or "tmbox-lab" in original:
        raise RuntimeError("Caddy baseline changed; inspect before installing")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8797))
    before = runtime_identity()
    if any(before[name].get("status") != "ok" for name in ("server", "cloud")):
        raise RuntimeError("Existing services must be healthy")
    if (payload / "REVISION").read_text().strip() != sha:
        raise RuntimeError("Payload revision mismatch")
    manifest = json.loads((payload / "MANIFEST.json").read_text())
    actual_files = {str(p.relative_to(payload)) for p in payload.rglob("*") if p.is_file()} - {"MANIFEST.json", "REVISION"}
    if actual_files != set(manifest):
        raise RuntimeError("Payload file set mismatch")
    for relative, expected in manifest.items():
        path = payload / relative
        if (Path(relative).is_absolute() or ".." in Path(relative).parts
                or not path.resolve().is_relative_to(payload.resolve())
                or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise RuntimeError("Payload content mismatch: " + relative)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/backups/trainmeet") / ("tmbox-lab-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(CADDY, backup / "Caddyfile")
    (backup / "before.json").write_text(json.dumps(before, indent=2))
    release = ROOT / "releases" / sha
    release.parent.mkdir(parents=True)
    shutil.copytree(payload, release)
    # The dedicated DynamicUser must read the pinned source, never write it.
    for directory in (ROOT, release.parent, release):
        directory.chmod(0o755)
    for path in release.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    unit = f"""[Unit]
Description=TrainMeet isolated TMBox test bench
After=network.target

[Service]
Type=simple
DynamicUser=yes
WorkingDirectory={release}
Environment=PYTHONPATH={release}/src
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -m tmbox_gateway.terminal16_public --origin https://server.trainmeet.app --prefix /tmbox-lab/ --port 8797
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
IPAddressDeny=any
IPAddressAllow=localhost
InaccessiblePaths=-/etc/trainmeet-cloud -/etc/trainmeet-server -/opt/trainmeet-cloud -/opt/trainmeet-server -/var/lib/docker -/var/lib/trainmeet-server -/var/backups/trainmeet
MemoryMax=96M
CPUQuota=35%
TasksMax=64
LimitNOFILE=256
UMask=0077

[Install]
WantedBy=multi-user.target
"""
    caddy_changed = False
    try:
        UNIT.write_text(unit)
        run("systemd-analyze", "verify", str(UNIT))
        run("systemctl", "daemon-reload")
        run("systemctl", "start", "trainmeet-tmbox-lab")
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
            raise RuntimeError("New test service did not become healthy")
        if CADDY.read_text() != original or CLOUD_SITE.read_bytes() != cloud_original:
            raise RuntimeError("Proxy configuration changed during installation")
        candidate = backup / "Caddyfile.candidate"
        candidate.write_text(original.replace(OLD_BLOCK, NEW_BLOCK, 1))
        run("caddy", "validate", "--adapter", "caddyfile", "--config", str(candidate))
        shutil.copyfile(candidate, CADDY)
        caddy_changed = True
        run("systemctl", "reload", "caddy")
        # Verify the actual HTTPS route and certificate through the local proxy.
        public = json.loads(run("curl", "--fail", "--silent", "--show-error", "--max-time", "10",
                                "--resolve", "server.trainmeet.app:443:127.0.0.1",
                                "https://server.trainmeet.app/tmbox-lab/healthz"))
        if public.get("service") != "tmbox-lab":
            raise RuntimeError("HTTPS route failed")
        if runtime_identity() != before or CLOUD_SITE.read_bytes() != cloud_original:
            raise RuntimeError("Existing service baseline changed")
        run("systemctl", "enable", "trainmeet-tmbox-lab")
        result = {"url": "https://server.trainmeet.app/tmbox-lab/", "sha": sha,
                  "release": str(release), "backup": str(backup), "existing_services": before}
        (ROOT / "deployment.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    except Exception:
        if caddy_changed:
            # Restore only if the file is still our candidate, never overwrite a concurrent edit.
            if CADDY.read_text() == original.replace(OLD_BLOCK, NEW_BLOCK, 1):
                CADDY.write_text(original)
                run("systemctl", "reload", "caddy")
        run("systemctl", "stop", "trainmeet-tmbox-lab")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    with open("/var/lock/trainmeet-tmbox-lab-install.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        install(args.payload.resolve(), args.sha)
