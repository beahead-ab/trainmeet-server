"""Flera personer kan sköta servern, men bara ägaren styr vilka.

Servern hade en enda administratör i en singleton-rad. Nu finns en lista, med
två roller: ägaren får dessutom lägga till och ta bort andra, en administratör
kan allt annat.

Listan är serverns egen och delas inte med Cloud. En Pi i en klubblokal ska
fungera utan nät, och en användarlista som kräver uppkoppling för att logga in
vore fel sorts beroende.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import (
    HTTPAPIError,
    HTTPServerConfig,
    TrainMeetHTTPApplication,
    TrainMeetHTTPServer,
)
from tmbox_gateway.identity import (
    AdminAccessError,
    DeviceKind,
    IdentityStore,
    PairedClient,
    PairingService,
)
from tmbox_gateway.models import DispatchMode
from session_fixture import sample_session


class AdminUserTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.configure_admin_access("casper", "ett-langt-losenord")

    def _invite(self, username: str, password: str, role: str = "admin") -> dict:
        """Bjud in och lös in koden — det är så ett konto blir användbart."""

        invited = self.identities.invite_admin_user(username, role)
        self.identities.redeem_admin_setup(username, str(invited["setup_code"]), password)
        return invited

    def _session_count(self) -> int:
        return int(
            self.identities._connection.execute(
                "SELECT COUNT(*) FROM admin_sessions"
            ).fetchone()[0]
        )

    def _owner(self) -> dict:
        return next(user for user in self.identities.list_admin_users() if user["role"] == "owner")

    # --------------------------------------------------------- migrering

    def test_the_existing_administrator_becomes_the_first_owner(self) -> None:
        """Det farligaste i hela ändringen: ingen får låsas ute."""

        store = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(store.close)

        owners = [user for user in store.list_admin_users() if user["role"] == "owner"]
        self.assertEqual(1, len(owners))
        self.assertEqual("casper", owners[0]["username"])

    def test_the_adopted_owner_can_still_sign_in(self) -> None:
        self.assertIsNotNone(self.identities.create_admin_session("casper", "ett-langt-losenord"))

    def test_an_unfinished_installation_gets_no_owner(self) -> None:
        """Utan lösenord finns ingen att adoptera - och ingen att låsa ute."""

        blank = IdentityStore(Path(self._dir.name) / "tom.db")
        self.addCleanup(blank.close)

        self.assertEqual([], blank.list_admin_users())

    # ------------------------------------------------------------ listan

    def test_the_owner_invites_instead_of_setting_a_password(self) -> None:
        """Ägaren känner aldrig till någon annans lösenord.

        Mönstret är hämtat från be-a-legend-2, där inbjudan går med mejl. Här
        lämnas koden över på plats i stället: servern har ingen e-post, och den
        som bjuder in står ändå i samma klubblokal.
        """

        invited = self.identities.invite_admin_user("lars")

        self.assertEqual("admin", invited["role"])
        self.assertTrue(invited["setup_code"], "en kod att lämna över")
        self.assertTrue(invited["invitation_pending"])
        self.assertFalse(invited["password_configured"], "inget lösenord förrän hen valt ett")
        self.assertEqual(2, len(self.identities.list_admin_users()))

    def test_an_invited_user_cannot_sign_in_until_the_code_is_redeemed(self) -> None:
        self.identities.invite_admin_user("lars")

        self.assertIsNone(self.identities.create_admin_session("lars", "vilket-losenord-som-helst"))

    def test_redeeming_the_code_lets_the_invited_choose_their_own_password(self) -> None:
        invited = self.identities.invite_admin_user("lars")

        self.identities.redeem_admin_setup("lars", str(invited["setup_code"]), "mitt-eget-losenord")

        self.assertIsNotNone(self.identities.create_admin_session("lars", "mitt-eget-losenord"))
        lars = next(u for u in self.identities.list_admin_users() if u["username"] == "lars")
        self.assertFalse(lars["invitation_pending"], "koden ska vara förbrukad")

    def test_a_code_works_only_once(self) -> None:
        invited = self.identities.invite_admin_user("lars")
        self.identities.redeem_admin_setup("lars", str(invited["setup_code"]), "mitt-eget-losenord")

        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("lars", str(invited["setup_code"]), "ett-annat-forsok")

    def test_a_wrong_code_is_refused(self) -> None:
        self.identities.invite_admin_user("lars")

        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("lars", "FEL-KOD1", "mitt-eget-losenord")

    def test_an_expired_code_is_refused_and_says_what_to_do(self) -> None:
        from datetime import datetime, timedelta, timezone

        invited = self.identities.invite_admin_user("lars")
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.identities._connection.execute(
            "UPDATE admin_users SET setup_expires_at = ? WHERE username = 'lars'", (past,)
        )

        with self.assertRaises(AdminAccessError) as caught:
            self.identities.redeem_admin_setup("lars", str(invited["setup_code"]), "mitt-eget-losenord")

        self.assertIn("ny", str(caught.exception).lower())

    def test_a_new_code_can_be_issued(self) -> None:
        """Koden kommer bort, eller går ut. Då ska det inte krävas ett nytt konto."""

        invited = self.identities.invite_admin_user("lars")
        again = self.identities.reissue_admin_setup(str(invited["user_id"]))

        self.assertNotEqual(invited["setup_code"], again["setup_code"])
        self.identities.redeem_admin_setup("lars", str(again["setup_code"]), "mitt-eget-losenord")
        self.assertIsNotNone(self.identities.create_admin_session("lars", "mitt-eget-losenord"))

        with self.assertRaises(AdminAccessError):
            self.identities.redeem_admin_setup("lars", str(invited["setup_code"]), "gamla-koden")

    def test_an_added_user_can_sign_in(self) -> None:
        self._invite("lars", "ett-annat-losenord")

        self.assertIsNotNone(self.identities.create_admin_session("lars", "ett-annat-losenord"))

    def test_a_username_is_not_case_sensitive_and_cannot_be_taken_twice(self) -> None:
        self._invite("lars", "ett-annat-losenord")

        with self.assertRaises(AdminAccessError):
            self._invite("LARS", "ett-tredje-losenord")

    def test_a_short_password_is_refused(self) -> None:
        with self.assertRaises(AdminAccessError):
            self._invite("lars", "kort")

    # ------------------------------------------------------- sista ägaren

    def test_the_last_owner_cannot_be_removed(self) -> None:
        """En server utan ägare har ingen som kan utse en ny."""

        with self.assertRaises(AdminAccessError) as caught:
            self.identities.delete_admin_user(str(self._owner()["user_id"]))

        self.assertIn("minst en ägare", str(caught.exception))

    def test_the_last_owner_cannot_be_demoted(self) -> None:
        with self.assertRaises(AdminAccessError):
            self.identities.set_admin_user_role(str(self._owner()["user_id"]), "admin")

    def test_an_owner_can_step_down_once_there_is_another(self) -> None:
        lars = self._invite("lars", "ett-annat-losenord")
        self.identities.set_admin_user_role(str(lars["user_id"]), "owner")

        self.identities.set_admin_user_role(str(self._owner()["user_id"]), "admin")

        owners = [user for user in self.identities.list_admin_users() if user["role"] == "owner"]
        self.assertEqual(["lars"], [user["username"] for user in owners])

    def test_an_administrator_can_be_removed(self) -> None:
        lars = self._invite("lars", "ett-annat-losenord")

        self.identities.delete_admin_user(str(lars["user_id"]))

        self.assertEqual(["casper"], [u["username"] for u in self.identities.list_admin_users()])

    # -------------------------------------------------------- sessionerna

    def test_a_session_knows_who_it_belongs_to(self) -> None:
        self._invite("lars", "ett-annat-losenord")
        token = self.identities.create_admin_session("lars", "ett-annat-losenord")

        user = self.identities.admin_session_user(str(token))

        self.assertEqual("lars", user["username"])
        self.assertEqual("admin", user["role"])

    def test_removing_someone_ends_their_session(self) -> None:
        """Annars fortsätter den som just tagits bort att vara inloggad.

        Att sessionen slutar lösas ut räcker inte som prov: uppslaget mot
        användaren misslyckas ändå när hen är borta, så raden kunde ligga kvar
        utan att testet märkte det. Här kontrolleras båda.
        """

        lars = self._invite("lars", "ett-annat-losenord")
        token = self.identities.create_admin_session("lars", "ett-annat-losenord")

        self.identities.delete_admin_user(str(lars["user_id"]))

        self.assertIsNone(self.identities.admin_session_user(str(token)))
        self.assertEqual(0, self._session_count(), "sessionsraden ska städas bort")

    def test_a_new_password_ends_the_old_sessions(self) -> None:
        lars = self._invite("lars", "ett-annat-losenord")
        token = self.identities.create_admin_session("lars", "ett-annat-losenord")

        self.identities.set_admin_user_password(str(lars["user_id"]), "ett-nytt-losenord")

        self.assertIsNone(self.identities.admin_session_user(str(token)))
        self.assertIsNotNone(self.identities.create_admin_session("lars", "ett-nytt-losenord"))

    def test_a_wrong_password_never_signs_anyone_in(self) -> None:
        self._invite("lars", "ett-annat-losenord")

        self.assertIsNone(self.identities.create_admin_session("lars", "fel-losenord"))
        self.assertIsNone(self.identities.create_admin_session("lars", "ett-langt-losenord"))


if __name__ == "__main__":
    unittest.main()


class RoleEnforcementTests(unittest.TestCase):
    """Spärren är det som gör rollerna verkliga.

    En administratör kan sköta hela servern men inte ändra vilka som har
    tillgång. Skillnaden är avsiktligt smal: den som lagts till ska kunna köra
    en träff fullt ut, men inte kunna ge sig själv sällskap eller ta bort den
    som bjöd in hen.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.configure_admin_access("casper", "ett-langt-losenord")
        self.lars = self._invite("lars", "ett-annat-losenord")

    def _invite(self, username: str, password: str, role: str = "admin") -> dict:
        """Bjud in och lös in koden — det är så ett konto blir användbart."""

        invited = self.identities.invite_admin_user(username, role)
        self.identities.redeem_admin_setup(username, str(invited["setup_code"]), password)
        return invited

    def _client(self, username: str) -> object:
        """En klient som bär rollen, precis som en inloggad session ger."""

        from tmbox_gateway.identity import DeviceKind, PairedClient

        user = next(u for u in self.identities.list_admin_users() if u["username"] == username)
        return PairedClient(
            client_id="local-web-admin", display_name=username,
            kind=DeviceKind.WEB_ADMIN, panel_ids=(),
            admin_user_id=str(user["user_id"]), admin_role=str(user["role"]),
        )

    def test_an_administrator_carries_the_administrator_role(self) -> None:
        self.assertEqual("admin", self._client("lars").admin_role)
        self.assertEqual("owner", self._client("casper").admin_role)

    def test_a_session_gives_the_role_the_user_has(self) -> None:
        token = self.identities.create_admin_session("lars", "ett-annat-losenord")

        self.assertEqual("admin", self.identities.admin_session_user(str(token))["role"])

    def test_a_promoted_user_gets_the_owner_role_on_the_next_session(self) -> None:
        self.identities.set_admin_user_role(str(self.lars["user_id"]), "owner")
        token = self.identities.create_admin_session("lars", "ett-annat-losenord")

        self.assertEqual("owner", self.identities.admin_session_user(str(token))["role"])

    def test_the_console_shortcut_is_still_owner(self) -> None:
        """Oförändrat beteende: den som sitter vid lådan har full behörighet.

        Det är avsiktligt lämnat som det var — men det betyder att
        rollseparationen inte gäller vid maskinen själv.
        """

        from tmbox_gateway.identity import DeviceKind, PairedClient

        console = PairedClient(
            client_id="local-web-admin", display_name="Lokal administratör",
            kind=DeviceKind.WEB_ADMIN, panel_ids=(),
        )
        self.assertEqual("owner", console.admin_role)
        self.assertIsNone(console.admin_user_id)


