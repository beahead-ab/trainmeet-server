"""Device-scoped copy, isolated from station/traffic authority."""
import unittest
from unittest.mock import MagicMock
from test_protocol_v2 import DEVICE, STATION, ProtocolV2Base
from tmbox_gateway.device_ui import LANGUAGES, MESSAGES, text
from tmbox_gateway.identity import IdentityStore, PairedClient, DeviceKind
from tmbox_gateway.http_server import HTTPAPIError


class CatalogueTests(unittest.TestCase):
    def test_five_complete_catalogues(self):
        self.assertEqual({"sv", "da", "nb", "en", "de"}, {c for c, _ in LANGUAGES})
        for code, _ in LANGUAGES:
            with self.subTest(code=code):
                self.assertEqual(set(MESSAGES["sv"]), set(MESSAGES[code]))
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
        self.identities.set_device_language(DEVICE, "de")
        self.assertEqual("de", self.service.device_ui(DEVICE)["language"])
        self.assertEqual(before, self.service.snapshot_payload(STATION))
        self.assertEqual(original["station_id"], self.service.assignment_payload(DEVICE)["station_id"])
        self.assertEqual(original["config_version"], self.service.assignment_payload(DEVICE)["config_version"])
        self.assertEqual("sv", self.service.device_ui(other)["language"])
        another_connection = IdentityStore(self.runtime_store.path)
        try:
            self.assertEqual("de", another_connection.device_language(DEVICE))
        finally:
            another_connection.close()

    def test_an_invalid_language_is_refused_and_changes_nothing(self):
        for language in ("xx", "", None, {}, ["en"]):
            with self.assertRaises(ValueError):
                self.identities.set_device_language(DEVICE, language)
        self.assertEqual("sv", self.identities.device_language(DEVICE))

    def test_a_saved_language_reaches_a_box_without_a_station_and_the_config(self):
        self.identities.record_discovery("TMBOX-NEW", "TMBOX-NEW")
        self.identities.set_device_language("TMBOX-NEW", "nb")
        self.assertEqual("nb", self.service.device_ui("TMBOX-NEW")["language"])
        self.assertIsNone(self.identities.discovered_device("TMBOX-NEW").station_id)
        self.identities.set_device_language(DEVICE, "da")
        self.assertEqual("da", self.service.config_payload(STATION, device_id=DEVICE)["ui"]["language"])

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
