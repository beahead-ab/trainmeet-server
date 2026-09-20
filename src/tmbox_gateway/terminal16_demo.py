"""Loopback-only, disposable TMBox 16x2 lab. No MQTT or production endpoints.

Run: PYTHONPATH=src python -m tmbox_gateway.terminal16_demo --port 8796
"""
import argparse
from copy import deepcopy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Condition
import time
from urllib.parse import urlsplit

from .engine import TrafficEngine
from .models import ConnectionConfig, DispatchMode, SessionConfig, StationConfig, TrackConfig
from .terminal16 import Terminal16Lab
from .terminal16_glyphs import language_samples


def demo_lab(mode="clearance"):
    stations = {
        "mun": StationConfig("mun", "MUN", "Munkeröd"),
        "cda": StationConfig("cda", "CDA", "Charlottendal"),
        "va": StationConfig("va", "VA", "Vagnsta"),
    }
    connections = {"west": ConnectionConfig("west", "mun", "cda"),
                   "east": ConnectionConfig("east", "cda", "va")}
    tracks = {f"{station}-{n}": TrackConfig(f"{station}-{n}", str(n), station, sort_order=n)
              for station in stations for n in (1, 2)}
    engine = TrafficEngine(SessionConfig("isolated-16x2", "TMBox 16×2 – separat test",
                                        DispatchMode(mode), stations, connections, {}, tracks, "12:34"))
    started = time.monotonic()
    def clock():
        minute = (12 * 60 + 34 + int(time.monotonic() - started) // 60) % (24 * 60)
        return {"configured": True, "running": True, "time": f"{minute // 60:02d}:{minute % 60:02d}"}
    engine.set_clock_source(clock)
    package = {"publication_id": "isolated-16x2", "meet": {"active_day": "Dagl", "default_dispatch_mode": mode},
               "stations": [{"id": s.id, "code": s.code, "name": s.name} for s in stations.values()],
               "connections": [{"id": c.id, "station_a_id": c.station_a_id, "station_b_id": c.station_b_id,
                                "track_type": "single", "display_side_a": "right", "display_side_b": "left"}
                               for c in connections.values()], "services": [], "trains": []}
    for number, origin, destination, departure, arrival in (
            ("17", "cda", "mun", "12:35", "12:42"),
            ("39", "cda", "va", "12:38", "12:46"),
            ("93", "mun", "cda", "12:32", "12:40"),
            ("94", "va", "cda", "12:44", "12:52")):
        stops = []
        for order, station in enumerate((origin, destination)):
            stop = {"station_id": station, "stop_order": order,
                    "arrival_time": arrival if order else None, "departure_time": None if order else departure}
            stops.append(stop)
            package["trains"].append({**stop, "id": f"{number}-{station}", "service_id": number,
                                      "train_number": number, "days": "Dagl", "track_id": f"{station}-1"})
        package["services"].append({"id": number, "train_number": number, "days": "Dagl", "stops": stops})
    return Terminal16Lab(engine, package, {"DEMO-MUN": "mun", "DEMO-CDA": "cda", "DEMO-VA": "va"})


class LabState:
    """One in-memory test, independent of its HTTP transport."""
    def __init__(self, mode="clearance"):
        self.lab = demo_lab(mode)
        self.mode = mode
        self.changed = Condition()

    def reset_devices(self):
        """Clear all disposable runtime state, retaining configuration and clock."""
        with self.changed:
            previous = self.lab
            with previous.lock:
                engine = TrafficEngine(deepcopy(previous.engine.config))
                engine.set_clock_source(previous.engine.clock_source)
                assignments = {device: terminal.station for device, terminal in previous.terminals.items()}
                self.lab = Terminal16Lab(engine, previous.publication, assignments)
            self.changed.notify_all()
            return self.snapshot()

    def snapshot(self):
        with self.changed:
            lab = self.lab
            with lab.lock:
                return {"frames": lab.frames(), "mode": self.mode,
                        "timetables": {device: lab.timetable(device) for device in lab.terminals},
                        "language_samples": language_samples(lab.engine.meeting_clock()["time"]),
                        "audit": lab.engine.audit[-10:], "arrivals": lab.arrivals,
                        "text": {"title": "TMBox · 16 × 2", "subtitle": "Isolerad provkörning · ingen koppling till er träff",
                                 "entry": "Siffrorna stannar här tills du trycker #.",
                                 "offline": "Testservern är frånkopplad. Trafikknapparna är spärrade.",
                                 "ready": "Serverstyrd display och knappar", "sending": "Inväntar servern…"}}


class LabServer(ThreadingHTTPServer, LabState):
    daemon_threads = True

    def __init__(self, address, mode="clearance"):
        LabState.__init__(self, mode)
        ThreadingHTTPServer.__init__(self, address, Handler)


class Handler(BaseHTTPRequestHandler):
    @property
    def context(self):
        return self.server

    def extra_headers(self):
        pass

    def stream_active(self):
        return True

    def log_message(self, *_):
        pass

    def _local(self, *, mutation=False):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            return False
        if self.headers.get("Origin") not in (None, f"http://{expected}"):
            return False
        return not mutation or self.headers.get("Sec-Fetch-Site") != "cross-site"

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.extra_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._local():
            return self._send(403, {"message": "Endast lokal testmiljö"})
        path = urlsplit(self.path).path
        if path == "/api/state":
            return self._send(200, self.context.snapshot())
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            previous = None
            try:
                while self.stream_active():
                    current = json.dumps(self.context.snapshot(), ensure_ascii=False)
                    if current != previous:
                        self.wfile.write(f"data: {current}\n\n".encode())
                        previous = current
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    with self.context.changed:
                        self.context.changed.wait(timeout=1)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/terminal.js": ("terminal.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in files:
            filename, content_type = files[path]
            return self._send(200, (Path(__file__).parent / "terminal16_web" / filename).read_bytes(), content_type)
        return self._send(404, {"message": "Finns inte"})

    def do_POST(self):
        if not self._local(mutation=True):
            return self._send(403, {"message": "Endast lokal testmiljö"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError()
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            return self._send(400, {"message": "Ogiltigt meddelande"})
        with self.context.changed:
            if self.path == "/api/reset-devices":
                if body:
                    return self._send(400, {"message": "Nollställningen tar inga inställningar"})
                return self._send(200, self.context.reset_devices())
            if self.path == "/api/reset":
                mode = body.get("mode")
                if mode not in {"clearance", "direct"}:
                    return self._send(400, {"message": "Ogiltigt testläge"})
                self.context.lab, self.context.mode = demo_lab(mode), mode
                self.context.changed.notify_all()
                return self._send(200, self.context.snapshot())
            if self.path == "/api/key":
                device = body.get("device_id")
                if not isinstance(device, str) or device not in self.context.lab.terminals:
                    return self._send(404, {"message": "Okänd testbox"})
                result = self.context.lab.command(device, body)
                # Bounded debug history for long-running disposable test sessions.
                del self.context.lab.engine.audit[:-100]
                self.context.changed.notify_all()
                return self._send(200 if result["status"] == "accepted" else 409, result)
        return self._send(404, {"message": "Finns inte"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8796)
    args = parser.parse_args()
    server = LabServer(("127.0.0.1", args.port))
    print(f"Isolerad TMBox-provbank: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
