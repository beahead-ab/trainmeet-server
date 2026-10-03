"""Normtexten för 16×2-profilen får inte glida isär från koden.

docs/protocol/terminal16/README.md är den text vi hänvisar till när
TMBox-protokollet föreslås som standard (MQTT-LCP och Bennys mqttTamBox,
2026-10-01). Tabellen "Värden" där jämförs här med konstanterna i Servern.
"""
import json
from pathlib import Path
import re
import unittest

import test_shared_traffic
from tmbox_gateway.http_server import DEVICE_OFFLINE_SECONDS, DEVICE_ONLINE_SECONDS
from tmbox_gateway.terminal16_mqtt import MAX_PAYLOAD_BYTES, MAX_SESSIONS, SESSION_IDLE_SECONDS, Terminal16Gateway
from tmbox_gateway.terminal16_runtime import Terminal16Service
from tmbox_gateway.terminal16 import NOTICE_SECONDS

DOC = Path(__file__).resolve().parents[1] / "docs" / "protocol" / "terminal16" / "README.md"


def values():
    text = DOC.read_text(encoding="utf-8")
    table = text.split("## Värden", 1)[1]
    rows = re.findall(r"^\| (.+?) \| (.+?) \|$", table, flags=re.M)
    return {name.strip("`"): value.strip("`") for name, value in rows if name != "Namn" and not name.startswith("-")}


class ProtocolTextMatchesCodeTests(unittest.TestCase):
    def test_the_values_table_is_what_the_server_does(self):
        fixture = test_shared_traffic.SharedTrafficTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        frame = Terminal16Service(fixture.service).frame("esp8266")
        expected = {
            "Ämnesprefix": Terminal16Gateway.PREFIX,
            "MAX_SESSIONS": str(MAX_SESSIONS),
            "SESSION_IDLE_SECONDS": str(SESSION_IDLE_SECONDS),
            "MAX_PAYLOAD_BYTES": str(MAX_PAYLOAD_BYTES),
            "input_guard_ms": str(frame["input_guard_ms"]),
            "NOTICE_SECONDS": str(NOTICE_SECONDS),
            "entry.max_length": str(frame["entry"]["max_length"]),
            "DEVICE_ONLINE_SECONDS": str(DEVICE_ONLINE_SECONDS),
            "DEVICE_OFFLINE_SECONDS": str(DEVICE_OFFLINE_SECONDS),
        }
        self.assertEqual(expected, values())

    def test_a_message_over_the_limit_is_dropped(self):
        fixture = test_shared_traffic.SharedTrafficTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        sent = []
        gateway = Terminal16Gateway(Terminal16Service(fixture.service), lambda *args: sent.append(args))

        def hello(size):
            body = {"boot": "b-%d" % size, "device_code": "esp32", "model": ""}
            body["model"] = "x" * (size - len(json.dumps(body)))
            payload = json.dumps(body).encode()
            self.assertEqual(size, len(payload))
            gateway.on_message(gateway.PREFIX + "esp32/hello", payload)

        hello(MAX_PAYLOAD_BYTES + 1)
        self.assertEqual([], sent, "one byte over the limit is dropped")
        hello(MAX_PAYLOAD_BYTES)
        self.assertTrue(any(topic.endswith("/frame") for topic, *_ in sent), "at the limit it is taken")

    def test_every_topic_the_server_uses_is_in_the_text(self):
        text = DOC.read_text(encoding="utf-8")
        leaves = [topic.rsplit("/", 1)[1] for topic in Terminal16Gateway.SUBSCRIPTIONS] + ["alive", "frame", "ack"]
        for leaf in leaves:
            self.assertIn(f"`{Terminal16Gateway.PREFIX}<id>/{leaf}`", text)



if __name__ == "__main__":
    unittest.main()
