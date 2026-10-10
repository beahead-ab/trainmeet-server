from copy import deepcopy
import json
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

from tmbox_gateway.models import ConnectionConfig, ConnectionState as State
from tmbox_gateway.terminal16 import Terminal16Lab
from tmbox_gateway.terminal16_demo import demo_lab, LabServer


class Terminal16Tests(unittest.TestCase):
    def setUp(self):
        self.lab = demo_lab()
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})

    def send(self, device, key, **extra):
        return self.lab.command(device, {"command_id": str(uuid4()), "view_token": self.lab.frame(device)["view_token"], "key": key, **extra})

    def lookup(self, number, device="DEMO-KNB"):
        result = self.send(device, "#", train_number=number, entry_context=self.lab.frame(device)["entry"]["context"])
        self.assertEqual(result["status"], "accepted", result)
        return result

    def accept(self, device, key):
        result = self.send(device, key)
        self.assertEqual(result["status"], "accepted", result)
        for frame in self.lab.frames():
            self.assertEqual([len(r) for r in frame["lines"]], [16, 16])
            self.assertTrue(frame["lines"][1].endswith("12:34"))
        return result

    def departure(self):
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "#")
        self.accept("DEMO-KNB", "#")

    def test_initial_geometry_and_no_destination_buttons(self):
        for frame in self.lab.frames():
            self.assertEqual((frame["cols"], frame["rows"]), (16, 2))
            self.assertTrue(frame["lines"][1].endswith("12:34"))
            self.assertEqual(frame["keys"]["A"]["label"], "Förfrågningskö (0 väntar)")
            self.assertEqual(frame["keys"]["#"]["label"], "Visa kommande tåg")
            self.assertEqual(frame["entry"]["commit"], "#")
            self.assertEqual(frame["lines"][0], " " * 16)

    def test_overview_shows_only_active_traffic_then_becomes_blank(self):
        self.lab.now = lambda: 100
        for device in ("DEMO-KNB", "DEMO-DY"):
            self.lab.terminals["WATCH-" + device] = deepcopy(self.lab.terminals[device])
        def check(marker):
            if self.lab.terminals["WATCH-DEMO-DY"].screen == "requests":
                self.accept("WATCH-DEMO-DY", "B")
            self.assertEqual(self.lab.frame("WATCH-DEMO-KNB")["lines"][0], f"39{marker}DY".rjust(16))
            self.assertEqual(self.lab.frame("WATCH-DEMO-DY")["lines"][0], f"KNB{marker}39".ljust(16))
            self.assertEqual(self.lab.frame("DEMO-SVM")["lines"][0], " " * 16)
        self.lookup("39"); check("?")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "#"); check(">")
        self.accept("DEMO-KNB", "#"); check("▶")
        self.accept("DEMO-DY", "#")
        self.assertIn("39 MOTTAGET", self.lab.frame("WATCH-DEMO-KNB")["lines"][0])
        self.lab.now = lambda: 106
        for device in ("WATCH-DEMO-KNB", "WATCH-DEMO-DY", "DEMO-SVM"):
            self.assertEqual(self.lab.frame(device)["lines"], [" " * 16, "Nr# A:Kö   12:34"])

    def test_overview_becomes_blank_after_cancel_or_rejection(self):
        for actor in ("DEMO-KNB", "DEMO-DY"):
            with self.subTest(actor=actor):
                self.setUp()
                self.lab.terminals["WATCH"] = deepcopy(self.lab.terminals["DEMO-KNB"])
                self.lookup("39")
                if actor == "DEMO-DY":
                    self.lookup("39", actor)
                self.accept(actor, "*"); self.accept(actor, "#")
                self.assertEqual(self.lab.frame("WATCH")["lines"][0], " " * 16)

    def test_extra_inactive_connections_do_not_take_display_space(self):
        # Two connections to the left and one to the right must still be blank at rest.
        self.lab.engine.config.connections["unused-west"] = ConnectionConfig("unused-west", "svm", "knb")
        self.assertEqual(self.lab.frame("DEMO-KNB")["lines"][0], " " * 16)
        self.lookup("39"); self.accept("DEMO-KNB", "B")
        self.assertEqual(self.lab.frame("DEMO-KNB")["lines"][0], "39?DY".rjust(16))

    def test_station_timetable_has_only_its_trains_in_time_order(self):
        expected = {
            "DEMO-SVM": [("93", "Avg 12:32", "Till Knastebo"), ("17", "Ank 12:42", "Från Knastebo")],
            "DEMO-KNB": [("17", "Avg 12:35", "Till Sölvmora"), ("39", "Avg 12:38", "Till Dimmeby"),
                         ("93", "Ank 12:40", "Från Sölvmora"), ("94", "Ank 12:52", "Från Dimmeby")],
            "DEMO-DY": [("94", "Avg 12:44", "Till Knastebo"), ("39", "Ank 12:46", "Från Knastebo")],
        }
        for device, rows in expected.items():
            table = self.lab.timetable(device)
            self.assertEqual([(row["train_number"], row["time"], row["route"]) for row in table["rows"]], rows)
            self.assertEqual(table["columns"], ["Tåg", "Tid", "Från / till"])
        self.assertEqual(self.lab.engine.audit, [])

    def test_reference_timetable_remains_after_departure_arrival_and_filter_changes(self):
        before = {device: self.lab.timetable(device) for device in self.lab.terminals}
        self.departure(); self.accept("DEMO-DY", "#")
        self.accept("DEMO-SVM", "D"); self.accept("DEMO-SVM", "B")
        self.assertEqual({device: self.lab.timetable(device) for device in self.lab.terminals}, before)

    def test_reference_timetable_uses_publication_times(self):
        next(m for m in self.lab.publication["trains"] if m["id"] == "17-knb").update(departure_time="00:05", service_day_offset=1)
        rows = self.lab.timetable("DEMO-KNB")["rows"]
        self.assertEqual([row["train_number"] for row in rows], ["39", "93", "94", "17"])
        self.assertEqual(rows[-1]["time"], "Avg 00:05")

    def test_lookup_finds_destination_and_asks_at_once(self):
        """39# is enough (Casper, 2026-10-02): no second # to send the request."""
        frame = self.lookup("39")["frame"]
        self.assertTrue(frame["lines"][0].endswith("39?DY"), frame["lines"])
        self.assertEqual("Återta begäran…", frame["keys"]["*"]["label"])
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(["request"], [entry["action"] for entry in self.lab.engine.audit])
        # The receiver's lookup only shows the request: answering is its own key.
        frame = self.lookup("39", "DEMO-DY")["frame"]
        self.assertEqual("Ge klart", frame["keys"]["#"]["label"])
        self.assertEqual(1, len(self.lab.engine.audit))

    def test_no_per_digit_server_commands(self):
        for key in "1234567890":
            self.assertEqual(self.send("DEMO-KNB", key)["status"], "rejected")
        self.assertEqual(self.lab.engine.revision, 0)

    def test_unknown_train_and_clock(self):
        frame = self.lookup("999")["frame"]
        self.assertIn("INGET TÅG", frame["lines"][0])
        self.assertTrue(frame["lines"][1].endswith("12:34"))
        self.assertEqual(self.lab.engine.revision, 0)

    def test_input_rejects_six_digits_letters_and_foreign_context(self):
        for number, context in (("123456", ""), ("9A", ""), ("39", "another-box")):
            result = self.send("DEMO-KNB", "#", train_number=number, entry_context=context)
            self.assertEqual(result["status"], "rejected")

    def test_d_browses_without_requesting(self):
        self.accept("DEMO-KNB", "D")
        self.assertIn("17", self.lab.frame("DEMO-KNB")["lines"][0])
        self.accept("DEMO-KNB", "D")
        self.assertIn("39", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertEqual(self.lab.engine.revision, 0)

    def test_browse_mixes_arrivals_and_departures_in_station_time_order(self):
        self.lookup("93", "DEMO-SVM")
        self.accept("DEMO-KNB", "B")
        before = deepcopy(self.lab.engine.audit)
        # From the request just shown (93): on to 94, then round to the first.
        expected = [("94-dy", "arrival", "12:52"), ("17-knb", "departure", "12:35"),
                    ("39-knb", "departure", "12:38"), ("93-svm", "arrival", "12:40")]
        for movement, kind, time in expected:
            frame = self.accept("DEMO-KNB", "D")["frame"]
            self.assertEqual((frame["upcoming"]["movement_id"], frame["upcoming"]["kind"], frame["upcoming"]["time"]),
                             (movement, kind, time))
            self.assertIn("ANK" if kind == "arrival" else "AVG", frame["lines"][0])
            self.assertTrue(frame["lines"][0].endswith(time))
        self.assertEqual(self.lab.engine.audit, before)

    def test_browse_previous_next_wrap_both_directions(self):
        for key, selected in (("C", "94-dy"), ("D", "17-knb"), ("C", "94-dy"), ("C", "93-svm")):
            self.assertEqual(self.accept("DEMO-KNB", key)["frame"]["upcoming"]["movement_id"], selected)
        self.assertEqual(self.lab.engine.audit, [])

    def test_hash_opens_then_selects_without_requesting(self):
        frame = self.accept("DEMO-KNB", "#")["frame"]
        self.assertEqual(frame["keys"]["#"]["label"], "Välj tåg")
        frame = self.accept("DEMO-KNB", "#")["frame"]
        self.assertIsNone(frame["upcoming"])
        self.assertEqual(frame["keys"]["#"]["label"], "Begär klartecken")
        self.assertEqual(self.lab.engine.audit, [])
        self.accept("DEMO-KNB", "#")
        self.assertEqual([event["action"] for event in self.lab.engine.audit], ["request"])

    def test_a_train_never_sent_can_be_placed_from_its_number(self):
        """Casper, 2026-10-02: the receiver places a train nobody sent, with
        the timetable's track, so the game goes on. Until 2.1.0: EJ BEGÄRT ÄN.
        Since issue #115 the box asks first whether to move it here."""
        frame = self.lookup("93")["frame"]                 # SVM never sent 93
        self.assertEqual(["FLYTTA 93 HIT?  ", "#Ja B:Sp   12:34"], frame["lines"])
        self.assertEqual("Flytta hit", frame["keys"]["#"]["label"])
        self.assertEqual("FLYTTA", frame["keys"]["#"]["short"])
        self.assertEqual(self.lab.engine.audit, [])        # a lookup sends nothing
        self.accept("DEMO-KNB", "#")
        self.assertEqual("knb-1", self.lab.arrivals["93-svm"]["track"])
        self.assertIn("93 ANK SP1", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertNotIn("93-svm", self.lab._candidates(self.lab.terminals["DEMO-SVM"]))

    def test_arrival_departure_filters_are_read_only_and_station_scoped(self):
        self.lookup("93", "DEMO-SVM")
        self.accept("DEMO-KNB", "B")
        before = deepcopy(self.lab.engine.audit)
        self.accept("DEMO-KNB", "D")
        for kind, count, movement in (("arrival", 2, "93-svm"), ("departure", 2, "17-knb"), ("all", 4, "17-knb")):
            frame = self.accept("DEMO-KNB", "B")["frame"]
            self.assertEqual((frame["upcoming"]["filter"], frame["upcoming"]["count"], frame["upcoming"]["movement_id"]),
                             (kind, count, movement))
        self.assertEqual(self.lab.engine.audit, before)

    def test_empty_departure_filter_has_no_confirmation_and_can_be_left(self):
        self.lab.completed.add("94-dy")
        self.accept("DEMO-DY", "D")
        self.accept("DEMO-DY", "B")
        frame = self.accept("DEMO-DY", "B")["frame"]
        self.assertIn("INGA AVGÅNGAR", frame["lines"][0])
        self.assertNotIn("#", frame["keys"])
        self.accept("DEMO-DY", "B")
        frame = self.lab.frame("DEMO-DY")
        self.assertIn("39 ANK", frame["lines"][0])          # the arrival to come is left
        self.lookup("39")
        self.accept("DEMO-DY", "D")
        self.assertEqual(self.lab.frame("DEMO-DY")["upcoming"]["count"], 1)

    def test_default_browse_has_the_departures_and_the_arrivals_to_come(self):
        """Since 2.1.0 the timetable also has the arrivals nobody has sent yet,
        so a train can be found and placed by its time."""
        kinds = [self.accept("DEMO-KNB", "D")["frame"]["upcoming"]["kind"] for _ in range(4)]
        self.assertEqual(["departure", "departure", "arrival", "arrival"], kinds)
        self.accept("DEMO-KNB", "B")
        self.assertEqual(2, self.lab.frame("DEMO-KNB")["upcoming"]["count"])
        self.assertEqual(self.lab.engine.audit, [])

    def test_only_exact_requested_arrival_is_exposed_on_a_shared_connection(self):
        package = deepcopy(self.lab.publication)
        service = deepcopy(next(s for s in package["services"] if s["id"] == "39"))
        service.update(id="41", train_number="41")
        package["services"].append(service)
        for source in list(package["trains"]):
            if source["service_id"] == "39":
                movement = deepcopy(source)
                movement.update(id=source["id"].replace("39", "41"), service_id="41", train_number="41")
                package["trains"].append(movement)
        self.lab = Terminal16Lab(self.lab.engine, package, {"DEMO-KNB": "knb", "DEMO-DY": "dy"})
        self.lookup("39")
        self.assertEqual(self.lab._candidates(self.lab.terminals["DEMO-DY"]), ["94-dy", "39-knb", "41-knb"])
        # 39 is the request to answer; 41, never sent, can only be moved here.
        self.assertEqual("Ge klart", self.lookup("39", "DEMO-DY")["frame"]["keys"]["#"]["label"])
        frame = self.lookup("41", "DEMO-DY")["frame"]
        self.assertEqual(self.lab.terminals["DEMO-DY"].selected, "41-knb")
        self.assertEqual("Flytta hit", frame["keys"]["#"]["label"])

    def test_withdrawn_incoming_disappears_and_stale_selection_cannot_confirm(self):
        self.lookup("39")
        self.accept("DEMO-DY", "D")
        stale = self.lab.frame("DEMO-DY")["view_token"]
        self.accept("DEMO-KNB", "*"); self.accept("DEMO-KNB", "#")
        # Withdrawn, 39 is an arrival in the timetable again: no request to answer.
        self.assertEqual(self.lab._candidates(self.lab.terminals["DEMO-DY"]), ["94-dy", "39-knb"])
        # DY is told, and # only closes the notice: it can no longer answer anything.
        frame = self.lab.frame("DEMO-DY")
        self.assertEqual(["39 ÅTERTAGET    ", "KNB        12:34"], frame["lines"])
        self.assertFalse(frame["keys"]["#"]["acts"])
        result = self.lab.command("DEMO-DY", {"key": "#", "command_id": "withdrawn-selection", "view_token": stale})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual([item["action"] for item in self.lab.engine.audit], ["request", "cancel"])

    def test_direct_traffic_arrival_appears_only_after_sender_reserves(self):
        self.lab = demo_lab("direct")
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        receiver = self.lab.terminals["DEMO-DY"]
        self.assertEqual(self.lab._candidates(receiver), ["94-dy", "39-knb"])
        self.lookup("39")                                  # reserved at once
        # Typed at the receiver, a reserved train never reported departed is
        # offered as a question (issue #115); only a typed number gets there.
        frame = self.lookup("39", "DEMO-DY")["frame"]
        self.assertEqual(["FLYTTA 39 HIT?  ", "#Ja B:Sp   12:34"], frame["lines"])
        self.accept("DEMO-KNB", "#")
        # Seen to leave, it is the ordinary arrival on the same screen.
        self.assertEqual(self.lab.frame("DEMO-DY")["keys"]["#"]["label"], "Rapportera ankomst")

    def test_departed_train_leaves_sender_list_but_remains_for_receiver(self):
        self.departure()
        knb = self.lab.terminals["DEMO-KNB"]
        dy = self.lab.terminals["DEMO-DY"]
        self.assertNotIn("39-knb", self.lab._candidates(knb))
        self.assertIn("39-knb", self.lab._candidates(dy))
        self.accept("DEMO-DY", "#")
        self.assertNotIn("39-knb", self.lab._candidates(dy))

    def test_overdue_unreported_train_does_not_disappear(self):
        frame = self.accept("DEMO-SVM", "D")["frame"]
        self.assertEqual(frame["upcoming"]["movement_id"], "93-svm")
        self.assertEqual(frame["upcoming"]["time"], "12:32")

    def test_browse_respects_service_day_offset_across_midnight(self):
        for movement in self.lab.publication["trains"]:
            if movement["id"] == "17-knb":
                movement.update(departure_time="00:05", service_day_offset=1)
        self.assertEqual(self.lab._candidates(self.lab.terminals["DEMO-KNB"]), ["39-knb", "93-svm", "94-dy", "17-knb"])

    def test_finished_browse_selection_does_not_confirm_different_train(self):
        self.departure()
        self.accept("DEMO-DY", "D")
        self.accept("DEMO-DY", "D")
        self.assertEqual(self.lab.terminals["DEMO-DY"].selected, "39-knb")
        stale = self.lab.frame("DEMO-DY")["view_token"]
        self.lab.terminals["SECOND-DY"] = deepcopy(self.lab.terminals["DEMO-DY"])
        self.lookup("39", "SECOND-DY"); self.accept("SECOND-DY", "#")
        frame = self.lab.frame("DEMO-DY")
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(self.lab.command("DEMO-DY", {"command_id": "stale-choice", "view_token": stale, "key": "#"})["status"], "rejected")
        self.assertEqual(len(self.lab.arrivals), 1)

    def through93(self):
        """93 goes on from KNB to DY: in from SVM, out to DY with one number."""
        package = deepcopy(self.lab.publication)
        next(m for m in package["trains"] if m["id"] == "93-knb")["departure_time"] = "12:43"
        service = next(s for s in package["services"] if s["id"] == "93")
        service["stops"][1]["departure_time"] = "12:43"
        stop = {"station_id": "dy", "stop_order": 2, "arrival_time": "12:50", "departure_time": None}
        service["stops"].append(stop)
        package["trains"].append({**stop, "id": "93-dy", "service_id": "93", "train_number": "93", "days": "Dagl", "track_id": "dy-1"})
        self.lab = Terminal16Lab(self.lab.engine, package, {"DEMO-SVM": "svm", "DEMO-KNB": "knb", "DEMO-DY": "dy"})

    def test_a_through_train_never_seen_to_come_is_moved_here_first(self):
        """Benny #170, Casper 2026-10-09: SVM never sent 93, so the system has
        it at SVM. At KNB 93# asks FLYTTA 93 HIT?, and asks DY nothing; once
        it is here, the next 93# asks DY at once."""
        self.through93()
        frame = self.lookup("93")["frame"]
        self.assertEqual("FLYTTA 93 HIT?  ", frame["lines"][0])
        self.assertEqual("Flytta hit", frame["keys"]["#"]["label"])
        self.assertEqual(0, self.lab.frame("DEMO-DY")["requests"]["count"], "nothing asked of DY")
        self.accept("DEMO-KNB", "#")                       # moved here on its planned track
        self.assertIn("93-svm", self.lab.completed)        # SVM's part is over
        self.assertNotIn("93-svm", self.lab._candidates(self.lab.terminals["DEMO-SVM"]))
        self.accept("DEMO-KNB", "#")                       # dismiss the arrival notice
        frame = self.lookup("93")["frame"]                 # here now: the departure, asked at once
        self.assertTrue(frame["lines"][0].endswith("93?DY"), frame["lines"])

    def test_a_departure_picked_from_the_list_still_takes_the_train_along(self):
        """The departure picked in the list can still be asked for before the
        train is seen to come; sent on, it jumps here (Casper, 2026-10-02)."""
        self.through93()
        terminal = self.lab.terminals["DEMO-KNB"]
        terminal.selected, terminal.screen = "93-knb", "detail"
        self.accept("DEMO-KNB", "#")                       # asks DY
        self.lookup("93", "DEMO-DY"); self.accept("DEMO-DY", "#")
        frame = self.lab.frame("DEMO-KNB")
        self.assertEqual("Rapportera avgång", frame["keys"]["#"]["label"])
        self.assertEqual("           93>DY", frame["lines"][0])
        self.accept("DEMO-KNB", "#")
        self.assertIn("93-svm", self.lab.completed)        # SVM's part is over
        self.assertEqual("Rapportera ankomst", self.lookup("93", "DEMO-DY")["frame"]["keys"]["#"]["label"])

    def test_a_through_train_in_the_usual_order(self):
        self.through93()
        self.lookup("93", "DEMO-SVM")                      # SVM asks KNB
        frame = self.lookup("93")["frame"]                 # the request, not the departure
        self.assertEqual("Ge klart", frame["keys"]["#"]["label"])
        self.accept("DEMO-KNB", "#")
        self.accept("DEMO-SVM", "#")
        self.assertEqual(self.lookup("93")["frame"]["keys"]["#"]["label"], "Rapportera ankomst")
        self.accept("DEMO-KNB", "#")
        self.accept("DEMO-KNB", "#")  # Dismiss the arrival acknowledgement.
        frame = self.lookup("93")["frame"]                 # arrived: the departure, asked at once
        self.assertTrue(frame["lines"][0].endswith("93?DY"), frame["lines"])

    def test_every_notice_fits_and_can_be_drawn_in_every_language(self):
        """FLERA TÅG - ADMIN was 17 characters: the frame raised and the box
        got no answer at all."""
        from tmbox_gateway.device_ui import LANGUAGES
        terminal = self.lab.terminals["DEMO-KNB"]
        for language, _ in LANGUAGES:
            for notice in ("ANNAN SIDA", "INGET TÅG", "FLERA TÅG ADMIN", "12345 MOTTAGET",
                           "12345 ÅTERTAGET", "12345 NEKAT", "12345 ANK SP12A", "12345 UPPT SPÅR"):
                with self.subTest(language=language, notice=notice):
                    terminal.language, terminal.notice = language, notice
                    frame = self.lab.frame("DEMO-KNB")
                    self.assertEqual([16, 16], [len(line) for line in frame["lines"]])
        terminal.notice = ""

    def test_full_clearance_chain(self):
        self.lookup("39")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertIn("39?DY", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertNotIn("#", self.lab.frame("DEMO-KNB")["keys"])
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)
        self.assertIn("39>DY", self.lab.frame("DEMO-KNB")["lines"][0])
        self.accept("DEMO-KNB", "#")
        self.assertIn("39▶DY", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertIn("KNB▶39", self.lab.frame("DEMO-DY")["lines"][0])
        self.accept("DEMO-DY", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)
        self.assertEqual(self.lab.arrivals["39-knb"]["track"], "dy-1")
        self.assertEqual([x["action"] for x in self.lab.engine.audit], ["request", "accept", "depart", "arrive"])

    def test_two_simultaneous_requests_share_top_row(self):
        self.lookup("17")
        self.lookup("39")
        self.accept("DEMO-KNB", "B")
        self.assertEqual(self.lab.frame("DEMO-KNB")["lines"][0], "SVM?17     39?DY")

    def test_left_arrow_direction_changes_only_on_departure(self):
        self.lookup("17")
        self.lookup("17", "DEMO-SVM"); self.accept("DEMO-SVM", "#")
        self.assertIn("SVM<17", self.lab.frame("DEMO-KNB")["lines"][0])
        self.accept("DEMO-KNB", "#")
        self.assertIn("SVM◀17", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertIn("17◀KNB", self.lab.frame("DEMO-SVM")["lines"][0])

    def test_rejection_notifies_sender_without_departure(self):
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "*"); self.accept("DEMO-DY", "#")
        self.assertIn("NEKAT", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_home_does_not_cancel_pending_request(self):
        self.lookup("39"); self.accept("DEMO-KNB", "B")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "overview")

    def test_cancel_pending_requires_explicit_confirmation(self):
        self.lookup("39"); self.accept("DEMO-KNB", "*")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.accept("DEMO-KNB", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_cancel_approved_before_departure(self):
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "#")
        self.accept("DEMO-KNB", "*"); self.accept("DEMO-KNB", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_star_opens_withdrawal_and_second_star_keeps_request(self):
        self.lookup("39")
        frame = self.accept("DEMO-KNB", "*")["frame"]
        self.assertIn("ÅTER 39?", frame["lines"][0])
        self.assertEqual(frame["keys"]["#"]["label"], "Bekräfta återtagning")
        self.accept("DEMO-KNB", "*")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "detail")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual([event["action"] for event in self.lab.engine.audit], ["request"])

    def test_star_opens_rejection_and_second_star_keeps_incoming_request(self):
        self.lookup("39")
        frame = self.lookup("39", "DEMO-DY")["frame"]
        self.assertEqual(frame["keys"]["B"]["label"], "Översikt utan trafikändring")
        frame = self.accept("DEMO-DY", "*")["frame"]
        self.assertIn("NEKA 39?", frame["lines"][0])
        self.assertEqual(len(self.lab.engine.audit), 1)
        frame = self.accept("DEMO-DY", "*")["frame"]
        self.assertEqual(frame["keys"]["#"]["label"], "Ge klart")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(len(self.lab.engine.audit), 1)

    def test_home_keeps_incoming_request_without_answering(self):
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "B")
        self.assertEqual(self.lab.terminals["DEMO-DY"].screen, "overview")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(len(self.lab.engine.audit), 1)

    def test_star_keeps_approved_clearance_when_confirmation_is_abandoned(self):
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "#")
        self.accept("DEMO-KNB", "*"); self.accept("DEMO-KNB", "*")
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)
        self.assertEqual(self.lab.frame("DEMO-KNB")["keys"]["#"]["label"], "Rapportera avgång")

    def test_star_after_departure_only_goes_back_at_either_station(self):
        self.departure()
        for device in ("DEMO-KNB", "DEMO-DY"):
            self.assertEqual(self.lab.frame(device)["keys"]["*"]["label"], "Tillbaka")
            self.accept(device, "*")
            self.assertEqual(self.lab.terminals[device].screen, "overview")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)
        self.assertEqual(len(self.lab.engine.audit), 3)

    def test_star_leaves_track_picker_without_arrival(self):
        self.departure(); self.accept("DEMO-DY", "B"); self.accept("DEMO-DY", "D")
        frame = self.accept("DEMO-DY", "*")["frame"]
        self.assertEqual(frame["keys"]["#"]["label"], "Rapportera ankomst")
        self.assertEqual(self.lab.arrivals, {})
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_star_leaves_browse_without_requesting(self):
        self.accept("DEMO-KNB", "D"); self.accept("DEMO-KNB", "*")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "overview")
        self.assertEqual(self.lab.engine.audit, [])

    def test_stale_rejection_cannot_reject_after_sender_withdraws(self):
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "*")
        stale = self.lab.frame("DEMO-DY")["view_token"]
        self.accept("DEMO-KNB", "*"); self.accept("DEMO-KNB", "#")
        frame = self.lab.frame("DEMO-DY")
        self.assertFalse(frame["keys"]["#"]["acts"], "# closes the notice, it cannot reject")
        self.assertIn("39 ÅTERTAGET", frame["lines"][0])
        self.assertEqual(self.lab.command("DEMO-DY", {"command_id": "stale-rejection", "view_token": stale, "key": "#"})["status"], "rejected")
        self.accept("DEMO-DY", "*")
        self.assertEqual([event["action"] for event in self.lab.engine.audit], ["request", "cancel"])

    def test_stale_withdrawal_cannot_cancel_after_another_sender_reports_departure(self):
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "#")
        self.lab.terminals["SECOND-KNB"] = deepcopy(self.lab.terminals["DEMO-KNB"])
        self.accept("DEMO-KNB", "*")
        stale = self.lab.frame("DEMO-KNB")["view_token"]
        self.accept("SECOND-KNB", "#")
        frame = self.lab.frame("DEMO-KNB")
        self.assertNotIn("#", frame["keys"])
        self.assertIn("LÄGET ÄNDRAT", frame["lines"][0])
        self.assertEqual(self.lab.command("DEMO-KNB", {"command_id": "stale-withdrawal", "view_token": stale, "key": "#"})["status"], "rejected")
        self.accept("DEMO-KNB", "*")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_typing_new_train_during_withdrawal_does_not_withdraw_old_train(self):
        self.lookup("39"); self.accept("DEMO-KNB", "*")
        self.lookup("17")                                  # asks for 17 too, on the other line
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(self.lab.terminals["DEMO-KNB"].selected, "17-knb")
        self.assertEqual(["request", "request"], [entry["action"] for entry in self.lab.engine.audit])

    def test_no_cancel_after_departure(self):
        self.departure()
        self.assertEqual(self.lab.frame("DEMO-KNB")["keys"]["*" ]["label"], "Tillbaka")
        self.accept("DEMO-KNB", "A")  # Queue navigation cannot cancel an occupied line.
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_after_clearance_hash_never_takes_the_train_in(self):
        """The # after #Ja must not receive the train. One the sender never
        reported departed can still be placed, with B and then #."""
        self.lookup("39")
        self.lookup("39", "DEMO-DY"); self.accept("DEMO-DY", "#")
        frame = self.lab.frame("DEMO-DY")
        self.assertNotIn("#", frame["keys"])
        self.assertEqual("Placera på spår…", frame["keys"]["B"]["label"])
        self.assertEqual(self.send("DEMO-DY", "#")["status"], "rejected")
        self.assertEqual(self.lab.arrivals, {})
        self.accept("DEMO-DY", "B"); self.accept("DEMO-DY", "#")
        self.assertEqual("dy-1", self.lab.arrivals["39-knb"]["track"])
        self.assertEqual(State.FREE, self.lab.engine.connections["east"].state)

    def test_arrival_track_override_preserves_plan(self):
        self.departure(); self.accept("DEMO-DY", "B"); self.accept("DEMO-DY", "D")
        self.assertIn("SPÅR 2", self.lab.frame("DEMO-DY")["lines"][0])
        self.accept("DEMO-DY", "#")
        self.assertEqual(self.lab.arrivals["39-knb"]["track"], "dy-2")
        self.assertEqual(self.lab.arrivals["39-knb"]["planned_track"], "dy-1")

    def test_an_occupied_track_does_not_stop_the_arrival(self):
        """Casper, 2026-10-02: taken in all the same, and the box says so."""
        self.departure()
        self.lab.arrivals["another-leg"] = {"station": "dy", "track": "dy-1"}
        self.accept("DEMO-DY", "#")
        self.assertEqual("39 UPPT SPÅR    ", self.lab.frame("DEMO-DY")["lines"][0])
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)
        self.assertEqual("dy-1", self.lab.arrivals["39-knb"]["track"])

    def test_direct_mode_reserves_without_receiver_but_still_requires_departure(self):
        self.lab = demo_lab("direct")
        self.lab.engine.set_clock_source(lambda: {"configured": True, "time": "12:34"})
        self.lookup("39")
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)
        self.accept("DEMO-KNB", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_wrong_station_cannot_select_nonlocal_train(self):
        self.lookup("39", "DEMO-SVM")
        self.assertIn("INGET TÅG", self.lab.frame("DEMO-SVM")["lines"][0])

    def test_client_cannot_supply_action_station_or_destination(self):
        self.lookup("39")
        revision = self.lab.engine.revision
        for extra in ({"action": "depart"}, {"station_id": "dy"}, {"connection_id": "west"}):
            self.assertEqual(self.send("DEMO-KNB", "#", **extra)["status"], "rejected")
        self.assertEqual(self.lab.engine.revision, revision)

    def test_malformed_keys_and_lookup_overrides_are_rejected(self):
        for key in (None, [], {}, 1, "AA"):
            self.assertEqual(self.send("DEMO-KNB", key)["status"], "rejected")
        for extra in ({"action": "depart"}, {"station_id": "dy"}, {"connection_id": "west"}):
            result = self.send("DEMO-KNB", "#", train_number="39",
                               entry_context=self.lab.frame("DEMO-KNB")["entry"]["context"], **extra)
            self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.lab.engine.revision, 0)

    def test_completed_sender_does_not_advertise_unavailable_key(self):
        self.departure(); self.accept("DEMO-DY", "#")
        frame = self.lab.frame("DEMO-KNB")
        self.assertEqual(set(frame["keys"]), {"#", "*", "A"})
        self.assertEqual(frame["keys"]["#"]["label"], "Stäng meddelande")
        self.assertIn("39 MOTTAGET", frame["lines"][0])
        before = len(self.lab.engine.audit)
        self.accept("DEMO-KNB", "#")
        self.assertEqual(len(self.lab.engine.audit), before)
        self.assertNotIn("39", self.lab.frame("DEMO-KNB")["lines"][0])

    def test_route_is_revalidated_at_mutation(self):
        self.lab.publication["services"][1]["stops"][1]["station_id"] = "svm"
        result = self.send("DEMO-KNB", "#", train_number="39", entry_context=self.lab.frame("DEMO-KNB")["entry"]["context"])
        self.assertEqual("rejected", result["status"])
        self.assertEqual(self.lab.engine.revision, 0)

    def test_duplicate_command_is_idempotent_and_id_cannot_be_repurposed(self):
        frame = self.lab.frame("DEMO-KNB")
        body = {"command_id": "same-id", "view_token": frame["view_token"], "key": "#",
                "train_number": "39", "entry_context": frame["entry"]["context"]}
        first = self.lab.command("DEMO-KNB", body)
        second = self.lab.command("DEMO-KNB", body)
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(self.lab.engine.revision, 1)
        self.assertEqual(self.lab.command("DEMO-KNB", {**body, "key": "D"})["status"], "rejected")

    def test_stale_view_rejected_after_other_terminal_changes_state(self):
        stale = self.lab.frame("DEMO-KNB")
        self.lookup("93", "DEMO-SVM")                      # SVM asks KNB: every view changes
        result = self.lab.command("DEMO-KNB", {"command_id": "old", "view_token": stale["view_token"], "key": "#",
                                               "train_number": "39", "entry_context": stale["entry"]["context"]})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_old_reset_frame_cannot_control_new_lab(self):
        old = self.lab.frame("DEMO-KNB")
        self.lab = demo_lab()
        result = self.lab.command("DEMO-KNB", {"command_id": "old", "view_token": old["view_token"], "key": "C"})
        self.assertEqual(result["status"], "rejected")

    def test_completed_leg_cannot_be_sent_again(self):
        self.departure(); self.accept("DEMO-DY", "#")
        self.lookup("39")
        self.assertIn("INGET TÅG", self.lab.frame("DEMO-KNB")["lines"][0])

    def test_long_identities_use_active_navigation_instead_of_truncate(self):
        for old, new in (("17", "12345"), ("39", "67890")):
            for item in self.lab.publication["trains"] + self.lab.publication["services"]:
                if item["train_number"] == old: item["train_number"] = new
            self.lab.legs[f"{old}-knb"]["train_number"] = new
            self.lookup(new)
        self.accept("DEMO-KNB", "B")
        self.assertIn("SVM?12345", self.lab.frame("DEMO-KNB")["lines"][0])
        self.accept("DEMO-KNB", "B")
        self.assertIn("SVM?12345", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertEqual(self.lab.frame("DEMO-KNB")["active"]["count"], 2)
        self.accept("DEMO-KNB", "D")
        self.assertIn("67890?DY", self.lab.frame("DEMO-KNB")["lines"][0])


class Terminal16ReceiptTests(unittest.TestCase):
    send = Terminal16Tests.send
    lookup = Terminal16Tests.lookup
    accept = Terminal16Tests.accept
    departure = Terminal16Tests.departure

    def setUp(self):
        Terminal16Tests.setUp(self)
        self.now = 100.0
        self.lab.now = lambda: self.now

    def depart93(self):
        self.lookup("93", "DEMO-SVM")
        self.accept("DEMO-KNB", "#")
        self.accept("DEMO-SVM", "#")

    def test_sender_sees_receipt_then_train_disappears_without_acknowledgement(self):
        self.depart93()
        self.accept("DEMO-KNB", "#")
        frame = self.lab.frame("DEMO-SVM")
        self.assertEqual(frame["lines"], ["93 MOTTAGET     ", "KNB        12:34"])
        self.assertIn("mottaget i Knastebo", frame["status"])
        self.assertIsNone(self.lab.terminals["DEMO-SVM"].selected)
        self.now += 4.9
        self.assertIn("93 MOTTAGET", self.lab.frame("DEMO-SVM")["lines"][0])
        self.now += 0.1
        frame = self.lab.frame("DEMO-SVM")
        self.assertEqual(frame["lines"][0], " " * 16)
        self.assertNotIn("93", frame["status"])
        self.assertNotIn("93-svm", self.lab._candidates(self.lab.terminals["DEMO-SVM"]))
        self.assertEqual(len(self.lab.engine.audit), 4)
        self.now += 60
        self.assertEqual(self.lab.frame("DEMO-SVM")["lines"][0], " " * 16)

    def test_receipt_reaches_all_sender_boxes_but_no_unrelated_station(self):
        self.lab.terminals["SECOND-SVM"] = deepcopy(self.lab.terminals["DEMO-SVM"])
        self.depart93(); self.accept("DEMO-KNB", "#")
        self.assertIn("93 MOTTAGET", self.lab.frame("SECOND-SVM")["lines"][0])
        self.assertEqual(self.lab.terminals["DEMO-DY"].receipts, [])
        self.assertEqual(self.lab.terminals["DEMO-KNB"].receipts, [])

    def test_receipt_is_deferred_while_sender_handles_a_different_train(self):
        self.departure()
        self.lookup("17")
        self.accept("DEMO-DY", "#")
        terminal = self.lab.terminals["DEMO-KNB"]
        self.assertEqual((terminal.selected, terminal.screen), ("17-knb", "detail"))
        self.assertIsNone(terminal.receipt_until)
        self.now += 20
        self.assertIn("SVM?17", self.lab.frame("DEMO-KNB")["lines"][0])
        self.accept("DEMO-KNB", "B")
        self.assertIn("39 MOTTAGET", self.lab.frame("DEMO-KNB")["lines"][0])
        self.now += 5
        self.assertNotIn("39", self.lab.frame("DEMO-KNB")["lines"][0])

    def test_alternate_arrival_track_also_notifies_sender(self):
        self.depart93()
        self.accept("DEMO-KNB", "B"); self.accept("DEMO-KNB", "D")
        self.accept("DEMO-KNB", "#")
        self.assertEqual(self.lab.arrivals["93-svm"]["track"], "knb-2")
        self.assertIn("93 MOTTAGET", self.lab.frame("DEMO-SVM")["lines"][0])

    def test_an_arrival_on_an_occupied_track_still_tells_the_sender(self):
        self.depart93()
        self.lab.arrivals["other"] = {"station": "knb", "track": "knb-1"}
        self.accept("DEMO-KNB", "#")
        self.assertIn("93 MOTTAGET", self.lab.frame("DEMO-SVM")["lines"][0])

    def test_direct_traffic_also_shows_receipt_and_clears_completed_selection(self):
        self.lab = demo_lab("direct")
        self.lab.now = lambda: self.now
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.lookup("93", "DEMO-SVM")
        self.lookup("93", "DEMO-KNB")
        self.accept("DEMO-SVM", "#"); self.accept("DEMO-KNB", "#")
        self.assertIn("93 MOTTAGET", self.lab.frame("DEMO-SVM")["lines"][0])
        self.now += 5
        self.assertEqual(self.lab.frame("DEMO-SVM")["lines"][0], " " * 16)
        self.assertEqual([event["action"] for event in self.lab.engine.audit], ["request", "depart", "arrive"])

    def test_long_station_code_is_not_truncated_and_clock_stays_visible(self):
        self.depart93(); self.accept("DEMO-KNB", "#")
        station = self.lab.engine.config.stations["knb"]
        self.lab.engine.config.stations["knb"] = type(station)(station.id, "CHARLOTTENDAL", station.name)
        frame = self.lab.frame("DEMO-SVM")
        self.assertEqual(frame["lines"], ["93 CHARLOTTENDAL", "MOTTAGET   12:34"])

    def test_duplicate_arrival_never_replays_receipt(self):
        self.depart93()
        body = {"command_id": "arrival", "view_token": self.lab.frame("DEMO-KNB")["view_token"], "key": "#"}
        self.lab.command("DEMO-KNB", body)
        self.lab.frame("DEMO-SVM")
        self.now += 5
        self.lab.frame("DEMO-SVM")
        self.lab.command("DEMO-KNB", body)
        self.assertEqual(self.lab.terminals["DEMO-SVM"].receipts, [])
        self.assertEqual(len(self.lab.engine.audit), 4)

    def test_timeout_invalidates_old_dismiss_key_before_it_can_open_other_view(self):
        self.depart93(); self.accept("DEMO-KNB", "#")
        old = self.lab.frame("DEMO-SVM")["view_token"]
        self.now += 5
        result = self.lab.command("DEMO-SVM", {"command_id": "old-dismiss", "view_token": old, "key": "#"})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.lab.terminals["DEMO-SVM"].screen, "overview")
        self.assertEqual(result["frame"]["lines"][0], " " * 16)

    def test_queue_shortcut_dismisses_receipt_without_replaying_it(self):
        self.depart93(); self.accept("DEMO-KNB", "#")
        self.accept("DEMO-SVM", "A")
        self.assertEqual(self.lab.terminals["DEMO-SVM"].screen, "requests")
        self.assertEqual(self.lab.terminals["DEMO-SVM"].receipts, [])
        self.accept("DEMO-SVM", "*")   # INGA FRÅGOR: * (or three seconds) leaves it
        self.assertEqual(self.lab.frame("DEMO-SVM")["lines"][0], " " * 16)

    def test_multiple_receipts_are_shown_in_order_for_five_seconds_each(self):
        # Two different outbound legs finish while sender works on another view.
        for number in ("17", "39"):
            self.lookup(number)
            receiver = "DEMO-SVM" if number == "17" else "DEMO-DY"
            self.accept(receiver, "#"); self.accept("DEMO-KNB", "#")
        self.accept("DEMO-KNB", "A")
        self.accept("DEMO-SVM", "#"); self.accept("DEMO-DY", "#")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "requests")
        self.accept("DEMO-KNB", "*")
        self.assertIn("17 MOTTAGET", self.lab.frame("DEMO-KNB")["lines"][0])
        self.now += 5
        self.assertIn("39 MOTTAGET", self.lab.frame("DEMO-KNB")["lines"][0])
        self.now += 5
        self.assertEqual(self.lab.frame("DEMO-KNB")["lines"][0], " " * 16)


