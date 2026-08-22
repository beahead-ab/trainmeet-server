"""D2: the server edits by producing a new revision of the package.

The thing that was missing was not editing - a server could always build a
configuration of its own. What was missing was editing *the package Cloud
published*, which is the only thing worth correcting during a meet.

The old local configuration carried stations, lines and panels but never
trains, so the package it built had `trains: []` and the server ran a railway
with nothing on it. That special case is what these tests hold shut.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime_fixture import runtime_package_v3
from tmbox_gateway.local_config import (
    LOCAL_ID_PREFIX,
    LocalConfigurationError,
    SQLiteLocalConfigurationStore,
    empty_local_configuration,
    local_configuration_from_publication,
    local_configuration_runtime_package,
    local_id,
    validate_local_configuration,
)
from tmbox_gateway.runtime import SQLiteRuntimeStore


class WorkingCopyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "runtime.db"
        self.local = SQLiteLocalConfigurationStore(self.database)
        self.runtime = SQLiteRuntimeStore(self.database)

    def tearDown(self):
        self.local.close()
        self.runtime.close()
        self.directory.cleanup()

    # --------------------------------------------------- the whole chain

    def test_a_fetched_package_can_be_opened_edited_and_activated(self):
        """The acceptance test from the issue, start to finish."""
        cloud = self.runtime.install(runtime_package_v3())

        state = self.local.seed_from_publication(cloud.payload)
        self.assertTrue(state["draft"]["trains"], "arbetskopian ska bära tåg")

        draft = state["draft"]
        target = draft["trains"][0]
        original = target["departure_time"]
        target["departure_time"] = "09:47"
        saved = self.local.save(draft, expected_revision=state["revision"])

        installed = self.runtime.install(self.local.runtime_package())

        row = next(t for t in installed.payload["trains"] if t["id"] == target["id"])
        self.assertEqual("09:47", row["departure_time"])
        self.assertNotEqual(original, row["departure_time"])

        # config_version is what a box watches to know it must re-read.
        self.assertNotEqual(cloud.publication_id, installed.publication_id)
        self.assertEqual(
            f"{cloud.publication_id}+local-r{saved['revision']}",
            installed.publication_id,
            "revisionen ska bära sin Cloud-publikation i namnet",
        )

    def test_the_package_a_local_revision_builds_is_not_empty_of_traffic(self):
        """The special case D2 removed: `trains: []` on a running railway."""
        cloud = self.runtime.install(runtime_package_v3())
        self.local.seed_from_publication(cloud.payload)
        package = self.local.runtime_package()

        self.assertTrue(package["trains"])
        self.assertTrue(package["tracks"])
        self.assertTrue(package["routes"])
        self.assertTrue(package["services"])

    def test_routes_and_services_follow_an_edited_time(self):
        """They are derived, not stored, so a corrected time cannot disagree
        with the timetable that displays it."""
        cloud = self.runtime.install(runtime_package_v3())
        state = self.local.seed_from_publication(cloud.payload)
        draft = state["draft"]
        draft["trains"][0]["departure_time"] = "23:59"
        draft["trains"][0]["arrival_time"] = None
        self.local.save(draft, expected_revision=state["revision"])

        package = self.local.runtime_package()
        moved = draft["trains"][0]
        stop = next(
            route for route in package["routes"]
            if route["station_id"] == moved["station_id"]
            and route["train_number"] == moved["train_number"]
        )
        self.assertEqual("23:59", stop["departure_time"])

    # ------------------------------------------------------- id namespace

    def test_rows_the_server_mints_cannot_collide_with_cloud(self):
        self.assertTrue(local_id("service", "421", "Dagl").startswith(LOCAL_ID_PREFIX))
        cloud = self.runtime.install(runtime_package_v3())
        self.local.seed_from_publication(cloud.payload)
        package = self.local.runtime_package()
        for service in package["services"]:
            self.assertTrue(service["id"].startswith(LOCAL_ID_PREFIX), service["id"])

    def test_cloud_ids_are_carried_across_unchanged(self):
        """Renaming them would make it impossible to tell which rows Cloud
        published and which the server added."""
        cloud = self.runtime.install(runtime_package_v3())
        draft = local_configuration_from_publication(cloud.payload)
        cloud_train_ids = {row["id"] for row in cloud.payload["trains"]}
        for row in draft["trains"]:
            self.assertIn(row["id"], cloud_train_ids)

    # ----------------------------------------------------- one model, not two

    def test_a_draft_written_before_trains_existed_still_reads(self):
        """Reading migrates it, so an existing meet is not blocked."""
        old = empty_local_configuration()
        del old["tracks"]
        del old["trains"]
        migrated = validate_local_configuration(old)
        self.assertEqual([], migrated["tracks"])
        self.assertEqual([], migrated["trains"])

    def test_a_runnable_configuration_with_no_trains_still_builds_a_package(self):
        """A meet built by hand on the night, before any timetable exists, is
        a legitimate starting point - just not the only thing the model can
        express any more."""
        draft = empty_local_configuration()
        draft["stations"] = [{"id": "a", "code": "A", "name": "A"}]
        draft["panels"] = [{
            "id": "p1", "station_id": "a", "name": "A TMBox",
            "slots": {"A": None, "B": None, "C": None, "D": None},
        }]
        package = local_configuration_runtime_package(
            validate_local_configuration(draft), revision=1
        )
        self.assertEqual([], package["trains"])
        self.assertEqual([], package["routes"])
        self.assertEqual([], package["services"])

    # ------------------------------------------------------------ refusals

    def test_a_train_on_a_station_that_does_not_exist_is_refused(self):
        draft = empty_local_configuration()
        draft["trains"] = [{
            "id": "t1", "train_number": "93", "station_id": "spöke",
            "days": "Dagl", "departure_time": "10:00",
        }]
        with self.assertRaisesRegex(LocalConfigurationError, "station som inte finns"):
            validate_local_configuration(draft)

    def test_a_train_with_neither_arrival_nor_departure_is_refused(self):
        draft = empty_local_configuration()
        draft["stations"] = [{"id": "a", "code": "A", "name": "A"}]
        draft["trains"] = [{"id": "t1", "train_number": "93", "station_id": "a", "days": "Dagl"}]
        with self.assertRaisesRegex(LocalConfigurationError, "saknar både"):
            validate_local_configuration(draft)

    def test_a_train_on_another_stations_track_is_refused(self):
        draft = empty_local_configuration()
        draft["stations"] = [
            {"id": "a", "code": "A", "name": "A"},
            {"id": "b", "code": "B", "name": "B"},
        ]
        draft["tracks"] = [{"id": "tr-b", "station_id": "b", "display_label": "1"}]
        draft["trains"] = [{
            "id": "t1", "train_number": "93", "station_id": "a",
            "days": "Dagl", "departure_time": "10:00", "track_id": "tr-b",
        }]
        with self.assertRaisesRegex(LocalConfigurationError, "spår på fel station"):
            validate_local_configuration(draft)


class SeedEndpointTests(unittest.TestCase):
    """The HTTP door onto the same thing, since that is how it is reached."""

    def setUp(self):
        from tmbox_gateway.engine import TrafficEngine
        from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication
        from tmbox_gateway.identity import IdentityStore, PairingService
        from session_fixture import sample_session
        from tmbox_gateway.models import DispatchMode

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.identities = IdentityStore(root / "identity.db")
        self.runtime = SQLiteRuntimeStore(root / "runtime.db")
        self.local = SQLiteLocalConfigurationStore(root / "runtime.db")
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

    def test_seeding_without_an_active_publication_says_so(self):
        from tmbox_gateway.http_server import HTTPAPIError

        with self.assertRaises(HTTPAPIError) as refused:
            self.application.seed_local_configuration(self.client)
        self.assertEqual("no_active_publication", refused.exception.code)

    def test_seeding_opens_the_active_publication_for_editing(self):
        self.runtime.install(runtime_package_v3())
        state = self.application.seed_local_configuration(self.client)
        self.assertTrue(state["configured"])
        self.assertTrue(state["draft"]["trains"])
        self.assertTrue(state["draft"]["base_publication_id"])

    def test_seeding_is_admin_only(self):
        from tmbox_gateway.http_server import HTTPAPIError
        from tmbox_gateway.identity import DeviceKind, PairedClient

        box = PairedClient(
            client_id="tmbox-1", display_name="TMBox",
            kind=DeviceKind.ESP32_PANEL, panel_ids=(),
        )
        with self.assertRaises(HTTPAPIError):
            self.application.seed_local_configuration(box)


if __name__ == "__main__":
    unittest.main()
