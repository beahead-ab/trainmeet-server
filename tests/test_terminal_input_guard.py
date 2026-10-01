"""Vilka tangenter klienten får spärra efter ett skärmbyte.

Boxen och webbklienten spärrade förut alla tangenter i en halv sekund efter
varje kommando, även när man bara bläddrade med C och D. Det var den största
delen av det som kändes segt i /tmbox/: servern svarade på några millisekunder,
klienten väntade sedan själv 500 ms till.

Spärren finns för att ett tryck avsett för förra bilden inte ska utföra något
på den nya. Det gäller bara tangenter som ändrar trafiken. Servern vet vilka det
är och märker dem "acts" i bilden; klienterna spärrar bara dem. Märkningen får
därför aldrig ljuga åt det farliga hållet: en tangent märkt som ren navigering
får aldrig ändra trafiken.
"""
import random
import unittest
from uuid import uuid4

from tmbox_gateway.terminal16 import NAVIGATION_ACTIONS, Terminal
from tmbox_gateway.terminal16_demo import demo_lab


TRAFFIC_ACTIONS = {"request", "accept", "reject", "cancel", "depart", "arrive", "arrive_track"}
# Reached only through a few exact paths, so they are pressed on purpose in
# the scripted tests below rather than hoped for in the random walk.
SCRIPTED = {"reject_view", "next_request", "previous_request", "next_track", "previous_track"}


