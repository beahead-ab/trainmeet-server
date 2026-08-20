from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.identity import DeviceKind, IdentityStore
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.mqtt_adapter import MQTTGatewayAdapter


class MQTTIntegrationTests(unittest.TestCase):
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

    def test_qos1_command_returns_ack_and_retained_snapshot(self):
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        temporary_directory = tempfile.TemporaryDirectory()
        identities = IdentityStore(Path(temporary_directory.name) / "identity.db")
        client_id = "integration-swift"
        identities.register_client(
            client_id,
            "Integration Swift",
            DeviceKind.SWIFT_PANEL,
            "unused-local-token",
            ("panel-a",),
        )
        gateway = MQTTGatewayAdapter(
            engine,
            host="127.0.0.1",
            port=self.port,
            identities=identities,
        )
        gateway.client.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
        gateway.client.loop_start()

        received: dict[str, dict] = {}
        ack_event = threading.Event()
        snapshot_event = threading.Event()
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv5,
        )

        def on_connect(active_client, userdata, flags, reason_code, properties):
            del userdata, flags, reason_code, properties
            active_client.subscribe(f"tambox/v1/client/{client_id}/ack", qos=1)
            active_client.subscribe(f"tambox/v1/client/{client_id}/snapshot/+", qos=1)
            active_client.publish(
                f"tambox/v1/client/{client_id}/presence",
                json.dumps({"status": "online"}),
                qos=1,
                retain=True,
            )

        def on_message(active_client, userdata, message):
            del active_client, userdata
            payload = json.loads(message.payload.decode("utf-8"))
            if message.topic.endswith("/ack"):
                received["ack"] = payload
                ack_event.set()
            elif "/snapshot/" in message.topic:
                received["snapshot"] = payload
                snapshot_event.set()

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
        client.loop_start()
        reconnected_client = None

        try:
            self.assertTrue(snapshot_event.wait(5), "initial retained snapshot was not received")
            now = datetime.now(timezone.utc)
            command = {
                "protocol_version": 1,
                "command_id": "integration-command-1",
                "client_id": client_id,
                "traffic_session_id": "test-session",
                "panel_id": "panel-a",
                "expected_revision": received["snapshot"]["revision"],
                "action": "key_press",
                "key": "A",
                "sent_at": now.isoformat().replace("+00:00", "Z"),
                "expires_at": (now + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
            }
            info = client.publish(
                f"tambox/v1/client/{client_id}/command",
                json.dumps(command),
                qos=1,
                retain=False,
            )
            self.assertEqual(info.rc, mqtt.MQTT_ERR_SUCCESS)
            self.assertTrue(ack_event.wait(5), "command acknowledgement was not received")
            self.assertEqual(received["ack"]["status"], "accepted")
            self.assertEqual(received["ack"]["revision"], 1)
            self.assertEqual(
                received["ack"]["snapshots"]["panel-a"]["interaction"]["mode"],
                "enter_train",
            )
            self.assertEqual(
                received["ack"]["snapshots"]["panel-a"]["display"],
                {"line1": "Till: LEK   #=OK", "line2": "Tåg: _     *=Avb"},
            )

            ack_event.clear()
            client.publish(
                f"tambox/v1/client/{client_id}/command",
                json.dumps(command),
                qos=1,
                retain=False,
            )
            self.assertTrue(ack_event.wait(5), "duplicate acknowledgement was not received")
            self.assertEqual(received["ack"]["status"], "duplicate")
            self.assertEqual(received["ack"]["revision"], 1)

            client.disconnect()
            client.loop_stop()
            snapshot_event.clear()
            reconnected_client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                protocol=mqtt.MQTTv5,
            )
            reconnected_client.on_connect = on_connect
            reconnected_client.on_message = on_message
            reconnected_client.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
            reconnected_client.loop_start()
            self.assertTrue(snapshot_event.wait(5), "snapshot was not restored after reconnect")
            self.assertEqual(received["snapshot"]["revision"], 1)
            self.assertEqual(received["snapshot"]["interaction"]["mode"], "enter_train")
        finally:
            client.disconnect()
            client.loop_stop()
            if reconnected_client is not None:
                reconnected_client.disconnect()
                reconnected_client.loop_stop()
            gateway.client.disconnect()
            gateway.client.loop_stop()
            identities.close()
            temporary_directory.cleanup()

    def test_physical_box_needs_only_its_printed_code_and_device_id(self):
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        temporary_directory = tempfile.TemporaryDirectory()
        identities = IdentityStore(Path(temporary_directory.name) / "identity.db")
        gateway = MQTTGatewayAdapter(
            engine,
            host="127.0.0.1",
            port=self.port,
            identities=identities,
        )
        gateway.client.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
        gateway.client.loop_start()

        device_id = "esp32-integration-box"
        assignment_event = threading.Event()
        snapshot_event = threading.Event()
        received: dict[str, dict] = {}
        device = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=device_id,
            protocol=mqtt.MQTTv5,
        )

        def publish_hello(active_client):
            active_client.publish(
                f"tambox/v1/device/{device_id}/hello",
                json.dumps(
                    {
                        "device_code": "TBX-A7K2",
                        "model": "TMBox ESP32-S3",
                        "firmware_version": "0.1.0",
                    }
                ),
                qos=1,
                retain=False,
            )

        def on_connect(active_client, userdata, flags, reason_code, properties):
            del userdata, flags, reason_code, properties
            active_client.subscribe(f"tambox/v1/device/{device_id}/assignment", qos=1)
            active_client.subscribe(f"tambox/v1/client/{device_id}/snapshot/+", qos=1)
            publish_hello(active_client)

        def on_message(active_client, userdata, message):
            del active_client, userdata
            payload = json.loads(message.payload.decode("utf-8"))
            if message.topic.endswith("/assignment"):
                received["assignment"] = payload
                assignment_event.set()
            elif "/snapshot/" in message.topic:
                received["snapshot"] = payload
                snapshot_event.set()

        device.on_connect = on_connect
        device.on_message = on_message
        device.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
        device.loop_start()

        try:
            self.assertTrue(assignment_event.wait(5), "box discovery was not acknowledged")
            self.assertEqual(received["assignment"]["status"], "waiting_for_assignment")
            self.assertEqual(identities.discovered_devices()[0].device_code, "TBX-A7K2")

            identities.assign_discovered_device("TBX-A7K2", ("panel-b",))
            assignment_event.clear()
            publish_hello(device)

            self.assertTrue(assignment_event.wait(5), "assigned box did not get its mapping")
            self.assertEqual(received["assignment"]["status"], "assigned")
            self.assertEqual(received["assignment"]["assigned_panel_ids"], ["panel-b"])
            self.assertTrue(snapshot_event.wait(5), "assigned box did not get its panel")
            self.assertEqual(received["snapshot"]["panel_id"], "panel-b")
        finally:
            device.disconnect()
            device.loop_stop()
            gateway.client.disconnect()
            gateway.client.loop_stop()
            identities.close()
            temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
