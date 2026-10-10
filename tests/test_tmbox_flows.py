"""The flows page shows what the 16x2 engine does, not what someone remembers."""
import importlib.util
from pathlib import Path
import unittest

from tmbox_gateway.terminal16_glyphs import text_cells

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tmbox_flows", ROOT / "scripts/tmbox_flows.py")
flows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flows)


class TMBoxFlowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = flows.build_flows()
        cls.by_id = {flow["id"]: flow for flow in cls.data["flows"]}

    def test_the_shipped_page_is_what_the_engine_shows_today(self):
        self.assertEqual(flows.browser_source(),
                         (ROOT / "src/tmbox_gateway/terminal16_web/flows.js").read_text(),
                         "Kör: PYTHONPATH=src python3 scripts/tmbox_flows.py > src/tmbox_gateway/terminal16_web/flows.js")

    def test_every_picture_is_a_whole_16_by_2_display(self):
        for flow in self.data["flows"]:
            for step in [{"screens": flow["start"]}] + flow["steps"]:
                for shown in step["screens"]:
                    self.assertEqual([16, 16], [len(text_cells(line)) for line in shown["lines"]], (flow["id"], shown))
                    self.assertTrue(shown["lines"][1].endswith("12:34"), (flow["id"], shown))

    def test_each_step_shows_every_box_of_its_flow_and_marks_what_changed(self):
        for flow in self.data["flows"]:
            boxes = [box["id"] for box in flow["boxes"]]
            previous = {shown["box"]: shown["lines"] for shown in flow["start"]}
            for step in flow["steps"]:
                self.assertEqual(boxes, [shown["box"] for shown in step["screens"]], flow["id"])
                for shown in step["screens"]:
                    self.assertEqual(shown["changed"], shown["lines"] != previous[shown["box"]], (flow["id"], step["caption"]))
                    previous[shown["box"]] = shown["lines"]
                if step["box"]:
                    self.assertIn(step["box"], boxes)
                    self.assertTrue(step["meanings"], step["caption"])
                    self.assertTrue(set(key for key, _ in step["meanings"]) <= set("#*ABCD"))

    def lines(self, flow, step, box):
        return next(shown["lines"] for shown in self.by_id[flow]["steps"][step]["screens"] if shown["box"] == box)

    def test_the_flows_say_what_casper_asked_for(self):
        """Klarera, ta emot, hitta ett klarerat tåg - and the ways a box says no."""
        self.assertEqual(14, len(self.data["flows"]))
        self.assertEqual("TÅG: 39___      ", self.lines("klarera", 0, "KNB")[0])
        # 39# finds the train and asks Dimmeby in one press (Casper, 2026-10-02).
        self.assertEqual(["           39?DY", "*Åter B:Öv 12:34"], self.lines("klarera", 1, "KNB"))
        self.assertEqual("KNB?39       1/1", self.lines("klarera", 1, "DY")[0])
        self.assertEqual("39 MOTTAGET     ", self.lines("klarera", 4, "KNB")[0])
        self.assertEqual(" " * 16, self.lines("klarera", -1, "KNB")[0])
        self.assertEqual("39 ANK SP2      ", self.lines("annat-spar", -1, "DY")[0])
        self.assertEqual("39 NEKAT        ", self.lines("neka", 1, "KNB")[0])
        # Taken back, both stations are told and neither waits for #OK (Benny, 2026-10-03).
        self.assertEqual(["39 ÅTERTAGET    ", "KNB        12:34"], self.lines("aterta-klartecken", 1, "DY"))
        self.assertEqual(["39 ÅTERTAGET    ", "DY         12:34"], self.lines("aterta-klartecken", 1, "KNB"))
        self.assertEqual(["39 ÅTERTAGET    ", "KNB        12:34"], self.lines("aterta-begaran", 1, "DY"))
        self.assertEqual(" " * 16, self.lines("aterta-begaran", -1, "DY")[0])
        self.assertEqual("2/2        94?DY", self.lines("tva-forfragningar", 2, "KNB")[0])
        self.assertEqual("SVM<17       1/2", self.lines("hitta-aktivt", 2, "KNB")[0])
        self.assertEqual(["           39>DY", "#Avg *Åter 12:34"], self.lines("direkt", 0, "KNB"))
        # The timetable has the arrivals nobody has sent yet (2.1.0).
        self.assertEqual("93 ANK     12:40", self.lines("tidtabell", 2, "KNB")[0])
        self.assertEqual("INGET TÅG       ", self.lines("nej", 0, "KNB")[0])
        # A through train in the usual order: the arrival first, then the departure.
        self.assertEqual("1/1        55?DY", self.lines("genomgaende", 0, "KNB")[0])
        self.assertEqual("55 ANK SP2      ", self.lines("genomgaende", 3, "KNB")[0])
        self.assertEqual(["SVM?55          ", "*Åter B:Öv 12:34"], self.lines("genomgaende", 5, "KNB"))
        self.assertEqual(["          55◀KNB", "#In B:Sp   12:34"], self.lines("genomgaende", -1, "SVM"))
        # Never sent from DY: a through train is moved here first, then sent
        # on (Benny #170); nothing is asked of SVM until it is here.
        self.assertEqual(["FLYTTA 55 HIT?  ", "#Ja B:Sp   12:34"], self.lines("flytta-genomgaende", 0, "KNB"))
        self.assertEqual(" " * 16, self.lines("flytta-genomgaende", 0, "SVM")[0])
        self.assertEqual("55 MOTTAGET     ", self.lines("flytta-genomgaende", 1, "DY")[0], "DY is told, and has nothing to send")
        self.assertEqual(" " * 16, self.lines("flytta-genomgaende", -1, "DY")[0])
        self.assertEqual(["SVM?55          ", "*Åter B:Öv 12:34"], self.lines("flytta-genomgaende", 3, "KNB"))
        self.assertEqual(["          55◀KNB", "#In B:Sp   12:34"], self.lines("flytta-genomgaende", -1, "SVM"))
        # Never sent from SVM: the box asks to move it here (issue #115).
        self.assertEqual(["FLYTTA 93 HIT?  ", "#Ja B:Sp   12:34"], self.lines("placera", 0, "KNB"))
        self.assertEqual("93 ANK SP2      ", self.lines("placera", -1, "KNB")[0])
        self.assertEqual("ANNAN SIDA      ", self.lines("tva-boxar", 0, "KNB-V")[0])
        self.assertEqual(" " * 16, self.lines("tva-boxar", -1, "KNB-V")[0])

    def test_a_key_the_engine_refuses_stops_the_generator(self):
        """A flow that no longer works must fail, not ship a picture of a refusal."""
        flow = flows.Flow("clearance", ["KNB", "DY"])
        with self.assertRaisesRegex(AssertionError, "DY \\*"):
            flow.press("DY", "*")  # the overview has nothing to go back from

    def test_digits_stay_in_the_box_until_hash(self):
        flow = flows.Flow("clearance", ["KNB"])
        flow.press("KNB", "394")
        self.assertEqual("TÅG: 394__      ", flow.lines("KNB")[0])
        self.assertEqual([], flow.lab.engine.audit)
        flow.press("KNB", "*")
        self.assertEqual(" " * 16, flow.lines("KNB")[0])


if __name__ == "__main__":
    unittest.main()
