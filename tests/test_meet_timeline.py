"""Träffens tid på Drift: tidslinjen, förhandsvisningen och Ångra (Casper 2026-10-12).

Under klockan på Drift ligger träffens dygn på rad. Admin drar i tidslinjen
och ser hur tågen kommer att stå innan tidsmaskinen hoppar; inget ändras
förrän hon trycker Hoppa. Direkt efter ett hopp eller ett nytt dygn finns
Ångra, som lägger tillbaka kopian som togs före.

Fixturens tåg: 101 går Dagl (A 09:20 → B 09:35), 202 bara söndagar.
Trafikdagen är Lör, så dygnsskiftet ligger en timme före 101: 08:20.
"""
from __future__ import annotations

import dataclasses
import time
from pathlib import Path
import unittest

from test_meet_calendar import _Meet, at
from tmbox_gateway import backup
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.operations import _now_iso


class TimelineTests(_Meet):
    def test_every_day_runs_from_its_day_change_to_the_next_with_its_traffic(self):
        timeline = self.app.meet_timeline(self.admin)
        self.assertTrue(timeline["supported"])
        days = timeline["days"]
        self.assertEqual([(day["day_number"], day["weekday"]) for day in days], [(1, "Lör"), (2, "Sön"), (3, "Mån")],
                         "at least three days, and always the one after today")
        self.assertEqual((days[0]["start"], days[0]["end"]), (at(8, 20), 86400 + at(8, 20)),
                         "an hour before the first train, to the next day's change")
        self.assertEqual((days[0]["first"], days[0]["last"]), (at(9, 20), at(9, 35)))
        self.assertEqual(timeline["slot_seconds"], 1800)
        self.assertEqual(len(days[0]["density"]), 48)
        self.assertEqual(days[0]["density"][2], 1, "101 runs in the half hour from 09:20")
        self.assertEqual(sum(days[0]["density"]), 1)
        self.assertGreater(sum(days[1]["density"]), sum(days[0]["density"]), "Sunday has 202 as well")
        self.assertEqual(timeline["now"]["day_number"], 1)
        self.assertIsNone(timeline["undo"])

    def test_more_days_when_asked_and_the_day_after_today_always(self):
        self.assertEqual(len(self.app.meet_timeline(self.admin, days=6)["days"]), 6)
        self.runtime.set_meet_calendar(self.pub.meet_id, "Lör", 5)
        self.assertEqual(self.app.meet_timeline(self.admin)["days"][-1]["day_number"], 6)
        long = self.app.meet_timeline(self.admin, days=80)["days"]
        self.assertEqual((long[0]["day_number"], long[-1]["day_number"], len(long)), (50, 80, 31), "a month at a time")

    def test_the_preview_says_how_the_trains_will_stand_and_changes_nothing(self):
        before = self.ops.positions()
        early = self.app.time_machine_preview(self.admin, 1, at(9))
        self.assertEqual((early["at_station"], early["on_line"], early["finished"], early["trains"]), (1, 0, 0, 1),
                         "101 stands at its first station before it leaves")
        self.assertEqual(early["next"], {"train_number": "101", "time": "09:20", "from": "Charlottendahl", "to": "Lekeberg"})
        self.assertEqual((early["weekday"], early["time"]), ("Lör", "09:00"))
        out = self.app.time_machine_preview(self.admin, 1, at(9, 25))
        self.assertEqual((out["on_line"], out["at_station"]), (1, 0), "between A and B by the timetable")
        done = self.app.time_machine_preview(self.admin, 1, at(10))
        self.assertEqual((done["finished"], done["next"]), (1, None))
        sunday = self.app.time_machine_preview(self.admin, 2, at(6))
        self.assertEqual((sunday["weekday"], sunday["trains"]), ("Sön", 2))
        self.assertEqual(self.ops.positions(), before, "a preview moves no train")
        with self.assertRaises(HTTPAPIError):
            self.app.time_machine_preview(self.admin, 0, at(6))
        with self.assertRaises(HTTPAPIError):
            self.app.time_machine_preview(self.admin, 1, 3 * 86400)

    def test_the_time_machine_takes_seconds_into_the_night(self):
        self.app.time_machine(self.admin, {"day_number": 1, "seconds": 86400 + at(2, 0, 30), "meet_generation": self.generation()})
        clock = self.ops.clock_status()
        self.assertEqual(clock["time"][:5], "02:00", "whole minutes")
        self.assertEqual(self.app.display_snapshot()["calendar"]["day_number"], 1, "the night belongs to the day before")
        for bad in (-1, 2 * 86400, True, "02:00"):
            with self.assertRaises(HTTPAPIError):
                self.app.time_machine(self.admin, {"day_number": 1, "seconds": bad, "meet_generation": self.generation()})


