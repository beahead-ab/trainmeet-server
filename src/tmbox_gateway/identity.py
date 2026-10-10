from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

PAIRING_HASH_ITERATIONS = 210_000
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{3,64}$")
ADMIN_SETUP_TTL = timedelta(days=7)
# En kod för nytt lösenord skickas med e-post och kan begäras av vem som helst
# som känner en adress. Den ska därför gälla kort.
ADMIN_RESET_TTL = timedelta(minutes=30)
ADMIN_EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
# Visningsnamnet är bara ett namn: det loggar inte in någon och behöver inte
# vara unikt. Två Lars på samma server skiljs åt av sina adresser.
ADMIN_NAME_MAX = 100
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
ADMIN_SESSION_TTL = timedelta(hours=12)
# A connection code printed on the meeting's screens has to keep working for as
# long as it is on display, so it may be issued without an expiry.
NEVER_EXPIRES = datetime(9999, 12, 31, tzinfo=timezone.utc)


class DeviceKind(StrEnum):
    SWIFT_PANEL = "swift_panel"
    SWIFT_ADMIN = "swift_admin"
    WEB_ADMIN = "web_admin"
    ESP32_PANEL = "esp32_panel"
    TKL_TERMINAL = "tkl_terminal"
    US_CONDUCTOR = "us_conductor"


class PairingError(RuntimeError):
    code = "pairing_failed"


class InvalidPairingCodeError(PairingError):
    code = "invalid_pairing_code"


class InvalidClientError(PairingError):
    code = "invalid_client"


class ProvisioningError(PairingError):
    code = "provisioning_failed"


class AdminAccessError(ValueError):
    """Invalid local administrator configuration or credentials."""


@dataclass(frozen=True)
class PairingGrant:
    pairing_id: str
    panel_ids: tuple[str, ...]
    allowed_kinds: tuple[DeviceKind, ...]


@dataclass(frozen=True)
class DisplayCapability:
    """What a box can actually render.

    Four geometries are supported and the logic is identical between them;
    only how much context fits per screen differs. A box that announces
    something else is treated as the smallest one rather than locked out.
    """

    rows: int = 2
    cols: int = 16
    charset: str = "ascii"

    @classmethod
    def parse(cls, value: Any) -> "DisplayCapability":
        if not isinstance(value, dict):
            return cls()
        rows = value.get("rows")
        cols = value.get("cols")
        charset = str(value.get("charset") or "ascii").lower()
        return cls(
            rows=rows if rows in (2, 4) else 2,
            cols=cols if cols in (16, 20) else 16,
            charset=charset if charset in ("ascii", "cgram") else "ascii",
        )

    def to_dict(self) -> dict[str, Any]:
        return {"rows": self.rows, "cols": self.cols, "charset": self.charset}


#: Which lines a box at a station handles. Several boxes can share a
#: station: one for traffic to the left and one to the right, as at Dimmeby in the test bench.
#: Left and right mean the same as on the station's TMBox placement.
STATION_SIDES = ("both", "left", "right")


@dataclass(frozen=True)
class PairedClient:
    client_id: str
    display_name: str
    kind: DeviceKind
    panel_ids: tuple[str, ...]
    station_id: str | None = None
    #: Vem som är inloggad, när det är en människa. En TMBox har ingen roll.
    #: Förvalet är ägare: det är vad varje inloggning betydde innan
    #: användarlistan fanns, och konsolgenvägen vid lådan fungerar likadant
    #: som förut.
    admin_user_id: str | None = None
    admin_role: str = "owner"


@dataclass(frozen=True)
class DiscoveredDevice:
    device_id: str
    device_code: str
    model: str
    firmware_version: str
    last_seen_at: str
    panel_ids: tuple[str, ...]
    station_id: str | None = None
    hardware_version: str = ""
    protocol_version: int = 1
    display: DisplayCapability = DisplayCapability()
    station_side: str = "both"


@dataclass(frozen=True)
class PairingResult:
    client: PairedClient
    access_token: str


