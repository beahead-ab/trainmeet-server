"""Om trafikspelet på servern: texterna Cloud skickar med paketet.

Paketet bär ``about`` (berättelsen, instruktionerna per språk, programmet) och
stationernas ``instructions``. /v1/about lämnar dem öppet, som /v1/display,
och /v1/display säger bara vad som finns, så att klienterna vet om de ska
visa Om trafikspelet. Ett paket utan texter, eller med trasiga, fäller inget.
"""
from __future__ import annotations

import copy
import unittest
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime_fixture import runtime_package_v3
from test_pending_revisions import dispatch_request
from tmbox_gateway import meet_about
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublication, SQLiteRuntimeStore


def package_with_texts(**changes):
    package = runtime_package_v3()
    package["about"] = {"story": "# Tema\nModern trafik dygnet runt.", "instructions": {"sv": "Ring bara vid fel.", "da": "Ring kun ved fejl."},
                        "program": "- 07:30 Frukost", **changes}
    package["stations"][1]["instructions"] = "Lekeberg är en kritisk station."
    return package


class AboutOfTests(unittest.TestCase):
    def test_the_texts_are_read_as_they_are(self):
        package = package_with_texts()
        self.assertEqual({"story": "# Tema\nModern trafik dygnet runt.", "instructions": {"sv": "Ring bara vid fel.", "da": "Ring kun ved fejl."},
                          "program": "- 07:30 Frukost"}, meet_about.about_of(package))
        self.assertEqual([{"id": "station-b", "name": "Lekeberg", "code": "LEK", "instructions": "Lekeberg är en kritisk station."}],
                         meet_about.station_texts(package))
        self.assertEqual({"story": True, "instructions": ["sv", "da"], "program": True, "stations": 1, "available": True},
                         meet_about.summary(package))

    def test_an_older_package_or_a_broken_field_gives_nothing_to_read(self):
        empty = {"story": "", "instructions": {}, "program": ""}
        self.assertEqual(empty, meet_about.about_of(runtime_package_v3()))
        self.assertEqual(empty, meet_about.about_of(None))
        self.assertEqual(empty, meet_about.about_of({"about": "text"}))
        broken = meet_about.about_of({"about": {"story": 7, "instructions": {"fr": "Bonjour", "sv": ["x"], "de": "Hallo"}, "program": None}})
        self.assertEqual({"story": "", "instructions": {"de": "Hallo"}, "program": ""}, broken)
        self.assertFalse(meet_about.summary(runtime_package_v3())["available"])

    def test_the_package_still_parses_with_the_texts(self):
        publication = RuntimePublication.parse(package_with_texts())
        self.assertIn("about", publication.payload)
        self.assertEqual("Lekeberg är en kritisk station.", publication.payload["stations"][1]["instructions"])


class AboutHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "server.db"
        self.runtime = SQLiteRuntimeStore(self.path)
        self.ops = SQLiteOperationsStore(self.path)
        self.ids = IdentityStore(self.path)

    def tearDown(self):
        self.ops.close(); self.ids.close(); self.runtime.close()
        if hasattr(self, "app"):
            self.app.lifecycle.close()
        self.temp.cleanup()

    def start(self, package):
        publication = self.runtime.install(package)
        self.app = TrainMeetHTTPApplication(TrafficEngine(publication.session_config()), self.ids,
                                            PairingService(self.ids, {"panel-a", "panel-b"}), HTTPServerConfig(local_development=True),
                                            runtime_store=self.runtime, operations_store=self.ops)
        self.app.automatic.set_enabled(False)
        self.ops.ensure_publication(publication)
        return publication

    def test_about_is_open_and_carries_the_texts_and_the_meets_language(self):
        publication = self.start(package_with_texts())
        status, body = dispatch_request(self.app, None, "/v1/about", method="GET")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(publication.publication_id, body["publication_id"])
        self.assertEqual({"name": "Sommarträffen", "language": "sv"}, body["meet"])
        self.assertEqual("# Tema\nModern trafik dygnet runt.", body["about"]["story"])
        self.assertEqual(["sv", "da"], list(body["about"]["instructions"]))
        self.assertEqual(["station-b"], [station["id"] for station in body["stations"]])

    def test_the_display_says_what_there_is_to_read_but_not_the_texts(self):
        self.start(package_with_texts(program=""))
        display = self.app.display_snapshot()
        self.assertEqual({"story": True, "instructions": ["sv", "da"], "program": False, "stations": 1, "available": True}, display["about"])
        self.assertNotIn("Modern trafik", str(display["about"]))
        # Stationens instruktioner följer med stationen, som de står i paketet.
        self.assertEqual("Lekeberg är en kritisk station.", next(s for s in display["stations"] if s["id"] == "station-b")["instructions"])

    def test_a_meet_without_texts_has_nothing_to_read(self):
        self.start(runtime_package_v3())
        status, body = dispatch_request(self.app, None, "/v1/about", method="GET")
        self.assertEqual((HTTPStatus.OK, {"story": "", "instructions": {}, "program": ""}, []), (status, body["about"], body["stations"]))
        self.assertFalse(self.app.display_snapshot()["about"]["available"])

    def test_a_danish_meet_falls_back_to_danish(self):
        package = package_with_texts()
        package["meet"]["country"] = "dk"
        self.start(package)
        self.assertEqual("da", self.app.about_snapshot()["meet"]["language"])

    def test_a_local_timetable_change_keeps_the_texts(self):
        """Den lokala redigeringen bygger det gällande paketet ur Clouds; texterna följer med."""
        import json
        from tmbox_gateway.local_edits import apply_draft, draft_from_publication
        package = json.loads((Path(__file__).resolve().parent / "cloud_runtime_package.json").read_text(encoding="utf-8"))
        package["about"] = {"story": "Berättelsen", "instructions": {"sv": "Ring"}, "program": ""}
        package["stations"][0]["instructions"] = "Kritisk"
        draft = draft_from_publication(package)
        draft["trains"][0]["departure_time"] = "23:59"
        effective, _ = apply_draft(package, draft)
        self.assertEqual(package["about"], effective["about"])
        self.assertEqual("Kritisk", effective["stations"][0]["instructions"])
