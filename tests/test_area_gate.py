"""T3: the gate is about the line, not about the whole draft.

The old rule refused every local change while Cloud was linked. It made two
different things indistinguishable: correcting a departure time was refused for
the same reason as redrawing the line.

During a meet the server *is* the operation. Trains run late and movements get
cancelled, and there is no route through Cloud for that at 13:40 on a Saturday.
The line is the other way round - interpreted from the meet's own documents,
reviewed in Cloud, drawn once, and then still.

Both halves need holding. A gate that is too wide makes the timetable
uneditable; a gate that is too narrow lets somebody redraw a line that Cloud
will overwrite on its next publication.
"""

from __future__ import annotations

import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.local_config import (
    SQLiteLocalConfigurationStore,
    empty_local_configuration,
    local_configuration_from_publication,
)
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore


class AreaGateTests(unittest.TestCase):
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
        self.runtime.install(runtime_package_v3())

    def tearDown(self):
        self.local.close()
        self.runtime.close()
        self.directory.cleanup()

    def _cloud_mode_with_a_working_copy(self) -> dict:
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        self.application.seed_local_configuration(self.client)
        return self.local.current()["draft"]

    # ------------------------------------------------ the timetable is open

    def test_a_departure_time_can_be_corrected_in_cloud_mode(self):
        draft = self._cloud_mode_with_a_working_copy()
        draft["trains"][0]["departure_time"] = "09:44"
        saved = self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertEqual("09:44", saved["draft"]["trains"][0]["departure_time"])

    def test_a_movement_can_be_cancelled_in_cloud_mode(self):
        draft = self._cloud_mode_with_a_working_copy()
        before = len(draft["trains"])
        draft["trains"].pop()
        saved = self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertEqual(before - 1, len(saved["draft"]["trains"]))

    def test_a_corrected_timetable_becomes_a_local_revision(self):
        """The decision in one test: correct, activate, and it is a revision.

        Not an overwrite of what Cloud published - a version on top of it, so
        it stays visible what was changed and what it was changed from.
        """
        draft = self._cloud_mode_with_a_working_copy()
        draft["trains"][0]["departure_time"] = "09:44"
        self.application.save_local_configuration(self.client, {"draft": draft})

        self.application.activate_local_configuration(self.client, {})

        revisions = self.runtime.local_revisions()
        self.assertTrue(revisions, "en rättad tidtabell ska ge en lokal revision")
        self.assertIn("+local-r", revisions[-1])
        self.assertEqual(revisions[-1], self.runtime.active().publication_id)

    def test_the_local_revision_is_built_on_the_cloud_publication(self):
        draft = self._cloud_mode_with_a_working_copy()
        draft["trains"][0]["departure_time"] = "09:44"
        self.application.save_local_configuration(self.client, {"draft": draft})
        self.application.activate_local_configuration(self.client, {})
        self.assertTrue(
            self.runtime.active().publication_id.startswith("publication-2026-08-11-a"),
            "revisionen ska bära Cloud-publiceringens id som bas",
        )

    # --------------------------------------------------- the line is closed

    def test_adding_a_station_in_cloud_mode_is_refused(self):
        draft = self._cloud_mode_with_a_working_copy()
        draft["stations"].append({"id": "local-x", "code": "XXX", "name": "Påhittad"})
        with self.assertRaises(HTTPAPIError) as refused:
            self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertEqual("topology_locked_by_cloud", refused.exception.code)
        self.assertEqual(HTTPStatus.CONFLICT, refused.exception.status)
        self.assertIn("stationer", str(refused.exception))

    def test_removing_a_connection_in_cloud_mode_is_refused(self):
        draft = self._cloud_mode_with_a_working_copy()
        draft["connections"] = []
        with self.assertRaises(HTTPAPIError) as refused:
            self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertIn("sträckor", str(refused.exception))

    def test_renaming_a_station_in_cloud_mode_is_refused(self):
        draft = self._cloud_mode_with_a_working_copy()
        draft["stations"][0]["name"] = "Något annat"
        with self.assertRaises(HTTPAPIError):
            self.application.save_local_configuration(self.client, {"draft": draft})

    def test_repointing_a_panel_slot_in_cloud_mode_is_refused(self):
        draft = self._cloud_mode_with_a_working_copy()
        draft["panels"][0]["slots"]["B"] = "connection-a-b"
        with self.assertRaises(HTTPAPIError) as refused:
            self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertIn("paneler", str(refused.exception))

    def test_activation_checks_the_draft_it_is_about_to_activate(self):
        """Save and activate are two calls, and can arrive in any order.

        A draft written straight to the store, or left over from before the
        mode changed, must not become a runtime package just because activate
        was called separately.
        """
        self._cloud_mode_with_a_working_copy()
        smuggled = local_configuration_from_publication(runtime_package_v3())
        smuggled["stations"].append({"id": "local-x", "code": "XXX", "name": "Påhittad"})
        self.local.save(smuggled)

        with self.assertRaises(HTTPAPIError) as refused:
            self.application.activate_local_configuration(self.client, {})
        self.assertEqual("topology_locked_by_cloud", refused.exception.code)

    def test_row_order_alone_is_not_a_change(self):
        """A gate that refuses a save over row order is one people route around."""
        draft = self._cloud_mode_with_a_working_copy()
        draft["stations"].reverse()
        draft["connections"].reverse()
        self.application.save_local_configuration(self.client, {"draft": draft})

    # ------------------------------------------------- offline opens it all

    def test_offline_mode_opens_the_line_too(self):
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        self.application.seed_local_configuration(self.client)
        draft = self.local.current()["draft"]
        draft["stations"].append({"id": "local-x", "code": "XXX", "name": "Påhittad"})
        saved = self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertEqual(3, len(saved["draft"]["stations"]))

    def test_nothing_from_the_server_is_sent_to_cloud_by_editing(self):
        """The one-way rule, checked at the point work is actually produced."""
        calls = []
        self.application.linked_runtime_fetcher = lambda *a, **k: calls.append(a)
        draft = self._cloud_mode_with_a_working_copy()
        draft["trains"][0]["departure_time"] = "09:44"
        self.application.save_local_configuration(self.client, {"draft": draft})
        self.application.activate_local_configuration(self.client, {})
        self.assertEqual([], calls, "redigering ska inte tala med Cloud alls")