class IdentityStore:
    """Durable local client registry and one-time pairing-code store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pairing_codes (
                pairing_id TEXT PRIMARY KEY,
                secret_salt BLOB NOT NULL,
                secret_digest BLOB NOT NULL,
                expires_at TEXT NOT NULL,
                max_uses INTEGER NOT NULL CHECK(max_uses > 0),
                uses INTEGER NOT NULL DEFAULT 0 CHECK(uses >= 0),
                allowed_kinds_json TEXT NOT NULL,
                panel_ids_json TEXT NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                credential_digest BLOB NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_paired_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS client_panels (
                client_id TEXT NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
                panel_id TEXT NOT NULL,
                PRIMARY KEY(client_id, panel_id)
            );

            CREATE TABLE IF NOT EXISTS discovered_devices (
                device_id TEXT PRIMARY KEY,
                device_code TEXT NOT NULL UNIQUE,
                model TEXT NOT NULL,
                firmware_version TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                hardware_version TEXT NOT NULL DEFAULT '',
                protocol_version INTEGER NOT NULL DEFAULT 1,
                display_rows INTEGER NOT NULL DEFAULT 2,
                display_cols INTEGER NOT NULL DEFAULT 16,
                charset TEXT NOT NULL DEFAULT 'ascii'
            );

            CREATE TABLE IF NOT EXISTS device_ui_preferences (
                device_id TEXT PRIMARY KEY,
                language TEXT NOT NULL CHECK(language IN ('sv','da','nb','en','de'))
            );

            CREATE TABLE IF NOT EXISTS admin_access (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                username TEXT NOT NULL,
                password_salt BLOB,
                password_digest BLOB,
                updated_at TEXT NOT NULL,
                must_change_password INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS admin_sessions (
                session_digest BLOB PRIMARY KEY,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Flera personer kan sköta servern. Ägaren är den som dessutom får
            -- lägga till och ta bort andra; en administratör kan allt annat.
            --
            -- Listan är serverns egen och delas inte med Cloud. En Pi i en
            -- klubblokal ska fungera utan nät, och en användarlista som kräver
            -- uppkoppling för att logga in vore fel sorts beroende.
            CREATE TABLE IF NOT EXISTS admin_users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt BLOB,
                password_digest BLOB,
                role TEXT NOT NULL CHECK(role IN ('owner', 'admin')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                -- Ägaren skriver aldrig någon annans lösenord. Den som bjuds
                -- in får en engångskod och väljer sitt eget. Koden lämnas över
                -- på plats, eller med e-post via TrainMeet Cloud när servern
                -- är kopplad och kontot har en adress.
                setup_code_digest BLOB,
                setup_expires_at TEXT
            );
            """
        )
        self._add_missing_columns("admin_sessions", {"user_id": "TEXT"})
        self._add_missing_columns(
            "admin_users",
            {
                "setup_code_digest": "BLOB",
                "setup_expires_at": "TEXT",
                # Kontot är adressen: den loggar in, och dit går en inbjudan
                # och en kod för nytt lösenord när servern är kopplad till
                # TrainMeet Cloud. Ett konto utan adress kan inte logga in.
                "email": "TEXT",
                "reset_code_digest": "BLOB",
                "reset_expires_at": "TEXT",
                "display_name": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self._add_missing_columns(
            "admin_access",
            {"must_change_password": "INTEGER NOT NULL DEFAULT 0"},
        )
        # A device is assigned one station. Panels stay for the v1 clients
        # that still speak the panel protocol.
        self._add_missing_columns("clients", {"station_id": "TEXT", "browser_workspace": "TEXT",
                                              "station_side": "TEXT NOT NULL DEFAULT 'both'"})
        self._add_missing_columns(
            "discovered_devices",
            {
                "hardware_version": "TEXT NOT NULL DEFAULT ''",
                "protocol_version": "INTEGER NOT NULL DEFAULT 1",
                "display_rows": "INTEGER NOT NULL DEFAULT 2",
                "display_cols": "INTEGER NOT NULL DEFAULT 16",
                "charset": "TEXT NOT NULL DEFAULT 'ascii'",
                "removed_at": "TEXT",
            },
        )
        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT INTO admin_access(singleton, username, updated_at)
            VALUES (1, '', ?)
            ON CONFLICT(singleton) DO NOTHING
            """,
            (now,),
        )
        self._adopt_singleton_admin_as_owner(now)
        self._bind_accounts_to_email()

    def _adopt_singleton_admin_as_owner(self, now: str) -> None:
        """Den befintliga administratören blir den första ägaren.

        Servern hade en enda administratör i en singleton-rad. Den raden är
        fortfarande sanningen om vem som är ägare - allt gammalt API pekar på
        den - men den finns nu också i användarlistan, så att flera personer
        kan finnas vid sidan av.

        Det här får aldrig låsa ute någon. Saknas användarnamn eller lösenord
        är installationen inte färdig, och då finns ingen ägare att adoptera.
        """

        if self._connection.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone():
            return
        row = self._connection.execute(
            "SELECT username, password_salt, password_digest, must_change_password"
            " FROM admin_access WHERE singleton = 1"
        ).fetchone()
        if row is None or not str(row[0] or "").strip() or row[2] is None:
            return
        self._connection.execute(
            """
            INSERT INTO admin_users(
                user_id, username, password_salt, password_digest,
                role, created_at, updated_at, must_change_password
            ) VALUES (?, ?, ?, ?, 'owner', ?, ?, ?)
            """,
            (secrets.token_hex(16), str(row[0]).strip(), row[1], row[2], now, now, int(row[3] or 0)),
        )

    def _bind_accounts_to_email(self) -> None:
        """Kontona binds till e-postadressen, och användarnamnet blir ett namn.

        Kolumnen `username` ligger kvar, eftersom SQLite inte kan ta bort en
        kolumn med UNIQUE utan att bygga om tabellen, men den läses aldrig mer.
        Nya konton får sitt eget id där.

        Ett gammalt konto behåller sitt användarnamn som visningsnamn. Har det
        ingen adress kan det inte logga in förrän ägaren ger det en, och en
        ägare utan adress använder `tmbox_gateway.recover` på maskinen.

        Adressen blir unik. Hade två konton samma adress behåller den äldsta
        ägaren eller administratören med lösenord den, och de andra blir utan
        tills ägaren rättar dem. Det skrivs i loggen.
        """

        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("UPDATE admin_users SET display_name = username WHERE display_name = ''")
            connection.execute("UPDATE admin_users SET email = NULL WHERE trim(email) = ''")
            duplicates = connection.execute(
                "SELECT email FROM admin_users WHERE email IS NOT NULL GROUP BY email HAVING COUNT(*) > 1"
            ).fetchall()
            for (email,) in duplicates:
                keeper = connection.execute(
                    "SELECT user_id FROM admin_users WHERE email = ?"
                    " ORDER BY role = 'owner' DESC, password_digest IS NOT NULL DESC, created_at LIMIT 1",
                    (email,),
                ).fetchone()[0]
                others = connection.execute(
                    "SELECT display_name FROM admin_users WHERE email = ? AND user_id != ?", (email, keeper)
                ).fetchall()
                connection.execute("UPDATE admin_users SET email = NULL WHERE email = ? AND user_id != ?", (email, keeper))
                LOGGER.warning(
                    "Flera konton hade samma e-postadress. Den står kvar på ett av dem; %s saknar nu adress"
                    " och kan inte logga in förrän ägaren ger dem en.",
                    ", ".join(str(row[0]) for row in others),
                )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS admin_users_email ON admin_users(email) WHERE email IS NOT NULL"
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {
            row[1]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )

    def issue_pairing_code(
        self,
        panel_ids: list[str] | tuple[str, ...],
        *,
        allowed_kinds: list[DeviceKind] | tuple[DeviceKind, ...] = (
            DeviceKind.SWIFT_PANEL,
            DeviceKind.SWIFT_ADMIN,
            DeviceKind.WEB_ADMIN,
        ),
        ttl: timedelta | None = timedelta(minutes=15),
        max_uses: int = 1,
        label: str | None = None,
        code: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Issue a pairing code. A ttl of None never expires."""
        if not panel_ids and tuple(allowed_kinds) != (DeviceKind.US_CONDUCTOR,):
            raise ValueError("A pairing code must grant at least one panel")
        if not allowed_kinds:
            raise ValueError("A pairing code must allow at least one device kind")
        if max_uses < 1:
            raise ValueError("max_uses must be positive")

        now = now or datetime.now(timezone.utc)
        raw_code = _normalize_code(code or f"{secrets.randbelow(1_000_000):06d}")
        if len(raw_code) < 6:
            raise ValueError("Pairing codes must contain at least six characters")
        salt = secrets.token_bytes(16)
        digest = _pairing_digest(raw_code, salt)
        pairing_id = secrets.token_hex(16)

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO pairing_codes (
                    pairing_id, secret_salt, secret_digest, expires_at, max_uses,
                    uses, allowed_kinds_json, panel_ids_json, label, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    pairing_id,
                    salt,
                    digest,
                    (NEVER_EXPIRES if ttl is None else now + ttl).isoformat(),
                    max_uses,
                    json.dumps([kind.value for kind in allowed_kinds]),
                    json.dumps(sorted(set(panel_ids))),
                    label,
                    now.isoformat(),
                ),
            )
        return _display_code(raw_code)

    def reserve_pairing_code(
        self,
        code: str,
        kind: DeviceKind,
        *,
        now: datetime | None = None,
    ) -> PairingGrant:
        now = now or datetime.now(timezone.utc)
        normalized = _normalize_code(code)
        if not normalized:
            raise InvalidPairingCodeError("Parkopplingskoden är ogiltig eller har gått ut")

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                rows = self._connection.execute(
                    """
                    SELECT pairing_id, secret_salt, secret_digest,
                           allowed_kinds_json, panel_ids_json
                    FROM pairing_codes
                    WHERE uses < max_uses AND expires_at >= ?
                    """,
                    (now.isoformat(),),
                ).fetchall()
                selected = None
                for row in rows:
                    if hmac.compare_digest(_pairing_digest(normalized, row[1]), row[2]):
                        selected = row
                        break
                if selected is None:
                    raise InvalidPairingCodeError("Parkopplingskoden är ogiltig eller har gått ut")

                allowed_kinds = tuple(DeviceKind(value) for value in json.loads(selected[3]))
                if kind not in allowed_kinds:
                    raise InvalidPairingCodeError("Koden gäller inte för den här typen av enhet")

                self._connection.execute(
                    "UPDATE pairing_codes SET uses = uses + 1 WHERE pairing_id = ?",
                    (selected[0],),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

        return PairingGrant(
            pairing_id=selected[0],
            panel_ids=tuple(json.loads(selected[4])),
            allowed_kinds=allowed_kinds,
        )

    def release_pairing_code(self, pairing_id: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE pairing_codes
                SET uses = CASE WHEN uses > 0 THEN uses - 1 ELSE 0 END
                WHERE pairing_id = ?
                """,
                (pairing_id,),
            )

    def pairing_code_state(self, *, label: str) -> dict[str, Any] | None:
        """How far the code with this label has been used, and until when.

        There is one at a time: a new one is issued after revoke_pairing_codes."""
        with self._lock:
            row = self._connection.execute(
                "SELECT uses, max_uses, expires_at FROM pairing_codes WHERE label = ?",
                (label,),
            ).fetchone()
        return None if row is None else {"uses": row[0], "max_uses": row[1], "expires_at": row[2]}

    def revoke_pairing_codes(self, *, label: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM pairing_codes WHERE label = ?",
                (label,),
            )

    def device_language(self, device_id: str, default: str = "sv") -> str:
        from .device_ui import language_code
        with self._lock:
            row = self._connection.execute(
                "SELECT language FROM device_ui_preferences WHERE device_id = ?", (device_id,)
            ).fetchone()
        return language_code(row[0] if row else default)

    def set_device_language(self, device_id: str, language: str) -> str:
        from .device_ui import language_code
        code = language_code(language)
        # Preferences cannot create an identity or grant a station/role.
        with self._lock:
            if self._connection.execute(
                "SELECT 1 FROM discovered_devices WHERE device_id = ? AND removed_at IS NULL", (device_id,)
            ).fetchone() is None:
                raise ValueError("Unknown TMBox")
            self._connection.execute(
                "INSERT INTO device_ui_preferences(device_id, language) VALUES (?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET language = excluded.language",
                (device_id, code),
            )
        return code

    def register_client(
        self,
        client_id: str,
        display_name: str,
        kind: DeviceKind,
        credential: str,
        panel_ids: tuple[str, ...],
        *,
        station_id: str | None = None,
        station_side: str = "both",
        now: datetime | None = None,
        reactivate_device: bool = False,
        preserve_credential: bool = False,
        browser_workspace: str | None = None,
    ) -> PairedClient:
        now = now or datetime.now(timezone.utc)
        digest = _credential_digest(credential)
        if station_side not in STATION_SIDES:
            raise InvalidClientError("Sidan måste vara båda, vänster eller höger")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                removed = self._connection.execute(
                    "SELECT removed_at FROM discovered_devices WHERE device_id = ?",
                    (client_id,),
                ).fetchone()
                if removed and removed[0] and not reactivate_device:
                    raise InvalidClientError("Boxen är borttagen. Be administratören koppla den igen med boxens kod.")
                created_at = self._connection.execute(
                    "SELECT created_at, credential_digest, enabled FROM clients WHERE client_id = ?",
                    (client_id,),
                ).fetchone()
                if preserve_credential and created_at and created_at[2]:
                    digest = created_at[1]
                self._connection.execute(
                    """
                    INSERT INTO clients (
                        client_id, display_name, kind, credential_digest,
                        enabled, created_at, last_paired_at, station_id, browser_workspace,
                        station_side
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        kind = excluded.kind,
                        credential_digest = excluded.credential_digest,
                        enabled = 1,
                        last_paired_at = excluded.last_paired_at,
                        station_id = excluded.station_id,
                        station_side = excluded.station_side
                    """,
                    (
                        client_id,
                        display_name,
                        kind.value,
                        digest,
                        created_at[0] if created_at else now.isoformat(),
                        now.isoformat(),
                        station_id,
                        browser_workspace,
                        station_side,
                    ),
                )
                self._connection.execute(
                    "DELETE FROM client_panels WHERE client_id = ?",
                    (client_id,),
                )
                self._connection.executemany(
                    "INSERT INTO client_panels (client_id, panel_id) VALUES (?, ?)",
                    [(client_id, panel_id) for panel_id in sorted(set(panel_ids))],
                )
                if reactivate_device:
                    self._connection.execute(
                        "UPDATE discovered_devices SET removed_at = NULL WHERE device_id = ?",
                        (client_id,),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

        return PairedClient(
            client_id, display_name, kind, tuple(sorted(set(panel_ids))), station_id
        )

    def authenticate(self, credential: str) -> PairedClient | None:
        digest = _credential_digest(credential)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT client_id, display_name, kind, station_id
                FROM clients
                WHERE credential_digest = ? AND enabled = 1
                """,
                (digest,),
            ).fetchone()
            if row is None:
                return None
            panels = self._panel_ids_locked(row[0])
        return PairedClient(row[0], row[1], DeviceKind(row[2]), panels, row[3])

    def enroll_physical_box(self, client_id: str) -> PairedClient:
        """Enroll a discovered box without granting a station or changing one.

        Code redemption is handled by the caller. Keep this atomic with admin
        assignment: a repeated enrollment must never wipe an assigned station,
        change panel grants or re-enable a disabled device.
        """
        with self._lock:
            discovered = self._connection.execute(
                "SELECT device_code, removed_at FROM discovered_devices WHERE device_id = ?",
                (client_id,),
            ).fetchone()
            if discovered is None:
                raise InvalidClientError("Boxen har inte upptäckts ännu. Invänta MQTT-anslutningen.")
            if discovered[1]:
                raise InvalidClientError("Boxen är borttagen. Be administratören koppla den igen med boxens kod.")
            now = datetime.now(timezone.utc).isoformat()
            self._connection.execute(
                """INSERT OR IGNORE INTO clients (
                    client_id, display_name, kind, credential_digest, enabled,
                    created_at, last_paired_at, station_id
                ) VALUES (?, ?, ?, ?, 1, ?, ?, NULL)""",
                (client_id, discovered[0], DeviceKind.ESP32_PANEL.value,
                 _credential_digest(secrets.token_urlsafe(32)), now, now),
            )
            row = self._connection.execute(
                "SELECT display_name, kind, enabled, station_id FROM clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if not row[2] or row[1] != DeviceKind.ESP32_PANEL.value:
                raise InvalidClientError("Boxen är spärrad eller har fel enhetstyp. Kontakta administratören.")
            return PairedClient(client_id, row[0], DeviceKind(row[1]),
                                self._panel_ids_locked(client_id), row[3])

    def client(self, client_id: str) -> PairedClient | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT client_id, display_name, kind, station_id
                FROM clients
                WHERE client_id = ? AND enabled = 1
                """,
                (client_id,),
            ).fetchone()
            if row is None:
                return None
            panels = self._panel_ids_locked(client_id)
        return PairedClient(row[0], row[1], DeviceKind(row[2]), panels, row[3])

    def panels_for_client(self, client_id: str) -> tuple[str, ...]:
        client = self.client(client_id)
        return client.panel_ids if client else ()

    def station_for_client(self, client_id: str) -> str | None:
        """The one station this device is assigned to, if any.

        A station can have several boxes in the same operating room, but a box
        has exactly one station.
        """
        client = self.client(client_id)
        return client.station_id if client else None

    def station_side_for_client(self, client_id: str) -> str:
        """Both, left or right: the lines this box handles at its station."""
        with self._lock:
            return self._station_side_locked(client_id)

    def enabled_clients(self) -> tuple[PairedClient, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT client_id, display_name, kind, station_id
                FROM clients
                WHERE enabled = 1
                ORDER BY client_id
                """
            ).fetchall()
            return tuple(
                PairedClient(
                    row[0], row[1], DeviceKind(row[2]), self._panel_ids_locked(row[0]), row[3]
                )
                for row in rows
            )

    def disable_client(self, client_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE clients SET enabled = 0 WHERE client_id = ?",
                (client_id,),
            )

    def clear_meet_assignments(self) -> None:
        """Keep accounts, tokens and hardware IDs, but retire meet-scoped grants."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute("DELETE FROM client_panels")
                self._connection.execute("UPDATE clients SET station_id = NULL")
                # Previously issued panel grants must not rebind a new meet.
                self._connection.execute("DELETE FROM pairing_codes")
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def reconcile_panels(self, valid_panel_ids: set[str]) -> None:
        """Keep physical assignments that still exist and grant admins all active panels."""
        ordered_panels = sorted(valid_panel_ids)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if ordered_panels:
                    placeholders = ",".join("?" for _ in ordered_panels)
                    self._connection.execute(
                        f"DELETE FROM client_panels WHERE panel_id NOT IN ({placeholders})",
                        ordered_panels,
                    )
                else:
                    self._connection.execute("DELETE FROM client_panels")
                admin_rows = self._connection.execute(
                    """
                    SELECT client_id FROM clients
                    WHERE enabled = 1 AND kind IN (?, ?)
                    """,
                    (DeviceKind.WEB_ADMIN.value, DeviceKind.SWIFT_ADMIN.value),
                ).fetchall()
                for row in admin_rows:
                    self._connection.execute(
                        "DELETE FROM client_panels WHERE client_id = ?",
                        (row[0],),
                    )
                    self._connection.executemany(
                        "INSERT INTO client_panels(client_id, panel_id) VALUES (?, ?)",
                        [(row[0], panel_id) for panel_id in ordered_panels],
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def record_discovery(
        self,
        device_id: str,
        device_code: str,
        *,
        model: str = "TMBox",
        firmware_version: str = "unknown",
        hardware_version: str = "",
        protocol_version: int = 1,
        display: DisplayCapability | None = None,
        now: datetime | None = None,
    ) -> DiscoveredDevice:
        device_id = device_id.strip()
        device_code = _normalize_device_code(device_code)
        if not CLIENT_ID_PATTERN.fullmatch(device_id):
            raise InvalidClientError("Ogiltigt enhets-ID från TMBox")
        if len(device_code) < 4 or len(device_code) > 24:
            raise InvalidClientError("Ogiltig kod från TMBox")
        now = now or datetime.now(timezone.utc)
        capability = display or DisplayCapability()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO discovered_devices (
                    device_id, device_code, model, firmware_version,
                    first_seen_at, last_seen_at, hardware_version,
                    protocol_version, display_rows, display_cols, charset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    device_code = excluded.device_code,
                    model = excluded.model,
                    firmware_version = excluded.firmware_version,
                    last_seen_at = excluded.last_seen_at,
                    hardware_version = excluded.hardware_version,
                    protocol_version = excluded.protocol_version,
                    display_rows = excluded.display_rows,
                    display_cols = excluded.display_cols,
                    charset = excluded.charset
                """,
                (
                    device_id,
                    device_code,
                    model[:80],
                    firmware_version[:40],
                    now.isoformat(),
                    now.isoformat(),
                    hardware_version[:40],
                    int(protocol_version),
                    capability.rows,
                    capability.cols,
                    capability.charset,
                ),
            )
        return self.discovered_device(device_id)

    def touch_discovered_device(self, device_id: str, *, now: datetime | None = None) -> None:
        """Record liveness without registration, reassigning or reviving a box."""
        with self._lock:
            self._connection.execute(
                "UPDATE discovered_devices SET last_seen_at = ? WHERE device_id = ? AND removed_at IS NULL",
                ((now or datetime.now(timezone.utc)).isoformat(), device_id),
            )

    def discovered_device(self, device_id: str) -> DiscoveredDevice:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT device_id, device_code, model, firmware_version, last_seen_at, hardware_version, protocol_version, display_rows, display_cols, charset
                FROM discovered_devices WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            if row is None:
                raise InvalidClientError("TMBox-enheten har ännu inte hittats")
            panels = self._panel_ids_locked(device_id)
            station_id = self._station_id_locked(device_id)
            side = self._station_side_locked(device_id)
        return _discovered_device_from_row(row, panels, station_id, side)

    def discovered_device_or_none(self, device_id: str) -> DiscoveredDevice | None:
        try:
            return self.discovered_device(device_id)
        except InvalidClientError:
            return None

    def discovered_devices(self) -> tuple[DiscoveredDevice, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT device_id, device_code, model, firmware_version, last_seen_at, hardware_version, protocol_version, display_rows, display_cols, charset
                FROM discovered_devices WHERE removed_at IS NULL ORDER BY last_seen_at DESC
                """
            ).fetchall()
            return tuple(
                _discovered_device_from_row(
                    row,
                    self._panel_ids_locked(row[0]),
                    self._station_id_locked(row[0]),
                    self._station_side_locked(row[0]),
                )
                for row in rows
            )

    def removed_devices(self) -> tuple[DiscoveredDevice, ...]:
        """Boxes an administrator removed. They stay out until reconnected
        by their code; the caller decides which are worth showing."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT device_id, device_code, model, firmware_version, last_seen_at, hardware_version, protocol_version, display_rows, display_cols, charset
                FROM discovered_devices WHERE removed_at IS NOT NULL ORDER BY last_seen_at DESC
                """
            ).fetchall()
            return tuple(_discovered_device_from_row(row, (), None) for row in rows)

    def assign_discovered_device(
        self,
        device_code: str,
        panel_ids: tuple[str, ...] = (),
        *,
        station_id: str | None = None,
        station_side: str = "both",
        now: datetime | None = None,
    ) -> PairedClient:
        normalized = _normalize_device_code(device_code)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT device_id, model FROM discovered_devices
                WHERE device_code = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                raise InvalidClientError("Ingen inkopplad TMBox har den koden")
            self._require_physical_box_locked(row[0])
            existing = self._connection.execute("SELECT kind FROM clients WHERE client_id = ?", (row[0],)).fetchone()
            return self.register_client(
                row[0],
                f"{row[1]} {normalized}",
                DeviceKind(existing[0]) if existing else DeviceKind.ESP32_PANEL,
                secrets.token_urlsafe(32),
                panel_ids,
                station_id=station_id,
                station_side=station_side,
                now=now,
                reactivate_device=True,
                preserve_credential=True,
            )

    def browser_workspace(self, client_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT browser_workspace FROM clients WHERE client_id = ? AND enabled = 1", (client_id,),
            ).fetchone()
            return row[0] if row else None

    def _require_physical_box_locked(self, device_id: str) -> None:
        row = self._connection.execute(
            "SELECT kind, browser_workspace FROM clients WHERE client_id = ?", (device_id,),
        ).fetchone()
        if row and row[0] != DeviceKind.ESP32_PANEL.value and not (
            row[0] == DeviceKind.TKL_TERMINAL.value and row[1] == "tkl"
        ):
            raise InvalidClientError("Enheten är inte en TMBox eller virtuell TKL")

    def remove_discovered_device(self, device_id: str, *, idle_before: datetime | None = None) -> bool:
        """Revoke a box, preserving traffic and a tombstone against rediscovery.

        A retained MQTT hello must not undo the administrator's decision.
        Only explicit assignment by its printed code brings the box back.
        """
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if idle_before is not None:
                    # Re-check liveness and assignment in the same transaction
                    # as revocation: a stale polling snapshot may not remove a
                    # box an administrator just assigned or that just checked in.
                    row = self._connection.execute(
                        "SELECT d.last_seen_at FROM discovered_devices d JOIN clients c ON c.client_id = d.device_id "
                        "WHERE d.device_id = ? AND d.removed_at IS NULL AND c.enabled = 1 "
                        "AND c.browser_workspace = 'tmbox' AND c.station_id IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM client_panels p WHERE p.client_id = c.client_id)",
                        (device_id,),
                    ).fetchone()
                    seen = datetime.fromisoformat(row[0]) if row else None
                    if seen is not None and seen.tzinfo is None:
                        seen = seen.replace(tzinfo=timezone.utc)
                    if seen is None or seen > idle_before:
                        self._connection.execute("COMMIT")
                        return False
                self.discovered_device(device_id)  # Reject unknown IDs before writing.
                self._require_physical_box_locked(device_id)
                self._connection.execute(
                    "UPDATE discovered_devices SET removed_at = COALESCE(removed_at, ?) WHERE device_id = ?",
                    (datetime.now(timezone.utc).isoformat(), device_id),
                )
                self._connection.execute(
                    "UPDATE clients SET enabled = 0, station_id = NULL, credential_digest = ? WHERE client_id = ?",
                    (_credential_digest(secrets.token_urlsafe(32)), device_id),
                )
                self._connection.execute("DELETE FROM client_panels WHERE client_id = ?", (device_id,))
                self._connection.execute("COMMIT")
                return True
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def admin_access_summary(self) -> dict[str, object]:
        """Har servern ett konto som kan logga in?

        Frågan avgör om installationen är öppen, och den gäller hela servern.
        Den namnger ingen: svaret ges innan någon har loggat in.
        """

        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM admin_users WHERE password_digest IS NOT NULL LIMIT 1"
            ).fetchone()
        # must_change_password finns kvar i svaret för klienter som läser det,
        # men ingenting sätter det längre: var och en väljer sitt lösenord själv.
        return {"password_configured": row is not None, "must_change_password": False}

    def create_first_owner(self, display_name: str, email: str, password: str) -> dict[str, object]:
        """Installationens första konto, ägaren. Bara när inget konto kan logga in."""

        display_name = clean_admin_display_name(display_name)
        email = required_admin_email(email)
        _check_admin_password(password)
        now = datetime.now(timezone.utc).isoformat()
        salt = secrets.token_bytes(16)
        user_id = secrets.token_hex(16)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if self._connection.execute(
                    "SELECT 1 FROM admin_users WHERE password_digest IS NOT NULL LIMIT 1"
                ).fetchone():
                    raise AdminAccessError("Administratören är redan skapad")
                self._assert_email_free_locked(email)
                self._connection.execute(
                    """
                    INSERT INTO admin_users(
                        user_id, username, display_name, email, password_salt, password_digest,
                        role, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'owner', ?, ?)
                    """,
                    (user_id, user_id, display_name, email, salt,
                     _admin_password_digest(password, salt), now, now),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.admin_user(user_id) or {}

    def _assert_email_free_locked(self, email: str, user_id: str | None = None) -> None:
        if self._connection.execute(
            "SELECT 1 FROM admin_users WHERE email = ? AND user_id IS NOT ?", (email, user_id)
        ).fetchone():
            raise AdminAccessError("Det finns redan ett konto med den e-postadressen")

    # ---------------------------------------------------------- användare

    def list_admin_users(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT user_id, display_name, role, created_at, updated_at,"
                " password_digest IS NOT NULL, must_change_password,"
                " setup_code_digest IS NOT NULL, setup_expires_at, email"
                " FROM admin_users ORDER BY role DESC, display_name COLLATE NOCASE, email"
            ).fetchall()
        return [
            {
                "user_id": row[0],
                "display_name": row[1],
                "role": row[2],
                "created_at": row[3],
                "updated_at": row[4],
                "password_configured": bool(row[5]),
                "must_change_password": bool(row[6]),
                "invitation_pending": bool(row[7]),
                "invitation_expires_at": row[8],
                "email": row[9] or "",
            }
            for row in rows
        ]

    def admin_user(self, user_id: str) -> dict[str, object] | None:
        return next(
            (user for user in self.list_admin_users() if user["user_id"] == user_id), None
        )

    def invite_admin_user(self, display_name: str, email: str, role: str = "admin") -> dict[str, object]:
        """Skapa ett konto och en engångskod att lämna över.

        Ägaren sätter inte någon annans lösenord. Den inbjudne löser in koden
        med sin e-postadress och väljer sitt eget - då finns lösenordet aldrig
        hos någon annan, inte ens en kort stund.

        Koden lämnas över på plats eller, när servern är kopplad till
        TrainMeet Cloud, med e-post till adressen.
        """

        display_name = clean_admin_display_name(display_name)
        email = required_admin_email(email)
        if role not in {"owner", "admin"}:
            raise AdminAccessError("Rollen måste vara ägare eller administratör")

        now = datetime.now(timezone.utc)
        code = _new_account_code()
        user_id = secrets.token_hex(16)
        with self._lock:
            self._assert_email_free_locked(email)
            self._connection.execute(
                """
                INSERT INTO admin_users(
                    user_id, username, display_name, role, created_at, updated_at,
                    must_change_password, setup_code_digest, setup_expires_at, email
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (user_id, user_id, display_name, role, now.isoformat(), now.isoformat(),
                 _credential_digest(code), (now + ADMIN_SETUP_TTL).isoformat(), email),
            )
        return {**(self.admin_user(user_id) or {}), "setup_code": code}

    def reissue_admin_setup(self, user_id: str) -> dict[str, object]:
        """En ny kod när den gamla gått ut eller kommit bort."""

        now = datetime.now(timezone.utc)
        code = _new_account_code()
        with self._lock:
            if self._connection.execute(
                "SELECT 1 FROM admin_users WHERE user_id = ?", (user_id,)
            ).fetchone() is None:
                raise AdminAccessError("Användaren finns inte")
            self._connection.execute(
                "UPDATE admin_users SET setup_code_digest = ?, setup_expires_at = ?,"
                " updated_at = ? WHERE user_id = ?",
                (_credential_digest(code), (now + ADMIN_SETUP_TTL).isoformat(),
                 now.isoformat(), user_id),
            )
        return {**(self.admin_user(user_id) or {}), "setup_code": code}

    def set_admin_user_email(self, user_id: str, email: str) -> dict[str, object]:
        """Byt kontots adress. Den kan inte tas bort, bara bytas: utan adress
        kan kontot inte logga in."""

        email = required_admin_email(email)
        with self._lock:
            if self._connection.execute(
                "SELECT 1 FROM admin_users WHERE user_id = ?", (user_id,)
            ).fetchone() is None:
                raise AdminAccessError("Användaren finns inte")
            self._assert_email_free_locked(email, user_id)
            self._connection.execute(
                "UPDATE admin_users SET email = ?, updated_at = ? WHERE user_id = ?",
                (email, datetime.now(timezone.utc).isoformat(), user_id),
            )
        return self.admin_user(user_id) or {}

    def set_admin_user_display_name(self, user_id: str, display_name: str) -> dict[str, object]:
        display_name = clean_admin_display_name(display_name)
        with self._lock:
            if self._connection.execute(
                "SELECT 1 FROM admin_users WHERE user_id = ?", (user_id,)
            ).fetchone() is None:
                raise AdminAccessError("Användaren finns inte")
            self._connection.execute(
                "UPDATE admin_users SET display_name = ?, updated_at = ? WHERE user_id = ?",
                (display_name, datetime.now(timezone.utc).isoformat(), user_id),
            )
        return self.admin_user(user_id) or {}

    def issue_admin_password_reset(
        self, email: str, *, now: datetime | None = None
    ) -> tuple[dict[str, object], str] | None:
        """En kod för nytt lösenord till kontot med den här adressen.

        Svarar None för en adress som inget konto har. Det lösenord som finns
        slutar inte gälla förrän koden lösts in, så den som begär koder åt
        någon annan kan inte låsa ute hen. En ny kod ersätter den förra.
        """

        try:
            email = normalise_admin_email(email)
        except AdminAccessError:
            return None
        if not email:
            return None
        now = now or datetime.now(timezone.utc)
        code = _new_account_code()
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id FROM admin_users WHERE email = ?", (email,)
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "UPDATE admin_users SET reset_code_digest = ?, reset_expires_at = ?"
                " WHERE user_id = ?",
                (_credential_digest(code), (now + ADMIN_RESET_TTL).isoformat(), row[0]),
            )
        return self.admin_user(str(row[0])) or {}, code

    def redeem_admin_setup(
        self, email: str, code: str, password: str, *, now: datetime | None = None
    ) -> dict[str, object]:
        """Löser in en inbjudan eller en kod för nytt lösenord.

        Båda är engångskoder som leder till samma sak, ett lösenord som
        användaren väljer själv. Efter inlösen gäller ingen av dem, och den som
        satt inloggad med det gamla lösenordet loggas ut.
        """

        _check_admin_password(password)
        try:
            email = normalise_admin_email(email)
        except AdminAccessError:
            raise AdminAccessError("Koden gäller inte") from None
        now = now or datetime.now(timezone.utc)
        # Koden digererades i sin skrivna form när den utfärdades, så indata
        # förs tillbaka dit: streck, mellanslag eller ingenting blir samma kod.
        digest = _credential_digest(_display_code(str(code)))
        salt = secrets.token_bytes(16)
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id, setup_code_digest, setup_expires_at,"
                " reset_code_digest, reset_expires_at FROM admin_users"
                " WHERE email = ?",
                (email,),
            ).fetchone()
            if row is None or not email:
                raise AdminAccessError("Koden gäller inte")
            invitation = row[1] is not None and hmac.compare_digest(bytes(row[1]), digest)
            reset = row[3] is not None and hmac.compare_digest(bytes(row[3]), digest)
            if not invitation and not reset:
                raise AdminAccessError("Koden gäller inte")
            expires = row[2] if invitation else row[4]
            if expires and datetime.fromisoformat(expires) < now:
                raise AdminAccessError(
                    "Koden har gått ut. Be ägaren om en ny." if invitation
                    else "Koden har gått ut. Begär en ny."
                )
            self._connection.execute(
                "UPDATE admin_users SET password_salt = ?, password_digest = ?,"
                " setup_code_digest = NULL, setup_expires_at = NULL,"
                " reset_code_digest = NULL, reset_expires_at = NULL,"
                " must_change_password = 0, updated_at = ? WHERE user_id = ?",
                (salt, _admin_password_digest(password, salt), now.isoformat(), row[0]),
            )
            self._connection.execute("DELETE FROM admin_sessions WHERE user_id = ?", (row[0],))
        return self.admin_user(str(row[0])) or {}

    def delete_admin_user(self, user_id: str) -> None:
        """Den sista ägaren går inte att ta bort.

        En server utan ägare har ingen som kan lägga till en - den vore låst
        för alltid utan att någonting gått sönder.
        """

        with self._lock:
            row = self._connection.execute(
                "SELECT role FROM admin_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                raise AdminAccessError("Användaren finns inte")
            if row[0] == "owner" and self._would_strand_the_server_locked(user_id):
                raise AdminAccessError(
                    "Det måste finnas minst en ägare. Utse någon annan till ägare först."
                )
            self._connection.execute("DELETE FROM admin_users WHERE user_id = ?", (user_id,))
            self._connection.execute("DELETE FROM admin_sessions WHERE user_id = ?", (user_id,))

    def set_admin_user_role(self, user_id: str, role: str) -> dict[str, object]:
        if role not in {"owner", "admin"}:
            raise AdminAccessError("Rollen måste vara ägare eller administratör")
        with self._lock:
            row = self._connection.execute(
                "SELECT role FROM admin_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                raise AdminAccessError("Användaren finns inte")
            # Samma skäl som vid borttagning: en degradering får inte lämna
            # servern utan ägare.
            if row[0] == "owner" and role != "owner" and self._would_strand_the_server_locked(user_id):
                raise AdminAccessError(
                    "Det måste finnas minst en ägare. Utse någon annan till ägare först."
                )
            self._connection.execute(
                "UPDATE admin_users SET role = ?, updated_at = ? WHERE user_id = ?",
                (role, datetime.now(timezone.utc).isoformat(), user_id),
            )
        return self.admin_user(user_id) or {}

    def set_admin_user_password(self, user_id: str, password: str) -> dict[str, object]:
        _check_admin_password(password)
        salt = secrets.token_bytes(16)
        with self._lock:
            if self._connection.execute(
                "SELECT 1 FROM admin_users WHERE user_id = ?", (user_id,)
            ).fetchone() is None:
                raise AdminAccessError("Användaren finns inte")
            self._connection.execute(
                "UPDATE admin_users SET password_salt = ?, password_digest = ?,"
                " updated_at = ?, must_change_password = 0 WHERE user_id = ?",
                (salt, _admin_password_digest(password, salt),
                 datetime.now(timezone.utc).isoformat(), user_id),
            )
            # Ett byte av lösenord ska stänga ute den som satt inloggad med det
            # gamla. Sessionerna för användaren rensas därför.
            self._connection.execute("DELETE FROM admin_sessions WHERE user_id = ?", (user_id,))
        return self.admin_user(user_id) or {}

    def _owner_count_locked(self, exclude_user_id: str | None = None) -> int:
        """Ägare som faktiskt kan logga in.

        En inbjuden ägare har ännu inget lösenord. Räknades hen med kunde den
        sista riktiga ägaren tas bort så länge det låg en obesvarad inbjudan i
        listan - och då hade servern ingen som kunde logga in och bjuda in en
        ny. Koden kan ha gått ut, tappats bort eller aldrig lämnats över.
        """

        row = self._connection.execute(
            "SELECT COUNT(*) FROM admin_users"
            " WHERE role = 'owner' AND password_digest IS NOT NULL"
            " AND user_id IS NOT ?",
            (exclude_user_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    def _would_strand_the_server_locked(self, user_id: str) -> bool:
        """Blir servern utan ägare som kan logga in om user_id försvinner?

        Frågan ställs om den som tas bort eller degraderas, inte om listan i
        stort: att degradera en inbjuden ägare tar inte bort någon som kan
        logga in, och ska inte nekas. Finns det ingen inloggningsbar ägare från
        början - en installation som inte gjorts färdig - blockeras ingenting
        heller. Man kan inte förlora det som inte finns.
        """

        return self._owner_count_locked() >= 1 and self._owner_count_locked(user_id) == 0

    def create_admin_session(
        self,
        email: str,
        password: str,
        *,
        now: datetime | None = None,
        ttl: timedelta = ADMIN_SESSION_TTL,
    ) -> str | None:
        """Logga in med e-postadress och lösenord.

        Adressen är kontot. Ett konto utan adress kommer inte in förrän ägaren
        har gett det en, och ett gammalt användarnamn öppnar ingenting.
        """

        try:
            email = normalise_admin_email(email)
        except AdminAccessError:
            return None
        if not email or len(password) > 256:
            return None
        now = now or datetime.now(timezone.utc)
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id, password_salt, password_digest FROM admin_users WHERE email = ?",
                (email,),
            ).fetchone()
            if row is None or row[1] is None or row[2] is None:
                return None
            if not hmac.compare_digest(_admin_password_digest(password, row[1]), row[2]):
                return None
            token = secrets.token_urlsafe(32)
            self._connection.execute(
                """
                INSERT INTO admin_sessions(session_digest, expires_at, created_at, user_id)
                VALUES (?, ?, ?, ?)
                """,
                (_credential_digest(token), (now + ttl).isoformat(), now.isoformat(), str(row[0])),
            )
        return token

    def authenticate_admin_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        digest = _credential_digest(token)
        with self._lock:
            row = self._connection.execute(
                "SELECT expires_at FROM admin_sessions WHERE session_digest = ?",
                (digest,),
            ).fetchone()
            if row is None:
                return False
            if datetime.fromisoformat(row[0]) < now:
                self._connection.execute(
                    "DELETE FROM admin_sessions WHERE session_digest = ?",
                    (digest,),
                )
                return False
        return True

    def admin_session_user(self, token: str, *, now: datetime | None = None) -> dict[str, object] | None:
        """Vem sessionen tillhör, eller None om den inte gäller.

        En session utan användare kom från den gamla enda inloggningen. Den
        finns inte längre, så en sådan session gäller inte.
        """

        if not self.authenticate_admin_session(token, now=now):
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id FROM admin_sessions WHERE session_digest = ?",
                (_credential_digest(token),),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return self.admin_user(str(row[0]))

    def revoke_admin_session(self, token: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM admin_sessions WHERE session_digest = ?",
                (_credential_digest(token),),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _station_id_locked(self, client_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT station_id FROM clients WHERE client_id = ? AND enabled = 1",
            (client_id,),
        ).fetchone()
        return row[0] if row else None

    def _station_side_locked(self, client_id: str) -> str:
        row = self._connection.execute(
            "SELECT station_side FROM clients WHERE client_id = ? AND enabled = 1 AND station_id IS NOT NULL",
            (client_id,),
        ).fetchone()
        # Only read with a station: every assignment writes its side, so a side
        # left behind by a removal or a new meet is never used.
        return row[0] if row and row[0] in STATION_SIDES else "both"

    def _panel_ids_locked(self, client_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT panel_id FROM client_panels WHERE client_id = ? ORDER BY panel_id",
            (client_id,),
        ).fetchall()
        return tuple(row[0] for row in rows)


class PairingService:
    def __init__(
        self,
        store: IdentityStore,
        valid_panel_ids: set[str],
    ):
        self.store = store
        self.valid_panel_ids = valid_panel_ids
        self._lock = threading.Lock()

    def replace_valid_panels(self, panel_ids: set[str]) -> None:
        with self._lock:
            self.valid_panel_ids = set(panel_ids)

    def pair(
        self,
        *,
        pairing_code: str,
        client_id: str,
        display_name: str,
        kind: DeviceKind,
    ) -> PairingResult:
        client_id = client_id.strip()
        display_name = display_name.strip()
        if not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise InvalidClientError("Enhets-ID måste vara 3–64 tecken")
        if not display_name or len(display_name) > 80:
            raise InvalidClientError("Enheten måste ha ett namn")

        with self._lock:
            grant = self.store.reserve_pairing_code(pairing_code, kind)
            if not set(grant.panel_ids).issubset(self.valid_panel_ids):
                self.store.release_pairing_code(grant.pairing_id)
                raise InvalidPairingCodeError("Koden innehåller en panel som inte längre finns")

            access_token = secrets.token_urlsafe(32)
            try:
                client = self.store.register_client(
                    client_id,
                    display_name,
                    kind,
                    access_token,
                    grant.panel_ids,
                )
            except Exception as error:
                self.store.release_pairing_code(grant.pairing_id)
                raise ProvisioningError("Enheten kunde inte registreras") from error

        return PairingResult(client=client, access_token=access_token)


def _discovered_device_from_row(
    row: tuple[Any, ...],
    panel_ids: tuple[str, ...],
    station_id: str | None,
    station_side: str = "both",
) -> DiscoveredDevice:
    return DiscoveredDevice(
        device_id=row[0],
        device_code=row[1],
        model=row[2],
        firmware_version=row[3],
        last_seen_at=row[4],
        panel_ids=panel_ids,
        station_id=station_id,
        hardware_version=row[5] or "",
        protocol_version=int(row[6] or 1),
        display=DisplayCapability(
            rows=int(row[7] or 2), cols=int(row[8] or 16), charset=row[9] or "ascii"
        ),
        station_side=station_side,
    )


def _normalize_code(code: str) -> str:
    """Koden utan skiljetecken, i versaler.

    Strikt där koden visas, tolerant där den tas emot. Den som läser upp en kod
    i telefon säger inte "bindestreck", och den som skriver av den från en skärm
    väljer själv mellan streck, mellanslag och ingenting alls. Alla tre är samma
    kod, och servern ska inte låtsas något annat.

    Bara skiljetecken och versaler normaliseras - aldrig tecknen själva. Ett O
    blir inte en nolla här. Koderna lottas redan ur ett alfabet utan de paren
    (inget O, inget I), och att gissa åt användaren skulle göra en felskriven kod
    till en som ser rätt ut. Då slutar "Koden gäller inte" vara ett svar man kan
    lita på.
    """

    return "".join(character for character in code.upper() if character.isalnum())


def _new_account_code() -> str:
    """Åtta tecken i två grupper, utan tecken som går att förväxla (0/O, 1/I)."""

    return "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(2))


def clean_admin_display_name(value: object) -> str:
    """Ett namn att visa: blanksteg i följd blir ett, och 1–100 tecken."""

    name = " ".join(str(value or "").split())
    if not 1 <= len(name) <= ADMIN_NAME_MAX:
        raise AdminAccessError(f"Ange ett namn, högst {ADMIN_NAME_MAX} tecken")
    return name


def required_admin_email(value: str) -> str:
    """Kontots adress. Den krävs: utan den kan kontot inte logga in."""

    email = normalise_admin_email(value)
    if not email:
        raise AdminAccessError("Ange en e-postadress")
    return email


def _check_admin_password(password: str) -> None:
    if not 8 <= len(password) <= 256:
        raise AdminAccessError("Lösenordet måste vara 8–256 tecken")


def normalise_admin_email(value: str) -> str:
    """En e-postadress i gemener, eller tom sträng för ingen adress."""

    email = str(value or "").strip().lower()
    if not email:
        return ""
    if len(email) > 254 or not ADMIN_EMAIL_PATTERN.fullmatch(email):
        raise AdminAccessError("E-postadressen ser inte ut som en e-postadress")
    return email


def _display_code(code: str) -> str:
    """Kodens skrivna form: två lika stora grupper med ett streck emellan.

    Samma form för sexsiffriga träffkoder (123-456) som för åttateckens
    inbjudningar (ABCD-EFGH), så fälten kan visa formen utan att veta vilken
    sorts kod de bär.
    """

    code = _normalize_code(code)
    midpoint = len(code) // 2
    return f"{code[:midpoint]}-{code[midpoint:]}"


def _pairing_digest(code: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("utf-8"),
        salt,
        PAIRING_HASH_ITERATIONS,
    )


def _credential_digest(credential: str) -> bytes:
    return hashlib.sha256(credential.encode("utf-8")).digest()


def _admin_password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


def _normalize_device_code(code: str) -> str:
    compact = "".join(character for character in code.upper() if character.isalnum())
    if compact.startswith("TMBOX") and len(compact) > 5:
        return f"TMBOX-{compact[5:]}"
    if compact.startswith("TBX") and len(compact) > 3:
        return f"TBX-{compact[3:]}"
    # The iPhone TMBox's own code, written like a box's: IOS-4F7A2C.
    if compact.startswith("IOS") and len(compact) > 3:
        return f"IOS-{compact[3:]}"
    return compact
