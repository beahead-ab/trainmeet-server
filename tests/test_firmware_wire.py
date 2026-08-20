"""The wire between the physical TMBox firmware and this server.

Every payload here is transcribed from `firmware/esp32/TrainMeetTMBox.ino` in
trainmeet-tmbox, and every field read back is one the firmware actually parses.
Both sides were written against `docs/tmbox.md`, but nothing had ever run one
against the other: the firmware's own CI only compiles it, and this server's
tests wrote their own idea of a box message. A field renamed on either side
would have passed both suites and failed on the bench.

The firmware slice in flight publishes `hello`, `presence` and one complete
write command, and reads `assignment`, `config`, `snapshot` and `ack`. That is
the whole contract this file pins down.
"""

from __future__ import annotations

import json

from tmbox_gateway.mqtt_v2 import device_topic

from test_protocol_v2 import DEPARTURE, DEVICE, STATION, ProtocolV2Base


# --------------------------------------------------------------- box → server
# Transcribed from publishHello(), publishPresence() and sendPositionCommand().
# The firmware sends no `display` in hello, no `hardware_version`, and no
# `expected_revision` on a command; those omissions are the point of the test.

FIRMWARE_HELLO = {
    "device_code": DEVICE,
    "model": "TrainMeet TMBox",
    "firmware_version": "0.3.0",
}

FIRMWARE_PRESENCE = {
    "status": "online",
    "device_code": DEVICE,
    "uptime_ms": 41234,
}


def firmware_command(message_id: str, movement_id: str) -> dict:
    return {
        "protocol_version": 2,
        "message_id": message_id,
        "device_id": DEVICE,
        "station_id": STATION,
        "action": "train.position.set",
        "payload": {"movement_id": movement_id},
    }


class FirmwareWireTests(ProtocolV2Base):
    def _send_raw(self, leaf: str, body: dict) -> None:
        """Publish exactly the bytes the firmware would put on the wire."""
        self.gateway.on_message(
            device_topic(DEVICE, leaf), json.dumps(body).encode("utf-8")
        )

    def _last(self, leaf: str) -> dict:
        topic = device_topic(DEVICE, leaf)
        matching = [payload for published, payload, _ in self.published if published == topic]
        self.assertTrue(matching, f"servern publicerade aldrig {leaf}")
        return matching[-1]

    # ---------------------------------------------------------------- inbound

    def test_hello_without_display_is_accepted_and_answered(self):
        """The box announces itself with three fields and gets a full state."""
        self._send_raw("hello", FIRMWARE_HELLO)

        topics = [topic for topic, _ in self._retained()]
        self.assertEqual(
            topics,
            [
                device_topic(DEVICE, "assignment"),
                device_topic(DEVICE, "config"),
                device_topic(DEVICE, "snapshot"),
            ],
        )

    def test_presence_republishes_the_retained_state(self):
        self._send_raw("presence", FIRMWARE_PRESENCE)

        self.assertIn(device_topic(DEVICE, "assignment"), [t for t, _ in self._retained()])

    def test_a_command_without_expected_revision_is_accepted(self):
        """The firmware does not send a revision yet; the write is optimistic.

        Pinned deliberately: when the firmware starts sending one, this test
        should be the thing that fails and forces the decision, rather than a
        box in a meeting hall silently having its writes refused.
        """
        self._send_raw("command", firmware_command("m-1", DEPARTURE))

        acknowledgement = self._acks()[0]
        self.assertEqual(acknowledgement["status"], "accepted")

    def test_the_same_command_twice_is_not_a_second_decision(self):
        """A reconnect replays the box's in-flight command; it must not double."""
        self._send_raw("command", firmware_command("m-dup", DEPARTURE))
        self._send_raw("command", firmware_command("m-dup", DEPARTURE))

        first, second = self._acks()[0], self._acks()[1]
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "duplicate")

    # --------------------------------------------------------------- outbound

    def test_assignment_carries_the_fields_the_firmware_reads(self):
        """handleAssignment() reads exactly `status` and `station_id`."""
        self._send_raw("hello", FIRMWARE_HELLO)
        assignment = self._last("assignment")

        self.assertEqual(assignment["status"], "assigned")
        self.assertEqual(assignment["station_id"], STATION)

    def test_config_carries_the_fields_the_firmware_reads(self):
        """handleConfig() reads `config_version` and `station.code`/`.name`."""
        self._send_raw("hello", FIRMWARE_HELLO)
        config = self._last("config")

        self.assertIn("config_version", config)
        self.assertIn("code", config["station"])
        self.assertIn("name", config["station"])

    def test_snapshot_movements_carry_the_fields_the_firmware_reads(self):
        """handleSnapshot() reads id, train_number, departure, arrival, crewReady.

        `crewReady` is camelCase on the wire while its neighbours are
        snake_case. That is not tidy, but it is what both sides do, and a
        silent rename here is exactly the bug this file exists to catch.
        """
        self._send_raw("hello", FIRMWARE_HELLO)
        snapshot = self._last("snapshot")

        self.assertTrue(snapshot["movements"], "stationen hade inga rörelser")
        for movement in snapshot["movements"]:
            for field in ("id", "train_number", "departure", "arrival", "crewReady"):
                self.assertIn(field, movement)

    def test_ack_carries_the_status_the_firmware_branches_on(self):
        """handleAck() shows KOMMANDO OK only on `status == "accepted"`."""
        self._send_raw("command", firmware_command("m-ack", DEPARTURE))

        self.assertEqual(self._acks()[0]["status"], "accepted")

    def test_an_unassigned_box_is_told_to_wait_rather_than_left_silent(self):
        """handleAssignment() shows KOPPLA BOXEN for any status but assigned."""
        self.identities.record_discovery("TMBOX-NEW01", "TMBOX-NEW01")
        self.gateway.on_message(
            device_topic("TMBOX-NEW01", "hello"),
            json.dumps({**FIRMWARE_HELLO, "device_code": "TMBOX-NEW01"}).encode("utf-8"),
        )

        topic = device_topic("TMBOX-NEW01", "assignment")
        assignment = [payload for published, payload, _ in self.published if published == topic][-1]
        self.assertEqual(assignment["status"], "waiting_for_assignment")
