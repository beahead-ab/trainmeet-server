"""Text-only US changes must never rewrite stored authority documents."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tmbox_gateway.us import PROFILE, SCHEMA, USStore, warrant_text


def package():
    return {"schema": SCHEMA, "profile": PROFILE, "publication_id": "test-terms",
            "name": "Terminology test", "territories": [{"id": "t", "name": "Test Subdivision"}],
            "nodes": [{"id": "a", "name": "Yard A", "territory_id": "t", "mp": 10, "x": 30, "y": 0},
                      {"id": "b", "name": "Yard B", "territory_id": "t", "mp": 20, "x": 30, "y": 100}],
            "segments": [{"id": "main", "name": "Main", "from_node": "a", "to_node": "b"}],
            "runs": [{"id": "plan", "symbol": "SP 834", "direction": "east", "schedule": []}]}


class USTerminologyTests(unittest.TestCase):
    def test_new_proceed_and_work_text_describe_different_movements(self):
        for start, end in [(10.25, 19.75), (19.75, 10.25)]:
            w = {"number": "W001", "kind": "proceed", "notes": "Imported note: Spara",
                 "path": [{"segment_id": "main", "from_mp": start, "to_mp": end}]}
            p = package()
            result = warrant_text(p, w, p["runs"][0])
            self.assertIn("Track Warrant W001 · SP 834", result)
            self.assertIn("Train direction: EASTBOUND", result)
            self.assertIn(f"Proceed from MP {start} to MP {end}", result)
            w["kind"] = "work"
            result = warrant_text(p, w, p["runs"][0])
            self.assertIn(f"Work between MP {start} and MP {end} (either direction)", result)
            self.assertNotIn("→", result)
            self.assertNotIn("Proceed", result)
            self.assertIn("Imported note: Spara", result)
            p["runs"][0]["direction"] = "west"
            self.assertIn("Train direction: WESTBOUND", warrant_text(p, w, p["runs"][0]))

    def test_stored_old_text_survives_restart_and_every_lifecycle_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "us.sqlite"
            store = USStore(db)
            def command(action, conductor=False, **payload):
                current = store.context("dispatcher", True)["session"]
                if current:
                    payload.update(session_id=current["id"], expected_revision=current["revision"])
                return store.execute("crew" if conductor else "dispatcher", not conductor,
                                     action, {"command_id": str(uuid4()), **payload}, "06:10")
            try:
                command("create_session", package=package())
                run_id = store.context("dispatcher", True)["session"]["runs"][0]["id"]
                command("assign", run_id=run_id, conductor_id="crew", conductor_name="Sam")
                old_text = "W001 · SP 834 · EAST\nProceed\nOld imported text: Spara"
                with patch("tmbox_gateway.us.warrant_text", return_value=old_text):
                    result = command("draft", run_id=run_id, kind="proceed",
                                     path=[{"segment_id": "main", "from_mp": 10, "to_mp": 20}])
                warrant_id = result["target_id"]
                store.close()
                store = USStore(db)
                with patch("tmbox_gateway.us.warrant_text", side_effect=AssertionError("Stored text regenerated")):
                    for action, conductor, status in [("transmit", False, "transmitted"),
                            ("receive", True, "received"), ("readback", True, "readback_pending"),
                            ("activate", False, "active"), ("request_release", True, "release_requested"),
                            ("close_warrant", False, "closed")]:
                        command(action, conductor, warrant_id=warrant_id, confirmed=True)
                        for actor, dispatcher in [("dispatcher", True), ("crew", False)]:
                            w = store.context(actor, dispatcher)["session"]["warrants"][0]
                            self.assertEqual(w["text"], old_text)
                            self.assertEqual(w["status"], status)
            finally:
                store.close()
