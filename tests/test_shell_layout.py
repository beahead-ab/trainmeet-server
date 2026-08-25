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


def _rule(selector: str) -> str:
    """Regelkroppen för en exakt selektor, eller tom sträng."""
    match = re.search(
        rf"(?:^|\}}|\*/)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", CSS, re.MULTILINE
    )
    return match.group(1) if match else ""


class RunModeLayoutTests(unittest.TestCase):
    def test_run_mode_gives_the_shell_its_own_layout(self):
        """Utan den här ärver KÖR tvåkolumnsgridden och innehållet hamnar i
        sidokolumnen."""
        body = _rule('body[data-mode="kor"] .server-admin-shell')
        self.assertTrue(body, "KÖR sätter ingen egen layout på skalet")
        self.assertIn("display: block", body)

    def test_build_mode_still_has_its_sidebar_row(self):
        body = _rule('body[data-mode="bygg"] .server-admin-shell')
        self.assertTrue(body)
        self.assertIn("display: flex", body)

    def test_the_workspace_does_not_carry_its_own_width(self):
        """Bredden ska komma från skalet, inte från två ställen som kan
        hamna i otakt."""
        body = _rule(".server-workspace")
        self.assertIn("width: auto", body)
        self.assertIn("min-width: 0", body)

    def test_both_modes_are_covered(self):
        """Ett läge utan egen regel faller tillbaka på den gamla gridden,
        vilket är precis felet som nådde produktion."""
        for mode in ("kor", "bygg"):
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

    def test_a_station_picker_is_not_as_wide_as_the_window(self):
        body = _rule(".traffic-filters select")
        self.assertIn("width: auto", body)
        self.assertIn("max-width", body)

    def test_a_link_among_buttons_carries_no_underline(self):
        self.assertIn("a.overview-action { text-decoration: none; }", CSS)


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
        for selector in (".basics-grid", ".access-grid"):
            body = _rule(selector)
            with self.subTest(selector=selector):
                self.assertIn("auto-fit", body, f"{selector} har fast kolumnantal")
                self.assertNotIn("repeat(3,", body)

    def test_the_minimum_can_never_exceed_the_space(self):
        """`min(180px, 100%)` betyder "180px, eller allt som finns om det är
        mindre" - alltså aldrig mer än ytan."""
        for selector in (".basics-grid", ".access-grid"):
            with self.subTest(selector=selector):
                self.assertIn("min(", _rule(selector))
