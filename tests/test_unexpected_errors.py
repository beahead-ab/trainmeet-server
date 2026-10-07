"""Ett oväntat fel på servern ger ett svar sidan kan läsa.

Casper såg "The string did not match the expected pattern." på klockraden i
Drift (2026-10-07). Det är Safaris text när ett svar inte är JSON. Ett fel
ingen förutsåg stängde förut anslutningen utan svar, och bakom Cloudflare och
Render blev det en HTML-sida. Nu svarar servern med JSON (500, internal_error)
och skriver hela spåret i loggen. Sidans del, att en HTML-sida eller inget svar
alls ger en begriplig text, provas i tests/js/server-unreachable.test.cjs.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import (
    HTTPServerConfig,
    TrainMeetHTTPApplication,
    TrainMeetHTTPServer,
    TrainMeetRequestHandler,
)
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode


class UnexpectedErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.identities = IdentityStore(Path(directory.name) / "identity.db")
        self.addCleanup(self.identities.close)
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(engine, self.identities,
            PairingService(self.identities, set(engine.config.panels)), HTTPServerConfig(local_development=True))
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.application)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, path, body=None):
        request = Request(self.base + path, method="POST" if body is not None else "GET",
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.headers.get("Content-Type"), response.read()
        except HTTPError as error:
            return error.code, error.headers.get("Content-Type"), error.read()

    def assert_readable(self, status, content_type, body):
        self.assertEqual(500, status)
        self.assertTrue(content_type.startswith("application/json"), content_type)
        payload = json.loads(body)
        self.assertEqual("internal_error", payload["error"])
        self.assertIn("Något gick fel på servern", payload["message"])
        self.assertNotIn("boom", payload["message"], "the inside of the error stays in the log")

    def test_a_failing_post_answers_with_json_and_logs_the_trace(self):
        with patch.object(TrainMeetRequestHandler, "_read_json", side_effect=RuntimeError("boom")), \
                self.assertLogs("tmbox_gateway.http", "ERROR") as logged:
            self.assert_readable(*self.call("/v1/clock", {"action": "start"}))
        self.assertIn("POST /v1/clock", logged.output[0])
        self.assertIn("RuntimeError: boom", logged.output[0])

    def test_a_failing_get_answers_with_json(self):
        with patch.object(self.application, "display_snapshot", side_effect=KeyError("boom")), \
                self.assertLogs("tmbox_gateway.http", "ERROR"):
            self.assert_readable(*self.call("/v1/display"))

    def test_the_server_keeps_answering_afterwards(self):
        with patch.object(self.application, "display_snapshot", side_effect=KeyError("boom")), \
                self.assertLogs("tmbox_gateway.http", "ERROR"):
            self.call("/v1/display")
        status, _, _ = self.call("/healthz")
        self.assertEqual(200, status)


if __name__ == "__main__":
    unittest.main()
