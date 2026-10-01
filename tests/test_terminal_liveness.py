"""En långsam server får inte se ut som en död server.

Boxen (firmware 0.7.1) frågar "finns servern?" var femte sekund och ger upp
efter femton sekunder utan svar. Frågan besvarades förut av samma kö som gjorde
allt arbete. Uppmätt i fält 2026-09-30 och återskapat i tools/tmbox-rig: när
varje meddelande tog en knapp sekund räckte en enda störning för att alla boxar
skulle dö samtidigt och sedan fortsätta dö efter exakt två pingar per session.
Servern svarade hela tiden, 16-41 sekunder för sent, och journalen var ren.

Tre saker höll igång det, och varsitt prov nedan håller dem borta:
livstecknet väntade i kön, pingar från döda sessioner kostade lika mycket som
levande, och ingenting i journalen sa att kön låg efter.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test_shared_traffic
from tmbox_gateway import mqtt_transport
from tmbox_gateway.mqtt_transport import MQTTTransport
from tmbox_gateway.terminal16_mqtt import Terminal16Gateway
from tmbox_gateway.terminal16_runtime import Terminal16Service


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class TerminalLivenessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.terminals = Terminal16Service(self.fixture.service)
        self.published = []
        self.clock = Clock()
        self.gateway = Terminal16Gateway(self.terminals, lambda topic, body, retain: self.published.append((topic, body)),
                                         now=self.clock)
        self.lock = self.fixture.service.operations_store.command_lock

    def topic(self, leaf, device="esp8266"):
        return self.gateway.PREFIX + device + "/" + leaf

    def payload(self, **body):
        return json.dumps(body).encode()

    def hello(self, boot, device="esp8266"):
        return self.gateway.receive(self.topic("hello", device), self.payload(boot=boot, device_code=device))

    def presence(self, boot, nonce, device="esp8266"):
        return self.gateway.receive(self.topic("presence", device), self.payload(boot=boot, nonce=nonce))

    def alives(self):
        return [body for topic, body in self.published if topic.endswith("/alive")]

    def frames(self):
        return [body for topic, body in self.published if topic.endswith("/frame")]

    # ------------------------------------------------------------ kanten

    def test_presence_is_answered_while_the_traffic_lock_is_held(self):
        """Fältfelet i ett prov: arbetet står still, men livstecknet ska ändå
        besvaras - direkt, inte när arbetet råkar bli klart."""

        self.gateway.handle(self.hello("A"))
        taken, release = threading.Event(), threading.Event()

        def hold():
            with self.lock:
                taken.set()
                release.wait(timeout=10)

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.addCleanup(holder.join, 5)
        self.addCleanup(release.set)
        self.assertTrue(taken.wait(timeout=5))

        before = time.monotonic()
        item = self.presence("A", "p1")
        spent = time.monotonic() - before
        self.assertLess(spent, 0.5, f"receive väntade {spent:.2f} s på trafiklåset")
        self.assertEqual([{"boot": "A", "nonce": "p1"}], self.alives())
        self.assertIsNotNone(item, "bilden ska fortfarande uppdateras när arbetaren hinner")

    def test_a_session_the_server_does_not_know_gets_no_answer(self):
        """Efter en omstart känner servern ingen session. Tystnaden är det som
        får boxen att koppla upp igen och skicka hello - ett svar här skulle
        lämna den ansluten utan att någonsin få en bild."""

        self.assertIsNone(self.presence("A", "p1"))
        self.assertEqual([], self.alives())

    def test_a_replaced_session_gets_no_answer(self):
        self.gateway.handle(self.hello("A"))
        self.gateway.handle(self.hello("B"))
        self.assertIsNone(self.presence("A", "p1"))
        self.assertEqual([], self.alives())

    # ------------------------------------------------------- arbetaren

    def test_work_from_a_replaced_session_is_skipped(self):
        """Pingar och knapptryck från en session boxen redan gett upp om
        kostade lika mycket som levande. Det var så boxarna själva höll igång
        kollapsen."""

        old = [self.hello("A"), self.presence("A", "p1"), self.presence("A", "p2")]
        new_hello = self.hello("B")
        with patch.object(self.terminals, "frame", wraps=self.terminals.frame) as frame:
            self.assertEqual([False, False, False], [self.gateway.handle(item) for item in old])
            frame.assert_not_called()
        self.assertTrue(self.gateway.handle(new_hello))
        self.assertEqual(["B"], [body["boot"] for body in self.frames()])

    def test_a_command_from_a_replaced_session_is_not_carried_out(self):
        """Boxen har redan gett upp om svaret och visar att ingenting hände.
        Att ändå utföra kommandot vore att göra något operatören inte vet om,
        och troligen trycker igen för."""

        self.gateway.handle(self.hello("A"))
        frame = self.frames()[-1]["frame"]
        command = self.gateway.receive(self.topic("command"), self.payload(
            boot="A", key="#", train_number="101", entry_context=frame["entry"]["context"],
            view_token=frame["view_token"], command_id="A-1"))
        self.hello("B")
        with patch.object(self.terminals, "command", wraps=self.terminals.command) as carried_out, \
                self.assertLogs("tmbox_gateway.terminal16", level="WARNING") as logs:
            self.assertFalse(self.gateway.handle(command))
        carried_out.assert_not_called()
        self.assertEqual([], [topic for topic, _ in self.published if topic.endswith("/ack")])
        self.assertIn("A-1", logs.output[0])

    def test_a_command_from_the_current_session_is_carried_out(self):
        """Motprovet: det nya får inte kasta det som ska göras."""

        self.gateway.handle(self.hello("A"))
        frame = self.frames()[-1]["frame"]
        command = self.gateway.receive(self.topic("command"), self.payload(
            boot="A", key="#", train_number="101", entry_context=frame["entry"]["context"],
            view_token=frame["view_token"], command_id="A-1"))
        self.assertTrue(self.gateway.handle(command))
        acks = [body for topic, body in self.published if topic.endswith("/ack")]
        self.assertEqual([("accepted", "A-1")], [(ack["status"], ack["command_id"]) for ack in acks])

    def test_only_the_latest_presence_is_worked(self):
        self.gateway.handle(self.hello("A"))
        items = [self.presence("A", f"p{n}") for n in range(1, 4)]
        self.assertEqual(3, len(self.alives()), "varje ping besvaras vid kanten")
        self.assertEqual([False, False, True], [self.gateway.handle(item) for item in items])

    def test_a_hello_that_cannot_be_recorded_stops_the_answers(self):
        """Annars får boxen livstecken för en session servern aldrig kunde
        registrera, visar ingen bild, och kopplar aldrig upp igen."""

        item = self.hello("A")
        with patch.object(self.fixture.service.identities, "record_discovery", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                self.gateway.handle(item)
        self.assertIsNone(self.presence("A", "p1"))
        self.assertEqual([], self.alives())

    def test_a_connection_that_expired_while_the_worker_was_behind_is_resumed(self):
        """Kanten svarar, så boxen ger inte upp. Har anslutningen hunnit gå ut
        på arbetarsidan måste den tas upp igen - annars får boxen livstecken
        men aldrig en ny bild."""

        self.gateway.handle(self.hello("A"))
        self.published.clear()
        self.clock.value += 60
        self.gateway.tick()
        self.assertNotIn("esp8266", self.gateway.connections)
        self.gateway.handle(self.presence("A", "p1"))
        self.assertIn("esp8266", self.gateway.connections)
        self.assertEqual(["A"], [body["boot"] for body in self.frames()])

    def test_the_single_thread_path_still_does_both(self):
        """on_message finns kvar för den som inte har en kö emellan."""

        self.gateway.on_message(self.topic("hello"), self.payload(boot="A", device_code="esp8266"))
        self.gateway.on_message(self.topic("presence"), self.payload(boot="A", nonce="p1"))
        self.assertEqual([{"boot": "A", "nonce": "p1"}], self.alives())
        self.assertEqual(1, len(self.frames()))


class AdapterWithRealTerminalTests(unittest.TestCase):
    """Hela vägen genom transporten: arbetaren sitter fast, boxen får svar ändå."""

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.adapter = MQTTTransport(gateway_id="test", host="127.0.0.1", port=1883)
        self.adapter.client = MagicMock()
        self.published = []
        self.terminal = Terminal16Gateway(Terminal16Service(self.fixture.service),
                                          lambda topic, body, retain: self.published.append((topic, body)))
        self.adapter.terminal_gateway = self.terminal
        self.adapter._worker = threading.Thread(target=self.adapter._work, daemon=True)
        self.adapter._worker.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.adapter._inbox.put(None)
        self.adapter._worker.join(timeout=5)

    def message(self, leaf, **body):
        return SimpleNamespace(topic=self.terminal.PREFIX + "esp8266/" + leaf,
                               payload=json.dumps(body).encode(), retain=False)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.01)
        return predicate()

    def test_presence_is_answered_while_the_worker_is_stuck(self):
        self.adapter._on_message(None, None, self.message("hello", boot="A", device_code="esp8266"))
        self.assertTrue(self.wait_for(lambda: any(t.endswith("/frame") for t, _ in self.published)))

        lock = self.fixture.service.operations_store.command_lock
        taken, release = threading.Event(), threading.Event()

        def hold():
            with lock:
                taken.set()
                release.wait(timeout=10)

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.addCleanup(holder.join, 5)
        self.addCleanup(release.set)
        self.assertTrue(taken.wait(timeout=5))
        # Ett knapptryck som fastnar bakom låset - arbetaren står still.
        self.adapter._on_message(None, None, self.message("command", boot="A", key="C", command_id="A-1"))
        self.adapter._on_message(None, None, self.message("presence", boot="A", nonce="p1"))
        self.assertIn(("tmbox/terminal/device/esp8266/alive", {"boot": "A", "nonce": "p1"}), self.published)


class QueueWaitIsReportedTests(unittest.TestCase):
    """Utan det här syntes ingenting: servern svarade, bara för sent, och
    journalen var ren."""

    def setUp(self):
        self.adapter = MQTTTransport(gateway_id="test", host="127.0.0.1", port=1883)
        self.clock = Clock()
        self.adapter.now = self.clock

    def test_a_long_wait_is_reported_and_so_is_the_recovery(self):
        with self.assertLogs("tmbox_gateway.mqtt_transport", level="INFO") as logs:
            self.adapter._note_wait(0.1)
            self.adapter._note_wait(16.4)
            self.adapter._note_wait(0.05)
        self.assertEqual(2, len(logs.output))
        self.assertIn("ligger efter: 16.4 s", logs.output[0])
        self.assertIn("hämtat sig", logs.output[1])

    def test_a_long_backlog_is_reported_at_a_bounded_rate(self):
        with self.assertLogs("tmbox_gateway.mqtt_transport", level="WARNING") as logs:
            for _ in range(50):
                self.adapter._note_wait(20.0)
                self.clock.value += 0.5
        # 25 sekunder, en rad per tio: tre rader, inte femtio.
        self.assertEqual(3, len(logs.output))

    def test_a_quiet_queue_says_nothing(self):
        with patch.object(mqtt_transport.LOGGER, "info") as info, patch.object(mqtt_transport.LOGGER, "warning") as warning:
            for _ in range(20):
                self.adapter._note_wait(0.3)
        info.assert_not_called()
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
