"""Inställningar → Klarering → Tellurian Dispatch (#222): inställning, skugga och rapport.

Avstängt som standard och valbart per träff. I läget "skugga" läser servern
Tellurian bredvid sin egen klarering och visar avvikelser för administratören.
Den skriver ingenting till Tellurian och ändrar ingenting i TrainMeets trafik.

Auktoritetsläget (etapp 4) går inte att slå på. Platsen för det är
`AUTHORITY_AVAILABLE` och `TellurianCommands` i tellurian.py; se docs/tellurian-dispatch.md.
"""
from __future__ import annotations

import logging
from threading import RLock
import time
from typing import Any, Callable

from .tellurian import (
    AddressRejected, ShadowRunner, TellurianClient, check_address, preflight,
)

LOGGER = logging.getLogger("tmbox_gateway.integrations.tellurian")

MODES = ("off", "shadow")
#: Etapp 4: skrivningar går till Tellurian och TrainMeets egen klareringsmotor
#: stängs av för träffen. Slås på först när Stefans server finns och
#: skrivvägen (tellurian.TellurianCommands via kommandologgen) är kopplad till
#: TMBox, TKL och Drift genom serverns behörighetskontroll, med paus och
#: administratörens byte vid avbrott. Till dess avvisas läget "authority".
AUTHORITY_AVAILABLE = False
DEFAULTS: dict[str, Any] = {"base_url": "", "test_instance": False, "mode": "off"}


