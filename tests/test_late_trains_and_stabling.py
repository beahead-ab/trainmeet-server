"""Sena tåg, tåg som slutar och nedräkningen till automatik (Casper, 2026-10-10).

"Om ett tåg är sent stannar det några min och sen klareras vidare": ett
försenat tåg står två spelminuter vid en automatisk station, eller
tidtabellens uppehåll om det är kortare, och går sedan. Ett tåg i tid följer
tidtabellen.

"Om ett tåg avslutas på en station ska instruktionen följa om vad som händer
med tåget. Ska det ställas åt sidan?": vid en automatisk station ställs det
undan efter fem minuter som förut, och syns nu som Undanställt. Vid en
bemannad station visar boxen och TKL att tåget slutar här, och operatören
anmäler att det är undanställt. Då blir spåret fritt.

"hur lång tid till station blir automatisk, inkludera att den aldrig går
till automatik automatiskt": Drift räknar ned till automatiken för en station
utan kontakt, och Aldrig betyder att stationen väntar på sin operatör.
"""
import unittest

from runtime_fixture import runtime_package_v3
from test_automatic_stations import at
from test_station_automatic import StationAutomaticFixture
from test_timetable_placement import add_service
from test_train_detail import through_package
from tmbox_gateway.automatic import ACTOR
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import DeviceKind


def minute(seconds):
    return f"{int(seconds) // 3600:02d}:{int(seconds) % 3600 // 60:02d}"


class LateTrainTests(StationAutomaticFixture):
    """505 LEK (station-b) 09:30 → CDA (station-a) 09:45/09:50 → MUN (station-c) 10:05.
    CDA och MUN är automatiska. LEK är bemannad när tåget ska bli sent."""

    def package(self):
        return through_package()

    def ids_505(self):
        rows = {row["station_id"]: row["id"] for row in self.pub.payload["trains"] if row["train_number"] == "505"}
        connection = next(item["id"] for item in self.pub.payload["connections"]
                          if {item["station_a_id"], item["station_b_id"]} == {"station-a", "station-b"})
        return rows, connection

    def send_late_from_lek(self, departs):
        """LEK:s operatör skickar 505 först vid `departs` (planerat 09:30)."""
        box = self.box("box-lek", "station-b")
        rows, connection = self.ids_505()
        self.clock(departs - 60)
        self.service.execute_station_command(box, "station-b", "clearance.request",
                                             {"movement_id": rows["station-b"], "connection_id": connection})
        self.auto.tick()
        self.clock(departs)
        self.auto.tick()
        self.service.execute_station_command(box, "station-b", "train.departed", {"movement_id": rows["station-b"]})
        return box, rows

    def follow_cda(self, start, end, box=None):
        """Ticka var trettionde sekund; ge tillbaka när 505 kom in i och gick från CDA."""
        rows, _ = self.ids_505()
        seen = {}
        moment = start
        while moment <= end:
            self.clock(moment)
            if box:
                self.service.observe_operator(box)
            self.auto.tick()
            live = self.movement("station-a", rows["station-a"])
            for field, value in (("arrival", "arrived"), ("departure", "departed")):
                if live.get(field) == value:
                    seen.setdefault(field, moment)
            moment += 30
        return seen

    def test_a_late_train_stands_two_minutes_and_then_goes_on(self):
        """Regressionen: ankomsten i samma varv räknades med planerad tid, så
        505 kom in 09:55 och gick samma halvminut."""
        box, _ = self.send_late_from_lek(at(9, 40))
        seen = self.follow_cda(at(9, 41), at(10, 5), box)
        self.assertIn("arrival", seen)
        self.assertGreaterEqual(seen["arrival"], at(9, 55), "15 minutes on the line")
        self.assertIn("departure", seen, "it goes on")
        self.assertGreaterEqual(seen["departure"] - seen["arrival"], 2 * 60, "two game minutes at CDA first")
        self.assertLessEqual(seen["departure"] - seen["arrival"], 4 * 60, "then on as soon as it may")

    def test_the_stop_is_the_setting_when_it_is_shorter_than_the_timetable(self):
        self.app.control_automatic_stations(self.admin, {"action": "disturbance", "late_stop_minutes": 4})
        box, _ = self.send_late_from_lek(at(9, 40))
        seen = self.follow_cda(at(9, 41), at(10, 10), box)
        self.assertGreaterEqual(seen["departure"] - seen["arrival"], 4 * 60)

    def test_the_timetable_stop_is_kept_when_it_is_shorter(self):
        """Tidtabellens uppehåll i CDA är fem minuter; inställningen tio gör det inte längre."""
        self.app.control_automatic_stations(self.admin, {"action": "disturbance", "late_stop_minutes": 10})
        box, _ = self.send_late_from_lek(at(9, 40))
        seen = self.follow_cda(at(9, 41), at(10, 15), box)
        self.assertGreaterEqual(seen["departure"] - seen["arrival"], 5 * 60)
        self.assertLessEqual(seen["departure"] - seen["arrival"], 7 * 60)

    def test_a_train_on_time_keeps_to_the_timetable(self):
        self.auto.started = self.time
        seen = self.follow_cda(at(9, 26), at(9, 55))
        self.assertEqual("09:45", minute(seen["arrival"]), "in at 09:45")
        self.assertGreaterEqual(seen["departure"], at(9, 50), "not before the timetable")
        self.assertLessEqual(seen["departure"], at(9, 51))

    def test_the_late_stop_is_one_to_ten_game_minutes_and_only_for_the_administrator(self):
        self.assertEqual(2, self.app.automatic_stations_status(self.admin)["disturbance"]["late_stop_minutes"])
        for wrong in (0, 11, "2", True, None):
            with self.assertRaises(HTTPAPIError, msg=wrong):
                self.app.control_automatic_stations(self.admin, {"action": "disturbance", "late_stop_minutes": wrong})
        self.assertEqual(10, self.app.control_automatic_stations(
            self.admin, {"action": "disturbance", "late_stop_minutes": 10})["disturbance"]["late_stop_minutes"])
        box = self.ids.register_client("tkl-lek", "LEK TKL", DeviceKind.TKL_TERMINAL, "secret", ("panel-b",))
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.control_automatic_stations(box, {"action": "disturbance", "late_stop_minutes": 3})
        self.assertEqual(403, raised.exception.status)


