"""Lokföraren: förarens tjänst, incheckningar och ikon (#225, #226).

Förare är inloggade användare med rollen ``user`` (eller en administratör som
också kör). Allt här är arbetsstöd för den som kör ett tåg: vilken tjänst hen
har, var hen säger att hen är och vilken ikon de andra ser. Ingenting här styr
trafik, klarering, tidtabell eller klocka, och ingenting här tolkar underlag –
förartjänsterna kommer färdiga i driftpaketet från TrainMeet Cloud
(``driver_duties``), eller är den uttryckligt märkta övningstjänsten DEMO 132.

En incheckning på en station syns i tio spelminuter och tonar sedan ut under
fem. En incheckning kan också vara ett läge utan station (``status``): paus,
eller att föraren inväntar start av sitt uppdrag. Den som har ett läge är inte
ute på banan, står inte på kartorna och tonar inte ut – läget gäller tills
föraren checkar in igen (som längst ett dygn).
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: En incheckning syns fullt så här länge …
CHECKIN_VISIBLE = timedelta(minutes=10)
#: … och tonar sedan ut under så här lång tid. Båda i spelminuter: på
#: träffklockan, så att en stoppad klocka håller kvar incheckningen och 4×
#: tonar ut den fyra gånger så fort.
CHECKIN_FADE = timedelta(minutes=5)
#: En incheckning gäller som längst ett dygn i verklig tid, vad klockan än gör.
CHECKIN_KEPT = timedelta(days=1)
#: Lägen utan station: föraren är inte ute på banan. "pause" är paus (chillar),
#: "waiting" att föraren inväntar start av sitt uppdrag.
CHECKIN_STATUSES = ("pause", "waiting")
#: En ikon är en liten bild som webbläsaren redan beskurit och skalat ned.
AVATAR_MAX_BYTES = 96 * 1024
AVATAR_TYPES = ("image/png", "image/jpeg", "image/webp")
NAME_MAX = 80
TRACK_MAX = 12

DEMO_ID = "demo-132"

#: Övningstjänsten ur issue #225. Fakta därifrån: tjänsten 17:10–21:02,
#: CargoNet, CN 02 CD312, vmax 80, G 5453 Spiten 17:30 → Kjøllstad 18:25 med
#: rundgång och skjutning i Tveiten, G 5456 Kjøllstad 19:35 → Spiten 20:42 med
#: rundgång i Tveiten. Tiderna i Tveiten är exempel; spår anges inte.
DEMO_DUTY: dict[str, Any] = {
    "id": DEMO_ID,
    "number": "132",
    "demo": True,
    "operator": "CargoNet",
    "vehicle": "CN 02 CD312",
    "vmax": 80,
    "start": "17:10",
    "end": "21:02",
    "trains": [
        {"number": "G 5453", "from": "Spiten", "to": "Kjøllstad", "vmax": 80},
        {"number": "G 5456", "from": "Kjøllstad", "to": "Spiten", "vmax": 80},
    ],
    "notes": ["Övningstjänst. Tiderna i Tveiten är exempel."],
    "events": [
        {"kind": "duty_start", "time": "17:10", "station": "Spiten", "notes": ["Förberedelse", "CN 02 CD312"]},
        {"kind": "departure", "time": "17:30", "station": "Spiten", "train": "G 5453"},
        {"kind": "stop", "arrival": "17:58", "departure": "18:14", "station": "Tveiten", "train": "G 5453",
         "notes": ["Rundgång", "Skjut vagnar"]},
        {"kind": "arrival", "time": "18:25", "station": "Kjøllstad", "train": "G 5453"},
        {"kind": "departure", "time": "19:35", "station": "Kjøllstad", "train": "G 5456"},
        {"kind": "stop", "arrival": "20:02", "departure": "20:14", "station": "Tveiten", "train": "G 5456",
         "notes": ["Rundgång"]},
        {"kind": "arrival", "time": "20:42", "station": "Spiten", "train": "G 5456"},
        {"kind": "duty_end", "time": "21:02", "station": "Spiten"},
    ],
}

EVENT_KINDS = {"duty_start", "duty_end", "departure", "arrival", "stop", "pass", "note"}
#: Anvisningarna i ett köruppdrag, som uppgifter Lokföraren skriver på
#: förarens språk (se lokforaren-koruppdrag.js och docs/LOKFORAREN.md).
INSTRUCTION_KINDS = {
    "crosses", "overtakes", "overtaken_by", "exchange_passengers", "no_exchange",
    "couple", "uncouple", "use", "from_parking", "to_parking", "lift_on", "lift_off",
    "fetch_from_track", "put_on_track", "shunt_wagons_to_departure", "shunt_wagons_to_arrival",
    "circulate", "turn", "turn_and_circulate", "push_wagons", "pick_up_key", "leave_key",
}
MEET_KINDS = {"crosses", "overtakes", "overtaken_by"}
INSTRUCTIONS_MAX = 12
_TIME = re.compile(r"^(\d{1,2}):([0-5]\d)$")


class DriverError(ValueError):
    """Något föraren skickade gick inte att använda. Bär HTTP-status och kod."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _time(value: Any) -> str | None:
    text = str(value or "").strip()
    match = _TIME.match(text)
    if not match or int(match.group(1)) > 47:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def initials(name: str) -> str:
    parts = [part for part in re.split(r"[\s\-]+", name.strip()) if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _when(raw: dict[str, Any]) -> dict[str, list[int]]:
    """Veckodagar (1 = måndag) eller pass som en anvisning gäller."""
    for key, highest in (("days", 7), ("sessions", 14)):
        values = raw.get(key)
        if isinstance(values, list):
            numbers = sorted({value for value in values if isinstance(value, int) and not isinstance(value, bool)
                              and 1 <= value <= highest})
            if numbers:
                return {key: numbers}
    return {}


def meet_seconds(clock: dict[str, Any] | None) -> float | None:
    """Träffklockans löpande sekunder (fortsätter över midnatt), eller
    klockslaget i sekunder när klockan inte har dem (en extern klocka)."""

    if not clock:
        return None
    elapsed = clock.get("elapsed_seconds")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
        return float(elapsed)
    return _clock_seconds(clock.get("time"))


def _clock_seconds(value: Any) -> float | None:
    match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$", str(value or ""))
    if not match:
        return None
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3] or 0)


