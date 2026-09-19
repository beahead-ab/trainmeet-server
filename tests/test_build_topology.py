"""The former build API is closed; legacy draft serialization remains readable.

Archived topology helpers are retained for older backups. Their direct calls
below are storage compatibility tests, not public authoring authorization.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from test_pending_revisions import CloudDeliveryFixture, dispatch_request
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.local_config import SQLiteLocalConfigurationStore
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore

WEB = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web"


class BuildTopologyAPITests(CloudDeliveryFixture):
    def test_build_topology_endpoint_is_gone(self):
        status, body = dispatch_request(self.application, self.client, "/v1/build/topology", method="GET")
        self.assertEqual(HTTPStatus.GONE, status)
        self.assertEqual("cloud_authoring_only", body["code"])

    def test_topology_cannot_be_written_through_old_build_routes(self):
        before = self.runtime.active()
        for route in ("/v1/local-configuration", "/v1/local-configuration/build", "/v1/local-configuration/activate"):
            status, _ = dispatch_request(self.application, self.client, route, {"draft": {"stations": []}})
            self.assertEqual(HTTPStatus.GONE, status)
        self.assertEqual(before, self.runtime.active())

    def test_published_topology_remains_visible_without_build_workspace(self):
        display = self.application.display_snapshot()
        self.assertEqual("cloud-first", display["publication_id"])
        self.assertEqual(["station-a", "station-b"], [s["id"] for s in display["stations"]])
        self.assertEqual(["connection-a-b"], [c["id"] for c in display["connections"]])
        self.assertNotIn("build", self.application.server_context(self.client)["available_workspaces"])

    def test_saved_legacy_draft_does_not_shadow_published_topology(self):
        self.local.seed_from_publication(self.runtime.active().payload)
        draft = self.local.current()["draft"]
        draft["stations"][0]["name"] = "Archived local name"
        self.local.save(draft)
        display = self.application.display_snapshot()
        self.assertNotIn("Archived local name", [s["name"] for s in display["stations"]])
        self.assertEqual("Archived local name", self.local.current()["draft"]["stations"][0]["name"])


class LegacyBuildTopologyArchiveTests(unittest.TestCase):
    """Internal legacy serializer only; the HTTP authoring route is gone."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        database = root / "runtime.db"
        self.identities = IdentityStore(root / "identity.db")
        self.runtime = SQLiteRuntimeStore(database)
        self.local = SQLiteLocalConfigurationStore(database)
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True),
            runtime_store=self.runtime,
            local_configuration_store=self.local,
        )
        self.client = self.application.local_admin()

    def tearDown(self):
        self.local.close()
        self.runtime.close()
        self.directory.cleanup()

    def _cloud_linked_with_package(self, package: dict | None = None) -> dict:
        self.runtime.install(package or runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        return self.application.build_topology(self.client)

    # ------------------------------------------------- låst läge (Cloud)

    def test_a_cloud_package_comes_back_locked(self):
        topology = self._cloud_linked_with_package()
        self.assertTrue(topology["locked"])
        self.assertEqual("cloud", topology["source"])

    def test_the_cloud_package_stations_are_the_ones_returned(self):
        topology = self._cloud_linked_with_package()
        self.assertEqual(
            [("station-a", "CDA"), ("station-b", "LEK")],
            [(station["id"], station["code"]) for station in topology["stations"]],
        )

    def test_stations_come_back_in_the_order_cloud_drew_them(self):
        """diagram_order, not insertion order.

        The list *is* the line, so a package that arrives with its stations in
        some other order must still be read from one end of the line to the
        other. Reversing the payload must not reverse the view.
        """
        package = runtime_package_v3()
        package["stations"][0]["diagram_order"] = 5
        package["stations"][1]["diagram_order"] = 1
        topology = self._cloud_linked_with_package(package)
        self.assertEqual(
            ["station-b", "station-a"],
            [station["id"] for station in topology["stations"]],
        )
        self.assertEqual([1, 2], [station["order"] for station in topology["stations"]])

    def test_connections_and_panels_come_back_too(self):
        topology = self._cloud_linked_with_package()
        self.assertEqual(["connection-a-b"], [c["id"] for c in topology["connections"]])
        self.assertEqual(
            ["panel-a", "panel-b"], [panel["id"] for panel in topology["panels"]]
        )

    def test_a_panel_slot_carries_the_connection_id_not_a_station(self):
        """The two-step lookup the view has to do, pinned.

        A slot points at a *link*; the neighbour is that link's far end seen
        from the panel's own station. If this ever became a station id the
        view would still render - it would just quietly stop agreeing with
        the box on the table.
        """
        topology = self._cloud_linked_with_package()
        panel = next(p for p in topology["panels"] if p["id"] == "panel-a")
        self.assertEqual("connection-a-b", panel["slots"]["A"])
        self.assertIsNone(panel["slots"]["B"])
        link = topology["connections"][0]
        self.assertIn(panel["station_id"], (link["station_a_id"], link["station_b_id"]))

    def test_every_slot_letter_is_present_even_when_empty(self):
        topology = self._cloud_linked_with_package()
        for panel in topology["panels"]:
            self.assertEqual(["A", "B", "C", "D"], sorted(panel["slots"]))

    # ------------------------------------------- öppet läge (lokalt utkast)

    def test_a_local_draft_comes_back_unlocked(self):
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        topology = self.application.build_topology(self.client)
        self.assertFalse(topology["locked"])
        self.assertEqual("lokal", topology["source"])

    def test_an_empty_local_draft_is_empty_not_the_cloud_package(self):
        """The open view must not fall back to showing the active package.

        That would be the worst kind of wrong: fields that look editable,
        holding rows that belong to somebody else.
        """
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        topology = self.application.build_topology(self.client)
        self.assertEqual([], topology["stations"])
        self.assertEqual([], topology["connections"])

    def test_the_seeded_draft_is_what_the_open_view_shows(self):
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        self.application.seed_local_configuration(self.client)
        topology = self.application.build_topology(self.client)
        self.assertEqual(
            ["station-a", "station-b"],
            [station["id"] for station in topology["stations"]],
        )
        self.assertFalse(topology["locked"])

    # -------------------------------------------------------------- åtkomst

    def test_reading_the_topology_needs_an_admin(self):
        """A TMBox on the table must not be able to read the build view."""
        panel = PairedClient(
            client_id="tmbox-1",
            display_name="CDA TMBox",
            kind=DeviceKind.ESP32_PANEL,
            panel_ids=("panel-a",),
        )
        with self.assertRaises(HTTPAPIError) as raised:
            self.application.build_topology(panel)
        self.assertEqual(HTTPStatus.FORBIDDEN, raised.exception.status)


class CloudTopologyViewTests(unittest.TestCase):
    """Cloud owns topology; Server renders the active published meet read-only."""

    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "app.js").read_text(encoding="utf-8")

    def test_authoring_forms_and_actions_are_removed_not_hidden(self):
        for marker in ('data-build-panel="bana"', 'id="bana-shortcut"',
                       'id="bana-seed"', 'id="station-editor"', 'id="tid-save"'):
            self.assertNotIn(marker, self.html)
        for action in ("withDraft(", "editStation(", "editConnection(", "tidSave(",
                       "/v1/local-configuration", "/v1/timetable"):
            self.assertNotIn(action, self.js)

    def test_runtime_topology_is_still_visible(self):
        self.assertIn('id="overview-topology"', self.html)
        self.assertIn("function renderTopology(", self.js)
        self.assertIn("function renderOverviewTopology()", self.js)
        self.assertIn('id="overview-route-list"', self.html)

    def test_pending_config_has_status_not_a_local_edit_activation_button(self):
        self.assertIn('id="cloud-version-state"', self.html)
        self.assertIn("pending_publication_id", self.js)
        self.assertIn("väntar på säker aktivering", self.js)
        self.assertNotIn('id="activate-pending"', self.html)

    def test_device_station_assignment_remains_runtime_admin(self):
        self.assertIn('data-admin-section="devices"', self.html)
        self.assertIn('id="device-form-modal"', self.html)
        self.assertIn('"/v1/devices/assign"', self.js)
