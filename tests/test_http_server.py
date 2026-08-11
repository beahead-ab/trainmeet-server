from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tambox_gateway.demo import demo_session
from tambox_gateway.engine import TrafficEngine
from tambox_gateway.http_server import (
    HTTPServerConfig,
    TamboxHTTPApplication,
    TamboxHTTPServer,
)
from tambox_gateway.identity import IdentityStore, PairingService
from tambox_gateway.local_config import SQLiteLocalConfigurationStore
from tambox_gateway.models import DispatchMode
from tambox_gateway.runtime import SQLiteRuntimeStore
from runtime_fixture import runtime_package


class HTTPServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.identities = IdentityStore(Path(self.temporary_directory.name) / "identity.db")
        self.runtime_store = SQLiteRuntimeStore(Path(self.temporary_directory.name) / "runtime.db")
        self.local_configuration_store = SQLiteLocalConfigurationStore(
            Path(self.temporary_directory.name) / "runtime.db"
        )
        self.engine = TrafficEngine(demo_session(DispatchMode.CLEARANCE))
        pairing = PairingService(
            self.identities,
            set(self.engine.config.panels),
        )
        self.pairing_code = self.identities.issue_pairing_code(
            ["panel-a"],
            code="123456",
        )
        application = TamboxHTTPApplication(
            self.engine,
            self.identities,
            pairing,
            HTTPServerConfig(local_development=True),
            runtime_store=self.runtime_store,
            local_configuration_store=self.local_configuration_store,
            runtime_fetcher=lambda code, _url: (
                runtime_package()
                if "".join(character for character in code if character.isdigit()) == "654321"
                else (_ for _ in ()).throw(ValueError("unexpected sync code"))
            ),
        )
        self.application = application
        self.server = TamboxHTTPServer(("127.0.0.1", 0), application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.identities.close()
        self.runtime_store.close()
        self.local_configuration_store.close()
        self.temporary_directory.cleanup()

    def test_web_view_is_served_by_gateway(self):
        with urlopen(f"{self.base_url}/", timeout=2) as response:
            html = response.read().decode("utf-8")
        self.assertIn("TrainMeet Server", html)
        self.assertIn("Tambox-simulering", html)
        self.assertIn("Lokal konfiguration", html)

    def test_swift_admin_gets_automatic_http_token_but_no_mqtt_password(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "swift-passwordless",
                "display_name": "Swift-klient",
                "device_kind": "swift_admin",
            },
            expected_status=201,
        )

        self.assertTrue(paired["access_token"])
        self.assertEqual(set(paired["mqtt"]), {"host", "port", "tls"})

    def test_pairing_grants_only_assigned_panel_for_snapshots_and_commands(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "web-integration",
                "display_name": "Webbtest",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        token = paired["access_token"]

        snapshots = self._json_request("/v1/snapshots", token=token)
        self.assertEqual(
            [snapshot["panel_id"] for snapshot in snapshots["snapshots"]],
            ["panel-a"],
        )

        with self.assertRaises(HTTPError) as denied:
            self._json_request(
                "/v1/command",
                {"panel_id": "panel-b", "expected_revision": 0, "key": "A"},
                token=token,
            )
        self.assertEqual(denied.exception.code, 403)

        acknowledgement = self._json_request(
            "/v1/command",
            {"panel_id": "panel-a", "expected_revision": 0, "key": "A"},
            token=token,
        )
        self.assertEqual(acknowledgement["status"], "accepted")
        self.assertEqual(set(acknowledgement["snapshots"]), {"panel-a"})

    def test_web_admin_assigns_discovered_physical_box_by_printed_code(self):
        self.identities.record_discovery(
            "esp32-real-box",
            "TBX-A7K2",
            model="Tambox S3",
            firmware_version="0.1.0",
        )
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "web-admin-devices",
                "display_name": "Webbadministratör",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        token = paired["access_token"]

        before = self._json_request("/v1/devices", token=token)
        self.assertEqual(before["devices"][0]["assigned_panel_ids"], [])

        assigned = self._json_request(
            "/v1/devices/assign",
            {"device_code": "tbx a7k2", "panel_id": "panel-b"},
            token=token,
        )
        self.assertEqual(assigned["device_id"], "esp32-real-box")
        self.assertEqual(assigned["assigned_panel_ids"], ["panel-b"])

    def test_admin_installs_runtime_and_clients_read_station_timetable(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "runtime-admin",
                "display_name": "Runtime admin",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        token = paired["access_token"]

        installed = self._json_request(
            "/v1/runtime/install",
            {"package": runtime_package()},
            token=token,
            expected_status=201,
        )
        self.assertTrue(installed["configured"])
        self.assertEqual(installed["publication_id"], "publication-2026-08-11-a")
        self.assertTrue(installed["restart_required"])

        timetable = self._json_request(
            "/v1/timetable?station_id=station-a",
            token=token,
        )
        self.assertEqual([train["train_number"] for train in timetable["trains"]], ["101"])

        self._json_request(
            "/v1/runtime/active-day",
            {"active_day": "Sön"},
            token=token,
        )
        sunday = self._json_request(
            "/v1/timetable?station_id=station-a",
            token=token,
        )
        self.assertEqual([train["train_number"] for train in sunday["trains"]], ["101", "202"])

    def test_admin_syncs_published_runtime_with_six_digit_code(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "sync-admin",
                "display_name": "Sync admin",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        synced = self._json_request(
            "/v1/runtime/sync",
            {"sync_code": "654 321"},
            token=paired["access_token"],
            expected_status=201,
        )
        self.assertEqual(synced["meet_name"], "Sommarträffen")
        self.assertTrue(synced["restart_required"])

    def test_admin_saves_and_activates_a_local_station_configuration(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "local-config-admin",
                "display_name": "Lokal admin",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        token = paired["access_token"]
        configuration = {
            "schema_version": 1,
            "id": "lokal-hosttraff",
            "name": "Lokal höstträff",
            "timezone": "Europe/Stockholm",
            "active_day": "Lör",
            "default_dispatch_mode": "clearance",
            "clock_time": "09:15",
            "stations": [
                {"id": "station-a", "code": "CDA", "name": "Charlottendahl"},
                {"id": "station-b", "code": "LEK", "name": "Lekeberg"},
            ],
            "connections": [
                {
                    "id": "connection-a-b",
                    "station_a_id": "station-a",
                    "station_b_id": "station-b",
                    "track_type": "double",
                    "dispatch_mode_override": None,
                    "display_side_a": "right",
                    "display_side_b": "left",
                    "display_order_a": 0,
                    "display_order_b": 0,
                }
            ],
            "panels": [
                {
                    "id": "panel-a",
                    "station_id": "station-a",
                    "name": "CDA Tambox",
                    "slots": {"A": "connection-a-b", "B": None, "C": None, "D": None},
                },
                {
                    "id": "panel-b",
                    "station_id": "station-b",
                    "name": "LEK Tambox",
                    "slots": {"A": "connection-a-b", "B": None, "C": None, "D": None},
                },
            ],
        }

        empty = self._json_request("/v1/local-configuration", token=token)
        self.assertFalse(empty["configured"])

        saved = self._json_request(
            "/v1/local-configuration",
            {"expected_revision": 0, "draft": configuration},
            token=token,
        )
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["draft"]["stations"][0]["code"], "CDA")

        activated = self._json_request(
            "/v1/local-configuration/activate",
            {"expected_revision": 1},
            token=token,
            expected_status=201,
        )
        self.assertEqual(activated["source"], "local")
        self.assertEqual(activated["meet_name"], "Lokal höstträff")
        self.assertTrue(activated["restart_required"])

        with self.assertRaises(HTTPError) as stale:
            self._json_request(
                "/v1/local-configuration",
                {"expected_revision": 0, "draft": configuration},
                token=token,
            )
        self.assertEqual(stale.exception.code, 409)

    def test_admin_can_request_a_supervised_server_restart(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "restart-admin",
                "display_name": "Omstartsadmin",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        self.application.config = HTTPServerConfig(
            local_development=True,
            allow_restart=True,
        )
        restarted = self._json_request(
            "/v1/server/restart",
            {},
            token=paired["access_token"],
            expected_status=202,
        )
        self.assertEqual(restarted["status"], "restarting")
        self.assertTrue(self.server.restart_requested)

    def _json_request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        token: str | None = None,
        expected_status: int = 200,
    ) -> dict:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers)
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, expected_status)
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
