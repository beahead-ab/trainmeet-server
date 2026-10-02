"""The system follows the game instead of stopping it (Casper, 2026-10-02).

A train someone lost track of is where it is: sent on from where it stands,
it jumps there first, and the receiver can place one nobody sent. Every
earlier part of its route still open is finished - its request withdrawn or
clearance released, so the line is free, and the departure there recorded.
Only the clearance itself is still required to send a train on.

Until 2.1.0 the server refused these (train_not_arrived, train_not_departed,
track_occupied) and the boxes hid #Avg until the arrival was recorded.
"""
from unittest.mock import MagicMock
import unittest

import test_shared_traffic
from test_terminal16_runtime import through_package, unmanned_package
from tmbox_gateway.http_server import HTTPAPIError

CDA, LEK = "station-a", "station-b"
IN_FROM_LEK, AT_CDA = "movement-102-0", "movement-102-1"   # LEK -> CDA -> LEK


class TrainJumpsAheadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def install(self, package=None):
        self.fixture.install(package or through_package())
        self.service, self.app, self.admin = self.fixture.service, self.fixture.app, self.fixture.admin
        self.fixture.start_shift(CDA)

    def accepted(self, action, body, device):
        ack = self.fixture.v2(action, body, device)
        self.assertEqual("accepted", ack["status"], ack)
        return ack

    def request(self, movement, sender):
        self.accepted("clearance.request", {"movement_id": movement, "connection_id": "connection-a-b"}, sender)
        return next(c for c in self.service.open_cases(None) if c["movement_id"] == movement)

    def clear(self, movement, sender, receiver):
        case = self.request(movement, sender)
        self.accepted("clearance.response", {"clearance_id": case["clearance_id"], "approved": True}, receiver)
        return case

    def state(self, station=CDA, movement=AT_CDA):
        return next(m for m in self.service.snapshot_payload(station)["movements"] if m["id"] == movement)

    def tkl_depart(self):
        return self.app.tkl_clearance_action(self.admin, {"station_id": CDA, "connection_id": "connection-a-b", "action": "depart"})

    def assert_jumped(self):
        """102 is at CDA and gone on: LEK's part is over, and it is on the line to LEK."""
        self.assertEqual(("arrived", "departed"), (self.state()["arrival"], self.state()["departure"]))
        self.assertEqual("departed", self.state(LEK, IN_FROM_LEK)["departure"])
        position = next(p for p in self.fixture.ops.positions() if p["train_number"] == "102")
        self.assertEqual(("connection", LEK), (position["status"], position["to_station_id"]))

    def test_tkl_sends_on_a_train_never_seen_to_come_and_it_jumps_here(self):
        self.install()
        self.clear(AT_CDA, "esp32-a", "esp32")           # asked for before it came
        self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA, "departure": "ready"})
        self.assertIn("train.departed", self.state()["allowed_actions"])
        self.tkl_depart()
        self.assert_jumped()

    def test_the_tkl_form_does_the_same(self):
        self.install()
        self.clear(AT_CDA, "esp32-a", "esp32")
        self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA, "departure": "departed"})
        self.assert_jumped()

    def test_a_box_does_the_same(self):
        self.install()
        self.clear(AT_CDA, "esp32-a", "esp32")
        self.accepted("train.departed", {"movement_id": AT_CDA}, "esp32-a")
        self.assert_jumped()

    def test_the_clearance_is_still_required(self):
        self.install()
        ack = self.fixture.v2("train.departed", {"movement_id": AT_CDA}, "esp32-a")
        self.assertEqual("departure_not_reserved", ack["reason"])
        self.assertEqual("none", self.state()["arrival"])                  # nothing jumped
        self.assertEqual("none", self.state(LEK, IN_FROM_LEK)["departure"])

    def double_track(self, name):
        # Double track: CDA's way on can be cleared while LEK's is still open.
        package = through_package(name)
        package["connections"][0]["track_type"] = "double"
        self.install(package)

    def test_a_request_left_behind_is_withdrawn(self):
        self.double_track("through-102-waiting")
        left = self.request(IN_FROM_LEK, "esp32")
        self.clear(AT_CDA, "esp32-a", "esp32")
        self.accepted("train.departed", {"movement_id": AT_CDA}, "esp32-a")
        self.assertNotIn(left["clearance_id"], [c["clearance_id"] for c in self.service.open_cases(None)])
        self.assertEqual("departed", self.state(LEK, IN_FROM_LEK)["departure"])

    def test_a_clearance_left_behind_is_released_and_the_line_freed(self):
        self.double_track("through-102-approved")
        left = self.clear(IN_FROM_LEK, "esp32", "esp32-a")
        self.clear(AT_CDA, "esp32-a", "esp32")
        self.accepted("train.departed", {"movement_id": AT_CDA}, "esp32-a")
        self.assertNotIn(left["clearance_id"], [c["clearance_id"] for c in self.service.open_cases(None)])
        self.assertEqual("departed", self.state(LEK, IN_FROM_LEK)["departure"])

    def test_once_it_has_come_it_goes_as_before(self):
        self.install()
        self.clear(IN_FROM_LEK, "esp32", "esp32-a")
        self.accepted("train.departed", {"movement_id": IN_FROM_LEK}, "esp32")
        self.accepted("train.arrived", {"movement_id": AT_CDA}, "esp32-a")
        self.clear(AT_CDA, "esp32-a", "esp32")
        self.tkl_depart()
        self.assert_jumped()

    def test_the_receiver_takes_in_a_cleared_train_never_reported_departed(self):
        self.install()
        case = self.clear(IN_FROM_LEK, "esp32", "esp32-a")
        self.accepted("train.arrived", {"movement_id": AT_CDA}, "esp32-a")
        self.assertEqual("arrived", self.state()["arrival"])
        self.assertEqual("departed", self.state(LEK, IN_FROM_LEK)["departure"])
        self.assertNotIn(case["clearance_id"], [c["clearance_id"] for c in self.service.open_cases(None)])

    def test_the_receiver_places_a_train_nobody_sent(self):
        self.install()
        self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA, "arrival": "arrived"})
        self.assertEqual("arrived", self.state()["arrival"])
        self.assertEqual("departed", self.state(LEK, IN_FROM_LEK)["departure"])

    def test_an_occupied_track_does_not_stop_the_arrival(self):
        package = through_package("through-102-busy-track")
        next(t for t in package["trains"] if t["id"] == AT_CDA)["track_id"] = "track-station-a-1"   # 101 stands there
        self.install(package)
        result = self.service.execute_station_command("esp32-a", CDA, "train.arrived", {"movement_id": AT_CDA})
        self.assertEqual("101", result["track_occupied_by"])
        self.assertEqual(("arrived", "track-station-a-1"), (self.state()["arrival"], self.state()["actualTrack"]))

    def test_the_form_records_the_arrival_before_the_departure(self):
        self.double_track("through-102-form")
        self.clear(IN_FROM_LEK, "esp32", "esp32-a")
        self.accepted("train.departed", {"movement_id": IN_FROM_LEK}, "esp32")
        self.clear(AT_CDA, "esp32-a", "esp32")
        movement = self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA,
                                                             "arrival": "arrived", "departure": "departed"})["movement"]
        self.assertEqual(("arrived", "departed"), (movement["arrival"], movement["departure"]))

    def test_a_refused_departure_keeps_the_arrival_of_the_same_form_out_too(self):
        self.install()
        with self.assertRaises(HTTPAPIError) as caught:     # no onward clearance
            self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA,
                                                      "arrival": "arrived", "departure": "departed"})
        self.assertEqual("departure_not_reserved", caught.exception.code)
        self.assertEqual("none", self.state()["arrival"])
        self.assertEqual("none", self.state(LEK, IN_FROM_LEK)["departure"])

    def test_a_train_from_outside_the_meet_has_nothing_to_jump(self):
        package = through_package("through-102-from-outside")
        for item in package["services"]:
            if item["train_number"] == "102":
                item["stops"] = item["stops"][1:]
        package["trains"] = [row for row in package["trains"] if row["id"] != IN_FROM_LEK]
        self.install(package)
        self.clear(AT_CDA, "esp32-a", "esp32")
        self.accepted("train.departed", {"movement_id": AT_CDA}, "esp32-a")

    def test_a_train_that_starts_here_needs_no_arrival(self):
        self.install()
        self.clear(IN_FROM_LEK, "esp32", "esp32-a")
        self.accepted("train.departed", {"movement_id": IN_FROM_LEK}, "esp32")
        self.assertEqual("none", self.state(LEK, IN_FROM_LEK)["arrival"])


