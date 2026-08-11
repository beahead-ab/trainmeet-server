from __future__ import annotations

from .models import (
    ConnectionRuntime,
    ConnectionState,
    InteractionMode,
    PanelConfig,
    PanelRuntime,
    SessionConfig,
    SlotKey,
)


LCD_COLUMNS = 16


def fit_line(left: str = "", right: str = "") -> str:
    """Render two tokens into an exact 16 character LCD row."""
    right = right[-LCD_COLUMNS:]
    room_for_left = max(0, LCD_COLUMNS - len(right))
    left = left[:room_for_left]
    gap = LCD_COLUMNS - len(left) - len(right)
    return f"{left}{' ' * gap}{right}"


def allowed_keys(runtime: PanelRuntime, panel: PanelConfig) -> list[str]:
    if runtime.mode == InteractionMode.IDLE:
        return [key for key, value in panel.slots.items() if value is not None] + ["*"]
    if runtime.mode == InteractionMode.ENTER_TRAIN:
        return [str(value) for value in range(10)] + ["#", "*"]
    if runtime.mode == InteractionMode.AWAITING_PERMISSION:
        return ["*"]
    if runtime.mode == InteractionMode.INCOMING_REQUEST:
        return ["#", "*"]
    if runtime.mode == InteractionMode.READY_DEPARTURE:
        return [runtime.selected_slot, "*"] if runtime.selected_slot else ["*"]
    if runtime.mode in {
        InteractionMode.CONFIRM_DEPARTURE,
        InteractionMode.CONFIRM_CANCEL,
        InteractionMode.INCOMING_ARRIVAL,
    }:
        return ["#", "*"]
    return []


def render_panel(
    config: SessionConfig,
    panel: PanelConfig,
    panel_runtime: PanelRuntime,
    connection_runtime: dict[str, ConnectionRuntime],
) -> tuple[str, str]:
    interaction = _render_interaction(config, panel, panel_runtime, connection_runtime)
    if interaction is not None:
        return interaction

    tokens: dict[SlotKey, str] = {"A": "", "B": "", "C": "", "D": ""}
    for key, connection_id in panel.slots.items():
        if connection_id is None:
            continue
        connection = config.connections[connection_id]
        runtime = connection_runtime[connection_id]
        other_id = connection.other_station(panel.station_id)
        other_code = config.stations[other_id].code[:3].upper()
        tokens[key] = _slot_token(key, panel.station_id, other_code, runtime)

    line1 = fit_line(tokens["A"], tokens["B"])
    line2_right = tokens["D"] or config.clock_time[:5]
    line2 = fit_line(tokens["C"], line2_right)
    return line1, line2


def _render_interaction(
    config: SessionConfig,
    panel: PanelConfig,
    runtime: PanelRuntime,
    connection_runtime: dict[str, ConnectionRuntime],
) -> tuple[str, str] | None:
    if runtime.mode == InteractionMode.IDLE or runtime.selected_slot is None:
        return None

    connection_id = panel.slots.get(runtime.selected_slot)
    if connection_id is None:
        return None
    connection = config.connections[connection_id]
    line = connection_runtime[connection_id]
    other_id = connection.other_station(panel.station_id)
    other_code = config.stations[other_id].code[:3].upper()
    train = runtime.train_number or line.train_number or ""

    if runtime.mode == InteractionMode.ENTER_TRAIN:
        cursor = "_" if len(train) < 5 else ""
        return fit_line(f"Till: {other_code}", "#=OK"), fit_line(f"Tåg: {train}{cursor}", "*=Avb")
    if runtime.mode == InteractionMode.AWAITING_PERMISSION:
        return fit_line(f"{train}->{other_code}"), fit_line("Väntar svar...", "*=Avb")
    if runtime.mode == InteractionMode.INCOMING_REQUEST:
        from_code = config.stations[line.from_station_id or other_id].code[:3].upper()
        return fit_line(f"Från {from_code}"), fit_line(f"Tåg {train}", "#/*")
    if runtime.mode == InteractionMode.READY_DEPARTURE:
        arrow = _departure_symbol(runtime.selected_slot)
        return fit_line(f"{train}{arrow}{other_code}", "KLAR"), fit_line(f"{runtime.selected_slot}=Avg", "*=Avb")
    if runtime.mode == InteractionMode.CONFIRM_DEPARTURE:
        arrow = _departure_symbol(runtime.selected_slot)
        return fit_line(f"{train}{arrow}{other_code}", "Tåg ut?"), fit_line("#=Ja", "*=Nej")
    if runtime.mode == InteractionMode.CONFIRM_CANCEL:
        return fit_line("Ångra körning?"), fit_line("#=Ja", "*=Nej")
    if runtime.mode == InteractionMode.INCOMING_ARRIVAL:
        from_code = config.stations[line.from_station_id or other_id].code[:3].upper()
        return fit_line(f"Ank {train}", f"från {from_code}"), fit_line("#=Kvittera", "*=Avb")
    return None


def _slot_token(
    key: SlotKey,
    station_id: str,
    other_code: str,
    runtime: ConnectionRuntime,
) -> str:
    if runtime.state == ConnectionState.FREE:
        marker = _arrow(key, outgoing=True)
        label = other_code
    else:
        outgoing = runtime.from_station_id == station_id
        marker = _arrow(key, outgoing=outgoing)
        if runtime.state == ConnectionState.REQUESTED:
            marker = "~" if outgoing else "!"
        label = runtime.train_number or other_code

    if key in {"A", "C"}:
        return f"{key}{marker}{label}"
    return f"{label}{marker}{key}"


def _arrow(key: SlotKey, *, outgoing: bool) -> str:
    points_left = (key in {"A", "C"} and outgoing) or (key in {"B", "D"} and not outgoing)
    return "<" if points_left else ">"


def _departure_symbol(key: SlotKey) -> str:
    return "◀" if key in {"A", "C"} else "▶"
