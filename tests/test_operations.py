from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime_fixture import runtime_package_v2
from tambox_gateway.operations import SQLiteOperationsStore
from tambox_gateway.runtime import RuntimePublication


class OperationsStoreTests(unittest.TestCase):
    def test_meeting_clock_progresses_at_configured_speed_and_can_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                publication = RuntimePublication.parse(runtime_package_v2())
                store.ensure_publication(publication)
                base = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
                store.start_clock(time_value="09:15:00", now=base)

                progressed = store.clock_status(now=base + timedelta(seconds=15))
                self.assertEqual(progressed["time"], "09:16:00")

                stopped = store.stop_clock("Tekniskt stopp")
                self.assertFalse(stopped["running"])
                self.assertEqual(stopped["stopped_reason"], "Tekniskt stopp")
            finally:
                store.close()

    def test_engine_transitions_create_last_known_train_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                publication = RuntimePublication.parse(runtime_package_v2())
                store.ensure_publication(publication)
                moment = datetime.now(timezone.utc)
                free = {"connections": {"connection-a-b": {"state": "free"}}}
                reserved = {
                    "connections": {
                        "connection-a-b": {
                            "state": "reserved",
                            "train_number": "101",
                            "from_station_id": "station-a",
                            "to_station_id": "station-b",
                        }
                    }
                }
                occupied = {
                    "connections": {
                        "connection-a-b": {
                            "state": "occupied",
                            "train_number": "101",
                            "from_station_id": "station-a",
                            "to_station_id": "station-b",
                        }
                    }
                }
                store.record_engine_transition(free, reserved, moment)
                self.assertEqual(store.positions()[0]["station_id"], "station-a")
                store.record_engine_transition(reserved, occupied, moment)
                self.assertEqual(store.positions()[0]["connection_id"], "connection-a-b")
                store.record_engine_transition(occupied, free, moment)
                self.assertEqual(store.positions()[0]["station_id"], "station-b")
            finally:
                store.close()

    def test_tkl_shift_and_movement_survive_new_operator_handover(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                first = store.start_tkl_shift(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "Anna",
                    "CDA TKL 1",
                )
                movement = store.update_tkl_movement(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-101-a",
                    arrival="none",
                    departure="ready",
                    actual_track="2",
                    updated_by="Anna",
                    shift_id=first["shift_id"],
                    event_type="ready_departure",
                )
                self.assertEqual(movement["departure"], "ready")

                handed_over = store.finish_tkl_shift(
                    first["shift_id"],
                    status="handover",
                    note="Tåg 101 väntar på klarering",
                )
                self.assertEqual(handed_over["status"], "handover")
                waiting = store.tkl_station_state("publication-a", "Dagl", "station-a")
                self.assertIsNone(waiting["shift"])
                self.assertEqual(
                    waiting["previous_shift"]["handover_note"],
                    "Tåg 101 väntar på klarering",
                )

                second = store.start_tkl_shift(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "Bertil",
                    "CDA TKL 2",
                )
                state = store.tkl_station_state("publication-a", "Dagl", "station-a")
                self.assertNotEqual(first["shift_id"], second["shift_id"])
                self.assertEqual(state["shift"]["operator_name"], "Bertil")
                self.assertEqual(state["movements"]["movement-101-a"]["departure"], "ready")

                ended = store.finish_tkl_shift(
                    second["shift_id"],
                    status="closed",
                    note="Klart för dagen",
                )
                self.assertEqual(ended["status"], "closed")
                self.assertIsNone(store.tkl_station_state("publication-a", "Dagl", "station-a")["shift"])
            finally:
                store.close()

    def test_crew_ready_is_declared_by_tkl_separately_from_positioned(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                shift = store.start_tkl_shift(
                    "publication-a", "Dagl", "station-a", "Anna", "CDA TKL 1",
                )
                store.update_tkl_movement(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-421-a",
                    arrival="none",
                    departure="positioned",
                    actual_track="1B",
                    updated_by="Anna",
                    shift_id=shift["shift_id"],
                    event_type="positioned",
                )

                declared = store.set_crew_ready(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-421-a",
                    crew_ready=True,
                    updated_by="Anna",
                    shift_id=shift["shift_id"],
                )
                self.assertTrue(declared["crew_ready"])

                state = store.tkl_station_state("publication-a", "Dagl", "station-a")
                movement = state["movements"]["movement-421-a"]
                self.assertEqual(movement["departure"], "positioned")
                self.assertTrue(movement["crewReady"])

                store.set_crew_ready(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-421-a",
                    crew_ready=False,
                    updated_by="Anna",
                    shift_id=shift["shift_id"],
                )
                state = store.tkl_station_state("publication-a", "Dagl", "station-a")
                self.assertFalse(state["movements"]["movement-421-a"]["crewReady"])
                self.assertEqual(state["movements"]["movement-421-a"]["departure"], "positioned")
            finally:
                store.close()

    def test_crew_ready_can_be_declared_before_any_movement_row_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                store.set_crew_ready(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-421-a",
                    crew_ready=True,
                    updated_by="Anna",
                    shift_id=None,
                )
                movement = store.tkl_station_state(
                    "publication-a", "Dagl", "station-a"
                )["movements"]["movement-421-a"]
                self.assertTrue(movement["crewReady"])
                self.assertEqual(movement["departure"], "none")
            finally:
                store.close()

    def test_movement_revision_increases_on_position_and_crew_ready_but_not_on_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                first = store.update_tkl_movement(
                    "publication-a", "Dagl", "station-a", "movement-421-a",
                    arrival="none", departure="positioned", actual_track="1B",
                    updated_by="Anna", shift_id=None, event_type="positioned",
                )
                self.assertEqual(first["revision"], 1)

                second = store.set_crew_ready(
                    "publication-a", "Dagl", "station-a", "movement-421-a",
                    crew_ready=True, updated_by="Anna", shift_id=None,
                )
                self.assertEqual(second["revision"], 2)

                unrelated = store.update_tkl_movement(
                    "publication-a", "Dagl", "station-a", "movement-999-b",
                    arrival="none", departure="positioned", actual_track=None,
                    updated_by="Anna", shift_id=None, event_type="positioned",
                )
                self.assertEqual(unrelated["revision"], 1)
                self.assertEqual(
                    store.tkl_station_state("publication-a", "Dagl", "station-a")
                    ["movements"]["movement-421-a"]["revision"],
                    2,
                )
            finally:
                store.close()

    def test_clearance_request_is_granted_when_channel_is_free(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                clearance = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                self.assertEqual(clearance["status"], "waiting")
                self.assertEqual(clearance["channel_id"], "connection-a-b")
                self.assertEqual(clearance["revision"], 1)
            finally:
                store.close()

    def test_second_request_on_the_same_single_track_channel_is_rejected_as_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                second = store.request_clearance(
                    "publication-a", "Dagl", "movement-422-a",
                    "connection-a-b", "single", "station-b", "station-a",
                    requested_by="Bertil", ttl_seconds=30,
                )
                self.assertEqual(second, {"status": "rejected", "reason": "connection_busy"})
            finally:
                store.close()

    def test_double_track_channels_in_opposite_directions_do_not_block_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                first = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "double", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                second = store.request_clearance(
                    "publication-a", "Dagl", "movement-422-a",
                    "connection-a-b", "double", "station-b", "station-a",
                    requested_by="Bertil", ttl_seconds=30,
                )
                self.assertEqual(first["status"], "waiting")
                self.assertEqual(second["status"], "waiting")
                self.assertNotEqual(first["channel_id"], second["channel_id"])
            finally:
                store.close()

    def test_clearance_response_approves_and_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                clearance = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                approved = store.respond_clearance(
                    clearance["clearance_id"], accept=True, responded_by="Bertil"
                )
                self.assertEqual(approved["status"], "approved")
                self.assertEqual(approved["revision"], 2)

                again = store.respond_clearance(
                    clearance["clearance_id"], accept=False, responded_by="Bertil"
                )
                self.assertEqual(again, {"status": "rejected", "reason": "request_no_longer_pending"})
            finally:
                store.close()

    def test_clearance_cancel_only_works_while_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                clearance = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                cancelled = store.cancel_clearance(clearance["clearance_id"], cancelled_by="Anna")
                self.assertEqual(cancelled["status"], "cancelled")

                again = store.cancel_clearance(clearance["clearance_id"], cancelled_by="Anna")
                self.assertEqual(again, {"status": "rejected", "reason": "request_no_longer_pending"})

                reopened = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                self.assertEqual(reopened["status"], "waiting")
            finally:
                store.close()

    def test_expired_clearance_frees_the_channel_for_a_new_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
                clearance = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=5, now=start,
                )
                blocked = store.request_clearance(
                    "publication-a", "Dagl", "movement-422-a",
                    "connection-a-b", "single", "station-b", "station-a",
                    requested_by="Bertil", ttl_seconds=30, now=start,
                )
                self.assertEqual(blocked["status"], "rejected")

                later = start + timedelta(seconds=10)
                freed = store.request_clearance(
                    "publication-a", "Dagl", "movement-422-a",
                    "connection-a-b", "single", "station-b", "station-a",
                    requested_by="Bertil", ttl_seconds=30, now=later,
                )
                self.assertEqual(freed["status"], "waiting")

                stale = store.clearance(clearance["clearance_id"])
                self.assertEqual(stale["status"], "expired")
            finally:
                store.close()

    def test_invalidate_clearance_marks_waiting_case_but_leaves_resolved_ones_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                waiting = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                invalidated = store.invalidate_clearance(waiting["clearance_id"])
                self.assertEqual(invalidated["status"], "invalidated_by_revision")

                approved = store.request_clearance(
                    "publication-a", "Dagl", "movement-421-a",
                    "connection-a-b", "single", "station-a", "station-b",
                    requested_by="Anna", ttl_seconds=30,
                )
                store.respond_clearance(approved["clearance_id"], accept=True, responded_by="Bertil")
                unchanged = store.invalidate_clearance(approved["clearance_id"])
                self.assertEqual(unchanged["status"], "approved")
            finally:
                store.close()

    def test_tkl_and_ranger_share_tåg_klart_but_only_ranger_requires_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                ranger_ready = store.set_train_readiness(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-freight",
                    "station-a-rbg",
                    action="ready",
                    actor="Bertil",
                    actor_role="ranger",
                    shift_id=None,
                )
                self.assertEqual(ranger_ready["status"], "ready")
                self.assertEqual(ranger_ready["prepared_by_role"], "ranger")

                acknowledged = store.set_train_readiness(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-freight",
                    "station-a-rbg",
                    action="acknowledge",
                    actor="Anna",
                    actor_role="tkl",
                    shift_id=None,
                )
                self.assertEqual(acknowledged["status"], "acknowledged")
                self.assertEqual(acknowledged["acknowledged_by"], "Anna")

                passenger_ready = store.set_train_readiness(
                    "publication-a",
                    "Dagl",
                    "station-a",
                    "movement-passenger",
                    "station-a-main",
                    action="ready",
                    actor="Anna",
                    actor_role="tkl",
                    shift_id=None,
                )
                self.assertEqual(passenger_ready["status"], "acknowledged")
                self.assertEqual(passenger_ready["prepared_by_role"], "tkl")
                self.assertEqual(len(store.train_readiness("publication-a", "Dagl", "station-a")), 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
