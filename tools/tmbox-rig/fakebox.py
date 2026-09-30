"""Låtsasboxar som beter sig som firmware/common/server_terminal.h.

Samma klient-id, clean session, keepalive 10 s, MQTT 3.1.1. hello vid anslutning,
presence var 5:e sekund, 15 s tålamod på alive/frame, ny boot vid återanslutning.

FIRMWARE väljer hur ett obesvarat knapptryck hanteras:
  0.7.1  fem sekunder utan kvitto fäller hela sessionen
  0.7.2  kommandot väntar så länge servern svarar på presence; efter 30 s ges
         kommandot upp, men sessionen behålls
PRESS_EVERY (sekunder) får boxen att trycka på en knapp servern erbjuder.

Mäter tiden från varje presence till dess alive, och från varje knapptryck
till dess kvitto.
"""
import json, os, random, sys, threading, time
import paho.mqtt.client as mqtt

HOST, PORT = "127.0.0.1", int(os.environ.get("MQTT_PORT", "18830"))
PREFIX = "tmbox/terminal/device/"
PATIENCE, PING = 15.0, 5.0
FIRMWARE = os.environ.get("FIRMWARE", "0.7.1")
PRESS_EVERY = float(os.environ.get("PRESS_EVERY", "0"))
ACK_DROPS_SESSION = 5.0      # 0.7.1
ACK_GIVE_UP = 30.0           # 0.7.2


class Box:
    def __init__(self, mac, log):
        self.id = "esp8266-" + mac
        self.code = "TBX-" + mac[-6:].upper()
        self.boot_id = "%016x" % random.getrandbits(64)
        self.connection = 0
        self.sequence = 0
        self.log = log
        self.lock = threading.Lock()
        self.pings = {}          # nonce -> sent time
        self.latencies = []      # (sent_at, latency or None)
        self.deaths = []         # (time, boot, pings in session, reason)
        self.frames = 0
        self.stale = {}          # nonce -> sent time, from sessions already given up
        self.late = []           # latency of answers that came after the box gave up
        self.acks = []           # seconds from key press to its ack
        self.given_up = 0        # commands 0.7.2 gave up on without dropping the session
        self.frame = None
        self.pending = None
        self.stop = False

    def _connect(self):
        self.connection += 1
        self.boot = f"{self.boot_id}-{self.connection}"
        self.session_pings = 0
        self.frame, self.pending = None, None
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.id,
                             protocol=mqtt.MQTTv311, clean_session=True)
        client.on_message = self._on_message
        client.connect(HOST, PORT, keepalive=10)
        client.loop_start()
        for leaf in ("frame", "ack", "alive"):
            client.subscribe(PREFIX + self.id + "/" + leaf, qos=1)
        self.client = client
        now = time.monotonic()
        self.seen = self.ping = now
        self.next_press = now + PRESS_EVERY * (0.5 + random.random())
        self._publish("hello", {"device_code": self.code, "model": "NodeMCU ESP8266 16x2",
                                "firmware_version": FIRMWARE, "hardware_version": "server-16x2"})

    def _publish(self, leaf, body):
        body["boot"] = self.boot
        self.client.publish(PREFIX + self.id + "/" + leaf, json.dumps(body), qos=1, retain=False)

    def _on_message(self, client, userdata, message):
        if message.retain:
            return
        try:
            doc = json.loads(message.payload)
        except ValueError:
            return
        now = time.monotonic()
        if doc.get("boot") != self.boot:
            if message.topic.endswith("/alive"):
                with self.lock:
                    sent = self.stale.pop(doc.get("nonce"), None)
                    if sent is not None:
                        self.late.append(now - sent)
            return
        with self.lock:
            if message.topic.endswith("/alive"):
                sent = self.pings.pop(doc.get("nonce"), None)
                if sent is not None:
                    self.latencies.append((sent, now - sent))
                    self.seen = now
            elif message.topic.endswith("/ack"):
                if self.pending and doc.get("command_id") == self.pending[0]:
                    self.acks.append(now - self.pending[1])
                    self.pending = None
                if isinstance(doc.get("frame"), dict):
                    self.frame = doc["frame"]
                self.seen = now
            elif message.topic.endswith("/frame"):
                self.frames += 1
                if isinstance(doc.get("frame"), dict):
                    self.frame = doc["frame"]
                self.seen = now

    def _press(self, now):
        keys = (self.frame or {}).get("keys") or {}
        key = "C" if "C" in keys else next(iter(keys), None)
        if key is None:
            return
        self.sequence += 1
        command_id = f"{self.boot}-{self.sequence}"
        self.pending = (command_id, now)
        self._publish("command", {"command_id": command_id, "view_token": self.frame.get("view_token", ""), "key": key})

    def _die(self, reason):
        with self.lock:
            for nonce, sent in self.pings.items():
                self.latencies.append((sent, None))
                self.stale[nonce] = sent
            self.pings.clear()
            self.deaths.append((time.time(), self.boot, self.session_pings, reason))
        self.log(f"{self.id} DÖR  boot {self.boot} efter {self.session_pings} pingar ({reason})")
        self.client.disconnect(); self.client.loop_stop()
        time.sleep(1.0)       # firmware: nextConnection = current + 1000
        self._connect()

    def run(self):
        self._connect()
        while not self.stop:
            time.sleep(0.05)
            now = time.monotonic()
            with self.lock:
                silent = now - self.seen >= PATIENCE
                waited = now - self.pending[1] if self.pending else 0.0
            if silent:
                self._die("tystnad")
                continue
            if self.pending and FIRMWARE == "0.7.1" and waited >= ACK_DROPS_SESSION:
                self._die("kvitto uteblev 5 s")
                continue
            if self.pending and FIRMWARE != "0.7.1" and waited >= ACK_GIVE_UP:
                with self.lock:
                    self.pending = None
                    self.given_up += 1
            if PRESS_EVERY and not self.pending and self.frame and now >= self.next_press:
                self.next_press = now + PRESS_EVERY
                with self.lock:
                    self._press(now)
            if now - self.ping >= PING:
                self.ping = now
                self.sequence += 1
                nonce = f"{self.boot}-p{self.sequence}"
                with self.lock:
                    self.pings[nonce] = now
                    self.session_pings += 1
                self._publish("presence", {"nonce": nonce})
        self.client.disconnect(); self.client.loop_stop()
