"""Server-owned FastClock adapter. Clients never contact the provider directly.

The wire contract is the one used by Lovable's useFastClock/useMeetClock.
Only the named provider is supported; no arbitrary URL/redirect or credentials
from Cloud are accepted. Read errors never fall back to a running local clock.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

PROVIDER_URL = "https://fastclock.azurewebsites.net"
DEFAULT_SETTINGS = {"source": "internal", "clock_name": "", "user": "", "password": "", "poll_interval": 2}


class FastClockError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provider_request(settings, action="time", reason=""):
    """Bounded HTTPS request; errors intentionally exclude URL/body/secrets."""
    url = PROVIDER_URL + "/api/clocks/" + quote(settings["clock_name"], safe="") + "/" + action
    if action != "time":
        params = {"user": settings["user"]}
        if settings["password"]:
            params["password"] = settings["password"]
        if action == "stop":
            params["reason"] = reason
        url += "?" + urlencode(params)
    request = Request(url, method="GET" if action == "time" else "PUT", headers={"Accept": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=4) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversize")
            return json.loads(raw) if action == "time" else None
    except Exception:
        if action == "time":
            raise FastClockError("FastClock kunde inte läsas. Kontrollera klocknamn och internetanslutning.") from None
        raise FastClockError("FastClock bekräftade inte kommandot. Kontrollera klockans status och inloggningsuppgifter innan du försöker igen.") from None


def validate_settings(payload, previous):
    if not isinstance(payload, dict) or payload.get("source") not in {"internal", "fastclock"}:
        raise FastClockError("Välj intern klocka eller FastClock.")
    result = {**DEFAULT_SETTINGS, **previous, "source": payload["source"]}
    for field, limit in (("clock_name", 100), ("user", 100), ("password", 256)):
        if field in payload:
            value = payload[field]
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
                raise FastClockError("Klocknamn eller inloggningsuppgifter har ogiltigt format.")
            result[field] = value if field == "password" else value.strip()
    # Never forward an old clock's password to another named clock/user.
    if "password" not in payload and any(result[k] != previous.get(k, "") for k in ("clock_name", "user")):
        result["password"] = ""
    interval = payload.get("poll_interval", result["poll_interval"])
    if type(interval) is not int or not 2 <= interval <= 30:
        raise FastClockError("Hämtningsintervallet måste vara 2–30 sekunder.")
    result["poll_interval"] = interval
    if result["source"] == "fastclock" and not result["clock_name"]:
        raise FastClockError("Ange namnet på FastClock-klockan.")
    return result


def parse_status(data):
    if not isinstance(data, dict) or data.get("isUnavailable") is True:
        raise FastClockError("FastClock är inte tillgänglig.")
    clock = data.get("time")
    speed = data.get("speed")
    if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", clock):
        raise FastClockError("FastClock returnerade ett ogiltigt klockslag.")
    if type(data.get("isRunning")) is not bool or type(speed) not in (int, float) or not math.isfinite(speed) or not 0 < speed <= 600:
        raise FastClockError("FastClock returnerade ogiltig hastighet eller status.")
    for field in ("isPaused", "isCompleted", "isUnavailable"):
        if field in data and type(data[field]) is not bool:
            raise FastClockError("FastClock returnerade ogiltig status.")
    parts = list(map(int, clock.split(":")))
    running = data["isRunning"] and not data.get("isPaused", False) and not data.get("isCompleted", False)
    reason = data.get("pauseReason") or data.get("stoppingReason") or data.get("message") or ""
    return {"seconds": parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0),
            "speed": speed, "running": running, "stopped_reason": str(reason)[:160] if not running else None,
            "weekday": str(data.get("weekday") or "")[:40]}


class ExternalClock:
    def __init__(self, transport=provider_request, monotonic=time.monotonic):
        self.transport, self.now = transport, monotonic
        self.lock = threading.RLock()
        self.io_lock = threading.Lock()
        self.scope = ""
        self.settings = dict(DEFAULT_SETTINGS)
        self.version = 0
        self.sample = None
        self.received = 0.0
        self.last_sync = None
        self.error = ""
        self.next_poll = 0.0

    def configure(self, scope, settings, sample=None):
        with self.lock:
            if scope == self.scope and settings == self.settings and sample is None:
                return
            self.scope, self.settings = scope, dict(settings)
            self.version += 1
            self.sample, self.error, self.last_sync = None, "", None
            self.next_poll = 0
            if sample is not None:
                self._accept(sample)

    def _accept(self, sample):
        self.sample, self.received, self.error = sample, self.now(), ""
        self.last_sync = datetime.now(timezone.utc).isoformat()
        self.next_poll = self.now() + self.settings["poll_interval"]

    def probe(self, settings):
        return parse_status(self.transport(settings))

    def poll(self, force=False):
        # Exactly one provider read regardless of the number of connected clients.
        if not self.io_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                if self.settings["source"] != "fastclock" or (not force and self.now() < self.next_poll):
                    return False
                settings, version = dict(self.settings), self.version
                self.next_poll = self.now() + settings["poll_interval"]
            try:
                sample, error = self.probe(settings), ""
            except FastClockError as failure:
                sample, error = None, str(failure)
            with self.lock:
                if version != self.version:
                    return False  # A late reply must not enter another meet/source.
                if error:
                    self.error = error
                else:
                    self._accept(sample)
                return True
        finally:
            self.io_lock.release()

    def status(self, region=None):
        with self.lock:
            if self.settings["source"] != "fastclock" or (region and not self.scope.startswith(region + ":")):
                return None
            sample = self.sample
            stale = self.now() - self.received > max(6, self.settings["poll_interval"] * 3)
            available = bool(sample and not self.error and not stale)
            seconds = sample["seconds"] if sample else 0
            if sample and available and sample["running"]:
                seconds += max(0, self.now() - self.received) * sample["speed"]
            whole = int(seconds) % 86400
            return {"configured": True, "source": "fastclock", "external_name": self.settings["clock_name"],
                    "time": f"{whole // 3600:02}:{whole % 3600 // 60:02}:{whole % 60:02}" if sample else "--:--:--",
                    "seconds": seconds, "speed": sample["speed"] if sample else 1,
                    "running": bool(available and sample["running"]), "available": available,
                    "can_control": bool(self.settings["user"]), "last_sync": self.last_sync,
                    "stopped_reason": (sample["stopped_reason"] if available else
                        self.error or "Väntar på FastClock. Senast mottagna tid visas."),
                    "weekday": sample.get("weekday", "") if sample else "", "scope": self.scope.split(":", 1)[0]}

    def control(self, action, reason=""):
        if action not in {"start", "stop"}:
            raise FastClockError("Tid och hastighet ändras i FastClock när extern klocka används.")
        with self.io_lock:
            with self.lock:
                settings = dict(self.settings)
                if settings["source"] != "fastclock" or not settings["user"]:
                    raise FastClockError("Ange FastClock-användare under klockans anslutning för att starta eller stoppa.")
            try:
                self.transport(settings, action, str(reason)[:160])
            except FastClockError:
                with self.lock:
                    self.error = "FastClock bekräftade inte kommandot. Kontrollera klockans status."
                raise
            # Do not optimistically mark the shared clock as started/stopped.
            try:
                sample = self.probe(settings)
            except FastClockError:
                with self.lock:
                    self.error = "Kommandot skickades men klockans status kunde inte bekräftas."
                raise FastClockError(self.error) from None
            with self.lock:
                self._accept(sample)