class UndoTests(_Meet):
    def setUp(self):
        super().setUp()
        self.app.config = dataclasses.replace(self.app.config, allow_restart=True)

    def jump(self, day=2, seconds=at(10)):
        return self.app.time_machine(self.admin, {"day_number": day, "seconds": seconds, "meet_generation": self.generation()})

    def test_a_jump_can_be_undone_with_the_copy_taken_before_it(self):
        self.run_clock_at(at(9, 30))
        result = self.jump()
        self.assertEqual(result["undo"]["backup"], result["backup"])
        self.assertEqual(result["undo"]["from"], {"day_number": 1, "weekday": "Lör", "time": "09:30"})
        self.assertEqual(self.app.meet_timeline(self.admin)["undo"]["backup"], result["backup"], "a reloaded page still offers it")
        undone = self.app.undo_jump(self.admin, {"backup": result["backup"]})
        self.assertEqual(undone["status"], "restoring")
        self.assertEqual(Path(undone["path"]).name, result["backup"])
        self.assertIn("Dag 1 · Lör kl. 09:30", undone["message"])
        with self.assertRaises(HTTPAPIError) as again:
            self.app.undo_jump(self.admin, {"backup": result["backup"]})
        self.assertEqual(again.exception.code, "undo_expired", "once")

    def test_a_new_day_can_be_undone_too(self):
        self.run_clock_at(86400 + at(6))
        result = self.app.start_new_day(self.admin, {"meet_generation": self.generation()})
        self.assertEqual(result["undo"]["kind"], "nytt-dygn")
        self.assertEqual(result["undo"]["from"]["day_number"], 1)

    def test_only_the_latest_copy_within_half_an_hour(self):
        first = self.jump()
        time.sleep(1.1)  # kopiornas namn har sekunden i sig
        second = self.jump(day=3)
        self.assertNotEqual(first["backup"], second["backup"])
        with self.assertRaises(HTTPAPIError) as old:
            self.app.undo_jump(self.admin, {"backup": first["backup"]})
        self.assertEqual(old.exception.code, "undo_expired", "only the last jump")
        self.app._last_jump["at"] -= self.app.UNDO_WINDOW + 1  # noqa: SLF001
        self.assertIsNone(self.app.meet_timeline(self.admin)["undo"])
        with self.assertRaises(HTTPAPIError) as late:
            self.app.undo_jump(self.admin, {"backup": second["backup"]})
        self.assertEqual(late.exception.code, "undo_expired")

    def test_not_after_a_newer_copy_or_when_access_changed(self):
        result = self.jump()
        time.sleep(1.1)
        self.app._backup_before("nollställning")  # noqa: SLF001
        with self.assertRaises(HTTPAPIError) as newer:
            self.app.undo_jump(self.admin, {"backup": result["backup"]})
        self.assertEqual(newer.exception.code, "undo_superseded")

        result = self.jump()
        with self.ids._lock:  # noqa: SLF001
            self.ids._connection.execute(  # noqa: SLF001
                "INSERT INTO admin_users(user_id, username, role, created_at, updated_at) VALUES('u-x','Nisse','admin',?,?)",
                (_now_iso(), _now_iso()))
            self.ids._connection.commit()  # noqa: SLF001
        with self.assertRaises(HTTPAPIError) as access:
            self.app.undo_jump(self.admin, {"backup": result["backup"]})
        self.assertEqual(access.exception.code, "undo_access_changed", "who may log in is the owner's to restore")

    def test_no_undo_where_the_server_cannot_restart(self):
        self.app.config = dataclasses.replace(self.app.config, allow_restart=False)
        result = self.jump()
        self.assertIsNone(result["undo"])
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.undo_jump(self.admin, {"backup": result["backup"]})
        self.assertEqual(refused.exception.code, "undo_unavailable")
        self.assertEqual([backup.kind_of(path) for path in (Path(self.temp.name) / "backups").glob("*.db")], ["tidsmaskin"],
                         "the copy is still taken")


if __name__ == "__main__":
    unittest.main()
