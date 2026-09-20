"""Device-scoped copy, isolated from station/traffic authority."""
import json
import unittest
from unittest.mock import MagicMock
from test_protocol_v2 import DEVICE, STATION, ProtocolV2Base, device_topic
import test_mqtt_commands as mqtt_fixture
from tmbox_gateway.device_ui import LANGUAGES, MESSAGES, ui_payload, text
from tmbox_gateway.identity import IdentityStore, PairedClient, DeviceKind
from tmbox_gateway.http_server import HTTPAPIError


class CatalogueTests(unittest.TestCase):
    def test_five_complete_catalogues_and_small_legacy_pack(self):
        self.assertEqual({"sv", "da", "nb", "en", "de"}, {c for c, _ in LANGUAGES})
        for code, _ in LANGUAGES:
            with self.subTest(code=code):
                self.assertEqual(set(MESSAGES["sv"]), set(MESSAGES[code]))
                self.assertLess(len(json.dumps(ui_payload(code, legacy=True)).encode()), 2032)
                self.assertEqual("MUN", text(code, "MUN"))
                self.assertEqual("93", text(code, "93"))
                self.assertLessEqual(len(text(code, "C=TAG D=SPRAK")), 16)
                for key in ("C=NEXT *=BACK", "SAVING...", "NOT SAVED #=TRY"):
                    self.assertLessEqual(len(text(code, key)), 16)


class LanguageProtocolTests(ProtocolV2Base):
    def test_choice_persists_is_per_device_and_never_changes_traffic(self):
        other = "TMBOX-OTHER"
        self.identities.record_discovery(other, other)
        self.identities.assign_discovered_device(other, station_id=STATION)
        before = self.service.snapshot_payload(STATION)
        original = self.service.assignment_payload(DEVICE)
        self._send("preferences/set", {"request_id": "lang-1", "language": "de"})
        topic, reply, retained = self.published[-1]
        self.assertEqual(device_topic(DEVICE, "preferences"), topic)
        self.assertFalse(retained)
        self.assertEqual("accepted", reply["status"])
        self.assertEqual("de", reply["ui"]["language"])
        self.assertEqual(before, self.service.snapshot_payload(STATION))
        self.assertEqual(original["station_id"], self.service.assignment_payload(DEVICE)["station_id"])
        self.assertEqual(original["config_version"], self.service.assignment_payload(DEVICE)["config_version"])
        self.assertEqual("sv", self.service.device_ui(other)["language"])
        another_connection = IdentityStore(self.runtime_store.path)
        try:
            self.assertEqual("de", another_connection.device_language(DEVICE))
        finally:
            another_connection.close()

    def test_invalid_removed_and_retained_requests_cannot_change_language(self):
        for language in ("xx", "", None, {}, ["en"]):
            self._send("preferences/set", {"request_id": "bad", "language": language})
            self.assertEqual("rejected", self.published[-1][1]["status"])
        self.gateway.on_message(device_topic(DEVICE, "preferences/set"),
                                json.dumps({"request_id": "old", "language": "en"}).encode(), retained=True)
        self.assertEqual("sv", self.identities.device_language(DEVICE))
        self.identities.remove_discovered_device(DEVICE)
        self._send("preferences/set", {"request_id": "gone", "language": "en"})
        self.assertEqual("rejected", self.published[-1][1]["status"])

    def test_hello_resends_saved_catalogue_even_without_station(self):
        self.identities.record_discovery("TMBOX-NEW", "TMBOX-NEW")
        self.identities.set_device_language("TMBOX-NEW", "nb")
        self.gateway.publish_device_state("TMBOX-NEW")
        reply = next(p for t, p, _ in self.published if t == device_topic("TMBOX-NEW", "preferences"))
        self.assertEqual("nb", reply["ui"]["language"])
        self.assertIsNone(self.identities.discovered_device("TMBOX-NEW").station_id)
        self.identities.set_device_language(DEVICE, "da")
        self.assertEqual("da", self.service.config_payload(STATION, device_id=DEVICE)["ui"]["language"])

    def test_admin_push_contains_only_current_language_not_config_or_traffic(self):
        self.identities.set_device_language(DEVICE, "de")
        self.gateway.publish_device_language(DEVICE)
        self.assertEqual(len(self.published), 1)
        topic, body, retained = self.published[0]
        self.assertEqual(topic, device_topic(DEVICE, "preferences"))
        self.assertFalse(retained)
        self.assertEqual(body["ui"], ui_payload("de"))
        self.assertEqual(set(body["ui"]["languages"][0]), {"code", "name"})

    def test_us_default_is_english_until_operator_makes_a_choice(self):
        from types import SimpleNamespace
        self.service.lifecycle = SimpleNamespace(selected=lambda: {"region": "us"})
        self.assertEqual("en", self.service.device_ui(DEVICE)["language"])
        self.identities.set_device_language(DEVICE, "sv")
        self.assertEqual("sv", self.service.device_ui(DEVICE)["language"])


