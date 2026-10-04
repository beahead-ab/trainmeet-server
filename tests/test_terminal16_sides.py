"""Flera boxar på samma station, var och en för sin sida.

Vagnsta har en box för trafiken åt vänster och en för trafiken åt höger
(Casper, 2026-10-01). Vid tilldelningen väljer man Båda (förval), Vänster eller
Höger. Vänster och höger betyder samma sak som i kortet TMBox-placering. En box
på ena sidan ser och hanterar bara tågen på sträckorna åt sitt håll: översikt,
förfrågningskö, aktiva tåg, tidtabell, sökning och kvitton. Båda ger samma
bilder som förut.

Banan i provet: LEK – CDA – MUN. Vid CDA ligger LEK åt ena hållet och MUN åt
det andra.
"""
from uuid import uuid4
import unittest

import test_shared_traffic
from runtime_fixture import runtime_package_v3
import test_terminal16_runtime
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import InvalidClientError
from tmbox_gateway.terminal16_runtime import Terminal16Service


def _minute(value):
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def add_train(package, number, stops):
    """stops: (station_id, code, track, arrival, departure) in running order."""
    service_id = f"service-{number}-Dagl"
    package["services"].append({"id": service_id, "train_number": number, "days": "Dagl", "train_type": "person", "stops": [
        {"station_id": station, "station_name": code, "stop_order": order, "arrival_time": arrival,
         "departure_time": departure, "service_day_offset": 0, "service_minute": _minute(arrival or departure)}
        for order, (station, code, _, arrival, departure) in enumerate(stops)]})
    for order, (station, code, track, arrival, departure) in enumerate(stops):
        package["trains"].append({
            "id": f"movement-{number}-{station[-1]}", "train_number": number, "station_id": station, "station": code,
            "track_id": track, "days": "Dagl", "arrival_time": arrival, "departure_time": departure,
            "arrival_from": stops[order - 1][1] if order else None,
            "departure_to": stops[order + 1][1] if order + 1 < len(stops) else None,
            "sort_time": arrival or departure, "no_stop": False, "note": None, "manual_sort_order": 0,
            "service_id": service_id})
        package["routes"].append({
            "id": f"route-{number}-{station[-1]}", "train_number": number, "station_id": station, "station_name": code,
            "stop_order": order, "arrival_time": arrival, "departure_time": departure, "service_id": service_id,
            "days": "Dagl", "service_day_offset": 0, "service_minute": _minute(arrival or departure)})


def two_sided_package():
    package = runtime_package_v3(publication_id="two-sided-station")
    package["display"]["graph_station_order"].append("station-c")
    package["stations"].append({**package["stations"][1], "id": "station-c", "code": "MUN", "name": "Munkeröd", "diagram_order": 2})
    package["tracks"].append({**package["tracks"][2], "id": "track-station-c-1", "station_id": "station-c"})
    package["tracks"].append({**package["tracks"][1], "id": "track-station-a-3", "display_label": "3", "sort_order": 30})
    package["connections"].append({**package["connections"][0], "id": "connection-a-c", "station_b_id": "station-c"})
    package["panels"][0]["slots"]["B"] = "connection-a-c"
    package["panels"].append({"id": "panel-c", "station_id": "station-c", "name": "MUN",
                              "slots": {"A": "connection-a-c", "B": None, "C": None, "D": None}})
    add_train(package, "303", [("station-a", "CDA", "track-station-a-2", None, "09:25"),
                               ("station-c", "MUN", "track-station-c-1", "09:40", None)])
    add_train(package, "404", [("station-c", "MUN", "track-station-c-1", None, "09:30"),
                               ("station-a", "CDA", "track-station-a-3", "09:45", None)])
    return package


