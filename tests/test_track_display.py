"""Stationens spårvisning för TMBox-appens Spår-vy (TMBox iOS #46).

Visningsvalet är presentation på träffservern, per träff och station. Det
överlever omstart, delas av alla appar på stationen, kräver stationens egen
tilldelning och rör aldrig trafiken, spårkatalogen eller Cloud.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_http_server as fixtures
from runtime_fixture import runtime_package_v3
from tmbox_gateway import track_display
from tmbox_gateway.models import TrackConfig
from tmbox_gateway.runtime import SQLiteRuntimeStore, TrackDisplayConflict


def tracks(*ids):
    return [TrackConfig(track_id, track_id.rsplit("-", 1)[-1], "station-a", sort_order=index)
            for index, track_id in enumerate(ids)]


class EffectiveOrderTests(unittest.TestCase):
    def test_without_a_saved_choice_the_catalogue_order_is_shown_in_full(self):
        result = track_display.effective(tracks("t-1", "t-2", "t-10"), None)
        self.assertEqual([(track.id, visible) for track, visible in result],
                         [("t-1", True), ("t-2", True), ("t-10", True)])

    def test_saved_order_and_hidden_tracks_survive_and_new_tracks_come_last_visible(self):
        saved = {"revision": 3, "order": ["t-10", "t-gone", "t-1", "t-2"], "hidden": ["t-2", "t-gone"]}
        result = track_display.effective(tracks("t-1", "t-2", "t-10", "t-new"), saved)
        self.assertEqual([(track.id, visible) for track, visible in result],
                         [("t-10", True), ("t-1", True), ("t-2", False), ("t-new", True)])

    def test_a_save_must_name_every_current_track_exactly_once(self):
        catalogue = tracks("t-1", "t-2")
        self.assertEqual(track_display.parse_save(catalogue, [{"id": "t-2", "visible": True},
                                                              {"id": "t-1", "visible": False}]),
                         (["t-2", "t-1"], ["t-1"]))
        for malformed in (None, [], [{"id": "t-1"}], [{"id": "t-1", "visible": "ja"}],
                          [{"id": "t-1", "visible": True, "extra": 1}],
                          [{"id": "t-1", "visible": True}, {"id": "t-1", "visible": False}]):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                track_display.parse_save(catalogue, malformed)
        for changed in ([{"id": "t-1", "visible": True}],
                        [{"id": "t-1", "visible": True}, {"id": "t-2", "visible": True}, {"id": "t-3", "visible": True}]):
            with self.subTest(changed=changed), self.assertRaises(KeyError):
                track_display.parse_save(catalogue, changed)


class StoredChoiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "runtime.db"
        self.store = SQLiteRuntimeStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_choices_are_per_meet_and_station_and_survive_a_restart(self):
        self.store.save_track_display("meet-a", "station-a", 0, ["t-2", "t-1"], ["t-1"])
        self.assertEqual(self.store.track_display("meet-b", "station-a"), {"revision": 0})
        self.assertEqual(self.store.track_display("meet-a", "station-b"), {"revision": 0})
        self.store.close()
        self.store = SQLiteRuntimeStore(self.path)
        self.assertEqual(self.store.track_display("meet-a", "station-a"),
                         {"revision": 1, "order": ["t-2", "t-1"], "hidden": ["t-1"]})

    def test_a_stale_revision_never_overwrites_and_reset_keeps_counting(self):
        self.store.save_track_display("meet-a", "station-a", 0, ["t-2", "t-1"], [])
        with self.assertRaises(TrackDisplayConflict) as caught:
            self.store.save_track_display("meet-a", "station-a", 0, ["t-1", "t-2"], [])
        self.assertEqual(caught.exception.revision, 1)
        self.assertEqual(self.store.track_display("meet-a", "station-a")["order"], ["t-2", "t-1"])
        self.assertEqual(self.store.save_track_display("meet-a", "station-a", 1, None), {"revision": 2})
        with self.assertRaises(TrackDisplayConflict):
            self.store.save_track_display("meet-a", "station-a", 1, ["t-1", "t-2"], [])

    def test_the_choice_is_not_a_config_change(self):
        version = self.store.config_version()
        self.store.save_track_display("meet-a", "station-a", 0, ["t-1"], [])
        self.assertEqual(self.store.config_version(), version)


class TrackDisplayHTTPTests(unittest.TestCase):
    """Two phones on the same station, through the real HTTP server."""

    setUp = fixtures.HTTPServerTests.setUp
    tearDown = fixtures.HTTPServerTests.tearDown
    _json_request = fixtures.HTTPServerTests._json_request
    _public_client = fixtures.HTTPServerTests._public_client
    _public_refused = fixtures.HTTPServerTests._public_refused
    _ensure_owner = fixtures.HTTPServerTests._ensure_owner
    _select_package = fixtures.HTTPServerTests._select_package
    _linked_runtime = staticmethod(fixtures.HTTPServerTests._linked_runtime)

    def _phone(self, station_id="station-a", *, device=False):
        phone = self._public_client(client="ios")
        if station_id:
            self._assign(phone, station_id)
        return phone if device else phone["access_token"]

    def _assign(self, phone, station_id):
        self.application.assign_device(self.application.local_admin(),
                                       {"device_code": phone["device_code"], "station_id": station_id})

    def _refused(self, path, payload, token, status):
        request = Request(self.base_url + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, status)
        return json.loads(caught.exception.read().decode())

    def _save(self, token, current, order, hidden=(), **extra):
        return {"meet_id": current["meet_id"], "publication_id": current["publication_id"],
                "station_id": current["station"]["id"], "revision": current["revision"],
                "tracks": [{"id": track_id, "visible": track_id not in hidden} for track_id in order], **extra}

    def test_two_phones_share_the_station_choice_and_the_timetable_follows_it(self):
        first, second = self._phone(), self._phone()
        current = self._json_request("/v1/tmbox/terminal/track-display", token=first)
        self.assertEqual((current["supported"], current["station"]["code"], current["revision"], current["customized"]),
                         (True, "CDA", 0, False))
        self.assertEqual([(t["id"], t["label"], t["visible"]) for t in current["tracks"]],
                         [("track-station-a-1", "1", True), ("track-station-a-2", "2", True)])
        version = self.runtime_store.config_version()
        engine_revision = self.engine.revision

        saved = self._json_request("/v1/tmbox/terminal/track-display", self._save(
            first, current, ["track-station-a-2", "track-station-a-1"], hidden={"track-station-a-1"}), token=first)
        self.assertEqual((saved["revision"], saved["customized"]), (1, True))
        self.assertEqual([(t["id"], t["visible"]) for t in saved["tracks"]],
                         [("track-station-a-2", True), ("track-station-a-1", False)])

        # The other phone sees the same choice, and its timetable follows it.
        self.assertEqual(self._json_request("/v1/tmbox/terminal/track-display", token=second), saved)
        table = self._json_request("/v1/tmbox/terminal/timetable", token=second)
        self.assertEqual(table["tracks"], ["2"], "older apps get the visible tracks in the saved order")
        self.assertEqual(table["track_display"], {key: saved[key] for key in ("revision", "customized", "tracks")})
        # Only presentation changed: every train and track placement is still there.
        by_movement = {row["movement_id"]: row for row in table["rows"]}
        self.assertTrue(by_movement)
        for row in by_movement.values():
            self.assertIn(row["track_id"], {"track-station-a-1", "track-station-a-2", None})
            if row["track_id"]:
                self.assertEqual(row["track"], {"track-station-a-1": "1", "track-station-a-2": "2"}[row["track_id"]])
        self.assertIn("track-station-a-1", {row["track_id"] for row in table["rows"]},
                      "a train on a hidden track keeps its row")
        self.assertEqual([t.id for t in self.application.station_service.session_config().tracks_for_station("station-a")],
                         ["track-station-a-1", "track-station-a-2"], "the catalogue and track choices are unchanged")
        self.assertEqual(self.runtime_store.config_version(), version)
        self.assertEqual(self.engine.revision, engine_revision)

        # The first phone saved from revision 0 once; a second save from it is a conflict.
        conflict = self._refused("/v1/tmbox/terminal/track-display", self._save(
            first, current, ["track-station-a-1", "track-station-a-2"]), first, 409)
        self.assertEqual(conflict["error"], "revision_conflict")
        self.assertEqual(conflict["current"]["revision"], 1)
        self.assertEqual(self._json_request("/v1/tmbox/terminal/track-display", token=first)["revision"], 1)

        # Reset returns to the meet's base order with every track shown.
        reset = self._json_request("/v1/tmbox/terminal/track-display/reset",
                                   {key: value for key, value in self._save(first, saved, []).items() if key != "tracks"},
                                   token=first)
        self.assertEqual((reset["revision"], reset["customized"]), (2, False))
        self.assertEqual([(t["id"], t["visible"]) for t in reset["tracks"]],
                         [("track-station-a-1", True), ("track-station-a-2", True)])
        self.assertEqual(self._json_request("/v1/tmbox/terminal/timetable", token=second)["tracks"], ["1", "2"])

    def test_only_an_assigned_box_changes_only_its_own_station(self):
        unassigned = self._phone(station_id=None)
        waiting = self._json_request("/v1/tmbox/terminal/track-display", token=unassigned)
        self.assertEqual((waiting["station"], waiting["tracks"]), (None, []))
        payload = {"meet_id": "meet-a", "publication_id": waiting["publication_id"], "station_id": "station-a",
                   "revision": 0, "tracks": [{"id": "track-station-a-1", "visible": False},
                                             {"id": "track-station-a-2", "visible": True}]}
        self.assertEqual(self._refused("/v1/tmbox/terminal/track-display", payload, unassigned, 409)["error"],
                         "station_not_assigned")

        other = self._phone("station-b")
        # The station comes from the assignment, never from the request.
        self.assertEqual(self._refused("/v1/tmbox/terminal/track-display", payload, other, 409)["error"],
                         "station_changed")
        self.assertEqual(self.runtime_store.track_display("meet-a", "station-a"), {"revision": 0})
        self._public_refused("/v1/tmbox/terminal/track-display", status=401)
        self._public_refused("/v1/tmbox/terminal/track-display", payload, status=401)

        # A web administrator is not a box: no station of its own here.
        self._json_request("/v1/auth/login", {"email": "admin@example.se", "password": "test-password"})
        with self.assertRaises(HTTPError) as caught:
            self._json_request("/v1/tmbox/terminal/track-display")
        self.assertEqual(caught.exception.code, 403)

    def test_reassignment_publication_and_payload_are_checked_on_every_write(self):
        phone = self._phone(device=True)
        token = phone["access_token"]
        current = self._json_request("/v1/tmbox/terminal/track-display", token=token)
        order = ["track-station-a-2", "track-station-a-1"]
        for change, error in (({"publication_id": "old-publication"}, "stale_publication"),
                              ({"meet_id": "another-meet"}, "stale_publication")):
            with self.subTest(change=change):
                self.assertEqual(self._refused("/v1/tmbox/terminal/track-display",
                                               self._save(token, current, order, **change), token, 409)["error"], error)
        self.assertEqual(self._refused("/v1/tmbox/terminal/track-display",
                                       self._save(token, current, order[:1]), token, 409)["error"], "tracks_changed")
        for broken in ({"revision": "0"}, {"tracks": [{"id": "track-station-a-1", "visible": True}] * 2}):
            with self.subTest(broken=broken):
                self.assertEqual(self._refused("/v1/tmbox/terminal/track-display",
                                               self._save(token, current, order, **broken), token, 400)["error"],
                                 "invalid_track_display")
        self.assertEqual(self.runtime_store.track_display("meet-a", "station-a"), {"revision": 0})

        self._json_request("/v1/tmbox/terminal/track-display", self._save(token, current, order), token=token)
        self._assign(phone, "station-b")
        moved = self._json_request("/v1/tmbox/terminal/track-display", token=token)
        self.assertEqual((moved["station"]["code"], moved["customized"]), ("LEK", False))
        self.assertEqual(self._refused("/v1/tmbox/terminal/track-display",
                                       self._save(token, current, order), token, 409)["error"], "station_changed")

    def test_a_new_publication_of_the_same_meet_keeps_choices_for_remaining_tracks(self):
        self._ensure_owner()
        self._json_request("/v1/auth/login", {"email": "admin@example.se", "password": "test-password"})
        token = self._phone()
        current = self._json_request("/v1/tmbox/terminal/track-display", token=token)
        self._json_request("/v1/tmbox/terminal/track-display", self._save(
            token, current, ["track-station-a-2", "track-station-a-1"], hidden={"track-station-a-1"}), token=token)

        package = deepcopy(runtime_package_v3(publication_id="publication-with-track-3"))
        package["tracks"].append({"id": "track-station-a-3", "display_label": "3", "station_id": "station-a",
                                  "operating_point_id": None, "active": True, "sort_order": 30})
        self._select_package(package)
        after = self._json_request("/v1/tmbox/terminal/track-display", token=token)
        self.assertEqual(after["publication_id"], "publication-with-track-3")
        self.assertEqual((after["revision"], after["customized"]), (1, True))
        self.assertEqual([(t["id"], t["visible"]) for t in after["tracks"]],
                         [("track-station-a-2", True), ("track-station-a-1", False), ("track-station-a-3", True)])
        self.assertEqual(self._json_request("/v1/tmbox/terminal/timetable", token=token)["tracks"], ["2", "3"])

        # A track removed in Cloud leaves no ghost row.
        package = deepcopy(package)
        package["publication_id"] = "publication-without-track-2"
        package["tracks"] = [t for t in package["tracks"] if t["id"] != "track-station-a-2"]
        package["trains"] = [t for t in package["trains"] if t.get("track_id") != "track-station-a-2"]
        self._select_package(package)
        after = self._json_request("/v1/tmbox/terminal/track-display", token=token)
        self.assertEqual([(t["id"], t["visible"]) for t in after["tracks"]],
                         [("track-station-a-1", False), ("track-station-a-3", True)])


if __name__ == "__main__":
    unittest.main()
