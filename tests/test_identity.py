from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tmbox_gateway.identity import (
    DeviceKind,
    DisplayCapability,
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

    def test_code_without_ttl_never_expires(self):
        issued_at = datetime(2026, 8, 10, tzinfo=timezone.utc)
        code = self.store.issue_pairing_code(
            ["panel-a"],
            code="55667788",
            ttl=None,
            now=issued_at,
        )

        far_future = issued_at + timedelta(days=3650)
        grant = self.store.reserve_pairing_code(code, DeviceKind.SWIFT_PANEL, now=far_future)

        self.assertEqual(grant.panel_ids, ("panel-a",))

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

    def test_a_box_is_assigned_one_station_and_keeps_what_it_can_render(self):
        discovered = self.store.record_discovery(
            "TMBOX-7A42F1",
            "TMBOX-7A42F1",
            model="TMBox ESP32-S3",
            firmware_version="0.3.0",
            hardware_version="esp32-s3",
            protocol_version=2,
            display=DisplayCapability(rows=4, cols=20, charset="cgram"),
        )
        self.assertEqual(discovered.device_code, "TMBOX-7A42F1")
        self.assertIsNone(discovered.station_id)
        self.assertEqual(discovered.display.cols, 20)

        paired = self.store.assign_discovered_device(
            "tmbox 7a42f1", station_id="st-cda"
        )

        self.assertEqual(paired.station_id, "st-cda")
        self.assertEqual(paired.panel_ids, ())
        self.assertEqual(self.store.station_for_client("TMBOX-7A42F1"), "st-cda")
        stored = self.store.discovered_device("TMBOX-7A42F1")
        self.assertEqual(stored.station_id, "st-cda")
        self.assertEqual(stored.protocol_version, 2)
        self.assertEqual(stored.hardware_version, "esp32-s3")

    def test_two_boxes_can_share_one_station(self):
        for suffix in ("A", "B"):
            self.store.record_discovery(f"TMBOX-ROOM{suffix}", f"TMBOX-ROOM{suffix}")
            self.store.assign_discovered_device(f"TMBOX-ROOM{suffix}", station_id="st-cda")

        assigned = [
            device.device_id
            for device in self.store.discovered_devices()
            if device.station_id == "st-cda"
        ]
        self.assertEqual(sorted(assigned), ["TMBOX-ROOMA", "TMBOX-ROOMB"])

    def test_a_nonsense_display_claim_falls_back_to_the_smallest_geometry(self):
        self.store.record_discovery(
            "TMBOX-ODD",
            "TMBOX-ODD",
            display=DisplayCapability.parse({"rows": 7, "cols": 99, "charset": "runes"}),
        )

        display = self.store.discovered_device("TMBOX-ODD").display
        self.assertEqual((display.rows, display.cols, display.charset), (2, 16, "ascii"))

    def test_physical_tmbox_is_discovered_and_assigned_by_printed_code(self):
        discovered = self.store.record_discovery(
            "esp32-a1b2c3",
            "tbx-a7k2",
            model="Bennys TMBox",
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
        self.assertEqual(before["username"], "")
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
