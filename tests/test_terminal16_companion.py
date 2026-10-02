"""iPhone TMBox: stationens tidtabell bredvid boxen och korta ord under tangenterna.

Casper (2026-10-02) valde iPhone-appen som en TKL-companion: svart, stationens
tidtabell ovanför den blå displayen och en knappsats i iPhones stil, där varje
funktionstangent har ett kort ord under sig. Servern bestämmer orden och
tidtabellens läge, precis som den bestämmer bilden; appen tolkar ingenting.
"""
from uuid import uuid4
import unittest

import test_shared_traffic
from tmbox_gateway.terminal16 import SHORT_LABELS
from tmbox_gateway.terminal16_demo import demo_lab
from tmbox_gateway.terminal16_i18n import MESSAGES
from tmbox_gateway.terminal16_runtime import Terminal16Service

TRANSLATED = ("sv", "en", "da", "nb", "de")


class CompanionLabTests(unittest.TestCase):
    def setUp(self):
        self.lab = demo_lab()
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.seen = []

    def send(self, device, key, **extra):
        frame = self.lab.frame(device)
        if "train_number" in extra:
            extra["entry_context"] = frame["entry"]["context"]
        result = self.lab.command(device, {"command_id": str(uuid4()), "view_token": frame["view_token"], "key": key, **extra})
        self.assertEqual(result["status"], "accepted", result)
        self.seen.extend(self.lab.frames())
        return result

    def states(self, device):
        return {(row["train_number"], row["kind"]): row["state"] for row in self.lab.station_timetable(device)["rows"]}

    def test_rows_are_the_stations_trains_in_timetable_order(self):
        table = self.lab.station_timetable("DEMO-CDA")
        self.assertEqual(table["station"], {"code": "CDA", "name": "Charlottendal"})
        self.assertEqual((table["side"], table["clock"]), ("both", "12:34"))
        self.assertEqual([(r["train_number"], r["kind"], r["time"], r["station"]["code"]) for r in table["rows"]],
                         [("17", "departure", "12:35", "MUN"), ("39", "departure", "12:38", "VA"),
                          ("93", "arrival", "12:40", "MUN"), ("94", "arrival", "12:52", "VA")])
        self.assertEqual({r["state"] for r in table["rows"]}, {"planned"})
        self.assertEqual({r["selected"] for r in table["rows"]}, {False})
        self.assertEqual(self.lab.engine.audit, [], "reading the timetable changes nothing")

    def test_state_follows_the_train_from_request_to_arrival_at_both_ends(self):
        self.send("DEMO-CDA", "#", train_number="39")  # 39# begär direkt (Server 2.1)
        self.assertEqual(self.states("DEMO-CDA")[("39", "departure")], "requested")
        self.assertEqual(self.states("DEMO-VA")[("39", "arrival")], "requested")
        selected = [r["train_number"] for r in self.lab.station_timetable("DEMO-CDA")["rows"] if r["selected"]]
        self.assertEqual(selected, ["39"])
        self.send("DEMO-VA", "#", train_number="39")
        self.send("DEMO-VA", "#")
        self.assertEqual(self.states("DEMO-CDA")[("39", "departure")], "cleared")
        self.send("DEMO-CDA", "#")
        self.assertEqual(self.states("DEMO-VA")[("39", "arrival")], "departed")
        self.send("DEMO-VA", "#")
        self.assertEqual(self.states("DEMO-CDA")[("39", "departure")], "arrived")
        self.assertEqual(self.states("DEMO-VA")[("39", "arrival")], "arrived")
        self.assertEqual(self.states("DEMO-CDA")[("17", "departure")], "planned")

    def test_a_box_on_one_side_sees_only_that_sides_trains(self):
        self.lab.terminals["DEMO-CDA"].side = "left"
        rows = self.lab.station_timetable("DEMO-CDA")["rows"]
        self.assertEqual([r["train_number"] for r in rows], ["17", "93"])
        self.assertEqual({r["side"] for r in rows}, {"left"})

    def test_every_key_on_every_screen_has_a_short_word(self):
        self.seen.extend(self.lab.frames())
        self.send("DEMO-CDA", "D")
        self.send("DEMO-CDA", "B")
        self.send("DEMO-CDA", "*")
        self.send("DEMO-CDA", "#", train_number="39")
        self.send("DEMO-CDA", "*")
        self.send("DEMO-CDA", "*")
        self.send("DEMO-VA", "#", train_number="39")
        self.send("DEMO-VA", "#")
        self.send("DEMO-CDA", "#")
        self.send("DEMO-VA", "B")
        self.send("DEMO-VA", "D")
        self.send("DEMO-VA", "#")
        for frame in self.seen:
            for key, info in frame["keys"].items():
                self.assertTrue(info["short"], (frame["lines"], key, info["label"]))
            self.assertEqual(frame["entry"]["short"], {"#": "SÖK", "*": "AVBRYT", "B": "SUDDA", "A": "KÖ"})

    def test_queue_and_active_words_carry_their_counts(self):
        self.assertEqual(self.lab.frame("DEMO-VA")["keys"]["A"]["short"], "KÖ")
        self.send("DEMO-CDA", "#", train_number="39")
        self.assertEqual(self.lab.frame("DEMO-VA")["keys"]["A"]["short"], "KÖ 1")
        self.send("DEMO-CDA", "B")  # back to the overview, where B lists the active trains
        self.assertEqual(self.lab.frame("DEMO-CDA")["keys"]["B"]["short"], "AKTIVA 1")

    def test_short_words_follow_the_boxs_language(self):
        self.lab.terminals["DEMO-CDA"].language = "en"
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual(frame["keys"]["A"]["short"], "QUEUE")
        self.assertEqual(frame["entry"]["short"]["B"], "ERASE")

    def test_every_short_word_is_translated_and_fits_under_a_key(self):
        words = set(SHORT_LABELS.values()) | {"KÖ", "AKTIVA", "SÖK", "AVBRYT", "SUDDA"}
        for word in words:
            for language in TRANSLATED:
                self.assertIn(word, MESSAGES[language], (language, word))
                self.assertLessEqual(len(MESSAGES[language][word]), 12, (language, word))


class CompanionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.terminals = Terminal16Service(self.fixture.service)

    def tearDown(self):
        self.fixture.tearDown()

    def test_unassigned_box_gets_an_empty_timetable(self):
        self.assertEqual(self.terminals.timetable("nobody"), {"station": None, "side": None, "clock": None, "revision": 0, "rows": []})

    def test_live_timetable_reads_the_shared_cases(self):
        before = {r["train_number"]: r["state"] for r in self.terminals.timetable("esp8266")["rows"] if r["kind"] == "departure"}
        self.assertEqual(before["101"], "planned")
        frame = self.terminals.frame("esp8266")
        body = {"key": "#", "train_number": "101", "entry_context": frame["entry"]["context"]}  # 101# begär direkt
        answer = self.terminals.command("esp8266", {"command_id": uuid4().hex, "view_token": frame["view_token"], **body})
        self.assertEqual(answer["status"], "accepted", answer)
        sender = {r["train_number"]: r["state"] for r in self.terminals.timetable("esp8266")["rows"] if r["kind"] == "departure"}
        receiver = {r["train_number"]: r["state"] for r in self.terminals.timetable("esp32")["rows"] if r["kind"] == "arrival"}
        self.assertEqual((sender["101"], receiver["101"]), ("requested", "requested"))


if __name__ == "__main__":
    unittest.main()
