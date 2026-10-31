"""Deterministic sandbox adapter. No network, secrets or runtime traffic access."""
from __future__ import annotations

from .framework import Command, Connection, Outcome, Scope, SessionExpired, Train, Unsupported


class StubAdapter:
    simulated = True
    capabilities = frozenset({"timetable", "status", "clear"})

    def __init__(self):
        self._scope: Scope | None = None
        self._trains: dict[str, str] = {"test-101": "scheduled"}
        self.executions = 0

    def authenticate(self, connection: Connection) -> Scope:
        self._scope = connection.scope
        return self._scope

    def identity(self) -> Scope:
        if self._scope is None:
            raise SessionExpired("Sandbox session expired")
        return self._scope

    def timetable(self) -> tuple[Train, ...]:
        self.identity()
        return tuple(Train(train_id, status) for train_id, status in self._trains.items())

    def train_status(self, train_id: str) -> str:
        self.identity()
        return self._trains[train_id]

    def execute(self, command: Command) -> bool:
        self.identity()
        self.executions += 1
        self._trains[command.train_id] = "cleared"
        return True

    def close(self) -> None:
        self._scope = None

    def command_status(self, command: Command) -> Outcome:
        raise Unsupported("The basic stub does not retain correlated command receipts")
