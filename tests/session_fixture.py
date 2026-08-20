from __future__ import annotations

from tmbox_gateway.models import (
    ConnectionConfig,
    DispatchMode,
    PanelConfig,
    SessionConfig,
    StationConfig,
    TrackType,
)


def sample_session(mode: DispatchMode = DispatchMode.CLEARANCE) -> SessionConfig:
    return SessionConfig(
        id="test-session",
        name="Test session",
        default_dispatch_mode=mode,
        stations={
            "station-a": StationConfig(id="station-a", code="CDA", name="Station A"),
            "station-b": StationConfig(id="station-b", code="LEK", name="Station B"),
        },
        connections={
            "connection-a-b": ConnectionConfig(
                id="connection-a-b",
                station_a_id="station-a",
                station_b_id="station-b",
                track_type=TrackType.SINGLE,
            )
        },
        panels={
            "panel-a": PanelConfig(
                id="panel-a",
                station_id="station-a",
                name="Panel A",
                slots={"A": "connection-a-b", "B": None, "C": None, "D": None},
            ),
            "panel-b": PanelConfig(
                id="panel-b",
                station_id="station-b",
                name="Panel B",
                slots={"A": "connection-a-b", "B": None, "C": None, "D": None},
            ),
        },
        clock_time="12:34",
    )
