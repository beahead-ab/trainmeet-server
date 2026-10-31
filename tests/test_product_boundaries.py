"""De definitiva produktbesluten, som tester i stället för bara text.

Ett dokument som säger "servern tolkar aldrig PDF:er" hindrar ingen från att
lägga till en PDF-tolk. Det här gör det. Varje test här motsvarar en rad i
docs/cloud-server.md och faller om gränsen suddas ut.

De beslut som ännu inte är implementerade står som ⛔ i checklistan och har
medvetet *inget* test här: ett test som påstår att nuläget är rätt vore värre
än inget test alls.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway"


def _code_only(text: str) -> str:
    """Källan utan kommentarer och docstrings.

    Ett negativt test som läser hela filen faller på sin egen förklaring: den
    här filen *nämner* de ord den förbjuder, och det har hänt förut.
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body[0].value.value = ""
    stripped = ast.unparse(tree)
    return "\n".join(line.split("#")[0] for line in stripped.splitlines())


def _python_sources() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(SOURCE.rglob("*.py"))]


class NoDocumentInterpretationTests(unittest.TestCase):
    """PDF, JPG och PNG tolkas endast i TrainMeet Cloud."""

    #: Bibliotek och verktyg som bara finns för att läsa ut innehåll ur ett
    #: underlag. Ett av dem i servern betyder att tolkningen börjat flytta hit.
    INTERPRETERS = (
        "pdftoppm", "pdfplumber", "pypdf", "PyPDF2", "fitz", "pdfminer",
        "pytesseract", "tesseract", "PIL", "Pillow", "cv2",
    )

    def test_no_document_interpreting_dependency_is_imported(self):
        for path, text in _python_sources():
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    self.assertNotIn(
                        root,
                        self.INTERPRETERS,
                        f"{path.name} importerar {name}. Tolkning hör hemma i "
                        "trainmeet-cloud - se docs/cloud-server.md.",
                    )

    def test_the_manual_import_accepts_a_json_operating_package(self):
        """"Importerad fil" är ett driftpaket, aldrig ett underlag.

        Den validerar mot RuntimePublication.parse, som är samma parser Cloud
        publicerar igenom. Ett filformat till här vore en andra väg in.
        """
        http_server = (SOURCE / "http_server.py").read_text(encoding="utf-8")
        self.assertIn("RuntimePublication.parse(package)", http_server)


class SyncGoesOneWayTests(unittest.TestCase):
    """Synk går endast Cloud → Server. Serverns ändringar skickas aldrig upp."""

    def test_every_request_to_cloud_is_a_read(self):
        """urllib gör en POST så fort `data` sätts - även utan method=.

        Det är den enda raden som skulle behöva ändras för att servern skulle
        börja skriva uppåt, så det är den raden testet vaktar.
        """
        text = (SOURCE / "central_sync.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        requests = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Request"
        ]
        self.assertTrue(requests, "hittade inga Request(...) att kontrollera")
        for call in requests:
            keywords = {keyword.arg for keyword in call.keywords}
            self.assertNotIn("data", keywords, "en Request med data blir en POST till Cloud")
            self.assertNotIn("method", keywords, "en Request med method kan bli PUT/POST")

    def test_nothing_posts_a_configuration_anywhere(self):
        for path, text in _python_sources():
            if path.name == "software_update.py":
                continue  # hämtar releaser från GitHub, inte träffdata till Cloud
            self.assertNotIn(
                "urlopen(Request(",
                text.replace(" ", ""),
                f"{path.name}: kontrollera att anropet är en läsning",
            )


