"""Transport-independent integration scaffold, isolated from live traffic.

All operations require the full connection scope. Adapter factories must return
fresh instances, with private session storage for each connection. No credentials,
cookies or provider responses belong in compatibility reports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Callable, Protocol


class IntegrationError(Exception):
    """A provider operation could not be completed."""


class SessionExpired(IntegrationError):
    pass


class ScopeMismatch(IntegrationError):
    pass


class Unsupported(IntegrationError):
    pass


class Outcome(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Scope:
    meet_id: str
    provider_id: str
    account_id: str
    station_id: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (
            self.meet_id, self.provider_id, self.account_id, self.station_id
        )):
            raise ValueError("All connection scope fields are required")


@dataclass(frozen=True)
class Connection:
    scope: Scope
    enabled: bool = False
    sandbox: bool = False
    # A reference for a future credential store, never a password or cookie.
    credential_ref: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Train:
    train_id: str
    status: str


@dataclass(frozen=True)
class Command:
    command_id: str
    train_id: str
    action: str = "clear"


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    outcome: Outcome


@dataclass(frozen=True)
class Check:
    name: str
    status: str


@dataclass(frozen=True)
class CompatibilityReport:
    scope: Scope
    checks: tuple[Check, ...]
    simulated: bool

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.status == "passed" for c in self.checks)


class Adapter(Protocol):
    """A single connection's adapter, including its private session lifecycle.

authenticate/identity must return the external identity mapped to TrainMeet IDs.
execute acknowledges acceptance only; success additionally requires readback.
Adapters map expired logins to SessionExpired, unavailable capabilities to
Unsupported, and other transport errors to IntegrationError. close must release
session resources. Do not add automatic mutation retries in a transport.
"""

    simulated: bool
    capabilities: frozenset[str]

    def authenticate(self, connection: Connection) -> Scope: ...
    def identity(self) -> Scope: ...
    def timetable(self) -> tuple[Train, ...]: ...
    def train_status(self, train_id: str) -> str: ...
    def execute(self, command: Command) -> bool: ...
    def command_status(self, command: Command) -> Outcome:
        """Optional capability: correlate this exact command and read back its effect.

An unrelated train status is never sufficient to resolve an unknown command.
Missing receipts return UNKNOWN; this method must never submit or replay writes.
"""
        ...
    def close(self) -> None: ...


class CommandJournal(Protocol):
    def get(self, scope: Scope, command_id: str) -> tuple[Command, CommandResult] | None: ...
    def unresolved(self, scope: Scope) -> tuple[Command, ...]: ...
    def claim(self, scope: Scope, command: Command) -> tuple[bool, CommandResult]: ...
    def record(self, scope: Scope, command: Command, outcome: Outcome) -> CommandResult: ...
    def close(self) -> None: ...


@dataclass
class _Session:
    connection: Connection
    adapter: Adapter = field(repr=False)
    authenticated: bool = False
    lock: RLock = field(default_factory=RLock, repr=False)


class IntegrationService:
    """Opt-in sandbox facade. Never attached to HTTP or live traffic.

