from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime_fixture import runtime_package_v2
from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublication


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

    def test_panel_snapshots_follow_the_running_meeting_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                store.ensure_publication(RuntimePublication.parse(runtime_package_v2()))
                base = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
                store.start_clock(time_value="09:15:00", now=base)

                moment = base
                engine = TrafficEngine(sample_session())
                engine.set_clock_source(lambda: store.clock_status(now=moment))

                self.assertEqual(engine.snapshot("panel-a")["clock"]["time"], "09:15")

                # Fifteen wall seconds at the publication's speed of four.
                moment = base + timedelta(seconds=15)
                snapshot = engine.snapshot("panel-a")
                self.assertEqual(snapshot["clock"]["time"], "09:16")
                self.assertTrue(snapshot["clock"]["running"])
                self.assertEqual(snapshot["display"]["line2"], "           09:16")

                store.stop_clock("Rast")
                stopped = engine.snapshot("panel-a")["clock"]
                self.assertFalse(stopped["running"])
                self.assertEqual(stopped["stopped_reason"], "Rast")
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
                # A reservation is not a movement yet. Nothing should appear on
                # the displays until the train has actually departed.
                store.record_engine_transition(free, reserved, moment)
                self.assertEqual(store.positions(), [])
                store.record_engine_transition(reserved, occupied, moment)
                self.assertEqual(store.positions()[0]["connection_id"], "connection-a-b")
                store.record_engine_transition(occupied, free, moment)
                self.assertEqual(store.positions()[0]["station_id"], "station-b")
            finally:
                store.close()

    def test_a_rejected_or_cancelled_request_never_appears_on_the_displays(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                publication = RuntimePublication.parse(runtime_package_v2())
                store.ensure_publication(publication)
                moment = datetime.now(timezone.utc)
                free = {"connections": {"connection-a-b": {"state": "free"}}}
                requested = {
                    "connections": {
                        "connection-a-b": {
                            "state": "requested",
                            "train_number": "202",
                            "from_station_id": "station-a",
                            "to_station_id": "station-b",
                        }
                    }
                }
                # A request that is rejected, or cancelled by the requester,
                # goes straight from requested/reserved back to free - the
                # transition that used to leave a ghost train on the map.
                store.record_engine_transition(free, requested, moment)
                store.record_engine_transition(requested, free, moment)

                self.assertEqual(store.positions(), [])
            finally:
                store.close()

    def test_a_rejected_second_request_does_not_erase_where_the_train_already_stood(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteOperationsStore(Path(directory) / "runtime.db")
            try:
                publication = RuntimePublication.parse(runtime_package_v2())
                store.ensure_publication(publication)
                moment = datetime.now(timezone.utc)
                free = {"connections": {"connection-a-b": {"state": "free"}}}
                occupied = {
                    "connections": {
                        "connection-a-b": {
                            "state": "occupied",
                            "train_number": "303",
                            "from_station_id": "station-a",
                            "to_station_id": "station-b",
                        }
                    }
                }
                arrived = {"connections": {"connection-a-b": {"state": "free"}}}
                requested_again = {
                    "connections": {
                        "connection-b-c": {
                            "state": "requested",
                            "train_number": "303",
                            "from_station_id": "station-b",
                            "to_station_id": "station-c",
                        }
                    }
                }
                store.record_engine_transition(free, occupied, moment)
                store.record_engine_transition(occupied, arrived, moment)
                self.assertEqual(store.positions()[0]["station_id"], "station-b")

                # The train really is standing at station-b. A rejected onward
                # request must leave that in place, not clear it.
                store.record_engine_transition(free, requested_again, moment)
                store.record_engine_transition(requested_again, free, moment)

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
