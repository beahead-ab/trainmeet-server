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


if __name__ == "__main__":
    unittest.main()
