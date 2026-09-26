from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from runtime_fixture import runtime_package_v3
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import TrainMeetHTTPApplication, HTTPServerConfig, HTTPAPIError
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import CommandRejected
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.simulation import TrafficSimulation, SimulationError, build_plan


def three_station_package():
    p = runtime_package_v3()
    p["stations"].append({**p["stations"][0], "id": "station-c", "code": "VA", "name": "Vagnsta", "diagram_order": 2})
    p["display"]["graph_station_order"].append("station-c")
    p["tracks"].append({**p["tracks"][-1], "id": "track-c-1", "station_id": "station-c"})
    p["connections"].append({**p["connections"][0], "id": "connection-b-c", "station_a_id": "station-b", "station_b_id": "station-c"})
    p["panels"].append({"id": "panel-c", "station_id": "station-c", "name": "VA", "slots": {"A": "connection-b-c"}})
    p["panels"][1]["slots"]["B"] = "connection-b-c"
    p["services"][0]["stops"][1]["departure_time"] = "09:38"
    p["services"][0]["stops"].append({"station_id": "station-c", "station_name": "VA", "stop_order": 2,
        "arrival_time": "09:48", "departure_time": None, "service_day_offset": 0, "service_minute": 588})
    row = next(r for r in p["trains"] if r["id"] == "movement-101-b")
    row["departure_time"] = "09:38"
    p["trains"].append({**row, "id": "movement-101-c", "station_id": "station-c", "track_id": "track-c-1",
                        "arrival_time": "09:48", "departure_time": None, "sort_time": "09:48"})
    return p