class EndsHereFixture(StationAutomaticFixture):
    """101 slutar i LEK (station-b) på spår 1 09:35; 103 ska in på samma spår 10:15."""

    def package(self):
        package = runtime_package_v3()
        add_service(package, "103", [("station-a", None, "10:00", "track-station-a-1"),
                                     ("station-b", "10:15", None, "track-station-b-1")])
        return package

    def run_101_in(self):
        for moment in (at(9, 18), at(9, 20), at(9, 35, 1)):
            self.advance(moment)
        self.assertEqual("arrived", self.movement("station-b", "movement-101-b")["arrival"])

    def stabled(self):
        return self.auto.stabled_by(self.pub, self.pub.active_day)

    def occupied(self):
        """Står något på LEK spår 1 för 103, så som en bemannad station räknar?"""
        return self.service.track_conflict(self.pub, self.pub.active_day, "station-b", "movement-103-1", "track-station-b-1")


class TheBoxPutsAwayTests(EndsHereFixture):
    def test_the_box_says_the_train_ends_here_and_hash_puts_it_away(self):
        self.run_101_in()
        box = self.box()
        self.advance(at(9, 41))
        self.assertEqual({}, self.stabled(), "a manned terminal is not stabled by the automation")
        self.assertIsNotNone(self.occupied(), "101 holds the track")
        frame = self.frame(box)
        self.assertEqual("Aktiva tåg (1)", frame["keys"]["B"]["label"], "the train to put away is active")
        frame = self.press(box, "B")
        self.assertEqual(["101 SLUTAR HÄR", "#Undan"], [frame["lines"][0].strip(), frame["lines"][1][:11].strip()])
        self.assertEqual("Ställ undan", frame["keys"]["#"]["label"])
        self.assertTrue(frame["keys"]["#"]["acts"], "putting away is a traffic act")
        frame = self.press(box, "#")
        self.assertEqual("UNDANSTÄLLT", frame["lines"][0].strip()[:11])
        self.assertEqual({"movement-101-b": box}, self.stabled())
        self.assertIsNone(self.occupied(), "the track is free for 103")
        self.assertEqual("101", frame["lines"][1][:11].strip())
        frame = self.press(box, "#")                                  # OK
        self.assertEqual("Aktiva tåg (0)", frame["keys"]["B"]["label"], "nothing more to do with 101")
        audit = self.ops._connection.execute(  # noqa: SLF001
            "SELECT actor, station_id, movement_id FROM audit_events WHERE action='train.stabled'").fetchall()
        self.assertEqual([(box, "station-b", "movement-101-b")], [tuple(row) for row in audit])

    def test_the_automation_puts_away_at_its_own_station_and_says_so(self):
        self.run_101_in()
        self.advance(at(9, 41))
        self.assertEqual({"movement-101-b": ACTOR}, self.stabled())
        self.assertIsNone(self.occupied())

    def test_a_train_that_goes_on_is_not_offered(self):
        box = self.box("box-a", "station-a")
        self.advance(at(9, 18))
        self.assertNotIn("SLUTAR", " ".join(self.press(box, "B")["lines"]))

    def test_while_the_automation_has_the_station_the_box_is_not_offered_it(self):
        self.run_101_in()
        box = self.box()
        self.leave(box)
        self.assertNotIn("SLUTAR", " ".join(self.frame(box)["lines"]))


