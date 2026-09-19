"""Integration of Cloud delivery, the shared meet gate and both traffic engines."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_fixture import runtime_package_v3
from test_us_cloud import cloud_package as us_package
from tmbox_gateway.central_sync import CentralRuntimeDownload, CentralRuntimeManifest, CentralSyncError
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import TrainMeetHTTPApplication, HTTPServerConfig, HTTPAPIError
from tmbox_gateway.identity import IdentityStore, PairingService, DeviceKind, PairedClient
from tmbox_gateway.lifecycle import MeetLifecycleError
from tmbox_gateway.models import unconfigured_session, ConnectionState
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.us import USStore


class CloudOnlyDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "test.db"
        self.runtime = SQLiteRuntimeStore(path)
        self.operations = SQLiteOperationsStore(path)
        self.identities = IdentityStore(path)
        self.us = USStore(path)
        for store in (self.runtime, self.operations, self.identities, self.us):
            self.addCleanup(store.close)
        engine = TrafficEngine(unconfigured_session())
        self.offered = runtime_package_v3(publication_id="first")
        self.app = TrainMeetHTTPApplication(engine, self.identities, PairingService(self.identities, set()),
            HTTPServerConfig(), runtime_store=self.runtime, operations_store=self.operations, us_store=self.us,
            runtime_fetcher=lambda code, url: CentralRuntimeDownload(copy.deepcopy(self.offered), "test-link"),
            linked_runtime_fetcher=self.fetch)
        self.addCleanup(self.app.lifecycle.close)
        self.admin = self.app.local_admin()

    def fetch(self, token, url, manifest_only):
        if manifest_only:
            encoded = json.dumps(self.offered, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            return CentralRuntimeManifest(self.offered["publication_id"], self.offered.get("published_at", ""), hashlib.sha256(encoded).hexdigest())
        return CentralRuntimeDownload(copy.deepcopy(self.offered), "test-link")

    def connect(self, **data):
        return self.app.sync_runtime(self.admin, {"sync_code": "123456", **data})

    def test_cloud_selects_one_meet_hot_without_restart(self):
        result = self.connect()
        self.assertFalse(result["restart_required"])
        self.assertEqual("first", self.app.engine.config.id)
        self.assertEqual("eu", self.app.server_context(self.admin)["operating_region"])
        self.assertEqual(["administration", "tkl", "tmbox"], self.app.server_context(self.admin)["available_workspaces"])
        self.assertTrue(self.runtime.cloud_auto_sync_enabled())
        self.assertIsNone(self.app._eu_runtime_guard())
        self.assertTrue(self.app.config.connection_code)

    def test_tmbox_workspace_requires_eu_meet_and_admin(self):
        self.assertEqual(["administration"], self.app.server_context(self.admin)["available_workspaces"])
        self.connect()
        terminal = PairedClient("terminal", "TKL", DeviceKind.TKL_TERMINAL, ("panel-a",))
        self.assertEqual(["tkl"], self.app.server_context(terminal)["available_workspaces"])
        self.offered = us_package()
        self.connect(confirm_meet_change=True)
        self.assertEqual(["administration", "dispatcher"], self.app.server_context(self.admin)["available_workspaces"])

    def test_automatic_update_preserves_exact_clock_and_does_not_restart(self):
        self.connect()
        self.app.control_clock(self.admin, {"action": "start", "time": "07:08:09", "speed": 4})
        before = self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone()
        self.offered["publication_id"] = "second"
        self.offered["clock"].update(start_time="23:00", speed=20)
        result = self.app.auto_sync_cloud_runtime()
        after = self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone()
        self.assertTrue(result["activated"])
        self.assertEqual("second", self.app.engine.config.id)
        self.assertEqual(before[:1] + ("second",) + before[2:], after)
        self.assertIsNone(self.app.lifecycle.transition())

    def test_open_traffic_waits_then_adopts_on_next_check(self):
        self.connect()
        self.app.engine.connections["connection-a-b"].state = ConnectionState.OCCUPIED
        before = self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone()
        self.offered["publication_id"] = "second"
        result = self.app.auto_sync_cloud_runtime()
        self.assertTrue(result["pending"])
        self.assertEqual("first", self.runtime.active().publication_id)
        self.assertEqual(before, self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone())
        self.app.engine.connections["connection-a-b"].state = ConnectionState.FREE
        self.assertTrue(self.app.check_config_update(self.admin)["activated"])

    def test_background_cannot_select_another_meet(self):
        self.connect()
        self.offered["publication_id"] = "different"
        self.offered["meet"]["id"] = "other-meet"
        with self.assertRaisesRegex(CentralSyncError, "annan träff"):
            self.app.auto_sync_cloud_runtime()
        self.assertEqual("first", self.runtime.active().publication_id)
        with self.assertRaises(HTTPAPIError):
            self.connect()

    def test_explicit_switch_keeps_box_identity_admin_login_and_history(self):
        self.connect()
        self.identities.configure_admin_access("admin", "password")
        session = self.identities.create_admin_session("admin", "password")
        self.identities.register_client("box-id", "Benny", DeviceKind.ESP32_PANEL, "box-secret", ("panel-a",), station_id="station-a")
        self.offered = us_package()
        result = self.connect(confirm_meet_change=True)
        self.assertEqual("us", result["operating_region"])
        self.assertIsNone(self.runtime.active())
        self.assertIsNotNone(self.runtime.publication("first"))
        self.assertIsNotNone(self.identities.client("box-id"))
        self.assertEqual((), self.identities.client("box-id").panel_ids)
        self.assertIsNone(self.identities.client("box-id").station_id)
        self.assertTrue(self.identities.admin_access_summary()["password_configured"])
        self.assertIsNotNone(session)
        self.assertTrue(self.identities.authenticate_admin_session(session))
        with self.assertRaises(MeetLifecycleError):
            self.app.command(self.admin, {"key": "1", "panel_id": "panel-a", "expected_revision": 0})

    def test_us_initial_selection_and_overview_clock_share_same_session(self):
        self.offered = us_package()
        self.connect()
        self.assertIsNone(self.us.current_session())
        result = self.app.control_clock(self.admin, {"action": "start", "time": "06:30", "speed": 4})
        self.assertTrue(result["running"])
        self.assertEqual("us", result["scope"])
        session_id = self.us.current_session()["id"]
        self.app.control_clock(self.admin, {"action": "stop"})
        self.assertEqual(session_id, self.us.current_session()["id"])
        self.assertFalse(self.us.current_session()["clock"]["running"])
        with self.assertRaises(HTTPAPIError):
            self.app.us_command(self.admin, {"action": "create_session", "package": self.offered})

    def test_stale_manifest_bad_checksum_and_offline_preserve_active_version(self):
        self.connect()
        self.offered["publication_id"] = "second"
        self.app.linked_runtime_fetcher = lambda *args: CentralRuntimeManifest("second", "", "not-a-checksum") if args[-1] else CentralRuntimeDownload(self.offered)
        with self.assertRaisesRegex(CentralSyncError, "kontrollsumma"):
            self.app.auto_sync_cloud_runtime()
        self.assertEqual("first", self.runtime.active().publication_id)

    def test_transition_failure_blocks_writes_without_logging_out_admin(self):
        self.connect()
        self.app.lifecycle.begin_transition("eu", self.runtime.active().meet_id, "second")
        with self.assertRaises(MeetLifecycleError):
            self.app.control_clock(self.admin, {"action": "start"})
        self.assertTrue(self.app.server_context(self.admin)["transition_pending"])

    def test_formatted_code_accepts_spaces_and_dash_but_not_other_characters(self):
        self.assertTrue(self.connect(sync_code="123-456")["linked"])
        self.assertTrue(self.connect(sync_code="123 456")["linked"])
        for code in ("abc123456", "12345", "１２３４５６", "1234567"):
            with self.subTest(code=code), self.assertRaises(HTTPAPIError):
                self.connect(sync_code=code)

    def test_disconnected_cloud_never_changes_running_clock_or_selection(self):
        self.connect()
        self.app.control_clock(self.admin, {"action": "start", "time": "08:00"})
        before = self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone()
        selected = self.app.lifecycle.selected()
        with patch.object(self.app, "linked_runtime_fetcher", side_effect=CentralSyncError("offline")):
            with self.assertRaises(CentralSyncError):
                self.app.auto_sync_cloud_runtime()
        self.assertEqual(before, self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone())
        self.assertEqual(selected, self.app.lifecycle.selected())
        self.app.control_clock(self.admin, {"action": "stop"})
        self.assertFalse(self.operations.clock_status()["running"])

    def test_notifications_fall_back_when_cloud_is_old_or_at_capacity(self):
        self.connect()
        config = self.app.cloud_config
        self.assertFalse(config.wait_for_change())
        config.notifications_supported = True
        with patch("tmbox_gateway.cloud_config.wait_for_runtime_change", return_value=CentralRuntimeManifest("first", "", "", True)), \
             patch("tmbox_gateway.cloud_config.time.monotonic", side_effect=[100, 100.1]):
            self.assertFalse(config.wait_for_change())
        with patch("tmbox_gateway.cloud_config.wait_for_runtime_change", return_value=CentralRuntimeManifest("second", "", "", True)):
            self.assertTrue(config.wait_for_change())

    def test_closed_us_session_is_archived_not_a_permanent_update_blocker(self):
        self.offered = us_package()
        self.connect()
        self.app.control_clock(self.admin, {"action": "start"})
        session = self.us.current_session()
        self.us.execute("admin", True, "finish_session", {"command_id": "finish", "session_id": session["id"],
                        "expected_revision": session["revision"]}, "06:00")
        old_session_id = session["id"]
        self.offered["publication_id"] = "us-next"
        self.assertTrue(self.app.auto_sync_cloud_runtime()["activated"])
        self.assertIsNone(self.us.current_session())
        self.assertIsNotNone(self.us.db.execute("SELECT 1 FROM us_sessions WHERE id=?", (old_session_id,)).fetchone())
        generation = self.app.lifecycle.selected()["generation"]
        self.app.control_clock(self.admin, {"action": "start", "meet_generation": generation})
        self.assertNotEqual(old_session_id, self.us.current_session()["id"])
        self.assertEqual("us-next", self.us.current_session()["package"]["publication_id"])

    def test_new_same_region_meet_rejects_commands_from_old_browser(self):
        self.connect()
        old_generation = self.app.lifecycle.selected()["generation"]
        self.offered["publication_id"] = "other-eu"
        self.offered["meet"]["id"] = "other-meet"
        self.connect(confirm_meet_change=True)
        for payload in ({"action": "start"}, {"action": "start", "meet_generation": old_generation}):
            with self.subTest(payload=payload), self.assertRaises(HTTPAPIError) as rejected:
                self.app.control_clock(self.admin, payload)
            self.assertEqual(409, rejected.exception.status)
        self.assertFalse(self.operations.clock_status()["running"])
        current = self.app.lifecycle.selected()["generation"]
        self.assertTrue(self.app.control_clock(self.admin, {"action": "start", "meet_generation": current})["running"])


if __name__ == "__main__":
    unittest.main()
