from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tambox_gateway.mqtt_adapter import _decode_command


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


if __name__ == "__main__":
    unittest.main()
