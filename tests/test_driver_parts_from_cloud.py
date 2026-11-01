"""Köruppdragen från Cloud (Cloud #113, Server #225).

Köruppdragen kan läggas in under fliken Lokförare i Cloud. Tjänsterna följer
med paketet (``driver_duties``) och varje tjänst hänvisar till sina egna sidor,
en liten PDF med sha256 och storlek. Servern hämtar delarna med sin
serverkoppling innan paketet tas emot, och föraren får dem sedan från servern,
också utan internet. En träff kan börja med bara lokförarna, utan bana och
utan TMBoxar.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from runtime_fixture import runtime_package_v3
from tmbox_gateway.central_sync import (
    CentralRuntimeDownload, CentralRuntimeManifest, CentralSyncError, fetch_driver_part,
)
from tmbox_gateway.driver_parts import CloudDriverParts, package_parts, part_reference, source_pages
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import unconfigured_session
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublication, RuntimePublicationError, SQLiteRuntimeStore
from tmbox_gateway.us import USStore


def pdf(text: str) -> bytes:
    return b"%PDF-1.7\n" + text.encode() * 40 + b"\n%%EOF\n"


def reference(body: bytes) -> dict:
    return {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}


PART_81, PART_132 = pdf("tjanst 81 "), pdf("tjanst 132 ")


def duty(number: str, part: bytes | None, pages=(3, 5), station="Tveiten") -> dict:
    raw = {"id": f"duty-{number}", "number": number, "operator": "CargoNet", "start": "17:10", "end": "18:40",
           "trains": [{"number": "G 5456", "from": station, "to": station}],
           "events": [{"kind": "departure", "time": "17:30", "station": station, "train": "G 5456"},
                      {"kind": "arrival", "time": "18:20", "station": station, "train": "G 5456"}]}
    source = {"pages": list(pages)} if pages else {}
    if part is not None:
        source["part"] = reference(part)
    if source:
        raw["source"] = source
    return raw


def drivers_only(publication_id="drivers-only", duties=None) -> dict:
    """En träff med bara lokförarna, som Cloud bygger den (Cloud #113)."""
    package = runtime_package_v3(publication_id=publication_id)
    for key in ("stations", "connections", "panels", "trains", "routes", "services", "tracks", "autonomous_links"):
        package[key] = []
    package["display"] = {"graph_station_order": []}
    package["driver_duties"] = duties if duties is not None else [duty("132", PART_132)]
    return package


def checksum(package: dict) -> str:
    return hashlib.sha256(json.dumps(package, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class PackageShapeTests(unittest.TestCase):
    def test_part_references_are_read_strictly(self):
        self.assertEqual(reference(PART_81), part_reference(duty("81", PART_81)))
        for broken in ({"sha256": "x" * 64, "size": 10}, {"sha256": reference(PART_81)["sha256"], "size": 0},
                       {"sha256": reference(PART_81)["sha256"], "size": 11 * 1024 * 1024},
                       {"sha256": reference(PART_81)["sha256"], "size": True}, "inte ett objekt"):
            raw = duty("81", None)
            raw["source"] = {"part": broken}
            self.assertIsNone(part_reference(raw), broken)
        self.assertIsNone(part_reference(duty("81", None)))
        self.assertEqual([3, 5], source_pages(duty("81", None)))
        self.assertEqual([], source_pages({"source": {"pages": [0, 5]}}))
        self.assertEqual([7, 7], source_pages({"source": {"pages": [7]}}))

    def test_each_part_is_listed_once(self):
        package = {"driver_duties": [duty("81", PART_81), duty("81b", PART_81), duty("132", PART_132), duty("9", None)]}
        self.assertEqual([reference(PART_81), reference(PART_132)], package_parts(package))
        self.assertEqual([], package_parts(runtime_package_v3()))

    def test_a_meet_with_only_the_drivers_needs_no_tmbox(self):
        publication = RuntimePublication.parse(drivers_only())
        self.assertEqual([], publication.payload["panels"])
        TrafficEngine(publication.session_config())
        # Med en bana krävs paneler som förut, och utan köruppdrag också.
        with_line = runtime_package_v3()
        with_line["panels"] = []
        with_line["driver_duties"] = [duty("132", PART_132)]
        with self.assertRaisesRegex(RuntimePublicationError, "TMBox-paneler"):
            RuntimePublication.parse(with_line)
        empty = drivers_only(duties=[])
        with self.assertRaisesRegex(RuntimePublicationError, "TMBox-paneler"):
            RuntimePublication.parse(empty)


class CloudDriverPartsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.parts = CloudDriverParts(Path(directory.name) / "lokforaren" / "cloud")
        self.package = {"driver_duties": [duty("81", PART_81), duty("132", PART_132)]}
        self.served = {reference(PART_81)["sha256"]: PART_81, reference(PART_132)["sha256"]: PART_132}
        self.calls = []

    def fetch(self, sha, size):
        self.calls.append(sha)
        return self.served[sha]

    def test_parts_are_fetched_once_and_kept_under_their_checksum(self):
        self.assertEqual({"parts": 2, "fetched": 2}, self.parts.ensure(self.package, self.fetch))
        self.assertEqual(PART_81, self.parts.path(reference(PART_81)["sha256"]).read_bytes())
        self.assertEqual({"parts": 2, "fetched": 0}, self.parts.ensure(self.package, self.fetch))
        self.assertEqual(2, len(self.calls))
        self.assertEqual([], self.parts.missing(self.package))
        self.assertIsNone(self.parts.path("../../etc/passwd"))

    def test_a_part_that_does_not_match_is_never_kept(self):
        for wrong in (PART_132, PART_81[:-1], b"<html>" + PART_81[6:]):
            self.served[reference(PART_81)["sha256"]] = wrong
            with self.subTest(wrong=wrong[:10]), self.assertRaises(CentralSyncError):
                self.parts.ensure({"driver_duties": [duty("81", PART_81)]}, self.fetch)
            self.assertIsNone(self.parts.path(reference(PART_81)["sha256"]))
        self.assertEqual([], [p for p in self.parts.folder.glob("*.tmp")])

    def test_the_part_is_asked_for_with_the_server_link_and_checked(self):
        class Response:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self, limit): return self.body[:limit]
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, request.headers))
            return Response(PART_81)
        ref = reference(PART_81)
        with patch("tmbox_gateway.central_sync.urlopen", opener):
            self.assertEqual(PART_81, fetch_driver_part("hemlig-länk", "https://cloud.trainmeet.app/config", ref["sha256"], ref["size"]))
            self.assertEqual(PART_81, fetch_driver_part("hemlig-länk", "https://trainmeet.app/konfig", ref["sha256"], ref["size"]))
            with self.assertRaises(CentralSyncError):
                fetch_driver_part("hemlig-länk", "https://cloud.trainmeet.app/config", ref["sha256"], ref["size"] - 1)
            with self.assertRaises(CentralSyncError):
                fetch_driver_part("", "https://cloud.trainmeet.app/config", ref["sha256"], ref["size"])
        self.assertEqual(f"https://cloud.trainmeet.app/config/driver-parts/{ref['sha256']}?token=hemlig-l%C3%A4nk", seen[0][0])
        self.assertEqual(seen[0][0], seen[1][0])


