from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any
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
    SlotKey,
)
from .storage import CorruptStateError, StateStore, session_config_fingerprint


class TrafficEngine:
    """Single-process authoritative Tambox state machine.

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
        self._validate_config()
        if self.state_store is not None:
            state = self.state_store.load(self.config.id, self.config_fingerprint)
            if state is not None:
                self._restore_state(state)

    def press(self, command: Command, *, now: datetime | None = None) -> CommandAck:
        with self._lock:
            return self._press_locked(command, now=now)

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

        restored_panels: dict[str, PanelRuntime] = {}
        for panel_id, value in panels.items():
            try:
                selected_slot = value.get("selected_slot")
                if selected_slot is not None and selected_slot not in {"A", "B", "C", "D"}:
                    raise ValueError("invalid selected slot")
                restored_panels[panel_id] = PanelRuntime(
                    mode=InteractionMode(value["mode"]),
                    selected_slot=selected_slot,
                    train_number=str(value.get("train_number", "")),
                    owner_client_id=value.get("owner_client_id"),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise CorruptStateError(f"Invalid panel state for {panel_id}") from error

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
        line1, line2 = render_panel(self.config, panel, runtime, self.connections)

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
            }

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
            "display": {"line1": line1, "line2": line2},
            "clock": {"time": self.config.clock_time[:5], "running": True, "source": "pi_internal"},
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

        if runtime.mode == InteractionMode.INCOMING_REQUEST:
            if key == "#":
                return self._respond_request(panel, runtime, accept=True)
            if key == "*":
                return self._respond_request(panel, runtime, accept=False)
            return False, "answer_with_hash_or_star"

        if runtime.mode == InteractionMode.READY_DEPARTURE:
            if key == "*":
                runtime.mode = InteractionMode.CONFIRM_CANCEL
                runtime.owner_client_id = command.client_id
                return True, None
            if key != runtime.selected_slot:
                return False, "confirm_with_same_slot"
            runtime.mode = InteractionMode.CONFIRM_DEPARTURE
            runtime.owner_client_id = command.client_id
            return True, None

        if runtime.mode == InteractionMode.CONFIRM_CANCEL:
            if key == "*":
                runtime.mode = InteractionMode.READY_DEPARTURE
                runtime.owner_client_id = None
                return True, None
            if key != "#":
                return False, "confirm_with_hash_or_star"
            return self._cancel_reservation(panel, runtime)

        if runtime.mode == InteractionMode.CONFIRM_DEPARTURE:
            if key == "*":
                runtime.mode = InteractionMode.READY_DEPARTURE
                runtime.owner_client_id = None
                return True, None
            if key != "#":
                return False, "confirm_with_hash_or_star"
            return self._depart(panel, runtime)

        if runtime.mode == InteractionMode.INCOMING_ARRIVAL:
            if key == "*":
                runtime.reset()
                return True, None
            if key != "#":
                return False, "confirm_with_hash_or_star"
            return self._arrive(panel, runtime)

        return False, "unsupported_interaction"

    def _reserve_or_request(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        connection_id = self._selected_connection(panel, runtime)
        line = self.connections[connection_id]
        if line.state != ConnectionState.FREE:
            return False, "connection_busy"
        connection = self.config.connections[connection_id]
        other_station_id = connection.other_station(panel.station_id)
        mode = connection.dispatch_mode_override or self.config.default_dispatch_mode

        line.from_station_id = panel.station_id
        line.to_station_id = other_station_id
        line.train_number = runtime.train_number
        line.request_id = str(uuid4())

        if mode == DispatchMode.CLEARANCE:
            line.state = ConnectionState.REQUESTED
            line.request_status = RequestStatus.PENDING
            runtime.mode = InteractionMode.AWAITING_PERMISSION
            runtime.owner_client_id = None
            receiving = self._panel_for_station_and_connection(other_station_id, connection_id)
            receiving_runtime = self.panels[receiving.id]
            receiving_runtime.mode = InteractionMode.INCOMING_REQUEST
            receiving_runtime.selected_slot = self._slot_for_connection(receiving, connection_id)
            receiving_runtime.train_number = line.train_number or ""
            receiving_runtime.owner_client_id = None
        else:
            line.state = ConnectionState.RESERVED
            line.request_status = RequestStatus.ACCEPTED
            runtime.mode = InteractionMode.READY_DEPARTURE
            runtime.owner_client_id = None
        return True, None

    def _respond_request(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
        *,
        accept: bool,
    ) -> tuple[bool, str | None]:
        connection_id = self._selected_connection(panel, runtime)
        line = self.connections[connection_id]
        if line.state != ConnectionState.REQUESTED or line.to_station_id != panel.station_id:
            return False, "request_no_longer_pending"

        sending_panel = self._panel_for_station_and_connection(line.from_station_id or "", connection_id)
        sending_runtime = self.panels[sending_panel.id]
        if accept:
            line.state = ConnectionState.RESERVED
            line.request_status = RequestStatus.ACCEPTED
            sending_runtime.mode = InteractionMode.READY_DEPARTURE
            sending_runtime.selected_slot = self._slot_for_connection(sending_panel, connection_id)
            sending_runtime.train_number = line.train_number or ""
            sending_runtime.owner_client_id = None
        else:
            line.request_status = RequestStatus.REJECTED
            line.clear()
            sending_runtime.reset()
        runtime.reset()
        return True, None

    def _cancel_request(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        connection_id = self._selected_connection(panel, runtime)
        line = self.connections[connection_id]
        if line.state != ConnectionState.REQUESTED or line.from_station_id != panel.station_id:
            return False, "request_no_longer_pending"
        receiving_panel = self._panel_for_station_and_connection(line.to_station_id or "", connection_id)
        line.request_status = RequestStatus.CANCELED
        line.clear()
        runtime.reset()
        self.panels[receiving_panel.id].reset()
        return True, None

    def _cancel_reservation(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        connection_id = self._selected_connection(panel, runtime)
        line = self.connections[connection_id]
        if line.state != ConnectionState.RESERVED or line.from_station_id != panel.station_id:
            return False, "reservation_no_longer_active"
        receiving_panel = self._panel_for_station_and_connection(line.to_station_id or "", connection_id)
        line.request_status = RequestStatus.CANCELED
        line.clear()
        runtime.reset()
        self.panels[receiving_panel.id].reset()
        return True, None

    def _depart(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        connection_id = self._selected_connection(panel, runtime)
        line = self.connections[connection_id]
        if line.state != ConnectionState.RESERVED or line.from_station_id != panel.station_id:
            return False, "departure_not_reserved"
        line.state = ConnectionState.OCCUPIED
        runtime.reset()
        receiving_panel = self._panel_for_station_and_connection(line.to_station_id or "", connection_id)
        self.panels[receiving_panel.id].reset()
        return True, None

    def _arrive(
        self,
        panel: PanelConfig,
        runtime: PanelRuntime,
    ) -> tuple[bool, str | None]:
        connection_id = self._selected_connection(panel, runtime)
        line = self.connections[connection_id]
        if line.state != ConnectionState.OCCUPIED or line.to_station_id != panel.station_id:
            return False, "train_not_departed"
        sending_panel = self._panel_for_station_and_connection(line.from_station_id or "", connection_id)
        line.clear()
        runtime.reset()
        self.panels[sending_panel.id].reset()
        return True, None

    def _selected_connection(self, panel: PanelConfig, runtime: PanelRuntime) -> str:
        if runtime.selected_slot is None:
            raise RuntimeError("Interaction has no selected slot")
        connection_id = panel.slots.get(runtime.selected_slot)
        if connection_id is None:
            raise RuntimeError("Selected slot is not configured")
        return connection_id

    def _panel_for_station_and_connection(self, station_id: str, connection_id: str) -> PanelConfig:
        matches = [
            panel
            for panel in self.config.panels.values()
            if panel.station_id == station_id and connection_id in panel.slots.values()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one panel for station {station_id} and connection {connection_id}"
            )
        return matches[0]

    @staticmethod
    def _slot_for_connection(panel: PanelConfig, connection_id: str) -> SlotKey:
        for key, value in panel.slots.items():
            if value == connection_id:
                return key
        raise RuntimeError(f"Panel {panel.id} does not expose {connection_id}")

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
