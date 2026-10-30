"""Provider contract from Lovable, exercised with no real provider side effects."""
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

import test_cloud_only_http as fixture
from tmbox_gateway.external_clock import (DEFAULT_SETTINGS, ExternalClock, FastClockError,
                                          parse_status, provider_request, validate_settings)
from tmbox_gateway.http_server import HTTPAPIError
from tmbox_gateway.identity import DeviceKind, PairedClient
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.us import USError


def provider_status(**changes):
    return {"name": "Test", "time": "06:12:34", "speed": 4, "isRunning": True,
            "isPaused": False, "isUnavailable": False, "weekday": "Saturday", **changes}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.reply = provider_status()
        self.calls = []
        def transport(settings, action="time", reason=""):
            self.calls.append((settings.copy(), action, reason))
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        self.clock = ExternalClock(transport, lambda: self.now)
        self.settings = {**DEFAULT_SETTINGS, "source": "fastclock", "clock_name": "Test"}
        self.clock.configure("eu:one", self.settings)

    def test_read_interpolation_pause_and_resume(self):
        self.assertTrue(self.clock.poll())
        self.now += 1
        self.assertEqual(self.clock.status()["time"], "06:12:38")
        self.assertFalse(self.clock.poll())
        self.assertEqual(len(self.calls), 1)
        self.reply = provider_status(time="09:00", isPaused=True, pauseReason="Lunch")
        self.now += 2
        self.clock.poll()
        self.now += 1
        self.assertEqual(self.clock.status()["time"], "09:00:00")
        self.assertFalse(self.clock.status()["running"])
        self.assertEqual(self.clock.status()["stopped_reason"], "Lunch")

    def test_live_messages_follow_provider_through_pause_resume_and_clear(self):
        for payload, text in (
            (provider_status(message="Trafiken börjar strax"), "Trafiken börjar strax"),
            (provider_status(isPaused=True, pauseReason="Lunch"), "Lunch"),
            (provider_status(message="Körningen fortsätter"), "Körningen fortsätter"),
            (provider_status(message="Ändrat meddelande"), "Ändrat meddelande"),
            (provider_status(message="   "), None),
            (provider_status(), None),
        ):
            with self.subTest(payload=payload):
                self.reply = payload
                self.clock.poll(force=True)
                status = self.clock.status()
                self.assertEqual(status["message"], text)
                self.assertEqual(status["running"], not payload.get("isPaused", False))
                if status["running"]:
                    self.assertIsNone(status["stopped_reason"])

    def test_message_and_pause_reason_are_separate(self):
        status = parse_status(provider_status(isPaused=True, pauseReason="Lunch", message="Vi börjar 13:00"))
        self.assertEqual(status["message"], "Vi börjar 13:00")
        self.assertEqual(status["stopped_reason"], "Lunch")
        self.assertEqual(parse_status(provider_status(stoppingReason="Information"))["message"], "Information")
        self.assertEqual(len(parse_status(provider_status(message="a" * 500))["message"]), 160)

    def test_message_change_does_not_reset_clock_interpolation(self):
        self.reply = provider_status(time="09:15", message="Första meddelandet")
        self.clock.poll(force=True)
        self.now += 3
        before = self.clock.status()["seconds"]
        self.reply = provider_status(time="09:15", message="Nytt meddelande")
        self.clock.poll(force=True)
        self.assertEqual(self.clock.status()["message"], "Nytt meddelande")
        self.assertEqual(self.clock.status()["seconds"], before)
        self.now += 1
        self.assertEqual(self.clock.status()["seconds"], before + 4)

    def test_outage_staleness_and_recovery_never_fall_back(self):
        self.clock.poll()
        self.now += 7
        self.assertFalse(self.clock.status()["available"])
        self.assertEqual(self.clock.status()["time"], "06:12:34")
        self.reply = FastClockError("Unavailable")
        self.clock.poll()
        self.assertFalse(self.clock.status()["running"])
        self.reply = provider_status(time="23:59:59")
        self.clock.poll(force=True)
        self.now += 1
        self.assertEqual(self.clock.status()["time"], "00:00:03")

    def test_minute_only_fastclock_does_not_rewind_every_poll(self):
        self.reply = provider_status(time="09:15", speed=5, weekday="Monday")
        self.assertTrue(self.clock.poll())
        self.assertEqual(self.clock.status()["weekday"], "Mån")
        self.assertEqual(self.clock.status()["time_precision"], "minute")
        previous = 9 * 3600 + 15 * 60
        for _ in range(5):
            self.now += 2
            self.assertTrue(self.clock.poll())
            current = self.clock.status()["seconds"]
            self.assertGreater(current, previous, "equal HH:MM samples must never rewind")
            previous = current
        self.reply = provider_status(time="09:16", speed=5, weekday="Monday")
        self.now += 2
        self.assertTrue(self.clock.poll())
        self.assertGreater(self.clock.status()["seconds"], previous)
        self.assertGreaterEqual(self.clock.status()["seconds"], 9 * 3600 + 16 * 60)
        self.assertLess(self.clock.status()["seconds"], 9 * 3600 + 16 * 60 + 11)
        self.now += 2
        previous = self.clock.status()["seconds"]
        self.assertTrue(self.clock.poll())
        self.assertEqual(self.clock.status()["seconds"], previous,
                         "a poll at the same instant must not reset time")
        self.now += 2
        self.assertGreater(self.clock.status()["seconds"], previous)

    def test_second_precision_is_followed_exactly(self):
        self.reply = provider_status(time="09:15:25", speed=5)
        self.clock.poll()
        self.assertEqual(self.clock.status()["time"], "09:15:25")
        self.assertEqual(self.clock.status()["time_precision"], "second")
        self.now += 2
        self.reply = provider_status(time="09:15:35", speed=5)
        self.clock.poll()
        self.assertEqual(self.clock.status()["time"], "09:15:35")

    def test_explicit_zero_second_feed_learns_minute_precision(self):
        # Some compatible clocks send HH:MM:00 on every request, even at 5x.
        self.reply = provider_status(time="09:15:00", speed=5)
        self.clock.poll()
        self.now += 2
        self.clock.poll()
        self.assertEqual(self.clock.status()["time_precision"], "minute")
        self.assertEqual(self.clock.status()["time"], "09:15:10")
        self.now += 2
        self.clock.poll()
        self.assertEqual(self.clock.status()["time"], "09:15:20")
        self.reply = provider_status(time="09:16:00", speed=5)
        self.now += 8
        before = self.clock.status()["seconds"]
        self.clock.poll()
        self.assertGreaterEqual(self.clock.status()["seconds"], before)
        self.assertEqual(self.clock.status()["speed"], 5)

    def test_precision_capable_clock_stays_precise_at_exact_minute(self):
        self.reply = provider_status(time="09:14:55", speed=5)
        self.clock.poll()
        self.now += 1
        self.reply = provider_status(time="09:15:00", speed=5)
        self.clock.poll(force=True)
        self.assertEqual(self.clock.status()["time_precision"], "second")
        self.now += 1
        self.reply = provider_status(time="09:15:05", speed=5)
        self.clock.poll(force=True)
        self.assertEqual(self.clock.status()["time_precision"], "second")

    def test_no_sample_and_wrong_region_are_distinct(self):
        self.assertEqual(self.clock.status("eu")["time"], "--:--:--")
        self.assertFalse(self.clock.status()["running"])
        self.assertIsNone(self.clock.status("us"))

    def test_late_poll_reply_is_discarded_after_meet_switch(self):
        entered, release = threading.Event(), threading.Event()
        self.clock.transport = lambda *_: (entered.set(), release.wait(2), provider_status())[2]
        worker = threading.Thread(target=self.clock.poll)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.clock.configure("us:two", self.settings)
        release.set(); worker.join(2)
        self.assertIsNone(self.clock.status("us")["last_sync"])

    def test_anonymous_is_read_only_and_commands_are_not_optimistic(self):
        with self.assertRaises(FastClockError):
            self.clock.control("start")
        self.assertEqual(self.calls, [])
        self.clock.configure("eu:one", {**self.settings, "user": "Dispatcher"})
        self.reply = provider_status(isRunning=False)
        self.clock.control("start")
        self.assertEqual([c[1] for c in self.calls], ["start", "time"])
        self.assertFalse(self.clock.status()["running"], "only provider-confirmed state is shown")
        with self.assertRaises(FastClockError):
            self.clock.control("set")

    def test_strict_provider_payload_validation(self):
        for data in [None, {}, provider_status(time="24:01"), provider_status(speed=float("nan")),
                     provider_status(speed=True), provider_status(isRunning="true"),
                     provider_status(isUnavailable=True), provider_status(isPaused="false")]:
            with self.subTest(data=data), self.assertRaises(FastClockError):
                parse_status(data)

    def test_credentials_do_not_cross_clock_identity(self):
        old = {**self.settings, "password": "private", "user": "One"}
        self.assertEqual(validate_settings({"source": "fastclock"}, old)["password"], "private")
        self.assertEqual(validate_settings({"source": "fastclock", "clock_name": "Other"}, old)["password"], "")
        for changes in ({"clock_name": ""}, {"poll_interval": 1}, {"poll_interval": True}, {"password": "bad\nvalue"}):
            with self.assertRaises(FastClockError):
                validate_settings({"source": "fastclock", **changes}, old)

    def test_https_contract_encodes_name_and_never_returns_provider_errors(self):
        response = MagicMock()
        response.read.return_value = json.dumps(provider_status()).encode()
        with patch("tmbox_gateway.external_clock.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value = response
            provider_request({**self.settings, "clock_name": "Club / A"})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://fastclock.azurewebsites.net/api/clocks/Club%20%2F%20A/time")
            self.assertEqual(request.method, "GET")
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 4)
            provider_request({**self.settings, "user": "A B", "password": "secret"}, "stop", "Lunch")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.method, "PUT")
            self.assertIn("reason=Lunch", request.full_url)
            opener.return_value.open.side_effect = RuntimeError("private URL ?password=secret")
            with self.assertRaises(FastClockError) as error:
                provider_request(self.settings)
            self.assertNotIn("secret", str(error.exception))

    def test_custom_https_url_and_url_validation(self):
        previous = {**self.settings, "user": "Controller", "password": "secret"}
        settings = validate_settings({"source": "fastclock", "url": "https://clock.example.org/"}, previous)
        self.assertEqual(settings["url"], "https://clock.example.org")
        self.assertEqual(settings["password"], "", "changing the provider must revoke old credentials")
        result = MagicMock()
        result.read.return_value = json.dumps(provider_status()).encode()
        with patch("tmbox_gateway.external_clock.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value = result
            provider_request({**settings, "clock_name": "A/B"})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://clock.example.org/api/clocks/A%2FB/time")
        for bad in ["http://clock.example.org", "https://localhost", "https://127.0.0.1",
                    "https://10.0.0.1", "https://clock.local", "https://user:pass@clock.example.org",
                    "https://clock.example.org/path", "https://clock.example.org?token=private",
                    "https://clock.example.org#fragment", "https://clock.example.org:bad"]:
            with self.subTest(url=bad), self.assertRaises(FastClockError):
                validate_settings({"source": "fastclock", "url": bad}, previous)

    def test_start_stop_failure_is_not_retried_or_reported_as_success(self):
        self.clock.configure("eu:one", {**self.settings, "user": "Operator"})
        self.reply = FastClockError("No confirmation")
        with self.assertRaises(FastClockError):
            self.clock.control("start")
        self.assertEqual([c[1] for c in self.calls], ["start"])
        self.assertFalse(self.clock.status()["available"])


class ExternalClockIntegrationTests(unittest.TestCase):
    setUp = fixture.CloudOnlyDeliveryTests.setUp
    fetch = fixture.CloudOnlyDeliveryTests.fetch
    connect = fixture.CloudOnlyDeliveryTests.connect

    def configure(self, **changes):
        return self.app.configure_clock_source(self.admin, {"source": "fastclock", "clock_name": "Test",
            "meet_generation": self.app.lifecycle.selected()["generation"], **changes})

    def enable(self, **changes):
        self.connect()
        self.app.external_clock.transport = MagicMock(return_value=provider_status())
        return self.configure(**changes)

    def test_same_server_clock_for_display_engine_and_v2(self):
        self.enable()
        from tmbox_gateway.protocol_v2 import TMBoxStationService
        service = TMBoxStationService(self.runtime, self.operations, self.identities)
        for clock in (self.app.clock_status(self.admin), self.app.display_snapshot()["clock"],
                      self.operations.clock_status(), service.clock_source()):
            self.assertEqual(clock["time"][:5], "06:12")
            self.assertEqual(clock["source"], "fastclock")
        self.assertEqual(self.app.engine.snapshot("panel-a")["clock"]["time"], "06:12")
        self.assertEqual(self.app.external_clock.transport.call_count, 1, "clients read cache, not provider")

    def test_display_gets_live_message_while_running_and_clears_it(self):
        self.enable()
        for paused, text in ((False, "Välkomna!"), (True, "Lunch"), (False, "Nu kör vi"), (False, None)):
            with self.subTest(paused=paused, text=text):
                self.app.external_clock.transport.return_value = provider_status(isPaused=paused, message=text)
                self.app.external_clock.poll(force=True)
                for clock in (self.app.clock_status(self.admin), self.app.display_snapshot()["clock"]):
                    self.assertEqual(clock["message"], text)
                    self.assertEqual(clock["running"], not paused)

    def test_password_is_local_redacted_and_cloud_updates_preserve_source(self):
        self.enable(user="Controller", password="private-password")
        public = json.dumps([self.app.clock_source_settings(self.admin), self.app.display_snapshot(),
                             self.app.server_context(self.admin), self.app.clock_status(self.admin)])
        self.assertNotIn("private-password", public)
        self.assertTrue(self.app.clock_source_settings(self.admin)["has_password"])
        self.offered["publication_id"] = "second"
        self.offered["clock"].update(source="internal", start_time="23:00", speed=20)
        self.app.auto_sync_cloud_runtime()
        self.assertEqual(self.app.clock_status(self.admin)["source"], "fastclock")
        self.configure()
        self.assertEqual(self.runtime.clock_source_settings(self.app._clock_scope())["password"], "private-password")
        reopened = SQLiteRuntimeStore(self.runtime.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.clock_source_settings(self.app._clock_scope())["source"], "fastclock")

    def test_failure_keeps_previous_clock_and_has_no_local_fallback(self):
        self.enable()
        previous = self.runtime.clock_source_settings(self.app._clock_scope())
        self.app.external_clock.transport.side_effect = FastClockError("Test disconnected")
        with self.assertRaises(HTTPAPIError):
            self.configure(clock_name="Wrong")
        self.assertEqual(self.runtime.clock_source_settings(self.app._clock_scope()), previous)
        self.app.external_clock.poll(force=True)
        self.assertFalse(self.app.display_snapshot()["clock"]["running"])
        self.assertEqual(self.app.clock_status(self.admin)["source"], "fastclock")

    def test_only_admin_can_configure_or_read_control_settings(self):
        self.enable()
        terminal = PairedClient("terminal", "TKL", DeviceKind.TKL_TERMINAL, ("panel-a",))
        for method, args in ((self.app.clock_source_settings, (terminal,)),
                             (self.app.configure_clock_source, (terminal, {"source": "internal"})),
                             (self.app.control_clock, (terminal, {"action": "start"}))):
            with self.assertRaises(HTTPAPIError) as error:
                method(*args)
            self.assertEqual(error.exception.status, 403)

    def test_switch_to_internal_preserves_time_but_stops_old_local_clock(self):
        self.connect()
        self.app.control_clock(self.admin, {"action": "start", "time": "10:00"})
        self.app.external_clock.transport = MagicMock(return_value=provider_status())
        self.configure()
        self.configure(source="internal")
        result = self.app.clock_status(self.admin)
        self.assertFalse(result["running"])
        self.assertEqual(result["time"][:5], "06:12")

    def test_poll_notifies_boxes_without_configuration_change(self):
        self.enable()
        self.app.on_clock_changed = MagicMock()
        self.app.external_clock.next_poll = 0
        self.app.poll_external_clock()
        self.app.on_clock_changed.assert_called_once()

    def test_external_us_start_creates_one_session_and_stop_keeps_it(self):
        self.offered = fixture.us_package()
        self.connect()
        self.app.external_clock.transport = MagicMock(return_value=provider_status())
        self.configure(user="Dispatcher")
        self.assertIsNone(self.us.current_session())
        self.assertTrue(self.app.control_clock(self.admin, {"action": "start"})["running"])
        session_id = self.us.current_session()["id"]
        self.app.external_clock.transport.return_value = provider_status(isRunning=False)
        self.assertFalse(self.app.control_clock(self.admin, {"action": "stop"})["running"])
        self.assertEqual(self.us.current_session()["id"], session_id)

    def test_failed_external_start_does_not_create_us_session(self):
        self.offered = fixture.us_package()
        self.connect()
        self.app.external_clock.transport = MagicMock(return_value=provider_status())
        self.configure(user="Dispatcher")
        self.app.external_clock.transport.side_effect = FastClockError("No confirmation")
        with self.assertRaises(HTTPAPIError):
            self.app.control_clock(self.admin, {"action": "start"})
        self.assertIsNone(self.us.current_session())


    def test_delayed_configuration_does_not_cross_meet_or_admin_edit(self):
        self.enable()
        def changed(_):
            self.runtime.save_clock_source_settings(self.app._clock_scope(), {**DEFAULT_SETTINGS})
            return provider_status()
        self.app.external_clock.transport = changed
        with self.assertRaises(HTTPAPIError) as error:
            self.configure()
        self.assertEqual(error.exception.status, 409)

    def test_us_uses_same_source_but_eu_preferences_do_not_cross_meets(self):
        self.enable()
        self.configure(source="internal")
        self.offered = fixture.us_package()
        self.connect(confirm_meet_change=True)
        self.assertEqual(self.app.clock_source_settings(self.admin)["source"], "internal")
        self.configure()
        self.assertEqual(self.app.us_context(self.admin)["clock"]["time"][:5], "06:12")
        self.assertEqual(self.app.display_snapshot()["clock"]["source"], "fastclock")
        self.assertNotEqual(self.operations.clock_status().get("source"), "fastclock")
        package = self.us.package_catalogue()[0]
        result = self.app.us_command(self.admin, {"action": "create_session", "command_id": "test-session", "confirmed": True,
            "publication_id": package["publication_id"], "package_checksum": package["checksum"]})
        context = self.app.us_context(self.admin)
        self.assertEqual(context["session"]["events"][0]["meet_time"][:5], "06:12")
        with self.assertRaises(USError):
            self.app.us_command(self.admin, {"action": "clock", "command_id": "bad-clock", "confirmed": True,
                "session_id": result["session_id"], "expected_revision": result["revision"],
                "clock_time": "21:00", "clock_speed": 3, "running": True})
        self.configure(source="internal")
        self.assertFalse(self.app.us_context(self.admin)["clock"]["running"])
        self.assertEqual(self.app.us_context(self.admin)["clock"]["time"][:5], "06:12")
