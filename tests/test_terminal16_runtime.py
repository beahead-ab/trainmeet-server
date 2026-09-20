from unittest.mock import patch
from uuid import uuid4
import unittest

import test_shared_traffic
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
        self.send("esp8266", "#")
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

    def test_recreated_profile_restores_departed_train_without_replay(self):
        self.depart()
        self.terminals = Terminal16Service(self.service)
        self.assertIn("101", self.terminals.frame("esp32")["lines"][0])
        self.send("esp32", "#", train_number="101")
        self.send("esp32", "#")
        self.assertEqual(self.service.open_cases(None), [])

    def test_lost_commit_does_not_release_line_or_change_arrival(self):
        self.depart()
        with patch.object(self.fixture.ops, "release_clearance", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                self.send("esp32", "#")
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
        self.send("esp8266", "#")
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
                self.send("esp8266", "#")
                self.send("esp32", "*")
                self.send("esp32", "#")
                self.assertEqual(self.service.open_cases(None), [])

    def test_operator_language_menu_persists_and_admin_can_replace_it(self):
        self.send("esp8266", "*")
        for _ in range(3): self.send("esp8266", "D")
        self.send("esp8266", "#")
        self.assertEqual(self.fixture.ids.device_language("esp8266"), "en")
        frame = self.terminals.frame("esp8266")
        self.assertEqual(frame["keys"]["#"]["label"], "Show upcoming trains")
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
        self.send("esp8266", "#")
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
