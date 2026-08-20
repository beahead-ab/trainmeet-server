from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from .display import allowed_keys, render_panel
from .models import (
    Command,
    CommandAck,
    ConnectionRuntime,
    ConnectionState,
    DispatchMode,
    InteractionMode,
    PanelConfig,
    PanelRuntime,
    RequestStatus,
    SessionConfig,
)
from .storage import CorruptStateError, StateStore, session_config_fingerprint


class TrafficEngine:
    """Single-process authoritative TMBox state machine.

    The engine is intentionally transport-independent. Its public boundary is a
    command plus one or more complete snapshots, matching the MQTT contract.
    """

    def __init__(self, config: SessionConfig, *, state_store: StateStore | None = None):
        self.config = config
        self.state_store = state_store
        self.config_fingerprint = session_config_fingerprint(config)
        self._lock = RLock()
        self.revision = 0
        self.connections = {
            connection_id: ConnectionRuntime()
            for connection_id in config.connections
        }
        self.panels = {
            panel_id: PanelRuntime()
            for panel_id in config.panels
        }
        self.processed_commands: dict[str, CommandAck] = {}
        self.audit: list[dict[str, Any]] = []
        self.transition_observer: Callable[[dict[str, Any], dict[str, Any], datetime], None] | None = None
        self.clock_source: Callable[[], dict[str, Any]] | None = None
        self._validate_config()
        if self.state_store is not None:
            state = self.state_store.load(self.config.id, self.config_fingerprint)
            if state is not None:
                self._restore_state(state)

    def press(self, command: Command, *, now: datetime | None = None) -> CommandAck:
        with self._lock:
            return self._press_locked(command, now=now)

    def perform(
        self,
        *,
        station_id: str,
        connection_id: str,
        action: str,
        train_number: str = "",
        client_id: str = "terminal",
        now: datetime | None = None,
    ) -> tuple[bool, str | None]:
        """Apply one traffic action directly, with the same bookkeeping.

        A terminal used to reach these transitions by having the server replay
        a key sequence into the panel state machine on its behalf. That made
        the key grammar part of the HTTP contract; this does not.
        """
        with self._lock:
            if connection_id not in self.connections:
                return False, "unknown_connection"
            moment = now or datetime.now(timezone.utc)
            previous = self.revision
            checkpoint = self._checkpoint()
            before_state = self.export_state()
            handlers = {
                "request": lambda: self.open_case(station_id, connection_id, train_number),
                "accept": lambda: self.answer_case(station_id, connection_id, accept=True),
                "reject": lambda: self.answer_case(station_id, connection_id, accept=False),
                "cancel": lambda: self.withdraw_case(
                    station_id,
                    connection_id,
                    expect=(
                        ConnectionState.REQUESTED
                        if self.connections[connection_id].state == ConnectionState.REQUESTED
                        else ConnectionState.RESERVED
                    ),
                ),
                "depart": lambda: self.depart_case(station_id, connection_id),
                "arrive": lambda: self.arrive_case(station_id, connection_id),
            }
            handler = handlers.get(action)
            if handler is None:
                return False, "unknown_action"
            accepted, reason = handler()
            if not accepted:
                self._persist_or_rollback(checkpoint)
                return False, reason

            self.revision += 1
            self.audit.append(
                {
                    "client_id": client_id,
                    "station_id": station_id,
                    "connection_id": connection_id,
                    "action": action,
                    "previous_revision": previous,
                    "revision": self.revision,
                    "recorded_at": moment.isoformat(),
                }
            )
            self._persist_or_rollback(checkpoint)
            if self.transition_observer is not None:
                try:
                    self.transition_observer(before_state, self.export_state(), moment)
                except Exception:
                    pass
            return True, None

    def set_transition_observer(
        self,
        observer: Callable[[dict[str, Any], dict[str, Any], datetime], None] | None,
    ) -> None:
        self.transition_observer = observer

    def set_clock_source(self, source: Callable[[], dict[str, Any]] | None) -> None:
        """Point the engine at the meeting clock owned by the operations layer.

        Every time a client sees is meeting time, never wall time, and the
        meeting clock can run at another speed or be stopped entirely. The
        engine keeps no clock of its own; it only reads the authoritative one.
        """
        self.clock_source = source

    def meeting_clock(self) -> dict[str, Any]:
        """Return the meeting clock exactly as clients should render it."""
        if self.clock_source is not None:
            try:
                status = self.clock_source()
            except Exception:
                # A clock that cannot be read must never take the traffic
                # engine down with it. Fall back to the publication below.
                status = None
            if status is not None and status.get("configured"):
                return {
                    "time": str(status.get("time") or "")[:5],
                    "running": bool(status.get("running")),
                    "stopped_reason": status.get("stopped_reason"),
                    "configured": True,
                    "source": "meeting_clock",
                }
        # No meeting clock has been configured yet. Show the publication's
        # start time, but never claim that it is running - a frozen clock that
        # says it is running is worse than one that admits it is not.
        return {
            "time": self.config.clock_time[:5],
            "running": False,
            "stopped_reason": None,
            "configured": False,
            "source": "publication_start_time",
        }

    def _press_locked(self, command: Command, *, now: datetime | None = None) -> CommandAck:
        now = now or datetime.now(timezone.utc)
        previous = self.revision

        if command.command_id in self.processed_commands:
            original = self.processed_commands[command.command_id]
            return CommandAck(
                command_id=command.command_id,
                status="duplicate",
                reason=original.reason,
                previous_revision=original.previous_revision,
                revision=original.revision,
                snapshots=original.snapshots,
            )

        rejection = self._validate_command(command, now)
        if rejection is not None:
            checkpoint = self._checkpoint()
            ack = self._ack(command, "rejected", rejection, previous)
            self.processed_commands[command.command_id] = ack
            self._persist_or_rollback(checkpoint)
            return ack

        checkpoint = self._checkpoint()
        before_state = self.export_state()
        panel = self.config.panels[command.panel_id]
        runtime = self.panels[command.panel_id]
        accepted, reason = self._handle_key(panel, runtime, command)
        if not accepted:
            ack = self._ack(command, "rejected", reason, previous)
            self.processed_commands[command.command_id] = ack
            self._persist_or_rollback(checkpoint)
            return ack

        self.revision += 1
        self.audit.append(
            {
                "command_id": command.command_id,
                "client_id": command.client_id,
                "panel_id": command.panel_id,
                "key": command.key,
                "previous_revision": previous,
                "revision": self.revision,
                "recorded_at": now.isoformat(),
            }
        )
        ack = self._ack(command, "accepted", None, previous)
        self.processed_commands[command.command_id] = ack
        self._persist_or_rollback(checkpoint)
        if self.transition_observer is not None:
            try:
                self.transition_observer(before_state, self.export_state(), now)
            except Exception:
                # Display telemetry must never make an otherwise valid traffic
                # command fail. The next full snapshot repairs display state.
                pass
        return ack

    def export_state(self) -> dict[str, Any]:
        return {
            "state_format_version": 1,
            "revision": self.revision,
            "connections": {
                connection_id: {
                    "state": runtime.state.value,
                    "from_station_id": runtime.from_station_id,
                    "to_station_id": runtime.to_station_id,
                    "train_number": runtime.train_number,
                    "request_id": runtime.request_id,
                    "request_status": runtime.request_status.value if runtime.request_status else None,
                }
                for connection_id, runtime in self.connections.items()
            },
            "panels": {
                panel_id: {
                    "mode": runtime.mode.value,
                    "selected_slot": runtime.selected_slot,
                    "train_number": runtime.train_number,
                    "owner_client_id": runtime.owner_client_id,
                }
                for panel_id, runtime in self.panels.items()
            },
            "processed_commands": {
                command_id: ack.to_dict()
                for command_id, ack in self.processed_commands.items()
            },
            "audit": self.audit,
        }

    def _restore_state(self, state: dict[str, Any]) -> None:
        if state.get("state_format_version") != 1:
            raise CorruptStateError("Unsupported engine state format")
        connections = state.get("connections")
        panels = state.get("panels")
        if not isinstance(connections, dict) or set(connections) != set(self.config.connections):
            raise CorruptStateError("Persisted connections do not match the active configuration")
        if not isinstance(panels, dict) or set(panels) != set(self.config.panels):
            raise CorruptStateError("Persisted panels do not match the active configuration")

        restored_connections: dict[str, ConnectionRuntime] = {}
        for connection_id, value in connections.items():
            try:
                request_status = value.get("request_status")
                restored_connections[connection_id] = ConnectionRuntime(
                    state=ConnectionState(value["state"]),
                    from_station_id=value.get("from_station_id"),
                    to_station_id=value.get("to_station_id"),
                    train_number=value.get("train_number"),
                    request_id=value.get("request_id"),
                    request_status=RequestStatus(request_status) if request_status else None,
                )
            except (KeyError, TypeError, ValueError) as error:
                raise CorruptStateError(f"Invalid connection state for {connection_id}") from error

        # Panel interaction is deliberately ephemeral. A restart restores the
        # authoritative traffic state below, but never strands a physical box
        # in an old input, waiting or confirmation screen. Every client can
        # reconstruct the next available action from the connection snapshot.
        restored_panels = {
            panel_id: PanelRuntime()
            for panel_id in panels
        }

        processed = state.get("processed_commands", {})
        if not isinstance(processed, dict):
            raise CorruptStateError("Invalid processed-command cache")
        restored_commands: dict[str, CommandAck] = {}
        for command_id, value in processed.items():
            try:
                restored_commands[command_id] = CommandAck(
                    command_id=value["command_id"],
                    status=value["status"],
                    reason=value.get("reason"),
                    previous_revision=int(value["previous_revision"]),
                    revision=int(value["revision"]),
                    snapshots=value["snapshots"],
                )
            except (KeyError, TypeError, ValueError) as error:
                raise CorruptStateError(f"Invalid command cache entry {command_id}") from error

        revision = state.get("revision")
        audit = state.get("audit")
        if not isinstance(revision, int) or revision < 0 or not isinstance(audit, list):
            raise CorruptStateError("Invalid persisted revision or audit journal")

        self.revision = revision
        self.connections = restored_connections
        self.panels = restored_panels
        self.processed_commands = restored_commands
        self.audit = audit

    def _checkpoint(self) -> tuple[
        int,
        dict[str, ConnectionRuntime],
        dict[str, PanelRuntime],
        dict[str, CommandAck],
        list[dict[str, Any]],
    ]:
        return (
            self.revision,
            deepcopy(self.connections),
            deepcopy(self.panels),
            deepcopy(self.processed_commands),
            deepcopy(self.audit),
        )

    def _persist_or_rollback(
        self,
        checkpoint: tuple[
            int,
            dict[str, ConnectionRuntime],
            dict[str, PanelRuntime],
            dict[str, CommandAck],
            list[dict[str, Any]],
        ],
    ) -> None:
        if self.state_store is None:
            return
        try:
            self.state_store.save(
                self.config.id,
                self.config_fingerprint,
                self.revision,
                self.export_state(),
            )
        except Exception:
            (
                self.revision,
                self.connections,
                self.panels,
                self.processed_commands,
                self.audit,
            ) = checkpoint
            raise

    def snapshot(self, panel_id: str) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(panel_id)

    def _snapshot_locked(self, panel_id: str) -> dict[str, Any]:
        panel = self.config.panels[panel_id]
        runtime = self.panels[panel_id]
        clock = self.meeting_clock()
        line1, line2 = render_panel(
            self.config, panel, runtime, self.connections, clock_time=clock["time"]
        )

        slots: dict[str, Any] = {}
        for key in ("A", "B", "C", "D"):
            connection_id = panel.slots.get(key)  # type: ignore[arg-type]
            if connection_id is None:
                slots[key] = {"key": key, "connection_id": None, "state": "unused"}
                continue
            connection = self.config.connections[connection_id]
            connection_state = self.connections[connection_id]
            other_id = connection.other_station(panel.station_id)
            slots[key] = {
                "key": key,
                "connection_id": connection_id,
                "station_id": other_id,
                "station_code": self.config.stations[other_id].code[:3].upper(),
                "track_type": connection.track_type.value,
                "state": connection_state.state.value,
                "from_station_id": connection_state.from_station_id,
                "to_station_id": connection_state.to_station_id,
                "train_number": connection_state.train_number,
                "action": self._slot_action(panel.station_id, connection_state),
                "needs_attention": self._slot_needs_attention(panel.station_id, connection_state),
            }

        attention_slots = [
            key
            for key, slot in slots.items()
            if slot.get("needs_attention")
        ]

        return {
            "protocol_version": 1,
            "traffic_session_id": self.config.id,
            "panel_id": panel.id,
            "panel_name": panel.name,
            "station_id": panel.station_id,
            "station_code": self.config.stations[panel.station_id].code[:3].upper(),
            "revision": self.revision,
            "connection_status": "online",
            "interaction": {
                "mode": runtime.mode.value,
                "selected_slot": runtime.selected_slot,
                "train_number": runtime.train_number,
                "owner_client_id": runtime.owner_client_id,
                "allowed_keys": allowed_keys(runtime, panel),
            },
            "slots": slots,
            "attention": {
                "count": len(attention_slots),
                "slots": attention_slots,
            },
            "display": {"line1": line1, "line2": line2},
            "clock": clock,
        }

    def snapshots(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                panel_id: self._snapshot_locked(panel_id)
                for panel_id in self.config.panels
            }

    def _validate_config(self) -> None:
        for connection in self.config.connections.values():
            if connection.station_a_id not in self.config.stations:
                raise ValueError(f"Unknown endpoint {connection.station_a_id}")
            if connection.station_b_id not in self.config.stations:
                raise ValueError(f"Unknown endpoint {connection.station_b_id}")
        for panel in self.config.panels.values():
            if panel.station_id not in self.config.stations:
                raise ValueError(f"Unknown station {panel.station_id} for panel {panel.id}")
            for key, connection_id in panel.slots.items():
                if key not in {"A", "B", "C", "D"}:
                    raise ValueError(f"Invalid slot {key}")
                if connection_id is None:
                    continue
                connection = self.config.connections[connection_id]
                if panel.station_id not in {connection.station_a_id, connection.station_b_id}:
                    raise ValueError(f"Panel {panel.id} cannot use connection {connection_id}")

    def _validate_command(self, command: Command, now: datetime) -> str | None:
        if command.traffic_session_id != self.config.id:
            return "wrong_session"
        if command.panel_id not in self.config.panels:
            return "unknown_panel"
        if command.expected_revision != self.revision:
            return "stale_revision"
        if command.expires_at is not None and now > command.expires_at:
            return "expired_command"
        if command.key not in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "B", "C", "D", "#", "*"}:
            return "unknown_key"
        return None

    def _handle_key(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
        command: Command,
    ) -> tuple[bool, str | None]:
        key = command.key

        if runtime.owner_client_id and runtime.owner_client_id != command.client_id:
            return False, "interaction_owned"

        if runtime.mode == InteractionMode.IDLE:
            if key == "*":
                return True, None
            if key not in {"A", "B", "C", "D"}:
                return False, "select_slot_first"
            slot = key  # type: ignore[assignment]
            connection_id = panel.slots.get(slot)
            if connection_id is None:
                return False, "unused_slot"
            line = self.connections[connection_id]
            if line.state == ConnectionState.FREE:
                runtime.mode = InteractionMode.ENTER_TRAIN
                runtime.selected_slot = slot
                runtime.owner_client_id = command.client_id
                return True, None
            if line.state == ConnectionState.REQUESTED:
                if line.from_station_id == panel.station_id:
                    runtime.mode = InteractionMode.AWAITING_PERMISSION
                else:
                    runtime.mode = InteractionMode.INCOMING_REQUEST
                runtime.selected_slot = slot
                runtime.train_number = line.train_number or ""
                return True, None
            if line.state == ConnectionState.RESERVED and line.from_station_id == panel.station_id:
                runtime.mode = InteractionMode.READY_DEPARTURE
                runtime.selected_slot = slot
                runtime.train_number = line.train_number or ""
                return True, None
            if line.state == ConnectionState.OCCUPIED and line.to_station_id == panel.station_id:
                runtime.mode = InteractionMode.INCOMING_ARRIVAL
                runtime.selected_slot = slot
                runtime.train_number = line.train_number or ""
                runtime.owner_client_id = command.client_id
                return True, None
            return False, "connection_busy"

        if runtime.mode == InteractionMode.ENTER_TRAIN:
            if key.isdigit():
                if len(runtime.train_number) >= 5:
                    return False, "train_number_too_long"
                runtime.train_number += key
                return True, None
            if key == "*":
                runtime.reset()
                return True, None
            if key != "#":
                return False, "enter_train_number"
            if not runtime.train_number:
                return False, "train_number_required"
            return self._reserve_or_request(panel, runtime)

        if runtime.mode == InteractionMode.AWAITING_PERMISSION:
            if key != "*":
                return False, "awaiting_response"
            return self._cancel_request(panel, runtime)

        # An operational decision - KLART, EJ KLART, AVGÅTT, ANKOMMIT - is
        # always given on A or B and never on #. The rule is a safety rule,
        # and it holds in the engine so no client can make its own exception.
        if runtime.mode == InteractionMode.INCOMING_REQUEST:
            if key == "A":
                return self._respond_request(panel, runtime, accept=True)
            if key == "B":
                return self._respond_request(panel, runtime, accept=False)
            if key == "*":
                # Leaving the screen is not an answer. The request stays where
                # it was and remains visible in the overview.
                runtime.reset()
                return True, None
            return False, "answer_with_a_or_b"

        if runtime.mode == InteractionMode.READY_DEPARTURE:
            if key == "*":
                runtime.mode = InteractionMode.CONFIRM_CANCEL
                runtime.owner_client_id = command.client_id
                return True, None
            if key != "A":
                return False, "depart_with_a"
            runtime.mode = InteractionMode.CONFIRM_DEPARTURE
            runtime.owner_client_id = command.client_id
            return True, None

        if runtime.mode == InteractionMode.CONFIRM_CANCEL:
            # Withdrawing a request is not one of the four operational
            # decisions, and the spec asks for it on # after an explicit
            # question.
            if key == "*":
                runtime.mode = InteractionMode.READY_DEPARTURE
                runtime.owner_client_id = None
                return True, None
            if key != "#":
                return False, "confirm_with_hash_or_star"
            return self._cancel_reservation(panel, runtime)

        if runtime.mode == InteractionMode.CONFIRM_DEPARTURE:
            if key in {"B", "*"}:
                runtime.mode = InteractionMode.READY_DEPARTURE
                runtime.owner_client_id = None
                return True, None
            if key != "A":
                return False, "depart_with_a"
            return self._depart(panel, runtime)

        if runtime.mode == InteractionMode.INCOMING_ARRIVAL:
            if key in {"B", "*"}:
                runtime.reset()
                return True, None
            if key != "A":
                return False, "arrive_with_a"
            return self._arrive(panel, runtime)

        return False, "unsupported_interaction"

    # ------------------------------------------------------------------
    # Traffic transitions. These are keyed on a station and a connection, not
    # on a panel and a slot, so a terminal reaching them over HTTP calls the
    # same code a key press does instead of replaying a key sequence.

    def open_case(
        self,
        station_id: str,
        connection_id: str,
        train_number: str,
    ) -> tuple[bool, str | None]:
        line = self.connections[connection_id]
        if line.state != ConnectionState.FREE:
            return False, "connection_busy"
        connection = self.config.connections[connection_id]
        mode = connection.dispatch_mode_override or self.config.default_dispatch_mode

        line.from_station_id = station_id
        line.to_station_id = connection.other_station(station_id)
        line.train_number = train_number
        line.request_id = str(uuid4())

        if mode == DispatchMode.CLEARANCE:
            line.state = ConnectionState.REQUESTED
            line.request_status = RequestStatus.PENDING
        else:
            line.state = ConnectionState.RESERVED
            line.request_status = RequestStatus.ACCEPTED
        return True, None

    def answer_case(
        self,
        station_id: str,
        connection_id: str,
        *,
        accept: bool,
    ) -> tuple[bool, str | None]:
        line = self.connections[connection_id]
        if line.state != ConnectionState.REQUESTED or line.to_station_id != station_id:
            return False, "request_no_longer_pending"
        if accept:
            line.state = ConnectionState.RESERVED
            line.request_status = RequestStatus.ACCEPTED
        else:
            line.request_status = RequestStatus.REJECTED
            line.clear()
        return True, None

    def withdraw_case(
        self,
        station_id: str,
        connection_id: str,
        *,
        expect: ConnectionState,
    ) -> tuple[bool, str | None]:
        line = self.connections[connection_id]
        if line.state != expect or line.from_station_id != station_id:
            return False, (
                "request_no_longer_pending"
                if expect == ConnectionState.REQUESTED
                else "reservation_no_longer_active"
            )
        line.request_status = RequestStatus.CANCELED
        line.clear()
        return True, None

    def depart_case(self, station_id: str, connection_id: str) -> tuple[bool, str | None]:
        line = self.connections[connection_id]
        if line.state != ConnectionState.RESERVED or line.from_station_id != station_id:
            return False, "departure_not_reserved"
        line.state = ConnectionState.OCCUPIED
        return True, None

    def arrive_case(self, station_id: str, connection_id: str) -> tuple[bool, str | None]:
        line = self.connections[connection_id]
        if line.state != ConnectionState.OCCUPIED or line.to_station_id != station_id:
            return False, "train_not_departed"
        line.clear()
        return True, None

    def _reserve_or_request(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        accepted, reason = self.open_case(
            panel.station_id,
            self._selected_connection(panel, runtime),
            runtime.train_number,
        )
        # The case now belongs to the connection, not to the foreground
        # screen. Return immediately so this panel can handle other trains
        # while the case remains visible in its A-D slot.
        if accepted:
            runtime.reset()
        return accepted, reason

    def _respond_request(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
        *,
        accept: bool,
    ) -> tuple[bool, str | None]:
        accepted, reason = self.answer_case(
            panel.station_id,
            self._selected_connection(panel, runtime),
            accept=accept,
        )
        if accepted:
            runtime.reset()
        return accepted, reason

    def _cancel_request(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        accepted, reason = self.withdraw_case(
            panel.station_id,
            self._selected_connection(panel, runtime),
            expect=ConnectionState.REQUESTED,
        )
        if accepted:
            runtime.reset()
        return accepted, reason

    def _cancel_reservation(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        accepted, reason = self.withdraw_case(
            panel.station_id,
            self._selected_connection(panel, runtime),
            expect=ConnectionState.RESERVED,
        )
        if accepted:
            runtime.reset()
        return accepted, reason

    def _depart(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        accepted, reason = self.depart_case(
            panel.station_id, self._selected_connection(panel, runtime)
        )
        if accepted:
            runtime.reset()
        return accepted, reason

    def _arrive(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        accepted, reason = self.arrive_case(
            panel.station_id, self._selected_connection(panel, runtime)
        )
        if accepted:
            runtime.reset()
        return accepted, reason

    @staticmethod
    def _slot_action(station_id: str, runtime: ConnectionRuntime) -> str:
        if runtime.state == ConnectionState.FREE:
            return "request_departure"
        outgoing = runtime.from_station_id == station_id
        if runtime.state == ConnectionState.REQUESTED:
            return "wait_permission" if outgoing else "answer_request"
        if runtime.state == ConnectionState.RESERVED:
            return "depart" if outgoing else "await_departure"
        if runtime.state == ConnectionState.OCCUPIED:
            return "in_transit" if outgoing else "confirm_arrival"
        return "view"

    @staticmethod
    def _slot_needs_attention(station_id: str, runtime: ConnectionRuntime) -> bool:
        if runtime.state == ConnectionState.REQUESTED:
            return runtime.to_station_id == station_id
        if runtime.state == ConnectionState.RESERVED:
            return runtime.from_station_id == station_id
        return False

    def _selected_connection(self, panel: PanelConfig, runtime: PanelRuntime) -> str:
        if runtime.selected_slot is None:
            raise RuntimeError("Interaction has no selected slot")
        connection_id = panel.slots.get(runtime.selected_slot)
        if connection_id is None:
            raise RuntimeError("Selected slot is not configured")
        return connection_id

    def _ack(
        self,
        command: Command,
        status: str,
        reason: str | None,
        previous_revision: int,
    ) -> CommandAck:
        return CommandAck(
            command_id=command.command_id,
            status=status,  # type: ignore[arg-type]
            reason=reason,
            previous_revision=previous_revision,
            revision=self.revision,
            snapshots=self.snapshots(),
        )
