import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs, urlsplit

import test_cloud_only_http as delivery
from test_us_cloud import cloud_package as us_package
from tmbox_gateway.central_sync import fetch_linked_runtime, wait_for_runtime_change, CentralRuntimeManifest
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import PairedClient, DeviceKind
from tmbox_gateway.models import ConnectionState
from tmbox_gateway.display_placement import apply_placement, effective_sides
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.storage import session_config_fingerprint
from tmbox_gateway.terminal16_runtime import RuntimeViews


class ServerCloudContractTests(unittest.TestCase):
    setUp = delivery.CloudOnlyDeliveryTests.setUp
    fetch = delivery.CloudOnlyDeliveryTests.fetch
    connect = delivery.CloudOnlyDeliveryTests.connect

    def save(self, sides, **extra):
        return self.app.save_display_placement(self.admin, {
            "publication_id": self.runtime.active().publication_id,
            "config_version": self.runtime.config_version(), "station_id": "station-a", "sides": sides, **extra})

    def test_pending_download_is_not_reported_as_running(self):
        self.connect()
        self.runtime.save_server_name("Pi – Grimslöv")
        self.app.engine.connections["connection-a-b"].state = ConnectionState.OCCUPIED
        self.offered["publication_id"] = "second"
        self.assertTrue(self.app.auto_sync_cloud_runtime()["pending"])
        self.assertEqual({"name": "Pi – Grimslöv", "running_version": "first"}, self.app.cloud_config.heartbeat())
        self.app.cloud_config.notifications_supported = True
        with patch("tmbox_gateway.cloud_config.wait_for_runtime_change", return_value=CentralRuntimeManifest("second", "", "", True)) as wait:
            self.app.cloud_config.wait_for_change()
            self.assertEqual("second", wait.call_args.args[2])
            self.assertEqual("first", wait.call_args.kwargs["running_version"])
        self.app.engine.connections["connection-a-b"].state = ConnectionState.FREE
        self.app.auto_sync_cloud_runtime()
        self.assertEqual("second", self.app.cloud_config.heartbeat()["running_version"])

    def test_absent_or_interrupted_configuration_reports_no_running_version(self):
        self.assertIsNone(self.app.cloud_config.heartbeat()["running_version"])
        self.connect()
        self.app.lifecycle.begin_transition("eu", "meet-a", "second")
        self.assertIsNone(self.app.cloud_config.heartbeat()["running_version"])

    def test_us_reports_selected_package_without_starting_a_session(self):
        self.offered = us_package()
        self.connect()
        self.assertIsNone(self.us.current_session())
        self.assertEqual(self.offered["publication_id"], self.app.cloud_config.heartbeat()["running_version"])

    def test_placement_preserves_traffic_and_original_package(self):
        self.connect()
        original = copy.deepcopy(self.runtime.active().payload)
        checksum = self.runtime.active().checksum
        engine_fingerprint = self.app.engine.config_fingerprint
        before = self.app.engine.export_state()
        before_config = self.runtime.config_version()
        self.app.engine.connections["connection-a-b"].state = ConnectionState.OCCUPIED
        callback = self.app.on_config_applied = MagicMock()
        views = RuntimeViews(self.app.station_service)
        self.save({"connection-a-b": "right"})
        self.assertEqual(ConnectionState.OCCUPIED, self.app.engine.connections["connection-a-b"].state)
        self.assertEqual(before["panels"], self.app.engine.export_state()["panels"])
        self.assertEqual(original, self.runtime.active().payload)
        self.assertEqual(checksum, self.runtime.active().checksum)
        self.assertEqual(engine_fingerprint, session_config_fingerprint(self.app.engine.config))
        self.assertEqual(before_config + 1, self.runtime.config_version())
        self.assertFalse(self.app.restart_required())
        self.assertEqual("right", self.app.engine.snapshot("panel-a")["slots"]["A"]["side"])
        self.assertEqual("right", self.app.station_service.config_payload("station-a")["connections"][0]["display_side"])
        views.refresh()
        self.assertEqual("right", views._side("station-a", "station-b", "connection-a-b"))
        self.save({})
        views.refresh()
        self.assertEqual("left", views._side("station-a", "station-b", "connection-a-b"))
        self.assertEqual(2, callback.call_count)

    def test_override_survives_cloud_update_and_database_reopen(self):
        self.connect()
        self.save({"connection-a-b": "right"})
        self.offered["publication_id"] = "second"
        self.app.auto_sync_cloud_runtime()
        self.assertEqual("right", self.app.engine.config.panels["panel-a"].slot_position("A")[1])
        reopened = SQLiteRuntimeStore(self.runtime.path)
        self.addCleanup(reopened.close)
        self.assertEqual("right", reopened.session_config(reopened.active()).panels["panel-a"].slot_position("A")[1])
        self.assertEqual("left", reopened.active().session_config().panels["panel-a"].slot_position("A")[1])

    def test_meet_scoping_and_default_reset_follow_latest_cloud(self):
        self.connect()
        self.save({"connection-a-b": "left"})
        self.offered["publication_id"] = "second"
        self.offered["panels"][0]["slots"] = {"A": None, "B": "connection-a-b", "C": None, "D": None}
        self.app.auto_sync_cloud_runtime()
        self.assertEqual("left", self.app.engine.config.panels["panel-a"].slot_position("B")[1])
        self.save({})
        self.assertEqual("right", self.app.engine.config.panels["panel-a"].slot_position("B")[1])
        self.offered["publication_id"] = "third"
        self.offered["meet"]["id"] = "another-meet"
        self.connect(confirm_meet_change=True)
        self.assertEqual({}, self.runtime.display_placement_overrides("another-meet"))

    def test_only_admin_and_current_config_can_change_display(self):
        self.connect()
        client = PairedClient("box", "Box", DeviceKind.ESP32_PANEL, ("panel-a",))
        with self.assertRaises(HTTPAPIError):
            self.app.cloud_presentation(client)
        with self.assertRaises(HTTPAPIError):
            self.app.save_display_placement(client, {})
        before = self.runtime.config_version()
        for extra in ({"publication_id": "old"}, {"config_version": 0}, {"config_version": True}, {"station_id": "unknown"}):
            with self.assertRaises(HTTPAPIError):
                self.save({"connection-a-b": "right"}, **extra)
        for sides in (None, [], {"connection-a-b": "up"}, {"foreign": "right"}, {"connection-a-b": []}):
            with self.assertRaises(HTTPAPIError):
                self.save(sides)
        self.assertEqual(before, self.runtime.config_version())

    def test_findings_are_optional_immutable_advisory_metadata(self):
        self.connect()
        self.assertIsNone(self.app.cloud_presentation(self.admin)["findings"])
        finding = {"key": "A:example", "rule": "A", "level": "conflict", "message": "Exempel", "source_rows": ["movement-101-a"]}
        self.offered.update(publication_id="with-findings", findings=[finding])
        self.app.auto_sync_cloud_runtime()
        self.assertEqual([finding], self.app.cloud_presentation(self.admin)["findings"])
        self.assertIsNone(self.app._eu_runtime_guard())

    def test_findings_follow_active_not_pending_publication(self):
        self.connect()
        self.app.engine.connections["connection-a-b"].state = ConnectionState.OCCUPIED
        self.offered.update(publication_id="pending", findings=[{"message": "Pending only"}])
        self.app.auto_sync_cloud_runtime()
        self.assertIsNone(self.app.cloud_presentation(self.admin)["findings"])
        self.assertEqual("first", self.app.cloud_presentation(self.admin)["publication_id"])

    def test_malformed_optional_findings_do_not_break_old_contract(self):
        self.connect()
        for index, findings in enumerate((False, "unknown", {"conflicts": None, "observations": [None, {"message": "OK"}]})):
            self.offered.update(publication_id=f"metadata-{index}", findings=findings)
            self.app.auto_sync_cloud_runtime()
            result = self.app.cloud_presentation(self.admin)
            self.assertTrue(result["supported"])
            self.assertEqual([{"message": "OK"}] if index == 2 else None, result["findings"])

    def test_failed_transaction_preserves_override_and_generation(self):
        self.connect()
        before = self.runtime.config_version()
        with patch.object(self.runtime, "bump_config_version", side_effect=RuntimeError("disk error")):
            with self.assertRaises(RuntimeError):
                self.save({"connection-a-b": "right"})
        self.assertEqual({}, self.runtime.display_placement_overrides(self.runtime.active().meet_id))
        self.assertEqual(before, self.runtime.config_version())
        self.assertEqual("left", self.app.engine.config.panels["panel-a"].slot_position("A")[1])

    def test_reused_connection_id_cannot_move_a_different_neighbor(self):
        self.connect()
        config = self.runtime.active().session_config()
        overrides = {"station-a": {"connection-a-b": {"side": "right", "other_station_id": "old-neighbor"}}}
        self.assertEqual("left", effective_sides(config, overrides, "station-a")["connection-a-b"])
        self.assertEqual(config, apply_placement(config, overrides))

    def test_legacy_overflow_retains_all_slots_and_modern_view_keeps_override(self):
        self.connect()
        config = self.runtime.active().session_config()
        connection = config.connections["connection-a-b"]
        connections = {key: replace(connection, id=key) for key in ("one", "two", "three")}
        panel = replace(config.panels["panel-a"], slots={"A": "one", "B": "two", "C": "three", "D": None})
        config = replace(config, connections=connections, panels={panel.id: panel})
        overrides = {"station-a": {key: {"side": "right", "other_station_id": "station-b"} for key in connections}}
        result = apply_placement(config, overrides)
        self.assertEqual(config, result)
        self.assertTrue(all(row <= 2 for row, side in (result.panels[panel.id].slot_position(key) for key in panel.slots)))
        self.assertEqual({key: "right" for key in connections}, effective_sides(result, overrides, "station-a"))


