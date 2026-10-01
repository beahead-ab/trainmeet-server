"""MQTT transport for the 16x2 terminals: tmbox/terminal/device/<id>/...

Since 2.0.0 the only protocol the server speaks over MQTT. The older
tambox/v1 and tmbox/v2 topics are gone; a message on any other topic is
dropped. On its first connection the transport also clears what those
protocols left retained on the broker.

Until 2.0.0 this lived in mqtt_v2.py, beside the v2 gateway.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

from . import mqtt_session


LOGGER = logging.getLogger("tmbox_gateway.mqtt_transport")

# En box ger upp efter femton sekunder utan svar, och ett knapptryck känns segt
# långt innan dess. Två sekunders väntan i kön är tidigt nog att se det komma.
QUEUE_BEHIND_SECONDS = 2.0
QUEUE_CAUGHT_UP_SECONDS = 0.5
# Hellre en rad var tionde sekund som säger hur illa det är än en per meddelande
# som dränker journalen när det väl händer.
QUEUE_REPORT_INTERVAL_SECONDS = 10.0

# Retained messages tambox/v1 and tmbox/v2 left on the broker: a gateway
# status stuck at "online", assignments, snapshots. Nothing reads them any
# more; they are cleared once, after the first connection.
LEGACY_TOPICS = ("tambox/#", "tmbox/v2/#")
LEGACY_CLEAR_SECONDS = 2.0


class MQTTTransport:
    """Paho client, one worker thread, and the terminal gateway it carries."""

    def __init__(self, *, gateway_id: str, host: str = "127.0.0.1", port: int = 1883):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as error:  # pragma: no cover - exercised by deployment
            raise RuntimeError(
                "Install the mqtt optional dependency: pip install -e '.[mqtt]'"
            ) from error

        self.terminal_gateway = None
        self.host = host
        self.port = port
        # Gränsen är satt för att märkas, inte för att räcka till allt: en kö
        # som växer obegränsat döljer en server som inte hinner med, tills
        # minnet tar slut i stället.
        self._inbox: queue.Queue = queue.Queue(maxsize=2000)
        self._worker: threading.Thread | None = None
        self._behind = False
        self._last_report = float("-inf")
        self._worst_wait = 0.0
        self._skipped = 0
        self.now = time.monotonic
        # A new client id: the broker keeps a session for five minutes, and
        # the old one (tmbox-gateway-v2-...) still held the v2 subscriptions.
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"tmbox-terminal-{gateway_id}",
            protocol=mqtt.MQTTv5,
            reconnect_on_failure=True,
        )
        self.client.reconnect_delay_set(min_delay=1, max_delay=8)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._legacy_clearing = False
        self._legacy_cleared: set[str] = set()
        self._legacy_done = False

    def connect(self) -> None:
        self._worker = threading.Thread(target=self._work, name="tmbox-terminal-arbete", daemon=True)
        self._worker.start()
        mqtt_session.connect(self.client, self.host, self.port)
        self.client.loop_start()

    def disconnect(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()
        if self._worker is not None:
            self._inbox.put(None)
            self._worker.join(timeout=5)
            self._worker = None

    def publish(self, topic: str, payload: dict[str, Any], retain: bool) -> None:
        self.client.publish(topic, _encode(payload), qos=1, retain=retain)

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        del userdata, properties
        if reason_code.is_failure:
            LOGGER.error("Broker avvisade anslutningen: %s", reason_code)
            return
        if self.terminal_gateway:
            for subscription in self.terminal_gateway.SUBSCRIPTIONS:
                client.subscribe(subscription, qos=1)
        if not self._legacy_done and not self._legacy_clearing:
            self._legacy_clearing = True
            for topic in LEGACY_TOPICS:
                client.subscribe(topic, qos=1)
            timer = threading.Timer(LEGACY_CLEAR_SECONDS, self._end_legacy_clearing)
            timer.daemon = True
            timer.start()
        # Om sessionen återupptogs syns det här. En rad som upprepas var
        # halvminut är inte "servern startar" utan en anslutning som ramlar,
        # och det ska gå att se i journalen utan att gissa.
        LOGGER.info(
            "TMBox-terminaler online (%s)",
            "återupptagen session" if getattr(flags, "session_present", False) else "ny session",
        )

    def _end_legacy_clearing(self) -> None:
        self._legacy_clearing = False
        self._legacy_done = True
        for topic in LEGACY_TOPICS:
            self.client.unsubscribe(topic)
        if self._legacy_cleared:
            LOGGER.info("Rensade %d sparade meddelanden från tambox/v1 och tmbox/v2", len(self._legacy_cleared))

    def _clear_legacy(self, topic: str, payload: bytes, retained: bool) -> None:
        # Only what the broker stored: a live message is an old box talking,
        # and an empty payload is the clearing itself coming back.
        if not self._legacy_clearing or not retained or not payload or topic in self._legacy_cleared:
            return
        self._legacy_cleared.add(topic)
        self.client.publish(topic, b"", qos=1, retain=True)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        """Ta emot och lämna vidare. Ingenting mer får hända här.

        paho kör den här återanropet på sin nätverkstråd - samma tråd som
        skickar keepalive och läser nästa meddelande. Låg arbetet kvar här
        betalade rummet för det: ett kommando kunde leda till att varje stations
        ögonblicksbild publicerades om, och under tiden kunde tråden varken pinga
        eller läsa. Mäklaren släppte gatewayen, alla TMBoxar såg servern försvinna
        samtidigt, och knapptryck som kom in under tiden fick vänta på sin tur.

        Uppmätt på en träff: gatewayen tappade anslutningen med 50 sekunders till
        fem minuters mellanrum, oregelbundet, och operatörerna beskrev det som att
        det tog lång tid från knapptryck till att något hände. Två symptom, en
        orsak.
        """

        del client, userdata
        topic, payload, retained = message.topic, message.payload, bool(message.retain)
        if topic.startswith(("tambox/", "tmbox/v2/")):
            self._clear_legacy(topic, payload, retained)
            return
        if not self.terminal_gateway or not topic.startswith(self.terminal_gateway.PREFIX):
            return
        # Terminalens livstecken besvaras här, innan kön - se
        # Terminal16Gateway. Bara det arbete som återstår köas.
        try:
            item = self.terminal_gateway.receive(topic, payload, retained=retained)
        except Exception:  # pragma: no cover - defensive transport boundary
            LOGGER.exception("Ett terminalmeddelande kunde inte tas emot: %s", topic)
            return
        if item is None:
            return
        try:
            self._inbox.put_nowait((self.now(), topic, (self.terminal_gateway.handle, (item,), {})))
        except queue.Full:  # pragma: no cover - kräver en server som redan tappat greppet
            # Hellre säga ifrån än att blockera nätverkstråden: det vore att
            # återinföra precis det som lagades.
            LOGGER.error(
                "Kön till TMBox-arbetet är full, meddelandet kastas: %s", topic
            )

    def _work(self) -> None:
        """Allt riktigt arbete, på en egen tråd och i tur och ordning.

        En tråd, inte flera: meddelanden från en box ska hanteras i den ordning
        de skickades, och två trådar som publicerar bilder för samma box
        skulle kunna skriva om varandra.
        """

        while True:
            item = self._inbox.get()
            if item is None:
                return
            enqueued_at, topic, (handler, args, kwargs) = item
            self._note_wait(self.now() - enqueued_at)
            try:
                if handler(*args, **kwargs) is False:
                    self._skipped += 1
            except Exception:  # pragma: no cover - defensive transport boundary
                LOGGER.exception("Ett terminalmeddelande kunde inte hanteras: %s", topic)

    def _note_wait(self, waited: float) -> None:
        """Säg till när kön ligger efter, och när den hämtat sig.

        Utan det här syntes ingenting: servern svarade, bara för sent, och
        journalen var ren medan varje box i hallen tappade kontakten. Det
        fick räknas fram ur nonce-nummer i en MQTT-klient.
        """

        self._worst_wait = max(self._worst_wait, waited)
        now = self.now()
        if waited >= QUEUE_BEHIND_SECONDS:
            if not self._behind or now - self._last_report >= QUEUE_REPORT_INTERVAL_SECONDS:
                LOGGER.warning(
                    "TMBox-kön ligger efter: %.1f s väntetid (värst %.1f s), %d i kön, "
                    "%d inaktuella hoppades över",
                    waited, self._worst_wait, self._inbox.qsize(), self._skipped,
                )
                self._behind, self._last_report = True, now
                self._worst_wait, self._skipped = 0.0, 0
        elif self._behind and waited < QUEUE_CAUGHT_UP_SECONDS:
            LOGGER.info(
                "TMBox-kön har hämtat sig (värst %.1f s sedan förra raden, %d inaktuella hoppades över)",
                self._worst_wait, self._skipped,
            )
            self._behind, self._last_report = False, now
            self._worst_wait, self._skipped = 0.0, 0


def _encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
