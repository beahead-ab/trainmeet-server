"""Where the version comes from, for everything that shows one.

One file is authoritative: `VERSION` at the repo root, holding a SemVer
string. The installer copies it next to the code, the API reads it, the web
admin shows it, and a test asserts pyproject.toml still agrees. Nothing else
is allowed its own opinion — three of them had drifted apart before this
existed (pyproject said 0.6.0, the User-Agent said 0.7, and what an operator
actually saw was a git sha).

The git commit is kept, but as what it is: build information. It answers
"exactly which code is this", which a version number deliberately does not.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Looks like an abbreviated git sha rather than a version.
_BUILD_LIKE = re.compile(r"^[0-9a-f]{7,40}$")

UNKNOWN_VERSION = "okänd"
DEVELOPMENT_VERSION = "utvecklingsversion"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _candidates(install_root: Path | None) -> list[Path]:
    module_dir = Path(__file__).resolve().parent
    roots = [install_root] if install_root else []
    roots += [
        module_dir.parent.parent,  # repo checkout: src/tmbox_gateway/../..
        module_dir.parent,         # installed tree: <root>/src/..
        module_dir,                # packaged beside the code
    ]
    return [root / "VERSION" for root in roots if root is not None]


def product_version(install_root: Path | None = None) -> str:
    """The SemVer string a person should see, e.g. `1.0.0`.

    A sha found in a VERSION file is skipped rather than returned, and the
    search continues. Two things put one there: an installation made before
    this existed, and - for exactly one update - the *old* updater script,
    which overwrites VERSION with the sha after the new installer has already
    written the real number. The installer therefore also drops a copy beside
    the code, where the old script does not reach, so the first update after
    this change already shows 1.0.0 instead of "okänd".
    """
    saw_build_only = False
    for candidate in _candidates(install_root):
        value = _read(candidate)
        if not value:
            continue
        if _BUILD_LIKE.match(value):
            saw_build_only = True
            continue
        return value
    # Every candidate held a build. Saying "okänd" is honest where inventing a
    # number for a tree we cannot identify would not be.
    return UNKNOWN_VERSION if saw_build_only else DEVELOPMENT_VERSION


def build_identifier(install_root: Path | None = None) -> str:
    """The git commit this code was built from, or an empty string."""
    roots = [install_root] if install_root else []
    module_dir = Path(__file__).resolve().parent
    roots += [module_dir.parent.parent, module_dir.parent, module_dir]
    for root in roots:
        if root is None:
            continue
        value = _read(root / "BUILD")
        if value:
            return value
        # Same transition: an old install's VERSION holds the sha.
        legacy = _read(root / "VERSION")
        if legacy and _BUILD_LIKE.match(legacy):
            return legacy
    return ""


def display_version(install_root: Path | None = None) -> str:
    """`Version 1.0.0 · build 4bd9c9a`, or just the version with no build."""
    version = product_version(install_root)
    build = build_identifier(install_root)
    return f"Version {version} · build {build}" if build else f"Version {version}"


def user_agent(product: str = "TrainMeet-Server") -> str:
    """One place decides what we call ourselves on the wire."""
    return f"{product}/{product_version()}"
