from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path


PAIRING_HASH_ITERATIONS = 210_000
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{3,64}$")
ADMIN_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._@+-]{3,64}$")
ADMIN_SESSION_TTL = timedelta(hours=12)


class DeviceKind(StrEnum):
    SWIFT_PANEL = "swift_panel"
    SWIFT_ADMIN = "swift_admin"
    WEB_ADMIN = "web_admin"
    ESP32_PANEL = "esp32_panel"


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
class PairedClient:
    client_id: str
    display_name: str
    kind: DeviceKind
    panel_ids: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveredDevice:
    device_id: str
    device_code: str
    model: str
    firmware_version: str
    last_seen_at: str
    panel_ids: tuple[str, ...]


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
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_access (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                username TEXT NOT NULL,
                password_salt BLOB,
                password_digest BLOB,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_sessions (
                session_digest BLOB PRIMARY KEY,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT INTO admin_access(singleton, username, updated_at)
            VALUES (1, 'admin', ?)
            ON CONFLICT(singleton) DO NOTHING
            """,
            (now,),
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
        ttl: timedelta = timedelta(minutes=15),
        max_uses: int = 1,
        label: str | None = None,
        code: str | None = None,
        now: datetime | None = None,
    ) -> str:
        if not panel_ids:
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
                    (now + ttl).isoformat(),
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

    def revoke_pairing_codes(self, *, label: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM pairing_codes WHERE label = ?",
                (label,),
            )

    def register_client(
        self,
        client_id: str,
        display_name: str,
        kind: DeviceKind,
        credential: str,
        panel_ids: tuple[str, ...],
        *,
        now: datetime | None = None,
    ) -> PairedClient:
        now = now or datetime.now(timezone.utc)
        digest = _credential_digest(credential)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                created_at = self._connection.execute(
                    "SELECT created_at FROM clients WHERE client_id = ?",
                    (client_id,),
                ).fetchone()
                self._connection.execute(
                    """
                    INSERT INTO clients (
                        client_id, display_name, kind, credential_digest,
                        enabled, created_at, last_paired_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(client_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        kind = excluded.kind,
                        credential_digest = excluded.credential_digest,
                        enabled = 1,
                        last_paired_at = excluded.last_paired_at
                    """,
                    (
                        client_id,
                        display_name,
                        kind.value,
                        digest,
                        created_at[0] if created_at else now.isoformat(),
                        now.isoformat(),
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
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

        return PairedClient(client_id, display_name, kind, tuple(sorted(set(panel_ids))))

    def authenticate(self, credential: str) -> PairedClient | None:
        digest = _credential_digest(credential)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT client_id, display_name, kind
                FROM clients
                WHERE credential_digest = ? AND enabled = 1
                """,
                (digest,),
            ).fetchone()
            if row is None:
                return None
            panels = self._panel_ids_locked(row[0])
        return PairedClient(row[0], row[1], DeviceKind(row[2]), panels)

    def client(self, client_id: str) -> PairedClient | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT client_id, display_name, kind
                FROM clients
                WHERE client_id = ? AND enabled = 1
                """,
                (client_id,),
            ).fetchone()
            if row is None:
                return None
            panels = self._panel_ids_locked(client_id)
        return PairedClient(row[0], row[1], DeviceKind(row[2]), panels)

    def panels_for_client(self, client_id: str) -> tuple[str, ...]:
        client = self.client(client_id)
        return client.panel_ids if client else ()

    def enabled_clients(self) -> tuple[PairedClient, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT client_id, display_name, kind
                FROM clients
                WHERE enabled = 1
                ORDER BY client_id
                """
            ).fetchall()
            return tuple(
                PairedClient(row[0], row[1], DeviceKind(row[2]), self._panel_ids_locked(row[0]))
                for row in rows
            )

    def disable_client(self, client_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE clients SET enabled = 0 WHERE client_id = ?",
                (client_id,),
            )

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
        model: str = "Tambox",
        firmware_version: str = "unknown",
        now: datetime | None = None,
    ) -> DiscoveredDevice:
        device_id = device_id.strip()
        device_code = _normalize_device_code(device_code)
        if not CLIENT_ID_PATTERN.fullmatch(device_id):
            raise InvalidClientError("Ogiltigt enhets-ID från Tambox")
        if len(device_code) < 4 or len(device_code) > 24:
            raise InvalidClientError("Ogiltig kod från Tambox")
        now = now or datetime.now(timezone.utc)
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO discovered_devices (
                    device_id, device_code, model, firmware_version,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    device_code = excluded.device_code,
                    model = excluded.model,
                    firmware_version = excluded.firmware_version,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    device_id,
                    device_code,
                    model[:80],
                    firmware_version[:40],
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return self.discovered_device(device_id)

    def discovered_device(self, device_id: str) -> DiscoveredDevice:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT device_id, device_code, model, firmware_version, last_seen_at
                FROM discovered_devices WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            if row is None:
                raise InvalidClientError("Tambox-enheten har ännu inte hittats")
            panels = self._panel_ids_locked(device_id)
        return DiscoveredDevice(row[0], row[1], row[2], row[3], row[4], panels)

    def discovered_devices(self) -> tuple[DiscoveredDevice, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT device_id, device_code, model, firmware_version, last_seen_at
                FROM discovered_devices ORDER BY last_seen_at DESC
                """
            ).fetchall()
            return tuple(
                DiscoveredDevice(row[0], row[1], row[2], row[3], row[4], self._panel_ids_locked(row[0]))
                for row in rows
            )

    def assign_discovered_device(
        self,
        device_code: str,
        panel_ids: tuple[str, ...],
        *,
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
            raise InvalidClientError("Ingen inkopplad Tambox har den koden")
        internal_credential = f"local-device:{row[0]}"
        return self.register_client(
            row[0],
            f"{row[1]} {normalized}",
            DeviceKind.ESP32_PANEL,
            internal_credential,
            panel_ids,
            now=now,
        )

    def admin_access_summary(self) -> dict[str, object]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT username, password_digest IS NOT NULL, updated_at
                FROM admin_access WHERE singleton = 1
                """
            ).fetchone()
        return {
            "username": row[0],
            "password_configured": bool(row[1]),
            "updated_at": row[2],
        }

    def configure_admin_access(
        self,
        username: str,
        password: str | None = None,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        username = username.strip()
        if not ADMIN_USERNAME_PATTERN.fullmatch(username):
            raise AdminAccessError(
                "Användarnamnet måste vara 3–64 tecken och får innehålla bokstäver, siffror, punkt, bindestreck och @"
            )
        if password is not None and not 8 <= len(password) <= 256:
            raise AdminAccessError("Lösenordet måste vara 8–256 tecken")

        now = now or datetime.now(timezone.utc)
        salt = secrets.token_bytes(16) if password is not None else None
        digest = _admin_password_digest(password, salt) if password is not None else None
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if password is None:
                    self._connection.execute(
                        """
                        UPDATE admin_access
                        SET username = ?, updated_at = ?
                        WHERE singleton = 1
                        """,
                        (username, now.isoformat()),
                    )
                else:
                    self._connection.execute(
                        """
                        UPDATE admin_access
                        SET username = ?, password_salt = ?, password_digest = ?, updated_at = ?
                        WHERE singleton = 1
                        """,
                        (username, salt, digest, now.isoformat()),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.admin_access_summary()

    def create_admin_session(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        ttl: timedelta = ADMIN_SESSION_TTL,
    ) -> str | None:
        username = str(username).strip()
        if not ADMIN_USERNAME_PATTERN.fullmatch(username) or len(password) > 256:
            return None
        now = now or datetime.now(timezone.utc)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT username, password_salt, password_digest
                FROM admin_access WHERE singleton = 1
                """
            ).fetchone()
            if (
                row is None
                or row[1] is None
                or row[2] is None
                or not hmac.compare_digest(username.encode("utf-8"), row[0].encode("utf-8"))
                or not hmac.compare_digest(_admin_password_digest(password, row[1]), row[2])
            ):
                return None
            token = secrets.token_urlsafe(32)
            self._connection.execute(
                """
                INSERT INTO admin_sessions(session_digest, expires_at, created_at)
                VALUES (?, ?, ?)
                """,
                (_credential_digest(token), (now + ttl).isoformat(), now.isoformat()),
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

    def revoke_admin_session(self, token: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM admin_sessions WHERE session_digest = ?",
                (_credential_digest(token),),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

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


def _normalize_code(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())


def _display_code(code: str) -> str:
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
    if compact.startswith("TBX") and len(compact) > 3:
        return f"TBX-{compact[3:]}"
    return compact
