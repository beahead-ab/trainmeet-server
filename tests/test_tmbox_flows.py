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
        self.assertEqual(11, len(self.data["flows"]))
        self.assertEqual("TÅG: 39___      ", self.lines("klarera", 0, "CDA")[0])
        self.assertEqual("CDA?39       1/1", self.lines("klarera", 2, "VA")[0])
        self.assertEqual("39 MOTTAGET     ", self.lines("klarera", 5, "CDA")[0])
        self.assertEqual(" " * 16, self.lines("klarera", -1, "CDA")[0])
        self.assertEqual("39 ANK SP2      ", self.lines("annat-spar", -1, "VA")[0])
        self.assertEqual("39 NEKAT        ", self.lines("neka", 1, "CDA")[0])
        self.assertEqual("CDA-39          ", self.lines("aterta-klartecken", 1, "VA")[0])
        self.assertEqual("2/2        94?VA", self.lines("tva-forfragningar", 4, "CDA")[0])
        self.assertEqual("MUN<17       1/2", self.lines("hitta-aktivt", 2, "CDA")[0])
        self.assertEqual("#Sändklar  12:34", self.lines("direkt", 0, "CDA")[1])
        self.assertEqual("INGA ANKOMSTER  ", self.lines("tidtabell", 2, "CDA")[0])
        self.assertEqual("EJ BEGÄRT ÄN    ", self.lines("nej", 0, "CDA")[0])
        self.assertEqual("INGET TÅG       ", self.lines("nej", 2, "CDA")[0])
        self.assertEqual("ANNAN SIDA      ", self.lines("tva-boxar", 0, "CDA-V")[0])
        self.assertEqual(" " * 16, self.lines("tva-boxar", -1, "CDA-V")[0])

    def test_a_key_the_engine_refuses_stops_the_generator(self):
        """A flow that no longer works must fail, not ship a picture of a refusal."""
        flow = flows.Flow("clearance", ["CDA", "VA"])
        with self.assertRaisesRegex(AssertionError, "VA \\*"):
            flow.press("VA", "*")  # the overview has nothing to go back from

    def test_digits_stay_in_the_box_until_hash(self):
        flow = flows.Flow("clearance", ["CDA"])
        flow.press("CDA", "394")
        self.assertEqual("TÅG: 394__      ", flow.lines("CDA")[0])
        self.assertEqual([], flow.lab.engine.audit)
        flow.press("CDA", "*")
        self.assertEqual(" " * 16, flow.lines("CDA")[0])


if __name__ == "__main__":
    unittest.main()
