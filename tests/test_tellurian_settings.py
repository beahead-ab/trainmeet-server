"""Inställningar → Klarering → Tellurian Dispatch i servern (#222, etapp 3).

Avstängt som standard, valbart per träff, bara administratören, och läget
"skugga" skriver aldrig till Tellurian och ändrar ingenting i TrainMeets trafik.
Simulerat mot TellurianSimulator; inte en verifiering mot Stefans server.
"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from runtime_fixture import runtime_package_v3
from tmbox_gateway.central_sync import CentralRuntimeDownload
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.integrations.tellurian_service import AUTHORITY_AVAILABLE, MODES
from tmbox_gateway.integrations.tellurian_sim import TellurianSimulator
from tmbox_gateway.models import unconfigured_session
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore

#: Samma stationer som runtime_package_v3: CDA och LEK med enkelspår emellan.
SCENARIO = {
    "stations": [{"signature": "CDA", "name": "Charlottendahl"}, {"signature": "LEK", "name": "Lekeberg"}],
    "track_stretches": [{"from": "CDA", "to": "LEK", "tracks": [["1", "both"]]}],
    "dispatch_stretches": [{"from": "CDA", "to": "LEK"}],
    "trains": [
        {"company": "SJ", "prefix": "RC", "number": 101, "calls": [["CDA", None, "09:20"], ["LEK", "09:45", None]]},
        {"company": "GC", "prefix": "G", "number": 300, "calls": [["LEK", None, "10:00"], ["CDA", "10:30", None]]},
    ],
}


def wait_for(condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


class TellurianSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "test.db"
        self.runtime = SQLiteRuntimeStore(path)
        self.operations = SQLiteOperationsStore(path)
        self.identities = IdentityStore(path)
        for store in (self.runtime, self.operations, self.identities):
            self.addCleanup(store.close)
        offered = runtime_package_v3(publication_id="first")
        self.app = TrainMeetHTTPApplication(
            TrafficEngine(unconfigured_session()), self.identities, PairingService(self.identities, set()),
            HTTPServerConfig(local_development=True), runtime_store=self.runtime, operations_store=self.operations,
            runtime_fetcher=lambda code, url: CentralRuntimeDownload(copy.deepcopy(offered), "test-link"))
        self.addCleanup(self.app.lifecycle.close)
        self.addCleanup(self.app.tellurian.close)
        self.admin = self.app.local_admin()
        self.app.tellurian._runner_options = {"poll_interval": 0.2, "liveness_interval": 0.3,
                                              "contact_timeout": 1.0, "sse_read_timeout": 1.0}
        self.sim = TellurianSimulator(copy.deepcopy(SCENARIO), heartbeat=0.1)
        self.sim.__enter__()
        self.addCleanup(self.sim.__exit__, None, None, None)

    def connect(self):
        self.app.sync_runtime(self.admin, {"sync_code": "123456"})
        return self.app.lifecycle.selected()["meet_id"]

    def call(self, payload=None, action="status"):
        return self.app.tellurian_clearance(self.admin, payload, action=action)

    def refused(self, payload, action="configure"):
        with self.assertRaises(HTTPAPIError) as caught:
            self.call(payload, action)
        return int(caught.exception.status), caught.exception.code

    def test_off_by_default_and_authority_cannot_be_chosen(self):
        self.assertEqual("no_meet", self.call()["reason"])
        meet = self.connect()
        status = self.call()
        self.assertTrue(status["available"])
        self.assertEqual({"base_url": "", "test_instance": False, "mode": "off"}, status["settings"])
        self.assertEqual(("off", "shadow"), MODES)
        self.assertFalse(AUTHORITY_AVAILABLE)
        self.assertFalse(status["authority_available"])
        self.assertIsNone(status["shadow"])
        self.assertEqual((409, "tellurian_authority_unavailable"),
                         self.refused({"meet_id": meet, "mode": "authority", "base_url": "192.168.1.20"}))
        self.assertEqual("off", self.call()["settings"]["mode"])

    def test_address_rules_and_stale_meet(self):
        meet = self.connect()
        self.assertEqual((400, "tellurian_address_not_local"),
                         self.refused({"meet_id": meet, "mode": "off", "base_url": "https://tellurian.example.org"}))
        self.assertEqual((400, "tellurian_address_required"), self.refused({"meet_id": meet, "mode": "shadow"}))
        self.assertEqual((409, "tellurian_stale_meet"),
                         self.refused({"meet_id": "another", "mode": "off", "base_url": "192.168.1.20"}))
        saved = self.call({"meet_id": meet, "mode": "off", "base_url": "192.168.1.20:5000"}, "configure")
        self.assertEqual("http://192.168.1.20:5000", saved["settings"]["base_url"])
        public = self.call({"meet_id": meet, "mode": "off", "base_url": "https://tellurian.example.org",
                            "test_instance": True}, "configure")
        self.assertEqual("https://tellurian.example.org:443", public["settings"]["base_url"])

    def test_test_connection_reports_without_saving_or_writing(self):
        self.connect()
        report = self.call({"base_url": self.sim.base_url, "test_instance": True}, "test")
        self.assertTrue(report["simulated"])
        self.assertEqual("passed", report["api"]["status"])
        self.assertEqual(["CDA", "LEK"], [m["code"] for m in report["stations"]["matched"]])
        self.assertEqual(["101"], [m["number"] for m in report["trains"]["matched"]])
        self.assertEqual(["GC G 300"], report["trains"]["unmatched_tellurian"])
        self.assertTrue(report["clock"]["warning"], "intern serverklocka ska ge varning")
        self.assertTrue(report["dispatch_mode"]["ok"])
        self.assertEqual("off", self.call()["settings"]["mode"])
        self.assertEqual(report, self.call()["report"])
        self.assertEqual(0, self.sim.posts())
        self.assertEqual((400, "tellurian_address_not_local"),
                         self.refused({"base_url": self.sim.base_url}, "test"))

    def test_shadow_mirrors_and_shows_deviations_without_touching_traffic(self):
        meet = self.connect()
        before = self.operations.open_clearances_for_station("first", self.runtime.active_day() or "Lör", None)
        status = self.call({"meet_id": meet, "mode": "shadow", "base_url": self.sim.base_url,
                            "test_instance": True}, "configure")
        self.assertEqual("shadow", status["settings"]["mode"])
        self.assertTrue(wait_for(lambda: (self.call()["shadow"] or {}).get("state") == "connected"), self.call())
        self.sim.act(101, "CDA", "manned")
        self.sim.act(101, "CDA", "request")
        self.assertTrue(wait_for(lambda: self.call()["shadow"]["deviations"]["count"] == 1), self.call()["shadow"])
        deviation = self.call()["shadow"]["deviations"]["items"][0]
        self.assertEqual(("only_tellurian", "101", "CDA", "LEK", "requested"),
                         (deviation["kind"], deviation["train"], deviation["from"], deviation["to"],
                          deviation["tellurian"]))
        self.assertTrue(self.call()["shadow"]["simulated"])
        self.assertRegex(self.call()["shadow"]["last_contact_local"], r"^\d\d:\d\d$")
        # Skuggan skriver aldrig, och TrainMeets egna ärenden är orörda.
        self.assertEqual(0, self.sim.posts())
        self.assertEqual(before, self.operations.open_clearances_for_station(
            "first", self.runtime.active_day() or "Lör", None))
        self.call({"meet_id": meet, "mode": "off"}, "configure")
        self.assertIsNone(self.call()["shadow"])
        self.assertTrue(wait_for(lambda: self.sim.stream_count() == 0))

    def test_shadow_restarts_with_the_server_and_follows_the_meet(self):
        meet = self.connect()
        self.call({"meet_id": meet, "mode": "shadow", "base_url": self.sim.base_url, "test_instance": True},
                  "configure")
        self.app.tellurian.close()
        self.assertIsNone(self.call()["shadow"])
        # Samma tillfälle som klockkällan läses om (start och träffbyte).
        self.app.refresh_clock_source()
        self.assertTrue(wait_for(lambda: (self.call()["shadow"] or {}).get("state") == "connected"))
        self.runtime.save_tellurian_settings(f"eu:{meet}", {"base_url": "", "test_instance": False, "mode": "off"})
        self.app.refresh_clock_source()
        self.assertIsNone(self.call()["shadow"])


class TellurianRouteTests(unittest.TestCase):
    """HTTP-vägarna: administratör, samma ursprung, aldrig en box."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "test.db"
        self.runtime = SQLiteRuntimeStore(path)
        self.operations = SQLiteOperationsStore(path)
        self.identities = IdentityStore(path)
        for store in (self.runtime, self.operations, self.identities):
            self.addCleanup(store.close)
        offered = runtime_package_v3(publication_id="first")
        self.app = TrainMeetHTTPApplication(
            TrafficEngine(unconfigured_session()), self.identities, PairingService(self.identities, set()),
            HTTPServerConfig(local_development=True), runtime_store=self.runtime, operations_store=self.operations,
            runtime_fetcher=lambda code, url: CentralRuntimeDownload(copy.deepcopy(offered), "test-link"))
        self.addCleanup(self.app.lifecycle.close)
        self.addCleanup(self.app.tellurian.close)
        self.app.sync_runtime(self.app.local_admin(), {"sync_code": "123456"})
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, payload=None, token=None, headers=None):
        request = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json",
                                   **({"Authorization": "Bearer " + token} if token else {}), **(headers or {})})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            body = json.loads(error.read() or b"{}")
            error.close()
            return error.code, body

    def test_routes_are_admin_only_and_same_origin(self):
        status, body = self.request("/v1/clearance/tellurian")
        self.assertEqual(200, status)
        meet = body["meet_id"]
        status, body = self.request("/v1/clearance/tellurian", {"meet_id": meet, "mode": "authority"})
        self.assertEqual((409, "tellurian_authority_unavailable"), (status, body["error"]))
        status, _ = self.request("/v1/clearance/tellurian", {"meet_id": meet, "mode": "off"},
                                 headers={"Origin": "https://unrelated.example"})
        self.assertEqual(403, status)
        status, body = self.request("/v1/clearance/tellurian/test", {"base_url": "8.8.8.8"})
        self.assertEqual((400, "tellurian_address_not_local"), (status, body["error"]))
        # Med ett konto är installationen stängd: en box (eller ingen inloggning) når inte inställningen.
        self.identities.create_first_owner("Admin", "admin@example.se", "test-password")
        status, box = self.request("/v1/browser-clients", {"workspace": "tmbox"})
        self.assertEqual(201, status)
        for path, payload in (("/v1/clearance/tellurian", None), ("/v1/clearance/tellurian", {"meet_id": meet}),
                              ("/v1/clearance/tellurian/test", {"base_url": "192.168.1.20"})):
            with self.subTest(path=path, payload=payload):
                self.assertEqual(403, self.request(path, payload, token=box["access_token"])[0])
                self.assertEqual(401, self.request(path, payload)[0])


if __name__ == "__main__":
    unittest.main()
