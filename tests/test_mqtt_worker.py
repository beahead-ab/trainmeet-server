"""Nätverkstråden ska ta emot, inte arbeta.

paho kör on_message på sin egen nätverkstråd - samma tråd som skickar keepalive
och läser nästa meddelande. Allt arbete låg där: ett enda kommando kunde leda
till att varje stations ögonblicksbild publicerades om, och under tiden kunde
tråden varken pinga eller läsa.

Rummet betalade. Uppmätt på en träff: gatewayen tappade mäklaren med 50 sekunders
till fem minuters mellanrum, oregelbundet, och operatörerna beskrev det som att
det tog lång tid från knapptryck till att något hände. Två symptom, en orsak -
och det syns bara när man frågar vilken tråd som gör jobbet.
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tmbox_gateway.mqtt_v2 import MQTTV2Adapter


def _message(topic: str, payload: bytes = b"{}", retain: bool = False) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, payload=payload, retain=retain)


class WorkLeavesTheNetworkThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handled: list[str] = []
        self.gateway = MagicMock()
        self.gateway.gateway_id = "test"
        self.gateway.offline_will.return_value = ("status", {"status": "offline"})
        self.adapter = MQTTV2Adapter(self.gateway, host="127.0.0.1", port=1883)
        self.adapter.client = MagicMock()
        self.adapter._worker = threading.Thread(target=self.adapter._work, daemon=True)
        self.adapter._worker.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.adapter._inbox.put(None)
        self.adapter._worker.join(timeout=5)

    def _wait_for(self, count: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(self.handled) < count:
            time.sleep(0.01)

    def test_a_slow_handler_does_not_hold_the_network_thread(self) -> None:
        """Det som gjorde att mäklaren släppte gatewayen: tråden satt fast i
        arbetet och hann aldrig skicka sin ping."""

        busy = threading.Event()
        started = threading.Event()

        def slow(topic, payload, retained):
            started.set()
            busy.wait(timeout=5)
            self.handled.append(topic)

        self.gateway.on_message.side_effect = slow

        self.adapter._on_message(None, None, _message("tmbox/v2/a"))
        self.assertTrue(started.wait(timeout=5), "arbetet startade aldrig")

        # Nätverkstråden ska vara tillbaka direkt, mitt under det långsamma
        # arbetet. Mäts, inte antas: anropet får ta någon millisekund, inte
        # sekunderna som handläggaren sitter fast i.
        before = time.monotonic()
        self.adapter._on_message(None, None, _message("tmbox/v2/b"))
        spent = time.monotonic() - before
        self.assertLess(spent, 0.2, f"on_message blockerade i {spent:.2f} s")

        busy.set()
        self._wait_for(2)
        self.assertEqual(["tmbox/v2/a", "tmbox/v2/b"], self.handled)

    def test_messages_keep_their_order(self) -> None:
        """En box skickar knapptryck i en ordning och menyn följer med. Två
        trådar hade kunnat kasta om dem."""

        self.gateway.on_message.side_effect = lambda topic, payload, retained: self.handled.append(topic)
        for index in range(25):
            self.adapter._on_message(None, None, _message(f"tmbox/v2/{index:02d}"))
        self._wait_for(25)
        self.assertEqual([f"tmbox/v2/{index:02d}" for index in range(25)], self.handled)

    def test_one_broken_message_does_not_stop_the_rest(self) -> None:
        def handle(topic, payload, retained):
            if topic.endswith("trasig"):
                raise ValueError("oväntad payload")
            self.handled.append(topic)

        self.gateway.on_message.side_effect = handle
        self.adapter._on_message(None, None, _message("tmbox/v2/trasig"))
        self.adapter._on_message(None, None, _message("tmbox/v2/nasta"))
        self._wait_for(1)
        self.assertEqual(["tmbox/v2/nasta"], self.handled)

    def test_terminal_messages_take_their_own_road(self) -> None:
        terminal = MagicMock()
        terminal.PREFIX = "tmbox/terminal16/"
        self.adapter.terminal_gateway = terminal
        self.gateway.on_message.side_effect = lambda topic, payload, retained: self.handled.append(topic)

        self.adapter._on_message(None, None, _message("tmbox/terminal16/knapp"))
        self.adapter._on_message(None, None, _message("tmbox/v2/annat"))
        self._wait_for(1)
        terminal.on_message.assert_called_once()
        self.assertEqual(["tmbox/v2/annat"], self.handled)

    def test_a_full_queue_is_said_out_loud_not_silently_blocking(self) -> None:
        """En kö som växer obegränsat döljer en server som inte hinner med,
        tills minnet tar slut i stället. Och att blockera nätverkstråden vore
        att återinföra felet."""

        # En egen adapter utan arbetartråd: annars töms kön snabbare än den
        # hinner bli full, och provet mäter ingenting.
        idle = MQTTV2Adapter(self.gateway, host="127.0.0.1", port=1883)
        idle.client = MagicMock()
        idle._inbox.maxsize = 1
        idle._inbox.put(("upptagen", b"{}", False))
        with self.assertLogs("tmbox_gateway.mqtt_v2", level="ERROR") as logs:
            before = time.monotonic()
            idle._on_message(None, None, _message("tmbox/v2/full"))
            self.assertLess(time.monotonic() - before, 0.2)
        self.assertIn("full", logs.output[0])


if __name__ == "__main__":
    unittest.main()
