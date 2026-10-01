"""MQTT transport for the 16×2 terminals (2.0.0: the only protocol over MQTT).

Nätverkstråden ska ta emot, inte arbeta. paho kör on_message på sin egen
nätverkstråd - samma tråd som skickar keepalive och läser nästa meddelande.
Allt arbete låg där: ett enda kommando kunde leda till att varje stations
ögonblicksbild publicerades om, och under tiden kunde tråden varken pinga
eller läsa.

Rummet betalade. Uppmätt på en träff: gatewayen tappade mäklaren med 50 sekunders
till fem minuters mellanrum, oregelbundet, och operatörerna beskrev det som att
det tog lång tid från knapptryck till att något hände. Två symptom, en orsak -
och det syns bara när man frågar vilken tråd som gör jobbet.

Sedan 2.0.0 bär transporten bara tmbox/terminal/…: tambox/v1 och tmbox/v2 är
borta, och det de lämnat sparat hos mäklaren rensas vid första anslutningen.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tmbox_gateway import mqtt_transport
from tmbox_gateway.mqtt_transport import MQTTTransport

PREFIX = "tmbox/terminal/device/"


def _message(topic: str, payload: bytes = b"{}", retain: bool = False) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, payload=payload, retain=retain)


class FakeTerminal:
    """What the transport needs from Terminal16Gateway: receive on the network
    thread, handle on the worker."""

    PREFIX = PREFIX
    SUBSCRIPTIONS = tuple(PREFIX + "+/" + leaf for leaf in ("hello", "presence", "command"))

    def __init__(self) -> None:
        self.receiving_threads: list[threading.Thread] = []
        self.handled: list[str] = []
        self.handle_hook = None

    def receive(self, topic, payload, *, retained=False):
        self.receiving_threads.append(threading.current_thread())
        return None if topic.endswith("klart") or retained else ("arbete", topic)

    def handle(self, item):
        if self.handle_hook:
            return self.handle_hook(item)
        self.handled.append(item[1])
        return True


class WorkLeavesTheNetworkThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.terminal = FakeTerminal()
        self.transport = MQTTTransport(gateway_id="test", host="127.0.0.1", port=1883)
        self.transport.client = MagicMock()
        self.transport.terminal_gateway = self.terminal
        self.transport._worker = threading.Thread(target=self.transport._work, daemon=True)
        self.transport._worker.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.transport._inbox.put(None)
        self.transport._worker.join(timeout=5)

    def _wait_for(self, count: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(self.terminal.handled) < count:
            time.sleep(0.01)

    def test_a_slow_handler_does_not_hold_the_network_thread(self) -> None:
        """Det som gjorde att mäklaren släppte gatewayen: tråden satt fast i
        arbetet och hann aldrig skicka sin ping."""

        busy = threading.Event()
        started = threading.Event()

        def slow(item):
            started.set()
            busy.wait(timeout=5)
            self.terminal.handled.append(item[1])

        self.terminal.handle_hook = slow
        self.transport._on_message(None, None, _message(PREFIX + "a/command"))
        self.assertTrue(started.wait(timeout=5), "arbetet startade aldrig")

        # Nätverkstråden ska vara tillbaka direkt, mitt under det långsamma
        # arbetet. Mäts, inte antas: anropet får ta någon millisekund, inte
        # sekunderna som handläggaren sitter fast i.
        before = time.monotonic()
        self.transport._on_message(None, None, _message(PREFIX + "b/command"))
        spent = time.monotonic() - before
        self.assertLess(spent, 0.2, f"on_message blockerade i {spent:.2f} s")

        busy.set()
        self._wait_for(2)
        self.assertEqual([PREFIX + "a/command", PREFIX + "b/command"], self.terminal.handled)

    def test_messages_keep_their_order(self) -> None:
        """En box skickar knapptryck i en ordning och menyn följer med. Två
        trådar hade kunnat kasta om dem."""

        for index in range(25):
            self.transport._on_message(None, None, _message(f"{PREFIX}{index:02d}/command"))
        self._wait_for(25)
        self.assertEqual([f"{PREFIX}{index:02d}/command" for index in range(25)], self.terminal.handled)

    def test_one_broken_message_does_not_stop_the_rest(self) -> None:
        def handle(item):
            if item[1].endswith("trasig/command"):
                raise ValueError("oväntad payload")
            self.terminal.handled.append(item[1])

        self.terminal.handle_hook = handle
        self.transport._on_message(None, None, _message(PREFIX + "trasig/command"))
        self.transport._on_message(None, None, _message(PREFIX + "nasta/command"))
        self._wait_for(1)
        self.assertEqual([PREFIX + "nasta/command"], self.terminal.handled)

    def test_only_terminal_work_reaches_the_queue(self) -> None:
        """Terminalen tas emot på nätverkstråden och arbetas i kön - bara det
        receive() lämnar vidare hamnar där. tambox/v1, tmbox/v2 och allt annat
        kastas utan att någon tittar på det (2.0.0)."""

        self.transport._on_message(None, None, _message(PREFIX + "box/klart"))
        self.transport._on_message(None, None, _message(PREFIX + "box/command"))
        for topic in ("tmbox/v2/device/esp32/command", "tambox/v1/client/esp8266/command", "något/annat"):
            self.transport._on_message(None, None, _message(topic))
        self._wait_for(1)
        time.sleep(0.1)
        self.assertEqual([threading.current_thread()] * 2, self.terminal.receiving_threads)
        self.assertEqual([PREFIX + "box/command"], self.terminal.handled)
        self.assertEqual(0, self.transport._inbox.qsize())
        self.transport.client.publish.assert_not_called()

    def test_a_full_queue_is_said_out_loud_not_silently_blocking(self) -> None:
        """En kö som växer obegränsat döljer en server som inte hinner med,
        tills minnet tar slut i stället. Och att blockera nätverkstråden vore
        att återinföra felet."""

        # En egen transport utan arbetartråd: annars töms kön snabbare än den
        # hinner bli full, och provet mäter ingenting.
        idle = MQTTTransport(gateway_id="idle", host="127.0.0.1", port=1883)
        idle.client = MagicMock()
        idle.terminal_gateway = FakeTerminal()
        idle._inbox.maxsize = 1
        idle._inbox.put(("upptagen", b"{}", False))
        with self.assertLogs("tmbox_gateway.mqtt_transport", level="ERROR") as logs:
            before = time.monotonic()
            idle._on_message(None, None, _message(PREFIX + "full/command"))
            self.assertLess(time.monotonic() - before, 0.2)
        self.assertIn("full", logs.output[0])


class OnlyTheTerminalsAreSpokenTests(unittest.TestCase):
    """What the transport asks of the broker."""

    def setUp(self) -> None:
        self.transport = MQTTTransport(gateway_id="pi", host="127.0.0.1", port=1883)
        self.transport.client = MagicMock()
        self.transport.terminal_gateway = FakeTerminal()

    def connect(self, session_present=False):
        client = self.transport.client
        self.transport._on_connect(client, None, SimpleNamespace(session_present=session_present),
                                   SimpleNamespace(is_failure=False), None)
        return [call.args[0] for call in client.subscribe.call_args_list]

    def test_a_new_client_id_and_no_will(self) -> None:
        """The broker keeps a session five minutes: the old id still held the
        v2 subscriptions. And no gateway status on tmbox/v2 any more."""

        transport = MQTTTransport(gateway_id="pi", host="127.0.0.1", port=1883)
        self.assertEqual(b"tmbox-terminal-pi", transport.client._client_id)
        self.assertIsNone(transport.client._will_topic or None)

    def test_it_subscribes_to_the_terminals_and_once_to_the_old_topics(self) -> None:
        self.transport._legacy_done = True  # the clearing is the next test's
        self.assertEqual(list(FakeTerminal.SUBSCRIPTIONS), self.connect())
        self.transport.client.publish.assert_not_called()

    def test_what_the_old_protocols_left_retained_is_cleared_once(self) -> None:
        """A gateway status stuck at "online", assignments and snapshots:
        nothing reads them, and they would lie in the broker for good."""

        timers = []

        class Timer:
            def __init__(self, delay, function):
                self.started = False
                timers.append((delay, function, self))

            def start(self):
                self.started = True

        original = mqtt_transport.threading.Timer
        mqtt_transport.threading.Timer = Timer
        self.addCleanup(setattr, mqtt_transport.threading, "Timer", original)
        subscribed = self.connect()
        self.assertEqual(["tambox/#", "tmbox/v2/#"], subscribed[-2:])
        self.assertEqual(mqtt_transport.LEGACY_CLEAR_SECONDS, timers[0][0])
        self.assertTrue(timers[0][2].started, "the window closes by itself")
        client = self.transport.client
        for message in (
            _message("tmbox/v2/gateway/pi/status", b'{"status":"online"}', retain=True),
            _message("tambox/v1/device/esp8266-a/assignment", b'{"status":"assigned"}', retain=True),
            _message("tambox/v1/device/esp8266-a/assignment", b'{"status":"assigned"}', retain=True),  # twice: once
            _message("tmbox/v2/device/esp32/command", b'{"action":"x"}'),  # live: an old box, not stored
            _message("tmbox/v2/device/esp32/config", b"", retain=True),  # the clearing coming back
        ):
            self.transport._on_message(None, None, message)
        self.assertEqual(
            [("tmbox/v2/gateway/pi/status", b"", True), ("tambox/v1/device/esp8266-a/assignment", b"", True)],
            [(call.args[0], call.args[1], call.kwargs["retain"]) for call in client.publish.call_args_list])
        with self.assertLogs("tmbox_gateway.mqtt_transport", level="INFO") as logs:
            timers[0][1]()
        self.assertEqual(["tambox/#", "tmbox/v2/#"], [call.args[0] for call in client.unsubscribe.call_args_list])
        self.assertIn("Rensade 2 sparade", logs.output[0])
        # Done: a reconnect does not do it again, and later stragglers are left.
        client.reset_mock()
        self.assertEqual(list(FakeTerminal.SUBSCRIPTIONS), self.connect(session_present=True))
        self.transport._on_message(None, None, _message("tambox/v1/client/x/presence", b"{}", retain=True))
        client.publish.assert_not_called()


class RealBrokerTests(unittest.TestCase):
    """The transport against a real Mosquitto: what a 0.7.x box sees, and what
    is left on the broker afterwards."""

    @classmethod
    def setUpClass(cls) -> None:
        executable = shutil.which("mosquitto")
        if executable is None:
            raise unittest.SkipTest("mosquitto is not installed")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.broker = subprocess.Popen([executable, "-p", str(cls.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            cls.broker.terminate()
            raise RuntimeError("testmäklaren startade inte")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.broker.terminate()
        cls.broker.wait(timeout=10)

    def client(self, name):
        import paho.mqtt.client as mqtt

        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=name, protocol=mqtt.MQTTv5)
        received: list[tuple[str, bytes, bool]] = []
        connected = threading.Event()
        client.on_connect = lambda *_: connected.set()
        client.on_message = lambda _c, _u, message: received.append((message.topic, message.payload, bool(message.retain)))
        client.connect("127.0.0.1", self.port)
        client.loop_start()
        self.addCleanup(client.disconnect)
        self.addCleanup(client.loop_stop)
        self.assertTrue(connected.wait(5))
        return client, received

    def wait(self, condition, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            time.sleep(0.02)
        return condition()

    def test_a_box_is_answered_and_the_old_retained_topics_are_gone(self) -> None:
        old, _ = self.client("old-server")
        for topic in ("tmbox/v2/gateway/pi/status", "tambox/v1/device/esp8266-a/assignment"):
            old.publish(topic, b'{"status":"online"}', qos=1, retain=True).wait_for_publish(5)

        class Answering(FakeTerminal):
            def handle(inner, item):
                transport.publish(item[1].rsplit("/", 1)[0] + "/frame", {"lines": ["TRAINMEET", "VANTAR"]}, False)
                return True

        transport = MQTTTransport(gateway_id="pi", host="127.0.0.1", port=self.port)
        transport.terminal_gateway = Answering()
        everything, seen = self.client("everything")
        everything.subscribe("#", qos=1)
        time.sleep(0.3)
        seen.clear()
        transport.connect()
        self.addCleanup(transport.disconnect)
        box, frames = self.client("box")
        box.subscribe(PREFIX + "esp8266-b/frame", qos=1)
        time.sleep(0.2)
        box.publish(PREFIX + "esp8266-b/hello", json.dumps({"boot": "1"}), qos=1)
        self.assertTrue(self.wait(lambda: frames), "the box got no frame")
        self.assertEqual({"lines": ["TRAINMEET", "VANTAR"]}, json.loads(frames[0][1]))
        # After the clearing window nothing old is stored, and nothing old was
        # said except the clearing itself.
        time.sleep(mqtt_transport.LEGACY_CLEAR_SECONDS + 0.5)
        late, stored = self.client("late")
        late.subscribe("tambox/#", qos=1)
        late.subscribe("tmbox/v2/#", qos=1)
        time.sleep(0.5)
        self.assertEqual([], stored, "retained v1/v2 topics are still on the broker")
        said = [(topic, payload) for topic, payload, _ in seen if topic.startswith(("tambox/", "tmbox/v2/"))]
        self.assertTrue(said and all(payload == b"" for _, payload in said), said)


if __name__ == "__main__":
    unittest.main()
