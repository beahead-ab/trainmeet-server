"""The 20x4 box (trainmeet-tmbox #39), the station name on an idle box (#43) and
the sounds the server tells a box to make (#44).

The pictures are the ones in Benny's proposal (TMBox-20x4-forslag.pdf, 9
October 2026), drawn by the server's own engine for the bench's three stations:
Knastebo is the proposal's Charlottendal (CDA), Dimmeby its Vagnsta (VA) and
Sölvmora its Munkeröd (MUN). Rows one and four say what a 16x2 says; rows two
and three are the new ones. A 16x2 is not touched by any of this - the older
test files check every 16x2 picture, and the last test here checks that a box
which says nothing about its size is still one.
"""
from dataclasses import replace
import json
import unittest
from uuid import uuid4

from test_terminal16_runtime import _Boxes
from tmbox_gateway.device_ui import LANGUAGES
from tmbox_gateway.terminal16 import ATTENTION_KINDS, ATTENTION_WINDOW_SECONDS, NOTICE_SECONDS
from tmbox_gateway.terminal16_demo import demo_lab
from tmbox_gateway.terminal16_glyphs import GEOMETRIES, encode_lcd, fits_cgram
from tmbox_gateway.terminal16_i18n import text
from tmbox_gateway.terminal16_mqtt import Terminal16Gateway
from tmbox_gateway.terminal16_runtime import Terminal16Service

KNB, DY, SVM = "DEMO-KNB", "DEMO-DY", "DEMO-SVM"
BLANK = " " * 20


def fit(left="", right="", width=20):
    return left + " " * (width - len(left) - len(right)) + right


