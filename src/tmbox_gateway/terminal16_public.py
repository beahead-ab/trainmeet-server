"""Bounded, cookie-isolated TMBox demo behind the existing HTTPS proxy.

Only disposable demo data; no accounts, files, MQTT, or production runtime.
The listener is always loopback. HTTPS origin and route prefix are explicit.
"""
import argparse
from collections import deque
from http.cookies import SimpleCookie, CookieError
from http.server import ThreadingHTTPServer
import re
import secrets
from threading import BoundedSemaphore, RLock
import time
from urllib.parse import urlsplit

from .terminal16_demo import Handler, LabState

COOKIE = "trainmeet_tmbox_lab"
MAX_AGE = 4 * 60 * 60
IDLE_TTL = 30 * 60


class TestSession(LabState):
    def __init__(self, now):
        super().__init__()
        self.created = self.touched = now
        self.streams = 0
        self.requests = deque()

    def snapshot(self):
        state = super().snapshot()
        state["text"]["subtitle"] = "Din egen provbänk · endast testdata · ingen koppling till riktiga träffar"
        state["text"]["session"] = "Den här webbläsaren har en egen testsession i upp till fyra timmar. Nollställning påverkar bara ditt test. Efter utgång eller omstart: ladda om sidan för ett nytt test."
        return state


class SessionStore:
    def __init__(self, *, limit=24, now=time.monotonic):
        self.sessions = {}
        self.lock = RLock()
        self.limit = limit
        self.now = now
        self.created = deque()
        self.streams = 0

    def _alive(self, session, now):
        return now - session.created < MAX_AGE and now - session.touched < IDLE_TTL

    def acquire(self, token, *, create=False):
        with self.lock:
            now = self.now()
            for key, session in list(self.sessions.items()):
                if not self._alive(session, now):
                    del self.sessions[key]
            if token in self.sessions:
                session = self.sessions[token]
                session.touched = now
                return token, session, False
            if not create:
                return None, None, False
            while self.created and now - self.created[0] >= 60:
                self.created.popleft()
            if len(self.sessions) >= self.limit or len(self.created) >= self.limit:
                return None, None, False
            token = secrets.token_urlsafe(32)
            session = TestSession(now)
            self.sessions[token] = session
            self.created.append(now)
            return token, session, True

    def touch(self, token, session):
        with self.lock:
            now = self.now()
            if self.sessions.get(token) is not session or not self._alive(session, now):
                return False
            session.touched = now
            return True

    def allow_command(self, session):
        with self.lock:
            now = self.now()
            while session.requests and now - session.requests[0] >= 10:
                session.requests.popleft()
            if len(session.requests) >= 30:
                return False
            session.requests.append(now)
            return True

    def open_stream(self, session):
        with self.lock:
            # Reserve HTTP workers for commands even when many tabs remain open.
            if session.streams >= 2 or self.streams >= 32:
                return False
            session.streams += 1
            self.streams += 1
            return True

    def close_stream(self, session):
        with self.lock:
            session.streams -= 1
            self.streams -= 1


class PublicLabServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, *, origin, prefix="/tmbox-lab/", sessions=None):
        parsed = urlsplit(origin)
        if (address[0] != "127.0.0.1" or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Loopback listener and an explicit HTTPS origin are required")
        if not re.fullmatch(r"/[a-z0-9-]+/", prefix):
            raise ValueError("An explicit single-segment path prefix is required")
        self.origin, self.host, self.prefix = origin, parsed.netloc, prefix
        self.sessions = sessions if sessions is not None else SessionStore()
        self.workers = BoundedSemaphore(48)
        super().__init__(address, PublicHandler)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 10\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()


class PublicHandler(Handler):
    server_version = "TrainMeetLab"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)
        self.session = None
        self.session_token = None
        self.cookie_value = None

    @property
    def context(self):
        return self.session

    def extra_headers(self):
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if self.cookie_value:
            self.send_header("Set-Cookie", self.cookie_value)

    def _local(self, *, mutation=False):
        if self.headers.get("Host") != self.server.host:
            return False
        origin = self.headers.get("Origin")
        if mutation:
            return origin == self.server.origin and self.headers.get("Sec-Fetch-Site") not in {"cross-site", "same-site"}
        return origin in (None, self.server.origin)

    def _prepare(self, *, mutation=False):
        if not self._local(mutation=mutation):
            self._send(403, {"message": "Ogiltigt ursprung"})
            return False
        path = urlsplit(self.path).path
        if not path.startswith(self.server.prefix):
            self._send(404, {"message": "Finns inte"})
            return False
        self.path = "/" + path[len(self.server.prefix):]
        if self.path in {"/style.css", "/terminal.js", "/healthz"} and not mutation:
            return True
        if self.path not in {"/", "/events", "/api/state", "/api/key", "/api/reset", "/api/reset-devices"}:
            self._send(404, {"message": "Finns inte"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and self.path != "/":
            self._send(403, {"message": "Ogiltigt ursprung"})
            return False
        cookie = SimpleCookie()
        raw = self.headers.get("Cookie", "")
        try:
            if len(raw) > 8192:
                raise CookieError()
            cookie.load(raw)
        except CookieError:
            self._send(400, {"message": "Ogiltig testcookie"})
            return False
        token = cookie[COOKIE].value if COOKIE in cookie else None
        create = self.path == "/" and not mutation
        token, session, created = self.server.sessions.acquire(token, create=create)
        if session is None:
            self._send(503 if create else 401, {"message": "Provbänken är full. Försök senare." if create else "Testet har gått ut. Ladda om sidan."})
            return False
        self.session, self.session_token = session, token
        if created:
            secure = "; Secure" if self.server.origin.startswith("https://") else ""
            self.cookie_value = f"{COOKIE}={token}; Path={self.server.prefix}; Max-Age={MAX_AGE}{secure}; HttpOnly; SameSite=Strict"
        if mutation and not self.server.sessions.allow_command(session):
            self._send(429, {"message": "För många knapptryck. Vänta en stund."})
            return False
        return True

    def stream_active(self):
        return self.server.sessions.touch(self.session_token, self.session)

    def do_GET(self):
        if not self._prepare():
            return
        if self.path == "/healthz":
            return self._send(200, {"status": "ok", "service": "tmbox-lab", "profile": "server-16x2-pilot"})
        stream = self.path == "/events"
        if stream and not self.server.sessions.open_stream(self.session):
            return self._send(429, {"message": "Stäng en annan provbänksflik och försök igen."})
        try:
            super().do_GET()
        finally:
            if stream:
                self.server.sessions.close_stream(self.session)

    def do_POST(self):
        if self._prepare(mutation=True):
            super().do_POST()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8797)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--prefix", default="/tmbox-lab/")
    args = parser.parse_args()
    server = PublicLabServer(("127.0.0.1", args.port), origin=args.origin, prefix=args.prefix)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
