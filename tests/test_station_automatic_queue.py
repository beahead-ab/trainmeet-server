"""Stationsgemensam automatik tar över redan väntande frågor skyndsamt.

Tre grannar begär in till B på varsitt spår. 201 frågar först men går
senare enligt tidtabellen än 101.
"""
import unittest
from unittest.mock import patch

from runtime_fixture import runtime_package_v3
from test_automatic_stations import AutomaticFixture, at
from test_timetable_placement import add_service
from tmbox_gateway.identity import DeviceKind


class StationQueueTests(AutomaticFixture):
    def package(self):
        package = runtime_package_v3()
        for suffix in ("c", "d"):
            station = f"station-{suffix}"
            package["stations"].append({"id": station, "code": suffix.upper(), "name": station})
            package["display"]["graph_station_order"].append(station)
            package["connections"].append({"id": f"connection-{suffix}-b", "station_a_id": station,
                "station_b_id": "station-b", "track_type": "single"})
            package["tracks"].append({"id": f"track-{station}-1", "station_id": station,
                "display_label": "1", "active": True, "sort_order": 10, "operating_point_id": None})
        for number in (2, 3, 4):
            package["tracks"].append({"id": f"track-station-b-{number}", "station_id": "station-b",
                "display_label": str(number), "active": True, "sort_order": number * 10, "operating_point_id": None})
        next(row for row in package["trains"] if row["id"] == "movement-101-b")["track_id"] = "track-station-b-2"
        add_service(package, "201", [("station-c", None, "09:40", "track-station-c-1"),
                                     ("station-b", "09:55", None, "track-station-b-1")])
        add_service(package, "301", [("station-d", None, "09:45", "track-station-d-1"),
                                     ("station-b", "10:00", None, "track-station-b-3")])
        add_service(package, "401", [("station-b", None, "10:30", "track-station-b-1"),
                                     ("station-d", "10:45", None, "track-station-d-1")])
        return package

    def setUp(self):
        super().setUp()
        self.boxes = {suffix: self.box(f"box-{suffix}", f"station-{suffix}") for suffix in "abcd"}
        self.auto.set_disturbance({"answer_seconds": 30, "stabling": False})
        self.clock(at(9, 18))
        self.auto.tick()

    def request(self, suffix, movement):
        result = self.service.execute_station_command(self.boxes[suffix], f"station-{suffix}",
            "clearance.request", {"movement_id": movement, "connection_id": f"connection-{suffix}-b"})
        return result["revision"]["key"]

    def queue(self):
        return [self.request("c", "movement-201-0"), self.request("a", "movement-101-a"),
                self.request("d", "movement-301-0")]

    def statuses(self, cases):
        return [self.ops.clearance(case)["status"] for case in cases]

    def test_existing_queue_is_answered_next_tick_in_request_order_without_a_new_delay(self):
        cases = self.queue()
        self.auto.hand_back("station-b", by="operator", device=self.boxes["b"])
        with patch.object(self.service, "execute_station_command", wraps=self.service.execute_station_command) as commands:
            self.auto.tick()
            self.auto.tick()
        self.assertEqual(["approved"] * 3, self.statuses(cases))
        responses = [call.args[3]["clearance_id"] for call in commands.call_args_list
                     if call.args[2] == "clearance.response"]
        self.assertEqual(cases, responses, "köordning, varje fråga besvaras bara en gång")

    def test_an_occupied_track_does_not_block_the_independent_request(self):
        self.service.execute_station_command(self.boxes["b"], "station-b", "train.position.set",
            {"movement_id": "movement-401-0"})
        cases = self.queue()
        tkl = self.ids.register_client("tkl-b", "B TKL", DeviceKind.TKL_TERMINAL, "secret", ("panel-b",))
        self.app.tkl_automatic(tkl, {"station_id": "station-b", "automatic": True})
        self.auto.tick()
        self.assertEqual(["waiting", "approved", "approved"], self.statuses(cases))
        # Admin flyttar undan tåget; den väntande frågan finns kvar och
        # besvaras på nästa körning utan en ny svarsfördröjning.
        self.service.execute_station_command(self.admin.client_id, "station-b", "train.track.change",
            {"movement_id": "movement-401-0", "track_id": "track-station-b-4"})
        self.auto.tick()
        self.assertEqual(["approved"] * 3, self.statuses(cases))

    def test_paused_clock_keeps_the_queue_until_the_clock_runs(self):
        cases = self.queue()
        self.clock(at(9, 18), running=False)
        self.auto.hand_back("station-b")
        self.auto.tick()
        self.assertEqual(["waiting"] * 3, self.statuses(cases))
        self.clock(at(9, 18))
        self.auto.tick()
        self.assertEqual(["approved"] * 3, self.statuses(cases))

    def test_new_requests_still_have_the_configured_answer_delay(self):
        self.auto.hand_back("station-b")
        case = self.request("a", "movement-101-a")
        self.auto.tick()
        self.assertEqual(["waiting"], self.statuses([case]))
        self.time += 30
        self.auto.tick()
        self.assertEqual(["approved"], self.statuses([case]))


if __name__ == "__main__":
    unittest.main()
