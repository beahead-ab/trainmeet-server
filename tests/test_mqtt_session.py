"""Gatewayens anslutning ska tåla ett segt varv.

Båda gatewayerna anslöt med tio sekunders keepalive och `clean_start=True`.
Mäklaren släpper en klient som inte hörts på ungefär 1,5 × keepalive, alltså
femton sekunder, och på en Raspberry Pi under last räcker ett enda segt varv i
serverns loop.

Priset betalas i rummet, inte i servern. Gatewayen har ett kvarhållet
testamente - `status: offline` - så i samma stund mäklaren släpper den ser varje
TMBox att servern är borta, samtidigt. Uppmätt på en träff: elva sekunders
drift, två sekunder SERVER SAKNAS, tolv sekunder ANSLUTER SERVER, i cykel. Att
alla boxar pendlade *samtidigt* var det som pekade bort från boxarna och hit.

Provet kör mot en riktig mäklare, för det är mäklaren som avgör båda sakerna:
hur länge den väntar, och om sessionen finns kvar när klienten kommer tillbaka.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import unittest

from tmbox_gateway import mqtt_session

BROKER_START_TIMEOUT = 10


class ConnectionSettingTests(unittest.TestCase):
    def test_the_margin_fits_the_hardware_it_runs_on(self) -> None:
        """Mäklaren väntar 1,5 × keepalive. Marginalen ska vara bred nog för en
        Pi som hackar till, men inte så bred att en död server syns sent."""

        self.assertGreaterEqual(mqtt_session.KEEPALIVE_SECONDS, 45)
        self.assertLessEqual(mqtt_session.KEEPALIVE_SECONDS, 120)

    def test_a_blink_does_not_outlive_the_session(self) -> None:
        self.assertGreater(
            mqtt_session.SESSION_EXPIRY_SECONDS,
            mqtt_session.KEEPALIVE_SECONDS * 2,
            "sessionen måste överleva längre än det tar att upptäcka en tappad anslutning",
        )

    def test_every_gateway_connects_through_the_same_door(self) -> None:
        """Tre anslutningar fanns, alla med sina egna siffror. En ändring på ett
        ställe ska gälla allihop - också för den som läggs till senare."""

        from pathlib import Path

        source = Path(__file__).resolve().parent.parent / "src" / "tmbox_gateway"
        clients = [path for path in sorted(source.glob("*.py")) if "mqtt.Client(" in path.read_text(encoding="utf-8")]
        self.assertEqual(["mqtt_transport.py"], [path.name for path in clients])
        for path in clients:
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertIn("mqtt_session.connect(", text)
                self.assertNotIn("keepalive=", text)
                self.assertNotIn("clean_start=", text)


class SessionSurvivesADropTests(unittest.TestCase):
    """Det som faktiskt kostade tolv sekunder: sessionen började om från noll."""

    @classmethod
    def setUpClass(cls) -> None:
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
        deadline = time.monotonic() + BROKER_START_TIMEOUT
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
        cls.broker.wait(timeout=BROKER_START_TIMEOUT)

    def _client(self):
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="tmbox-gateway-test",
            protocol=mqtt.MQTTv5,
        )
        self.seen: list[bool] = []
        client.on_connect = lambda c, u, flags, rc, props: self.seen.append(
            bool(getattr(flags, "session_present", False))
        )
        return client

    def _wait_for_connect(self, expected: int) -> None:
        deadline = time.monotonic() + BROKER_START_TIMEOUT
        while time.monotonic() < deadline and len(self.seen) < expected:
            time.sleep(0.05)
        self.assertEqual(expected, len(self.seen), "anslutningen kom aldrig fram")

    def test_the_broker_remembers_the_gateway_across_a_drop(self) -> None:
        client = self._client()
        mqtt_session.connect(client, "127.0.0.1", self.port)
        client.loop_start()
        self.addCleanup(client.loop_stop)
        self._wait_for_connect(1)
        self.assertFalse(self.seen[0], "första anslutningen har ingen session att ärva")

        # En tappad anslutning, inte en avsiktlig frånkoppling: det är skillnaden
        # mellan att ramla och att gå. Bara den första lämnar en session kvar.
        client._sock.close()
        self._wait_for_connect(2)
        self.assertTrue(
            self.seen[1],
            "sessionen kastades vid tappet - då måste allt prenumereras och skickas om",
        )

    def test_the_keepalive_reaches_the_broker(self) -> None:
        """Värdet ska vara det klienten faktiskt förhandlar, inte bara en
        konstant i vår kod."""

        client = self._client()
        mqtt_session.connect(client, "127.0.0.1", self.port)
        client.loop_start()
        self.addCleanup(client.loop_stop)
        self._wait_for_connect(1)
        self.assertEqual(mqtt_session.KEEPALIVE_SECONDS, client._keepalive)


if __name__ == "__main__":
    unittest.main()
