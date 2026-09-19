"""Cloud authors config in every connectivity state; Server only operates it.

Legacy modes/drafts remain readable at the storage layer for archive/backup,
but no public endpoint can reopen the local editor or discard its old history.
"""
from __future__ import annotations

import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from runtime_fixture import runtime_package_v3
from test_pending_revisions import CloudDeliveryFixture, dispatch_request
from tmbox_gateway.central_sync import CentralSyncError
from tmbox_gateway.identity import DeviceKind, PairedClient
from tmbox_gateway.local_config import SQLiteLocalConfigurationStore
from tmbox_gateway.runtime import SQLiteRuntimeStore


EDITOR_ROUTES = (
    "/v1/local-configuration", "/v1/local-configuration/seed",
    "/v1/local-configuration/build", "/v1/local-configuration/activate",
    "/v1/operating-mode", "/v1/runtime/install", "/v1/us/packages",
)


class OperatingModeTests(CloudDeliveryFixture):
    def test_authoring_is_cloud_only_regardless_of_legacy_mode(self):
        for mode in ("cloud-linked", "offline-meet"):
            self.runtime.set_operating_mode(mode)
            state = self.application.server_context(self.client)
            self.assertEqual("cloud", state["config_authority"])
            self.assertFalse(state["local_editing"])
            self.assertEqual(["administration", "tkl", "tmbox"], state["available_workspaces"])

    def test_old_editor_post_routes_are_gone_even_for_admin(self):
        before = self.runtime.active()
        draft_before = self.local.current()
        for route in EDITOR_ROUTES:
            with self.subTest(route=route):
                status, body = dispatch_request(self.application, self.client, route,
                    {"mode": "offline-meet", "package": self.offered, "draft": {}})
                self.assertEqual(HTTPStatus.GONE, status)
                self.assertEqual("cloud_authoring_only", body["code"])
        self.assertEqual(before, self.runtime.active())
        self.assertEqual(draft_before, self.local.current())

    def test_old_editor_get_routes_do_not_advertise_an_editable_draft(self):
        for route in ("/v1/local-configuration", "/v1/operating-mode", "/v1/build/topology"):
            with self.subTest(route=route):
                status, body = dispatch_request(self.application, self.client, route, method="GET")
                self.assertEqual(HTTPStatus.GONE, status)
                self.assertEqual("cloud_authoring_only", body["code"])

    def test_public_editor_block_still_requires_admin(self):
        panel = PairedClient("box", "CDA TMBox", DeviceKind.ESP32_PANEL, ("panel-a",))
        for route in EDITOR_ROUTES:
            status, _ = dispatch_request(self.application, panel, route)
            self.assertEqual(HTTPStatus.FORBIDDEN, status)

    def test_network_outage_never_unlocks_local_editing(self):
        before = self.clock_record()
        def offline(*args):
            raise CentralSyncError("Cloud kan inte nås")
        self.application.linked_runtime_fetcher = offline
        with self.assertRaises(CentralSyncError):
            self.application.auto_sync_cloud_runtime()
        self.assertEqual(before, self.clock_record())
        self.assertFalse(self.application.server_context(self.client)["local_editing"])
        for route in EDITOR_ROUTES:
            status, _ = dispatch_request(self.application, self.client, route)
            self.assertEqual(HTTPStatus.GONE, status)

    def test_legacy_offline_mode_cannot_disable_the_new_cloud_pipeline(self):
        self.runtime.set_operating_mode("offline-meet")
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["activated"])
        self.assertEqual("cloud-second", self.runtime.active().publication_id)
        self.assertFalse(self.application.server_context(self.client)["local_editing"])

    def test_old_discard_confirmation_does_not_delete_local_archives(self):
        self.local.seed_from_publication(self.runtime.active().payload)
        draft = self.local.current()["draft"]
        draft["trains"][0]["departure_time"] = "09:47"
        self.local.save(draft)
        archive = self.runtime.install(self.local.runtime_package(), activate=False)
        before = self.local.current()
        status, _ = dispatch_request(self.application, self.client, "/v1/operating-mode",
                                    {"mode": "cloud-linked", "discard_local_revisions": True})
        self.assertEqual(HTTPStatus.GONE, status)
        self.assertEqual(before, self.local.current())
        self.assertIsNotNone(self.runtime.publication(archive.publication_id))
        self.assertEqual("cloud-first", self.runtime.active().publication_id)

    def test_clock_controls_remain_runtime_actions(self):
        first = self.runtime.active()
        started = self.application.control_clock(self.client, {"action": "start", "time": "07:00", "speed": 4})
        self.assertTrue(started["running"])
        self.assertFalse(self.application.control_clock(self.client, {"action": "stop"})["running"])
        self.assertEqual(first, self.runtime.active())


class LegacyRevisionArchiveTests(unittest.TestCase):
    """Storage compatibility is intentionally retained for old drafts/backups.

    These direct-store tests do not expose an editor through the Server API.
    Existing publications and revisions must remain recoverable after upgrade.
    """
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "runtime.db"
        self.runtime = SQLiteRuntimeStore(database)
        self.local = SQLiteLocalConfigurationStore(database)
        self.addCleanup(self.runtime.close)
        self.addCleanup(self.local.close)

    def test_legacy_revision_retains_its_original_corrected_timetable(self):
        cloud = self.runtime.install(runtime_package_v3())
        state = self.local.seed_from_publication(cloud.payload)
        draft = state["draft"]
        draft["trains"][0]["departure_time"] = "09:47"
        saved = self.local.save(draft, expected_revision=state["revision"])
        archive = self.runtime.install(self.local.runtime_package(), activate=False)
        self.assertIn(f"+local-r{saved['revision']}", archive.publication_id)
        restored = self.runtime.publication(archive.publication_id)
        movement = next(row for row in restored.payload["trains"] if row["id"] == draft["trains"][0]["id"])
        self.assertEqual("09:47", movement["departure_time"])
        self.assertEqual(cloud, self.runtime.active())

    def test_cloud_base_is_not_overwritten_by_archived_local_revision(self):
        cloud = self.runtime.install(runtime_package_v3())
        state = self.local.seed_from_publication(cloud.payload)
        draft = state["draft"]
        draft["trains"][0]["departure_time"] = "09:47"
        self.local.save(draft, expected_revision=state["revision"])
        self.runtime.install(self.local.runtime_package(), activate=False)
        self.assertEqual(cloud, self.runtime.publication(cloud.publication_id))

    def test_every_legacy_revision_remains_listed(self):
        cloud = self.runtime.install(runtime_package_v3())
        self.local.seed_from_publication(cloud.payload)
        for departure in ("09:47", "09:48"):
            draft = self.local.current()["draft"]
            draft["trains"][0]["departure_time"] = departure
            self.local.save(draft)
            self.runtime.install(self.local.runtime_package(), activate=False)
        self.assertEqual(2, len(self.runtime.local_revisions()))
        self.assertEqual(cloud, self.runtime.active())


if __name__ == "__main__":
    unittest.main()
