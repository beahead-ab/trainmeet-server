"""Kör låtsasboxar + tilldelning + simulering + webbläsarpollning, mät allt."""
import json, os, sys, threading, time, urllib.error, urllib.request
from collections import defaultdict
from fakebox import Box

HTTP = "http://127.0.0.1:" + os.environ.get("HTTP_PORT", "18787")
STATIONS = ["st-cda", "st-lek", "st-vst", "st-kun"]

# Takten avläst ur Bennys journal 22:29:27-22:29:56 (per webbläsare).
POLL = {"/v1/display": 2.0, "/v1/display/connection": 13.0, "/v1/simulation": 2.5,
        "/v1/devices": 13.0, "/v1/clock": 11.0, "/v1/clock/source": 10.0,
        "/v1/server-context": 13.0, "/v1/cloud/presentation": 12.0, "/v1/info": 13.0,
        "/v1/admin/access": 13.0, "/v1/runtime": 13.0}


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(HTTP + path, data=data, method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw[:1] in (b"{", b"[") else raw


class Browser:
    def __init__(self, name):
        self.name, self.stop = name, False
        self.latency = defaultdict(list)
        self.errors = defaultdict(int)

    def run(self):
        due = {path: time.monotonic() + i * 0.3 for i, path in enumerate(POLL)}
        while not self.stop:
            now = time.monotonic()
            for path, every in POLL.items():
                if now >= due[path]:
                    due[path] = now + every
                    started = time.monotonic()
                    try:
                        call(path)
                        self.latency[path].append(time.monotonic() - started)
                    except Exception as error:
                        self.errors[f"{path}: {type(error).__name__} {getattr(error, 'code', '')}"] += 1
            time.sleep(0.05)


def main():
    count, duration = int(sys.argv[1]), float(sys.argv[2])
    speed = float(os.environ.get("SIM_SPEED", "1"))
    browsers_on = os.environ.get("BROWSERS", "2") != "0"
    start = time.monotonic()
    def log(text):
        print(f"[{time.monotonic() - start:7.1f}s] {text}", flush=True)
    boxes = [Box("308398b5%04x" % (0x5263 + i * 0x0111), log) for i in range(count)]
    threads = [threading.Thread(target=b.run, daemon=True) for b in boxes]
    for t in threads:
        t.start(); time.sleep(0.2)
    time.sleep(2)
    for index, box in enumerate(boxes):
        try:
            call("/v1/devices/assign", {"device_code": box.code, "station_id": STATIONS[index % len(STATIONS)]})
        except urllib.error.HTTPError as error:
            if error.code != 409:
                raise
    log(f"{count} boxar tilldelade")
    if speed > 0:
        status = call("/v1/simulation")
        if not status.get("active"):
          call("/v1/simulation", {"action": "start", "confirmed": True, "profile": "timetable", "time": "09:00",
                                "speed": speed, "meet_generation": status["meet_generation"]})
        log(f"simulering startad, hastighet {speed}")
    browsers = [Browser(n) for n in ("kiosk", "admin")] if browsers_on else []
    for b in browsers:
        threading.Thread(target=b.run, daemon=True).start()
    time.sleep(duration)
    for b in boxes + browsers:
        b.stop = True
    time.sleep(1)
    answered = sorted(lat for b in boxes for _, lat in b.latencies if lat is not None)
    lost = sum(1 for b in boxes for _, lat in b.latencies if lat is None)
    def pct(values, p):
        return round(values[min(len(values) - 1, int(p * len(values)))] * 1000, 1) if values else None
    http = {}
    for b in browsers:
        for path, values in b.latency.items():
            http.setdefault(path, []).extend(values)
    report = {
        "boxes": count, "seconds": duration, "sim_speed": speed, "browsers": len(browsers),
        "pings_answered": len(answered), "pings_unanswered": lost,
        "alive_ms": {"p50": pct(answered, .5), "p95": pct(answered, .95), "p99": pct(answered, .99),
                     "max": round(answered[-1] * 1000, 1) if answered else None},
        "session_deaths": sum(len(b.deaths) for b in boxes),
        "frames": sum(b.frames for b in boxes),
        "late_answers": len([x for b in boxes for x in b.late]),
        "late_answer_s": {"min": round(min([x for b in boxes for x in b.late] or [0]), 1),
                          "max": round(max([x for b in boxes for x in b.late] or [0]), 1)},
        "http_ms": {p: {"n": len(v), "p50": pct(sorted(v), .5), "max": round(max(v) * 1000, 1)} for p, v in sorted(http.items())},
        "http_errors": {k: v for b in browsers for k, v in b.errors.items()},
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
