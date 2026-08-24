"""Flera personer kan sköta servern, men bara ägaren styr vilka.

Servern hade en enda administratör i en singleton-rad. Nu finns en lista, med
två roller: ägaren får dessutom lägga till och ta bort andra, en administratör
kan allt annat.

Listan är serverns egen och delas inte med Cloud. En Pi i en klubblokal ska
fungera utan nät, och en användarlista som kräver uppkoppling för att logga in
vore fel sorts beroende.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import (
    HTTPAPIError,
    HTTPServerConfig,
    TrainMeetHTTPApplication,
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

    def test_the_owner_can_add_someone(self) -> None:
        added = self.identities.create_admin_user("lars", "ett-annat-losenord")

        self.assertEqual("admin", added["role"])
        self.assertTrue(added["must_change_password"], "ett satt lösenord ska bytas vid första inloggningen")
        self.assertEqual(2, len(self.identities.list_admin_users()))

    def test_an_added_user_can_sign_in(self) -> None:
        self.identities.create_admin_user("lars", "ett-annat-losenord")

        self.assertIsNotNone(self.identities.create_admin_session("lars", "ett-annat-losenord"))

    def test_a_username_is_not_case_sensitive_and_cannot_be_taken_twice(self) -> None:
        self.identities.create_admin_user("lars", "ett-annat-losenord")

        with self.assertRaises(AdminAccessError):
            self.identities.create_admin_user("LARS", "ett-tredje-losenord")

    def test_a_short_password_is_refused(self) -> None:
        with self.assertRaises(AdminAccessError):
            self.identities.create_admin_user("lars", "kort")

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
        lars = self.identities.create_admin_user("lars", "ett-annat-losenord")
        self.identities.set_admin_user_role(str(lars["user_id"]), "owner")

        self.identities.set_admin_user_role(str(self._owner()["user_id"]), "admin")

        owners = [user for user in self.identities.list_admin_users() if user["role"] == "owner"]
        self.assertEqual(["lars"], [user["username"] for user in owners])

    def test_an_administrator_can_be_removed(self) -> None:
        lars = self.identities.create_admin_user("lars", "ett-annat-losenord")

        self.identities.delete_admin_user(str(lars["user_id"]))

        self.assertEqual(["casper"], [u["username"] for u in self.identities.list_admin_users()])

    # -------------------------------------------------------- sessionerna

    def test_a_session_knows_who_it_belongs_to(self) -> None:
        self.identities.create_admin_user("lars", "ett-annat-losenord")
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

        lars = self.identities.create_admin_user("lars", "ett-annat-losenord")
        token = self.identities.create_admin_session("lars", "ett-annat-losenord")

        self.identities.delete_admin_user(str(lars["user_id"]))

        self.assertIsNone(self.identities.admin_session_user(str(token)))
        self.assertEqual(0, self._session_count(), "sessionsraden ska städas bort")

    def test_a_new_password_ends_the_old_sessions(self) -> None:
        lars = self.identities.create_admin_user("lars", "ett-annat-losenord")
        token = self.identities.create_admin_session("lars", "ett-annat-losenord")

        self.identities.set_admin_user_password(str(lars["user_id"]), "ett-nytt-losenord")

        self.assertIsNone(self.identities.admin_session_user(str(token)))
        self.assertIsNotNone(self.identities.create_admin_session("lars", "ett-nytt-losenord"))

    def test_a_wrong_password_never_signs_anyone_in(self) -> None:
        self.identities.create_admin_user("lars", "ett-annat-losenord")

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
        self.lars = self.identities.create_admin_user("lars", "ett-annat-losenord")

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
        self.lars = self.identities.create_admin_user("lars", "ett-annat-losenord")

        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True),
        )

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
