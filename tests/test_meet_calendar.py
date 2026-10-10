"""Träffkalendern: dagen går fram vid dygnsskiftet, och tidsmaskinen (Casper 2026-10-08).

Träffen börjar på en dag admin väljer. Vid varje dygnsskifte går dagen fram
och tidtabellen för den nya veckodagen gäller, Dagl alltid. Skiftet ligger
en timme före den nya dagens första tågrörelse (en fast tid kan anges):
mellan midnatt och skiftet är det kvar gårdagens trafikdygn, så att nattåg
kör klart. Vid
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
from tmbox_gateway import backup
from test_timetable_placement import add_service
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore, _now_iso
from tmbox_gateway.runtime import SQLiteRuntimeStore, calendar_weekday, matches_active_day


def at(hours, minutes=0, seconds=0):
    return hours * 3600 + minutes * 60 + seconds


#: Klockan vid dygnsskiftet nästa morgon, i sekunder sedan träffdagens början:
#: en timme före söndagens första tågrörelse (101 från A 09:20).
CHANGE = 86400 + at(8, 20)
CHANGE_TIME = "08:20"


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
        if self.day_change_mode:
            self.runtime.set_day_change_mode(self.pub.meet_id, self.day_change_mode)

    #: Nytt trafikdygn: None är förvalet (manuellt, Starta ny dag).
    day_change_mode = None

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
    day_change_mode = "auto"

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
        self.assertLess(clock["elapsed_seconds"], at(8, 20) + 120, "and goes on from the day change")
        self.assertEqual(clock["time"][:5], "08:20")
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

    def test_with_fastclock_the_new_day_starts_when_the_clock_passes_the_day_change_after_midnight(self):
        self.runtime.set_day_change_time(self.pub.meet_id, "05:00")
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
        self.assertLess(after["start"], at(8, 20) + 60, "the new day's trains are sent from the day change, not history")
        self.assertEqual(after["stations"], {"station-a": "tmbox-1"}, "who works where stays")


class ManualNewDayTests(_Meet):
    """Starta ny dag (Casper 2026-10-09): förvalet är att admin startar nästa
    dag själv. Automatiskt dygnsskifte är ett val, för tidtabeller som går
    dygnet runt."""

    def new_day(self, client=None, **payload):
        return self.app.start_new_day(client or self.admin, {"meet_generation": self.generation(), **payload})

    def arrive_101(self, day):
        self.ops.update_tkl_movement(self.pub.publication_id, day, "station-b", "movement-101-b", arrival="arrived",
                                     departure="none", actual_track=None, updated_by="TKL Bor", shift_id=None,
                                     event_type="train.arrived", operator_note=None)

    def test_by_default_the_clock_passing_the_day_change_only_reminds(self):
        self.assertEqual(self.app.display_snapshot()["calendar"]["day_change_mode"], "manual")
        self.run_clock_at(CHANGE + 30)
        self.assertIsNone(self.app.calendar_tick(), "no new day by itself")
        calendar = self.app.display_snapshot()["calendar"]
        self.assertEqual((calendar["day_number"], calendar["weekday"], calendar["due"], calendar["waiting"]), (1, "Lör", True, False))
        self.assertEqual(calendar["next_day"], {"day_number": 2, "weekday": "Sön", "time": CHANGE_TIME},
                         "after midnight the new day starts where the clock is")
        self.run_clock_at(at(12, 0))
        self.assertIsNone(self.app.calendar_tick())
        self.assertFalse(self.app.display_snapshot()["calendar"]["due"], "set back before the day change: nothing to remind of")

    def test_starting_a_new_day_is_the_day_change_when_admin_says_so(self):
        self.run_clock_at(at(9, 30))
        self.depart_101("Lör")
        self.arrive_101("Lör")
        self.run_clock_at(86400 + at(6, 0))
        result = self.new_day()
        self.assertEqual((result["day_number"], result["active_day"]), (2, "Sön"))
        snapshot = self.app.display_snapshot()
        self.assertEqual((snapshot["active_day"], snapshot["calendar"]["day_number"], snapshot["calendar"]["due"]), ("Sön", 2, False))
        self.assertEqual(self.numbers(), ["101", "202"], "Sunday's trains and the daily ones")
        self.assertTrue(self.live()["movement-101-a"]["by_timetable"], "statuses start over")
        self.assertEqual({p["train_number"]: p["station_id"] for p in self.ops.positions()}.get("101"), "station-a")
        clock = self.ops.clock_status()
        self.assertEqual((clock["time"][:5], clock["running"]), ("06:00", True), "the clock goes on from where it was")
        self.assertEqual(snapshot["calendar"]["last_change"]["day_number"], 2, "every view is told")
        backups = sorted((Path(self.temp.name) / "backups").glob("*.db"))
        self.assertEqual([backup.kind_of(path) for path in backups], ["nytt-dygn"], "a backup is taken first")
        logged = self.ops._connection.execute(  # noqa: SLF001
            "SELECT detail_json FROM audit_events WHERE action='meet.day_started'").fetchall()
        self.assertEqual(len(logged), 1)
        rows = self.ops._connection.execute(  # noqa: SLF001
            "SELECT count(*) FROM tkl_events WHERE publication_id=? AND active_day='Lör'", (self.pub.publication_id,)).fetchone()[0]
        self.assertEqual(rows, 2, "yesterday stays as history")

    def test_in_the_evening_the_new_day_starts_at_the_time_given(self):
        self.run_clock_at(at(21, 0))
        self.assertEqual(self.app.display_snapshot()["calendar"]["next_day"]["time"], CHANGE_TIME,
                         "before midnight the suggestion is the day change, an hour before the first train")
        self.new_day(time="07:15")
        self.assertEqual(self.ops.clock_status()["time"][:5], "07:15")
        self.assertEqual(self.app.display_snapshot()["calendar"]["day_number"], 2)

    def test_a_train_out_on_the_line_runs_its_course_first(self):
        self.run_clock_at(at(9, 30))
        self.depart_101("Lör")
        with self.assertRaises(HTTPAPIError) as refused:
            self.new_day()
        self.assertEqual((int(refused.exception.status), refused.exception.code), (409, "day_still_running"))
        self.assertEqual(self.app.display_snapshot()["calendar"]["day_number"], 1)
        self.arrive_101("Lör")
        self.assertEqual(self.new_day()["day_number"], 2)

    def test_only_an_administrator_with_the_current_meet(self):
        box = PairedClient("box", "Box", DeviceKind.ESP32_PANEL, ("panel-a",))
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.start_new_day(box, {"meet_generation": self.generation()})
        self.assertEqual(int(refused.exception.status), 403)
        with self.assertRaises(HTTPAPIError) as stale:
            self.app.start_new_day(self.admin, {"meet_generation": self.generation() - 1})
        self.assertEqual(int(stale.exception.status), 409)
        with self.assertRaises(HTTPAPIError) as bad:
            self.new_day(time="25:99")
        self.assertEqual(bad.exception.code, "invalid_time")

    def test_automatic_is_a_setting_for_timetables_around_the_clock(self):
        changed = self.app.save_meet_calendar(self.admin, {"start_day": "Lör", "day_change_mode": "auto",
                                                           "meet_generation": self.generation()})
        self.assertEqual(changed["day_change_mode"], "auto")
        self.run_clock_at(CHANGE + 30)
        self.assertEqual(self.app.calendar_tick()["day_number"], 2, "the day changes by itself")
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.save_meet_calendar(self.admin, {"start_day": "Sön", "day_change_mode": "ibland",
                                                     "meet_generation": self.generation()})
        self.assertEqual(refused.exception.code, "invalid_day_change_mode")


class DayChangeSettingsTests(_Meet):
    """Skiftets tid, utgångspunkterna och toasten i vyerna."""
    day_change_mode = "auto"


    def package(self):
        package = runtime_package_v3()
        # 401 går tidigt, 04:30: skiftet blir 03:30, en timme före.
        add_service(package, "401", [("station-a", None, "04:30", "track-station-a-1"),
                                     ("station-b", "04:45", None, "track-station-b-1")])
        return package

    def test_the_day_change_is_an_hour_before_the_new_days_first_train(self):
        calendar = self.app.display_snapshot()["calendar"]
        self.assertEqual((calendar["change_time"], calendar["change_auto"], calendar["change_time_set"]), ("03:30", True, None))
        self.run_clock_at(86400 + at(3, 29))
        self.assertIsNone(self.app.calendar_tick(), "midnight until the day change is still yesterday")
        snapshot = self.app.display_snapshot()
        self.assertEqual((snapshot["active_day"], snapshot["calendar"]["day_number"]), ("Lör", 1))
        self.run_clock_at(86400 + at(3, 30, 30))
        self.assertEqual(self.app.calendar_tick()["day_number"], 2)

    def test_every_train_of_the_new_day_stands_at_its_starting_point(self):
        self.runtime.set_day_change_time(self.pub.meet_id, "05:00")
        self.run_clock_at(86400 + at(5, 0, 30))
        self.assertEqual(self.app.calendar_tick()["day_number"], 2)
        positions = {p["train_number"]: p["station_id"] for p in self.ops.positions()}
        self.assertEqual((positions.get("401"), positions.get("101")), ("station-a", "station-a"),
                         "401 is due at 04:30 but has not left: it waits at its first station")
        live = self.live()
        self.assertEqual(live["movement-401-0"]["departure"], "positioned")
        self.assertNotEqual(live.get("movement-401-1", {}).get("arrival"), "arrived")

    def test_a_fixed_day_change_time_can_be_set_and_cleared(self):
        result = self.app.save_meet_calendar(self.admin, {"start_day": "Lör", "change_time": "4:15",
                                                          "meet_generation": self.generation()})
        self.assertEqual((result["change_time"], result["change_auto"], result["changed"]), ("04:15", False, False),
                         "a fixed time, and no new day for it")
        self.run_clock_at(86400 + at(4, 15, 5))
        self.assertEqual(self.app.calendar_tick()["day_number"], 2)
        cleared = self.app.save_meet_calendar(self.admin, {"start_day": "Lör", "change_time": "", "meet_generation": self.generation()})
        self.assertEqual((cleared["change_time"], cleared["change_auto"]), ("03:30", True), "empty: automatic again")
        for wrong in ("25:00", "kväll", "12"):
            with self.assertRaises(HTTPAPIError) as raised:
                self.app.save_meet_calendar(self.admin, {"start_day": "Lör", "change_time": wrong, "meet_generation": self.generation()})
            self.assertEqual(raised.exception.status, 400, wrong)
        box = PairedClient(client_id="esp32", display_name="Box", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-b",),
                           station_id="station-b", admin_role="")
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.save_meet_calendar(box, {"start_day": "Lör", "change_time": "03:00", "meet_generation": self.generation()})
        self.assertEqual(raised.exception.status, 403)
        self.assertIsNone(self.runtime.meet_calendar(self.pub.meet_id)["change_time_set"])

    def test_a_day_change_is_announced_to_the_views_and_a_time_machine_jump_is_not(self):
        self.assertIsNone(self.app.display_snapshot()["calendar"]["last_change"])
        self.run_clock_at(86400 + at(3, 30, 5))
        self.app.calendar_tick()
        last = self.app.display_snapshot()["calendar"]["last_change"]
        self.assertEqual((last["kind"], last["day_number"], last["weekday"]), ("day_change", 2, "Sön"))
        self.assertTrue(last["at"].endswith("Z"))
        self.app.time_machine(self.admin, {"day_number": 4, "time": "10:00", "meet_generation": self.generation()})
        self.assertEqual(self.app.display_snapshot()["calendar"]["last_change"], last, "the jump is not a day change")


class NextDayChangeTimeTests(_Meet):
    """It is the new day's first train that counts, not today's."""
    day_change_mode = "auto"


    def package(self):
        package = runtime_package_v3()
        # 808 går bara på söndagar, 06:00. Lördagens första tåg är 101 09:20.
        add_service(package, "808", [("station-a", None, "06:00", "track-station-a-1"),
                                     ("station-b", "06:15", None, "track-station-b-1")])
        for collection in ("services", "routes", "trains"):
            for item in package[collection]:
                if item.get("train_number") == "808":
                    item["days"] = "Sön"
        return package

    def test_saturday_into_sunday_changes_an_hour_before_sundays_first_train(self):
        self.assertEqual(self.app.display_snapshot()["calendar"]["change_time"], "05:00")
        self.run_clock_at(86400 + at(5, 0, 30))
        self.assertEqual(self.app.calendar_tick()["active_day"], "Sön")


class ResetToDayOneTests(_Meet):
    package = NextDayChangeTimeTests.package

    def test_a_reset_starts_the_meet_over_on_day_one(self):
        """Nollställ träffen börjar om från början: dag 1 och planens
        starttid, oavsett vart tidsmaskinen hoppat (Casper 2026-10-08)."""
        saturday = self.numbers()
        self.run_clock_at(at(9, 30))
        jumped = self.app.time_machine(self.admin, {"day_number": 2, "time": "14:00", "meet_generation": self.generation()})
        self.assertEqual(self.app.display_snapshot()["calendar"]["day_number"], 2)
        self.assertIn("808", self.numbers(), "Sunday")
        reset = self.app.reset_meet(self.admin, {"confirmation": self.app._current_meet_name(), "meet_generation": self.generation()})  # noqa: SLF001
        calendar = self.app.display_snapshot()["calendar"]
        self.assertEqual((calendar["day_number"], calendar["weekday"], self.runtime.active_day()), (1, "Lör", "Lör"))
        self.assertFalse(self.ops.clock_status()["running"])
        self.assertEqual(self.numbers(), saturday, "Saturday's trains again")
        # Kopiorna säger varför de togs och var träffen stod.
        folder = Path(self.temp.name) / "backups"
        before_jump = backup.describe(folder / jumped["backup"])
        self.assertEqual((before_jump["kind"], before_jump["meet_day"], before_jump["clock_time"]),
                         ("tidsmaskin", {"day_number": 1, "weekday": "Lör"}, "09:30"))
        before_reset = backup.describe(folder / reset["backup"])
        self.assertEqual((before_reset["kind"], before_reset["meet_day"]), ("nollstallning", {"day_number": 2, "weekday": "Sön"}))
        self.assertEqual(before_reset["clock_time"][:4], "14:0")


class AutomaticChangeTimeTests(_Meet):
    """The day change follows the next day's first train, day by day."""
    day_change_mode = "auto"


    def package(self):
        package = runtime_package_v3()
        # 707 går varje dag 00:30, strax efter midnatt.
        add_service(package, "707", [("station-a", None, "00:30", "track-station-a-1"),
                                     ("station-b", "00:45", None, "track-station-b-1")])
        return package

    def test_never_before_midnight_and_five_without_trains(self):
        self.assertEqual(self.app.display_snapshot()["calendar"]["change_time"], "00:00",
                         "a train at 00:30: the change at midnight, not the evening before")
        self.runtime.set_meet_calendar(self.pub.meet_id, "Lör", 1)
        real = self.app._first_movement_seconds  # noqa: SLF001
        self.app._first_movement_seconds = lambda publication, day: None  # noqa: SLF001
        try:
            self.assertEqual(self.app.display_snapshot()["calendar"]["change_time"], "05:00", "no trains that day")
        finally:
            self.app._first_movement_seconds = real  # noqa: SLF001


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