class Terminal16NoticeTests(unittest.TestCase):
    """Notices clear themselves, name the other station and never ask for #OK."""
    send = Terminal16Tests.send
    lookup = Terminal16Tests.lookup
    accept = Terminal16Tests.accept
    setUp = Terminal16ReceiptTests.setUp

    def request(self, number="93", sender="DEMO-SVM"):
        return Terminal16RequestQueueTests.request(self, number, sender)

    def test_an_empty_queue_says_so_and_goes_home_by_itself(self):
        """Benny, 2026-10-03: INGA FRÅGOR stayed until a key was pressed."""
        frame = self.accept("DEMO-KNB", "A")["frame"]
        self.assertEqual("INGA FRÅGOR     ", frame["lines"][0])
        self.now += 2.9
        self.assertEqual("INGA FRÅGOR     ", self.lab.frame("DEMO-KNB")["lines"][0])
        self.now += 0.2
        self.assertEqual(" " * 16, self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertEqual("overview", self.lab.terminals["DEMO-KNB"].screen)

    def test_a_request_replacing_inga_fragor_is_not_sent_home_by_its_timer(self):
        self.accept("DEMO-KNB", "A")
        self.lab.frame("DEMO-KNB")            # INGA FRÅGOR is shown, its three seconds start
        self.request()
        self.assertIn("SVM?93", self.lab.frame("DEMO-KNB")["lines"][0])
        self.now += 10
        self.assertIn("SVM?93", self.lab.frame("DEMO-KNB")["lines"][0], "a question waits for its answer")

    def test_a_box_busy_with_another_train_is_left_alone(self):
        self.lookup("39")                         # KNB asks DY for 39
        self.accept("DEMO-DY", "B")               # DY leaves the queue ...
        self.lookup("94", "DEMO-DY")              # ... and works on its own 94
        busy = self.lab.frame("DEMO-DY")["lines"][0]
        self.accept("DEMO-KNB", "*"); self.accept("DEMO-KNB", "#")
        terminal = self.lab.terminals["DEMO-DY"]
        self.assertEqual((busy, "", "detail", "94-dy"),
                         (self.lab.frame("DEMO-DY")["lines"][0], terminal.notice, terminal.screen, terminal.selected))
        # Only what 94 can do changes: the line 39 held is free, so # asks for 94 now.
        self.assertEqual("#Beg A:Kö  12:34", self.lab.frame("DEMO-DY")["lines"][1])

    def test_no_notice_asks_for_hash_ok(self):
        """Nothing on the box waits for #OK (Casper, 2026-10-03): every notice
        names the other station, or nothing, and clears itself."""
        cases = {"INGET TÅG": lambda: self.lookup("123"),
                 "39 ÅTERTAGET": lambda: (self.lookup("39"), self.accept("DEMO-KNB", "*"), self.accept("DEMO-KNB", "#"))}
        for notice, make in cases.items():
            with self.subTest(notice):
                self.setUp()
                make()
                frame = self.lab.frame("DEMO-KNB")
                self.assertTrue(frame["lines"][0].startswith(notice), frame["lines"])
                self.assertNotIn("#OK", frame["lines"][1])
                self.now += 3
                self.assertEqual(" " * 16, self.lab.frame("DEMO-KNB")["lines"][0])


class Terminal16RequestQueueTests(unittest.TestCase):
    setUp = Terminal16Tests.setUp
    send = Terminal16Tests.send
    lookup = Terminal16Tests.lookup
    accept = Terminal16Tests.accept

    def two_senders(self):
        self.assertIn("94-dy", self.lab.legs)  # Public bench also supports the two-sender example.

    def request(self, number="93", sender="DEMO-SVM"):
        self.lookup(number, sender)

    def test_idle_receiver_opens_request_and_accepts_without_train_entry(self):
        self.request()
        frame = self.lab.frame("DEMO-KNB")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "requests")
        self.assertIn("SVM?93", frame["lines"][0]); self.assertIn("1/1", frame["lines"][0])
        self.assertTrue(frame["lines"][1].startswith("#Ja *Nej"))
        self.assertEqual(frame["keys"]["#"]["label"], "Ge klart")
        self.assertEqual(len(self.lab.engine.audit), 1)  # Showing is not acceptance.
        self.accept("DEMO-KNB", "#")
        self.accept("DEMO-SVM", "#")
        self.accept("DEMO-KNB", "#")
        self.assertEqual([e["action"] for e in self.lab.engine.audit], ["request", "accept", "depart", "arrive"])

    def test_multiple_requests_keep_fifo_order_and_current_focus(self):
        self.two_senders(); self.request("94", "DEMO-DY"); self.request()
        frame = self.lab.frame("DEMO-KNB")
        self.assertEqual(frame["requests"]["count"], 2)
        self.assertEqual(frame["requests"]["position"], 1)
        self.assertEqual(self.lab.terminals["DEMO-KNB"].selected, "94-dy")
        self.assertIn("1/2", frame["lines"][0])
        self.assertIn("2 väntar", frame["requests"]["label"])
        self.assertIn("93", self.accept("DEMO-KNB", "D")["frame"]["lines"][0])
        self.assertIn("2/2", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertIn("94", self.accept("DEMO-KNB", "C")["frame"]["lines"][0])
        self.assertEqual([e["action"] for e in self.lab.engine.audit], ["request", "request"])

    def test_accept_does_not_repurpose_hash_for_next_request(self):
        self.two_senders(); self.request(); self.request("94", "DEMO-DY")
        frame = self.lab.frame("DEMO-KNB")
        body = {"key": "#", "view_token": frame["view_token"], "command_id": "one-approval"}
        self.assertEqual(self.lab.command("DEMO-KNB", body)["status"], "accepted")
        self.assertEqual(self.lab.command("DEMO-KNB", body)["status"], "accepted")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].selected, "93-svm")
        frame = self.lab.frame("DEMO-KNB")
        self.assertEqual(frame["requests"]["count"], 1)
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(self.send("DEMO-KNB", "#")["status"], "rejected")
        self.accept("DEMO-KNB", "A"); self.accept("DEMO-KNB", "#")
        self.assertEqual([e["action"] for e in self.lab.engine.audit], ["request", "request", "accept", "accept"])

    def test_queue_can_be_left_and_reopened_by_a_or_overview_hash(self):
        self.request(); self.accept("DEMO-KNB", "B")
        frame = self.lab.frame("DEMO-KNB")
        self.assertIn("A:Kö", frame["lines"][1])
        self.assertEqual(frame["keys"]["#"]["label"], "Visa väntande förfrågningar")
        self.accept("DEMO-KNB", "#")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "requests")
        self.accept("DEMO-KNB", "B"); self.accept("DEMO-KNB", "D")
        self.accept("DEMO-KNB", "A")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].selected, "93-svm")
        self.assertEqual(len(self.lab.engine.audit), 1)

    def test_request_does_not_interrupt_other_views_or_notices(self):
        for screen in ("browse", "detail", "tracks", "cancel", "reject"):
            with self.subTest(screen=screen):
                self.setUp()
                terminal = self.lab.terminals["DEMO-KNB"]
                terminal.screen, terminal.selected = screen, "39-knb"
                self.request()
                self.assertEqual((terminal.screen, terminal.selected), (screen, "39-knb"))
                self.assertEqual(self.lab.frame("DEMO-KNB")["requests"]["count"], 1)
                self.accept("DEMO-KNB", "A")
                self.assertEqual(terminal.selected, "93-svm")
        self.setUp(); terminal = self.lab.terminals["DEMO-KNB"]
        terminal.notice = "INGET TÅG"
        self.request()
        self.assertEqual(terminal.notice, "INGET TÅG")
        self.accept("DEMO-KNB", "A")
        self.assertEqual(terminal.notice, "")
        self.assertEqual(terminal.screen, "requests")

    def test_withdrawn_request_cannot_silently_switch_to_another(self):
        self.two_senders(); self.request(); self.request("94", "DEMO-DY")
        stale = self.lab.frame("DEMO-KNB")["view_token"]
        self.accept("DEMO-SVM", "*"); self.accept("DEMO-SVM", "#")
        frame = self.lab.frame("DEMO-KNB")
        self.assertEqual(frame["requests"]["count"], 1)
        self.assertIn("93 ÅTERTAGET", frame["lines"][0])
        self.assertFalse(frame["keys"]["#"]["acts"])
        self.assertEqual(self.lab.command("DEMO-KNB", {"key": "#", "view_token": stale, "command_id": "late"})["status"], "rejected")
        # # only closes the notice; 94 is still waiting, never answered by it.
        self.accept("DEMO-KNB", "#")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "overview")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.accept("DEMO-KNB", "A")
        self.assertIn("94", self.lab.frame("DEMO-KNB")["lines"][0])
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)

    def test_another_receiver_answer_invalidates_queue_selection(self):
        self.lab.terminals["SECOND-KNB"] = deepcopy(self.lab.terminals["DEMO-KNB"])
        self.request()
        self.accept("SECOND-KNB", "#")
        frame = self.lab.frame("DEMO-KNB")
        self.assertEqual(frame["requests"]["count"], 0)
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(self.send("DEMO-KNB", "#")["status"], "rejected")

    def test_neka_still_requires_confirmation_and_keeps_remaining_queue(self):
        self.two_senders(); self.request(); self.request("94", "DEMO-DY")
        self.accept("DEMO-KNB", "*")
        self.assertEqual(self.lab.engine.connections["west"].state, State.REQUESTED)
        self.accept("DEMO-KNB", "#")
        self.assertEqual(self.lab.frame("DEMO-KNB")["requests"]["count"], 1)
        self.accept("DEMO-KNB", "A")
        self.assertIn("94", self.lab.frame("DEMO-KNB")["lines"][0])

    def test_abandoned_rejection_returns_to_the_same_queue_item(self):
        self.two_senders(); self.request(); self.request("94", "DEMO-DY")
        self.accept("DEMO-KNB", "*"); self.accept("DEMO-KNB", "*")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "requests")
        self.assertEqual(self.lab.terminals["DEMO-KNB"].selected, "93-svm")
        self.assertEqual(self.lab.frame("DEMO-KNB")["requests"]["count"], 2)

    def test_empty_queue_is_always_reachable_without_side_effects(self):
        frame = self.accept("DEMO-KNB", "A")["frame"]
        self.assertIn("INGA FRÅGOR", frame["lines"][0])
        self.assertEqual(frame["requests"]["count"], 0)
        self.assertFalse(frame["keys"]["#"]["acts"])
        self.assertEqual(self.lab.engine.audit, [])
        self.request()
        self.assertIn("SVM?93", self.lab.frame("DEMO-KNB")["lines"][0])

    def test_direct_traffic_never_asks_for_approval(self):
        self.lab = demo_lab("direct")
        self.lab.engine.set_clock_source(lambda: {"time": "12:34"})
        self.request()
        self.assertEqual(self.lab.frame("DEMO-KNB")["requests"]["count"], 0)
        self.assertEqual(self.lab.terminals["DEMO-KNB"].screen, "overview")

    def test_new_request_invalidates_an_old_overview_key(self):
        stale = self.lab.frame("DEMO-KNB")["view_token"]
        self.request()
        self.assertEqual(self.lab.command("DEMO-KNB", {"key": "#", "view_token": stale, "command_id": "old-overview"})["status"], "rejected")
        self.assertEqual(self.lab.engine.connections["west"].state, State.REQUESTED)

    def test_long_train_numbers_keep_identity_and_queue_count(self):
        for item in self.lab.publication["services"] + self.lab.publication["trains"]:
            if item["train_number"] == "93":
                item["train_number"] = "12345"
        self.lab.legs["93-svm"]["train_number"] = "12345"
        self.request("12345")
        frame = self.lab.frame("DEMO-KNB")
        self.assertIn("SVM?12345", frame["lines"][0]); self.assertIn("1/1", frame["lines"][0])


