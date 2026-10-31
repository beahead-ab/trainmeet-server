"""Planens speldygn i Clouds paket (Cloud #100, `clock.traffic_week`).

Värnamo Moduldagar kör sex modelldygn från måndag, dygnet runt, och några
tåg går bara vissa dagar: TXL CG 38958 måndag och tisdag, HCTOR HG 45696
onsdag till fredag, i samma läge. En ny träff ur en sådan plan börjar på
planens veckodag och byter dygn automatiskt, så att bara dagens tåg kör.
En träff som redan har en kalender behåller den: det admin valt gäller, och
en pågående träff ändras inte av en ny publicering.
"""
from __future__ import annotations

import unittest

from runtime_fixture import runtime_package_v3
from test_meet_calendar import _Meet, at
from tmbox_gateway.runtime import RuntimePublication, calendar_weekday, matches_active_day

WEEK = {"start_weekday": 1, "days": 6, "runs_over_midnight": True}


def with_week(package, week=WEEK):
    package["clock"]["traffic_week"] = dict(week)
    return package


class PlanWeekTests(_Meet):
    def package(self):
        return with_week(runtime_package_v3())

    def test_a_new_meet_starts_on_the_plans_weekday_and_changes_day_by_itself(self):
        calendar = self.app.display_snapshot()["calendar"]
        self.assertEqual({key: calendar[key] for key in ("start_day", "day_number", "weekday", "day_change_mode")},
                         {"start_day": "Mån", "day_number": 1, "weekday": "Mån", "day_change_mode": "auto"})
        self.assertEqual("Mån", self.runtime.active_day(), "the traffic day is the plan's, not the package's Lör")

    def test_at_the_day_change_monday_becomes_tuesday(self):
        # Fixturens första tåg på tisdag är 101 kl 09:20, så skiftet är 08:20.
        self.run_clock_at(86400 + at(8, 20) + 30)
        result = self.app.calendar_tick()
        self.assertEqual((result["day_number"], result["active_day"]), (2, "Tis"))

    def test_the_game_week_runs_monday_to_saturday_and_shared_paths_never_meet(self):
        week = [calendar_weekday("Mån", number) for number in range(1, WEEK["days"] + 1)]
        self.assertEqual(["Mån", "Tis", "Ons", "Tor", "Fre", "Lör"], week)
        # Så som Cloud skriver planens dagar: M,Ti blir Mån-Tis och O-F Ons-Fre.
        txl = [day for day in week if matches_active_day("Mån-Tis", day)]
        hctor = [day for day in week if matches_active_day("Ons-Fre", day)]
        self.assertEqual((["Mån", "Tis"], ["Ons", "Tor", "Fre"]), (txl, hctor))
        self.assertFalse(set(txl) & set(hctor), "never both on the same evening")

    def test_a_meet_that_already_has_a_calendar_keeps_it(self):
        self.runtime.set_meet_calendar(self.pub.meet_id, "Lör", 3)
        self.runtime.set_day_change_mode(self.pub.meet_id, "manual")
        self.runtime.install(with_week(runtime_package_v3(publication_id="publication-2026-08-11-b")))
        calendar = self.runtime.meet_calendar(self.pub.meet_id)
        self.assertEqual((calendar["start_day"], calendar["day_number"], calendar["day_change_mode"]), ("Lör", 3, "manual"))
        self.assertEqual("Lör", self.runtime.active_day(), "the package's traffic day, as before")


class OlderPackageTests(_Meet):
    def test_a_package_without_a_game_week_starts_as_before(self):
        calendar = self.app.display_snapshot()["calendar"]
        self.assertEqual((calendar["start_day"], calendar["day_change_mode"]), ("Lör", "manual"))
        self.assertEqual("Lör", self.runtime.active_day())


class ReadingTests(unittest.TestCase):
    def test_the_game_week_is_read_strictly(self):
        def week(value):
            package = runtime_package_v3()
            package["clock"]["traffic_week"] = value
            return RuntimePublication.parse(package).traffic_week
        self.assertEqual({"start_day": "Mån", "days": 6, "runs_over_midnight": True}, week(WEEK))
        self.assertEqual({"start_day": "Sön"}, week({"start_weekday": 7}))
        self.assertEqual({}, week({"start_weekday": 8, "days": 0, "runs_over_midnight": "ja"}))
        self.assertEqual({}, week(None))
        self.assertEqual({}, week({"start_weekday": True}))


if __name__ == "__main__":
    unittest.main()
