"""The fixtures the golden files and the documentation view now share.

The reference station lived inside tests/js/render_golden.mjs, which was
enough while the golden test was its only reader. KÖR → TMBox v2 draws its
screen catalogue and its flow map out of the same data now, and a second copy
of a reference station is a second one to drift.

Sharing it moves the risk rather than removing it, so these pin what the view
claims: that its frames are the golden ones, and that the pace its traces run
at is the lock the box actually enforces.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "fixture_contract.mjs"
FRAMES = Path(__file__).resolve().parent / "tmbox_golden_frames.txt"
TRACES = Path(__file__).resolve().parent / "tmbox_golden_traces.txt"


def _golden_frames() -> dict[str, list[str]]:
    frames: dict[str, list[str]] = {}
    current = None
    for line in FRAMES.read_text(encoding="utf-8").splitlines():
        if line.startswith("["):
            current = line.strip("[]")
            frames[current] = []
        elif line.startswith("|") and current:
            frames[current].append(line[1:-1])
    return frames


class FixtureContractTests(unittest.TestCase):
    def setUp(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node saknas")
        result = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, cwd=str(ROOT), timeout=60
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.shared = json.loads(result.stdout)

    def test_the_unhurried_pace_is_the_input_lock(self) -> None:
        """The traces are only meaningful at the lock's own length.

        UNHURRIED is written as a number in the fixtures rather than imported,
        because the page loads the file before the navigation module. If the
        firmware ever changes the lock, this says so instead of the traces
        quietly starting to mean something else.
        """

        self.assertEqual(self.shared["inputLock"], self.shared["unhurried"])
        self.assertLess(self.shared["hurried"], self.shared["inputLock"])

    def test_the_screen_catalogue_draws_the_golden_frames(self) -> None:
        """What the view tells the reader it is showing."""

        golden = _golden_frames()
        self.assertTrue(golden, "guldfilen gick inte att läsa")
        for name, lines in self.shared["frames"].items():
            with self.subTest(frame=name):
                self.assertIn(name, golden)
                self.assertEqual(golden[name], lines)

    def test_every_golden_frame_is_in_the_catalogue(self) -> None:
        """The other direction - a screen the box can draw and the catalogue
        cannot is a screen nobody will find without a box on the bench."""

        missing = sorted(set(_golden_frames()) - set(self.shared["frames"]))
        self.assertEqual([], missing)

    def test_the_flow_map_covers_every_golden_trace(self) -> None:
        named = re.findall(r"^\[(.+)\]$", TRACES.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertEqual(sorted(named), sorted(self.shared["traces"]))


if __name__ == "__main__":
    unittest.main()
