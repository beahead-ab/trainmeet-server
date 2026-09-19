"""Documentation is generated from accepted commands, not imagined LCDs."""
import importlib.util
from pathlib import Path
import unittest

from tmbox_gateway.models import InteractionMode

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("catalog", ROOT / "scripts/tmbox_legacy_catalog.py")
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class TMBoxCatalogTest(unittest.TestCase):
    def test_vendored_artifact_matches_real_engine(self):
        self.assertEqual(catalog.browser_source(), (ROOT / "src/tmbox_gateway/web/tmbox-legacy-catalog.js").read_text())

    def test_every_interaction_mode_has_a_real_16_by_2_frame(self):
        data = catalog.build_catalog()
        self.assertEqual({mode.value for mode in InteractionMode}, {s["name"] for s in data["screens"]})
        for screen in data["screens"]:
            self.assertEqual(2, len(screen["lines"]))
            self.assertTrue(all(len(line) == 16 for line in screen["lines"]), screen)

    def test_flow_terminal_states(self):
        flows = {f["id"]: f for f in catalog.build_catalog()["flows"]}
        self.assertEqual(8, len(flows))
        for name, flow in flows.items():
            expected = {"leave-request": "requested", "cancel-departure-confirmation": "reserved"}.get(name, "free")
            self.assertEqual(expected, flow["steps"][-1]["line_state"], name)
        for name in ("clearance", "direct"):
            self.assertIn("occupied", [step["line_state"] for step in flows[name]["steps"]])


if __name__ == "__main__":
    unittest.main()
