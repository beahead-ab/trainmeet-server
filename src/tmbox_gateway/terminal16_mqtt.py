"""Thin, non-retained display transport. Discovery never grants a station."""
import json
import logging
import threading
from time import monotonic

from .identity import CLIENT_ID_PATTERN, DisplayCapability


LOGGER = logging.getLogger("tmbox_gateway.terminal16")

# Samma tak som för anslutningarna: en box per rad, och ingen på nätet ska
# kunna fylla minnet genom att hitta på enhets-id:n.
MAX_SESSIONS = 256
SESSION_IDLE_SECONDS = 45


class Terminal16Gateway:
    """Två steg: ta emot på nätverkstråden, arbeta på arbetartråden.

    Boxen frågar "finns servern?" var femte sekund och ger upp efter femton
    sekunder utan svar. Den frågan besvarades förut av samma kö som gjorde allt
    arbete. Uppmätt 2026-09-30: när varje meddelande tog en knapp sekund räckte
    en enda störning för att alla boxar skulle dö samtidigt och sedan fortsätta
    dö efter exakt två pingar per session. Servern svarade hela tiden, bara för
    sent, och journalen var ren. Varje återanslutning lade dessutom mer arbete i
    kön, och pingar från redan döda sessioner kostade lika mycket att hantera
    som levande - boxarna höll själva igång kollapsen.

    Därför besvaras livstecknet i receive(), innan kön, utan trafiklås och utan
    databas. Svaret betyder "servern känner din session" - inte "servern har
    hunnit ikapp". Och handle() hoppar över det som hunnit bli inaktuellt: allt
    från en session som ersatts av en nyare, och varje ping utom den senaste.
    """

    PREFIX = "tmbox/terminal/device/"
    SUBSCRIPTIONS = tuple("tmbox/terminal/device/+/" + leaf for leaf in ("hello", "presence", "command"))

    def __init__(self, terminals, publish, *, now=monotonic, on_seen=None):
        self.terminals, self.publish, self.now = terminals, publish, now
        # Told about every message from a session the server knows - a cheap
        # in-memory note, so the admin can see which boxes are alive.
        self.on_seen = on_seen
        self.connections = {}
        # Kanten: den senaste sessionen per box, som nätverkstråden ser den.
        # Eget lås, aldrig trafiklåset - det är hela poängen.
        self._sessions = {}
        self._sessions_lock = threading.Lock()
        self._sequence = 0

    # ------------------------------------------------------------- en tråd

    def on_message(self, topic, payload, *, retained=False):
        """Båda stegen i följd, för den som inte har en kö emellan."""

        item = self.receive(topic, payload, retained=retained)
        if item is not None:
            self.handle(item)

    # ------------------------------------------------------- nätverkstråden

    def receive(self, topic, payload, *, retained=False):
        """Tolka, svara på livstecken och lämna arbetet vidare.

        Körs på paho:s nätverkstråd och får därför aldrig vänta på något:
        ingen databas, inget trafiklås. Returnerar det arbete som återstår,
        eller None om ingenting återstår.
        """

        if retained or not topic.startswith(self.PREFIX) or len(payload) > 4096:
            return None
        parts = topic[len(self.PREFIX):].split("/")
        if len(parts) != 2 or not CLIENT_ID_PATTERN.fullmatch(parts[0]):
            return None
        device, leaf = parts
        if leaf not in ("hello", "presence", "command"):
            return None
        try:
            body = json.loads(payload)
        except (ValueError, UnicodeError):
            return None
        if not isinstance(body, dict) or not isinstance(body.get("boot"), str) or not 1 <= len(body["boot"]) <= 64:
            return None
        boot = body["boot"]
        now = self.now()
        with self._sessions_lock:
            session = self._sessions.get(device)
            if leaf == "hello":
                if session is None and len(self._sessions) >= MAX_SESSIONS:
                    self._expire_sessions(now)
                    if len(self._sessions) >= MAX_SESSIONS:
                        return None
                replaced = session["boot"] if session else None
                session = self._sessions[device] = {"boot": boot, "presence": 0, "seen": now}
            elif session is None or session["boot"] != boot:
                # En session servern inte känner: efter en omstart, eller en
                # som redan ersatts. Tystnaden är det som får boxen att koppla
                # upp igen och skicka hello.
                return None
            session["seen"] = now
            self._sequence += 1
            sequence = self._sequence
            if leaf == "presence":
                session["presence"] = sequence
        if self.on_seen:
            self.on_seen(device)
        if leaf == "hello":
            if replaced is None:
                LOGGER.info("TMBox %s ansluten (session %s)", device, boot)
            elif replaced != boot:
                LOGGER.info("TMBox %s kopplade upp igen (session %s ersätter %s)", device, boot, replaced)
        elif leaf == "presence":
            self.publish(self.PREFIX + device + "/alive", {"boot": boot, "nonce": body.get("nonce")}, False)
        return (device, leaf, body, sequence)

    def _expire_sessions(self, now):
        for device in [d for d, s in self._sessions.items() if now - s["seen"] > SESSION_IDLE_SECONDS]:
            self._sessions.pop(device)

    def _forget(self, device, boot):
        """Sluta svara en session som inte gick att registrera, så att boxen
        kopplar upp igen i stället för att vänta för evigt på en bild."""

        with self._sessions_lock:
            session = self._sessions.get(device)
            if session is not None and session["boot"] == boot:
                self._sessions.pop(device)

    # --------------------------------------------------------- arbetartråden

    def handle(self, item):
        """Gör arbetet, om det fortfarande behövs. False om det hoppades över."""

        device, leaf, body, sequence = item
        with self._sessions_lock:
            session = self._sessions.get(device)
            current = session is not None and session["boot"] == body["boot"]
            if current and leaf == "presence" and session["presence"] != sequence:
                # En nyare ping från samma box väntar redan längre fram.
                current = False
        if not current:
            if leaf == "command":
                # Boxen har redan gett upp om svaret och visar att ingenting
                # hände. Att ändå utföra det vore att göra något operatören
                # inte vet om - och troligen trycker igen för.
                LOGGER.warning("Kommando från en avslutad session utfördes inte: %s %s",
                               device, body.get("command_id"))
            return False
        self._handle_current(device, leaf, body)
        return True

    def _handle_current(self, device, leaf, body):
        with self.terminals.service.operations_store.command_lock:
            if leaf == "hello":
                self._expire()
                if device not in self.connections and len(self.connections) >= MAX_SESSIONS:
                    self._forget(device, body["boot"])
                    return
                try:
                    self.terminals.service.identities.record_discovery(device, str(body.get("device_code") or device),
                        model=str(body.get("model") or "TMBox 16×2")[:80], firmware_version=str(body.get("firmware_version") or "")[:32],
                        hardware_version=str(body.get("hardware_version") or "server-16x2")[:80],
                        protocol_version=2, display=DisplayCapability(rows=2, cols=16, charset="cgram"))
                except Exception:
                    self._forget(device, body["boot"])
                    raise
                self.connections[device] = {"boot": body["boot"], "seen": self.now(), "frame": None}
                self._frame(device)
            connection = self.connections.get(device)
            if not connection or connection["boot"] != body["boot"]:
                # Kanten säger att sessionen lever - annars hade vi inte kommit
                # hit - men anslutningen gick ut medan arbetaren låg efter.
                # Att tyst släppa den som förut vore att lämna en box som får
                # livstecken men aldrig en ny bild, och därför aldrig kopplar
                # upp igen.
                if device not in self.connections and len(self.connections) >= MAX_SESSIONS:
                    self._forget(device, body["boot"])
                    return
                connection = self.connections[device] = {"boot": body["boot"], "seen": self.now(), "frame": None}
            connection["seen"] = self.now()
            self.terminals.service.observe_operator(device)
            if leaf == "presence":
                # Livstecknet är redan besvarat. Kvar är att bilden ska
                # stämma, och det räcker att göra för den senaste pingen.
                self._frame(device)
            elif leaf == "command":
                answer = self.terminals.command(device, {key: value for key, value in body.items() if key != "boot"})
                self.publish(self.PREFIX + device + "/ack", {**answer, "boot": body["boot"], "command_id": body.get("command_id")}, False)
                self.tick()

    def _expire(self):
        for device in list(self.connections):
            if self.now() - self.connections[device]["seen"] > SESSION_IDLE_SECONDS:
                self.connections.pop(device)

    def _frame(self, device):
        connection = self.connections[device]
        frame = self.terminals.frame(device)
        if frame != connection["frame"]:
            self.publish(self.PREFIX + device + "/frame", {"boot": connection["boot"], "frame": frame}, False)
            connection["frame"] = frame

    def tick(self):
        with self.terminals.service.operations_store.command_lock:
            self._expire()
            for device in list(self.connections):
                self._frame(device)
