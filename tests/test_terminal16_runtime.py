from unittest.mock import patch
from copy import deepcopy
from uuid import uuid4
import unittest

import test_shared_traffic
from runtime_fixture import runtime_package_v3
from tmbox_gateway.terminal16_runtime import Terminal16Service
from tmbox_gateway.terminal16_mqtt import Terminal16Gateway
from tmbox_gateway.device_ui import LANGUAGES
import json


class RuntimeTerminalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.service = self.fixture.service
        self.terminals = Terminal16Service(self.service)

    def tearDown(self):
        self.fixture.tearDown()

    def send(self, device, key, **extra):
        frame = self.terminals.frame(device)
        body = {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": key, **extra}
        if "train_number" in extra:
            body["entry_context"] = frame["entry"]["context"]
        answer = self.terminals.command(device, body)
        self.assertEqual(answer["status"], "accepted", answer)
        return answer

    def depart(self):
        self.send("esp8266", "#", train_number="101")
        self.assertEqual(self.terminals.frame("esp32")["requests"]["count"], 1)
        self.send("esp32", "#")
        self.send("esp8266", "#")

    def test_live_flow_uses_shared_durable_cases_and_tracks(self):
        self.depart()
        self.assertEqual(self.fixture.ops.positions()[0]["status"], "connection")
        self.send("esp32", "#")
        self.assertEqual(self.service.open_cases(None), [])
        live = self.fixture.ops.tkl_station_state(self.fixture.publication.publication_id, "Lör", "station-b")
        self.assertEqual(live["movements"]["movement-101-b"]["arrival"], "arrived")
        self.assertEqual(live["movements"]["movement-101-b"]["actualTrack"], "track-station-b-1")
        self.assertIn("MOTTAGET", self.terminals.frame("esp8266")["lines"][0])

    def test_two_cleared_departures_active_navigation_survives_restart_without_retargeting(self):
        package = runtime_package_v3(publication_id="two-active-departures")
        package["display"]["graph_station_order"].append("station-c")
        package["stations"].append({**package["stations"][1], "id": "station-c", "code": "MUN", "name": "Munkeröd", "diagram_order": 2})
        package["tracks"].append({**package["tracks"][2], "id": "track-station-c-1", "station_id": "station-c"})
        package["connections"].append({**package["connections"][0], "id": "connection-a-c", "station_b_id": "station-c"})
        package["panels"][0]["slots"]["B"] = "connection-a-c"
        package["panels"].append({"id": "panel-c", "station_id": "station-c", "name": "MUN", "slots": {"A": "connection-a-c", "B": None, "C": None, "D": None}})
        service = deepcopy(package["services"][0])
        service.update(id="service-303-Dagl", train_number="303")
        service["stops"][1].update(station_id="station-c", station_name="MUN")
        package["services"].append(service)
        for collection in ("trains", "routes"):
            for original in list(package[collection]):
                if original["train_number"] != "101":
                    continue
                item = deepcopy(original)
                item.update(id=item["id"].replace("101", "303"), train_number="303", service_id=service["id"])
                if item["station_id"] == "station-b":
                    item.update(id=item["id"][:-1] + "c", station_id="station-c")
                if collection == "trains":
                    item.update(track_id="track-station-a-2" if item["station_id"] == "station-a" else "track-station-c-1",
                                station="CDA" if item["station_id"] == "station-a" else "MUN")
                    if item["departure_to"]: item["departure_to"] = "MUN"
                elif item["station_id"] == "station-c":
                    item["station_name"] = "MUN"
                package[collection].append(item)
        self.fixture.install(package)
        self.fixture.ids.record_discovery("third", "third", protocol_version=2)
        self.fixture.ids.assign_discovered_device("third", station_id="station-c")
        for number, receiver in (("101", "esp32"), ("303", "third")):
            self.send("esp8266", "#", train_number=number)
            self.send(receiver, "#")
        # Rebuilding views from durable cases must expose both, even after reconnect.
        self.terminals = Terminal16Service(self.service)
        self.assertEqual(self.terminals.frame("esp8266")["active"]["count"], 2)
        self.assertEqual(self.send("esp8266", "B")["frame"]["active"]["movement_id"], "movement-101-a")
        cases = deepcopy(self.service.open_cases(None))
        self.assertEqual(self.send("esp8266", "D")["frame"]["active"]["movement_id"], "movement-303-a")
        self.assertEqual(cases, self.service.open_cases(None))
        frame = self.send("esp8266", "#")["frame"]
        self.assertNotIn("#", frame["keys"])
        self.assertEqual(frame["active"]["movement_id"], "movement-303-a")
        self.assertEqual(self.terminals.command("esp8266", {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": "#"})["status"], "rejected")
        self.assertEqual(self.fixture.ops.positions()[0]["train_number"], "303")
        self.terminals = Terminal16Service(self.service)
        self.assertEqual(self.send("esp8266", "B")["frame"]["active"]["movement_id"], "movement-101-a")
        self.send("esp8266", "#")
        self.assertEqual({p["train_number"] for p in self.fixture.ops.positions()}, {"101", "303"})

    def test_placement_updates_frame_without_resetting_traffic_selection_or_input_context(self):
        self.send("esp8266", "#", train_number="101")
        before = self.terminals.frame("esp8266")
        cases = self.service.open_cases(None)
        views = self.terminals._views("esp8266")
        selected = views.terminals["esp8266"].selected
        old_side = views._side("station-a", "station-b", "connection-a-b")
        new_side = "right" if old_side == "left" else "left"
        self.fixture.runtime.save_display_placement(self.fixture.publication, "station-a", {"connection-a-b": new_side})
        after = self.terminals.frame("esp8266")
        self.assertIs(views, self.terminals._views("esp8266"))
        self.assertEqual(selected, views.terminals["esp8266"].selected)
        self.assertEqual(cases, self.service.open_cases(None))
        self.assertEqual(before["entry"]["context"], after["entry"]["context"])
        self.assertEqual(before["revision"], after["revision"])
        self.assertGreater(after["view_revision"], before["view_revision"])
        self.assertNotEqual(before["view_token"], after["view_token"])
        self.assertNotEqual(before["lines"][0], after["lines"][0])
        self.assertEqual(new_side, views._side("station-a", "station-b", "connection-a-b"))
        self.assertEqual(after["view_revision"], self.terminals.frame("esp8266")["view_revision"])
        result = self.terminals.command("esp8266", {"command_id": uuid4().hex, "key": "*", "view_token": before["view_token"]})
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(cases, self.service.open_cases(None))

    def test_recreated_profile_restores_departed_train_without_replay(self):
        self.depart()
        self.terminals = Terminal16Service(self.service)
        self.assertIn("101", self.terminals.frame("esp32")["lines"][0])
        self.send("esp32", "#", train_number="101")
        self.send("esp32", "#")
        self.assertEqual(self.service.open_cases(None), [])

    def test_lost_commit_does_not_release_line_or_change_arrival(self):
        self.depart()
        with patch.object(self.fixture.ops, "release_clearance", side_effect=RuntimeError("disk")), \
                self.assertLogs("tmbox_gateway.terminal16", "ERROR"):
            frame = self.terminals.frame("esp32")
            answer = self.terminals.command("esp32", {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": "#"})
            # A no with the screen, not silence (since 2.0.3); nothing changed.
            self.assertEqual("rejected", answer["status"])
        self.assertEqual(len(self.service.open_cases(None)), 1)
        live = self.fixture.ops.tkl_station_state(self.fixture.publication.publication_id, "Lör", "station-b")
        self.assertNotEqual(live["movements"].get("movement-101-b", {}).get("arrival"), "arrived")
        self.send("esp32", "#")
        self.assertEqual(self.service.open_cases(None), [])

    def test_unassigned_device_has_no_input_or_traffic_actions(self):
        frame = self.terminals.frame("unknown-box")
        self.assertEqual(frame["keys"], {})
        self.assertIsNone(frame["entry"])
        self.assertEqual(self.terminals.command("unknown-box", {"key": "#"})["status"], "rejected")

    def test_station_reassignment_rejects_previous_view(self):
        old = self.terminals.frame("esp8266")
        self.fixture.ids.assign_discovered_device("esp8266", station_id="station-b")
        answer = self.terminals.command("esp8266", {"key": "#", "command_id": uuid4().hex,
            "view_token": old["view_token"], "train_number": "101", "entry_context": old["entry"]["context"]})
        self.assertEqual(answer["status"], "rejected")

    def test_old_v2_receiver_can_approve_new_terminal_request(self):
        self.send("esp8266", "#", train_number="101")
        self.fixture.approve(self.service.open_cases(None)[0]["clearance_id"])
        self.assertIn("Rapportera avgång", self.terminals.frame("esp8266")["keys"]["#"]["label"])
        self.send("esp8266", "#")

    def test_languages_come_from_server_and_keep_all_lcd_frames_valid(self):
        for code, _ in LANGUAGES:
            with self.subTest(language=code):
                self.fixture.ids.set_device_language("esp8266", code)
                self.fixture.ids.set_device_language("esp32", code)
                self.terminals = Terminal16Service(self.service)
                for device in ("esp8266", "esp32"):
                    frame = self.terminals.frame(device)
                    self.assertEqual(frame["language"], code)
                    self.assertEqual([len(line) for line in frame["lines"]], [16, 16])
                self.send("esp8266", "#", train_number="101")
                self.send("esp32", "*")
                self.send("esp32", "#")
                self.assertEqual(self.service.open_cases(None), [])

    def test_language_is_set_by_the_administrator_not_on_the_box(self):
        """The box has no language menu: the start screen shows only what an
        operator needs. The administrator sets each box's language in Server."""

        frame = self.terminals.frame("esp8266")
        self.assertNotIn("*", frame["keys"])
        self.assertTrue(frame["lines"][1].startswith("Nr# A:Kö"))
        refused = self.terminals.command("esp8266", {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": "*"})
        self.assertEqual("rejected", refused["status"])
        self.fixture.ids.set_device_language("esp8266", "en")
        frame = self.terminals.frame("esp8266")
        self.assertEqual("en", frame["language"])
        self.assertEqual(frame["keys"]["#"]["label"], "Show upcoming trains")
        self.assertTrue(frame["lines"][1].startswith("No# A:Q"))
        before = frame["view_token"]
        self.fixture.ids.set_device_language("esp8266", "de")
        self.assertNotEqual(self.terminals.frame("esp8266")["view_token"], before)
        self.assertEqual(self.service.open_cases(None), [])

    def test_reused_id_with_different_payload_is_not_a_duplicate_success(self):
        frame = self.terminals.frame("esp8266")
        body = {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": "D"}
        self.assertEqual(self.terminals.command("esp8266", body)["status"], "accepted")
        self.assertEqual(self.terminals.command("esp8266", body)["status"], "duplicate")
        self.assertEqual(self.terminals.command("esp8266", {**body, "key": "#"})["status"], "rejected")

    def test_arrival_on_different_track_is_one_command(self):
        package = test_shared_traffic.runtime_package_v3()
        package["publication_id"] = "extra-track"
        package["tracks"].append({**package["tracks"][-1], "id":"track-b-extra", "station_id":"station-b", "display_label":"2", "active":True, "sort_order":999})
        self.fixture.install(package)
        self.depart()
        self.send("esp32", "B")
        self.send("esp32", "D")
        selected = self.terminals.views._tracks(self.terminals.views.terminals["esp32"])[1].id
        self.send("esp32", "#")
        state = self.fixture.ops.tkl_station_state(self.fixture.publication.publication_id, "Lör", "station-b")
        self.assertEqual(state["movements"]["movement-101-b"]["actualTrack"], selected)
        self.assertEqual(self.service.open_cases(None), [])

    def test_queue_can_be_left_and_reopened_without_typing_number(self):
        self.send("esp8266", "#", train_number="101")
        self.send("esp32", "B")
        self.assertEqual(self.terminals.views.terminals["esp32"].screen, "overview")
        self.send("esp32", "A")
        self.assertEqual(self.terminals.frame("esp32")["keys"]["#"]["label"], "Ge klart")

    def test_gateway_keeps_config_quiet_and_refuses_retained_or_old_boot_commands(self):
        messages = []
        gateway = Terminal16Gateway(self.terminals, lambda *args: messages.append(args))
        base = gateway.PREFIX + "esp8266/"
        hello = json.dumps({"boot": "test", "device_code": "esp8266"}).encode()
        gateway.on_message(base + "hello", hello)
        self.assertEqual(len(messages), 1)
        gateway.tick()
        self.assertEqual(len(messages), 1)
        gateway.on_message(base + "presence", json.dumps({"boot":"test", "nonce":"one"}).encode())
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[-1][0].endswith("/alive"))
        frame = messages[0][1]["frame"]
        body = {"boot":"test", "key":"#", "train_number":"101", "entry_context":frame["entry"]["context"],
                "view_token":frame["view_token"], "command_id":"one"}
        gateway.on_message(base + "command", json.dumps(body).encode(), retained=True)
        gateway.on_message(base + "command", json.dumps({**body, "boot":"previous"}).encode())
        self.assertEqual(len(messages), 2)
        gateway.on_message(base + "command", json.dumps(body).encode())
        self.assertTrue(any(item[0].endswith("/ack") and item[1]["status"]=="accepted" for item in messages))

    def test_a_request_travels_from_one_box_to_the_other_over_mqtt(self):
        """Benny begärde klartecken från en box och såg inget på den andra
        (2026-10-01). Hela vägen box → server → box: avsändarens två tryck på
        # ska ge mottagaren en ny bild med förfrågan, utan att den trycker."""

        messages = []
        gateway = Terminal16Gateway(self.terminals, lambda *args: messages.append(args))
        for device in ("esp8266", "esp32"):
            gateway.on_message(gateway.PREFIX + device + "/hello",
                               json.dumps({"boot": device + "-1", "device_code": device}).encode())
        frames = lambda device: [m[1]["frame"] for m in messages if m[0] == gateway.PREFIX + device + "/frame"]
        self.assertEqual(0, frames("esp32")[-1]["requests"]["count"])
        pushed_before = len(frames("esp32"))

        def press(body, command_id):
            gateway.on_message(gateway.PREFIX + "esp8266/command",
                               json.dumps({"boot": "esp8266-1", "command_id": command_id, **body}).encode())
            ack = [m[1] for m in messages if m[0] == gateway.PREFIX + "esp8266/ack"][-1]
            self.assertEqual("accepted", ack["status"], ack)
            return ack["frame"]

        sender = frames("esp8266")[-1]
        # 101# finds the train and asks LEK in the same press (2.1.0).
        press({"key": "#", "train_number": "101", "entry_context": sender["entry"]["context"],
               "view_token": sender["view_token"]}, "search")
        pushed = frames("esp32")
        self.assertGreater(len(pushed), pushed_before, "the receiving box got no new frame")
        self.assertEqual(1, pushed[-1]["requests"]["count"])
        self.assertTrue(pushed[-1]["lines"][0].startswith("CDA?101"), pushed[-1]["lines"])
        self.assertTrue(pushed[-1]["lines"][1].startswith("#Ja *Nej"), pushed[-1]["lines"])


def through_package(publication_id="through-102"):
    """Benny's 102 (2026-10-02), on the fixture's two stations: in from LEK to
    CDA, and on to LEK again with the same number. CDA plays his VAG."""
    package = runtime_package_v3(publication_id=publication_id)
    stops = [("station-b", "LEK", None, "09:40"), ("station-a", "CDA", "09:50", "09:55"), ("station-b", "LEK", "10:05", None)]
    package["services"].append({"id": "service-102-Dagl", "train_number": "102", "days": "Dagl", "train_type": "person",
        "stops": [{"station_id": station, "station_name": code, "stop_order": order, "arrival_time": arrival,
                   "departure_time": departure, "service_day_offset": 0, "service_minute": 580 + 10 * order}
                  for order, (station, code, arrival, departure) in enumerate(stops)]})
    template = next(row for row in package["trains"] if row["id"] == "movement-101-a")
    for order, (station, code, arrival, departure) in enumerate(stops):
        package["trains"].append({**deepcopy(template), "id": f"movement-102-{order}", "train_number": "102",
            "service_id": "service-102-Dagl", "station_id": station, "station": code, "arrival_time": arrival,
            "departure_time": departure, "sort_time": arrival or departure, "stop_order": order,
            "arrival_from": None, "departure_to": None,
            "track_id": "track-station-a-2" if station == "station-a" else "track-station-b-1"})
    return package


def unmanned_package(publication_id="unmanned-103", track="track-station-a-2", direct=False):
    """Benny's meet as he runs it, with boxes on two stations only: 103 comes
    in to CDA from VA, where there is no box, and goes on to LEK. CDA plays
    his VAG and LEK his MUN."""
    package = runtime_package_v3(publication_id=publication_id)
    package["stations"].append({**package["stations"][1], "id": "station-c", "code": "VA", "name": "Vagnsta", "diagram_order": 2})
    package["display"]["graph_station_order"].append("station-c")
    package["tracks"].append({**package["tracks"][2], "id": "track-station-c-1", "station_id": "station-c"})
    package["tracks"].append({**package["tracks"][2], "id": "track-station-b-2", "display_label": "2", "sort_order": 2})
    package["connections"].append({**package["connections"][0], "id": "connection-a-c", "station_b_id": "station-c"})
    package["panels"][0]["slots"]["B"] = "connection-a-c"
    package["panels"].append({"id": "panel-c", "station_id": "station-c", "name": "VA",
                              "slots": {"A": "connection-a-c", "B": None, "C": None, "D": None}})
    if direct:
        package["connections"][0]["dispatch_mode_override"] = "direct"
    stops = [("station-c", "VA", None, "09:40", "track-station-c-1"), ("station-a", "CDA", "09:50", "09:55", track),
             ("station-b", "LEK", "10:05", None, "track-station-b-2")]
    package["services"].append({"id": "service-103-Dagl", "train_number": "103", "days": "Dagl", "train_type": "person",
        "stops": [{"station_id": station, "station_name": code, "stop_order": order, "arrival_time": arrival,
                   "departure_time": departure, "service_day_offset": 0, "service_minute": 580 + 10 * order}
                  for order, (station, code, arrival, departure, _) in enumerate(stops)]})
    template = next(row for row in package["trains"] if row["id"] == "movement-101-a")
    for order, (station, code, arrival, departure, track_id) in enumerate(stops):
        package["trains"].append({**deepcopy(template), "id": f"movement-103-{order}", "train_number": "103",
            "service_id": "service-103-Dagl", "station_id": station, "station": code, "arrival_time": arrival,
            "departure_time": departure, "sort_time": arrival or departure, "stop_order": order,
            "arrival_from": None, "departure_to": None, "track_id": track_id})
    return package


class _Boxes(unittest.TestCase):
    """Real boxes on the shared station service, without RuntimeTerminalTests' tests."""

    setUp, tearDown, send = RuntimeTerminalTests.setUp, RuntimeTerminalTests.tearDown, RuntimeTerminalTests.send


class ThroughTrainTests(_Boxes):
    """A train that arrives and leaves again with the same number (Benny's 102)."""

    def setUp(self):
        super().setUp()
        self.fixture.install(through_package())
        self.terminals = Terminal16Service(self.service)

    def lookup(self, device, number="102"):
        return self.send(device, "#", train_number=number)["frame"]

    def movement(self, station, movement_id):
        return next(m for m in self.service.snapshot_payload(station)["movements"] if m["id"] == movement_id)

    def test_bennys_102_is_asked_for_at_once_and_jumps_here_when_sent_on(self):
        # Benny's film: 102 # at VAG gave LEK-102 without a #, and MUN got
        # nothing. His photos: cleared, but no #Avg and no #In, ever. Since
        # 2.1.0 102# asks LEK at once, and once cleared it goes; the system
        # never saw it come, so it jumps here (Casper, 2026-10-02).
        frame = self.lookup("esp8266")
        self.assertEqual(["LEK?102         ", "*Åter B:Öv 09:15"], frame["lines"])
        lek = self.terminals.frame("esp32")
        self.assertEqual(1, lek["requests"]["count"])
        self.assertTrue(lek["lines"][0].startswith("CDA?102"), lek["lines"])
        self.send("esp32", "#")
        frame = self.terminals.frame("esp8266")
        self.assertEqual("Rapportera avgång", frame["keys"]["#"]["label"])
        self.assertEqual("LEK<102         ", frame["lines"][0])
        self.send("esp8266", "#")
        self.assertEqual(("arrived", "departed"), tuple(self.movement("station-a", "movement-102-1")[k] for k in ("arrival", "departure")))
        self.assertEqual("departed", self.movement("station-b", "movement-102-0")["departure"])
        self.assertEqual("Rapportera ankomst", self.terminals.frame("esp32")["keys"]["#"]["label"])

    def test_an_occupied_track_takes_the_train_in_and_says_so(self):
        package = through_package("through-102-busy-track")
        next(t for t in package["trains"] if t["id"] == "movement-102-1")["track_id"] = "track-station-a-1"   # 101 stands there
        self.fixture.install(package)
        self.terminals = Terminal16Service(self.service)
        self.lookup("esp32")                                # LEK asks CDA
        self.lookup("esp8266"); self.send("esp8266", "#")    # CDA clears
        self.send("esp32", "#")                              # LEK departs
        answer = self.send("esp8266", "#")                   # CDA takes it in on track 1
        self.assertEqual("102 UPPT SPÅR   ", answer["frame"]["lines"][0])
        self.assertEqual("arrived", self.movement("station-a", "movement-102-1")["arrival"])

    def test_received_first_then_sent_on(self):
        self.lookup("esp32")
        frame = self.lookup("esp8266")          # the request, not the departure
        self.assertTrue(frame["lines"][0].startswith("LEK?102"), frame["lines"])
        self.assertEqual("Ge klart", frame["keys"]["#"]["label"])
        self.send("esp8266", "#")
        self.send("esp32", "#")
        self.assertEqual("Rapportera ankomst", self.lookup("esp8266")["keys"]["#"]["label"])
        self.send("esp8266", "#")
        frame = self.lookup("esp8266")          # arrived: now it is the departure, asked at once
        self.assertTrue(frame["lines"][0].startswith("LEK?102"), frame["lines"])
        self.assertEqual(1, self.terminals.frame("esp32")["requests"]["count"])

    def install_twin(self):
        """Another train 102, from CDA to LEK: the number alone cannot tell them apart."""
        package = through_package("through-102-twice")
        twin = deepcopy(next(s for s in package["services"] if s["train_number"] == "101"))
        twin.update(id="service-102b", train_number="102")
        package["services"].append(twin)
        for row in [r for r in package["trains"] if r["train_number"] == "101"]:
            package["trains"].append({**deepcopy(row), "id": row["id"].replace("101", "102b"),
                                      "train_number": "102", "service_id": "service-102b"})
        self.fixture.install(package)
        self.terminals = Terminal16Service(self.service)

    def test_two_trains_with_one_number_say_so_instead_of_falling_silent(self):
        self.install_twin()
        self.assertEqual("FLERA TÅG ADMIN ", self.lookup("esp8266")["lines"][0])

    def test_of_two_trains_with_one_number_the_one_under_way_is_meant(self):
        self.install_twin()
        self.lookup("esp32")
        frame = self.lookup("esp8266")
        self.assertTrue(frame["lines"][0].startswith("LEK?102"), frame["lines"])
        self.assertEqual("Ge klart", frame["keys"]["#"]["label"])


class CommandErrorTests(_Boxes):
    def test_an_error_answers_no_with_the_screen_instead_of_silence(self):
        before = self.terminals.frame("esp8266")
        body = {"command_id": uuid4().hex, "view_token": before["view_token"], "key": "#"}
        with patch("tmbox_gateway.terminal16.Terminal16Lab.command", side_effect=ValueError("broken")), \
                self.assertLogs("tmbox_gateway.terminal16", "ERROR"):
            answer = self.terminals.command("esp8266", body)
        self.assertEqual("rejected", answer["status"])
        self.assertEqual(before["lines"], answer["frame"]["lines"])
        # Nothing was kept: a press on the screen it was sent works.
        self.assertEqual("accepted", self.terminals.command("esp8266", {
            "command_id": uuid4().hex, "view_token": answer["frame"]["view_token"], "key": "#"})["status"])


class PublicationParsedOnceTests(_Boxes):
    """Until 2.0.2 every frame parsed the whole publication again, once per
    station: on a Pi with a real meet a key press took four seconds."""

    def test_frames_and_presses_parse_the_publication_once(self):
        from tmbox_gateway.runtime import RuntimePublication
        parse = RuntimePublication.parse
        with patch.object(RuntimePublication, "parse", side_effect=parse) as parsed:
            for _ in range(5):
                self.terminals.frame("esp8266"); self.terminals.frame("esp32")
            self.send("esp8266", "#", train_number="101")
            self.assertEqual(0, parsed.call_count, "the installed publication is already parsed")
            self.send("esp8266", "*"); self.send("esp8266", "#")  # take it back: nothing open
            self.fixture.install(through_package("parsed-again"))  # installing parses, as before
            parsed.reset_mock()
            for _ in range(5):
                self.terminals.frame("esp8266"); self.terminals.frame("esp32")
            # The new publication is the one the boxes see: 102 only exists there.
            self.assertEqual("LEK?102         ", self.send("esp8266", "#", train_number="102")["frame"]["lines"][0])
            self.send("esp32", "#")
            self.assertEqual(0, parsed.call_count, "the new publication is parsed once, on activation")

    def test_nobody_changes_the_shared_publication(self):
        import json
        stored = json.loads(self.fixture.runtime._connection.execute(
            "SELECT payload_json FROM runtime_publications WHERE active = 1").fetchone()[0])
        self.send("esp8266", "#", train_number="101"); self.send("esp32", "#")
        self.send("esp8266", "#"); self.send("esp32", "#")
        self.assertEqual(stored, self.service.publication().payload)


class OnePressRequestTests(_Boxes):
    """101# is enough (Casper, 2026-10-02): the request goes with the lookup,
    and * takes it back until the receiver has answered."""

    def lookup(self, device, number="101"):
        return self.send(device, "#", train_number=number)["frame"]

    def test_the_number_asks_at_once_and_star_takes_it_back_before_the_answer(self):
        frame = self.lookup("esp8266")
        self.assertEqual(["LEK?101         ", "*Åter B:Öv 09:15"], frame["lines"])
        self.assertEqual(["waiting"], [case["status"] for case in self.service.open_cases(None)])
        self.assertEqual(1, self.terminals.frame("esp32")["requests"]["count"])
        self.assertEqual("ÅTER 101?       ", self.send("esp8266", "*")["frame"]["lines"][0])
        self.send("esp8266", "#")
        self.assertEqual([], self.service.open_cases(None))
        self.assertEqual(0, self.terminals.frame("esp32")["requests"]["count"])

    def test_the_receivers_number_only_shows_the_request(self):
        self.lookup("esp8266")
        frame = self.lookup("esp32")
        self.assertEqual("Ge klart", frame["keys"]["#"]["label"])
        self.assertEqual(["waiting"], [case["status"] for case in self.service.open_cases(None)])

    def test_a_request_that_cannot_go_shows_the_train_and_why(self):
        self.fixture.install(through_package("one-press-busy"))
        self.terminals = Terminal16Service(self.service)
        self.lookup("esp32", "102")                        # LEK asks for 102 to CDA on the single track
        frame = self.terminals.frame("esp8266")
        answer = self.terminals.command("esp8266", {"command_id": uuid4().hex, "view_token": frame["view_token"],
                                                    "key": "#", "train_number": "101", "entry_context": frame["entry"]["context"]})
        self.assertEqual(("rejected", "Sträckan är upptagen"), (answer["status"], answer["message"]))
        self.assertEqual(["LEK-101         ", "#Beg A:Kö  09:15"], answer["frame"]["lines"])
        self.assertEqual(["movement-102-0"], [case["movement_id"] for case in self.service.open_cases(None)])

    def test_a_direct_line_is_reserved_at_once(self):
        self.fixture.install(unmanned_package("one-press-direct", direct=True))
        self.terminals = Terminal16Service(self.service)
        frame = self.lookup("esp8266")
        self.assertEqual(["approved"], [case["status"] for case in self.service.open_cases(None)])
        self.assertEqual("Rapportera avgång", frame["keys"]["#"]["label"])


class NeverSentTests(_Boxes):
    """Benny's 102 (2026-10-02, photos): cleared VAG -> MUN, but the system
    never saw it come, so VAG got no #Avg and MUN no #In, ever. 103 here
    comes from VA, goes through CDA (his VAG) and on to LEK (his MUN)."""

    def setUp(self):
        super().setUp()
        self.install()

    def install(self, **options):
        self.fixture.install(unmanned_package(**options))
        self.terminals = Terminal16Service(self.service)

    def cleared(self):
        self.send("esp8266", "#", train_number="103")       # CDA asks LEK at once
        self.send("esp32", "#")                              # LEK clears
        return self.terminals.frame("esp8266")

    def movement(self, station, movement_id):
        return next(m for m in self.service.snapshot_payload(station)["movements"] if m["id"] == movement_id)

    def test_sent_on_it_jumps_here_whoever_was_to_send_it(self):
        # Whether VA has a box, a TKL shift or nobody makes no difference.
        self.fixture.ids.record_discovery("esp-va", "esp-va", protocol_version=2)
        self.fixture.ids.assign_discovered_device("esp-va", station_id="station-c")
        frame = self.cleared()
        self.assertEqual("Rapportera avgång", frame["keys"]["#"]["label"])
        self.assertEqual("LEK<103         ", frame["lines"][0])
        self.send("esp8266", "#")
        movement = self.movement("station-a", "movement-103-1")
        self.assertEqual(("arrived", "departed"), (movement["arrival"], movement["departure"]))
        self.assertEqual("departed", self.movement("station-c", "movement-103-0")["departure"])
        self.assertEqual("Rapportera ankomst", self.terminals.frame("esp32")["keys"]["#"]["label"])
        self.send("esp32", "#")
        self.assertEqual([], self.service.open_cases(None))

    def test_an_occupied_planned_track_does_not_stop_it(self):
        self.install(publication_id="unmanned-103-busy-track", track="track-station-a-1")   # 101 stands there
        frame = self.cleared()
        answer = self.terminals.command("esp8266", {"command_id": uuid4().hex, "view_token": frame["view_token"], "key": "#"})
        self.assertEqual("accepted", answer["status"], answer)
        movement = self.movement("station-a", "movement-103-1")
        self.assertEqual(("arrived", "departed", "track-station-a-1"),
                         (movement["arrival"], movement["departure"], movement["actualTrack"]))

    def test_the_receiver_places_a_train_nobody_sent(self):
        """Casper: placed on a track afterwards, with the timetable's help."""
        frame = self.send("esp32", "#", train_number="103")["frame"]
        self.assertEqual(["FLYTTA 103 HIT? ", "#Ja B:Sp   09:15"], frame["lines"])
        self.assertEqual("Flytta hit", frame["keys"]["#"]["label"])
        self.assertEqual([], self.service.open_cases(None))   # the lookup sent nothing
        answer = self.send("esp32", "#")
        self.assertEqual("103 MOTTAGET    ", answer["frame"]["lines"][0])
        self.assertEqual(("arrived", "track-station-b-2"),
                         tuple(self.movement("station-b", "movement-103-2")[k] for k in ("arrival", "actualTrack")))
        # The whole route before it is over: VA and CDA have nothing left of 103.
        self.assertEqual("departed", self.movement("station-c", "movement-103-0")["departure"])
        self.assertEqual(("arrived", "departed"),
                         tuple(self.movement("station-a", "movement-103-1")[k] for k in ("arrival", "departure")))

    def test_a_cleared_train_never_reported_departed_is_placed_through_the_track_picker(self):
        self.cleared()
        frame = self.terminals.frame("esp32")
        self.assertNotIn("#", frame["keys"])                  # the # after #Ja never takes it in
        self.assertEqual("Placera på spår…", frame["keys"]["B"]["label"])
        self.send("esp32", "B")
        self.send("esp32", "#")
        self.assertEqual("arrived", self.movement("station-b", "movement-103-2")["arrival"])
        self.assertEqual("departed", self.movement("station-a", "movement-103-1")["departure"])
        self.assertEqual([], self.service.open_cases(None))

    def test_a_cleared_train_never_reported_departed_is_moved_here_when_typed(self):
        """Issue #115: the sender forgot to report the departure; the
        receiver types the number and the box asks to move it here."""
        self.cleared()
        self.send("esp32", "*")
        frame = self.send("esp32", "#", train_number="103")["frame"]
        self.assertEqual("FLYTTA 103 HIT? ", frame["lines"][0])
        answer = self.send("esp32", "#")
        self.assertEqual("103 MOTTAGET    ", answer["frame"]["lines"][0])
        self.assertEqual("arrived", self.movement("station-b", "movement-103-2")["arrival"])
        self.assertEqual("departed", self.movement("station-a", "movement-103-1")["departure"])
        self.assertEqual([], self.service.open_cases(None))


if __name__ == "__main__":
    unittest.main()


class TakenBackTests(_Boxes):
    """Benny's table, 2026-10-03: the sender takes 101 back. Both stations are
    told "101 ÅTERTAGET" and go home by themselves after three seconds; the
    receiver neither stays on INGA FRÅGOR nor keeps a #In for a train that
    never left. Nothing waits for #OK (Casper)."""

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.terminals = Terminal16Service(self.service, now=lambda: self.now)

    def lines(self, device):
        return self.terminals.frame(device)["lines"]

    def take_back(self):
        self.assertEqual("ÅTER 101?       ", self.send("esp8266", "*")["frame"]["lines"][0])
        self.send("esp8266", "#")

    def assert_both_told_then_home(self):
        self.assertEqual(["101 ÅTERTAGET   ", "LEK        09:15"], self.lines("esp8266"))
        self.assertEqual(["101 ÅTERTAGET   ", "CDA        09:15"], self.lines("esp32"))
        self.now += 3
        for device in ("esp8266", "esp32"):
            self.assertEqual(" " * 16, self.lines(device)[0], device)
        self.assertEqual([], self.service.open_cases(None))

    def test_a_request_taken_back_before_the_answer(self):
        self.send("esp8266", "#", train_number="101")
        self.assertIn("CDA?101", self.lines("esp32")[0])            # LEK's queue is open
        self.take_back()
        self.assertNotIn("INGA FRÅGOR", self.lines("esp32")[0])
        self.assert_both_told_then_home()

    def test_a_clearance_taken_back_before_the_departure(self):
        self.send("esp8266", "#", train_number="101")
        self.send("esp32", "#")                                       # LEK clears
        self.take_back()
        frame = self.terminals.frame("esp32")
        self.assertNotIn("#In", frame["lines"][1])
        self.assertFalse(frame["keys"]["#"]["acts"], "# only closes the notice")
        self.assert_both_told_then_home()

    def test_a_rejection_tells_both_and_asks_for_nothing(self):
        self.send("esp8266", "#", train_number="101")
        self.send("esp32", "*"); self.send("esp32", "#")             # NEKA 101? -> yes
        self.assertEqual(["101 NEKAT       ", "LEK        09:15"], self.lines("esp8266"))
        self.assertEqual(["101 NEKAT       ", "CDA        09:15"], self.lines("esp32"))
        self.now += 3
        self.assertEqual(" " * 16, self.lines("esp8266")[0])
