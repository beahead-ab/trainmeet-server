from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tambox_gateway.local_server import _reset_server_state, _wait_for_port


class LocalServerStartupTests(unittest.TestCase):
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
