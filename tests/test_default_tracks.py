"""Standardspår när tidtabellen saknar spår (4.9.0).

Casper 2026-10-15: stationen har standardspår för persontåg, godståg och tåg
som passerar, åt vänster och åt höger, med flera spår per ruta: godståg åt
höger kommer in på godsbangårdens 11–13. Tidtabellens spår går alltid före.
Servern tar det första lediga standardspåret vid ankomst, vid utplacering
och i automatiken, och spårvalen visar tågets spår först, sedan
standardspåren och sist resten.

Banan: A (CDA) – B (LEK), B med spår 1 och 2 och godsbangården RBG 11 och 12.
101 är persontåget från fixturen, planerat på B spår 1. 5101 och 5103 är
godståg A → B utan spår på B, 303 ett persontåg utan spår på B.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest

import test_shared_traffic
from runtime_fixture import runtime_package_v3
from test_timetable_placement import add_service, at
from tmbox_gateway import default_tracks
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.legs import plan_legs
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import CommandRejected
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.terminal16_runtime import Terminal16Service
from tmbox_gateway.timetable_placement import timetable_positions

B_TRACKS = [("track-station-b-2", "2", 20, None), ("track-b-11", "11", 10, "op-rbg"), ("track-b-12", "12", 20, "op-rbg")]
DEFAULTS = {"goods": {"right": ["track-b-11", "track-b-12"]}, "person": {"right": ["track-station-b-2"]}}


def with_defaults(package, defaults=DEFAULTS):
    """B:s spår och standardspår; A ligger till vänster om B, så tåg från A kör åt höger."""
    for track_id, label, order, point in B_TRACKS:
        package["tracks"].append({"id": track_id, "display_label": label, "station_id": "station-b",
                                  "operating_point_id": point, "active": True, "sort_order": order})
    station = next(item for item in package["stations"] if item["id"] == "station-b")
    station.setdefault("operating_points", []).append({"id": "op-rbg", "code": "RBG", "name": "RBG", "kind": "yard"})
    if defaults:
        station["default_tracks"] = deepcopy(defaults)
        station["default_track_sides"] = {"left": ["station-a"], "right": []}
    return package


def package(defaults=DEFAULTS):
    result = with_defaults(runtime_package_v3(), defaults)
    add_service(result, "5101", [("station-a", None, "09:30", "track-station-a-1"), ("station-b", "09:45", None, None)])
    add_service(result, "5103", [("station-a", None, "09:32", "track-station-a-2"), ("station-b", "09:50", None, None)])
    add_service(result, "303", [("station-a", None, "09:50", "track-station-a-2"), ("station-b", "10:05", None, None)])
    for row in result["trains"]:
        if row["train_number"] in {"5101", "5103"}:
            row["train_type"] = "goods"
    return result


class DefaultTrackServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)

    def start(self, data):
        self.pub = self.runtime.install(data)
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)
        self.app = TrainMeetHTTPApplication(TrafficEngine(self.pub.session_config()), self.ids,
            PairingService(self.ids, {"panel-a", "panel-b"}), HTTPServerConfig(local_development=True),
            runtime_store=self.runtime, operations_store=self.ops)
        self.service = self.app.station_service
        self.app.automatic.set_enabled(False)
        self.ops.ensure_publication(self.pub)
        self.day = self.runtime.active_day()

    def tearDown(self):
        if hasattr(self, "app"):
            self.ops.close()
            self.ids.close()
            self.app.lifecycle.close()
        self.runtime.close()
        self.temp.cleanup()

    def row(self, number, station="station-b"):
        return next(r for r in self.pub.payload["trains"] if r["train_number"] == number and r["station_id"] == station)

    def arrive(self, number, **body):
        row = self.row(number)
        self.service.execute_station_command("tkl-b", "station-b", "train.arrived", {"movement_id": row["id"], **body})
        return self.ops.tkl_station_state(self.pub.publication_id, self.day, "station-b")["movements"][row["id"]]["actualTrack"]

    def test_the_train_gets_its_kind_and_directions_tracks_and_a_planned_track_comes_first(self):
        self.start(package())
        payload = self.pub.payload
        self.assertEqual(["track-b-11", "track-b-12"], default_tracks.candidates(payload, self.row("5101")))
        self.assertEqual(["track-station-b-2"], default_tracks.candidates(payload, self.row("303")))
        self.assertEqual([], default_tracks.candidates(payload, self.row("5101", "station-a")), "A has no default tracks")
        self.assertEqual(["track-station-b-1", "track-b-11", "track-b-12", "track-station-b-2"],
                         default_tracks.ordered("track-station-b-1", ["track-b-11", "track-b-12"],
                                                ["track-station-b-1", "track-station-b-2", "track-b-11", "track-b-12"]))

    def test_an_arrival_without_a_track_takes_the_first_free_default_track(self):
        self.start(package())
        self.assertEqual("track-b-11", self.arrive("5101"))
        self.assertEqual("track-b-12", self.arrive("5103"), "11 is taken by 5101")
        self.assertEqual("track-station-b-2", self.arrive("303"), "a passenger train takes the passenger track")

    def test_the_stations_order_comes_first_and_the_reserve_when_the_box_is_full(self):
        # Charlottendal: 2b före 2a, och reservspår 3 när rutan är full. Här
        # godståg åt höger på 12 före 11, och spår 2 som reserv för alla tåg.
        self.start(package({"goods": {"right": ["track-b-12", "track-b-11"]}, "reserve": ["track-station-b-2"]}))
        self.assertEqual(["track-b-12", "track-b-11", "track-station-b-2"], default_tracks.candidates(self.pub.payload, self.row("5101")))
        self.assertEqual(["track-station-b-2"], default_tracks.candidates(self.pub.payload, self.row("303")),
                         "a passenger train without a box of its own takes the reserve")
        rows = {(r["train_number"], r["station_id"]): r for r in self.pub.timetable(active_day=self.day)["trains"]}
        self.assertEqual(["12", "11", "2"], rows[("5101", "station-b")]["default_tracks"])
        self.assertEqual("track-b-12", self.arrive("5101"), "the station's first choice, not the catalogue's")
        self.assertEqual("track-b-11", self.arrive("5103"))
        self.assertEqual("track-station-b-2", self.arrive("303"))

    def test_a_full_box_and_reserve_give_the_first_choice_as_before(self):
        self.start(package({"goods": {"right": ["track-b-11"]}, "reserve": ["track-b-12"]}))
        self.assertEqual("track-b-11", self.arrive("5101"))
        self.assertEqual("track-b-12", self.arrive("5103"), "11 is taken: the reserve")
        self.assertEqual("track-b-11", default_tracks.choose(self.pub.payload, self.row("5103"), lambda track: True),
                         "everything taken: the first choice, as with a planned track that is taken")

    def test_the_timetables_track_and_a_chosen_track_always_win(self):
        self.start(package())
        self.assertEqual("track-station-b-1", self.arrive("101"), "101 is planned on 1")
        self.assertEqual("track-station-b-2", self.arrive("5101", track_id="track-station-b-2"))

    def test_without_default_tracks_an_arrival_without_a_track_is_refused_as_before(self):
        self.start(package(defaults=None))
        with self.assertRaises(CommandRejected) as refused:
            self.arrive("5101")
        self.assertEqual("unknown_track", refused.exception.reason)

    def test_automatic_stations_run_a_train_without_a_track_and_take_the_first_free(self):
        self.start(package())
        legs, errors = plan_legs(self.pub, self.day)
        leg = legs[self.row("5101", "station-a")["id"]]
        self.assertIsNone(leg["track_id"], "chosen when the train comes in")
        self.assertFalse([error for error in errors if "5101" in error or "5103" in error])
        self.assertEqual("track-b-11", self.app.automatic._arrival_track(self.pub, self.day, "station-b", leg))  # noqa: SLF001
        self.arrive("5101")
        other = legs[self.row("5103", "station-a")["id"]]
        self.assertEqual("track-b-12", self.app.automatic._arrival_track(self.pub, self.day, "station-b", other))  # noqa: SLF001
        self.assertEqual("track-station-b-1", legs["movement-101-a"]["track_id"], "a planned track is still the leg's")

    def test_without_default_tracks_the_automation_leaves_a_train_without_a_track_to_people(self):
        self.start(package(defaults=None))
        legs, errors = plan_legs(self.pub, self.day)
        self.assertNotIn(self.row("5101", "station-a")["id"], legs)
        self.assertTrue(any("5101" in error and "Aktivt planerat spår saknas" in error for error in errors))

    def test_the_timetable_puts_trains_without_a_track_on_separate_default_tracks(self):
        self.start(package())
        positions = timetable_positions(self.pub.payload, self.day, at(9, 55))
        self.assertEqual(("station-b", "track-b-11"), (positions["5101"]["station_id"], positions["5101"]["track_id"]))
        self.assertEqual(("station-b", "track-b-12"), (positions["5103"]["station_id"], positions["5103"]["track_id"]))
        self.assertEqual("track-b-12", positions["5103"]["states"][self.row("5103")["id"]]["track_id"])
        self.assertEqual("track-station-b-1", positions["101"]["track_id"])

    def test_clients_get_the_default_tracks_and_the_station_order_puts_the_yard_last(self):
        self.start(package())
        rows = {(r["train_number"], r["station_id"]): r for r in self.pub.timetable(active_day=self.day)["trains"]}
        self.assertEqual(["11", "12"], rows[("5101", "station-b")]["default_tracks"])
        self.assertEqual(["2"], rows[("101", "station-b")]["default_tracks"], "beside its own planned track")
        self.assertNotIn("default_tracks", rows[("5101", "station-a")])
        self.assertEqual(["1", "2", "11", "12"],
                         [t.display_label for t in self.pub.session_config().tracks_for_station("station-b")])


class DefaultTrackTerminalTests(unittest.TestCase):
    """Boxens spårval (16×2 och iPhone-knappsatsen): tågets spår först och därifrån."""

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        data = with_defaults(test_shared_traffic.runtime_package_v3())
        data["publication_id"] = "default-tracks"
        next(r for r in data["trains"] if r["id"] == "movement-101-b")["track_id"] = None
        self.fixture.install(data)
        self.terminals = Terminal16Service(self.fixture.service)

    def tearDown(self):
        self.fixture.tearDown()

    def send(self, device, key, **extra):
        frame = self.terminals.frame(device)
        body = {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": key, **extra}
        if "train_number" in extra:
            body["entry_context"] = frame["entry"]["context"]
        answer = self.terminals.command(device, body)
        self.assertEqual(answer["status"], "accepted", answer)
        return answer

    def depart(self):
        self.send("esp8266", "#", train_number="101")
        self.send("esp32", "#")
        self.send("esp8266", "#")

    def test_the_picker_starts_on_the_default_track_and_offers_the_defaults_first(self):
        self.depart()
        self.send("esp32", "B")
        views = self.terminals.views
        choices = [track.display_label for track in views._track_choices(views.terminals["esp32"])]  # noqa: SLF001
        self.assertEqual(["2", "1", "11", "12"], choices, "101 has no track: its default 2 first, then the rest in order")
        self.assertIn("101 SPÅR 2", self.terminals.frame("esp32")["lines"][0])

    def test_hash_takes_a_train_without_a_track_in_on_its_default_track(self):
        self.depart()
        self.send("esp32", "#")
        state = self.fixture.ops.tkl_station_state(self.fixture.publication.publication_id, "Lör", "station-b")
        self.assertEqual("track-station-b-2", state["movements"]["movement-101-b"]["actualTrack"])

    def test_the_station_timetable_lists_every_track_and_where_the_train_stands(self):
        self.depart()
        self.send("esp32", "#")
        table = self.terminals.timetable("esp32")
        self.assertEqual(["1", "2", "11", "12"], table["tracks"], "empty tracks too, the yard last")
        row = next(item for item in table["rows"] if item["train_number"] == "101")
        self.assertEqual((None, "2"), (row["track"], row["actual_track"]))


if __name__ == "__main__":
    unittest.main()
