"""The one hand-written thing in the flow map, held to the traces.

Every frame and every outcome under KÖR → TMBox v2 → Flöden is derived: the
key sequences run through the same state machine the box runs, and the
screens are drawn by the renderer. The scenario notes are the exception -
they are prose, written for a person.

Prose is where this went wrong once already. A note claimed that A and B are
ignored on a line message; the trace says both send a `clearance.response`.
That is the same defect as a manual describing a key the firmware does not
have, and it deserves the same mechanical answer.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "trace_notes.mjs"

#: "B ignoreras", "A och B ignoreras", "det andra trycket på # ignoreras".
CLAIMS_IGNORED = re.compile(r"(?:^|[\s(])([A-D0-9*#])(?:\s*(?:och|,)\s*([A-D0-9*#]))?\s+ignoreras")


class FlowNoteTests(unittest.TestCase):
    def _traces(self) -> list[dict]:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node saknas")
        result = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, cwd=str(ROOT), timeout=60
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    def test_every_trace_has_a_title_and_a_note(self) -> None:
        for trace in self._traces():
            with self.subTest(trace=trace["name"]):
                self.assertTrue(trace["note"].strip())

    def test_a_note_never_calls_a_key_ignored_that_sends(self) -> None:
        """The mistake this file exists for."""

        wrong = []
        for trace in self._traces():
            sent = set(trace["sent"])
            for match in CLAIMS_IGNORED.finditer(trace["note"]):
                for key in filter(None, match.groups()):
                    if key in sent:
                        wrong.append(f"{trace['name']}: notisen säger att {key} ignoreras, men den skickar")
        self.assertEqual([], wrong)

    def test_a_note_that_names_an_ignored_key_names_one_that_is(self) -> None:
        """The other direction: a key called ignored must have been."""

        wrong = []
        for trace in self._traces():
            ignored = set(trace["ignored"])
            for match in CLAIMS_IGNORED.finditer(trace["note"]):
                for key in filter(None, match.groups()):
                    if key not in ignored:
                        wrong.append(f"{trace['name']}: notisen säger att {key} ignoreras, men inget tryck gjorde det")
        self.assertEqual([], wrong)


if __name__ == "__main__":
    unittest.main()
