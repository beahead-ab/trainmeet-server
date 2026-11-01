"""Lokföraren (#225, #226): förarkonton, tjänster, incheckningar och ikoner.

En förare (rollen user) är inloggad men ingen administratör. Hen kommer åt
/v1/driver/* och Lokföraren-appen, aldrig ett admin-API. Förartjänsterna kommer
i Clouds paket (driver_duties) eller är övningstjänsten DEMO 132. En
incheckning syns tio minuter i verklig tid och tonar sedan ut på fem.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.central_sync import CentralSyncError
from tmbox_gateway.driver import DEMO_DUTY, DriverError, DriverStore, game_age, meet_seconds, normalize_duty
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import AdminAccessError, IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublicationError, SQLiteRuntimeStore

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def data_url(kind: str, data: bytes) -> str:
    return f"data:{kind};base64," + base64.b64encode(data).decode()


class DriverRoleInIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "trainmeet.db"

    def test_an_older_database_gets_the_driver_role_without_losing_anyone(self) -> None:
        """Förut tillät CHECK bara owner och admin. Ombyggnaden behåller alla konton."""
        old = sqlite3.connect(self.path)
        old.executescript(
            """
            CREATE TABLE admin_users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt BLOB,
                password_digest BLOB,
                role TEXT NOT NULL CHECK(role IN ('owner', 'admin')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                setup_code_digest BLOB,
                setup_expires_at TEXT
            );
            INSERT INTO admin_users(user_id, username, role, created_at, updated_at)
            VALUES ('u1', 'u1', 'owner', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00');
            """
        )
        old.close()
        store = IdentityStore(self.path)
        self.addCleanup(store.close)
        self.assertEqual(["u1"], [user["user_id"] for user in store.list_admin_users()])
        driver = store.invite_admin_user("Benny", "benny@example.se", "user")
        self.assertEqual("user", driver["role"])
        sql = store._connection.execute("SELECT sql FROM sqlite_master WHERE name = 'admin_users'").fetchone()[0]
        self.assertIn("'user'", sql)
        # En andra öppning bygger inte om något.
        again = IdentityStore(self.path)
        self.addCleanup(again.close)
        self.assertEqual(2, len(again.list_admin_users()))

    def test_unknown_roles_are_still_refused(self) -> None:
        store = IdentityStore(self.path)
        self.addCleanup(store.close)
        with self.assertRaises(AdminAccessError):
            store.invite_admin_user("Någon", "nagon@example.se", "superuser")

    def test_owners_come_first_then_administrators_then_drivers(self) -> None:
        store = IdentityStore(self.path)
        self.addCleanup(store.close)
        store.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
        store.invite_admin_user("Anna", "anna@example.se", "user")
        store.invite_admin_user("Bo", "bo@example.se", "admin")
        self.assertEqual(["owner", "admin", "user"], [user["role"] for user in store.list_admin_users()])


class DutyShapeTests(unittest.TestCase):
    def test_the_demo_duty_has_the_facts_from_the_issue(self) -> None:
        duty = normalize_duty(DEMO_DUTY)
        self.assertEqual(("132", "17:10", "21:02", "CargoNet"), (duty["number"], duty["start"], duty["end"], duty["operator"]))
        self.assertEqual(["G 5453", "G 5456"], [train["number"] for train in duty["trains"]])
        self.assertTrue(duty["demo"])

    def test_unreadable_events_are_skipped_not_the_whole_duty(self) -> None:
        duty = normalize_duty({"number": "9", "events": [
            {"kind": "departure", "time": "25:99", "station": "A"},
            {"kind": "teleport", "time": "10:00", "station": "B"},
            {"kind": "stop", "arrival": "10:05", "station": "C"},
            {"kind": "arrival", "time": "10:20", "station": "D", "notes": ["", "Koppla loss"]},
        ]})
        self.assertEqual([("arrival", "D")], [(event["kind"], event["station"]) for event in duty["events"]])
        self.assertEqual(["Koppla loss"], duty["events"][0]["notes"])
        self.assertIsNone(normalize_duty({"number": "9", "events": []}))
        self.assertIsNone(normalize_duty({"events": [{"kind": "arrival", "time": "10:00"}]}))


class InstructionShapeTests(unittest.TestCase):
    def test_instructions_keep_known_kinds_valid_times_and_days(self) -> None:
        from tmbox_gateway.driver import normalize_duty

        duty = normalize_duty({"number": "39", "events": [{
            "kind": "stop", "arrival": "13:41", "departure": "13:44", "station": "Steinsnes",
            "instructions": [
                {"kind": "crosses", "meets": [{"train": "NSB L 410", "from": "13:41", "to": "13:43", "days": [2, 4]},
                                              {"train": "Utan tid", "from": "x", "to": "13:00"}]},
                {"kind": "couple", "vehicles": ["NSB 02 Di4", ""], "days": [2, 4, 6, 9, True]},
                {"kind": "fetch_from_track", "train": True, "track": "18", "usage": "Opstilling", "wagonset": "ja"},
                {"kind": "pick_up_key", "key": "K16", "place": "Hammarlind"},
                {"kind": "crosses", "meets": []},
                {"kind": "script", "vehicles": ["<b>"]},
                "inte en anvisning",
            ],
        }]})
        self.assertEqual([
            {"kind": "crosses", "meets": [{"train": "NSB L 410", "from": "13:41", "to": "13:43", "days": [2, 4]}]},
            {"kind": "couple", "vehicles": ["NSB 02 Di4"], "days": [2, 4, 6]},
            {"kind": "fetch_from_track", "track": "18", "usage": "Opstilling", "train": True},
            {"kind": "pick_up_key", "key": "K16", "place": "Hammarlind"},
        ], duty["events"][0]["instructions"])


class DriverStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = DriverStore(Path(directory.name) / "trainmeet.db")
        self.addCleanup(self.store.close)

    def test_without_a_meet_clock_a_check_in_is_seen_for_fifteen_minutes_of_real_time(self) -> None:
        start = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
        self.store.check_in("u1", "meet", {"station": "Tveiten"}, duty=None, meet_time="17:58:00", now=start)
        self.assertIn("u1", self.store.latest_checkins("meet", now=start + timedelta(minutes=14, seconds=59)))
        self.assertNotIn("u1", self.store.latest_checkins("meet", now=start + timedelta(minutes=15, seconds=1)))
        self.assertNotIn("u1", self.store.latest_checkins("another-meet", now=start))

    def test_on_the_meet_clock_a_check_in_lasts_fifteen_game_minutes(self) -> None:
        """Spelminuter (Casper 2026-11-01): en stoppad klocka håller kvar
        incheckningen, och vid 4× är den borta efter knappt fyra riktiga minuter."""

        start = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
        clock = lambda seconds: {"configured": True, "elapsed_seconds": seconds,
                                 "time": f"{int(seconds // 3600) % 24:02d}:{int(seconds // 60) % 60:02d}:00"}
        self.store.check_in("u1", "meet", {"station": "Tveiten"}, duty=None, meet_time="23:58:00",
                            meet_seconds=86280, now=start)
        # Klockan står: tre riktiga timmar senare syns incheckningen fortfarande, nyss.
        latest = self.store.latest_checkins("meet", clock=clock(86280), now=start + timedelta(hours=3))
        self.assertEqual(0.0, latest["u1"]["age_seconds"])
        # Över midnatt: 00:12 nästa dygn är fjorton spelminuter senare.
        self.assertEqual(840.0, self.store.latest_checkins("meet", clock=clock(87120), now=start)["u1"]["age_seconds"])
        self.assertNotIn("u1", self.store.latest_checkins("meet", clock=clock(86280 + 901), now=start))
        # Klockan ställd tillbaka förbi incheckningen: inte aktuell längre.
        self.assertNotIn("u1", self.store.latest_checkins("meet", clock=clock(80000), now=start))
        # Ett dygn i verklig tid är gränsen, vad klockan än gör.
        self.assertNotIn("u1", self.store.latest_checkins("meet", clock=clock(86280), now=start + timedelta(days=1, minutes=1)))

    def test_game_age_from_the_clock_time_when_the_seconds_are_missing(self) -> None:
        clock = {"configured": True, "time": "00:04:00"}  # en extern klocka har bara klockslaget
        self.assertEqual(480.0, game_age(None, "23:56:00", clock))
        self.assertEqual(0.0, game_age(None, "00:04:30", clock))  # en halv minut fram: nyss
        self.assertIsNone(game_age(None, "00:10:00", clock))
        self.assertIsNone(game_age(None, None, clock))
        self.assertEqual(86280.0, meet_seconds({"time": "23:58:00"}))
        self.assertEqual(90000.5, meet_seconds({"elapsed_seconds": 90000.5, "time": "01:00:00"}))

    def test_only_the_latest_place_counts(self) -> None:
        start = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
        duty = normalize_duty(DEMO_DUTY)
        self.store.check_in("u1", "meet", {"station": "Spiten"}, duty=duty, meet_time=None, now=start)
        self.store.check_in("u1", "meet", {"station": "Tveiten", "track": "1"}, duty=duty, meet_time=None,
                            now=start + timedelta(minutes=3))
        latest = self.store.latest_checkins("meet", now=start + timedelta(minutes=4))["u1"]
        self.assertEqual(("Tveiten", "1", "132", "G 5456", 60.0), (
            latest["station"], latest["track"], latest["duty_number"], latest["train"], latest["age_seconds"]))

    def test_a_station_is_required(self) -> None:
        with self.assertRaises(DriverError):
            self.store.check_in("u1", "meet", {"station": "  "}, duty=None, meet_time=None)

    def test_an_icon_is_a_small_png_jpeg_or_webp_and_nothing_else(self) -> None:
        self.assertTrue(self.store.set_avatar("u1", data_url("image/png", PNG))["has_avatar"])
        self.assertEqual((PNG, "image/png"), self.store.avatar("u1"))
        self.assertEqual("image/jpeg", self.store.avatar("u1") and self.store.set_avatar("u1", data_url("image/jpeg", JPEG)) and self.store.avatar("u1")[1])
        for bad in (data_url("image/png", b"<svg onload=alert(1)>"), "data:image/svg+xml;base64,PHN2Zz4=",
                    "https://example.se/a.png", data_url("image/png", PNG + b"\x00" * 200_000)):
            with self.assertRaises(DriverError):
                self.store.set_avatar("u1", bad)
        self.assertEqual(0, self.store.remove_avatar("u1")["has_avatar"])
        self.assertIsNone(self.store.avatar("u1"))


class LokforarenHTTPTests(unittest.TestCase):
    """Hela vägen genom HTTP, med riktiga sessioner och en riktig databas."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "trainmeet.db"
        self.identities = IdentityStore(database)
        self.runtime = SQLiteRuntimeStore(database)
        self.operations = SQLiteOperationsStore(database)
        for store in (self.operations, self.runtime, self.identities):
            self.addCleanup(store.close)
        self.identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
        for name in ("Anna", "Benny", "Maja"):
            role = "admin" if name == "Anna" else "user"
            invited = self.identities.invite_admin_user(name, f"{name.lower()}@example.se", role)
            self.identities.redeem_admin_setup(f"{name.lower()}@example.se", str(invited["setup_code"]), "ett-losenord-till")
        self.runtime.complete_installation()
        package = runtime_package_v3()
        package["driver_duties"] = [{
            "id": "duty-7", "number": "7", "events": [
                {"kind": "departure", "time": "17:55", "station": package["stations"][0]["name"], "train": "P 101"},
                {"kind": "arrival", "time": "18:20", "station": package["stations"][-1]["name"], "train": "P 101"},
            ],
        }, {"number": "", "events": []}]
        publication = self.runtime.install(package)
        self.operations.ensure_publication(publication)
        self.operations.start_clock(time_value="17:51:00", now=datetime.now(timezone.utc))
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.app = TrainMeetHTTPApplication(
            engine, self.identities, PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True, state_dir=directory.name),
            runtime_store=self.runtime, operations_store=self.operations,
        )
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def request(self, path: str, body: dict | None = None, *, cookie: str | None = None,
                headers: dict | None = None) -> tuple[int, dict, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                          headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {}), **(headers or {})})
        try:
            with urlopen(request) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if response.headers.get_content_type() == "application/json" else {"raw": raw, "type": response.headers.get_content_type()}), dict(response.headers)
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}"), dict(error.headers)

    def login(self, name: str) -> str:
        password = "ett-langt-losenord" if name == "casper" else "ett-losenord-till"
        status, payload, headers = self.request("/v1/auth/login", {"email": f"{name}@example.se", "password": password})
        self.assertEqual(200, status, payload)
        cookie = SimpleCookie()
        cookie.load(headers["Set-Cookie"])
        return "; ".join(f"{key}={morsel.value}" for key, morsel in cookie.items())

    def test_a_driver_signs_in_and_is_never_an_administrator(self) -> None:
        status, payload, headers = self.request("/v1/auth/login", {"email": "benny@example.se", "password": "ett-losenord-till"})
        self.assertEqual("user", payload["role"])
        benny = self.login("benny")
        for path in ("/v1/admin/users", "/v1/clock", "/v1/server-context", "/v1/runtime", "/v1/devices"):
            self.assertEqual(403, self.request(path, cookie=benny)[0], path)
        self.assertEqual(403, self.request("/v1/clock", {"action": "stop"}, cookie=benny)[0])
        self.assertEqual(403, self.request("/v1/admin/users", {"display_name": "X", "email": "x@example.se", "role": "owner"}, cookie=benny)[0])
        status = self.request("/v1/auth/status", cookie=benny)[1]
        self.assertEqual((True, "user"), (status["authenticated"], status["account_role"]))

    def test_the_driver_api_needs_a_session(self) -> None:
        for path in ("/v1/driver/session", "/v1/driver/clock", "/v1/driver/duties", "/v1/driver/presence"):
            self.assertEqual(401, self.request(path)[0], path)
        self.assertEqual(401, self.request("/v1/driver/checkins", {"station": "X"})[0])

    def test_choose_activate_and_check_in(self) -> None:
        benny, maja = self.login("benny"), self.login("maja")
        session = self.request("/v1/driver/session", cookie=benny)[1]
        self.assertEqual(("Benny", "user", None), (session["user"]["name"], session["user"]["role"], session["active"]))
        self.assertTrue(session["clock"]["running"])
        self.assertEqual(["7", "132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        self.assertEqual(["132"], [duty["number"] for duty in self.request("/v1/driver/duties?q=13", cookie=benny)[1]["duties"]])
        self.assertEqual(404, self.request("/v1/driver/active", {"duty_id": "nope"}, cookie=benny)[0])
        active = self.request("/v1/driver/active", {"duty_id": "demo-132"}, cookie=benny)[1]["active"]
        self.assertEqual("132", active["duty"]["number"])
        checkin = self.request("/v1/driver/checkins", {"station": "Tveiten", "track": "1"}, cookie=benny)[1]["checkin"]
        self.assertEqual(("Tveiten", "1", "G 5456"), (checkin["station"], checkin["track"], checkin["train"]))
        seen = self.request("/v1/driver/presence", cookie=maja)[1]
        benny_row = next(driver for driver in seen["drivers"] if driver["name"] == "Benny")
        self.assertEqual(("132", "Tveiten", False), (benny_row["duty_number"], benny_row["checkin"]["station"], benny_row["me"]))
        self.assertEqual((600, 300), (seen["checkin_visible_seconds"], seen["checkin_fade_seconds"]))
        # Synligheten av: Maja ser inte Benny, Benny ser sig själv.
        self.request("/v1/driver/profile", {"visible": False}, cookie=benny)
        self.assertNotIn("Benny", [driver["name"] for driver in self.request("/v1/driver/presence", cookie=maja)[1]["drivers"]])
        self.assertIn("Benny", [driver["name"] for driver in self.request("/v1/driver/presence", cookie=benny)[1]["drivers"]])
        self.request("/v1/driver/active/end", {}, cookie=benny)
        self.assertIsNone(self.request("/v1/driver/session", cookie=benny)[1]["active"])

    def test_the_maps_show_the_drivers_who_want_to_be_seen(self) -> None:
        """/v1/display (Drift, storskärmarna, deltagarnas sida) har förarna som
        checkat in och valt att synas. Ikonen är öppen bara för dem, bara medan
        de står på kartan."""

        benny, maja = self.login("benny"), self.login("maja")
        station = runtime_package_v3()["stations"][0]
        self.request("/v1/driver/avatar", {"image": data_url("image/png", PNG)}, cookie=benny)
        self.request("/v1/driver/avatar", {"image": data_url("image/png", PNG)}, cookie=maja)
        self.assertEqual([], self.request("/v1/display")[1]["drivers"])
        self.request("/v1/driver/active", {"duty_id": "demo-132"}, cookie=benny)
        self.request("/v1/driver/checkins", {"station": station["name"], "station_id": station["id"]}, cookie=benny)
        drivers = self.request("/v1/display")[1]["drivers"]
        self.assertEqual([("Benny", "BE", station["id"], "132")],
                         [(d["name"], d["initials"], d["station_id"], d["duty_number"]) for d in drivers])
        self.assertLess(drivers[0]["age_seconds"], 60)
        status, payload, headers = self.request(drivers[0]["avatar_url"])  # utan inloggning
        self.assertEqual((200, PNG, "private, max-age=300"), (status, payload["raw"], headers["Cache-Control"]))
        maja_id = next(user["user_id"] for user in self.identities.list_admin_users() if user["display_name"] == "Maja")
        self.assertEqual(404, self.request(f"/v1/display/driver-avatars/{maja_id}?v=1")[0])  # inte incheckad
        # Synligheten av: borta från kartorna, och ikonen lämnas inte ut.
        self.request("/v1/driver/profile", {"visible": False}, cookie=benny)
        self.assertEqual([], self.request("/v1/display")[1]["drivers"])
        self.assertEqual(404, self.request(drivers[0]["avatar_url"])[0])

    def test_what_moves_a_driver_on_the_map_is_announced(self) -> None:
        """Banan i Drift hämtar förarna igen när /v1/events säger "drivers":
        en tjänst, en incheckning, synligheten och ikonen. Bara namnet går ut."""

        benny = self.login("benny")
        for path, body in (("/v1/driver/active", {"duty_id": "demo-132"}),
                           ("/v1/driver/checkins", {"station": "Tveiten"}),
                           ("/v1/driver/profile", {"visible": False}),
                           ("/v1/driver/avatar", {"image": data_url("image/png", PNG)}),
                           ("/v1/driver/avatar/remove", {}),
                           ("/v1/account/avatar", {"image": data_url("image/png", PNG)}),
                           ("/v1/driver/active/end", {})):
            with self.subTest(path=path):
                since = self.app.changes.seq
                self.assertEqual(200, self.request(path, body, cookie=benny)[0])
                self.assertIn("drivers", self.app.changes.wait(since, 0)[1])

    def test_icons_are_served_to_drivers_only(self) -> None:
        benny, maja = self.login("benny"), self.login("maja")
        user = self.request("/v1/driver/avatar", {"image": data_url("image/png", PNG)}, cookie=benny)[1]["user"]
        self.assertTrue(user["avatar_url"].startswith(f"/v1/driver/avatars/{user['user_id']}?v="))
        status, payload, headers = self.request(user["avatar_url"], cookie=maja)
        self.assertEqual((200, PNG, "image/png"), (status, payload["raw"], payload["type"]))
        self.assertEqual(401, self.request(user["avatar_url"])[0])
        self.assertEqual(400, self.request("/v1/driver/avatar", {"image": "data:text/html;base64,PGI+"}, cookie=benny)[0])

    def test_the_icon_is_the_same_in_lokforaren_and_in_the_account(self) -> None:
        anna, benny, maja = self.login("anna"), self.login("benny"), self.login("maja")
        # Lagd i Lokföraren: syns i användarlistan och i det egna kontot.
        driver = self.request("/v1/driver/avatar", {"image": data_url("image/png", PNG)}, cookie=benny)[1]["user"]
        listed = {user["display_name"]: user for user in self.request("/v1/admin/users", cookie=anna)[1]["users"]}
        self.assertTrue(listed["Benny"]["avatar_url"].startswith(f"/v1/account/avatars/{driver['user_id']}?v="))
        self.assertIsNone(listed["Anna"]["avatar_url"])
        self.assertEqual("AN", listed["Anna"]["initials"])
        own = self.request("/v1/account", cookie=benny)[1]["user"]
        self.assertEqual(listed["Benny"]["avatar_url"], own["avatar_url"])
        status, payload, _ = self.request(own["avatar_url"], cookie=benny)
        self.assertEqual((200, PNG), (status, payload["raw"]))
        # Administratören ser allas ikoner, en annan förare inte här.
        self.assertEqual(200, self.request(own["avatar_url"], cookie=anna)[0])
        self.assertEqual(404, self.request(own["avatar_url"], cookie=maja)[0])
        self.assertEqual(401, self.request(own["avatar_url"])[0])
        # Byt i kontot: Lokföraren får samma, med ny version.
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
        changed = self.request("/v1/account/avatar", {"image": data_url("image/jpeg", jpeg)}, cookie=benny)[1]["user"]
        self.assertNotEqual(own["avatar_url"], changed["avatar_url"])
        session = self.request("/v1/driver/session", cookie=benny)[1]["user"]
        status, payload, _ = self.request(session["avatar_url"], cookie=maja)
        self.assertEqual((200, jpeg, "image/jpeg"), (status, payload["raw"], payload["type"]))
        self.assertEqual(400, self.request("/v1/account/avatar", {"image": "data:text/html;base64,PGI+"}, cookie=benny)[0])
        self.assertEqual(403, self.request("/v1/account/avatar", {"image": data_url("image/png", PNG)}, cookie=benny,
                                           headers={"Origin": "https://evil.example"})[0])
        # Ta bort i kontot: borta i Lokföraren också.
        removed = self.request("/v1/account/avatar/remove", {}, cookie=benny)[1]["user"]
        self.assertIsNone(removed["avatar_url"])
        self.assertIsNone(self.request("/v1/driver/session", cookie=benny)[1]["user"]["avatar_url"])
        # Administratören byter sin egen på samma sätt.
        self.assertTrue(self.request("/v1/account/avatar", {"image": data_url("image/png", PNG)}, cookie=anna)[1]["user"]["avatar_url"])

    def test_a_driver_post_from_another_site_is_refused(self) -> None:
        benny = self.login("benny")
        status = self.request("/v1/driver/checkins", {"station": "Tveiten"}, cookie=benny,
                              headers={"Origin": "https://evil.example"})[0]
        self.assertEqual(403, status)

    def test_an_administrator_manages_nonowners_and_removes_driver_profile(self) -> None:
        anna = self.login("anna")
        status, payload, _ = self.request("/v1/admin/users", {"display_name": "Erik", "email": "erik@example.se", "role": "user"}, cookie=anna)
        self.assertEqual(201, status, payload)
        self.assertEqual(201, self.request("/v1/admin/users", {"display_name": "Olle", "email": "olle@example.se", "role": "admin"}, cookie=anna)[0])
        erik = payload["user"]["user_id"]
        # Administrators may assign admin/user, never owner.
        self.assertEqual(200, self.request("/v1/admin/users/update", {"user_id": erik, "role": "admin"}, cookie=anna)[0])
        self.assertEqual(403, self.request("/v1/admin/users/update", {"user_id": erik, "role": "owner"}, cookie=anna)[0])
        self.assertEqual(200, self.request("/v1/admin/users/update", {"user_id": erik, "display_name": "Erik E"}, cookie=anna)[0])
        benny = next(user for user in self.identities.list_admin_users() if user["display_name"] == "Benny")
        self.app.drivers.set_avatar(benny["user_id"], data_url("image/png", PNG))
        self.assertEqual(200, self.request("/v1/admin/users/delete", {"user_id": benny["user_id"]}, cookie=anna)[0])
        self.assertIsNone(self.app.drivers.avatar(benny["user_id"]))
        casper = next(user for user in self.identities.list_admin_users() if user["role"] == "owner")
        self.assertEqual(403, self.request("/v1/admin/users/delete", {"user_id": casper["user_id"]}, cookie=anna)[0])

    def test_the_owner_can_make_a_driver_an_administrator(self) -> None:
        casper = self.login("casper")
        maja = next(user for user in self.identities.list_admin_users() if user["display_name"] == "Maja")
        self.assertEqual(200, self.request("/v1/admin/users/update", {"user_id": maja["user_id"], "role": "admin"}, cookie=casper)[0])
        self.assertEqual("admin", self.identities.admin_user(maja["user_id"])["role"])

    def test_the_app_is_served_under_its_own_path(self) -> None:
        opener = build_opener(type("NoRedirect", (HTTPRedirectHandler,), {"redirect_request": lambda *args: None})())
        with self.assertRaises(HTTPError) as caught:
            opener.open(self.base + "/lokforaren")
        self.assertEqual((308, "/lokforaren/"), (caught.exception.code, caught.exception.headers["Location"]))
        expected = {"/lokforaren/": "text/html", "/lokforaren/app.js": "text/javascript", "/lokforaren/korplan.js": "text/javascript",
                    "/lokforaren/messages.js": "text/javascript", "/lokforaren/style.css": "text/css",
                    "/lokforaren/sw.js": "text/javascript", "/lokforaren/manifest.webmanifest": "application/manifest+json"}
        for path, kind in expected.items():
            status, payload, headers = self.request(path)
            self.assertEqual((200, kind), (status, payload["type"]), path)
            self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    # ── Egen app och cachebust ─────────────────────────────────────────

    def test_every_file_address_carries_the_build_so_phones_get_new_versions(self) -> None:
        html = self.request("/lokforaren/")[1]["raw"].decode()
        build = re.search(r'data-build="([0-9a-f]{12})"', html).group(1)
        self.assertNotIn("__LF_BUILD__", html)
        for asset in ("/lokforaren/app.js", "/lokforaren/style.css", "/lokforaren/manifest.webmanifest", "/assets/clock-sync.js"):
            self.assertIn(f"{asset}?v={build}", html)
        sw = self.request("/lokforaren/sw.js")[1]["raw"].decode()
        self.assertIn(f'const BUILD = "{build}"', sw)
        benny = self.login("benny")
        self.assertEqual(build, self.request("/v1/driver/session", cookie=benny)[1]["build"])
        self.assertEqual(build, self.request("/v1/driver/clock", cookie=benny)[1]["build"])
        manifest = json.loads(self.request("/lokforaren/manifest.webmanifest")[1]["raw"])
        self.assertEqual(("/lokforaren/", "Lokföraren", "/lokforaren/"), (manifest["id"], manifest["name"], manifest["scope"]))
        for icon in manifest["icons"]:
            status, payload, _ = self.request(icon["src"])
            self.assertEqual(200, status, icon["src"])
        self.assertEqual("image/png", self.request("/lokforaren/ikon-180.png")[1]["type"])

    def test_the_way_to_lokforaren_is_on_the_public_page_the_login_and_in_settings(self) -> None:
        html = self.request("/")[1]["raw"].decode()
        self.assertIn('id="pv-lokforaren" href="/lokforaren/"', html)
        self.assertIn('id="login-lokforaren" class="login-lokforaren" href="/lokforaren/"', html)
        self.assertIn('id="header-lokforare" href="/installningar#lokforare"', html)
        self.assertIn('data-section="lokforare"', html)
        self.assertEqual(200, self.request("/assets/lokforare-admin.js")[0])

    # ── Köruppdragen från Cloud ────────────────────────────────────────
    #
    # En träff är en träff: köruppdragen läggs in och görs tillgängliga i
    # Cloud och kommer med träffens paket (driver_duties). Varje tjänsts egna
    # sidor är en PDF-del som servern hämtar med sin serverkoppling.

    PART = b"%PDF-1.7\n" + b"3" * 900 + b"\n%%EOF\n"
    PART_SHA = hashlib.sha256(PART).hexdigest()

    def cloud_duty(self, number: str, **extra) -> dict:
        stations = runtime_package_v3()["stations"]
        return {"id": f"duty-{number}", "number": number, "operator": "NSB", "trains": [{"number": f"RE {number}0"}],
                "events": [{"kind": "departure", "time": "13:30", "station": stations[0]["name"], "train": f"RE {number}0"},
                           {"kind": "arrival", "time": "13:55", "station": stations[-1]["name"], "train": f"RE {number}0"}],
                **extra}

    def install_cloud(self, duties: list[dict], *, publication_id: str = "publication-2026-08-11-b",
                      drivers_only: bool = False) -> None:
        package = runtime_package_v3(publication_id=publication_id)
        package["driver_duties"] = duties
        if drivers_only:
            for key in ("stations", "connections", "trains", "panels", "routes", "services", "tracks", "autonomous_links"):
                package[key] = []
            package["display"]["graph_station_order"] = []
        publication = self.runtime.install(package)
        self.operations.ensure_publication(publication)

    def link_cloud(self) -> list[tuple[str, str, str]]:
        """Kopplar servern till Cloud och byter hämtningen mot en som svarar
        med PART. Returnerar anropen."""
        self.runtime.save_central_url("https://cloud.example/config")
        self.runtime.save_link_token("server-link-token")
        calls: list[tuple[str, str, str]] = []

        def fetch(token: str, url: str, sha: str) -> bytes:
            calls.append((token, url, sha))
            return self.PART

        self.app.driver_part_fetcher = fetch
        return calls

    def test_the_duty_sheets_come_from_cloud_with_each_duty_s_own_pages(self) -> None:
        anna, benny = self.login("anna"), self.login("benny")
        calls = self.link_cloud()
        self.install_cloud([self.cloud_duty("81", source={"pages": [5, 6], "part": {"sha256": self.PART_SHA, "size": len(self.PART)}}),
                            self.cloud_duty("82", source={"pages": [7, 7]}),
                            self.cloud_duty("83", source={"part": {"sha256": "inte-en-summa"}})])
        duties = self.request("/v1/driver/duties", cookie=benny)[1]["duties"]
        self.assertEqual(["81", "82", "83", "132"], [duty["number"] for duty in duties])
        found = self.request("/v1/driver/duties/duty-81", cookie=benny)[1]["duty"]
        self.assertEqual({"pages": [5, 6], "part": True, "cloud": self.PART_SHA}, found["source"])
        # Bara sidor utan PDF-del, eller en del som inte är en innehållsadress: ingen förlaga.
        self.assertNotIn("source", self.request("/v1/driver/duties/duty-82", cookie=benny)[1]["duty"])
        self.assertNotIn("source", self.request("/v1/driver/duties/duty-83", cookie=benny)[1]["duty"])

        # Föraren öppnar sina sidor: servern hämtar dem en gång och har dem sedan.
        status, original, headers = self.request("/v1/driver/duties/duty-81.pdf", cookie=benny)
        self.assertEqual((200, "application/pdf", self.PART), (status, original["type"], original["raw"]))
        self.assertIn('filename="Tjanst-81.pdf"', headers["Content-Disposition"])
        self.assertEqual([("server-link-token", "https://cloud.example/config", self.PART_SHA)], calls)
        self.assertEqual(200, self.request("/v1/driver/duties/duty-81.pdf", cookie=benny)[0])
        self.assertEqual(1, len(calls))
        self.assertEqual(404, self.request("/v1/driver/duties/duty-82.pdf", cookie=benny)[0])
        self.assertEqual(404, self.request("/v1/driver/duties/duty-99.pdf", cookie=benny)[0])
        self.assertEqual(401, self.request("/v1/driver/duties/duty-81.pdf")[0])

        # Inställningar visar vad träffen har och vägen till Cloud.
        cloud = self.request("/v1/driver-admin/overview", cookie=anna)[1]["cloud"]
        slug = runtime_package_v3()["meet"]["slug"]
        self.assertEqual({"duties": 3, "with_pages": 2, "parts": 1, "parts_here": 1, "linked": True,
                          "url": f"https://cloud.example/traff/{slug}/lokforare"},
                         {key: cloud[key] for key in ("duties", "with_pages", "parts", "parts_here", "linked", "url")})

    def test_a_part_that_cannot_be_fetched_is_tried_again_later(self) -> None:
        benny = self.login("benny")
        self.install_cloud([self.cloud_duty("81", source={"pages": [5, 6], "part": {"sha256": self.PART_SHA}})])
        # Inte kopplad: inget att hämta med.
        self.assertEqual(404, self.request("/v1/driver/duties/duty-81.pdf", cookie=benny)[0])
        self.assertEqual({"parts": 1, "fetched": 0, "failed": 0, "missing": 1}, self.app.sync_driver_parts())
        calls = self.link_cloud()

        def offline(token: str, url: str, sha: str) -> bytes:
            calls.append((token, url, sha))
            raise CentralSyncError("TrainMeet kunde inte nås")

        self.app.driver_part_fetcher = offline
        self.assertEqual(404, self.request("/v1/driver/duties/duty-81.pdf", cookie=benny)[0])
        self.assertEqual({"parts": 1, "fetched": 0, "failed": 1, "missing": 1}, self.app.sync_driver_parts())
        # Något annat än tjänstens PDF tas inte emot.
        self.app.driver_part_fetcher = lambda token, url, sha: b"%PDF-1.7 men en annan fil"
        self.assertEqual({"parts": 1, "fetched": 0, "failed": 1, "missing": 1}, self.app.sync_driver_parts())
        self.app.driver_part_fetcher = lambda token, url, sha: self.PART
        self.assertEqual({"parts": 1, "fetched": 1, "failed": 0, "missing": 0}, self.app.sync_driver_parts())
        self.assertEqual(200, self.request("/v1/driver/duties/duty-81.pdf", cookie=benny)[0])
        # En ny version av träffen utan den delen: den gamla tas bort.
        old = self.app.driver_documents.cloud_part_path(self.PART_SHA)
        self.install_cloud([self.cloud_duty("81")], publication_id="publication-2026-08-11-c")
        self.assertEqual({"parts": 0, "fetched": 0, "failed": 0, "missing": 0}, self.app.sync_driver_parts())
        self.assertFalse(old.exists())

    def test_the_server_fetches_the_pages_in_the_background_once_per_publication(self) -> None:
        anna = self.login("anna")
        calls = self.link_cloud()
        self.install_cloud([self.cloud_duty("81", source={"pages": [5, 6], "part": {"sha256": self.PART_SHA}})])
        self.request("/v1/driver-admin/overview", cookie=anna)
        deadline = time.monotonic() + 5
        while self.app.driver_documents.cloud_part_path(self.PART_SHA) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNotNone(self.app.driver_documents.cloud_part_path(self.PART_SHA))
        while self.app._driver_parts_state["running"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.request("/v1/display")
        self.request("/v1/driver-admin/overview", cookie=anna)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, self.request("/v1/driver-admin/overview", cookie=anna)[1]["cloud"]["parts_here"])

    def test_a_meet_with_only_drivers_runs_the_clock_lokforaren_and_the_maps(self) -> None:
        """Första lagret i en träff: bara köruppdragen, ingen bana än. Servern
        tar emot paketet och visar det träffen har."""
        benny = self.login("benny")
        self.install_cloud([self.cloud_duty("81")], drivers_only=True)
        self.assertEqual([], self.runtime.active().payload["stations"])
        self.assertEqual(["81", "132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        status, display, _ = self.request("/v1/display")
        self.assertEqual((200, []), (status, display["stations"]))
        self.assertEqual(200, self.request("/v1/driver/clock", cookie=benny)[0])
        # Utan köruppdrag är ett paket utan bana fortfarande inget driftpaket.
        package = runtime_package_v3(publication_id="publication-empty")
        for key in ("stations", "connections", "trains", "panels"):
            package[key] = []
        with self.assertRaises(RuntimePublicationError):
            self.runtime.install(package)

    def test_the_cloud_summary_counts_the_duties_the_meet_has(self) -> None:
        anna = self.login("anna")
        overview = self.request("/v1/driver-admin/overview", cookie=anna)[1]
        # Paketet har tjänst 7 och en utan nummer, som inte räknas.
        self.assertEqual((1, 0, 0, False), (overview["cloud"]["duties"], overview["cloud"]["parts"],
                                             overview["cloud"]["parts_here"], overview["cloud"]["linked"]))
        self.install_cloud([])
        self.assertEqual(0, self.request("/v1/driver-admin/overview", cookie=anna)[1]["cloud"]["duties"])

    # ── Köruppdrag som lades in på servern före Cloud (4.20–4.21) ───────

    PDF = b"%PDF-1.7\n" + b"0" * 4096 + b"\n%%EOF\n"

    def duty(self, number: str, **extra) -> dict:
        return {"number": number, "trains": [{"number": f"G {number}0", "from": "A", "to": "B"}],
                "events": [{"kind": "departure", "time": "18:00", "station": "A", "train": f"G {number}0"},
                           {"kind": "arrival", "time": "18:40", "station": "B", "train": f"G {number}0"}], **extra}

    def legacy_document(self, duties: list[dict] | None = None, *, part: bool = False) -> dict:
        """Ett köruppdrag som det lades in på servern i 4.20–4.21."""
        documents = self.app.driver_documents
        meet_key = self.runtime.active().meet_id
        document = documents.create({"filename": "Grimslöv 2027.json", "fingerprint": "a1b2c3d4e5f60718", "name": "Grimslöv 2027",
                                     "duties": duties or [self.duty("211"), self.duty("212")], "warnings": []},
                                    meet_key=meet_key, user_id=None)
        documents.attach_pdf(document["id"], io.BytesIO(self.PDF), len(self.PDF), filename="Köruppdrag Grimslöv.pdf")
        documents.set_pages(document["id"], {"total": 9, "pages": {"211": [1, 4], "212": [5, 8]}})
        if part:
            documents.store_part(document["id"], "211", io.BytesIO(self.PART), len(self.PART))
        return documents.document(document["id"])

    def test_duty_sheets_added_here_before_cloud_can_be_reviewed_withdrawn_and_removed(self) -> None:
        anna, benny = self.login("anna"), self.login("benny")
        self.install_cloud([])  # träffen har inga köruppdrag i Cloud
        document = self.legacy_document(part=True)
        review = self.request(f"/v1/driver-admin/documents/{document['id']}", cookie=anna)[1]
        self.assertEqual([("211", [1, 4], True), ("212", [5, 8], False)],
                         [(duty["number"], duty["pages"], duty["part"]) for duty in review["duties"]])
        duty_211 = review["duties"][0]["id"]
        self.assertEqual((200, self.PART), (lambda r: (r[0], r[1]["raw"]))(self.request(f"/v1/driver-admin/duties/{duty_211}.pdf", cookie=anna)))

        self.assertEqual(["132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        self.assertEqual(200, self.request(f"/v1/driver-admin/documents/{document['id']}/publish", {"published": True}, cookie=anna)[0])
        self.assertEqual(["211", "212", "132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        self.assertEqual({"document_id": document["id"], "pages": [1, 4], "part": True},
                         self.request(f"/v1/driver/duties/{duty_211}", cookie=benny)[1]["duty"]["source"])
        status, original, _ = self.request(f"/v1/driver/duties/{duty_211}.pdf", cookie=benny)
        self.assertEqual((200, self.PART), (status, original["raw"]))
        status, original, _ = self.request(f"/v1/driver/documents/{document['id']}.pdf", cookie=benny)
        self.assertEqual((200, self.PDF), (status, original["raw"]))
        self.request("/v1/driver/active", {"duty_id": duty_211}, cookie=benny)

        # Tillbakadraget: borta ur listan, men den som kör behåller sin tjänst.
        self.request(f"/v1/driver-admin/documents/{document['id']}/publish", {"published": False}, cookie=anna)
        self.assertEqual(["132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        self.assertEqual("211", self.request("/v1/driver/session", cookie=benny)[1]["active"]["duty"]["number"])
        overview = self.request("/v1/driver-admin/overview", cookie=anna)[1]
        self.assertEqual([(document["id"], True)], [(item["id"], item["current"]) for item in overview["documents"]])

        # Borttaget: raderna och filerna försvinner.
        paths = [self.app.driver_documents.pdf_path(document["id"]), self.app.driver_documents.part_path(duty_211)]
        self.assertTrue(all(path.is_file() for path in paths))
        self.assertEqual(200, self.request(f"/v1/driver-admin/documents/{document['id']}/remove", {}, cookie=anna)[0])
        self.assertFalse(any(path.exists() for path in paths))
        self.assertEqual([], self.request("/v1/driver-admin/overview", cookie=anna)[1]["documents"])

    def test_cloud_s_duty_sheets_replace_those_added_here(self) -> None:
        anna, benny = self.login("anna"), self.login("benny")
        document = self.legacy_document()
        self.request(f"/v1/driver-admin/documents/{document['id']}/publish", {"published": True}, cookie=anna)
        # Träffens paket har tjänst 7 från Cloud: den gäller, inte de som lagts in här.
        self.assertEqual(["7", "132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        self.install_cloud([])
        self.assertEqual(["211", "212", "132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])

    def test_duty_sheets_are_no_longer_uploaded_to_the_server(self) -> None:
        anna, benny = self.login("anna"), self.login("benny")
        document = self.legacy_document()
        for path in ("/v1/driver-admin/documents", f"/v1/driver-admin/documents/{document['id']}/pdf",
                     f"/v1/driver-admin/documents/{document['id']}/parts", f"/v1/driver-admin/documents/{document['id']}/pages"):
            status, payload, _ = self.request(path, {"fingerprint": "a1b2c3d4e5f60718"}, cookie=anna)
            self.assertEqual((410, "cloud_authoring_only"), (status, payload.get("error")), path)
            self.assertEqual(403, self.request(path, {}, cookie=benny)[0], path)
            self.assertEqual(401, self.request(path, {})[0], path)
        self.assertEqual(403, self.request(f"/v1/driver-admin/documents/{document['id']}/publish", {"published": True},
                                           cookie=anna, headers={"Origin": "https://evil.example"})[0])
        self.assertEqual(404, self.request("/v1/driver-admin/documents/000000000000", cookie=anna)[0])
        self.assertEqual(403, self.request(f"/v1/driver-admin/documents/{document['id']}", cookie=benny)[0])
        self.assertEqual(403, self.request("/v1/driver-admin/overview", cookie=benny)[0])
        # Läsaren och PDF-biblioteken finns i Cloud, inte här.
        for asset in ("/assets/lokforaren-koruppdrag.js", "/assets/pdfjs/pdf.min.mjs", "/assets/pdflib/pdf-lib.min.js"):
            self.assertEqual(404, self.request(asset)[0], asset)

if __name__ == "__main__":
    unittest.main()
