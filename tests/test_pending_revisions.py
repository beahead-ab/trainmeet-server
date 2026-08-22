"""T4: a Cloud revision never takes over on its own.

The poller runs every fifteen seconds with nobody watching. It used to call
`install()`, whose `activate` defaults to True, and then restart the server to
apply the result. An operator who had corrected three departure times at 13:00
lost them when Cloud published at 14:00 - and the meet restarted underneath
them while it happened.

These tests hold that shut from both ends: polling must not activate, and what
is running must survive polling. The rest is about making the decision
possible - showing what waits, showing what saying yes would replace, and
letting somebody say it.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.central_sync import CentralRuntimeDownload, CentralRuntimeManifest
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.local_config import SQLiteLocalConfigurationStore
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore

LINK = "central-test-link"


def cloud_package(publication_id: str = "cloud-second") -> dict:
    return runtime_package_v3(publication_id=publication_id)


class PendingRevisionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        database = root / "runtime.db"
        self.identities = IdentityStore(root / "identity.db")
        self.runtime = SQLiteRuntimeStore(database)
        self.local = SQLiteLocalConfigurationStore(database)
        self.offered = cloud_package()
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True),
            runtime_store=self.runtime,
            local_configuration_store=self.local,
            linked_runtime_fetcher=self._cloud,
        )
        self.client = self.application.local_admin()

    def tearDown(self):
        self.local.close()
        self.runtime.close()
        self.directory.cleanup()

    def _cloud(self, token: str, _url: str, manifest_only: bool):
        if token != LINK:
            raise ValueError("unexpected runtime link")
        if manifest_only:
            return CentralRuntimeManifest(
                publication_id=self.offered["publication_id"],
                published_at=self.offered["published_at"],
                package_checksum="fixture-checksum",
            )
        return CentralRuntimeDownload(package=self.offered, link_token=token)

    def _linked_and_running(self, publication_id: str = "cloud-first") -> None:
        self.runtime.install(runtime_package_v3(publication_id=publication_id))
        self.runtime.save_link_token(LINK)
        self.application.configure_cloud_auto_sync(self.client, {"enabled": True})

    # ----------------------------------------------- polling does not activate

    def test_polling_does_not_activate_what_it_fetches(self):
        self._linked_and_running()
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["pending"])
        self.assertEqual("cloud-first", self.runtime.active().publication_id)

    def test_the_running_revision_survives_repeated_polling(self):
        """Fifteen seconds is not a long time. Nor is an afternoon of them."""
        self._linked_and_running()
        for _ in range(10):
            self.application.auto_sync_cloud_runtime()
        self.assertEqual("cloud-first", self.runtime.active().publication_id)

    def test_a_local_revision_survives_polling(self):
        """The case the bug actually cost work in.

        A local revision is an operator's own correction, made during the meet
        and activated by them. Polling must not replace it.
        """
        self._linked_and_running()
        corrected = runtime_package_v3(publication_id="cloud-first+local-r1")
        self.runtime.install(corrected)
        self.assertEqual("cloud-first+local-r1", self.runtime.active().publication_id)

        self.application.auto_sync_cloud_runtime()

        self.assertEqual("cloud-first+local-r1", self.runtime.active().publication_id)
        self.assertIn("cloud-first+local-r1", self.runtime.local_revisions())

    def test_polling_does_not_run_at_all_in_offline_mode(self):
        """Not "fetch but do not activate" - do not fetch.

        Offline-meet is a deliberate choice to leave Cloud. Queueing revisions
        nobody asked for would put a "waiting" badge over work that left on
        purpose.
        """
        self._linked_and_running()
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        result = self.application.auto_sync_cloud_runtime()
        self.assertFalse(result["checked"])
        self.assertEqual("offline_meet", result["reason"])
        self.assertIsNone(self.runtime.pending_publication())

    def test_polling_twice_does_not_refetch_what_is_already_waiting(self):
        self._linked_and_running()
        first = self.application.auto_sync_cloud_runtime()
        second = self.application.auto_sync_cloud_runtime()
        self.assertEqual(first["publication_id"], second["publication_id"])
        self.assertTrue(second["pending"])

    # -------------------------------------------------- the pending is visible

    def test_nothing_waiting_says_so(self):
        self._linked_and_running()
        self.assertEqual({"pending": False}, self.application.pending_revision_state(self.client))

    def test_a_waiting_revision_is_exposed(self):
        self._linked_and_running()
        self.application.auto_sync_cloud_runtime()
        state = self.application.pending_revision_state(self.client)
        self.assertTrue(state["pending"])
        self.assertEqual("cloud-second", state["publication_id"])
        self.assertEqual("cloud-first", state["active_publication_id"])

    def test_the_diff_names_a_renamed_station(self):
        """Counting rows is not enough to decide with."""
        self._linked_and_running()
        self.offered = cloud_package()
        self.offered["stations"][1]["name"] = "Lekeberg norra"
        self.application.auto_sync_cloud_runtime()

        changes = self.application.pending_revision_state(self.client)["changes"]
        self.assertFalse(changes["first_activation"])
        self.assertEqual(1, changes["stations"]["renamed"]["count"])
        self.assertIn("Lekeberg → Lekeberg norra", changes["stations"]["renamed"]["names"])

    def test_the_diff_names_the_trains_whose_times_move(self):
        self._linked_and_running()
        self.offered = cloud_package()
        self.offered["trains"][0]["departure_time"] = "09:44"
        self.application.auto_sync_cloud_runtime()

        timetable = self.application.pending_revision_state(self.client)["changes"]["timetable"]
        self.assertEqual(1, timetable["changed"]["count"])
        self.assertIn("101", timetable["changed"]["names"])

    def test_the_diff_counts_added_and_removed_movements(self):
        self._linked_and_running()
        self.offered = cloud_package()
        removed = self.offered["trains"].pop()
        extra = copy.deepcopy(removed)
        extra["id"] = "movement-new"
        extra["train_number"] = "909"
        self.offered["trains"].append(extra)
        self.application.auto_sync_cloud_runtime()

        timetable = self.application.pending_revision_state(self.client)["changes"]["timetable"]
        self.assertEqual(1, timetable["added"])
        self.assertEqual(1, timetable["removed"])

    def test_a_first_activation_says_so_instead_of_diffing_against_nothing(self):
        self.runtime.save_link_token(LINK)
        self.application.configure_cloud_auto_sync(self.client, {"enabled": True})
        self.application.auto_sync_cloud_runtime()
        changes = self.application.pending_revision_state(self.client)["changes"]
        self.assertTrue(changes["first_activation"])

    # ------------------------------------------------- explicit activation

    def test_an_explicit_activation_works(self):
        self._linked_and_running()
        self.application.auto_sync_cloud_runtime()
        result = self.application.activate_pending_revision(
            self.client, {"publication_id": "cloud-second"}
        )
        self.assertTrue(result["activated"])
        self.assertEqual("cloud-second", self.runtime.active().publication_id)

    def test_activating_clears_the_waiting_marker(self):
        self._linked_and_running()
        self.application.auto_sync_cloud_runtime()
        self.application.activate_pending_revision(self.client, {"publication_id": "cloud-second"})
        self.assertIsNone(self.runtime.pending_publication())
        self.assertEqual({"pending": False}, self.application.pending_revision_state(self.client))

    def test_activating_without_naming_the_revision_is_refused(self):
        """A stale button in a tab left open since this morning.

        Without the id, that button would activate whatever arrived since -
        the same silent overwrite, entering through the UI instead of the
        poller.
        """
        self._linked_and_running()
        self.application.auto_sync_cloud_runtime()
        with self.assertRaises(HTTPAPIError) as raised:
            self.application.activate_pending_revision(self.client, {})
        self.assertEqual(HTTPStatus.CONFLICT, raised.exception.status)
        self.assertEqual("cloud-first", self.runtime.active().publication_id)

    def test_activating_the_wrong_revision_is_refused(self):
        self._linked_and_running()
        self.application.auto_sync_cloud_runtime()
        with self.assertRaises(HTTPAPIError) as raised:
            self.application.activate_pending_revision(
                self.client, {"publication_id": "cloud-something-else"}
            )
        self.assertEqual("pending_revision_changed", raised.exception.code)

    def test_activating_when_nothing_waits_is_refused(self):
        self._linked_and_running()
        with self.assertRaises(HTTPAPIError) as raised:
            self.application.activate_pending_revision(
                self.client, {"publication_id": "cloud-second"}
            )
        self.assertEqual("no_pending_revision", raised.exception.code)

    def test_reading_what_waits_needs_an_admin(self):
        from tmbox_gateway.identity import DeviceKind, PairedClient

        panel = PairedClient(
            client_id="tmbox-1",
            display_name="CDA TMBox",
            kind=DeviceKind.ESP32_PANEL,
            panel_ids=("panel-a",),
        )
        with self.assertRaises(HTTPAPIError) as raised:
            self.application.pending_revision_state(panel)
        self.assertEqual(HTTPStatus.FORBIDDEN, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
