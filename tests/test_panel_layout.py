"""Cloud's column layout and the older row layout must not be confused."""
from copy import deepcopy
import unittest

from runtime_fixture import runtime_package_v3
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.runtime import RuntimePublication, RuntimePublicationError


class PanelLayoutTests(unittest.TestCase):
    def test_all_four_ports_keep_their_published_position_and_empty_slots(self):
        for layout, positions in (
            ("rows", ((1, "left"), (1, "right"), (2, "left"), (2, "right"))),
            ("columns", ((1, "left"), (2, "left"), (1, "right"), (2, "right"))),
        ):
            for key, (row, side) in zip("ABCD", positions):
                with self.subTest(layout=layout, key=key):
                    package = deepcopy(runtime_package_v3())
                    package["panels"][0].update(slot_layout=layout, slots={port: "connection-a-b" if port == key else None for port in "ABCD"})
                    engine = TrafficEngine(RuntimePublication.parse(package).session_config())
                    snapshot = engine.snapshot("panel-a")
                    self.assertEqual((snapshot["slots"][key]["row"], snapshot["slots"][key]["side"]), (row, side))
                    line = snapshot["display"][f"line{row}"]
                    code = snapshot["slots"][key]["station_code"]
                    self.assertTrue(line.startswith(f"{key}<{code}") if side == "left" else line.endswith(f"{code}>{key}"), line)
                    self.assertEqual(sum(slot["state"] != "unused" for slot in snapshot["slots"].values()), 1)

    def test_old_package_keeps_row_layout_and_unknown_layout_is_rejected(self):
        package = runtime_package_v3()
        panel = RuntimePublication.parse(package).session_config().panels["panel-a"]
        self.assertEqual(panel.slot_position("B"), (1, "right"))
        package["panels"][0]["slot_layout"] = "guess"
        with self.assertRaises(RuntimePublicationError):
            RuntimePublication.parse(package)
