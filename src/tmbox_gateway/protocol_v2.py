"""TMBox protocol v2: the station-centred service behind the MQTT surface.

The contract this implements lives in docs/protocol/v2. A box is assigned one
station, caches that station's config and snapshot in RAM, and speaks only to
send a complete command. The server owns every decision; the box renders what
comes back.

Three things separate this from the v1 panel engine. A device is bound to a
station rather than to A-D slots against connections. A command is a finished
operational act, not a key press. And revision is scoped - per movement, per
case, per station configuration - because one global counter would make an
unrelated event at another station reject a command here.
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from uuid import uuid4

from .identity import DisplayCapability, IdentityStore
from .models import SessionConfig, TrackType, UnknownTrackError, resolve_track_id
from .operations import SQLiteOperationsStore
from .runtime import RuntimePublication, SQLiteRuntimeStore, matches_active_day


LOGGER = logging.getLogger("tmbox_gateway.protocol_v2")

PROTOCOL_VERSION = 2

#: Actions that change one movement's state. Each maps to the field it moves.
MOVEMENT_ACTIONS: dict[str, tuple[str, str]] = {
    "train.position.set": ("departure", "positioned"),
    "train.departed": ("departure", "departed"),
    "train.arrived": ("arrival", "arrived"),
    "train.approaching": ("arrival", "approaching"),
}

READ_ACTIONS = {"train.lookup"}
CLEARANCE_ACTIONS = {"clearance.request", "clearance.response", "clearance.cancel"}
LINE_ACTIONS = {"line.available.publish", "line.available.acknowledge"}
CREW_ACTION = "train.crew_ready.set"
TRACK_ACTIONS = {"train.track.change"}
CONFIG_ACTIONS = {"device.config.ack"}

REVISION_SCOPES = {"movement", "case", "config"}

#: How long a request waits for an answer before it lapses. Counted in wall
#: time, but only while the meeting clock runs - a break must not expire the
#: requests that were open when everyone went for coffee.
CLEARANCE_TTL_SECONDS = 300


class CommandRejected(Exception):
    """A command the server refuses, carrying the reason a box should show."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class TMBoxStationService:
    """Builds retained payloads for a station and applies complete commands."""

    def __init__(
        self,
        runtime_store: SQLiteRuntimeStore,
        operations_store: SQLiteOperationsStore,
        identities: IdentityStore,
        *,
        clock_source: Callable[[], dict[str, Any]] | None = None,
    ):
        self.runtime_store = runtime_store
        self.operations_store = operations_store
        self.identities = identities
        self.clock_source = clock_source or operations_store.clock_status
        self._cached_publication_id: str | None = None
        self._cached_session_config: SessionConfig | None = None

    # ---------------------------------------------------------------- state

    def publication(self) -> RuntimePublication | None:
        return self.runtime_store.active()

    def session_config(self) -> SessionConfig | None:
        publication = self.publication()
        if publication is None:
            return None
        if publication.publication_id != self._cached_publication_id:
            self._cached_session_config = publication.session_config()
            self._cached_publication_id = publication.publication_id
        return self._cached_session_config

    def config_version(self) -> int:
        return self.runtime_store.config_version()

    # -------------------------------------------------------------- payloads

    def assignment_payload(self, device_id: str) -> dict[str, Any]:
        device = self.identities.client(device_id)
        station_id = device.station_id if device else None
        config = self.session_config()
        station = config.stations.get(station_id) if config and station_id else None
        return {
            "protocol_version": PROTOCOL_VERSION,
            "status": "assigned" if station_id else "waiting_for_assignment",
            "device_id": device_id,
            "device_code": device.display_name.split()[-1] if device else device_id,
            "station_id": station_id,
            "station_code": station.code if station else None,
        }

    def config_payload(
        self,
        station_id: str,
        display: DisplayCapability | None = None,
    ) -> dict[str, Any] | None:
        """The station's static configuration, replaced wholesale on publish."""
        config = self.session_config()
        if config is None:
            return None
        station = config.stations.get(station_id)
        if station is None:
            return None
        capability = display or DisplayCapability()
        rows = []
        for order, connection in enumerate(
            sorted(config.connections.values(), key=lambda entry: entry.id), start=1
        ):
            if station_id not in (connection.station_a_id, connection.station_b_id):
                continue
            other = config.stations.get(connection.other_station(station_id))
            rows.append(
                {
                    "connection_id": connection.id,
                    "other_station_code": other.code[:3].upper() if other else "",
                    "track_type": connection.track_type.value,
                    "dispatch_mode": (
                        connection.dispatch_mode_override or config.default_dispatch_mode
                    ).value,
                    "display_row": order,
                }
            )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "config_version": self.config_version(),
            "station": {"id": station.id, "code": station.code, "name": station.name},
            "tracks": [
                {
                    "id": track.id,
                    "display_label": track.display_label,
                    "operating_point_id": track.operating_point_id,
                    "sort_order": track.sort_order,
                }
                for track in config.tracks_for_station(station_id)
            ],
            "connections": rows,
            "display": capability.to_dict(),
        }

    def snapshot_payload(self, station_id: str) -> dict[str, Any] | None:
        """The station's live state, replaced wholesale on every publish."""
        publication = self.publication()
        config = self.session_config()
        if publication is None or config is None or station_id not in config.stations:
            return None
        active_day = self.runtime_store.active_day() or publication.active_day
        state = self.operations_store.tkl_station_state(
            publication.publication_id, active_day, station_id
        )
        movements = []
        revisions: dict[str, int] = {}
        for row in publication.payload["trains"]:
            if str(row["station_id"]) != station_id:
                continue
            if not matches_active_day(str(row["days"]), active_day):
                continue
            movement_id = str(row["id"])
            live = state["movements"].get(movement_id, {})
            revisions[movement_id] = int(live.get("revision", 0))
            movements.append(
                {
                    "id": movement_id,
                    "train_number": str(row["train_number"]),
                    "arrival_time": row.get("arrival_time"),
                    "departure_time": row.get("departure_time"),
                    "departure": live.get("departure", "none"),
                    "arrival": live.get("arrival", "none"),
                    "assignedTrackId": live.get("actualTrack") or row.get("track_id"),
                    "actualTrack": live.get("actualTrack"),
                    "crewReady": bool(live.get("crewReady", False)),
                    "allowed_actions": _allowed_actions(row, live),
                }
            )
        clock = self.clock_source()
        self._expire_due(publication.publication_id, active_day, clock)
        clearances = self.operations_store.open_clearances_for_station(
            publication.publication_id, active_day, station_id
        )
        line_messages = self.operations_store.open_line_messages_for_station(
            publication.publication_id, active_day, station_id
        )
        clock = self.clock_source()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "station_id": station_id,
            "revision": {
                "config_version": self.config_version(),
                "movements": revisions,
                "cases": {
                    **{case["clearance_id"]: case["revision"] for case in clearances},
                    **{
                        message["message_id"]: message["revision"]
                        for message in line_messages
                    },
                },
            },
            "movements": movements,
            "active_clearances": [
                {
                    "clearance_id": case["clearance_id"],
                    "movement_id": case["movement_id"],
                    "connection_id": case["connection_id"],
                    "status": case["status"],
                    "from_station_id": case["from_station_id"],
                    "to_station_id": case["to_station_id"],
                }
                for case in clearances
            ],
            "line_messages": [
                {
                    "message_id": message["message_id"],
                    "connection_id": message["connection_id"],
                    "status": message["status"],
                    "from_station_id": message["from_station_id"],
                }
                for message in line_messages
            ],
            "clock": {
                "time": str(clock.get("time") or "")[:5],
                "running": bool(clock.get("running")),
                "stopped_reason": clock.get("stopped_reason"),
            },
        }

    # -------------------------------------------------------------- commands

    def handle_command(self, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply one complete command and return the acknowledgement for it."""
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id:
            return self._ack(None, "rejected", "missing_message_id", None)

        cached = self.operations_store.device_command_response(device_id, message_id)
        if cached is not None:
            # The same question, not a second decision.
            return {**cached, "status": "duplicate"}

        try:
            station_id = self._require_station(device_id, payload)
            result = self._apply(device_id, station_id, payload)
        except CommandRejected as rejection:
            station_id = self.identities.station_for_client(device_id)
            return self._ack(message_id, "rejected", rejection.reason, station_id)

        acknowledgement = self._ack(
            message_id, "accepted", None, station_id, revision=result.get("revision")
        )
        if result.get("result") is not None:
            acknowledgement["result"] = result["result"]
        self.operations_store.remember_device_command(device_id, message_id, acknowledgement)
        return acknowledgement

    def _require_station(self, device_id: str, payload: dict[str, Any]) -> str:
        if int(payload.get("protocol_version") or 0) != PROTOCOL_VERSION:
            raise CommandRejected("unsupported_protocol")
        station_id = self.identities.station_for_client(device_id)
        if not station_id:
            raise CommandRejected("not_assigned")
        claimed = str(payload.get("station_id") or "").strip()
        if claimed and claimed != station_id:
            raise CommandRejected("station_mismatch")
        return station_id

    def _apply(
        self,
        device_id: str,
        station_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        publication = self.publication()
        config = self.session_config()
        if publication is None or config is None:
            raise CommandRejected("no_active_configuration")
        active_day = self.runtime_store.active_day() or publication.active_day

        if action in CONFIG_ACTIONS:
            self._check_revision(payload, "config", station_id, self.config_version())
            return {"revision": {"scope": "config", "key": station_id, "value": self.config_version()}}

        if action in READ_ACTIONS:
            return {"result": self._lookup(publication, active_day, station_id, payload)}

        if action in CLEARANCE_ACTIONS:
            return self._clearance(
                device_id, station_id, action, payload, publication, config, active_day
            )

        if action in LINE_ACTIONS:
            return self._line_available(
                device_id, station_id, action, payload, publication, config, active_day
            )

        if (
            action not in MOVEMENT_ACTIONS
            and action not in TRACK_ACTIONS
            and action != CREW_ACTION
        ):
            raise CommandRejected("unknown_action")

        body = payload.get("payload") or {}
        movement_id = str(body.get("movement_id") or "").strip()
        movement = self._movement(publication, active_day, station_id, movement_id)
        if movement is None:
            raise CommandRejected("unknown_movement")

        state = self.operations_store.tkl_station_state(
            publication.publication_id, active_day, station_id
        )["movements"].get(movement_id, {})
        self._check_revision(payload, "movement", movement_id, int(state.get("revision", 0)))

        arrival = state.get("arrival", "none")
        # The stored value never says ready: that is derived from the two
        # declarations, so writing it back would let a client fake it.
        departure = state.get("storedDeparture", state.get("departure", "none"))
        if departure == "ready":
            departure = "positioned"
        crew_ready = bool(state.get("crewReady", False))
        track = state.get("actualTrack") or movement.get("track_id")

        if action == CREW_ACTION:
            crew_ready = bool((body.get("crew_ready", True)))
        elif action in TRACK_ACTIONS:
            try:
                track = resolve_track_id(
                    config.tracks, station_id, str(body.get("track_id") or "")
                )
            except UnknownTrackError as error:
                raise CommandRejected("unknown_track") from error
            if track is None:
                raise CommandRejected("unknown_track")
        else:
            field, value = MOVEMENT_ACTIONS[action]
            if field == "arrival":
                arrival = value
            else:
                departure = value

        updated = self.operations_store.update_tkl_movement(
            publication.publication_id,
            active_day,
            station_id,
            movement_id,
            arrival=arrival,
            departure=departure,
            actual_track=track,
            updated_by=device_id,
            shift_id=None,
            event_type=action,
            crew_ready=crew_ready,
        )
        if action in TRACK_ACTIONS:
            self.operations_store.invalidate_clearances_for_movement(
                publication.publication_id,
                active_day,
                movement_id,
                device_id,
                "track_changed",
            )
        if action == "train.arrived":
            self._release_on_arrival(
                publication, active_day, station_id, movement, device_id
            )
        return {
            "revision": {
                "scope": "movement",
                "key": movement_id,
                "value": int(updated["revision"]),
            }
        }

    def _clearance(
        self,
        device_id: str,
        station_id: str,
        action: str,
        payload: dict[str, Any],
        publication: RuntimePublication,
        config: SessionConfig,
        active_day: str,
    ) -> dict[str, Any]:
        body = payload.get("payload") or {}
        self._expire_due(publication.publication_id, active_day, self.clock_source())

        if action == "clearance.request":
            movement_id = str(body.get("movement_id") or "").strip()
            movement = self._movement(publication, active_day, station_id, movement_id)
            if movement is None:
                raise CommandRejected("unknown_movement")
            connection = config.connections.get(str(body.get("connection_id") or ""))
            if connection is None or station_id not in (
                connection.station_a_id,
                connection.station_b_id,
            ):
                raise CommandRejected("unknown_connection")

            state = self.operations_store.tkl_station_state(
                publication.publication_id, active_day, station_id
            )["movements"].get(movement_id, {})
            self._check_revision(payload, "movement", movement_id, int(state.get("revision", 0)))

            channel_id = self.operations_store.channel_id(
                connection.id,
                station_id,
                double_track=connection.track_type is TrackType.DOUBLE,
            )
            occupied = self.operations_store.open_clearance_on_channel(
                publication.publication_id, active_day, channel_id
            )
            if occupied is not None:
                raise CommandRejected("channel_occupied")

            case = self.operations_store.request_clearance(
                publication.publication_id,
                active_day,
                clearance_id=str(body.get("clearance_id") or "") or f"clr-{uuid4().hex[:8]}",
                movement_id=movement_id,
                connection_id=connection.id,
                channel_id=channel_id,
                from_station_id=station_id,
                to_station_id=connection.other_station(station_id),
                track_id=state.get("actualTrack") or movement.get("track_id"),
                requested_by=device_id,
                ttl_seconds=CLEARANCE_TTL_SECONDS,
            )
            return {
                "revision": {
                    "scope": "case",
                    "key": case["clearance_id"],
                    "value": case["revision"],
                }
            }

        case = self.operations_store.clearance(str(body.get("clearance_id") or ""))
        if case is None:
            raise CommandRejected("unknown_clearance")
        self._check_revision(payload, "case", case["clearance_id"], case["revision"])
        if case["status"] != "waiting":
            raise CommandRejected("clearance_not_pending")

        if action == "clearance.response":
            if case["to_station_id"] != station_id:
                raise CommandRejected("not_receiver")
            approved = bool(body.get("approved"))
            settled = self.operations_store.settle_clearance(
                case["clearance_id"],
                "approved" if approved else "rejected",
                device_id,
            )
        else:
            if case["from_station_id"] != station_id:
                raise CommandRejected("not_sender")
            settled = self.operations_store.settle_clearance(
                case["clearance_id"], "cancelled", device_id
            )
        return {
            "revision": {
                "scope": "case",
                "key": settled["clearance_id"],
                "value": settled["revision"],
            }
        }

    def _line_available(
        self,
        device_id: str,
        station_id: str,
        action: str,
        payload: dict[str, Any],
        publication: RuntimePublication,
        config: SessionConfig,
        active_day: str,
    ) -> dict[str, Any]:
        """One-sided information, never a question.

        Deliberately never checked against channel occupancy and never turned
        into a clearance case: it carries no decision, so a receiving station
        can only acknowledge that it was shown.
        """
        body = payload.get("payload") or {}
        if action == "line.available.acknowledge":
            message_id = str(body.get("message_id") or "")
            message = self.operations_store.line_message(message_id)
            if message is None:
                raise CommandRejected("unknown_message")
            self._check_revision(payload, "case", message_id, message["revision"])
            if message["to_station_id"] != station_id:
                raise CommandRejected("not_receiver")
            if message["status"] != "delivered_to_device":
                raise CommandRejected("already_acknowledged")
            acknowledged = self.operations_store.acknowledge_line_available(
                message_id, device_id
            )
            return {
                "revision": {
                    "scope": "case",
                    "key": message_id,
                    "value": acknowledged["revision"],
                }
            }

        connection = config.connections.get(str(body.get("connection_id") or ""))
        if connection is None or station_id not in (
            connection.station_a_id,
            connection.station_b_id,
        ):
            raise CommandRejected("unknown_connection")
        movement_id = str(body.get("movement_id") or "").strip() or None
        if movement_id and self._movement(publication, active_day, station_id, movement_id) is None:
            raise CommandRejected("unknown_movement")
        message = self.operations_store.publish_line_available(
            publication.publication_id,
            active_day,
            message_id=str(body.get("message_id") or "") or f"line-{uuid4().hex[:8]}",
            connection_id=connection.id,
            from_station_id=station_id,
            to_station_id=connection.other_station(station_id),
            movement_id=movement_id,
            created_by=device_id,
        )
        return {
            "revision": {
                "scope": "case",
                "key": message["message_id"],
                "value": message["revision"],
            }
        }

    def _release_on_arrival(
        self,
        publication: RuntimePublication,
        active_day: str,
        station_id: str,
        movement: dict[str, Any],
        actor: str,
    ) -> None:
        """An arriving train frees the line it was granted.

        The two stations hold separate rows for the same train, so the case is
        matched by train number: the approved clearance heading here for this
        train is the one the arrival releases.
        """
        train_number = str(movement.get("train_number") or "")
        numbers = {
            str(row["id"]): str(row["train_number"]) for row in publication.payload["trains"]
        }
        for case in self.operations_store.open_clearances_for_station(
            publication.publication_id, active_day, station_id
        ):
            if case["status"] != "approved" or case["to_station_id"] != station_id:
                continue
            if numbers.get(case["movement_id"]) != train_number:
                continue
            self.operations_store.release_clearance(case["clearance_id"], actor)

    def _expire_due(
        self,
        publication_id: str,
        active_day: str,
        clock: dict[str, Any],
    ) -> None:
        if not bool(clock.get("running")):
            # A stopped meeting clock means the meet is paused. Nothing should
            # lapse while nobody is running trains.
            return
        self.operations_store.expire_due_clearances(publication_id, active_day)

    def _lookup(
        self,
        publication: RuntimePublication,
        active_day: str,
        station_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Find a train number's movements at this station.

        A train number can have several movements at one station on one day -
        an arrival and a later departure. The meeting clock picks first; only
        a genuinely ambiguous number comes back as a list for the box to page
        through.
        """
        body = payload.get("payload") or {}
        train_number = str(body.get("train_number") or "").strip()
        if not train_number:
            raise CommandRejected("missing_train_number")
        matches = [
            {
                "movement_id": str(row["id"]),
                "train_number": str(row["train_number"]),
                "arrival_time": row.get("arrival_time"),
                "departure_time": row.get("departure_time"),
                "sort_time": row.get("sort_time"),
                "track_id": row.get("track_id"),
            }
            for row in publication.payload["trains"]
            if str(row["station_id"]) == station_id
            and str(row["train_number"]) == train_number
            and matches_active_day(str(row["days"]), active_day)
        ]
        if not matches:
            raise CommandRejected("unknown_train_number")
        clock = str(self.clock_source().get("time") or "")[:5]
        matches.sort(key=lambda entry: _minutes_from(clock, str(entry["sort_time"] or "")))
        return {
            "train_number": train_number,
            "matches": matches,
            "ambiguous": len(matches) > 1,
        }

    @staticmethod
    def _movement(
        publication: RuntimePublication,
        active_day: str,
        station_id: str,
        movement_id: str,
    ) -> dict[str, Any] | None:
        for row in publication.payload["trains"]:
            if str(row["id"]) != movement_id:
                continue
            if str(row["station_id"]) != station_id:
                return None
            if not matches_active_day(str(row["days"]), active_day):
                return None
            return row
        return None

    @staticmethod
    def _check_revision(
        payload: dict[str, Any],
        scope: str,
        key: str,
        current: int,
    ) -> None:
        expected = payload.get("expected_revision")
        if expected is None:
            # The firmware slice in flight does not send one yet. A command
            # without the field is optimistic and allowed through.
            return
        if not isinstance(expected, dict):
            raise CommandRejected("invalid_revision")
        if str(expected.get("scope")) not in REVISION_SCOPES:
            raise CommandRejected("invalid_revision")
        if str(expected.get("scope")) != scope or str(expected.get("key")) != key:
            raise CommandRejected("invalid_revision")
        if int(expected.get("value", -1)) != current:
            raise CommandRejected("stale_revision")

    def _ack(
        self,
        message_id: str | None,
        status: str,
        reason: str | None,
        station_id: str | None,
        *,
        revision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "message_id": message_id,
            "status": status,
            "reason": reason,
            "revision": revision,
            "snapshot": self.snapshot_payload(station_id) if station_id else None,
        }


def _allowed_actions(row: dict[str, Any], live: dict[str, Any]) -> list[str]:
    """What the server will accept for this movement right now.

    Firmware derives the same thing from the cached snapshot to decide which
    key labels to draw, but the decision itself is always the server's: a
    button that looks allowed and is refused shows the reason and waits.
    """
    departure = live.get("departure", "none")
    arrival = live.get("arrival", "none")
    actions: list[str] = []
    departs = bool(row.get("departure_time"))
    arrives = bool(row.get("arrival_time"))

    if departs and departure != "departed":
        if departure == "none":
            actions.append("train.position.set")
        elif departure == "positioned":
            actions.append(CREW_ACTION)
        elif departure == "ready":
            actions.append("clearance.request")
            actions.append("train.departed")
        if departure != "none":
            actions.append("train.track.change")
    if arrives and arrival != "arrived":
        if arrival == "none":
            actions.append("train.approaching")
        actions.append("train.arrived")
    return actions


def _minutes_from(clock: str, moment: str) -> int:
    """Distance forward from the meeting clock, wrapping past midnight."""
    now = _minutes(clock)
    then = _minutes(moment)
    if now is None or then is None:
        return 24 * 60
    return (then - now) % (24 * 60)


def _minutes(value: str) -> int | None:
    parts = value.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None
