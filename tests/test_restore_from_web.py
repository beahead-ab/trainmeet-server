"""Återställning från säkerhetskopia, gjord från webbgränssnittet.

Uppdateraren tar en kopia före varje installation. Vägen tillbaka fanns bara i
terminalen, vilket är fel plats den dagen man behöver den: den som märker att
något gått fel sitter framför webbadmin, inte i ett SSH-fönster.

Det farliga med en återställning är inte att skriva filen. Det är att skriva
den vid fel tidpunkt. En SQLite-databas som byts under en levande anslutning
ger samma sorts tyst fel som den ursprungliga backupbuggen: en fil som öppnas
utan protest och innehåller fel saker. Därför gör HTTP-lagret ingenting mer än
att lägga en lapp om vilken kopia som gäller; bytet sker i tillsynsprocessen,
efter att varje store är stängd och innan omstarten.

Provet täcker de tre fallen ärendet ber om: en lyckad återställning, en trasig
kopia, och en avbruten - plus de två sätt en webbläsare kan be om fel fil.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from http import HTTPStatus
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from runtime_fixture import runtime_package
from session_fixture import sample_session
from tmbox_gateway import backup, local_server
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import (
    HTTPServerConfig,
    TrainMeetHTTPApplication,
    TrainMeetHTTPServer,
)
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore


class RestoreOverHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.state = Path(self._dir.name)
        self.database = self.state / "trainmeet.db"
        self.backups = self.state / "backups"

        self.runtime_store = SQLiteRuntimeStore(self.database)
        self.addCleanup(self.runtime_store.close)
        self.runtime_store.install(runtime_package())
        self.identities = IdentityStore(self.database)
        self.addCleanup(self.identities.close)
        self.identities.create_first_owner("casper", "casper@example.se", "ett-langt-losenord")

        self.copy = backup.create_backup(self.database, self.backups, "20260825-101500")
        assert self.copy is not None

        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(
                local_development=True, allow_restart=True, state_dir=str(self.state)
            ),
            runtime_store=self.runtime_store,
        )
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.application)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        token = self.identities.create_admin_session("casper@example.se", "ett-langt-losenord")
        self.cookie = f"trainmeet_admin={token}"

    def _call(self, path: str, body: dict | None = None) -> tuple[int, dict]:
        headers = {"Accept": "application/json", "Cookie": self.cookie}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    # ── listan ─────────────────────────────────────────────────────────

    def test_the_list_says_what_each_copy_holds(self) -> None:
        """Datum och storlek räcker inte för att välja. Träffnamnet gör det, och
        det läses ur kopian - inte ur filnamnet."""

        status, payload = self._call("/v1/server/backups")
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["backups"]))
        listed = payload["backups"][0]
        self.assertEqual("trainmeet-20260825-101500.db", listed["name"])
        self.assertEqual("2026-08-25T10:15:00+00:00", listed["taken_at"])
        self.assertEqual("Sommarträffen", listed["meet_name"])
        self.assertTrue(listed["usable"])
        self.assertGreater(listed["size_bytes"], 0)

    def test_a_broken_copy_is_listed_and_marked_not_hidden(self) -> None:
        """Den som letar efter sin backup ska få veta att den finns och att den
        inte duger - inte undra var den tog vägen."""

        (self.backups / "trainmeet-20260101-000000.db").write_bytes(b"inte en databas")
        _, payload = self._call("/v1/server/backups")
        broken = next(b for b in payload["backups"] if b["name"].endswith("000000.db"))
        self.assertFalse(broken["usable"])
        self.assertEqual(
            "kopian går inte att läsa - filen är skadad eller inte en databas",
            broken["problem"],
            "felet ska vara läsbart för den som står vid servern, inte SQLites egen text",
        )

    def test_the_list_names_what_a_restore_would_overwrite(self) -> None:
        _, payload = self._call("/v1/server/backups")
        self.assertEqual("Sommarträffen", payload["overwrites"])

    def test_the_list_says_how_the_last_restore_went(self) -> None:
        """Webbläsaren som bad om återställningen ser bara servern gå ner och
        komma tillbaka. Efter inloggningen ska listan säga hur det gick."""

        _, payload = self._call("/v1/server/backups")
        self.assertIsNone(payload["last_restore"])

        backup.record_restore(self.state, self.copy, "kopian är tom")
        _, payload = self._call("/v1/server/backups")
        self.assertEqual(self.copy.name, payload["last_restore"]["backup"])
        self.assertFalse(payload["last_restore"]["restored"])
        self.assertEqual("kopian är tom", payload["last_restore"]["problem"])

    # ── bekräftelsen ───────────────────────────────────────────────────

    def test_the_confirmation_must_name_the_data_that_is_overwritten(self) -> None:
        """NOLLSTÄLL duger för fabriksåterställningen, som alltid tar allt. Här
        beror det på vad som ligger i servern, och då ska man skriva det."""

        status, error = self._call(
            "/v1/server/restore",
            {"backup": self.copy.name, "confirmation": "JA"},
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual("restore_not_confirmed", error["error"])
        self.assertIn("Sommarträffen", error["message"])
        self.assertIsNone(self.server.restore_requested)

    def test_a_confirmed_restore_is_scheduled_not_performed(self) -> None:
        """Svaret går ut medan servern kör. Filen byts efteråt, av
        tillsynsprocessen, när ingen har databasen öppen."""

        before = self.database.read_bytes()
        status, payload = self._call(
            "/v1/server/restore",
            {"backup": self.copy.name, "confirmation": "sommarträffen"},
        )
        self.assertEqual(HTTPStatus.ACCEPTED, status)
        self.assertEqual("restoring", payload["status"])
        self.assertIsNotNone(self.server.restore_requested)
        # macOS exposes its temporary directory through both /var and /private/var.
        self.assertEqual(self.copy.resolve(), self.server.restore_requested.resolve())
        self.assertTrue(self.server.restart_requested)
        self.assertEqual(before, self.database.read_bytes(), "databasen rördes direkt")
        self.assertNotIn("path", payload, "sökvägen på disk hör inte hemma i svaret")

    def test_the_answer_says_what_happens_to_the_connected(self) -> None:
        """Hela databasen byts, inte bara träffen. Den som trycker ska veta att
        inloggningar och parkopplingar följer med bakåt."""

        _, payload = self._call(
            "/v1/server/restore",
            {"backup": self.copy.name, "confirmation": "Sommarträffen"},
        )
        consequences = " ".join(payload["consequences"])
        self.assertIn("Inloggningar", consequences)
        self.assertIn("parkopplats", consequences)
        self.assertIn("återansluter", consequences)

    # ── vad en webbläsare inte får be om ────────────────────────────────

    def test_a_path_cannot_be_smuggled_in_as_a_name(self) -> None:
        for name in ("../trainmeet.db", "/etc/passwd", "trainmeet-20260825-101500.db.partial"):
            with self.subTest(name=name):
                status, error = self._call(
                    "/v1/server/restore",
                    {"backup": name, "confirmation": "Sommarträffen"},
                )
                self.assertEqual(HTTPStatus.BAD_REQUEST, status)
                self.assertEqual("invalid_backup", error["error"])
                self.assertIsNone(self.server.restore_requested)

    def test_only_a_backup_can_be_restored_even_inside_the_backup_folder(self) -> None:
        """Namnkontrollen prövad för sig.

        En mutation som tog bort mönstret överlevde: sökvägskontrollen fångade
        redan ../ och allt utanför mappen. Kvar fanns det som bara mönstret
        stoppar - en riktig databas som ligger i backupmappen utan att vara en
        säkerhetskopia. Den ska inte gå att lägga in som serverns databas bara
        för att någon råkat lägga filen där.
        """

        smuggled = self.backups / "anteckningar.db"
        connection = sqlite3.connect(smuggled)
        connection.execute("CREATE TABLE anteckningar(rad TEXT)")
        connection.commit()
        connection.close()

        status, error = self._call(
            "/v1/server/restore",
            {"backup": smuggled.name, "confirmation": "Sommarträffen"},
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual("invalid_backup", error["error"])
        self.assertIsNone(self.server.restore_requested)

    def test_the_list_only_shows_backups(self) -> None:
        """Samma gräns i listan: mappen kan innehålla annat."""

        (self.backups / "anteckningar.db").write_bytes(b"")
        _, payload = self._call("/v1/server/backups")
        self.assertEqual(
            ["trainmeet-20260825-101500.db"],
            [item["name"] for item in payload["backups"]],
        )

    def test_a_broken_copy_is_refused_before_anything_stops(self) -> None:
        """Att upptäcka att kopian är trasig efter att servern stannat vore det
        sämsta möjliga ögonblicket."""

        broken = self.backups / "trainmeet-20260101-000000.db"
        broken.write_bytes(b"inte en databas")
        status, error = self._call(
            "/v1/server/restore",
            {"backup": broken.name, "confirmation": "Sommarträffen"},
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("backup_not_usable", error["error"])
        self.assertIsNone(self.server.restore_requested)
        self.assertFalse(self.server.restart_requested)

    def test_an_administrator_may_look_but_not_restore(self) -> None:
        """Återställningen byter ut hela databasen, användarlistan inkluderad.
        Att ändra vilka som har tillgång är ägarens ensak, och en återställning
        gör precis det på omvägen."""

        invited = self.identities.invite_admin_user("Benny", "benny@example.se", "admin")
        self.identities.redeem_admin_setup("benny@example.se", str(invited["setup_code"]), "benny-losenord")
        self.cookie = f"trainmeet_admin={self.identities.create_admin_session('benny@example.se', 'benny-losenord')}"

        status, _ = self._call("/v1/server/backups")
        self.assertEqual(200, status, "en administratör ska få se listan")

        status, error = self._call(
            "/v1/server/restore",
            {"backup": self.copy.name, "confirmation": "Sommarträffen"},
        )
        self.assertEqual(HTTPStatus.FORBIDDEN, status)
        self.assertEqual("owner_required", error["error"])
        self.assertIsNone(self.server.restore_requested)


class RestoreOnDiskTests(unittest.TestCase):
    """Själva bytet, gjort där det ska göras: efter att servern stannat."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.state = Path(self._dir.name)
        self.database = self.state / "trainmeet.db"

        store = SQLiteRuntimeStore(self.database)
        store.install(runtime_package())
        store.close()
        self.copy = backup.create_backup(self.database, self.state / "backups", "20260825-101500")

    def _meet_names(self) -> list[str]:
        connection = sqlite3.connect(self.database)
        try:
            return [row[0] for row in connection.execute("SELECT meet_name FROM runtime_publications")]
        finally:
            connection.close()

    def test_the_copy_replaces_the_database(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE runtime_publications SET meet_name = 'Fel träff'")
        connection.commit()
        connection.close()
        self.assertEqual(["Fel träff"], self._meet_names())

        local_server._restore_from_backup(self.copy, self.database)
        self.assertEqual(["Sommarträffen"], self._meet_names())

    def test_a_write_ahead_log_left_behind_does_not_come_back(self) -> None:
        """Den fällan kostade en hel återställning en gång: SQLite spelar upp
        loggen över kopian och skriver tillbaka precis det man ville bli av
        med, utan att något ser trasigt ut."""

        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE runtime_publications SET meet_name = 'SKADAT'")
        connection.commit()
        connection.close()

        local_server._restore_from_backup(self.copy, self.database)
        self.assertEqual(["Sommarträffen"], self._meet_names())
        self.assertFalse(self.database.with_name("trainmeet.db-wal").exists())

    def test_an_interrupted_restore_leaves_the_database_alone(self) -> None:
        """Avbrottet ska kosta återställningen, inte databasen."""

        before = self.database.read_bytes()
        missing = self.state / "backups" / "trainmeet-20200101-000000.db"
        local_server._restore_from_backup(missing, self.database)
        self.assertEqual(before, self.database.read_bytes())
        self.assertFalse(self.database.with_suffix(".db.restoring").exists())

    def test_a_broken_copy_never_reaches_the_database(self) -> None:
        broken = self.state / "backups" / "trainmeet-20260101-000000.db"
        broken.write_bytes(b"inte en databas")
        before = self.database.read_bytes()
        local_server._restore_from_backup(broken, self.database)
        self.assertEqual(before, self.database.read_bytes())

    # ── beskedet efter omstarten ───────────────────────────────────────

    def test_a_restore_that_worked_is_written_down(self) -> None:
        local_server._restore_from_backup(self.copy, self.database)
        record = backup.last_restore(self.state)
        self.assertEqual(
            {"backup": self.copy.name, "taken_at": "2026-08-25T10:15:00+00:00",
             "restored": True, "problem": None},
            {key: record[key] for key in ("backup", "taken_at", "restored", "problem")},
        )
        self.assertTrue(record["attempted_at"])

    def test_a_restore_that_failed_is_written_down_too(self) -> None:
        """Annars startar servern om med den gamla databasen och ägaren tror
        att kopian ligger på plats. Felet stod bara i loggen."""

        broken = self.state / "backups" / "trainmeet-20260101-000000.db"
        broken.write_bytes(b"inte en databas")
        local_server._restore_from_backup(broken, self.database)
        record = backup.last_restore(self.state)
        self.assertEqual(broken.name, record["backup"])
        self.assertFalse(record["restored"])
        self.assertEqual(
            "kopian går inte att läsa - filen är skadad eller inte en databas",
            record["problem"],
            "samma ord som listan, inte SQLites egen text",
        )

    def test_the_written_problem_names_no_path_on_disk(self) -> None:
        missing = self.state / "backups" / "trainmeet-20200101-000000.db"
        local_server._restore_from_backup(missing, self.database)
        self.assertEqual(
            "säkerhetskopian finns inte: trainmeet-20200101-000000.db",
            backup.last_restore(self.state)["problem"],
        )

    def test_a_full_disk_is_said_in_words_without_the_path(self) -> None:
        full = OSError(28, "No space left on device", str(self.database) + ".restoring")
        with mock.patch.object(backup, "restore", side_effect=full):
            local_server._restore_from_backup(self.copy, self.database)
        problem = backup.last_restore(self.state)["problem"]
        self.assertEqual("kopian kunde inte skrivas på plats (No space left on device)", problem)
        self.assertNotIn(str(self.state), problem)

    def test_an_outcome_that_cannot_be_written_does_not_stop_the_restart(self) -> None:
        with mock.patch.object(backup, "record_restore", side_effect=OSError("skrivskyddat")):
            local_server._restore_from_backup(self.copy, self.database)
        self.assertEqual(["Sommarträffen"], self._meet_names())

    def test_a_damaged_record_reads_as_no_restore(self) -> None:
        record = self.state / backup.RESTORE_RECORD
        for content in ("{inte json", "[1, 2]", ""):
            record.write_text(content, encoding="utf-8")
            self.assertIsNone(backup.last_restore(self.state), content)

    def test_the_record_is_kept_for_the_next_restore_to_replace(self) -> None:
        earlier = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)
        backup.record_restore(self.state, self.copy, "kopian är tom", now=earlier)
        local_server._restore_from_backup(self.copy, self.database)
        record = backup.last_restore(self.state)
        self.assertTrue(record["restored"])
        self.assertNotEqual(earlier.isoformat(), record["attempted_at"])
        self.assertFalse((self.state / (backup.RESTORE_RECORD + ".partial")).exists())

    def test_a_factory_reset_forgets_the_last_restore(self) -> None:
        backup.record_restore(self.state, self.copy)
        local_server._reset_server_state(self.database, self.state)
        self.assertIsNone(backup.last_restore(self.state))


if __name__ == "__main__":
    unittest.main()
