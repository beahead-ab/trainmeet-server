from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime_fixture import fictional_runtime_package
from tmbox_gateway.identity import DisplayCapability, IdentityStore
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway import protocol_v2
from tmbox_gateway.protocol_v2 import find_track_conflict, TMBoxStationService
from tmbox_gateway.runtime import RuntimePublication, SQLiteRuntimeStore


DEVICE = "TMBOX-7A42F1"
NEIGHBOUR = "TMBOX-VST001"
STATION = "st-cda"
DEPARTURE = "movement-421-cda"


class ProtocolV2Base(unittest.TestCase):
    """Shared fixture: one meet, one box at Charlottendal.

    A box's command goes straight to the station service, the same way
    /v1/tmbox-v2/command and the 16x2 terminals reach it. Until 2.0.0 these
    tests went through the tmbox/v2 MQTT gateway, which is gone.
    """

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
        self.acks: list[dict] = []
        # Every committed change is announced; boxes and Drift redraw from it.
        self.notified: list[bool] = []
        self.service.subscribe(lambda: self.notified.append(True))

    def tearDown(self):
        self.identities.close()
        self.operations_store.close()
        self.runtime_store.close()
        self.directory.cleanup()

    def _send(self, leaf: str, body: dict) -> None:
        self._send_from(DEVICE, leaf, body)

    def _send_from(self, device_id: str, leaf: str, body: dict) -> None:
        """What a box's hello or command does on the server."""
        if leaf == "hello":
            self.identities.record_discovery(device_id, str(body.get("device_code") or device_id), protocol_version=2,
                                             display=DisplayCapability.parse(body.get("display")))
        elif leaf == "command":
            self.acks.append(self.service.handle_command(device_id, body))
        else:
            raise AssertionError(f"no such message: {leaf}")

    def _acks(self) -> list[dict]:
        return self.acks

    def _grant_departure(self, movement_id=DEPARTURE):
        """Departure now has the same clearance prerequisite as v1/TKL."""
        requested = self.service.execute_station_command(
            DEVICE, STATION, "clearance.request",
            {"movement_id": movement_id, "connection_id": "connection-cda-vst"},
        )
        self.service.execute_station_command(
            NEIGHBOUR, "st-vst", "clearance.response",
            {"clearance_id": requested["revision"]["key"], "approved": True},
        )


