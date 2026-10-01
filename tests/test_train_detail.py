"""Klicka på ett tåg i Drift: hela sträckningen och var tåget är just nu.

Casper ville kunna klicka på ett tåg i "Kommande enligt tidtabell" och se
tågets sträckning och var det befinner sig (2026-10-01). /v1/train samlar
det som redan finns: tidtabellen, varje stations läge för rörelsen, öppna
klareringar och linjepositionen. Den bestämmer ingenting själv.

Banan i provet: LEK – CDA – MUN, och tåg 505 går hela vägen.
"""
from types import SimpleNamespace
import unittest

import test_shared_traffic
import test_terminal16_runtime
from test_terminal16_sides import add_train, two_sided_package
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.terminal16_runtime import Terminal16Service


def through_package():
    package = two_sided_package()
    package["tracks"].append({**package["tracks"][1], "id": "track-station-a-4", "display_label": "4", "sort_order": 40})
    package["tracks"].append({**package["tracks"][1], "id": "track-station-a-5", "display_label": "5", "sort_order": 50})
    package["tracks"].append({**package["tracks"][2], "id": "track-station-c-2", "station_id": "station-c", "display_label": "2", "sort_order": 20})
    add_train(package, "505", [("station-b", "LEK", "track-station-b-1", None, "09:30"),
                               ("station-a", "CDA", "track-station-a-4", "09:45", "09:50"),
                               ("station-c", "MUN", "track-station-c-2", "10:05", None)])
    return package


