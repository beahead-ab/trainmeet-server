"""Låtsasboxar som beter sig som firmware 0.7.1 (common/server_terminal.h).

Samma klient-id, clean session, keepalive 10 s, MQTT 3.1.1. hello vid anslutning,
presence var 5:e sekund, 15 s tålamod på alive/frame, ny boot vid återanslutning.
Mäter tiden från varje presence till dess alive.
"""
import json, os, random, sys, threading, time
import paho.mqtt.client as mqtt

HOST, PORT = "127.0.0.1", int(os.environ.get("MQTT_PORT", "18830"))
PREFIX = "tmbox/terminal/device/"
PATIENCE, PING = 15.0, 5.0


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
        self.deaths = []         # (time, boot, pings in session)
        self.frames = 0
        self.stop = False

    def _connect(self):
        self.connection += 1
        self.boot = f"{self.boot_id}-{self.connection}"
        self.session_pings = 0
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
        self._publish("hello", {"device_code": self.code, "model": "NodeMCU ESP8266 16x2",
                                "firmware_version": "0.7.1", "hardware_version": "server-16x2"})

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
        if doc.get("boot") != self.boot:
            return
        now = time.monotonic()
        with self.lock:
            if message.topic.endswith("/alive"):
                sent = self.pings.pop(doc.get("nonce"), None)
                if sent is not None:
                    self.latencies.append((sent, now - sent))
                    self.seen = now
            elif message.topic.endswith("/frame"):
                self.frames += 1
                self.seen = now

    def run(self):
        self._connect()
        while not self.stop:
            time.sleep(0.05)
            now = time.monotonic()
            with self.lock:
                dead = now - self.seen >= PATIENCE
                if dead:
                    for nonce, sent in self.pings.items():
                        self.latencies.append((sent, None))
                    self.pings.clear()
                    self.deaths.append((time.time(), self.boot, self.session_pings))
            if dead:
                self.log(f"{self.id} DÖR  boot {self.boot} efter {self.session_pings} pingar")
                self.client.disconnect(); self.client.loop_stop()
                time.sleep(1.0)       # firmware: nextConnection = current + 1000
                self._connect()
                continue
            if now - self.ping >= PING:
                self.ping = now
                self.sequence += 1
                nonce = f"{self.boot}-p{self.sequence}"
                with self.lock:
                    self.pings[nonce] = now
                    self.session_pings += 1
                self._publish("presence", {"nonce": nonce})
        self.client.disconnect(); self.client.loop_stop()


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    start = time.monotonic()
    def log(text):
        print(f"[{time.monotonic() - start:7.1f}s] {text}", flush=True)
    boxes = [Box("308398b5%04x" % (0x5263 + i * 0x0111), log) for i in range(count)]
    threads = [threading.Thread(target=b.run, daemon=True) for b in boxes]
    for t in threads:
        t.start(); time.sleep(0.2)
    time.sleep(duration)
    for b in boxes:
        b.stop = True
    for t in threads:
        t.join(timeout=5)
    answered = [lat for b in boxes for _, lat in b.latencies if lat is not None]
    lost = sum(1 for b in boxes for _, lat in b.latencies if lat is None)
    answered.sort()
    def pct(p):
        return answered[min(len(answered) - 1, int(p * len(answered)))] * 1000 if answered else float("nan")
    print(json.dumps({
        "boxes": count, "seconds": duration,
        "pings_answered": len(answered), "pings_unanswered": lost,
        "alive_ms": {"p50": round(pct(0.5), 1), "p95": round(pct(0.95), 1), "p99": round(pct(0.99), 1),
                     "max": round(max(answered) * 1000, 1) if answered else None},
        "session_deaths": sum(len(b.deaths) for b in boxes),
        "frames": sum(b.frames for b in boxes),
    }, indent=1))


if __name__ == "__main__":
    main()
