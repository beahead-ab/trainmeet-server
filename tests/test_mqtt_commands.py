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
from tmbox_gateway.mqtt_adapter import MQTTGatewayAdapter, _decode_command


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


if __name__ == "__main__":
    unittest.main()
