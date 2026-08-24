"""The v2 simulator's HTTP surface, checked against the box's own wire.

The simulator exists to exercise protocol v2 without hardware, which is only
worth anything if it travels the same path a box travels. So these tests do
not assert that the endpoints "work" - they assert that what HTTP returns is
what the MQTT gateway would publish, and that a command sent through HTTP is
indistinguishable from one that arrived over the broker.
"""

from __future__ import annotations


from test_protocol_v2 import DEPARTURE, DEVICE, STATION, ProtocolV2Base
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, PairedClient, PairingService


class TMBoxV2HTTPTests(ProtocolV2Base):
    def setUp(self):
        super().setUp()
        publication = self.runtime_store.active()
        self.engine = TrafficEngine(publication.session_config())
        self.application = TrainMeetHTTPApplication(
            self.engine,
            self.identities,
            PairingService(self.identities, set(self.engine.config.panels)),
            HTTPServerConfig(local_development=True),
            runtime_store=self.runtime_store,
            operations_store=self.operations_store,
            # The gateway's own service, so the simulator and a real box cannot
            # answer from two different caches.
            station_service=self.service,
        )
        self.client = self.application.local_admin()

    def _command(self, action, payload, *, message_id="sim-1", station_id=STATION):
        return self.application.tmbox_v2_command(
            self.client,
            {
                "device_id": DEVICE,
                "command": {
                    "protocol_version": 2,
                    "message_id": message_id,
                    "action": action,
                    "station_id": station_id,
                    "payload": payload,
                },
            },
        )

    # ------------------------------------------------------- same payloads

    def test_http_serves_exactly_what_the_box_would_receive(self):
        """The three reads are the three retained topics, byte for byte."""
        self.assertEqual(
            self.service.assignment_payload(DEVICE),
            self.application.tmbox_v2_assignment(self.client, DEVICE),
        )
        self.assertEqual(
            self.service.config_payload(STATION),
            self.application.tmbox_v2_config(self.client, STATION),
        )
        self.assertEqual(
            self.service.snapshot_payload(STATION),
            self.application.tmbox_v2_snapshot(self.client, STATION),
        )

    def test_snapshot_carries_the_actions_the_ui_renders_buttons_from(self):
        """The simulator must not decide for itself what is allowed."""
        snapshot = self.application.tmbox_v2_snapshot(self.client, STATION)
        movement = next(row for row in snapshot["movements"] if row["id"] == DEPARTURE)
        self.assertIn("allowed_actions", movement)
        self.assertIsInstance(movement["allowed_actions"], list)

    # ------------------------------------------------------------ commands

    def test_a_command_over_http_moves_the_same_state_as_one_over_mqtt(self):
        acknowledgement = self._command("train.position.set", {"movement_id": DEPARTURE})
        self.assertEqual("accepted", acknowledgement["status"])

        snapshot = self.application.tmbox_v2_snapshot(self.client, STATION)
        movement = next(row for row in snapshot["movements"] if row["id"] == DEPARTURE)
        self.assertEqual("positioned", movement["departure"])

    def test_the_same_message_id_twice_is_not_a_second_decision(self):
        first = self._command("train.position.set", {"movement_id": DEPARTURE}, message_id="sim-7")
        second = self._command("train.position.set", {"movement_id": DEPARTURE}, message_id="sim-7")
        self.assertEqual("accepted", first["status"])
        self.assertEqual("duplicate", second["status"])

    def test_a_rejection_comes_back_as_an_answer_not_a_transport_error(self):
        """A box renders the reason on its display, so it has to reach the client."""
        acknowledgement = self._command(
            "train.position.set", {"movement_id": DEPARTURE}, station_id="st-someone-else"
        )
        self.assertEqual("rejected", acknowledgement["status"])
        self.assertEqual("station_mismatch", acknowledgement["reason"])

    def test_an_unknown_action_is_rejected_rather_than_quietly_ignored(self):
        acknowledgement = self._command("train.teleport", {"movement_id": DEPARTURE})
        self.assertEqual("rejected", acknowledgement["status"])

    def test_a_command_without_a_message_id_is_rejected(self):
        acknowledgement = self.application.tmbox_v2_command(
            self.client,
            {
                "device_id": DEVICE,
                "command": {"protocol_version": 2, "action": "train.position.set", "payload": {}},
            },
        )
        self.assertEqual("rejected", acknowledgement["status"])
        self.assertEqual("missing_message_id", acknowledgement["reason"])

    # ------------------------------------------------------------- shape

    def test_a_malformed_request_is_refused_before_it_reaches_the_service(self):
        with self.assertRaises(HTTPAPIError):
            self.application.tmbox_v2_command(self.client, {"device_id": DEVICE})
        with self.assertRaises(HTTPAPIError):
            self.application.tmbox_v2_command(self.client, {"command": {}})

    def test_an_unknown_station_is_a_not_found_rather_than_an_empty_snapshot(self):
        with self.assertRaises(HTTPAPIError):
            self.application.tmbox_v2_snapshot(self.client, "st-nowhere")
        with self.assertRaises(HTTPAPIError):
            self.application.tmbox_v2_config(self.client, "st-nowhere")

    def test_the_simulator_is_admin_only(self):
        box = PairedClient(
            client_id=DEVICE,
            display_name=DEVICE,
            kind=DeviceKind.ESP32_PANEL,
            panel_ids=(),
        )
        for call in (
            lambda: self.application.tmbox_v2_snapshot(box, STATION),
            lambda: self.application.tmbox_v2_assignment(box, DEVICE),
            lambda: self.application.tmbox_v2_stations(box),
            lambda: self.application.tmbox_v2_command(box, {"device_id": DEVICE, "command": {}}),
        ):
            with self.assertRaises(HTTPAPIError):
                call()

    def test_stations_lists_what_the_simulator_can_stand_in_for(self):
        listed = self.application.tmbox_v2_stations(self.client)["stations"]
        self.assertIn(STATION, [entry["id"] for entry in listed])
        self.assertEqual(sorted(listed, key=lambda e: e["name"]), listed)