class SimulationTests(unittest.TestCase):
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
        self.sim = self.app.simulation
        self.admin = self.app.local_admin()
        self.time = 1000
        self.sim.now = lambda: self.time

    def tearDown(self):
        self.ops.close()
        self.ids.close()
        self.runtime.close()
        self.app.lifecycle.close()
        self.temp.cleanup()

    def start(self, **options):
        return self.sim.start({"profile": "timetable", "time": "09:17", "speed": 1, **options})

    def advance(self, value):
        # Deterministic game-clock ticks, never wall-clock sleeps.
        self.sim._set_seconds(value, running=True)
        self.sim.tick()

    def register(self, name="box-b", station="station-b"):
        self.ids.record_discovery(name, name, protocol_version=2)
        if station:
            self.ids.assign_discovered_device(name, station_id=station)
        return name

    def command(self, action, **extra):
        return self.app.control_simulation(self.admin, {"action": action,
            "meet_generation": self.app.lifecycle.selected()["generation"],
            "run_id": self.sim.run["id"] if self.sim.active else None, **extra})

    def test_auto_departure_and_arrival_use_shared_traffic(self):
        self.start()
        self.advance(9 * 3600 + 18 * 60)
        self.assertEqual(self.service.open_cases(None)[0]["status"], "approved")
        self.assertFalse(self.sim.run["departed"])
        self.advance(9 * 3600 + 20 * 60)
        self.assertEqual(self.ops.positions()[0]["status"], "connection")
        self.advance(9 * 3600 + 35 * 60 + 1)
        self.assertEqual(self.ops.positions()[0]["station_id"], "station-b")
        self.assertFalse(self.service.open_cases(None))

    def test_normal_state_and_clock_are_untouched(self):
        self.ops.record_traffic_position("777", status="station", station_id="station-a")
        before = self.ops.clock_status()
        self.start(time="09:25")
        self.assertNotEqual(self.ops.positions()[0]["train_number"], "777")
        self.sim.finish()
        self.assertEqual(self.ops.positions()[0]["train_number"], "777")
        self.assertEqual(self.ops.clock_status(), before)

    def test_reset_at_current_time_preserves_operators_but_not_old_commands(self):
        box = self.register()
        self.start()
        self.service.observe_operator(box)
        old_frame = self.app.terminal16.frame(box)
        old_run = self.sim.run["id"]
        self.sim._set_seconds(9 * 3600 + 25 * 60)
        self.sim.reset()
        self.assertNotEqual(old_run, self.sim.run["id"])
        self.assertEqual(self.ops.clock_status()["time"], "09:25:00")
        self.assertFalse(self.ops.clock_status()["running"])
        self.assertEqual(self.sim.run["stations"]["station-b"], box)
        self.assertEqual(self.ops.positions()[0]["status"], "connection")
        answer = self.app.terminal16.command(box, {"command_id": uuid4().hex, "key": "#", "view_token": old_frame["view_token"]})
        self.assertEqual(answer["status"], "rejected")

    def test_manual_receiver_is_not_approved_by_auto_operator(self):
        box = self.register()
        self.start()
        self.service.observe_operator(box)
        self.advance(9 * 3600 + 20 * 60)
        case = self.service.open_cases(None)[0]
        self.assertEqual(case["status"], "waiting")
        self.service.execute_station_command(box, "station-b", "clearance.response", {"clearance_id": case["clearance_id"], "approved": True})
        self.sim.tick()
        self.assertIn("movement-101-a", self.sim.run["departed"])
        with self.assertRaisesRegex(CommandRejected, "ännu"):
            self.service.execute_station_command(box, "station-b", "train.arrived", {"movement_id": "movement-101-b"})
        self.advance(9 * 3600 + 36 * 60)
        self.assertTrue(self.service.open_cases(None))
        self.service.execute_station_command(box, "station-b", "train.arrived", {"movement_id": "movement-101-b"})
        self.assertFalse(self.service.open_cases(None))

    def test_unassigned_client_cannot_claim_station_by_name(self):
        box = self.register("CDA-TKL", None)
        self.start()
        self.service.observe_operator(box)
        self.assertFalse(self.sim.run["stations"])

    def test_ticks_do_not_fake_liveness_and_offline_station_stays_manual(self):
        box = self.register()
        self.start()
        self.service.observe_operator(box)
        self.time += 40
        self.sim.tick()
        self.time += 10
        self.sim.tick()
        modes = {s["id"]: s["mode"] for s in self.sim.status()["stations"]}
        self.assertEqual(modes["station-b"], "disconnected")
        self.sim.hand_back("station-b")
        self.service.observe_operator(box)
        self.assertEqual(self.sim._mode("station-b"), "automatic")

    def test_only_one_primary_operator(self):
        a, b = self.register("first"), self.register("second")
        self.start()
        self.service.observe_operator(a)
        self.service.observe_operator(b)
        with self.assertRaises(CommandRejected):
            self.service.execute_station_command(b, "station-b", "train.arrived", {"movement_id": "movement-101-b"})

    def test_pause_preserves_clock_and_forbids_traffic(self):
        box = self.register("sender", "station-a")
        self.start()
        self.service.observe_operator(box)
        self.sim.pause()
        before = deepcopy(self.sim.run)
        self.time += 1000
        self.sim.tick()
        self.assertEqual(self.sim.run, before)
        with self.assertRaisesRegex(CommandRejected, "pausad"):
            self.service.execute_station_command(box, "station-a", "clearance.request", {"movement_id": "movement-101-a", "connection_id": "connection-a-b"})

    def test_disturbance_is_seeded_and_no_early_departure(self):
        self.start(profile="disrupted", seed="repeatable")
        delay = self.sim._delay("movement-101-a")
        self.assertEqual(delay, self.sim._delay("movement-101-a"))
        self.advance(9 * 3600 + 20 * 60 - 1)
        self.assertFalse(self.sim.run["departed"])
        self.advance(9 * 3600 + 20 * 60 + delay)
        self.assertTrue(self.sim.run["departed"])

    def test_active_clock_and_live_traffic_block_start(self):
        self.ops.start_clock()
        with self.assertRaises(SimulationError): self.start()
        self.ops.stop_clock()
        self.service.execute_station_command("test", "station-a", "clearance.request", {"movement_id": "movement-101-a", "connection_id": "connection-a-b"})
        with self.assertRaises(SimulationError): self.start()

    def test_recovery_is_paused_and_keeps_inflight_train(self):
        self.start(time="09:25")
        run_id = self.sim.run["id"]
        self.sim.close()
        self.sim = TrafficSimulation(self.service)
        self.app.simulation = self.sim
        self.assertEqual(self.sim.run["id"], run_id)
        self.assertFalse(self.ops.clock_status()["running"])
        self.assertEqual(self.ops.positions()[0]["status"], "connection")

    def test_http_control_requires_admin_generation_and_confirmation(self):
        self.register()
        with self.assertRaises(HTTPAPIError): self.app.simulation_status(self.ids.client("box-b"))
        self.command("start", time="09:17", profile="timetable")
        with self.assertRaises(HTTPAPIError): self.command("reset")
        self.command("reset", confirmed=True)
        self.assertFalse(self.ops.clock_status()["running"])
        with self.assertRaises(HTTPAPIError):
            self.app.control_simulation(self.admin, {"action": "finish", "confirmed": True, "meet_generation": 0})
        self.command("finish", confirmed=True)
        self.assertFalse(self.sim.active)

    def test_day_and_external_clock_changes_blocked(self):
        self.start()
        with self.assertRaises(HTTPAPIError): self.app.set_active_day(self.admin, {"active_day": "Sön"})
        with self.assertRaises(HTTPAPIError): self.app.configure_clock_source(self.admin, {})

    def test_missing_routes_fail_before_touching_live_data(self):
        payload = deepcopy(self.pub.payload)
        payload["services"][0]["stops"][1]["arrival_time"] = "09:36"
        from tmbox_gateway.runtime import RuntimePublication
        bad = RuntimePublication.parse(payload)
        with self.assertRaises(SimulationError): build_plan(bad, "Lör")
        self.assertFalse(self.sim.active)

    def test_following_leg_waits_for_actual_arrival_and_station_work(self):
        p = three_station_package()
        from tmbox_gateway.runtime import RuntimePublication
        self.pub = RuntimePublication.parse(p)
        self.service.runtime_store.active = lambda: self.pub
        self.service._cached_publication_id = None
        self.start()
        self.advance(9 * 3600 + 25 * 60)  # five minutes late
        self.advance(9 * 3600 + 39 * 60)
        self.assertNotIn("movement-101-b", self.sim.run["departed"])
        self.advance(9 * 3600 + 40 * 60 + 1)
        self.assertIn("movement-101-a", self.sim.run["arrived"])
        self.advance(9 * 3600 + 42 * 60)
        self.assertNotIn("movement-101-b", self.sim.run["departed"])
        self.advance(9 * 3600 + 43 * 60 + 2)
        self.assertIn("movement-101-b", self.sim.run["departed"])

    def test_midnight_retains_day_offset_when_pausing_and_resetting(self):
        p = runtime_package_v3()
        for row in p["trains"]:
            if row["id"] == "movement-101-a": row["departure_time"] = "23:55"
            if row["id"] == "movement-101-b": row["arrival_time"] = "00:10"
        p["services"][0]["stops"][0]["departure_time"] = "23:55"
        p["services"][0]["stops"][1].update(arrival_time="00:10", service_day_offset=1)
        from tmbox_gateway.runtime import RuntimePublication
        self.pub = RuntimePublication.parse(p)
        self.service.runtime_store.active = lambda: self.pub
        self.start(time="23:56")
        self.advance(86400 + 5 * 60)
        self.sim.pause()
        self.assertGreaterEqual(self.ops.clock_status()["elapsed_seconds"], 86400)
        self.sim.reset()
        self.assertEqual(self.ops.clock_status()["time"], "00:05:00")
        self.assertEqual(self.ops.positions()[0]["status"], "connection")
        self.advance(86400 + 10 * 60 + 1)
        self.assertIn("movement-101-a", self.sim.run["arrived"])

    def test_late_timetable_rows_do_not_reserve_all_tracks(self):
        self.start(time="09:25")
        self.assertIsNone(self.service.track_conflict(self.pub, "Lör", "station-b", "unrelated", "track-station-b-1"))
        self.advance(9 * 3600 + 35 * 60 + 1)
        self.assertIsNotNone(self.service.track_conflict(self.pub, "Lör", "station-b", "unrelated", "track-station-b-1"))
        self.advance(9 * 3600 + 40 * 60 + 2)
        self.assertIn("movement-101-a", self.sim.run["stabled"])
        self.assertIsNone(self.service.track_conflict(self.pub, "Lör", "station-b", "unrelated", "track-station-b-1"))

    def test_cloud_update_is_staged_until_simulation_ends(self):
        self.start()
        p = runtime_package_v3(publication_id="next-config")
        result = self.app.cloud_config._deliver(p)
        self.assertTrue(result["pending"])
        self.assertEqual(self.runtime.active().publication_id, self.pub.publication_id)
        self.assertIn("simuleringen", result["message"])

    def test_external_clock_never_receives_simulation_commands(self):
        from unittest.mock import Mock
        self.app.external_clock = Mock()
        self.app.external_clock.status.return_value = {"configured": True, "running": False, "time": "09:10:00", "speed": 1}
        self.start()
        self.app.control_clock(self.admin, {"action": "stop", "meet_generation": self.app.lifecycle.selected()["generation"]})
        self.app.external_clock.control.assert_not_called()
        self.assertFalse(self.ops.clock_status()["running"])

    def test_admin_can_hand_station_back_then_explicitly_reassign_same_operator(self):
        box = self.register()
        self.start()
        self.service.observe_operator(box)
        self.sim.hand_back("station-b")
        self.assertEqual(self.sim._mode("station-b"), "automatic")
        self.sim.take_over("station-b", box)
        self.assertEqual(self.sim._mode("station-b"), "manual")

    def test_rejected_clearance_is_not_repeated_automatically(self):
        box = self.register()
        self.start()
        self.service.observe_operator(box)
        self.advance(9 * 3600 + 18 * 60)
        case = self.service.open_cases(None)[0]
        self.service.execute_station_command(box, "station-b", "clearance.response", {"clearance_id": case["clearance_id"], "approved": False})
        for _ in range(3): self.sim.tick()
        self.assertFalse(self.service.open_cases(None))
        self.assertIn("nekad", self.sim.run["blocked"]["movement-101-a"])

    def test_expiry_uses_game_time_not_wall_time(self):
        box = self.register()
        self.start()
        self.service.observe_operator(box)
        self.advance(9 * 3600 + 18 * 60)
        case = self.service.open_cases(None)[0]
        self.sim.tick()  # record pending request game time
        self.ops._connection.execute("UPDATE clearances SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.service.open_cases(None)[0]["status"], "waiting")
        self.sim.pause()
        self.time += 1000
        self.sim.tick()
        self.assertEqual(self.ops.clearance(case["clearance_id"])["status"], "waiting")
        self.advance(9 * 3600 + 24 * 60)
        self.assertEqual(self.ops.clearance(case["clearance_id"])["status"], "expired")

    def test_retained_and_offline_presence_do_not_claim_station(self):
        from tmbox_gateway.mqtt_v2 import TMBoxV2Gateway
        box = self.register()
        self.start()
        gateway = TMBoxV2Gateway(self.service, self.ids, gateway_id="sim-test", publish=lambda *args: None)
        gateway.on_message(f"tmbox/v2/device/{box}/presence", b'{"status":"online"}', retained=True)
        gateway.on_message(f"tmbox/v2/device/{box}/presence", b'{"status":"offline"}')
        self.assertFalse(self.sim.run["stations"])
        gateway.on_message(f"tmbox/v2/device/{box}/presence", b'{"status":"online"}')
        self.assertEqual(self.sim.run["stations"]["station-b"], box)

    def test_rolled_back_compound_command_cannot_advance_train(self):
        self.start()
        with self.assertRaises(RuntimeError):
            with self.ops.atomic_command():
                self.sim.record_action("train.departed", "movement-101-a")
                self.sim._capture(0)
                self.assertIn("movement-101-a", self.sim.run["departed"])
                raise RuntimeError("failed second step")
        self.sim._capture(0)
        self.assertNotIn("movement-101-a", self.sim.run["departed"])

    def test_reset_notifies_only_completed_run(self):
        self.start()
        notices = []
        self.service.subscribe(lambda: notices.append(self.sim._initializing))
        self.sim._set_seconds(9 * 3600 + 25 * 60)
        self.sim.reset()
        self.assertTrue(notices)
        self.assertFalse(any(notices))

    def test_finish_does_not_restart_external_live_clock(self):
        self.start()
        self.sim.normal_external_source = lambda: {"running": True}
        with self.assertRaisesRegex(SimulationError, "externa"):
            self.sim.finish()
        self.assertTrue(self.sim.active)
        self.assertFalse(self.ops.clock_status()["running"])
        self.sim.normal_external_source = None

    def test_new_run_releases_previous_suppression(self):
        box = self.register()
        self.start()
        self.sim.observe(box)
        self.sim.hand_back("station-b")
        self.sim.finish()
        self.start()
        self.sim.observe(box)
        self.assertEqual(self.sim._mode("station-b"), "manual")

    def test_backup_and_factory_reset_cannot_overwrite_active_simulation(self):
        self.start()
        with self.assertRaises(HTTPAPIError): self.app.restore_backup(self.admin, {})
        with self.assertRaises(HTTPAPIError): self.app.factory_reset_server(self.admin, {}, local_access=True)

    def test_direct_dispatch_respects_the_same_clock_and_arrival_chain(self):
        from tmbox_gateway.runtime import RuntimePublication
        p = runtime_package_v3()
        p["connections"][0]["dispatch_mode_override"] = "direct"
        self.pub = RuntimePublication.parse(p)
        self.service.runtime_store.active = lambda: self.pub
        self.start()
        self.advance(9 * 3600 + 18 * 60)
        self.assertEqual(self.service.open_cases(None)[0]["status"], "approved")
        self.assertFalse(self.sim.run["departed"])
        self.advance(9 * 3600 + 20 * 60)
        self.advance(9 * 3600 + 36 * 60)
        self.assertIn("movement-101-a", self.sim.run["arrived"])


if __name__ == "__main__":
    unittest.main()
