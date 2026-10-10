"""En station lämnas till automatiken och tas tillbaka (Casper, 2026-10-10).

"Jag går på toa": operatören vid boxen, webb-TMBoxen, iPhone eller TKL lämnar
sin station till automatiken och tar tillbaka den efteråt. Admin kan göra
samma sak från Drift även när en box eller TKL är ansluten. Valet står kvar
över dygnsskiftet. Medan automatiken sköter stationen frågar en trafiktangent
först om stationen ska tas tillbaka. Tappar en bemannad station kontakten tar
automatiken över efter så många minuter som admin valt; av som förval.

Tåg 101 går CDA (station-a) 09:20 → LEK (station-b) 09:35.
"""
import unittest
from uuid import uuid4

from test_automatic_stations import AutomaticFixture, at
from tmbox_gateway.automatic import AutomaticStations
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import DeviceKind
from tmbox_gateway.protocol_v2 import CommandRejected
from tmbox_gateway.terminal16 import NAVIGATION_ACTIONS


class StationAutomaticFixture(AutomaticFixture):
    def setUp(self):
        super().setUp()
        self.terminals = self.app.terminal16
        self.auto.started = self.time                                 # the fixture's clock, not the real one

    def frame(self, device):
        return self.terminals.frame(device)

    def press(self, device, key, **extra):
        """Tryck som boxen gör: livstecknet först, sedan tangenten."""
        self.service.observe_operator(device)
        frame = self.frame(device)
        body = {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": key, **extra}
        if "train_number" in extra:
            body["entry_context"] = frame["entry"]["context"]
        answer = self.terminals.command(device, body)
        self.assertEqual("accepted", answer["status"], answer)
        return answer["frame"]

    def lines(self, device):
        return [line.rstrip() for line in self.frame(device)["lines"]]

    def leave(self, device):
        frame = self.press(device, "*")
        self.assertEqual("AUTOMATIK?", frame["lines"][0].strip())
        return self.press(device, "#")

    def released(self, station):
        return next(item for item in self.auto.status()["stations"] if item["id"] == station)


class TheBoxLeavesAndTakesBackTests(StationAutomaticFixture):
    def test_star_asks_and_hash_leaves_the_station_to_the_automation(self):
        box = self.box()                                              # LEK
        frame = self.frame(box)
        self.assertEqual("Lämna till automatiken…", frame["keys"]["*"]["label"])
        self.assertFalse(frame["keys"]["*"]["acts"], "* only opens the question")
        frame = self.press(box, "*")
        self.assertEqual(["AUTOMATIK?", "#Ja *Nej"], [line[:11].strip() for line in frame["lines"]])
        self.assertTrue(frame["keys"]["#"]["acts"], "# on the question is the act")
        self.assertEqual("manual", self.auto.mode("station-b"), "nothing happens on the question")
        frame = self.press(box, "#")
        self.assertEqual("automatic", self.auto.mode("station-b"))
        self.assertEqual("operator", self.released("station-b")["released_by"])
        self.assertTrue(frame["lines"][0].startswith("AUTOMATIK"), frame["lines"])
        self.assertTrue(frame["lines"][0].rstrip().endswith("LEK"), frame["lines"])
        self.assertEqual("automatic", frame["station_mode"])
        self.assertEqual("Ta tillbaka stationen…", frame["keys"]["#"]["label"])
        self.assertNotIn("*", frame["keys"], "no second way to leave it")

    def test_star_then_star_changes_nothing(self):
        box = self.box()
        self.press(box, "*")
        frame = self.press(box, "*")
        self.assertEqual("manual", self.auto.mode("station-b"))
        self.assertEqual("manned", frame["station_mode"])

    def test_the_automation_works_the_station_the_box_left(self):
        box = self.box()
        self.advance(at(9, 18))
        self.assertEqual("waiting", self.service.open_cases(None)[0]["status"], "a manned station answers itself")
        frame = self.frame(box)
        self.assertEqual("reject_view", "reject_view" if frame["keys"]["*"]["label"] == "Neka begäran…" else None,
                         "the box opened the request; * there means refuse")
        self.press(box, "B")                                          # to the start screen first
        self.leave(box)
        self.advance(at(9, 18, 30))
        self.assertEqual("approved", self.service.open_cases(None)[0]["status"], "the automation answered")

    def test_hash_on_the_start_screen_takes_it_back(self):
        box = self.box()
        self.leave(box)
        frame = self.press(box, "#")
        self.assertEqual("TA TILLBAKA?", frame["lines"][0].strip())
        self.assertEqual("automatic", self.auto.mode("station-b"), "nothing happens on the question")
        frame = self.press(box, "#")
        self.assertEqual("manual", self.auto.mode("station-b"))
        self.assertEqual("manned", frame["station_mode"])
        self.assertFalse(frame["lines"][0].startswith("AUTOMATIK"), frame["lines"])
        self.advance(at(9, 18))
        self.assertEqual("waiting", self.service.open_cases(None)[0]["status"], "manned again: no automatic answer")

    def test_a_traffic_key_asks_first_and_acts_after_the_station_is_back(self):
        box = self.box("box-a", "station-a")                         # CDA sends 101
        self.leave(box)
        self.clock(at(9, 10), running=False)
        frame = self.press(box, "#", train_number="101")              # 101# begär direkt annars
        self.assertEqual("TA TILLBAKA?", frame["lines"][0].strip())
        self.assertEqual([], self.service.open_cases(None), "asked first: no request")
        frame = self.press(box, "#")                                  # ja
        self.assertEqual("manual", self.auto.mode("station-a"))
        self.assertIn("101", frame["lines"][0], "back on the train it was about")
        self.press(box, "#")                                          # begär klartecken
        self.assertEqual(1, len(self.service.open_cases(None)))

    def test_star_on_the_question_keeps_the_automation(self):
        box = self.box("box-a", "station-a")
        self.leave(box)
        self.clock(at(9, 10), running=False)
        self.press(box, "#", train_number="101")
        frame = self.press(box, "*")
        self.assertEqual("automatic", self.auto.mode("station-a"))
        self.assertIn("101", frame["lines"][0], "back where it was, nothing done")
        self.assertEqual([], self.service.open_cases(None))

    def test_the_station_service_refuses_a_box_on_a_station_left_to_the_automation(self):
        box = self.box()
        self.leave(box)
        with self.assertRaises(CommandRejected) as refused:
            self.service.execute_station_command(box, "station-b", "train.position.set", {"movement_id": "movement-101-b"})
        self.assertEqual("station_automatic", refused.exception.reason)
        # Looking something up is not acting.
        self.service.execute_station_command(box, "station-b", "train.lookup", {"train_number": "101"})

    def test_the_marks_hold_for_the_new_keys(self):
        self.assertTrue({"automatic_view", "takeback_view"} <= NAVIGATION_ACTIONS)
        self.assertFalse({"automatic_on", "take_back"} & NAVIGATION_ACTIONS)

    def test_a_press_meant_for_the_old_picture_is_refused_when_the_other_box_leaves(self):
        left, right = self.box("box-l", "station-b"), self.box("box-r", "station-b")
        stale = self.frame(right)
        self.assertEqual("browse", "browse" if stale["keys"]["#"]["label"] == "Visa kommande tåg" else None)
        self.leave(left)
        # # meant "show trains" on the old picture and "take back" on the new.
        answer = self.terminals.command(right, {"command_id": uuid4().hex, "view_token": stale["view_token"], "key": "#"})
        self.assertEqual("rejected", answer["status"], "a press for the old picture never acts on the new one")
        self.assertTrue(answer["frame"]["lines"][0].startswith("AUTOMATIK"))
        frame = self.press(right, "#")
        self.press(right, "#")
        self.assertEqual("manual", self.auto.mode("station-b"), "either box takes it back")

    def test_not_offered_while_the_automation_is_switched_off(self):
        box = self.box()
        self.auto.set_enabled(False)
        self.assertNotIn("*", self.frame(box)["keys"])

    def test_switching_the_automation_off_gives_the_station_back_to_its_box(self):
        box = self.box()
        self.leave(box)
        self.auto.set_enabled(False)
        frame = self.frame(box)
        self.assertEqual("manned", frame["station_mode"])
        self.assertFalse(frame["lines"][0].startswith("AUTOMATIK"))
        self.press(box, "D")                                          # works as usual


class StandsOverTheNewDayTests(StationAutomaticFixture):
    def test_a_station_left_to_the_automation_stays_so_over_the_new_day(self):
        box = self.box()
        self.leave(box)
        self.auto.new_day(self.pub, self.pub.active_day, at(5, 0))
        self.service.observe_operator(box)                            # the box is still there
        self.assertEqual("automatic", self.auto.mode("station-b"))
        self.assertTrue(self.frame(box)["lines"][0].startswith("AUTOMATIK"))

    def test_an_admin_hand_back_stays_too(self):
        box = self.box()
        self.auto.hand_back("station-b")
        self.auto.new_day(self.pub, self.pub.active_day, at(5, 0))
        self.service.observe_operator(box)
        self.assertEqual("automatic", self.auto.mode("station-b"))
        self.assertEqual("admin", self.released("station-b")["released_by"])


class AdminTests(StationAutomaticFixture):
    def test_the_administrator_leaves_a_station_with_a_box_and_gives_it_back(self):
        box = self.box()
        status = self.app.control_automatic_stations(self.admin, {"action": "automatic", "station_id": "station-b",
                                                                  "confirmed": True})
        self.assertEqual("automatic", next(s["mode"] for s in status["stations"] if s["id"] == "station-b"))
        self.assertTrue(self.frame(box)["lines"][0].startswith("AUTOMATIK"), "the box is told")
        status = self.app.control_automatic_stations(self.admin, {"action": "manual", "station_id": "station-b",
                                                                  "device_id": box, "confirmed": True})
        self.assertEqual("manual", next(s["mode"] for s in status["stations"] if s["id"] == "station-b"))
        self.assertEqual("manned", self.frame(box)["station_mode"])

    def test_the_lost_contact_setting(self):
        self.assertEqual(0, self.app.automatic_stations_status(self.admin)["lost_contact_minutes"])
        status = self.app.control_automatic_stations(self.admin, {"action": "lost_contact", "minutes": 5})
        self.assertEqual(5, status["lost_contact_minutes"])
        for wrong in (3, "5", None, -1):
            with self.assertRaises(HTTPAPIError):
                self.app.control_automatic_stations(self.admin, {"action": "lost_contact", "minutes": wrong})
        box = self.ids.register_client("box-x", "Box", DeviceKind.ESP32_PANEL, "secret", ("panel-b",))
        with self.assertRaises(HTTPAPIError):
            self.app.control_automatic_stations(box, {"action": "lost_contact", "minutes": 0})


class LostContactTests(StationAutomaticFixture):
    def test_off_the_station_waits_for_its_operator(self):
        self.box()
        self.time += 3600
        self.advance(at(9, 18))
        self.assertEqual("disconnected", self.auto.mode("station-b"))

    def test_after_the_chosen_minutes_the_automation_takes_over(self):
        box = self.box()
        self.auto.set_lost_contact_minutes(5)
        self.time += 5 * 60 - 1
        self.advance(at(9, 18))
        self.assertEqual("disconnected", self.auto.mode("station-b"), "not before five minutes")
        self.assertEqual("waiting", self.service.open_cases(None)[0]["status"])
        self.time += 1
        self.advance(at(9, 18, 30))
        self.assertEqual("automatic", self.auto.mode("station-b"))
        self.assertEqual("lost_contact", self.released("station-b")["released_by"])
        self.assertEqual("approved", self.service.open_cases(None)[0]["status"])
        # The operator comes back: the station stays with the automation
        # until it is taken back, and the box says so.
        self.service.observe_operator(box)
        self.assertEqual("automatic", self.auto.mode("station-b"))
        self.assertTrue(self.frame(box)["lines"][0].startswith("AUTOMATIK"))
        self.press(box, "#")
        self.press(box, "#")
        self.assertEqual("manual", self.auto.mode("station-b"))

    def test_another_box_still_at_the_station_keeps_it(self):
        first, second = self.box("box-l", "station-b"), self.box("box-r", "station-b")
        self.auto.set_lost_contact_minutes(2)
        for _ in range(6):                                            # box-l is gone, box-r pings
            self.time += 30
            self.service.observe_operator(second)
            self.advance(at(9, 10))
        self.assertEqual("manual", self.auto.mode("station-b"))

    def test_a_restart_is_not_minutes_without_contact(self):
        self.box()
        self.auto.set_lost_contact_minutes(2)
        self.time += 3600
        restarted = AutomaticStations(self.service, now=lambda: self.time)  # nobody heard yet
        self.clock(at(9, 9))
        restarted.tick()
        self.assertEqual("disconnected", restarted.mode("station-b"))
        self.time += 2 * 60
        self.clock(at(9, 10))
        restarted.tick()
        self.assertEqual("automatic", restarted.mode("station-b"))


class SignalBoxTests(StationAutomaticFixture):
    """CDA-TKL (here at LEK): POST /v1/tkl/automatic."""

    def setUp(self):
        super().setUp()
        self.tkl = self.ids.register_client("tkl-lek", "LEK TKL", DeviceKind.TKL_TERMINAL, "secret", ("panel-b",))
        self.app.tkl_context(self.tkl, "station-b")                  # polling mans it

    def test_the_tkl_leaves_and_takes_back_its_station(self):
        self.assertEqual({"available": True, "active": False, "released_by": None},
                         self.app.tkl_context(self.tkl, "station-b")["automatic"])
        answer = self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": True})
        self.assertTrue(answer["automatic"]["active"])
        self.assertEqual("operator", answer["automatic"]["released_by"])
        self.app.tkl_context(self.tkl, "station-b")                  # still polling
        self.assertEqual("automatic", self.auto.mode("station-b"))
        answer = self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": False})
        self.assertFalse(answer["automatic"]["active"])
        self.assertEqual("manual", self.auto.mode("station-b"))

    def test_a_tkl_step_on_a_station_left_to_the_automation_asks_first(self):
        self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": True})
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.update_tkl_movement(self.tkl, {"station_id": "station-b", "movement_id": "movement-101-b",
                                                    "arrival": "arrived"})
        self.assertEqual("station_automatic", refused.exception.code)
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.tkl_clearance_action(self.tkl, {"station_id": "station-b", "connection_id": "connection-a-b",
                                                     "action": "request", "train_number": "102"})
        self.assertEqual("station_automatic", refused.exception.code)
        self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": False})
        self.app.update_tkl_movement(self.tkl, {"station_id": "station-b", "movement_id": "movement-101-b",
                                                "arrival": "arrived"})

    def test_a_client_arriving_while_the_automation_has_the_station_does_not_become_its_operator(self):
        self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": True})
        box = self.box()                                              # a box is assigned and pings
        self.assertEqual("automatic", self.auto.mode("station-b"))
        self.assertIsNone(self.released("station-b")["operator"])
        self.press(box, "#")
        self.press(box, "#")
        self.assertEqual((box, "manual"), (self.released("station-b")["operator"], self.auto.mode("station-b")))

    def test_only_its_own_station(self):
        with self.assertRaises(HTTPAPIError):
            self.app.tkl_automatic(self.tkl, {"station_id": "station-a", "automatic": True})
        self.assertEqual("automatic", self.auto.mode("station-a"))
        self.assertNotIn("station-a", self.auto._state(self.pub, self.pub.active_day)["automatic"])

    def test_refused_while_the_automation_is_switched_off(self):
        self.auto.set_enabled(False)
        self.assertEqual({"available": False, "active": False}, self.app.tkl_context(self.tkl, "station-b")["automatic"])
        with self.assertRaises(HTTPAPIError) as refused:
            self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": True})
        self.assertEqual("automatic_rejected", refused.exception.code)

    def test_a_yes_or_no_is_required(self):
        with self.assertRaises(HTTPAPIError):
            self.app.tkl_automatic(self.tkl, {"station_id": "station-b", "automatic": "yes"})


if __name__ == "__main__":
    unittest.main()
