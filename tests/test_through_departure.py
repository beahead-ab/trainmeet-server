"""A train that comes in and goes on cannot be reported departed before it has
come, whichever client reports it (TKL, the TKL form, a box).

Until 2.0.3 the server only asked for the clearance: TKL hid "Tåg ut" until
the arrival, and the boxes hid #Avg, but a stale window or a direct call
could still send a train on that had never come in.
"""
from copy import deepcopy
import unittest

import test_shared_traffic
from test_terminal16_runtime import through_package
from tmbox_gateway.http_server import HTTPAPIError

CDA, LEK = "station-a", "station-b"
IN_FROM_LEK, AT_CDA = "movement-102-0", "movement-102-1"   # LEK -> CDA -> LEK


class DepartureNeedsArrivalTests(unittest.TestCase):
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

    def clear(self, movement, sender, receiver):
        self.accepted("clearance.request", {"movement_id": movement, "connection_id": "connection-a-b"}, sender)
        case = next(c for c in self.service.open_cases(None) if c["movement_id"] == movement)
        self.accepted("clearance.response", {"clearance_id": case["clearance_id"], "approved": True}, receiver)

    def comes_in(self):
        """LEK sends 102 to CDA, and it is not yet received."""
        self.clear(IN_FROM_LEK, "esp32", "esp32-a")
        self.accepted("train.departed", {"movement_id": IN_FROM_LEK}, "esp32")

    def state(self):
        return next(m for m in self.service.snapshot_payload(CDA)["movements"] if m["id"] == AT_CDA)

    def tkl_depart(self):
        return self.app.tkl_clearance_action(self.admin, {"station_id": CDA, "connection_id": "connection-a-b", "action": "depart"})

    def test_no_client_can_send_on_a_train_that_has_not_come(self):
        self.install()
        self.clear(AT_CDA, "esp32-a", "esp32")           # asked for before it came (allowed since 2.0.3)
        self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA, "departure": "ready"})
        self.assertNotIn("train.departed", self.state()["allowed_actions"])
        with self.assertRaises(HTTPAPIError) as caught:
            self.tkl_depart()                             # TKL "Tåg ut"
        self.assertEqual("Tåget har inte ankommit till stationen", str(caught.exception))
        with self.assertRaises(HTTPAPIError) as caught:   # the TKL form
            self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA, "departure": "departed"})
        self.assertEqual("train_not_arrived", caught.exception.code)
        self.assertEqual("Tåget har inte ankommit till stationen", str(caught.exception))
        ack = self.fixture.v2("train.departed", {"movement_id": AT_CDA}, "esp32-a")   # a box
        self.assertEqual("train_not_arrived", ack["reason"])
        # Nothing changed: still cleared, still here, not on the line.
        self.assertEqual(["approved"], [c["status"] for c in self.service.open_cases(CDA)])
        self.assertEqual(("ready", "none"), (self.state()["departure"], self.state()["arrival"]))
        self.assertEqual([], [p for p in self.fixture.ops.positions() if p["status"] == "connection"])

    def test_once_it_has_come_it_can_go(self):
        self.install()
        self.comes_in()
        self.accepted("train.arrived", {"movement_id": AT_CDA}, "esp32-a")
        self.clear(AT_CDA, "esp32-a", "esp32")
        self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA, "departure": "ready"})
        self.assertIn("train.departed", self.state()["allowed_actions"])
        self.tkl_depart()
        self.assertEqual("departed", self.state()["departure"])
        position = next(p for p in self.fixture.ops.positions() if p["train_number"] == "102")
        self.assertEqual(("connection", LEK), (position["status"], position["to_station_id"]))

    def test_the_form_records_the_arrival_before_the_departure(self):
        # Double track: the onward line can be cleared while 102 is still coming in.
        package = through_package("through-102-double")
        package["connections"][0]["track_type"] = "double"
        self.install(package)
        self.comes_in()
        self.clear(AT_CDA, "esp32-a", "esp32")
        movement = self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA,
                                                             "arrival": "arrived", "departure": "departed"})["movement"]
        self.assertEqual(("arrived", "departed"), (movement["arrival"], movement["departure"]))

    def test_a_refused_departure_keeps_the_arrival_of_the_same_form_out_too(self):
        self.install()
        self.comes_in()
        before = self.state()["arrival"]
        with self.assertRaises(HTTPAPIError) as caught:     # no onward clearance
            self.app.update_tkl_movement(self.admin, {"station_id": CDA, "movement_id": AT_CDA,
                                                      "arrival": "arrived", "departure": "departed"})
        self.assertEqual("departure_not_reserved", caught.exception.code)
        self.assertEqual(before, self.state()["arrival"])
        self.assertEqual(1, len([c for c in self.service.open_cases(None) if c["movement_id"] == IN_FROM_LEK]))

    def test_a_train_from_outside_the_meet_needs_no_arrival(self):
        """No station of the meet sends it in, so nobody could report the
        arrival - and the box shows #Avg straight away. The server agrees."""
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


if __name__ == "__main__":
    unittest.main()
