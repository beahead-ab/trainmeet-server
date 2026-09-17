from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.identity import IdentityStore
from tmbox_gateway.mqtt_adapter import MQTTGatewayAdapter, _decode_command


class CapturedMQTT:
    def __init__(self):
        self.messages = {}

    def publish(self, topic, payload, **_kwargs):
        self.messages[topic] = json.loads(payload)


class NodeMCUAssignmentTests(unittest.TestCase):
    DEVICE = "esp8266-aabbcc123456"
    CODE = "TBX-123456"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.identities = IdentityStore(Path(self.temporary.name) / "identity.db")
        self.engine = TrafficEngine(sample_session())
        # Exercise the actual adapter handler without a broker in these unit
        # tests. The separate MQTT integration suite covers the transport.
        self.adapter = object.__new__(MQTTGatewayAdapter)
        self.adapter.engine = self.engine
        self.adapter.identities = self.identities
        self.adapter.client = CapturedMQTT()

    def tearDown(self):
        self.identities.close()
        self.temporary.cleanup()

    def hello(self, protocol=1):
        self.adapter._handle_device_hello(self.DEVICE, json.dumps({
            "protocol_version": protocol, "device_code": self.CODE,
            "model": "NodeMCU ESP8266 PCF8574", "firmware_version": "test",
        }).encode())
        return self.adapter.client.messages[f"tambox/v1/device/{self.DEVICE}/assignment"]

    def assign_station(self):
        self.hello()
        self.identities.assign_discovered_device(self.CODE, station_id="station-a")

    def test_unassigned_box_is_not_granted_a_panel(self):
        self.assertEqual(self.hello()["status"], "waiting_for_assignment")
        self.assertEqual(self.identities.panels_for_client(self.DEVICE), ())

    def test_station_assignment_delivers_snapshot_and_authorizes_v1_input(self):
        self.assign_station()
        assignment = self.hello()
        self.assertEqual(assignment["assigned_panel_ids"], ["panel-a"])
        snapshot = self.adapter.client.messages[f"tambox/v1/client/{self.DEVICE}/snapshot/panel-a"]
        self.assertIn("A", snapshot["interaction"]["allowed_keys"])
        command = _decode_command({
            "protocol_version": 1, "command_id": "nodemcu-boot-1",
            "client_id": self.DEVICE, "panel_id": "panel-a",
            "traffic_session_id": snapshot["traffic_session_id"],
            "expected_revision": snapshot["revision"],
            "action": "key_press", "key": "A", "device_uptime_ms": 1000,
        }, use_gateway_clock=True)
        self.assertIn(command.panel_id, self.identities.panels_for_client(self.DEVICE))
        self.assertEqual(self.engine.press(command).status, "accepted")
        # Periodic hello must preserve, not repeatedly recreate, the mapping.
        self.assertEqual(self.hello()["assigned_panel_ids"], ["panel-a"])

    def test_ambiguous_station_does_not_pick_first_panel(self):
        self.assign_station()
        self.engine.config.panels["second-panel"] = replace(
            self.engine.config.panels["panel-a"], id="second-panel",
        )
        self.assertEqual(self.hello()["assigned_panel_ids"], [])

    def test_station_without_panel_does_not_get_another_stations_panel(self):
        self.assign_station()
        self.engine.config.panels.pop("panel-a")
        self.assertEqual(self.hello()["assigned_panel_ids"], [])

    def test_explicit_panel_assignment_is_preserved(self):
        self.hello()
        self.identities.assign_discovered_device(self.CODE, ("panel-b",), station_id="station-b")
        self.assertEqual(self.hello()["assigned_panel_ids"], ["panel-b"])

    def test_v2_does_not_receive_a_legacy_panel(self):
        self.assign_station()
        self.assertEqual(self.hello(protocol=2)["assigned_panel_ids"], [])

    def test_disabled_box_is_not_reactivated_by_hello(self):
        self.assign_station()
        self.identities.disable_client(self.DEVICE)
        self.assertEqual(self.hello()["status"], "waiting_for_assignment")
        self.assertIsNone(self.identities.client(self.DEVICE))

    def test_binding_cannot_undo_a_concurrent_station_change(self):
        self.assign_station()
        self.identities.assign_discovered_device(self.CODE, station_id="station-b")
        self.identities.bind_legacy_station_panel(self.DEVICE, "station-a", "panel-a")
        self.assertEqual(self.identities.panels_for_client(self.DEVICE), ())
        self.assertEqual(self.identities.station_for_client(self.DEVICE), "station-b")

    def test_binding_cannot_undo_a_concurrent_disable_or_explicit_panel(self):
        self.assign_station()
        self.identities.disable_client(self.DEVICE)
        self.identities.bind_legacy_station_panel(self.DEVICE, "station-a", "panel-a")
        self.assertIsNone(self.identities.client(self.DEVICE))
        self.identities.assign_discovered_device(self.CODE, ("chosen-panel",), station_id="station-a")
        self.identities.bind_legacy_station_panel(self.DEVICE, "station-a", "panel-a")
        self.assertEqual(self.identities.panels_for_client(self.DEVICE), ("chosen-panel",))


if __name__ == "__main__":
    unittest.main()
