from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from runtime_fixture import runtime_package
from tambox_gateway.runtime import (
    RuntimePublication,
    RuntimePublicationError,
    SQLiteRuntimeStore,
    matches_active_day,
)


class RuntimePublicationTests(unittest.TestCase):
    def test_publication_builds_tambox_session_and_filters_active_day(self):
        publication = RuntimePublication.parse(runtime_package())

        session = publication.session_config()
        self.assertEqual(session.id, "publication-2026-08-11-a")
        self.assertEqual(session.clock_time, "09:15")
        self.assertEqual(session.panels["panel-a"].slots["A"], "connection-a-b")

        saturday = publication.timetable(active_day="Lör", station_id="station-a")
        self.assertEqual([train["train_number"] for train in saturday["trains"]], ["101"])
        sunday = publication.timetable(active_day="Sön", station_id="station-a")
        self.assertEqual(
            [train["train_number"] for train in sunday["trains"]],
            ["101", "202"],
        )

    def test_rejects_unknown_panel_connection(self):
        payload = deepcopy(runtime_package())
        payload["panels"][0]["slots"]["A"] = "missing"
        with self.assertRaises(RuntimePublicationError):
            RuntimePublication.parse(payload)

    def test_day_matching_follows_trainmeet_labels(self):
        self.assertTrue(matches_active_day("Dagl", "Ons"))
        self.assertTrue(matches_active_day("M-Fr", "Tor"))
        self.assertTrue(matches_active_day("Lör,Sön", "Sön"))
        self.assertFalse(matches_active_day("M-Fr", "Lör"))


class RuntimeStoreTests(unittest.TestCase):
    def test_install_is_atomic_and_active_day_is_local(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                installed = store.install(runtime_package())
                active = store.active()
                self.assertIsNotNone(active)
                self.assertEqual(active.publication_id, installed.publication_id)
                self.assertEqual(store.summary()["active_day"], "Lör")

                store.set_active_day("Sön")
                self.assertEqual(store.active_day(), "Sön")
                self.assertEqual(store.active().active_day, "Lör")
            finally:
                store.close()

    def test_same_publication_id_cannot_change_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                store.install(runtime_package())
                changed = deepcopy(runtime_package())
                changed["meet"]["name"] = "Ändrad utan ny version"
                with self.assertRaises(RuntimePublicationError):
                    store.install(changed)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
