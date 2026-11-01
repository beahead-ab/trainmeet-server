"""Fotona av ett köruppdrag tolkas i TrainMeet Cloud (#225).

Servern tolkar inga bilder (produktgränsen i docs/cloud-server.md). När en
administratör eller ägare fotar pappret som förarna har, för att servern har
en annan version av en tjänst, skickas fotona till Cloud med kopplingsnyckeln.
Cloud skriver av pappret med AI och bygger tjänsten; svaret är ett förslag som
administratören granskar här innan något sparas.

Det är ett undantag från D1 (Casper, 1 november 2026; se docs/cloud-server.md),
som e-posten och klockans livetrafik. Begäran bär bara `SCAN_FIELDS`: sidorna,
tjänstens nummer, stationernas namn som stavningshjälp och språket. Ingen
konfiguration, ingen historik och inga konton.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.error import HTTPError
from urllib.request import Request

from .central_sync import CentralSyncError, _read_json, canonical_runtime_url

#: Det enda en tolkning får bära.
SCAN_FIELDS = frozenset({"pages", "number", "stations", "language"})


def duty_scan_url(endpoint_url: str) -> str:
    """Clouds adress för tolkningen, på samma värd som driftpaketet."""
    parsed = urlparse(canonical_runtime_url(endpoint_url))
    path = parsed.path.rstrip("/")
    for suffix in ("/config", "/konfig"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunparse((parsed.scheme, parsed.netloc, f"{path}/api/server-duty-scan", "", "", ""))


def read_duty_photos(link_token: str, endpoint_url: str, payload: dict[str, Any],
                     *, timeout: float = 240) -> dict[str, Any]:
    """Be Cloud tolka fotona. Svaret är ``{"duty", "warnings", "pages"}``.

    Nyckeln står i Authorization-huvudet, aldrig i adressen. Fält utanför
    `SCAN_FIELDS` skickas inte. Tolkningen tar upp till ett par minuter.
    """
    token = link_token.strip()
    if not token:
        raise CentralSyncError("Servern är inte kopplad till TrainMeet Cloud")
    body = {key: value for key, value in payload.items() if key in SCAN_FIELDS}
    request = Request(
        duty_scan_url(endpoint_url),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "Authorization": f"Bearer {token}", "User-Agent": "TrainMeet-Server"},
    )
    try:
        return _read_json(request, timeout=timeout, failure="TrainMeet Cloud kunde inte tolka fotona")
    except CentralSyncError as error:
        # En Cloud utan avläsningen svarar 404: säg vad som saknas.
        if isinstance(error.__cause__, HTTPError) and error.__cause__.code == 404:
            raise CentralSyncError("TrainMeet Cloud kan inte läsa av foton än. Cloud behöver uppdateras.") from error
        raise
