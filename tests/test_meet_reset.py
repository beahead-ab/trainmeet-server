"""Nollställ träffen: samma plan, från början.

Farozon → Nollställ träffen tar bort allt som hänt i träffen och ställer
klockan på planens starttid. Det som inte är träffens förlopp står kvar:
planen, Cloud-kopplingen, enheterna och var de sitter, användarna och
klockans inställningar. En säkerhetskopia tas först, och går den inte att ta
görs ingenting.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime_fixture import runtime_package_v3
from tmbox_gateway import backup
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.models import Command, ConnectionState, InteractionMode
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import TMBoxStationService
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.storage import SQLiteStateStore

MEET = "Sommarträffen"


class MeetResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Samma namn som på en riktig server, så att säkerhetskopian tas.
        path = Path(self.temp.name) / "trainmeet.db"
        self.runtime = SQLiteRuntimeStore(path)
        self.publication = self.runtime.install(runtime_package_v3())
        self.runtime.save_link_token("kopplingen-till-cloud")
        self.ops = SQLiteOperationsStore(path)
        self.ids = IdentityStore(path)
        self.store = SQLiteStateStore(path)
        for name, station in (("esp8266", "station-a"), ("esp32", "station-b")):
            self.ids.record_discovery(name, name, protocol_version=1 if name == "esp8266" else 2)
            self.ids.assign_discovered_device(name, station_id=station)
        self.service = TMBoxStationService(self.runtime, self.ops, self.ids)
        self.engine = TrafficEngine(self.publication.session_config(), state_store=self.store)
        self.app = TrainMeetHTTPApplication(
            self.engine, self.ids, PairingService(self.ids, set(self.engine.config.panels)),
            HTTPServerConfig(local_development=True, state_dir=self.temp.name),
            runtime_store=self.runtime, operations_store=self.ops, station_service=self.service)
        for closeable in (self.store, self.ids, self.ops, self.runtime):
            self.addCleanup(closeable.close)
        self.admin = self.app.local_admin()
        self.seq = 0

    # -------------------------------------------------------------- hjälp

    def generation(self):
        return self.app.lifecycle.selected()["generation"]

    def key(self, key, panel="panel-a", device="esp8266"):
        self.seq += 1
        revision = self.engine.snapshot(panel)["revision"]
        result = self.engine.press(Command(str(self.seq), device, self.engine.config.id, panel, revision, key))
        self.assertEqual("accepted", result.status, result.reason)

    def request(self):
        for key in "A101#":
            self.key(key)
        return self.service.open_cases("station-a")[0]["clearance_id"]

    def approve(self, case):
        self.seq += 1
        ack = self.service.handle_command("esp32", {
            "protocol_version": 2, "message_id": f"v2-{self.seq}", "action": "clearance.response",
            "payload": {"clearance_id": case, "approved": True}, **self.service.runtime_scope()})
        self.assertEqual("accepted", ack["status"], ack)

    def play_for_a_while(self):
        """Ett tåg ute på linjen, ett TKL-pass och en klocka som går."""
        self.app.control_clock(self.admin, {"action": "start", "time": "10:40:00", "speed": 4,
                                            "meet_generation": self.generation()})
        self.app.control_clock(self.admin, {"action": "appearance", "style": "digital", "show_seconds": False,
                                            "meet_generation": self.generation()})
        self.approve(self.request())
        for key in "AAA":
            self.key(key)
        self.app.start_tkl_shift(self.admin, {"station_id": "station-b", "operator_name": "Tester",
                                              "terminal_name": "TKL"})
        self.assertTrue(self.ops.positions())
        self.assertEqual("occupied", self.lines()["connection-a-b"])

    def lines(self):
        return {line["id"]: line["state"] for line in self.app.display_snapshot()["connection_states"]}

    def reset(self, confirmation=MEET, client=None):
        return self.app.reset_meet(client or self.admin, {"confirmation": confirmation})

    def count(self, table):
        return self.ops._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: SLF001

    # -------------------------------------------------------------- proven

    def test_the_meet_starts_over_on_the_same_plan(self):
        self.play_for_a_while()
        before = self.generation()

        result = self.reset()

        self.assertTrue(result["reset"])
        self.assertEqual([], self.service.open_cases(None))
        self.assertEqual({"free"}, set(self.lines().values()))
        self.assertEqual([], self.ops.positions())
        for table in ("clearances", "clearance_events", "line_available_messages", "tkl_movement_states",
                      "tkl_events", "train_readiness", "device_commands"):
            with self.subTest(table=table):
                self.assertEqual(0, self.count(table))
        self.assertEqual(0, self.ops._connection.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM tkl_shifts WHERE status != 'closed'").fetchone()[0])
        self.assertGreater(result["meet_generation"], before, "boxar och skärmar hämtar läget på nytt")
        self.assertEqual(self.publication.publication_id, self.runtime.active().publication_id)

    def test_the_clock_stands_still_on_the_plans_start_time_with_its_settings_kept(self):
        self.play_for_a_while()

        clock = self.reset()["clock"]

        self.assertEqual(("09:15:00", False), (clock["time"], clock["running"]))
        self.assertEqual((4, False), (clock["speed"], clock["show_seconds"]))
        self.assertEqual("09:15:00", self.ops.clock_status()["time"], "och det står kvar")

    def test_what_is_not_the_meets_course_stays(self):
        self.play_for_a_while()
        users_before = self.ids.list_admin_users()

        self.reset()

        self.assertEqual("kopplingen-till-cloud", self.runtime.link_token())
        self.assertEqual("station-a", self.ids.client("esp8266").station_id)
        self.assertEqual("station-b", self.ids.client("esp32").station_id)
        self.assertEqual(users_before, self.ids.list_admin_users())
        self.assertEqual(1, self.count("runtime_meet_archives"), "läget före nollställningen arkiveras")

    def test_a_backup_is_taken_first_and_named_in_the_log(self):
        self.play_for_a_while()

        result = self.reset()

        written = Path(self.temp.name) / "backups" / result["backup"]
        self.assertTrue(written.is_file())
        self.assertTrue(backup.describe(written)["usable"])
        [event] = self.ops.audit_trail(f"meet-reset-{self.publication.publication_id}")
        self.assertEqual(("meet.reset", "ok"), (event["action"], event["outcome"]))
        self.assertEqual(result["backup"], event["detail"]["backup"])

    def test_traffic_runs_again_after_the_reset(self):
        self.play_for_a_while()
        self.reset()

        case = self.request()

        self.approve(case)
        self.assertEqual("reserved", self.lines()["connection-a-b"])

    def test_a_box_halfway_through_typing_starts_over(self):
        for key in "A1":
            self.key(key)
        self.assertNotEqual(InteractionMode.IDLE, self.engine.panels["panel-a"].mode)

        self.reset()

        self.assertEqual(InteractionMode.IDLE, self.engine.panels["panel-a"].mode)
        self.assertTrue(all(line.state == ConnectionState.FREE for line in self.engine.connections.values()))

    def test_the_confirmation_is_the_meets_name(self):
        self.play_for_a_while()
        for wrong in ("", "NOLLSTÄLL", "Sommar"):
            with self.subTest(confirmation=wrong), self.assertRaises(HTTPAPIError) as raised:
                self.reset(wrong)
            self.assertEqual("meet_reset_not_confirmed", raised.exception.code)
        self.assertEqual("occupied", self.lines()["connection-a-b"], "ingenting togs bort")
        self.assertTrue(self.reset("  sommarträffen ")["reset"], "versaler och mellanrum spelar ingen roll")

    def test_without_a_backup_nothing_is_removed(self):
        self.play_for_a_while()
        with patch.object(backup, "create_backup", side_effect=backup.BackupError("disken är full")), \
                self.assertRaises(HTTPAPIError) as raised:
            self.reset()
        self.assertEqual("backup_failed", raised.exception.code)
        self.assertEqual("occupied", self.lines()["connection-a-b"])
        self.assertEqual("10:40", self.ops.clock_status()["time"][:5])

    def test_only_an_administrator_and_never_during_a_simulation(self):
        self.play_for_a_while()
        box = PairedClient(client_id="esp8266", display_name="CDA TMBox", kind=DeviceKind.ESP32_PANEL,
                           panel_ids=("panel-a",))
        with self.assertRaises(HTTPAPIError):
            self.reset(client=box)
        with patch.object(self.app.simulation, "run", object()), \
                self.assertRaises(HTTPAPIError) as raised:
            self.reset()
        self.assertEqual("simulation_active", raised.exception.code)
        self.assertEqual("occupied", self.lines()["connection-a-b"])

    def test_without_a_meet_there_is_nothing_to_reset(self):
        with patch.object(self.runtime, "active", return_value=None), self.assertRaises(HTTPAPIError) as raised:
            self.reset()
        self.assertEqual("runtime_not_configured", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
