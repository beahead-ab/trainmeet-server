from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tambox_gateway.local_server import (
    _reset_operational_state,
    _reset_server_state,
    _wait_for_port,
)


class LocalServerStartupTests(unittest.TestCase):
    def test_remote_reset_preserves_admin_session_and_server_name(self):
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            database = state_directory / "tambox.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE engine_state (value TEXT);
                CREATE TABLE runtime_clock (value TEXT);
                CREATE TABLE train_positions (value TEXT);
                CREATE TABLE tkl_shifts (value TEXT);
                CREATE TABLE tkl_movement_states (value TEXT);
                CREATE TABLE tkl_events (value TEXT);
                CREATE TABLE train_readiness (value TEXT);
                CREATE TABLE local_configuration_current (value TEXT);
                CREATE TABLE local_configuration_revisions (value TEXT);
                CREATE TABLE cloud_change_outbox (value TEXT);
                CREATE TABLE runtime_publications (value TEXT);
                CREATE TABLE pairing_codes (value TEXT);
                CREATE TABLE client_panels (value TEXT);
                CREATE TABLE discovered_devices (value TEXT);
                CREATE TABLE clients (kind TEXT);
                CREATE TABLE runtime_settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE admin_access (username TEXT);
                CREATE TABLE admin_sessions (token TEXT);
                """
            )
            for table in (
                "engine_state", "runtime_clock", "train_positions", "tkl_shifts",
                "tkl_movement_states", "tkl_events", "train_readiness",
                "local_configuration_current", "local_configuration_revisions",
                "cloud_change_outbox", "runtime_publications", "pairing_codes",
                "client_panels", "discovered_devices",
            ):
                connection.execute(f"INSERT INTO {table} VALUES ('data')")
            connection.execute("INSERT INTO clients VALUES ('web_admin')")
            connection.execute("INSERT INTO clients VALUES ('esp32_panel')")
            connection.execute("INSERT INTO runtime_settings VALUES ('server_name', 'Min server')")
            connection.execute("INSERT INTO runtime_settings VALUES ('central_link_token', 'hemlig')")
            connection.execute("INSERT INTO admin_access VALUES ('admin')")
            connection.execute("INSERT INTO admin_sessions VALUES ('session')")
            connection.commit()
            connection.close()
            (state_directory / "connection-code.txt").write_text("123456", encoding="utf-8")

            _reset_operational_state(database, state_directory)

            connection = sqlite3.connect(database)
            self.assertEqual(("admin",), connection.execute("SELECT username FROM admin_access").fetchone())
            self.assertEqual(("session",), connection.execute("SELECT token FROM admin_sessions").fetchone())
            self.assertEqual(("Min server",), connection.execute("SELECT value FROM runtime_settings WHERE key='server_name'").fetchone())
            self.assertEqual([("web_admin",)], connection.execute("SELECT kind FROM clients").fetchall())
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM runtime_publications").fetchone()[0])
            self.assertIsNone(connection.execute("SELECT value FROM runtime_settings WHERE key='central_link_token'").fetchone())
            connection.close()
            self.assertFalse((state_directory / "connection-code.txt").exists())

    def test_factory_reset_removes_runtime_identity_but_keeps_software_files(self):
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            database = state_directory / "tambox.db"
            for path in (
                database,
                state_directory / "tambox.db-wal",
                state_directory / "tambox.db-shm",
                state_directory / "connection-code.txt",
                state_directory / "update-status.json",
            ):
                path.write_text("test", encoding="utf-8")

            _reset_server_state(database, state_directory)

            self.assertFalse(database.exists())
            self.assertFalse((state_directory / "tambox.db-wal").exists())
            self.assertFalse((state_directory / "tambox.db-shm").exists())
            self.assertFalse((state_directory / "connection-code.txt").exists())
            self.assertTrue((state_directory / "update-status.json").exists())

    @patch("tambox_gateway.local_server.time.sleep")
    @patch(
        "tambox_gateway.local_server._port_is_open",
        side_effect=[False, False, True],
    )
    def test_external_broker_can_start_after_server_container(
        self,
        port_is_open,
        sleep,
    ) -> None:
        self.assertTrue(_wait_for_port("mosquitto", 1883, 2))
        self.assertEqual(port_is_open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("tambox_gateway.local_server._port_is_open", return_value=False)
    def test_zero_wait_performs_one_final_broker_check(self, port_is_open) -> None:
        self.assertFalse(_wait_for_port("mosquitto", 1883, 0))
        port_is_open.assert_called_once_with("mosquitto", 1883)


if __name__ == "__main__":
    unittest.main()