def game_age(then_seconds: float | None, then_time: str | None, clock: dict[str, Any] | None) -> float | None:
    """Hur många spelsekunder sedan incheckningen, på träffklockan.

    Med löpande sekunder i båda ändar räknas skillnaden rakt av; annars
    klockslagen, närmaste vägen runt midnatt. Har klockan ställts tillbaka
    förbi incheckningen (tidsmaskinen, ett nytt försök) är den inte aktuell
    längre: None. En minut bakåt räknas som nyss (avrundning, synk).
    """

    if not clock:
        return None
    elapsed = clock.get("elapsed_seconds")
    if then_seconds is not None and isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
        difference = float(elapsed) - float(then_seconds)
    else:
        now, then = _clock_seconds(clock.get("time")), _clock_seconds(then_time)
        if now is None or then is None:
            return None
        difference = (now - then + 43200) % 86400 - 43200
    if difference < -60:
        return None
    return max(0.0, difference)


def normalize_instruction(raw: Any) -> dict[str, Any] | None:
    """En anvisning i rätt form, eller None. Okända sorter hoppas över."""

    if not isinstance(raw, dict) or raw.get("kind") not in INSTRUCTION_KINDS:
        return None
    instruction: dict[str, Any] = {"kind": raw["kind"]}
    vehicles = raw.get("vehicles")
    if isinstance(vehicles, list):
        names = [_text(name, NAME_MAX) for name in vehicles[:8] if _text(name, NAME_MAX)]
        if names:
            instruction["vehicles"] = names
    for key in ("track", "usage", "key", "place"):
        if _text(raw.get(key), NAME_MAX):
            instruction[key] = _text(raw.get(key), NAME_MAX)
    for flag in ("train", "wagonset"):
        if raw.get(flag) is True:
            instruction[flag] = True
    if raw["kind"] in MEET_KINDS:
        meets = []
        for meet in raw.get("meets") or []:
            if not isinstance(meet, dict) or not _text(meet.get("train"), NAME_MAX):
                continue
            start, end = _time(meet.get("from")), _time(meet.get("to"))
            if not start or not end:
                continue
            meets.append({"train": _text(meet.get("train"), NAME_MAX), "from": start, "to": end} | _when(meet))
        if not meets:
            return None
        instruction["meets"] = meets[:INSTRUCTIONS_MAX]
    return instruction | _when(raw)