class ProtocolV2Tests(ProtocolV2Base):
    def test_config_preserves_cloud_ports_blanks_order_and_sides(self):
        package = fictional_runtime_package()
        package["publication_id"] = "remapped-ports"
        connections = [item["id"] for item in package["connections"]]
        panel = next(item for item in package["panels"] if item["station_id"] == STATION)
        panel.update(slot_layout="columns", slots={"A": None, "B": connections[2], "C": connections[0], "D": connections[1]})
        self.runtime_store.install(package)
        payload = self.service.config_payload(STATION, self.identities.discovered_device(DEVICE).display, device_id=DEVICE)
        self.assertEqual(payload["panels"][0]["slots"], panel["slots"])
        self.assertEqual([row["connection_id"] for row in payload["connections"]], [connections[2], connections[0], connections[1]])
        self.assertEqual([row["display_row"] for row in payload["connections"]], [1, 2, 3])
        self.assertEqual([row["display_side"] for row in payload["connections"]], ["left", "right", "right"])
        self.assertEqual(payload["connections"][0]["panel_slots"], [{"panel_id": panel["id"], "key": "B", "row": 2, "side": "left"}])

    def test_multiple_panels_with_opposite_placements_do_not_invent_a_side(self):
        package = fictional_runtime_package()
        package["publication_id"] = "two-panels"
        connection = package["connections"][0]["id"]
        panel = next(item for item in package["panels"] if item["station_id"] == STATION)
        panel.update(slot_layout="columns", slots={"A": connection, "B": None, "C": None, "D": None})
        package["panels"].append({**panel, "id": "second-panel", "slots": {"A": None, "B": None, "C": connection, "D": None}})
        self.runtime_store.install(package)
        row = next(row for row in self.service.config_payload(STATION)["connections"] if row["connection_id"] == connection)
        self.assertEqual(len(row["panel_slots"]), 2)
        self.assertIsNone(row["display_side"])

    def test_removed_box_waits_and_cannot_send_or_replay_commands(self):
        command = {"protocol_version": 2, "message_id": "before-remove", "device_id": DEVICE,
                   "action": "train.position.set", "payload": {"movement_id": DEPARTURE}}
        self._send("command", command)
        self.assertEqual(self._acks()[-1]["status"], "accepted")
        before = self.service.snapshot_payload(STATION)
        self.identities.remove_discovered_device(DEVICE)
        self.assertEqual(self.service.assignment_payload(DEVICE)["status"], "waiting_for_assignment")
        for message_id in ("before-remove", "after-remove"):
            self._send("command", {**command, "message_id": message_id})
            self.assertEqual(self._acks()[-1]["reason"], "not_assigned")
        self._send("hello", {"device_code": DEVICE})
        self.assertEqual(self.identities.discovered_devices(), ())
        self.assertEqual(self.service.snapshot_payload(STATION), before)
        # History survives; the old acknowledgement is retained for audit.
        self.assertIsNotNone(self.operations_store.device_command_response(DEVICE, "before-remove"))

    # ------------------------------------------------------------ state

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
        assignment = self.service.assignment_payload("TMBOX-NEW")
        self.assertEqual(assignment["status"], "waiting_for_assignment")
        self.assertIsNone(assignment["station_id"])

    def test_there_is_no_event_replay_surface(self):
        for payload in (self.service.assignment_payload(DEVICE), self.service.config_payload(STATION),
                        self.service.snapshot_payload(STATION)):
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
        # Every box at the station is told, not just the sender.
        self.assertTrue(self.notified)

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
        self._send_from(
            "TMBOX-LOOSE",
            "command",
            {
                "protocol_version": 2,
                "message_id": "loose-1",
                "device_id": "TMBOX-LOOSE",
                "action": "train.position.set",
                "payload": {"movement_id": DEPARTURE},
            },
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
        planned = acknowledgement["result"]["matches"][0]["departure_route"]
        self.assertEqual(planned["status"], "resolved")
        self.assertEqual(planned["to_station_code"], "VST")
        self.assertEqual(planned["to_movement_id"], "movement-421-vst")
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


class ClearanceTests(ProtocolV2Base):
    """The clearance aggregate: its own case, its own channel, its own clock."""

    def setUp(self):
        super().setUp()
        self.identities.record_discovery(NEIGHBOUR, NEIGHBOUR)
        self.identities.assign_discovered_device(NEIGHBOUR, station_id="st-vst")

    def _request(self, device_id=DEVICE, movement=DEPARTURE, connection="connection-cda-vst",
                 message_id="req-1", clearance_id=None) -> dict:
        body = {"movement_id": movement, "connection_id": connection}
        if clearance_id:
            body["clearance_id"] = clearance_id
        self._send_from(
            device_id,
            "command",
            {
                "protocol_version": 2,
                "message_id": message_id,
                "device_id": device_id,
                "action": "clearance.request",
                "payload": body,
            },
        )
        return self._acks()[-1]

    def test_a_request_waits_for_the_other_station_to_answer(self):
        acknowledgement = self._request()

        self.assertEqual(acknowledgement["status"], "accepted")
        self.assertEqual(acknowledgement["revision"]["scope"], "case")
        case = acknowledgement["snapshot"]["active_clearances"][0]
        self.assertEqual(case["status"], "waiting")
        self.assertEqual(case["from_station_id"], STATION)
        self.assertEqual(case["to_station_id"], "st-vst")
        # The case revision lives in its own space, beside the movements.
        self.assertEqual(
            acknowledgement["snapshot"]["revision"]["cases"][case["clearance_id"]], 1
        )

    def test_the_receiving_station_approves_and_both_ends_see_it(self):
        clearance_id = self._request()["revision"]["key"]

        self._send_from(
            NEIGHBOUR,
            "command",
            {
                "protocol_version": 2,
                "message_id": "answer-1",
                "device_id": NEIGHBOUR,
                "action": "clearance.response",
                "payload": {"clearance_id": clearance_id, "approved": True},
            },
        )

        answer = self._acks()[-1]
        self.assertEqual(answer["status"], "accepted")
        self.assertEqual(answer["revision"], {"scope": "case", "key": clearance_id, "value": 2})
        sender_view = self.service.snapshot_payload(STATION)["active_clearances"][0]
        self.assertEqual(sender_view["status"], "approved")

    def test_only_the_receiving_station_may_answer(self):
        clearance_id = self._request()["revision"]["key"]

        self._send_from(
            DEVICE,
            "command",
            {
                "protocol_version": 2,
                "message_id": "self-answer",
                "device_id": DEVICE,
                "action": "clearance.response",
                "payload": {"clearance_id": clearance_id, "approved": True},
            },
        )

        self.assertEqual(self._acks()[-1]["reason"], "not_receiver")

    def test_only_the_sender_may_cancel(self):
        clearance_id = self._request()["revision"]["key"]

        self._send_from(
            NEIGHBOUR,
            "command",
            {
                "protocol_version": 2,
                "message_id": "wrong-cancel",
                "device_id": NEIGHBOUR,
                "action": "clearance.cancel",
                "payload": {"clearance_id": clearance_id},
            },
        )
        self.assertEqual(self._acks()[-1]["reason"], "not_sender")

        self._send_from(
            DEVICE,
            "command",
            {
                "protocol_version": 2,
                "message_id": "right-cancel",
                "device_id": DEVICE,
                "action": "clearance.cancel",
                "payload": {"clearance_id": clearance_id},
            },
        )
        self.assertEqual(self._acks()[-1]["status"], "accepted")
        self.assertEqual(self.service.snapshot_payload(STATION)["active_clearances"], [])

    def test_a_second_request_on_an_occupied_channel_is_refused(self):
        self._request()

        second = self._request(movement="movement-428-cda", message_id="req-2")

        self.assertEqual(second["reason"], "channel_occupied")

    def test_opposite_directions_on_a_double_track_never_block_each_other(self):
        """Decision B7: directed channels from the start.

        Vagnsta and Charlottendal are joined by double track. A train leaving
        each end at the same time uses a different track, so neither request
        may see the other's channel as taken.
        """
        outbound = self._request(message_id="out-1")
        inbound = self._request(
            device_id=NEIGHBOUR,
            movement="movement-428-vst",
            message_id="in-1",
        )

        self.assertEqual(outbound["status"], "accepted")
        self.assertEqual(inbound["status"], "accepted")
        self.assertNotEqual(outbound["revision"]["key"], inbound["revision"]["key"])
        # Each station sees both cases; they simply do not contend.
        self.assertEqual(len(self.service.snapshot_payload(STATION)["active_clearances"]), 2)

    def test_a_single_track_connection_has_one_shared_channel(self):
        first = self._request(connection="connection-cda-kun", message_id="single-1")
        self.assertEqual(first["status"], "accepted")

        second = self._request(
            movement="movement-428-cda",
            connection="connection-cda-kun",
            message_id="single-2",
        )
        self.assertEqual(second["reason"], "channel_occupied")

    def test_a_track_change_under_a_waiting_request_invalidates_it(self):
        clearance_id = self._request()["revision"]["key"]

        # 1A, not 2A: 428 stands on 2A, and this test is about the clearance
        # being invalidated, not about occupancy refusing the move.
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": "track-move",
                "device_id": DEVICE,
                "action": "train.track.change",
                "payload": {"movement_id": DEPARTURE, "track_id": "1A"},
            },
        )

        case = self.operations_store.clearance(clearance_id)
        self.assertEqual(case["status"], "invalidated_by_revision")
        # Not a silent rewrite: the case is closed and the channel is free.
        self.assertEqual(self.service.snapshot_payload(STATION)["active_clearances"], [])
        history = [entry["event_type"] for entry in self.operations_store.clearance_history(clearance_id)]
        self.assertEqual(history, ["requested", "invalidated_by_revision"])

    def test_a_request_lapses_when_its_time_runs_out(self):
        self.operations_store.start_clock(time_value="09:00:00")
        with mock.patch.object(protocol_v2, "CLEARANCE_TTL_SECONDS", 0):
            clearance_id = self._request()["revision"]["key"]
            # The next case action checks the clock on the way in - no
            # background job is involved.
            self._request(movement="movement-428-cda", message_id="req-2")

        self.assertEqual(self.operations_store.clearance(clearance_id)["status"], "expired")

    def test_a_stopped_meeting_clock_does_not_expire_a_request(self):
        # The clock is stopped in this fixture: the meet is paused, so nothing
        # that was open when everyone left for coffee may lapse.
        with mock.patch.object(protocol_v2, "CLEARANCE_TTL_SECONDS", 0):
            clearance_id = self._request()["revision"]["key"]
            self._request(movement="movement-428-cda", message_id="req-2")

        self.assertEqual(self.operations_store.clearance(clearance_id)["status"], "waiting")

    def test_an_arriving_train_frees_the_line_it_was_granted(self):
        clearance_id = self._request()["revision"]["key"]
        self._send_from(
            NEIGHBOUR,
            "command",
            {
                "protocol_version": 2,
                "message_id": "answer-1",
                "device_id": NEIGHBOUR,
                "action": "clearance.response",
                "payload": {"clearance_id": clearance_id, "approved": True},
            },
        )
        self.assertEqual(len(self.service.snapshot_payload("st-vst")["active_clearances"]), 1)

        self.service.execute_station_command(DEVICE, STATION, "train.departed", {"movement_id": DEPARTURE})

        self._send_from(
            NEIGHBOUR,
            "command",
            {
                "protocol_version": 2,
                "message_id": "arrived-1",
                "device_id": NEIGHBOUR,
                "action": "train.arrived",
                "payload": {"movement_id": "movement-421-vst"},
            },
        )

        self.assertEqual(self.service.snapshot_payload("st-vst")["active_clearances"], [])
        history = [entry["event_type"] for entry in self.operations_store.clearance_history(clearance_id)]
        self.assertEqual(history, ["requested", "approved", "released"])

    def test_an_answer_to_a_settled_case_is_refused(self):
        clearance_id = self._request()["revision"]["key"]
        self._send_from(
            DEVICE,
            "command",
            {
                "protocol_version": 2,
                "message_id": "cancel-1",
                "device_id": DEVICE,
                "action": "clearance.cancel",
                "payload": {"clearance_id": clearance_id},
            },
        )

        self._send_from(
            NEIGHBOUR,
            "command",
            {
                "protocol_version": 2,
                "message_id": "late-answer",
                "device_id": NEIGHBOUR,
                "action": "clearance.response",
                "payload": {"clearance_id": clearance_id, "approved": True},
            },
        )

        self.assertEqual(self._acks()[-1]["reason"], "clearance_not_pending")

    def test_a_stale_case_revision_is_rejected(self):
        clearance_id = self._request()["revision"]["key"]

        self._send_from(
            NEIGHBOUR,
            "command",
            {
                "protocol_version": 2,
                "message_id": "stale-answer",
                "device_id": NEIGHBOUR,
                "action": "clearance.response",
                "expected_revision": {"scope": "case", "key": clearance_id, "value": 99},
                "payload": {"clearance_id": clearance_id, "approved": True},
            },
        )

        self.assertEqual(self._acks()[-1]["reason"], "stale_revision")


