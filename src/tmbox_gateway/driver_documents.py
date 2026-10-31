"""Köruppdrag som administratören laddar upp i Inställningar → Lokförare (#225).

Ett köruppdrag är en PDF med många förartjänster (en träff kan ha 600 sidor).
Flödet är uttryckligt i tre steg, så att ingenting når förarna av misstag:

1. **Ladda upp.** PDF:en sparas oförändrad i serverns datakatalog, i mappen
   ``lokforaren/`` bredvid ``trainmeet.db``, under sin sha256. Samma fil två
   gånger för samma träff blir ett och samma köruppdrag.
2. **Läs in.** Tjänsterna läses ur PDF:en i administratörens webbläsare och
   skickas hit som färdiga tjänster i samma form som Clouds ``driver_duties``.
   Servern tolkar alltså ingen PDF; den kontrollerar formen med
   :func:`normalize_duty`, precis som för Cloud.
3. **Gör tillgängliga.** Först då ser förarna tjänsterna under Välj tjänst,
   och bara på den träff köruppdraget laddades upp för.

Tjänsterna ligger i databasen och följer med i säkerhetskopiorna. PDF-filerna
gör det inte (de kan vara stora); försvinner en fil finns tjänsterna kvar och
bara förlagan saknas.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from .driver import DriverError, normalize_duty

MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_DUTIES = 3000
MAX_PAGES = 5000
FILENAME_MAX = 120
WARNINGS_MAX = 200
_CHUNK = 1024 * 1024
_SLUG = re.compile(r"[^0-9A-Za-z]+")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _filename(value: Any) -> str:
    name = Path(str(value or "")).name.strip()
    name = "".join(char for char in name if char.isprintable())[:FILENAME_MAX]
    return name or "koruppdrag.pdf"


def _page(value: Any) -> int | None:
    return value if isinstance(value, int) and 1 <= value <= MAX_PAGES else None


class DriverDocuments:
    """Köruppdragen på den här servern: filer, inlästa tjänster och vilka
    som är tillgängliga för förarna."""

    def __init__(self, database: str | Path, folder: str | Path | None = None):
        self.database = Path(database)
        self.folder = Path(folder) if folder else self.database.parent / "lokforaren"
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database, timeout=10, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS driver_documents (
                document_id TEXT PRIMARY KEY,
                meet_key TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                filename TEXT NOT NULL,
                size INTEGER NOT NULL,
                pages INTEGER,
                status TEXT NOT NULL CHECK(status IN ('uploaded', 'indexed')),
                published INTEGER NOT NULL DEFAULT 0,
                warnings TEXT NOT NULL DEFAULT '[]',
                uploaded_by TEXT,
                uploaded_at TEXT NOT NULL,
                indexed_at TEXT,
                published_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS driver_documents_by_file ON driver_documents(meet_key, sha256);

            -- Tjänsterna som lästs ur ett köruppdrag, i den form förarna får dem.
            CREATE TABLE IF NOT EXISTS driver_document_duties (
                document_id TEXT NOT NULL,
                duty_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                number TEXT NOT NULL,
                page_from INTEGER,
                page_to INTEGER,
                duty_json TEXT NOT NULL,
                PRIMARY KEY(document_id, duty_id)
            );
            CREATE INDEX IF NOT EXISTS driver_document_duties_by_number ON driver_document_duties(number);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # ------------------------------------------------------------ filen

    def store(self, stream: BinaryIO, length: int, *, filename: Any, meet_key: str,
              user_id: str | None) -> dict[str, Any]:
        """Tar emot PDF:en i bitar direkt till disk. Inget hålls i minnet."""

        if length < 1:
            raise DriverError(400, "empty_document", "Filen är tom")
        if length > MAX_PDF_BYTES:
            raise DriverError(413, "document_too_large",
                              f"PDF:en är större än {MAX_PDF_BYTES // (1024 * 1024)} MB")
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = self.folder / f".upload-{secrets.token_hex(8)}.partial"
        digest = hashlib.sha256()
        head = b""
        try:
            with partial.open("wb") as target:
                remaining = length
                while remaining:
                    chunk = stream.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise DriverError(400, "incomplete_document", "Uppladdningen avbröts innan hela filen kom fram")
                    if len(head) < 1024:
                        head += chunk[: 1024 - len(head)]
                    digest.update(chunk)
                    target.write(chunk)
                    remaining -= len(chunk)
            # PDF-huvudet får enligt standarden stå en bit in i filen.
            if b"%PDF-" not in head:
                raise DriverError(415, "not_a_pdf", "Filen är ingen PDF")
            sha = digest.hexdigest()
            final = self.folder / f"{sha}.pdf"
            if final.exists():
                partial.unlink()
            else:
                partial.replace(final)
        finally:
            if partial.exists():
                partial.unlink()

        with self._lock:
            row = self._connection.execute(
                "SELECT document_id FROM driver_documents WHERE meet_key = ? AND sha256 = ?", (meet_key, sha)
            ).fetchone()
            if row is not None:
                return self.document(row[0]) | {"duplicate": True}
            document_id = secrets.token_hex(6)
            self._connection.execute(
                "INSERT INTO driver_documents(document_id, meet_key, sha256, filename, size, status, uploaded_by,"
                " uploaded_at) VALUES (?, ?, ?, ?, ?, 'uploaded', ?, ?)",
                (document_id, meet_key, sha, _filename(filename), length, user_id, _now()),
            )
        return self.document(document_id) | {"duplicate": False}

    def pdf_path(self, document_id: str) -> Path | None:
        document = self._row(document_id)
        if document is None:
            return None
        path = self.folder / f"{document['sha256']}.pdf"
        return path if path.is_file() else None

    # ------------------------------------------------------- inläsningen

    def index(self, document_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Sparar tjänsterna som lästs ur PDF:en. Ersätter en tidigare
        inläsning helt. Ett köruppdrag som redan är tillgängligt förblir
        det, med de nya tjänsterna."""

        if self._row(document_id) is None:
            raise DriverError(404, "document_not_found", "Köruppdraget finns inte")
        raw_duties = payload.get("duties")
        if not isinstance(raw_duties, list):
            raise DriverError(400, "duties_required", "Inläsningen saknar tjänster")
        if len(raw_duties) > MAX_DUTIES:
            raise DriverError(400, "too_many_duties", f"Högst {MAX_DUTIES} tjänster per köruppdrag")
        pages = _page(payload.get("pages"))
        warnings = [str(item)[:240] for item in (payload.get("warnings") or []) if str(item).strip()][:WARNINGS_MAX]

        rows: list[tuple[Any, ...]] = []
        used: dict[str, int] = {}
        skipped = 0
        for raw in raw_duties:
            duty = normalize_duty(raw)
            if duty is None:
                skipped += 1
                continue
            page_range = raw.get("pages") if isinstance(raw, dict) else None
            page_from = _page(page_range[0]) if isinstance(page_range, list) and page_range else None
            page_to = _page(page_range[-1]) if isinstance(page_range, list) and page_range else None
            slug = _SLUG.sub("-", duty["number"]).strip("-").lower() or "tjanst"
            used[slug] = used.get(slug, 0) + 1
            duty["id"] = f"k{document_id}-{slug}" + (f"-{used[slug]}" if used[slug] > 1 else "")
            duty["demo"] = False
            duty["source"] = {"document_id": document_id, "pages": [page_from, page_to or page_from] if page_from else []}
            rows.append((document_id, duty["id"], len(rows), duty["number"], page_from, page_to or page_from,
                         json.dumps(duty, ensure_ascii=False)))
        if skipped:
            warnings.insert(0, f"{skipped} tjänster saknade nummer eller läsbara tider och togs inte med")

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute("DELETE FROM driver_document_duties WHERE document_id = ?", (document_id,))
                self._connection.executemany(
                    "INSERT INTO driver_document_duties(document_id, duty_id, position, number, page_from, page_to,"
                    " duty_json) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                self._connection.execute(
                    "UPDATE driver_documents SET status = 'indexed', pages = ?, warnings = ?, indexed_at = ?,"
                    " published = CASE WHEN ? = 0 THEN 0 ELSE published END WHERE document_id = ?",
                    (pages, json.dumps(warnings[:WARNINGS_MAX], ensure_ascii=False), _now(), len(rows), document_id),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return self.document(document_id)

    def publish(self, document_id: str, published: bool) -> dict[str, Any]:
        document = self.document(document_id)
        if published and (document["status"] != "indexed" or not document["duties"]):
            raise DriverError(409, "document_not_indexed", "Läs in tjänsterna innan köruppdraget görs tillgängligt")
        with self._lock:
            self._connection.execute(
                "UPDATE driver_documents SET published = ?, published_at = ? WHERE document_id = ?",
                (1 if published else 0, _now() if published else None, document_id),
            )
        return self.document(document_id)

    def remove(self, document_id: str) -> None:
        document = self._row(document_id)
        if document is None:
            raise DriverError(404, "document_not_found", "Köruppdraget finns inte")
        with self._lock:
            self._connection.execute("DELETE FROM driver_document_duties WHERE document_id = ?", (document_id,))
            self._connection.execute("DELETE FROM driver_documents WHERE document_id = ?", (document_id,))
            still_used = self._connection.execute(
                "SELECT 1 FROM driver_documents WHERE sha256 = ?", (document["sha256"],)).fetchone()
        if not still_used:
            (self.folder / f"{document['sha256']}.pdf").unlink(missing_ok=True)

    # ---------------------------------------------------------- läsning

    def _row(self, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._connection.execute("SELECT * FROM driver_documents WHERE document_id = ?", (str(document_id),))
            row = cursor.fetchone()
            names = [column[0] for column in cursor.description]
        return dict(zip(names, row)) if row else None

    def document(self, document_id: str) -> dict[str, Any]:
        row = self._row(document_id)
        if row is None:
            raise DriverError(404, "document_not_found", "Köruppdraget finns inte")
        with self._lock:
            count, first, last = self._connection.execute(
                "SELECT COUNT(*), MIN(number), MAX(number) FROM driver_document_duties WHERE document_id = ?",
                (row["document_id"],),
            ).fetchone()
        return {
            "id": row["document_id"], "meet_key": row["meet_key"], "filename": row["filename"], "size": row["size"],
            "pages": row["pages"], "status": row["status"], "published": bool(row["published"]),
            "warnings": json.loads(row["warnings"] or "[]"), "duties": count, "sha256": row["sha256"],
            "uploaded_at": row["uploaded_at"], "indexed_at": row["indexed_at"], "published_at": row["published_at"],
            "has_file": (self.folder / f"{row['sha256']}.pdf").is_file(),
        }

    def documents(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = [row[0] for row in self._connection.execute(
                "SELECT document_id FROM driver_documents ORDER BY uploaded_at DESC, document_id").fetchall()]
        return [self.document(document_id) for document_id in ids]

    def duties(self, document_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT duty_json FROM driver_document_duties WHERE document_id = ? ORDER BY position",
                (str(document_id),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def published_duties(self, meet_key: str) -> list[dict[str, Any]]:
        """Tjänsterna förarna får välja bland på den här träffen."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT d.duty_json FROM driver_document_duties d JOIN driver_documents k"
                " ON k.document_id = d.document_id WHERE k.published = 1 AND k.meet_key = ?"
                " ORDER BY k.uploaded_at, d.position", (meet_key,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def published_document(self, document_id: str, meet_key: str) -> bool:
        row = self._row(document_id)
        return bool(row and row["published"] and row["meet_key"] == meet_key)
