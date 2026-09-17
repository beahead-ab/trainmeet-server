"""Real Mosquitto: v1 key presses and v2 decisions share one live session."""
import json
import threading
import unittest

import paho.mqtt.client as mqtt
import test_mqtt_integration as broker_fixture
import test_shared_traffic as mixed_fixture
from tmbox_gateway.mqtt_adapter import MQTTGatewayAdapter
from tmbox_gateway.mqtt_v2 import MQTTV2Adapter


class MixedMQTTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        broker_fixture.MQTTIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        broker_fixture.MQTTIntegrationTests.tearDownClass.__func__(cls)

    def test_mqtt311_8266_and_v2_esp32_complete_one_clearance(self):
        fixture = mixed_fixture.SharedTrafficTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        # Assign the panel exactly as the station-only hello resolver does.
        fixture.ids.bind_legacy_station_panel("esp8266", "station-a", "panel-a")
        v1 = MQTTGatewayAdapter(fixture.engine, host="127.0.0.1", port=self.port, identities=fixture.ids)
        v2 = MQTTV2Adapter(fixture.gateway, host="127.0.0.1", port=self.port)
        fixture.service.subscribe(v1._publish_snapshots)
        v1.client.connect("127.0.0.1", self.port, keepalive=10, clean_start=True)
        v1.client.loop_start()
        v2.connect()
        ready = threading.Event()
        received = []
        condition = threading.Condition()
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                             client_id="mixed-wire-test", protocol=mqtt.MQTTv311)
        def on_connect(active, *_):
            active.subscribe([("tambox/v1/client/esp8266/#", 1), ("tmbox/v2/device/esp32/#", 1)])
        def on_subscribe(*_):
            ready.set()
        def on_message(active, userdata, message):
            with condition:
                received.append((message.topic, json.loads(message.payload)))
                condition.notify_all()
        client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
        client.connect("127.0.0.1", self.port, keepalive=10)
        client.loop_start()
        def wait_for(predicate):
            with condition:
                found = lambda: next((body for topic, body in reversed(received) if predicate(topic, body)), None)
                self.assertTrue(condition.wait_for(lambda: found() is not None, timeout=30), "mixed-protocol message missing")
                return found()
        counter = 0
        def press(key):
            nonlocal counter
            counter += 1
            command_id = f"key-{counter}"
            client.publish("tambox/v1/client/esp8266/command", json.dumps({
                "protocol_version": 1, "client_id": "esp8266", "command_id": command_id,
                "traffic_session_id": fixture.engine.config.id, "panel_id": "panel-a",
                "expected_revision": fixture.engine.snapshot("panel-a")["revision"],
                "action": "key_press", "key": key,
            }), qos=1)
            ack = wait_for(lambda topic, body: topic.endswith("/ack") and body.get("command_id") == command_id)
            self.assertEqual(ack["status"], "accepted", ack)
        def command(action, body, message_id):
            client.publish("tmbox/v2/device/esp32/command", json.dumps({
                "protocol_version": 2, "message_id": message_id, "action": action, "payload": body,
            }), qos=1)
            ack = wait_for(lambda topic, body: topic.endswith("/ack") and body.get("message_id") == message_id)
            self.assertEqual(ack["status"], "accepted", ack)
        try:
            self.assertTrue(ready.wait(30))
            # Wait for BOTH gateways to subscribe before sending commands.
            # A hello is repeatable and produces an assignment after startup.
            client.publish("tambox/v1/device/esp8266/hello", json.dumps({"protocol_version": 1, "device_code": "esp8266"}), qos=1)
            wait_for(lambda topic, body: "/snapshot/" in topic)
            client.publish("tmbox/v2/device/esp32/hello", json.dumps({"protocol_version": 2, "device_code": "esp32"}), qos=1)
            wait_for(lambda topic, body: topic == "tmbox/v2/device/esp32/snapshot")
            for key in "A101#":
                press(key)
            incoming = wait_for(lambda topic, body: topic == "tmbox/v2/device/esp32/snapshot" and bool(body["active_clearances"]))
            case = incoming["active_clearances"][0]["clearance_id"]
            command("clearance.response", {"clearance_id": case, "approved": True}, "approve")
            wait_for(lambda topic, body: "/snapshot/" in topic and body["slots"]["A"]["state"] == "reserved")
            for key in "AAA":
                press(key)
            command("train.arrived", {"movement_id": "movement-101-b"}, "arrive")
            wait_for(lambda topic, body: "/snapshot/" in topic and body["slots"]["A"]["state"] == "free" and body["revision"] > 5)
            self.assertEqual(fixture.service.open_cases("station-a"), [])
        finally:
            client.disconnect()
            client.loop_stop()
            v1.client.disconnect()
            v1.client.loop_stop()
            v2.disconnect()
