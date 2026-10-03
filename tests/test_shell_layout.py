"""Skalets layout i KÖR och BYGG.

Bakgrunden är ett fel som nådde produktion: i KÖR ritades hela innehållet i
vänstra fjärdedelen av ett brett fönster, med resten tomt.

Orsaken var att `.server-admin-shell` ärvde tvåkolumnsgridden från de tolv
menypunkterna. I KÖR är byggsidofältet `display: none`, men **grid-spåret
finns kvar** - så arbetsytan hamnade i sidokolumnen på 250px och svämmade över
den.

Det är den sortens fel som inga befintliga tester kunde se, för markup och
JavaScript var rätt. Bara måtten i en webbläsare avslöjar det, och de mäts
inte här. Testerna nedan vaktar därför att reglerna som *gör* måtten rätt
finns kvar och verifieras av testerna nedan.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web" / "app.css").read_text(
    encoding="utf-8"
)


KR_CSS = (Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web" / "kontrollrummet.css").read_text(
    encoding="utf-8"
)


def _rule(selector: str, css: str = CSS) -> str:
    """Regelkroppen för en exakt selektor, eller tom sträng."""
    match = re.search(
        rf"(?:^|\}}|\*/)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css, re.MULTILINE
    )
    return match.group(1) if match else ""


class RunModeLayoutTests(unittest.TestCase):
    def test_run_mode_gives_the_shell_its_own_layout(self):
        """Utan den här ärver KÖR tvåkolumnsgridden och innehållet hamnar i
        sidokolumnen."""
        body = _rule('body[data-mode="kor"] .server-admin-shell')
        self.assertTrue(body, "KÖR sätter ingen egen layout på skalet")
        self.assertIn("display: block", body)

    def test_workspace_picker_does_not_inherit_a_sidebar(self):
        body = _rule('body[data-mode="workspaces"] .server-admin-shell')
        self.assertTrue(body)
        self.assertIn("display: block", body)

    def test_the_workspace_does_not_carry_its_own_width(self):
        """Bredden ska komma från skalet, inte från två ställen som kan
        hamna i otakt."""
        body = _rule(".server-workspace")
        self.assertIn("width: auto", body)
        self.assertIn("min-width: 0", body)

    def test_both_modes_are_covered(self):
        """Ett läge utan egen regel faller tillbaka på den gamla gridden,
        vilket är precis felet som nådde produktion."""
        for mode in ("kor", "workspaces", "installningar", "tmbox", "skarmar"):
            with self.subTest(mode=mode):
                self.assertTrue(_rule(f'body[data-mode="{mode}"] .server-admin-shell'))


class ControlShapeTests(unittest.TestCase):
    """Kryssrutor är inte textfält.

    Den globala fältregeln gav varje `input` full bredd, kontrollhöjd och
    14px radie. På en kryssruta blir det en stor rundad fyrkant, vilket är
    vad "Bara avvikelser" i KÖR › Trafik renderade som.

    Steg 5 hade redan lappat symptomet inuti `.server-step-card`. Den lappen
    är kvar men behövs inte längre; det här testet vaktar orsaken.
    """

    def test_the_global_field_rule_excludes_checkboxes_and_radios(self):
        self.assertNotIn("\ninput, select {", CSS, "den globala regeln träffar kryssrutor")
        self.assertIn('input:not([type="checkbox"]):not([type="radio"]), select {', CSS)

    def test_checkboxes_get_their_own_shape(self):
        body = _rule('input[type="checkbox"], input[type="radio"]')
        self.assertTrue(body, "kryssrutor saknar egen regel")
        self.assertIn("width: auto", body)
        self.assertIn("min-height: 0", body)

    def test_a_drift_picker_is_not_as_wide_as_the_window(self):
        """Tidsfönstret i tågdiagrammet är en liten väljare, inte ett fält."""
        body = _rule("#app-view select.kr-select, .kr-select", KR_CSS)
        self.assertIn("width: auto", body)

    def test_drift_panels_shrink_to_the_window_width(self):
        """Stationerna och händelserna lägger sig under varandra i stället för att tvinga fram bredd,
        och tabellen rullar i sidled om den ändå är för bred."""
        self.assertIn("flex-wrap: wrap", _rule(".kr-split", KR_CSS))
        self.assertIn("overflow-x: auto", _rule(".kr-scroll-x", KR_CSS))
        self.assertIn("width: 100%", _rule(".kr-map svg", KR_CSS))

    def test_the_station_list_becomes_cards_on_a_phone(self):
        """På en telefon ska åtgärden ("Välj station", "Redigera") aldrig ligga utanför bild."""
        phone = KR_CSS[KR_CSS.index("@media (max-width: 700px)"):]
        self.assertIn("grid-template-areas", phone)
        self.assertIn(".kr-tbl thead { display: none; }", phone)
        self.assertIn(".kr-hide-sm { display: none; }", KR_CSS)


class ContainerAwareGridTests(unittest.TestCase):
    """Formulär som lägger om i stället för att sticka ut.

    Ett fast antal kolumner med ett hårt minimum kräver en viss bredd. Är
    ytan smalare svämmar formuläret över, och medieförfrågningar hjälper inte:
    de lyssnar på **fönstrets** bredd, men den yta ett byggsteg har beror på
    om sidofältet står där. Vid 924px fönster är innehållsytan 572px, och ett
    formulär som kräver 598 sticker ut 26px utan att någon förfrågan slår till.

    `auto-fit` räknar på den plats som faktiskt finns.
    """

    def test_no_form_grid_demands_a_fixed_number_of_wide_columns(self):
        for selector in (".access-grid",):
            body = _rule(selector)
            with self.subTest(selector=selector):
                self.assertIn("auto-fit", body, f"{selector} har fast kolumnantal")
                self.assertNotIn("repeat(3,", body)

    def test_the_minimum_can_never_exceed_the_space(self):
        """`min(180px, 100%)` betyder "180px, eller allt som finns om det är
        mindre" - alltså aldrig mer än ytan."""
        for selector in (".access-grid",):
            with self.subTest(selector=selector):
                self.assertIn("min(", _rule(selector))

    def test_settings_rows_wrap_instead_of_sticking_out(self):
        """Inställningarna är rader (etikett, fält, förklaring) som lägger om sig,
        och sidomenyn blir en rad överst på smala skärmar."""
        self.assertIn("flex-wrap: wrap", _rule(".kr-kv", KR_CSS))
        narrow = KR_CSS[KR_CSS.index("@media (max-width: 900px) {\n  #admin-view.kr-settings"):]
        self.assertIn("flex-direction: column", narrow)
        self.assertIn("overflow-x: auto", narrow)
        self.assertIn(".kr-kv .kr-k, .kr-lbl { width: 100%; }", narrow)


class ModalFormLayoutTests(unittest.TestCase):
    def test_modal_inline_forms_reset_explicit_button_positions(self):
        body = _rule(".admin-modal .inline-form > *")
        self.assertIn("grid-column: 1 / -1", body)
        self.assertIn("grid-row: auto", body)
        self.assertNotIn(".device-form > button", CSS)

    def test_device_assignment_uses_a_separate_save_cancel_footer(self):
        web = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web"
        html = (web / "index.html").read_text(encoding="utf-8")
        form = re.search(r'<form id="device-form"[^>]*>(.*?)</form>', html, re.S).group(0)
        self.assertNotIn("inline-form", form)
        footer = form.split('<div class="modal-actions">', 1)[1]
        self.assertIn('data-close-modal', footer)
        self.assertIn('type="submit"', footer)
        js = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn("deviceForm.querySelector('button[type=\"submit\"]')", js)