if __name__ == "__main__":
    unittest.main()


class BuildFromStationOrderTests(unittest.TestCase):
    """The shortcut the package calls the most important move in the step.

    You should not have to enter the same thing three times: the station list
    already says what the line looks like.

    Idempotence is the property that matters, and it is not a nicety. During a
    meet you press the button when you are not sure whether you already pressed
    it - so a second press has to be a no-op, and pressing it after a hand-edit
    must not undo the hand-edit.
    """

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
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})

    def tearDown(self):
        self.local.close()
        self.runtime.close()
        self.directory.cleanup()

    def _three_stations(self) -> dict:
        draft = empty_local_configuration()
        draft["stations"] = [
            {"id": "a", "code": "CDA", "name": "Charlottendahl"},
            {"id": "b", "code": "LEK", "name": "Lekeberg"},
            {"id": "c", "code": "VST", "name": "Vistberga"},
        ]
        self.local.save(draft)
        return draft

    def test_it_builds_a_connection_between_each_pair_in_order(self):
        self._three_stations()
        state = self.application.build_local_configuration_from_stations(self.client)
        links = state["draft"]["connections"]
        self.assertEqual(2, len(links))
        self.assertEqual(
            [("a", "b"), ("b", "c")],
            [(link["station_a_id"], link["station_b_id"]) for link in links],
        )

    def test_it_builds_one_panel_per_station_pointing_at_the_neighbours(self):
        """The slot pattern from the package's own screenshot.

        An end station reaches one neighbour, a through station reaches two,
        and A is the one earlier in the line.
        """
        self._three_stations()
        state = self.application.build_local_configuration_from_stations(self.client)
        panels = {panel["station_id"]: panel["slots"] for panel in state["draft"]["panels"]}
        self.assertEqual(3, len(panels))
        self.assertEqual(1, sum(1 for value in panels["a"].values() if value))
        self.assertEqual(2, sum(1 for value in panels["b"].values() if value))
        self.assertEqual(1, sum(1 for value in panels["c"].values() if value))

    def test_running_it_twice_changes_nothing(self):
        self._three_stations()
        once = self.application.build_local_configuration_from_stations(self.client)["draft"]
        twice = self.application.build_local_configuration_from_stations(self.client)["draft"]
        self.assertEqual(once["connections"], twice["connections"])
        self.assertEqual(once["panels"], twice["panels"])

    def test_running_it_ten_times_makes_no_duplicates(self):
        self._three_stations()
        for _ in range(10):
            state = self.application.build_local_configuration_from_stations(self.client)
        self.assertEqual(2, len(state["draft"]["connections"]))
        self.assertEqual(3, len(state["draft"]["panels"]))

    def test_it_does_not_undo_a_hand_edit(self):
        """A double track somebody set stays double track.

        This is the difference between "idempotent" and "regenerates from
        scratch". The second is easier to write and loses work.
        """
        self._three_stations()
        state = self.application.build_local_configuration_from_stations(self.client)
        draft = state["draft"]
        draft["connections"][0]["track_type"] = "double"
        draft["panels"][0]["name"] = "Charlottendahls låda"
        self.application.save_local_configuration(self.client, {"draft": draft})

        after = self.application.build_local_configuration_from_stations(self.client)["draft"]
        self.assertEqual("double", after["connections"][0]["track_type"])
        self.assertEqual("Charlottendahls låda", after["panels"][0]["name"])

    def test_it_fills_only_the_empty_slots(self):
        self._three_stations()
        state = self.application.build_local_configuration_from_stations(self.client)
        draft = state["draft"]
        panel = next(p for p in draft["panels"] if p["station_id"] == "b")
        panel["slots"] = {"A": None, "B": None, "C": panel["slots"]["A"], "D": None}
        self.application.save_local_configuration(self.client, {"draft": draft})

        after = self.application.build_local_configuration_from_stations(self.client)["draft"]
        panel = next(p for p in after["panels"] if p["station_id"] == "b")
        self.assertIsNotNone(panel["slots"]["C"], "en satt plats ska inte flyttas")
        self.assertEqual(2, sum(1 for value in panel["slots"].values() if value))

    def test_a_new_station_extends_the_line_without_rebuilding_it(self):
        self._three_stations()
        state = self.application.build_local_configuration_from_stations(self.client)
        draft = state["draft"]
        first_link_id = draft["connections"][0]["id"]
        draft["stations"].append({"id": "d", "code": "ÅBY", "name": "Åby"})
        self.application.save_local_configuration(self.client, {"draft": draft})

        after = self.application.build_local_configuration_from_stations(self.client)["draft"]
        self.assertEqual(3, len(after["connections"]))
        self.assertEqual(first_link_id, after["connections"][0]["id"], "befintliga id ska stå kvar")
        self.assertEqual(4, len(after["panels"]))

    def test_the_shortcut_is_refused_in_cloud_mode(self):
        """It builds the line, and the line is Cloud's while Cloud is linked."""
        self.runtime.install(runtime_package_v3())
        self.application.seed_local_configuration(self.client)

        # Ett utkast där en sträcka saknas, så genvägen har något att bygga.
        # Panelplatserna måste släppa den först - en plats som pekar på en
        # sträcka som inte finns är inte en giltig konfiguration.
        draft = self.local.current()["draft"]
        for panel in draft["panels"]:
            panel["slots"] = {slot: None for slot in ("A", "B", "C", "D")}
        draft["connections"] = []
        self.local.save(draft)

        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        with self.assertRaises(HTTPAPIError) as refused:
            self.application.build_local_configuration_from_stations(self.client)
        self.assertEqual("topology_locked_by_cloud", refused.exception.code)

    def test_without_stations_it_says_so_instead_of_building_nothing(self):
        with self.assertRaises(HTTPAPIError) as refused:
            self.application.build_local_configuration_from_stations(self.client)
        self.assertEqual("no_stations", refused.exception.code)