class OwnerGateTests(unittest.TestCase):
    """Spärren, provad genom applikationen och inte bredvid den.

    Att klienten bär rätt roll är inte samma sak som att någon stoppas. Det
    första provet på rollerna mätte det förra, vilket lät en ändring i spärren
    passera obemärkt.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.configure_admin_access("casper", "ett-langt-losenord")
        self.lars = self._invite("lars", "ett-annat-losenord")

        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True),
        )

    def _invite(self, username: str, password: str, role: str = "admin") -> dict:
        """Bjud in och lös in koden — det är så ett konto blir användbart."""

        invited = self.identities.invite_admin_user(username, role)
        self.identities.redeem_admin_setup(username, str(invited["setup_code"]), password)
        return invited

    def _as(self, username: str) -> PairedClient:
        user = next(u for u in self.identities.list_admin_users() if u["username"] == username)
        return PairedClient(
            client_id="local-web-admin", display_name=username,
            kind=DeviceKind.WEB_ADMIN, panel_ids=(),
            admin_user_id=str(user["user_id"]), admin_role=str(user["role"]),
        )

    def test_an_administrator_may_not_add_a_user(self) -> None:
        with self.assertRaises(HTTPAPIError) as caught:
            self.application.create_admin_user(
                self._as("lars"), {"username": "nyan", "password": "ett-langt-losenord"}
            )

        self.assertEqual(403, int(caught.exception.status))

    def test_an_administrator_may_not_remove_a_user(self) -> None:
        with self.assertRaises(HTTPAPIError) as caught:
            self.application.delete_admin_user(
                self._as("lars"), {"user_id": str(self.lars["user_id"])}
            )

        self.assertEqual(403, int(caught.exception.status))

    def test_an_administrator_may_not_change_a_role(self) -> None:
        with self.assertRaises(HTTPAPIError):
            self.application.update_admin_user(
                self._as("lars"), {"user_id": str(self.lars["user_id"]), "role": "owner"}
            )

    def test_an_administrator_may_change_their_own_password(self) -> None:
        """Det ska gå. Spärren gäller vilka som finns, inte den egna nyckeln."""

        result = self.application.update_admin_user(
            self._as("lars"),
            {"user_id": str(self.lars["user_id"]), "password": "ett-helt-nytt-losenord"},
        )

        self.assertEqual("lars", result["user"]["username"])
        self.assertIsNotNone(
            self.identities.create_admin_session("lars", "ett-helt-nytt-losenord")
        )

    def test_an_administrator_may_not_change_someone_elses_password(self) -> None:
        owner = next(u for u in self.identities.list_admin_users() if u["role"] == "owner")

        with self.assertRaises(HTTPAPIError) as caught:
            self.application.update_admin_user(
                self._as("lars"), {"user_id": str(owner["user_id"]), "password": "kapat-losenord"}
            )

        self.assertEqual(403, int(caught.exception.status))

    def test_an_administrator_may_still_see_the_list(self) -> None:
        """Att veta vilka som har tillgång är inte samma sak som att ändra det."""

        result = self.application.admin_users(self._as("lars"))

        self.assertEqual({"casper", "lars"}, {u["username"] for u in result["users"]})
        self.assertEqual("admin", result["role"])

    def test_the_owner_may_do_all_of_it(self) -> None:
        added = self.application.create_admin_user(
            self._as("casper"), {"username": "nyan", "password": "ett-langt-losenord"}
        )
        self.application.update_admin_user(
            self._as("casper"), {"user_id": str(added["user"]["user_id"]), "role": "owner"}
        )
        remaining = self.application.delete_admin_user(
            self._as("casper"), {"user_id": str(self.lars["user_id"])}
        )

        self.assertEqual({"casper", "nyan"}, {u["username"] for u in remaining["users"]})

    def test_an_owner_cannot_remove_themselves(self) -> None:
        """Mönstret från be-a-legend-2: den som tar bort sig själv gör det av
        misstag oftare än med avsikt."""

        owner = next(u for u in self.identities.list_admin_users() if u["role"] == "owner")
        self.identities.set_admin_user_role(str(self.lars["user_id"]), "owner")

        with self.assertRaises(HTTPAPIError) as caught:
            self.application.delete_admin_user(
                self._as("casper"), {"user_id": str(owner["user_id"])}
            )

        self.assertEqual(409, int(caught.exception.status))
        self.assertIn("annan ägare", str(caught.exception))

    def test_an_owner_cannot_demote_themselves(self) -> None:
        owner = next(u for u in self.identities.list_admin_users() if u["role"] == "owner")
        self.identities.set_admin_user_role(str(self.lars["user_id"]), "owner")

        with self.assertRaises(HTTPAPIError) as caught:
            self.application.update_admin_user(
                self._as("casper"), {"user_id": str(owner["user_id"]), "role": "admin"}
            )

        self.assertEqual(409, int(caught.exception.status))

    def test_an_owner_can_remove_someone_else(self) -> None:
        remaining = self.application.delete_admin_user(
            self._as("casper"), {"user_id": str(self.lars["user_id"])}
        )

        self.assertEqual(["casper"], [u["username"] for u in remaining["users"]])

    def test_the_invitation_flow_needs_no_login(self) -> None:
        """Den inbjudne har inget konto att logga in med förrän koden är
        inlöst. Koden är hela beviset."""

        invited = self.application.create_admin_user(
            self._as("casper"), {"username": "nyan"}
        )

        self.application.redeem_admin_setup({
            "username": "nyan",
            "code": str(invited["user"]["setup_code"]),
            "password": "mitt-eget-losenord",
        })

        self.assertIsNotNone(self.identities.create_admin_session("nyan", "mitt-eget-losenord"))


class UserRoutesOverHTTPTests(unittest.TestCase):
    """Rutterna provade över en riktig socket.

    Resten av det här provet anropar applikationen direkt, vilket är snabbt och
    läsbart - men det hoppar över begäranshanteraren. Alla fem användarrutter
    läste kroppen en gång till, efter att do_POST redan läst den. Andra läsningen
    väntade på byte som aldrig skulle komma: begäran hängde tills klienten gav
    upp. Ingenting loggades, ingenting felade, servern bara teg.

    Applikationstesterna kunde inte se det, eftersom de aldrig gick genom HTTP.
    Webbläsaren såg det direkt.

    Tidsgränsen är beviset: en rutt som läser kroppen två gånger blir en
    timeout, inte ett felaktigt svar.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.configure_admin_access("casper", "ett-langt-losenord")

        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True),
        )
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), application)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.cookie = self._sign_in("casper", "ett-langt-losenord")

    def _sign_in(self, username: str, password: str) -> str:
        """Servern kräver inloggning även på maskinen själv, så provet loggar
        in på riktigt i stället för att luta sig mot var det står."""

        token = self.identities.create_admin_session(username, password)
        assert token is not None
        return f"trainmeet_admin={token}"

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Cookie": self.cookie},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_every_user_route_answers_instead_of_hanging(self) -> None:
        status, listed = self._post("/v1/admin/users", {"username": "benny", "role": "admin"})
        self.assertEqual(201, status)
        code = listed["user"]["setup_code"]
        user_id = listed["user"]["user_id"]

        status, reissued = self._post("/v1/admin/users/reissue", {"user_id": user_id})
        self.assertEqual(200, status)
        self.assertNotEqual(code, reissued["user"]["setup_code"])

        status, _ = self._post(
            "/v1/admin/users/redeem",
            {"username": "benny", "code": reissued["user"]["setup_code"], "password": "ett-langt-nog"},
        )
        self.assertEqual(200, status)

        status, _ = self._post("/v1/admin/users/update", {"user_id": user_id, "role": "owner"})
        self.assertEqual(200, status)

        status, remaining = self._post("/v1/admin/users/delete", {"user_id": user_id})
        self.assertEqual(200, status)
        self.assertNotIn("benny", [user["username"] for user in remaining["users"]])

    def test_the_list_is_readable_over_http_too(self) -> None:
        request = Request(f"{self.base}/v1/admin/users", headers={"Cookie": self.cookie})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual("owner", payload["role"])