class TKLPutsAwayTests(EndsHereFixture):
    def setUp(self):
        super().setUp()
        self.tkl = self.ids.register_client("tkl-lek", "LEK TKL", DeviceKind.TKL_TERMINAL, "secret", ("panel-b",))

    def stable(self, movement="movement-101-b", station="station-b"):
        return self.app.tkl_stable(self.tkl, {"station_id": station, "movement_id": movement})

    def test_the_tkl_puts_the_train_away_and_its_context_says_so(self):
        self.run_101_in()
        self.service.observe_operator(self.tkl.client_id, "station-b")
        self.advance(at(9, 37))
        self.assertEqual({}, self.app.tkl_context(self.tkl, "station-b")["stabled"])
        answer = self.stable()
        self.assertEqual({"movement-101-b": "tkl-lek"}, answer["stabled"])
        self.assertEqual({"movement-101-b": "tkl-lek"}, self.app.tkl_context(self.tkl, "station-b")["stabled"])
        self.assertIsNone(self.occupied())

    def test_only_a_train_that_ended_here_and_has_arrived(self):
        self.service.observe_operator(self.tkl.client_id, "station-b")
        self.advance(at(9, 30))
        with self.assertRaises(HTTPAPIError) as raised:
            self.stable()
        self.assertEqual((409, "Tåget har inte kommit in."), (raised.exception.status, str(raised.exception)))
        with self.assertRaises(HTTPAPIError) as raised:
            self.stable("movement-101-a")
        self.assertEqual((409, "Tåget slutar inte här."), (raised.exception.status, str(raised.exception)))
        self.assertEqual({}, self.stabled())

    def test_another_station_is_refused(self):
        self.run_101_in()
        with self.assertRaises(HTTPAPIError) as raised:
            self.stable(station="station-a")
        self.assertEqual(403, raised.exception.status)

    def test_on_a_station_the_automation_works_the_tkl_asks_to_take_it_back_first(self):
        self.run_101_in()
        self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": True})
        with self.assertRaises(HTTPAPIError) as raised:
            self.stable()
        self.assertEqual((409, "station_automatic"), (raised.exception.status, raised.exception.code))


class StabledIsVisibleTests(EndsHereFixture):
    def test_display_and_the_train_panel_show_the_train_put_away(self):
        self.run_101_in()
        self.assertEqual([], self.app.display_snapshot()["stabled"])
        self.assertEqual("arrived", self.app.train_detail(self.admin, "101")["services"][0]["now"]["state"])
        self.advance(at(9, 41))
        self.assertEqual(["movement-101-b"], self.app.display_snapshot()["stabled"])
        now = self.app.train_detail(self.admin, "101")["services"][0]["now"]
        self.assertEqual(("stabled", "station-b"), (now["state"], now["station_id"]))

    def at_lek(self):
        return [position["train_number"] for position in self.app.display_snapshot()["train_positions"]
                if position["status"] == "station" and position["station_id"] == "station-b"]

    def test_a_train_put_away_is_off_the_map(self):
        """Casper, 2026-10-10: "ta bort undanställda tåg från kartan". The
        map, the trains-in count and TKL all read /v1/display."""
        self.run_101_in()
        self.assertEqual(["101"], self.at_lek(), "in: on the map at LEK")
        self.advance(at(9, 41))
        self.assertEqual([], self.at_lek(), "put away: off the map")
        self.assertEqual("station-b", self.ops.positions()[0]["station_id"], "the stored position is untouched")

    def test_an_older_position_without_its_movement_is_matched_on_train_and_station(self):
        self.run_101_in()
        self.advance(at(9, 41))
        self.ops._connection.execute("UPDATE train_positions SET movement_id=NULL WHERE train_number='101'")  # noqa: SLF001
        self.assertEqual([], self.at_lek())

    def test_a_train_on_the_line_is_never_hidden(self):
        positions = [{"train_number": "101", "status": "connection", "station_id": None, "movement_id": "movement-101-b"},
                     {"train_number": "103", "status": "station", "station_id": "station-b", "movement_id": "movement-103-1"}]
        kept = self.app._without_stabled(self.pub, positions, ["movement-101-b"])  # noqa: SLF001
        self.assertEqual(positions, kept)


class TakesOverInTests(StationAutomaticFixture):
    def lek(self):
        return next(item for item in self.auto.status()["stations"] if item["id"] == "station-b")

    def test_drift_is_told_how_long_until_the_automation_takes_over(self):
        self.auto.set_lost_contact_minutes(5)
        box = self.box()
        self.clock(at(9, 0))
        self.assertIsNone(self.lek()["takes_over_in"], "in contact: nothing to count down")
        self.time += 100                                          # silent for 100 s
        self.assertEqual("disconnected", self.lek()["mode"])
        self.assertEqual(200, self.lek()["takes_over_in"])
        self.time += 150
        self.assertEqual(50, self.lek()["takes_over_in"])
        self.service.observe_operator(box)
        self.assertIsNone(self.lek()["takes_over_in"], "heard again")

    def test_never_means_the_station_waits(self):
        self.box()
        self.clock(at(9, 0))
        self.time += 600
        self.assertEqual(("disconnected", None), (self.lek()["mode"], self.lek()["takes_over_in"]))


if __name__ == "__main__":
    unittest.main()
