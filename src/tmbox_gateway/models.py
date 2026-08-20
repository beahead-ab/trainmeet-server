from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal


SlotKey = Literal["A", "B", "C", "D"]


class UnknownTrackError(ValueError):
    """Raised when a track cannot be resolved against the station catalogue."""


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
class TrackConfig:
    """One track in a station's catalogue.

    A track is a stable text identifier, never free text and never an
    enumeration. Inactivating a track hides it from new choices without
    breaking the history that already points at it, so ids are never reused.
    """

    id: str
    display_label: str
    station_id: str
    operating_point_id: str | None = None
    active: bool = True
    sort_order: int = 0


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
    tracks: dict[str, TrackConfig] = field(default_factory=dict)
    clock_time: str = "12:00"

    def tracks_for_station(
        self,
        station_id: str,
        operating_point_id: str | None = None,
        *,
        include_inactive: bool = False,
    ) -> list[TrackConfig]:
        """Return a station's tracks in the order a track selector shows them.

        sort_order decides, not the label, so 1A can precede 10 even though
        string sorting disagrees.
        """
        return sorted(
            (
                track
                for track in self.tracks.values()
                if track.station_id == station_id
                and (include_inactive or track.active)
                and (
                    operating_point_id is None
                    or track.operating_point_id == operating_point_id
                )
            ),
            key=lambda track: (track.sort_order, track.display_label),
        )

    def resolve_track(self, station_id: str, value: str | None) -> str | None:
        return resolve_track_id(self.tracks, station_id, value)


def resolve_track_id(
    tracks: dict[str, TrackConfig],
    station_id: str,
    value: str | None,
) -> str | None:
    """Turn a catalogue id or a visible label into a catalogue id.

    Terminals have always sent the label an operator reads on screen.
    Accepting it keeps them working, but what gets stored is always an id from
    the catalogue - an unknown track is refused, never written through.
    """
    candidate = (value or "").strip()
    if not candidate:
        return None
    track = tracks.get(candidate)
    if track is not None and track.station_id == station_id:
        return track.id
    matches = [
        track
        for track in tracks.values()
        if track.station_id == station_id
        and track.display_label.casefold() == candidate.casefold()
    ]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise UnknownTrackError(f"Spåret {candidate} finns inte i stationens spårkatalog")
    raise UnknownTrackError(
        f"Spårbeteckningen {candidate} finns på flera driftplatser; ange spårets id"
    )


def unconfigured_session() -> SessionConfig:
    """Return the deliberately empty state used before first installation."""
    return SessionConfig(
        id="unconfigured",
        name="Ingen träff konfigurerad",
        default_dispatch_mode=DispatchMode.CLEARANCE,
        stations={},
        connections={},
        panels={},
        tracks={},
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
