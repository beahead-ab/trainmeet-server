"""Behavioral contract for the opt-in external integration scaffold."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
import unittest

from tmbox_gateway.integrations.framework import (
    Command, Connection, IntegrationError, IntegrationService, Outcome,
    Scope, ScopeMismatch, SessionExpired, Unsupported,
)
from tmbox_gateway.integrations.stub import StubAdapter


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope("meet-a", "stub", "account-a", "station-a")
        self.adapters = []

        def factory():
            adapter = StubAdapter()
            self.adapters.append(adapter)
            return adapter

        self.service = IntegrationService({"stub": factory})
        self.add(self.scope)

    def add(self, scope):
        self.service.register(Connection(scope, enabled=True, sandbox=True))
        self.service.connect(scope)

    def test_disabled_by_default(self):
        with self.assertRaisesRegex(IntegrationError, "disabled"):
            self.service.register(Connection(replace(self.scope, station_id="other")))

    def test_scope_requires_all_fields(self):
        with self.assertRaises(ValueError):
            replace(self.scope, account_id=" ")

    def test_unknown_provider_and_real_transport_cannot_be_activated(self):
        with self.assertRaises(Unsupported):
            self.service.register(Connection(replace(self.scope, provider_id="unknown"), True, True))
        adapter = StubAdapter()
        adapter.simulated = False
        service = IntegrationService({"stub": lambda: adapter})
        with self.assertRaises(Unsupported):
            service.register(Connection(self.scope, True, True))

    def test_stub_requires_sandbox(self):
        service = IntegrationService({"stub": StubAdapter})
        with self.assertRaises(Unsupported):
            service.register(Connection(self.scope, enabled=True))

    def test_same_adapter_cannot_share_session_between_connections(self):
        service = IntegrationService({"stub": lambda: self.adapters[0]})
        service.register(Connection(self.scope, True, True))
        with self.assertRaises(IntegrationError):
            service.register(Connection(replace(self.scope, station_id="other"), True, True))

    def test_duplicate_registration_preserves_existing_session(self):
        with self.assertRaises(IntegrationError):
            self.service.register(Connection(self.scope, True, True))
        self.assertEqual("scheduled", self.service.timetable(self.scope)[0].status)

    def test_station_account_and_meet_are_separate_boundaries(self):
        scopes = [replace(self.scope, **{field: "other"})
                  for field in ("station_id", "account_id", "meet_id")]
        for scope in scopes:
            self.add(scope)
        self.service.submit(self.scope, Command("clear-1", "test-101"))
        for scope in scopes:
            self.assertEqual("scheduled", self.service.timetable(scope)[0].status)

    def test_unregistered_scope_cannot_submit(self):
        with self.assertRaises(ScopeMismatch):
            self.service.submit(replace(self.scope, station_id="other"), Command("1", "test-101"))
        self.assertEqual(0, self.adapters[0].executions)

    def test_external_scope_mismatch_blocks_read_and_write(self):
        self.adapters[0]._scope = replace(self.scope, account_id="other")
        with self.assertRaises(ScopeMismatch):
            self.service.submit(self.scope, Command("1", "test-101"))
        with self.assertRaises(SessionExpired):
            self.service.timetable(self.scope)
        self.assertEqual(0, self.adapters[0].executions)

    def test_expiration_does_not_interrupt_other_station_and_can_reconnect(self):
        other = replace(self.scope, station_id="other")
        self.add(other)
        self.adapters[0].close()
        report = self.service.preflight(self.scope)
        self.assertFalse(report.passed)
        self.assertEqual("scheduled", self.service.timetable(other)[0].status)
        self.service.connect(self.scope)
        self.assertEqual("scheduled", self.service.timetable(self.scope)[0].status)

    def test_read_only_preflight_never_certifies_untested_writes(self):
        report = self.service.preflight(self.scope)
        self.assertTrue(report.simulated)
        self.assertFalse(report.passed)
        self.assertIn(("clear", "not_tested"), [(c.name, c.status) for c in report.checks])
        self.assertEqual(0, self.adapters[0].executions)

    def test_explicit_sandbox_preflight_verifies_write_and_readback(self):
        report = self.service.preflight(self.scope, test_command=Command("probe", "test-101"))
        self.assertTrue(report.passed)
        self.assertTrue(report.simulated)
        self.assertEqual(1, self.adapters[0].executions)

    def test_unsupported_capability_is_reported_and_blocked(self):
        self.adapters[0].capabilities = frozenset({"timetable", "status"})
        report = self.service.preflight(self.scope)
        self.assertIn("unsupported", [c.status for c in report.checks])
        with self.assertRaises(Unsupported):
            self.service.submit(self.scope, Command("1", "test-101"))

    def test_unknown_train_or_action_cannot_mutate(self):
        with self.assertRaises(ScopeMismatch):
            self.service.submit(self.scope, Command("1", "missing"))
        with self.assertRaises(Unsupported):
            self.service.submit(self.scope, Command("2", "test-101", "delete"))
        self.assertEqual(0, self.adapters[0].executions)

    def test_missing_readback_capability_blocks_writes(self):
        self.adapters[0].capabilities = frozenset({"timetable", "clear"})
        with self.assertRaises(Unsupported):
            self.service.submit(self.scope, Command("1", "test-101"))
        self.assertEqual(0, self.adapters[0].executions)

    def test_duplicate_commands_execute_once_even_concurrently(self):
        command = Command("1", "test-101")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.service.submit(self.scope, command), range(8)))
        self.assertTrue(all(r.outcome == Outcome.CONFIRMED for r in results))
        self.assertEqual(1, self.adapters[0].executions)
        self.service.connect(self.scope)
        self.service.submit(self.scope, command)
        self.assertEqual(1, self.adapters[0].executions)
        with self.assertRaises(IntegrationError):
            self.service.submit(self.scope, replace(command, train_id="different"))

    def test_acceptance_without_readback_is_pending(self):
        self.adapters[0].train_status = lambda _: "scheduled"
        result = self.service.submit(self.scope, Command("1", "test-101"))
        self.assertEqual(Outcome.PENDING, result.outcome)

    def test_interrupted_write_is_unknown_and_is_not_replayed(self):
        adapter = self.adapters[0]
        original = adapter.execute

        def interrupted(command):
            original(command)
            raise IntegrationError("Simulated response loss")

        adapter.execute = interrupted
        command = Command("1", "test-101")
        result = self.service.submit(self.scope, command)
        self.assertEqual(Outcome.UNKNOWN, result.outcome)
        self.service.connect(self.scope)
        self.assertEqual(result, self.service.submit(self.scope, command))
        self.assertEqual(1, adapter.executions)

    def test_failed_check_does_not_publish_provider_error_or_credentials(self):
        def failed():
            raise IntegrationError("secret-cookie=do-not-publish")

        self.adapters[0].timetable = failed
        report = self.service.preflight(self.scope)
        self.assertFalse(report.passed)
        self.assertNotIn("secret-cookie", repr(report))
        self.assertNotIn("private-reference", repr(Connection(self.scope, credential_ref="private-reference")))

    def test_blocked_connection_does_not_block_another_station(self):
        other = replace(self.scope, station_id="other")
        self.add(other)
        entered, release = Event(), Event()
        original = self.adapters[0].execute

        def blocked(command):
            entered.set()
            if not release.wait(3):
                raise IntegrationError("Test timeout")
            return original(command)

        self.adapters[0].execute = blocked
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.service.submit, self.scope, Command("1", "test-101"))
            try:
                self.assertTrue(entered.wait(2))
                independent = pool.submit(self.service.submit, other, Command("1", "test-101"))
                self.assertEqual(Outcome.CONFIRMED, independent.result(timeout=2).outcome)
            finally:
                release.set()
            self.assertEqual(Outcome.CONFIRMED, pending.result(timeout=2).outcome)


if __name__ == "__main__":
    unittest.main()
