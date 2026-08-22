"""The version tool, exercised against real git repositories.

`decide` reads commit messages and changed paths, so it cannot be tested
honestly with mocks - these build actual repositories and commit into them.
That matters more than usual here: this code runs unattended on every merge
to main, and a mistake in it is a wrong version number on shipped software.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "scripts" / "version.py"


class _Repo:
    """A throwaway git repository with the tool installed in it."""

    def __init__(self, directory: str):
        self.root = Path(directory)
        (self.root / "scripts").mkdir()
        shutil.copy(TOOL, self.root / "scripts" / "version.py")
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "Test")
        (self.root / "src").mkdir()
        (self.root / "docs").mkdir()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        ).stdout.strip()

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str) -> str:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD")

    def tool(self, *args: str) -> str:
        return self.run(*args)[0]

    def run(self, *args: str) -> tuple[str, str]:
        """stdout and stderr. sync reports what it touched on stderr, so a
        test that discarded it could not tell a no-op from a rewrite."""
        result = subprocess.run(
            [sys.executable, str(self.root / "scripts" / "version.py"), *args],
            cwd=self.root, capture_output=True, text=True,
        )
        if result.returncode and "check" not in args:
            raise AssertionError(result.stderr)
        return result.stdout.strip(), result.stderr.strip()

    def read(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")


class VersionArithmeticTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = _Repo(self.directory.name)
        self.repo.write("VERSION", "1.2.3\n")
        self.repo.write("src/a.txt", "start\n")
        self.repo.commit("start")

    def tearDown(self):
        self.directory.cleanup()

    def test_each_level_moves_the_part_it_says_and_zeroes_the_rest(self):
        self.assertEqual("1.2.4", self.repo.tool("next", "patch"))
        self.assertEqual("1.3.0", self.repo.tool("next", "minor"))
        self.assertEqual("2.0.0", self.repo.tool("next", "major"))

    def test_bump_writes_the_file(self):
        self.assertEqual("1.3.0", self.repo.tool("bump", "minor"))
        self.assertEqual("1.3.0", self.repo.read("VERSION").strip())
        self.assertEqual("1.3.0", self.repo.tool("current"))


class DerivedFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = _Repo(self.directory.name)
        self.repo.write("VERSION", "1.2.3\n")
        self.repo.write("pyproject.toml", '[project]\nname = "x"\nversion = "1.2.3"\n')
        self.repo.write("package.json", '{\n  "name": "x",\n  "version": "1.2.3"\n}\n')
        self.repo.write(
            "project.yml",
            "settings:\n    MARKETING_VERSION: 1.2.3\n    CURRENT_PROJECT_VERSION: 7\n",
        )
        self.repo.commit("start")

    def tearDown(self):
        self.directory.cleanup()

    def test_a_bump_reaches_every_derived_copy(self):
        self.repo.tool("bump", "minor")
        self.assertIn('version = "1.3.0"', self.repo.read("pyproject.toml"))
        self.assertEqual("1.3.0", json.loads(self.repo.read("package.json"))["version"])
        self.assertIn("MARKETING_VERSION: 1.3.0", self.repo.read("project.yml"))

    def test_the_ios_build_number_counts_up_on_every_bump(self):
        """TestFlight refuses a build number it has seen, whatever the version
        says, so this one is counted rather than derived."""
        self.repo.tool("bump", "patch")
        self.assertIn("CURRENT_PROJECT_VERSION: 8", self.repo.read("project.yml"))
        self.repo.tool("bump", "patch")
        self.assertIn("CURRENT_PROJECT_VERSION: 9", self.repo.read("project.yml"))

    def test_check_catches_a_derived_copy_that_drifted(self):
        self.repo.write("pyproject.toml", '[project]\nname = "x"\nversion = "9.9.9"\n')
        result = subprocess.run(
            [sys.executable, str(self.repo.root / "scripts" / "version.py"), "check"],
            cwd=self.repo.root, capture_output=True, text=True,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("9.9.9", result.stderr)

    def test_sync_repairs_a_drifted_copy(self):
        self.repo.write("package.json", '{\n  "name": "x",\n  "version": "0.0.1"\n}\n')
        self.repo.tool("sync")
        self.assertEqual("1.2.3", json.loads(self.repo.read("package.json"))["version"])

    def test_nothing_is_rewritten_when_everything_already_agrees(self):
        """Otherwise every merge would produce a commit with no change in it."""
        before = self.repo.read("pyproject.toml")
        _, reported = self.repo.run("sync")
        self.assertEqual(before, self.repo.read("pyproject.toml"))
        self.assertEqual("", reported, "sync rapporterade en ändring den inte gjorde")

    def test_sync_reports_exactly_the_files_it_changed(self):
        self.repo.write("package.json", '{\n  "name": "x",\n  "version": "0.0.1"\n}\n')
        _, reported = self.repo.run("sync")
        self.assertIn("package.json", reported)
        self.assertNotIn("pyproject.toml", reported)


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = _Repo(self.directory.name)
        self.repo.write("VERSION", "1.2.3\n")
        self.repo.write("src/a.txt", "start\n")
        self.repo.write("docs/d.md", "doc\n")
        self.base = self.repo.commit("start")

    def tearDown(self):
        self.directory.cleanup()

    def _decide(self, before: str) -> str:
        return self.repo.tool("decide", "--range", f"{before}..HEAD")

    def test_a_change_to_shipping_code_is_a_patch_by_default(self):
        self.repo.write("src/a.txt", "changed\n")
        self.repo.commit("Ändra beteende")
        self.assertEqual("patch", self._decide(self.base))

    def test_a_documentation_only_change_mints_no_version(self):
        """A README fix ships nothing, so it should not produce a release."""
        self.repo.write("docs/d.md", "better\n")
        self.repo.commit("Rätta stavfel")
        self.assertEqual("skip", self._decide(self.base))

    def test_a_workflow_only_change_mints_no_version(self):
        self.repo.write(".github/workflows/ci.yml", "name: ci\n")
        self.repo.commit("Justera CI")
        self.assertEqual("skip", self._decide(self.base))

    def test_docs_alongside_code_still_counts_as_shipping(self):
        """Only a change confined to non-shipping paths is skipped."""
        self.repo.write("docs/d.md", "better\n")
        self.repo.write("src/a.txt", "changed\n")
        self.repo.commit("Rätta och dokumentera")
        self.assertEqual("patch", self._decide(self.base))

    def test_an_explicit_marker_beats_the_default(self):
        for marker, expected in (("[minor]", "minor"), ("[major]", "major")):
            with self.subTest(marker=marker):
                before = self.repo._git("rev-parse", "HEAD")
                self.repo.write("src/a.txt", f"changed {marker}\n")
                self.repo.commit(f"En ändring {marker}")
                self.assertEqual(expected, self._decide(before))

    def test_the_strongest_marker_in_the_range_wins(self):
        """A squashed merge can carry several commits; one breaking change in
        the set makes the whole release breaking."""
        self.repo.write("src/a.txt", "one\n")
        self.repo.commit("En sak [patch]")
        self.repo.write("src/a.txt", "two\n")
        self.repo.commit("Brytande ändring [major]")
        self.assertEqual("major", self._decide(self.base))

    def test_skip_version_stops_it_even_with_another_marker(self):
        self.repo.write("src/a.txt", "changed\n")
        self.repo.commit("Höj version [minor] [skip version]")
        self.assertEqual("skip", self._decide(self.base))

    def test_prose_about_a_marker_is_not_a_marker(self):
        """This is not hypothetical. On the first real run of the workflow,
        a commit body explaining what [skip version] does contained the
        literal string, and the job skipped for that reason rather than the
        intended one. Markers are read from subject lines only.
        """
        self.repo.write("src/a.txt", "changed\n")
        self.repo._git("add", "-A")
        self.repo._git(
            "commit", "-q", "-m", "Rätta ett fel",
            "-m", "Robotens commit bär [skip version] så den inte triggar sig själv, "
                  "och en brytande ändring skrivs [major].",
        )
        self.assertEqual("patch", self._decide(self.base))

    def test_the_robots_own_commits_never_trigger_another_round(self):
        """Identity, not text: prose cannot impersonate a committer."""
        self.repo.write("src/a.txt", "changed\n")
        self.repo._git("add", "-A")
        self.repo._git(
            "-c", "user.email=version@trainmeet.app", "-c", "user.name=trainmeet-version",
            "commit", "-q", "-m", "Version 1.2.4",
        )
        self.assertEqual("skip", self._decide(self.base))

    def test_a_human_commit_alongside_the_robots_still_counts(self):
        """A range that is only robot commits is a loop; a range that mixes
        them is a real merge that happens to include one."""
        self.repo.write("src/a.txt", "changed\n")
        self.repo.commit("En riktig ändring")
        self.repo.write("VERSION", "1.2.3\n")
        self.repo._git("add", "-A")
        self.repo._git(
            "-c", "user.email=version@trainmeet.app", "-c", "user.name=trainmeet-version",
            "commit", "-q", "--allow-empty", "-m", "Version 1.2.3",
        )
        self.assertEqual("patch", self._decide(self.base))

    def test_a_merge_that_sets_the_version_itself_is_not_bumped_past_it(self):
        """The merge that introduces a version, or picks one by hand, has
        already decided. Bumping on top of it would lose that decision - and
        would have turned the deliberate 1.0.0 into 1.0.1 on its first merge.
        """
        self.repo.write("VERSION", "2.0.0\n")
        self.repo.write("src/a.txt", "changed\n")
        self.repo.commit("Sätt versionen för hand")
        self.assertEqual("skip", self._decide(self.base))

    def test_an_explicit_marker_does_not_override_a_hand_set_version(self):
        """Otherwise a stray [minor] in the same PR would undo the choice."""
        self.repo.write("VERSION", "2.0.0\n")
        self.repo.write("src/a.txt", "changed\n")
        self.repo.commit("Sätt versionen [minor]")
        self.assertEqual("skip", self._decide(self.base))

    def test_a_range_with_no_changes_mints_nothing(self):
        head = self.repo._git("rev-parse", "HEAD")
        self.assertEqual("skip", self._decide(head))


class IdempotenceTests(unittest.TestCase):
    """sync repairs; bump advances. Running one twice must not do the other."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = _Repo(self.directory.name)
        self.repo.write("VERSION", "1.2.3\n")
        self.repo.write(
            "project.yml",
            "settings:\n    MARKETING_VERSION: 1.2.3\n    CURRENT_PROJECT_VERSION: 7\n",
        )
        self.repo.commit("start")

    def tearDown(self):
        self.directory.cleanup()

    def test_sync_never_advances_the_build_number(self):
        """It ran on every sync before, so two syncs burned two build numbers
        and a repair looked like a release."""
        for _ in range(3):
            self.repo.tool("sync")
        self.assertIn("CURRENT_PROJECT_VERSION: 7", self.repo.read("project.yml"))

    def test_bump_advances_it_exactly_once(self):
        self.repo.tool("bump", "patch")
        self.assertIn("CURRENT_PROJECT_VERSION: 8", self.repo.read("project.yml"))
        self.repo.tool("sync")
        self.assertIn("CURRENT_PROJECT_VERSION: 8", self.repo.read("project.yml"))


if __name__ == "__main__":
    unittest.main()