class ReadinessAndLineTests(ProtocolV2Base):
    """TKL's two declarations, the state derived from them, and line-available."""

    def _command(self, message_id: str, action: str, body: dict | None = None) -> dict:
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": message_id,
                "device_id": DEVICE,
                "action": action,
                "payload": body or {},
            },
        )
        return self._acks()[-1]

    def _movement(self, snapshot: dict, movement_id: str = DEPARTURE) -> dict:
        return next(entry for entry in snapshot["movements"] if entry["id"] == movement_id)

    def test_ready_is_derived_from_the_two_declarations(self):
        start = self._movement(self.service.snapshot_payload(STATION))
        self.assertEqual(start["departure"], "none")
        self.assertFalse(start["crewReady"])

        positioned = self._movement(
            self._command("a", "train.position.set", {"movement_id": DEPARTURE})["snapshot"]
        )
        self.assertEqual(positioned["departure"], "positioned")
        self.assertEqual(positioned["allowed_actions"][0], "train.crew_ready.set")

        ready = self._movement(
            self._command("b", "train.crew_ready.set", {"movement_id": DEPARTURE})["snapshot"]
        )
        self.assertEqual(ready["departure"], "ready")
        self.assertTrue(ready["crewReady"])
        self.assertIn("clearance.request", ready["allowed_actions"])

    def test_crew_alone_is_not_ready(self):
        ready = self._movement(
            self._command("a", "train.crew_ready.set", {"movement_id": DEPARTURE})["snapshot"]
        )

        # The driver is aboard but the train is not set up. Ready is what both
        # declarations add up to, so one of them is not enough.
        self.assertTrue(ready["crewReady"])
        self.assertEqual(ready["departure"], "none")

    def test_the_declarations_survive_a_server_restart(self):
        self._command("a", "train.position.set", {"movement_id": DEPARTURE})
        self._command("b", "train.crew_ready.set", {"movement_id": DEPARTURE})

        self.operations_store.close()
        reopened = SQLiteOperationsStore(Path(self.directory.name) / "runtime.db")
        try:
            service = TMBoxStationService(self.runtime_store, reopened, self.identities)
            movement = self._movement(service.snapshot_payload(STATION))
            self.assertEqual(movement["departure"], "ready")
            self.assertTrue(movement["crewReady"])
        finally:
            reopened.close()
        self.operations_store = SQLiteOperationsStore(Path(self.directory.name) / "runtime.db")

    def test_a_departed_train_offers_nothing_more_to_declare(self):
        self._command("a", "train.position.set", {"movement_id": DEPARTURE})
        self._command("b", "train.crew_ready.set", {"movement_id": DEPARTURE})
        self._grant_departure()
        departed = self._movement(
            self._command("c", "train.departed", {"movement_id": DEPARTURE})["snapshot"]
        )

        self.assertEqual(departed["departure"], "departed")
        self.assertEqual(departed["allowed_actions"], [])

    def test_line_available_never_occupies_a_channel(self):
        """A one-sided message is not a request and is never treated as one."""
        published = self._command(
            "line-1",
            "line.available.publish",
            {"connection_id": "connection-cda-vst"},
        )
        self.assertEqual(published["status"], "accepted")
        self.assertEqual(published["revision"]["scope"], "case")

        # A clearance on the very same connection is still free to be made.
        requested = self._command(
            "clr-1",
            "clearance.request",
            {"movement_id": DEPARTURE, "connection_id": "connection-cda-vst"},
        )
        self.assertEqual(requested["status"], "accepted")

    def test_line_available_is_acknowledged_never_answered(self):
        message_id = self._command(
            "line-1",
            "line.available.publish",
            {"connection_id": "connection-cda-vst"},
        )["revision"]["key"]

        # It travels to the other end as information, not as a case awaiting a
        # decision, and there is no approve or reject to give it.
        neighbour = self.service.snapshot_payload("st-vst")
        self.assertEqual(neighbour["active_clearances"], [])
        self.assertEqual(neighbour["line_messages"][0]["status"], "delivered_to_device")

        self.identities.record_discovery(NEIGHBOUR, NEIGHBOUR)
        self.identities.assign_discovered_device(NEIGHBOUR, station_id="st-vst")
        self._send_from(
            NEIGHBOUR,
            "command",
            {
                "protocol_version": 2,
                "message_id": "ack-1",
                "device_id": NEIGHBOUR,
                "action": "line.available.acknowledge",
                "payload": {"message_id": message_id},
            },
        )

        self.assertEqual(self._acks()[-1]["status"], "accepted")
        self.assertEqual(
            self.operations_store.line_message(message_id)["status"],
            "display_acknowledged",
        )
        self.assertEqual(self.service.snapshot_payload("st-vst")["line_messages"], [])

    def test_only_the_receiving_station_acknowledges_a_line_message(self):
        message_id = self._command(
            "line-1",
            "line.available.publish",
            {"connection_id": "connection-cda-vst"},
        )["revision"]["key"]

        refused = self._command(
            "ack-self", "line.available.acknowledge", {"message_id": message_id}
        )

        self.assertEqual(refused["reason"], "not_receiver")


