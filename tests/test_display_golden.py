"""The web renderer must draw what the box draws, character for character.

The firmware's C++ renderer is the original. It publishes golden_frames.txt,
and a copy lives here. If the two implementations of the TMBox layout ever
disagree, this fails - which is the whole reason the simulator is allowed to
render locally at all.

Every functional change to TMBox has to reach this simulator. It is where the
features get tested: the whole command page runs here, in any of the four
geometries, without a box on the bench.

Order matters - the firmware is the original and the simulator mirrors it:

  1. change lib/tmbox_core/ in trainmeet-tmbox
  2. make -C firmware/esp32/test_native golden
  3. copy golden_frames.txt, golden_traces.txt and golden_attention.txt
     into tests/ here
  4. mirror the change in tmbox-render.js, tmbox-nav.js or tmbox-attention.js
  5. run this suite - it names the frame, trace or signal that moved
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "tmbox_golden_frames.txt"
FRAME_HARNESS = Path(__file__).resolve().parent / "js" / "render_golden.mjs"
TRACES = Path(__file__).resolve().parent / "tmbox_golden_traces.txt"
TRACE_HARNESS = Path(__file__).resolve().parent / "js" / "nav_traces.mjs"
ATTENTION = Path(__file__).resolve().parent / "tmbox_golden_attention.txt"
ATTENTION_HARNESS = Path(__file__).resolve().parent / "js" / "attention_traces.mjs"


class DisplayGoldenTests(unittest.TestCase):
    def _compare(self, harness: Path, golden: Path, what: str) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node saknas")
        result = subprocess.run(
            [node, str(harness)], capture_output=True, text=True, cwd=str(ROOT), timeout=60
        )
        self.assertEqual(0, result.returncode, result.stderr)

        expected = golden.read_text(encoding="utf-8").splitlines()
        actual = result.stdout.splitlines()
        if expected == actual:
            return

        # A raw assertEqual on hundreds of lines is unreadable. Say what moved.
        for index, (want, got) in enumerate(zip(expected, actual)):
            if want != got:
                context = next(
                    (line for line in reversed(expected[: index + 1]) if line.startswith("[")),
                    "?",
                )
                self.fail(
                    f"{what} {context} skiljer sig pa rad {index + 1}:\n"
                    f"  firmware: {want}\n"
                    f"  webb:     {got}"
                )
        self.fail(f"{what}: olika antal rader, firmware {len(expected)}, webb {len(actual)}")

    def test_the_web_renderer_reproduces_the_firmware_frames(self):
        self._compare(FRAME_HARNESS, GOLDEN, "ruta")

    def test_the_web_navigation_answers_what_the_firmware_answers(self):
        """Same key sequences, same screens, same commands - or this fails."""
        self._compare(TRACE_HARNESS, TRACES, "spar")

    def test_the_web_decides_the_same_things_are_worth_a_sound(self):
        """Same snapshots, same signals - and, mostly, the same silences."""
        self._compare(ATTENTION_HARNESS, ATTENTION, "signal")

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
