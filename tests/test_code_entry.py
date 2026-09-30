"""Koder ska gå att skriva av, inte bara kopiera.

En kod läses upp i telefon, skrivs av från en skärm på andra sidan rummet eller
knappas in på en surfplatta med fettfläckar. Den som gör det väljer själv om
strecket kommer med. Alla varianter är samma kod, och mottagaren ska inte låtsas
något annat - annars får man "Koden gäller inte" på en kod som är rätt, vilket
skickar folk att leta efter fel sak.

Regeln: strikt där koden visas, tolerant där den tas emot.

Vad toleransen *inte* omfattar är tecknen själva. Ett O blir aldrig en nolla.
Koderna lottas redan ur ett alfabet utan de paren, och att gissa åt användaren
skulle göra en felskriven kod till en som ser rätt ut.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tmbox_gateway.identity import (
    AdminAccessError,
    IdentityStore,
    _display_code,
    _normalize_code,
)


def _variants(code: str) -> dict[str, str]:
    """Samma kod, skriven som sex olika personer skriver den."""

    bare = code.replace("-", "")
    return {
        "som den visas": code,
        "utan streck": bare,
        "med mellanslag": bare[: len(bare) // 2] + " " + bare[len(bare) // 2 :],
        "gemener": code.lower(),
        "blanksteg runt": f"  {code} ",
        "punkt i stället": code.replace("-", "."),
    }


class WrittenFormTests(unittest.TestCase):
    def test_both_code_shapes_get_the_same_written_form(self) -> None:
        """Två lika stora grupper med streck emellan, oavsett kodsort."""

        self.assertEqual("123-456", _display_code("123456"))
        self.assertEqual("ABCD-EFGH", _display_code("ABCDEFGH"))

    def test_the_written_form_is_reached_from_every_variant(self) -> None:
        for name, variant in _variants("ABCD-EFGH").items():
            with self.subTest(name=name):
                self.assertEqual("ABCD-EFGH", _display_code(variant))

    def test_letters_are_never_guessed_into_digits(self) -> None:
        """O blir inte 0. En felskriven kod ska förbli felskriven, annars slutar
        "Koden gäller inte" vara ett svar man kan lita på."""

        self.assertEqual("O0I1", _normalize_code("o0i1"))


class InvitationCodeTests(unittest.TestCase):
    """Inbjudningskoden krävde strecket.

    Digesten räknades på den råa strängen, så koden som lämnats över på en lapp
    gick bara att lösa in om man skrev bindestrecket också. Felmeddelandet sa
    "Koden gäller inte", vilket lät som att koden var fel i stället för att
    skiljetecknet var det.
    """

    def _fresh(self) -> tuple[IdentityStore, str]:
        store = IdentityStore(Path(tempfile.mkdtemp()) / "identity.db")
        self.addCleanup(store.close)
        store.configure_admin_access("casper", "ett-langt-losenord")
        code = str(store.invite_admin_user("benny", "admin")["setup_code"])
        return store, code

    def test_every_way_of_writing_it_is_accepted(self) -> None:
        for name in _variants("XXXX-XXXX"):
            with self.subTest(name=name):
                # Varje variant prövas mot en nyutfärdad kod. Att återanvända
                # samma butik vore att pröva fel sak: en inlöst kod är förbrukad.
                store, code = self._fresh()
                variant = _variants(code)[name]
                store.redeem_admin_setup("benny", variant, "ett-nytt-losenord")
                self.assertIsNotNone(store.create_admin_session("benny", "ett-nytt-losenord"))

    def test_a_wrong_code_is_still_wrong(self) -> None:
        store, _ = self._fresh()
        with self.assertRaises(AdminAccessError):
            store.redeem_admin_setup("benny", "AAAA-BBBB", "ett-nytt-losenord")

    def test_the_issued_code_carries_its_dash(self) -> None:
        """Toleransen gäller inmatningen. Det som visas ska ha formen."""

        _, code = self._fresh()
        self.assertRegex(code, r"^[0-9A-Z]{4}-[0-9A-Z]{4}$")


class CodeFieldTests(unittest.TestCase):
    """Fälten visar kodens form.

    Ett vanligt textfält säger ingenting om hur lång koden är eller om strecket
    ska skrivas. Rutorna säger båda utan ett ord, och sedan tar servern emot
    koden hur den än skrivs. Samma regel från två håll.
    """

    def setUp(self) -> None:
        web = Path(__file__).resolve().parent.parent / "src" / "tmbox_gateway" / "web"
        self.markup = (web / "index.html").read_text(encoding="utf-8")
        self.script = (web / "app.js").read_text(encoding="utf-8")

    def test_every_code_a_person_types_is_entered_in_boxes(self) -> None:
        """Tre ställen: träffkoden vid installationen, träffkoden vid
        Cloud-kopplingen, och inbjudningskoden."""

        for field in ("setup-sync-code", "runtime-sync-code", "redeem-code"):
            with self.subTest(field=field):
                self.assertIn(f'id="{field}-boxes"', self.markup)
                self.assertIn(f'wireCodeBoxes("#{field}-boxes", "#{field}")', self.script)

    def test_the_invitation_field_is_no_longer_a_plain_input(self) -> None:
        self.assertNotIn('id="redeem-code" class="redeem-code"', self.markup)
        self.assertIn('<input id="redeem-code" type="hidden">', self.markup)

    def test_each_group_of_boxes_carries_its_dash(self) -> None:
        """Strecket är förtryckt, inte något man skriver."""

        self.assertEqual(3, self.markup.count('class="code-boxes-dash"'))

    def test_the_invitation_boxes_take_letters_the_others_do_not(self) -> None:
        """Träffkoden är siffror, inbjudningskoden bokstäver och siffror. Rutorna
        vet vilket, så de kan avvisa ett tecken som ändå aldrig kan stämma."""

        invitation = self.markup[self.markup.index('id="redeem-code-boxes"'):]
        self.assertIn('data-code-alphabet="alnum"', invitation[:220])
        sync = self.markup[self.markup.index('id="setup-sync-code-boxes"'):]
        self.assertNotIn("data-code-alphabet", sync[:220])
        self.assertIn('container.dataset.codeAlphabet === "alnum"', self.script)

    def test_the_boxes_are_counted_out_for_a_screen_reader(self) -> None:
        for label in ("Siffra 1 av 6", "Tecken 1 av 8", "Tecken 8 av 8"):
            with self.subTest(label=label):
                self.assertIn(label, self.markup)


if __name__ == "__main__":
    unittest.main()
