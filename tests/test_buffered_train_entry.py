"""One confirmed box input, same server traffic rules as legacy key entry."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from test_engine import EngineDriver
from test_mqtt_commands import command_payload
from tmbox_gateway.models import Command, ConnectionState, DispatchMode
from tmbox_gateway.mqtt_adapter import _decode_command


class BufferedTrainEntryTests(unittest.TestCase):
    def setUp(self):
        self.driver = EngineDriver()
        self.engine = self.driver.engine
        self.driver.press("panel-a", "A")

    def command(self, number="421", **changes):
        now = datetime.now(timezone.utc)
        return replace(Command("submit-1", "client-panel-a", "test-session", "panel-a",
                               self.engine.revision, "#", now, now + timedelta(seconds=5), number), **changes)

    def test_mqtt_decoder_preserves_complete_input(self):
        payload = {**command_payload(), "key": "#", "train_number": "00421"}
        self.assertEqual(_decode_command(payload, use_gateway_clock=True).train_number, "00421")
        self.assertIsNone(_decode_command(command_payload(), use_gateway_clock=True).train_number)

    def test_no_server_digits_until_one_confirmed_request(self):
        snapshot = self.engine.snapshot("panel-a")
        self.assertTrue(snapshot["interaction"]["local_train_entry"])
        self.assertEqual(snapshot["interaction"]["train_number"], "")
        revision = self.engine.revision
        command = self.command("00421")
        ack = self.engine.press(command)
        self.assertEqual(ack.status, "accepted")
        self.assertEqual(self.engine.revision, revision + 1)
        line = next(iter(self.engine.connections.values()))
        self.assertEqual(line.train_number, "00421")
        self.assertEqual(line.state, ConnectionState.REQUESTED)
        self.assertEqual(self.engine.press(command).status, "duplicate")
        self.assertEqual(self.engine.revision, revision + 1)

    def test_invalid_input_is_atomic(self):
        for index, number in enumerate(("", "123456", "12A", " 421", "٤٢١", 421, ["4"])):
            before = self.engine.snapshot("panel-a")
            ack = self.engine.press(self.command(number, command_id=f"bad-{index}"))
            self.assertEqual((ack.status, ack.reason), ("rejected", "invalid_train_number"))
            self.assertEqual(self.engine.snapshot("panel-a"), before)

    def test_wrong_owner_stale_session_expiry_and_nonconfirm_are_rejected(self):
        for index, (changes, reason) in enumerate((
            ({"client_id": "other-box"}, "interaction_owned"),
            ({"expected_revision": -1}, "stale_revision"),
            ({"traffic_session_id": "other-meet"}, "wrong_session"),
            ({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}, "expired_command"),
            ({"key": "*"}, "train_entry_requires_hash"),
        )):
            ack = self.engine.press(self.command(command_id=f"unsafe-{index}", **changes))
            self.assertEqual((ack.status, ack.reason), ("rejected", reason))
        self.assertEqual(self.engine.snapshot("panel-a")["interaction"]["train_number"], "")

    def test_cancel_discards_unsubmitted_input_and_stale_submit_cannot_reopen(self):
        self.assertEqual(self.driver.press("panel-a", "*").status, "accepted")
        ack = self.engine.press(self.command())
        self.assertEqual(ack.reason, "not_entering_train")
        self.assertTrue(all(line.state == ConnectionState.FREE for line in self.engine.connections.values()))

    def test_direct_mode_reserves_without_departure(self):
        self.driver = EngineDriver(DispatchMode.DIRECT)
        self.engine = self.driver.engine
        self.driver.press("panel-a", "A")
        self.assertEqual(self.engine.press(self.command()).status, "accepted")
        self.assertEqual(next(iter(self.engine.connections.values())).state, ConnectionState.RESERVED)
