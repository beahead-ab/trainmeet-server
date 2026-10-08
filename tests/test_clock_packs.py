"""Egna klockor: klockpaket som laddas upp till servern (Casper 2026-10-08).

"kan du göra så att jag kan ladda upp sbb klockan som en egen klocka. Bra sätt
att utveckla plugin för klocka." Ett klockpaket (.tmclock) är en zip med
clock.json och urtavlans lager som SVG eller PNG. Servern kontrollerar paketet
(inga skript, inga länkar ut, kvadratiska lager), sparar det och visar det som
en stil "custom:<id>". Den som laddar upp intygar rätten att använda tavlan.
Den inbyggda schweiziska tavlan (SBB) följer inte med längre.
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

import test_cloud_only_http as fixture
from test_pending_revisions import dispatch_request
from tmbox_gateway import clock_pack
from tmbox_gateway.clock_pack import ClockPackError, read_pack
from tmbox_gateway.http_server import HTTPAPIError, TrainMeetHTTPServer
from tmbox_gateway.identity import DeviceKind, PairedClient
from tmbox_gateway.runtime import AVAILABLE_CLOCK_STYLES, MAX_CLOCK_FACES

SQUARE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">{}</svg>'
HAND = SQUARE.format('<rect x="99" y="20" width="2" height="80"/>')


def manifest(**changes):
    value = {"format": 1, "name": "Mitt ur", "version": "1.0",
             "layers": {"dial": "dial.svg", "hour": "hour.svg", "minute": "minute.svg", "second": "second.svg"},
             "motion": {"minute": "jump", "minute_bounce": True, "second": "sweep", "sweep_seconds": 58.5}}
    value.update(changes)
    return value


def pack(files=None, *, prefix="", **manifest_changes) -> bytes:
    contents = {"clock.json": json.dumps(manifest(**manifest_changes)), "dial.svg": SQUARE.format('<circle cx="100" cy="100" r="96" fill="#fff"/>'),
                "hour.svg": HAND, "minute.svg": HAND, "second.svg": HAND}
    contents.update(files or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in contents.items():
            if text is not None:
                archive.writestr(prefix + name, text)
    return buffer.getvalue()


def png(width, height) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\x00" * width * 4 for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class ClockPackFormatTests(unittest.TestCase):
    def rejected(self, data, message):
        with self.assertRaises(ClockPackError) as caught:
            read_pack(data)
        self.assertIn(message, str(caught.exception))

    def test_a_pack_gives_its_layers_and_how_the_hands_move(self):
        result = read_pack(pack())
        self.assertEqual((result.id, result.name, result.version), ("mitt-ur", "Mitt ur", "1.0"))
        self.assertEqual(list(result.layers), ["dial", "hour", "minute", "second"])
        self.assertEqual(result.layers["dial"].content_type, "image/svg+xml")
        self.assertEqual(result.motion, {"hour": "smooth", "minute": "jump", "minute_bounce": True, "second": "sweep", "sweep_seconds": 58.5})

    def test_the_example_pack_that_ships_with_the_server_is_valid(self):
        result = read_pack(clock_pack.example_pack())
        self.assertEqual((result.id, sorted(result.layers)), ("exempelur", ["dial", "hour", "minute", "second", "top"]))
        self.assertEqual(clock_pack.example_pack(), clock_pack.example_pack(), "built the same way every time")

    def test_a_pack_zipped_as_a_folder_on_a_mac_is_read(self):
        data = pack({"__MACOSX/._clock.json": "x", ".DS_Store": "x"}, prefix="mitt-ur/")
        self.assertEqual(read_pack(data).name, "Mitt ur")

    def test_svg_that_could_run_or_fetch_something_is_refused(self):
        cases = {
            "<script>": SQUARE.format("<script>alert(1)</script>"),
            "onload": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" onload="alert(1)"/>',
            "<foreignObject>": SQUARE.format("<foreignObject><div/></foreignObject>"),
            "länkar till andra filer": SQUARE.format('<image href="https://example.com/x.png" width="10" height="10"/>'),
            "skriptlänk": SQUARE.format('<use href="#a" fill="javascript:alert(1)"/>'),
            "DOCTYPE": '<!DOCTYPE svg [<!ENTITY a "aaaa">]>' + SQUARE.format("&a;"),
            "stilen hämtar": SQUARE.format("<style>@import url(https://example.com/a.css);</style>"),
            "<animate>": SQUARE.format('<rect><animate attributeName="x" to="5"/></rect>'),
        }
        for message, svg in cases.items():
            with self.subTest(message):
                self.rejected(pack({"hour.svg": svg}), message)

    def test_inline_pictures_and_internal_references_are_allowed(self):
        tiny = base64.b64encode(png(64, 64)).decode()
        svg = SQUARE.format(f'<defs><linearGradient id="g"/></defs><rect fill="url(#g)" width="10" height="10"/>'
                            f'<image href="data:image/png;base64,{tiny}" width="10" height="10"/><use href="#g"/>')
        self.assertIn("dial", read_pack(pack({"dial.svg": svg})).layers)

    def test_layers_must_be_square_so_the_hands_turn_about_the_centre(self):
        self.rejected(pack({"dial.svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"/>'}), "kvadratisk")
        self.rejected(pack({"dial.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>'}), "viewBox saknas")
        self.rejected(pack({"dial.png": png(64, 32)}, layers={"dial": "dial.png", "hour": "hour.svg", "minute": "minute.svg"}), "kvadratisk")
        self.assertEqual(read_pack(pack({"dial.png": png(128, 128)}, layers={"dial": "dial.png", "hour": "hour.svg", "minute": "minute.svg"}))
                         .layers["dial"].content_type, "image/png")

    def test_a_broken_or_incomplete_pack_says_what_is_wrong(self):
        self.rejected(b"not a zip", "zip")
        self.rejected(pack({"clock.json": None}), "clock.json saknas")
        self.rejected(pack(layers={"dial": "dial.svg", "hour": "hour.svg"}), "minute saknas")
        self.rejected(pack({"minute.svg": None}), "minute.svg finns inte")
        self.rejected(pack(layers={"dial": "../dial.svg", "hour": "hour.svg", "minute": "minute.svg"}), "utan mappar")
        self.rejected(pack(format=2), "format")
        self.rejected(pack(motion={"second": "spin"}), "motion.second")
        self.rejected(pack(motion={"secnd": "tick"}), "okänd inställning")
        self.rejected(pack(motion={"second": "sweep", "sweep_seconds": 10}), "sweep_seconds")
        self.rejected(pack(motion={"minute_bounce": True}), "minute_bounce")
        self.rejected(pack(id="Mitt Ur!"), "id")

    def test_size_limits_hold_even_when_the_zip_lies_about_sizes(self):
        self.rejected(b"x" * (clock_pack.MAX_PACKAGE_BYTES + 1), "större än 2 MB")
        big = SQUARE.format("<g>" + " " * (clock_pack.MAX_LAYER_BYTES + 10) + "</g>")
        self.rejected(pack({"dial.svg": big}), "större än 1024 kB")

    def test_the_command_line_builds_and_checks_a_pack_from_the_example(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "mitt-ur"
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(clock_pack.main(["example", str(folder)]), 0)
                self.assertEqual(clock_pack.main(["check", str(folder)]), 0)
                self.assertEqual(clock_pack.main(["build", str(folder)]), 0)
            self.assertIn("OK: Exempelur 1.0 (id exempelur)", output.getvalue())
            self.assertEqual(read_pack((Path(directory) / "mitt-ur.tmclock").read_bytes()).id, "exempelur")
            (folder / "hour.svg").write_text(SQUARE.format("<script/>"))
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(clock_pack.main(["check", str(folder)]), 1)
            self.assertIn("<script>", errors.getvalue())


class ClockFaceServerTests(unittest.TestCase):
    setUp = fixture.CloudOnlyDeliveryTests.setUp
    fetch = fixture.CloudOnlyDeliveryTests.fetch
    connect = fixture.CloudOnlyDeliveryTests.connect

    def upload(self, data=None, client=None, **payload):
        body = {"file_name": "mitt-ur.tmclock", "data": base64.b64encode(data or pack()).decode(), "rights_confirmed": True, **payload}
        return self.app.upload_clock_face(client or self.admin, body)

    def audit(self, action):
        rows = self.operations._connection.execute("SELECT detail_json FROM audit_events WHERE action = ?", (action,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def test_an_uploaded_clock_becomes_a_style_every_screen_can_draw(self):
        self.connect()
        result = self.upload()
        self.assertEqual((result["face"]["style"], result["replaced"]), ("custom:mitt-ur", False))
        clock = self.app.display_snapshot()["clock"]
        self.assertIn("custom:mitt-ur", clock["available_styles"])
        face, = clock["faces"]
        sha = read_pack(pack()).sha256
        self.assertEqual(face["layers"]["dial"], f"/v1/clock-faces/mitt-ur/{sha[:16]}/dial")
        self.assertEqual(face["motion"]["second"], "sweep")
        self.assertNotIn("uploaded_by", face, "/v1/display is public: not who uploaded it")
        chosen = self.app.control_clock(self.admin, {"action": "appearance", "style": "custom:mitt-ur", "show_seconds": True})
        self.assertEqual(chosen["style"], "custom:mitt-ur")
        self.assertEqual(self.app.display_snapshot()["clock"]["style"], "custom:mitt-ur")

    def test_the_uploader_confirms_the_right_to_use_the_face_and_it_is_logged(self):
        self.connect()
        with self.assertRaises(HTTPAPIError) as refused:
            self.upload(rights_confirmed=False)
        self.assertEqual((int(refused.exception.status), refused.exception.code), (400, "clock_face_rights"))
        self.assertEqual(self.runtime.clock_faces(), [])
        self.upload()
        logged, = self.audit("clock_face.uploaded")
        self.assertEqual((logged["id"], logged["rights_confirmed"], logged["file_name"]), ("mitt-ur", True, "mitt-ur.tmclock"))
        listed, = self.app.clock_faces_state(self.admin)["faces"]
        self.assertTrue(listed["rights_confirmed_at"])
        self.assertTrue(listed["uploaded_by"])

    def test_only_an_administrator_uploads_lists_or_removes_clocks(self):
        self.connect()
        terminal = PairedClient("terminal", "TKL", DeviceKind.TKL_TERMINAL, ("panel-a",))
        for call in (lambda: self.upload(client=terminal), lambda: self.app.clock_faces_state(terminal),
                     lambda: self.app.delete_clock_face(terminal, {"id": "mitt-ur"})):
            with self.assertRaises(HTTPAPIError) as refused:
                call()
            self.assertEqual(int(refused.exception.status), 403)

    def test_a_bad_pack_is_refused_with_the_reason(self):
        self.connect()
        with self.assertRaises(HTTPAPIError) as refused:
            self.upload(pack({"hour.svg": SQUARE.format("<script/>")}))
        self.assertEqual(refused.exception.code, "invalid_clock_pack")
        self.assertIn("<script>", str(refused.exception))
        with self.assertRaises(HTTPAPIError):
            self.app.upload_clock_face(self.admin, {"data": "not base64!", "rights_confirmed": True})
        self.assertEqual(self.runtime.clock_faces(), [])

    def test_the_same_id_replaces_the_clock_and_gives_new_addresses(self):
        self.connect()
        first = self.upload()["face"]
        second = self.upload(pack(version="1.1"))
        self.assertTrue(second["replaced"])
        self.assertEqual(len(self.runtime.clock_faces()), 1)
        self.assertNotEqual(first["layers"]["dial"], second["face"]["layers"]["dial"], "a new version is fetched anew")
        self.assertIsNone(self.app.clock_face_layer(first["layers"]["dial"]), "the old address is gone")
        content_type, data = self.app.clock_face_layer(second["face"]["layers"]["dial"])
        self.assertEqual((content_type, data[:4]), ("image/svg+xml", b"<svg"))

    def test_removing_the_chosen_clock_falls_back_to_the_station_clock(self):
        self.connect()
        self.upload()
        self.app.control_clock(self.admin, {"action": "appearance", "style": "custom:mitt-ur", "show_seconds": True})
        self.app.delete_clock_face(self.admin, {"id": "mitt-ur"})
        clock = self.app.display_snapshot()["clock"]
        self.assertEqual((clock["style"], clock["faces"]), ("stationsur", []))
        self.assertNotIn("custom:mitt-ur", clock["available_styles"])
        self.assertEqual(len(self.audit("clock_face.deleted")), 1)
        with self.assertRaises(HTTPAPIError) as missing:
            self.app.delete_clock_face(self.admin, {"id": "mitt-ur"})
        self.assertEqual(int(missing.exception.status), 404)

    def test_an_unknown_custom_style_cannot_be_chosen(self):
        self.connect()
        with self.assertRaises(HTTPAPIError):
            self.app.control_clock(self.admin, {"action": "appearance", "style": "custom:finns-inte", "show_seconds": True})
        with self.assertRaises(ValueError):
            self.runtime.save_clock_display_settings("eu", "custom:finns-inte", True)

    def test_a_server_holds_at_most_twenty_clocks(self):
        self.connect()
        for number in range(MAX_CLOCK_FACES):
            self.upload(pack(id=f"ur-{number}"))
        with self.assertRaises(HTTPAPIError) as full:
            self.upload(pack(id="en-till"))
        self.assertIn("Ta bort en först", str(full.exception))
        self.assertTrue(self.upload(pack(id="ur-3", version="2"))["replaced"], "replacing one still works")

    def test_the_http_routes_upload_list_and_remove(self):
        self.connect()
        status, body = dispatch_request(self.app, self.admin, "/v1/clock-faces",
                                        {"data": base64.b64encode(pack()).decode(), "rights_confirmed": True})
        self.assertEqual((int(status), body["face"]["id"]), (200, "mitt-ur"))
        status, body = dispatch_request(self.app, self.admin, "/v1/clock-faces", method="GET")
        self.assertEqual([face["id"] for face in body["faces"]], ["mitt-ur"])
        status, body = dispatch_request(self.app, self.admin, "/v1/clock-faces/delete", {"id": "mitt-ur"})
        self.assertEqual((int(status), body["faces"]), (200, []))


class SwissStyleTests(unittest.TestCase):
    """Den schweiziska tavlan (SBB) är licensbelagd och följer inte med längre."""
    setUp = fixture.CloudOnlyDeliveryTests.setUp
    fetch = fixture.CloudOnlyDeliveryTests.fetch
    connect = fixture.CloudOnlyDeliveryTests.connect

    def test_the_swiss_face_is_not_built_in(self):
        self.assertNotIn("swiss", AVAILABLE_CLOCK_STYLES)
        web = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web"
        for name in ("app.js", "settings.js", "shell-messages.js"):
            text = (web / name).read_text()
            self.assertNotIn("swiss", text.lower(), name)
            self.assertNotIn("SBB", text, name)

    def test_a_package_or_setting_that_says_swiss_shows_the_station_clock(self):
        self.offered["clock"]["available_styles"] = ["swiss", "swedish", "digital"]
        self.connect()
        clock = self.app.display_snapshot()["clock"]
        self.assertEqual(clock["style"], "stationsur", "an older Cloud package lists swiss first")
        self.assertNotIn("swiss", clock["available_styles"])
        self.runtime._save_setting("clock_display:" + self.app._clock_scope(), json.dumps({"style": "swiss", "show_seconds": False}))
        clock = self.app.display_snapshot()["clock"]
        self.assertEqual((clock["style"], clock["show_seconds"]), ("stationsur", False), "a saved choice of swiss")
        with self.assertRaises(HTTPAPIError):
            self.app.control_clock(self.admin, {"action": "appearance", "style": "swiss", "show_seconds": True})


class ClockLayerHTTPTests(unittest.TestCase):
    """Lagren hämtas av skärmarna utan inloggning, som /v1/display."""
    setUp = fixture.CloudOnlyDeliveryTests.setUp
    fetch = fixture.CloudOnlyDeliveryTests.fetch
    connect = fixture.CloudOnlyDeliveryTests.connect

    def serve(self):
        server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_a_layer_is_served_as_a_sandboxed_image_that_may_be_cached(self):
        self.connect()
        face = self.app.upload_clock_face(self.admin, {"data": base64.b64encode(pack()).decode(), "rights_confirmed": True})["face"]
        base = self.serve()
        with urllib.request.urlopen(base + face["layers"]["hour"]) as response:
            headers, body = response.headers, response.read()
        self.assertEqual(body, HAND.encode())
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("immutable", headers["Cache-Control"])
        for missing in ("/v1/clock-faces/mitt-ur/0000000000000000/hour", "/v1/clock-faces/mitt-ur/" + face["layers"]["hour"].split("/")[4] + "/script"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + missing)
            self.assertEqual(error.exception.code, 404)
        with urllib.request.urlopen(base + "/v1/clock-faces/exempelur.tmclock") as response:
            self.assertEqual(read_pack(response.read()).id, "exempelur")
        with urllib.request.urlopen(base + "/assets/clock-face.js") as response:
            self.assertIn(b"TrainMeetClockFace", response.read())


if __name__ == "__main__":
    unittest.main()