Locks serialize each connection independently. An injected file-backed journal
keeps reservations and results across restarts. The default journal is in-memory.
The service owns its journal handle; close it after disconnecting sessions.
"""

    def __init__(self, factories: dict[str, Callable[[], Adapter]],
                 *, journal: CommandJournal | None = None):
        from .journal import SqliteCommandJournal

        self._journal = journal if journal is not None else SqliteCommandJournal()
        self._factories = dict(factories)
        self._sessions: dict[Scope, _Session] = {}
        self._registry_lock = RLock()

    def register(self, connection: Connection) -> None:
        if not connection.enabled:
            raise IntegrationError("Connection is disabled")
        with self._registry_lock:
            if connection.scope in self._sessions:
                raise IntegrationError("Connection already registered")
            factory = self._factories.get(connection.scope.provider_id)
            if factory is None:
                raise Unsupported("Provider is not registered")
            adapter = factory()
            if any(s.adapter is adapter for s in self._sessions.values()):
                raise IntegrationError("Adapter instance must not be shared")
            # Both the in-process and loopback HTTP adapters are simulators.
            if not adapter.simulated or not connection.sandbox:
                adapter.close()
                raise Unsupported("Only sandbox simulation is available")
            self._sessions[connection.scope] = _Session(connection, adapter)

    def _session(self, scope: Scope) -> _Session:
        with self._registry_lock:
            try:
                return self._sessions[scope]
            except KeyError:
                raise ScopeMismatch("No connection for the requested scope") from None

    @staticmethod
    def _verify(session: _Session) -> None:
        if not session.authenticated:
            raise SessionExpired("Authentication required")
        try:
            if session.adapter.identity() != session.connection.scope:
                session.authenticated = False
                raise ScopeMismatch("External identity does not match connection scope")
        except SessionExpired:
            session.authenticated = False
            raise

    def connect(self, scope: Scope) -> None:
        session = self._session(scope)
        with session.lock:
            session.authenticated = False
            session.adapter.close()
            try:
                if session.adapter.authenticate(session.connection) != scope:
                    raise ScopeMismatch("External identity does not match connection scope")
                session.authenticated = True
            except Exception:
                session.adapter.close()
                raise

    def disconnect(self, scope: Scope) -> None:
        session = self._session(scope)
        with session.lock:
            session.authenticated = False
            session.adapter.close()

    def close(self) -> None:
        """Release sessions and the owned journal; caller stops new work first."""
        try:
            with self._registry_lock:
                scopes = tuple(self._sessions)
            for scope in scopes:
                self.disconnect(scope)
        finally:
            self._journal.close()

    def timetable(self, scope: Scope) -> tuple[Train, ...]:
        session = self._session(scope)
        with session.lock:
            self._verify(session)
            if "timetable" not in session.adapter.capabilities:
                raise Unsupported("Timetable is not supported")
            return session.adapter.timetable()

    def submit(self, scope: Scope, command: Command) -> CommandResult:
        session = self._session(scope)
        if not command.command_id.strip() or not command.train_id.strip():
            raise ValueError("Command and train IDs are required")
        with session.lock:
            self._verify(session)
            if command.action != "clear" or not {"timetable", "status", "clear"}.issubset(
                session.adapter.capabilities
            ):
                raise Unsupported("Command is not supported")
            previous = self._journal.get(scope, command.command_id)
            if previous:
                if previous[0] != command:
                    raise IntegrationError("Command ID already used for different content")
                return previous[1]
            # Check train membership before any mutation.
            if command.train_id not in {t.train_id for t in session.adapter.timetable()}:
                raise ScopeMismatch("Train is not available in this connection")
            # Reservation commits before execute. A second service/process using
            # the same journal sees UNKNOWN and must not send the command again.
            claimed, result = self._journal.claim(scope, command)
            if not claimed:
                return result
            try:
                accepted = session.adapter.execute(command)
                if accepted:
                    result = self._journal.record(scope, command, Outcome.PENDING)
                    if "command_status" in session.adapter.capabilities:
                        outcome = session.adapter.command_status(command)
                        if not isinstance(outcome, Outcome):
                            raise IntegrationError("Invalid command outcome")
                        if outcome == Outcome.CONFIRMED:
                            result = CommandResult(command.command_id, Outcome.CONFIRMED)
                    elif session.adapter.train_status(command.train_id) == "cleared":
                        result = CommandResult(command.command_id, Outcome.CONFIRMED)
            except SessionExpired:
                session.authenticated = False
            except IntegrationError:
                # An interrupted write may have succeeded. Never replay blindly.
                pass
            return self._journal.record(scope, command, result.outcome)

    def reconcile(self, scope: Scope, command_id: str) -> CommandResult:
        """Read-only reconciliation of a durable command; never retransmits it."""
        session = self._session(scope)
        with session.lock:
            self._verify(session)
            previous = self._journal.get(scope, command_id)
            if previous is None:
                raise IntegrationError("No command in this connection")
            command, result = previous
            if result.outcome == Outcome.CONFIRMED:
                return result
            if "command_status" not in session.adapter.capabilities:
                return result
            try:
                outcome = session.adapter.command_status(command)
                if not isinstance(outcome, Outcome):
                    raise IntegrationError("Invalid command outcome")
            except SessionExpired:
                session.authenticated = False
                return result
            except IntegrationError:
                return result
            return self._journal.record(scope, command, outcome)

    def reconcile_pending(self, scope: Scope) -> tuple[CommandResult, ...]:
        """Discover outstanding commands after restart and check them without writes."""
        session = self._session(scope)
        with session.lock:
            self._verify(session)
            return tuple(self.reconcile(scope, command.command_id)
                         for command in self._journal.unresolved(scope))

    def preflight(self, scope: Scope, *, test_command: Command | None = None) -> CompatibilityReport:
        """Read-only by default; an explicit sandbox command enables a write check.

Passing results describe only the adapter's declared current capabilities. They
do not certify reconnect, concurrency or all future provider features; those are
part of the executable adapter contract tests and provider qualification.
"""
        session = self._session(scope)
        checks: list[Check] = []
        with session.lock:
            try:
                self._verify(session)
                checks.extend((Check("session", "passed"), Check("scope", "passed")))
            except IntegrationError:
                return CompatibilityReport(scope, (Check("session_and_scope", "failed"),),
                                           session.adapter.simulated)
            for name in ("timetable", "status", "clear"):
                if name not in session.adapter.capabilities:
                    checks.append(Check(name, "unsupported"))
                    continue
                if name == "clear":
                    # A declared capability is not evidence of a working write.
                    checks.append(Check("clear", "not_tested"))
                    continue
                try:
                    trains = session.adapter.timetable()
                    if name == "status":
                        if not trains:
                            checks.append(Check(name, "not_tested"))
                            continue
                        for train in trains:
                            session.adapter.train_status(train.train_id)
                    checks.append(Check(name, "passed"))
                except SessionExpired:
                    session.authenticated = False
                    checks.append(Check(name, "failed"))
                except Unsupported:
                    checks.append(Check(name, "unsupported"))
                except IntegrationError:
                    checks.append(Check(name, "failed"))
            if test_command is not None:
                # Registration currently enforces sandbox-only operation as well.
                if not session.connection.sandbox:
                    raise Unsupported("Write checks require a sandbox")
                try:
                    result = self.submit(scope, test_command)
                    confirmed = result.outcome == Outcome.CONFIRMED
                    # A historic journal result alone cannot certify this test run.
                    if confirmed and "command_status" in session.adapter.capabilities:
                        current = session.adapter.command_status(test_command)
                        confirmed = isinstance(current, Outcome) and current == Outcome.CONFIRMED
                    elif confirmed:
                        confirmed = session.adapter.train_status(test_command.train_id) == "cleared"
                    status = "passed" if confirmed else "failed"
                except IntegrationError:
                    status = "failed"
                checks = [c for c in checks if c.name != "clear"]
                checks.append(Check("clear", status))
        return CompatibilityReport(scope, tuple(checks), session.adapter.simulated)
