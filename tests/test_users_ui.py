"""Användarvyn ska visa det API:et faktiskt svarar.

Två fel är lätta att göra i en vy som den här, och båda syns i filerna:

1. Vyn läser ett fält som API:et inte skickar. Då blir kolumnen tom utan att
   något går sönder - ingen felkod, ingen loggrad, bara en lista som ser fel ut.
2. Inbjudningsformuläret ligger kvar för en administratör. Servern säger nej
   (403), men först efter att någon fyllt i namnet och tryckt. Att erbjuda en
   åtgärd som alltid nekas är ett gränssnittsfel även när behörigheten håller.

Testet läser markup och `app.js` mot `identity.py`/`http_server.py` i stället
för att köra en webbläsare: det som kan glida isär är fältnamn och rutter, och
de står i filerna.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "tmbox_gateway" / "web"
MARKUP = (WEB / "index.html").read_text(encoding="utf-8")
SCRIPT = (WEB / "app.js").read_text(encoding="utf-8")
STYLE = (WEB / "app.css").read_text(encoding="utf-8")
IDENTITY = (ROOT / "src" / "tmbox_gateway" / "identity.py").read_text(encoding="utf-8")
HTTP = (ROOT / "src" / "tmbox_gateway" / "http_server.py").read_text(encoding="utf-8")


class UsersViewTests(unittest.TestCase):
    def test_the_section_exists_and_belongs_to_the_settings_view(self) -> None:
        self.assertIn('data-admin-section="users"', MARKUP)
        sections = re.search(r"const SETTINGS_SECTIONS = \[([^\]]*)\]", SCRIPT)
        assert sections
        self.assertIn("users", sections.group(1))

    def test_opening_the_settings_view_loads_the_users(self) -> None:
        """Utan det här anropet står listan tom tills något annat råkar hämta
        den, vilket ser ut som att servern hänger sig."""

        body = SCRIPT[SCRIPT.index("function showSettings()"):]
        self.assertIn("refreshUsers();", body[: body.index("\nfunction ")])

    def test_every_field_the_view_reads_is_one_the_server_sends(self) -> None:
        view = SCRIPT[SCRIPT.index("function renderUsers()"): SCRIPT.index("function usersButton(")]
        listed = set(re.findall(r'"([a-z_]+)": row\[\d+\]|"([a-z_]+)": bool\(row\[\d+\]\)', IDENTITY))
        sent = {name for pair in listed for name in pair if name}
        sent.add("setup_code")  # läggs på av invite_admin_user/reissue_admin_setup
        for field in sorted(set(re.findall(r"\buser\.([a-z_]+)\b", view))):
            self.assertIn(field, sent, f"vyn läser user.{field} som servern inte skickar")

    def test_every_route_the_view_calls_is_one_the_server_answers(self) -> None:
        routes = set(re.findall(r'"(/v1/admin/users[a-z/]*)"', HTTP))
        called = set(re.findall(r'"(/v1/admin/users[a-z/]*)"', SCRIPT))
        self.assertTrue(called, "vyn anropar inga användarrutter alls")
        self.assertLessEqual(called, routes, f"vyn anropar rutter som saknas: {called - routes}")

    def test_an_administrator_is_not_offered_what_the_server_will_refuse(self) -> None:
        """Ägargränsen ska synas i vyn, inte bara i svaret."""

        view = SCRIPT[SCRIPT.index("function renderUsers()"): SCRIPT.index("function usersButton(")]
        self.assertIn('const owner = users.role === "owner"', view)
        self.assertRegex(view, r'invite-form.*classList\.toggle\("hidden", !owner\)')
        self.assertIn("if (owner) {", view)

    def test_removing_a_user_asks_first(self) -> None:
        remove = SCRIPT[SCRIPT.index("async function removeUser("):]
        remove = remove[: remove.index("\nfunction ")]
        self.assertIn("window.confirm", remove)
        self.assertIn("${user.username}", remove)


class RedeemViewTests(unittest.TestCase):
    """Den inbjudne har en kod men inget lösenord - och kan alltså inte logga
    in för att sätta det. Saknas den här vägen är inbjudan en återvändsgränd."""

    def test_the_login_card_offers_the_invitation_path(self) -> None:
        self.assertIn('id="redeem-form"', MARKUP)
        self.assertIn('id="redeem-open"', MARKUP)
        login = MARKUP[MARKUP.index('<section id="login"'):]
        self.assertIn('id="redeem-form"', login[: login.index("</section>")])

    def test_the_card_says_what_it_is_when_the_code_form_is_open(self) -> None:
        """Rubriken "Logga in" över ett formulär som sätter lösenord för första
        gången är fel skylt på rätt dörr."""

        self.assertIn('id="redeem-intro"', MARKUP)
        self.assertIn("Välj ditt lösenord", MARKUP)
        show = SCRIPT[SCRIPT.index("function showRedeem(open)"):]
        show = show[: show.index("\n}")]
        self.assertIn('#login-intro', show)
        self.assertIn('#redeem-intro', show)

    def test_redeeming_needs_no_session(self) -> None:
        """authorizedFetch skickar sessionen. Här finns ingen - att använda den
        vore att kräva inloggning för att kunna logga in."""

        redeem = SCRIPT[SCRIPT.index('redeemForm?.addEventListener("submit"'):]
        redeem = redeem[: redeem.index("\nsetupAdminForm")]
        self.assertIn('await fetch("/v1/admin/users/redeem"', redeem)
        self.assertNotIn("authorizedFetch", redeem)
        for field in ("username", "code", "password"):
            self.assertIn(f"#redeem-{field}", redeem)

    def test_the_redeem_route_is_open_on_the_server_too(self) -> None:
        route = HTTP[HTTP.index('"/v1/admin/users/redeem"'):][:400]
        self.assertNotIn("_require_admin", route)


class UsersStyleTests(unittest.TestCase):
    def test_the_classes_the_view_paints_with_exist(self) -> None:
        """En klass som inte finns i app.css ritar ingenting - och märks inte
        förrän någon tittar på sidan."""

        view = SCRIPT[SCRIPT.index("function renderUsers()"): SCRIPT.index("async function refreshUsers(")]
        used = set(re.findall(r'className = "([a-z- ]+)"', view))
        for group in used:
            for name in group.split():
                self.assertIn(f".{name}", STYLE, f"klassen {name} saknar stil")


class UsersFeedbackTests(unittest.TestCase):
    def test_the_receipt_survives_the_refresh_that_follows_it(self) -> None:
        """Varje åtgärd hämtar listan på nytt. Nollställde hämtningen
        meddelandet suddades kvittot i samma andetag som det sattes - mätt i
        webbläsaren: "casper är borttagen" blev tom rad."""

        refresh = SCRIPT[SCRIPT.index("async function refreshUsers()"):]
        refresh = refresh[: refresh.index("\nasync function usersPost(")]
        self.assertNotIn('setMessage(message, "", "")', refresh)
        self.assertIn('setMessage(message, "Användarna kunde inte läsas", "error")', refresh)


class UsersOnAPhoneTests(unittest.TestCase):
    """Åtgärder man måste rulla i sidled för att nå finns inte.

    Uppmätt i Chromium på 360 px: tabellens minsta bredd sköt ut kolumnen med
    "Ta bort" utanför skärmen. Roll och läge flyttar därför in under namnet på
    smala skärmar, och kolumnerna fälls bort.
    """

    def test_the_narrow_layout_folds_the_two_columns_away(self) -> None:
        narrow = STYLE[STYLE.index("@media (max-width: 560px) {\n  .users-table"):]
        narrow = narrow[: narrow.index("\n}")]
        self.assertIn("th:nth-child(2)", narrow)
        self.assertIn("th:nth-child(3)", narrow)
        self.assertIn("td.users-role", narrow)
        self.assertIn("td.users-state", narrow)
        self.assertIn(".users-state-inline { display: block", narrow)
        self.assertIn(".users-table { min-width: 0; }", narrow)

    def test_the_same_row_carries_both_forms(self) -> None:
        """En brytpunkt i CSS och en i JS skulle kunna glida isär. Raden ritas
        en gång och bär båda formerna; CSS väljer vilken som syns."""

        view = SCRIPT[SCRIPT.index("function renderUsers()"): SCRIPT.index("function usersButton(")]
        self.assertIn("users-state-inline", view)
        self.assertIn('role.className = "users-role"', view)
        self.assertIn('column.className = "users-state"', view)
        self.assertNotIn("matchMedia", view)
        self.assertIn(".users-state-inline { display: none; }", STYLE)


if __name__ == "__main__":
    unittest.main()
