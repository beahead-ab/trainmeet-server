"""The web renderer must draw what the box draws, character for character.

The firmware's C++ renderer is the original. It publishes golden_frames.txt,
and a copy lives here. If the two implementations of the TMBox layout ever
disagree, this fails - which is the whole reason the simulator is allowed to
render locally at all.

When the layout changes: change it in trainmeet-tmbox first, run `make golden`
there, copy the file here, then change tmbox-render.js to match.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "tmbox_golden_frames.txt"
HARNESS = Path(__file__).resolve().parent / "js" / "render_golden.mjs"


class DisplayGoldenTests(unittest.TestCase):
    def test_the_web_renderer_reproduces_the_firmware_frames(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node saknas")
        result = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, cwd=str(ROOT), timeout=60
        )
        self.assertEqual(0, result.returncode, result.stderr)

        expected = GOLDEN.read_text(encoding="utf-8").splitlines()
        actual = result.stdout.splitlines()
        if expected == actual:
            return

        # A raw assertEqual on 200 lines is unreadable. Say which frame moved.
        for index, (want, got) in enumerate(zip(expected, actual)):
            if want != got:
                context = next(
                    (line for line in reversed(expected[: index + 1]) if line.startswith("[")),
                    "?",
                )
                self.fail(
                    f"{context} skiljer sig pa rad {index + 1}:\n"
                    f"  firmware: {want}\n"
                    f"  webb:     {got}"
                )
        self.fail(f"olika antal rader: firmware {len(expected)}, webb {len(actual)}")

    def test_every_golden_line_is_as_wide_as_its_geometry_says(self):
        """A guard on the file itself, in case it is ever hand-edited."""
        width = 0
        for line in GOLDEN.read_text(encoding="utf-8").splitlines():
            if line.startswith("["):
                # "[16x2 station-overview]" -> 16
                width = int(line[1:].split(" ")[0].split("x")[0])
            elif line.startswith("|"):
                self.assertTrue(line.endswith("|"), line)
                self.assertEqual(width, len(line) - 2, f"{line} var inte {width} tecken")