class OperatorNoteSurvivesTheBoxTests(TMBoxV2HTTPTests):
    """En TMBox får aldrig radera vad tågklareraren skrivit.

    Boxen har fyra rader siffror och A/B/C — inga bokstäver. Den kan alltså
    inte skriva en anteckning, och skickar därför ingen. Skulle den skicka ett
    tomt fält i stället för inget fält skulle varje spårbyte tömma texten.

    Det här provet går hela vägen genom protokollet, inte bara till lagret:
    det var där den första versionen av testet missade.
    """

    def _note_of(self, movement_id: str) -> str | None:
        publication = self.runtime_store.active()
        state = self.operations_store.tkl_station_state(
            publication.publication_id, publication.payload["meet"]["active_day"], STATION
        )
        return state["movements"].get(movement_id, {}).get("operatorNote")

    def test_a_command_from_the_box_leaves_the_note_alone(self) -> None:
        publication = self.runtime_store.active()
        movement_id = str(DEPARTURE)
        self.operations_store.update_tkl_movement(
            publication.publication_id,
            publication.payload["meet"]["active_day"],
            STATION,
            movement_id,
            arrival="none", departure="none", actual_track=None,
            updated_by="tkl", shift_id=None, event_type="test",
            operator_note="Kort tåg, stannar vid stoppbocken",
        )

        self._command("train.position.set", {"movement_id": movement_id})

        self.assertEqual("Kort tåg, stannar vid stoppbocken", self._note_of(movement_id))


class TerminalWritesTheNoteTests(TMBoxV2HTTPTests):
    """Terminalen är den enda som kan skriva anteckningen.

    Boxen har inga bokstäver. Tågklareraren sitter vid en skärm, och det är
    därifrån en beskrivning på en avgång kommer.
    """

    def setUp(self) -> None:
        super().setUp()
        publication = self.runtime_store.active()
        self.publication_id = publication.publication_id
        self.day = publication.payload["meet"]["active_day"]
        # Passet måste vara igång innan rörelser får hanteras.
        self.operations_store.start_tkl_shift(
            self.publication_id, self.day, STATION,
            operator_name="Casper", terminal_name="prov",
        )

    def _update(self, **payload) -> dict:
        body = {"station_id": STATION, "movement_id": str(DEPARTURE), **payload}
        return self.application.update_tkl_movement(self.client, body)

    def _note(self) -> str | None:
        state = self.operations_store.tkl_station_state(self.publication_id, self.day, STATION)
        return state["movements"].get(str(DEPARTURE), {}).get("operatorNote")

    def test_the_terminal_can_write_a_note(self) -> None:
        self._update(arrival="none", departure="none", operator_note="Väntar på lokförare")

        self.assertEqual("Väntar på lokförare", self._note())

    def test_an_update_without_a_note_leaves_it_alone(self) -> None:
        """Samma skydd som för boxen, men från terminalens håll."""

        self._update(arrival="none", departure="none", operator_note="Väntar på lokförare")
        self._update(arrival="arrived", departure="none")

        self.assertEqual("Väntar på lokförare", self._note())

    def test_the_terminal_can_clear_a_note(self) -> None:
        self._update(arrival="none", departure="none", operator_note="Väntar på lokförare")
        self._update(arrival="none", departure="none", operator_note="")

        self.assertIsNone(self._note())
