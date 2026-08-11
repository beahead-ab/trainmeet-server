from __future__ import annotations

from .models import (
    ConnectionConfig,
    DispatchMode,
    PanelConfig,
    SessionConfig,
    StationConfig,
    TrackType,
)


def demo_session(mode: DispatchMode = DispatchMode.CLEARANCE) -> SessionConfig:
    stations = {
        "station-a": StationConfig(id="station-a", code="CDA", name="Charlottendahl"),
        "station-b": StationConfig(id="station-b", code="LEK", name="Lekeberg"),
    }
    connections = {
        "connection-a-b": ConnectionConfig(
            id="connection-a-b",
            station_a_id="station-a",
            station_b_id="station-b",
            track_type=TrackType.SINGLE,
        )
    }
    panels = {
        "panel-a": PanelConfig(
            id="panel-a",
            station_id="station-a",
            name="CDA Tambox",
            slots={"A": "connection-a-b", "B": None, "C": None, "D": None},
        ),
        "panel-b": PanelConfig(
            id="panel-b",
            station_id="station-b",
            name="LEK Tambox",
            slots={"A": "connection-a-b", "B": None, "C": None, "D": None},
        ),
    }
    return SessionConfig(
        id="demo-session",
        name="Tambox demo",
        default_dispatch_mode=mode,
        stations=stations,
        connections=connections,
        panels=panels,
        clock_time="12:34",
    )

