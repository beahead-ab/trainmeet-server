"""Köruppdragens sidor från TrainMeet Cloud (Cloud #113, Server #225).

Köruppdragen kan läggas in på två sätt. Under Inställningar → Lokförare på
servern (``driver_documents.py``), eller under fliken Lokförare i Cloud. Då
följer tjänsterna med träffpaketet (``driver_duties``), och varje tjänst
hänvisar till sina egna sidor som en liten PDF:

    "source": {"pages": [349, 352], "part": {"sha256": "…", "size": 25811}}

Servern hämtar delarna med sin serverkoppling när den hämtar paketet, innan
paketet tas emot, och sparar dem under sin sha256 i ``lokforaren/cloud/``
bredvid databasen. Efter synken behövs inte Cloud: föraren får sidorna från
servern också utan internet. En del som redan finns hämtas inte igen.

Mappen är skild från ``lokforaren/delar/`` (serverns egna köruppdrag), så att
städningen där aldrig tar en del som ett Cloud-paket hänvisar till.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from .central_sync import MAX_DRIVER_PART_BYTES, CentralSyncError

_SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_PAGES = 5000


def part_reference(raw_duty: Any) -> dict[str, Any] | None:
    """``{"sha256", "size"}`` för en tjänst i paketet, eller None om den inte har en giltig del."""
    if not isinstance(raw_duty, dict):
        return None
    source = raw_duty.get("source")
    part = source.get("part") if isinstance(source, dict) else None
    if not isinstance(part, dict):
        return None
    sha, size = part.get("sha256"), part.get("size")
    if not isinstance(sha, str) or not _SHA.match(sha):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DRIVER_PART_BYTES:
        return None
    return {"sha256": sha, "size": size}


def source_pages(raw_duty: Any) -> list[int]:
    """Sidorna i förlagan som tjänsten står på: ``[första, sista]`` eller tomt."""
    source = raw_duty.get("source") if isinstance(raw_duty, dict) else None
    pages = source.get("pages") if isinstance(source, dict) else None
    if not isinstance(pages, list) or not pages:
        return []
    valid = [page for page in pages[:2]
             if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= MAX_PAGES]
    if len(valid) != len(pages[:2]):
        return []
    return [valid[0], valid[-1]]


def package_parts(package: Any) -> list[dict[str, Any]]:
    """Alla delar som ett paket hänvisar till, en gång var."""
    seen: dict[str, dict[str, Any]] = {}
    duties = package.get("driver_duties") if isinstance(package, dict) else None
    for raw in duties if isinstance(duties, list) else []:
        reference = part_reference(raw)
        if reference is not None:
            seen.setdefault(reference["sha256"], reference)
    return list(seen.values())


Fetcher = Callable[[str, int], bytes]


class CloudDriverParts:
    """Clouds PDF-delar på den här servern, innehållsadresserade."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self._lock = threading.Lock()

    def path(self, sha256: str) -> Path | None:
        if not _SHA.match(str(sha256)):
            return None
        path = self.folder / f"{sha256}.pdf"
        return path if path.is_file() else None

    def missing(self, package: Any) -> list[dict[str, Any]]:
        return [part for part in package_parts(package) if self.path(part["sha256"]) is None]

    def ensure(self, package: Any, fetch: Fetcher) -> dict[str, int]:
        """Hämta delarna paketet hänvisar till och som inte redan finns.

        Allt eller inget för paketet: går en del inte att hämta, eller stämmer
        den inte, kastas ett fel och paketet ska inte tas emot. De delar som
        redan kommit fram ligger kvar och hämtas inte igen nästa gång.
        """
        parts = package_parts(package)
        fetched = 0
        for part in parts:
            if self.path(part["sha256"]) is not None:
                continue
            body = fetch(part["sha256"], part["size"])
            self._store(part, body)
            fetched += 1
        return {"parts": len(parts), "fetched": fetched}

    def _store(self, part: dict[str, Any], body: bytes) -> None:
        if (len(body) != part["size"] or hashlib.sha256(body).hexdigest() != part["sha256"]
                or not body.startswith(b"%PDF-")):
            raise CentralSyncError("Köruppdragets sidor från Cloud stämmer inte med paketet")
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(body)
                Path(temporary).replace(self.folder / f"{part['sha256']}.pdf")
            finally:
                Path(temporary).unlink(missing_ok=True)
