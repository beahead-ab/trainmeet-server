"""Lokala ändringar i tidtabellen, ovanpå Clouds publicering.

Admin kan flytta en tid, byta spår, ta bort ett tåg eller göra en sträcka
dubbelspårig mitt under träffen, utan att gå via Cloud. Proven visar att
ändringen ligger som ett lager under samma publicerings-id, att TKL-läget
följer med raden, att tjänster och rutter byggs om som Cloud bygger dem,
att bara fria tåg och sträckor får ändras, och att Clouds version står
orörd tills admin kastar lagret eller tar nästa Cloud-version.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

from session_fixture import sample_session
from test_pending_revisions import CloudDeliveryFixture, cloud_package, dispatch_request
from tmbox_gateway import local_edits
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.identity import DeviceKind, PairedClient
from tmbox_gateway.local_edits import LocalEditError, apply_draft, draft_from_publication
from tmbox_gateway.models import ConnectionState, TrackType
from tmbox_gateway.runtime import RuntimePublication, SQLiteRuntimeStore

PACKAGE = Path(__file__).resolve().parent / "cloud_runtime_package.json"


class AdapterTests(unittest.TestCase):
    """Clouds paket fram och tillbaka genom utkastformen, utan databas."""

    def setUp(self):
        self.package = json.loads(PACKAGE.read_text(encoding="utf-8"))

    def test_the_draft_looks_like_clouds(self):
        draft = draft_from_publication(self.package)
        self.assertEqual(self.package["meet"]["name"], draft["name"])
        self.assertEqual(float(self.package["clock"]["speed"]), float(draft["clock_speed"]))
        self.assertEqual([], draft["files"])
        labels = {track["id"]: track["display_label"] for track in self.package["tracks"]}
        for row, original in zip(draft["trains"], self.package["trains"]):
            self.assertEqual(labels.get(original.get("track_id"), ""), row["track"])
            self.assertNotIn("service_id", row)
            self.assertTrue(row["station"])

    def test_an_unchanged_draft_is_the_same_package(self):
        effective, edits = apply_draft(self.package, draft_from_publication(self.package))
        self.assertEqual({"trains": {}, "removed_trains": [], "connections": {}}, edits)
        self.assertEqual((self.package["trains"], self.package["services"], self.package["routes"]),
                         (effective["trains"], effective["services"], effective["routes"]))

    def test_a_time_change_rebuilds_the_services_as_cloud_would(self):
        draft = draft_from_publication(self.package)
        row = draft["trains"][0]
        row["departure_time"] = "23:59"
        effective, edits = apply_draft(self.package, draft)
        self.assertEqual({"from": self.package["trains"][0]["departure_time"], "to": "23:59"},
                         edits["trains"][row["id"]]["departure_time"])
        service = next(s for s in effective["services"] if s["id"] == effective["trains"][0]["service_id"])
        self.assertEqual("23:59", next(s for s in service["stops"] if s["station_id"] == row["station_id"])["departure_time"])
        RuntimePublication.parse(effective)

    def test_what_cloud_owns_cannot_change_here(self):
        cases = (
            (lambda d: d["trains"].append({**d["trains"][0], "id": "ny"}), "Nya tåg läggs till i Cloud"),
            (lambda d: d["trains"][0].update(station_id="station-x"), "ändras i Cloud"),
            (lambda d: d["trains"][0].update(departure_time="25:00"), "inte ett klockslag"),
            (lambda d: d["trains"][0].update(track="Spår 99"), "finns inte i stationens spårkatalog"),
            (lambda d: d["trains"][0].update(train_number=" "), "får inte vara tomt"),
            (lambda d: d["trains"][0].update(arrival_time="", departure_time=""), "ankomst eller avgång måste finnas"),
            (lambda d: d["connections"][0].update(station_a_id="x"), "ändras i Cloud"),
            (lambda d: d["connections"].pop(), "Sträckor tas bort"),
            (lambda d: d["connections"][0].update(track_type="triple"), "single eller double"),
        )
        for change, message in cases:
            draft = draft_from_publication(self.package)
            change(draft)
            with self.subTest(message), self.assertRaisesRegex(LocalEditError, message):
                apply_draft(self.package, draft)

    def test_the_lines_read_like_a_person_would_say_it(self):
        draft = draft_from_publication(self.package)
        first = draft["trains"][0]
        first["departure_time"] = "23:59"
        draft["trains"].pop()
        connection = draft["connections"][0]
        connection["track_type"] = "double" if connection["track_type"] == "single" else "single"
        _, edits = apply_draft(self.package, draft)
        lines = local_edits.describe(edits, self.package)
        self.assertEqual(3, len(lines))
        self.assertIn(f"Tåg {first['train_number']} vid {first['station']}: avgång", lines[0])
        self.assertIn("23:59", lines[0])
        self.assertTrue(lines[1].endswith(": borttaget"))
        self.assertTrue(lines[2].startswith("Sträckan "))
        self.assertEqual(3, local_edits.edit_count(edits))


class EngineTests(unittest.TestCase):
    def test_only_a_free_section_changes_track_type_and_the_rest_keeps_its_state(self):
        engine = TrafficEngine(sample_session())
        connection_id = next(iter(engine.config.connections))
        current = engine.config.connections[connection_id]
        target = TrackType("double") if current.track_type == TrackType("single") else TrackType("single")
        config = replace(engine.config, connections={**engine.config.connections, connection_id: replace(current, track_type=target)})
        runtime = engine.connections[connection_id]
        runtime.state = ConnectionState.RESERVED
        with self.assertRaisesRegex(ValueError, "upptagen"):
            engine.apply_connection_rules(config, {connection_id})
        self.assertEqual(current.track_type, engine.config.connections[connection_id].track_type)
        runtime.state = ConnectionState.FREE
        fingerprint, revision = engine.config_fingerprint, engine.revision
        engine.apply_connection_rules(config, {connection_id})
        self.assertEqual(target, engine.config.connections[connection_id].track_type)
        self.assertNotEqual(fingerprint, engine.config_fingerprint)
        self.assertIs(runtime, engine.connections[connection_id], "the runtime objects stay; only the rules change")
        self.assertEqual(revision + 1, engine.revision)


class MeetDataTests(CloudDeliveryFixture):
    """Hela vägen genom HTTP-routern, med riktig runtime-, operations- och livscykellagring."""

    def get(self):
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data", method="GET")
        self.assertEqual(HTTPStatus.OK, status, body)
        return body

    def post(self, draft, *, expected=None, client=None):
        state = self.get()
        payload = {"draft": draft, "expected_revision": state["revision"] if expected is None else expected,
                   "base_publication_id": state["base_publication_id"]}
        return dispatch_request(self.application, client or self.client, "/v1/meet-data", payload)

    @staticmethod
    def row(draft, movement_id):
        return next(row for row in draft["trains"] if row["id"] == movement_id)

    def station_name(self, station_id):
        return next(s["name"] for s in self.runtime.active().payload["stations"] if s["id"] == station_id)

    def active_row(self, movement_id):
        return next((row for row in self.runtime.active().payload["trains"] if row["id"] == movement_id), None)

    def test_a_time_change_takes_effect_at_once_under_the_same_publication(self):
        before = self.get()
        self.assertEqual((0, 0, False), (before["revision"], before["local_edits"]["count"], before["local_edits"]["active"]))
        draft = before["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "09:30"
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertEqual((True, 1, 1), (body["changed"], body["revision"], body["local_edits"]["count"]))
        self.assertEqual(before["meet_generation"] + 1, body["meet_generation"], "a new generation rebuilds the 16x2 views")
        self.assertEqual([f"Tåg 101 vid {self.station_name('station-a')}: avgång 09:20 → 09:30"], body["local_edits"]["lines"])
        active = self.runtime.active()
        self.assertEqual(("cloud-first", 1), (active.publication_id, active.local_revision))
        moved = self.active_row("movement-101-a")
        self.assertEqual("09:30", moved["departure_time"])
        service = next(s for s in active.payload["services"] if s["id"] == moved["service_id"])
        self.assertEqual("09:30", next(s for s in service["stops"] if s["station_id"] == "station-a")["departure_time"])
        # Det TKL och boxarna läser
        shown = next(t for t in self.application.display_snapshot()["trains"] if t["id"] == "movement-101-a")
        self.assertEqual("09:30", shown["departure_time"])
        # Clouds version är orörd, och checksumman är Clouds
        cloud = self.runtime.publication("cloud-first")
        self.assertEqual("09:20", next(r for r in cloud.payload["trains"] if r["id"] == "movement-101-a")["departure_time"])
        self.assertEqual((cloud.checksum, 0), (active.checksum, cloud.local_revision))
        [event] = self.operations.audit_trail("meet-data-cloud-first")
        self.assertEqual(("meet_data.saved", 1), (event["action"], event["detail"]["revision"]))

    def test_saving_the_same_rows_changes_nothing(self):
        before = self.get()
        status, body = self.post(before["draft"])
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertEqual((False, 0, before["meet_generation"]), (body["changed"], body["revision"], body["meet_generation"]))

    def test_tkl_state_follows_the_row_through_a_change(self):
        day = self.runtime.active_day()
        self.operations._connection.execute(
            "INSERT INTO tkl_movement_states (publication_id, active_day, movement_id, station_id, arrival_status, departure_status,"
            " actual_track, operator_note, updated_by, updated_at, revision, crew_ready)"
            " VALUES ('cloud-first', ?, 'movement-101-a', 'station-a', 'none', 'positioned', NULL, 'lok 3', 'tkl', '2026-10-06T10:00:00Z', 2, 1)",
            (day,))
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "09:30"
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        state = self.operations.tkl_station_state("cloud-first", day, "station-a")["movements"]
        self.assertIn("movement-101-a", state)
        identity = self.operations._connection.execute(
            "SELECT train_number, station_id, stop_index FROM movement_identity WHERE publication_id='cloud-first' AND movement_id='movement-101-a'").fetchone()
        self.assertEqual(("101", "station-a", 0), tuple(identity))

    def test_a_train_with_an_open_clearance_cannot_be_changed_but_others_can(self):
        self.busy()
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "09:30"
        status, body = self.post(draft)
        self.assertEqual((HTTPStatus.CONFLICT, "local_edit_blocked"), (status, body["code"]), body)
        self.assertIn("Tåg 101 har ett öppet körtillstånd", body["message"])
        self.assertEqual("09:20", self.active_row("movement-101-a")["departure_time"])
        draft = self.get()["draft"]
        self.row(draft, "movement-202-a")["note"] = "Extra vagn"
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertEqual("Extra vagn", self.active_row("movement-202-a")["note"])
        draft = self.get()["draft"]
        connection = next(c for c in draft["connections"] if c["id"] == "connection-a-b")
        connection["track_type"] = "double" if connection["track_type"] == "single" else "single"
        status, body = self.post(draft)
        self.assertEqual((HTTPStatus.CONFLICT, "local_edit_blocked"), (status, body["code"]), body)
        self.assertIn("öppet körtillstånd", body["message"])

    def test_a_train_on_the_line_cannot_be_changed(self):
        self.operations._connection.execute(
            "INSERT INTO train_positions (train_number, status, station_id, connection_id, from_station_id, to_station_id, updated_at)"
            " VALUES ('202', 'connection', NULL, 'connection-a-b', 'station-a', 'station-b', '2026-10-06T10:00:00Z')")
        draft = self.get()["draft"]
        self.row(draft, "movement-202-a")["arrival_time"] = "10:05"
        status, body = self.post(draft)
        self.assertEqual((HTTPStatus.CONFLICT, "local_edit_blocked"), (status, body["code"]), body)
        self.assertIn("Tåg 202 är ute på linjen", body["message"])

    def test_a_row_nobody_touched_can_be_removed_but_not_one_with_state(self):
        draft = self.get()["draft"]
        draft["trains"] = [row for row in draft["trains"] if row["id"] != "movement-202-a"]
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertIsNone(self.active_row("movement-202-a"))
        self.assertNotIn("service-202-Sön", {s["id"] for s in self.runtime.active().payload["services"]})
        self.assertEqual([f"Tåg 202 vid {self.station_name('station-a')}: borttaget"], body["local_edits"]["lines"])
        day = self.runtime.active_day()
        self.operations._connection.execute(
            "INSERT INTO tkl_movement_states (publication_id, active_day, movement_id, station_id, arrival_status, departure_status,"
            " actual_track, operator_note, updated_by, updated_at, revision, crew_ready)"
            " VALUES ('cloud-first', ?, 'movement-101-b', 'station-b', 'approaching', 'none', NULL, NULL, 'tkl', '2026-10-06T10:00:00Z', 1, 0)",
            (day,))
        draft = self.get()["draft"]
        draft["trains"] = [row for row in draft["trains"] if row["id"] != "movement-101-b"]
        status, body = self.post(draft)
        self.assertEqual((HTTPStatus.CONFLICT, "local_edit_blocked"), (status, body["code"]), body)
        self.assertIn("Tåg 101 har registrerade driftuppgifter", body["message"])
        self.assertIsNotNone(self.active_row("movement-101-b"))

    def test_a_free_section_can_change_track_type_while_traffic_continues(self):
        draft = self.get()["draft"]
        connection = next(c for c in draft["connections"] if c["id"] == "connection-a-b")
        target = "double" if connection["track_type"] == "single" else "single"
        connection["track_type"] = target
        fingerprint = self.application.engine.config_fingerprint
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertEqual(TrackType(target), self.application.engine.config.connections["connection-a-b"].track_type)
        self.assertNotEqual(fingerprint, self.application.engine.config_fingerprint)
        self.assertEqual(TrackType(target), self.runtime.session_config(self.runtime.active()).connections["connection-a-b"].track_type)
        word = "dubbelspår" if target == "double" else "enkelspår"
        self.assertEqual([f"Sträckan {self.station_name('station-a')}–{self.station_name('station-b')}: {word}"], body["local_edits"]["lines"])

    def test_a_page_that_is_behind_cannot_save(self):
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["note"] = "a"
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        status, body = self.post(draft, expected=0)
        self.assertEqual((HTTPStatus.CONFLICT, "stale_local_edits"), (status, body["code"]))
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data",
                                        {"draft": draft, "expected_revision": 1, "base_publication_id": "cloud-other"})
        self.assertEqual((HTTPStatus.CONFLICT, "stale_local_edits"), (status, body["code"]))
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data/discard", {"expected_revision": 0})
        self.assertEqual((HTTPStatus.CONFLICT, "stale_local_edits"), (status, body["code"]))

    def test_what_cloud_owns_is_refused_with_the_reason(self):
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "9.5"
        status, body = self.post(draft)
        self.assertEqual((HTTPStatus.BAD_REQUEST, "invalid_meet_data"), (status, body["code"]))
        self.assertIn("inte ett klockslag", body["message"])
        self.assertEqual(0, self.runtime.active().local_revision)

    def test_discarding_returns_to_clouds_rows_and_keeps_history_and_a_backup(self):
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "09:30"
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        generation = body["meet_generation"]
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data/discard", {"expected_revision": 1})
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertEqual((True, 0, 0, generation + 1), (body["changed"], body["revision"], body["local_edits"]["count"], body["meet_generation"]))
        self.assertTrue(body["backup"] and Path(body["backup"]).exists(), body)
        self.assertEqual((0, "09:20"), (self.runtime.active().local_revision, self.active_row("movement-101-a")["departure_time"]))
        self.assertEqual([(1, "discarded")], self.runtime._connection.execute(
            "SELECT revision, status FROM local_timetable_edits ORDER BY revision").fetchall())
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "09:31"
        status, body = self.post(draft)
        self.assertEqual((HTTPStatus.OK, 2), (status, body["revision"]), "revisions never repeat, not even after a discard")
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data/discard", {"expected_revision": 2})
        self.assertEqual(HTTPStatus.OK, status, body)
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data/discard", {"expected_revision": 0})
        self.assertEqual((HTTPStatus.OK, False), (status, body["changed"]), "nothing to discard is not an error")

    def test_only_admin_and_never_during_a_simulation(self):
        box = PairedClient(client_id="box", display_name="CDA TMBox", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-a",))
        status, body = dispatch_request(self.application, box, "/v1/meet-data", method="GET")
        self.assertEqual((HTTPStatus.FORBIDDEN, "admin_required"), (status, body["code"]))
        status, body = dispatch_request(self.application, box, "/v1/meet-data/discard", {})
        self.assertEqual((HTTPStatus.FORBIDDEN, "admin_required"), (status, body["code"]))
        draft = self.get()["draft"]
        self.application.simulation = SimpleNamespace(active=True)
        status, body = self.post(draft)
        self.assertEqual((HTTPStatus.CONFLICT, "simulation_active"), (status, body["code"]))

    def test_the_layer_survives_a_restart(self):
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "09:30"
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        reopened = SQLiteRuntimeStore(self.runtime.path)
        self.addCleanup(reopened.close)
        active = reopened.active()
        self.assertEqual(("cloud-first", 1), (active.publication_id, active.local_revision))
        self.assertEqual("09:30", next(r for r in active.payload["trains"] if r["id"] == "movement-101-a")["departure_time"])
        self.assertEqual({"active": True, "revision": 1, "count": 1}, {k: reopened.summary()["local_edits"][k] for k in ("active", "revision", "count")})

    def test_a_newer_cloud_version_ends_the_layer(self):
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["departure_time"] = "09:30"
        status, body = self.post(draft)
        self.assertEqual(HTTPStatus.OK, status, body)
        self.runtime.install(cloud_package("cloud-second"), activate=False)
        self.runtime.activate("cloud-second", preserve_active_day=True)
        active = self.runtime.active()
        self.assertEqual(("cloud-second", 0), (active.publication_id, active.local_revision))
        self.assertEqual(("superseded", "other_publication_activated"), tuple(self.runtime._connection.execute(
            "SELECT status, ended_reason FROM local_timetable_edits WHERE revision = 1").fetchone()))

    def test_the_header_knows_about_local_changes(self):
        self.assertEqual({"active": False, "revision": 0, "count": 0}, self.application.server_context(self.client)["local_edits"])
        draft = self.get()["draft"]
        self.row(draft, "movement-101-a")["note"] = "Extra vagn"
        self.post(draft)
        context = self.application.server_context(self.client)["local_edits"]
        self.assertEqual((True, 1, 1), (context["active"], context["revision"], context["count"]))
        self.assertEqual(1, self.runtime.summary()["local_edits"]["count"])


if __name__ == "__main__":
    unittest.main()
