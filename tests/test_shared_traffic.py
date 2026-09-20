"""Mixed-generation acceptance tests: no cloud, no client traffic authority."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime_fixture import runtime_package_v3
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import Command
from tmbox_gateway.mqtt_v2 import TMBoxV2Gateway
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import CommandRejected, TMBoxStationService
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.storage import SQLiteStateStore
from tmbox_gateway.central_sync import CentralRuntimeDownload


class SharedTrafficTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)
        self.publication = self.runtime.install(runtime_package_v3())
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)
        self.store = SQLiteStateStore(self.path)
        for name, station, version in (("esp8266", "station-a", 1), ("esp32", "station-b", 2), ("esp32-a", "station-a", 2)):
            self.ids.record_discovery(name, name, protocol_version=version)
            self.ids.assign_discovered_device(name, station_id=station)
        self.service = TMBoxStationService(self.runtime, self.ops, self.ids)
        self.engine = TrafficEngine(self.publication.session_config(), state_store=self.store)
        self.app = TrainMeetHTTPApplication(self.engine, self.ids, PairingService(self.ids, set(self.engine.config.panels)),
            HTTPServerConfig(local_development=True), runtime_store=self.runtime, operations_store=self.ops, station_service=self.service)
        self.admin = self.app.local_admin()
        self.seq = 0
        self.messages = []
        self.gateway = TMBoxV2Gateway(self.service, self.ids, gateway_id="test",
            publish=lambda topic, body, retain: self.messages.append((topic, body)))

    def tearDown(self):
        self.store.close()
        self.ids.close()
        self.ops.close()
        self.runtime.close()
        self.temp.cleanup()

    def install(self, package):
        self.app.runtime_fetcher = lambda *_: CentralRuntimeDownload(package, "test-link")
        result = self.app.sync_runtime(self.admin, {"sync_code": "123456"})
        self.assertFalse(result.get("pending"))
        self.publication = self.runtime.active()
        return self.publication

    def key(self, key, panel="panel-a", device="esp8266"):
        self.seq += 1
        revision = self.engine.snapshot(panel)["revision"]
        result = self.engine.press(Command(str(self.seq), device, self.engine.config.id, panel, revision, key))
        self.assertEqual(result.status, "accepted", result.reason)
        return result

    def request_v1(self):
        for key in "A101#":
            self.key(key)
        self.assertEqual(self.engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        return self.service.open_cases("station-a")[0]["clearance_id"]

    def v2(self, action, body, device="esp32", **extra):
        self.seq += 1
        return self.service.handle_command(device, {"protocol_version": 2, "message_id": "v2-" + str(self.seq),
            "action": action, "payload": body, **extra})

    def approve(self, case):
        ack = self.v2("clearance.response", {"clearance_id": case, "approved": True})
        self.assertEqual(ack["status"], "accepted", ack)

    def depart_v1(self):
        for key in "AAA":
            self.key(key)

    def start_shift(self, station):
        self.app.start_tkl_shift(self.admin, {"station_id": station, "operator_name": "Tester", "terminal_name": "TKL"})

    def test_8266_request_32_answer_8266_depart_tkl_arrival(self):
        case = self.request_v1()
        self.assertTrue(any(topic == "tmbox/v2/device/esp32/snapshot" and body["active_clearances"] for topic, body in self.messages))
        self.approve(case)
        self.assertEqual(self.engine.snapshot("panel-a")["slots"]["A"]["state"], "reserved")
        self.depart_v1()
        self.assertEqual(self.app.display_snapshot()["connection_states"][0]["state"], "occupied")
        self.assertEqual(self.service.snapshot_payload("station-a")["movements"][0]["departure"], "departed")
        self.start_shift("station-b")
        self.app.update_tkl_movement(self.admin, {"station_id": "station-b", "movement_id": "movement-101-b", "arrival": "arrived"})
        self.assertEqual(self.engine.snapshot("panel-a")["slots"]["A"]["state"], "free")
        self.assertEqual(self.service.open_cases("station-a"), [])
        self.assertEqual(self.ops.positions()[0]["station_id"], "station-b")

    def test_32_request_8266_answer_tkl_depart_8266_arrive(self):
        result = self.v2("clearance.request", {"movement_id": "movement-101-a", "connection_id": "connection-a-b"}, "esp32-a")
        self.assertEqual(result["status"], "accepted")
        self.key("A", "panel-b")
        self.key("A", "panel-b")
        self.start_shift("station-a")
        self.app.tkl_clearance_action(self.admin, {"station_id": "station-a", "connection_id": "connection-a-b", "action": "depart"})
        self.key("A", "panel-b")
        self.key("A", "panel-b")
        self.assertEqual(self.service.open_cases("station-a"), [])

    def test_32_cannot_depart_without_8266_or_tkl_permission(self):
        result = self.v2("train.departed", {"movement_id": "movement-101-a"}, "esp32-a")
        self.assertEqual(result["reason"], "departure_not_reserved")

    def test_tkl_repeated_movement_form_after_line_action_is_a_noop(self):
        self.approve(self.request_v1())
        self.start_shift("station-a")
        self.app.tkl_clearance_action(self.admin, {"station_id": "station-a", "connection_id": "connection-a-b", "action": "depart"})
        result = self.app.update_tkl_movement(self.admin, {
            "station_id": "station-a", "movement_id": "movement-101-a", "departure": "departed",
            "arrival": "none", "actual_track": "1",
        })
        self.assertEqual(result["movement"]["departure"], "departed")

    def test_tkl_cannot_bypass_permission_in_movement_form(self):
        self.start_shift("station-a")
        with self.assertRaises(HTTPAPIError) as caught:
            self.app.update_tkl_movement(self.admin, {"station_id": "station-a", "movement_id": "movement-101-a", "departure": "departed"})
        self.assertEqual(caught.exception.code, "departure_not_reserved")

    def test_arrival_does_not_release_a_train_that_has_not_departed(self):
        case = self.request_v1()
        self.approve(case)
        result = self.v2("train.arrived", {"movement_id": "movement-101-b"})
        self.assertEqual(result["reason"], "train_not_departed")
        self.assertEqual(len(self.service.open_cases("station-a")), 1)

    def test_restart_restores_shared_case_and_no_input_session(self):
        case = self.request_v1()
        self.approve(case)
        self.depart_v1()
        engine = TrafficEngine(self.publication.session_config(), state_store=self.store)
        from tmbox_gateway.shared_traffic import SharedPanelTraffic
        SharedPanelTraffic(engine, TMBoxStationService(self.runtime, self.ops, self.ids))
        self.assertEqual(engine.snapshot("panel-b")["slots"]["A"]["state"], "occupied")
        self.assertEqual(engine.snapshot("panel-b")["interaction"]["mode"], "idle")

    def test_request_receipt_and_traffic_change_roll_back_together(self):
        with patch.object(self.ops, "remember_device_command", side_effect=RuntimeError("disk failure")):
            with self.assertRaises(RuntimeError):
                self.v2("clearance.request", {"movement_id": "movement-101-a", "connection_id": "connection-a-b"}, "esp32-a")
        self.assertEqual(self.service.open_cases("station-a"), [])

    def test_8266_failed_disk_write_never_publishes_or_keeps_the_request(self):
        for key in "A101":
            self.key(key)
        self.messages.clear()
        with patch.object(self.ops, "save_panel_cache", side_effect=RuntimeError("disk failure")):
            with self.assertRaises(RuntimeError):
                self.key("#")
        self.assertEqual(self.service.open_cases("station-a"), [])
        self.assertEqual(self.messages, [])
        self.assertEqual(self.engine.panels["panel-a"].train_number, "101")

    def test_notification_happens_after_commit_visible_to_another_connection(self):
        observer = SQLiteOperationsStore(self.path)
        self.addCleanup(observer.close)
        observed = []
        self.service.subscribe(lambda: observed.append(observer.open_clearances_for_station(
            self.publication.publication_id, self.runtime.active_day(), "station-a")))
        self.request_v1()
        self.assertTrue(observed[-1])

    def test_simultaneous_requests_have_one_winner(self):
        def request(number):
            return self.service.handle_command("esp32-a", {"protocol_version": 2, "message_id": str(number), "action": "clearance.request",
                "payload": {"movement_id": "movement-101-a", "connection_id": "connection-a-b"}})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, range(2)))
        self.assertEqual(sorted(r["status"] for r in results), ["accepted", "rejected"])
        self.assertEqual(len(self.service.open_cases("station-a")), 1)

    def test_same_v2_message_is_only_one_decision(self):
        command = {"protocol_version": 2, "message_id": "fixed", "action": "clearance.request",
                   "payload": {"movement_id": "movement-101-a", "connection_id": "connection-a-b"}}
        self.assertEqual(self.service.handle_command("esp32-a", command)["status"], "accepted")
        self.assertEqual(self.service.handle_command("esp32-a", command)["status"], "duplicate")
        self.assertEqual(len(self.service.open_cases("station-a")), 1)

    def test_ambiguous_train_number_is_not_guessed_for_8266(self):
        package = runtime_package_v3()
        extra = deepcopy(package["trains"][0])
        extra["id"] = "another-visit"
        extra["departure_time"] = "12:00"
        package["trains"].append(extra)
        package["publication_id"] = "ambiguous-test"
        self.install(package)
        result = self.engine.perform(station_id="station-a", connection_id="connection-a-b", action="request", train_number="101")
        self.assertEqual(result, (False, "ambiguous_train_number"))
        self.assertEqual(self.service.open_cases("station-a"), [])

    def test_approved_case_can_be_cancelled_but_departed_case_cannot(self):
        case = self.request_v1()
        self.approve(case)
        result = self.v2("clearance.cancel", {"clearance_id": case}, "esp32-a")
        self.assertEqual(result["status"], "accepted")
        case = self.request_v1()
        self.approve(case)
        self.depart_v1()
        result = self.v2("clearance.cancel", {"clearance_id": case}, "esp32-a")
        self.assertEqual(result["reason"], "train_already_departed")

    def test_direct_dispatch_uses_same_approval_on_all_clients(self):
        package = runtime_package_v3()
        package["connections"][0]["dispatch_mode_override"] = "direct"
        package["publication_id"] = "direct-test"
        self.install(package)
        self.request_v1()
        self.assertEqual(self.service.open_cases("station-a")[0]["status"], "approved")

    def test_other_day_cannot_answer_an_old_case(self):
        case = self.request_v1()
        self.runtime.set_active_day("Sön")
        result = self.v2("clearance.response", {"clearance_id": case, "approved": True})
        self.assertEqual(result["reason"], "unknown_clearance")

    def test_upgrade_never_discards_an_active_legacy_clearance(self):
        legacy = TrafficEngine(self.publication.session_config())
        legacy.perform(station_id="station-a", connection_id="connection-a-b", action="request", train_number="101")
        from tmbox_gateway.shared_traffic import SharedPanelTraffic
        with self.assertRaisesRegex(RuntimeError, "Ingen trafikdata har raderats"):
            SharedPanelTraffic(legacy, self.service)
        self.assertEqual(legacy.connections["connection-a-b"].train_number, "101")
        self.assertEqual(self.service.open_cases("station-a"), [])

    def test_8266_confirmation_becomes_stale_when_32_cancels(self):
        case = self.request_v1()
        self.approve(case)
        self.key("A")
        self.key("A")
        old_revision = self.engine.snapshot("panel-a")["revision"]
        self.v2("clearance.cancel", {"clearance_id": case}, "esp32-a")
        ack = self.engine.press(Command("late", "esp8266", self.engine.config.id, "panel-a", old_revision, "A"))
        self.assertEqual(ack.reason, "stale_revision")
        self.assertEqual(self.engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        self.assertEqual(self.service.open_cases("station-a"), [])

    def test_opposite_8266_and_32_trains_share_double_track_not_single_state(self):
        package = runtime_package_v3()
        package["publication_id"] = "double-mixed"
        package["connections"][0]["track_type"] = "double"
        reverse = deepcopy(package["trains"][1])
        reverse.update(id="movement-102-b", train_number="102", arrival_time=None, departure_time="10:00")
        package["trains"].append(reverse)
        self.install(package)
        outgoing = self.request_v1()
        self.approve(outgoing)
        opposite = self.v2("clearance.request", {"movement_id": "movement-102-b", "connection_id": "connection-a-b"})
        self.assertEqual(opposite["status"], "accepted", opposite)
        # A handles the incoming request, then the same slot resumes our departure.
        self.key("A")
        self.assertEqual(self.engine.snapshot("panel-a")["interaction"]["mode"], "incoming_request")
        self.key("A")
        self.assertEqual(len(self.service.open_cases("station-a")), 2)
        self.key("A")
        self.assertEqual(self.engine.snapshot("panel-a")["interaction"]["mode"], "ready_departure")
