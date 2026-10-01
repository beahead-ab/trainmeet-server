#!/usr/bin/env python3
"""Retire the stand-alone test bench: TrainMeet Server serves /tmbox-lab/ itself.

The stand-alone service ran a pinned commit and drifted from every Server
release: an old page and an old keypad on server.trainmeet.app/tmbox-lab/.
From 1.17.3 Server answers /tmbox-lab/ with the same code as everything else.

Run as root on the TrainMeet test host after Server 1.17.3 or later is
installed. Puts the proxy block for server.trainmeet.app back to exactly what
deploy.py found, then stops and disables trainmeet-tmbox-lab. Never restarts
Server or Cloud. On any failure before the switch is verified, the proxy file
is restored and the old service keeps running. The old release directory and
backups are left in place.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
from urllib.request import Request, urlopen

CADDY = Path("/etc/caddy/Caddyfile")
CLOUD_SITE = Path("/etc/caddy/sites/trainmeet-cloud.caddy")
BACKUPS = Path("/var/backups/trainmeet")
SERVICE = "trainmeet-tmbox-lab"
HOST = "server.trainmeet.app"
# Exactly as in deploy.py; a test keeps the two files in step.
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


def health(port, path="/healthz", headers=None):
    request = Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def server_lab():
    """/tmbox-lab/healthz from Server itself, asked the way the proxy asks."""
    return health(8787, "/tmbox-lab/healthz", {"Host": HOST, "X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"})


def public_lab():
    return json.loads(run("curl", "--fail", "--silent", "--show-error", "--max-time", "10",
                          "--resolve", f"{HOST}:443:127.0.0.1", f"https://{HOST}/tmbox-lab/healthz"))


def runtime_identity():
    return {"server": health(8787), "cloud": health(8791),
            "server_start": run("systemctl", "show", "trainmeet-server", "-p", "ActiveEnterTimestamp", "-p", "NRestarts"),
            "cloud_start": run("docker", "inspect", "-f", "{{.State.StartedAt}} {{.Image}}", "trainmeet-cloud-cloud-1")}


def retire():
    original = CADDY.read_text()
    if original.count(NEW_BLOCK) != 1:
        if original.count(OLD_BLOCK) == 1 and "tmbox-lab" not in original and "8797" not in original:
            # The proxy already points at Server; only the service may remain.
            run("systemctl", "disable", "--now", SERVICE)
            print(json.dumps({"status": "already_retired", "service": "disabled"}, indent=2))
            return
        raise RuntimeError("Caddy baseline changed; inspect before retiring")
    lab = server_lab()
    if lab.get("service") != "tmbox-lab" or lab.get("served_by") != "server":
        raise RuntimeError("Server does not serve /tmbox-lab/ itself yet; install Server 1.17.3 or later first")
    before = runtime_identity()
    if any(before[name].get("status") != "ok" for name in ("server", "cloud")):
        raise RuntimeError("Existing services must be healthy")
    cloud_original = CLOUD_SITE.read_bytes()
    backup = BACKUPS / ("tmbox-lab-retire-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(CADDY, backup / "Caddyfile")
    (backup / "before.json").write_text(json.dumps(before, indent=2))
    candidate = backup / "Caddyfile.candidate"
    candidate.write_text(original.replace(NEW_BLOCK, OLD_BLOCK, 1))
    caddy_changed = False
    try:
        run("caddy", "validate", "--adapter", "caddyfile", "--config", str(candidate))
        if CADDY.read_text() != original:
            raise RuntimeError("Proxy configuration changed during retirement")
        shutil.copyfile(candidate, CADDY)
        caddy_changed = True
        run("systemctl", "reload", "caddy")
        public = public_lab()
        if public.get("service") != "tmbox-lab" or public.get("served_by") != "server":
            raise RuntimeError("HTTPS route does not reach Server's test bench")
        if runtime_identity() != before or CLOUD_SITE.read_bytes() != cloud_original:
            raise RuntimeError("Existing service baseline changed")
    except Exception:
        # Restore only our own candidate, never a concurrent edit.
        if caddy_changed and CADDY.read_text() == candidate.read_text():
            CADDY.write_text(original)
            run("systemctl", "reload", "caddy")
        raise
    run("systemctl", "disable", "--now", SERVICE)
    result = {"status": "retired", "url": f"https://{HOST}/tmbox-lab/", "served_by": "server",
              "version": public.get("version"), "backup": str(backup), "existing_services": before}
    (backup / "retired.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    with open("/var/lock/trainmeet-tmbox-lab-install.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        retire()
