#!/usr/bin/env python3
"""The version number, and everything that has to agree with it.

`VERSION` in the repo root is the single authoritative source. Every other
place a version appears - pyproject.toml, package.json, project.yml - is a
*derived* copy, written from here by `sync` and checked by the test suite.
Three of them had drifted apart before this existed, which is the whole
reason it does.

Nothing here talks to GitHub. It is a plain file editor, so the same commands
run identically in CI, on a laptop, and in a test.

  version.py current                 print the version
  version.py next minor              print what a minor bump would give
  version.py bump minor              write it, and sync the derived files
  version.py sync                    rewrite the derived files from VERSION
  version.py check                   exit 1 if any derived file disagrees
  version.py decide --range A..B     read commits and print major|minor|patch|skip
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# Which file holds a derived copy, and how to find it there. Each entry is a
# regex with one group - the group is what gets replaced - and a flag for
# whether every occurrence counts or only the first.
DERIVED: list[tuple[str, str, bool]] = [
    ("pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"', False),
    ("package.json", r'(?m)^\s*"version":\s*"([^"]+)"', False),
    ("project.yml", r"(?m)^\s*MARKETING_VERSION:\s*(\S+)", False),
    # CI builds the committed .xcodeproj rather than regenerating it from
    # project.yml, so this is the file a build actually reads. It carries one
    # copy per build configuration, hence every occurrence.
    (
        "TrainMeetIPhone.xcodeproj/project.pbxproj",
        r"(?m)^(?:\s*)MARKETING_VERSION = ([^;]+);",
        True,
    ),
]

#: A change confined to these paths ships nothing, so it mints no version.
#: Getting this wrong in the safe direction costs an unnecessary version
#: number; getting it wrong the other way ships code under an old one.
NON_SHIPPING = (
    ".github/",
    "docs/",
    "README.md",
    "LICENSE",
    ".gitignore",
    "CHANGELOG.md",
)

LEVELS = ("major", "minor", "patch")


def read_version() -> tuple[int, int, int]:
    raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", raw)
    if match is None:
        raise SystemExit(f"VERSION är inte större.funktion.rättning: {raw!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def next_version(level: str) -> str:
    major, minor, patch = read_version()
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"Okänd nivå: {level}")


def sync(version: str) -> list[str]:
    """Write `version` into every derived file that exists. Returns the ones
    it touched, so a caller can say what actually changed."""
    touched = []
    for name, pattern, every in DERIVED:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        matches = list(re.finditer(pattern, text))
        if not matches:
            continue
        if not every:
            matches = matches[:1]
        if all(match.group(1) == version for match in matches):
            continue
        # Right to left, so an earlier replacement cannot move a later span.
        for match in reversed(matches):
            start, end = match.span(1)
            text = text[:start] + version + text[end:]
        path.write_text(text, encoding="utf-8")
        touched.append(name)

    return touched


#: Where the iPhone build number lives. Two files, same reason as the version:
#: project.yml is the source XcodeGen reads, the .pbxproj is what CI builds.
BUILD_NUMBER_FILES: list[tuple[str, str]] = [
    ("project.yml", r"(?m)^(\s*CURRENT_PROJECT_VERSION:\s*)(\d+)"),
    ("TrainMeetIPhone.xcodeproj/project.pbxproj", r"(?m)^(\s*CURRENT_PROJECT_VERSION = )(\d+);"),
]


def advance_build_number() -> str | None:
    """Count the iPhone build number up by one.

    Separate from `sync` on purpose. Syncing repairs drift and has to be safe
    to run twice; this changes state and must run exactly once per release.
    TestFlight refuses a build number it has already seen, whatever the
    version string says, so it is counted rather than derived.
    """
    # Read the highest number anywhere, then write one more everywhere: if the
    # two files ever disagree, going up from the larger keeps the sequence
    # monotonic, which is the only property TestFlight actually cares about.
    current = 0
    found = False
    for name, pattern in BUILD_NUMBER_FILES:
        path = ROOT / name
        if not path.exists():
            continue
        for match in re.finditer(pattern, path.read_text(encoding="utf-8")):
            current = max(current, int(match.group(2)))
            found = True
    if not found:
        return None

    nxt = str(current + 1)
    for name, pattern in BUILD_NUMBER_FILES:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in reversed(list(re.finditer(pattern, text))):
            start, end = match.span(2)
            text = text[:start] + nxt + text[end:]
        path.write_text(text, encoding="utf-8")
    return nxt


def check() -> int:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    problems = []
    for name, pattern, _every in DERIVED:
        path = ROOT / name
        if not path.exists():
            continue
        for match in re.finditer(pattern, path.read_text(encoding="utf-8")):
            if match.group(1) != version:
                problems.append(f"{name} säger {match.group(1)}, VERSION säger {version}")
                break
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def decide(commit_range: str) -> str:
    """major, minor, patch or skip - read off the commits in `range`.

    Precedence, strongest first:

      1. `[skip version]`  - the loop guard the bump commit carries
      2. VERSION was edited - somebody typed an exact number
      3. `[major]`, `[minor]`, `[patch]` - strongest marker in the range wins
      4. nothing outside docs/README/.github changed
      5. `patch` - on these repos a merge to main is a deployable change
    """
    lowered = _git("log", "--format=%B", commit_range).lower()
    files = [line for line in _git("diff", "--name-only", commit_range).splitlines() if line]

    # The loop guard, absolute: the bump commit carries this, and nothing may
    # talk it out of stopping.
    if "[skip version]" in lowered:
        return "skip"

    # Somebody already decided. A merge that sets VERSION itself - the one that
    # introduced this mechanism, or a deliberate hand-picked number - must not
    # then be bumped past the number it just chose. Without this, introducing
    # 1.0.0 would immediately produce 1.0.1 and the decision would be lost.
    #
    # Ahead of the markers on purpose: typing an exact number is the more
    # explicit act, so a stray [minor] in the same range must not undo it.
    if "VERSION" in files:
        return "skip"

    for level in LEVELS:
        if f"[{level}]" in lowered:
            return level

    # An empty range lands here too, and falls out as "skip" because all() over
    # nothing is True. That is deliberate rather than incidental: a range with
    # no changes has nothing to release either.
    if all(any(name.startswith(prefix) for prefix in NON_SHIPPING) for name in files):
        return "skip"
    return "patch"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("current")
    sub.add_parser("sync")
    sub.add_parser("check")
    for name in ("next", "bump"):
        item = sub.add_parser(name)
        item.add_argument("level", choices=LEVELS)
    decide_parser = sub.add_parser("decide")
    decide_parser.add_argument("--range", required=True, dest="commit_range")

    args = parser.parse_args()
    if args.command == "current":
        print(VERSION_FILE.read_text(encoding="utf-8").strip())
    elif args.command == "next":
        print(next_version(args.level))
    elif args.command == "bump":
        version = next_version(args.level)
        VERSION_FILE.write_text(version + "\n", encoding="utf-8")
        touched = sync(version)
        build = advance_build_number()
        if build is not None and "project.yml" not in touched:
            touched.append("project.yml")
        print(version)
        for name in touched:
            print(f"  uppdaterade {name}", file=sys.stderr)
    elif args.command == "sync":
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
        for name in sync(version):
            print(f"uppdaterade {name}", file=sys.stderr)
    elif args.command == "check":
        return check()
    elif args.command == "decide":
        print(decide(args.commit_range))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