class Terminal16HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = LabServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def post(self, path, body, **headers):
        request = Request(self.url + path, method="POST", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json", **headers})
        with urlopen(request) as response:
            return json.load(response)

    def command(self, device, key, **extra):
        frame = self.server.lab.frame(device)
        body = {"command_id": str(uuid4()), "view_token": frame["view_token"], "key": key, **extra}
        if "train_number" in body:
            body["entry_context"] = frame["entry"]["context"]
        return self.post("/api/key", {"device_id": device, **body})

    def test_http_returns_only_test_boxes_and_assets(self):
        with urlopen(self.url + "/api/state") as response:
            state = json.load(response)
        self.assertEqual(len(state["frames"]), 3)
        self.assertTrue(all(f["device_id"].startswith("DEMO-") for f in state["frames"]))
        with urlopen(self.url + "/") as response:
            html = response.read()
            self.assertIn(b"ENBART TESTDATA", html)
            self.assertIn("Nollställ alla enheter".encode(), html)

    def placement_body(self, stations=None):
        state = self.server.snapshot()["placement"]
        return {"epoch": state["epoch"], "revision": state["revision"],
                "stations": stations or {"svm": {}, "knb": {"west": "right", "east": "left"}, "dy": {}}}

    def test_placement_preserves_live_lab_traffic_and_uses_shared_defaults(self):
        self.command("DEMO-SVM", "#", train_number="93")
        before = self.server.lab.frame("DEMO-KNB")
        engine = self.server.lab.engine
        audit = deepcopy(engine.audit)
        selected = self.server.lab.terminals["DEMO-KNB"].selected
        state = self.post("/api/display-placement", self.placement_body())
        after = next(f for f in state["frames"] if f["device_id"] == "DEMO-KNB")
        self.assertEqual(state["placement"]["scope"], "isolated-lab")
        self.assertIs(self.server.lab.engine, engine)
        self.assertEqual(audit, engine.audit)
        self.assertEqual(selected, self.server.lab.terminals["DEMO-KNB"].selected)
        self.assertIn("SVM?93", before["lines"][0])
        self.assertIn("93?SVM", after["lines"][0])
        self.assertEqual(before["entry"]["context"], after["entry"]["context"])
        self.assertGreater(after["view_revision"], before["view_revision"])
        self.assertEqual(after["requests"], before["requests"])
        self.command("DEMO-KNB", "#")
        self.command("DEMO-SVM", "#")
        self.command("DEMO-KNB", "#")
        self.assertIn("MOTTAGET", self.server.lab.frame("DEMO-SVM")["lines"][0])
        self.post("/api/reset-devices", {})
        self.assertEqual(self.server.lab._side("knb", "svm", "west"), "right")
        self.post("/api/display-placement", self.placement_body({"svm": {}, "knb": {}, "dy": {}}))
        self.assertEqual(self.server.lab._side("knb", "svm", "west"), "left")

    def test_placement_rejects_invalid_or_stale_drafts_atomically(self):
        before = self.server.snapshot()["placement"]
        for stations in ({"svm": {}, "knb": {"west": "up"}, "dy": {}},
                         {"svm": {"east": "left"}, "knb": {}, "dy": {}},
                         {"knb": {}}, {"svm": {}, "knb": [], "dy": {}}):
            with self.assertRaises(HTTPError) as error:
                self.post("/api/display-placement", self.placement_body(stations))
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.server.snapshot()["placement"], before)
        stale = self.placement_body()
        self.post("/api/display-placement", stale)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/display-placement", stale)
        self.assertEqual(error.exception.code, 409)
        stale = self.placement_body()
        self.post("/api/reset", {"mode": "direct"})
        with self.assertRaises(HTTPError) as error:
            self.post("/api/display-placement", stale)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.server.lab._side("knb", "svm", "west"), "left")

    def test_cross_origin_placement_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/display-placement", self.placement_body(), Origin="https://unrelated.example")
        self.assertEqual(error.exception.code, 403)

    def test_reset_all_devices_clears_every_traffic_phase(self):
        for phase in ("requested", "reserved", "occupied", "arrived"):
            with self.subTest(phase=phase):
                self.command("DEMO-KNB", "#", train_number="39")
                if phase != "requested":
                    self.command("DEMO-DY", "#", train_number="39")
                    self.command("DEMO-DY", "#")
                if phase in {"occupied", "arrived"}:
                    self.command("DEMO-KNB", "#")
                if phase == "arrived":
                    self.command("DEMO-DY", "#")
                self.command("DEMO-KNB", "#", train_number="17")
                before = self.server.lab
                old_contexts = {f["device_id"]: f["entry"]["context"] for f in before.frames()}
                state = self.post("/api/reset-devices", {})
                self.assertEqual(state["audit"], [])
                self.assertEqual(state["arrivals"], {})
                self.assertEqual(self.server.lab.bindings, {})
                self.assertEqual(self.server.lab.completed, set())
                self.assertEqual(self.server.lab.processed, {})
                self.assertTrue(all(line.state == State.FREE for line in self.server.lab.engine.connections.values()))
                for frame in state["frames"]:
                    terminal = self.server.lab.terminals[frame["device_id"]]
                    self.assertEqual(terminal.screen, "overview")
                    self.assertIsNone(terminal.selected)
                    self.assertEqual(terminal.notice, "")
                    self.assertEqual(terminal.receipts, [])
                    self.assertIsNone(terminal.receipt_until)
                    self.assertEqual(frame["requests"]["count"], 0)
                    self.assertEqual(frame["lines"][0], " " * 16)
                    self.assertNotEqual(frame["entry"]["context"], old_contexts[frame["device_id"]])
                self.assertEqual(self.server.lab.publication, before.publication)

    def test_reset_preserves_current_mode_station_assignments_and_live_clock(self):
        self.post("/api/reset", {"mode": "direct"})
        before = self.server.lab
        tick = ["17:06"]
        before.engine.set_clock_source(lambda: {"configured": True, "running": True, "time": tick[0]})
        before.terminals["EXTRA-BOX"] = deepcopy(before.terminals["DEMO-SVM"])
        self.command("DEMO-KNB", "#", train_number="39")
        state = self.post("/api/reset-devices", {})
        self.assertEqual(state["mode"], "direct")
        self.assertEqual(self.server.lab.engine.config, before.engine.config)
        self.assertEqual({d: t.station for d, t in self.server.lab.terminals.items()},
                         {d: t.station for d, t in before.terminals.items()})
        self.assertTrue(all(frame["lines"][1].endswith("17:06") for frame in state["frames"]))
        tick[0] = "17:07"
        self.assertTrue(all(frame["lines"][1].endswith("17:07") for frame in self.server.snapshot()["frames"]))
        self.command("DEMO-KNB", "#", train_number="39")
        self.assertEqual(self.server.lab.engine.connections["east"].state, State.RESERVED)

    def test_commands_from_before_reset_cannot_recreate_traffic(self):
        self.command("DEMO-KNB", "#", train_number="39")
        stale = self.server.lab.frame("DEMO-KNB")
        self.post("/api/reset-devices", {})
        with self.assertRaises(HTTPError) as error:
            self.post("/api/key", {"device_id": "DEMO-KNB", "command_id": "late-command",
                                   "view_token": stale["view_token"], "key": "#"})
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.server.lab.engine.audit, [])

    def test_device_reset_rejects_settings_without_changing_state(self):
        before = self.server.lab
        with self.assertRaises(HTTPError) as error:
            self.post("/api/reset-devices", {"mode": "direct"})
        self.assertEqual(error.exception.code, 400)
        self.assertIs(self.server.lab, before)

    def test_cross_origin_device_reset_is_rejected(self):
        before = self.server.lab
        with self.assertRaises(HTTPError) as error:
            self.post("/api/reset-devices", {}, Origin="https://unrelated.example")
        self.assertEqual(error.exception.code, 403)
        self.assertIs(self.server.lab, before)

    def test_cross_origin_reset_is_rejected(self):
        request = Request(self.url + "/api/reset", method="POST", data=b'{"mode":"direct"}',
                          headers={"Content-Type":"application/json", "Origin":"https://unrelated.example"})
        with self.assertRaises(HTTPError) as error: urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_no_production_endpoint(self):
        with self.assertRaises(HTTPError) as error: urlopen(self.url + "/v1/browser-clients")
        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__": unittest.main()
