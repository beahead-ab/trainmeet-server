"""Tåg som rör sig på kartan och vilken sida de går på vid dubbelspår.

Kartan låter ett avgånget tåg glida mot nästa station i takt med
träffklockan, räknat från den faktiska avgången. Servern ger därför
träffklockans tid vid avgången på kanalen i /v1/display. Sidan på dubbelspår
följer träffens land som förval (vänster i Sverige och Norge, höger i Danmark,
Tyskland och USA) och kan väljas per träff.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime_fixture import runtime_package_v3
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.models import Command
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import TMBoxStationService
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.storage import SQLiteStateStore


class TrainMotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)
        self.publication = self.runtime.install(runtime_package_v3())
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)
        self.store = SQLiteStateStore(self.path)
        for name, station in (("esp8266", "station-a"), ("esp32", "station-b")):
            self.ids.record_discovery(name, name, protocol_version=1 if name == "esp8266" else 2)
            self.ids.assign_discovered_device(name, station_id=station)
        self.service = TMBoxStationService(self.runtime, self.ops, self.ids)
        self.engine = TrafficEngine(self.publication.session_config(), state_store=self.store)
        self.app = TrainMeetHTTPApplication(self.engine, self.ids, PairingService(self.ids, set(self.engine.config.panels)),
            HTTPServerConfig(local_development=True), runtime_store=self.runtime, operations_store=self.ops, station_service=self.service)
        self.admin = self.app.local_admin()
        self.seq = 0

    def tearDown(self):
        self.store.close()
        self.ids.close()
        self.ops.close()
        self.runtime.close()
        self.temp.cleanup()

    def key(self, key):
        self.seq += 1
        revision = self.engine.snapshot("panel-a")["revision"]
        result = self.engine.press(Command(str(self.seq), "esp8266", self.engine.config.id, "panel-a", revision, key))
        self.assertEqual(result.status, "accepted", result.reason)

    def depart_101_at(self, time_value):
        for key in "A101#":
            self.key(key)
        case = self.service.open_cases("station-a")[0]["clearance_id"]
        self.seq += 1
        ack = self.service.handle_command("esp32", {"protocol_version": 2, "message_id": f"v2-{self.seq}",
            "action": "clearance.response", "payload": {"clearance_id": case, "approved": True}})
        self.assertEqual(ack["status"], "accepted", ack)
        self.ops.configure_clock(time_value=time_value, running=False)
        for key in "AAA":
            self.key(key)

    def channel(self, snapshot):
        return next(channel for state in snapshot["connection_states"] for channel in state.get("channels", [])
                    if channel.get("train_number") == "101")

    def test_the_display_gives_the_meet_clock_at_the_actual_departure(self):
        self.depart_101_at("09:23:30")  # planerad avgång 09:20
        channel = self.channel(self.app.display_snapshot())
        self.assertEqual(channel["state"], "occupied")
        self.assertEqual(channel["movement_id"], "movement-101-a")
        self.assertEqual(channel["departed_seconds"], 9 * 3600 + 23 * 60 + 30)

    def test_the_latest_departure_counts_and_an_old_event_without_clock_gives_none(self):
        publication, day = self.publication.publication_id, self.runtime.active_day() or self.publication.active_day
        self.ops.configure_clock(time_value="09:21:00", running=False)
        for departure in ("departed", "positioned"):
            self.ops.update_tkl_movement(publication, day, "station-a", "movement-101-a", arrival="none", departure=departure,
                                         actual_track=None, updated_by="prov", shift_id=None, event_type="movement_updated")
        self.ops.configure_clock(time_value="09:25:00", running=False)
        self.ops.update_tkl_movement(publication, day, "station-a", "movement-101-a", arrival="none", departure="departed",
                                     actual_track=None, updated_by="prov", shift_id=None, event_type="movement_updated")
        self.assertEqual(self.ops.departure_clock_seconds(publication, day, ["movement-101-a"]),
                         {"movement-101-a": 9 * 3600 + 25 * 60}, "the second departure, not the first")
        # En händelse från en äldre server har ingen klocktid: då gäller den planerade avgången.
        with self.ops._lock:
            self.ops._connection.execute("UPDATE tkl_events SET payload_json = ? WHERE movement_id = ?",
                                         (json.dumps({"departure": "departed"}), "movement-101-a"))
        self.assertEqual(self.ops.departure_clock_seconds(publication, day, ["movement-101-a"]), {})

    def test_a_train_that_has_not_departed_has_no_departure_time(self):
        for key in "A101#":
            self.key(key)
        channel = self.channel(self.app.display_snapshot())
        self.assertNotIn("departed_seconds", channel)

    def test_traffic_side_follows_the_country_and_can_be_chosen_per_meet(self):
        meet = self.publication.meet_id
        self.assertEqual(self.runtime.traffic_side(meet, "se")["side"], "left")
        self.assertEqual(self.runtime.traffic_side(meet, "no")["side"], "left")
        for country in ("dk", "de", "us"):
            self.assertEqual(self.runtime.traffic_side(meet, country)["side"], "right", country)
        state = self.app.traffic_side_state(self.admin)
        self.assertEqual((state["side"], state["default"], state["overridden"]), ("left", "left", False))
        self.assertEqual(self.app.display_snapshot()["display"]["traffic_side"], "left")
        chosen = self.app.save_traffic_side(self.admin, {"side": "right"})
        self.assertEqual((chosen["side"], chosen["overridden"]), ("right", True))
        self.assertEqual(self.app.display_snapshot()["display"]["traffic_side"], "right")
        back = self.app.save_traffic_side(self.admin, {"side": "default"})
        self.assertEqual((back["side"], back["overridden"]), ("left", False))
        with self.assertRaises(HTTPAPIError):
            self.app.save_traffic_side(self.admin, {"side": "middle"})

    def test_only_an_administrator_chooses_the_side(self):
        box = PairedClient(client_id="esp32", display_name="Box", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-b",),
                           station_id="station-b", admin_role="")
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.save_traffic_side(box, {"side": "right"})
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.runtime.traffic_side(self.publication.meet_id, "se")["side"], "left")


if __name__ == "__main__":
    unittest.main()


def twice_a_day_package() -> dict:
    """Tåg 101 går CDA–LEK på morgonen (09:20–09:35) och igen på kvällen
    (18:50–19:05). Kvällsturens rader ligger sist i planen, så den som tar
    planens sista rad för numret på stationen får kvällsturen."""
    package = runtime_package_v3(publication_id="same-number-twice")
    service_id = "service-101-kvall"
    package["services"].append({"id": service_id, "train_number": "101", "days": "Dagl", "train_type": "person", "stops": [
        {"station_id": "station-a", "station_name": "CDA", "stop_order": 0, "arrival_time": None,
         "departure_time": "18:50", "service_day_offset": 0, "service_minute": 18 * 60 + 50},
        {"station_id": "station-b", "station_name": "LEK", "stop_order": 1, "arrival_time": "19:05",
         "departure_time": None, "service_day_offset": 0, "service_minute": 19 * 60 + 5}]})
    for row in [row for row in package["trains"] if row["train_number"] == "101"]:
        evening = {**row, "id": row["id"] + "-kvall", "service_id": service_id}
        if row["station_id"] == "station-a":
            evening.update(departure_time="18:50", sort_time="18:50")
        else:
            evening.update(arrival_time="19:05", sort_time="19:05")
        package["trains"].append(evening)
    for row in [row for row in package["routes"] if row["train_number"] == "101"]:
        evening = {**row, "id": row["id"] + "-kvall", "service_id": service_id}
        if row["station_id"] == "station-a":
            evening.update(departure_time="18:50", service_minute=18 * 60 + 50)
        else:
            evening.update(arrival_time="19:05", service_minute=19 * 60 + 5)
        package["routes"].append(evening)
    return package


class SameNumberTwiceADayTests(unittest.TestCase):
    """Casper såg tåg 319 på linjen med "ank 19:05" när klockan var 10:26: numret
    går flera gånger om dagen och skärmen tog den första turen med numret. Läget
    på linjen bär därför den rörelse som avgick, och den faktiska avgångstiden
    hör till just den."""

    def setUp(self):
        import test_shared_traffic

        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.install(twice_a_day_package())
        self.app, self.ops, self.service = self.fixture.app, self.fixture.ops, self.fixture.service

    def depart_morning_101_at(self, time_value):
        v2 = self.fixture.v2
        self.assertEqual(v2("train.position.set", {"movement_id": "movement-101-a"}, "esp32-a")["status"], "accepted")
        self.assertEqual(v2("clearance.request", {"movement_id": "movement-101-a", "connection_id": "connection-a-b"}, "esp32-a")["status"], "accepted")
        self.fixture.approve(self.service.open_cases("station-a")[0]["clearance_id"])
        self.ops.configure_clock(time_value=time_value, running=False)
        self.assertEqual(v2("train.departed", {"movement_id": "movement-101-a"}, "esp32-a")["status"], "accepted")

    def position(self, snapshot):
        return next(position for position in snapshot["train_positions"] if position["train_number"] == "101")

    def test_the_position_on_the_line_names_the_movement_that_left(self):
        self.depart_morning_101_at("09:23:30")
        stored = next(position for position in self.ops.positions() if position["train_number"] == "101")
        self.assertEqual((stored["status"], stored["movement_id"]), ("connection", "movement-101-a"))
        position = self.position(self.app.display_snapshot())
        self.assertEqual(position["movement_id"], "movement-101-a")
        self.assertEqual(position["departed_seconds"], 9 * 3600 + 23 * 60 + 30, "the morning run's departure, not the evening row")
        channel = next(channel for state in self.app.display_snapshot()["connection_states"]
                       for channel in state.get("channels", []) if channel.get("train_number") == "101")
        self.assertEqual((channel["movement_id"], channel["departed_seconds"]), ("movement-101-a", 9 * 3600 + 23 * 60 + 30))

    def test_an_older_position_without_a_movement_gets_the_run_that_departed(self):
        self.depart_morning_101_at("09:23:30")
        with self.ops._lock:
            self.ops._connection.execute("UPDATE train_positions SET movement_id = NULL WHERE train_number = '101'")
        position = self.position(self.app.display_snapshot())
        self.assertIsNone(position["movement_id"])
        self.assertEqual(position["departed_seconds"], 9 * 3600 + 23 * 60 + 30)

    def test_the_arrival_records_its_movement_too(self):
        self.depart_morning_101_at("09:23:30")
        self.assertEqual(self.fixture.v2("train.arrived", {"movement_id": "movement-101-b"})["status"], "accepted")
        stored = next(position for position in self.ops.positions() if position["train_number"] == "101")
        self.assertEqual((stored["status"], stored["station_id"], stored["movement_id"]), ("station", "station-b", "movement-101-b"))
        self.assertNotIn("departed_seconds", self.position(self.app.display_snapshot()))