def idle(name, width=20):
    return (" " * ((width - len(name)) // 2) + name).ljust(width)


class TwentyByFourBench(unittest.TestCase):
    """The proposal's flows on the bench, every box a 20x4."""

    def setUp(self):
        self.lab = demo_lab()
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.t = 100.0
        self.lab.now = lambda: self.t
        for terminal in self.lab.terminals.values():
            terminal.rows, terminal.cols = 4, 20

    def press(self, device, key, **extra):
        frame = self.lab.frame(device)
        body = {"command_id": str(uuid4()), "view_token": frame["view_token"], "key": key, **extra}
        if "train_number" in extra:
            body["entry_context"] = frame["entry"]["context"]
        result = self.lab.command(device, body)
        self.assertEqual("accepted", result["status"], result)
        # Whatever the box shows, it is four complete rows of twenty.
        for shown in self.lab.frames():
            self.assertEqual([20] * 4, [len(line) for line in shown["lines"]], shown["lines"])
            self.assertEqual((4, 20), (shown["rows"], shown["cols"]))
            self.assertEqual([20] * 4, [len(cells) for cells in shown["lcd"]["cells"]])
        return result["frame"]

    def type(self, device, number):
        return self.press(device, "#", train_number=number)

    def lines(self, device):
        return self.lab.frame(device)["lines"]

    # ----------------------------------------------------------- start screen

    def test_an_idle_box_names_its_station_and_lists_the_two_nearest_trains(self):
        self.assertEqual([idle("Knastebo"), fit("17 AVG SVM", "12:35"), fit("39 AVG DY", "12:38"),
                          fit("Nr# A:Kö", "12:34")], self.lines(KNB))
        # An arrival is told from a departure by ANK; the earliest come first.
        self.assertEqual([idle("Dimmeby"), fit("94 AVG KNB", "12:44"), fit("39 ANK KNB", "12:46"),
                          fit("Nr# A:Kö", "12:34")], self.lines(DY))

    def test_the_entry_picture_keeps_the_digits_where_they_were(self):
        entry = self.lab.frame(KNB)["entry"]
        self.assertEqual([fit("TÅG: _____"), BLANK, BLANK, fit("#Sök B:Del", "12:34")], entry["lines"])
        self.assertEqual((0, 5, 5), (entry["row"], entry["column"], entry["max_length"]))
        self.assertEqual(4, len(entry["lcd"]["cells"]))

    # ------------------------------------------------- clearance, departure, arrival

    def test_a_train_from_request_to_arrival(self):
        self.type(KNB, "39")
        self.assertEqual([fit("", "39?DY"), fit("Till Dimmeby", "12:38"), fit("Väntar svar", "Sp 1"),
                          fit("*Åter B:Öv", "12:34")], self.lines(KNB))
        self.assertEqual([fit("KNB?39", "1/1"), fit("Från Knastebo", "12:46"), fit("Vill skicka", "Sp 1"),
                          fit("#Ja *Nej", "12:34")], self.lines(DY))
        self.press(DY, "#")
        self.assertEqual([fit("", "39>DY"), fit("Till Dimmeby", "12:38"), fit("Klar att avgå", "Sp 1"),
                          fit("#Avg *Åter", "12:34")], self.lines(KNB))
        self.assertEqual([fit("KNB>39"), fit("Från Knastebo", "12:46"), fit("Klarerad", "Sp 1"),
                          fit("C/D A:Kö", "12:34")], self.lines(DY))
        self.press(KNB, "#")
        self.assertEqual([fit("", "39▶DY"), fit("Till Dimmeby", "12:38"), fit("Avgått", "Sp 1"),
                          fit("C/D A:Kö", "12:34")], self.lines(KNB))
        self.assertEqual([fit("KNB▶39"), fit("Från Knastebo", "12:46"), fit("På väg hit", "Sp 1"),
                          fit("#In B:Sp", "12:34")], self.lines(DY))
        self.press(DY, "#")
        # The receipt names the other station in full on row two and leaves row three empty.
        self.assertEqual([fit("39 MOTTAGET"), fit("Dimmeby"), BLANK, fit("DY", "12:34")], self.lines(KNB))
        self.assertEqual([fit("39 ANK SP1"), fit("Knastebo"), BLANK, fit("KNB", "12:34")], self.lines(DY))
        # Five seconds on, the sender is back at its start screen; 39 is done there.
        self.t += 5
        self.assertEqual([idle("Knastebo"), fit("17 AVG SVM", "12:35"), fit("93 ANK SVM", "12:40"),
                          fit("Nr# A:Kö", "12:34")], self.lines(KNB))
        self.assertEqual([idle("Dimmeby"), fit("94 AVG KNB", "12:44"), BLANK, fit("Nr# A:Kö", "12:34")],
                         self.lines(DY))

    def test_a_refusal_and_a_withdrawal_name_the_other_station(self):
        self.type(KNB, "39")
        self.press(DY, "*")
        self.assertEqual([fit("NEKA 39?"), fit("Från Knastebo", "12:46"), fit("Vill skicka", "Sp 1"),
                          fit("#Ja *Nej", "12:34")], self.lines(DY))
        self.press(DY, "#")
        self.assertEqual([fit("39 NEKAT"), fit("Dimmeby"), BLANK, fit("DY", "12:34")], self.lines(KNB))
        self.assertEqual([fit("39 NEKAT"), fit("Knastebo"), BLANK, fit("KNB", "12:34")], self.lines(DY))
        self.t += NOTICE_SECONDS + .5
        self.assertEqual(idle("Knastebo"), self.lines(KNB)[0])
        self.type(KNB, "39")
        self.press(KNB, "*")
        self.assertEqual([fit("ÅTER 39?"), fit("Till Dimmeby", "12:38"), fit("Väntar svar", "Sp 1"),
                          fit("#Ja *Nej", "12:34")], self.lines(KNB))
        self.press(KNB, "#")
        self.assertEqual([fit("39 ÅTERTAGET"), fit("Dimmeby"), BLANK, fit("DY", "12:34")], self.lines(KNB))
        self.assertEqual([fit("39 ÅTERTAGET"), fit("Knastebo"), BLANK, fit("KNB", "12:34")], self.lines(DY))

    def test_a_notice_without_another_station_has_empty_rows_two_and_three(self):
        self.type(KNB, "123")
        self.assertEqual([fit("INGET TÅG"), BLANK, BLANK, fit("", "12:34")], self.lines(KNB))
        self.t += NOTICE_SECONDS + .5
        self.assertEqual(idle("Knastebo"), self.lines(KNB)[0])

    # ----------------------------------------------------------------- the lists

    def test_the_request_queue_counts_and_each_request_says_where_it_is_from(self):
        self.type(SVM, "93")
        self.type(DY, "94")
        self.assertEqual(2, self.lab.frame(KNB)["requests"]["count"])
        self.press(KNB, "A")
        self.assertEqual([fit("SVM?93", "1/2"), fit("Från Sölvmora", "12:40"), fit("Vill skicka", "Sp 1"),
                          fit("#Ja *Nej", "12:34")], self.lines(KNB))
        self.press(KNB, "D")
        self.assertEqual([fit("2/2", "94?DY"), fit("Från Dimmeby", "12:52"), fit("Vill skicka", "Sp 1"),
                          fit("#Ja *Nej", "12:34")], self.lines(KNB))

    def test_browsing_the_timetable_names_the_state_of_each_train(self):
        self.press(KNB, "#")
        self.assertEqual([fit("17 AVG", "12:35"), fit("Till Sölvmora", "12:35"), fit("Ej begärd", "Sp 1"),
                          fit("#Välj A:Kö", "12:34")], self.lines(KNB))
        self.press(KNB, "B")      # arrivals first
        self.assertEqual([fit("93 ANK", "12:40"), fit("Från Sölvmora", "12:40"), fit("Ej skickad", "Sp 1"),
                          fit("#Välj A:Kö", "12:34")], self.lines(KNB))

    def test_a_train_nobody_sent_is_moved_here_and_a_track_can_be_chosen(self):
        self.type(KNB, "93")
        self.assertEqual([fit("FLYTTA 93 HIT?"), fit("Från Sölvmora", "12:40"), fit("Ej skickad", "Sp 1"),
                          fit("#Ja B:Sp", "12:34")], self.lines(KNB))
        self.press(KNB, "B")
        # The track on row three is the planned one; the chosen one is on row one.
        self.assertEqual([fit("93 SPÅR 1"), fit("Från Sölvmora", "12:40"), fit("Ej skickad", "Sp 1"),
                          fit("#In C/D:Sp", "12:34")], self.lines(KNB))
        self.press(KNB, "D")
        self.assertEqual(fit("93 SPÅR 2"), self.lines(KNB)[0])
        self.press(KNB, "#")
        self.assertEqual([fit("93 ANK SP2"), fit("Sölvmora"), BLANK, fit("SVM", "12:34")], self.lines(KNB))

    # --------------------------------------------- the eight places for own characters

    def swap_station_name(self, station, name):
        stations = self.lab.engine.config.stations
        stations[station] = replace(stations[station], name=name)

    def test_rows_two_and_three_give_way_when_a_picture_needs_more_than_eight_own_characters(self):
        self.swap_station_name("dy", "ÅåÄäÖöÆæØ")       # nine own characters in one name
        self.type(KNB, "39")
        shown = self.lines(KNB)
        self.assertEqual([fit("", "39?DY"), BLANK, BLANK, fit("*Åter B:Öv", "12:34")], shown)
        # Rows one and four are always shown, and the picture still encodes.
        self.assertEqual(4, len(encode_lcd(shown)["cells"]))

    def test_rows_two_and_three_give_way_for_a_character_the_box_cannot_draw(self):
        self.swap_station_name("dy", "Kraków")
        self.type(KNB, "39")
        self.assertEqual([fit("", "39?DY"), BLANK, BLANK, fit("*Åter B:Öv", "12:34")], self.lines(KNB))

    def test_eight_own_characters_still_fit(self):
        self.swap_station_name("dy", "ÅåÄäÖöÆæ")
        self.type(KNB, "39")
        self.assertEqual(fit("Till ÅåÄäÖöÆæ", "12:38"), self.lines(KNB)[1])

    def test_a_name_too_long_for_the_row_is_cut_but_never_the_time(self):
        self.swap_station_name("dy", "Dimmeby Norra Hållplats")
        self.type(KNB, "39")
        self.assertEqual(fit("Dimmeby Norra", "12:38"), self.lines(KNB)[1])     # "Till " gives way, then the name
        self.assertEqual(fit("Väntar svar", "Sp 1"), self.lines(KNB)[2])

    def test_a_title_the_box_cannot_draw_gives_the_code_on_both_sizes(self):
        """A name with a character the box has no place for must never stop the
        box from showing a picture: the code takes its place."""
        self.swap_station_name("knb", "Kraków")
        self.assertEqual(idle("KNB"), self.lines(KNB)[0])
        for terminal in self.lab.terminals.values():
            terminal.rows, terminal.cols = 2, 16
        self.assertEqual(idle("KNB", 16), self.lines(KNB)[0])
        self.swap_station_name("knb", "ÅåÄäÖöÆæØ")
        self.assertEqual(idle("KNB", 16), self.lines(KNB)[0])
        self.assertEqual(["    Dimmeby     ", "Nr# A:Kö   12:34"], self.lines(DY), "other stations are not affected")

    def test_a_station_name_too_long_for_the_title_gives_the_code(self):
        self.swap_station_name("knb", "Knastebo Södra Godsbangård")
        self.assertEqual(idle("KNB"), self.lines(KNB)[0])

    # ------------------------------------------------------------- every language

    def test_every_word_fits_beside_a_two_digit_track_in_every_language(self):
        words = ("Vill skicka", "Väntar svar", "Klar att avgå", "Klarerad", "Avgått", "På väg hit", "Ej begärd", "Ej skickad")
        for code, _ in LANGUAGES:
            track = text(code, "Sp {track}", track="99")
            for word in words:
                translated = text(code, word)
                self.assertLessEqual(len(translated) + 1 + len(track), 20, (code, translated))
            for word in ("Till", "Från"):
                self.assertTrue(text(code, word), (code, word))

    def test_the_proposals_last_page_in_every_language(self):
        expected = {
            "sv": ("Till Dimmeby", "Väntar svar", "Sp 1", "Vill skicka"),
            "en": ("To Dimmeby", "Awaiting OK", "Trk 1", "Asks to send"),
            "da": ("Til Dimmeby", "Venter svar", "Sp 1", "Vil sende"),
            "nb": ("Til Dimmeby", "Venter svar", "Sp 1", "Vil sende"),
            "de": ("Nach Dimmeby", "Wartet Antw.", "Gl 1", "Will senden"),
        }
        for code, (to, waiting, track, asks) in expected.items():
            self.setUp()
            for terminal in self.lab.terminals.values():
                terminal.language = code
            self.type(KNB, "39")
            sender, receiver = self.lines(KNB), self.lines(DY)
            self.assertEqual(fit(to, "12:38"), sender[1], code)
            self.assertEqual(fit(waiting, track), sender[2], code)
            self.assertEqual(fit(asks, track), receiver[2], code)
            for line in sender + receiver:
                self.assertEqual(20, len(line), (code, line))
            for line_set in (sender, receiver):
                self.assertTrue(fits_cgram(line_set), (code, line_set))


class AttentionBench(unittest.TestCase):
    """Who is told to sound what, on the bench's three stations (#44)."""

    def setUp(self):
        self.lab = demo_lab()
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.t = 100.0
        self.lab.now = lambda: self.t

    def press(self, device, key, **extra):
        frame = self.lab.frame(device)
        body = {"command_id": str(uuid4()), "view_token": frame["view_token"], "key": key, **extra}
        if "train_number" in extra:
            body["entry_context"] = frame["entry"]["context"]
        result = self.lab.command(device, body)
        self.assertEqual("accepted", result["status"], result)

    def attention(self, device):
        frame = self.lab.frame(device)["attention"]
        return frame["kind"], frame["seq"]

    def test_a_new_box_has_nothing_to_sound_and_says_which_series_it_is_in(self):
        for device in (KNB, DY, SVM):
            frame = self.lab.frame(device)
            self.assertEqual((None, 0), (frame["attention"]["kind"], frame["attention"]["seq"]))
            self.assertEqual(self.lab.epoch, frame["attention"]["epoch"])

    def test_the_receiver_hears_a_request_and_the_sender_hears_the_answer_and_nobody_hears_themselves(self):
        self.press(KNB, "#", train_number="39")
        kind, seq = self.attention(DY)
        self.assertEqual("incoming_request", kind)
        self.assertGreater(seq, 0)
        self.assertEqual((None, 0), self.attention(KNB), "the sender's own request is silent")
        self.assertEqual((None, 0), self.attention(SVM), "a box at another station hears nothing")
        self.t += ATTENTION_WINDOW_SECONDS + 1
        self.press(DY, "#")
        self.assertEqual("request_approved", self.attention(KNB)[0])
        self.assertEqual((kind, seq), self.attention(DY), "the one who answered hears nothing new")
        self.t += ATTENTION_WINDOW_SECONDS + 1
        before = self.attention(KNB)
        self.press(KNB, "#")
        self.assertEqual("incoming_train", self.attention(DY)[0])
        self.assertEqual(before, self.attention(KNB), "the sender's own departure is silent")

    def test_a_refusal_is_a_denial_for_the_one_who_asked(self):
        self.press(KNB, "#", train_number="39")
        self.press(DY, "*")
        self.press(DY, "#")
        self.assertEqual("request_denied", self.attention(KNB)[0])

    def test_a_withdrawal_and_an_arrival_make_no_sound(self):
        self.press(KNB, "#", train_number="39")
        _, seq = self.attention(DY)
        self.press(KNB, "*")
        self.press(KNB, "#")
        self.assertEqual(seq, self.attention(DY)[1])
        self.assertEqual((None, 0), self.attention(KNB))

    def test_each_sound_gets_a_new_number_taken_from_one_series_for_all_boxes(self):
        self.press(SVM, "#", train_number="93")
        first = self.attention(KNB)[1]
        self.t += ATTENTION_WINDOW_SECONDS + 1
        self.press(DY, "#", train_number="94")
        second = self.attention(KNB)[1]
        self.assertGreater(second, first, "a second request to the same box is a new sound")
        self.t += ATTENTION_WINDOW_SECONDS + 1
        self.press(KNB, "A")      # the queue opens at the first request, 93 from Sölvmora
        self.press(KNB, "#")
        kind, seq = self.attention(SVM)
        self.assertEqual("request_approved", kind)
        self.assertGreater(seq, second, "the series runs across boxes, so a number is never reused")
        self.assertEqual(second, self.attention(KNB)[1], "answering is silent for the one who answers")

    def test_a_less_important_sound_right_after_a_more_important_one_is_dropped(self):
        self.press(KNB, "#", train_number="39")
        self.press(DY, "#")
        self.press(KNB, "#")          # departs in the same breath as the request came
        kind, seq = self.attention(DY)
        self.assertEqual("incoming_request", kind, "the more important one is the one heard")
        self.t += ATTENTION_WINDOW_SECONDS + 1
        # ... but a more important one is never held back, and the same kind plays again.
        self.press(SVM, "#", train_number="93")
        self.assertEqual("incoming_request", self.attention(KNB)[0])

    def test_the_kinds_are_the_ones_the_firmware_and_the_protocol_know(self):
        self.assertEqual(("incoming_request", "request_denied", "request_approved", "incoming_train"), ATTENTION_KINDS)


class RealBoxes(_Boxes):
    """A 20x4 announces itself in its hello; the server then draws it so, and tells it what to sound."""

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.terminals = Terminal16Service(self.service, now=lambda: self.now)
        self.messages = []
        self.gateway = Terminal16Gateway(self.terminals, lambda *args: self.messages.append(args))

    def hello(self, device, display=None):
        body = {"boot": device + "-1", "device_code": device}
        if display is not None:
            body["display"] = display
        self.gateway.on_message(self.gateway.PREFIX + device + "/hello", json.dumps(body).encode())

    def frames(self, device):
        return [m[1]["frame"] for m in self.messages if m[0] == self.gateway.PREFIX + device + "/frame"]

    def test_a_box_that_says_nothing_about_its_size_is_a_16x2(self):
        self.hello("esp8266")
        frame = self.frames("esp8266")[-1]
        self.assertEqual((2, 16), (frame["rows"], frame["cols"]))
        self.assertEqual(2, len(frame["lines"]))

    def test_the_announced_size_is_kept_and_drawn(self):
        self.hello("esp8266", {"rows": 4, "cols": 20})
        frame = self.frames("esp8266")[-1]
        self.assertEqual((4, 20), (frame["rows"], frame["cols"]))
        self.assertEqual([20] * 4, [len(line) for line in frame["lines"]])
        self.assertEqual(4, len(frame["lcd"]["cells"]))
        info = self.service.identities.discovered_device_or_none("esp8266")
        self.assertEqual((4, 20, "cgram"), (info.display.rows, info.display.cols, info.display.charset))

    def test_a_size_that_is_not_known_is_a_16x2_and_the_box_is_not_turned_away(self):
        for odd in ({"rows": 4, "cols": 16}, {"rows": 1, "cols": 8}, {"rows": "4", "cols": "20"}, "4x20", [4, 20]):
            self.messages.clear()
            self.hello("esp8266", odd)
            frame = self.frames("esp8266")[-1]
            self.assertEqual((2, 16), (frame["rows"], frame["cols"]), odd)
        self.assertIn((4, 20), GEOMETRIES)

    def test_a_20x4_waiting_for_a_station_says_so_on_four_rows(self):
        self.service.identities.record_discovery("fresh", "FRESHCODE", protocol_version=2,
                                                 display=self.gateway._display({"rows": 4, "cols": 20}))
        frame = self.terminals.frame("fresh")
        self.assertEqual((4, 20), (frame["rows"], frame["cols"]))
        self.assertEqual(["VÄNTAR PÅ ADMIN".ljust(20), "FRESHCODE".ljust(20), BLANK, BLANK], frame["lines"])

    def test_a_20x4_at_the_stations_of_the_fixture_shows_the_station_and_the_trains(self):
        self.hello("esp8266", {"rows": 4, "cols": 20})
        lines = self.frames("esp8266")[-1]["lines"]
        self.assertEqual(idle("Charlottendahl"), lines[0])
        self.assertTrue(lines[3].startswith("Nr# A:Kö"), lines)

    def command(self, device, frame, **body):
        self.gateway.on_message(self.gateway.PREFIX + device + "/command", json.dumps({
            "boot": device + "-1", "view_token": frame["view_token"], **body}).encode())

    def test_the_sounds_travel_to_the_right_boxes_over_mqtt_and_the_first_picture_is_silent(self):
        self.hello("esp8266", {"rows": 4, "cols": 20})
        self.hello("esp32")
        first = self.frames("esp32")[-1]["attention"]
        self.assertEqual((None, 0), (first["kind"], first["seq"]), "nothing from before the box connected")
        sender = self.frames("esp8266")[-1]
        self.command("esp8266", sender, command_id="ask", key="#", train_number="101",
                     entry_context=sender["entry"]["context"])
        heard = self.frames("esp32")[-1]["attention"]
        self.assertEqual("incoming_request", heard["kind"])
        self.assertNotEqual(first["seq"], heard["seq"])
        self.assertEqual(first["epoch"], heard["epoch"])
        silent = self.frames("esp8266")[-1]["attention"]
        self.assertEqual((None, 0), (silent["kind"], silent["seq"]), "the box that asked is not told about its own request")
        # The receiver answers; the one who asked hears the answer.
        self.now += ATTENTION_WINDOW_SECONDS + 1
        self.command("esp32", self.frames("esp32")[-1], command_id="clear", key="#")
        approved = self.frames("esp8266")[-1]["attention"]
        self.assertEqual("request_approved", approved["kind"])
        self.assertEqual(heard, self.frames("esp32")[-1]["attention"], "the one who answered hears nothing new")
        # A box that connects again is given the same numbers: it plays nothing it already had.
        self.hello("esp8266", {"rows": 4, "cols": 20})
        self.assertEqual(approved, self.frames("esp8266")[-1]["attention"])


if __name__ == "__main__":
    unittest.main()
