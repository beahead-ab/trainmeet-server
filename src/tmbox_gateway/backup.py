"""Taking a copy of the database that actually contains the database.

Every store in this server opens SQLite in WAL mode, which means the bytes
you just wrote are usually *not* in `trainmeet.db`. They are in
`trainmeet.db-wal` beside it, and they stay there until something
checkpoints. On a server that is running - which is exactly when an update
runs - the main file can be 4 KiB of empty header while the whole meet lives
in a half-megabyte write-ahead log.

So `cp trainmeet.db backup.db` copies an empty database. The cruel part is
that the result is not corrupt: it opens cleanly, `PRAGMA integrity_check`
answers `ok`, and it has no tables at all. A backup that fails loudly is a
nuisance; this one fails silently and is only discovered by the person
trying to restore it, on the worst day they have had.

SQLite's online backup API is built for precisely this. It reads through the
WAL, takes a consistent snapshot of a live database, and needs no lock held
across the copy. This module wraps it, then refuses to keep the result
unless the copy holds as much as the source did.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

#: How many backups to keep. The updater takes one per update, and an
#: operator chasing a bad release wants a few steps of history - but the
#: Raspberry Pi's card is small, and nothing has ever deleted these.
DEFAULT_KEEP = 10


class BackupError(RuntimeError):
    """A backup could not be taken, or could not be trusted once taken."""


def _table_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()
    return int(row[0]) if row else 0


def _verify(path: Path, expected_tables: int) -> None:
    """Open the finished copy cold and confirm it is worth keeping."""

    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            answer = integrity[0] if integrity else "inget svar"
            raise BackupError(f"kopian klarade inte integritetskontrollen: {answer}")
        found = _table_count(connection)
    finally:
        connection.close()

    # The check that would have caught the WAL bug. A copy with fewer tables
    # than the source is the empty-backup failure, and it is indistinguishable
    # from a good one by any other measure.
    if found < expected_tables:
        raise BackupError(
            f"kopian innehåller {found} tabeller men källan har {expected_tables} - "
            "backupen är ofullständig och sparas inte"
        )


def prune(backup_dir: Path, keep: int = DEFAULT_KEEP) -> list[Path]:
    """Delete all but the newest `keep` backups. Returns what was removed."""

    if keep < 1:
        raise ValueError("keep måste vara minst 1")
    # The names are UTC timestamps, so they sort chronologically, but mtime is
    # what actually says which file is oldest if a name is ever hand-made.
    backups = sorted(
        backup_dir.glob("trainmeet-*.db"),
        key=lambda item: (item.stat().st_mtime, item.name),
    )
    removed = []
    for stale in backups[: max(0, len(backups) - keep)]:
        stale.unlink()
        removed.append(stale)
    return removed


def create_backup(
    database: Path, backup_dir: Path, stamp: str, keep: int = DEFAULT_KEEP
) -> Path | None:
    """Back up a live database. Returns the file written, or None if there
    was nothing worth backing up.

    `stamp` names the file. The caller passes it so this stays deterministic
    and the shell keeps owning the clock.
    """

    database = Path(database)
    backup_dir = Path(backup_dir)
    if not database.exists():
        return None

    source = sqlite3.connect(database)
    try:
        expected = _table_count(source)
        # A database with no tables is a server that has not started yet.
        # There is nothing to protect, and failing here would block the very
        # update that is about to give it some.
        if expected == 0:
            return None

        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"trainmeet-{stamp}.db"
        # Write under a temporary name so an interrupted backup never leaves
        # a half-copy sitting there looking like a real one.
        partial = target.with_suffix(".db.partial")
        partial.unlink(missing_ok=True)
        try:
            destination = sqlite3.connect(partial)
            try:
                source.backup(destination)
            finally:
                destination.close()
            _verify(partial, expected)
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    finally:
        source.close()

    prune(backup_dir, keep)
    return target


def restore(backup: Path, database: Path) -> None:
    """Put a backup back, safely. The server must not be running.

    Copying the file into place is the obvious half. The half that ruins the
    day is `trainmeet.db-wal`: if the crashed database left one behind,
    SQLite treats it as belonging to whatever file now carries that name and
    replays it over the backup. The restore then silently produces the very
    data it was meant to replace, and `PRAGMA integrity_check` still answers
    `ok`. So the log has to go, and it has to go together with the copy.
    """

    backup = Path(backup)
    database = Path(database)
    if not backup.exists():
        raise BackupError(f"säkerhetskopian finns inte: {backup}")

    connection = sqlite3.connect(backup)
    try:
        if _table_count(connection) == 0:
            raise BackupError(f"säkerhetskopian är tom: {backup}")
    finally:
        connection.close()

    database.parent.mkdir(parents=True, exist_ok=True)
    # Land the file under a temporary name and rename it into place, so an
    # interrupted restore cannot leave a partial database where the real one
    # used to be.
    staged = database.with_suffix(".db.restoring")
    staged.unlink(missing_ok=True)
    try:
        with open(backup, "rb") as source, open(staged, "wb") as target:
            while chunk := source.read(1 << 20):
                target.write(chunk)
        for stale in (
            database.with_name(database.name + "-wal"),
            database.with_name(database.name + "-shm"),
        ):
            stale.unlink(missing_ok=True)
        staged.replace(database)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TrainMeets databaskopior")
    commands = parser.add_subparsers(dest="command", required=True)

    creating = commands.add_parser("create", help="Säkerhetskopiera en databas")
    creating.add_argument("database")
    creating.add_argument("backup_dir")
    creating.add_argument("stamp")
    creating.add_argument("--keep", type=int, default=DEFAULT_KEEP)

    restoring = commands.add_parser("restore", help="Återställ en kopia")
    restoring.add_argument("backup")
    restoring.add_argument("database")

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            written = create_backup(
                Path(args.database), Path(args.backup_dir), args.stamp, args.keep
            )
            if written is not None:
                print(written)
        else:
            restore(Path(args.backup), Path(args.database))
            print(args.database)
    except (BackupError, ValueError, OSError, sqlite3.Error) as error:
        print(f"{args.command} misslyckades: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
