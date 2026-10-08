"""Förseningar i TMBox-tidtabellen (webb-TMBoxen och iPhone-appen), Casper 2026-10-08.

train_live.py räknar som web/drift-model.js trainLive: samma fall som
tests/js/train-live.test.cjs. /v1/tmbox/terminal/timetable ger varje rad
tågets försening, den nya tiden och träffens förval för hur mycket som visas.
"""
from __future__ import annotations

import unittest

from test_movement_live import _Meet, at
from tmbox_gateway.train_live import expected_time, on_line_trains, train_live

# 101: A 09:20 → B 09:35/09:37 → C 09:50
STOPS = [("a", None, "09:20"), ("b", "09:35", "09:37"), ("c", "09:50", None)]


def services(train_type=None):
    service = {"id": "s101", "train_number": "101",
               "stops": [{"station_id": station, "arrival_time": arrival, "departure_time": departure, "stop_order": order}
                         for order, (station, arrival, departure) in enumerate(STOPS)]}
    if train_type:
        service["train_type"] = train_type
    return [service]


TRAINS = [{"id": f"m{order}", "service_id": "s101", "train_number": "101", "station_id": station,
           "arrival_time": arrival, "departure_time": departure} for order, (station, arrival, departure) in enumerate(STOPS)]


def live101(movement_live=None, now="09:00", *, train_type=None, on_line=frozenset()):
    hours, minutes = (int(part) for part in now.split(":"))
    return train_live(services(train_type), TRAINS, movement_live or {}, at(hours, minutes), on_line)["101"]


def hhmm(value):
    hours, minutes = (int(part) for part in value.split(":"))
    return at(hours, minutes)


class TrainLiveTests(unittest.TestCase):
    def test_a_late_departure_is_late_from_three_minutes(self):
        two = live101({"m0": {"departure": "departed", "departed_seconds": hhmm("09:22")}}, "09:25")
        self.assertEqual((two["state"], two["delay_minutes"], two["late"]), ("on_line", 2, False))
        seven = live101({"m0": {"departure": "departed", "departed_seconds": hhmm("09:27")}}, "09:30")
        self.assertEqual((seven["delay_minutes"], seven["late"], seven["estimated"]), (7, True, False))
        three = live101({"m0": {"departure": "departed", "departed_seconds": hhmm("09:23")}}, "09:24")
        self.assertTrue(three["late"], "exactly three is late")

    def test_a_train_still_standing_after_its_departure_counts_up_estimated(self):
        waiting = live101(None, "09:26")
        self.assertEqual((waiting["state"], waiting["delay_minutes"], waiting["estimated"]), ("waiting", 6, True))
        self.assertEqual(live101(None, "09:30")["delay_minutes"], 10, "and keeps counting")
        self.assertEqual(live101(None, "09:00")["state"], "not_departed")

    def test_a_position_the_timetable_gave_is_on_time_until_its_departure_has_passed(self):
        placed = {"m0": {"departure": "departed", "by_timetable": True},
                  "m1": {"arrival": "arrived", "departure": "positioned", "by_timetable": True}}
        before = live101(placed, "09:36")
        self.assertEqual((before["state"], before["delay_minutes"], before["late"]), ("at_station", 0, False))
        after = live101(placed, "09:45")
        self.assertEqual((after["state"], after["delay_minutes"], after["estimated"]), ("waiting", 8, True))

    def test_a_position_recorded_without_a_time_keeps_the_delay_known_before_it(self):
        jumped = {"m0": {"departure": "departed", "departed_seconds": hhmm("09:27")},
                  "m1": {"arrival": "arrived", "departure": "positioned"}}
        train = live101(jumped, "09:36")
        self.assertEqual((train["state"], train["delay_minutes"], train["late"]), ("at_station", 7, True))

    def test_an_arrival_at_the_last_station_gives_the_arrival_delay(self):
        done = {"m0": {"departure": "departed", "departed_seconds": hhmm("09:20")},
                "m1": {"arrival": "arrived", "departure": "departed", "arrived_seconds": hhmm("09:35"), "departed_seconds": hhmm("09:38")},
                "m2": {"arrival": "arrived", "arrived_seconds": hhmm("09:56")}}
        train = live101(done, "10:10")
        self.assertEqual((train["state"], train["delay_minutes"], train["late"]), ("arrived", 6, True))

    def test_a_train_on_the_line_by_its_channel_is_on_the_line_even_without_times(self):
        train = live101(None, "09:21", on_line={"101"})
        self.assertEqual((train["state"], train["late"], train["estimated"]), ("on_line", False, False))
        self.assertEqual(live101(None, "09:30", on_line={"101"})["delay_minutes"], 0, "not counted up while it runs")

    def test_early_departure_and_arrival_say_which_and_carry_the_train_type(self):
        early = live101({"m0": {"departure": "departed", "departed_seconds": hhmm("09:18")}}, "09:19")
        self.assertEqual((early["delay_minutes"], early["early_minutes"], early["early_kind"], early["train_type"]), (0, 2, "dep", "person"))
        goods = live101({"m0": {"departure": "departed", "departed_seconds": hhmm("09:18")}}, "09:19", train_type="Goods")
        self.assertEqual((goods["early_minutes"], goods["train_type"]), (2, "goods"), "the client decides; freight may leave early")
        arrival = live101({"m0": {"departure": "departed", "departed_seconds": hhmm("09:20")},
                           "m1": {"arrival": "arrived", "arrived_seconds": hhmm("09:32")}}, "09:33")
        self.assertEqual((arrival["early_minutes"], arrival["early_kind"]), (3, "arr"))
        estimated = live101(None, "09:26")
        self.assertEqual((estimated["early_minutes"], estimated["early_kind"]), (0, None), "an estimate is never early")

    def test_a_stop_after_midnight_is_on_the_next_day(self):
        night = [{"id": "s7", "train_number": "7", "stops": [
            {"station_id": "a", "departure_time": "23:50", "stop_order": 0},
            {"station_id": "b", "arrival_time": "00:10", "stop_order": 1, "service_day_offset": 1}]}]
        rows = [{"id": "n0", "service_id": "s7", "station_id": "a", "departure_time": "23:50"},
                {"id": "n1", "service_id": "s7", "station_id": "b", "arrival_time": "00:10"}]
        train = train_live(night, rows, {"n0": {"departure": "departed", "departed_seconds": hhmm("23:50")},
                                         "n1": {"arrival": "arrived", "arrived_seconds": 86400 + hhmm("00:14")}}, 86400 + hhmm("00:20"))["7"]
        self.assertEqual((train["state"], train["delay_minutes"]), ("arrived", 4))

    def test_on_line_trains_follow_occupied_channels_and_positions(self):
        states = [{"channels": [{"train_number": "101", "state": "occupied"}, {"train_number": "102", "state": "reserved"},
                                {"train_number": "103", "state": "free"}]}]
        positions = [{"train_number": "102", "status": "connection"}, {"train_number": "104", "status": "connection"},
                     {"train_number": "105", "status": "station"}]
        self.assertEqual(on_line_trains(states, positions), {"101", "104"})

    def test_expected_time_adds_the_delay_across_midnight(self):
        self.assertEqual(expected_time("09:35", 7), "09:42")
        self.assertEqual(expected_time("23:58", 5), "00:03")
        self.assertIsNone(expected_time("09:35", 0))
        self.assertIsNone(expected_time("--:--", 4))