class CloudDeliveryWithDutiesTests(unittest.TestCase):
    """Synken tar emot paketet först när alla sidor finns på servern."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "test.db"
        self.runtime, self.operations = SQLiteRuntimeStore(path), SQLiteOperationsStore(path)
        self.identities, self.us = IdentityStore(path), USStore(path)
        for store in (self.runtime, self.operations, self.identities, self.us):
            self.addCleanup(store.close)
        self.offered = runtime_package_v3(publication_id="first")
        self.app = TrainMeetHTTPApplication(
            TrafficEngine(unconfigured_session()), self.identities, PairingService(self.identities, set()),
            HTTPServerConfig(), runtime_store=self.runtime, operations_store=self.operations, us_store=self.us,
            runtime_fetcher=lambda code, url: CentralRuntimeDownload(copy.deepcopy(self.offered), "test-link"),
            linked_runtime_fetcher=self.fetch)
        self.addCleanup(self.app.lifecycle.close)
        self.admin = self.app.local_admin()
        self.cloud = {reference(body)["sha256"]: body for body in (PART_81, PART_132)}
        self.part_calls = []
        self.app.driver_part_fetcher = self.fetch_part

    def fetch(self, token, url, manifest_only):
        if manifest_only:
            return CentralRuntimeManifest(self.offered["publication_id"], self.offered.get("published_at", ""), checksum(self.offered))
        return CentralRuntimeDownload(copy.deepcopy(self.offered), "test-link")

    def fetch_part(self, token, url, sha, size):
        self.part_calls.append((token, url, sha, size))
        if sha not in self.cloud:
            raise CentralSyncError("404")
        return self.cloud[sha]

    def test_an_older_package_without_duties_fetches_nothing(self):
        self.assertTrue(self.app.sync_runtime(self.admin, {"sync_code": "123456"})["activated"])
        self.assertEqual([], self.part_calls)
        self.assertEqual(["132"], [d["number"] for d in self.app.driver_catalog()])

    def test_linking_fetches_every_part_before_the_meet_is_taken(self):
        self.offered["driver_duties"] = [duty("81", PART_81), duty("132", PART_132)]
        self.assertTrue(self.app.sync_runtime(self.admin, {"sync_code": "123456"})["activated"])
        self.assertEqual({("test-link", "https://cloud.trainmeet.app/config")}, {call[:2] for call in self.part_calls})
        self.assertEqual(2, len(self.part_calls))
        catalog = {d["id"]: d for d in self.app.driver_catalog()}
        self.assertEqual({"cloud": True, "pages": [3, 5], "part": True}, catalog["duty-81"]["source"])
        self.assertEqual(PART_81, self.app.driver_duty_pdf("duty-81").read_bytes())
        # Utan internet efter synken: sidorna kommer från servern.
        self.app.driver_part_fetcher = lambda *_: (_ for _ in ()).throw(AssertionError("Cloud ska inte behövas"))
        self.assertEqual(PART_132, self.app.driver_duty_pdf("duty-132").read_bytes())
        with self.assertRaises(HTTPAPIError):
            self.app.driver_duty_pdf("demo-132")

    def test_a_missing_part_keeps_the_running_meet_and_is_tried_again(self):
        self.assertTrue(self.app.sync_runtime(self.admin, {"sync_code": "123456"})["activated"])
        selected = self.app.lifecycle.selected()
        self.offered = runtime_package_v3(publication_id="second")
        self.offered["driver_duties"] = [duty("81", PART_81), duty("404", pdf("saknas"))]
        with self.assertRaisesRegex(CentralSyncError, "Köruppdragens sidor kunde inte hämtas"):
            self.app.cloud_config.check()
        self.assertEqual(selected, self.app.lifecycle.selected())
        self.assertEqual("first", self.runtime.active().publication_id)
        self.assertIsNone(self.runtime.pending_publication())
        # Nästa kontroll, med delen på plats: bara den som saknades hämtas.
        self.cloud[reference(pdf("saknas"))["sha256"]] = pdf("saknas")
        self.part_calls.clear()
        self.assertTrue(self.app.cloud_config.check()["activated"])
        self.assertEqual("second", self.runtime.active().publication_id)
        self.assertEqual([reference(pdf("saknas"))["sha256"]], [call[2] for call in self.part_calls])

    def test_a_part_lost_from_the_disk_is_fetched_again(self):
        self.offered["driver_duties"] = [duty("81", PART_81)]
        self.app.sync_runtime(self.admin, {"sync_code": "123456"})
        path = self.app.driver_duty_pdf("duty-81")
        path.unlink()
        # Vid nästa kontroll av samma version …
        self.app.cloud_config.check()
        self.assertTrue(path.is_file())
        path.unlink()
        # … eller när föraren öppnar sidorna.
        self.assertEqual(PART_81, self.app.driver_duty_pdf("duty-81").read_bytes())
        path.unlink()
        self.app.driver_part_fetcher = lambda *_: (_ for _ in ()).throw(CentralSyncError("ingen kontakt"))
        with self.assertRaises(HTTPAPIError):
            self.app.driver_duty_pdf("duty-81")

    def test_a_meet_with_only_the_drivers_starts_without_tmboxes(self):
        self.offered = drivers_only()
        result = self.app.sync_runtime(self.admin, {"sync_code": "123456"})
        self.assertTrue(result["activated"], result)
        self.assertEqual("drivers-only", self.runtime.active().publication_id)
        self.assertEqual({}, dict(self.app.engine.config.panels))
        self.assertEqual(["132", "132"], [d["number"] for d in self.app.driver_catalog()])
        self.assertEqual(PART_132, self.app.driver_duty_pdf("duty-132").read_bytes())
        self.assertEqual([], self.app._driver_stations(self.runtime.active()))


class DriverPagesOverHTTPTests(unittest.TestCase):
    """Föraren hämtar Clouds sidor genom /v1/driver/duties/{id}.pdf."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Path(directory.name) / "trainmeet.db"
        self.identities, self.runtime = IdentityStore(database), SQLiteRuntimeStore(database)
        self.operations = SQLiteOperationsStore(database)
        for store in (self.operations, self.runtime, self.identities):
            self.addCleanup(store.close)
        self.identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
        invited = self.identities.invite_admin_user("Benny", "benny@example.se", "user")
        self.identities.redeem_admin_setup("benny@example.se", str(invited["setup_code"]), "ett-losenord-till")
        self.runtime.complete_installation()
        publication = self.runtime.install(drivers_only(duties=[duty("81", PART_81), duty("82", None, pages=(9, 9))]))
        self.operations.ensure_publication(publication)
        self.operations.start_clock(time_value="17:51:00", now=datetime.now(timezone.utc))
        engine = TrafficEngine(publication.session_config())
        self.app = TrainMeetHTTPApplication(
            engine, self.identities, PairingService(self.identities, set()),
            HTTPServerConfig(local_development=True, state_dir=directory.name),
            runtime_store=self.runtime, operations_store=self.operations)
        self.app.cloud_driver_parts.ensure(publication.payload, lambda sha, size: PART_81)
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def request(self, path, body=None, cookie=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                          headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
        try:
            with urlopen(request) as response:
                raw = response.read()
                kind = response.headers.get_content_type()
                return response.status, (json.loads(raw) if kind == "application/json" else raw), dict(response.headers)
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}"), dict(error.headers)

    def login(self):
        status, payload, headers = self.request("/v1/auth/login", {"email": "benny@example.se", "password": "ett-losenord-till"})
        self.assertEqual(200, status, payload)
        cookie = SimpleCookie()
        cookie.load(headers["Set-Cookie"])
        return "; ".join(f"{key}={morsel.value}" for key, morsel in cookie.items())

    def test_the_driver_gets_the_pages_of_the_duty_from_the_server(self):
        benny = self.login()
        self.assertEqual(["81", "82", "132"], [d["number"] for d in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]])
        found = self.request("/v1/driver/duties/duty-81", cookie=benny)[1]["duty"]
        self.assertEqual({"cloud": True, "pages": [3, 5], "part": True}, found["source"])
        status, body, headers = self.request("/v1/driver/duties/duty-81.pdf", cookie=benny)
        self.assertEqual((200, PART_81), (status, body))
        self.assertIn('filename="Tjanst-81.pdf"', headers["Content-Disposition"])
        # Sidnummer utan egen del: ingen PDF att hämta, men sidorna står i tjänsten.
        self.assertEqual({"cloud": True, "pages": [9, 9], "part": False},
                         self.request("/v1/driver/duties/duty-82", cookie=benny)[1]["duty"]["source"])
        self.assertEqual(404, self.request("/v1/driver/duties/duty-82.pdf", cookie=benny)[0])
        self.assertEqual(401, self.request("/v1/driver/duties/duty-81.pdf")[0])
        self.assertEqual(404, self.request("/v1/driver/duties/..%2F..%2Ftrainmeet.db.pdf", cookie=benny)[0])

    def test_a_meet_with_only_the_drivers_serves_its_pages_and_screens(self):
        benny = self.login()
        session = self.request("/v1/driver/session", cookie=benny)[1]
        self.assertEqual(([], True), (session["stations"], session["clock"]["running"]))
        status, display, _ = self.request("/v1/display")
        self.assertEqual(200, status)
        self.assertEqual([], display["stations"])
        for path in ("/", "/lokforaren/", "/display/clock"):
            self.assertEqual(200, self.request(path)[0], path)


if __name__ == "__main__":
    unittest.main()
