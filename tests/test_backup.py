"""What a backup has to survive: being taken while the server is running."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from tmbox_gateway.backup import BackupError, create_backup, prune, restore


def _live_database(path: Path) -> sqlite3.Connection:
    """A database exactly as the server leaves it: WAL, and still open.

    The connection is returned still open on purpose. That is the state the
    updater finds the file in, and it is the state in which the main file is
    empty because nothing has checkpointed the log yet.
    """

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE runtime_settings (key TEXT, value TEXT)")
    connection.execute("CREATE TABLE runtime_publications (id TEXT)")
    connection.execute(
        "INSERT INTO runtime_settings VALUES ('meet_name', 'Sommarträffen')"
    )
    connection.execute("INSERT INTO runtime_publications VALUES ('publication-a')")
    connection.commit()
    return connection


class BackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.database = self.root / "trainmeet.db"
        self.backups = self.root / "backups"
        self.connection = _live_database(self.database)
        self.addCleanup(self.connection.close)
        self.addCleanup(self._dir.cleanup)

    def test_a_plain_file_copy_of_a_live_database_is_empty(self) -> None:
        """The bug this module exists for, pinned so it cannot come back.

        This is what the updater used to do. It must keep being wrong, or the
        test below stops proving anything.
        """

        plain = self.root / "plain.db"
        shutil.copy(self.database, plain)

        copied = sqlite3.connect(plain)
        self.addCleanup(copied.close)
        tables = copied.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        self.assertEqual(tables, 0)
        # And the truly dangerous part: it looks perfectly healthy.
        self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_a_backup_of_a_live_database_holds_the_data(self) -> None:
        written = create_backup(self.database, self.backups, "20260823-140000")

        self.assertEqual(written, self.backups / "trainmeet-20260823-140000.db")
        restored = sqlite3.connect(written)
        self.addCleanup(restored.close)
        self.assertEqual(
            restored.execute(
                "SELECT value FROM runtime_settings WHERE key = 'meet_name'"
            ).fetchone()[0],
            "Sommarträffen",
        )
        self.assertEqual(
            restored.execute("SELECT COUNT(*) FROM runtime_publications").fetchone()[0],
            1,
        )

    def test_a_write_after_the_backup_does_not_reach_it(self) -> None:
        """A backup is a moment, not a live mirror."""

        create_backup(self.database, self.backups, "20260823-140000")
        self.connection.execute("INSERT INTO runtime_publications VALUES ('later')")
        self.connection.commit()

        restored = sqlite3.connect(self.backups / "trainmeet-20260823-140000.db")
        self.addCleanup(restored.close)
        self.assertEqual(
            restored.execute("SELECT COUNT(*) FROM runtime_publications").fetchone()[0],
            1,
        )

    def test_a_database_that_has_never_started_is_skipped(self) -> None:
        """A first install has no tables. Refusing to update it would be worse
        than having no backup of nothing."""

        empty = self.root / "empty.db"
        sqlite3.connect(empty).close()

        self.assertIsNone(create_backup(empty, self.backups, "20260823-140000"))
        self.assertFalse(self.backups.exists())

    def test_a_missing_database_is_skipped(self) -> None:
        self.assertIsNone(
            create_backup(self.root / "nope.db", self.backups, "20260823-140000")
        )

    def test_a_failed_backup_leaves_nothing_behind(self) -> None:
        """Half a backup must not be mistaken for a whole one."""

        with unittest.mock.patch(
            "tmbox_gateway.backup._verify", side_effect=BackupError("nej")
        ):
            with self.assertRaises(BackupError):
                create_backup(self.database, self.backups, "20260823-140000")

        self.assertEqual(sorted(self.backups.iterdir()), [])

    def test_old_backups_are_pruned(self) -> None:
        """Nothing used to delete these, on a machine with a small card."""

        for minute in range(14):
            create_backup(self.database, self.backups, f"20260823-1400{minute:02d}", keep=10)

        kept = sorted(item.name for item in self.backups.glob("*.db"))
        self.assertEqual(len(kept), 10)
        self.assertEqual(kept[0], "trainmeet-20260823-140004.db")
        self.assertEqual(kept[-1], "trainmeet-20260823-140013.db")

    def test_pruning_keeps_the_newest(self) -> None:
        self.backups.mkdir()
        for index in range(5):
            item = self.backups / f"trainmeet-2026082{index}-140000.db"
            item.write_bytes(b"")
            import os

            os.utime(item, (index, index))

        removed = prune(self.backups, keep=2)

        self.assertEqual(
            [item.name for item in removed],
            [
                "trainmeet-20260820-140000.db",
                "trainmeet-20260821-140000.db",
                "trainmeet-20260822-140000.db",
            ],
        )

    def test_keep_must_be_positive(self) -> None:
        """keep=0 would delete every backup there is."""

        with self.assertRaises(ValueError):
            prune(self.backups, keep=0)


class RestoreTest(unittest.TestCase):
    """Putting a backup back is not just copying the file."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.database = self.root / "trainmeet.db"
        self.addCleanup(self._dir.cleanup)

        # A good backup, holding what we want back.
        self.backup = self.root / "good.db"
        good = sqlite3.connect(self.backup)
        good.execute("CREATE TABLE t (v TEXT)")
        good.execute("INSERT INTO t VALUES ('rätt')")
        good.commit()
        good.close()

    def _crashed_database_with_a_leftover_log(self) -> None:
        """The state a server leaves behind when it dies: data in the WAL."""

        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t (v TEXT)")
        connection.execute("INSERT INTO t VALUES ('skadat')")
        connection.commit()
        # Keep copies of the log, then close - closing checkpoints, and a
        # crash does not.
        keep = {}
        for suffix in ("-wal", "-shm"):
            source = self.database.with_name(self.database.name + suffix)
            keep[suffix] = source.read_bytes()
        connection.close()
        for suffix, payload in keep.items():
            self.database.with_name(self.database.name + suffix).write_bytes(payload)

    def _content(self) -> list[str]:
        connection = sqlite3.connect(self.database)
        self.addCleanup(connection.close)
        return [row[0] for row in connection.execute("SELECT v FROM t")]

    def test_a_leftover_log_would_overwrite_the_restored_backup(self) -> None:
        """Why restore removes the log rather than only copying the file.

        Copying the backup into place and leaving `-wal` behind makes SQLite
        replay the crashed database over it. The result is the old data, and
        an integrity check that says `ok`.
        """

        self._crashed_database_with_a_leftover_log()
        self.database.write_bytes(self.backup.read_bytes())  # bara filkopian

        self.assertEqual(self._content(), ["skadat"])

    def test_restore_removes_the_log_and_gives_back_the_backup(self) -> None:
        self._crashed_database_with_a_leftover_log()

        restore(self.backup, self.database)

        self.assertEqual(self._content(), ["rätt"])
        for suffix in ("-wal", "-shm"):
            self.assertFalse(
                self.database.with_name(self.database.name + suffix).exists()
            )

    def test_restore_works_when_there_is_no_database_at_all(self) -> None:
        restore(self.backup, self.database)

        self.assertEqual(self._content(), ["rätt"])

    def test_an_empty_backup_is_refused(self) -> None:
        """The old cp-produced backups look exactly like this."""

        empty = self.root / "empty.db"
        sqlite3.connect(empty).close()
        self._crashed_database_with_a_leftover_log()

        with self.assertRaises(BackupError):
            restore(empty, self.database)
        # And the database it refused to overwrite is untouched.
        self.assertEqual(self._content(), ["skadat"])

    def test_a_missing_backup_is_refused(self) -> None:
        """And is not quietly conjured into existence.

        `sqlite3.connect` creates the file it is pointed at, so a missing
        backup would otherwise be reported as an empty one - after leaving an
        empty database behind in the backup directory.
        """

        missing = self.root / "nope.db"

        with self.assertRaises(BackupError) as caught:
            restore(missing, self.database)

        self.assertIn("finns inte", str(caught.exception))
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
