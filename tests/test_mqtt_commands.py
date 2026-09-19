from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.identity import IdentityStore
from tmbox_gateway.mqtt_adapter import MQTTGatewayAdapter, _decode_command, _snapshot_token


def command_payload() -> dict:
    return {
        "protocol_version": 1,
        "command_id": "esp32-aabbcc-1234",
        "client_id": "esp32-aabbcc",
        "traffic_session_id": "test-session",
        "panel_id": "panel-a",
        "expected_revision": 7,
        "action": "key_press",
        "key": "A",
        "device_uptime_ms": 123456,
    }


class MQTTCommandTests(unittest.TestCase):
    def test_gateway_supplies_time_for_registered_physical_box(self):
        received_at = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

        decoded = _decode_command(
            command_payload(),
            received_at=received_at,
            use_gateway_clock=True,
        )

        self.assertEqual(decoded.sent_at, received_at)
        self.assertEqual(decoded.expires_at, received_at + timedelta(seconds=5))

    def test_other_mqtt_clients_still_need_explicit_expiry(self):
        with self.assertRaises(KeyError):
            _decode_command(command_payload())


class DeviceHelloTests(unittest.TestCase):
    def test_removed_box_loses_retained_assignment_and_cannot_control_panel(self):
        self._hello()
        device_id = "TMBOX-7A42F1"
        self.identities.assign_discovered_device(device_id, ("panel-a",), station_id="station-a")
        self.identities.remove_discovered_device(device_id)
        self.adapter.publish_device_assignment(device_id)
        self.assertEqual(self._last_assignment()["status"], "waiting_for_assignment")
        self.assertEqual(self._last_assignment()["assigned_panel_ids"], [])
        self._hello()
        self.assertEqual(self.identities.discovered_devices(), ())
        # Real physical commands have uptime, not sent_at/expires_at.
        payload = {**command_payload(), "client_id": device_id}
        message = MagicMock(topic=f"tambox/v1/client/{device_id}/command", payload=json.dumps(payload).encode(), mid=1, qos=1)
        self.adapter.engine.press = MagicMock()
        self.adapter._on_message(self.adapter.client, None, message)
        self.adapter.engine.press.assert_not_called()
        acks = [json.loads(call.args[1]) for call in self.adapter.client.publish.call_args_list if call.args[0].endswith("/ack")]
        self.assertEqual(acks[-1]["reason"], "panel_not_assigned")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.identities = IdentityStore(Path(self.directory.name) / "identity.db")
        self.adapter = MQTTGatewayAdapter(
            TrafficEngine(sample_session()),
            identities=self.identities,
        )
        self.adapter.client = MagicMock()

    def tearDown(self):
        self.identities.close()
        self.directory.cleanup()

    def _hello(self, **extra) -> None:
        payload = {
            "device_code": "TMBOX-7A42F1",
            "model": "TMBox ESP32-S3",
            "firmware_version": "0.3.0",
            **extra,
        }
        self.adapter._handle_device_hello("TMBOX-7A42F1", json.dumps(payload).encode())

    def _last_assignment(self) -> dict:
        for call in reversed(self.adapter.client.publish.call_args_list):
            topic, body = call.args[:2]
            if topic.endswith("/assignment"):
                return json.loads(body)
        self.fail("assignment was not published")

    def test_an_unassigned_box_is_told_to_wait(self):
        self._hello()

        assignment = self._last_assignment()
        self.assertEqual(assignment["status"], "waiting_for_assignment")
        self.assertIsNone(assignment["station_id"])
        self.assertTrue(self.adapter.client.publish.call_args.kwargs["retain"])

    def test_an_assigned_box_is_told_which_station_it_serves(self):
        self._hello()
        self.identities.assign_discovered_device(
            "TMBOX-7A42F1", station_id="station-a"
        )

        self._hello()

        assignment = self._last_assignment()
        self.assertEqual(assignment["status"], "assigned")
        self.assertEqual(assignment["station_id"], "station-a")

    def test_what_the_box_can_render_is_kept_from_its_hello(self):
        self._hello(
            hardware_version="esp32-s3",
            protocol_version=2,
            display={"rows": 4, "cols": 20, "charset": "cgram"},
        )

        device = self.identities.discovered_device("TMBOX-7A42F1")
        self.assertEqual(device.hardware_version, "esp32-s3")
        self.assertEqual(device.protocol_version, 2)
        self.assertEqual(device.display.to_dict(), {"rows": 4, "cols": 20, "charset": "cgram"})

    def _assign(self):
        self._hello(protocol_version=1)
        self.identities.assign_discovered_device("TMBOX-7A42F1", ("panel-a",), station_id="station-a")
        self.adapter.publish_device_assignment("TMBOX-7A42F1")
        self.adapter.client.reset_mock()

    def _presence(self, *, retained=False, **extra):
        payload = {
            "status": "online", "request_id": "boot-123-1", "panel_id": "panel-a",
            "state_token": _snapshot_token(self.adapter.engine.snapshot("panel-a")), **extra,
        }
        message = MagicMock(topic="tambox/v1/client/TMBOX-7A42F1/presence",
                            payload=json.dumps(payload).encode(), mid=1, qos=1, retain=retained)
        self.adapter._on_message(self.adapter.client, None, message)

    def _publications(self):
        return [(call.args[0], json.loads(call.args[1]), call.kwargs)
                for call in self.adapter.client.publish.call_args_list]

    def test_unchanged_presence_does_not_resend_assignment_or_snapshot(self):
        self._assign()
        before = self.identities.discovered_device("TMBOX-7A42F1").last_seen_at
        for index in range(6):
            self._presence(request_id=f"boot-123-{index}")
        publications = self._publications()
        self.assertEqual(6, len(publications))
        for index, (topic, body, options) in enumerate(publications):
            self.assertTrue(topic.endswith("/state"))
            self.assertEqual("current", body["status"])
            self.assertEqual(f"boot-123-{index}", body["request_id"])
            self.assertFalse(options["retain"])
        self.assertGreater(self.identities.discovered_device("TMBOX-7A42F1").last_seen_at, before)

    def test_explicit_snapshot_request_does_not_request_assignment(self):
        self._assign()
        self._presence(state_token="")
        publications = self._publications()
        self.assertEqual(1, len(publications))
        self.assertTrue(publications[0][0].endswith("/snapshot/panel-a"))
        self.assertEqual(_snapshot_token(self.adapter.engine.snapshot("panel-a")), publications[0][1]["state_token"])

    def test_clock_change_is_refreshed_even_without_revision_change(self):
        self._assign()
        token = _snapshot_token(self.adapter.engine.snapshot("panel-a"))
        revision = self.adapter.engine.revision
        self.adapter.engine.set_clock_source(lambda: {"configured": True, "time": "15:32", "running": True})
        self._presence(state_token=token)
        self.assertEqual(revision, self.adapter.engine.revision)
        publications = self._publications()
        self.assertEqual(1, len(publications))
        self.assertTrue(publications[0][0].endswith("/snapshot/panel-a"))
        self.assertEqual("15:32", publications[0][1]["clock"]["time"])
        self.assertNotEqual(token, publications[0][1]["state_token"])

    def test_admin_assignment_push_does_not_wait_for_box_poll(self):
        self._assign()
        self.identities.assign_discovered_device("TMBOX-7A42F1", ("panel-b",), station_id="station-b")
        self.adapter.publish_device_assignment("TMBOX-7A42F1")
        self.assertEqual(["panel-b"], self._last_assignment()["assigned_panel_ids"])
        self.assertTrue(self._publications()[-1][0].endswith("/snapshot/panel-b"))

    def test_status_request_recovers_missed_reassignment_but_never_renews_old_panel(self):
        self._assign()
        self.identities.assign_discovered_device("TMBOX-7A42F1", ("panel-b",), station_id="station-b")
        self._presence()
        self.assertEqual(["panel-b"], self._last_assignment()["assigned_panel_ids"])
        self.assertFalse(any(topic.endswith(("/state", "/snapshot/panel-a")) for topic, _, _ in self._publications()))

    def test_removed_box_cannot_use_matching_fingerprint_to_keep_access(self):
        self._assign()
        self.identities.remove_discovered_device("TMBOX-7A42F1")
        before = self.identities.discovered_device("TMBOX-7A42F1").last_seen_at
        self._presence()
        self.assertEqual("waiting_for_assignment", self._last_assignment()["status"])
        self.assertEqual([], self._last_assignment()["assigned_panel_ids"])
        self.assertEqual((), self.identities.discovered_devices())
        self.assertEqual(before, self.identities.discovered_device("TMBOX-7A42F1").last_seen_at)
        self.assertFalse(any(topic.endswith("/state") or "/snapshot/" in topic for topic, _, _ in self._publications()))

    def test_waiting_device_is_not_assigned_again_by_each_status_check(self):
        self._hello()
        self.adapter.client.reset_mock()
        self._presence(panel_id="", state_token="")
        publications = self._publications()
        self.assertEqual(1, len(publications))
        self.assertTrue(publications[0][0].endswith("/state"))
        self.assertEqual("waiting_for_assignment", publications[0][1]["status"])
        self.assertEqual((), self.identities.panels_for_client("TMBOX-7A42F1"))

    def test_waiting_device_recovers_lost_admin_assignment(self):
        self._assign()
        self._presence(panel_id="", state_token="")
        self.assertEqual(["panel-a"], self._last_assignment()["assigned_panel_ids"])

    def test_retained_or_offline_heartbeat_does_not_reply_or_touch_last_seen(self):
        self._assign()
        before = self.identities.discovered_device("TMBOX-7A42F1").last_seen_at
        self._presence(retained=True)
        self._presence(status="offline")
        self.adapter.client.publish.assert_not_called()
        self.assertEqual(before, self.identities.discovered_device("TMBOX-7A42F1").last_seen_at)

    def test_legacy_presence_still_gets_a_full_snapshot(self):
        self._assign()
        self.adapter._handle_presence("TMBOX-7A42F1", b'{"status":"online"}', retained=False)
        self.assertEqual(1, len(self._publications()))
        self.assertTrue(self._publications()[0][0].endswith("/snapshot/panel-a"))

    def test_unknown_heartbeat_does_not_register_or_grant_access(self):
        self._presence(panel_id="", state_token="")
        self.assertEqual((), self.identities.discovered_devices())
        self.assertEqual((), self.identities.panels_for_client("TMBOX-7A42F1"))

    def test_token_changes_with_session_and_input_context(self):
        snapshot = self.adapter.engine.snapshot("panel-a")
        token = _snapshot_token(snapshot)
        self.assertNotEqual(token, _snapshot_token({**snapshot, "traffic_session_id": "new-meet"}))
        self.assertNotEqual(token, _snapshot_token({**snapshot, "interaction": {**snapshot["interaction"], "owner_client_id": "new-owner"}}))
        self.assertEqual(token, _snapshot_token(dict(reversed(list(snapshot.items())))))


if __name__ == "__main__":
    unittest.main()
