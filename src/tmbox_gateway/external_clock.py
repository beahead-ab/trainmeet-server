"""Server-owned FastClock adapter. Clients never contact the provider directly.

The wire contract is the one used by Lovable's useFastClock/useMeetClock.
Only configured HTTPS FastClock-compatible origins are supported; no redirects
or credentials from Cloud. Read errors never fall back to a local clock.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

PROVIDER_URL = "https://fastclock.azurewebsites.net"
DEFAULT_SETTINGS = {"source": "internal", "clock_name": "", "url": PROVIDER_URL, "user": "", "password": "", "poll_interval": 2}

# An address here identifies an HTTPS API origin, not an arbitrary remote resource.
# Local/internal targets and URL credentials are rejected to reduce SSRF risk.
def normalize_provider_url(value):
    if not isinstance(value, str) or not value or len(value) > 255 or any(ord(c) < 33 for c in value):
        raise FastClockError("Ange en giltig HTTPS-adress till FastClock.")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise FastClockError("Ogiltig FastClock-adress.") from None
    if (parts.scheme != "https" or not host or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path not in ("", "/")
            or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
        raise FastClockError("Ange en publik HTTPS-basadress utan sökväg, inloggning eller parametrar.")
    try:
        port = parts.port
    except ValueError:
        raise FastClockError("Ogiltig port i FastClock-adressen.") from None
    if port is not None and not (1 <= port <= 65535):
        raise FastClockError("Ogiltig port i FastClock-adressen.")
    try:
        address = ip_address(host)
    except ValueError:
        pass  # Public HTTPS DNS names may be configured by the administrator.
    else:
        if not address.is_global:
            raise FastClockError("FastClock-adressen får inte peka på ett lokalt eller privat nät.")
    return value.rstrip("/")

_WEEKDAYS = {
    "monday": "Mån", "mon": "Mån", "mån": "Mån", "måndag": "Mån",
    "tuesday": "Tis", "tue": "Tis", "tues": "Tis", "tis": "Tis", "tisdag": "Tis",
    "wednesday": "Ons", "wed": "Ons", "ons": "Ons", "onsdag": "Ons",
    "thursday": "Tor", "thu": "Tor", "thur": "Tor", "tors": "Tor", "tor": "Tor", "torsdag": "Tor",
    "friday": "Fre", "fri": "Fre", "fre": "Fre", "fredag": "Fre",
    "saturday": "Lör", "sat": "Lör", "lör": "Lör", "lördag": "Lör",
    "sunday": "Sön", "sun": "Sön", "sön": "Sön", "söndag": "Sön",
}


class FastClockError(ValueError):
    pass


# FastClock fills its reason fields even when there is nothing to say: a
# running clock reports pauseReason "None" and stoppingReason "NoReason" or
# "SelectStopReason". Those are placeholders, not text for a screen.
_PLACEHOLDERS = frozenset({"", "none", "noreason", "selectstopreason", "null", "undefined", "na", "unknown"})


def provider_text(value):
    """The provider's text with whitespace collapsed, or "" for a placeholder."""
    text = " ".join(str(value or "").split())[:160]
    return "" if re.sub(r"[\s_./-]", "", text).casefold() in _PLACEHOLDERS else text


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provider_request(settings, action="time", reason=""):
    """Bounded HTTPS request; errors intentionally exclude URL/body/secrets."""
    url = normalize_provider_url(settings.get("url") or PROVIDER_URL) + "/api/clocks/" + quote(settings["clock_name"], safe="") + "/" + action
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
    result["url"] = normalize_provider_url(payload.get("url", result["url"]))
    for field, limit in (("clock_name", 100), ("user", 100), ("password", 256)):
        if field in payload:
            value = payload[field]
            if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
                raise FastClockError("Klocknamn eller inloggningsuppgifter har ogiltigt format.")
            result[field] = value if field == "password" else value.strip()
    # Never forward an old clock's password to another named clock/user.
    if "password" not in payload and any(result[k] != previous.get(k, DEFAULT_SETTINGS.get(k, "")) for k in ("clock_name", "user", "url")):
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
    paused, completed = data.get("isPaused", False), data.get("isCompleted", False)
    running = data["isRunning"] and not paused and not completed
    # A reason belongs to the state it explains: pauseReason to a pause,
    # stoppingReason to a stop. A running or completed clock has none.
    reason = ""
    if paused:
        reason = provider_text(data.get("pauseReason"))
    elif not running and not completed:
        reason = provider_text(data.get("stoppingReason"))
    # message is the provider's free-text field and nothing else. It used to
    # fall back to the reasons, which put "NoReason" under a running clock.
    message = provider_text(data.get("message")) or None
    weekday = data.get("weekday")
    return {"seconds": parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0),
            "speed": speed, "running": running, "stopped_reason": reason if not running else None,
            "message": message, "completed": bool(completed),
            "minute_precision": len(parts) == 2,
            "explicit_zero_seconds": len(parts) == 3 and parts[2] == 0,
            "weekday": _WEEKDAYS.get(weekday.strip().casefold(), "") if isinstance(weekday, str) else ""}


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
        self.last_seen = 0.0
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
            self.received, self.last_seen = 0.0, 0.0
            self.next_poll = 0
            if sample is not None:
                self._accept(sample)

    def _accept(self, sample):
        now = self.now()
        previous = self.sample
        # HH:MM:00 on every poll is also a minute-only source, but a
        # legitimate exact :00 reading on a seconds-capable source must not
        # be mistaken for coarse precision. Detect repeated zero-second
        # readings and retain the original monotonic anchor.
        if (sample.get("explicit_zero_seconds") and previous
                and sample["running"] and previous["running"]
                and sample["speed"] == previous["speed"]
                and int(sample["seconds"] // 60) == int(previous["seconds"] // 60)
                and (previous.get("explicit_zero_seconds") or previous.get("minute_precision"))):
            sample["minute_precision"] = True
            previous["minute_precision"] = True
        elif (sample.get("explicit_zero_seconds") and previous
                and previous.get("minute_precision") and sample["running"]
                and previous["running"] and sample["speed"] == previous["speed"]):
            sample["minute_precision"] = True
        # Some FastClock instances return HH:MM only. Polling the same value every
        # two seconds must not reset the clock to :00 and make the hands bounce.
        # Retain the original monotonic anchor until the provider changes minute.
        if (previous and sample["running"] and previous["running"] and sample["speed"] == previous["speed"]
                and ((sample.get("minute_precision") and previous.get("minute_precision")
                      and int(sample["seconds"] // 60) == int(previous["seconds"] // 60))
                     or (not sample.get("minute_precision") and not previous.get("minute_precision")
                         and sample["seconds"] == previous["seconds"]))):
            self.error = ""
            self.last_seen = now
            # Repeated time samples keep their interpolation anchor, while
            # live text and weekday still follow the newest provider response.
            self.sample = {**previous, "message": sample.get("message"), "completed": sample.get("completed", False),
                           "stopped_reason": sample["stopped_reason"], "weekday": sample.get("weekday", "")}
        else:
            if (sample.get("minute_precision") and previous and previous.get("minute_precision")
                    and sample["running"] and previous["running"] and sample["speed"] == previous["speed"]
                    and int(sample["seconds"] // 60) != int(previous["seconds"] // 60) and self.last_seen):
                # The minute changed between the last and current successful
                # polls. Estimate the provider's minute boundary at their
                # midpoint. That removes the persistent one-minute lag without
                # pretending that HH:MM carries an exact second value.
                seconds_after_boundary = min(59.0, max(0.0, (now - self.last_seen) * sample["speed"] / 2.0))
                sample = {**sample, "seconds": sample["seconds"] + seconds_after_boundary}
            self.sample, self.received, self.error = sample, now, ""
            self.last_seen = now
        self.last_sync = datetime.now(timezone.utc).isoformat()
        self.next_poll = now + self.settings["poll_interval"]

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
            stale = self.now() - self.last_seen > max(6, self.settings["poll_interval"] * 3)
            available = bool(sample and not self.error and not stale)
            seconds = sample["seconds"] if sample else 0
            if sample and available and sample["running"]:
                seconds += max(0, self.now() - self.received) * sample["speed"]
                # A HH:MM-only provider is sampled at discrete minute
                # boundaries. Keep the hands advancing at its reported speed
                # rather than clamping at :59 and jumping back every poll.
            whole = int(seconds) % 86400
            return {"configured": True, "source": "fastclock", "external_name": self.settings["clock_name"],
                    "time": f"{whole // 3600:02}:{whole % 3600 // 60:02}:{whole % 60:02}" if sample else "--:--:--",
                    "seconds": seconds, "speed": sample["speed"] if sample else 1,
                    "running": bool(available and sample["running"]), "available": available,
                    "can_control": bool(self.settings["user"]), "last_sync": self.last_sync,
                    "message": sample.get("message") if available else None,
                    "completed": bool(available and sample.get("completed")),
                    "stopped_reason": (sample["stopped_reason"] if available else
                        self.error or "Väntar på FastClock. Senast mottagna tid visas."),
                    "weekday": sample.get("weekday", "") if sample else "",
                    "time_precision": "minute" if sample and sample.get("minute_precision") else "second",
                    "scope": self.scope.split(":", 1)[0]}

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
