"""Nya versioner av förartjänster, från foton av pappret (#225).

Pappret förarna har i handen och köruppdraget på servern kan komma från olika
versioner av planen. En administratör eller ägare fotar då pappret, en bild
per sida, och Cloud tolkar fotona (servern tolkar inga bilder). Förslaget
visas bredvid tjänsten som den är nu, och först när administratören sparar
blir det en ny version av tjänsten.

* En **fotografering** (``driver_scans``) är sidorna och förslaget, tills
  administratören sparar eller låter bli. Bilderna ligger i
  ``lokforaren/foton/`` och tas bort när versionen sparats eller efter ett dygn.
* En **version** (``driver_duty_versions``) är tjänsten som den stod på
  pappret och fotona som en liten PDF (``lokforaren/versioner/``), som blir
  förarens förlaga för den versionen.
* Vilken version som **gäller** (``driver_duty_current``) väljer
  administratören. Version 0 är tjänsten som den kom, ur planeringsfilen
  eller Clouds paket; en ny version gäller när den sparats. Allt hör till
  träffen.

En förare som redan kör tjänsten byts aldrig mitt i: Lokföraren frågar om hen
vill byta till den nya versionen.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from .driver import DriverError

MAX_SCAN_PAGES = 12
#: En sida efter att webbläsaren förminskat fotot (ungefär 2000 px, oftast
#: under 1 MB). Tolv sidor ska rymmas i en begäran till Cloud (högst 36 MB).
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_PAGE_PIXELS = 8000
SCAN_LIFETIME = timedelta(hours=24)
#: Cloud svarar inom fyra minuter. En tolkning som stått längre än så har
#: tappats (servern startades om) och får göras om.
READING_STALE = timedelta(minutes=6)
WARNINGS_MAX = 40
_CHUNK = 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def photos_pdf(pages: list[tuple[bytes, int, int]]) -> bytes:
    """En PDF med en JPEG per sida, i fotots proportioner (A4-bredd).

    Bilderna läggs in som de är (DCTDecode); ingenting i dem läses. Bredd och
    höjd kommer från webbläsaren som förminskade fotot.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    kids = []
    pages_id = 3 * len(pages) + 1
    for index, (jpeg, width, height) in enumerate(pages):
        image = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB"
                    b" /BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (width, height, len(jpeg))
                    + jpeg + b"\nendstream")
        page_width = 595.0
        page_height = round(page_width * height / width, 2)
        stream = b"q %.2f 0 0 %.2f 0 0 cm /P%d Do Q" % (page_width, page_height, index)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /XObject << /P%d %d 0 R >> >>"
                        b" /Contents %d 0 R >>" % (pages_id, page_width, page_height, index, image, content)))
    assert len(objects) + 1 == pages_id
    add(b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % kid for kid in kids) + b"] /Count %d >>" % len(kids))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    return bytes(out)


