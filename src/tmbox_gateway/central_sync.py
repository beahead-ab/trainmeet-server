from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_RUNTIME_PUBLICATION_URL = "https://cloud.trainmeet.app/config"
LEGACY_RUNTIME_PUBLICATION_URLS = frozenset({
    "https://trainmeet.app/konfig",
    "https://cloud.trainmeet.app/konfig",
})


def canonical_runtime_url(value: str) -> str:
    url = value.strip().rstrip("/")
    return DEFAULT_RUNTIME_PUBLICATION_URL if url in LEGACY_RUNTIME_PUBLICATION_URLS else url


class CentralSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class CentralRuntimeDownload:
    package: dict[str, Any]
    link_token: str | None = None


@dataclass(frozen=True)
class CentralRuntimeManifest:
    publication_id: str
    published_at: str
    package_checksum: str


def fetch_runtime_package(
    sync_code: str,
    endpoint_url: str = DEFAULT_RUNTIME_PUBLICATION_URL,
    *,
    timeout: float = 20,
) -> dict[str, Any]:
    return fetch_runtime_download(sync_code, endpoint_url, timeout=timeout).package


def fetch_runtime_download(
    sync_code: str,
    endpoint_url: str = DEFAULT_RUNTIME_PUBLICATION_URL,
    *,
    server_name: str | None = None,
    timeout: float = 20,
) -> CentralRuntimeDownload:
    code = "".join(character for character in sync_code if character.isdigit())
    if len(code) != 6:
        raise CentralSyncError("Synkkoden ska ha sex siffror")

    endpoint_url = canonical_runtime_url(endpoint_url)
    query = {"code": code}
    if server_name and server_name.strip():
        query["server_name"] = server_name.strip()
    separator = "&" if "?" in endpoint_url else "?"
    request = Request(
        f"{endpoint_url}{separator}{urlencode(query)}",
        headers={"Accept": "application/json", "User-Agent": "TrainMeet-Server/0.6"},
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
    link_token = payload.get("link_token")
    return CentralRuntimeDownload(
        package=package,
        link_token=str(link_token) if link_token else None,
    )


def fetch_linked_runtime(
    link_token: str,
    endpoint_url: str = DEFAULT_RUNTIME_PUBLICATION_URL,
    *,
    manifest_only: bool = False,
    timeout: float = 20,
) -> CentralRuntimeDownload | CentralRuntimeManifest:
    token = link_token.strip()
    if not token:
        raise CentralSyncError("Servern är inte kopplad till en central träff")
    query = {"token": token}
    if manifest_only:
        query["manifest"] = "1"
    endpoint_url = canonical_runtime_url(endpoint_url)
    separator = "&" if "?" in endpoint_url else "?"
    request = Request(
        f"{endpoint_url}{separator}{urlencode(query)}",
        headers={"Accept": "application/json", "User-Agent": "TrainMeet-Server/0.6"},
    )
    payload = _read_json(request, timeout=timeout)
    if manifest_only:
        try:
            return CentralRuntimeManifest(
                publication_id=str(payload["publication_id"]),
                published_at=str(payload["published_at"]),
                package_checksum=str(payload.get("package_checksum") or ""),
            )
        except (KeyError, TypeError) as error:
            raise CentralSyncError("TrainMeet skickade inget versionsbesked") from error
    package = payload.get("package") if isinstance(payload, dict) else None
    if not isinstance(package, dict):
        raise CentralSyncError("TrainMeet skickade inget driftpaket")
    return CentralRuntimeDownload(package=package, link_token=token)


def _read_json(request: Request, *, timeout: float) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            detail = None
        raise CentralSyncError(detail or "Synkningen kunde inte hämtas") from error
    except (URLError, TimeoutError) as error:
        raise CentralSyncError("TrainMeet kunde inte nås. Kontrollera internetanslutningen.") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CentralSyncError("TrainMeet skickade ett ogiltigt svar") from error
    if not isinstance(payload, dict):
        raise CentralSyncError("TrainMeet skickade ett ogiltigt svar")
    return payload