class LanguageHTTPTests(ProtocolV2Base):
    def setUp(self):
        super().setUp()
        from tmbox_gateway.engine import TrafficEngine
        from tmbox_gateway.http_server import TrainMeetHTTPApplication, HTTPServerConfig
        from tmbox_gateway.identity import PairingService
        engine = TrafficEngine(self.runtime_store.active().session_config())
        self.application = TrainMeetHTTPApplication(
            engine, self.identities, PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True), runtime_store=self.runtime_store,
            operations_store=self.operations_store, station_service=self.service)
        self.client = self.application.local_admin()

    def box(self):
        return PairedClient(client_id=DEVICE, display_name=DEVICE, kind=DeviceKind.ESP32_PANEL, panel_ids=())

    def test_admin_can_push_to_one_box_without_reassigning_it(self):
        callback = self.application.on_device_language_changed = MagicMock()
        assignments = self.application.on_device_assignment_changed = MagicMock()
        before = self.service.snapshot_payload(STATION)
        result = self.application.set_device_language(self.client, {"device_id": DEVICE, "language": "da"})
        self.assertTrue(result["saved"])
        callback.assert_called_once_with(DEVICE)
        assignments.assert_not_called()
        self.assertEqual(before, self.service.snapshot_payload(STATION))
        listed = next(d for d in self.application.devices(self.client)["devices"] if d["device_id"] == DEVICE)
        self.assertEqual("da", listed["language"])
        self.assertEqual(STATION, listed["station_id"])
        # Admin choice is not a lock: the station operator can change it again.
        self.application.tmbox_preferences(self.box(), {"language": "nb"})
        self.assertEqual("nb", self.identities.device_language(DEVICE))

    def test_offline_push_is_saved_for_next_hello_and_requires_admin(self):
        with self.assertRaises(HTTPAPIError):
            self.application.set_device_language(self.box(), {"device_id": DEVICE, "language": "en"})
        with self.assertRaises(HTTPAPIError):
            self.application.set_device_language(self.client, {"device_id": "absent", "language": "en"})
        self.application.on_device_language_changed = MagicMock(side_effect=ConnectionError("offline"))
        with self.assertLogs("tmbox_gateway.http", level="ERROR"):
            result = self.application.set_device_language(self.client, {"device_id": DEVICE, "language": "de"})
        self.assertTrue(result["saved"])
        self.assertEqual("de", self.service.device_ui(DEVICE)["language"])

    def test_unassigned_browser_operator_gets_language_without_station_authority(self):
        self.identities.record_discovery("TMBOX-NEW", "TMBOX-NEW")
        new = self.identities.enroll_physical_box("TMBOX-NEW")
        result = self.application.tmbox_preferences(new, {"language": "de"})
        self.assertEqual("de", result["ui"]["language"])
        self.assertIsNone(self.identities.station_for_client("TMBOX-NEW"))

    def test_operator_changes_only_own_presentation_without_admin(self):
        before = self.service.snapshot_payload(STATION)
        result = self.application.tmbox_preferences(self.box(), {"language": "en"})
        self.assertEqual("en", result["ui"]["language"])
        self.assertEqual("en", self.application.tmbox_v2_config(self.box(), STATION)["ui"]["language"])
        self.assertEqual(before, self.service.snapshot_payload(STATION))
        for payload in ({"language": "de", "device_id": "other"}, {"language": "de", "station_id": "other"}, {}, {"language": "xx"}):
            with self.assertRaises(HTTPAPIError):
                self.application.tmbox_preferences(self.box(), payload)
        with self.assertRaises(HTTPAPIError):
            self.application.tmbox_preferences(self.client, {"language": "de"})
        self.identities.remove_discovered_device(DEVICE)
        with self.assertRaises(HTTPAPIError):
            self.application.tmbox_preferences(self.box(), {"language": "de"})


class LegacyLanguageTests(unittest.TestCase):
    setUp = mqtt_fixture.DeviceHelloTests.setUp
    tearDown = mqtt_fixture.DeviceHelloTests.tearDown
    _hello = mqtt_fixture.DeviceHelloTests._hello
    _assign = mqtt_fixture.DeviceHelloTests._assign
    _presence = mqtt_fixture.DeviceHelloTests._presence
    _publications = mqtt_fixture.DeviceHelloTests._publications
    def _language(self, language, *, retained=False):
        message = MagicMock(topic="tambox/v1/device/TMBOX-7A42F1/preferences/set",
                            payload=json.dumps({"request_id": "lang-1", "language": language}).encode(),
                            mid=2, qos=1, retain=retained)
        self.adapter._on_message(self.adapter.client, None, message)

    def test_language_has_separate_ack_and_preserves_interaction(self):
        self._assign()
        self.adapter.engine.press = MagicMock()
        before = self.adapter.engine.snapshot("panel-a")
        self._language("en")
        self.adapter.engine.press.assert_not_called()
        self.assertEqual(before, self.adapter.engine.snapshot("panel-a"))
        publications = self._publications()
        reply = next(p for t, p, _ in publications if t.endswith("/preferences"))
        self.assertEqual("accepted", reply["status"])
        self.assertEqual("en", reply["ui"]["language"])
        self.assertTrue(any("/snapshot/" in t for t, _, _ in publications))
        self.assertFalse(any(t.endswith("/ack") for t, _, _ in publications))

    def test_localized_snapshot_token_prevents_repeated_traffic_frames(self):
        self._assign()
        self._language("de")
        self.adapter.client.reset_mock()
        from tmbox_gateway.mqtt_adapter import _snapshot_token
        token = _snapshot_token(self.adapter.engine.snapshot("panel-a", language="de"))
        self._presence(state_token=token)
        self.assertFalse(any("/snapshot/" in t for t, _, _ in self._publications()))

    def test_retained_preference_write_is_ignored(self):
        self._assign()
        self._language("en", retained=True)
        self.assertEqual("sv", self.identities.device_language("TMBOX-7A42F1"))
