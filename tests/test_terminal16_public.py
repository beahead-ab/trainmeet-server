import http.client
import json
import threading
import unittest
from uuid import uuid4

from tmbox_gateway.models import ConnectionState
from tmbox_gateway.terminal16_public import COOKIE, MAX_AGE, IDLE_TTL, PublicLabServer, SessionStore


class PublicSessionTests(unittest.TestCase):
    def test_unknown_token_cannot_choose_session_identity(self):
        store = SessionStore()
        token, session, created = store.acquire("chosen-by-client", create=True)
        self.assertTrue(created)
        self.assertNotEqual(token, "chosen-by-client")
        self.assertEqual(len(token), 43)
        self.assertIs(store.acquire(token)[1], session)
        self.assertIsNone(store.acquire("different")[1])

    def test_capacity_and_creation_rate_are_bounded(self):
        tick = [0]
        store = SessionStore(limit=2, now=lambda: tick[0])
        first, session, _ = store.acquire(None, create=True)
        store.acquire(None, create=True)
        self.assertIsNone(store.acquire(None, create=True)[1])
        self.assertIs(store.acquire(first)[1], session)
        tick[0] = IDLE_TTL + 1
        self.assertIsNotNone(store.acquire(None, create=True)[1])
        self.assertEqual(len(store.sessions), 1)

    def test_active_stream_cannot_extend_absolute_lifetime(self):
        tick = [0]
        store = SessionStore(now=lambda: tick[0])
        token, session, _ = store.acquire(None, create=True)
        for tick[0] in range(60, MAX_AGE, 60):
            self.assertTrue(store.touch(token, session))
        tick[0] = MAX_AGE
        self.assertFalse(store.touch(token, session))
        self.assertIsNone(store.acquire(token)[1])

    def test_command_rate_limit_recovers_and_streams_are_bounded(self):
        tick = [0]
        store = SessionStore(now=lambda: tick[0])
        _, session, _ = store.acquire(None, create=True)
        self.assertTrue(all(store.allow_command(session) for _ in range(30)))
        self.assertFalse(store.allow_command(session))
        tick[0] = 10
        self.assertTrue(store.allow_command(session))
        self.assertTrue(store.open_stream(session))
        self.assertTrue(store.open_stream(session))
        self.assertFalse(store.open_stream(session))
        store.close_stream(session)
        self.assertTrue(store.open_stream(session))

    def test_public_listener_cannot_bind_all_interfaces_or_use_http_origin(self):
        for address, origin in (("0.0.0.0", "https://demo.example"), ("127.0.0.1", "http://demo.example")):
            with self.assertRaises(ValueError):
                PublicLabServer((address, 0), origin=origin)

    def test_streams_leave_http_workers_for_commands(self):
        store = SessionStore()
        sessions = [store.acquire(None, create=True)[1] for _ in range(24)]
        for session in sessions[:16]:
            self.assertTrue(store.open_stream(session))
            self.assertTrue(store.open_stream(session))
        self.assertFalse(store.open_stream(sessions[16]))
        store.close_stream(sessions[0])
        self.assertTrue(store.open_stream(sessions[16]))


class PublicHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = PublicLabServer(("127.0.0.1", 0), origin="https://cloud.example")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def request(self, path="/", *, cookie=None, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        request_headers = {"Host": "cloud.example"}
        if cookie:
            request_headers["Cookie"] = cookie
        if body is not None:
            request_headers.update({"Origin": "https://cloud.example", "Content-Type": "application/json"})
        request_headers.update(headers or {})
        connection.request("POST" if body is not None else "GET", "/tmbox-lab" + path,
                           body=None if body is None else json.dumps(body), headers=request_headers)
        response = connection.getresponse()
        status, response_headers, data = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, response_headers, data

    def session(self):
        status, headers, _ = self.request()
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";")[0]

    def state(self, cookie):
        status, _, data = self.request("/api/state", cookie=cookie)
        self.assertEqual(status, 200)
        return json.loads(data)

    def key(self, cookie, key, number=None):
        frame = next(f for f in self.state(cookie)["frames"] if f["device_id"] == "DEMO-CDA")
        body = {"device_id": "DEMO-CDA", "view_token": frame["view_token"], "command_id": str(uuid4()), "key": key}
        if number:
            body.update(train_number=number, entry_context=frame["entry"]["context"])
        status, _, data = self.request("/api/key", cookie=cookie, body=body)
        self.assertEqual(status, 200, data)

    def test_secure_cookie_and_relative_assets(self):
        status, headers, data = self.request()
        self.assertEqual(status, 200)
        for attribute in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/tmbox-lab/"):
            self.assertIn(attribute, headers["Set-Cookie"])
        self.assertIn(b'./style.css', data)
        self.assertIn(b'./terminal.js', data)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_two_browsers_are_isolated_and_reset_does_not_affect_other(self):
        first, second = self.session(), self.session()
        self.assertNotEqual(first, second)
        self.key(first, "#", "39"); self.key(first, "#")
        self.key(second, "#", "17"); self.key(second, "#")
        before_other = self.state(second)
        status, _, data = self.request("/api/reset-devices", cookie=first, body={})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["audit"], [])
        self.assertEqual(self.state(second)["audit"], before_other["audit"])
        self.assertEqual(len(self.state(second)["audit"]), 1)

    def test_same_browser_reload_resumes_without_allocating_another_session(self):
        cookie = self.session()
        self.key(cookie, "#", "39"); self.key(cookie, "#")
        status, headers, _ = self.request(cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(len(self.server.sessions.sessions), 1)
        self.assertEqual(len(self.state(cookie)["audit"]), 1)

    def test_cookie_less_api_and_forged_cookie_are_rejected(self):
        for cookie in (None, f"{COOKIE}=unknown"):
            self.assertEqual(self.request("/api/state", cookie=cookie)[0], 401)
            self.assertEqual(self.request("/api/reset-devices", cookie=cookie, body={})[0], 401)
        self.assertEqual(len(self.server.sessions.sessions), 0)

    def test_origin_and_host_are_enforced_for_mutations(self):
        cookie = self.session()
        before = self.state(cookie)["frames"][0]["entry"]["context"]
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://other.example"},
                        {"Origin": ""}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.request("/api/reset-devices", cookie=cookie, body={}, headers=headers)[0], 403)
        self.assertEqual(self.state(cookie)["frames"][0]["entry"]["context"], before)

    def test_static_and_health_do_not_allocate_sessions(self):
        for path in ("/healthz", "/style.css", "/terminal.js"):
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(len(self.server.sessions.sessions), 0)
        self.assertEqual(self.request("/api/users")[0], 404)
        self.assertEqual(self.request("/../api/state")[0], 404)

    def test_cross_browser_frame_cannot_control_other_session(self):
        first, second = self.session(), self.session()
        self.key(first, "#", "39")
        frame = next(f for f in self.state(first)["frames"] if f["device_id"] == "DEMO-CDA")
        self.assertEqual(self.request("/api/key", cookie=second, body={"device_id": "DEMO-CDA",
                         "command_id": "cross-browser", "view_token": frame["view_token"], "key": "#"})[0], 409)
        self.assertEqual(self.state(second)["audit"], [])

    def test_reset_mode_is_private_to_its_browser(self):
        first, second = self.session(), self.session()
        self.assertEqual(self.request("/api/reset", cookie=first, body={"mode": "direct"})[0], 200)
        self.assertEqual(self.state(first)["mode"], "direct")
        self.assertEqual(self.state(second)["mode"], "clearance")

    def test_sse_uses_only_session_snapshot(self):
        first, second = self.session(), self.session()
        self.key(first, "#", "39"); self.key(first, "#")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("GET", "/tmbox-lab/events", headers={"Host": "cloud.example", "Cookie": second})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        event = response.readline().decode()
        self.assertTrue(event.startswith("data: "))
        self.assertEqual(json.loads(event[6:])["audit"], [])
        response.close(); connection.close()


if __name__ == "__main__":
    unittest.main()
