#!/usr/bin/env python3
"""Create a minimal, checksummed test-bench archive from one pinned commit."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

MODULES = (
    "__init__", "device_ui", "display", "engine", "models", "observability",
    "runtime", "storage", "terminal16", "terminal16_demo", "terminal16_glyphs",
    "terminal16_public", "train_routes",
)
FILES = [f"src/tmbox_gateway/{name}.py" for name in MODULES] + [
    f"src/tmbox_gateway/terminal16_web/{name}" for name in ("index.html", "style.css", "terminal.js")
] + ["deploy/terminal16/deploy.py", "deploy/terminal16/package.py",
     "deploy/terminal16/README.md", "docs/TMBOX-16X2-PILOT.md"]


def package(revision, output):
    root = Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(["git", "rev-parse", f"{revision}^{{commit}}"], cwd=root, text=True).strip()
    files = {name: subprocess.check_output(["git", "show", f"{sha}:{name}"], cwd=root) for name in FILES}
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files["REVISION"] = (sha + "\n").encode()
    files["MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an earlier delivery artifact.
    with output.open("xb") as target, tarfile.open(fileobj=target, mode="w:gz") as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
            archive.addfile(entry, io.BytesIO(data))
    print(json.dumps({"revision": sha, "archive": str(output.resolve()),
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "files": len(files)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.revision, args.output)