class DriverVersions:
    """Fotograferingar och versioner av förartjänsterna på den här servern."""

    def __init__(self, database: str | Path, folder: str | Path | None = None):
        self.database = Path(database)
        self.folder = Path(folder) if folder else self.database.parent / "lokforaren"
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database, timeout=10, isolation_level=None, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS driver_scans (
                scan_id TEXT PRIMARY KEY,
                meet_key TEXT NOT NULL,
                duty_id TEXT NOT NULL,
                number TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                pages TEXT NOT NULL DEFAULT '[]',
                result TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                read_at TEXT
            );

            -- Tjänsten som den stod på pappret. Version 0 finns aldrig här:
            -- den är tjänsten som den kom, ur planeringsfilen eller paketet.
            CREATE TABLE IF NOT EXISTS driver_duty_versions (
                meet_key TEXT NOT NULL,
                duty_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                number TEXT NOT NULL,
                duty_json TEXT NOT NULL,
                warnings TEXT NOT NULL DEFAULT '[]',
                pdf_sha256 TEXT,
                pages INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_by_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY(meet_key, duty_id, version)
            );

            CREATE TABLE IF NOT EXISTS driver_duty_current (
                meet_key TEXT NOT NULL,
                duty_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                changed_by TEXT,
                changed_at TEXT NOT NULL,
                PRIMARY KEY(meet_key, duty_id)
            );
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @property
    def photos_folder(self) -> Path:
        return self.folder / "foton"

    @property
    def versions_folder(self) -> Path:
        return self.folder / "versioner"

    # ---------------------------------------------------- fotograferingen

    def create_scan(self, meet_key: str, duty: dict[str, Any], user_id: str | None) -> dict[str, Any]:
        self.purge_stale_scans()
        scan_id = secrets.token_hex(8)
        with self._lock:
            self._connection.execute(
                "INSERT INTO driver_scans(scan_id, meet_key, duty_id, number, status, created_by, created_at)"
                " VALUES (?, ?, ?, ?, 'draft', ?, ?)",
                (scan_id, meet_key, str(duty["id"]), str(duty["number"]), user_id, _iso(_now())))
        return self.scan(scan_id, meet_key)

    def _scan_row(self, scan_id: str, meet_key: str | None = None) -> dict[str, Any]:
        with self._lock:
            cursor = self._connection.execute("SELECT * FROM driver_scans WHERE scan_id = ?", (str(scan_id),))
            row = cursor.fetchone()
            names = [column[0] for column in cursor.description]
        if row is None:
            raise DriverError(404, "scan_not_found", "Fotograferingen finns inte längre. Börja om.")
        scan = dict(zip(names, row))
        if meet_key is not None and scan["meet_key"] != meet_key:
            raise DriverError(404, "scan_not_found", "Fotograferingen hör till en annan träff")
        return scan

    def scan(self, scan_id: str, meet_key: str | None = None) -> dict[str, Any]:
        row = self._scan_row(scan_id, meet_key)
        pages = json.loads(row["pages"] or "[]")
        return {
            "id": row["scan_id"], "duty_id": row["duty_id"], "number": row["number"], "status": row["status"],
            "message": row["message"], "created_at": row["created_at"], "read_at": row["read_at"],
            "pages": [{"width": page["width"], "height": page["height"], "size": page["size"]} for page in pages],
            "result": json.loads(row["result"]) if row["result"] else None,
        }

    def add_page(self, scan_id: str, meet_key: str, stream: BinaryIO, length: int, *, width: int, height: int) -> dict[str, Any]:
        """En sida som JPEG, i bitar direkt till disk."""

        row = self._scan_row(scan_id, meet_key)
        if row["status"] != "draft":
            raise DriverError(409, "scan_started", "Sidorna är redan skickade för tolkning. Börja om för att lägga till fler.")
        pages = json.loads(row["pages"] or "[]")
        if len(pages) >= MAX_SCAN_PAGES:
            raise DriverError(400, "too_many_pages", f"Högst {MAX_SCAN_PAGES} sidor per tjänst")
        if not (0 < width <= MAX_PAGE_PIXELS and 0 < height <= MAX_PAGE_PIXELS):
            raise DriverError(400, "invalid_size", "Bildens storlek saknas")
        if length < 4:
            raise DriverError(400, "empty_photo", "Bilden är tom")
        if length > MAX_PAGE_BYTES:
            raise DriverError(413, "photo_too_large", f"Bilden är större än {MAX_PAGE_BYTES // (1024 * 1024)} MB")
        self.photos_folder.mkdir(parents=True, exist_ok=True)
        partial = self.photos_folder / f".upload-{secrets.token_hex(8)}.partial"
        digest = hashlib.sha256()
        try:
            with partial.open("wb") as target:
                remaining, head = length, b""
                while remaining:
                    chunk = stream.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise DriverError(400, "incomplete_photo", "Uppladdningen avbröts")
                    head = head or chunk[:3]
                    digest.update(chunk)
                    target.write(chunk)
                    remaining -= len(chunk)
            if head != b"\xff\xd8\xff":
                raise DriverError(415, "not_a_jpeg", "Bilden är ingen JPEG")
            sha = digest.hexdigest()
            final = self.photos_folder / f"{sha}.jpg"
            if final.exists():
                partial.unlink()
            else:
                partial.replace(final)
        finally:
            if partial.exists():
                partial.unlink()
        pages.append({"sha256": sha, "width": width, "height": height, "size": length})
        with self._lock:
            self._connection.execute("UPDATE driver_scans SET pages = ? WHERE scan_id = ?", (json.dumps(pages), scan_id))
        return self.scan(scan_id)

    def begin_read(self, scan_id: str, meet_key: str) -> tuple[dict[str, Any], list[str]]:
        """Sidorna som base64, för Cloud. Fotograferingen står som 'reading'."""

        row = self._scan_row(scan_id, meet_key)
        if row["status"] == "reading" and row["read_at"] and row["read_at"] > _iso(_now() - READING_STALE):
            raise DriverError(409, "scan_reading", "Fotona tolkas redan")
        pages = json.loads(row["pages"] or "[]")
        if not pages:
            raise DriverError(400, "pages_required", "Fota minst en sida först")
        encoded = []
        for page in pages:
            path = self.photos_folder / f"{page['sha256']}.jpg"
            if not path.is_file():
                raise DriverError(410, "photo_missing", "Ett foto finns inte längre på servern. Börja om.")
            encoded.append(base64.b64encode(path.read_bytes()).decode("ascii"))
        with self._lock:
            # read_at är när tolkningen började tills den är klar.
            self._connection.execute("UPDATE driver_scans SET status = 'reading', message = '', result = NULL,"
                                     " read_at = ? WHERE scan_id = ?", (_iso(_now()), scan_id))
        return self.scan(scan_id), encoded

    def finish_read(self, scan_id: str, duty: dict[str, Any], warnings: list[str]) -> None:
        result = {"duty": duty, "warnings": [str(item)[:240] for item in warnings][:WARNINGS_MAX]}
        with self._lock:
            self._connection.execute(
                "UPDATE driver_scans SET status = 'done', message = '', result = ?, read_at = ? WHERE scan_id = ?",
                (json.dumps(result, ensure_ascii=False), _iso(_now()), scan_id))

    def fail_read(self, scan_id: str, message: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE driver_scans SET status = 'failed', message = ?, read_at = ? WHERE scan_id = ?",
                (str(message)[:300], _iso(_now()), scan_id))

    def _forget_scan(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM driver_scans WHERE scan_id = ?", (row["scan_id"],))
            used = {page["sha256"] for (pages,) in self._connection.execute("SELECT pages FROM driver_scans")
                    for page in json.loads(pages or "[]")}
        for page in json.loads(row["pages"] or "[]"):
            if page["sha256"] not in used:
                (self.photos_folder / f"{page['sha256']}.jpg").unlink(missing_ok=True)

    def discard(self, scan_id: str, meet_key: str) -> None:
        self._forget_scan(self._scan_row(scan_id, meet_key))

    def purge_stale_scans(self, now: datetime | None = None) -> int:
        """Fotograferingar som ingen sparat inom ett dygn, med sina foton."""
        cutoff = _iso((now or _now()) - SCAN_LIFETIME)
        with self._lock:
            cursor = self._connection.execute("SELECT * FROM driver_scans WHERE created_at < ?", (cutoff,))
            names = [column[0] for column in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        for row in rows:
            self._forget_scan(row)
        return len(rows)

    # ---------------------------------------------------------- versioner

    def save(self, scan_id: str, meet_key: str, *, user_id: str | None, user_name: str) -> dict[str, Any]:
        """Förslaget som en ny version, som gäller från och med nu. Fotona
        blir versionens förlaga (en PDF) och fotograferingen tas bort."""

        row = self._scan_row(scan_id, meet_key)
        if row["status"] != "done" or not row["result"]:
            raise DriverError(409, "scan_not_read", "Fotona är inte tolkade än")
        result = json.loads(row["result"])
        pages = json.loads(row["pages"] or "[]")
        photos = []
        for page in pages:
            path = self.photos_folder / f"{page['sha256']}.jpg"
            if path.is_file():
                photos.append((path.read_bytes(), int(page["width"]), int(page["height"])))
        pdf_sha = None
        if photos and len(photos) == len(pages):
            pdf = photos_pdf(photos)
            pdf_sha = hashlib.sha256(pdf).hexdigest()
            self.versions_folder.mkdir(parents=True, exist_ok=True)
            target = self.versions_folder / f"{pdf_sha}.pdf"
            if not target.exists():
                partial = self.versions_folder / f".{pdf_sha}.partial"
                partial.write_bytes(pdf)
                partial.replace(target)
        duty = copy.deepcopy(result["duty"])
        now = _iso(_now())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                (latest,) = self._connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM driver_duty_versions WHERE meet_key = ? AND duty_id = ?",
                    (meet_key, row["duty_id"])).fetchone()
                version = latest + 1
                self._connection.execute(
                    "INSERT INTO driver_duty_versions(meet_key, duty_id, version, number, duty_json, warnings, pdf_sha256,"
                    " pages, created_by, created_by_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (meet_key, row["duty_id"], version, str(duty.get("number") or row["number"]),
                     json.dumps(duty, ensure_ascii=False), json.dumps(result.get("warnings") or [], ensure_ascii=False),
                     pdf_sha, len(pages), user_id, user_name[:120], now))
                self._set_current(meet_key, row["duty_id"], version, user_id, now)
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
        self._forget_scan(row)
        return next(item for item in self.versions(meet_key, row["duty_id"])["versions"] if item["version"] == version)

    def _set_current(self, meet_key: str, duty_id: str, version: int, user_id: str | None, now: str) -> None:
        self._connection.execute(
            "INSERT INTO driver_duty_current(meet_key, duty_id, version, changed_by, changed_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(meet_key, duty_id) DO UPDATE SET version = excluded.version,"
            " changed_by = excluded.changed_by, changed_at = excluded.changed_at",
            (meet_key, duty_id, version, user_id, now))

    def use(self, meet_key: str, duty_id: str, version: Any, user_id: str | None) -> dict[str, Any]:
        """Låt en annan version gälla, även 0: tjänsten som den kom."""
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            raise DriverError(400, "version_required", "Ange vilken version som ska gälla")
        with self._lock:
            if version and not self._connection.execute(
                    "SELECT 1 FROM driver_duty_versions WHERE meet_key = ? AND duty_id = ? AND version = ?",
                    (meet_key, duty_id, version)).fetchone():
                raise DriverError(404, "version_not_found", "Versionen finns inte")
            self._set_current(meet_key, duty_id, version, user_id, _iso(_now()))
        return self.versions(meet_key, duty_id)

    def versions(self, meet_key: str, duty_id: str) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT version, number, warnings, pdf_sha256, pages, created_by_name, created_at FROM driver_duty_versions"
                " WHERE meet_key = ? AND duty_id = ? ORDER BY version", (meet_key, duty_id)).fetchall()
            current = self._connection.execute(
                "SELECT version, changed_at FROM driver_duty_current WHERE meet_key = ? AND duty_id = ?",
                (meet_key, duty_id)).fetchone()
        return {
            "current": current[0] if current else 0,
            "versions": [{"version": version, "number": number, "warnings": json.loads(warnings or "[]"),
                          "has_pdf": bool(sha and (self.versions_folder / f"{sha}.pdf").is_file()), "pages": pages,
                          "source": "photo", "created_by_name": name, "created_at": created}
                         for version, number, warnings, sha, pages, name, created in rows],
        }

    def _current_rows(self, meet_key: str) -> dict[str, tuple[int, str | None, str | None, int, str, str]]:
        """Den version som gäller per tjänst på träffen, när det inte är 0."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT c.duty_id, c.version, v.duty_json, v.pdf_sha256, v.pages, v.created_at, v.created_by_name"
                " FROM driver_duty_current c JOIN driver_duty_versions v ON v.meet_key = c.meet_key"
                " AND v.duty_id = c.duty_id AND v.version = c.version WHERE c.meet_key = ? AND c.version > 0",
                (meet_key,)).fetchall()
            touched = {duty_id for (duty_id,) in self._connection.execute(
                "SELECT duty_id FROM driver_duty_current WHERE meet_key = ?", (meet_key,))}
        current = {row[0]: row[1:] for row in rows}
        return {duty_id: current.get(duty_id, (0, None, None, 0, "", "")) for duty_id in touched}

    def apply_all(self, meet_key: str, duties: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Tjänsterna med den version som gäller. En tjänst som fått en
        version bär ``version``; de andra är oförändrade."""
        current = self._current_rows(meet_key)
        if not current:
            return duties
        result = []
        for duty in duties:
            found = current.get(str(duty.get("id")))
            if found is None or duty.get("demo"):
                result.append(duty)
                continue
            version, duty_json, pdf_sha, pages, created_at, name = found
            if not version or not duty_json:
                result.append({**duty, "version": 0})
                continue
            replaced = json.loads(duty_json)
            replaced.update(id=duty["id"], demo=False, version=version,
                            version_info={"source": "photo", "created_at": created_at, "created_by_name": name})
            has_pdf = bool(pdf_sha and (self.versions_folder / f"{pdf_sha}.pdf").is_file())
            replaced["source"] = {"photos": True, "pages": [1, pages] if has_pdf and pages else [], "part": has_pdf}
            result.append(replaced)
        return result

    def current_version(self, meet_key: str, duty_id: str) -> tuple[int, str | None]:
        """Versionen som gäller och när den bestämdes."""
        with self._lock:
            row = self._connection.execute(
                "SELECT version, changed_at FROM driver_duty_current WHERE meet_key = ? AND duty_id = ?",
                (meet_key, duty_id)).fetchone()
        return (row[0], row[1]) if row else (0, None)

    def pdf_path(self, meet_key: str, duty_id: str, version: int | None = None) -> Path | None:
        """Fotona som PDF för versionen (den som gäller om inget anges)."""
        if version is None:
            version, _ = self.current_version(meet_key, duty_id)
        if not version:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT pdf_sha256 FROM driver_duty_versions WHERE meet_key = ? AND duty_id = ? AND version = ?",
                (meet_key, duty_id, version)).fetchone()
        if not row or not row[0]:
            return None
        path = self.versions_folder / f"{row[0]}.pdf"
        return path if path.is_file() else None
