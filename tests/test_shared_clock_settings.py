"""Meet-wide presentation and time settings, independent of each browser."""
import unittest
import test_cloud_only_http as fixture
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import DeviceKind, PairedClient
from tmbox_gateway.runtime import SQLiteRuntimeStore


class SharedClockSettingsTests(unittest.TestCase):
    setUp = fixture.CloudOnlyDeliveryTests.setUp
    fetch = fixture.CloudOnlyDeliveryTests.fetch
    connect = fixture.CloudOnlyDeliveryTests.connect

    def test_setting_time_and_speed_preserves_pause_and_updates_every_consumer(self):
        self.connect()
        result = self.app.control_clock(self.admin, {"action": "set", "time": "10:31:45", "speed": 4.3})
        self.assertFalse(result["running"])
        self.assertEqual(result["time"], "10:31:45")
        self.assertEqual(result["speed"], 4.3)
        self.assertEqual(self.app.display_snapshot()["clock"], result)
        self.assertEqual(self.app.engine.snapshot("panel-a")["clock"]["time"], "10:31")
        self.app.control_clock(self.admin, {"action": "start"})
        result = self.app.control_clock(self.admin, {"action": "set", "time": "11:00:00", "speed": 2})
        self.assertTrue(result["running"])
        self.assertEqual(result["speed"], 2)

    def test_invalid_time_or_rate_cannot_partially_change_clock(self):
        self.connect()
        self.app.control_clock(self.admin, {"action": "set", "time": "09:15:00", "speed": 1})
        before = self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone()
        for changes in ({"time": "not-a-time", "speed": 4}, {"speed": float("nan")}, {"speed": float("inf")}, {"speed": 0}):
            with self.assertRaises(HTTPAPIError):
                self.app.control_clock(self.admin, {"action": "set", **changes})
            self.assertEqual(before, self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone())

    def test_appearance_persists_and_survives_same_meet_publication(self):
        self.connect()
        before = self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone()
        result = self.app.control_clock(self.admin, {"action": "appearance", "style": "swedish", "show_seconds": False})
        self.assertEqual(result["style"], "swedish")
        self.assertFalse(result["show_seconds"])
        self.assertEqual(before, self.operations._connection.execute("SELECT * FROM runtime_clock").fetchone())
        reopened = SQLiteRuntimeStore(self.runtime.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.clock_display_settings(self.app._clock_scope()), {"style": "swedish", "show_seconds": False})
        self.offered["publication_id"] = "second"
        self.connect()
        self.assertEqual(self.app.display_snapshot()["clock"]["style"], "swedish")

    def test_station_client_cannot_change_time_or_appearance(self):
        self.connect()
        terminal = PairedClient("terminal", "TKL", DeviceKind.TKL_TERMINAL, ("panel-a",))
        for action in ("set", "appearance"):
            with self.assertRaises(HTTPAPIError) as error:
                self.app.control_clock(terminal, {"action": action, "time": "11:12:13", "style": "swedish", "show_seconds": False})
            self.assertEqual(int(error.exception.status), 403)

    def test_us_clock_uses_same_presentation_without_leaking_eu_preferences(self):
        self.connect()
        self.app.control_clock(self.admin, {"action": "appearance", "style": "swedish", "show_seconds": False})
        self.offered = fixture.us_package()
        self.connect(confirm_meet_change=True)
        self.assertNotEqual(self.app.clock_status(self.admin)["style"], "swedish")
        generation = self.app.lifecycle.selected()["generation"]
        result = self.app.control_clock(self.admin, {"action": "set", "time": "06:30", "speed": 4, "meet_generation": generation})
        self.assertFalse(result["running"])
        self.assertEqual(result["time"][:5], "06:30")
        self.app.control_clock(self.admin, {"action": "appearance", "style": "digital", "show_seconds": True, "meet_generation": generation})
        self.assertEqual(self.app.display_snapshot()["clock"]["style"], "digital")
        self.assertEqual(self.app.clock_status(self.admin)["speed"], 4)

    def test_invalid_appearance_does_not_override_good_settings(self):
        self.connect()
        for style, seconds in (("bad", True), ("swedish", "false"), (None, False)):
            with self.assertRaises(HTTPAPIError):
                self.app.control_clock(self.admin, {"action": "appearance", "style": style, "show_seconds": seconds})
        self.assertEqual(self.runtime.clock_display_settings(self.app._clock_scope()), {})

    def test_invalid_us_settings_do_not_create_or_change_a_session(self):
        self.offered = fixture.us_package()
        self.connect()
        for changes in ({"time": "25:60"}, {"speed": float("nan")}, {"speed": 0}, {"speed": 61}):
            with self.assertRaises(HTTPAPIError):
                self.app.control_clock(self.admin, {"action": "set", **changes})
            self.assertIsNone(self.us.current_session())
        self.app.control_clock(self.admin, {"action": "set", "time": "10:00", "speed": 4})
        before = self.us.current_session()
        with self.assertRaises(HTTPAPIError):
            self.app.control_clock(self.admin, {"action": "set", "time": "bad", "speed": 2})
        self.assertEqual(self.us.current_session(), before)
