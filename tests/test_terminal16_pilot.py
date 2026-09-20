from copy import deepcopy
import json
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

from tmbox_gateway.models import ConnectionState as State
from tmbox_gateway.terminal16 import Terminal16Lab
from tmbox_gateway.terminal16_demo import demo_lab, LabServer


class Terminal16Tests(unittest.TestCase):
    def setUp(self):
        self.lab = demo_lab()
        self.lab.engine.set_clock_source(lambda: {"configured": True, "running": False, "time": "12:34"})

    def send(self, device, key, **extra):
        return self.lab.command(device, {"command_id": str(uuid4()), "view_token": self.lab.frame(device)["view_token"], "key": key, **extra})

    def lookup(self, number, device="DEMO-CDA"):
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
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "#")
        self.accept("DEMO-CDA", "#")

    def test_initial_geometry_and_no_destination_buttons(self):
        for frame in self.lab.frames():
            self.assertEqual((frame["cols"], frame["rows"]), (16, 2))
            self.assertTrue(frame["lines"][1].endswith("12:34"))
            self.assertNotIn("A", frame["keys"])
            self.assertEqual(frame["keys"]["#"]["label"], "Visa kommande tåg")
            self.assertEqual(frame["entry"]["commit"], "#")
        self.assertTrue(self.lab.frame("DEMO-CDA")["lines"][0].startswith("MUN-"))
        self.assertTrue(self.lab.frame("DEMO-CDA")["lines"][0].endswith("-VA"))

    def test_lookup_is_read_only_and_finds_destination(self):
        frame = self.lookup("39")["frame"]
        self.assertTrue(frame["lines"][0].endswith("39-VA"))
        self.assertEqual(frame["keys"]["#"]["label"], "Begär klartecken")
        self.assertEqual(self.lab.engine.revision, 0)
        self.assertEqual(self.lab.engine.audit, [])

    def test_no_per_digit_server_commands(self):
        for key in "1234567890":
            self.assertEqual(self.send("DEMO-CDA", key)["status"], "rejected")
        self.assertEqual(self.lab.engine.revision, 0)

    def test_unknown_train_and_clock(self):
        frame = self.lookup("999")["frame"]
        self.assertIn("INGET TÅG", frame["lines"][0])
        self.assertTrue(frame["lines"][1].endswith("12:34"))
        self.assertEqual(self.lab.engine.revision, 0)

    def test_input_rejects_six_digits_letters_and_foreign_context(self):
        for number, context in (("123456", ""), ("9A", ""), ("39", "another-box")):
            result = self.send("DEMO-CDA", "#", train_number=number, entry_context=context)
            self.assertEqual(result["status"], "rejected")

    def test_d_browses_without_requesting(self):
        self.accept("DEMO-CDA", "D")
        self.assertIn("17", self.lab.frame("DEMO-CDA")["lines"][0])
        self.accept("DEMO-CDA", "D")
        self.assertIn("39", self.lab.frame("DEMO-CDA")["lines"][0])
        self.assertEqual(self.lab.engine.revision, 0)

    def test_browse_mixes_arrivals_and_departures_in_station_time_order(self):
        expected = [("17-cda", "departure", "12:35"), ("39-cda", "departure", "12:38"),
                    ("93-mun", "arrival", "12:40")]
        for movement, kind, time in expected:
            frame = self.accept("DEMO-CDA", "D")["frame"]
            self.assertEqual((frame["upcoming"]["movement_id"], frame["upcoming"]["kind"], frame["upcoming"]["time"]),
                             (movement, kind, time))
            self.assertIn("ANK" if kind == "arrival" else "AVG", frame["lines"][0])
            self.assertTrue(frame["lines"][0].endswith(time))
        self.assertEqual(self.lab.engine.audit, [])

    def test_browse_previous_next_wrap_both_directions(self):
        for key, selected in (("C", "93-mun"), ("D", "17-cda"), ("C", "93-mun"), ("C", "39-cda")):
            self.assertEqual(self.accept("DEMO-CDA", key)["frame"]["upcoming"]["movement_id"], selected)
        self.assertEqual(self.lab.engine.audit, [])

    def test_hash_opens_then_selects_without_requesting(self):
        frame = self.accept("DEMO-CDA", "#")["frame"]
        self.assertEqual(frame["keys"]["#"]["label"], "Välj tåg")
        frame = self.accept("DEMO-CDA", "#")["frame"]
        self.assertIsNone(frame["upcoming"])
        self.assertEqual(frame["keys"]["#"]["label"], "Begär klartecken")
        self.assertEqual(self.lab.engine.audit, [])
        self.accept("DEMO-CDA", "#")
        self.assertEqual([event["action"] for event in self.lab.engine.audit], ["request"])

    def test_future_incoming_is_visible_but_cannot_be_accepted(self):
        frame = self.lookup("93")["frame"]
        self.assertIn("Ankomst från Munkeröd", frame["status"])
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(self.send("DEMO-CDA", "#")["status"], "rejected")
        self.assertEqual(self.lab.engine.audit, [])

    def test_arrival_departure_filters_are_read_only_and_station_scoped(self):
        self.accept("DEMO-CDA", "D")
        for kind, count, movement in (("arrival", 1, "93-mun"), ("departure", 2, "17-cda"), ("all", 3, "17-cda")):
            frame = self.accept("DEMO-CDA", "A")["frame"]
            self.assertEqual((frame["upcoming"]["filter"], frame["upcoming"]["count"], frame["upcoming"]["movement_id"]),
                             (kind, count, movement))
        self.assertEqual(self.lab.engine.audit, [])

    def test_empty_departure_filter_has_no_confirmation_and_can_be_left(self):
        self.accept("DEMO-VA", "D")
        self.accept("DEMO-VA", "A")
        frame = self.accept("DEMO-VA", "A")["frame"]
        self.assertIn("INGA AVGÅNGAR", frame["lines"][0])
        self.assertNotIn("#", frame["keys"])
        self.accept("DEMO-VA", "A")
        self.assertEqual(self.lab.frame("DEMO-VA")["upcoming"]["count"], 1)

    def test_departed_train_leaves_sender_list_but_remains_for_receiver(self):
        self.departure()
        cda = self.lab.terminals["DEMO-CDA"]
        va = self.lab.terminals["DEMO-VA"]
        self.assertNotIn("39-cda", self.lab._candidates(cda))
        self.assertIn("39-cda", self.lab._candidates(va))
        self.accept("DEMO-VA", "#")
        self.assertNotIn("39-cda", self.lab._candidates(va))

    def test_overdue_unreported_train_does_not_disappear(self):
        frame = self.accept("DEMO-MUN", "D")["frame"]
        self.assertEqual(frame["upcoming"]["movement_id"], "93-mun")
        self.assertEqual(frame["upcoming"]["time"], "12:32")

    def test_browse_respects_service_day_offset_across_midnight(self):
        for movement in self.lab.publication["trains"]:
            if movement["id"] == "17-cda":
                movement.update(departure_time="00:05", service_day_offset=1)
        self.assertEqual(self.lab._candidates(self.lab.terminals["DEMO-CDA"]), ["39-cda", "93-mun", "17-cda"])

    def test_finished_browse_selection_does_not_confirm_different_train(self):
        self.departure()
        self.accept("DEMO-VA", "D")
        stale = self.lab.frame("DEMO-VA")["view_token"]
        self.lab.terminals["SECOND-VA"] = deepcopy(self.lab.terminals["DEMO-VA"])
        self.lookup("39", "SECOND-VA"); self.accept("SECOND-VA", "#")
        frame = self.lab.frame("DEMO-VA")
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(self.lab.command("DEMO-VA", {"command_id": "stale-choice", "view_token": stale, "key": "#"})["status"], "rejected")
        self.assertEqual(len(self.lab.arrivals), 1)

    def test_through_train_cannot_depart_before_its_arrival(self):
        package = deepcopy(self.lab.publication)
        next(m for m in package["trains"] if m["id"] == "93-cda")["departure_time"] = "12:43"
        service = next(s for s in package["services"] if s["id"] == "93")
        service["stops"][1]["departure_time"] = "12:43"
        stop = {"station_id": "va", "stop_order": 2, "arrival_time": "12:50", "departure_time": None}
        service["stops"].append(stop)
        package["trains"].append({**stop, "id": "93-va", "service_id": "93", "train_number": "93", "days": "Dagl", "track_id": "va-1"})
        self.lab = Terminal16Lab(self.lab.engine, package, {"DEMO-MUN": "mun", "DEMO-CDA": "cda", "DEMO-VA": "va"})
        def choose(movement):
            for _ in range(5):
                if self.accept("DEMO-CDA", "D")["frame"]["upcoming"]["movement_id"] == movement:
                    return self.accept("DEMO-CDA", "#")["frame"]
            self.fail("Movement was not available in the station timetable")
        frame = choose("93-cda")
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(self.send("DEMO-CDA", "#")["status"], "rejected")
        self.lookup("93", "DEMO-MUN"); self.accept("DEMO-MUN", "#")
        choose("93-mun"); self.accept("DEMO-CDA", "#")
        self.accept("DEMO-MUN", "#"); self.accept("DEMO-CDA", "#")
        self.accept("DEMO-CDA", "#")  # Dismiss the arrival acknowledgement.
        frame = choose("93-cda")
        self.assertEqual(frame["keys"]["#"]["label"], "Begär klartecken")

    def test_full_clearance_chain(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertIn("39?VA", self.lab.frame("DEMO-CDA")["lines"][0])
        self.assertNotIn("#", self.lab.frame("DEMO-CDA")["keys"])
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)
        self.assertIn("39>VA", self.lab.frame("DEMO-CDA")["lines"][0])
        self.accept("DEMO-CDA", "#")
        self.assertIn("39▶VA", self.lab.frame("DEMO-CDA")["lines"][0])
        self.assertIn("CDA▶39", self.lab.frame("DEMO-VA")["lines"][0])
        self.accept("DEMO-VA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)
        self.assertEqual(self.lab.arrivals["39-cda"]["track"], "va-1")
        self.assertEqual([x["action"] for x in self.lab.engine.audit], ["request", "accept", "depart", "arrive"])

    def test_two_simultaneous_requests_share_top_row(self):
        self.lookup("17"); self.accept("DEMO-CDA", "#")
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.accept("DEMO-CDA", "A")
        self.assertEqual(self.lab.frame("DEMO-CDA")["lines"][0], "MUN?17     39?VA")

    def test_left_arrow_direction_changes_only_on_departure(self):
        self.lookup("17"); self.accept("DEMO-CDA", "#")
        self.lookup("17", "DEMO-MUN"); self.accept("DEMO-MUN", "#")
        self.assertIn("MUN<17", self.lab.frame("DEMO-CDA")["lines"][0])
        self.accept("DEMO-CDA", "#")
        self.assertIn("MUN◀17", self.lab.frame("DEMO-CDA")["lines"][0])
        self.assertIn("17◀CDA", self.lab.frame("DEMO-MUN")["lines"][0])

    def test_rejection_notifies_sender_without_departure(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "*"); self.accept("DEMO-VA", "#")
        self.assertIn("NEKAT", self.lab.frame("DEMO-CDA")["lines"][0])
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_home_does_not_cancel_pending_request(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#"); self.accept("DEMO-CDA", "A")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(self.lab.terminals["DEMO-CDA"].screen, "overview")

    def test_cancel_pending_requires_explicit_confirmation(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#"); self.accept("DEMO-CDA", "*")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.accept("DEMO-CDA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_cancel_approved_before_departure(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "#")
        self.accept("DEMO-CDA", "*"); self.accept("DEMO-CDA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_star_opens_withdrawal_and_second_star_keeps_request(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        frame = self.accept("DEMO-CDA", "*")["frame"]
        self.assertIn("ÅTER 39?", frame["lines"][0])
        self.assertEqual(frame["keys"]["#"]["label"], "Bekräfta återtagning")
        self.accept("DEMO-CDA", "*")
        self.assertEqual(self.lab.terminals["DEMO-CDA"].screen, "detail")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual([event["action"] for event in self.lab.engine.audit], ["request"])

    def test_star_opens_rejection_and_second_star_keeps_incoming_request(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        frame = self.lookup("39", "DEMO-VA")["frame"]
        self.assertNotIn("B", frame["keys"])
        frame = self.accept("DEMO-VA", "*")["frame"]
        self.assertIn("NEKA 39?", frame["lines"][0])
        self.assertEqual(len(self.lab.engine.audit), 1)
        frame = self.accept("DEMO-VA", "*")["frame"]
        self.assertEqual(frame["keys"]["#"]["label"], "Ge klart")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(len(self.lab.engine.audit), 1)

    def test_home_keeps_incoming_request_without_answering(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "A")
        self.assertEqual(self.lab.terminals["DEMO-VA"].screen, "overview")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(len(self.lab.engine.audit), 1)

    def test_star_keeps_approved_clearance_when_confirmation_is_abandoned(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "#")
        self.accept("DEMO-CDA", "*"); self.accept("DEMO-CDA", "*")
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)
        self.assertEqual(self.lab.frame("DEMO-CDA")["keys"]["#"]["label"], "Rapportera avgång")

    def test_star_after_departure_only_goes_back_at_either_station(self):
        self.departure()
        for device in ("DEMO-CDA", "DEMO-VA"):
            self.assertEqual(self.lab.frame(device)["keys"]["*"]["label"], "Tillbaka")
            self.accept(device, "*")
            self.assertEqual(self.lab.terminals[device].screen, "overview")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)
        self.assertEqual(len(self.lab.engine.audit), 3)

    def test_star_leaves_track_picker_without_arrival(self):
        self.departure(); self.accept("DEMO-VA", "B"); self.accept("DEMO-VA", "D")
        frame = self.accept("DEMO-VA", "*")["frame"]
        self.assertEqual(frame["keys"]["#"]["label"], "Rapportera ankomst")
        self.assertEqual(self.lab.arrivals, {})
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_star_leaves_browse_without_requesting(self):
        self.accept("DEMO-CDA", "D"); self.accept("DEMO-CDA", "*")
        self.assertEqual(self.lab.terminals["DEMO-CDA"].screen, "overview")
        self.assertEqual(self.lab.engine.audit, [])

    def test_stale_rejection_cannot_reject_after_sender_withdraws(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "*")
        stale = self.lab.frame("DEMO-VA")["view_token"]
        self.accept("DEMO-CDA", "*"); self.accept("DEMO-CDA", "#")
        frame = self.lab.frame("DEMO-VA")
        self.assertNotIn("#", frame["keys"])
        self.assertIn("LÄGET ÄNDRAT", frame["lines"][0])
        self.assertEqual(self.lab.command("DEMO-VA", {"command_id": "stale-rejection", "view_token": stale, "key": "#"})["status"], "rejected")
        self.accept("DEMO-VA", "*")
        self.assertEqual([event["action"] for event in self.lab.engine.audit], ["request", "cancel"])

    def test_stale_withdrawal_cannot_cancel_after_another_sender_reports_departure(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "#")
        self.lab.terminals["SECOND-CDA"] = deepcopy(self.lab.terminals["DEMO-CDA"])
        self.accept("DEMO-CDA", "*")
        stale = self.lab.frame("DEMO-CDA")["view_token"]
        self.accept("SECOND-CDA", "#")
        frame = self.lab.frame("DEMO-CDA")
        self.assertNotIn("#", frame["keys"])
        self.assertIn("LÄGET ÄNDRAT", frame["lines"][0])
        self.assertEqual(self.lab.command("DEMO-CDA", {"command_id": "stale-withdrawal", "view_token": stale, "key": "#"})["status"], "rejected")
        self.accept("DEMO-CDA", "*")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_typing_new_train_during_withdrawal_does_not_withdraw_old_train(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#"); self.accept("DEMO-CDA", "*")
        self.lookup("17")
        self.assertEqual(self.lab.engine.connections["east"].state, State.REQUESTED)
        self.assertEqual(self.lab.terminals["DEMO-CDA"].selected, "17-cda")
        self.assertEqual(len(self.lab.engine.audit), 1)

    def test_no_cancel_after_departure(self):
        self.departure()
        self.assertNotIn("A", self.lab.frame("DEMO-CDA")["keys"])
        self.assertEqual(self.send("DEMO-CDA", "A")["status"], "rejected")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_no_arrival_before_departure(self):
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.lookup("39", "DEMO-VA"); self.accept("DEMO-VA", "#")
        self.assertNotIn("#", self.lab.frame("DEMO-VA")["keys"])
        self.assertEqual(self.send("DEMO-VA", "#")["status"], "rejected")
        self.assertEqual(self.lab.arrivals, {})

    def test_arrival_track_override_preserves_plan(self):
        self.departure(); self.accept("DEMO-VA", "B"); self.accept("DEMO-VA", "D")
        self.assertIn("SPÅR 2", self.lab.frame("DEMO-VA")["lines"][0])
        self.accept("DEMO-VA", "#")
        self.assertEqual(self.lab.arrivals["39-cda"]["track"], "va-2")
        self.assertEqual(self.lab.arrivals["39-cda"]["planned_track"], "va-1")

    def test_occupied_arrival_track_does_not_release_line(self):
        self.departure()
        self.lab.arrivals["another-leg"] = {"station": "va", "track": "va-1"}
        self.assertEqual(self.send("DEMO-VA", "#")["status"], "rejected")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)
        self.assertNotIn("39-cda", self.lab.arrivals)

    def test_direct_mode_reserves_without_receiver_but_still_requires_departure(self):
        self.lab = demo_lab("direct")
        self.lab.engine.set_clock_source(lambda: {"configured": True, "time": "12:34"})
        self.lookup("39"); self.accept("DEMO-CDA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.RESERVED)
        self.accept("DEMO-CDA", "#")
        self.assertEqual(self.lab.engine.connections["east"].state, State.OCCUPIED)

    def test_wrong_station_cannot_select_nonlocal_train(self):
        self.lookup("39", "DEMO-MUN")
        self.assertIn("INGET TÅG", self.lab.frame("DEMO-MUN")["lines"][0])

    def test_client_cannot_supply_action_station_or_destination(self):
        self.lookup("39")
        for extra in ({"action": "depart"}, {"station_id": "va"}, {"connection_id": "west"}):
            self.assertEqual(self.send("DEMO-CDA", "#", **extra)["status"], "rejected")
        self.assertEqual(self.lab.engine.revision, 0)

    def test_malformed_keys_and_lookup_overrides_are_rejected(self):
        for key in (None, [], {}, 1, "AA"):
            self.assertEqual(self.send("DEMO-CDA", key)["status"], "rejected")
        for extra in ({"action": "depart"}, {"station_id": "va"}, {"connection_id": "west"}):
            result = self.send("DEMO-CDA", "#", train_number="39",
                               entry_context=self.lab.frame("DEMO-CDA")["entry"]["context"], **extra)
            self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.lab.engine.revision, 0)

    def test_completed_sender_does_not_advertise_unavailable_key(self):
        self.departure(); self.accept("DEMO-VA", "#")
        frame = self.lab.frame("DEMO-CDA")
        self.assertEqual(set(frame["keys"]), {"*"})
        self.assertTrue(frame["lines"][1].startswith("*=Bak"))

    def test_route_is_revalidated_at_mutation(self):
        self.lookup("39")
        self.lab.publication["services"][1]["stops"][1]["station_id"] = "mun"
        self.assertEqual(self.send("DEMO-CDA", "#")["status"], "rejected")
        self.assertEqual(self.lab.engine.revision, 0)

    def test_duplicate_command_is_idempotent_and_id_cannot_be_repurposed(self):
        self.lookup("39")
        body = {"command_id": "same-id", "view_token": self.lab.frame("DEMO-CDA")["view_token"], "key": "#"}
        first = self.lab.command("DEMO-CDA", body)
        second = self.lab.command("DEMO-CDA", body)
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(self.lab.engine.revision, 1)
        self.assertEqual(self.lab.command("DEMO-CDA", {**body, "key": "D"})["status"], "rejected")

    def test_stale_view_rejected_after_other_terminal_changes_state(self):
        self.lookup("39")
        stale = self.lab.frame("DEMO-CDA")["view_token"]
        self.lookup("93", "DEMO-MUN"); self.accept("DEMO-MUN", "#")
        result = self.lab.command("DEMO-CDA", {"command_id": "old", "view_token": stale, "key": "#"})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.lab.engine.connections["east"].state, State.FREE)

    def test_old_reset_frame_cannot_control_new_lab(self):
        old = self.lab.frame("DEMO-CDA")
        self.lab = demo_lab()
        result = self.lab.command("DEMO-CDA", {"command_id": "old", "view_token": old["view_token"], "key": "C"})
        self.assertEqual(result["status"], "rejected")

    def test_completed_leg_cannot_be_sent_again(self):
        self.departure(); self.accept("DEMO-VA", "#")
        self.lookup("39")
        self.assertIn("INGET TÅG", self.lab.frame("DEMO-CDA")["lines"][0])

    def test_long_identities_page_instead_of_truncate(self):
        for old, new in (("17", "12345"), ("39", "67890")):
            for item in self.lab.publication["trains"] + self.lab.publication["services"]:
                if item["train_number"] == old: item["train_number"] = new
            self.lab.legs[f"{old}-cda"]["train_number"] = new
            self.lookup(new); self.accept("DEMO-CDA", "#")
        self.accept("DEMO-CDA", "A")
        self.assertIn("MUN?12345", self.lab.frame("DEMO-CDA")["lines"][0])
        self.accept("DEMO-CDA", "B")
        self.assertIn("67890?VA", self.lab.frame("DEMO-CDA")["lines"][0])


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

    def test_reset_all_devices_clears_every_traffic_phase(self):
        for phase in ("requested", "reserved", "occupied", "arrived"):
            with self.subTest(phase=phase):
                self.command("DEMO-CDA", "#", train_number="39")
                self.command("DEMO-CDA", "#")
                if phase != "requested":
                    self.command("DEMO-VA", "#", train_number="39")
                    self.command("DEMO-VA", "#")
                if phase in {"occupied", "arrived"}:
                    self.command("DEMO-CDA", "#")
                if phase == "arrived":
                    self.command("DEMO-VA", "#")
                self.command("DEMO-CDA", "#", train_number="17")
                self.command("DEMO-CDA", "#")
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
                    self.assertNotEqual(frame["entry"]["context"], old_contexts[frame["device_id"]])
                self.assertEqual(self.server.lab.publication, before.publication)

    def test_reset_preserves_current_mode_station_assignments_and_live_clock(self):
        self.post("/api/reset", {"mode": "direct"})
        before = self.server.lab
        tick = ["17:06"]
        before.engine.set_clock_source(lambda: {"configured": True, "running": True, "time": tick[0]})
        before.terminals["EXTRA-BOX"] = deepcopy(before.terminals["DEMO-MUN"])
        self.command("DEMO-CDA", "#", train_number="39"); self.command("DEMO-CDA", "#")
        state = self.post("/api/reset-devices", {})
        self.assertEqual(state["mode"], "direct")
        self.assertEqual(self.server.lab.engine.config, before.engine.config)
        self.assertEqual({d: t.station for d, t in self.server.lab.terminals.items()},
                         {d: t.station for d, t in before.terminals.items()})
        self.assertTrue(all(frame["lines"][1].endswith("17:06") for frame in state["frames"]))
        tick[0] = "17:07"
        self.assertTrue(all(frame["lines"][1].endswith("17:07") for frame in self.server.snapshot()["frames"]))
        self.command("DEMO-CDA", "#", train_number="39"); self.command("DEMO-CDA", "#")
        self.assertEqual(self.server.lab.engine.connections["east"].state, State.RESERVED)

    def test_commands_from_before_reset_cannot_recreate_traffic(self):
        self.command("DEMO-CDA", "#", train_number="39")
        stale = self.server.lab.frame("DEMO-CDA")
        self.post("/api/reset-devices", {})
        with self.assertRaises(HTTPError) as error:
            self.post("/api/key", {"device_id": "DEMO-CDA", "command_id": "late-command",
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
