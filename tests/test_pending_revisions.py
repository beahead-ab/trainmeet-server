"""Safe Cloud versions activate automatically; downloads never reset traffic."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

from runtime_fixture import runtime_package_v3
from tmbox_gateway.central_sync import CentralRuntimeDownload, CentralRuntimeManifest, CentralSyncError
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetRequestHandler
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.local_config import SQLiteLocalConfigurationStore
from tmbox_gateway.models import ConnectionState
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore

LINK = "central-test-link"


def cloud_package(publication_id="cloud-second"):
    return runtime_package_v3(publication_id=publication_id)


def dispatch_request(application, client, path, payload=None, method="POST"):
    """Exercise the actual HTTP router with captured transport, without sockets."""
    handler = TrainMeetRequestHandler.__new__(TrainMeetRequestHandler)
    handler.path = path
    handler.server = SimpleNamespace(application=application)
    handler._read_json = lambda: payload or {}
    handler._authenticated_client = lambda: client
    responses = []
    handler._send_json = lambda status, body, **kwargs: responses.append((status, body))
    handler._send_api_error = lambda error: responses.append((error.status, {"code": error.code, "message": str(error)}))
    getattr(handler, "do_" + method)()
    assert len(responses) == 1
    return responses[0]


class CloudDeliveryFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "runtime.db"
        self.identities = IdentityStore(database)
        self.runtime = SQLiteRuntimeStore(database)
        self.local = SQLiteLocalConfigurationStore(database)
        self.operations = SQLiteOperationsStore(database)
        for store in (self.identities, self.runtime, self.local, self.operations):
            self.addCleanup(store.close)
        first = self.runtime.install(cloud_package("cloud-first"))
        engine = TrafficEngine(first.session_config())
        self.offered = cloud_package()
        self.fetches = []
        self.application = TrainMeetHTTPApplication(
            engine, self.identities, PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True), runtime_store=self.runtime,
            operations_store=self.operations, local_configuration_store=self.local,
            linked_runtime_fetcher=self._cloud,
        )
        self.addCleanup(self.application.lifecycle.close)
        self.client = self.application.local_admin()
        self.runtime.save_link_token(LINK)
        self.application.configure_cloud_auto_sync(self.client, {"enabled": True})

    def _cloud(self, token, _url, manifest_only):
        self.assertEqual(LINK, token)
        self.fetches.append(manifest_only)
        if manifest_only:
            encoded = json.dumps(self.offered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            return CentralRuntimeManifest(self.offered["publication_id"], self.offered["published_at"], hashlib.sha256(encoded).hexdigest())
        return CentralRuntimeDownload(copy.deepcopy(self.offered), token)

    def busy(self):
        self.application.engine.connections["connection-a-b"].state = ConnectionState.OCCUPIED

    def free(self):
        self.application.engine.connections["connection-a-b"].state = ConnectionState.FREE

    def clock_record(self):
        return self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone()


class PendingRevisionTests(CloudDeliveryFixture):
    def test_safe_poll_updates_selection_and_engine_without_resetting_live_clock(self):
        self.application.control_clock(self.client, {"action": "start", "time": "13:14:15", "speed": 6})
        before = self.clock_record()
        self.offered["clock"].update(start_time="01:00", speed=20)
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["activated"])
        self.assertFalse(result["restart_required"])
        self.assertEqual("cloud-second", self.runtime.active().publication_id)
        self.assertEqual("cloud-second", self.application.engine.config.id)
        self.assertEqual("cloud-second", self.application.lifecycle.selected()["publication_id"])
        self.assertEqual(before[:1] + ("cloud-second",) + before[2:], self.clock_record())

    def test_repeated_polling_of_current_publication_is_a_no_op(self):
        self.application.auto_sync_cloud_runtime()
        selected, before = self.application.lifecycle.selected(), self.clock_record()
        self.fetches.clear()
        for _ in range(10):
            self.assertFalse(self.application.auto_sync_cloud_runtime()["update_available"])
        self.assertEqual([True] * 10, self.fetches)
        self.assertEqual(selected, self.application.lifecycle.selected())
        self.assertEqual(before, self.clock_record())

    def test_cloud_default_day_does_not_change_the_selected_operating_day(self):
        self.runtime.set_active_day("Sön")
        self.offered["meet"]["active_day"] = "Mån"
        self.assertTrue(self.application.auto_sync_cloud_runtime()["activated"])
        self.assertEqual("Sön", self.runtime.active_day())
        self.assertEqual("Sön", self.application.display_snapshot()["active_day"])

    def test_withdrawn_pending_publication_clears_wait_cursor_without_deleting_archive(self):
        self.busy()
        self.assertTrue(self.application.auto_sync_cloud_runtime()["pending"])
        self.offered = cloud_package("cloud-first")
        self.application.auto_sync_cloud_runtime()
        config = self.application.cloud_config
        self.assertIsNone(config.status()["pending_publication_id"])
        self.assertIsNone(self.runtime.pending_publication())
        self.assertIsNotNone(self.runtime.publication("cloud-second"))
        config.notifications_supported = True
        with patch("tmbox_gateway.cloud_config.wait_for_runtime_change", return_value=CentralRuntimeManifest("cloud-first", "", "", True)) as wait:
            self.assertFalse(config.wait_for_change())
            self.assertEqual("cloud-first", wait.call_args.args[2])

    def test_staging_waits_without_changing_clock_or_active_state(self):
        self.busy()
        self.application.control_clock(self.client, {"action": "start", "time": "11:12:13"})
        before = self.clock_record()
        selected, engine = self.application.lifecycle.selected(), self.application.engine.export_state()
        self.offered["clock"].update(start_time="23:00", speed=99)
        for _ in range(3):
            self.assertTrue(self.application.auto_sync_cloud_runtime()["pending"])
        self.assertEqual("cloud-first", self.runtime.active().publication_id)
        self.assertEqual(selected, self.application.lifecycle.selected())
        self.assertEqual(engine, self.application.engine.export_state())
        self.assertEqual(before, self.clock_record())

    def test_stopping_clock_does_not_make_occupied_line_safe(self):
        self.busy()
        self.application.control_clock(self.client, {"action": "stop"})
        self.assertFalse(self.operations.clock_status()["running"])
        self.assertTrue(self.application.auto_sync_cloud_runtime()["pending"])
        self.assertEqual("cloud-first", self.runtime.active().publication_id)
        self.free()
        self.assertTrue(self.application.auto_sync_cloud_runtime()["activated"])
        self.assertFalse(self.operations.clock_status()["running"])

    def test_open_clearance_blocks_even_when_engine_and_clock_are_idle(self):
        first = self.runtime.active()
        self.operations.request_clearance(first.publication_id, first.active_day,
            clearance_id="pending-clearance", movement_id=first.payload["trains"][0]["id"],
            connection_id="connection-a-b", channel_id="a-b", from_station_id="station-a",
            to_station_id="station-b", track_id=None, requested_by="tkl", ttl_seconds=60)
        self.assertTrue(self.application.auto_sync_cloud_runtime()["pending"])
        self.assertIn("körtillstånd", self.application.cloud_config.status()["message"])
        self.operations.settle_clearance("pending-clearance", "cancelled", "tkl")
        self.assertTrue(self.application.auto_sync_cloud_runtime()["activated"])

    def test_pending_version_and_reason_are_visible(self):
        self.busy()
        self.application.auto_sync_cloud_runtime()
        update = self.application.server_context(self.client)["cloud_update"]
        self.assertEqual("waiting", update["state"])
        self.assertEqual("cloud-first", update["current_publication_id"])
        self.assertEqual("cloud-second", update["pending_publication_id"])
        self.assertTrue(update["message"])

    def test_manual_check_and_old_activate_routes_cannot_bypass_traffic_guard(self):
        self.busy()
        before = self.clock_record()
        for route in ("/v1/config/check", "/v1/runtime/update", "/v1/runtime/activate", "/v1/runtime/pending/activate"):
            with self.subTest(route=route):
                status, result = dispatch_request(self.application, self.client, route, {"publication_id": "stale-tab-id"})
                self.assertEqual(HTTPStatus.OK, status)
                self.assertTrue(result["pending"])
                self.assertEqual("cloud-first", self.runtime.active().publication_id)
                self.assertEqual(before, self.clock_record())
        self.free()
        status, result = dispatch_request(self.application, self.client, "/v1/config/check")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(result["activated"])

    def test_adoption_clears_waiting_marker_without_manual_approval(self):
        self.busy()
        self.application.auto_sync_cloud_runtime()
        self.assertIsNotNone(self.runtime.pending_publication())
        self.free()
        self.application.auto_sync_cloud_runtime()
        self.assertIsNone(self.runtime.pending_publication())
        self.assertIsNone(self.application.cloud_config.status()["pending_publication_id"])

    def test_disabling_automatic_checks_keeps_manual_check_available(self):
        self.application.configure_cloud_auto_sync(self.client, {"enabled": False})
        self.assertFalse(self.application.auto_sync_cloud_runtime()["checked"])
        self.assertEqual([], self.fetches)
        self.assertTrue(self.application.check_config_update(self.client)["activated"])

    def test_network_failure_preserves_meet_clock_and_editor_lock(self):
        before = self.clock_record()
        def offline(*args):
            raise CentralSyncError("Cloud kan inte nås")
        self.application.linked_runtime_fetcher = offline
        with self.assertRaises(CentralSyncError):
            self.application.auto_sync_cloud_runtime()
        self.assertEqual("cloud-first", self.runtime.active().publication_id)
        self.assertEqual(before, self.clock_record())
        self.assertFalse(self.application.server_context(self.client)["local_editing"])

    def test_archived_local_revision_and_draft_are_not_deleted(self):
        self.local.seed_from_publication(self.runtime.active().payload)
        draft = self.local.current()["draft"]
        draft["trains"][0]["departure_time"] = "09:47"
        self.local.save(draft)
        archived = self.runtime.install(self.local.runtime_package(), activate=False)
        before = self.local.current()
        self.application.auto_sync_cloud_runtime()
        self.assertEqual(before, self.local.current())
        self.assertIsNotNone(self.runtime.publication(archived.publication_id))
        self.assertIn(archived.publication_id, self.runtime.local_revisions())
        self.assertEqual("cloud-second", self.runtime.active().publication_id)

    def test_diff_names_renamed_station_while_version_waits(self):
        self.busy()
        self.offered["stations"][1]["name"] = "Lekeberg norra"
        self.application.auto_sync_cloud_runtime()
        changes = self.application.pending_revision_state(self.client)["changes"]
        self.assertFalse(changes["first_activation"])
        self.assertEqual(1, changes["stations"]["renamed"]["count"])
        self.assertIn("Lekeberg → Lekeberg norra", changes["stations"]["renamed"]["names"])

    def test_diff_names_retimed_trains_while_version_waits(self):
        self.busy()
        self.offered["trains"][0]["departure_time"] = "09:44"
        self.application.auto_sync_cloud_runtime()
        changes = self.application.pending_revision_state(self.client)["changes"]["timetable"]
        self.assertEqual(1, changes["changed"]["count"])
        self.assertIn("101", changes["changed"]["names"])

    def test_diff_counts_added_and_removed_movements(self):
        self.busy()
        removed = self.offered["trains"].pop()
        self.offered["trains"].append({**removed, "id": "new-row", "train_number": "909"})
        self.application.auto_sync_cloud_runtime()
        changes = self.application.pending_revision_state(self.client)["changes"]["timetable"]
        self.assertEqual(1, changes["added"])
        self.assertEqual(1, changes["removed"])

    def test_reading_pending_details_requires_admin(self):
        panel = PairedClient("box", "CDA TMBox", DeviceKind.ESP32_PANEL, ("panel-a",))
        with self.assertRaises(HTTPAPIError) as raised:
            self.application.pending_revision_state(panel)
        self.assertEqual(HTTPStatus.FORBIDDEN, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
