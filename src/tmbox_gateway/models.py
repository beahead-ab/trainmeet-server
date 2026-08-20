from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal


SlotKey = Literal["A", "B", "C", "D"]


class DispatchMode(StrEnum):
    CLEARANCE = "clearance"
    DIRECT = "direct"


class TrackType(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"


class ConnectionState(StrEnum):
    FREE = "free"
    REQUESTED = "requested"
    RESERVED = "reserved"
    OCCUPIED = "occupied"


class RequestStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELED = "canceled"
    EXPIRED = "expired"


class InteractionMode(StrEnum):
    IDLE = "idle"
    ENTER_TRAIN = "enter_train"
    AWAITING_PERMISSION = "awaiting_permission"
    INCOMING_REQUEST = "incoming_request"
    READY_DEPARTURE = "ready_departure"
    CONFIRM_DEPARTURE = "confirm_departure"
    CONFIRM_CANCEL = "confirm_cancel"
    INCOMING_ARRIVAL = "incoming_arrival"


@dataclass(frozen=True)
class StationConfig:
    id: str
    code: str
    name: str


@dataclass(frozen=True)
class ConnectionConfig:
    id: str
    station_a_id: str
    station_b_id: str
    track_type: TrackType = TrackType.SINGLE
    dispatch_mode_override: DispatchMode | None = None

    def other_station(self, station_id: str) -> str:
        if station_id == self.station_a_id:
            return self.station_b_id
        if station_id == self.station_b_id:
            return self.station_a_id
        raise ValueError(f"Station {station_id} is not an endpoint of {self.id}")


@dataclass(frozen=True)
class PanelConfig:
    id: str
    station_id: str
    name: str
    slots: dict[SlotKey, str | None]


@dataclass(frozen=True)
class SessionConfig:
    id: str
    name: str
    default_dispatch_mode: DispatchMode
    stations: dict[str, StationConfig]
    connections: dict[str, ConnectionConfig]
    panels: dict[str, PanelConfig]
    clock_time: str = "12:00"


def unconfigured_session() -> SessionConfig:
    """Return the deliberately empty state used before first installation."""
    return SessionConfig(
        id="unconfigured",
        name="Ingen träff konfigurerad",
        default_dispatch_mode=DispatchMode.CLEARANCE,
        stations={},
        connections={},
        panels={},
        clock_time="12:00",
    )


@dataclass
class ConnectionRuntime:
    state: ConnectionState = ConnectionState.FREE
    from_station_id: str | None = None
    to_station_id: str | None = None
    train_number: str | None = None
    request_id: str | None = None
    request_status: RequestStatus | None = None

    def clear(self) -> None:
        self.state = ConnectionState.FREE
        self.from_station_id = None
        self.to_station_id = None
        self.train_number = None
        self.request_id = None
        self.request_status = None


@dataclass
class PanelRuntime:
    mode: InteractionMode = InteractionMode.IDLE
    selected_slot: SlotKey | None = None
    train_number: str = ""
    owner_client_id: str | None = None

    def reset(self) -> None:
        self.mode = InteractionMode.IDLE
        self.selected_slot = None
        self.train_number = ""
        self.owner_client_id = None


@dataclass(frozen=True)
class Command:
    command_id: str
    client_id: str
    traffic_session_id: str
    panel_id: str
    expected_revision: int
    key: str
    sent_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


@dataclass(frozen=True)
class CommandAck:
    command_id: str
    status: Literal["accepted", "rejected", "duplicate"]
    reason: str | None
    previous_revision: int
    revision: int
    snapshots: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status,
            "reason": self.reason,
            "previous_revision": self.previous_revision,
            "revision": self.revision,
            "snapshots": self.snapshots,
        }