class HeartbeatTransportTests(unittest.TestCase):
    def test_poll_and_wait_encode_running_publication_without_extra_requests(self):
        response = {"publication_id": "new", "published_at": "", "package_checksum": "", "package": {"publication_id": "new"}}
        for manifest in (True, False):
            with patch("tmbox_gateway.central_sync._read_json", return_value=response) as read:
                fetch_linked_runtime("token", "https://example.test/config", manifest_only=manifest, name="Pi & Träff", running_version="old")
                query = parse_qs(urlsplit(read.call_args.args[0].full_url).query)
                self.assertEqual(["old"], query["running_version"])
                self.assertEqual(["Pi & Träff"], query["name"])
                self.assertEqual(1, read.call_count)
        with patch("tmbox_gateway.central_sync._read_json", return_value=response) as read:
            wait_for_runtime_change("token", "https://example.test/config", "new", name="Renamed", running_version="old")
            query = parse_qs(urlsplit(read.call_args.args[0].full_url).query)
            self.assertEqual(["new"], query["after"])
            self.assertEqual(["old"], query["running_version"])

    def test_unknown_running_version_is_explicitly_empty(self):
        with patch("tmbox_gateway.central_sync._read_json", return_value={"package": {}}) as read:
            fetch_linked_runtime("token", "https://example.test/config")
            query = parse_qs(urlsplit(read.call_args.args[0].full_url).query, keep_blank_values=True)
            self.assertEqual([""], query["running_version"])
