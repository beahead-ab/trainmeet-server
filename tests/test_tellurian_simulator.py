"""Simulatorn följer Tellurian Dispatch 1.1.0: ActionStateMachine, tågläge och kapacitet.

Reglerna är lästa ur tellurianinteractive/Tellurian.Trains.Dispatch på 0a0f42a
(ActionStateMachine.cs, ActionContextExtensions.cs, Train.cs). Ingen kod därifrån
körs. Där simulatorn avviker från biblioteket står det i testet och i
docs/tellurian-dispatch.md.
"""
import copy
import unittest

from tmbox_gateway.integrations.tellurian_sim import SimRefused, TellurianModel, default_scenario


def names(model, section, dispatcher):
    return [name for name, _, _ in model.available(section, dispatcher)]


class ActionStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.model = TellurianModel(default_scenario())
        self.first = self.model.section_for(421, "CDA")   # CDA → VST, blocksignal SÖB
        self.second = self.model.section_for(421, "VST")  # VST → LEK, dubbelspår

    def do(self, section, name, index=None):
        self.model.execute(section.id, name, index)

    def test_a_train_must_be_manned_before_the_first_request(self):
        self.assertEqual(["manned", "canceled"], names(self.model, self.first, "CDA"))
        self.assertEqual(["manned", "canceled"], names(self.model, self.first, "VST"))
        with self.assertRaises(SimRefused):
            self.do(self.first, "request")
        self.do(self.first, "manned")
        self.assertEqual(["request", "canceled", "undoTrainState"], names(self.model, self.first, "CDA"))

    def test_departure_and_arrival_dispatchers_get_their_own_actions(self):
        self.do(self.first, "manned")
        self.do(self.first, "request")
        self.assertEqual(["revoke"], names(self.model, self.first, "CDA")[:1])
        self.assertEqual(["accept", "reject"], names(self.model, self.first, "VST")[:2])
        self.do(self.first, "accept")
        self.assertEqual(["depart", "revoke"], names(self.model, self.first, "CDA")[:2])
        self.assertEqual(["revoke"], names(self.model, self.first, "VST")[:1])

    def test_rejected_and_revoked_can_be_requested_again(self):
        self.do(self.first, "manned")
        for answer in ("reject", "revoke"):
            self.do(self.first, "request")
            if answer == "revoke":
                self.do(self.first, "accept")
            self.do(self.first, answer)
            self.assertIn("request", names(self.model, self.first, "CDA"))

    def test_full_run_with_pass_at_signal_controlled_place(self):
        self.do(self.first, "manned")
        self.do(self.first, "request")
        self.do(self.first, "accept")
        self.do(self.first, "depart")
        self.assertEqual("Running", self.model.trains[self.first.train_id].state)
        # Arrive först när alla passager är gjorda; passagen hör till CDA som styr SÖB.
        self.assertNotIn("arrive", names(self.model, self.first, "VST"))
        self.assertEqual(["pass"], names(self.model, self.first, "CDA"))
        self.do(self.first, "pass", 1)
        self.assertEqual([], names(self.model, self.first, "CDA"))
        self.assertEqual(["arrive"], names(self.model, self.first, "VST"))
        self.do(self.first, "arrive")
        self.assertEqual("Arrived", self.first.state)

    def test_next_section_waits_until_the_previous_has_departed(self):
        self.do(self.first, "manned")
        self.assertNotIn("request", names(self.model, self.second, "VST"))
        self.do(self.first, "request")
        self.do(self.first, "accept")
        self.assertNotIn("request", names(self.model, self.second, "VST"))
        self.do(self.first, "depart")
        self.assertIn("request", names(self.model, self.second, "VST"))
        # Tågläge på senare sektioner: bara avbrutet.
        self.assertIn("aborted", names(self.model, self.second, "VST"))
        self.assertNotIn("manned", names(self.model, self.second, "VST"))

    def test_train_state_actions_and_one_level_of_undo(self):
        train = self.model.trains[self.first.train_id]
        self.do(self.first, "canceled")
        self.assertEqual(("Canceled", "Planned"), (train.state, train.previous))
        self.assertIn("undoTrainState", names(self.model, self.first, "CDA"))
        self.do(self.first, "undoTrainState")
        self.assertEqual(("Planned", None), (train.state, train.previous))
        self.assertNotIn("undoTrainState", names(self.model, self.first, "CDA"))

    def test_aborted_train_on_the_line_can_be_cleared(self):
        for name in ("manned", "request", "accept", "depart"):
            self.do(self.first, name)
        for name in ("request", "accept", "depart"):
            self.do(self.second, name)
        self.do(self.second, "aborted")
        self.assertIn("clear", names(self.model, self.second, "LEK"))
        self.do(self.second, "clear")
        self.assertEqual("Canceled", self.second.state)
        self.assertEqual({}, self.model.track_stretches[3].occupied)

    def test_an_unavailable_or_stale_action_is_refused(self):
        with self.assertRaises(SimRefused):
            self.do(self.first, "depart")
        for name in ("manned", "request", "accept", "depart"):
            self.do(self.first, name)
        with self.assertRaises(SimRefused):
            self.do(self.first, "pass", 2)  # fel index: en gammal href

    def test_arrived_sections_are_hidden_and_lists_are_capped(self):
        for name in ("manned", "request", "accept", "depart"):
            self.do(self.first, name)
        self.do(self.first, "pass", 1)
        self.do(self.first, "arrive")
        view = self.model.dispatcher_view(self.model.dispatcher_id("VST"))
        self.assertNotIn(self.first.id, [s["id"] for s in view["arrivals"]])
        capped = TellurianModel(default_scenario(), max_items=1)
        self.assertEqual(1, len(capped.dispatcher_view(capped.dispatcher_id("VST"))["arrivals"]))


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.model = TellurianModel(default_scenario())
        self.model.man_all()

    def ready(self, number, origin):
        section = self.model.section_for(number, origin)
        self.model.execute(section.id, "request")
        self.model.execute(section.id, "accept")
        return section

    def test_opposing_trains_are_blocked_on_single_track(self):
        northbound = self.ready(421, "CDA")
        southbound_lek = self.model.section_for(4711, "LEK")
        for name in ("request", "accept", "depart"):
            self.model.execute(southbound_lek.id, name)
        self.model.execute(southbound_lek.id, "arrive")
        southbound = self.ready(4711, "VST")
        self.model.execute(northbound.id, "depart")
        # Mötande tåg på samma enkelspår får inte avgå, inte ens in i det andra blocket.
        self.assertNotIn("depart", names(self.model, southbound, "VST"))
        self.model.execute(northbound.id, "pass", 1)
        self.model.execute(northbound.id, "arrive")
        self.assertIn("depart", names(self.model, southbound, "VST"))

    def test_reverse_direction_pass_follows_the_direction_of_travel(self):
        """Biblioteket använder TrackStretch.End även vid motriktad trafik (öppen fråga)."""
        southbound_lek = self.model.section_for(4711, "LEK")
        for name in ("request", "accept", "depart", "arrive"):
            self.model.execute(southbound_lek.id, name)
        southbound = self.ready(4711, "VST")
        self.model.execute(southbound.id, "depart")
        self.assertEqual(["pass", "aborted"], names(self.model, southbound, "CDA"))
        self.model.execute(southbound.id, "pass", 1)
        self.assertEqual(["arrive", "aborted"], names(self.model, southbound, "CDA"))

    def test_double_track_lets_opposing_trains_run_at_the_same_time(self):
        scenario = copy.deepcopy(default_scenario())
        scenario["trains"].append({"company": "SJ", "prefix": "RC", "number": 900,
                                   "calls": [["LEK", None, "09:00"], ["VST", "09:20", None]]})
        model = TellurianModel(scenario)
        model.man_all()
        outbound = model.section_for(100, "VST")
        inbound = model.section_for(900, "LEK")
        for section in (outbound, inbound):
            for name in ("request", "accept", "depart"):
                model.execute(section.id, name)
        self.assertEqual("Departed", outbound.state)
        self.assertEqual("Departed", inbound.state)

    def test_one_train_per_block_on_the_same_track(self):
        scenario = copy.deepcopy(default_scenario())
        scenario["trains"].append({"company": "SJ", "prefix": "RC", "number": 423,
                                   "calls": [["CDA", None, "09:22"], ["VST", "09:43", None]]})
        model = TellurianModel(scenario)
        model.man_all()
        first, follower = model.section_for(421, "CDA"), model.section_for(423, "CDA")
        for section in (first, follower):
            model.execute(section.id, "request")
            model.execute(section.id, "accept")
        model.execute(first.id, "depart")
        self.assertNotIn("depart", names(model, follower, "CDA"))
        model.execute(first.id, "pass", 1)
        # Blocket efter signalen är fritt för tåget bakom.
        self.assertIn("depart", names(model, follower, "CDA"))
        model.execute(follower.id, "depart")
        self.assertNotIn("pass", names(model, follower, "CDA"))


if __name__ == "__main__":
    unittest.main()
