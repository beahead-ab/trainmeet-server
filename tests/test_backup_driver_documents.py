"""#225: köruppdragens PDF:er följer med säkerhetskopiorna och kontrolleras vid återställning.

Databasen följde redan med, men inte filerna. Byttes förlagan ut efter kopian
var den gamla filen borta, och en återställd träff lovade förarna sidor som
inte fanns.
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tmbox_gateway import backup
from tmbox_gateway.driver_documents import DriverDocuments
from tmbox_gateway.driver import DriverError

PDF = b"%PDF-1.7\n" + b"0" * 4096 + b"\n%%EOF\n"
OTHER_PDF = b"%PDF-1.7\n" + b"1" * 4096 + b"\n%%EOF\n"
PART_211 = b"%PDF-1.7\n" + b"2" * 900 + b"\n%%EOF\n"
PART_212 = b"%PDF-1.7\n" + b"3" * 900 + b"\n%%EOF\n"


def duty(number: str) -> dict:
    return {"number": number, "trains": [{"number": f"G {number}0", "from": "A", "to": "B"}],
            "events": [{"kind": "departure", "time": "18:00", "station": "A", "train": f"G {number}0"},
                       {"kind": "arrival", "time": "18:40", "station": "B", "train": f"G {number}0"}]}


class DriverPdfBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.database = self.root / "trainmeet.db"
        self.backups = self.root / "backups"
        self.documents = DriverDocuments(self.database)
        self.addCleanup(lambda: self.documents.close())

    # ── hjälp ──────────────────────────────────────────────────────────

    def add(self, pdf: bytes = PDF, parts: bool = True) -> str:
        document = self.documents.create({"fingerprint": "a1b2c3d4e5f60718", "filename": "Grimslöv.json",
                                          "name": "Grimslöv 2027", "duties": [duty("211"), duty("212")]},
                                         meet_key="meet-a", user_id=None)
        doc = document["id"]
        self.documents.attach_pdf(doc, io.BytesIO(pdf), len(pdf), filename="Grimslöv.pdf")
        self.documents.set_pages(doc, {"total": 9, "pages": {"211": [1, 4], "212": [5, 8]}})
        if parts:
            self.documents.store_part(doc, "211", io.BytesIO(PART_211), len(PART_211))
            self.documents.store_part(doc, "212", io.BytesIO(PART_212), len(PART_212))
        self.documents.publish(doc, True)
        return doc

    def take(self, stamp: str, keep: int = backup.DEFAULT_KEEP) -> Path:
        written = backup.create_backup(self.database, self.backups, stamp, keep)
        assert written is not None
        return written

    def put_back(self, copy: Path) -> list[str]:
        """Återställ som servern gör: med alla stores stängda."""
        self.documents.close()
        notes = backup.restore(copy, self.database)
        self.documents = DriverDocuments(self.database)
        return notes

    def store_files(self) -> set[str]:
        store = self.backups / backup.DRIVER_FOLDER
        return {path.relative_to(store).as_posix() for path in store.rglob("*.pdf")} if store.is_dir() else set()

    # ── kopian ─────────────────────────────────────────────────────────

    def test_a_backup_keeps_the_original_and_every_part_once(self) -> None:
        doc = self.add()
        self.take("20261101-080000")
        self.take("20261101-080100")
        sha = self.documents._row(doc)["pdf_sha256"]
        self.assertEqual(3, len(self.store_files()))
        self.assertIn(f"{sha}.pdf", self.store_files())
        kept = self.backups / backup.DRIVER_FOLDER / f"{sha}.pdf"
        self.assertEqual(PDF, kept.read_bytes())

    def test_a_database_without_duty_sheets_backs_up_as_before(self) -> None:
        self.take("20261101-080000")
        self.assertFalse((self.backups / backup.DRIVER_FOLDER).exists())

    def test_the_files_go_with_the_last_copy_that_points_at_them(self) -> None:
        doc = self.add()
        self.take("20261101-080000", keep=2)
        old = self.documents._row(doc)["pdf_sha256"]
        self.documents.attach_pdf(doc, io.BytesIO(OTHER_PDF), len(OTHER_PDF), filename="ny.pdf")
        self.take("20261101-080100", keep=2)
        self.assertIn(f"{old}.pdf", self.store_files())
        self.take("20261101-080200", keep=2)
        self.take("20261101-080300", keep=2)
        self.assertNotIn(f"{old}.pdf", self.store_files())
        self.assertEqual(1, len(self.store_files()), self.store_files())

    # ── återställningen ────────────────────────────────────────────────

    def test_a_replaced_original_comes_back_with_the_copy(self) -> None:
        doc = self.add()
        copy = self.take("20261101-080000")
        old = self.documents._row(doc)["pdf_sha256"]
        # Förlagan byts ut efter kopian: den gamla filen tas bort på servern.
        self.documents.attach_pdf(doc, io.BytesIO(OTHER_PDF), len(OTHER_PDF), filename="ny.pdf")
        self.assertFalse((self.database.parent / "lokforaren" / f"{old}.pdf").exists())
        self.assertEqual([], self.put_back(copy))
        restored = self.documents.document(doc)
        self.assertTrue(restored["published"])
        self.assertFalse(restored["source_missing"])
        self.assertTrue(restored["pdf"]["has_file"])
        self.assertEqual(PDF, self.documents.pdf_path(doc).read_bytes())
        self.assertEqual(PART_211, self.documents.part_path(f"k{doc}-211", "meet-a").read_bytes())

    def test_a_missing_original_makes_the_duty_sheets_unavailable_and_says_so(self) -> None:
        doc = self.add()
        sha = self.documents._row(doc)["pdf_sha256"]
        # Filen var borta redan när kopian togs, och finns inte i lagringen.
        (self.database.parent / "lokforaren" / f"{sha}.pdf").unlink()
        copy = self.take("20261101-080000")
        notes = self.put_back(copy)
        self.assertEqual(1, len(notes))
        self.assertIn("Förlagan till köruppdraget Grimslöv 2027 saknas", notes[0])
        restored = self.documents.document(doc)
        self.assertFalse(restored["published"])
        self.assertTrue(restored["source_missing"])
        self.assertEqual([], self.documents.published_duties("meet-a"))
        with self.assertRaises(DriverError) as refused:
            self.documents.publish(doc, True)
        self.assertEqual("source_missing", refused.exception.code)

    def test_a_tampered_file_is_never_handed_out(self) -> None:
        doc = self.add()
        copy = self.take("20261101-080000")
        sha = self.documents._row(doc)["pdf_sha256"]
        part = self.documents._row(doc) and next(
            row[0] for row in self.documents._connection.execute(
                "SELECT part_sha256 FROM driver_plan_duties WHERE number = '212'"))
        # Både serverns och lagringens del är fel (samma fil via hård länk).
        for path in (self.database.parent / "lokforaren" / "delar" / f"{part}.pdf",
                     self.backups / backup.DRIVER_FOLDER / "delar" / f"{part}.pdf"):
            path.unlink()
            path.write_bytes(b"%PDF-1.7\nsomething else\n")
        notes = self.put_back(copy)
        self.assertEqual(1, len(notes))
        self.assertFalse((self.database.parent / "lokforaren" / "delar" / f"{part}.pdf").exists())
        restored = self.documents.document(doc)
        self.assertEqual(1, restored["pdf"]["missing_parts"])
        self.assertTrue(restored["source_missing"])
        self.assertFalse(restored["published"])
        # Förlagan själv var hel och står kvar.
        self.assertEqual(PDF, (self.database.parent / "lokforaren" / f"{sha}.pdf").read_bytes())

    def test_a_tampered_live_file_is_replaced_from_the_kept_copy(self) -> None:
        doc = self.add()
        copy = self.take("20261101-080000")
        sha = self.documents._row(doc)["pdf_sha256"]
        live = self.database.parent / "lokforaren" / f"{sha}.pdf"
        live.unlink()  # bryt den hårda länken innan filen skrivs över
        live.write_bytes(b"%PDF-1.7\nfel\n")
        self.assertEqual([], self.put_back(copy))
        self.assertEqual(PDF, live.read_bytes())
        self.assertTrue(self.documents.document(doc)["published"])

    def test_reading_the_pages_again_makes_it_publishable_again(self) -> None:
        doc = self.add()
        part = next(row[0] for row in self.documents._connection.execute(
            "SELECT part_sha256 FROM driver_plan_duties WHERE number = '211'"))
        (self.database.parent / "lokforaren" / "delar" / f"{part}.pdf").unlink()
        copy = self.take("20261101-080000")
        self.put_back(copy)
        self.assertTrue(self.documents.document(doc)["source_missing"])
        # Administratören läser sidorna igen: delarna görs om och kontrolleras.
        self.documents.set_pages(doc, {"total": 9, "pages": {"211": [1, 4], "212": [5, 8]}})
        self.documents.store_part(doc, "211", io.BytesIO(PART_211), len(PART_211))
        self.documents.store_part(doc, "212", io.BytesIO(PART_212), len(PART_212))
        self.assertFalse(self.documents.document(doc)["source_missing"])
        self.assertTrue(self.documents.publish(doc, True)["published"])

    def test_a_restore_without_duty_sheets_has_nothing_to_say(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE IF NOT EXISTS runtime_settings (key TEXT, value TEXT)")
        connection.commit()
        connection.close()
        copy = self.take("20261101-080000")
        self.assertEqual([], self.put_back(copy))

    def test_the_restore_record_carries_the_notes(self) -> None:
        doc = self.add()
        sha = self.documents._row(doc)["pdf_sha256"]
        (self.database.parent / "lokforaren" / f"{sha}.pdf").unlink()
        copy = self.take("20261101-080000")
        notes = self.put_back(copy)
        backup.record_restore(self.root, copy, None, notes=notes)
        record = backup.last_restore(self.root)
        self.assertTrue(record["restored"])
        self.assertEqual(notes, record["notes"])


if __name__ == "__main__":
    unittest.main()
