"""Träffens land från TrainMeet Cloud: SE, DK, DE, NO eller US.

Cloud skickar landet i paketets meet-block (meet.country). Servern visar det i
stället för "EU" och ger nya boxar landets språk. Trafikmotorn är densamma för
alla europeiska länder, så inget lagrat (träffvalet, klockans inställningar,
säkerhetskopiorna) ändras. Ett paket utan land, från ett äldre Cloud, är en
svensk träff, och ett okänt land stoppar aldrig paketet.
"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from runtime_fixture import runtime_package_v3
from test_protocol_v2 import DEVICE, ProtocolV2Base
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import TMBoxStationService
from tmbox_gateway.runtime import RuntimePublication, SQLiteRuntimeStore
from tmbox_gateway.storage import SQLiteStateStore

LANGUAGE = {"se": "sv", "dk": "da", "de": "de", "no": "nb"}


def package(country=None):
    result = deepcopy(runtime_package_v3())
    if country is not None:
        result["meet"]["country"] = country
        result["meet"]["default_language"] = LANGUAGE.get(country, "sv")
    return result


class PackageTests(unittest.TestCase):
    def test_the_country_comes_from_the_package(self):
        for country in ("se", "dk", "de", "no"):
            with self.subTest(country=country):
                self.assertEqual(country, RuntimePublication.parse(package(country)).country)

    def test_a_package_from_an_older_cloud_is_swedish(self):
        self.assertEqual("se", RuntimePublication.parse(package()).country)

    def test_an_unknown_country_is_swedish_and_never_stops_the_package(self):
        with self.assertLogs("tmbox_gateway.runtime", "WARNING") as logged:
            publication = RuntimePublication.parse(package("uk"))
        self.assertEqual("se", publication.country)
        self.assertIn("uk", "\n".join(logged.output))
        # US is its own package; in an EU package it is just an unknown country.
        with self.assertLogs("tmbox_gateway.runtime", "WARNING"):
            self.assertEqual("se", RuntimePublication.parse(package("us")).country)


class ServerTests(unittest.TestCase):
    def start(self, country):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "trainmeet.db"
        self.runtime = SQLiteRuntimeStore(path)
        publication = self.runtime.install(package(country))
        self.ops = SQLiteOperationsStore(path)
        self.ids = IdentityStore(path)
        self.store = SQLiteStateStore(path)
        for closeable in (self.store, self.ids, self.ops, self.runtime):
            self.addCleanup(closeable.close)
        self.ids.record_discovery("esp32", "esp32", protocol_version=2)
        service = TMBoxStationService(self.runtime, self.ops, self.ids)
        engine = TrafficEngine(publication.session_config(), state_store=self.store)
        self.app = TrainMeetHTTPApplication(
            engine, self.ids, PairingService(self.ids, set(engine.config.panels)),
            HTTPServerConfig(local_development=True, state_dir=self.temp.name),
            runtime_store=self.runtime, operations_store=self.ops, station_service=service)
        return self.app

    def test_the_country_is_shown_where_eu_was(self):
        for country in ("dk", "de", "no", "se"):
            with self.subTest(country=country):
                app = self.start(country)
                context = app.server_context(app.local_admin())
                self.assertEqual((country, country, "eu"),
                                 (context["country"], context["selected_meet"]["country"], context["operating_region"]))
                self.assertEqual(country, app.public_workspaces()["selected_meet"]["country"])
                self.assertEqual(country, app.display_snapshot()["meet"]["country"])
                self.assertIn("tmbox", context["available_workspaces"], "the EU engine runs")

    def test_a_new_box_gets_the_countrys_language_and_its_own_choice_stays(self):
        app = self.start("dk")
        self.assertEqual("da", app._device_ui("esp32")["language"])  # noqa: SLF001
        self.ids.set_device_language("esp32", "sv")
        self.assertEqual("sv", app._device_ui("esp32")["language"], "a box's own choice is kept")  # noqa: SLF001

    def test_without_a_country_the_meet_is_swedish_as_before(self):
        app = self.start(None)
        self.assertEqual("se", app.server_context(app.local_admin())["country"])
        self.assertEqual("sv", app._device_ui("esp32")["language"])  # noqa: SLF001

    def test_nothing_stored_changes_with_the_country(self):
        """The meet selection still says eu: a server that sees a country is
        not switching meets, and its clock settings keep their key."""
        app = self.start("no")
        self.assertEqual("eu", app.lifecycle.selected()["region"])
        self.assertTrue(app._clock_scope().startswith("eu:"))  # noqa: SLF001


class BoxLanguageTests(ProtocolV2Base):
    """The box protocol's own language default follows the country too."""

    def use(self, country):
        publication = RuntimePublication.parse(package(country))
        self.runtime_store.active = lambda: publication

    def test_each_country_gives_its_language(self):
        for country, language in LANGUAGE.items():
            with self.subTest(country=country):
                self.use(country)
                self.assertEqual(language, self.service.device_ui(DEVICE)["language"])

    def test_us_stays_english_and_a_choice_wins(self):
        self.service.lifecycle = SimpleNamespace(selected=lambda: {"region": "us"})
        self.assertEqual("en", self.service.device_ui(DEVICE)["language"])
        self.identities.set_device_language(DEVICE, "de")
        self.assertEqual("de", self.service.device_ui(DEVICE)["language"])


if __name__ == "__main__":
    unittest.main()
