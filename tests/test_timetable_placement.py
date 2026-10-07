"""Tågen står där tidtabellen säger (#136).

Efter en nollställning, vid träffens start och när admin ställer klockan står
varje tåg på den station tidtabellen anger vid klockans tid, på stoppets spår,
och räknas som ankommet där. Ett tåg som enligt tidtabellen är ute på linjen
saknar klarering och står kvar på avgångsstationen. Lägena är riktiga, men
spärrarna för en ny Cloud-version och den lokala redigeringen räknar dem inte
som trafikläge. Ett tåg med verkliga händelser rörs aldrig, och automatiska
stationer fortsätter från lägena utan att köra något tåg två gånger.

Banan: 101 A 09:20 → B 09:35 (slutar på B spår 1), 301 A 09:40 → B 09:55/10:00
→ A 10:15 och 102 B 10:30 från spår 1, samma tågsätt som 101 vänder. Planen
börjar 09:15.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime_fixture import runtime_package_v3
from tmbox_gateway.automatic import ACTOR
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublication, SQLiteRuntimeStore
from tmbox_gateway.timetable_placement import timetable_positions


def at(hours, minutes, seconds=0):
    return hours * 3600 + minutes * 60 + seconds


def add_service(package, number, stops):
    """stops: [(station, ankomst, avgång, spår)] i ordning."""
    service_id = f"service-{number}-Dagl"
    service = {"id": service_id, "train_number": number, "days": "Dagl", "train_type": "person", "stops": []}
    for order, (station, arrival, departure, track) in enumerate(stops):
        minute = int((departure or arrival)[:2]) * 60 + int((departure or arrival)[3:])
        service["stops"].append({"station_id": station, "station_name": station, "stop_order": order,
                                 "arrival_time": arrival, "departure_time": departure, "service_day_offset": 0,
                                 "service_minute": minute})
        package["routes"].append({"id": f"route-{number}-{order}", "train_number": number, "station_id": station,
                                  "station_name": station, "stop_order": order, "arrival_time": arrival,
                                  "departure_time": departure, "service_id": service_id, "days": "Dagl",
                                  "service_day_offset": 0, "service_minute": minute})
        package["trains"].append({"id": f"movement-{number}-{order}", "train_number": number, "station_id": station,
                                  "station": station, "track_id": track, "days": "Dagl", "arrival_time": arrival,
                                  "departure_time": departure, "arrival_from": None, "departure_to": None,
                                  "sort_time": departure or arrival, "no_stop": False, "note": None,
                                  "manual_sort_order": 0, "service_id": service_id, "stop_order": order})
    package["services"].append(service)


def package():
    result = runtime_package_v3()
    result["tracks"].append({"id": "track-station-b-2", "display_label": "2", "station_id": "station-b",
                             "operating_point_id": None, "active": True, "sort_order": 20})
    add_service(result, "301", [("station-a", None, "09:40", "track-station-a-2"),
                                ("station-b", "09:55", "10:00", "track-station-b-2"),
                                ("station-a", "10:15", None, "track-station-a-2")])
    add_service(result, "102", [("station-b", None, "10:30", "track-station-b-1"),
                                ("station-a", "10:45", None, "track-station-a-1")])
    return result


class TimetablePlacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)
        self.pub = self.runtime.install(package())
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)
        self.app = TrainMeetHTTPApplication(TrafficEngine(self.pub.session_config()), self.ids,
            PairingService(self.ids, {"panel-a", "panel-b"}), HTTPServerConfig(local_development=True),
            runtime_store=self.runtime, operations_store=self.ops)
        self.service = self.app.station_service
        self.auto = self.app.automatic
        self.auto.set_enabled(False)
        self.admin = self.app.local_admin()
        self.ops.ensure_publication(self.pub)
        self.day = self.runtime.active_day()  # Lör; tågen går Dagl

    def tearDown(self):
        self.ops.close()
        self.ids.close()
        self.runtime.close()
        self.app.lifecycle.close()
        self.temp.cleanup()

    def set_clock(self, seconds, running=False):
        value = f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
        self.app.control_clock(self.admin, {"action": "start" if running else "set", "time": value,
                                            "meet_generation": self.app.lifecycle.selected()["generation"]})

    def where(self):
        return {p["train_number"]: (p["status"], p["station_id"]) for p in self.ops.positions()}

    def state(self, movement):
        station = next(r["station_id"] for r in self.pub.payload["trains"] if r["id"] == movement)
        return self.ops.tkl_station_state(self.pub.publication_id, self.day, station)["movements"].get(movement, {})

    def summary(self, movement):
        state = self.state(movement)
        return (state.get("arrival", "none"), state.get("departure", "none"), state.get("actualTrack"))

    # ---------------------------------------------------------------- lägen

    def test_after_a_reset_every_train_stands_at_its_first_station(self):
        self.ops.reset_meet(self.pub, self.day)
        self.assertEqual({"101": ("station", "station-a"), "301": ("station", "station-a")}, self.where(),
                         "102 is 101 turned round: it is not on B until 101 has come in")
        self.assertEqual(("none", "positioned", "track-station-a-1"), self.summary("movement-101-a"))
        self.assertEqual(("none", "positioned", "track-station-a-2"), self.summary("movement-301-0"))
        self.assertEqual({}, self.state("movement-301-1"), "nothing yet where it has not been")
        self.assertEqual("tidtabell", self.state("movement-101-a")["updated_by"])
        # Kartorna räknar dem: två tåg inne på A.
        snapshot = self.app.display_snapshot()
        self.assertEqual(2, sum(1 for p in snapshot["train_positions"] if p["station_id"] == "station-a"))
        # Ingen historik: inget har hänt.
        self.assertEqual(0, self.ops._connection.execute("SELECT COUNT(*) FROM tkl_events").fetchone()[0])  # noqa: SLF001

    def test_setting_the_clock_puts_each_train_where_the_timetable_says_on_its_track(self):
        self.set_clock(at(9, 57))
        self.assertEqual({"101": ("station", "station-b"), "301": ("station", "station-b"),
                          "102": ("station", "station-b")}, self.where())
        # 101 har kommit fram och står på B spår 1; resan dit är gjord.
        self.assertEqual(("none", "departed", "track-station-a-1"), self.summary("movement-101-a"))
        self.assertEqual(("arrived", "none", "track-station-b-1"), self.summary("movement-101-b"))
        # 301 står på B spår 2, ankommen och uppställd för avgången 10:00.
        self.assertEqual(("arrived", "positioned", "track-station-b-2"), self.summary("movement-301-1"))
        self.assertEqual(("none", "departed", "track-station-a-2"), self.summary("movement-301-0"))
        # 102 står nu uppställd på spår 1, där 101 kom in.
        self.assertEqual(("none", "positioned", "track-station-b-1"), self.summary("movement-102-0"))

    def test_a_train_on_the_line_by_the_timetable_waits_at_its_departure_station(self):
        self.set_clock(at(9, 25))
        self.assertEqual(("station", "station-a"), self.where()["101"])
        self.assertEqual(("none", "positioned", "track-station-a-1"), self.summary("movement-101-a"))
        self.assertEqual([], self.service.open_cases(None), "no clearance is made up")
        self.assertEqual({"free"}, {line["state"] for line in self.app.display_snapshot()["connection_states"]})

    def test_after_its_last_arrival_a_train_stands_at_its_last_station(self):
        self.set_clock(at(10, 20))
        self.assertEqual(("station", "station-a"), self.where()["301"])
        self.assertEqual(("arrived", "none", "track-station-a-2"), self.summary("movement-301-2"))
        self.assertEqual(("arrived", "departed", "track-station-b-2"), self.summary("movement-301-1"))

    def test_moving_the_clock_back_moves_the_placed_trains_back(self):
        self.set_clock(at(10, 20))
        self.set_clock(at(9, 30))
        self.assertEqual({"101": ("station", "station-a"), "301": ("station", "station-a")}, self.where())
        self.assertEqual({}, self.state("movement-301-2"))
        self.assertEqual({}, self.state("movement-101-b"))

    def test_a_train_with_real_events_is_never_moved(self):
        self.set_clock(at(9, 17))
        # Tågklareraren flyttar 101 till spår 2: en verklig händelse.
        self.ops.update_tkl_movement(self.pub.publication_id, self.day, "station-a", "movement-101-a", arrival="none",
                                     departure="positioned", actual_track="track-station-a-2", updated_by="tkl-a",
                                     shift_id=None, event_type="train.track.change")
        self.set_clock(at(10, 20))
        self.assertEqual(("station", "station-a"), self.where()["101"], "the placed position stays")
        self.assertEqual(("none", "positioned", "track-station-a-2"), self.summary("movement-101-a"))
        self.assertEqual({}, self.state("movement-101-b"))
        self.assertEqual(("station", "station-a"), self.where()["301"], "the others still follow the clock")
        self.assertEqual(("arrived", "none", "track-station-a-2"), self.summary("movement-301-2"))

    def test_a_train_that_has_left_for_real_keeps_its_line(self):
        self.set_clock(at(9, 17))
        self.service.execute_station_command(ACTOR, "station-a", "clearance.request",
                                             {"movement_id": "movement-101-a", "connection_id": "connection-a-b"})
        case = self.service.open_cases(None)[0]
        self.service.execute_station_command(ACTOR, "station-b", "clearance.response",
                                             {"clearance_id": case["clearance_id"], "approved": True})
        self.service.execute_station_command(ACTOR, "station-a", "train.departed", {"movement_id": "movement-101-a"})
        self.set_clock(at(10, 20))
        self.assertEqual(("connection", None), self.where()["101"])
        self.assertEqual("occupied", {line["id"]: line["state"] for line in
                                      self.app.display_snapshot()["connection_states"]}["connection-a-b"])

    def test_the_same_time_again_changes_nothing_a_tkl_has_read(self):
        """Justera… skickar alltid tiden, även när bara hastigheten ändras."""
        self.set_clock(at(9, 57))
        before = self.state("movement-301-1")["revision"]
        self.set_clock(at(9, 57))
        self.assertEqual(before, self.state("movement-301-1")["revision"], "unchanged, so still current")
        self.set_clock(at(10, 20))
        self.assertGreater(self.state("movement-301-1")["revision"], before, "changed, so a stale TKL is told")

    def test_a_new_traffic_day_puts_that_days_trains_in_place(self):
        self.set_clock(at(10, 12))
        self.assertNotIn("202", self.where(), "202 runs on Sundays only")
        self.app.set_active_day(self.admin, {"active_day": "Sön",
                                             "meet_generation": self.app.lifecycle.selected()["generation"]})
        self.assertEqual(("station", "station-a"), self.where()["202"])
        state = self.ops.tkl_station_state(self.pub.publication_id, "Sön", "station-a")["movements"]["movement-202-a"]
        self.assertEqual(("arrived", "positioned", "track-station-a-2"),
                         (state["arrival"], state["departure"], state["actualTrack"]))

    # -------------------------------------------------------------- spärrar

    def test_a_new_cloud_version_without_a_placed_train_is_not_blocked(self):
        self.set_clock(at(10, 20))
        newer = deepcopy(self.pub.payload)
        newer["publication_id"] = "publication-utan-301"
        newer["trains"] = [row for row in newer["trains"] if row["train_number"] != "301"]
        newer["routes"] = [row for row in newer["routes"] if row["train_number"] != "301"]
        newer["services"] = [row for row in newer["services"] if row["train_number"] != "301"]
        self.assertEqual([], self.ops.config_update_blockers(self.pub, RuntimePublication.parse(newer)))

    def test_a_new_cloud_version_without_a_train_with_events_is_still_blocked(self):
        self.set_clock(at(9, 17))
        self.ops.update_tkl_movement(self.pub.publication_id, self.day, "station-a", "movement-301-0", arrival="none",
                                     departure="positioned", actual_track=None, updated_by="tkl-a", shift_id=None,
                                     event_type="train.position.set")
        self.ops.record_traffic_position("301", status="station", station_id="station-a")
        newer = deepcopy(self.pub.payload)
        newer["publication_id"] = "publication-utan-301"
        for key in ("trains", "routes", "services"):
            newer[key] = [row for row in newer[key] if row["train_number"] != "301"]
        blockers = self.ops.config_update_blockers(self.pub, RuntimePublication.parse(newer))
        self.assertIn("Config tar bort ett tåg eller en station med registrerat trafikläge.", blockers)

    def test_a_local_edit_may_remove_a_placed_movement(self):
        self.set_clock(at(10, 20))
        self.assertEqual([], self.ops.local_edit_blockers(self.pub.publication_id, changed_movements={},
            removed_movements={"movement-301-1": "301"}, changed_connections={}))

    # ------------------------------------------------------------ automatik

    def test_automatic_stations_continue_from_the_placed_trains(self):
        self.auto.set_enabled(True)
        self.auto.now = lambda: 1000.0
        self.ops.reset_meet(self.pub, self.day)
        self.auto.forget_meet()
        # Klockan ställs till 09:45: 101 har kommit till B, 301 skulle ha gått
        # 09:40 men står kvar på A utan klarering.
        self.set_clock(at(9, 45), running=True)
        self.auto.tick()
        sent = [case for case in self.service.open_cases(None)]
        self.assertEqual(["movement-301-0"], [case["movement_id"] for case in sent],
                         "301 is sent at once; 101 is already in and is never sent again")
        self.assertEqual("departed", self.state("movement-301-0")["departure"])
        self.ops.configure_clock(time_value="10:01:00", running=True)
        self.auto.tick()
        self.assertEqual("arrived", self.state("movement-301-1")["arrival"])
        clearances = self.ops._connection.execute("SELECT movement_id FROM clearances").fetchall()  # noqa: SLF001
        self.assertNotIn(("movement-101-a",), clearances)


class TimetablePositionsTests(unittest.TestCase):
    """Uträkningen för sig, utan databas."""

    def setUp(self):
        self.payload = RuntimePublication.parse(package()).payload

    def test_a_train_coming_from_outside_is_not_there_before_its_arrival(self):
        payload = deepcopy(self.payload)
        add_service(payload, "505", [("station-b", "11:00", "11:05", "track-station-b-2"),
                                     ("station-a", "11:20", None, "track-station-a-1")])
        self.assertIsNone(timetable_positions(payload, "Dagl", at(10, 50))["505"]["station_id"])
        self.assertEqual("station-b", timetable_positions(payload, "Dagl", at(11, 1))["505"]["station_id"])

    def test_the_same_number_twice_a_day_uses_the_run_that_has_begun(self):
        payload = deepcopy(self.payload)
        add_service(payload, "777", [("station-a", None, "09:30", "track-station-a-2"),
                                     ("station-b", "09:45", None, "track-station-b-2")])
        evening = deepcopy(payload)
        add_service(evening, "777", [("station-b", None, "18:00", "track-station-b-2"),
                                     ("station-a", "18:15", None, "track-station-a-2")])
        evening["services"][-1]["id"] = "service-777-kvall"
        for row in evening["trains"][-2:]:
            row["service_id"] = "service-777-kvall"
            row["id"] += "-kvall"
        self.assertEqual("station-a", timetable_positions(evening, "Dagl", at(9, 0))["777"]["station_id"])
        self.assertEqual("station-b", timetable_positions(evening, "Dagl", at(12, 0))["777"]["station_id"])
        self.assertEqual("station-a", timetable_positions(evening, "Dagl", at(18, 30))["777"]["station_id"])


if __name__ == "__main__":
    unittest.main()
