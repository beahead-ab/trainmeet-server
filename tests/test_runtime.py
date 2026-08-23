from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from runtime_fixture import fictional_runtime_package, runtime_package, runtime_package_v3
from tmbox_gateway.models import TrackType
from tmbox_gateway.runtime import (
    RuntimePublication,
    RuntimePublicationError,
    SQLiteRuntimeStore,
    matches_active_day,
)


class RuntimePublicationTests(unittest.TestCase):
    def test_publication_builds_tmbox_session_and_filters_active_day(self):
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

    def test_v3_publication_contains_clock_services_and_display_order(self):
        publication = RuntimePublication.parse(runtime_package_v3())

        timetable = publication.timetable(active_day="Lör")

        self.assertEqual(publication.schema_version, 3)
        self.assertEqual(timetable["clock"]["speed"], 4)
        self.assertEqual([service["train_number"] for service in timetable["services"]], ["101"])
        self.assertEqual(
            timetable["display"]["graph_station_order"],
            ["station-a", "station-b"],
        )

    def test_v3_rejects_incomplete_graph_station_order(self):
        payload = runtime_package_v3()
        payload["display"]["graph_station_order"] = ["station-a"]
        with self.assertRaises(RuntimePublicationError):
            RuntimePublication.parse(payload)

    def test_the_catalogue_is_parsed_and_ordered_by_sort_order(self):
        config = RuntimePublication.parse(runtime_package_v3()).session_config()

        self.assertEqual(len(config.tracks), 3)
        self.assertEqual(
            [track.display_label for track in config.tracks_for_station("station-a")],
            ["1", "2"],
        )
        self.assertEqual(config.tracks["track-station-a-1"].station_id, "station-a")

    def test_an_inactive_track_leaves_the_selector_without_losing_its_history(self):
        payload = runtime_package_v3()
        payload["tracks"][1]["active"] = False

        config = RuntimePublication.parse(payload).session_config()

        self.assertEqual(
            [track.display_label for track in config.tracks_for_station("station-a")],
            ["1"],
        )
        # The row that already points at it still resolves; only new choices
        # lose the track.
        self.assertIn("track-station-a-2", config.tracks)

    def test_a_train_row_cannot_reference_an_unknown_track(self):
        payload = runtime_package_v3()
        payload["trains"][0]["track_id"] = "track-that-was-never-published"

        with self.assertRaisesRegex(RuntimePublicationError, "okänt spår"):
            RuntimePublication.parse(payload)

    def test_a_train_row_cannot_reference_a_track_at_another_station(self):
        payload = runtime_package_v3()
        payload["trains"][0]["track_id"] = "track-station-b-1"

        with self.assertRaisesRegex(RuntimePublicationError, "spår på fel station"):
            RuntimePublication.parse(payload)

    def test_two_tracks_cannot_share_a_label_at_one_operating_point(self):
        payload = runtime_package_v3()
        payload["tracks"].append(
            {
                "id": "track-station-a-1-igen",
                "display_label": "1",
                "station_id": "station-a",
                "operating_point_id": None,
                "active": True,
                "sort_order": 30,
            }
        )

        with self.assertRaisesRegex(RuntimePublicationError, "samma beteckning"):
            RuntimePublication.parse(payload)

    def test_the_fictional_topology_carries_a_full_catalogue(self):
        # Decision B5: Charlottendal proves the real import, the fictional
        # topology exists where the topology itself has to be built - three
        # neighbours and double track in both directions.
        config = RuntimePublication.parse(fictional_runtime_package()).session_config()

        self.assertEqual(len(config.stations), 4)
        self.assertEqual(len(config.tracks), 16)
        self.assertEqual(
            [track.display_label for track in config.tracks_for_station("st-cda")],
            ["1A", "1B", "2A", "2B"],
        )
        double = [
            connection
            for connection in config.connections.values()
            if connection.track_type is TrackType.DOUBLE
        ]
        self.assertEqual(len(double), 2)

    def test_multi_operating_point_station_preserves_the_source_location(self):
        payload = runtime_package_v3()
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
        # A station split into operating points splits its track catalogue the
        # same way, so a train row and its track agree on where they are.
        for track in payload["tracks"]:
            if track["station_id"] == "station-a":
                track["operating_point_id"] = "station-a-main"

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
        payload = runtime_package_v3()
        payload["stations"][0]["operating_points"] = [
            {"id": "station-a-main", "code": "C", "name": "C", "aliases": []},
            {"id": "station-a-rbg", "code": "RBG", "name": "Rbg", "aliases": []},
        ]

        with self.assertRaisesRegex(RuntimePublicationError, "saknar operating_point_id"):
            RuntimePublication.parse(payload)

    def test_train_row_cannot_use_an_operating_point_from_another_station(self):
        payload = runtime_package_v3()
        payload["stations"][1]["operating_points"] = [
            {"id": "station-b-main", "code": "LEK", "name": "Lekby", "aliases": []}
        ]
        payload["trains"][0]["operating_point_id"] = "station-b-main"

        with self.assertRaisesRegex(RuntimePublicationError, "driftplats på fel station"):
            RuntimePublication.parse(payload)

    def test_rejects_duplicate_movements_even_when_notes_differ(self):
        payload = runtime_package_v3()
        duplicate = deepcopy(payload["trains"][0])
        duplicate["id"] = "train-movement-duplicate"
        duplicate["note"] = "Kompletterande anteckning från en annan källfil"
        payload["trains"].append(duplicate)

        with self.assertRaisesRegex(RuntimePublicationError, "samma tågrörelse"):
            RuntimePublication.parse(payload)

    def test_operating_point_kind_is_station_or_yard(self):
        payload = runtime_package_v3()
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

    def test_older_schemas_are_rejected_during_the_pre_release_phase(self):
        # No compatibility layer is kept before the first external release, so
        # a package built for schema 2 - one without a track catalogue - is
        # refused outright rather than quietly parsed with tracks missing.
        for version in (1, 2):
            with self.subTest(schema_version=version):
                payload = runtime_package_v3()
                payload["schema_version"] = version
                with self.assertRaisesRegex(RuntimePublicationError, "schema_version 3"):
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
                first = runtime_package_v3(publication_id="publication-v2-first")
                second = runtime_package_v3(publication_id="publication-v2-second")
                store.install(first)
                store.save_link_token("local-link-token")
                store.stage_pending(second)

                self.assertEqual(store.active().publication_id, "publication-v2-first")
                self.assertEqual(store.latest_staged().publication_id, "publication-v2-second")
                self.assertEqual(store.summary()["available_publication_id"], "publication-v2-second")
                self.assertEqual(store.link_token(), "local-link-token")

                store.activate("publication-v2-second")
                self.assertEqual(store.active().publication_id, "publication-v2-second")
                self.assertIsNone(store.summary()["available_publication_id"])
            finally:
                store.close()

    def test_invalid_active_publication_can_be_quarantined_without_being_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                installed = store.install(runtime_package_v3())
                broken = runtime_package_v3()
                duplicate = deepcopy(broken["trains"][0])
                duplicate["id"] = "duplicate-movement"
                broken["trains"].append(duplicate)
                store._connection.execute(
                    "UPDATE runtime_publications SET payload_json = ? WHERE publication_id = ?",
                    (json.dumps(broken), installed.publication_id),
                )

                with self.assertRaisesRegex(RuntimePublicationError, "samma tågrörelse"):
                    store.active()

                store.quarantine_active("Driftpaketet innehåller samma tågrörelse flera gånger")

                self.assertIsNone(store.active())
                self.assertEqual(
                    "Driftpaketet innehåller samma tågrörelse flera gånger",
                    store.summary()["error"],
                )
                row = store._connection.execute(
                    "SELECT COUNT(*) FROM runtime_publications WHERE publication_id = ?",
                    (installed.publication_id,),
                ).fetchone()
                self.assertEqual(1, row[0])
            finally:
                store.close()

    def test_auto_sync_is_a_setting_about_fetching_not_sending(self):
        """The only automatic traffic with Cloud is the server pulling (D1)."""
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                store.set_cloud_auto_sync(True)
                self.assertTrue(store.cloud_auto_sync_enabled())
                store.set_cloud_auto_sync(False)
                self.assertFalse(store.cloud_auto_sync_enabled())
            finally:
                store.close()

    def test_the_store_offers_no_way_to_send_anything_upstream(self):
        """D1: Cloud publishes, the server fetches. Nothing goes back.

        A negative test rather than a positive one, because the thing being
        asserted is an absence - and an absence is exactly what quietly grows
        back when someone adds a helpful little outbox.
        """
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                for name in (
                    "queue_cloud_changes",
                    "pending_cloud_changes",
                    "mark_cloud_changes_sent",
                    "pending_cloud_change_count",
                ):
                    self.assertFalse(hasattr(store, name), f"{name} ska vara borta")
                tables = {
                    row[0]
                    for row in store._connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertNotIn("cloud_change_outbox", tables)
            finally:
                store.close()

    def test_connection_badge_defaults_to_every_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                self.assertEqual(
                    ["clock", "topology", "graph", "dashboard"],
                    store.connection_badge_screens(),
                )
                self.assertEqual(0, store.connection_code_validity_hours())
            finally:
                store.close()

    def test_connection_badge_screens_can_be_narrowed_and_widened(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                store.set_connection_badge_screens(["dashboard", "clock", "not-a-real-screen"])
                self.assertEqual(["clock", "dashboard"], store.connection_badge_screens())

                store.set_connection_badge_screens([])
                self.assertEqual([], store.connection_badge_screens())
            finally:
                store.close()

    def test_connection_code_validity_only_accepts_known_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                store.set_connection_code_validity_hours(24)
                self.assertEqual(24, store.connection_code_validity_hours())

                with self.assertRaises(RuntimePublicationError):
                    store.set_connection_code_validity_hours(5)
                # A rejected write leaves the previous choice in place.
                self.assertEqual(24, store.connection_code_validity_hours())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
