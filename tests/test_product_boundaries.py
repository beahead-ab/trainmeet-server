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
