from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import paho.mqtt.client as mqtt

from runtime_fixture import runtime_package_v2
from tambox_gateway.engine import TrafficEngine
from tambox_gateway.http_server import HTTPServerConfig, TamboxHTTPApplication, TamboxHTTPServer
from tambox_gateway.identity import IdentityStore, PairingService
from tambox_gateway.local_config import SQLiteLocalConfigurationStore
from tambox_gateway.models import unconfigured_session
from tambox_gateway.mqtt_adapter_v2 import MQTTGatewayAdapterV2
from tambox_gateway.operations import SQLiteOperationsStore
from tambox_gateway.runtime import SQLiteRuntimeStore


class MQTTIntegrationV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = shutil.which("mosquitto")
        if executable is None:
            raise unittest.SkipTest("mosquitto is not installed")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.broker = subprocess.Popen(
            [executable, "-p", str(cls.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            cls.broker.terminate()
            raise RuntimeError("test broker did not start")

    @classmethod
    def tearDownClass(cls):
        cls.broker.terminate()
        cls.broker.wait(timeout=5)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        database_path = Path(self.directory.name) / "runtime.db"
        self.identities = IdentityStore(Path(self.directory.name) / "identity.db")
        self.runtime_store = SQLiteRuntimeStore(database_path)
        self.operations_store = SQLiteOperationsStore(database_path)
        self.local_configuration_store = SQLiteLocalConfigurationStore(database_path)
        self.engine = TrafficEngine(unconfigured_session())
        pairing = PairingService(self.identities, set())
        self.application = TamboxHTTPApplication(
            self.engine,
            self.identities,
            pairing,
            HTTPServerConfig(local_development=True),
            runtime_store=self.runtime_store,
            local_configuration_store=self.local_configuration_store,
            operations_store=self.operations_store,
        )
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        admin = self.application.local_admin()
        for station_id, operator_name in (("station-a", "Anna"), ("station-b", "Bertil")):
            self.application.start_tkl_shift(
                admin, {"station_id": station_id, "operator_name": operator_name, "terminal_name": station_id}
            )

        self.gateway = MQTTGatewayAdapterV2(self.application, self.identities, host="127.0.0.1", port=self.port)
        self.gateway.client.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
        self.gateway.client.loop_start()
        self._devices: list[mqtt.Client] = []

    def tearDown(self):
        for device in self._devices:
            device.disconnect()
            device.loop_stop()
        self.gateway.client.disconnect()
        self.gateway.client.loop_stop()
        self.identities.close()
        self.runtime_store.close()
        self.operations_store.close()
        self.local_configuration_store.close()
        self.directory.cleanup()

    def _make_device(self, device_id: str) -> tuple[mqtt.Client, dict[str, dict], dict[str, threading.Event]]:
        received: dict[str, dict] = {}
        events = {"assignment": threading.Event(), "config": threading.Event(), "snapshot": threading.Event(), "ack": threading.Event()}

        device = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=device_id, protocol=mqtt.MQTTv5,
        )

        def on_connect(active_client, userdata, flags, reason_code, properties):
            del userdata, flags, reason_code, properties
            active_client.subscribe(f"tmbox/v2/device/{device_id}/assignment", qos=1)
            active_client.subscribe(f"tmbox/v2/device/{device_id}/config", qos=1)
            active_client.subscribe(f"tmbox/v2/device/{device_id}/snapshot", qos=1)
            active_client.subscribe(f"tmbox/v2/device/{device_id}/ack", qos=1)

        def on_message(active_client, userdata, message):
            del active_client, userdata
            payload = json.loads(message.payload.decode("utf-8"))
            if message.topic.endswith("/assignment"):
                received["assignment"] = payload
                events["assignment"].set()
            elif message.topic.endswith("/config"):
                received["config"] = payload
                events["config"].set()
            elif message.topic.endswith("/snapshot"):
                received["snapshot"] = payload
                events["snapshot"].set()
            elif message.topic.endswith("/ack"):
                received["ack"] = payload
                events["ack"].set()

        device.on_connect = on_connect
        device.on_message = on_message
        device.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
        device.loop_start()
        self._devices.append(device)
        return device, received, events

    def _hello(self, device: mqtt.Client, device_id: str, code: str) -> None:
        device.publish(
            f"tmbox/v2/device/{device_id}/hello",
            json.dumps({"device_code": code, "model": "TMBox", "firmware_version": "2.0.0"}),
            qos=1,
        )

    def test_discovery_assignment_and_retained_config_and_snapshot(self):
        device_id = "esp32-v2-a"
        device, received, events = self._make_device(device_id)
        self._hello(device, device_id, "TBX-V2A1")

        self.assertTrue(events["assignment"].wait(5), "hello was not acknowledged")
        self.assertEqual(received["assignment"]["status"], "waiting_for_assignment")
        self.assertEqual(self.identities.discovered_devices()[0].device_code, "TBX-V2A1")

        self.identities.assign_discovered_device("TBX-V2A1", (), station_id="station-a")
        events["assignment"].clear()
        self._hello(device, device_id, "TBX-V2A1")

        self.assertTrue(events["assignment"].wait(5), "assignment was not delivered")
        self.assertEqual(received["assignment"]["status"], "assigned")
        self.assertEqual(received["assignment"]["station_id"], "station-a")
        self.assertTrue(events["config"].wait(5), "config was not delivered")
        self.assertEqual(received["config"]["station"]["id"], "station-a")
        self.assertTrue(events["snapshot"].wait(5), "snapshot was not delivered")
        self.assertEqual(
            [movement["train_number"] for movement in received["snapshot"]["movements"]],
            ["101"],
        )

    def test_complete_command_positions_train_and_republishes_snapshot(self):
        device_id = "esp32-v2-b"
        device, received, events = self._make_device(device_id)
        self.identities.record_discovery(device_id, "TBX-V2B1")
        self.identities.assign_discovered_device("TBX-V2B1", (), station_id="station-a")
        self._hello(device, device_id, "TBX-V2B1")
        self.assertTrue(events["snapshot"].wait(5))
        events["snapshot"].clear()

        device.publish(
            f"tmbox/v2/device/{device_id}/command",
            json.dumps(
                {
                    "protocol_version": 2,
                    "message_id": "cmd-1",
                    "device_id": device_id,
                    "station_id": "station-a",
                    "action": "train.position.set",
                    "payload": {"movement_id": "movement-101-a", "actual_track_id": "2"},
                }
            ),
            qos=1,
        )

        self.assertTrue(events["ack"].wait(5), "ack was not received")
        self.assertEqual(received["ack"]["status"], "accepted")
        self.assertEqual(received["ack"]["result"]["departure"], "positioned")
        self.assertTrue(events["snapshot"].wait(5), "snapshot was not republished after command")
        moved = next(m for m in received["snapshot"]["movements"] if m["id"] == "movement-101-a")
        self.assertEqual(moved["departure"], "positioned")

    def test_unknown_action_and_wrong_station_are_rejected(self):
        device_id = "esp32-v2-c"
        device, received, events = self._make_device(device_id)
        self.identities.record_discovery(device_id, "TBX-V2C1")
        self.identities.assign_discovered_device("TBX-V2C1", (), station_id="station-a")
        self._hello(device, device_id, "TBX-V2C1")
        self.assertTrue(events["snapshot"].wait(5))

        device.publish(
            f"tmbox/v2/device/{device_id}/command",
            json.dumps(
                {
                    "protocol_version": 2, "message_id": "cmd-x", "device_id": device_id,
                    "station_id": "station-a", "action": "train.teleport", "payload": {},
                }
            ),
            qos=1,
        )
        self.assertTrue(events["ack"].wait(5))
        self.assertEqual(received["ack"]["status"], "rejected")
        self.assertEqual(received["ack"]["reason"], "unknown_action")

        events["ack"].clear()
        device.publish(
            f"tmbox/v2/device/{device_id}/command",
            json.dumps(
                {
                    "protocol_version": 2, "message_id": "cmd-y", "device_id": device_id,
                    "station_id": "station-b", "action": "train.departed",
                    "payload": {"movement_id": "movement-101-a"},
                }
            ),
            qos=1,
        )
        self.assertTrue(events["ack"].wait(5))
        self.assertEqual(received["ack"]["status"], "rejected")
        self.assertEqual(received["ack"]["reason"], "station_not_assigned")

    def test_clearance_exchange_between_two_stations_and_busy_channel_rejection(self):
        device_a_id, device_b_id = "esp32-v2-cda", "esp32-v2-vst"
        device_a, received_a, events_a = self._make_device(device_a_id)
        device_b, received_b, events_b = self._make_device(device_b_id)
        self.identities.record_discovery(device_a_id, "TBX-CDA1")
        self.identities.assign_discovered_device("TBX-CDA1", (), station_id="station-a")
        self.identities.record_discovery(device_b_id, "TBX-VST1")
        self.identities.assign_discovered_device("TBX-VST1", (), station_id="station-b")
        self._hello(device_a, device_a_id, "TBX-CDA1")
        self._hello(device_b, device_b_id, "TBX-VST1")
        self.assertTrue(events_a["snapshot"].wait(5))
        self.assertTrue(events_b["snapshot"].wait(5))
        events_a["snapshot"].clear()
        events_b["snapshot"].clear()

        device_a.publish(
            f"tmbox/v2/device/{device_a_id}/command",
            json.dumps(
                {
                    "protocol_version": 2, "message_id": "clr-1", "device_id": device_a_id,
                    "station_id": "station-a", "action": "clearance.request",
                    "payload": {"movement_id": "movement-101-a", "connection_id": "connection-a-b"},
                }
            ),
            qos=1,
        )
        self.assertTrue(events_a["ack"].wait(5))
        self.assertEqual(received_a["ack"]["status"], "accepted")
        clearance_id = received_a["ack"]["result"]["clearance_id"]
        self.assertTrue(events_b["snapshot"].wait(5), "receiving station did not see the pending clearance")
        self.assertEqual(len(received_b["snapshot"]["active_clearances"]), 1)

        # A second request on the same single-track channel must be rejected as busy.
        events_a["ack"].clear()
        device_a.publish(
            f"tmbox/v2/device/{device_a_id}/command",
            json.dumps(
                {
                    "protocol_version": 2, "message_id": "clr-2", "device_id": device_a_id,
                    "station_id": "station-a", "action": "clearance.request",
                    "payload": {"movement_id": "movement-202-a", "connection_id": "connection-a-b"},
                }
            ),
            qos=1,
        )
        self.assertTrue(events_a["ack"].wait(5))
        self.assertEqual(received_a["ack"]["status"], "rejected")
        self.assertEqual(received_a["ack"]["reason"], "connection_busy")

        device_b.publish(
            f"tmbox/v2/device/{device_b_id}/command",
            json.dumps(
                {
                    "protocol_version": 2, "message_id": "resp-1", "device_id": device_b_id,
                    "station_id": "station-b", "action": "clearance.response",
                    "payload": {"clearance_id": clearance_id, "accept": True},
                }
            ),
            qos=1,
        )
        # The rejection above (and possibly this acceptance) can each trigger
        # their own snapshot republish on station-a's device, so wait for the
        # actual end state rather than assuming the first event delivered is
        # the final one.
        deadline = time.monotonic() + 5
        approved_status = None
        while time.monotonic() < deadline:
            snapshot = received_a.get("snapshot")
            clearances = snapshot["active_clearances"] if snapshot else []
            approved_status = clearances[0]["status"] if clearances else None
            if approved_status == "approved":
                break
            time.sleep(0.05)
        self.assertEqual(approved_status, "approved", "requesting station did not see the approval")


if __name__ == "__main__":
    unittest.main()
