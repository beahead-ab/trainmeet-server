"""Cloud frågar innan den ersätter lokala ändringar i tidtabellen.

Utan lokala ändringar tar servern en ny Cloud-version så fort banan är fri,
som förut. Med lokala ändringar hämtas versionen men aktiveras inte: admin
får se vad Cloud ändrar och vilka lokala ändringar som försvinner, och väljer
Ta Cloud-versionen eller Behåll mina ändringar. Behåll gäller bara just den
versionen; en nyare väcker frågan igen.
"""
from __future__ import annotations

import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

from test_pending_revisions import CloudDeliveryFixture, cloud_package, dispatch_request
from tmbox_gateway.identity import DeviceKind, PairedClient


class CloudGateTests(CloudDeliveryFixture):
    def setUp(self):
        super().setUp()
        self.offered["trains"][0]["note"] = "Från Cloud"

    def edit(self, departure="09:30"):
        state = dispatch_request(self.application, self.client, "/v1/meet-data", method="GET")[1]
        row = next(row for row in state["draft"]["trains"] if row["id"] == "movement-101-a")
        row["departure_time"] = departure
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data", {
            "draft": state["draft"], "expected_revision": state["revision"], "base_publication_id": state["base_publication_id"]})
        self.assertEqual(HTTPStatus.OK, status, body)
        return body

    def decide(self, decision, *, publication_id="cloud-second", expected_revision=1, client=None):
        return dispatch_request(self.application, client or self.client, "/v1/cloud/local-decision",
                                {"decision": decision, "publication_id": publication_id, "expected_revision": expected_revision})

    def departure(self):
        return next(r for r in self.runtime.active().payload["trains"] if r["id"] == "movement-101-a")["departure_time"]

    def pending(self):
        return dispatch_request(self.application, self.client, "/v1/runtime/pending", method="GET")[1]

    def test_without_local_changes_a_new_version_is_taken_as_before(self):
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["activated"])
        self.assertFalse(result["local_changes"])
        self.assertEqual("cloud-second", self.runtime.active().publication_id)

    def test_with_local_changes_the_new_version_waits_and_says_what_is_lost(self):
        self.edit()
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue((result["pending"], result["local_changes"]) == (True, True), result)
        self.assertEqual(("cloud-first", 1, "09:30"), (self.runtime.active().publication_id, self.runtime.active().local_revision, self.departure()))
        status = self.application.cloud_config.status()
        self.assertEqual(("local_changes", "cloud-second", True, None),
                         (status["state"], status["pending_publication_id"], status["local_changes"], status["local_decision"]))
        self.assertIn("1 lokala ändringar", status["message"])
        pending = self.pending()
        self.assertEqual(("cloud-second", None, 1), (pending["publication_id"], pending["decision"], pending["local_edits"]["count"]))
        self.assertEqual(1, len(pending["local_edits"]["lines"]))
        self.assertIn("avgång 09:20 → 09:30", pending["local_edits"]["lines"][0])
        self.assertEqual(self.application.server_context(self.client)["cloud_update"]["state"], "local_changes")

    def test_keep_stops_downloading_and_only_a_newer_version_asks_again(self):
        self.edit()
        self.application.auto_sync_cloud_runtime()
        status, body = self.decide("keep")
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertEqual(("local_changes_kept", "keep"), (body["state"], body["local_decision"]))
        self.fetches.clear()
        for _ in range(3):
            result = self.application.auto_sync_cloud_runtime()
            self.assertTrue(result["pending"] and result["local_changes"], result)
        self.assertEqual([True] * 3, self.fetches, "after Keep only the manifest is read, never the package again")
        self.assertEqual(("cloud-first", "09:30"), (self.runtime.active().publication_id, self.departure()))
        self.offered = cloud_package("cloud-third")
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["pending"] and result["local_changes"])
        status = self.application.cloud_config.status()
        self.assertEqual(("local_changes", "cloud-third", None), (status["state"], status["pending_publication_id"], status["local_decision"]))

    def test_take_backs_up_and_replaces_the_local_changes(self):
        self.application.control_clock(self.client, {"action": "start", "time": "13:14:15", "speed": 6})
        self.edit()
        self.application.auto_sync_cloud_runtime()
        before = self.clock_record()
        status, body = self.decide("take")
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertTrue(body["activated"], body)
        self.assertTrue(body["backup"] and Path(body["backup"]).exists(), body)
        active = self.runtime.active()
        self.assertEqual(("cloud-second", 0, "09:20"), (active.publication_id, active.local_revision, self.departure()))
        self.assertEqual("Från Cloud", active.payload["trains"][0]["note"])
        self.assertEqual(("discarded", "cloud_taken"), tuple(self.runtime._connection.execute(
            "SELECT status, ended_reason FROM local_timetable_edits WHERE revision = 1").fetchone()))
        self.assertEqual(before[:1] + ("cloud-second",) + before[2:], self.clock_record(), "the running clock is kept")
        self.assertEqual((False, None), (body["local_changes"], body["local_decision"]))
        self.assertIsNone(self.runtime.local_decision(), "a choice ends with the version it was made for")
        actions = [event["action"] for event in self.operations.audit_trail("meet-data-cloud-first")]
        self.assertEqual(["meet_data.saved", "meet_data.replaced_by_cloud"], actions)

    def test_take_while_traffic_blocks_waits_and_keeps_the_local_changes_until_free(self):
        self.edit()
        self.application.auto_sync_cloud_runtime()
        self.busy()
        status, body = self.decide("take")
        self.assertEqual(HTTPStatus.OK, status, body)
        self.assertTrue(body["pending"] and not body.get("activated"), body)
        self.assertEqual(("waiting", "take"), (body["state"], body["local_decision"]))
        self.assertEqual(("cloud-first", 1, "09:30"), (self.runtime.active().publication_id, self.runtime.active().local_revision, self.departure()))
        self.free()
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["activated"], result)
        self.assertEqual(("cloud-second", 0), (self.runtime.active().publication_id, self.runtime.active().local_revision))

    def test_discarding_locally_lets_the_waiting_version_in(self):
        self.edit()
        self.application.auto_sync_cloud_runtime()
        status, body = dispatch_request(self.application, self.client, "/v1/meet-data/discard", {"expected_revision": 1})
        self.assertEqual(HTTPStatus.OK, status, body)
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["activated"], result)
        self.assertEqual("cloud-second", self.runtime.active().publication_id)

    def test_a_choice_needs_the_page_to_be_current(self):
        self.edit()
        self.application.auto_sync_cloud_runtime()
        status, body = self.decide("take", publication_id="cloud-old")
        self.assertEqual((HTTPStatus.CONFLICT, "pending_revision_changed"), (status, body["code"]))
        status, body = self.decide("take", expected_revision=0)
        self.assertEqual((HTTPStatus.CONFLICT, "stale_local_edits"), (status, body["code"]))
        status, body = self.decide("later")
        self.assertEqual((HTTPStatus.BAD_REQUEST, "invalid_decision"), (status, body["code"]))
        box = PairedClient(client_id="box", display_name="CDA TMBox", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-a",))
        status, body = self.decide("take", client=box)
        self.assertEqual((HTTPStatus.FORBIDDEN, "admin_required"), (status, body["code"]))
        self.application.simulation = SimpleNamespace(active=True)
        status, body = self.decide("take")
        self.assertEqual((HTTPStatus.CONFLICT, "simulation_active"), (status, body["code"]))
        self.assertEqual(("cloud-first", 1), (self.runtime.active().publication_id, self.runtime.active().local_revision))

    def test_without_a_waiting_version_there_is_nothing_to_choose(self):
        self.edit()
        status, body = self.decide("keep")
        self.assertEqual((HTTPStatus.CONFLICT, "pending_revision_changed"), (status, body["code"]))


if __name__ == "__main__":
    unittest.main()
