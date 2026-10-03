"""Automatic stations in normal operation (issue #115)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime_fixture import runtime_package_v3
from tmbox_gateway.automatic import ACTOR
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import TrainMeetHTTPApplication, HTTPServerConfig, HTTPAPIError
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import CommandRejected
from tmbox_gateway.runtime import SQLiteRuntimeStore


def at(hours, minutes, seconds=0):
    return hours * 3600 + minutes * 60 + seconds


class AutomaticStationTests(unittest.TestCase):
    """Train 101 runs CDA (station-a) 09:20 → LEK (station-b) 09:35."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)
        self.pub = self.runtime.install(runtime_package_v3())
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)
        self.app = TrainMeetHTTPApplication(TrafficEngine(self.pub.session_config()), self.ids,
            PairingService(self.ids, {"panel-a", "panel-b"}), HTTPServerConfig(local_development=True),
            runtime_store=self.runtime, operations_store=self.ops)
        self.service = self.app.station_service
        self.auto = self.app.automatic
        self.admin = self.app.local_admin()
        self.time = 1000.0
        self.auto.now = lambda: self.time

    def tearDown(self):
        self.ops.close()
        self.ids.close()
        self.runtime.close()
        self.app.lifecycle.close()
        self.temp.cleanup()

    def clock(self, seconds, running=True):
        value = f"{int(seconds) // 3600:02d}:{int(seconds) % 3600 // 60:02d}:{int(seconds) % 60:02d}"
        self.ops.configure_clock(time_value=value, running=running)

    def advance(self, seconds):
        self.clock(seconds)
        self.auto.tick()

    def box(self, name="box-b", station="station-b"):
        self.ids.record_discovery(name, name, protocol_version=2)
        self.ids.assign_discovered_device(name, station_id=station)
        self.service.observe_operator(name)
        return name

    def movement(self, station, movement_id):
        return self.ops.tkl_station_state(self.pub.publication_id, self.pub.active_day, station)["movements"].get(movement_id, {})

    def test_two_unmanned_stations_run_the_timetable(self):
        self.advance(at(9, 17))
        self.assertEqual([], self.service.open_cases(None), "nothing before two minutes ahead")
        self.advance(at(9, 18))
        self.assertEqual("approved", self.service.open_cases(None)[0]["status"])
        self.advance(at(9, 20))
        self.assertEqual("departed", self.movement("station-a", "movement-101-a")["departure"])
        self.advance(at(9, 34, 59))
        self.assertNotEqual("arrived", self.movement("station-b", "movement-101-b").get("arrival"))
        self.advance(at(9, 35, 1))   # the test clock runs on a little between reads
        self.assertEqual("arrived", self.movement("station-b", "movement-101-b")["arrival"])
        self.assertEqual([], self.service.open_cases(None), "the line is free again")

    def test_a_manned_receiver_answers_and_reports_the_arrival_itself(self):
        box = self.box()
        self.advance(at(9, 18))
        case = self.service.open_cases(None)[0]
        self.assertEqual("waiting", case["status"], "never cleared on a manned station's behalf")
        self.service.execute_station_command(box, "station-b", "clearance.response",
                                             {"clearance_id": case["clearance_id"], "approved": True})
        self.advance(at(9, 20))
        self.assertEqual("departed", self.movement("station-a", "movement-101-a")["departure"])
        self.advance(at(9, 40))
        self.assertNotEqual("arrived", self.movement("station-b", "movement-101-b").get("arrival"))
        self.assertEqual("Väntar på mottagarens ankomst", self.auto.blocked["movement-101-a"])

    def test_a_manned_sender_is_answered_and_its_train_arrives_after_the_running_time(self):
        box = self.box("box-a", "station-a")
        self.advance(at(9, 18))
        self.assertEqual([], self.service.open_cases(None), "a manned sender asks itself")
        self.service.execute_station_command(box, "station-a", "clearance.request",
                                             {"movement_id": "movement-101-a", "connection_id": "connection-a-b"})
        self.advance(at(9, 19))
        self.assertEqual("approved", self.service.open_cases(None)[0]["status"], "the unmanned receiver clears it")
        self.service.execute_station_command(box, "station-a", "train.departed", {"movement_id": "movement-101-a"})
        self.advance(at(9, 33, 59))
        self.assertNotEqual("arrived", self.movement("station-b", "movement-101-b").get("arrival"))
        self.advance(at(9, 34, 1))   # departed 09:19, fifteen minutes on the line
        self.assertEqual("arrived", self.movement("station-b", "movement-101-b")["arrival"])

    def test_the_automation_is_refused_on_a_manned_station(self):
        self.box()
        with self.assertRaises(CommandRejected) as refused:
            self.service.execute_station_command(ACTOR, "station-b", "train.position.set",
                                                 {"movement_id": "movement-101-b"})
        self.assertEqual("automatic_station_manned", refused.exception.reason)

    def test_a_lost_operator_is_waited_for_until_the_station_is_handed_back(self):
        self.box()
        self.time += 60
        self.assertEqual("disconnected", self.auto.mode("station-b"))
        self.advance(at(9, 18))
        self.assertEqual("waiting", self.service.open_cases(None)[0]["status"], "no takeover on a lost contact")
        self.auto.hand_back("station-b")
        self.assertEqual("automatic", self.auto.mode("station-b"))
        self.advance(at(9, 19))
        self.assertEqual("approved", self.service.open_cases(None)[0]["status"])
        self.service.observe_operator("box-b")
        self.assertEqual("automatic", self.auto.mode("station-b"), "a handed-back box does not take it again")
        self.auto.take_over("station-b", "box-b")
        self.assertEqual("manual", self.auto.mode("station-b"))

    def test_a_signal_box_working_a_station_mans_it(self):
        terminal = self.ids.register_client("tkl-cda", "CDA TKL", DeviceKind.TKL_TERMINAL, "secret", ("panel-b",))
        self.app.tkl_context(terminal, "station-b")     # no traffic shift needed
        self.assertEqual("manual", self.auto.mode("station-b"))
        self.assertEqual("automatic", self.auto.mode("station-a"))

    def test_trains_planned_before_the_automation_started_are_not_sent(self):
        self.advance(at(10, 0))
        self.advance(at(10, 1))
        self.assertEqual([], self.service.open_cases(None))

    def test_switched_off_nothing_moves(self):
        self.auto.set_enabled(False)
        self.advance(at(9, 18))
        self.advance(at(9, 20))
        self.assertEqual([], self.service.open_cases(None))

    def test_a_stopped_clock_stops_the_automation(self):
        self.clock(at(9, 18), running=False)
        self.auto.tick()
        self.assertEqual([], self.service.open_cases(None))

    def test_a_simulation_stops_the_automation(self):
        self.app.simulation.start({"confirmed": True, "profile": "timetable", "time": "09:17", "speed": 1})
        self.assertFalse(self.auto.running())
        self.assertFalse(self.auto.status()["enabled"] and not self.auto.status()["simulation"])

    def test_admin_api_reads_and_controls_the_stations(self):
        self.box()
        status = self.app.automatic_stations_status(self.admin)
        self.assertTrue(status["enabled"])
        self.assertEqual({"station-a": "automatic", "station-b": "manual"},
                         {station["id"]: station["mode"] for station in status["stations"]})
        status = self.app.control_automatic_stations(self.admin, {"action": "automatic", "station_id": "station-b",
                                                                  "confirmed": True})
        self.assertEqual("automatic", next(s["mode"] for s in status["stations"] if s["id"] == "station-b"))
        status = self.app.control_automatic_stations(self.admin, {"action": "enable", "enabled": False})
        self.assertFalse(status["enabled"])
        with self.assertRaises(HTTPAPIError):
            self.app.control_automatic_stations(self.admin, {"action": "automatic", "station_id": "station-b"})


if __name__ == "__main__":
    unittest.main()
