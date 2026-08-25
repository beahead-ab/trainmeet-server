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
import re
import sqlite3
import sys
from datetime import datetime, timezone
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

    try:
        connection = sqlite3.connect(backup)
    except sqlite3.DatabaseError as error:
        raise BackupError(f"säkerhetskopian går inte att öppna: {error}") from error
    try:
        if _table_count(connection) == 0:
            raise BackupError(f"säkerhetskopian är tom: {backup}")
    except sqlite3.DatabaseError as error:
        # En fil som inte är en databas ser ut som en databas ända tills någon
        # frågar den något. Felet ska bli det här modulens eget, så att den som
        # anropar kan skilja "kopian duger inte" från ett programfel.
        raise BackupError(f"säkerhetskopian går inte att läsa: {error}") from error
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


#: Filnamnens form. Återställningen tar emot ett namn från webbläsaren och får
#: aldrig läsa något annat än en säkerhetskopia i mappen: inga sökvägar, inga
#: ".." och inget annat filnamn.
BACKUP_NAME = re.compile(r"^trainmeet-[0-9]{8}-[0-9]{6}\.db$")


def _meet_name(connection: sqlite3.Connection) -> str | None:
    """Vilken träff kopian bär, läst ur kopian själv.

    En lista med datum och storlek säger inte vad man återställer. Namnet gör
    det, och det ska komma ur filen - inte ur ett filnamn som någon kan ha
    döpt om.
    """

    try:
        row = connection.execute(
            "SELECT meet_name FROM runtime_publications"
            " ORDER BY active DESC, installed_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    return str(row[0]) if row and row[0] else None


def describe(path: Path) -> dict[str, object]:
    """Vad en fil i backupmappen innehåller, och om den går att lita på.

    En trasig kopia listas hellre än göms: den som letar efter sin backup ska
    få veta att den finns och att den inte duger, inte undra var den tog vägen.
    """

    path = Path(path)
    stamp = path.stem.removeprefix("trainmeet-")
    described: dict[str, object] = {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "taken_at": None,
        "meet_name": None,
        "usable": False,
        "problem": None,
    }
    try:
        described["taken_at"] = (
            datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).isoformat()
        )
    except ValueError:
        described["taken_at"] = None

    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.DatabaseError as error:
        described["problem"] = "kopian går inte att öppna"
        return described
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            described["problem"] = "kopian klarar inte integritetskontrollen"
            return described
        if _table_count(connection) == 0:
            described["problem"] = "kopian är tom"
            return described
        described["meet_name"] = _meet_name(connection)
        described["usable"] = True
    except sqlite3.DatabaseError as error:
        described["problem"] = "kopian går inte att läsa - filen är skadad eller inte en databas"
    finally:
        connection.close()
    return described


def available(backup_dir: Path) -> list[dict[str, object]]:
    """Kopiorna i mappen, nyast först."""

    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []
    found = [describe(path) for path in backup_dir.glob("trainmeet-*.db")]
    return sorted(found, key=lambda item: str(item["name"]), reverse=True)


def resolve(backup_dir: Path, name: str) -> Path:
    """Filnamn från en webbläsare till en sökväg i backupmappen.

    Namnet valideras mot mönstret och sökvägen kontrolleras mot mappen efteråt:
    det första stoppar `../`, det andra stoppar en länk som pekar ut ur den.
    """

    if not BACKUP_NAME.fullmatch(str(name)):
        raise BackupError("Det där är inget säkerhetskopienamn")
    backup_dir = Path(backup_dir).resolve()
    candidate = (backup_dir / str(name)).resolve()
    if candidate.parent != backup_dir or not candidate.is_file():
        raise BackupError("Säkerhetskopian finns inte")
    return candidate