class TerminalTimetableTests(_Meet):
    """The rows /v1/tmbox/terminal/timetable gives a box carry the deviation."""

    def rows(self):
        table = {"rows": [{"train_number": "101", "kind": "departure", "time": "09:20"},
                          {"train_number": "101", "kind": "arrival", "time": "09:35"}]}
        return self.app._timetable_with_deviations(table)  # noqa: SLF001

    def test_a_late_departure_gives_the_rows_the_delay_and_the_new_time(self):
        self.clock("09:27")
        self.move("station-a", "movement-101-a", departure="departed")
        table = self.rows()
        departure, arrival = table["rows"]
        self.assertEqual((arrival["delay_minutes"], arrival["expected_time"], arrival["estimated"]), (7, "09:42", False))
        self.assertEqual(departure["expected_time"], "09:27")
        self.assertEqual((arrival["early_minutes"], arrival["early_kind"], arrival["train_type"]), (0, None, "person"))
        self.assertEqual(table["deviation_level"], 2, "the meet's default")

    def test_a_train_left_standing_is_estimated_and_the_meet_level_follows(self):
        self.clock("09:26")
        self.runtime.set_deviation_level(self.pub.meet_id, 4)
        table = self.rows()
        self.assertEqual([(row["delay_minutes"], row["estimated"]) for row in table["rows"]], [(6, True), (6, True)])
        self.assertEqual(table["deviation_level"], 4)

    def test_on_time_rows_have_no_new_time(self):
        self.clock("09:10")
        table = self.rows()
        self.assertEqual([(row["delay_minutes"], row["expected_time"]) for row in table["rows"]], [(0, None), (0, None)])

    def test_an_early_departure_is_given_to_the_box(self):
        self.clock("09:18")
        self.move("station-a", "movement-101-a", departure="departed")
        arrival = self.rows()["rows"][1]
        self.assertEqual((arrival["delay_minutes"], arrival["early_minutes"], arrival["early_kind"]), (0, 2, "dep"))


if __name__ == "__main__":
    unittest.main()
