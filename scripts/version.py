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
  version.py notes --range A..B      record the headings of A..B under the current version
  version.py notes --backfill        rebuild RELEASES.json from the robot's version commits
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

#: What went into each version, newest first: one entry per version with the
#: headings of the changes that minted it - on these repos, the PR titles,
#: which a squash merge turns into commit subjects. The app shows them under
#: its update page, so a user can read what each update did.
RELEASES_FILE = ROOT / "RELEASES.json"
#: Where each app reads its copy, written by `sync` like a derived version.
#: Only the ones whose folder exists in this repo are written.
RELEASE_COPIES = ("src/tmbox_gateway/releases.json", "public/releases.json")
_MARKER = re.compile(r"\s*\[(?:major|minor|patch|skip version)\]", re.IGNORECASE)
_PR_NUMBER = re.compile(r"\s*\(#\d+\)\s*$")
_ROBOT_SUBJECT = re.compile(r"^Version (\S+) \[skip version\]$")


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

    touched += copy_releases()
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


def heading(subject: str) -> str:
    """A commit subject as a user reads it: no version markers, no PR number."""
    return _PR_NUMBER.sub("", _MARKER.sub("", subject)).strip()


def read_releases() -> list[dict]:
    if not RELEASES_FILE.exists():
        return []
    return json.loads(RELEASES_FILE.read_text(encoding="utf-8"))


def _releases_text(entries: list[dict]) -> str:
    return json.dumps(entries, ensure_ascii=False, indent=2) + "\n"


def copy_releases() -> list[str]:
    """Write RELEASES.json to every app copy whose folder exists."""
    if not RELEASES_FILE.exists():
        return []
    text = RELEASES_FILE.read_text(encoding="utf-8")
    touched = []
    for name in RELEASE_COPIES:
        path = ROOT / name
        if not path.parent.is_dir():
            continue
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        path.write_text(text, encoding="utf-8")
        touched.append(name)
    return touched


def record_notes(commit_range: str) -> dict:
    """Put the headings of `commit_range` under the current version."""
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    notes, date = [], ""
    for line in _git("log", "--no-merges", "--format=%ae%x09%cs%x09%s", commit_range).splitlines():
        author, day, subject = (line.split("\t", 2) + ["", ""])[:3]
        if author == ROBOT_EMAIL or not subject.strip():
            continue
        date = date or day
        title = heading(subject)
        if title and title not in notes:
            notes.append(title)
    entries = read_releases()
    entry = next((item for item in entries if item.get("version") == version), None)
    if entry is None:
        entry = {"version": version, "date": date, "notes": []}
        entries.insert(0, entry)
    entry["notes"] += [title for title in reversed(notes) if title not in entry["notes"]]
    RELEASES_FILE.write_text(_releases_text(entries), encoding="utf-8")
    copy_releases()
    return entry


def backfill() -> list[dict]:
    """Rebuild the notes from history: every robot "Version X" commit closes a
    version, and the commits on main since the one before are what it holds.
    Changes before the first robot commit predate versioning and are left out."""
    entries: list[dict] = []
    pending: list[str] = []
    # The commit that introduced VERSION starts the first version's notes.
    started = set(_git("log", "--diff-filter=A", "--format=%H", "--", "VERSION").split())
    # One record per commit; a GitHub merge commit carries the PR title as the
    # first line of its body rather than as its subject.
    log = _git("log", "--first-parent", "--reverse", "--format=%H%x09%ae%x09%cs%x09%s%x09%b%x1e")
    for record in log.split("\x1e"):
        commit, author, day, subject, body = (record.strip("\n").split("\t", 4) + [""] * 4)[:5]
        if not commit:
            continue
        if subject.startswith("Merge pull request") and body.strip():
            subject = body.strip().splitlines()[0]
        if commit in started:
            pending = []
        robot = _ROBOT_SUBJECT.match(subject) if author == ROBOT_EMAIL else None
        if robot:
            # A version with nothing of its own (a re-sync) is not news.
            if pending:
                entries.insert(0, {"version": robot.group(1), "date": day, "notes": pending})
            pending = []
        elif author != ROBOT_EMAIL and subject and not subject.startswith("Merge "):
            title = heading(subject)
            if title and title not in pending:
                pending.append(title)
    RELEASES_FILE.write_text(_releases_text(entries), encoding="utf-8")
    copy_releases()
    return entries


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
    if RELEASES_FILE.exists():
        notes = RELEASES_FILE.read_text(encoding="utf-8")
        for name in RELEASE_COPIES:
            path = ROOT / name
            if path.parent.is_dir() and (not path.exists() or path.read_text(encoding="utf-8") != notes):
                problems.append(f"{name} är inte en kopia av RELEASES.json")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


#: The robot's own committer address. Recognising its commits by author is
#: what stops the workflow triggering itself - a marker in the message cannot
#: do that job, because prose about the marker *is* the marker.
ROBOT_EMAIL = "version@trainmeet.app"


def decide(commit_range: str) -> str:
    """major, minor, patch or skip - read off the commits in `range`.

    Precedence, strongest first:

      1. every commit is the robot's own - the loop guard
      2. `[skip version]` in a commit *subject*
      3. VERSION was edited - somebody typed an exact number
      4. `[major]`, `[minor]`, `[patch]` in a subject - strongest wins
      5. nothing outside docs/README/.github changed
      6. `patch` - on these repos a merge to main is a deployable change

    Markers are read from subject lines only, never bodies. A commit body
    explaining what `[skip version]` does would otherwise *be* a
    `[skip version]`, which is not hypothetical: it happened on the first
    real run of this workflow, and only a second rule made the outcome
    correct anyway.
    """
    subjects = _git("log", "--format=%s", commit_range).lower()
    authors = [a.strip() for a in _git("log", "--format=%ae", commit_range).splitlines() if a.strip()]
    files = [line for line in _git("diff", "--name-only", commit_range).splitlines() if line]

    # The loop guard. Identity, not text: the robot cannot argue itself out of
    # being the robot, and no amount of prose can impersonate it.
    if authors and all(author == ROBOT_EMAIL for author in authors):
        return "skip"

    if "[skip version]" in subjects:
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
        if f"[{level}]" in subjects:
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
    notes_parser = sub.add_parser("notes")
    notes_how = notes_parser.add_mutually_exclusive_group(required=True)
    notes_how.add_argument("--range", dest="commit_range")
    notes_how.add_argument("--backfill", action="store_true")

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
    elif args.command == "notes":
        if args.backfill:
            print(f"{len(backfill())} versioner", file=sys.stderr)
        else:
            entry = record_notes(args.commit_range)
            print(f"{entry['version']}: {len(entry['notes'])} rubriker", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
