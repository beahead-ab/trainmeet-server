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
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

#: How many backups to keep. The updater takes one per update, and an
#: operator chasing a bad release wants a few steps of history - but the
#: Raspberry Pi's card is small, and nothing has ever deleted these.
DEFAULT_KEEP = 10

#: Varför en kopia togs. Skälet står sist i filnamnet
#: (trainmeet-20261008-142233-tidsmaskin.db); uppdaterarens kopior och äldre
#: kopior har inget.
KINDS = ("tidsmaskin", "nollstallning", "startdag", "nytt-dygn", "lokala-andringar", "cloud")

#: Tidsmaskinens kopior räknas för sig (Casper 2026-10-08): många hopp i rad
#: ska aldrig tränga ut kopian från före en uppdatering eller en nollställning.
TIME_MACHINE = "tidsmaskin"
TIME_MACHINE_KEEP = 5

#: Det som hör till servern och inte till träffen. En återställning lägger
#: tillbaka kopians träff, men de egna klockorna står kvar som de är nu: en
#: klocka som tagits bort, till exempel när licensen gått ut, ska inte komma
#: tillbaka med ett gammalt godkännande.
KEPT_ON_RESTORE = ("clock_faces", "clock_face_layers")

_KIND_IN_NAME = re.compile(r"^trainmeet-[0-9]{8}-[0-9]{6}-([a-z-]+)\.db$")


def kind_of(path: Path) -> str | None:
    """Skälet i filnamnet, eller None för en kopia utan skäl."""

    found = _KIND_IN_NAME.match(Path(path).name)
    return found.group(1) if found and found.group(1) in KINDS else None


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


def prune(backup_dir: Path, keep: int = DEFAULT_KEEP, kind: str | None = None) -> list[Path]:
    """Delete all but the newest `keep` backups. Returns what was removed.

    The time machine's copies are counted on their own (`kind` "tidsmaskin");
    every other copy is counted together.
    """

    if keep < 1:
        raise ValueError("keep måste vara minst 1")
    # The names are UTC timestamps, so they sort chronologically, but mtime is
    # what actually says which file is oldest if a name is ever hand-made.
    jumps = kind == TIME_MACHINE
    backups = sorted(
        (item for item in backup_dir.glob("trainmeet-*.db") if (kind_of(item) == TIME_MACHINE) == jumps),
        key=lambda item: (item.stat().st_mtime, item.name),
    )
    removed = []
    for stale in backups[: max(0, len(backups) - keep)]:
        stale.unlink()
        removed.append(stale)
    return removed


