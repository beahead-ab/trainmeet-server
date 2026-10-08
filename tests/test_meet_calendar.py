"""Träffkalendern: dagen går fram vid dygnsskiftet, och tidsmaskinen (Casper 2026-10-08).

Träffen börjar på en dag admin väljer. Vid varje dygnsskifte går dagen fram
och tidtabellen för den nya veckodagen gäller, Dagl alltid. Skiftet ligger
kl. 05:00 (inställbart), en timme innan trafikdygnet börjar: mellan midnatt
och skiftet är det kvar gårdagens trafikdygn, så att nattåg kör klart. Vid
skiftet är alla statusar för ankomst och avgång nollställda och varje tåg
står på sin utgångspunkt. Ett tåg som fortfarande är ute kör klart på
gårdagens dag först, högst en halvtimme. Tidsmaskinen hoppar till valfri dag
och tid; alla tåg följer med.

Fixturens tåg: 101 går Dagl (A 09:20 → B 09:35), 202 bara söndagar.
Fixturens trafikdag är Lör.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from runtime_fixture import runtime_package_v3
from test_timetable_placement import add_service
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore, _now_iso
from tmbox_gateway.runtime import SQLiteRuntimeStore, calendar_weekday, matches_active_day


def at(hours, minutes=0, seconds=0):
    return hours * 3600 + minutes * 60 + seconds


#: Klockan vid dygnsskiftet nästa morgon, i sekunder sedan träffdagens början.
CHANGE = 86400 + at(5)


class _Meet(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)
        self.pub = self.runtime.install(self.package())
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)
        self.app = TrainMeetHTTPApplication(TrafficEngine(self.pub.session_config()), self.ids,
            PairingService(self.ids, {"panel-a", "panel-b"}), HTTPServerConfig(local_development=True, state_dir=self.temp.name),
            runtime_store=self.runtime, operations_store=self.ops)
        self.app.automatic.set_enabled(False)
        self.admin = self.app.local_admin()
        self.ops.ensure_publication(self.pub)

    def package(self):
        return runtime_package_v3()

    def tearDown(self):
        self.ops.close()
        self.ids.close()
        self.runtime.close()
        self.app.lifecycle.close()
        self.temp.cleanup()

    def generation(self):
        return self.app.lifecycle.selected()["generation"]

    def run_clock_at(self, elapsed):
        """Klockan går och har kommit till `elapsed` sekunder sedan dagens början."""
        with self.ops._lock:  # noqa: SLF001
            self.ops._connection.execute(  # noqa: SLF001
                "UPDATE runtime_clock SET base_seconds=?, base_recorded_at=?, running=1, speed=1 WHERE singleton=1",
                (float(elapsed), _now_iso()))

    def depart_101(self, day):
        self.ops.update_tkl_movement(self.pub.publication_id, day, "station-a", "movement-101-a", arrival="none",
                                     departure="departed", actual_track=None, updated_by="TKL Alvesta", shift_id=None,
                                     event_type="train.departed", operator_note=None)

    def live(self):
        return self.app.display_snapshot()["movement_live"]

    def numbers(self):
        return sorted({str(service["train_number"]) for service in self.app.display_snapshot()["services"]})


class DayChangeTests(_Meet):
    def test_the_calendar_starts_on_the_traffic_day_as_day_one(self):
        calendar = self.app.display_snapshot()["calendar"]
        self.assertEqual({key: calendar[key] for key in ("start_day", "day_number", "weekday")}, {"start_day": "Lör", "day_number": 1, "weekday": "Lör"})
        self.assertEqual(calendar["week"], ["Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön"])
        self.assertIsNone(self.app.calendar_tick(), "nothing happens while the clock stands still")

    def test_at_the_day_change_the_next_day_starts_with_its_own_timetable_and_no_statuses(self):
        self.run_clock_at(at(9, 30))
        self.depart_101("Lör")
        self.assertEqual(self.live()["movement-101-a"]["departure"], "departed")
        self.assertIsNone(self.app.calendar_tick(), "not the day change yet")
        self.ops.update_tkl_movement(self.pub.publication_id, "Lör", "station-b", "movement-101-b", arrival="arrived",
                                     departure="none", actual_track=None, updated_by="TKL Bor", shift_id=None,
                                     event_type="train.arrived", operator_note=None)
        self.run_clock_at(86400 + 30)
        self.assertIsNone(self.app.calendar_tick(), "midnight is still yesterday's traffic day")
        self.run_clock_at(CHANGE + 30)
        result = self.app.calendar_tick()
        self.assertEqual((result["day_number"], result["active_day"]), (2, "Sön"))
        snapshot = self.app.display_snapshot()
        self.assertEqual((snapshot["active_day"], snapshot["calendar"]["day_number"]), ("Sön", 2))
        self.assertEqual(self.numbers(), ["101", "202"], "Sunday's trains and the daily ones")
        # Nollställt: 101 står på sin första station enligt tidtabellen, inte som avgånget i går.
        self.assertTrue(self.live()["movement-101-a"]["by_timetable"])
        self.assertNotIn("departed_seconds", self.live()["movement-101-a"])
        self.assertEqual({p["train_number"]: p["station_id"] for p in self.ops.positions()}.get("101"), "station-a")
        clock = self.ops.clock_status()
        self.assertTrue(clock["running"], "the clock keeps running")
        self.assertLess(clock["elapsed_seconds"], at(5) + 120, "and goes on from the day change")
        self.assertEqual(clock["time"][:5], "05:00")
        # Gårdagen står kvar som historik.
        rows = self.ops._connection.execute(  # noqa: SLF001
            "SELECT count(*) FROM tkl_events WHERE publication_id=? AND active_day='Lör'", (self.pub.publication_id,)).fetchone()[0]
        self.assertEqual(rows, 2)

    def test_the_same_weekday_a_week_later_starts_empty(self):
        """Med Dagl är trafikdagen samma varje dag: gårdagens statusar får ändå inte följa med."""
        self.runtime.set_meet_calendar(self.pub.meet_id, "Dagl", 1)
        self.runtime.set_active_day("Dagl")
        self.run_clock_at(at(9, 30))
        self.depart_101("Dagl")
        self.run_clock_at(CHANGE + 5)
        result = self.app.calendar_tick()
        self.assertEqual((result["day_number"], result["active_day"]), (2, "Dagl"))
        self.assertTrue(self.live()["movement-101-a"]["by_timetable"], "yesterday's departure is gone")

    def test_a_train_out_at_the_day_change_runs_its_course_on_yesterdays_day_first(self):
        self.run_clock_at(CHANGE + 60)
        with self.ops._lock:  # noqa: SLF001
            self.ops._connection.execute(  # noqa: SLF001
                "INSERT INTO train_positions(train_number,status,station_id,connection_id,from_station_id,to_station_id,updated_at)"
                " VALUES('101','connection',NULL,'conn-a-b','station-a','station-b',?)", (_now_iso(),))
        self.assertIsNone(self.app.calendar_tick(), "101 is still on the line")
        calendar = self.app.display_snapshot()["calendar"]
        self.assertEqual((calendar["day_number"], calendar["waiting"]), (1, True), "and the screens say the change waits")
        with self.ops._lock:  # noqa: SLF001
            self.ops._connection.execute("DELETE FROM train_positions WHERE train_number='101'")  # noqa: SLF001
        self.assertEqual(self.app.calendar_tick()["day_number"], 2, "it has arrived: the new day starts")
        self.assertFalse(self.app.display_snapshot()["calendar"]["waiting"])

    def test_a_train_that_never_arrives_does_not_hold_yesterday_forever(self):
        self.assertEqual(self.app.DAY_CHANGE_WAIT_LIMIT, 30 * 60, "half an hour, not the six hours after midnight")
        self.run_clock_at(CHANGE + self.app.DAY_CHANGE_WAIT_LIMIT + 1)
        with self.ops._lock:  # noqa: SLF001
            self.ops._connection.execute(  # noqa: SLF001
                "INSERT INTO train_positions(train_number,status,station_id,connection_id,from_station_id,to_station_id,updated_at)"
                " VALUES('101','connection',NULL,'conn-a-b','station-a','station-b',?)", (_now_iso(),))
        self.assertEqual(self.app.calendar_tick()["day_number"], 2)

    def test_a_tkl_shift_goes_on_into_the_new_day(self):
        shift = self.ops.start_tkl_shift(self.pub.publication_id, "Lör", "station-a", "Anna", "TKL Alvesta")
        self.run_clock_at(CHANGE + 5)
        self.app.calendar_tick()
        row = self.ops._connection.execute(  # noqa: SLF001
            "SELECT active_day, status FROM tkl_shifts WHERE shift_id=?", (shift["shift_id"],)).fetchone()
        self.assertEqual(tuple(row), ("Sön", "active"))

    def test_no_new_day_during_a_simulation(self):
        class Running:
            active = True
        self.app.simulation = Running()
        self.run_clock_at(CHANGE + 5)
        self.assertIsNone(self.app.calendar_tick())

    def test_with_fastclock_the_new_day_starts_when_the_clock_passes_the_day_change_after_midnight(self):
        readings = iter([{"running": True, "seconds": float(at(4, 30)), "time": "04:30:00"},
                         {"running": True, "seconds": float(at(5, 0, 10)), "time": "05:00:10"},
                         {"running": True, "seconds": float(at(23, 59, 50)), "time": "23:59:50"},
                         {"running": True, "seconds": 10.0, "time": "00:00:10"},
                         {"running": True, "seconds": float(at(4, 59, 50)), "time": "04:59:50"},
                         {"running": True, "seconds": float(at(5, 0, 10)), "time": "05:00:10"}])
        real = self.ops.clock_status
        self.ops.clock_status = lambda *args, **kwargs: next(readings, None) or real()
        self.assertIsNone(self.app.calendar_tick())
        self.assertIsNone(self.app.calendar_tick(), "the first morning is the meet's first day")
        self.assertIsNone(self.app.calendar_tick())
        self.assertIsNone(self.app.calendar_tick(), "midnight is still yesterday's traffic day")
        self.assertIsNone(self.app.calendar_tick())
        self.assertEqual(self.app.calendar_tick()["active_day"], "Sön")

    def test_the_automatic_stations_start_the_new_day_from_its_beginning(self):
        state = {"key": [self.pub.publication_id, "Lör"], "stations": {"station-a": "tmbox-1"}, "suppressed": [], "start": float(at(9))}
        self.app.automatic._save(state)  # noqa: SLF001
        self.run_clock_at(CHANGE + 5)
        self.app.calendar_tick()
        after = self.app.automatic._state(self.pub, "Sön")  # noqa: SLF001
        self.assertLess(after["start"], at(5) + 60, "the new day's trains are sent from the day change, not history")
        self.assertEqual(after["stations"], {"station-a": "tmbox-1"}, "who works where stays")


class DayChangeSettingsTests(_Meet):
    """Skiftets tid, utgångspunkterna och toasten i vyerna."""

    def package(self):
        package = runtime_package_v3()
        # 401 går tidigt, 04:30, före skiftet: det ska ändå stå på sin första station.
        add_service(package, "401", [("station-a", None, "04:30", "track-station-a-1"),
                                     ("station-b", "04:45", None, "track-station-b-1")])
        return package

    def test_nothing_happens_between_midnight_and_the_day_change(self):
        self.run_clock_at(86400 + at(4, 59))
        self.assertIsNone(self.app.calendar_tick())
        snapshot = self.app.display_snapshot()
        self.assertEqual((snapshot["active_day"], snapshot["calendar"]["day_number"]), ("Lör", 1))
        self.assertEqual(snapshot["calendar"]["change_time"], "05:00", "the default")

    def test_every_train_of_the_new_day_stands_at_its_starting_point(self):
        self.run_clock_at(CHANGE + 30)
        self.assertEqual(self.app.calendar_tick()["day_number"], 2)
        positions = {p["train_number"]: p["station_id"] for p in self.ops.positions()}
        self.assertEqual((positions.get("401"), positions.get("101")), ("station-a", "station-a"),
                         "401 is due at 04:30 but has not left: it waits at its first station")
        live = self.live()
        self.assertEqual(live["movement-401-0"]["departure"], "positioned")
        self.assertNotEqual(live.get("movement-401-1", {}).get("arrival"), "arrived")

    def test_the_day_change_time_is_a_setting_for_the_administrator(self):
        result = self.app.save_meet_calendar(self.admin, {"start_day": "Lör", "change_time": "4:15",
                                                          "meet_generation": self.generation()})
        self.assertEqual((result["change_time"], result["changed"]), ("04:15", False), "no new day for a new time")
        self.run_clock_at(86400 + at(4, 15, 5))
        self.assertEqual(self.app.calendar_tick()["day_number"], 2)
        for wrong in ("12:00", "25:00", "kväll", ""):
            with self.assertRaises(HTTPAPIError) as raised:
                self.app.save_meet_calendar(self.admin, {"start_day": "Lör", "change_time": wrong, "meet_generation": self.generation()})
            self.assertEqual(raised.exception.status, 400, wrong)
        box = PairedClient(client_id="esp32", display_name="Box", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-b",),
                           station_id="station-b", admin_role="")
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.save_meet_calendar(box, {"start_day": "Lör", "change_time": "03:00", "meet_generation": self.generation()})
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.runtime.meet_calendar(self.pub.meet_id)["change_time"], "04:15")

    def test_a_day_change_is_announced_to_the_views_and_a_time_machine_jump_is_not(self):
        self.assertIsNone(self.app.display_snapshot()["calendar"]["last_change"])
        self.run_clock_at(CHANGE + 5)
        self.app.calendar_tick()
        last = self.app.display_snapshot()["calendar"]["last_change"]
        self.assertEqual((last["kind"], last["day_number"], last["weekday"]), ("day_change", 2, "Sön"))
        self.assertTrue(last["at"].endswith("Z"))
        self.app.time_machine(self.admin, {"day_number": 4, "time": "10:00", "meet_generation": self.generation()})
        self.assertEqual(self.app.display_snapshot()["calendar"]["last_change"], last, "the jump is not a day change")


class TimeMachineTests(_Meet):
    def jump(self, day_number, time_value, client=None):
        return self.app.time_machine(client or self.admin, {"day_number": day_number, "time": time_value,
                                                            "meet_generation": self.generation()})

    def test_jump_forward_and_back_and_every_train_follows(self):
        self.run_clock_at(at(9, 30))
        self.depart_101("Lör")
        result = self.jump(3, "14:00")
        self.assertEqual((result["day_number"], result["active_day"]), (3, "Mån"), "Lör + 2 days")
        self.assertTrue(result["backup"], "a backup is taken first")
        self.assertTrue((Path(self.temp.name) / "backups" / result["backup"]).exists())
        self.assertEqual(self.ops.clock_status()["time"][:5], "14:00")
        self.assertEqual({p["train_number"]: p["station_id"] for p in self.ops.positions()}.get("101"), "station-b",
                         "at 14:00 101 has arrived at B")
        self.assertEqual(self.numbers(), ["101"], "Monday: no Sunday train")
        back = self.jump(1, "08:00")
        self.assertEqual(back["active_day"], "Lör")
        self.assertEqual({p["train_number"]: p["station_id"] for p in self.ops.positions()}.get("101"), "station-a")
        self.assertTrue(self.live()["movement-101-a"]["by_timetable"], "the real departure is gone")
        self.assertEqual(self.app.display_snapshot()["calendar"]["day_number"], 1)

    def test_only_an_administrator_and_only_with_the_current_meet(self):
        box = PairedClient(client_id="esp32", display_name="Box", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-b",),
                           station_id="station-b", admin_role="")
        with self.assertRaises(HTTPAPIError) as raised:
            self.jump(2, "10:00", client=box)
        self.assertEqual(raised.exception.status, 403)
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.time_machine(self.admin, {"day_number": 2, "time": "10:00", "meet_generation": self.generation() - 1})
        self.assertEqual(raised.exception.status, 409)
        for day_number, time_value in ((0, "10:00"), (2, "25:00"), (2, "kväll"), ("2", "10:00")):
            with self.assertRaises(HTTPAPIError) as raised:
                self.jump(day_number, time_value)
            self.assertEqual(raised.exception.status, 400, (day_number, time_value))
        self.assertEqual(self.app.display_snapshot()["calendar"]["day_number"], 1, "nothing changed")

    def test_a_new_start_day_moves_the_whole_meet(self):
        result = self.app.save_meet_calendar(self.admin, {"start_day": "Fre", "meet_generation": self.generation()})
        self.assertEqual((result["start_day"], result["weekday"], result["changed"]), ("Fre", "Fre", True))
        self.assertEqual(self.app.display_snapshot()["active_day"], "Fre")
        with self.assertRaises(HTTPAPIError):
            self.app.save_meet_calendar(self.admin, {"start_day": "Fredag kväll", "meet_generation": self.generation()})


class DayFixes(_Meet):
    def test_setting_the_day_with_fastclock_as_the_clock(self):
        """FastClocks tid har ingen löpande tid (elapsed_seconds): bytet fick KeyError."""
        real = self.ops.clock_status
        self.ops.clock_status = lambda *args, **kwargs: {**real(), "elapsed_seconds": None, "seconds": float(at(10)), "running": False}
        result = self.app.set_active_day(self.admin, {"active_day": "Sön", "meet_generation": self.generation()})
        self.assertTrue(result["changed"])
        self.assertEqual(self.runtime.meet_calendar(self.pub.meet_id)["weekday"], "Sön", "the calendar follows")

    def test_days_are_read_as_the_timetable_writes_them(self):
        self.assertTrue(matches_active_day("Mån-Fre,Sön", "Tis"), "a range inside a list")
        self.assertTrue(matches_active_day("M-F", "Ons"))
        self.assertTrue(matches_active_day("Lö", "Lör"))
        self.assertTrue(matches_active_day("S", "Sön"))
        self.assertTrue(matches_active_day("Fre-Mån", "Sön"), "over the week's end")
        self.assertTrue(matches_active_day("dagligen", "Tis"))
        self.assertFalse(matches_active_day("Mån-Fre", "Lör"))
        self.assertFalse(matches_active_day("Lör", "Sön"))

    def test_the_weekday_goes_forward_one_step_per_day(self):
        self.assertEqual([calendar_weekday("Fre", n) for n in (1, 2, 3, 4, 8)], ["Fre", "Lör", "Sön", "Mån", "Fre"])
        self.assertEqual(calendar_weekday("Dagl", 5), "Dagl")


if __name__ == "__main__":
    unittest.main()
