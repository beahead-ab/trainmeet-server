"""Ett ställverk (TKL) ansluts till Servern.

Casper kunde inte ansluta CDA:s ställverk (2026-10-01). Koden fanns bara som
liten grå text under Inställningar → Skärmar och klocka, den saknades helt
när träffen inte hade paneler, och ett anslutet ställverk syntes ingenstans.

Nu:
- koden och adressen står i Drift, med vad som ska skrivas var;
- det står varför det inte finns någon kod när den saknas;
- en ny kod kan tas när den gamla är förbrukad;
- varje anslutet ställverk syns i Klienter med station och onlineläge och kan
  tas bort.

Koden finns inte längre i den publika /v1/display. Den släpper in ett
ställverk, och den adressen är öppen även på Internet.
"""
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest

import test_shared_traffic
from tmbox_gateway import http_server
from tmbox_gateway.http_server import CONNECTION_CODE_LABEL, HTTPAPIError, TrainMeetHTTPServer


class TKLConnectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app, self.admin, self.ids = self.fixture.app, self.fixture.admin, self.fixture.ids
        self.app.config = http_server.replace(self.app.config, state_dir=self.fixture.temp.name)
        self.app.refresh_connection_grants()
        self.clock = 1_800_000_000.0
        self.app.wall_clock = lambda: self.clock

    def private(self):
        return self.app.connection_details(private=True)

    def pair(self, name="CDA TKL 1", code=None, kind="tkl_terminal"):
        return self.app.pair({"pairing_code": code or self.private()["code"], "client_id": "tkl-" + name.split()[0].lower(),
                              "display_name": name, "device_kind": kind}, "")

    def terminals(self):
        return self.app.devices(self.admin)["terminals"]

    def set_code(self, **values):
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.ids._connection.execute(f"UPDATE pairing_codes SET {assignments} WHERE label = ?", (*values.values(), CONNECTION_CODE_LABEL))

    def test_only_an_administrator_is_given_the_code(self):
        public = self.app.display_snapshot()["connection"]
        self.assertEqual(("", None), (public["code"], public["code_state"]))
        details = self.private()
        self.assertRegex(details["code"], r"^\d{3}-\d{3}$")
        self.assertEqual("valid", details["code_state"])

    def test_why_there_is_no_code_or_why_it_no_longer_works(self):
        self.set_code(uses=50)
        self.assertEqual("used_up", self.private()["code_state"])
        self.set_code(uses=49)
        self.assertEqual("valid", self.private()["code_state"])
        self.set_code(expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
        self.assertEqual("expired", self.private()["code_state"])
        # A new meet has cleared the code and none is issued yet.
        self.ids.clear_meet_assignments()
        self.assertEqual("no_panels", self.private()["code_state"])
        self.app.engine.config = http_server.replace(self.app.engine.config, panels={})
        self.app.refresh_connection_grants()
        self.assertEqual(("", "no_panels"), (self.private()["code"], self.private()["code_state"]))
        self.app.runtime_store = None
        self.assertEqual("no_meet", self.private()["code_state"])
        # A US meet has no signal boxes; Drift hides the card.
        self.app.lifecycle = SimpleNamespace(selected=lambda: {"region": "us"})
        self.assertEqual("us", self.private()["code_state"])

    def test_a_new_code_for_new_terminals_and_the_console_shows_it(self):
        old = self.private()["code"]
        since = self.app.changes.seq
        renewed = self.app.renew_connection_code(self.admin)
        self.assertNotEqual(old, renewed["code"])
        self.assertEqual("valid", renewed["code_state"])
        self.assertIn("devices", self.app.changes.wait(since, 0)[1])
        saved = Path(self.fixture.temp.name) / "connection-code.txt"
        self.assertEqual(renewed["code"].replace("-", ""), saved.read_text().strip(), "the console and the installer read this file")
        self.assertEqual(0o640, saved.stat().st_mode & 0o777, "it lets a signal box in: not for every user on the machine")
        with self.assertRaises(HTTPAPIError):
            self.pair(code=old)
        self.pair(code=renewed["code"])

    def test_a_new_meet_gets_a_new_code_and_the_console_follows(self):
        old = self.private()["code"]
        self.app.refresh_connection_grants(new_meet=True)
        new = self.private()["code"]
        self.assertNotEqual(old, new, "grants from the last meet must not open this one")
        self.assertEqual(new.replace("-", ""), (Path(self.fixture.temp.name) / "connection-code.txt").read_text().strip())

    def test_only_an_administrator_takes_a_new_code(self):
        terminal = self.ids.authenticate(self.pair()["access_token"])
        code = self.private()["code"]
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.renew_connection_code(terminal)
        self.assertEqual(403, refused.exception.status)
        self.assertEqual(code, self.private()["code"])

    def test_no_new_code_without_station_panels(self):
        self.app.engine.config = http_server.replace(self.app.engine.config, panels={})
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.renew_connection_code(self.admin)
        self.assertEqual("no_panels", refused.exception.code)

    def test_a_signal_box_shows_in_klienter_with_its_station_and_whether_it_is_heard(self):
        self.assertEqual([], self.terminals())
        since = self.app.changes.seq
        paired = self.pair()
        self.assertIn("devices", self.app.changes.wait(since, 0)[1], "Drift hears that it joined")
        terminal = self.ids.authenticate(paired["access_token"])
        self.assertEqual([("CDA TKL 1", None, "offline")],
                         [(t["name"], t["station"], t["connection"]["state"]) for t in self.terminals()])
        self.app.tkl_context(terminal, "station-a")
        listed = self.terminals()[0]
        self.assertEqual(("online", "CDA", True), (listed["connection"]["state"], listed["station"]["code"], listed["has_access"]))
        self.clock += http_server.DEVICE_ONLINE_SECONDS + 1
        self.assertEqual("lost", self.terminals()[0]["connection"]["state"])
        # Apps and boxes are not signal boxes; signal boxes are listed by name.
        self.pair("Alpha TKL")
        self.pair("Ipad Drift", kind="swift_panel")
        self.assertEqual(["Alpha TKL", "CDA TKL 1"], [t["name"] for t in self.terminals()])
        # A new meet takes their stations away until they pair with the new code.
        self.ids.clear_meet_assignments()
        self.assertEqual([False, False], [t["has_access"] for t in self.terminals()])

    def test_removing_a_signal_box_shuts_it_out_at_once(self):
        paired = self.pair()
        terminal = self.ids.authenticate(paired["access_token"])
        self.app.tkl_context(terminal, "station-a")
        since = self.app.changes.seq
        self.app.remove_terminal(self.admin, {"client_id": terminal.client_id})
        self.assertIn("devices", self.app.changes.wait(since, 0)[1])
        self.assertEqual([], self.terminals())
        self.assertIsNone(self.ids.authenticate(paired["access_token"]))
        # It can come back with the code, as a new signal box: not heard yet,
        # no station until it asks for one.
        self.pair()
        self.assertEqual([("CDA TKL 1", None, "offline")],
                         [(t["name"], t["station"], t["connection"]["state"]) for t in self.terminals()])
        self.pair("Ipad Drift", kind="swift_panel")
        for client_id in ("tkl-ipad", "nobody"):
            with self.assertRaises(HTTPAPIError) as refused:
                self.app.remove_terminal(self.admin, {"client_id": client_id})
            self.assertEqual("terminal_not_found", refused.exception.code)
        with self.assertRaises(HTTPAPIError):
            self.app.remove_terminal(terminal, {"client_id": terminal.client_id})


class TKLConnectionHTTPTests(unittest.TestCase):
    """The same, through a running server, as the TKL terminal and Drift use it."""

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app = self.fixture.app
        self.app.refresh_connection_grants()
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.app.changes.close()
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)
        self.addCleanup(stop)

    def request(self, method, path, body=None, token=None):
        # No password is set and this comes from the machine itself, so
        # without a token it is the administrator (an open installation).
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"null")

    def test_the_code_is_renewed_and_a_removed_signal_box_is_told_its_pairing_is_gone(self):
        status, display = self.request("GET", "/v1/display")
        self.assertEqual((200, ""), (status, display["connection"]["code"]), "the public display never carries the code")
        status, renewed = self.request("POST", "/v1/display/connection/code", {})
        self.assertEqual((200, "valid"), (status, renewed["code_state"]))
        status, paired = self.request("POST", "/v1/pair", {"pairing_code": renewed["code"], "client_id": "tkl-cda",
                                                           "display_name": "CDA TKL 1", "device_kind": "tkl_terminal"})
        self.assertEqual(201, status, paired)
        token = paired["access_token"]
        self.assertEqual(200, self.request("GET", "/v1/tkl/context?station_id=station-a", token=token)[0])
        self.assertEqual(403, self.request("POST", "/v1/display/connection/code", {}, token=token)[0])
        self.assertEqual(403, self.request("POST", "/v1/terminals/remove", {"client_id": "tkl-cda"}, token=token)[0])
        status, removed = self.request("POST", "/v1/terminals/remove", {"client_id": "tkl-cda"})
        self.assertEqual((200, True), (status, removed["removed"]))
        # The TKL terminal asks for the code again on exactly this answer.
        status, refused = self.request("GET", "/v1/tkl/context?station_id=station-a", token=token)
        self.assertEqual((401, "Parkopplingen gäller inte längre"), (status, refused["message"]))


if __name__ == "__main__":
    unittest.main()
