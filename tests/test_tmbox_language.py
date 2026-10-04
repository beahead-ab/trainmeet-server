"""Device-scoped copy, isolated from station/traffic authority."""
import ast
import json
import re
import unittest
from pathlib import Path
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


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT.parent / "trainmeet-tmbox" / "firmware"
# Every catalogue key the TMBox firmware looks up (tr/text/uiText literals,
# clearance_word, rejection_word and the primary action labels), as written
# in its source. The firmware folds Å/Ä/Ö to A/A/O for the lookup.
FIRMWARE_KEYS = {
    "  *=TILLBAKA", " AV ", " TAG  C=BLADDRA", " TRAFFAR", "*=AVBRYT", "*=TILLBAKA", "A=BEGAR  C=NASTA",
    "A=KLART  B=EJ", "A=KVITTERA", "A=SOK  B=SUDDA", "A=VALJ  C=NASTA", "ANK", "ANKOMMIT", "ANSLUTER SERVER",
    "ARENDET AR BORTA", "ATERTAGEN", "AVG", "AVGATT", "B=ANDRA", "BEGAR", "BEGAR MOT", "BOXEN EJ KOPPLAD",
    "C=NASTA  *=TILLBAKA", "C=NASTA #=VALJ", "C=TAG D=SPRAK", "EJ ER BEGARAN", "EJ ER FRAGA", "EJ KLART",
    "FEL PROTOKOLL", "FEL STATION", "FINNS EJ IDAG", "FORARE", "FORARE ", "FRAN ", "FÖRSÖKER IGEN",
    "HAMTAR DATA...", "INGA ARENDEN", "INGA MEDDELANDEN", "INGA SPAR", "INGA TAG IDAG", "INGEN GRANNE",
    "INGEN SADAN LINJE", "INGEN TRAFF", "INGET SVAR", "INGET TAG VALT", "INGET TILLATET", "INSTALLERA WIFI",
    "KLARERING ", "KLART", "KNAPPSATS SAKNAS", "KOMMANDO NEKAT", "KOMMANDO OK", "KONTROLLERA I2C", "KOPPLA BOXEN",
    "LAGET HAR ANDRATS", "LINJEN LEDIG", "LINJEN UPPTAGEN", "NARMAR", "NATVERK RADERAS", "NÄT SAKNAS",
    "OKANT KOMMANDO", "PA PLATS", "REDAN AVGJORD", "REDAN KVITTERAD", "SAKNAR NUMMER", "SAKNAS", "SERVER BORTA",
    "SIFFROR PA TANGENT", "SKICKAR...", "SPARET FINNS EJ", "STATION KOPPLAD", "SÖKER SERVER", "TAG",
    "TAGET FINNS EJ", "UPP", "UTGANGEN", "VALJ SPAR", "VANTAR", "VÄNTAR PÅ SVAR",
}
# Shown only on the 20 x 4 display (geometry.tall() in renderer.cpp).
TALL_ONLY = {"C=NASTA  *=TILLBAKA", "SIFFROR PA TANGENT"}


def fold(key):
    return key.translate(str.maketrans("ÅÄÖåäö", "AAOaao"))


class DeviceCopyCoverageTests(unittest.TestCase):
    """Every text a box can be told to show exists in all five languages and fits."""

    def test_every_firmware_key_has_all_five_languages(self):
        for key in sorted(FIRMWARE_KEYS):
            for code, _ in LANGUAGES:
                with self.subTest(key=key, language=code):
                    self.assertIn(fold(key), MESSAGES[code])

    def test_every_lcd_text_fits_its_line(self):
        for code, _ in LANGUAGES:
            for key, value in MESSAGES[code].items():
                with self.subTest(language=code, key=key):
                    self.assertLessEqual(len(value), 20 if key in TALL_ONLY else 16, value)

    def test_the_v1_panel_rows_fit_beside_their_key_hints(self):
        # display.py: fit_line(left, right) keeps the right part and cuts the left.
        rows = [("Väntar svar...", "*=Avb", 0), ("A=KLART", "B=EJ", 0), ("A=Avg", "*=Avb", 0),
                ("A=AVGÅTT", "B=EJ", 0), ("#=Ja", "*=Nej", 0), ("A=ANKOMMIT", "B=EJ", 0), ("Tåg: ", "*=Avb", 6)]
        for code, _ in LANGUAGES:
            for left, right, digits in rows:
                with self.subTest(language=code, row=left):
                    self.assertLess(len(text(code, left)) + digits + len(text(code, right)), 17)

    def test_the_list_matches_the_firmware_source(self):
        if not FIRMWARE.exists():
            self.skipTest("trainmeet-tmbox is not checked out beside this repository")
        source = "".join(path.read_text(errors="replace") for path in sorted(FIRMWARE.rglob("*"))
                         if path.suffix in {".h", ".cpp", ".ino"})
        keys = set(re.findall(r'\b(?:tr|text|uiText)\("([^"]*)"\)', source))
        renderer = (FIRMWARE / "esp32/lib/tmbox_core/renderer.cpp").read_text()
        for function in ("clearance_word", "rejection_word"):
            body = renderer[renderer.index(f"std::string {function}("):]
            keys |= set(re.findall(r'return "([^"]+)"', body[:body.index("\n}\n")]))
        keys |= set(re.findall(r'\{"[a-z_.]+", "([A-Z]+)"\}', renderer))
        self.assertEqual(set(), keys - FIRMWARE_KEYS, "new firmware texts: add them to FIRMWARE_KEYS and device_ui.py")

    def test_the_browser_box_says_wait_like_a_physical_box(self):
        # terminal16_web/terminal.js draws the same two lines a physical box gets.
        script = (ROOT / "src/tmbox_gateway/terminal16_web/terminal.js").read_text()
        body = script[script.index("const BOX_TEXT = {"):]
        table = json.loads("{" + re.sub(r"(\w+): \{", r'"\1": {', body[len("const BOX_TEXT = {"):body.index("};") ]).rstrip().rstrip(",") + "}")
        self.assertEqual({code for code, _ in LANGUAGES}, set(table))
        for code, lines in table.items():
            for key, shown in lines.items():
                self.assertEqual(MESSAGES[code][key], shown, (code, key))

    def test_every_terminal16_key_is_in_a_catalogue(self):
        from tmbox_gateway.terminal16_i18n import MESSAGES as TERMINAL16
        tree = ast.parse((ROOT / "src/tmbox_gateway/terminal16.py").read_text())

        def constants(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [node.value]
            if isinstance(node, ast.IfExp):
                return constants(node.body) + constants(node.orelse)
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Dict):
                return [value for item in node.value.values for value in constants(item)]
            return []
        keys = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"t", "translated"}:
                arguments = node.args[1:] if node.func.id == "translated" else node.args
                keys |= set(constants(arguments[0])) if arguments else set()
            # Hints are assigned as keys and translated once, with t(hint).
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "hint" for target in node.targets):
                keys |= set(constants(node.value))
            # Key labels: ("action", "Label") pairs, translated with t(label).
            if (isinstance(node, ast.Tuple) and len(node.elts) == 2
                    and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts)
                    and re.fullmatch(r"[a-z_]+", node.elts[0].value) and not re.fullmatch(r"[a-z_]+", node.elts[1].value)):
                keys.add(node.elts[1].value)
        keys -= {"", "C/D"}  # no words
        self.assertGreater(len(keys), 60)
        for key in sorted(keys):
            for code, _ in LANGUAGES:
                with self.subTest(key=key, language=code):
                    self.assertTrue(key in TERMINAL16[code] or key in MESSAGES[code], key)


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
