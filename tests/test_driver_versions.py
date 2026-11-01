"""Nya versioner av förartjänster från foton av pappret (#225).

Pappret förarna har och köruppdraget på servern kan vara olika versioner av
planen. En administratör eller ägare fotar pappret, Cloud tolkar fotona och
förslaget blir en ny version först när administratören sparar det. En förare
som redan kör tjänsten byts aldrig: Lokföraren frågar.

Inget prov når Cloud: tolkningen är en fejk, och den körs direkt i stället för
i en tråd.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.central_sync import CentralSyncError
from tmbox_gateway.driver_versions import MAX_SCAN_PAGES, DriverVersions, photos_pdf
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore

JPEG_1 = b"\xff\xd8\xff\xe0" + b"\x01" * 200 + b"\xff\xd9"
JPEG_2 = b"\xff\xd8\xff\xe0" + b"\x02" * 300 + b"\xff\xd9"

#: Tjänst 7 som den står på pappret: andra tåg, andra tider.
PAPER_7 = {
    "number": "7", "operator": "Green Cargo fjärr", "vehicle": "GC 07 185 Br", "vmax": 100,
    "start": "03:01", "end": "05:36",
    "trains": [{"number": "G 4007", "from": "Charlottendahl", "to": "Lekeberg", "vmax": 100}],
    "notes": [],
    "events": [
        {"kind": "duty_start", "time": "03:01", "station": "Charlottendahl", "notes": ["Förberedelse"]},
        {"kind": "departure", "time": "03:21", "station": "Charlottendahl", "track": "6", "train": "G 4007",
         "notes": ["Koppla GC 07 185 Br till tåget."]},
        {"kind": "arrival", "time": "05:06", "station": "Lekeberg", "track": "4", "train": "G 4007"},
        {"kind": "duty_end", "time": "05:36", "station": "Lekeberg", "notes": ["Avslutning"]},
    ],
}


class PhotosPdfTests(unittest.TestCase):
    def test_one_page_per_photo_with_a_correct_cross_reference(self):
        pdf = photos_pdf([(JPEG_1, 1200, 1600), (JPEG_2, 1600, 1200)])
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        self.assertEqual(2, pdf.count(b"/Filter /DCTDecode"))
        self.assertIn(JPEG_1, pdf)
        self.assertIn(b"/Count 2", pdf)
        self.assertIn(b"/MediaBox [0 0 595.00 793.33]", pdf)
        xref = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n", 1)[0])
        entries = pdf[xref:].split(b"\n")[3:]
        for number, entry in enumerate(entries[: entries.index(b"trailer")], start=1):
            offset = int(entry.split(b" ")[0])
            self.assertTrue(pdf[offset:].startswith(b"%d 0 obj" % number), number)


class StaleScanTests(unittest.TestCase):
    def test_a_photo_session_nobody_saved_is_gone_after_a_day(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        versions = DriverVersions(Path(directory.name) / "trainmeet.db")
        self.addCleanup(versions.close)
        scan = versions.create_scan("meet", {"id": "duty-7", "number": "7"}, None)
        import io
        versions.add_page(scan["id"], "meet", io.BytesIO(JPEG_1), len(JPEG_1), width=10, height=10)
        self.assertEqual(1, len(list(versions.photos_folder.glob("*.jpg"))))
        self.assertEqual(0, versions.purge_stale_scans())
        self.assertEqual(1, versions.purge_stale_scans(datetime.now(timezone.utc) + timedelta(hours=25)))
        self.assertEqual([], list(versions.photos_folder.glob("*.jpg")))


class StaleReadingTests(unittest.TestCase):
    def test_a_reading_that_was_lost_can_be_started_again(self):
        """Startas servern om medan Cloud läser står fotograferingen kvar som
        'reading'. Efter sex minuter får den läsas igen."""
        import io
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        versions = DriverVersions(Path(directory.name) / "trainmeet.db")
        self.addCleanup(versions.close)
        scan = versions.create_scan("meet", {"id": "duty-7", "number": "7"}, None)
        versions.add_page(scan["id"], "meet", io.BytesIO(JPEG_1), len(JPEG_1), width=10, height=10)
        versions.begin_read(scan["id"], "meet")
        with self.assertRaises(Exception) as caught:
            versions.begin_read(scan["id"], "meet")
        self.assertEqual("scan_reading", getattr(caught.exception, "code", None))
        stale = (datetime.now(timezone.utc) - timedelta(minutes=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        versions._connection.execute("UPDATE driver_scans SET read_at = ? WHERE scan_id = ?", (stale, scan["id"]))
        self.assertEqual("reading", versions.begin_read(scan["id"], "meet")[0]["status"])


class DutyPhotoHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        database = self.directory / "trainmeet.db"
        self.identities = IdentityStore(database)
        self.runtime = SQLiteRuntimeStore(database)
        self.operations = SQLiteOperationsStore(database)
        for store in (self.operations, self.runtime, self.identities):
            self.addCleanup(store.close)
        self.identities.create_first_owner("Casper", "casper@example.se", "ett-langt-losenord")
        for name in ("Anna", "Benny"):
            invited = self.identities.invite_admin_user(name, f"{name.lower()}@example.se", "admin" if name == "Anna" else "user")
            self.identities.redeem_admin_setup(f"{name.lower()}@example.se", str(invited["setup_code"]), "ett-losenord-till")
        self.runtime.complete_installation()
        package = runtime_package_v3()
        self.stations = [station["name"] for station in package["stations"]]
        package["driver_duties"] = [{
            "id": "duty-7", "number": "7", "events": [
                {"kind": "departure", "time": "05:10", "station": self.stations[0], "train": "Kp 8230"},
                {"kind": "arrival", "time": "05:22", "station": self.stations[-1], "train": "Kp 8230"},
            ],
        }]
        publication = self.runtime.install(package)
        self.operations.ensure_publication(publication)
        self.operations.start_clock(time_value="01:00:00", now=datetime.now(timezone.utc))
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.app = TrainMeetHTTPApplication(
            engine, self.identities, PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True, state_dir=directory.name,
                             central_runtime_url="https://cloud.example/config"),
            runtime_store=self.runtime, operations_store=self.operations,
        )
        self.sent: list[tuple[str, str, dict]] = []
        self.answer: dict | Exception = {"duty": PAPER_7, "warnings": ["Sida 2 är lite suddig"], "pages": 2}

        def reader(token, url, payload):
            self.sent.append((token, url, payload))
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        self.app.duty_photo_reader = reader
        self.app.run_duty_scan = lambda job: job()
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def link(self) -> None:
        self.runtime.save_link_token("kopplingsnyckel")

    def request(self, path: str, body: dict | None = None, *, cookie: str | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                          headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
        try:
            with urlopen(request) as response:
                raw = response.read()
                if response.headers.get_content_type() == "application/json":
                    return response.status, json.loads(raw)
                return response.status, {"raw": raw, "type": response.headers.get_content_type()}
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def photo(self, scan_id: str, data: bytes, *, cookie: str, kind: str = "image/jpeg",
              width: str = "1200", height: str = "1600") -> tuple[int, dict]:
        request = Request(f"{self.base}/v1/driver-admin/scans/{scan_id}/pages", data=data, method="POST",
                          headers={"Content-Type": kind, "X-Width": width, "X-Height": height, "Cookie": cookie})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def login(self, name: str) -> str:
        password = "ett-langt-losenord" if name == "casper" else "ett-losenord-till"
        request = Request(self.base + "/v1/auth/login", data=json.dumps({"email": f"{name}@example.se", "password": password}).encode(),
                          method="POST", headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            cookie = SimpleCookie()
            cookie.load(response.headers["Set-Cookie"])
        return "; ".join(f"{key}={morsel.value}" for key, morsel in cookie.items())

    def photographed(self, cookie: str, duty_id: str = "duty-7") -> str:
        status, payload = self.request("/v1/driver-admin/scans", {"duty_id": duty_id}, cookie=cookie)
        self.assertEqual(201, status, payload)
        scan_id = payload["scan"]["id"]
        for data in (JPEG_1, JPEG_2):
            self.assertEqual(200, self.photo(scan_id, data, cookie=cookie)[0])
        return scan_id

    def duty(self, cookie: str, duty_id: str = "duty-7") -> dict:
        return self.request(f"/v1/driver/duties/{duty_id}", cookie=cookie)[1]["duty"]

    def test_only_administrators_and_owners_photograph_a_duty(self) -> None:
        benny = self.login("benny")
        self.assertEqual(403, self.request("/v1/driver-admin/scans", {"duty_id": "duty-7"}, cookie=benny)[0])
        self.assertEqual(403, self.request("/v1/driver-admin/duties/duty-7/versions", cookie=benny)[0])
        self.assertEqual(403, self.request("/v1/driver-admin/duties/duty-7/versions/use", {"version": 0}, cookie=benny)[0])
        self.assertEqual(403, self.photo("0123456789abcdef", JPEG_1, cookie=benny)[0])
        for name in ("anna", "casper"):
            status, payload = self.request("/v1/driver-admin/scans", {"duty_id": "duty-7"}, cookie=self.login(name))
            self.assertEqual((201, "7", "draft"), (status, payload["scan"]["number"], payload["scan"]["status"]), name)
        self.assertEqual(404, self.request("/v1/driver-admin/scans", {"duty_id": "demo-132"}, cookie=self.login("anna"))[0],
                         "övningstjänsten får inga versioner")

    def test_the_photos_become_a_new_version_that_a_driver_may_switch_to(self) -> None:
        self.link()
        anna, benny = self.login("anna"), self.login("benny")
        self.request("/v1/driver/active", {"duty_id": "duty-7"}, cookie=benny)

        scan_id = self.photographed(anna)
        status, payload = self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        self.assertEqual(202, status, payload)
        status, payload = self.request(f"/v1/driver-admin/scans/{scan_id}", cookie=anna)
        self.assertEqual(("done", 2), (payload["scan"]["status"], len(payload["scan"]["pages"])))
        proposal = payload["scan"]["result"]["duty"]
        self.assertEqual(("duty-7", "03:01", ["G 4007"]), (proposal["id"], proposal["start"], [t["number"] for t in proposal["trains"]]))
        self.assertEqual(["Sida 2 är lite suddig"], payload["scan"]["result"]["warnings"])
        self.assertEqual("05:10", payload["current"]["start"], "tjänsten som den är nu, att jämföra med")

        # Inget har ändrats än.
        self.assertEqual("05:10", self.duty(benny)["start"])
        self.assertIsNone(self.request("/v1/driver/presence", cookie=benny)[1]["duty_update"])

        status, payload = self.request(f"/v1/driver-admin/scans/{scan_id}/save", {}, cookie=anna)
        self.assertEqual(200, status, payload)
        self.assertEqual((1, "Anna", 2, True), (payload["version"]["version"], payload["version"]["created_by_name"],
                                                payload["version"]["pages"], payload["version"]["has_pdf"]))
        self.assertEqual([], list((self.directory / "lokforaren" / "foton").glob("*.jpg")), "fotona är förlagan nu")

        duty = self.duty(benny)
        self.assertEqual(("03:01", 1, {"photos": True, "pages": [1, 2], "part": True}),
                         (duty["start"], duty["version"], duty["source"]))
        self.assertEqual("03:01–05:36", "–".join(next(d for d in self.request("/v1/driver/duties", cookie=benny)[1]["duties"]
                                                    if d["id"] == "duty-7")[key] for key in ("start", "end")))
        status, pdf = self.request("/v1/driver/duties/duty-7.pdf", cookie=benny)
        self.assertEqual((200, "application/pdf"), (status, pdf["type"]))
        self.assertIn(JPEG_2, pdf["raw"])

        # Benny kör den gamla tills han själv byter.
        session = self.request("/v1/driver/session", cookie=benny)[1]
        self.assertEqual("05:10", session["active"]["duty"]["start"])
        self.assertEqual({"duty_id": "duty-7", "number": "7", "version": 1},
                         {key: session["duty_update"][key] for key in ("duty_id", "number", "version")})
        self.assertEqual(1, self.request("/v1/driver/presence", cookie=benny)[1]["duty_update"]["version"])
        active = self.request("/v1/driver/active", {"duty_id": "duty-7"}, cookie=benny)[1]["active"]
        self.assertEqual(("03:01", 1), (active["duty"]["start"], active["duty"]["version"]))
        self.assertIsNone(self.request("/v1/driver/presence", cookie=benny)[1]["duty_update"])

        # Planeringsfilens version kan gälla igen.
        versions = self.request("/v1/driver-admin/duties/duty-7/versions", cookie=anna)[1]
        self.assertEqual((1, [1]), (versions["current"], [item["version"] for item in versions["versions"]]))
        status, versions = self.request("/v1/driver-admin/duties/duty-7/versions/use", {"version": 0}, cookie=anna)
        self.assertEqual((200, 0), (status, versions["current"]))
        self.assertEqual(("05:10", 0), (self.duty(benny)["start"], self.duty(benny)["version"]))
        self.assertEqual(0, self.request("/v1/driver/presence", cookie=benny)[1]["duty_update"]["version"])
        self.assertEqual(404, self.request("/v1/driver-admin/duties/duty-7/versions/use", {"version": 5}, cookie=anna)[0])

    def test_the_request_to_cloud_carries_the_photos_and_spelling_help_only(self) -> None:
        self.link()
        anna = self.login("anna")
        scan_id = self.photographed(anna)
        self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        [(token, url, payload)] = self.sent
        self.assertEqual(("kopplingsnyckel", "https://cloud.example/config"), (token, url))
        self.assertEqual({"pages", "number", "stations", "language"}, set(payload))
        self.assertEqual("7", payload["number"])
        self.assertEqual(2, len(payload["pages"]))
        self.assertIn(self.stations[0], payload["stations"])

    def test_without_a_link_to_cloud_the_photos_are_not_read(self) -> None:
        anna = self.login("anna")
        scan_id = self.photographed(anna)
        status, payload = self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        self.assertEqual((409, "cloud_link_required"), (status, payload["error"]))
        self.assertEqual([], self.sent)

    def test_a_failed_reading_is_shown_and_can_be_tried_again(self) -> None:
        self.link()
        anna = self.login("anna")
        scan_id = self.photographed(anna)
        self.answer = CentralSyncError("AI är inte inställd i TrainMeet Cloud")
        self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        scan = self.request(f"/v1/driver-admin/scans/{scan_id}", cookie=anna)[1]["scan"]
        self.assertEqual(("failed", "AI är inte inställd i TrainMeet Cloud"), (scan["status"], scan["message"]))
        self.assertEqual(409, self.request(f"/v1/driver-admin/scans/{scan_id}/save", {}, cookie=anna)[0])
        self.answer = {"duty": {"number": "7", "events": []}, "warnings": []}
        self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        scan = self.request(f"/v1/driver-admin/scans/{scan_id}", cookie=anna)[1]["scan"]
        self.assertEqual(("failed", "Inga tåg med tider gick att läsa på fotona"), (scan["status"], scan["message"]))
        self.answer = {"duty": PAPER_7, "warnings": []}
        self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        self.assertEqual("done", self.request(f"/v1/driver-admin/scans/{scan_id}", cookie=anna)[1]["scan"]["status"])

    def test_only_jpeg_pages_with_a_size_and_not_too_many(self) -> None:
        self.link()
        anna = self.login("anna")
        scan_id = self.request("/v1/driver-admin/scans", {"duty_id": "duty-7"}, cookie=anna)[1]["scan"]["id"]
        self.assertEqual(415, self.photo(scan_id, b"\x89PNG\r\n\x1a\n" + b"0" * 64, cookie=anna, kind="image/png")[0])
        self.assertEqual(415, self.photo(scan_id, b"GIF89a" + b"0" * 64, cookie=anna)[0])
        self.assertEqual(400, self.photo(scan_id, JPEG_1, cookie=anna, width="0")[0])
        self.assertEqual(400, self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)[0], "inga sidor")
        for _ in range(MAX_SCAN_PAGES):
            self.assertEqual(200, self.photo(scan_id, JPEG_1, cookie=anna)[0])
        self.assertEqual(400, self.photo(scan_id, JPEG_1, cookie=anna)[0])
        self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        self.assertEqual(409, self.photo(scan_id, JPEG_1, cookie=anna)[0], "sidorna är redan tolkade")

    def test_a_discarded_photo_session_leaves_nothing_behind(self) -> None:
        anna = self.login("anna")
        scan_id = self.photographed(anna)
        self.assertEqual(200, self.request(f"/v1/driver-admin/scans/{scan_id}/discard", {}, cookie=anna)[0])
        self.assertEqual(404, self.request(f"/v1/driver-admin/scans/{scan_id}", cookie=anna)[0])
        self.assertEqual([], list((self.directory / "lokforaren" / "foton").glob("*.jpg")))

    def test_another_number_on_the_paper_keeps_the_duty_it_was_taken_for(self) -> None:
        self.link()
        anna, benny = self.login("anna"), self.login("benny")
        self.answer = {"duty": {**PAPER_7, "number": "25"}, "warnings": ["Pappret säger tjänst 25, inte 7"]}
        scan_id = self.photographed(anna)
        self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        scan = self.request(f"/v1/driver-admin/scans/{scan_id}", cookie=anna)[1]["scan"]
        self.assertEqual(("7", ["Pappret säger tjänst 25, inte 7"]), (scan["result"]["duty"]["number"], scan["result"]["warnings"]))
        self.request(f"/v1/driver-admin/scans/{scan_id}/save", {}, cookie=anna)
        self.assertEqual(("7", "03:01"), (self.duty(benny)["number"], self.duty(benny)["start"]))
        self.assertEqual(["7"], [d["number"] for d in self.request("/v1/driver/duties", cookie=benny)[1]["duties"] if not d["demo"]])

    def test_a_duty_from_the_plan_file_gets_a_version_too(self) -> None:
        self.link()
        anna, benny = self.login("anna"), self.login("benny")
        duty = {"number": "24", "trains": [{"number": "Kp 8230", "from": "A", "to": "B"}],
                "events": [{"kind": "departure", "time": "05:10", "station": "A", "train": "Kp 8230"},
                           {"kind": "arrival", "time": "05:22", "station": "B", "train": "Kp 8230"}]}
        status, payload = self.request("/v1/driver-admin/documents", {
            "filename": "Värnamo.json", "fingerprint": "a1b2c3d4e5f60718", "name": "Värnamo", "duties": [duty], "warnings": []},
            cookie=anna)
        document_id = payload["document"]["id"]
        self.request(f"/v1/driver-admin/documents/{document_id}/publish", {"published": True}, cookie=anna)
        duty_id = f"k{document_id}-24"
        self.answer = {"duty": {**PAPER_7, "number": "24"}, "warnings": []}
        scan_id = self.photographed(anna, duty_id)
        self.request(f"/v1/driver-admin/scans/{scan_id}/read", {}, cookie=anna)
        self.request(f"/v1/driver-admin/scans/{scan_id}/save", {}, cookie=anna)
        self.assertEqual(("03:01", 1), (self.duty(benny, duty_id)["start"], self.duty(benny, duty_id)["version"]))
        status, pdf = self.request(f"/v1/driver/duties/{quote(duty_id)}.pdf", cookie=benny)
        self.assertEqual((200, True), (status, JPEG_1 in pdf["raw"]))


if __name__ == "__main__":
    unittest.main()
