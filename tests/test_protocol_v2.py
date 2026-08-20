from __future__ import annotations

import itertools
import json
import tempfile
import unittest
from pathlib import Path

from runtime_fixture import fictional_runtime_package
from tmbox_gateway.identity import DisplayCapability, IdentityStore
from tmbox_gateway.mqtt_v2 import TMBoxV2Gateway, device_topic
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import TMBoxStationService
from tmbox_gateway.runtime import RuntimePublication, SQLiteRuntimeStore


DEVICE = "TMBOX-7A42F1"
STATION = "st-cda"
DEPARTURE = "movement-421-cda"


class BoxCache:
    """What a physical box keeps in RAM, replaced wholesale on every publish.

    No delta logic and no merge: each payload replaces the previous one in its
    entirety. That is exactly why the three retained topics may arrive in any
    order.
    """

    def __init__(self):
        self.assignment: dict | None = None
        self.config: dict | None = None
        self.snapshot: dict | None = None

    def apply(self, topic: str, payload: dict) -> None:
        if topic.endswith("/assignment"):
            self.assignment = payload
        elif topic.endswith("/config"):
            self.config = payload
        elif topic.endswith("/snapshot"):
            self.snapshot = payload

    @property
    def ready(self) -> bool:
        return all((self.assignment, self.config, self.snapshot))

    def state(self) -> str:
        return json.dumps(
            {"assignment": self.assignment, "config": self.config, "snapshot": self.snapshot},
            sort_keys=True,
        )


