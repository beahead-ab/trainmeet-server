"""Read-only documentation fixtures executed through the real V1 engine.

Print the vendored browser file; do not connect to MQTT, HTTP or a database.
tests/test_tmbox_catalog.py verifies it against the shipped artifact.
"""
from datetime import datetime, timedelta, timezone
import json

from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.models import (Command, ConnectionConfig, DispatchMode,
    PanelConfig, SessionConfig, StationConfig)


def build_catalog():
    scenarios = [
        ("clearance", "Skicka med klartecken och ta emot", "clearance", [
            ("a", "A", None, "Välj motstation LEK."),
            ("a", "#", "421", "Skriv 421 lokalt; # skickar hela numret och begär klart."),
            ("b", "A", None, "Mottagaren öppnar begäran."),
            ("b", "A", None, "A ger klart. Tåget har ännu inte avgått."),
            ("a", "A", None, "Avsändaren öppnar det godkända ärendet."),
            ("a", "A", None, "A öppnar frågan Tåg ut?"),
            ("a", "A", None, "A bekräftar faktisk avgång. Linjen blir upptagen."),
            ("b", "A", None, "Mottagaren öppnar tåget på väg in."),
            ("b", "A", None, "A bekräftar hel ankomst och frigör linjen."),
        ]),
        ("direct", "Skicka utan klartecken", "direct", [
            ("a", "A", None, "Välj motstation."),
            ("a", "#", "421", "Skriv tågnummret lokalt och bekräfta med #. Servern reserverar en fri linje."),
            ("a", "A", None, "Öppna reservationen."),
            ("a", "A", None, "Öppna avgångsbekräftelsen."),
            ("a", "A", None, "Bekräfta Avgått. Inget klartecken från mottagaren krävs i detta läge."),
            ("b", "A", None, "Öppna inkommande tåg."),
            ("b", "A", None, "Bekräfta Ankommit."),
        ]),
        ("reject", "Neka en begäran", "clearance", [
            ("a", "A", None, "Välj motstation."),
            ("a", "#", "421", "Bekräfta lokalt inmatat tågnummer."),
            ("b", "A", None, "Öppna begäran på mottagarstationen."),
            ("b", "B", None, "B nekar. Linjen frigörs; avsändaren kan senare begära igen."),
        ]),
        ("withdraw-waiting", "Återta före klartecken", "clearance", [
            ("a", "A", None, "Välj motstation."),
            ("a", "#", "421", "Skicka begäran."),
            ("a", "A", None, "Öppna väntande begäran igen."),
            ("a", "*", None, "I denna V1-vy återtar * begäran direkt. Det är inte bara Tillbaka."),
        ]),
        ("withdraw-reserved", "Återta reserverat tåg före avgång", "direct", [
            ("a", "A", None, "Välj motstation."),
            ("a", "#", "421", "Reservera linjen."),
            ("a", "A", None, "Öppna reservationen."),
            ("a", "*", None, "* öppnar Avbryt begäran?."),
            ("a", "#", None, "# bekräftar återtagning. Detta är inte en avgång eller ankomst."),
        ]),
        ("cancel-entry", "Avbryt lokal inmatning", "clearance", [
            ("a", "A", None, "Öppna tågnummersinmatning. Siffror buffras på boxen."),
            ("a", "*", None, "* tömmer utkastet och lämnar inmatningen. Ingen begäran har skapats."),
        ]),
        ("leave-request", "Lämna en begäran obesvarad", "clearance", [
            ("a", "A", None, "Välj motstation."),
            ("a", "#", "421", "Skicka begäran."),
            ("b", "A", None, "Mottagaren öppnar begäran."),
            ("b", "*", None, "* lämnar visningen utan att neka. Begäran ligger kvar."),
        ]),
        ("cancel-departure-confirmation", "Avbryt avgångsbekräftelsen", "direct", [
            ("a", "A", None, "Välj motstation."),
            ("a", "#", "421", "Reservera linjen."),
            ("a", "A", None, "Öppna ärendet."),
            ("a", "A", None, "Öppna Tåg ut?."),
            ("a", "B", None, "B betyder inte avgått. Reservationen behålls; * ger samma resultat här."),
        ]),
    ]
    screens, flows = {}, []
    for name, title, mode, actions in scenarios:
        config = SessionConfig("catalog", "Skrivskyddat exempel", DispatchMode(mode),
            {"a": StationConfig("a", "CDA", "Charlottendal"), "b": StationConfig("b", "LEK", "Lekby")},
            {"line": ConnectionConfig("line", "a", "b")},
            {p: PanelConfig(p, p, p, {"A": "line", "B": None, "C": None, "D": None}) for p in ["a", "b"]},
            clock_time="09:00")
        engine = TrafficEngine(config)
        steps = []
        for number, (panel, key, train, instruction) in enumerate(actions):
            now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=number)
            ack = engine.press(Command(command_id=f"{name}-{number}", client_id=panel,
                traffic_session_id="catalog", panel_id=panel, expected_revision=engine.revision,
                key=key, sent_at=now, expires_at=now+timedelta(seconds=5), train_number=train), now=now)
            assert ack.status == "accepted", (name, number, ack.reason)
            snapshot = engine.snapshot(panel)
            step = {"actor": config.stations[panel].code, "key": key, "instruction": instruction,
                "mode": snapshot["interaction"]["mode"], "lines": list(snapshot["display"].values()),
                "line_state": engine.connections["line"].state.value}
            steps.append(step)
            screens.setdefault(step["mode"], {"name": step["mode"], "lines": step["lines"]})
        flows.append({"id": name, "title": title, "steps": steps})
    return {"profile": "ESP8266 · protokoll V1 · 16×2", "screens": list(screens.values()), "flows": flows}


def browser_source():
    return "// Generated by scripts/tmbox_legacy_catalog.py; verified against the real V1 engine.\n" + \
        "globalThis.TMBoxLegacyCatalog = " + json.dumps(build_catalog(), ensure_ascii=False, indent=2) + ";\n"


if __name__ == "__main__":
    print(browser_source(), end="")