class KeysSayWhetherTheyActTests(unittest.TestCase):
    def setUp(self):
        self.lab = demo_lab()
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})
        self.clock = 0
        self.lab.now = lambda: self.clock
        self.traffic = []
        original = self.lab._traffic

        def traffic(device, terminal, action):
            self.traffic.append((device, action))
            return original(device, terminal, action)

        self.lab._traffic = traffic

    def send(self, device, key, **extra):
        return self.lab.command(device, {"command_id": str(uuid4()), "view_token": self.lab.frame(device)["view_token"],
                                         "key": key, **extra})

    def press(self, device, key):
        """Tryck och kontrollera att märkningen höll. Returnerar åtgärden."""

        frame = self.lab.frame(device)
        buttons = self.lab._buttons(self.lab.terminals[device])
        self.assertEqual(set(frame["keys"]), set(buttons))
        action, acts = buttons[key][0], frame["keys"][key]["acts"]
        self.assertIsInstance(acts, bool, (device, key, action))
        self.assertEqual(acts, action not in NAVIGATION_ACTIONS, action)
        revision, calls = self.lab.engine.revision, len(self.traffic)
        self.send(device, key)
        if acts:
            self.assertEqual(len(self.traffic), calls + 1, f"{action} märkt som åtgärd men nådde inte trafiken")
        else:
            self.assertEqual(len(self.traffic), calls, f"{action} märkt som navigering men nådde trafiken")
            self.assertEqual(self.lab.engine.revision, revision, f"{action} märkt som navigering men ändrade trafiken")
        return action

    def lookup(self, device, number):
        frame = self.lab.frame(device)
        self.assertEqual("accepted", self.send(device, "#", train_number=number, entry_context=frame["entry"]["context"])["status"])

    def test_every_offered_key_is_marked_and_the_mark_holds(self):
        """En lång slumpvandring genom alla skärmar på alla boxar. Varje
        erbjuden tangent trycks någon gång; den som är märkt som navigering
        får aldrig nå trafiken, den som är märkt som åtgärd ska alltid göra det."""

        seen = self.walk(20261001)
        self.assertLessEqual(seen, NAVIGATION_ACTIONS | TRAFFIC_ACTIONS, seen - NAVIGATION_ACTIONS - TRAFFIC_ACTIONS)
        # Vandringen ska ha sett allt utom det som provas skriptat nedan,
        # annars bevisar den för lite.
        self.assertEqual(TRAFFIC_ACTIONS - {"reject"}, seen & TRAFFIC_ACTIONS - {"reject"})
        self.assertLessEqual(NAVIGATION_ACTIONS - seen, SCRIPTED)

    def walk(self, seed, after_each=lambda: None):
        walk = random.Random(seed)
        numbers = sorted({leg["train_number"] for leg in self.lab.legs.values()})
        devices = sorted(self.lab.terminals)
        seen = set()
        for step in range(4000):
            device = walk.choice(devices)
            if walk.random() < 0.08:
                frame = self.lab.frame(device)
                self.send(device, "#", train_number=walk.choice(numbers), entry_context=frame["entry"]["context"])
                after_each()
                continue
            if walk.random() < 0.05:
                self.clock += 4  # låter kvitton och meddelanden gå ut
            keys = sorted(self.lab.frame(device)["keys"])
            # '#' oftare: annars händer nästan ingen trafik.
            seen.add(self.press(device, "#" if "#" in keys and walk.random() < 0.5 else walk.choice(keys)))
            after_each()
        return seen

    def test_boxes_on_one_side_keep_to_their_side(self):
        """Samma vandring med två extra boxar på CDA, en för varje sida.
        Märkningen ska hålla, trafiken ska ändå gå runt, och en box på ena
        sidan får aldrig ha ett tåg från andra sidan valt."""

        lab = self.lab
        lab.terminals["CDA-L"] = Terminal("cda", side="left")
        lab.terminals["CDA-R"] = Terminal("cda", side="right")
        sides = {lab._side("cda", other) for other in ("mun", "va")}
        self.assertEqual({"left", "right"}, sides, "CDA needs a neighbour on each side")

        def keeps_to_its_side():
            for name in ("CDA-L", "CDA-R"):
                terminal = lab.terminals[name]
                if terminal.selected is not None and terminal.screen != "overview":
                    self.assertTrue(lab._on_side(terminal, lab.legs[terminal.selected]), (name, terminal.selected))

        self.walk(20261002, keeps_to_its_side)
        for name in ("CDA-L", "CDA-R"):
            done = {action for device, action in self.traffic if device == name}
            self.assertLessEqual({"request", "depart", "arrive"}, done, f"{name} ska köra trafik på sin sida")

    def test_reject_and_withdraw_paths_are_marked(self):
        self.lookup("DEMO-CDA", "39")
        self.assertEqual("request", self.press("DEMO-CDA", "#"))
        self.lookup("DEMO-VA", "39")
        self.assertEqual("reject_view", self.press("DEMO-VA", "*"))
        self.assertEqual("back", self.press("DEMO-VA", "*"))
        self.assertEqual("reject_view", self.press("DEMO-VA", "*"))
        self.assertEqual("reject", self.press("DEMO-VA", "#"))
        self.assertEqual("back", self.press("DEMO-CDA", "#"))  # meddelandet om nekat
        self.lookup("DEMO-CDA", "39")
        self.assertEqual("request", self.press("DEMO-CDA", "#"))
        self.assertEqual("cancel_view", self.press("DEMO-CDA", "*"))
        self.assertEqual("cancel", self.press("DEMO-CDA", "#"))

    def test_queue_and_track_paths_are_marked(self):
        self.lookup("DEMO-MUN", "93")
        self.assertEqual("request", self.press("DEMO-MUN", "#"))
        self.lookup("DEMO-VA", "94")
        self.assertEqual("request", self.press("DEMO-VA", "#"))
        self.assertEqual("requests", self.press("DEMO-CDA", "A"))
        self.assertEqual("next_request", self.press("DEMO-CDA", "D"))
        self.assertEqual("previous_request", self.press("DEMO-CDA", "C"))
        self.assertEqual("accept", self.press("DEMO-CDA", "#"))
        self.assertEqual("depart", self.press("DEMO-MUN", "#"))
        # CDA still shows 93 after giving clearance; once it has departed,
        # B picks another arrival track.
        self.assertEqual("tracks", self.press("DEMO-CDA", "B"))
        self.assertEqual("next_track", self.press("DEMO-CDA", "D"))
        self.assertEqual("previous_track", self.press("DEMO-CDA", "C"))
        self.assertEqual("arrive_track", self.press("DEMO-CDA", "#"))

    def test_no_traffic_action_is_called_navigation(self):
        self.assertFalse(NAVIGATION_ACTIONS & TRAFFIC_ACTIONS)

    def test_browsing_is_free_and_an_offered_departure_is_guarded(self):
        """Det användaren märker: C och D bläddrar direkt, '#' som begär
        klartecken spärras efter skärmbytet."""

        frame = self.lab.frame("DEMO-CDA")
        self.assertFalse(frame["keys"]["D"]["acts"])
        self.assertFalse(frame["keys"]["A"]["acts"])
        self.send("DEMO-CDA", "#", train_number="39", entry_context=frame["entry"]["context"])
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual("Begär klartecken", frame["keys"]["#"]["label"])
        self.assertTrue(frame["keys"]["#"]["acts"])
        self.assertFalse(frame["keys"]["C"]["acts"])


if __name__ == "__main__":
    unittest.main()
