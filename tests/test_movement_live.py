"""Verkliga tider i /v1/display, så att tidtabellen kan visa förseningar.

/v1/display lämnar `movement_live`: per rörelse läget (ankomst, avgång, spår)
och träffklockans tid för den senaste övergången till ankommet respektive
avgånget, ur händelserna. Ett läge som bara tidtabellen gav har inga tider, och
en övergång som systemet räknade fram i efterhand ("tåget hoppar fram") har
ingen tid. Vem som gjorde något och anteckningar lämnas aldrig ut: /v1/display
är öppen.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime_fixture import runtime_package_v3
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore


def at(hours, minutes, seconds=0):
    return hours * 3600 + minutes * 60 + seconds


class _Meet(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)
        self.pub = self.runtime.install(runtime_package_v3())
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)
        self.app = TrainMeetHTTPApplication(TrafficEngine(self.pub.session_config()), self.ids,
            PairingService(self.ids, {"panel-a", "panel-b"}), HTTPServerConfig(local_development=True),
            runtime_store=self.runtime, operations_store=self.ops)
        self.app.automatic.set_enabled(False)
        self.ops.ensure_publication(self.pub)
        self.day = self.runtime.active_day()

    def tearDown(self):
        self.ops.close()
        self.ids.close()
        self.runtime.close()
        self.app.lifecycle.close()
        self.temp.cleanup()

    def clock(self, value):
        self.ops.configure_clock(time_value=value, running=False)

    def move(self, station, movement, *, arrival="none", departure="none", by="TKL Alvesta", kind="movement_updated", note=None):
        self.ops.update_tkl_movement(self.pub.publication_id, self.day, station, movement, arrival=arrival, departure=departure,
                                     actual_track="track-station-b-1" if station == "station-b" else None, updated_by=by,
                                     shift_id=None, event_type=kind, operator_note=note)

    def live(self):
        return self.app.display_snapshot()["movement_live"]


class MovementLiveTests(_Meet):

    def test_the_meet_clock_at_departure_and_arrival(self):
        self.clock("09:24:00")  # planerad avgång 09:20: fyra minuter sent
        self.move("station-a", "movement-101-a", departure="departed", kind="train.departed")
        self.clock("09:41:30")  # planerad ankomst 09:35
        self.move("station-b", "movement-101-b", arrival="arrived", kind="train.arrived")
        live = self.live()
        self.assertEqual(live["movement-101-a"]["departed_seconds"], at(9, 24))
        self.assertEqual(live["movement-101-b"]["arrived_seconds"], at(9, 41, 30))
        self.assertEqual(live["movement-101-b"]["actual_track"], "track-station-b-1")
        self.assertEqual((live["movement-101-b"]["arrival"], live["movement-101-b"]["by_timetable"]), ("arrived", False))

    def test_a_train_that_jumped_ahead_has_no_time(self):
        """train.advanced: tiden är när systemet kom ikapp, inte när tåget gick."""
        self.clock("10:30:00")
        self.move("station-a", "movement-101-a", departure="departed", kind="train.advanced")
        self.assertNotIn("departed_seconds", self.live()["movement-101-a"])
        self.assertEqual(self.live()["movement-101-a"]["departure"], "departed")

    def test_a_position_the_timetable_gave_has_no_time(self):
        self.app.control_clock(self.app.local_admin(), {"action": "set", "time": "09:50:00",
                                                        "meet_generation": self.app.lifecycle.selected()["generation"]})
        live = self.live()
        self.assertEqual((live["movement-101-b"]["arrival"], live["movement-101-b"]["by_timetable"]), ("arrived", True))
        self.assertNotIn("arrived_seconds", live["movement-101-b"])
        self.assertNotIn("departed_seconds", live["movement-101-a"])

    def test_who_did_it_and_notes_never_leave_the_server(self):
        self.clock("09:24:00")
        self.move("station-a", "movement-101-a", departure="departed", by="Benny Bengtsson", note="Lok byttes, ring Anna 070-123")
        public = json.dumps(self.app.display_snapshot(), ensure_ascii=False)
        self.assertNotIn("Benny", public)
        self.assertNotIn("Anna", public)
        self.assertEqual(set(self.live()["movement-101-a"]), {"arrival", "departure", "actual_track", "by_timetable", "departed_seconds"})

    def test_a_new_event_and_a_new_placement_are_seen_at_once(self):
        """The answer is cached between polls, but never outlives a change."""
        self.clock("09:24:00")
        self.assertEqual({}, self.live())
        self.move("station-a", "movement-101-a", departure="departed")
        self.assertEqual(self.live()["movement-101-a"]["departed_seconds"], at(9, 24))
        self.ops.place_trains_by_timetable(self.pub, self.day, at(9, 0))  # 101 has real events: untouched
        self.assertEqual(self.live()["movement-101-a"]["departure"], "departed")
        self.ops._connection.execute("DELETE FROM tkl_events")  # noqa: SLF001
        self.ops._connection.execute("DELETE FROM tkl_movement_states")  # noqa: SLF001
        self.ops.place_trains_by_timetable(self.pub, self.day, at(9, 0))
        self.assertEqual(self.live()["movement-101-a"]["departure"], "positioned")

    def test_the_index_for_the_daily_events_exists(self):
        indexes = {row[1] for row in self.ops._connection.execute("PRAGMA index_list(tkl_events)")}  # noqa: SLF001
        self.assertIn("tkl_events_by_movement", indexes)


class DeviationLevelTests(_Meet):
    """Hur mycket förseningar och för tidiga tåg som visas: fem nivåer, där
    admin sätter träffens förval (2, "när det inträffar") och varje skärm och
    webbläsare kan välja eget. Förvalet följer med i /v1/display."""

    def test_the_meet_default_is_level_two_and_admin_can_change_it(self):
        state = self.app.deviation_level_state(self.app.local_admin())
        self.assertEqual((state["level"], state["default"], state["overridden"]), (2, 2, False))
        self.assertEqual(self.app.display_snapshot()["display"]["deviation_level"], 2)
        chosen = self.app.save_deviation_level(self.app.local_admin(), {"level": 4})
        self.assertEqual((chosen["level"], chosen["overridden"]), (4, True))
        self.assertEqual(self.app.display_snapshot()["display"]["deviation_level"], 4)
        back = self.app.save_deviation_level(self.app.local_admin(), {"level": "default"})
        self.assertEqual((back["level"], back["overridden"]), (2, False))
        for wrong in (0, 6, "allt"):
            with self.assertRaises(HTTPAPIError) as raised:
                self.app.save_deviation_level(self.app.local_admin(), {"level": wrong})
            self.assertEqual(raised.exception.status, 400, wrong)

    def test_only_an_administrator_sets_the_default(self):
        box = PairedClient(client_id="esp32", display_name="Box", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-b",),
                           station_id="station-b", admin_role="")
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.save_deviation_level(box, {"level": 5})
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.runtime.deviation_level(self.pub.meet_id)["level"], 2)

    def test_the_level_is_per_meet(self):
        self.runtime.set_deviation_level("en-annan-traff", "5")
        self.assertEqual(self.runtime.deviation_level(self.pub.meet_id)["level"], 2)
        self.assertEqual(self.runtime.deviation_level("en-annan-traff")["level"], 5)


if __name__ == "__main__":
    unittest.main()
