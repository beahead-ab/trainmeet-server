"""Kontona binds till e-postadressen när en äldre server uppgraderas.

Version 3 har inga användarnamn. Ett konto är ett namn, en e-postadress och
ett lösenord, och man loggar in med adressen. Proven här bygger databaser som
äldre versioner skrev dem och öppnar dem med den nya koden: det är så en Pi i
en klubblokal möter ändringen.

Den viktigaste egenskapen är att ingen släpps in på fel konto och att
installationen inte öppnar sig igen. Ett konto utan adress kan inte logga in
förrän ägaren ger det en, och en ägare utan adress får en med
återställningskommandot på maskinen.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tmbox_gateway.identity import AdminAccessError, IdentityStore, _admin_password_digest

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat()

# admin_users som version 2 skrev tabellen, utan display_name.
VERSION_2_USERS = """
CREATE TABLE admin_users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_salt BLOB,
    password_digest BLOB,
    role TEXT NOT NULL CHECK(role IN ('owner', 'admin')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    setup_code_digest BLOB,
    setup_expires_at TEXT,
    email TEXT,
    reset_code_digest BLOB,
    reset_expires_at TEXT
);
"""


def _credentials(password: str) -> tuple[bytes, bytes]:
    salt = password.encode("utf-8")[:16].ljust(16, b".")
    return salt, _admin_password_digest(password, salt)


class Version2UpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "trainmeet.db"
        connection = sqlite3.connect(self.path)
        connection.executescript(VERSION_2_USERS)
        rows = [
            # user_id, username, role, created, email, password
            ("u-casper", "casper", "owner", "2026-01-01", "casper@example.se", "caspers-losenord"),
            ("u-lars", "lars", "admin", "2026-02-01", None, "lars-losenord"),
            # Version 2 krävde ingen unik adress. Benny skrev samma som Casper.
            ("u-benny", "benny", "admin", "2026-03-01", "casper@example.se", "bennys-losenord"),
            ("u-tom", "tom", "admin", "2026-04-01", "", "toms-losenord"),
        ]
        for user_id, username, role, created, email, password in rows:
            salt, digest = _credentials(password)
            connection.execute(
                "INSERT INTO admin_users(user_id, username, password_salt, password_digest, role,"
                " created_at, updated_at, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, salt, digest, role, created, created, email),
            )
        connection.commit()
        connection.close()
        with self.assertLogs("tmbox_gateway.identity", "WARNING") as logged:
            self.store = IdentityStore(self.path)
        self.addCleanup(self.store.close)
        self.warnings = "\n".join(logged.output)

    def by_id(self, user_id: str) -> dict:
        return self.store.admin_user(user_id) or {}

    def test_the_old_username_becomes_the_name(self) -> None:
        self.assertEqual(
            {"u-casper": "casper", "u-lars": "lars", "u-benny": "benny", "u-tom": "tom"},
            {user["user_id"]: user["display_name"] for user in self.store.list_admin_users()},
        )
        self.assertNotIn("username", self.store.list_admin_users()[0])

    def test_the_owner_signs_in_with_the_address_and_the_same_password(self) -> None:
        self.assertIsNotNone(self.store.create_admin_session("casper@example.se", "caspers-losenord"))

    def test_an_old_username_opens_nothing(self) -> None:
        for name, password in (("casper", "caspers-losenord"), ("lars", "lars-losenord"), ("benny", "bennys-losenord")):
            with self.subTest(name=name):
                self.assertIsNone(self.store.create_admin_session(name, password))

    def test_a_shared_address_stays_with_the_owner(self) -> None:
        """Två konton med samma adress kunde annars logga in som varandra.
        Ägaren behåller den, och Benny får vänta på en ny från ägaren."""

        self.assertEqual("casper@example.se", self.by_id("u-casper")["email"])
        self.assertEqual("", self.by_id("u-benny")["email"])
        self.assertIsNone(self.store.create_admin_session("casper@example.se", "bennys-losenord"))
        self.assertIn("benny", self.warnings)

    def test_an_account_without_an_address_cannot_sign_in_until_the_owner_gives_it_one(self) -> None:
        self.assertEqual("", self.by_id("u-lars")["email"])
        self.assertEqual("", self.by_id("u-tom")["email"], "en tom adress är ingen adress")

        self.store.set_admin_user_email("u-lars", "Lars@Example.se")

        self.assertIsNotNone(self.store.create_admin_session("lars@example.se", "lars-losenord"))

    def test_the_address_is_unique_from_now_on(self) -> None:
        with self.assertRaises(AdminAccessError):
            self.store.set_admin_user_email("u-benny", "CASPER@example.se")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._connection.execute(  # noqa: SLF001 - indexet, inte bara koden, ska säga nej
                "UPDATE admin_users SET email = 'casper@example.se' WHERE user_id = 'u-benny'"
            )

    def test_the_server_still_counts_as_installed(self) -> None:
        """Annars öppnade uppgraderingen installationen för vem som helst på
        nätet, eftersom ingen kunde logga in med ett användarnamn längre."""

        self.assertTrue(self.store.admin_access_summary()["password_configured"])

    def test_opening_the_database_again_changes_nothing(self) -> None:
        before = self.store.list_admin_users()
        self.store.close()
        again = IdentityStore(self.path)
        self.addCleanup(again.close)
        self.assertEqual(before, again.list_admin_users())


class SingleLoginUpgradeTests(unittest.TestCase):
    """Den allra äldsta installationen hade en enda inloggning i admin_access."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "trainmeet.db"
        salt, digest = _credentials("det-gamla-losenordet")
        connection = sqlite3.connect(self.path)
        connection.executescript(
            "CREATE TABLE admin_access (singleton INTEGER PRIMARY KEY CHECK(singleton = 1),"
            " username TEXT NOT NULL, password_salt BLOB, password_digest BLOB, updated_at TEXT NOT NULL);"
        )
        connection.execute(
            "INSERT INTO admin_access(singleton, username, password_salt, password_digest, updated_at)"
            " VALUES (1, 'casper', ?, ?, ?)",
            (salt, digest, NOW),
        )
        connection.commit()
        connection.close()
        self.store = IdentityStore(self.path)
        self.addCleanup(self.store.close)

    def test_the_old_administrator_becomes_the_owner_without_an_address(self) -> None:
        [owner] = self.store.list_admin_users()
        self.assertEqual(("owner", "casper", ""), (owner["role"], owner["display_name"], owner["email"]))
        self.assertTrue(self.store.admin_access_summary()["password_configured"])

    def test_the_old_login_no_longer_works(self) -> None:
        self.assertIsNone(self.store.create_admin_session("casper", "det-gamla-losenordet"))

    def test_an_address_and_a_code_bring_the_owner_back(self) -> None:
        """Det är vad återställningskommandot gör med --email."""

        [owner] = self.store.list_admin_users()
        self.store.set_admin_user_email(str(owner["user_id"]), "casper@example.se")
        issued = self.store.reissue_admin_setup(str(owner["user_id"]))
        self.store.redeem_admin_setup("casper@example.se", str(issued["setup_code"]), "ett-nytt-losenord")

        self.assertIsNotNone(self.store.create_admin_session("casper@example.se", "ett-nytt-losenord"))


class NamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = IdentityStore(Path(self._dir.name) / "trainmeet.db")
        self.addCleanup(self.store.close)
        self.store.create_first_owner("  Casper   Andersson ", "casper@example.se", "ett-langt-losenord")

    def test_a_name_is_trimmed_and_two_people_may_share_it(self) -> None:
        self.assertEqual("Casper Andersson", self.store.list_admin_users()[0]["display_name"])
        self.store.invite_admin_user("Casper Andersson", "den.andra.casper@example.se")
        self.assertEqual(2, len(self.store.list_admin_users()))

    def test_an_empty_or_too_long_name_is_refused(self) -> None:
        for name in ("", "   ", "x" * 101):
            with self.subTest(name=name), self.assertRaises(AdminAccessError):
                self.store.invite_admin_user(name, "lars@example.se")


if __name__ == "__main__":
    unittest.main()