class TrainDetailTests(unittest.TestCase):
    send = test_terminal16_runtime.RuntimeTerminalTests.send

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.service = self.fixture.service
        self.fixture.install(through_package())
        self.app, self.admin = self.fixture.app, self.fixture.admin
        ids = self.fixture.ids
        ids.record_discovery("munbox", "TBX-MUNBOX", protocol_version=2)
        ids.assign_discovered_device("TBX-MUNBOX", station_id="station-c")
        self.terminals = Terminal16Service(self.service)

    def detail(self, number="505"):
        services = self.app.train_detail(self.admin, number)["services"]
        self.assertEqual(1, len(services))
        return services[0]

    def now(self, number="505"):
        return self.detail(number)["now"]

    def test_the_route_lists_every_call_with_times_and_tracks(self):
        detail = self.detail()
        self.assertEqual(["LEK", "CDA", "MUN"], [stop["station_code"] for stop in detail["stops"]])
        self.assertEqual([(None, "09:30"), ("09:45", "09:50"), ("10:05", None)],
                         [(stop["arrival_time"], stop["departure_time"]) for stop in detail["stops"]])
        self.assertEqual(["1", "4", "2"], [stop["planned_track"] for stop in detail["stops"]])
        self.assertEqual(["movement-505-b", "movement-505-a", "movement-505-c"],
                         [stop["movement_id"] for stop in detail["stops"]])
        self.assertEqual({"state": "not_departed", "station_id": "station-b", "track": "1", "time": "09:30"},
                         detail["now"])
        self.assertIsNone(detail["delay_minutes"], "no simulator, no delay")

    def test_where_the_train_is_follows_each_step(self):
        # LEK requests, CDA gives clear, LEK departs, CDA receives.
        self.send("esp32", "#", train_number="505")
        self.send("esp32", "#")
        self.assertEqual({"state": "waiting", "from_station_id": "station-b", "to_station_id": "station-a"}, self.now())
        self.send("esp8266", "#")
        now = self.now()
        self.assertEqual(("cleared", "station-b", "station-a", "1", "09:30"),
                         (now["state"], now["from_station_id"], now["to_station_id"], now["track"], now["time"]))
        self.send("esp32", "#")
        now = self.now()
        self.assertEqual(("on_line", "station-b", "station-a"), (now["state"], now["from_station_id"], now["to_station_id"]))
        # CDA takes it in on track 5 instead of the planned 4.
        self.send("esp8266", "B")
        for _ in range(4):
            self.send("esp8266", "D")
        self.send("esp8266", "#")
        detail = self.detail()
        self.assertEqual({"state": "at_station", "station_id": "station-a", "track": "5", "time": "09:50"}, detail["now"])
        self.assertEqual(("departed", "arrived"), (detail["stops"][0]["departure"], detail["stops"][1]["arrival"]))
        self.assertEqual(("4", "5"), (detail["stops"][1]["planned_track"], detail["stops"][1]["actual_track"]))
        # On to MUN, and in. The second leg is followed the same way.
        self.send("esp8266", "#", train_number="505")
        self.send("esp8266", "#")
        self.assertEqual({"state": "waiting", "from_station_id": "station-a", "to_station_id": "station-c"}, self.now())
        self.send("munbox", "#")
        now = self.now()
        self.assertEqual(("cleared", "station-a", "station-c", "5", "09:50"),
                         (now["state"], now["from_station_id"], now["to_station_id"], now["track"], now["time"]))
        self.send("esp8266", "#")
        now = self.now()
        self.assertEqual(("on_line", "station-a", "station-c"), (now["state"], now["from_station_id"], now["to_station_id"]))
        self.send("munbox", "#")
        self.assertEqual({"state": "arrived", "station_id": "station-c", "track": "2", "time": "10:05"}, self.now())

    def test_a_train_only_on_the_line_still_shows_there(self):
        """Older paths record just the line position; it still counts."""
        self.app.operations_store.positions = lambda: [{"train_number": "505", "status": "connection",
            "station_id": None, "connection_id": "connection-a-b", "from_station_id": "station-b",
            "to_station_id": "station-a", "updated_at": "x"}]
        now = self.now()
        self.assertEqual(("on_line", "station-b", "station-a"), (now["state"], now["from_station_id"], now["to_station_id"]))

    def test_the_simulator_gives_the_delay(self):
        self.app.simulation = SimpleNamespace(status=lambda: {"active": True, "trains": [
            {"movement_id": "movement-505-b", "status": "in_transit", "delay_seconds": 250},
            {"movement_id": "movement-101-a", "status": "in_transit", "delay_seconds": 900}]})
        self.assertEqual(4, self.detail()["delay_minutes"])
        self.app.simulation = SimpleNamespace(status=lambda: {"active": False})
        self.assertIsNone(self.detail()["delay_minutes"])

    def test_the_same_number_twice_today_gives_both(self):
        package = through_package()
        package["publication_id"] = "two-sided-station-late"
        add_train(package, "505", [("station-a", "CDA", "track-station-a-1", None, "14:00"),
                                   ("station-b", "LEK", "track-station-b-1", "14:15", None)])
        package["services"][-1]["id"] = "service-505-late"
        for collection in ("trains", "routes"):
            for item in package[collection][-2:]:
                item["service_id"] = "service-505-late"
                item["id"] += "-late"
        self.fixture.install(package)
        services = self.app.train_detail(self.admin, "505")["services"]
        self.assertEqual([["LEK", "CDA", "MUN"], ["CDA", "LEK"]],
                         [[stop["station_code"] for stop in service["stops"]] for service in services])

    def test_a_train_calling_twice_at_a_station_keeps_both_calls_apart(self):
        package = through_package()
        package["publication_id"] = "two-sided-station-return"
        add_train(package, "606", [("station-a", "CDA", "track-station-a-1", None, "11:00"),
                                   ("station-b", "LEK", "track-station-b-1", "11:15", "11:20")])
        add_train(package, "606x", [("station-a", "CDA", "track-station-a-2", "11:35", None)])
        back = package["trains"][-1]
        back.update(id="movement-606-a-back", train_number="606", service_id="service-606-Dagl", arrival_from="LEK")
        route = package["routes"][-1]
        route.update(id="route-606-a-back", train_number="606", service_id="service-606-Dagl", stop_order=2)
        package["services"].pop()
        package["services"][-1]["stops"].append({"station_id": "station-a", "station_name": "CDA", "stop_order": 2,
            "arrival_time": "11:35", "departure_time": None, "service_day_offset": 0, "service_minute": 695})
        package["trains"][-2]["departure_to"] = "CDA"
        self.fixture.install(package)
        stops = self.app.train_detail(self.admin, "606")["services"][0]["stops"]
        self.assertEqual([("CDA", "movement-606-a", "1"), ("LEK", "movement-606-b", "1"), ("CDA", "movement-606-a-back", "2")],
                         [(stop["station_code"], stop["movement_id"], stop["planned_track"]) for stop in stops])

    def test_unknown_trains_and_us_meets_say_so(self):
        with self.assertRaises(HTTPAPIError) as unknown:
            self.app.train_detail(self.admin, "999")
        self.assertEqual("unknown_train", unknown.exception.code)
        self.app.lifecycle = SimpleNamespace(selected=lambda: {"region": "us"})
        with self.assertRaises(HTTPAPIError) as us:
            self.app.train_detail(self.admin, "505")
        self.assertEqual(404, us.exception.status)


if __name__ == "__main__":
    unittest.main()