if __name__ == "__main__":
    unittest.main()


class TrackOccupancyTests(ProtocolV2Base):
    """§8: one track, one non-departed movement, per station and day.

    The reason `track_occupied` has always been in the contract's list of
    rejections a client must handle. Until now it could not actually happen.
    """

    ARRIVAL = "movement-428-cda"

    def _move(self, movement_id: str, track: str, *, message_id: str) -> dict:
        self._send(
            "command",
            {
                "protocol_version": 2,
                "message_id": message_id,
                "device_id": DEVICE,
                "action": "train.track.change",
                "payload": {"movement_id": movement_id, "track_id": track},
            },
        )
        return self._acks()[-1]

    def _depart(self, movement_id: str, *, message_id: str) -> dict:
        self._grant_departure(movement_id)
        for action, step in (
            ("train.position.set", "pos"),
            ("train.crew_ready.set", "crew"),
            ("train.departed", "gone"),
        ):
            self._send(
                "command",
                {
                    "protocol_version": 2,
                    "message_id": f"{message_id}-{step}",
                    "device_id": DEVICE,
                    "action": action,
                    "payload": {"movement_id": movement_id},
                },
            )
        return self._acks()[-1]

    # ------------------------------------------------------------- negative

    def test_a_track_another_train_stands_on_is_refused(self):
        """428 is scheduled onto 2A, so 421 cannot be moved there."""
        acknowledgement = self._move(DEPARTURE, "2A", message_id="collide")
        self.assertEqual("rejected", acknowledgement["status"])
        self.assertEqual("track_occupied", acknowledgement["reason"])

    def test_the_scheduled_track_counts_even_if_nobody_has_touched_it(self):
        """Two trains put on one track by Cloud collide as surely as two by hand.

        Nothing has been moved here - 428 holds 2A straight from the
        timetable - so a check that only looked at live state would miss it.
        """
        movement = next(
            row for row in self.service.snapshot_payload(STATION)["movements"]
            if row["id"] == self.ARRIVAL
        )
        self.assertIsNone(movement["actualTrack"], "inget har flyttats i det här testet")
        self.assertEqual("rejected", self._move(DEPARTURE, "2A", message_id="sched")["status"])

    def test_a_refusal_leaves_the_track_where_it_was(self):
        before = next(
            row for row in self.service.snapshot_payload(STATION)["movements"]
            if row["id"] == DEPARTURE
        )["assignedTrackId"]
        self._move(DEPARTURE, "2A", message_id="collide")
        after = next(
            row for row in self.service.snapshot_payload(STATION)["movements"]
            if row["id"] == DEPARTURE
        )["assignedTrackId"]
        self.assertEqual(before, after)

    # ------------------------------------------------------------- positive

    def test_a_free_track_is_accepted(self):
        self.assertEqual("accepted", self._move(DEPARTURE, "1A", message_id="free")["status"])

    def test_moving_a_train_to_the_track_it_already_holds_is_not_a_collision(self):
        """A movement never collides with itself."""
        self._move(DEPARTURE, "1A", message_id="first")
        self.assertEqual("accepted", self._move(DEPARTURE, "1A", message_id="again")["status"])

    def test_a_departed_train_releases_its_track(self):
        """The whole point of 'non-departed'."""
        self._move(DEPARTURE, "1A", message_id="park")
        self.assertEqual(
            "track_occupied", self._move(self.ARRIVAL, "1A", message_id="blocked")["reason"]
        )

        self._depart(DEPARTURE, message_id="leave")
        self.assertEqual(
            "accepted", self._move(self.ARRIVAL, "1A", message_id="now-free")["status"]
        )

    def test_another_stations_row_holding_the_same_track_id_is_not_our_collision(self):
        """The station filter, made load-bearing.

        Catalogue ids are station-scoped in practice, so nothing in the normal
        fixtures can tell whether the station check does anything - a mutation
        that removed it survived every other test here. A malformed package
        where two stations name the same track id is the case that separates
        them, and §8 is explicit that the rule is per station.
        """
        publication = self.runtime_store.active()
        rows = publication.payload["trains"]
        intruder = dict(
            next(row for row in rows if row["station_id"] != STATION),
            id="movement-intruder",
            track_id="track-cda-1a",
            days="Dagl",
        )
        conflict = find_track_conflict(
            rows + [intruder],
            {},
            STATION,
            publication.payload["meet"]["active_day"],
            DEPARTURE,
            "track-cda-1a",
        )
        self.assertIsNone(conflict, "en rad på en annan station upptar inte vårt spår")

        at_our_station = dict(intruder, station_id=STATION)
        self.assertIsNotNone(
            find_track_conflict(
                rows + [at_our_station],
                {},
                STATION,
                publication.payload["meet"]["active_day"],
                DEPARTURE,
                "track-cda-1a",
            )
        )
