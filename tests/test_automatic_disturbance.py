"""Störningar och undanställning i automatiken (Casper 2026-10-08).

Förseningsdelarna ur simuleringen är nu en del av automatiken, med en
inställning i admin: inga störningar som förval, normal eller störd trafik,
vid station (stationsarbete före avgång), på linjen (längre gångtid) eller
båda. Samma scenarionyckel ger samma störningar.

Undanställning vid slutstation: ett tåg som slutat vid en automatisk station
rangeras bort efter några spelminuter, så att spåret blir fritt. Utan det
höll tåget spåret hela dagen och mottagaren sa "Mottagningsspåret är upptaget".
"""
from __future__ import annotations

import unittest

from runtime_fixture import runtime_package_v3
from test_automatic_stations import AutomaticFixture, at
from test_timetable_placement import add_service
from tmbox_gateway.automatic import DEFAULT_DISTURBANCE
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import DeviceKind, PairedClient


def seed_with(auto, kind, key, wanted):
    """En scenarionyckel som ger tåget en störning på `wanted` (True) eller ingen."""
    for number in range(500):
        auto._settings = {**auto.disturbance(), "profile": "disrupted", "where": "both"}  # noqa: SLF001
        auto._seed = f"s{number}"  # noqa: SLF001
        if (auto._delay(kind, key) > 0) == wanted:  # noqa: SLF001
            return f"s{number}", auto._delay(kind, key)  # noqa: SLF001
    raise AssertionError("no seed")


class DisturbanceTests(AutomaticFixture):
    def set(self, **values):
        return self.app.control_automatic_stations(self.admin, {"action": "disturbance", **values})["disturbance"]

    def test_the_default_is_no_disturbance_and_stabling_after_five_minutes(self):
        status = self.app.automatic_stations_status(self.admin)
        self.assertEqual(status["disturbance"], DEFAULT_DISTURBANCE)
        self.assertEqual((DEFAULT_DISTURBANCE["profile"], DEFAULT_DISTURBANCE["stabling"], DEFAULT_DISTURBANCE["stabling_minutes"]),
                         ("off", True, 5))

    def test_without_disturbance_the_train_leaves_on_time(self):
        self.advance(at(9, 18))
        self.advance(at(9, 20))
        self.assertEqual("departed", self.movement("station-a", "movement-101-a")["departure"])

    def test_station_work_holds_the_departure_and_the_same_key_gives_the_same_minutes(self):
        seed, delay = seed_with(self.auto, "station", "movement-101-a", True)
        settings = self.set(profile="disrupted", where="station", seed=seed)
        self.assertEqual((settings["profile"], settings["where"], settings["seed"]), ("disrupted", "station", seed))
        self.advance(at(9, 18))
        self.advance(at(9, 20) + delay - 30)
        self.assertNotEqual("departed", self.movement("station-a", "movement-101-a").get("departure"), "station work goes on")
        self.assertEqual(self.auto.blocked.get("movement-101-a"), "Stationsarbete pågår")
        self.advance(at(9, 20) + delay + 1)
        self.assertEqual("departed", self.movement("station-a", "movement-101-a")["departure"])
        self.assertLessEqual(delay, 12 * 60)

    def test_a_disturbance_on_the_line_makes_the_arrival_later(self):
        seed, delay = seed_with(self.auto, "line", "movement-101-a", True)
        self.set(profile="disrupted", where="line", seed=seed)
        self.advance(at(9, 18))
        self.advance(at(9, 20))
        self.assertEqual("departed", self.movement("station-a", "movement-101-a")["departure"], "no station work: on time")
        self.advance(at(9, 35, 1))
        self.assertNotEqual("arrived", self.movement("station-b", "movement-101-b").get("arrival"), "still on the line")
        self.advance(at(9, 35, 1) + delay)
        self.assertEqual("arrived", self.movement("station-b", "movement-101-b")["arrival"])

    def test_where_decides_which_disturbance_applies(self):
        """A key that would delay 101 on the line does nothing when only station work is chosen."""
        for number in range(500):
            self.auto._settings = {**self.auto.disturbance(), "profile": "disrupted", "where": "both"}  # noqa: SLF001
            self.auto._seed = f"w{number}"  # noqa: SLF001
            if self.auto._delay("line", "movement-101-a") > 0 and self.auto._delay("station", "movement-101-a") == 0:  # noqa: SLF001
                break
        self.set(profile="disrupted", where="station", seed=f"w{number}")
        self.run_on_time()

    def run_on_time(self):
        self.advance(at(9, 18))
        self.advance(at(9, 20))
        self.assertEqual("departed", self.movement("station-a", "movement-101-a")["departure"])
        self.advance(at(9, 35, 1))
        self.assertEqual("arrived", self.movement("station-b", "movement-101-b")["arrival"], "no delay on the line")

    def test_only_an_administrator_and_only_known_values(self):
        for wrong in ({"profile": "kaos"}, {"where": "överallt"}, {"stabling_minutes": 0}, {"stabling": "ja"}, {"seed": "x" * 41}):
            with self.assertRaises(HTTPAPIError, msg=wrong):
                self.set(**wrong)
        box = PairedClient(client_id="esp32", display_name="Box", kind=DeviceKind.ESP32_PANEL, panel_ids=("panel-b",),
                           station_id="station-b", admin_role="")
        with self.assertRaises(HTTPAPIError) as raised:
            self.app.control_automatic_stations(box, {"action": "disturbance", "profile": "normal"})
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.auto.disturbance()["profile"], "off")


class StablingTests(AutomaticFixture):
    """101 slutar i LEK på spår 1 09:35; 103 ska in på samma spår 10:15."""

    def package(self):
        package = runtime_package_v3()
        add_service(package, "103", [("station-a", None, "10:00", "track-station-a-1"),
                                     ("station-b", "10:15", None, "track-station-b-1")])
        return package

    def run_101_in(self):
        for moment in (at(9, 18), at(9, 20), at(9, 35, 1)):
            self.advance(moment)
        self.assertEqual("arrived", self.movement("station-b", "movement-101-b")["arrival"])

    def test_a_terminating_train_is_stabled_and_the_next_one_gets_the_track(self):
        self.run_101_in()
        self.advance(at(9, 39))
        self.assertNotIn("movement-101-b", self.auto.stabled_movements(self.pub, self.pub.active_day), "not before five minutes")
        self.advance(at(9, 41))
        self.assertIn("movement-101-b", self.auto.stabled_movements(self.pub, self.pub.active_day))
        self.advance(at(9, 58))
        self.assertEqual("approved", self.service.open_cases(None)[0]["status"], "the track is free for 103")

    def test_without_stabling_the_terminating_train_holds_the_track(self):
        self.app.control_automatic_stations(self.admin, {"action": "disturbance", "stabling": False})
        self.run_101_in()
        self.advance(at(9, 58))
        self.assertEqual("waiting", self.service.open_cases(None)[0]["status"])
        self.assertEqual("Mottagningsspåret är upptaget", self.auto.blocked["movement-103-0"])

    def test_a_manned_terminal_station_is_not_stabled(self):
        self.run_101_in()
        self.box()  # LEK bemannas
        self.advance(at(9, 45))
        self.assertNotIn("movement-101-b", self.auto.stabled_movements(self.pub, self.pub.active_day))


if __name__ == "__main__":
    unittest.main()