class SettingsError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_settings(payload: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode", previous["mode"])
    if mode == "authority" and not AUTHORITY_AVAILABLE:
        raise SettingsError("authority_unavailable")
    if mode not in MODES:
        raise SettingsError("mode_invalid")
    base_url = payload.get("base_url", previous["base_url"])
    test_instance = payload.get("test_instance", previous["test_instance"])
    if not isinstance(base_url, str) or not isinstance(test_instance, bool):
        raise SettingsError("invalid")
    base_url = base_url.strip()
    if not base_url:
        if mode != "off":
            raise SettingsError("address_required")
        return {"base_url": "", "test_instance": test_instance, "mode": mode}
    try:
        endpoint = check_address(base_url, test_instance=test_instance)
    except AddressRejected as error:
        raise SettingsError(error.code) from None
    return {"base_url": endpoint.origin, "test_instance": test_instance, "mode": mode}


class TellurianClearance:
    """Serverns del: lagring per träff, skuggläsning i bakgrunden och förhandsrapport.

    Bara administratören når den (kontrolleras i http_server). Den ägs av
    TrainMeetHTTPApplication och läser träffen därifrån, utan egna kopior.
    """

    def __init__(self, app, *, client_factory: Callable[..., TellurianClient] | None = None,
                 runner_options: dict[str, Any] | None = None):
        self._app = app
        self._lock = RLock()
        self._runner: ShadowRunner | None = None
        self._runner_key: tuple | None = None
        self._report: tuple[str, dict[str, Any]] | None = None
        self._client_factory = client_factory or (lambda endpoint: TellurianClient(endpoint, timeout=3.0))
        self._runner_options = runner_options or {}

    # ── Träffen ──────────────────────────────────────────────────────────
    def _context(self) -> dict[str, Any] | None:
        app = self._app
        if not app.runtime_store or not app.lifecycle:
            return None
        selected = app.lifecycle.selected()
        if not selected:
            return None
        return {"scope": f"{selected['region']}:{selected['meet_id']}", "region": selected["region"],
                "meet_id": selected["meet_id"]}

    def _settings(self, scope: str) -> dict[str, Any]:
        return {**DEFAULTS, **(self._app.runtime_store.tellurian_settings(scope) or {})}

    def _meet(self, context: dict[str, Any]) -> dict[str, Any]:
        """Stationer, sträckor och dagens tåg ur den körande, publicerade configen."""
        from ..runtime import matches_active_day

        store = self._app.runtime_store
        publication = store.active() if context["region"] == "eu" else None
        if publication is None:
            return {"stations": [], "connections": [], "services": [], "dispatch_mode": "", "clock": {}}
        payload = publication.payload
        day = store.active_day() or publication.active_day
        clock = store.clock_source_settings(context["scope"])
        return {
            "stations": [{"id": s["id"], "code": s.get("code", ""), "name": s.get("name", "")}
                         for s in payload.get("stations", [])],
            "connections": list(payload.get("connections", [])),
            "services": [s for s in payload.get("services", [])
                         if matches_active_day(str(s.get("days", "Dagl")), day)],
            "dispatch_mode": str((payload.get("meet") or {}).get("default_dispatch_mode") or "clearance"),
            "clock": {"source": clock.get("source", "internal"), "clock_name": clock.get("clock_name", "")},
        }

    def _trainmeet_cases(self) -> list[dict[str, Any]]:
        """TrainMeets öppna klareringsärenden, bara läsning (ingen utgång av ärenden)."""
        app = self._app
        service = app._station_service
        if service is None or app.operations_store is None:
            return []
        publication = service.publication()
        if publication is None:
            return []
        day = app.runtime_store.active_day() or publication.active_day
        cases = []
        for case in app.operations_store.open_clearances_for_station(publication.publication_id, day, None):
            state = ("requested" if case["status"] == "waiting"
                     else "departed" if service.case_departed(case) else "accepted")
            cases.append({"from_station_id": case["from_station_id"], "to_station_id": case["to_station_id"],
                          "train_number": service.case_train_number(case), "state": state})
        return cases

    # ── Skuggläsning ─────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Starta, byt eller stoppa skuggläsningen efter träffens inställning."""
        context = self._context()
        settings = self._settings(context["scope"]) if context else DEFAULTS
        wanted = None
        if context and context["region"] == "eu" and settings["mode"] == "shadow" and settings["base_url"]:
            wanted = (context["scope"], settings["base_url"], settings["test_instance"])
        with self._lock:
            if wanted == self._runner_key:
                return
            old, self._runner, self._runner_key = self._runner, None, None
        if old is not None:
            old.stop(wait=False)
        if wanted is None:
            return
        try:
            endpoint = check_address(wanted[1], test_instance=wanted[2])
        except AddressRejected as error:
            LOGGER.warning("Tellurian-adressen godtas inte längre (%s); skugga avstängd", error.code)
            return
        scope = wanted[0]
        runner = ShadowRunner(
            self._client_factory(endpoint),
            stations=lambda: self._meet({"scope": scope, "region": "eu"})["stations"],
            trainmeet_cases=self._trainmeet_cases, **self._runner_options)
        with self._lock:
            if self._runner is not None:  # någon annan hann före
                runner = None
            else:
                self._runner, self._runner_key = runner, wanted
        if runner is not None:
            runner.start()

    def close(self) -> None:
        with self._lock:
            runner, self._runner, self._runner_key = self._runner, None, None
        if runner is not None:
            runner.stop()

    # ── För HTTP-vägarna (administratör) ─────────────────────────────────
    def status(self) -> dict[str, Any]:
        context = self._context()
        if context is None:
            return {"available": False, "reason": "no_meet", "settings": dict(DEFAULTS), "modes": list(MODES),
                    "authority_available": AUTHORITY_AVAILABLE}
        settings = self._settings(context["scope"])
        with self._lock:
            runner = self._runner if self._runner_key and self._runner_key[0] == context["scope"] else None
            report = self._report[1] if self._report and self._report[0] == context["scope"] else None
        shadow = runner.status() if runner else None
        if shadow and shadow["last_contact"]:
            shadow["last_contact_local"] = time.strftime("%H:%M", time.localtime(shadow["last_contact"]))
        return {"available": context["region"] == "eu", "reason": None if context["region"] == "eu" else "us",
                "meet_id": context["meet_id"], "settings": settings, "modes": list(MODES),
                "authority_available": AUTHORITY_AVAILABLE, "shadow": shadow, "report": report}

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        context = self._context()
        if context is None:
            raise SettingsError("no_meet")
        if context["region"] != "eu":
            raise SettingsError("not_eu")
        if payload.get("meet_id") != context["meet_id"]:
            raise SettingsError("stale_meet")
        settings = validate_settings(payload, self._settings(context["scope"]))
        self._app.runtime_store.save_tellurian_settings(context["scope"], settings)
        LOGGER.info("Tellurian Dispatch: läge %s för träffen", settings["mode"])
        self.refresh()
        return self.status()

    def test_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Förhandsrapporten. Använder formulärets osparade adress; skriver aldrig."""
        context = self._context()
        if context is None:
            raise SettingsError("no_meet")
        saved = self._settings(context["scope"])
        address = payload.get("base_url", saved["base_url"])
        test_instance = payload.get("test_instance", saved["test_instance"])
        if not isinstance(test_instance, bool):
            raise SettingsError("invalid")
        try:
            endpoint = check_address(address, test_instance=test_instance)
        except AddressRejected as error:
            raise SettingsError(error.code) from None
        meet = self._meet(context)
        report = preflight(self._client_factory(endpoint), stations=meet["stations"],
                           connections=meet["connections"], services=meet["services"],
                           clock_source=meet["clock"], dispatch_mode=meet["dispatch_mode"],
                           region=context["region"])
        report["address"] = endpoint.origin
        report["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report["checked_at_local"] = time.strftime("%H:%M", time.localtime())
        with self._lock:
            self._report = (context["scope"], report)
        return report


#: Svar till administratören. Koderna är stabila; texterna översätts i webben.
ERRORS: dict[str, tuple[int, str]] = {
    "authority_unavailable": (409, "Auktoritetsläget kommer när Stefans server finns. Välj Av eller Skugga."),
    "mode_invalid": (400, "Okänt läge för Tellurian Dispatch."),
    "invalid": (400, "Inställningen för Tellurian Dispatch är ogiltig."),
    "address_required": (400, "Ange adressen till Tellurian Dispatch."),
    "address_invalid": (400, "Adressen går inte att läsa. Ange till exempel http://192.168.1.20:5000."),
    "address_scheme": (400, "Adressen måste börja med http:// eller https://."),
    "address_credentials": (400, "Adressen får inte innehålla användarnamn eller lösenord."),
    "address_path": (400, "Ange bara serverns adress och port, utan sökväg."),
    "address_trainmeet_cloud": (400, "TrainMeet Cloud kan inte användas som Tellurian Dispatch."),
    "address_not_local": (400, "Bara en lokal adress på träffens nät godtas (privat IPv4, 169.254.x.x eller .local). En publik adress kräver valet Testinstans."),
    "no_meet": (409, "Koppla en träff först."),
    "not_eu": (409, "Tellurian Dispatch gäller bara EU-trafikspel."),
    "stale_meet": (409, "Träffen har ändrats. Läs in sidan igen."),
}


def error_response(code: str) -> tuple[int, str]:
    return ERRORS.get(code, (400, "Inställningen för Tellurian Dispatch är ogiltig."))
