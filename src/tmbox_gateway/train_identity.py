"""Tåg som delar nummer, och tåg som inte kan köras (Cloud #100).

Två saker som båda handlar om vilket tåg en rad gäller:

- **Delade nummer.** DSB IC 116 och SJ X 116 är två tåg samma dag. Cloud
  skiljer dem med `service_key` ("DSB IC", "SJ X"). Här får de en etikett:
  den långa ("DSB IC 116") för skärmar med plats, och den korta ("IC 116")
  för TMBox och TKL. Ett nummer som bara ett tåg har får ingen etikett, så
  allt annat ser ut som förut.

- **Manuella tåg (B4/B5).** Ett tåg som inte kan köras som planen säger -
  en tid saknas, två stopp i följd saknar egen sträcka på banan, gångtiden
  är negativ, eller Cloud har märkt det manuellt - körs av människor.
  Trafikmotorn kör ett tåg sträcka för sträcka, så två stopp utan egen
  sträcka går aldrig att köra här. Går banan en enda väg mellan dem lägger
  Cloud en passage på stationerna emellan i paketet (Caspers beslut
  1 november), och tåget körs som vanligt; skälet säger då bara att
  passagen saknas. Reglerna och texterna är desamma som Clouds
  (`cloud/manual_trains.py`), ord för ord. Det
  listas med en varning och automatiseras inte, men stoppar aldrig klockan,
  publiceringen eller något annat tåg. Vilka det är räknas ur paketet; om
  reglerna gäller för en publicering avgör runtime-lagret (en träff som
  redan pågick före uppdateringen kör vidare som förut).
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

from .timetable_core.findings import unique_path

_CLOCK = re.compile(r"(\d{1,2}):([0-5]\d)")


def _minutes(value: Any) -> int | None:
    match = _CLOCK.fullmatch(str(value or "").strip()[:5])
    if not match or int(match.group(1)) > 23:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _key(service: dict[str, Any]) -> str:
    return " ".join(str(service.get("service_key") or "").split())


def _short(keys: list[str]) -> list[str]:
    """Den kortaste formen som skiljer nycklarna åt: tågslaget ("IC", "X"),
    annars bolaget ("SJ", "DSB"), annars hela nyckeln."""
    for pick in (lambda key: key.split()[-1], lambda key: key.split()[0]):
        short = [pick(key) if key else "" for key in keys]
        if all(short) and len(set(short)) == len(short):
            return short
    return keys


def shared_labels(services: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """{tjänst-id: {"label", "short_label", "service_key"}} för tåg som delar nummer.

    Bara tjänster vars nummer delas med en annan i listan (en dags tidtabell)
    och som har en nyckel får en etikett.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for service in services:
        number = str(service.get("train_number") or "").strip()
        if number and service.get("id") is not None:
            groups.setdefault(number, []).append(service)
    result: dict[str, dict[str, str]] = {}
    for number, group in groups.items():
        if len(group) < 2:
            continue
        keys = [_key(service) for service in group]
        for service, key, short in zip(group, keys, _short(keys)):
            if not key:
                continue
            result[str(service["id"])] = {"label": f"{key} {number}", "short_label": f"{short} {number}", "service_key": key}
    return result


