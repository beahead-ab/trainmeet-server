"""E-post till serverns användare, via TrainMeet Cloud.

Det här är serverns enda anrop som skickar något *till* Cloud. Synken går
fortfarande bara Cloud → Server (central_sync.py läser, och
test_product_boundaries vaktar det). Här skickas ingen träffdata, ingen
konfiguration och ingen historik, bara ett brev att lägga i Clouds kö:
mottagare, kod, serverns adress och språk (`MAIL_FIELDS`). Kontot är
mottagarens adress, så inget användarnamn följer med. Cloud har avsändaren,
mallarna och träffens och serverns namn. Servern har ingen egen e-post och
ingen Resend-nyckel.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request

from .central_sync import CentralSyncError, _read_json, canonical_runtime_url

#: Det enda ett brev får bära. Allt annat stannar på servern.
MAIL_FIELDS = frozenset({"kind", "to", "code", "server_url", "language"})


def server_mail_url(endpoint_url: str) -> str:
    """Clouds adress för e-post åt servern, på samma värd som driftpaketet."""
    parsed = urlparse(canonical_runtime_url(endpoint_url))
    path = parsed.path.rstrip("/")
    for suffix in ("/config", "/konfig"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunparse((parsed.scheme, parsed.netloc, f"{path}/api/server-mail", "", "", ""))


def send_server_mail(link_token: str, endpoint_url: str, payload: dict[str, Any],
                     *, timeout: float = 15) -> None:
    """Be Cloud skicka en inbjudan eller en kod för nytt lösenord.

    Nyckeln är kopplingsnyckeln och står i Authorization-huvudet, aldrig i
    adressen. Fält utanför `MAIL_FIELDS` skickas inte.
    """
    token = link_token.strip()
    if not token:
        raise CentralSyncError("Servern är inte kopplad till TrainMeet Cloud")
    letter = {key: value for key, value in payload.items() if key in MAIL_FIELDS}
    request = Request(
        server_mail_url(endpoint_url),
        data=json.dumps(letter).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "Authorization": f"Bearer {token}", "User-Agent": "TrainMeet-Server"},
    )
    _read_json(request, timeout=timeout, failure="TrainMeet Cloud kunde inte skicka e-posten")
