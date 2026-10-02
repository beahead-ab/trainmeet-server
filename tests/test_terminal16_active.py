"""Active-train navigation is Server-owned, not a browser-only shortcut."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_terminal16_pilot as pilot
from tmbox_gateway.device_ui import LANGUAGES
from tmbox_gateway.models import ConnectionState as State
from tmbox_gateway.terminal16_demo import demo_lab


class ActiveTrainTests(unittest.TestCase):
    setUp = pilot.Terminal16Tests.setUp
    send = pilot.Terminal16Tests.send
    lookup = pilot.Terminal16Tests.lookup
    accept = pilot.Terminal16Tests.accept

    def ready(self, number, receiver):
        self.lookup(number)                                # asks at once (2.1.0)
        if self.lab.engine.connections[self.lab.legs[number + "-cda"]["connection_id"]].state == State.REQUESTED:
            self.accept(receiver, "#")

    def two_ready(self):
        self.ready("17", "DEMO-MUN")
        self.ready("39", "DEMO-VA")
        self.accept("DEMO-CDA", "B")

    def test_b_opens_first_ready_train_and_cd_switches_without_extra_selection(self):
        self.two_ready()
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual(frame["lines"], ["MUN<17     39>VA", "B:Akt2 C/D 12:34"])
        self.assertEqual(frame["keys"]["B"]["label"], "Aktiva tåg (2)")
        before = deepcopy(self.lab.engine.audit)
        for key, number, position in (("B", "17", 1), ("D", "39", 2), ("D", "17", 1), ("C", "39", 2)):
            frame = self.accept("DEMO-CDA", key)["frame"]
            self.assertEqual(frame["active"], {"count": 2, "position": position, "movement_id": number + "-cda"})
            self.assertIn(f"{position}/2", frame["lines"][0])
            self.assertEqual(frame["keys"]["#"]["label"], "Rapportera avgång")
        self.assertEqual(before, self.lab.engine.audit)

    def test_departure_pins_identity_and_duplicate_or_fresh_hash_cannot_send_next(self):
        self.two_ready()
        self.accept("DEMO-CDA", "B")
        body = {"command_id": uuid4().hex, "view_token": self.lab.frame("DEMO-CDA")["view_token"], "key": "#"}
        result = self.lab.command("DEMO-CDA", body)
        self.assertEqual(result["status"], "accepted")
        self.assertNotIn("#", result["frame"]["keys"])
        self.assertEqual(result["frame"]["active"]["movement_id"], "17-cda")
        before = deepcopy(self.lab.engine.audit)
        self.assertEqual(self.lab.command("DEMO-CDA", body)["status"], "accepted")
        self.assertEqual(self.lab.command("DEMO-CDA", {**body, "command_id": uuid4().hex})["status"], "rejected")
        self.assertEqual(self.send("DEMO-CDA", "#")["status"], "rejected")
        self.assertEqual(before, self.lab.engine.audit)
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)
        self.assertEqual(self.accept("DEMO-CDA", "D")["frame"]["active"]["movement_id"], "39-cda")
        self.accept("DEMO-CDA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_future_trains_and_unanswered_requests_are_not_active(self):
        self.assertEqual(self.accept("DEMO-CDA", "B")["frame"]["active"]["count"], 0)
        self.assertNotIn("#", self.lab.frame("DEMO-CDA")["keys"])
        self.lookup("93", "DEMO-MUN")
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual(frame["requests"]["count"], 1)
        self.assertEqual(frame["active"]["count"], 0)
        self.accept("DEMO-CDA", "A"); self.accept("DEMO-CDA", "#")
        self.accept("DEMO-CDA", "*"); self.accept("DEMO-CDA", "B")
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual(frame["active"]["movement_id"], "93-mun")
        self.assertNotIn("#", frame["keys"], "Approval is not arrival")
        self.accept("DEMO-MUN", "#")
        self.assertEqual(self.lab.frame("DEMO-CDA")["keys"]["#"]["label"], "Rapportera ankomst")

    def test_clear_departure_is_prioritized_over_waiting_outbound(self):
        self.lookup("17")
        self.ready("39", "DEMO-VA")
        self.accept("DEMO-CDA", "B")
        self.assertEqual(self.accept("DEMO-CDA", "B")["frame"]["active"]["movement_id"], "39-cda")
        frame = self.accept("DEMO-CDA", "D")["frame"]
        self.assertEqual(frame["active"]["movement_id"], "17-cda")
        self.assertNotIn("#", frame["keys"])
        self.assertIn("*", frame["keys"])

    def test_cd_from_overview_opens_active_train_but_hash_keeps_timetable(self):
        self.two_ready()
        self.assertEqual(self.accept("DEMO-CDA", "D")["frame"]["active"]["movement_id"], "17-cda")
        self.accept("DEMO-CDA", "B")
        self.assertEqual(self.accept("DEMO-CDA", "C")["frame"]["active"]["movement_id"], "39-cda")
        self.accept("DEMO-CDA", "B")
        frame = self.accept("DEMO-CDA", "#")["frame"]
        self.assertEqual(frame["keys"]["#"]["label"], "Välj tåg")
        self.assertIsNotNone(frame["upcoming"])

    def test_same_side_connections_are_all_reachable(self):
        self.lab.display_sides["cda"]["west"] = "right"
        self.two_ready()
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual(frame["active"]["count"], 2)
        self.assertIn("B:Akt2", frame["lines"][1])
        self.assertIn("17>MUN", self.accept("DEMO-CDA", "B")["frame"]["lines"][0])
        self.assertIn("39>VA", self.accept("DEMO-CDA", "D")["frame"]["lines"][0])

    def test_new_request_does_not_replace_active_selection_and_a_is_reachable(self):
        self.ready("39", "DEMO-VA")
        self.accept("DEMO-CDA", "B"); self.accept("DEMO-CDA", "B")
        self.lookup("93", "DEMO-MUN")
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual(frame["active"]["movement_id"], "39-cda")
        self.assertEqual(frame["requests"]["count"], 1)
        self.assertIn("93", self.accept("DEMO-CDA", "A")["frame"]["lines"][0])
        self.accept("DEMO-CDA", "B")
        self.assertIn("A:K1 B:Akt", self.lab.frame("DEMO-CDA")["lines"][1])
        self.assertEqual(self.accept("DEMO-CDA", "B")["frame"]["active"]["movement_id"], "39-cda")

    def test_withdrawal_can_be_aborted_and_does_not_change_other_train(self):
        self.two_ready(); self.accept("DEMO-CDA", "B")
        self.accept("DEMO-CDA", "*"); self.accept("DEMO-CDA", "*")
        self.assertEqual(self.lab.terminals["DEMO-CDA"].screen, "active")
        self.accept("DEMO-CDA", "*"); self.accept("DEMO-CDA", "#")
        self.assertEqual(self.lab.engine.connections["west"].state, State.FREE)
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)

    def test_other_box_withdrawal_never_retargets_hash(self):
        self.two_ready(); self.accept("DEMO-CDA", "B")
        self.lab.terminals["OTHER"] = deepcopy(self.lab.terminals["DEMO-CDA"])
        old = self.lab.frame("DEMO-CDA")
        self.accept("OTHER", "*"); self.accept("OTHER", "#")
        frame = self.lab.frame("DEMO-CDA")
        self.assertIn("LÄGET ÄNDRAT", frame["lines"][0])
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(frame["active"], {"count": 1, "position": 0, "movement_id": None})
        self.assertEqual(self.lab.command("DEMO-CDA", {"key": "#", "command_id": uuid4().hex, "view_token": old["view_token"]})["status"], "rejected")
        self.assertEqual(self.send("DEMO-CDA", "#")["status"], "rejected")
        self.assertEqual(self.accept("DEMO-CDA", "D")["frame"]["active"]["movement_id"], "39-cda")

    def test_direct_traffic_uses_same_active_navigation(self):
        self.lab = demo_lab("direct")
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.two_ready(); self.accept("DEMO-CDA", "B")
        self.assertEqual(self.lab.frame("DEMO-CDA")["keys"]["#"]["label"], "Rapportera avgång")
        self.assertEqual(self.lab.frame("DEMO-MUN")["requests"]["count"], 0)

    def test_all_languages_and_long_identities_preserve_clock_and_counter(self):
        self.two_ready()
        for language, _ in LANGUAGES:
            with self.subTest(language=language):
                self.lab.terminals["DEMO-CDA"].language = language
                self.accept("DEMO-CDA", "B")
                self.accept("DEMO-CDA", "D")
                self.accept("DEMO-CDA", "B")
        station = self.lab.engine.config.stations["mun"]
        self.lab.engine.config.stations["mun"] = replace(station, code="MUNKERÖDSTAD")
        for language, _ in LANGUAGES:
            self.lab.terminals["DEMO-CDA"].language = language
            frame = self.accept("DEMO-CDA", "B")["frame"]
            self.assertIn("MUNKERÖDSTAD<17", frame["lines"][0])
            self.assertIn("1/2", frame["lines"][1])
            self.accept("DEMO-CDA", "B")

    def test_large_counts_never_overflow_clock(self):
        for language, _ in LANGUAGES:
            self.lab.terminals["DEMO-CDA"].language = language
            with patch.object(self.lab, "_active_trains", return_value=["39-cda"] * 123):
                frame = self.lab.frame("DEMO-CDA")
                self.assertEqual(len(frame["lines"][1]), 16)
                self.assertTrue(frame["lines"][1].endswith("12:34"))
                self.assertIn("99+", frame["lines"][1])
                self.assertEqual(frame["active"]["count"], 123)
                with patch.object(self.lab, "_requests", return_value=["93-mun"] * 101):
                    self.assertEqual(len(self.lab.frame("DEMO-CDA")["lines"][1]), 16)
