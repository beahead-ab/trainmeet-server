from __future__ import annotations

import unittest
from unittest.mock import patch

from tambox_gateway.local_server import _wait_for_port


class LocalServerStartupTests(unittest.TestCase):
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

