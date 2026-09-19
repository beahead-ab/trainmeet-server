"""One persisted meet selection shared by the otherwise isolated EU/US engines.

Downloads do not select a meet. Callers hold ``lock`` while checking operational
blockers and changing the engine. A durable transition marker is written BEFORE
touching the separate stores and completed AFTER them: an interrupted transition
fails closed on restart rather than accepting traffic against mixed versions.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class MeetLifecycleError(ValueError):
    status = 409


def us_meet_id(package: dict) -> str:
    """Never infer shared identity from a display name or railroad territory."""
    for value in ((package.get("session") or {}).get("id"),
                  (package.get("meet") or {}).get("id"), package.get("meet_id")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "legacy-us:" + str(package.get("publication_id") or "unknown")


class SQLiteMeetLifecycle:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None,
                                  check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS server_meet_selection (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                selection_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS server_meet_transition (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                ticket TEXT NOT NULL, target_json TEXT NOT NULL,
                started_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS server_meet_history (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                selection_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
        """)

    def close(self):
        with self.lock:
            self.db.close()

    def selected(self) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT selection_json FROM server_meet_selection WHERE singleton=1").fetchone()
            return json.loads(row[0]) if row else None

    def transition(self) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT ticket,target_json,started_at FROM server_meet_transition WHERE singleton=1").fetchone()
            return {"ticket": row[0], "target": json.loads(row[1]), "started_at": row[2]} if row else None

    def assert_selected(self, region: str, meet_id: str | None = None,
                        publication_id: str | None = None) -> dict:
        with self.lock:
            if self.transition():
                raise MeetLifecycleError("Configbyte pågår eller avbröts. Trafik är spärrad tills bytet slutförts.")
            selected = self.selected()
            if not selected or selected["region"] != region:
                raise MeetLifecycleError("Den här arbetsytan tillhör inte serverns valda träff.")
            if meet_id is not None and selected["meet_id"] != meet_id:
                raise MeetLifecycleError("Kommandot tillhör en annan träff.")
            if publication_id is not None and selected["publication_id"] != publication_id:
                raise MeetLifecycleError("Config har uppdaterats. Hämta aktuellt trafikläge och försök igen.")
            return selected

    def begin_transition(self, region: str, meet_id: str, publication_id: str, *,
                         meet_name: str = "", expected_generation: int | None = None,
                         allow_switch: bool = False) -> str:
        if region not in {"eu", "us"} or not str(meet_id).strip() or not str(publication_id).strip():
            raise MeetLifecycleError("Träff, trafiktyp och publicerad config måste anges.")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.transition():
                    raise MeetLifecycleError("Ett annat configbyte pågår eller behöver återställas.")
                current = self.selected()
                generation = current["generation"] if current else 0
                if expected_generation is not None and expected_generation != generation:
                    raise MeetLifecycleError("Den valda träffen ändrades. Läs in sidan igen.")
                if current and (current["region"], current["meet_id"]) != (region, meet_id) and not allow_switch:
                    raise MeetLifecycleError("Servern representerar redan en träff. Använd det bekräftade träffbytet.")
                target = {"region": region, "meet_id": meet_id, "publication_id": publication_id,
                          "meet_name": meet_name or (current or {}).get("meet_name", ""),
                          "generation": generation + 1}
                ticket = str(uuid4())
                self.db.execute("INSERT INTO server_meet_transition VALUES(1,?,?,?)",
                                (ticket, json.dumps(target), datetime.now(timezone.utc).isoformat()))
                self.db.execute("COMMIT")
                return ticket
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def complete_transition(self, ticket: str) -> dict:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                pending = self.transition()
                if not pending or pending["ticket"] != ticket:
                    raise MeetLifecycleError("Configbytets bekräftelse är inte längre giltig.")
                encoded = json.dumps(pending["target"])
                self.db.execute("INSERT INTO server_meet_selection VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET selection_json=excluded.selection_json", (encoded,))
                self.db.execute("INSERT INTO server_meet_history(selection_json,recorded_at) VALUES(?,?)", (encoded, datetime.now(timezone.utc).isoformat()))
                self.db.execute("DELETE FROM server_meet_transition WHERE singleton=1")
                self.db.execute("COMMIT")
                return pending["target"]
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def abort_transition(self, ticket: str) -> None:
        """Only safe BEFORE any runtime write, or after an explicit full rollback."""
        with self.lock:
            changed = self.db.execute("DELETE FROM server_meet_transition WHERE singleton=1 AND ticket=?", (ticket,))
            if not changed.rowcount:
                raise MeetLifecycleError("Configbytets bekräftelse är inte längre giltig.")

    def select(self, region: str, meet_id: str, publication_id: str, **kwargs) -> dict:
        """Set a selection when no separate engine/store mutation is needed."""
        with self.lock:
            ticket = self.begin_transition(region, meet_id, publication_id, **kwargs)
            return self.complete_transition(ticket)

    def bootstrap(self, eu_publication=None, us_session: dict | None = None) -> dict | None:
        """Adopt unambiguous legacy state; preserve both histories on ambiguity.

        A closed US operating session still represents its selected meet. Neither
        a stopped clock nor closed operating session authorizes a silent switch.
        """
        with self.lock:
            if self.transition():
                raise MeetLifecycleError("Ett avbrutet configbyte behöver slutföras innan trafik kan köras.")
            if self.selected():
                return self.selected()
            if eu_publication is not None and us_session is not None:
                raise MeetLifecycleError("Både EU- och US-träff finns i äldre data. Administratören måste välja en; ingen data har raderats.")
            if eu_publication is not None:
                return self.select("eu", eu_publication.meet_id, eu_publication.publication_id,
                                   meet_name=eu_publication.meet_name)
            if us_session is not None:
                package = us_session["package"]
                return self.select("us", us_meet_id(package), package["publication_id"],
                                   meet_name=package.get("name", ""))
            return None