class MailIsTheOnlyWriteTests(unittest.TestCase):
    """Undantagen från D1, och hur smala de är.

    Två saker får gå uppåt, var och en i sin egen modul med ett enda anrop:
    ett brev (cloud_mail.py: en inbjudan eller en kod för nytt lösenord) och
    livetrafiken från klockan (cloud_live.py, Caspers beslut 29 oktober, se
    LiveTrafficIsNarrowTests). Inga träffdata, ingen konfiguration och ingen
    historik.
    """

    def test_one_request_in_one_module_writes_to_cloud(self):
        writers = []
        for path, text in _python_sources():
            for node in ast.walk(ast.parse(text)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Request"
                        and {keyword.arg for keyword in node.keywords} & {"data", "method"}):
                    writers.append(path.relative_to(SOURCE).as_posix())
        # Inte Cloud: GitHub-releaser, klubbens snabbklocka och den uttryckligt
        # loopbackbegränsade simulatorn. Matcha exakt sökväg, inte bara filnamn.
        # cloud_live.py är det smala undantaget från D1 för klockans livetrafik (#205/#206).
        not_cloud = {"software_update.py", "external_clock.py", "integrations/http_sandbox.py"}
        self.assertEqual(["cloud_live.py", "cloud_mail.py"], [name for name in writers if name not in not_cloud])

    def test_the_sandbox_writer_rejects_cloud_and_other_external_origins(self):
        from tmbox_gateway.integrations.http_sandbox import HttpSandboxAdapter

        for origin in ("https://cloud.trainmeet.app", "http://example.invalid:80",
                       "http://localhost:80", "http://127.0.0.1:80@cloud.trainmeet.app"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                HttpSandboxAdapter(origin, "synthetic-account", "synthetic-password")

    def test_the_letter_has_a_fixed_set_of_fields(self):
        from tmbox_gateway.cloud_mail import MAIL_FIELDS

        # Inget användarnamn: kontot är mottagarens adress.
        self.assertEqual({"kind", "to", "code", "server_url", "language"}, set(MAIL_FIELDS))
        text = (SOURCE / "cloud_mail.py").read_text(encoding="utf-8")
        self.assertIn("if key in MAIL_FIELDS", text)
        self.assertIn('"/api/server-mail"', text.replace("{path}", ""))


class LiveTrafficIsNarrowTests(unittest.TestCase):
    """Det andra undantaget från D1: livetrafik från klockan (#205, #206).

    Casper beslutade den 29 oktober om en egen kanal bara för livetrafik från
    klockan: TMBox-anrop genom reläet och push-händelser. Testerna här låser
    vad som får gå den vägen. En väg eller ett fält till ska vara ett nytt
    beslut, inte en rad kod som ingen märker.
    """

    def test_only_the_tmbox_calls_go_through_the_relay(self):
        from tmbox_gateway.cloud_live import RELAY_ROUTES
        self.assertEqual({
            ("GET", "/v1/tmbox/terminal"), ("POST", "/v1/tmbox/terminal"),
            ("GET", "/v1/tmbox/terminal/timetable"), ("GET", "/v1/clock"),
            ("POST", "/v1/tmbox/push"), ("POST", "/v1/tmbox/answer"),
        }, set(RELAY_ROUTES))

    def test_a_push_event_carries_only_what_the_notice_needs(self):
        from tmbox_gateway.cloud_live import EVENT_FIELDS, PUSH_KINDS, RECIPIENT_FIELDS
        from tmbox_gateway.terminal16 import ATTENTION_KINDS
        self.assertEqual({"event_id", "kind", "train_number", "from_station", "to_station", "request_id",
                          "proof", "language", "expires_in", "recipients"}, set(EVENT_FIELDS))
        self.assertEqual({"device", "token", "environment"}, set(RECIPIENT_FIELDS))
        self.assertEqual(ATTENTION_KINDS, PUSH_KINDS, "bara de händelser boxen redan låter för")

    def test_it_talks_to_two_cloud_addresses_and_nothing_else(self):
        code = _code_only((SOURCE / "cloud_live.py").read_text(encoding="utf-8"))
        addresses = {line.split("/api/")[1].split('"')[0].split("'")[0] for line in code.splitlines() if "/api/" in line}
        self.assertEqual({"server-relay", "server-push"}, addresses)
        for forbidden in ("runtime_store.active(", "local_config", "meet_data", "package", "history"):
            self.assertNotIn(forbidden, code, f"cloud_live.py rör {forbidden}")

    def test_it_is_off_until_an_administrator_turns_it_on(self):
        import tempfile
        from types import SimpleNamespace
        from tmbox_gateway.cloud_live import LiveChannel
        from tmbox_gateway.runtime import SQLiteRuntimeStore
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.db")
            try:
                store.save_link_token("kopplad")
                channel = LiveChannel(SimpleNamespace(runtime_store=store, config=SimpleNamespace(central_runtime_url="https://cloud.trainmeet.app/config")))
                self.assertFalse(channel.enabled())
                self.assertEqual("off", channel.status()["relay"]["state"])
            finally:
                store.close()


class TimetableStaysEditableTests(unittest.TestCase):
    """BYGG steg 3 är redigerbart även när grundrevisionen kommer från Cloud.

    Den här klassen pinnade tidigare *var grinden satt*, medan beslutet ännu
    inte var genomfört. Nu är det genomfört, så den pinnar i stället att den
    gamla globala grinden inte kan smyga tillbaka: en enda `cloud_linked` över
    hela utkastet gör tidtabellen oredigerbar igen utan att någon märker det
    förrän ett tåg är sent.

    Beteendet i sig ligger i test_operating_modes; det här är vakten mot
    återfall.
    """

    def test_the_global_editing_gate_is_gone(self):
        text = (SOURCE / "http_server.py").read_text(encoding="utf-8")
        self.assertNotIn("_require_editing_open", text)

    def test_the_gate_that_remains_is_about_the_line_only(self):
        text = (SOURCE / "http_server.py").read_text(encoding="utf-8")
        self.assertIn("def _require_topology_unchanged", text)
        self.assertIn('TOPOLOGY_SECTIONS = ("stations", "connections", "panels")', text)
        self.assertNotIn('"trains"', text.split("TOPOLOGY_SECTIONS =")[1].split(")")[0])


if __name__ == "__main__":
    unittest.main()


class ServerHasNoAccountMailTests(unittest.TestCase):
    """Kontohanteringen hör hemma i Cloud, inte här.

    TrainMeet Cloud har fått e-posttjänst, lösenordsåterställning, inbjudningar
    och organisationer. Servern ska inte ha någon del av det, och skälet är
    inte att det vore svårt - det är att servern står på en Raspberry Pi i en
    lokal, ofta utan utgående nät, och att en andra kontodatabas är en andra
    plats där någons lösenord kan läcka.

    Servern har exakt en administratör, satt vid installationen, och den
    identiteten räcker för det servern gör.
    """

    def test_the_server_never_sends_mail(self):
        for path, text in _python_sources():
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertNotIn(
                        name.split(".")[0],
                        {"smtplib", "email"},
                        f"{path.name} importerar {name}. E-post hör hemma i trainmeet-cloud.",
                    )

    def test_there_is_no_password_reset_or_invitation_route(self):
        text = (SOURCE / "http_server.py").read_text(encoding="utf-8")
        for route in ("/v1/auth/forgot", "/v1/auth/reset", "/v1/users/invite",
                      "/v1/auth/accept-invitation"):
            self.assertNotIn(route, text, f"{route} hör hemma i Cloud")

    def test_there_is_no_second_account_model(self):
        """Servern har en administratör, inte roller och organisationer."""
        for path, text in _python_sources():
            code = _code_only(text)
            for token in ("organization_id", "memberships", "invite_user"):
                self.assertNotIn(
                    token, code,
                    f"{path.name} innehåller {token} - kontomodellen hör hemma i Cloud",
                )


class TellurianIsALocalServiceTests(unittest.TestCase):
    """Tellurian Dispatch (#222) är en lokal tjänst på träffens nät, inte Cloud.

    D1 gäller oförändrat: klareringstrafik går aldrig till TrainMeet Cloud, och
    Cloud-modulerna anropar aldrig Tellurian. Servern på Pi:n ansluter till en
    lokal adress; en publik adress bara med uttryckligt val av testinstans.
    """

    def test_the_tellurian_client_rejects_cloud_and_public_origins(self):
        from tmbox_gateway.integrations.tellurian import AddressRejected, check_address

        for origin in ("https://cloud.trainmeet.app", "https://server.trainmeet.app", "https://trainmeet.app"):
            for test_instance in (False, True):
                with self.subTest(origin=origin, test_instance=test_instance), self.assertRaises(AddressRejected):
                    check_address(origin, test_instance=test_instance)
        for origin in ("http://example.invalid:80", "http://127.0.0.1:80", "http://localhost:5000", "https://8.8.8.8"):
            with self.subTest(origin=origin), self.assertRaises(AddressRejected):
                check_address(origin)

    def test_only_the_integration_adapters_open_raw_http_connections(self):
        users = sorted(path.relative_to(SOURCE).as_posix() for path, text in _python_sources()
                       if "HTTPConnection" in _code_only(text) or "http.client" in _code_only(text))
        self.assertEqual(["integrations/http_sandbox.py", "integrations/tellurian.py"], users)

    def test_cloud_modules_never_reach_tellurian(self):
        for name in ("central_sync.py", "cloud_config.py", "cloud_mail.py"):
            self.assertNotIn("tellurian", _code_only((SOURCE / name).read_text(encoding="utf-8")).lower(), name)

    def test_shadow_mode_and_the_report_have_no_write_path(self):
        """Skugga och Testa anslutning är bara läsning; skrivvägen är inte inkopplad (etapp 4)."""
        tree = ast.parse((SOURCE / "integrations" / "tellurian.py").read_text(encoding="utf-8"))
        readers = [node for node in tree.body if getattr(node, "name", "") in ("ShadowRunner", "Mirror", "preflight")]
        self.assertEqual(3, len(readers))
        for node in readers:
            calls = {child.func.attr for child in ast.walk(node)
                     if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)}
            self.assertNotIn("post", calls, node.name)
        for name in ("http_server.py", "integrations/tellurian_service.py"):
            code = _code_only((SOURCE / name).read_text(encoding="utf-8"))
            self.assertNotIn("TellurianCommands", code, name)
            self.assertNotIn(".post(", code, name)
