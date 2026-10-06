"""Clouds Data-vy i servern: web/data-workspace.js.

Filen byggs i Cloud (vite.embed.config.ts) och kopieras hit med Clouds
scripts/vendor-data-workspace.mjs, som också skriver data-workspace.json med
Clouds version, sha256 och storlek. Proven låser filen mot json-filen, så en
ändring syns i PR:en, och visar att den går att köra under serverns CSP.
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web"
BUNDLE = WEB / "data-workspace.js"
LOCK = json.loads((WEB / "data-workspace.json").read_text(encoding="utf-8"))


class DataWorkspaceBundleTests(unittest.TestCase):
    def setUp(self):
        self.code = BUNDLE.read_bytes()

    def test_the_file_is_the_one_cloud_built(self):
        self.assertEqual(LOCK["sha256"], hashlib.sha256(self.code).hexdigest(),
                         "data-workspace.js matchar inte data-workspace.json: kör scripts/vendor-data-workspace.mjs i Cloud")
        self.assertEqual(LOCK["bytes"], len(self.code))
        self.assertRegex(LOCK["cloud_version"], r"^\d+\.\d+\.\d+$")

    def test_it_runs_under_the_servers_csp(self):
        text = self.code.decode("utf-8")
        # script-src 'self' utan 'unsafe-eval'
        self.assertNotIn("new Function", text)
        self.assertIsNone(re.search(r"\beval\(", text))
        # style-src 'self': stilarna läggs som konstruerade stilmallar i en shadow root
        self.assertIn("adoptedStyleSheets", text)
        self.assertIn("TrainMeetDataWorkspace", text)

    def test_only_the_timetable_page_loads_it(self):
        """En halv megabyte: deltagarvyn och Drift ska inte hämta den."""
        self.assertNotIn("data-workspace.js", (WEB / "index.html").read_text(encoding="utf-8"))
        self.assertIn('"/assets/data-workspace.js"', (WEB / "data-page.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
