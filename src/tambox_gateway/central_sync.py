from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_RUNTIME_PUBLICATION_URL = (
    "https://cjpghcjpqaxzqhxpwmjf.supabase.co/functions/v1/runtime-publication"
)


class CentralSyncError(RuntimeError):
    pass


def fetch_runtime_package(
    sync_code: str,
    endpoint_url: str = DEFAULT_RUNTIME_PUBLICATION_URL,
    *,
    timeout: float = 20,
) -> dict[str, Any]:
    code = "".join(character for character in sync_code if character.isdigit())
    if len(code) != 6:
        raise CentralSyncError("Synkkoden ska ha sex siffror")

    separator = "&" if "?" in endpoint_url else "?"
    request = Request(
        f"{endpoint_url}{separator}{urlencode({'code': code})}",
        headers={"Accept": "application/json", "User-Agent": "TrainMeet-Server/0.4"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            detail = None
        raise CentralSyncError(detail or "Synkkoden kunde inte hämtas") from error
    except (URLError, TimeoutError) as error:
        raise CentralSyncError("TrainMeet kunde inte nås. Kontrollera internetanslutningen.") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CentralSyncError("TrainMeet skickade ett ogiltigt driftpaket") from error

    package = payload.get("package") if isinstance(payload, dict) else None
    if not isinstance(package, dict):
        raise CentralSyncError("TrainMeet skickade inget driftpaket")
    return package
