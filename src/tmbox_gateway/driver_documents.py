"""Köruppdrag som administratören lägger in i Inställningar → Lokförare (#225).

Ett köruppdrag är träffens alla förartjänster, och det kommer från två filer
med var sin uppgift:

* **Planeringsfilen** (JSON ur planeringsprogrammet, samma fil som går till
  Cloud) ger tjänsterna: tåg, stationer, spår och tider. Den läses i
  administratörens webbläsare och hit kommer färdiga tjänster i samma form som
  Clouds ``driver_duties``. Servern tolkar alltså ingen plan; den kontrollerar
  formen med :func:`normalize_duty`, precis som för Cloud.
* **PDF:en** (utskriften av samma tjänster, gärna 600 sidor) är förlagan.
  Den sparas oförändrad i serverns datakatalog, i mappen ``lokforaren/``
  bredvid ``trainmeet.db``, under sin sha256. Webbläsaren hittar vilka sidor
  som hör till vilken tjänst och skickar bara sidnumren hit.

Förarna ser tjänsterna först när administratören uttryckligen gjort
köruppdraget tillgängligt, och bara på den träff det lades in för.

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
_FINGERPRINT = re.compile(r"^[0-9a-f]{8,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _filename(value: Any, fallback: str) -> str:
    name = Path(str(value or "")).name.strip()
    name = "".join(char for char in name if char.isprintable())[:FILENAME_MAX]
    return name or fallback


def _page(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_PAGES else None


def _warnings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item)[:240] for item in values if str(item).strip()][:WARNINGS_MAX]


class DriverDocuments:
    """Köruppdragen på den här servern: tjänsterna ur planeringsfilen,
    förlagan som PDF och om förarna får se dem."""

    def __init__(self, database: str | Path, folder: str | Path | None = None):
        self.database = Path(database)
        self.folder = Path(folder) if folder else self.database.parent / "lokforaren"
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database, timeout=10, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS driver_plans (
                document_id TEXT PRIMARY KEY,
                meet_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                filename TEXT NOT NULL,
                plan_name TEXT NOT NULL DEFAULT '',
                warnings TEXT NOT NULL DEFAULT '[]',
                pdf_sha256 TEXT,
                pdf_filename TEXT,
                pdf_size INTEGER,
                pdf_pages INTEGER,
                pdf_warnings TEXT NOT NULL DEFAULT '[]',
                pdf_uploaded_at TEXT,
                pages_read_at TEXT,
                published INTEGER NOT NULL DEFAULT 0,
                uploaded_by TEXT,
                uploaded_at TEXT NOT NULL,
                published_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS driver_plans_by_file ON driver_plans(meet_key, fingerprint);

            -- Tjänsterna i ett köruppdrag, i den form förarna får dem, och
            -- var i PDF:en de står.
            CREATE TABLE IF NOT EXISTS driver_plan_duties (
                document_id TEXT NOT NULL,
                duty_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                number TEXT NOT NULL,
                page_from INTEGER,
                page_to INTEGER,
                duty_json TEXT NOT NULL,
                PRIMARY KEY(document_id, duty_id)
            );
            CREATE INDEX IF NOT EXISTS driver_plan_duties_by_number ON driver_plan_duties(number);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # ---------------------------------------------------- planeringsfilen

    def create(self, payload: dict[str, Any], *, meet_key: str, user_id: str | None) -> dict[str, Any]:
        """Ett köruppdrag ur planeringsfilens tjänster. Samma fil igen för
        samma träff ersätter tjänsterna i det befintliga köruppdraget och
        behåller PDF:en och sidorna för de tjänster som finns kvar."""

        fingerprint = str(payload.get("fingerprint") or "").lower()
        if not _FINGERPRINT.match(fingerprint):
            raise DriverError(400, "fingerprint_required", "Planeringsfilen saknar kontrollsumma")
        raw_duties = payload.get("duties")
        if not isinstance(raw_duties, list) or not raw_duties:
            raise DriverError(400, "duties_required", "Planeringsfilen har inga köruppdrag")
        if len(raw_duties) > MAX_DUTIES:
            raise DriverError(400, "too_many_duties", f"Högst {MAX_DUTIES} tjänster per köruppdrag")
        filename = _filename(payload.get("filename"), "planeringsfil.json")
        plan_name = " ".join(str(payload.get("name") or "").split())[:120]
        warnings = _warnings(payload.get("warnings"))

        with self._lock:
            row = self._connection.execute(
                "SELECT document_id FROM driver_plans WHERE meet_key = ? AND fingerprint = ?", (meet_key, fingerprint)
            ).fetchone()
            duplicate = row is not None
            document_id = row[0] if row else secrets.token_hex(6)
            pages = {number: (first, last) for number, first, last in self._connection.execute(
                "SELECT number, page_from, page_to FROM driver_plan_duties WHERE document_id = ?", (document_id,))}
            rows, skipped = self._duty_rows(document_id, raw_duties, pages)
            if not rows:
                raise DriverError(400, "duties_required", "Inga köruppdrag i planeringsfilen gick att läsa")
            if skipped:
                warnings.insert(0, f"{skipped} tjänster saknade nummer eller läsbara tider och togs inte med")
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if duplicate:
                    self._connection.execute(
                        "UPDATE driver_plans SET filename = ?, plan_name = ?, warnings = ?, uploaded_by = ?,"
                        " uploaded_at = ? WHERE document_id = ?",
                        (filename, plan_name, json.dumps(warnings, ensure_ascii=False), user_id, _now(), document_id))
                else:
                    self._connection.execute(
                        "INSERT INTO driver_plans(document_id, meet_key, fingerprint, filename, plan_name, warnings,"
                        " uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (document_id, meet_key, fingerprint, filename, plan_name,
                         json.dumps(warnings, ensure_ascii=False), user_id, _now()))
                self._connection.execute("DELETE FROM driver_plan_duties WHERE document_id = ?", (document_id,))
                self._connection.executemany(
                    "INSERT INTO driver_plan_duties(document_id, duty_id, position, number, page_from, page_to,"
                    " duty_json) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return self.document(document_id) | {"duplicate": duplicate}

    @staticmethod
    def _duty_rows(document_id: str, raw_duties: list[Any],
                   pages: dict[str, tuple[int | None, int | None]]) -> tuple[list[tuple[Any, ...]], int]:
        rows: list[tuple[Any, ...]] = []
        used: dict[str, int] = {}
        skipped = 0
        for raw in raw_duties:
            duty = normalize_duty(raw)
            if duty is None:
                skipped += 1
                continue
            slug = _SLUG.sub("-", duty["number"]).strip("-").lower() or "tjanst"
            used[slug] = used.get(slug, 0) + 1
            duty["id"] = f"k{document_id}-{slug}" + (f"-{used[slug]}" if used[slug] > 1 else "")
            duty["demo"] = False
            page_from, page_to = pages.get(duty["number"], (None, None))
            rows.append((document_id, duty["id"], len(rows), duty["number"], page_from, page_to,
                         json.dumps(duty, ensure_ascii=False)))
        return rows, skipped

    # ---------------------------------------------------------- förlagan

    def attach_pdf(self, document_id: str, stream: BinaryIO, length: int, *, filename: Any) -> dict[str, Any]:
        """Tar emot PDF:en i bitar direkt till disk. Inget hålls i minnet.
        En ny PDF ersätter den förra, och sidorna läses om."""

        document = self._row(document_id)
        if document is None:
            raise DriverError(404, "document_not_found", "Köruppdraget finns inte")
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

        same = document["pdf_sha256"] == sha
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "UPDATE driver_plans SET pdf_sha256 = ?, pdf_filename = ?, pdf_size = ?, pdf_uploaded_at = ?"
                    + ("" if same else ", pdf_pages = NULL, pdf_warnings = '[]', pages_read_at = NULL")
                    + " WHERE document_id = ?",
                    (sha, _filename(filename, "koruppdrag.pdf"), length, _now(), document_id))
                if not same:
                    self._connection.execute(
                        "UPDATE driver_plan_duties SET page_from = NULL, page_to = NULL WHERE document_id = ?",
                        (document_id,))
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        if document["pdf_sha256"] and not same:
            self._forget_file(document["pdf_sha256"])
        return self.document(document_id) | {"same_pdf": same}

    def set_pages(self, document_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Var i PDF:en varje tjänst står, som webbläsaren läst ut:
        ``{"pages": {"39": [12, 15], ...}, "total": 600, "warnings": [...]}``."""

        document = self._row(document_id)
        if document is None:
            raise DriverError(404, "document_not_found", "Köruppdraget finns inte")
        if not document["pdf_sha256"]:
            raise DriverError(409, "pdf_required", "Ladda upp PDF:en först")
        ranges = payload.get("pages")
        if not isinstance(ranges, dict):
            raise DriverError(400, "pages_required", "Sidorna saknas")
        total = _page(payload.get("total"))
        updates: list[tuple[int | None, int | None, str, str]] = []
        with self._lock:
            numbers = {row[0] for row in self._connection.execute(
                "SELECT number FROM driver_plan_duties WHERE document_id = ?", (document_id,))}
        for number in numbers:
            value = ranges.get(number)
            first = _page(value[0]) if isinstance(value, list) and len(value) == 2 else None
            last = _page(value[1]) if isinstance(value, list) and len(value) == 2 else None
            if first is None or last is None or last < first or (total and last > total):
                first = last = None
            updates.append((first, last, document_id, number))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.executemany(
                    "UPDATE driver_plan_duties SET page_from = ?, page_to = ? WHERE document_id = ? AND number = ?",
                    updates)
                self._connection.execute(
                    "UPDATE driver_plans SET pdf_pages = ?, pdf_warnings = ?, pages_read_at = ? WHERE document_id = ?",
                    (total, json.dumps(_warnings(payload.get("warnings")), ensure_ascii=False), _now(), document_id))
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        return self.document(document_id)

    def pdf_path(self, document_id: str) -> Path | None:
        document = self._row(document_id)
        if document is None or not document["pdf_sha256"]:
            return None
        path = self.folder / f"{document['pdf_sha256']}.pdf"
        return path if path.is_file() else None

    def _forget_file(self, sha: str) -> None:
        with self._lock:
            still_used = self._connection.execute(
                "SELECT 1 FROM driver_plans WHERE pdf_sha256 = ?", (sha,)).fetchone()
        if not still_used:
            (self.folder / f"{sha}.pdf").unlink(missing_ok=True)

    # ----------------------------------------------------- tillgängligt

    def publish(self, document_id: str, published: bool) -> dict[str, Any]:
        document = self.document(document_id)
        if published and not document["duties"]:
            raise DriverError(409, "document_empty", "Köruppdraget har inga tjänster")
        with self._lock:
            self._connection.execute(
                "UPDATE driver_plans SET published = ?, published_at = ? WHERE document_id = ?",
                (1 if published else 0, _now() if published else None, document_id),
            )
        return self.document(document_id)

    def remove(self, document_id: str) -> None:
        document = self._row(document_id)
        if document is None:
            raise DriverError(404, "document_not_found", "Köruppdraget finns inte")
        with self._lock:
            self._connection.execute("DELETE FROM driver_plan_duties WHERE document_id = ?", (document_id,))
            self._connection.execute("DELETE FROM driver_plans WHERE document_id = ?", (document_id,))
        if document["pdf_sha256"]:
            self._forget_file(document["pdf_sha256"])

    # ---------------------------------------------------------- läsning

    def _row(self, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            cursor = self._connection.execute("SELECT * FROM driver_plans WHERE document_id = ?", (str(document_id),))
            row = cursor.fetchone()
            names = [column[0] for column in cursor.description]
        return dict(zip(names, row)) if row else None

    def document(self, document_id: str) -> dict[str, Any]:
        row = self._row(document_id)
        if row is None:
            raise DriverError(404, "document_not_found", "Köruppdraget finns inte")
        with self._lock:
            count, paged = self._connection.execute(
                "SELECT COUNT(*), COUNT(page_from) FROM driver_plan_duties WHERE document_id = ?",
                (row["document_id"],),
            ).fetchone()
        pdf = None
        if row["pdf_sha256"]:
            pdf = {
                "filename": row["pdf_filename"], "size": row["pdf_size"], "pages": row["pdf_pages"],
                "uploaded_at": row["pdf_uploaded_at"], "pages_read_at": row["pages_read_at"],
                "warnings": json.loads(row["pdf_warnings"] or "[]"), "duties_with_pages": paged,
                "has_file": (self.folder / f"{row['pdf_sha256']}.pdf").is_file(),
            }
        return {
            "id": row["document_id"], "meet_key": row["meet_key"], "filename": row["filename"],
            "name": row["plan_name"], "published": bool(row["published"]),
            "warnings": json.loads(row["warnings"] or "[]"), "duties": count,
            "uploaded_at": row["uploaded_at"], "published_at": row["published_at"], "pdf": pdf,
        }

    def documents(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = [row[0] for row in self._connection.execute(
                "SELECT document_id FROM driver_plans ORDER BY uploaded_at DESC, document_id").fetchall()]
        return [self.document(document_id) for document_id in ids]

    @staticmethod
    def _with_source(duty_json: str, document_id: str, page_from: int | None, page_to: int | None,
                     has_pdf: bool) -> dict[str, Any]:
        duty = json.loads(duty_json)
        duty["source"] = {"document_id": document_id,
                          "pages": [page_from, page_to or page_from] if has_pdf and page_from else []}
        return duty

    def duties(self, document_id: str) -> list[dict[str, Any]]:
        document = self._row(document_id)
        if document is None:
            return []
        with self._lock:
            rows = self._connection.execute(
                "SELECT duty_json, page_from, page_to FROM driver_plan_duties WHERE document_id = ? ORDER BY position",
                (str(document_id),)).fetchall()
        return [self._with_source(duty_json, document_id, first, last, bool(document["pdf_sha256"]))
                for duty_json, first, last in rows]

    def published_duties(self, meet_key: str) -> list[dict[str, Any]]:
        """Tjänsterna förarna får välja bland på den här träffen."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT d.duty_json, d.document_id, d.page_from, d.page_to, k.pdf_sha256 FROM driver_plan_duties d"
                " JOIN driver_plans k ON k.document_id = d.document_id WHERE k.published = 1 AND k.meet_key = ?"
                " ORDER BY k.uploaded_at, d.position", (meet_key,)).fetchall()
        return [self._with_source(duty_json, document_id, first, last, bool(sha))
                for duty_json, document_id, first, last, sha in rows]

    def published_document(self, document_id: str, meet_key: str) -> bool:
        row = self._row(document_id)
        return bool(row and row["published"] and row["meet_key"] == meet_key)
