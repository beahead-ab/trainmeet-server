"""The selected Cloud meet, not a navigation link, owns the operating region."""
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src/tmbox_gateway"

class MeetTypeNavigationTests(unittest.TestCase):
    def test_global_eu_us_picker_is_absent(self):
        for relative in ("web/index.html", "us_web/index.html"):
            source = (ROOT / relative).read_text()
            self.assertNotIn('class="meet-type-nav"', source)
            self.assertNotIn('aria-label="Typ av tågträff"', source)

    def test_capabilities_and_selected_context_choose_workspaces(self):
        script = (ROOT / "web/app.js").read_text()
        self.assertIn('"/v1/server-context"', script)
        self.assertIn("available_workspaces", script)
        self.assertIn("operating_region", script)
        self.assertIn('const WORKSPACE_KEY = "trainmeet.workspace"', script)
        self.assertIn("sessionStorage.setItem(WORKSPACE_KEY, key)", script)
        self.assertNotIn('const BUILD_STEPS', script)

    def test_us_language_scope_is_preserved(self):
        self.assertIn('data-i18n-scope="us"', (ROOT / "us_web/index.html").read_text())
        self.assertNotIn('data-i18n-scope="us"', (ROOT / "web/index.html").read_text())
