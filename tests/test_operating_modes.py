"""D3 and D4: the gate on the editing D2 made possible.

While the server can reach Cloud, Cloud is the editor and the server refuses
local changes. That is a rule, not a recommendation - and the transition
between modes is explicit and sticky, because a rule decided by a live
network response would unlock and relock in the middle of somebody's edit.

Going back to Cloud discards the local revisions. It must never happen
silently.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.local_config import (
    SQLiteLocalConfigurationStore,
    local_configuration_from_publication,
)
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore


class OperatingModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = root / "runtime.db"
        self.identities = IdentityStore(root / "identity.db")
        self.runtime = SQLiteRuntimeStore(self.database)
        self.local = SQLiteLocalConfigurationStore(self.database)
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

    def _go_offline(self):
        return self.application.set_operating_mode(self.client, {"mode": "offline-meet"})

    # ------------------------------------------------------------- D3

    def test_a_server_never_linked_to_cloud_runs_locally_without_choosing(self):
        state = self.application.operating_mode_state(self.client)
        self.assertEqual("offline-meet", state["mode"])
        self.assertTrue(state["editing_open"])

    def test_the_mode_is_persistent_and_survives_a_restart(self):
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        self.runtime.close()

        reopened = SQLiteRuntimeStore(self.database)
        try:
            self.assertEqual("cloud-linked", reopened.operating_mode())
            self.assertFalse(reopened.editing_is_open())
        finally:
            reopened.close()
        self.runtime = SQLiteRuntimeStore(self.database)

    def test_cloud_linked_refuses_changes_to_the_line(self):
        """D3, narrowed by T3.

        This test used to assert that Cloud mode refused *every* editing path.
        That was one gate over the whole draft, and it made two different
        things indistinguishable: correcting a departure time was refused for
        the same reason as redrawing the line.

        The line is still Cloud's. The timetable is not - see the test below.
        """
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})

        draft = local_configuration_from_publication(runtime_package_v3())
        draft["stations"].append({"id": "local-x", "code": "XXX", "name": "Påhittad"})

        with self.assertRaises(HTTPAPIError) as refused:
            self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertEqual("topology_locked_by_cloud", refused.exception.code)
        self.assertIn("stationer", str(refused.exception))

    def test_cloud_linked_still_lets_the_timetable_be_corrected(self):
        """T3: during a meet the server *is* the operation.

        Trains run late and movements get cancelled, and there is no route
        through Cloud for that at 13:40 on a Saturday.
        """
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        self.application.seed_local_configuration(self.client)

        draft = self.local.current()["draft"]
        draft["trains"][0]["departure_time"] = "09:44"
        saved = self.application.save_local_configuration(self.client, {"draft": draft})

        self.assertEqual("09:44", saved["draft"]["trains"][0]["departure_time"])

    def test_seeding_is_a_copy_and_is_allowed_in_cloud_mode(self):
        """Seeding does not change the active meet; it fills a working copy.

        Refusing it in Cloud mode left the timetable with nothing to be
        corrected *in*, which is how a narrow rule turns into a wide one.
        """
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        state = self.application.seed_local_configuration(self.client)
        self.assertTrue(state["draft"]["trains"])
        self.assertEqual(
            "publication-2026-08-11-a", self.runtime.active().publication_id,
            "den aktiva träffen ska inte ha rörts av en kopiering",
        )

    def test_offline_meet_opens_them_again(self):
        self.runtime.install(runtime_package_v3())
        self._go_offline()
        state = self.application.seed_local_configuration(self.client)
        self.assertTrue(state["draft"]["trains"])

    def test_a_network_outage_on_its_own_never_unlocks_editing(self):
        """The test the issue asked for by name.

        Nothing here touches the network, and that is the point: the mode is
        state a person set. Losing Cloud changes what the server can *do*, not
        what it is *allowed* to do.
        """
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})

        # However unreachable Cloud becomes, the gate does not move on its own.
        self.application.linked_runtime_fetcher = lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("nätet är nere")
        )
        self.assertFalse(self.runtime.editing_is_open())
        draft = local_configuration_from_publication(runtime_package_v3())
        draft["connections"] = []
        with self.assertRaises(HTTPAPIError) as refused:
            self.application.save_local_configuration(self.client, {"draft": draft})
        self.assertEqual("topology_locked_by_cloud", refused.exception.code)

    # ------------------------------------------------------------- D4

    def test_returning_to_cloud_needs_confirmation_when_something_is_lost(self):
        self._install_a_local_revision()
        with self.assertRaises(HTTPAPIError) as refused:
            self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        self.assertEqual("confirm_discard", refused.exception.code)
        self.assertIn("kastas", str(refused.exception))
        self.assertEqual("offline-meet", self.runtime.operating_mode(), "läget ska inte ha flyttats")

    def test_confirming_lets_it_through(self):
        self._install_a_local_revision()
        state = self.application.set_operating_mode(
            self.client, {"mode": "cloud-linked", "discard_local_revisions": True}
        )
        self.assertEqual("cloud-linked", state["mode"])

    def test_returning_with_nothing_local_needs_no_confirmation(self):
        """A confirmation for a no-op teaches people to click through them."""
        self.runtime.install(runtime_package_v3())
        self._go_offline()
        state = self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        self.assertEqual("cloud-linked", state["mode"])

    def test_the_preview_names_the_rows_that_change_not_just_a_count(self):
        """Counting revisions is not enough: an operator has to see which
        rows they are agreeing to lose."""
        self._install_a_local_revision()
        preview = self.application.operating_mode_state(self.client)["discards_on_return"]
        self.assertEqual(1, preview["revisions"])
        self.assertTrue(preview["rows"], "förhandsvisningen ska namnge raderna")
        changed = preview["rows"][0]
        self.assertIn("avgång", changed["change"])
        self.assertIn("09:47", changed["change"])
        self.assertTrue(changed["train_number"])

    def test_an_added_row_shows_as_added(self):
        self.runtime.install(runtime_package_v3())
        self._go_offline()
        state = self.application.seed_local_configuration(self.client)
        draft = state["draft"]
        first = draft["trains"][0]
        draft["trains"].append({
            **first, "id": "local-train-999", "train_number": "999", "departure_time": "11:11",
        })
        self.application.save_local_configuration(
            self.client, {"draft": draft, "expected_revision": state["revision"]}
        )
        self.application.activate_local_configuration(self.client, {})

        rows = self.application.operating_mode_state(self.client)["discards_on_return"]["rows"]
        self.assertIn("tillagd", [row["change"] for row in rows])

    # ---------------------------------------------------------------- helper

    def _install_a_local_revision(self):
        self.runtime.install(runtime_package_v3())
        self._go_offline()
        state = self.application.seed_local_configuration(self.client)
        draft = state["draft"]
        draft["trains"][0]["departure_time"] = "09:47"
        self.application.save_local_configuration(
            self.client, {"draft": draft, "expected_revision": state["revision"]}
        )
        self.application.activate_local_configuration(self.client, {})


class DownstreamSeesTheRevisionTests(unittest.TestCase):
    """Step 5 of the acceptance chain: does a box find out?

    A local revision is worth nothing if the things that display it keep
    showing the old package. A box watches config_version; TKL and the
    display views read the active publication.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = root / "runtime.db"
        self.runtime = SQLiteRuntimeStore(self.database)
        self.local = SQLiteLocalConfigurationStore(self.database)

    def tearDown(self):
        self.local.close()
        self.runtime.close()
        self.directory.cleanup()

    def test_config_version_moves_and_carries_the_edit(self):
        cloud = self.runtime.install(runtime_package_v3())
        before = cloud.publication_id

        state = self.local.seed_from_publication(cloud.payload)
        draft = state["draft"]
        draft["trains"][0]["departure_time"] = "09:47"
        saved = self.local.save(draft, expected_revision=state["revision"])
        revised = self.runtime.install(self.local.runtime_package())

        # What a TMBox compares to decide whether to re-read.
        self.assertNotEqual(before, revised.publication_id)
        self.assertEqual(revised.publication_id, self.runtime.active().publication_id)

        # And the edit is in what it would read.
        row = next(
            train for train in self.runtime.active().payload["trains"]
            if train["id"] == draft["trains"][0]["id"]
        )
        self.assertEqual("09:47", row["departure_time"])
        self.assertIn(f"+local-r{saved['revision']}", revised.publication_id)

    def test_the_base_publication_is_still_there_to_go_back_to(self):
        """D4 restores Cloud's version, so it must not have been overwritten."""
        cloud = self.runtime.install(runtime_package_v3())
        state = self.local.seed_from_publication(cloud.payload)
        draft = state["draft"]
        draft["trains"][0]["departure_time"] = "09:47"
        self.local.save(draft, expected_revision=state["revision"])
        self.runtime.install(self.local.runtime_package())

        base = self.runtime.publication(cloud.publication_id)
        self.assertIsNotNone(base, "Clouds paket ska finnas kvar")
        original = next(
            train for train in base.payload["trains"]
            if train["id"] == draft["trains"][0]["id"]
        )
        self.assertNotEqual("09:47", original["departure_time"])

    def test_every_local_revision_is_listed(self):
        cloud = self.runtime.install(runtime_package_v3())
        state = self.local.seed_from_publication(cloud.payload)
        for index, time in enumerate(("09:47", "09:48"), start=1):
            draft = self.local.current()["draft"]
            draft["trains"][0]["departure_time"] = time
            self.local.save(draft)
            self.runtime.install(self.local.runtime_package())
        self.assertEqual(2, len(self.runtime.local_revisions()))


if __name__ == "__main__":
    unittest.main()