class TwoSidedStationTests(unittest.TestCase):
    send = test_terminal16_runtime.RuntimeTerminalTests.send

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.service = self.fixture.service
        self.fixture.install(two_sided_package())
        self.terminals = Terminal16Service(self.service)
        sides = self.terminals._views("esp8266").display_sides["station-a"]
        # Which side LEK and MUN are on at CDA, as the TMBox placement shows them.
        self.lek_side, self.mun_side = sides["connection-a-b"], sides["connection-a-c"]
        self.assertNotEqual(self.lek_side, self.mun_side, "the station needs one neighbour on each side")
        ids = self.fixture.ids
        for device, station, side in (("cda-lek", "station-a", self.lek_side), ("cda-mun", "station-a", self.mun_side),
                                      ("mun", "station-c", "both")):
            code = "TBX-" + device.replace("-", "").upper()
            ids.record_discovery(device, code, protocol_version=2)
            ids.assign_discovered_device(code, station_id=station, station_side=side)
        for device in ("esp8266", "esp32", "cda-lek", "cda-mun", "mun"):
            self.terminals.frame(device)

    def trains(self, device):
        views = self.terminals.views
        return {views.legs[key]["train_number"] for key in views._candidates(views.terminals[device])}

    def test_each_box_sees_and_finds_only_the_trains_on_its_side(self):
        # 404 comes in from MUN: since 2.1.0 an arrival nobody has sent is in
        # the list too, to be placed.
        self.assertEqual({"101", "303", "404"}, self.trains("esp8266"), "both: the whole station, as before")
        self.assertEqual({"101"}, self.trains("cda-lek"))
        self.assertEqual({"303", "404"}, self.trains("cda-mun"))
        timetable = lambda device: {row["train_number"] for row in self.terminals.views.timetable(device)["rows"]}
        self.assertEqual({"101", "303", "404"}, timetable("esp8266"))
        self.assertEqual({"101"}, timetable("cda-lek"))
        self.assertEqual({"303", "404"}, timetable("cda-mun"))
        # Searching for a train that runs on the other side says so.
        frame = self.send("cda-mun", "#", train_number="101")["frame"]
        self.assertEqual("ANNAN SIDA", frame["lines"][0].strip())
        self.send("cda-mun", "#")
        frame = self.send("cda-lek", "#", train_number="101")["frame"]
        self.assertTrue(frame["lines"][0].startswith("LEK") or frame["lines"][0].rstrip().endswith("LEK"), frame["lines"])
        self.assertIn("101", frame["lines"][0])
        self.assertEqual("ANNAN SIDA", self.send("cda-lek", "#", train_number="303")["frame"]["lines"][0].strip())
        self.send("cda-lek", "#")
        # Not "not requested yet" either: 404 arrives on the MUN side.
        self.assertEqual("ANNAN SIDA", self.send("cda-lek", "#", train_number="404")["frame"]["lines"][0].strip())
        self.send("cda-lek", "#")
        # 404 from MUN, never sent: found on its side, to be moved here (#115).
        frame = self.send("cda-mun", "#", train_number="404")["frame"]
        self.assertEqual("FLYTTA 404 HIT?", frame["lines"][0].strip())
        self.assertEqual("Flytta hit", frame["keys"]["#"]["label"])
        self.send("cda-mun", "*")                          # back, without placing it
        self.assertEqual("INGET TÅG", self.send("cda-lek", "#", train_number="999")["frame"]["lines"][0].strip())

    def test_a_request_reaches_only_the_box_on_its_side(self):
        self.send("mun", "#", train_number="404")
        mun_side, lek_side, both = (self.terminals.frame(device) for device in ("cda-mun", "cda-lek", "esp8266"))
        self.assertEqual(1, mun_side["requests"]["count"])
        self.assertIn("404", mun_side["lines"][0])
        self.assertTrue(mun_side["lines"][1].startswith("#Ja *Nej"), mun_side["lines"])
        self.assertEqual(1, both["requests"]["count"])
        self.assertEqual(0, lek_side["requests"]["count"])
        self.assertEqual("", lek_side["lines"][0].strip(), "nothing pushed to the other side's box")
        self.assertTrue(lek_side["lines"][1].startswith("Nr# A:Kö"), lek_side["lines"])
        self.send("cda-mun", "#")  # Ge klart
        self.send("mun", "#")      # Avgång
        mun_side, lek_side, both = (self.terminals.frame(device) for device in ("cda-mun", "cda-lek", "esp8266"))
        self.assertEqual(1, mun_side["active"]["count"])
        self.assertEqual(1, both["active"]["count"])
        self.assertEqual(0, lek_side["active"]["count"])
        self.assertEqual("", lek_side["lines"][0].strip())
        self.assertIn("404", mun_side["lines"][0], "still shows the train it cleared")
        self.send("cda-mun", "#")  # Ankomst
        self.assertIn("MOTTAGET", self.terminals.frame("mun")["lines"][0])

    def test_the_receipt_goes_to_the_senders_side_only(self):
        self.send("cda-lek", "#", train_number="101")
        self.assertEqual(1, self.terminals.frame("esp32")["requests"]["count"])
        self.send("esp32", "#")
        self.assertEqual(0, self.terminals.frame("cda-mun")["active"]["count"])
        self.send("cda-lek", "#")
        self.send("esp32", "#")
        self.assertIn("MOTTAGET", self.terminals.frame("cda-lek")["lines"][0])
        self.assertIn("MOTTAGET", self.terminals.frame("esp8266")["lines"][0])
        self.assertNotIn("MOTTAGET", self.terminals.frame("cda-mun")["lines"][0])

    def test_changing_side_starts_the_box_afresh(self):
        frame = self.terminals.frame("cda-lek")
        self.fixture.ids.assign_discovered_device("TBX-CDALEK", station_id="station-a", station_side=self.mun_side)
        fresh = self.terminals.frame("cda-lek")
        self.assertEqual({"303", "404"}, self.trains("cda-lek"))
        self.assertNotEqual(frame["view_token"], fresh["view_token"])
        # 101# typed on the old side would ask LEK at once (2.1.0): it must do nothing.
        stale = self.terminals.command("cda-lek", {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": "#",
                                                   "train_number": "101", "entry_context": frame["entry"]["context"]})
        self.assertEqual("rejected", stale["status"], "a key pressed for the old side does nothing")
        self.assertEqual([], self.service.open_cases(None))

    def test_assignment_takes_a_side_and_both_is_the_default(self):
        app, admin, ids = self.fixture.app, self.fixture.admin, self.fixture.ids
        body = lambda **extra: {"device_code": "TBX-CDALEK", "station_id": "station-a", **extra}
        with self.assertRaises(HTTPAPIError) as rejected:
            app.assign_device(admin, body(side="middle"))
        self.assertEqual("invalid_side", rejected.exception.code)
        self.assertEqual("right", app.assign_device(admin, body(side="right"))["side"])
        listed = {d["device_id"]: d for d in app.devices(admin)["devices"]}
        self.assertEqual("right", listed["cda-lek"]["station_side"])
        self.assertEqual("both", listed["esp8266"]["station_side"])
        # Without a side (an older admin page, or Reconnect) the box handles both.
        self.assertEqual("both", app.assign_device(admin, body())["side"])
        with self.assertRaises(InvalidClientError):
            ids.assign_discovered_device("TBX-CDALEK", station_id="station-a", station_side="middle")
        self.assertEqual("both", ids.station_side_for_client("cda-lek"), "a rejected side changes nothing")
        ids.assign_discovered_device("TBX-CDALEK", station_id="station-a", station_side="left")
        ids.remove_discovered_device("cda-lek")
        ids.assign_discovered_device("TBX-CDALEK", station_id="station-a")
        self.assertEqual("both", ids.station_side_for_client("cda-lek"), "removal forgets the side")
        ids.assign_discovered_device("TBX-CDALEK", station_id="station-a", station_side="left")
        ids.clear_meet_assignments()
        self.assertEqual("both", ids.station_side_for_client("cda-lek"), "a new meet starts on both sides")
