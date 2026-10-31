"""Durable deduplication, read-only reconciliation and real loopback HTTP tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
from pathlib import Path
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tmbox_gateway.integrations.framework import (
    Command, Connection, IntegrationError, IntegrationService, Outcome, Scope,
    ScopeMismatch, SessionExpired,
)
from tmbox_gateway.integrations.http_sandbox import HttpSandboxAdapter
from tmbox_gateway.integrations.journal import SqliteCommandJournal
from tmbox_gateway.integrations.sandbox_http import SandboxServer
from tmbox_gateway.integrations.stub import StubAdapter


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "commands.sqlite3"
        self.scope = Scope("meet-a", "stub", "account-a", "station-a")
        self.command = Command("clear-1", "test-101")

    def journal(self):
        journal = SqliteCommandJournal(self.path)
        self.addCleanup(journal.close)
        return journal

    def test_reservations_and_outcomes_survive_reopening(self):
        first = SqliteCommandJournal(self.path)
        self.assertTrue(first.claim(self.scope, self.command)[0])
        first.close()
        second = self.journal()
        self.assertFalse(second.claim(self.scope, self.command)[0])
        self.assertEqual(Outcome.UNKNOWN, second.get(self.scope, "clear-1")[1].outcome)
        second.record(self.scope, self.command, Outcome.PENDING)
        third = self.journal()
        self.assertEqual(Outcome.PENDING, third.get(self.scope, "clear-1")[1].outcome)
        third.record(self.scope, self.command, Outcome.CONFIRMED)
        self.assertEqual(Outcome.CONFIRMED, second.get(self.scope, "clear-1")[1].outcome)

    def test_scope_boundaries_include_meet_provider_account_and_station(self):
        journal = self.journal()
        journal.claim(self.scope, self.command)
        for field in ("meet_id", "provider_id", "account_id", "station_id"):
            other = replace(self.scope, **{field: "other"})
            self.assertIsNone(journal.get(other, self.command.command_id))
            self.assertTrue(journal.claim(other, self.command)[0])

    def test_conflicts_roll_back_without_replacing_previous_content(self):
        journal = self.journal()
        journal.claim(self.scope, self.command)
        with self.assertRaises(IntegrationError):
            journal.claim(self.scope, replace(self.command, train_id="another"))
        self.assertEqual(self.command, journal.get(self.scope, "clear-1")[0])

    def test_restart_discovers_only_unresolved_commands_in_this_scope(self):
        journal = self.journal()
        journal.claim(self.scope, self.command)
        confirmed = replace(self.command, command_id="confirmed")
        journal.claim(self.scope, confirmed)
        journal.record(self.scope, confirmed, Outcome.CONFIRMED)
        other = replace(self.scope, station_id="other")
        journal.claim(other, replace(self.command, command_id="other"))
        reopened = self.journal()
        self.assertEqual((self.command,), reopened.unresolved(self.scope))

    def test_concurrent_handles_claim_once_and_never_downgrade_results(self):
        journals = [self.journal() for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda j: j.claim(self.scope, self.command), journals))
        self.assertEqual(1, sum(claimed for claimed, _ in claims))
        journals[0].record(self.scope, self.command, Outcome.CONFIRMED)
        result = journals[1].record(self.scope, self.command, Outcome.PENDING)
        self.assertEqual(Outcome.CONFIRMED, result.outcome)

    def test_reservation_failure_prevents_transport_execution(self):
        adapter = StubAdapter()
        service = IntegrationService({"stub": lambda: adapter}, journal=self.journal())
        # The journal handle belongs to this test; close sessions separately.
        service.register(Connection(self.scope, True, True))
        service.connect(self.scope)
        self.addCleanup(service.disconnect, self.scope)
        with patch.object(service._journal, "claim", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                service.submit(self.scope, self.command)
        self.assertEqual(0, adapter.executions)

    def test_uncorrelated_train_state_cannot_resolve_an_unknown_command(self):
        journal = self.journal()
        journal.claim(self.scope, self.command)
        adapter = StubAdapter()
        adapter._trains["test-101"] = "cleared"
        service = IntegrationService({"stub": lambda: adapter}, journal=journal)
        service.register(Connection(self.scope, True, True))
        service.connect(self.scope)
        self.addCleanup(service.disconnect, self.scope)
        self.assertEqual(Outcome.UNKNOWN, service.reconcile(self.scope, "clear-1").outcome)
        self.assertEqual(0, adapter.executions)


class HttpSandboxTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope("meet-a", "http-sandbox", "account-a", "station-a")
        self.other = replace(self.scope, account_id="account-b", station_id="station-b")
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "commands.sqlite3"
        self.sandbox = SandboxServer((self.scope, self.other))
        self.sandbox.__enter__()
        self.addCleanup(self.sandbox.__exit__, None, None, None)
        self.command = Command("clear-1", "test-101")

    def adapter(self, scope):
        return HttpSandboxAdapter(self.sandbox.base_url, scope.account_id,
                                  self.sandbox.test_password(scope.account_id))

    def service(self, scope):
        service = IntegrationService({"http-sandbox": lambda: self.adapter(scope)},
                                     journal=SqliteCommandJournal(self.path))
        self.addCleanup(service.close)
        service.register(Connection(scope, True, True))
        service.connect(scope)
        return service

    def test_read_only_and_explicit_write_preflight_over_http(self):
        service = self.service(self.scope)
        self.assertFalse(service.preflight(self.scope).passed)
        self.assertEqual(0, self.sandbox.executions(self.scope))
        report = service.preflight(self.scope, test_command=self.command)
        self.assertTrue(report.passed)
        self.assertTrue(report.simulated)
        self.assertEqual(1, self.sandbox.executions(self.scope))

    def test_distinct_cookies_and_expired_session_do_not_affect_other_station(self):
        first, second = self.service(self.scope), self.service(self.other)
        self.sandbox.expire_sessions(self.scope)
        with self.assertRaises(SessionExpired):
            first.timetable(self.scope)
        self.assertEqual(Outcome.CONFIRMED, second.submit(self.other, self.command).outcome)
        first.connect(self.scope)
        self.assertEqual("scheduled", first.timetable(self.scope)[0].status)

    def test_server_assignment_cannot_be_overridden_by_client_scope(self):
        service = self.service(self.scope)
        # Login still belongs to account A; client requesting station B is rejected.
        service.register(Connection(self.other, True, True))
        with self.assertRaises(ScopeMismatch):
            service.connect(self.other)
        self.assertEqual("scheduled", service.timetable(self.scope)[0].status)

    def test_confirmed_command_is_not_resent_by_new_client(self):
        first = self.service(self.scope)
        self.assertEqual(Outcome.CONFIRMED, first.submit(self.scope, self.command).outcome)
        second = self.service(self.scope)
        self.assertEqual(Outcome.CONFIRMED, second.submit(self.scope, self.command).outcome)
        self.assertEqual(1, self.sandbox.executions(self.scope))

    def test_lost_response_reconciles_using_receipt_without_resending(self):
        first = self.service(self.scope)
        self.sandbox.lose_next_write_response(self.scope)
        self.assertEqual(Outcome.UNKNOWN, first.submit(self.scope, self.command).outcome)
        second = self.service(self.scope)
        self.assertEqual(Outcome.UNKNOWN, second.submit(self.scope, self.command).outcome)
        self.assertEqual(Outcome.CONFIRMED, second.reconcile(self.scope, "clear-1").outcome)
        self.assertEqual(1, self.sandbox.executions(self.scope))

    def test_pending_command_waits_for_its_own_completion(self):
        service = self.service(self.scope)
        self.sandbox.defer_next_command(self.scope)
        self.assertEqual(Outcome.PENDING, service.submit(self.scope, self.command).outcome)
        # Another command changes train state; it must not confirm the first one.
        service.submit(self.scope, Command("clear-2", "test-101"))
        self.assertEqual(Outcome.PENDING, service.reconcile(self.scope, "clear-1").outcome)
        self.sandbox.complete_command(self.scope, "clear-1")
        self.assertEqual(Outcome.CONFIRMED, service.reconcile(self.scope, "clear-1").outcome)
        self.assertEqual(2, self.sandbox.executions(self.scope))

    def test_missing_receipt_keeps_unknown_reservation_without_sending(self):
        service = self.service(self.scope)
        service._journal.claim(self.scope, self.command)
        self.assertEqual(Outcome.UNKNOWN, service.reconcile(self.scope, "clear-1").outcome)
        self.assertEqual(0, self.sandbox.executions(self.scope))

    def test_historic_confirmation_does_not_certify_current_preflight(self):
        service = self.service(self.scope)
        service._journal.claim(self.scope, self.command)
        service._journal.record(self.scope, self.command, Outcome.CONFIRMED)
        report = service.preflight(self.scope, test_command=self.command)
        self.assertFalse(report.passed)
        self.assertEqual(0, self.sandbox.executions(self.scope))

    def test_timeout_rate_limit_invalid_json_and_redirect_do_not_write(self):
        service = self.service(self.scope)
        session = service._session(self.scope)
        session.adapter._timeout = 0.05
        for kind in ("timeout", "rate_limit", "invalid_json", "redirect"):
            with self.subTest(kind=kind):
                self.sandbox.fail_next_read(self.scope, kind)
                with self.assertRaises(IntegrationError):
                    service.timetable(self.scope)
        self.assertEqual(0, self.sandbox.executions(self.scope))
        other = self.service(self.other)
        self.assertEqual("scheduled", other.timetable(self.other)[0].status)

    def test_reconciliation_cannot_read_another_stations_journal(self):
        self.service(self.scope).submit(self.scope, self.command)
        other = self.service(self.other)
        with self.assertRaises(IntegrationError):
            other.reconcile(self.other, "clear-1")

    def test_separate_clients_claim_once_under_concurrency(self):
        clients = [self.service(self.scope) for _ in range(3)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda service: service.submit(self.scope, self.command), clients))
        self.assertEqual(1, self.sandbox.executions(self.scope))
        self.assertEqual(Outcome.CONFIRMED, clients[0].reconcile(self.scope, "clear-1").outcome)

    def test_transport_rejects_external_origins_and_invalid_timeouts(self):
        for url in ("http://example.invalid:80", "http://localhost:80", "https://127.0.0.1:80",
                    "http://user:pass@127.0.0.1:80", "http://127.0.0.1:80/path"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                HttpSandboxAdapter(url, "account", "password")
        for timeout in (0, -1, float("inf")):
            with self.assertRaises(ValueError):
                HttpSandboxAdapter(self.sandbox.base_url, "account", "password", timeout=timeout)

    def test_wrong_password_is_not_exposed_by_error(self):
        password = "synthetic-secret-do-not-log"
        adapter = HttpSandboxAdapter(self.sandbox.base_url, self.scope.account_id, password)
        with self.assertRaises(SessionExpired) as error:
            adapter.authenticate(Connection(self.scope, True, True))
        self.assertNotIn(password, str(error.exception))

    def test_process_death_after_remote_effect_reconciles_on_restart(self):
        script = '''
import json, os, sys
from tmbox_gateway.integrations.framework import Command, Connection, IntegrationService, Scope
from tmbox_gateway.integrations.http_sandbox import HttpSandboxAdapter
from tmbox_gateway.integrations.journal import SqliteCommandJournal
config = json.load(sys.stdin)
scope = Scope(**config["scope"])
class Adapter(HttpSandboxAdapter):
    def execute(self, command):
        result = super().execute(command)
        if config["crash"]:
            os._exit(17)
        return result
service = IntegrationService({scope.provider_id: lambda: Adapter(
    config["url"], scope.account_id, config["password"])},
    journal=SqliteCommandJournal(config["path"]))
try:
    service.register(Connection(scope, True, True))
    service.connect(scope)
    result = service.submit(scope, Command("clear-1", "test-101"))
    reconciled = service.reconcile_pending(scope)[0]
    print(json.dumps([result.outcome.value, reconciled.outcome.value]))
finally:
    service.close()
'''
        config = {"scope": asdict(self.scope), "url": self.sandbox.base_url,
                  "password": self.sandbox.test_password(self.scope.account_id),
                  "path": str(self.path), "crash": True}
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        crashed = subprocess.run([sys.executable, "-c", script], input=json.dumps(config),
                                 text=True, capture_output=True, env=env, timeout=10)
        self.assertEqual(17, crashed.returncode, crashed.stderr)
        config["crash"] = False
        restarted = subprocess.run([sys.executable, "-c", script], input=json.dumps(config),
                                   text=True, capture_output=True, env=env, timeout=10)
        self.assertEqual(0, restarted.returncode, restarted.stderr)
        self.assertEqual(["unknown", "confirmed"], json.loads(restarted.stdout))
        self.assertEqual(1, self.sandbox.executions(self.scope))


if __name__ == "__main__":
    unittest.main()