def normalize_duty(raw: Any) -> dict[str, Any] | None:
    """En förartjänst ur paketet, eller None om den inte går att använda.

    Cloud är redaktör och har redan kontrollerat innehållet. Det här ser bara
    till att sidan får rätt form: kända händelser, giltiga klockslag och
    begränsade texter. En händelse som inte går att läsa hoppas över hellre
    än att hela tjänsten försvinner.
    """

    if not isinstance(raw, dict):
        return None
    number = _text(raw.get("number"), 16)
    if not number:
        return None
    events = []
    for item in raw.get("events") or []:
        if not isinstance(item, dict) or item.get("kind") not in EVENT_KINDS:
            continue
        event: dict[str, Any] = {"kind": item["kind"], "station": _text(item.get("station"), NAME_MAX)}
        for key in ("station_id", "track", "train"):
            if item.get(key):
                event[key] = _text(item.get(key), NAME_MAX)
        if item["kind"] == "stop":
            arrival, departure = _time(item.get("arrival")), _time(item.get("departure"))
            if not arrival or not departure:
                continue
            event.update(arrival=arrival, departure=departure)
        else:
            moment = _time(item.get("time"))
            if not moment:
                continue
            event["time"] = moment
        notes = [_text(note, 120) for note in (item.get("notes") or []) if _text(note, 120)]
        if notes:
            event["notes"] = notes[:6]
        instructions = [found for found in map(normalize_instruction, item.get("instructions") or []) if found]
        if instructions:
            event["instructions"] = instructions[:INSTRUCTIONS_MAX]
        events.append(event)
    if not events:
        return None
    duty = {
        "id": _text(raw.get("id"), 64) or f"duty-{number}",
        "number": number,
        "demo": bool(raw.get("demo")),
        "operator": _text(raw.get("operator"), NAME_MAX),
        "vehicle": _text(raw.get("vehicle"), NAME_MAX),
        "vmax": raw.get("vmax") if isinstance(raw.get("vmax"), int) else None,
        "start": _time(raw.get("start")) or _first_time(events),
        "end": _time(raw.get("end")) or _last_time(events),
        "trains": [
            {key: _text(train.get(key), NAME_MAX) for key in ("number", "from", "to")}
            | ({"vmax": train["vmax"]} if isinstance(train.get("vmax"), int) else {})
            for train in (raw.get("trains") or []) if isinstance(train, dict) and train.get("number")
        ],
        "notes": [_text(note, 200) for note in (raw.get("notes") or []) if _text(note, 200)][:6],
        "events": events,
    }
    return duty


def _first_time(events: list[dict[str, Any]]) -> str:
    first = events[0]
    return first.get("time") or first.get("arrival") or "00:00"


def _last_time(events: list[dict[str, Any]]) -> str:
    last = events[-1]
    return last.get("time") or last.get("departure") or "00:00"