def create_backup(
    database: Path, backup_dir: Path, stamp: str, keep: int = DEFAULT_KEEP, kind: str | None = None
) -> Path | None:
    """Back up a live database. Returns the file written, or None if there
    was nothing worth backing up.

    `stamp` names the file. The caller passes it so this stays deterministic
    and the shell keeps owning the clock. `kind` says why the copy is taken
    (one of KINDS) and ends the name.
    """

    if kind is not None and kind not in KINDS:
        raise ValueError(f"okänt skäl för en säkerhetskopia: {kind}")
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
        target = backup_dir / (f"trainmeet-{stamp}-{kind}.db" if kind else f"trainmeet-{stamp}.db")
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

    prune(backup_dir, TIME_MACHINE_KEEP if kind == TIME_MACHINE else keep, kind)
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
        try:
            _keep_server_tables(database, staged)
        except sqlite3.DatabaseError:
            # Går de inte att läsa ur den nuvarande databasen återställs kopian
            # som den är, hellre än inte alls.
            pass
        for stale in (
            database.with_name(database.name + "-wal"),
            database.with_name(database.name + "-shm"),
        ):
            stale.unlink(missing_ok=True)
        staged.replace(database)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _keep_server_tables(current: Path, staged: Path) -> None:
    """Lägg serverns egna tabeller (KEPT_ON_RESTORE) från databasen som gäller
    nu i den återställda kopian, innan den byts in. Saknar den nuvarande
    databasen tabellerna står kopians kvar som de är."""

    if not current.exists():
        return
    target = sqlite3.connect(Path(staged).resolve().as_uri(), uri=True, isolation_level=None)
    try:
        try:
            target.execute("ATTACH DATABASE ? AS present", (Path(current).resolve().as_uri() + "?mode=ro",))
        except sqlite3.DatabaseError:
            # En databas som inte går att läsa har inga klockor att behålla;
            # återställningen ska inte stoppas av den.
            return
        present = [table for table in KEPT_ON_RESTORE if target.execute(
            "SELECT 1 FROM present.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()]
        # Allt eller inget: en halv ändring skulle lämna kopian utan klockor.
        target.execute("BEGIN IMMEDIATE")
        try:
            # Tabellerna som de är nu, även om kopian är från en version där
            # de såg annorlunda ut: kopians tas bort och görs om.
            for table in reversed(present):
                target.execute(f"DROP TABLE IF EXISTS main.{table}")
            for table in present:
                for (sql,) in target.execute(
                    "SELECT sql FROM present.sqlite_master WHERE tbl_name=? AND sql IS NOT NULL"
                    " ORDER BY type = 'index'", (table,)).fetchall():
                    target.execute(sql)
                target.execute(f"INSERT INTO main.{table} SELECT * FROM present.{table}")
            target.execute("COMMIT")
        except BaseException:
            target.execute("ROLLBACK")
            raise
    finally:
        target.close()


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
BACKUP_NAME = re.compile(r"^trainmeet-[0-9]{8}-[0-9]{6}(?:-(?:" + "|".join(KINDS) + r"))?\.db$")


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


def _meet_position(connection: sqlite3.Connection, taken_at: str | None) -> dict[str, object]:
    """Var träffen stod när kopian togs: dag nummer, veckodag och klockan.
    Det är så man hittar kopian från före ett hopp med tidsmaskinen."""

    from .runtime import calendar_weekday

    try:
        row = connection.execute(
            "SELECT meet_id FROM runtime_publications ORDER BY active DESC, installed_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        settings = dict(connection.execute(
            "SELECT key, value FROM runtime_settings WHERE key IN (?, 'active_day')", ("meet_calendar:" + str(row[0]),)
        ).fetchall())
        clock = connection.execute(
            "SELECT base_seconds, base_recorded_at, speed, running FROM runtime_clock WHERE singleton = 1"
        ).fetchone()
    except sqlite3.DatabaseError:
        return {}
    position: dict[str, object] = {}
    try:
        saved = json.loads(settings.get("meet_calendar:" + str(row[0])) or "{}")
    except ValueError:
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    start = str(saved.get("start_day") or settings.get("active_day") or "")
    if start:
        try:
            number = max(1, int(saved.get("day_number") or 1))
        except (TypeError, ValueError):
            number = 1
        position["meet_day"] = {"day_number": number, "weekday": calendar_weekday(start, number)}
    if clock:
        seconds = float(clock[0])
        try:
            if clock[3] and taken_at:
                recorded = datetime.fromisoformat(str(clock[1]).replace("Z", "+00:00"))
                if recorded.tzinfo is None:
                    recorded = recorded.replace(tzinfo=timezone.utc)
                seconds += max(0.0, (datetime.fromisoformat(taken_at) - recorded).total_seconds()) * float(clock[2])
        except (TypeError, ValueError):
            pass
        minutes = int(seconds // 60) % (24 * 60)
        position["clock_time"] = f"{minutes // 60:02d}:{minutes % 60:02d}"
    return position


def _taken_at(path: Path) -> str | None:
    """När kopian togs, läst ur filnamnet (UTC)."""

    stamp = Path(path).stem.removeprefix("trainmeet-")[:15]
    try:
        return datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def describe(path: Path) -> dict[str, object]:
    """Vad en fil i backupmappen innehåller, och om den går att lita på.

    En trasig kopia listas hellre än göms: den som letar efter sin backup ska
    få veta att den finns och att den inte duger, inte undra var den tog vägen.
    """

    path = Path(path)
    described: dict[str, object] = {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "taken_at": _taken_at(path),
        "meet_name": None,
        "kind": kind_of(path),
        "meet_day": None,
        "clock_time": None,
        "usable": False,
        "problem": None,
    }

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
        described.update(_meet_position(connection, described["taken_at"]))
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
    # Efter tiden i namnet och sedan filens tid: två kopior samma sekund (före
    # tidsmaskinen och strax efter före en nollställning) står i rätt ordning.
    paths = sorted(backup_dir.glob("trainmeet-*.db"),
                   key=lambda path: (path.name[:len("trainmeet-00000000-000000")], path.stat().st_mtime_ns), reverse=True)
    return [describe(path) for path in paths]


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


#: Hur den senaste återställningen gick. Det kan inte stå i databasen - det är
#: den som byts ut - så det ligger bredvid den i statuskatalogen.
RESTORE_RECORD = "last-restore.json"


def record_restore(
    state_dir: Path, backup: Path, problem: str | None = None, now: datetime | None = None
) -> None:
    """Skriv ner utfallet innan servern startar om.

    Webbläsaren som bad om återställningen ser bara att servern går ner och
    kommer tillbaka. Utan det här går det inte att skilja en lyckad
    återställning från en misslyckad, där servern startar om med den gamla
    databasen och felet bara står i loggen.
    """

    record = {
        "backup": Path(backup).name,
        "taken_at": _taken_at(Path(backup)),
        "attempted_at": (now or datetime.now(timezone.utc)).isoformat(),
        "restored": problem is None,
        "problem": problem,
    }
    path = Path(state_dir) / RESTORE_RECORD
    staged = path.with_name(path.name + ".partial")
    staged.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    staged.replace(path)


def last_restore(state_dir: Path) -> dict[str, object] | None:
    """Utfallet av den senaste återställningen, eller None om ingen har gjorts.

    En fil som inte går att läsa räknas som ingen: listan över kopior ska
    fungera även när det här inte gör det.
    """

    try:
        record = json.loads((Path(state_dir) / RESTORE_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None
