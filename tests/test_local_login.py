"""Inloggning gäller även på serverdatorn.

Servern gav tidigare full ägarbehörighet till varje webbläsare som nådde den
från maskinen själv. Det var bekvämt så länge det bara fanns en administratör.
När servern fick flera användare med olika roller blev det ett hål: rollgränsen
gällde överallt utom vid tangentbordet, där vem som helst var ägare.

Nu skiljs två frågor åt som legat ihop:

  vem du är   -> inloggning, alltid, oavsett var du står
  var du står -> avgör vad du får göra, till exempel fabriksåterställning

Kvar av den gamla öppningen är bara det fall där en inloggning omöjligt kan
finnas: en installation som ännu inte satt sitt lösenord. Den stänger sig själv
när den första administratören skapas.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import (
    HTTPServerConfig,
    TrainMeetHTTPApplication,
    TrainMeetHTTPServer,
)
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.models import DispatchMode
from tmbox_gateway import recover


class LocalLoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.state = Path(self._dir.name)
        self.identities = IdentityStore(self.state / "identity.db")
        self.addCleanup(self.identities.close)
        self._start(HTTPServerConfig(local_development=True, allow_restart=True))

    def _start(self, config: HTTPServerConfig) -> None:
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            config,
        )
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.application)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def _call(self, path: str, body: dict | None = None, cookie: str = "") -> tuple[int, dict]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def _cookie(self, username: str, password: str) -> str:
        token = self.identities.create_admin_session(username, password)
        self.assertIsNotNone(token, "inloggningen misslyckades")
        return f"trainmeet_admin={token}"

    # ── innan installationen ────────────────────────────────────────────

    def test_an_unfinished_installation_still_opens_on_the_machine(self) -> None:
        """Utan lösenord finns ingen att logga in som. Skulle servern kräva
        inloggning här vore en ny Pi omöjlig att installera."""

        status, payload = self._call("/v1/auth/status")
        self.assertEqual(200, status)
        self.assertEqual("local", payload["access_mode"])

        created, _ = self._call(
            "/v1/setup/admin", {"username": "casper", "password": "ett-langt-losenord"}
        )
        self.assertEqual(HTTPStatus.CREATED, created)

    # ── efter installationen ────────────────────────────────────────────

    def test_the_console_needs_a_login_once_a_password_exists(self) -> None:
        self.identities.configure_admin_access("casper", "ett-langt-losenord")

        status, payload = self._call("/v1/auth/status")
        self.assertFalse(payload["authenticated"])
        self.assertEqual("external", payload["access_mode"])

        denied, _ = self._call("/v1/admin/users")
        self.assertEqual(HTTPStatus.UNAUTHORIZED, denied)

        allowed, listed = self._call(
            "/v1/admin/users", cookie=self._cookie("casper", "ett-langt-losenord")
        )
        self.assertEqual(200, allowed)
        self.assertEqual(["casper"], [user["username"] for user in listed["users"]])

    def test_the_open_installation_closes_with_the_first_administrator(self) -> None:
        """Öppningen är ett tillstånd, inte en inställning: den upphör i samma
        anrop som skapar den första administratören."""

        self._call("/v1/setup/admin", {"username": "casper", "password": "ett-langt-losenord"})
        denied, _ = self._call("/v1/admin/users")
        self.assertEqual(HTTPStatus.UNAUTHORIZED, denied)

    # ── var man står ────────────────────────────────────────────────────

    def test_the_machine_is_still_the_place_a_factory_reset_happens(self) -> None:
        """Platsen avgör inte längre vem man är, men den avgör fortfarande vad
        man får göra. Nollställningen kräver numera båda delarna."""

        self.identities.configure_admin_access("casper", "ett-langt-losenord")
        cookie = self._cookie("casper", "ett-langt-losenord")

        status, payload = self._call("/v1/auth/status", cookie=cookie)
        self.assertTrue(payload["at_the_machine"])

        accepted, _ = self._call(
            "/v1/server/factory-reset", {"confirmation": "NOLLSTÄLL"}, cookie=cookie
        )
        self.assertEqual(HTTPStatus.ACCEPTED, accepted)

    def test_the_same_login_cannot_factory_reset_from_the_network(self) -> None:
        self.identities.configure_admin_access("casper", "ett-langt-losenord")
        cookie = self._cookie("casper", "ett-langt-losenord")
        self.server.shutdown()
        self.server.server_close()
        self._start(
            HTTPServerConfig(local_development=True, allow_restart=True, force_external_auth=True)
        )

        status, payload = self._call("/v1/auth/status", cookie=cookie)
        self.assertTrue(payload["authenticated"])
        self.assertFalse(payload["at_the_machine"])

        denied, error = self._call(
            "/v1/server/factory-reset", {"confirmation": "NOLLSTÄLL"}, cookie=cookie
        )
        self.assertEqual(HTTPStatus.FORBIDDEN, denied)
        self.assertEqual("factory_reset_requires_local_access", error["error"])


class RecoveryTests(unittest.TestCase):
    """Ett glömt lösenord får inte låsa servern för gott.

    Beviset är fysisk åtkomst till maskinen: kommandot körs i serverns terminal
    och läser dess databas. Det sätter inget lösenord åt någon - det utfärdar
    samma engångskod som en inbjudan, och den som får den väljer sitt eget.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.state = Path(self._dir.name)
        store = IdentityStore(self.state / "identity.db")
        store.configure_admin_access("casper", "det-gamla-losenordet")
        store.close()

    def _run(self, *arguments: str) -> int:
        return recover.main(["--state-dir", str(self.state), *arguments])

    def test_without_a_user_it_lists_the_accounts(self) -> None:
        self.assertEqual(0, self._run())

    def test_an_unknown_user_is_refused(self) -> None:
        self.assertEqual(1, self._run("--user", "ingen"))

    def test_a_missing_installation_is_said_out_loud(self) -> None:
        with self.assertRaises(SystemExit):
            recover.main(["--state-dir", str(self.state / "finns-inte")])

    def test_the_issued_code_sets_a_new_password_and_retires_the_old(self) -> None:
        self.assertEqual(0, self._run("--user", "casper"))

        store = IdentityStore(self.state / "identity.db")
        self.addCleanup(store.close)
        user = next(u for u in store.list_admin_users() if u["username"] == "casper")
        self.assertTrue(user["invitation_pending"], "ingen kod utfärdades")

        # Koden syns bara i terminalen. Provet läser den ur databasen via en ny
        # utfärdning, vilket också visar att en ny kod ersätter den gamla.
        issued = store.reissue_admin_setup(str(user["user_id"]))
        store.redeem_admin_setup("casper", str(issued["setup_code"]), "ett-nytt-losenord")

        self.assertIsNone(store.create_admin_session("casper", "det-gamla-losenordet"))
        self.assertIsNotNone(store.create_admin_session("casper", "ett-nytt-losenord"))


