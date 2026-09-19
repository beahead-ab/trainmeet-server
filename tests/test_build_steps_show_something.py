"""Cloud-only runtime: retired authoring UI must not quietly return."""
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src/tmbox_gateway/web"
HTML = (WEB / "index.html").read_text()
JS = (WEB / "app.js").read_text()

class CloudOnlyViewTests(unittest.TestCase):
    def test_no_local_authoring_ui_or_writer_remains(self):
        for retired in ("build-chrome", "build-sidebar", "build-step-kalla", "build-step-bana",
                        "build-step-tid", "config-form", "runtime-import-file", "save-draft"):
            self.assertNotIn(f'id="{retired}"', HTML)
        for retired in ("/v1/local-configuration", "/v1/operating-mode", "BUILD_STEPS",
                        "saveConfiguration", "activateRuntimeImport", "tidSave"):
            self.assertNotIn(retired, JS)

    def test_every_runtime_panel_has_markup(self):
        for panel in ("overview-view", "overview-traffic", "displays-view", "tmbox-v2-view"):
            self.assertIn(f'id="{panel}"', HTML)
            self.assertIn(f'#{panel}', JS)

    def test_cloud_check_uses_safe_shared_pipeline(self):
        self.assertIn('authorizedFetch("/v1/config/check"', JS)
        self.assertIn('id="cloud-version-state"', HTML)
        self.assertIn("pending_publication_id", JS)
        self.assertNotIn('id="runtime-activate-update"', HTML)

    def test_station_assignment_remains_a_runtime_action(self):
        self.assertIn('data-admin-section="devices"', HTML)
        self.assertIn('authorizedFetch("/v1/devices/assign"', JS)
        self.assertIn('id="device-form-modal"', HTML)

    def test_screens_and_overview_do_not_offer_local_editing(self):
        self.assertNotIn("Bygg om träffen", HTML)
        self.assertNotIn('data-source="lokal"', HTML)
        self.assertNotIn('data-source="fil"', HTML)
        self.assertIn("Träffens innehåll ändras i", HTML)