class JumpBackAlongTheRouteTests(unittest.TestCase):
    """103 VA -> CDA -> LEK; LEK places it though neither VA nor CDA sent it."""

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.fixture.install(unmanned_package())
        self.service = self.fixture.service

    def tearDown(self):
        self.fixture.tearDown()

    def state(self, station, movement):
        return next(m for m in self.service.snapshot_payload(station)["movements"] if m["id"] == movement)

    def test_every_earlier_part_is_finished(self):
        ack = self.fixture.v2("train.arrived", {"movement_id": "movement-103-2"}, "esp32")
        self.assertEqual("accepted", ack["status"], ack)
        self.assertEqual("departed", self.state("station-c", "movement-103-0")["departure"])
        self.assertEqual(("arrived", "departed"),
                         tuple(self.state(CDA, "movement-103-1")[k] for k in ("arrival", "departure")))
        self.assertEqual("arrived", self.state(LEK, "movement-103-2")["arrival"])

    def test_a_clearance_further_back_is_released_too(self):
        # VA asked, CDA cleared, and then everyone lost track; LEK has it now.
        self.fixture.ids.record_discovery("esp-va", "esp-va", protocol_version=2)
        self.fixture.ids.assign_discovered_device("esp-va", station_id="station-c")
        self.assertEqual("accepted", self.fixture.v2("clearance.request", {"movement_id": "movement-103-0",
                                                                            "connection_id": "connection-a-c"}, "esp-va")["status"])
        case = self.service.open_cases(None)[0]
        self.assertEqual("accepted", self.fixture.v2("clearance.response", {"clearance_id": case["clearance_id"],
                                                                             "approved": True}, "esp32-a")["status"])
        self.assertEqual("accepted", self.fixture.v2("train.arrived", {"movement_id": "movement-103-2"}, "esp32")["status"])
        self.assertEqual([], self.service.open_cases(None))

    def test_the_simulation_hears_what_the_jump_recorded(self):
        self.service.simulation = MagicMock(active=False)
        self.fixture.v2("train.arrived", {"movement_id": "movement-103-2"}, "esp32")
        recorded = [call.args for call in self.service.simulation.record_action.call_args_list]
        for event in (("train.departed", "movement-103-0"), ("train.arrived", "movement-103-1"),
                      ("train.departed", "movement-103-1"), ("train.arrived", "movement-103-2")):
            self.assertIn(event, recorded)


if __name__ == "__main__":
    unittest.main()
