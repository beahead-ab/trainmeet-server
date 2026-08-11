from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tambox_gateway.identity import (
    DeviceKind,
    IdentityStore,
    InvalidPairingCodeError,
    PairingService,
)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = IdentityStore(Path(self.temporary_directory.name) / "identity.db")
        self.service = PairingService(
            self.store,
            {"panel-a", "panel-b"},
        )

    def tearDown(self):
        self.store.close()
        self.temporary_directory.cleanup()

    def test_one_time_code_pairs_client_and_grants_only_selected_panel(self):
        code = self.store.issue_pairing_code(
            ["panel-a"],
            code="12345678",
            allowed_kinds=[DeviceKind.SWIFT_PANEL],
        )

        result = self.service.pair(
            pairing_code=code,
            client_id="swift-test-1",
            display_name="Caspers iPhone",
            kind=DeviceKind.SWIFT_PANEL,
        )

        self.assertEqual(result.client.panel_ids, ("panel-a",))
        self.assertEqual(self.store.panels_for_client("swift-test-1"), ("panel-a",))
        self.assertEqual(self.store.authenticate(result.access_token), result.client)

        with self.assertRaises(InvalidPairingCodeError):
            self.service.pair(
                pairing_code=code,
                client_id="swift-test-2",
                display_name="Annan iPhone",
                kind=DeviceKind.SWIFT_PANEL,
            )

    def test_code_rejects_wrong_device_kind(self):
        code = self.store.issue_pairing_code(
            ["panel-a"],
            code="87654321",
            allowed_kinds=[DeviceKind.ESP32_PANEL],
        )

        with self.assertRaises(InvalidPairingCodeError):
            self.service.pair(
                pairing_code=code,
                client_id="swift-test-1",
                display_name="iPhone",
                kind=DeviceKind.SWIFT_PANEL,
            )

    def test_expired_code_is_rejected(self):
        issued_at = datetime(2026, 8, 10, tzinfo=timezone.utc)
        code = self.store.issue_pairing_code(
            ["panel-a"],
            code="11223344",
            ttl=timedelta(minutes=1),
            now=issued_at,
        )

        with self.assertRaises(InvalidPairingCodeError):
            self.store.reserve_pairing_code(
                code,
                DeviceKind.SWIFT_PANEL,
                now=issued_at + timedelta(minutes=2),
            )

    def test_repairing_rotates_credentials_and_assignments(self):
        first_code = self.store.issue_pairing_code(["panel-a"], code="11112222")
        first = self.service.pair(
            pairing_code=first_code,
            client_id="swift-admin",
            display_name="Admin",
            kind=DeviceKind.SWIFT_ADMIN,
        )
        second_code = self.store.issue_pairing_code(
            ["panel-a", "panel-b"],
            code="33334444",
        )
        second = self.service.pair(
            pairing_code=second_code,
            client_id="swift-admin",
            display_name="Admin iPhone",
            kind=DeviceKind.SWIFT_ADMIN,
        )

        self.assertIsNone(self.store.authenticate(first.access_token))
        self.assertEqual(self.store.authenticate(second.access_token), second.client)
        self.assertEqual(second.client.panel_ids, ("panel-a", "panel-b"))

    def test_physical_tambox_is_discovered_and_assigned_by_printed_code(self):
        discovered = self.store.record_discovery(
            "esp32-a1b2c3",
            "tbx-a7k2",
            model="Bennys Tambox",
            firmware_version="1.0.0",
        )
        self.assertEqual(discovered.device_code, "TBX-A7K2")
        self.assertEqual(discovered.panel_ids, ())

        paired = self.store.assign_discovered_device("TBX A7K2", ("panel-b",))

        self.assertEqual(paired.client_id, "esp32-a1b2c3")
        self.assertEqual(paired.kind, DeviceKind.ESP32_PANEL)
        self.assertEqual(paired.panel_ids, ("panel-b",))
        self.assertEqual(
            self.store.discovered_device("esp32-a1b2c3").panel_ids,
            ("panel-b",),
        )

    def test_admin_password_creates_a_temporary_session(self):
        before = self.store.admin_access_summary()
        self.assertEqual(before["username"], "admin")
        self.assertFalse(before["password_configured"])

        configured = self.store.configure_admin_access("traffadmin", "lokalt-losenord")
        self.assertTrue(configured["password_configured"])
        self.assertIsNone(self.store.create_admin_session("traffadmin", "felaktigt"))

        now = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
        token = self.store.create_admin_session(
            "traffadmin",
            "lokalt-losenord",
            now=now,
            ttl=timedelta(minutes=30),
        )
        self.assertIsNotNone(token)
        self.assertTrue(self.store.authenticate_admin_session(token, now=now))
        self.assertFalse(
            self.store.authenticate_admin_session(token, now=now + timedelta(minutes=31))
        )


if __name__ == "__main__":
    unittest.main()