def duty_summary(duty: dict[str, Any]) -> dict[str, Any]:
    stations: list[str] = []
    for event in duty["events"]:
        if event.get("station") and (not stations or stations[-1] != event["station"]):
            stations.append(event["station"])
    return {
        "id": duty["id"], "number": duty["number"], "demo": duty["demo"], "operator": duty["operator"],
        "vehicle": duty["vehicle"], "vmax": duty["vmax"], "start": duty["start"], "end": duty["end"],
        "trains": [train["number"] for train in duty["trains"]], "route": stations,
    }


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class DriverStore:
    """Förarnas tjänster, incheckningar och ikoner, i serverns databas.

    Tabellerna ligger i trainmeet.db och följer därmed med i säkerhets-
    kopiorna. Incheckningar och aktiva tjänster hör till en träff
    (``meet_key``) och blandas aldrig mellan träffar.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS driver_profiles (
                user_id TEXT PRIMARY KEY,
                visible INTEGER NOT NULL DEFAULT 1,
                avatar BLOB,
                avatar_type TEXT,
                avatar_version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            -- En förare har högst en aktiv tjänst per träff. Tjänsten sparas
            -- som den såg ut vid aktiveringen, så att en ny publicering inte
            -- tyst byter körplan under någon som kör.
            CREATE TABLE IF NOT EXISTS driver_active (
                user_id TEXT NOT NULL,
                meet_key TEXT NOT NULL,
                duty_id TEXT NOT NULL,
                duty_json TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, meet_key)
            );

            CREATE TABLE IF NOT EXISTS driver_checkins (
                checkin_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                meet_key TEXT NOT NULL,
                station TEXT NOT NULL,
                station_id TEXT,
                track TEXT,
                duty_number TEXT,
                train TEXT,
                meet_time TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS driver_checkins_by_meet ON driver_checkins(meet_key, created_at);
            """
        )
        # Träffklockans löpande sekunder vid incheckningen (över midnatt och
        # dygn), så att åldern räknas i spelminuter. Äldre rader har bara klockslaget.
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(driver_checkins)")}
        if "meet_seconds" not in columns:
            self._connection.execute("ALTER TABLE driver_checkins ADD COLUMN meet_seconds REAL")
        # Ett läge utan station (CHECKIN_STATUSES). Raden har då tom station.
        if "status" not in columns:
            self._connection.execute("ALTER TABLE driver_checkins ADD COLUMN status TEXT")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # ------------------------------------------------------------ profil

    def profile(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT visible, avatar_type, avatar_version FROM driver_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return {"visible": True, "avatar_version": 0, "has_avatar": False}
        return {"visible": bool(row[0]), "avatar_version": int(row[2]), "has_avatar": bool(row[1])}

    def _ensure_profile_locked(self, user_id: str) -> None:
        self._connection.execute(
            "INSERT INTO driver_profiles(user_id, updated_at) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
            (user_id, _iso(_now())),
        )

    def set_visible(self, user_id: str, visible: bool) -> dict[str, Any]:
        with self._lock:
            self._ensure_profile_locked(user_id)
            self._connection.execute(
                "UPDATE driver_profiles SET visible = ?, updated_at = ? WHERE user_id = ?",
                (1 if visible else 0, _iso(_now()), user_id),
            )
        return self.profile(user_id)

    def set_avatar(self, user_id: str, data_url: Any) -> dict[str, Any]:
        """Spara en ikon som webbläsaren redan beskurit till en liten bild.

        Servern tittar bara på filhuvudet för att veta vilken bildtyp den ska
        svara med; den läser aldrig ut något ur bilden. Webbläsaren har ritat
        om den på en canvas, så kamerans plats och annan metadata är redan
        borta.
        """

        text = str(data_url or "")
        match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$", text)
        if not match:
            raise DriverError(400, "invalid_avatar", "Ikonen måste vara en PNG-, JPEG- eller WebP-bild")
        try:
            data = base64.b64decode(match.group(2), validate=False)
        except (binascii.Error, ValueError):
            raise DriverError(400, "invalid_avatar", "Ikonen gick inte att läsa") from None
        if not data or len(data) > AVATAR_MAX_BYTES:
            raise DriverError(400, "avatar_too_large", "Ikonen är för stor. Välj bilden igen så görs den mindre.")
        kind = _sniff_image(data)
        if kind is None or kind not in AVATAR_TYPES:
            raise DriverError(400, "invalid_avatar", "Ikonen måste vara en PNG-, JPEG- eller WebP-bild")
        with self._lock:
            self._ensure_profile_locked(user_id)
            self._connection.execute(
                "UPDATE driver_profiles SET avatar = ?, avatar_type = ?, avatar_version = avatar_version + 1,"
                " updated_at = ? WHERE user_id = ?",
                (data, kind, _iso(_now()), user_id),
            )
        return self.profile(user_id)

    def remove_avatar(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_profile_locked(user_id)
            self._connection.execute(
                "UPDATE driver_profiles SET avatar = NULL, avatar_type = NULL, avatar_version = avatar_version + 1,"
                " updated_at = ? WHERE user_id = ?",
                (_iso(_now()), user_id),
            )
        return self.profile(user_id)

    def avatar(self, user_id: str) -> tuple[bytes, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT avatar, avatar_type FROM driver_profiles WHERE user_id = ? AND avatar IS NOT NULL", (user_id,)
            ).fetchone()
        return (bytes(row[0]), str(row[1])) if row else None

    def forget_user(self, user_id: str) -> None:
        """Ett borttaget konto lämnar ingen ikon, tjänst eller plats efter sig."""
        with self._lock:
            for table in ("driver_profiles", "driver_active", "driver_checkins"):
                self._connection.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

    # ------------------------------------------------------------ tjänst

    def active(self, user_id: str, meet_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT duty_json, activated_at FROM driver_active WHERE user_id = ? AND meet_key = ?",
                (user_id, meet_key),
            ).fetchone()
        if row is None:
            return None
        return {"duty": json.loads(row[0]), "activated_at": row[1]}

    def activate(self, user_id: str, meet_key: str, duty: dict[str, Any]) -> dict[str, Any]:
        now = _iso(_now())
        with self._lock:
            self._connection.execute(
                "INSERT INTO driver_active(user_id, meet_key, duty_id, duty_json, activated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id, meet_key) DO UPDATE SET duty_id = excluded.duty_id,"
                " duty_json = excluded.duty_json, activated_at = excluded.activated_at",
                (user_id, meet_key, duty["id"], json.dumps(duty, ensure_ascii=False), now),
            )
        return {"duty": copy.deepcopy(duty), "activated_at": now}

    def end(self, user_id: str, meet_key: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM driver_active WHERE user_id = ? AND meet_key = ?", (user_id, meet_key)
            )

    def active_drivers(self, meet_key: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT user_id, duty_json FROM driver_active WHERE meet_key = ?", (meet_key,)
            ).fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    # ------------------------------------------------------- incheckning

    def check_in(self, user_id: str, meet_key: str, payload: dict[str, Any], *,
                 duty: dict[str, Any] | None, meet_time: str | None, meet_seconds: float | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
        """Spara var föraren är: en station (med valfritt spår), eller ett
        läge utan station (``status``: paus eller inväntar start)."""

        status = payload.get("status") or None
        if status is not None and status not in CHECKIN_STATUSES:
            raise DriverError(400, "invalid_status", "Läget finns inte")
        station = "" if status else _text(payload.get("station"), NAME_MAX)
        if not station and not status:
            raise DriverError(400, "station_required", "Välj stationen du checkar in på")
        track = None if status else _text(payload.get("track"), TRACK_MAX) or None
        station_id = None if status else _text(payload.get("station_id"), NAME_MAX) or None
        train = None
        if duty and not status:
            # Tåget som stannar eller går där enligt tjänsten, om det finns ett.
            for event in duty.get("events", []):
                if event.get("station") == station and event.get("train"):
                    train = event["train"]
        moment = now or _now()
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO driver_checkins(user_id, meet_key, station, station_id, track, duty_number, train,"
                " meet_time, meet_seconds, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, meet_key, station, station_id, track, duty["number"] if duty else None, train,
                 meet_time, meet_seconds, _iso(moment), status),
            )
            # Bara den senaste platsen räknas. Äldre rader än en dag behövs inte.
            self._connection.execute(
                "DELETE FROM driver_checkins WHERE created_at < ?", (_iso(moment - CHECKIN_KEPT),)
            )
        return {"checkin_id": cursor.lastrowid, "station": station, "station_id": station_id, "track": track,
                "train": train, "meet_time": meet_time, "created_at": _iso(moment), "status": status}

    def latest_checkins(self, meet_key: str, *, clock: dict[str, Any] | None = None,
                        now: datetime | None = None) -> dict[str, dict[str, Any]]:
        """Varje förares senaste incheckning som fortfarande syns.

        ``age_seconds`` är i spelsekunder när träffklockan finns (``clock``,
        med ``elapsed_seconds`` eller ``time``), annars i verklig tid. En
        incheckning på en station syns i CHECKIN_VISIBLE + CHECKIN_FADE av den
        tiden. Ett läge (``status``) tonar inte ut: det gäller tills föraren
        checkar in igen, och en klocka som ställts tillbaka gör det inte osant.
        """
        moment = now or _now()
        since = _iso(moment - CHECKIN_KEPT)
        with self._lock:
            rows = self._connection.execute(
                "SELECT user_id, station, station_id, track, duty_number, train, meet_time, created_at, meet_seconds,"
                " status FROM driver_checkins WHERE meet_key = ? AND created_at >= ? ORDER BY created_at, checkin_id",
                (meet_key, since),
            ).fetchall()
        newest = {row[0]: row for row in rows}
        shown = (CHECKIN_VISIBLE + CHECKIN_FADE).total_seconds()
        on_the_clock = bool(clock) and clock.get("configured", True) is not False
        latest: dict[str, dict[str, Any]] = {}
        for user_id, row in newest.items():
            status = row[9] if row[9] in CHECKIN_STATUSES else None
            age = (game_age(row[8], row[6], clock) if on_the_clock
                   else max(0.0, (moment - _parse(row[7])).total_seconds()))
            if status and age is None:
                age = max(0.0, (moment - _parse(row[7])).total_seconds())
            if age is None or (age > shown and not status):
                continue
            latest[user_id] = {"station": row[1], "station_id": row[2], "track": row[3], "duty_number": row[4],
                               "train": row[5], "meet_time": row[6], "created_at": row[7], "age_seconds": round(age, 1),
                               "status": status}
        return latest
