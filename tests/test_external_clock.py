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

    def test_mqtt_clock_publish_never_resends_assignments_or_configs(self):
        from tmbox_gateway.local_server import publish_clock_to_devices
        from types import SimpleNamespace
        gateway, v2, identities = MagicMock(), MagicMock(), MagicMock()
        identities.enabled_clients.return_value = [SimpleNamespace(station_id=s) for s in ["a", "a", "b", None]]
        publish_clock_to_devices(gateway, v2, identities)
        gateway._publish_snapshots.assert_called_once()
        self.assertEqual(sorted(c.args[0] for c in v2.publish_station_snapshot.call_args_list), ["a", "b"])
        v2.publish_device_state.assert_not_called()
        gateway.publish_device_assignment.assert_not_called()

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