class SignedOutChromeTests(unittest.TestCase):
    """Locket ska inte erbjuda det som kräver inloggning.

    Flikraden, "Bygg om träffen" och kugghjulet stod kvar bakom inloggnings-
    rutan. Det gjorde ingen skada så länge servern ändå släppte in på maskinen -
    knapparna fungerade. Nu gör de inte det, och en knapp som alltid nekas är
    ett gränssnittsfel även när behörigheten håller.

    Uppmätt i Chromium: utloggad syns märket och anslutningsraden, ingenting
    annat; efter inloggning kommer allt tillbaka.
    """

    def setUp(self) -> None:
        web = Path(__file__).resolve().parent.parent / "src" / "tmbox_gateway" / "web"
        self.js = (web / "app.js").read_text(encoding="utf-8")
        self.css = (web / "app.css").read_text(encoding="utf-8")
        self.html = (web / "index.html").read_text(encoding="utf-8")

    def test_the_state_is_set_in_both_directions(self) -> None:
        self.assertIn('document.body.dataset.signedIn = "no";', self.js)
        self.assertIn('document.body.dataset.signedIn = "yes";', self.js)

    def test_the_controls_that_need_a_login_are_hidden_without_one(self) -> None:
        rule = self.css[self.css.index('body[data-signed-in="no"] .run-tabs'):]
        rule = rule[: rule.index("}")]
        for control in (".enter-build", ".settings-button", ".leave-settings", ".app-clock"):
            self.assertIn(control, rule)

    def test_the_card_no_longer_calls_itself_external(self) -> None:
        """Inloggningen gäller inte längre bara utifrån."""

        self.assertNotIn("EXTERN ADMIN", self.html)
        self.assertIn("Inloggning krävs, också på serverdatorn själv", self.html)
        self.assertIn("tmbox_gateway.recover", self.html)


if __name__ == "__main__":
    unittest.main()