class ProtocolV2Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name) / "runtime.db"
        self.runtime_store = SQLiteRuntimeStore(root)
        self.operations_store = SQLiteOperationsStore(root)
        self.identities = IdentityStore(root)
        publication = self.runtime_store.install(fictional_runtime_package())
        self.operations_store.ensure_publication(publication)
        self.identities.record_discovery(
            DEVICE,
            DEVICE,
            model="TMBox ESP32-S3",
            firmware_version="0.3.0",
            protocol_version=2,
            display=DisplayCapability(rows=4, cols=20, charset="ascii"),
        )
        self.identities.assign_discovered_device(DEVICE, station_id=STATION)
        self.service = TMBoxStationService(
            self.runtime_store, self.operations_store, self.identities
        )
        self.published: list[tuple[str, dict, bool]] = []
        self.gateway = TMBoxV2Gateway(
            self.service,
            self.identities,
            gateway_id="charlottendal",
            publish=lambda topic, payload, retain: self.published.append(
                (topic, payload, retain)
            ),
        )

    def tearDown(self):
        self.identities.close()
        self.operations_store.close()
        self.runtime_store.close()
        self.directory.cleanup()

    def _send(self, leaf: str, body: dict) -> None:
        self.gateway.on_message(
            device_topic(DEVICE, leaf), json.dumps(body).encode("utf-8")
        )

    def _acks(self) -> list[dict]:
        return [payload for topic, payload, _ in self.published if topic.endswith("/ack")]

    def _retained(self) -> list[tuple[str, dict]]:
        return [(topic, payload) for topic, payload, retain in self.published if retain]

    # ------------------------------------------------------------ retained

    def test_hello_answers_with_assignment_config_and_snapshot(self):
        self._send("hello", {"device_code": DEVICE, "display": {"rows": 4, "cols": 20}})

        topics = [topic for topic, _ in self._retained()]
        self.assertEqual(
            topics,
            [
                device_topic(DEVICE, "assignment"),
                device_topic(DEVICE, "config"),
                device_topic(DEVICE, "snapshot"),
            ],
        )
        assignment = self._retained()[0][1]
        self.assertEqual(assignment["status"], "assigned")
        self.assertEqual(assignment["station_id"], STATION)

    def test_config_carries_the_station_topology_and_track_catalogue(self):
        config = self.service.config_payload(
            STATION, DisplayCapability(rows=4, cols=20, charset="ascii")
        )

        self.assertEqual(config["station"]["code"], "CDA")
        self.assertEqual(
            [track["display_label"] for track in config["tracks"]],
            ["1A", "1B", "2A", "2B"],
        )
        # Three neighbours, laid out as display rows rather than A-D slots.
        self.assertEqual([row["display_row"] for row in config["connections"]], [1, 2, 3])
        self.assertEqual(
            {row["other_station_code"] for row in config["connections"]},
            {"LEK", "VST", "KUN"},
        )
        self.assertEqual(config["display"], {"rows": 4, "cols": 20, "charset": "ascii"})

    def test_a_box_without_a_station_is_told_to_wait(self):
        self.identities.record_discovery("TMBOX-NEW", "TMBOX-NEW")
        self.gateway.on_message(
            device_topic("TMBOX-NEW", "hello"),
            json.dumps({"device_code": "TMBOX-NEW"}).encode("utf-8"),
        )

        published = [payload for _, payload, _ in self.published]
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["status"], "waiting_for_assignment")
        self.assertIsNone(published[0]["station_id"])

    def test_retained_assignment_config_and_snapshot_may_arrive_in_any_order(self):
        """The named reconnect case from docs/tmbox.md section 19.

        A box that reconnects receives three retained topics with no ordering
        guarantee whatsoever. Every order has to leave it in the same, correct
        state - this is the situation a physical box meets at every restart.
        """
        self._send("hello", {"device_code": DEVICE})
        retained = self._retained()
        self.assertEqual(len(retained), 3)

        states = set()
        for order in itertools.permutations(retained):
            cache = BoxCache()
            for topic, payload in order:
                cache.apply(topic, payload)
            self.assertTrue(cache.ready)
            states.add(cache.state())

        self.assertEqual(len(states), 1, "olika ankomstordning gav olika slutläge")
        settled = json.loads(states.pop())
        self.assertEqual(settled["assignment"]["station_id"], STATION)
        self.assertEqual(settled["snapshot"]["station_id"], STATION)
        self.assertEqual(
            settled["config"]["config_version"],
            settled["snapshot"]["revision"]["config_version"],
        )

    def test_there_is_no_event_replay_surface(self):
        self._send("hello", {"device_code": DEVICE})

        for _, payload in self._retained():
            self.assertNotIn("last_event_id", payload)
            self.assertNotIn("events", payload)

    # ------------------------------------------------------------ commands

    def test_a_complete_command_moves_the_movement_and_raises_its_revision(self):
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "m-1",
                "device_id": DEVICE,
                "station_id": STATION,
                "action": "train.position.set",
                "payload": {"movement_id": DEPARTURE},
            },
        )

        acknowledgement = self._acks()[0]
        self.assertEqual(acknowledgement["status"], "accepted")
        self.assertEqual(
            acknowledgement["revision"],
            {"scope": "movement", "key": DEPARTURE, "value": 1},
        )
        movement = next(
            entry
            for entry in acknowledgement["snapshot"]["movements"]
            if entry["id"] == DEPARTURE
        )
        self.assertEqual(movement["departure"], "positioned")
        # Every box at the station sees the new state, not just the sender.
        self.assertIn(device_topic(DEVICE, "snapshot"), [topic for topic, _ in self._retained()])

    def test_a_stale_movement_revision_is_rejected(self):
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "m-1",
                "device_id": DEVICE,
                "action": "train.position.set",
                "payload": {"movement_id": DEPARTURE},
            },
        )
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "m-2",
                "device_id": DEVICE,
                "action": "train.departed",
                "expected_revision": {"scope": "movement", "key": DEPARTURE, "value": 0},
                "payload": {"movement_id": DEPARTURE},
            },
        )

        self.assertEqual(self._acks()[1]["reason"], "stale_revision")

    def test_a_movement_revision_never_collides_with_the_config_revision(self):
        # The movement is at revision 1 while config is at its own number.
        # A command conditioned on one scope must not be judged by the other.
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "m-1",
                "device_id": DEVICE,
                "action": "train.position.set",
                "payload": {"movement_id": DEPARTURE},
            },
        )
        config_version = self.service.config_version()
        self.assertNotEqual(config_version, 1)

        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "m-3",
                "device_id": DEVICE,
                "action": "device.config.ack",
                "expected_revision": {
                    "scope": "config",
                    "key": STATION,
                    "value": config_version,
                },
            },
        )
        self.assertEqual(self._acks()[1]["status"], "accepted")

        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "m-4",
                "device_id": DEVICE,
                "action": "train.departed",
                "expected_revision": {
                    "scope": "movement",
                    "key": DEPARTURE,
                    "value": config_version,
                },
                "payload": {"movement_id": DEPARTURE},
            },
        )
        self.assertEqual(self._acks()[2]["reason"], "stale_revision")

    def test_a_resent_message_id_answers_the_same_without_a_second_effect(self):
        command = {
            "protocol_version": 2,
            "message_id": "same-id",
            "device_id": DEVICE,
            "action": "train.position.set",
            "payload": {"movement_id": DEPARTURE},
        }
        self._send("command", command)
        self._send("command", command)

        first, second = self._acks()
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["revision"], first["revision"])

    def test_a_command_from_a_box_without_a_station_is_refused(self):
        self.identities.record_discovery("TMBOX-LOOSE", "TMBOX-LOOSE")
        self.gateway.on_message(
            device_topic("TMBOX-LOOSE", "command"),
            json.dumps(
                {
                    "protocol_version": 2,
                    "message_id": "loose-1",
                    "device_id": "TMBOX-LOOSE",
                    "action": "train.position.set",
                    "payload": {"movement_id": DEPARTURE},
                }
            ).encode("utf-8"),
        )

        self.assertEqual(self._acks()[0]["reason"], "not_assigned")

    def test_a_command_for_another_station_is_refused(self):
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "wrong-station",
                "device_id": DEVICE,
                "station_id": "st-lek",
                "action": "train.position.set",
                "payload": {"movement_id": DEPARTURE},
            },
        )

        self.assertEqual(self._acks()[0]["reason"], "station_mismatch")

    def test_a_track_change_must_name_a_track_in_the_catalogue(self):
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "track-1",
                "device_id": DEVICE,
                "action": "train.track.change",
                "payload": {"movement_id": DEPARTURE, "track_id": "1A"},
            },
        )
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "track-2",
                "device_id": DEVICE,
                "action": "train.track.change",
                "payload": {"movement_id": DEPARTURE, "track_id": "9Z"},
            },
        )

        accepted, refused = self._acks()
        self.assertEqual(accepted["status"], "accepted")
        movement = next(
            entry for entry in accepted["snapshot"]["movements"] if entry["id"] == DEPARTURE
        )
        self.assertEqual(movement["actualTrack"], "track-cda-1a")
        self.assertEqual(refused["reason"], "unknown_track")

    def test_train_lookup_answers_from_the_station_without_touching_state(self):
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "lookup-1",
                "device_id": DEVICE,
                "action": "train.lookup",
                "payload": {"train_number": "421"},
            },
        )

        acknowledgement = self._acks()[0]
        self.assertEqual(acknowledgement["status"], "accepted")
        self.assertEqual(acknowledgement["result"]["matches"][0]["movement_id"], DEPARTURE)
        self.assertFalse(acknowledgement["result"]["ambiguous"])
        self.assertEqual(
            acknowledgement["snapshot"]["revision"]["movements"][DEPARTURE], 0
        )

    def test_an_unknown_action_is_refused_rather_than_guessed_at(self):
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "odd-1",
                "device_id": DEVICE,
                "action": "train.teleport",
                "payload": {"movement_id": DEPARTURE},
            },
        )

        self.assertEqual(self._acks()[0]["reason"], "unknown_action")

    def test_a_v1_command_is_never_accepted_on_the_v2_surface(self):
        self._send(
            "command",
            {
                "protocol_version": 1,
                "message_id": "old-1",
                "device_id": DEVICE,
                "action": "key_press",
            },
        )

        self.assertEqual(self._acks()[0]["reason"], "unsupported_protocol")


if __name__ == "__main__":
    unittest.main()