def movement_labels(trains: list[dict[str, Any]], labels: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Samma etiketter per tidtabellsrad (rörelse-id)."""
    return {str(row["id"]): labels[str(row.get("service_id"))] for row in trains
            if str(row.get("service_id")) in labels}


def _reachable(graph: dict[str, list[tuple[str, str]]], a: str, b: str) -> bool:
    seen, queue = {a}, deque([a])
    while queue:
        node = queue.popleft()
        for neighbor, _ in graph.get(node, []):
            if neighbor == b:
                return True
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return False


def _gap_reason(graph: dict[str, list[tuple[str, str]]], names: dict[str, str],
                before: dict[str, Any], after: dict[str, Any]) -> str:
    """Skälet när två stopp i följd saknar egen sträcka, samma som Clouds.

    En färd (avgång, sedan ankomst) med en enda väg passerar stationerna
    emellan, men utan passage i paketet kan den inte köras. En färd med flera
    lika korta vägar är tvetydig. Annars finns ingen väg, eller så är luckan
    inte en färd.
    """
    a, b = str(before.get("station_id")), str(after.get("station_id"))
    moves = bool(before.get("departure_time")) and bool(after.get("arrival_time"))
    path = unique_path(graph, a, b) if moves else None
    if path and len(path) > 1:
        return f"Tåget passerar {', '.join(names.get(target, target) for _, target, _ in path[:-1])} utan tid"
    if moves and _reachable(graph, a, b):
        return f"Vägen {names.get(a, a)}–{names.get(b, b)} är inte entydig"
    return f"Sträckan {names.get(a, a)}–{names.get(b, b)} finns inte på banan"


def manual_trains(payload: dict[str, Any]) -> dict[str, list[str]]:
    """{tjänst-id: skäl} för tåg som inte kan köras som planen säger.

    Skälen är svenska meningar som visas för admin. Ett tåg kan ha flera.
    """
    names = {str(station.get("id")): str(station.get("name") or station.get("code") or station.get("id"))
             for station in payload.get("stations") or []}
    links = {frozenset((str(item.get("station_a_id")), str(item.get("station_b_id"))))
             for item in payload.get("connections") or []}
    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in payload.get("connections") or []:
        a, b = str(item.get("station_a_id")), str(item.get("station_b_id"))
        if a != b:
            graph[a].append((b, str(item.get("id"))))
            graph[b].append((a, str(item.get("id"))))
    rows_by_service: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("trains") or []:
        rows_by_service.setdefault(str(row.get("service_id")), []).append(row)
    result: dict[str, list[str]] = {}
    for service in payload.get("services") or []:
        service_id = str(service.get("id"))
        reasons: list[str] = []
        if service.get("manual") is True:
            given = service.get("manual_reasons")
            if isinstance(given, list):
                reasons.extend(" ".join(str(item).split()) for item in given if str(item or "").strip())
            elif str(service.get("manual_reason") or "").strip():
                reasons.append(" ".join(str(service["manual_reason"]).split()))
            if not reasons:
                reasons.append("Markerat som manuellt i Cloud")
        for row in rows_by_service.get(service_id, []):
            if _minutes(row.get("arrival_time")) is None and _minutes(row.get("departure_time")) is None:
                reasons.append(f"Tid saknas vid {names.get(str(row.get('station_id')), row.get('station_id'))}")
        stops = sorted(service.get("stops") or [], key=lambda stop: int(stop.get("stop_order") or 0))
        for before, after in zip(stops, stops[1:]):
            a, b = str(before.get("station_id")), str(after.get("station_id"))
            if a != b and frozenset((a, b)) not in links:
                reasons.append(_gap_reason(graph, names, before, after))
                continue
            leaves = _minutes(before.get("departure_time")) if before.get("departure_time") else _minutes(before.get("arrival_time"))
            arrives = _minutes(after.get("arrival_time")) if after.get("arrival_time") else _minutes(after.get("departure_time"))
            if leaves is None or arrives is None:
                continue
            leaves += int(before.get("service_day_offset") or 0) * 1440
            arrives += int(after.get("service_day_offset") or 0) * 1440
            if arrives < leaves:
                reasons.append(f"Negativ gångtid {names.get(a, a)}–{names.get(b, b)}")
        if reasons:
            result[service_id] = list(dict.fromkeys(reasons))
    return result


def manual_summary(payload: dict[str, Any], manual: dict[str, list[str]],
                   labels: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """De manuella tågen som en lista för skärmar och API:er, i nummerordning."""
    services = {str(service.get("id")): service for service in payload.get("services") or []}
    labels = labels if labels is not None else shared_labels(list(services.values()))
    result = []
    for service_id, reasons in manual.items():
        service = services.get(service_id, {})
        number = str(service.get("train_number") or "")
        result.append({"service_id": service_id, "train_number": number, "days": service.get("days"),
                       "label": labels.get(service_id, {}).get("label", number), "reasons": reasons})
    return sorted(result, key=lambda item: (len(item["train_number"]), item["train_number"], item["service_id"]))
