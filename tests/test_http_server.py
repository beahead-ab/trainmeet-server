from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from session_fixture import sample_session
from tambox_gateway.central_sync import DEFAULT_RUNTIME_PUBLICATION_URL, CentralRuntimeDownload, CentralRuntimeManifest
from tambox_gateway.engine import TrafficEngine
from tambox_gateway.http_server import (
    HTTPAPIError,
    HTTPServerConfig,
    TamboxHTTPApplication,
    TamboxHTTPServer,
)
from tambox_gateway.identity import DeviceKind, IdentityStore, PairingService
from tambox_gateway.local_config import SQLiteLocalConfigurationStore
from tambox_gateway.models import DispatchMode
from tambox_gateway.operations import SQLiteOperationsStore
from tambox_gateway.runtime import SQLiteRuntimeStore
from runtime_fixture import runtime_package, runtime_package_v2


class HTTPServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.identities = IdentityStore(Path(self.temporary_directory.name) / "identity.db")
        self.runtime_store = SQLiteRuntimeStore(Path(self.temporary_directory.name) / "runtime.db")
        self.operations_store = SQLiteOperationsStore(Path(self.temporary_directory.name) / "runtime.db")
        self.local_configuration_store = SQLiteLocalConfigurationStore(
            Path(self.temporary_directory.name) / "runtime.db"
        )
        self.engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
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
            linked_runtime_fetcher=self._linked_runtime,
            operations_store=self.operations_store,
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
        self.operations_store.close()
        self.local_configuration_store.close()
        self.temporary_directory.cleanup()

    def test_web_view_is_served_by_gateway(self):
        with urlopen(f"{self.base_url}/", timeout=2) as response:
            html = response.read().decode("utf-8")
        self.assertIn("TrainMeet Server", html)
        self.assertNotIn("LOKAL DRIFT", html)
        self.assertIn('id="overview-view"', html)
        self.assertIn('id="overview-topology"', html)
        self.assertIn('id="overview-route-list"', html)
        self.assertIn("TÅGRUTTER", html)
        self.assertIn("Administration", html)
        self.assertIn("Tambox-simulering", html)
        self.assertIn("AKTIV RUNTIME", html)
        self.assertIn("Aktiva sträckor", html)
        self.assertIn('id="copy-active-runtime"', html)
        self.assertIn('id="runtime-import"', html)
        self.assertIn('id="runtime-import-file"', html)
        self.assertIn("Nytt lokalt utkast", html)
        self.assertIn('id="overview-graph"', html)
        self.assertIn("Extern admininloggning", html)
        self.assertIn('id="login-form"', html)
        self.assertIn("Skärmar", html)
        self.assertIn('/trainmeet-logo.png', html)
        self.assertNotIn('class="server-sidebar-title"', html)
        with urlopen(f"{self.base_url}/trainmeet-logo.png", timeout=2) as response:
            logo = response.read()
            self.assertEqual(response.headers.get_content_type(), "image/png")
        self.assertTrue(logo.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_clean_server_runs_the_complete_first_start_flow(self):
        self.runtime_store.begin_installation()

        initial = self._json_request("/v1/setup")
        self.assertTrue(initial["required"])
        self.assertEqual(initial["step"], "admin")
        self.assertFalse(initial["runtime"]["configured"])

        created = self._json_request(
            "/v1/setup/admin",
            {"username": "trafikledare", "password": "ett-eget-losenord"},
            expected_status=201,
        )
        self.assertTrue(created["authenticated"])

        named = self._json_request(
            "/v1/setup/server",
            {"server_name": "Testanläggningen"},
        )
        self.assertEqual(named["server_name"], "Testanläggningen")

        synced = self._json_request(
            "/v1/runtime/sync",
            {
                "central_url": "https://config.example.test/runtime",
                "sync_code": "654321",
            },
            expected_status=201,
        )
        self.assertTrue(synced["configured"])
        self.assertEqual(self.runtime_store.central_url(), "https://config.example.test/runtime")

        finished = self._json_request(
            "/v1/setup/complete",
            {"active_day": "Lör"},
        )
        self.assertTrue(finished["completed"])
        self.assertFalse(self._json_request("/v1/setup")["required"])

    def test_legacy_default_configuration_url_is_migrated_to_cloud(self):
        self.runtime_store.save_central_url("https://trainmeet.app/konfig")
        self.application = TamboxHTTPApplication(
            self.engine,
            self.identities,
            PairingService(self.identities, set(self.engine.config.panels)),
            HTTPServerConfig(local_development=True),
            runtime_store=self.runtime_store,
            local_configuration_store=self.local_configuration_store,
            operations_store=self.operations_store,
        )

        self.assertEqual(DEFAULT_RUNTIME_PUBLICATION_URL, self.runtime_store.central_url())
        self.assertEqual(DEFAULT_RUNTIME_PUBLICATION_URL, self.application.runtime_summary(self.application.local_admin())["central_url"])

    def test_public_display_exposes_runtime_clock_services_and_live_state(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)

        display = self._json_request("/v1/display")

        self.assertEqual(display["publication_id"], publication.publication_id)
        self.assertEqual(display["clock"]["time"], "09:15:00")
        self.assertEqual(display["services"][0]["train_number"], "101")
        self.assertEqual(display["display"]["graph_station_order"], ["station-a", "station-b"])

        started = self._json_request(
            "/v1/clock",
            {"action": "start", "time": "10:30:00", "speed": 2},
        )
        self.assertTrue(started["running"])
        self.assertEqual(started["speed"], 2)
        self.assertTrue(started["time"].startswith("10:30:"))

    def test_tkl_context_persists_shift_and_train_progress(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()

        before = self.application.tkl_context(client, "station-a")
        self.assertIsNone(before["shift"])
        self.assertEqual(before["preflight"]["train_count"], 1)

        started = self.application.start_tkl_shift(
            client,
            {
                "station_id": "station-a",
                "operator_name": "Anna",
                "terminal_name": "CDA TKL 1",
            },
        )
        self.assertEqual(started["shift"]["operator_name"], "Anna")

        updated = self.application.update_tkl_movement(
            client,
            {
                "station_id": "station-a",
                "movement_id": "movement-101-a",
                "arrival": "none",
                "departure": "positioned",
                "actual_track": "2",
                "event_type": "positioned",
            },
        )
        self.assertEqual(updated["movement"]["actualTrack"], "2")
        after = self.application.tkl_context(client, "station-a")
        self.assertEqual(after["movements"]["movement-101-a"]["departure"], "positioned")

        finished = self.application.finish_tkl_shift(
            client,
            {
                "station_id": "station-a",
                "shift_id": started["shift"]["shift_id"],
                "status": "closed",
            },
        )
        self.assertEqual(finished["shift"]["status"], "closed")

    def test_tkl_line_actions_use_the_authoritative_traffic_engine(self):
        client = self.application.local_admin()
        for station_id, operator_name in (("station-a", "Anna"), ("station-b", "Bertil")):
            self.application.start_tkl_shift(
                client,
                {
                    "station_id": station_id,
                    "operator_name": operator_name,
                    "terminal_name": f"{station_id} TKL",
                },
            )
        requested = self.application.tkl_line_action(
            client,
            {
                "station_id": "station-a",
                "connection_id": "connection-a-b",
                "train_number": "101",
                "action": "request",
            },
        )
        self.assertEqual(requested["connection"]["state"], "requested")

        accepted = self.application.tkl_line_action(
            client,
            {
                "station_id": "station-b",
                "connection_id": "connection-a-b",
                "train_number": "101",
                "action": "accept",
            },
        )
        self.assertEqual(accepted["connection"]["state"], "reserved")

        departed = self.application.tkl_line_action(
            client,
            {
                "station_id": "station-a",
                "connection_id": "connection-a-b",
                "train_number": "101",
                "action": "depart",
            },
        )
        self.assertEqual(departed["connection"]["state"], "occupied")

        arrived = self.application.tkl_line_action(
            client,
            {
                "station_id": "station-b",
                "connection_id": "connection-a-b",
                "train_number": "101",
                "action": "arrive",
            },
        )
        self.assertEqual(arrived["connection"]["state"], "free")

    def test_v2_station_snapshot_exposes_tracks_and_movements(self):
        package = runtime_package_v2()
        package["tracks"] = [
            {"id": "track-a-1", "display_label": "1A", "station_id": "station-a", "sort_order": 10},
            {"id": "track-a-2", "display_label": "1B", "station_id": "station-a", "sort_order": 20},
        ]
        publication = self.runtime_store.install(package)
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        self.application.start_tkl_shift(
            client, {"station_id": "station-a", "operator_name": "Anna", "terminal_name": "CDA TKL"}
        )

        snapshot = self.application.v2_station_snapshot(client, "station-a")

        self.assertEqual(snapshot["protocol_version"], 2)
        self.assertEqual([track["display_label"] for track in snapshot["tracks"]], ["1A", "1B"])
        self.assertEqual(len(snapshot["movements"]), 1)
        self.assertEqual(snapshot["movements"][0]["train_number"], "101")
        self.assertEqual(snapshot["active_clearances"], [])

    def test_v2_train_lookup_returns_matches_for_the_station(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        self.application.start_tkl_shift(
            client, {"station_id": "station-a", "operator_name": "Anna", "terminal_name": "CDA TKL"}
        )

        found = self.application.v2_train_lookup(client, "station-a", "101")
        self.assertEqual(len(found["matches"]), 1)
        self.assertEqual(found["matches"][0]["id"], "movement-101-a")

        missing = self.application.v2_train_lookup(client, "station-a", "4711")
        self.assertEqual(missing["matches"], [])

    def test_v2_movement_command_positions_and_departs_without_clobbering_arrival(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        self.application.start_tkl_shift(
            client, {"station_id": "station-a", "operator_name": "Anna", "terminal_name": "CDA TKL"}
        )
        self.operations_store.update_tkl_movement(
            "publication-2026-08-11-a", "Lör", "station-a", "movement-101-a",
            arrival="approaching", departure="none", actual_track=None,
            updated_by="Anna", shift_id=None, event_type="approaching",
        )

        positioned = self.application.v2_movement_command(
            client,
            {"station_id": "station-a", "movement_id": "movement-101-a", "action": "position", "actual_track": "2"},
        )
        self.assertEqual(positioned["movement"]["departure"], "positioned")
        self.assertEqual(positioned["movement"]["arrival"], "approaching")

        ready = self.application.v2_movement_command(
            client,
            {"station_id": "station-a", "movement_id": "movement-101-a", "action": "crew_ready", "crew_ready": True},
        )
        self.assertTrue(ready["movement"]["crewReady"])

        departed = self.application.v2_movement_command(
            client, {"station_id": "station-a", "movement_id": "movement-101-a", "action": "departed"}
        )
        self.assertEqual(departed["movement"]["departure"], "departed")
        self.assertEqual(departed["movement"]["arrival"], "approaching")
        self.assertEqual(departed["movement"]["actualTrack"], "2")

    def test_v2_commands_work_without_an_active_tkl_shift(self):
        # Decision 2026-08-19: TMBox does not require a TKL shift to be
        # started — "onödigt komplext och tillför ingenting". The device
        # itself is the audit actor when no shift exists.
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        device = self.identities.register_client(
            "esp32-tmbox-noshift",
            "TMBox TBX-NOSHIFT",
            DeviceKind.ESP32_PANEL,
            "credential-noshift",
            (),
            station_id="station-a",
        )

        positioned = self.application.v2_movement_command(
            device,
            {"station_id": "station-a", "movement_id": "movement-101-a", "action": "position", "actual_track": "2"},
        )
        self.assertEqual(positioned["movement"]["departure"], "positioned")
        self.assertEqual(positioned["movement"]["updated_by"], "TMBox TBX-NOSHIFT")

        requested = self.application.v2_clearance_request(
            device,
            {"station_id": "station-a", "movement_id": "movement-101-a", "connection_id": "connection-a-b"},
        )
        self.assertEqual(requested["clearance"]["status"], "waiting")
        self.assertEqual(requested["clearance"]["requested_by"], "TMBox TBX-NOSHIFT")

    def test_v2_clearance_request_respond_and_channel_frees_afterward(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        for station_id, operator_name in (("station-a", "Anna"), ("station-b", "Bertil")):
            self.application.start_tkl_shift(
                client, {"station_id": station_id, "operator_name": operator_name, "terminal_name": station_id}
            )

        requested = self.application.v2_clearance_request(
            client,
            {"station_id": "station-a", "movement_id": "movement-101-a", "connection_id": "connection-a-b"},
        )
        self.assertEqual(requested["clearance"]["status"], "waiting")

        blocked = self.application.v2_clearance_request(
            client,
            {"station_id": "station-a", "movement_id": "movement-202-a", "connection_id": "connection-a-b"},
        )
        self.assertEqual(blocked["clearance"], {"status": "rejected", "reason": "connection_busy"})

        approved = self.application.v2_clearance_respond(
            client, {"clearance_id": requested["clearance"]["clearance_id"], "accept": True}
        )
        self.assertEqual(approved["clearance"]["status"], "approved")

        freed = self.application.v2_clearance_request(
            client,
            {"station_id": "station-b", "movement_id": "movement-101-b", "connection_id": "connection-a-b"},
        )
        self.assertEqual(freed["clearance"], {"status": "rejected", "reason": "connection_busy"})

    def test_v2_clearance_cancel_frees_the_channel(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        self.application.start_tkl_shift(
            client, {"station_id": "station-a", "operator_name": "Anna", "terminal_name": "CDA TKL"}
        )

        requested = self.application.v2_clearance_request(
            client,
            {"station_id": "station-a", "movement_id": "movement-101-a", "connection_id": "connection-a-b"},
        )
        cancelled = self.application.v2_clearance_cancel(
            client, {"clearance_id": requested["clearance"]["clearance_id"]}
        )
        self.assertEqual(cancelled["clearance"]["status"], "cancelled")

        reopened = self.application.v2_clearance_request(
            client,
            {"station_id": "station-a", "movement_id": "movement-101-a", "connection_id": "connection-a-b"},
        )
        self.assertEqual(reopened["clearance"]["status"], "waiting")

    def test_v2_movement_command_approaching(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        self.application.start_tkl_shift(
            client, {"station_id": "station-b", "operator_name": "Bertil", "terminal_name": "LEK TKL"}
        )

        approaching = self.application.v2_movement_command(
            client, {"station_id": "station-b", "movement_id": "movement-101-b", "action": "approaching"}
        )
        self.assertEqual(approaching["movement"]["arrival"], "approaching")

        arrived = self.application.v2_movement_command(
            client, {"station_id": "station-b", "movement_id": "movement-101-b", "action": "arrived"}
        )
        self.assertEqual(arrived["movement"]["arrival"], "arrived")

    def test_v2_assign_track_validates_catalog_and_occupancy(self):
        package = runtime_package_v2()
        package["tracks"] = [
            {"id": "track-a-1a", "display_label": "1A", "station_id": "station-a", "sort_order": 10},
            {"id": "track-a-1b", "display_label": "1B", "station_id": "station-a", "sort_order": 20},
        ]
        # movement-202-a is Sön-only in the fixture; make it a same-day
        # second movement at station-a so the occupancy check has two real
        # candidates to work with.
        for train in package["trains"]:
            if train["id"] == "movement-202-a":
                train["days"] = "Dagl"
        for route in package["routes"]:
            if route["train_number"] == "202":
                route["days"] = "Dagl"
        publication = self.runtime_store.install(package)
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        self.application.start_tkl_shift(
            client, {"station_id": "station-a", "operator_name": "Anna", "terminal_name": "CDA TKL"}
        )

        with self.assertRaises(HTTPAPIError) as raised:
            self.application.v2_assign_track(
                client,
                {"station_id": "station-a", "movement_id": "movement-101-a", "track_id": "not-a-track"},
            )
        self.assertEqual(raised.exception.code, "unknown_track")

        assigned = self.application.v2_assign_track(
            client, {"station_id": "station-a", "movement_id": "movement-101-a", "track_id": "track-a-1a"}
        )
        self.assertEqual(assigned["movement"]["status"], "assigned")

        occupied = self.application.v2_assign_track(
            client, {"station_id": "station-a", "movement_id": "movement-202-a", "track_id": "track-a-1a"}
        )
        self.assertEqual(occupied["movement"], {"status": "rejected", "reason": "track_occupied"})

        moved = self.application.v2_assign_track(
            client, {"station_id": "station-a", "movement_id": "movement-101-a", "track_id": "track-a-1b"}
        )
        self.assertEqual(moved["movement"]["assignedTrackId"], "track-a-1b")

        freed = self.application.v2_assign_track(
            client, {"station_id": "station-a", "movement_id": "movement-202-a", "track_id": "track-a-1a"}
        )
        self.assertEqual(freed["movement"]["status"], "assigned")

    def test_v2_line_available_is_a_one_way_notice_not_a_clearance(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        for station_id, operator_name in (("station-a", "Anna"), ("station-b", "Bertil")):
            self.application.start_tkl_shift(
                client, {"station_id": station_id, "operator_name": operator_name, "terminal_name": station_id}
            )

        published = self.application.v2_line_publish(
            client,
            {"station_id": "station-b", "movement_id": "movement-101-b", "connection_id": "connection-a-b"},
        )
        self.assertEqual(published["line_message"]["status"], "delivered_to_device")
        self.assertEqual(published["line_message"]["to_station_id"], "station-a")

        at_a = self.application.v2_station_snapshot(client, "station-a")
        self.assertEqual(len(at_a["line_messages"]), 1)

        acknowledged = self.application.v2_line_acknowledge(
            client, {"message_id": published["line_message"]["message_id"]}
        )
        self.assertEqual(acknowledged["line_message"]["status"], "display_acknowledged")

        at_a_after = self.application.v2_station_snapshot(client, "station-a")
        self.assertEqual(at_a_after["line_messages"], [])

    def test_v2_station_snapshot_exposes_connection_dispatch_mode(self):
        package = runtime_package_v2()
        package["connections"][0]["dispatch_mode_override"] = "direct"
        publication = self.runtime_store.install(package)
        self.operations_store.ensure_publication(publication)
        client = self.application.local_admin()
        self.application.start_tkl_shift(
            client, {"station_id": "station-a", "operator_name": "Anna", "terminal_name": "CDA TKL"}
        )

        snapshot = self.application.v2_station_snapshot(client, "station-a")
        self.assertEqual(snapshot["connections"][0]["dispatch_mode"], "direct")
        self.assertEqual(snapshot["connections"][0]["other_station_code"], "LEK")

    def test_v2_station_access_is_enforced_for_non_admin_clients(self):
        publication = self.runtime_store.install(runtime_package_v2())
        self.operations_store.ensure_publication(publication)
        tmbox = self.identities.register_client(
            "esp32-tmbox-a",
            "TMBOX-7A42F1",
            DeviceKind.ESP32_PANEL,
            "credential-tmbox-a",
            (),
            station_id="station-a",
        )

        allowed = self.application.v2_station_snapshot(tmbox, "station-a")
        self.assertEqual(allowed["station"]["id"], "station-a")

        with self.assertRaises(HTTPAPIError) as raised:
            self.application.v2_station_snapshot(tmbox, "station-b")
        self.assertEqual(raised.exception.code, "station_not_assigned")

    def test_linked_runtime_update_is_downloaded_before_activation(self):
        self.runtime_store.install(runtime_package_v2(publication_id="publication-v2-first"))
        self.runtime_store.save_link_token("central-test-link")

        manifest = self._json_request("/v1/runtime/update")
        self.assertTrue(manifest["update_available"])
        self.assertEqual(manifest["publication_id"], "publication-v2-second")

        downloaded = self._json_request("/v1/runtime/update", {}, expected_status=201)
        self.assertEqual(downloaded["downloaded_publication_id"], "publication-v2-second")
        self.assertEqual(self.runtime_store.active().publication_id, "publication-v2-first")

        activated = self._json_request(
            "/v1/runtime/activate",
            {"publication_id": "publication-v2-second"},
            expected_status=201,
        )
        self.assertEqual(activated["publication_id"], "publication-v2-second")
        self.assertEqual(self.runtime_store.active().publication_id, "publication-v2-second")

    def test_admin_can_enable_realtime_cloud_config_updates(self):
        self.runtime_store.install(runtime_package_v2(publication_id="publication-v2-first"))
        self.runtime_store.save_link_token("central-test-link")
        client = self.application.local_admin()

        setting = self.application.configure_cloud_auto_sync(client, {"enabled": True})
        result = self.application.auto_sync_cloud_runtime()

        self.assertTrue(setting["enabled"])
        self.assertTrue(result["updated"])
        self.assertEqual("publication-v2-second", self.runtime_store.active().publication_id)
        self.assertTrue(self.application.runtime_summary(client)["cloud_auto_sync"])

    def test_local_admin_opens_directly_and_external_admin_uses_login_cookie(self):
        local = self._json_request("/v1/local-configuration")
        self.assertIn("draft", local)

        access = self._json_request(
            "/v1/admin/access",
            {"username": "traffadmin", "password": "enkel-lokal-kod"},
        )
        self.assertEqual(access["username"], "traffadmin")
        self.assertTrue(access["password_configured"])

        self.application.config = HTTPServerConfig(
            local_development=True,
            force_external_auth=True,
        )
        status = self._json_request("/v1/auth/status")
        self.assertFalse(status["authenticated"])
        self.assertEqual(status["access_mode"], "external")

        with self.assertRaises(HTTPError) as denied:
            self._json_request("/v1/local-configuration")
        self.assertEqual(denied.exception.code, 401)

        with self.assertRaises(HTTPError) as invalid:
            self._json_request(
                "/v1/auth/login",
                {"username": "traffadmin", "password": "fel-losenord"},
            )
        self.assertEqual(invalid.exception.code, 401)

        request = Request(
            f"{self.base_url}/v1/auth/login",
            data=json.dumps(
                {"username": "traffadmin", "password": "enkel-lokal-kod"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        authenticated = Request(
            f"{self.base_url}/v1/admin/access",
            headers={"Cookie": cookie, "Accept": "application/json"},
        )
        with urlopen(authenticated, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["username"], "traffadmin")

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

    def test_swift_panel_gets_scoped_http_token_for_runtime_bootstrap(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "swift-panel-runtime",
                "display_name": "TrainMeet Tambox på iPhone",
                "device_kind": "swift_panel",
            },
            expected_status=201,
        )

        self.assertTrue(paired["access_token"])
        snapshots = self._json_request("/v1/snapshots", token=paired["access_token"])
        self.assertEqual(
            [snapshot["panel_id"] for snapshot in snapshots["snapshots"]],
            ["panel-a"],
        )

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

    def test_admin_validates_runtime_without_installing_it(self):
        validation = self._json_request(
            "/v1/runtime/validate",
            {"package": runtime_package_v2()},
        )

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["meet"]["name"], "Sommarträffen")
        self.assertEqual(validation["counts"]["stations"], 2)
        self.assertEqual(validation["counts"]["services"], 2)
        self.assertEqual(validation["stations"][0]["code"], "CDA")
        self.assertFalse(self.runtime_store.summary()["configured"])

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

    def test_admin_can_factory_reset_server_with_explicit_confirmation(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "reset-admin",
                "display_name": "Nollställningsadmin",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        self.application.config = HTTPServerConfig(
            local_development=True,
            allow_restart=True,
        )
        reset = self._json_request(
            "/v1/server/factory-reset",
            {"confirmation": "NOLLSTÄLL"},
            token=paired["access_token"],
            expected_status=202,
        )
        self.assertEqual(reset["status"], "resetting")
        self.assertTrue(self.server.factory_reset_requested)
        self.assertTrue(self.server.restart_requested)

    def test_external_admin_can_reset_meet_data_without_factory_reset(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "remote-reset-admin",
                "display_name": "Extern admin",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        self.application.config = HTTPServerConfig(
            local_development=True,
            allow_restart=True,
            force_external_auth=True,
        )
        reset = self._json_request(
            "/v1/server/operational-reset",
            {"confirmation": "NOLLSTÄLL"},
            token=paired["access_token"],
            expected_status=202,
        )
        self.assertEqual("operational", reset["mode"])
        self.assertTrue(self.server.operational_reset_requested)
        self.assertFalse(self.server.factory_reset_requested)

    def test_external_admin_cannot_factory_reset_server(self):
        paired = self._json_request(
            "/v1/pair",
            {
                "pairing_code": self.pairing_code,
                "client_id": "remote-factory-admin",
                "display_name": "Extern admin",
                "device_kind": "web_admin",
            },
            expected_status=201,
        )
        self.application.config = HTTPServerConfig(
            local_development=True,
            allow_restart=True,
            force_external_auth=True,
        )
        with self.assertRaises(HTTPError) as denied:
            self._json_request(
                "/v1/server/factory-reset",
                {"confirmation": "NOLLSTÄLL"},
                token=paired["access_token"],
                expected_status=202,
            )
        self.assertEqual(403, denied.exception.code)
        self.assertFalse(self.server.factory_reset_requested)

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

    @staticmethod
    def _linked_runtime(token: str, _url: str, manifest_only: bool):
        if token != "central-test-link":
            raise ValueError("unexpected runtime link")
        package = runtime_package_v2(publication_id="publication-v2-second")
        if manifest_only:
            return CentralRuntimeManifest(
                publication_id=package["publication_id"],
                published_at=package["published_at"],
                package_checksum="fixture-checksum",
            )
        return CentralRuntimeDownload(package=package, link_token=token)


if __name__ == "__main__":
    unittest.main()
