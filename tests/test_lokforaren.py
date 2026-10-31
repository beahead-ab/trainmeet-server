"""Lokföraren (#225, #226): förarkonton, tjänster, incheckningar och ikoner.

En förare (rollen user) är inloggad men ingen administratör. Hen kommer åt
/v1/driver/* och Lokföraren-appen, aldrig ett admin-API. Förartjänsterna kommer
i Clouds paket (driver_duties) eller är övningstjänsten DEMO 132. En
incheckning syns tio minuter i verklig tid och tonar sedan ut på fem.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
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

    # ── Köruppdrag i Inställningar → Lokförare ─────────────────────────

    def upload(self, document_id: str, data: bytes, *, cookie: str | None, filename: str = "Köruppdrag Grimslöv.pdf",
               kind: str = "application/pdf", headers: dict | None = None) -> tuple[int, dict]:
        request = Request(f"{self.base}/v1/driver-admin/documents/{document_id}/pdf", data=data, method="POST",
                          headers={"Content-Type": kind, "X-Filename": quote(filename),
                                   **({"Cookie": cookie} if cookie else {}), **(headers or {})})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    PDF = b"%PDF-1.7\n" + b"0" * 4096 + b"\n%%EOF\n"
    OTHER_PDF = b"%PDF-1.7\n" + b"1" * 4096 + b"\n%%EOF\n"

    def duty(self, number: str, **extra) -> dict:
        return {"number": number, "trains": [{"number": f"G {number}0", "from": "A", "to": "B"}],
                "events": [{"kind": "departure", "time": "18:00", "station": "A", "train": f"G {number}0"},
                           {"kind": "arrival", "time": "18:40", "station": "B", "train": f"G {number}0"}], **extra}

    def plan(self, cookie: str | None, duties: list[dict] | None = None, **extra) -> tuple[int, dict]:
        body = {"filename": "Grimslöv 2027.json", "fingerprint": "a1b2c3d4e5f60718", "name": "Grimslöv 2027",
                "duties": duties if duties is not None else [self.duty("211"), self.duty("212"), self.duty("213")],
                "warnings": [], **extra}
        status, payload, _ = self.request("/v1/driver-admin/documents", body, cookie=cookie)
        return status, payload

    def test_an_administrator_adds_the_plan_then_the_pdf_and_makes_the_duties_available(self) -> None:
        anna, benny = self.login("anna"), self.login("benny")
        status, payload = self.plan(anna, [self.duty("211"), self.duty("211"), self.duty("212"), {"number": "", "events": []}])
        self.assertEqual(201, status, payload)
        document = payload["document"]
        self.assertEqual(("Grimslöv 2027.json", "Grimslöv 2027", False, 3, None),
                         (document["filename"], document["name"], document["published"], document["duties"], document["pdf"]))
        self.assertIn("1 tjänster saknade nummer", document["warnings"][0])
        review = self.request(f"/v1/driver-admin/documents/{document['id']}", cookie=anna)[1]
        self.assertEqual(3, len({duty["id"] for duty in review["duties"]}))
        self.assertEqual([[], [], []], [duty["pages"] for duty in review["duties"]])

        # Sidorna kan inte sparas förrän PDF:en finns.
        self.assertEqual(409, self.request(f"/v1/driver-admin/documents/{document['id']}/pages",
                                           {"pages": {"212": [15, 16]}, "total": 600}, cookie=anna)[0])
        status, payload = self.upload(document["id"], self.PDF, cookie=anna)
        self.assertEqual(200, status, payload)
        self.assertEqual(("Köruppdrag Grimslöv.pdf", len(self.PDF), None, False),
                         (payload["document"]["pdf"]["filename"], payload["document"]["pdf"]["size"],
                          payload["document"]["pdf"]["pages"], payload["document"]["same_pdf"]))
        status, payload, _ = self.request(f"/v1/driver-admin/documents/{document['id']}/pages", {
            "total": 600, "warnings": ["Tjänst 213 hittades inte i PDF:en"],
            "pages": {"211": [12, 14], "212": [15, 16], "213": [20, 2], "999": [1, 1]},
        }, cookie=anna)
        self.assertEqual(200, status, payload)
        self.assertEqual((600, 3, ["Tjänst 213 hittades inte i PDF:en"]),
                         (payload["document"]["pdf"]["pages"], payload["document"]["pdf"]["duties_with_pages"],
                          payload["document"]["pdf"]["warnings"]))
        review = self.request(f"/v1/driver-admin/documents/{document['id']}", cookie=anna)[1]
        self.assertEqual([("211", [12, 14]), ("211", [12, 14]), ("212", [15, 16])],
                         [(duty["number"], duty["pages"]) for duty in review["duties"]])

        # Förarna ser ingenting förrän det är tillgängligt.
        self.assertEqual(["7", "132"], [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        self.assertEqual(404, self.request(f"/v1/driver/documents/{document['id']}.pdf", cookie=benny)[0])
        self.assertEqual(200, self.request(f"/v1/driver-admin/documents/{document['id']}/publish", {"published": True}, cookie=anna)[0])
        found = self.request("/v1/driver/duties?q=212", cookie=benny)[1]["duties"]
        self.assertEqual(["212"], [duty["number"] for duty in found])
        duty = self.request(f"/v1/driver/duties/{found[0]['id']}", cookie=benny)[1]["duty"]
        self.assertEqual({"document_id": document["id"], "pages": [15, 16], "part": False}, duty["source"])
        status, original, _ = self.request(f"/v1/driver/documents/{document['id']}.pdf", cookie=benny)
        self.assertEqual((200, "application/pdf", self.PDF), (status, original["type"], original["raw"]))
        active = self.request("/v1/driver/active", {"duty_id": found[0]["id"]}, cookie=benny)[1]["active"]
        self.assertEqual("212", active["duty"]["number"])

        # Samma planeringsfil igen: samma köruppdrag, tjänsterna uppdateras
        # och sidorna finns kvar för de tjänster som är kvar.
        status, payload = self.plan(anna, [self.duty("212"), self.duty("214")])
        self.assertEqual((200, document["id"], True, 2), (status, payload["document"]["id"], payload["document"]["duplicate"],
                                                         payload["document"]["duties"]))
        pages = {duty["number"]: duty["pages"] for duty in self.request(f"/v1/driver-admin/documents/{document['id']}", cookie=anna)[1]["duties"]}
        self.assertEqual({"212": [15, 16], "214": []}, pages)

        # En ny PDF ersätter den gamla; sidorna ska läsas om.
        old_path = self.app.driver_documents.pdf_path(document["id"])
        status, payload = self.upload(document["id"], self.OTHER_PDF, cookie=anna, filename="Ny.pdf")
        self.assertEqual((200, "Ny.pdf", 0, None), (status, payload["document"]["pdf"]["filename"],
                                                     payload["document"]["pdf"]["duties_with_pages"], payload["document"]["pdf"]["pages_read_at"]))
        self.assertFalse(old_path.exists())
        # Samma PDF igen behåller sidorna.
        self.request(f"/v1/driver-admin/documents/{document['id']}/pages", {"total": 9, "pages": {"212": [3, 4]}}, cookie=anna)
        status, payload = self.upload(document["id"], self.OTHER_PDF, cookie=anna, filename="Ny.pdf")
        self.assertEqual((True, 1), (payload["document"]["same_pdf"], payload["document"]["pdf"]["duties_with_pages"]))

        # Tillbakadraget: borta ur listan, men den som kör behåller sin tjänst.
        self.request(f"/v1/driver-admin/documents/{document['id']}/publish", {"published": False}, cookie=anna)
        self.assertNotIn("212", [duty["number"] for duty in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        self.assertEqual("212", self.request("/v1/driver/session", cookie=benny)[1]["active"]["duty"]["number"])
        overview = self.request("/v1/driver-admin/overview", cookie=anna)[1]
        benny_row = next(driver for driver in overview["drivers"] if driver["name"] == "Benny")
        self.assertEqual(("212", ["G 2120"]), (benny_row["duty_number"], benny_row["trains"]))
        self.assertEqual([document["id"]], [item["id"] for item in overview["documents"]])
        self.assertTrue(overview["documents"][0]["current"])

        # Borttaget: raderna och filen försvinner.
        path = self.app.driver_documents.pdf_path(document["id"])
        self.assertTrue(path.is_file())
        self.assertEqual(200, self.request(f"/v1/driver-admin/documents/{document['id']}/remove", {}, cookie=anna)[0])
        self.assertFalse(path.exists())
        self.assertEqual([], self.request("/v1/driver-admin/overview", cookie=anna)[1]["documents"])

    def part(self, document_id: str, number: str, data: bytes, *, cookie: str | None) -> tuple[int, dict]:
        request = Request(f"{self.base}/v1/driver-admin/documents/{document_id}/parts", data=data, method="POST",
                          headers={"Content-Type": "application/pdf", "X-Duty": quote(number),
                                   **({"Cookie": cookie} if cookie else {})})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_each_driver_gets_only_the_pages_of_their_duty(self) -> None:
        anna, benny = self.login("anna"), self.login("benny")
        document = self.plan(anna, [self.duty("211"), self.duty("212")])[1]["document"]
        doc = document["id"]
        self.assertEqual(409, self.part(doc, "211", self.PDF, cookie=anna)[0])  # förlagan först
        self.upload(doc, self.PDF, cookie=anna)
        self.request(f"/v1/driver-admin/documents/{doc}/pages", {"total": 9, "pages": {"211": [1, 4], "212": [5, 8]}}, cookie=anna)
        part_211 = b"%PDF-1.7\n" + b"2" * 900 + b"\n%%EOF\n"
        self.assertEqual(403, self.part(doc, "211", part_211, cookie=benny)[0])
        self.assertEqual(404, self.part(doc, "999", part_211, cookie=anna)[0])
        self.assertEqual(415, self.part(doc, "211", b"inte en pdf", cookie=anna)[0])
        self.assertEqual((200, {"part": {"number": "211", "size": len(part_211)}}), self.part(doc, "211", part_211, cookie=anna))
        review = self.request(f"/v1/driver-admin/documents/{doc}", cookie=anna)[1]
        self.assertEqual({"211": True, "212": False}, {duty["number"]: duty["part"] for duty in review["duties"]})
        self.assertEqual(1, review["document"]["pdf"]["duties_with_parts"])
        duty_211 = next(duty["id"] for duty in review["duties"] if duty["number"] == "211")
        duty_212 = next(duty["id"] for duty in review["duties"] if duty["number"] == "212")
        status, admin_copy, _ = self.request(f"/v1/driver-admin/duties/{duty_211}.pdf", cookie=anna)
        self.assertEqual((200, part_211), (status, admin_copy["raw"]))

        # Föraren får sidorna först när köruppdraget är tillgängligt.
        self.assertEqual(404, self.request(f"/v1/driver/duties/{duty_211}.pdf", cookie=benny)[0])
        self.request(f"/v1/driver-admin/documents/{doc}/publish", {"published": True}, cookie=anna)
        status, original, headers = self.request(f"/v1/driver/duties/{duty_211}.pdf", cookie=benny)
        self.assertEqual((200, "application/pdf", part_211), (status, original["type"], original["raw"]))
        self.assertIn('filename="Tjanst-211.pdf"', headers["Content-Disposition"])
        self.assertEqual(404, self.request(f"/v1/driver/duties/{duty_212}.pdf", cookie=benny)[0])
        self.assertEqual(401, self.request(f"/v1/driver/duties/{duty_211}.pdf")[0])
        found = self.request(f"/v1/driver/duties/{duty_211}", cookie=benny)[1]["duty"]
        self.assertEqual({"document_id": doc, "pages": [1, 4], "part": True}, found["source"])

        # Samma planeringsfil igen behåller delen; nya sidor tar bort den.
        part_path = self.app.driver_documents.part_path(duty_211)
        self.assertTrue(part_path.is_file())
        self.plan(anna, [self.duty("211"), self.duty("212")])
        self.assertEqual(1, self.request(f"/v1/driver-admin/documents/{doc}", cookie=anna)[1]["document"]["pdf"]["duties_with_parts"])
        self.request(f"/v1/driver-admin/documents/{doc}/pages", {"total": 9, "pages": {"211": [1, 3]}}, cookie=anna)
        self.assertEqual(0, self.request(f"/v1/driver-admin/documents/{doc}", cookie=anna)[1]["document"]["pdf"]["duties_with_parts"])
        self.assertFalse(part_path.exists())
        self.assertEqual(404, self.request(f"/v1/driver/duties/{duty_211}.pdf", cookie=benny)[0])

        # En ny PDF tar också bort delarna, och borttaget köruppdrag tar filerna.
        self.part(doc, "211", part_211, cookie=anna)
        self.upload(doc, self.OTHER_PDF, cookie=anna)
        self.assertFalse(part_path.exists())
        self.part(doc, "212", part_211, cookie=anna)
        self.assertTrue(part_path.exists())
        self.request(f"/v1/driver-admin/documents/{doc}/remove", {}, cookie=anna)
        self.assertFalse(part_path.exists())

    def test_only_administrators_add_duties_and_only_real_pdfs(self) -> None:
        anna, benny = self.login("anna"), self.login("benny")
        self.assertEqual(403, self.plan(benny)[0])  # inloggad förare: inte administratör
        self.assertEqual(401, self.plan(None)[0])
        self.assertEqual(403, self.request("/v1/driver-admin/overview", cookie=benny)[0])
        self.assertEqual(400, self.plan(anna, fingerprint="")[0])
        self.assertEqual(400, self.plan(anna, duties=[])[0])
        self.assertEqual(400, self.plan(anna, duties=[{"number": "1", "events": []}])[0])
        status, payload, _ = self.request("/v1/driver-admin/documents", {"fingerprint": "a1b2c3d4e5f60718", "duties": [self.duty("1")]},
                                          cookie=anna, headers={"Origin": "https://evil.example"})
        self.assertEqual(403, status)
        status, payload = self.plan(anna)
        self.assertEqual(201, status)
        document_id = payload["document"]["id"]
        self.assertEqual(403, self.upload(document_id, self.PDF, cookie=benny)[0])
        self.assertEqual(401, self.upload(document_id, self.PDF, cookie=None)[0])
        self.assertEqual(415, self.upload(document_id, b"<html>inte en pdf</html>", cookie=anna)[0])
        self.assertEqual(415, self.upload(document_id, self.PDF, cookie=anna, kind="text/plain")[0])
        self.assertEqual(403, self.upload(document_id, self.PDF, cookie=anna, headers={"Origin": "https://evil.example"})[0])
        self.assertEqual(404, self.upload("000000000000", self.PDF, cookie=anna)[0])
        self.assertEqual(403, self.request(f"/v1/driver-admin/documents/{document_id}/publish", {"published": True},
                                           cookie=anna, headers={"Origin": "https://evil.example"})[0])
        self.assertEqual(404, self.request("/v1/driver-admin/documents/000000000000", cookie=anna)[0])
        self.assertEqual(403, self.request(f"/v1/driver-admin/documents/{document_id}", cookie=benny)[0])

if __name__ == "__main__":
    unittest.main()
