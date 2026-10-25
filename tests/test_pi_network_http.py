import dataclasses
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from session_fixture import sample_session
from pi_network_fixture import FakeNetwork
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer, TrainMeetRequestHandler
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.pi_network import NetworkController


class NetworkHTTPTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.identities = IdentityStore(Path(directory.name) / "identity.db")
        self.addCleanup(self.identities.close)
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.app = TrainMeetHTTPApplication(engine, self.identities, PairingService(self.identities, set(engine.config.panels)), HTTPServerConfig(local_development=True))
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.controller = NetworkController(FakeNetwork())
        patcher = patch("tmbox_gateway.http_server.pi_network.host_request", side_effect=self.controller.handle)
        self.helper = patcher.start(); self.addCleanup(patcher.stop)

    def login(self):
        self.user = self.identities.create_first_owner("Tester", "tester@example.org", "long-test-password")
        token = self.identities.create_admin_session("tester@example.org", "long-test-password")
        return {"Cookie": "trainmeet_admin=" + token}

    def call(self, action="", payload=None, headers=None):
        defaults = {"Content-Type": "application/json", "X-TrainMeet-Network": "1"}
        defaults.update(headers or {})
        req = Request(self.base + "/v1/host/wifi" + ("/" + action if action else ""),
                      data=json.dumps(payload).encode() if payload is not None else None, headers=defaults)
        try:
            with urlopen(req, timeout=5) as response: return response.status, json.loads(response.read())
        except HTTPError as error: return error.code, json.loads(error.read())

    def test_login_is_required_even_during_initial_setup(self):
        self.assertEqual(self.call()[0], 401)
        self.assertEqual(self.call("save", {"ssid": "Test", "security": "open"})[0], 401)
        self.assertEqual(self.helper.call_count, 0)

    def test_authenticated_owner_can_manage_without_exposing_passwords(self):
        cookie = self.login()
        self.assertEqual(self.call(headers=cookie)[0], 200)
        status, body = self.call("save", dict(ssid="Test", security="wpa2", password="private-test-password"), cookie)
        self.assertEqual(status, 200)
        self.assertNotIn("private-test-password", json.dumps(body))
        self.assertNotIn("password", json.dumps(body))

    def test_missing_csrf_header_and_cross_origin_are_denied(self):
        cookie = self.login()
        for extra in ({"X-TrainMeet-Network": ""}, {"Origin": "https://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"}, {"Content-Type": "text/plain"}):
            self.assertEqual(self.call("scan", {"interface": "wlan0"}, {**cookie, **extra})[0], 403)
        self.assertEqual(self.helper.call_count, 0)

    def test_paired_bearer_is_not_a_network_admin_session(self):
        self.login()
        self.assertEqual(self.call(headers={"Authorization": "Bearer any-device-token"})[0], 401)
        self.assertEqual(self.helper.call_count, 0)

    def test_remote_plain_http_cannot_send_network_changes(self):
        cookie = self.login()
        with patch.object(TrainMeetRequestHandler, "_client_address_is_loopback", return_value=False):
            self.assertEqual(self.call("scan", {"interface": "wlan0"}, cookie)[1]["error"], "secure_connection_required")
            self.assertEqual(self.call(headers=cookie)[0], 200)
            self.assertFalse(self.call(headers=cookie)[1]["can_manage"])

    def test_lan_client_cannot_spoof_configured_proxy(self):
        cookie = self.login()
        self.app.config = dataclasses.replace(self.app.config, public_client_origin="https://pi.example.org")
        with patch.object(TrainMeetRequestHandler, "_client_address_is_loopback", return_value=False):
            self.assertEqual(self.call("scan", {"interface": "wlan0"}, {**cookie, "Host": "pi.example.org", "X-Forwarded-Proto": "https"})[0], 403)

    def test_untrusted_forwarded_proto_is_not_enough(self):
        cookie = self.login()
        self.assertEqual(self.call("scan", {"interface": "wlan0"}, {**cookie, "X-Forwarded-Proto": "https"})[0], 403)

    def test_configured_https_proxy_works(self):
        cookie = self.login()
        self.app.config = dataclasses.replace(self.app.config, public_client_origin="https://pi.example.org")
        self.assertEqual(self.call("scan", {"interface": "wlan0"}, {**cookie, "Host": "pi.example.org", "Origin": "https://pi.example.org", "X-Forwarded-Proto": "https"})[0], 200)

    def test_expired_session_denied_and_asset_served(self):
        cookie = self.login()
        self.identities.revoke_admin_session(cookie["Cookie"].split("=", 1)[1])
        self.assertEqual(self.call(headers=cookie)[0], 401)
        with urlopen(self.base + "/assets/pi-network.js") as response:
            self.assertIn(b"pi-network-panel", response.read())


if __name__ == "__main__": unittest.main()
