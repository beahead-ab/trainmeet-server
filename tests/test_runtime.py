from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from runtime_fixture import runtime_package, runtime_package_v2
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

    def test_v2_publication_contains_clock_services_and_display_order(self):
        publication = RuntimePublication.parse(runtime_package_v2())

        timetable = publication.timetable(active_day="Lör")

        self.assertEqual(publication.schema_version, 2)
        self.assertEqual(timetable["clock"]["speed"], 4)
        self.assertEqual([service["train_number"] for service in timetable["services"]], ["101"])
        self.assertEqual(
            timetable["display"]["graph_station_order"],
            ["station-a", "station-b"],
        )

    def test_v2_rejects_incomplete_graph_station_order(self):
        payload = runtime_package_v2()
        payload["display"]["graph_station_order"] = ["station-a"]
        with self.assertRaises(RuntimePublicationError):
            RuntimePublication.parse(payload)

    def test_multi_operating_point_station_preserves_the_source_location(self):
        payload = runtime_package_v2()
        payload["stations"][0]["operating_points"] = [
            {
                "id": "station-a-main",
                "code": "C",
                "name": "C",
                "aliases": ["Charlottendal C"],
            },
            {
                "id": "station-a-rbg",
                "code": "RBG",
                "name": "Rbg",
                "aliases": ["Charlottendal Rbg"],
            },
        ]
        for train in payload["trains"]:
            if train["station_id"] == "station-a":
                train["operating_point_id"] = "station-a-main"

        publication = RuntimePublication.parse(payload)
        timetable = publication.timetable(active_day="Lör", station_id="station-a")

        self.assertEqual(
            timetable["stations"][0]["operating_points"][1]["name"],
            "Rbg",
        )
        self.assertEqual(
            timetable["trains"][0]["operating_point_id"],
            "station-a-main",
        )

    def test_multi_operating_point_station_requires_a_location_on_each_train_row(self):
        payload = runtime_package_v2()
        payload["stations"][0]["operating_points"] = [
            {"id": "station-a-main", "code": "C", "name": "C", "aliases": []},
            {"id": "station-a-rbg", "code": "RBG", "name": "Rbg", "aliases": []},
        ]

        with self.assertRaisesRegex(RuntimePublicationError, "saknar operating_point_id"):
            RuntimePublication.parse(payload)

    def test_train_row_cannot_use_an_operating_point_from_another_station(self):
        payload = runtime_package_v2()
        payload["stations"][1]["operating_points"] = [
            {"id": "station-b-main", "code": "LEK", "name": "Lekby", "aliases": []}
        ]
        payload["trains"][0]["operating_point_id"] = "station-b-main"

        with self.assertRaisesRegex(RuntimePublicationError, "driftplats på fel station"):
            RuntimePublication.parse(payload)

    def test_rejects_duplicate_movements_even_when_notes_differ(self):
        payload = runtime_package_v2()
        duplicate = deepcopy(payload["trains"][0])
        duplicate["id"] = "train-movement-duplicate"
        duplicate["note"] = "Kompletterande anteckning från en annan källfil"
        payload["trains"].append(duplicate)

        with self.assertRaisesRegex(RuntimePublicationError, "samma tågrörelse"):
            RuntimePublication.parse(payload)

    def test_operating_point_kind_is_station_or_yard(self):
        payload = runtime_package_v2()
        payload["stations"][0]["operating_points"] = [
            {
                "id": "station-a-main",
                "code": "C",
                "name": "C",
                "kind": "depot",
                "aliases": ["Charlottendal C"],
            }
        ]

        with self.assertRaisesRegex(RuntimePublicationError, "ogiltig typ"):
            RuntimePublication.parse(payload)

    def test_v1_is_rejected_during_the_pre_release_schema_phase(self):
        payload = runtime_package_v2()
        payload["schema_version"] = 1
        with self.assertRaisesRegex(RuntimePublicationError, "schema_version 2"):
            RuntimePublication.parse(payload)


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

    def test_linked_update_is_staged_until_explicit_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                first = runtime_package_v2(publication_id="publication-v2-first")
                second = runtime_package_v2(publication_id="publication-v2-second")
                store.install(first)
                store.save_link_token("local-link-token")
                store.install(second, activate=False)

                self.assertEqual(store.active().publication_id, "publication-v2-first")
                self.assertEqual(store.latest_staged().publication_id, "publication-v2-second")
                self.assertEqual(store.summary()["available_publication_id"], "publication-v2-second")
                self.assertEqual(store.link_token(), "local-link-token")

                store.activate("publication-v2-second")
                self.assertEqual(store.active().publication_id, "publication-v2-second")
            finally:
                store.close()

    def test_local_cloud_changes_are_queued_record_by_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                store.queue_cloud_changes("meet-1", "publication-1", [
                    {"entity_type": "station", "entity_id": "station-a", "operation": "upsert", "payload": {"name": "A"}},
                    {"entity_type": "panel", "entity_id": "panel-a", "operation": "delete", "payload": {}},
                ])
                pending = store.pending_cloud_changes()
                self.assertEqual({"station", "panel"}, {item["entity_type"] for item in pending})
                self.assertEqual(2, store.pending_cloud_change_count())
                store.mark_cloud_changes_sent([pending[0]["id"]])
                self.assertEqual(1, store.pending_cloud_change_count())
                store.set_cloud_auto_sync(True)
                self.assertTrue(store.cloud_auto_sync_enabled())
            finally:
                store.close()

if __name__ == "__main__":
    unittest.main()