class UsableOwnerTests(unittest.TestCase):
    """Sista ägaren räknas bland dem som kan logga in.

    Fyndet kom ur en genomklickning: konsolen på servern tog bort den enda
    riktiga ägaren utan att spärren sa ifrån. En obesvarad inbjudan till ägare
    låg i listan och räknades som ägare - fast det kontot varken hade lösenord
    eller kunde få ett om koden gått ut. Servern hade blivit omöjlig att
    administrera utanför maskinen, utan att någonting gått sönder.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.configure_admin_access("casper", "ett-langt-losenord")
        self.casper = next(
            user for user in self.identities.list_admin_users() if user["username"] == "casper"
        )

    def test_an_invited_owner_does_not_hold_the_last_owner_open(self) -> None:
        self.identities.invite_admin_user("lars", "owner")
        with self.assertRaises(AdminAccessError):
            self.identities.delete_admin_user(str(self.casper["user_id"]))
        self.assertIn("casper", [u["username"] for u in self.identities.list_admin_users()])

    def test_an_invited_owner_does_not_allow_the_last_owner_to_step_down(self) -> None:
        self.identities.invite_admin_user("lars", "owner")
        with self.assertRaises(AdminAccessError):
            self.identities.set_admin_user_role(str(self.casper["user_id"]), "admin")

    def test_a_pending_owner_can_be_demoted_without_a_fuss(self) -> None:
        """Spärren frågar vad åtgärden lämnar efter sig, inte hur listan ser ut.

        Första versionen räknade inloggningsbara ägare i listan och nekade
        därför att degradera en inbjuden ägare - fast den som kan logga in satt
        kvar. Fångat i en genomklickning: knappen gjorde ingenting.
        """

        invited = self.identities.invite_admin_user("lars", "owner")
        self.identities.set_admin_user_role(str(invited["user_id"]), "admin")
        lars = next(u for u in self.identities.list_admin_users() if u["username"] == "lars")
        self.assertEqual("admin", lars["role"])

    def test_a_pending_owner_can_be_removed_too(self) -> None:
        invited = self.identities.invite_admin_user("lars", "owner")
        self.identities.delete_admin_user(str(invited["user_id"]))
        self.assertEqual(["casper"], [u["username"] for u in self.identities.list_admin_users()])

    def test_an_unfinished_installation_is_not_blocked_by_the_guard(self) -> None:
        """Utan någon inloggningsbar ägare finns ingenting att förlora, och en
        spärr där skulle bara låsa fast en halvfärdig installation."""

        empty = IdentityStore(Path(self._dir.name) / "empty.db")
        self.addCleanup(empty.close)
        invited = empty.invite_admin_user("lars", "owner")
        empty.delete_admin_user(str(invited["user_id"]))
        self.assertEqual([], empty.list_admin_users())

    def test_once_the_invitation_is_redeemed_the_seat_is_free(self) -> None:
        """Spärren ska skydda mot att bli utelåst, inte hindra ett överlämnande."""

        invited = self.identities.invite_admin_user("lars", "owner")
        self.identities.redeem_admin_setup("lars", str(invited["setup_code"]), "ett-annat-losenord")
        self.identities.delete_admin_user(str(self.casper["user_id"]))
        self.assertEqual(["lars"], [u["username"] for u in self.identities.list_admin_users()])


class RemovedUsersStayOutTests(unittest.TestCase):
    """Ett borttaget konto ska inte kunna logga in.

    Den första ägaren står på två ställen: i användarlistan och i den gamla
    singleton-raden som fanns innan servern hade flera användare. Att ta bort
    ägaren tog bara bort listraden. Singleton-raden låg kvar med namn och
    lösenord, och inloggningen föll tillbaka på den när namnet inte fanns i
    listan - alltså just när kontot var borttaget.

    Gränssnittet visade en användare. Servern släppte in två. Fyndet kom av att
    fråga vad som händer med den gamla raden när listan ändras, inte av något
    test som gick sönder.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.identities = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(self.identities.close)
        self.identities.configure_admin_access("casper", "det-gamla-losenordet")
        invited = self.identities.invite_admin_user("lars", "owner")
        self.identities.redeem_admin_setup("lars", str(invited["setup_code"]), "lars-eget-losenord")
        self.casper = next(
            user for user in self.identities.list_admin_users() if user["username"] == "casper"
        )

    def test_the_removed_owner_cannot_sign_in_afterwards(self) -> None:
        self.identities.delete_admin_user(str(self.casper["user_id"]))
        self.assertIsNone(self.identities.create_admin_session("casper", "det-gamla-losenordet"))

    def test_the_remaining_owner_still_can(self) -> None:
        self.identities.delete_admin_user(str(self.casper["user_id"]))
        self.assertIsNotNone(self.identities.create_admin_session("lars", "lars-eget-losenord"))

    def test_the_server_does_not_look_uninstalled_afterwards(self) -> None:
        """Installationsluckan öppnar sig om servern ser ut att sakna lösenord.
        Att ta bort den ursprungliga ägaren får inte se ut så."""

        self.identities.delete_admin_user(str(self.casper["user_id"]))
        summary = self.identities.admin_access_summary()
        self.assertTrue(summary["password_configured"])
        self.assertEqual("lars", summary["username"])

    def test_a_ghost_left_by_an_older_version_is_refused_too(self) -> None:
        """Servrar som redan uppdaterats bär spöket i sin databas.

        Den gamla borttagningen tog bara bort listraden. Att laga borttagningen
        hjälper alltså bara framtida borttagningar - en Pi som redan tagit bort
        sin ursprungliga ägare har kvar singleton-raden med namn och lösenord.

        Provet gör om det gamla felet med en rå DELETE, öppnar databasen igen
        och kräver att inloggningen ändå säger nej: så snart listan finns är
        den sanningen.
        """

        self.identities._connection.execute(  # noqa: SLF001 - härmar en äldre version
            "DELETE FROM admin_users WHERE user_id = ?", (str(self.casper["user_id"]),)
        )
        self.identities.close()

        reopened = IdentityStore(Path(self._dir.name) / "identity.db")
        self.addCleanup(reopened.close)
        self.assertEqual(["lars"], [u["username"] for u in reopened.list_admin_users()])
        self.assertIsNone(reopened.create_admin_session("casper", "det-gamla-losenordet"))

    def test_the_old_row_is_no_longer_a_way_in_once_the_list_exists(self) -> None:
        """Även utan borttagning: listan är sanningen så snart den finns."""

        self.identities.set_admin_user_password(str(self.casper["user_id"]), "ett-nytt-losenord")
        self.assertIsNone(self.identities.create_admin_session("casper", "det-gamla-losenordet"))
        self.assertIsNotNone(self.identities.create_admin_session("casper", "ett-nytt-losenord"))
