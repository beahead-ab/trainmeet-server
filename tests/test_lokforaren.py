"""Lokföraren (#225, #226): förarkonton, tjänster, incheckningar och ikoner.

En förare (rollen user) är inloggad men ingen administratör. Hen kommer åt
/v1/driver/* och Lokföraren-appen, aldrig ett admin-API. Förartjänsterna kommer
i Clouds paket (driver_duties) eller är övningstjänsten DEMO 132. En
incheckning syns tio minuter i verklig tid och tonar sedan ut på fem.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.driver import DEMO_DUTY, DriverError, DriverStore, normalize_duty
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import AdminAccessError, IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore

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


class DriverStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = DriverStore(Path(directory.name) / "trainmeet.db")
        self.addCleanup(self.store.close)

    def test_a_check_in_is_seen_for_fifteen_minutes_of_real_time(self) -> None:
        start = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
        self.store.check_in("u1", "meet", {"station": "Tveiten"}, duty=None, meet_time="17:58:00", now=start)
        self.assertIn("u1", self.store.latest_checkins("meet", now=start + timedelta(minutes=14, seconds=59)))
        self.assertNotIn("u1", self.store.latest_checkins("meet", now=start + timedelta(minutes=15, seconds=1)))
        self.assertNotIn("u1", self.store.latest_checkins("another-meet", now=start))

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
            self.assertEqual(401, self.request(path, cookie=benny)[0], path)
        self.assertEqual(401, self.request("/v1/clock", {"action": "stop"}, cookie=benny)[0])
        self.assertEqual(401, self.request("/v1/admin/users", {"display_name": "X", "email": "x@example.se", "role": "owner"}, cookie=benny)[0])
        status = self.request("/v1/auth/status", cookie=benny)[1]
        self.assertEqual((False, "user"), (status["authenticated"], status["account_role"]))

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

    def test_icons_are_served_to_drivers_only(self) -> None:
        benny, maja = self.login("benny"), self.login("maja")
        user = self.request("/v1/driver/avatar", {"image": data_url("image/png", PNG)}, cookie=benny)[1]["user"]
        self.assertTrue(user["avatar_url"].startswith(f"/v1/driver/avatars/{user['user_id']}?v="))
        status, payload, headers = self.request(user["avatar_url"], cookie=maja)
        self.assertEqual((200, PNG, "image/png"), (status, payload["raw"], payload["type"]))
        self.assertEqual(401, self.request(user["avatar_url"])[0])
        self.assertEqual(400, self.request("/v1/driver/avatar", {"image": "data:text/html;base64,PGI+"}, cookie=benny)[0])

    def test_a_driver_post_from_another_site_is_refused(self) -> None:
        benny = self.login("benny")
        status = self.request("/v1/driver/checkins", {"station": "Tveiten"}, cookie=benny,
                              headers={"Origin": "https://evil.example"})[0]
        self.assertEqual(403, status)

    def test_an_administrator_invites_and_removes_drivers_but_nothing_more(self) -> None:
        anna = self.login("anna")
        status, payload, _ = self.request("/v1/admin/users", {"display_name": "Erik", "email": "erik@example.se", "role": "user"}, cookie=anna)
        self.assertEqual(201, status, payload)
        self.assertEqual(403, self.request("/v1/admin/users", {"display_name": "Olle", "email": "olle@example.se", "role": "admin"}, cookie=anna)[0])
        erik = payload["user"]["user_id"]
        # Rollen delar bara ägaren ut.
        self.assertEqual(403, self.request("/v1/admin/users/update", {"user_id": erik, "role": "admin"}, cookie=anna)[0])
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


if __name__ == "__main__":
    unittest.main()
