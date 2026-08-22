"""KÖR och BYGG: två lägen i stället för tolv menypunkter.

Designpaketet är bindande för struktur. Dessa tester håller fast det som är
lätt att råka ändra tillbaka: att flikarna och stegen finns, att de heter rätt
saker, och att TMBox-simuleringen faktiskt är borta - inte bara dold.

De läser den serverade markupen, eftersom det är den en webbläsare får.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "tmbox_gateway" / "web"


class ShellStructureTests(unittest.TestCase):
    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.js = (WEB / "app.js").read_text(encoding="utf-8")
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_the_five_run_tabs_are_the_packages_five(self):
        for tab in ("oversikt", "trafik", "skarmar", "tkl", "tmbox"):
            self.assertIn(f'data-run-tab="{tab}"', self.html)
        self.assertEqual(5, self.html.count('data-run-tab="'))

    def test_the_five_build_steps_are_numbered_and_named(self):
        for step in ("kalla", "bana", "tid", "boxar", "server"):
            self.assertIn(f'data-build-step="{step}"', self.html)
        for label in ("Träffen", "Stationer och sträckor", "Tidtabell", "TMBoxar", "Server"):
            self.assertIn(label, self.html)

    def test_the_old_twelve_point_menu_is_gone(self):
        """Tolv menypunkter blir två lägen. Den gamla navigationen ska inte
        ligga kvar dold - då är det tretton."""
        self.assertNotIn("admin-section-link", self.html)
        self.assertNotIn('class="server-sidebar-nav"', self.html)

    def test_the_tmbox_simulation_is_removed_not_hidden(self):
        """Paketets karta: TMBox-simulering → Borttagen. v2 räcker."""
        self.assertNotIn('id="simulator-view"', self.html)
        self.assertNotIn('id="keypad"', self.html)
        self.assertNotIn("function renderTMBox", self.js)
        self.assertNotIn("function pressKey", self.js)
        # v2 finns kvar och är oförändrad.
        self.assertIn('id="tmbox-v2-view"', self.html)
        self.assertIn('id="keypad-v2"', self.html)

    def test_build_mode_has_its_own_chrome(self):
        """Konfiguration och drift ska inte kunna förväxlas: byggläget ser
        annorlunda ut, inte bara annorlunda märkt."""
        self.assertIn('id="build-chrome"', self.html)
        self.assertIn("Byggläge", self.html)
        self.assertIn("Lämna byggläget", self.html)
        self.assertIn('body[data-mode="bygg"] .run-tabs { display: none; }', self.css)

    def test_the_unsaved_panel_says_the_meet_keeps_running(self):
        """Det viktigaste löftet i byggläget: träffen påverkas inte förrän man
        uttryckligen aktiverar."""
        self.assertIn('id="unsaved-panel"', self.html)
        self.assertIn("Granska och aktivera", self.html)
        self.assertIn("Spara utkast", self.html)
        self.assertIn("fortsätter köra", self.html)

    def test_the_tkl_terminal_is_embedded_with_a_way_out(self):
        self.assertIn('id="tkl-frame"', self.html)
        self.assertIn("Öppna i egen flik", self.html)

    def test_the_traffic_view_has_the_three_sections(self):
        for heading in ("På linjen just nu", "Inne på stationerna", "Tidslinje"):
            self.assertIn(heading, self.html)


class DesignTokenTests(unittest.TestCase):
    """DEL 6. Paketet anger exakta värden, och "liknande" är inte godkänt."""

    def setUp(self):
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_the_palette_is_the_packages(self):
        for token, value in [
            ("--paper", "#faf9f5"),
            ("--surface-raised", "#ffffff"),
            ("--surface-muted", "#f7f5f0"),
            ("--line-field", "#e0dcd1"),
            ("--line-card", "#e8e5dc"),
            ("--ink-strong", "#1f1e1d"),
            ("--ink-muted", "#706c61"),
            ("--accent-warm", "#c96442"),
            ("--accent-warm-dark", "#a44f33"),
            ("--go-green", "#4b7a4f"),
            ("--chrome-dark", "#1f1e1d"),
        ]:
            with self.subTest(token=token):
                self.assertIn(f"{token}: {value};", self.css)

    def test_the_radii_are_the_packages(self):
        self.assertIn("--radius: 12px;", self.css)      # kort
        self.assertIn("--radius-sm: 8px;", self.css)    # fält och knappar
        self.assertIn("--radius-inner: 10px;", self.css)

    def test_times_and_numbers_are_monospace(self):
        """DEL 6: den enskilt viktigaste typografiska regeln - siffror som ska
        jämföras måste ligga i rad."""
        # Exakt selektor, inte substräng: .traffic-time och .traffic-times är
        # två olika regler och en substrängsökning hittar fel block.
        for selector in (".app-clock", ".traffic-time", ".traffic-times",
                         ".traffic-train-number", ".traffic-station-code"):
            index = self.css.index(selector + " {")
            block = self.css[index:index + 400]
            self.assertIn("ui-monospace", block, selector)

    def test_motion_is_only_where_it_means_something(self):
        """DEL 7.7: blinkar allt betyder blinkandet ingenting."""
        self.assertIn("@keyframes traffic-pulse", self.css)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_no_external_font_is_reintroduced(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fonts.gstatic.com", html)


class SourceChoiceTests(unittest.TestCase):
    """BYGG steg 1: källvalet styr faktisk låsning, inte bara märkning.

    Det är hela poängen med sammanslagningen - tre menypunkter blir ett val
    som styr resten. Valet är serverns driftläge, som redan låser
    redigeringsvägarna sedan 1.2.0, så UI:t hittar inte på en egen sanning.
    """

    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.js = (WEB / "app.js").read_text(encoding="utf-8")
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_the_three_sources_replace_three_menu_points(self):
        for source in ("cloud", "lokal", "fil"):
            self.assertIn(f'data-source="{source}"', self.html)
        for text in ("TrainMeet Cloud", "Lokalt utkast", "Importerad fil"):
            self.assertIn(text, self.html)

    def test_the_choice_is_the_servers_operating_mode(self):
        """Inte en etikett: cloud → cloud-linked, lokal → offline-meet."""
        self.assertIn('cloud: "cloud-linked"', self.js)
        self.assertIn('lokal: "offline-meet"', self.js)
        self.assertIn('"/v1/operating-mode"', self.js)

    def test_locking_is_read_from_the_server_never_decided_here(self):
        """locked = source === "cloud" (DEL 5), men härledd ur serverns svar
        så att UI och server inte kan tycka olika."""
        self.assertIn("modeState.editing_open", self.js)
        # dataset.sourceLocked är JS-formen av attributet data-source-locked,
        # som CSS:en hakar på. Två stavningar av samma sak.
        self.assertIn("dataset.sourceLocked", self.js)
        self.assertIn('body[data-source-locked="true"]', self.css)

    def test_going_back_to_cloud_shows_what_is_discarded(self):
        """D4: aldrig tyst. Servern räknar; UI:t visar och frågar."""
        self.assertIn("confirm_discard", self.js)
        self.assertIn("discards_on_return", self.js)
        self.assertIn("discard_local_revisions", self.js)

    def test_the_selected_card_uses_the_packages_colours(self):
        block = self.css[self.css.index(".source-card.selected {"):][:200]
        self.assertIn("var(--accent-warm)", block)
        self.assertIn("var(--accent-tint-light)", block)

    def test_the_step_rail_subtitles_come_from_real_data(self):
        """Ingen prototypdata: talen kommer ur /v1/runtime och /v1/devices."""
        self.assertIn("state.runtime || {}", self.js)
        self.assertIn("state.devices?.length", self.js)
        self.assertNotIn("Cloud rev 12", self.js)
        self.assertNotIn("499 rörelser", self.js)


class BuildStepFiveServerTests(unittest.TestCase):
    """BYGG steg 5 - Server, paketets DEL 3.11.

    Steget slår ihop tre av dagens menypunkter. Testerna håller fast att de
    faktiskt hamnade där, i paketets ordning, och att inget av det som flyttade
    tappades på vägen - särskilt de sju uppdateringsstegen, som kommer ur
    servern och inte får byggas om i webbläsaren.
    """

    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.js = (WEB / "app.js").read_text(encoding="utf-8")
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_the_step_gathers_the_three_old_menu_points(self):
        """Skärmkartan: Användare och åtkomst, Programuppdatering samt Server
        och nollställning hamnar alla i steg 5."""
        self.assertIn(
            'server: ["identity", "access", "software", "system"]',
            self.js,
        )

    def test_the_four_blocks_stand_in_the_packages_order(self):
        """DOM-ordningen är den som syns: identitet, inloggning, uppdatering,
        nollställning. Ett kort som glider förbi ett annat syns inte i något
        annat test."""
        order = [
            self.html.index('data-admin-section="identity"'),
            self.html.index('data-admin-section="access"'),
            self.html.index('data-admin-section="software"'),
            self.html.index('data-admin-section="system"'),
        ]
        self.assertEqual(order, sorted(order))

    def test_the_step_has_its_own_heading(self):
        self.assertIn('data-build-panel="server"', self.html)
        self.assertIn("<h2>Server</h2>", self.html)

    def test_identity_is_three_status_boxes_and_the_server_name(self):
        """3.11.1. Rutorna och namnfältet låg på var sitt håll förut."""
        panel = self._panel("server-identity-settings")
        for label in ("SERVER", "AKTIV TRÄFF", "CLOUD"):
            self.assertIn(f"<small>{label}</small>", panel)
        self.assertIn('id="admin-server-name"', panel)
        self.assertIn("Spara servernamn", panel)

    def test_the_status_boxes_read_real_server_state(self):
        """Ingen prototypdata: /v1/info föder alla tre."""
        self.assertIn('document.querySelector("#system-server-name").textContent = info.', self.js)
        self.assertIn("info.runtime?.linked", self.js)

    def test_the_status_boxes_stay_three_across(self):
        """Paketets bild visar tre i bredd vid 924px. auto-fit bröt till 2 + 1
        eftersom kortets insida är 556px där."""
        block = self.css[self.css.index(".identity-status-grid {"):][:220]
        self.assertIn("repeat(3, minmax(0, 1fr))", block)

    def test_external_login_is_three_columns_with_a_chip(self):
        """3.11.2."""
        panel = self._panel("admin-access-settings")
        self.assertIn("<h2>Extern admininloggning</h2>", panel)
        self.assertIn("På serverdatorn öppnas admin utan inloggning", panel)
        for field in ("admin-username", "admin-password", "admin-password-confirm"):
            self.assertIn(f'id="{field}"', panel)
        self.assertIn('id="access-mode"', panel)
        self.assertIn(".access-grid { grid-template-columns: repeat(3, minmax(180px, 1fr)); }", self.css)

    def test_the_access_chip_says_how_this_browser_is_connected(self):
        """Paketet skriver "Lokal åtkomst" i chippet. Det är inte en etikett
        utan serverns access_mode, som avgör både texten och vilken
        nollställning knappen längre ned gör."""
        self.assertIn('state.authStatus?.access_mode === "external"', self.js)
        self.assertIn('"Extern inloggning" : "Lokal åtkomst"', self.js)

    def test_the_login_username_field_is_never_filled_in_by_the_program(self):
        """Inloggningsfältet ska fortsätta vara tomt. Webbläsarens egen
        lösenordshanterare får gärna erbjuda ett sparat konto - det är
        användarens val, inte vårt."""
        writes = re.findall(r"#login-username\"\)\.value\s*=[^=]", self.js)
        self.assertEqual([], writes)
        # Fältet läses när man loggar in - det är inte att fylla i det.
        self.assertIn('#login-username").value,', self.js)
        self.assertIn("The username field is left alone", self.js)

    def test_the_seven_update_steps_come_from_the_server(self):
        """3.11.3. Stegen är serverns kontrakt. Webbläsaren får rita dem, inte
        hitta på dem: skulle etiketterna stå i app.js kunde de glida ifrån
        update_contract.py utan att något test märkte det."""
        from tmbox_gateway.update_contract import STAGE_LABELS, STAGES

        self.assertEqual(
            [STAGE_LABELS[stage] for stage in STAGES],
            [
                "Söker efter uppdatering",
                "Hämtar",
                "Verifierar",
                "Installerar",
                "Startar om",
                "Kontrollerar att tjänsten fungerar",
                "Klart",
            ],
        )
        # "Startar om" är också ett anslutningsläge i app.js, så bara de
        # etiketter som bara kan komma ur uppdateringskontraktet duger som
        # bevis för att listan inte är dubblerad i webbläsaren.
        for label in ("Söker efter uppdatering", "Verifierar",
                      "Kontrollerar att tjänsten fungerar"):
            self.assertNotIn(label, self.js)
        self.assertIn("payload.steps || []", self.js)
        self.assertIn("step.label", self.js)

    def test_each_step_carries_its_state_as_a_word(self):
        """Färg ensam räcker inte. Orden är en översättning av kontraktets
        fyra tillstånd, inte ett femte tillstånd."""
        self.assertIn("UPDATE_STATE_WORDS", self.js)
        for state in ("done", "active", "pending", "failed"):
            self.assertIn(f"  {state}: ", self.js)
        self.assertIn("update-step-state", self.js)
        self.assertIn(".update-step-state", self.css)

    def test_the_version_row_carries_version_and_build(self):
        """Paketet: "1.0.0 · build 4bd9c9a". Båda kommer ur /v1/server/update;
        build-id:t är tomt i en utvecklingskatalog och raden faller då tillbaka
        på enbart versionen."""
        self.assertIn("· build ${build}", self.js)
        self.assertIn("payload.installed_build", self.js)
        self.assertIn('class="version-row"', self.html)

    def test_the_restart_button_stands_where_the_package_puts_it(self):
        """Knappen finns på två ställen och delar tillstånd, så de inte kan
        säga olika om huruvida en omstart behövs."""
        self.assertIn('id="software-restart"', self.html)
        self.assertIn('id="restart-server"', self.html)
        self.assertIn('const restartButtons = ["#restart-server", "#software-restart"]', self.js)
        self.assertIn("setRestartButtonsVisible", self.js)

    def test_the_reset_is_collapsed_and_carries_the_packages_edges(self):
        """3.11.4: hopfällt <details>, kant #e6cfc7, botten #fdf6f3."""
        panel = self._panel("server-system-settings")
        self.assertIn("<details class=\"reset-details\">", panel)
        self.assertNotIn("open", panel.split("<summary")[0].split("<details")[1])
        block = self.css[self.css.index(".reset-card {"):][:220]
        self.assertIn("var(--accent-edge)", block)
        self.assertIn("var(--accent-tint-light)", block)
        self.assertIn("--accent-edge: #e6cfc7;", self.css)
        self.assertIn("--accent-tint-light: #fdf6f3;", self.css)

    def test_the_reset_still_demands_the_word(self):
        panel = self._panel("server-system-settings")
        self.assertIn("NOLLSTÄLL", panel)
        self.assertIn('id="factory-reset-confirmation"', panel)
        self.assertIn("disabled", panel)
        self.assertIn('!== "NOLLSTÄLL"', self.js)

    def test_the_reset_summary_says_which_reset_this_is(self):
        """Lokalt raderas administratören också. Det är den enda texten som
        syns när blocket är hopfällt, så den måste skilja de två åt."""
        self.assertIn('#reset-mode-summary', self.js)
        self.assertIn('"Fabriksåterställ servern"', self.js)
        self.assertIn('"Nollställ träffdata"', self.js)

    def test_the_old_admin_page_heading_is_gone(self):
        """Den skrevs inte längre av någon kod och sa emot byggstegets egen
        rubrik i varje steg."""
        for leftover in ("admin-page-heading", "admin-section-title",
                         "admin-section-eyebrow", "admin-section-state",
                         "selectedAdminSection", "adminSections"):
            self.assertNotIn(leftover, self.html)
            self.assertNotIn(leftover, self.js)
            self.assertNotIn(leftover, self.css)

    def test_the_step_five_buttons_carry_the_packages_shape(self):
        """DEL 6: 8px radie, kant #e0dcd1, 32-36px hög. Uppmätt i bygg-10."""
        block = self.css[self.css.index(".server-step-card button {"):][:320]
        self.assertIn("border-radius: var(--radius-sm)", block)
        self.assertIn("border: 1px solid var(--line-field)", block)
        self.assertIn("min-height: 34px", block)
        self.assertIn("--radius-sm: 8px;", self.css)
        self.assertIn("--line-field: #e0dcd1;", self.css)

    def test_the_button_shape_is_scoped_to_the_step_not_global(self):
        """Den globala knappregeln slår igenom på KÖR-vyer som redan är
        visuellt verifierade. Ändras den här av misstag blir de fel utan att
        någon tittar på dem."""
        self.assertIn("button {\n  min-height: var(--control-height);", self.css)
        self.assertIn("border-radius: 999px;", self.css)
        # Varje knappregel för steg 5 måste bära scopet. Basregeln matchas med
        # sin första deklaration: enbart selektorn räcker inte, eftersom
        # reduced-motion-regeln har samma selektor och skulle svara ja.
        for rule in (".server-step-card button {\n  min-height: 34px;",
                     ".server-step-card button.secondary {",
                     ".server-step-card button.primary {",
                     ".server-step-card button.danger-action {",
                     ".server-step-card button:disabled {"):
            self.assertIn(rule, self.css)
        # Alla fyra korten i steget bär klassen, annars faller något utanför.
        self.assertEqual(4, self.html.count("server-step-card"))
        for element_id in ("server-identity-settings", "admin-access-settings",
                           "software-update-settings", "server-system-settings"):
            self.assertIn("server-step-card", self._panel(element_id)[:220])

    def test_the_destructive_button_stays_distinct_from_the_primary(self):
        """Paketets adminpalett har ingen röd, eftersom nollställningen ligger
        hopfälld i varje skärmbild. Att ge den accentfärgen skulle göra
        "installera en uppdatering" och "radera servern" till samma knapp."""
        danger = self.css[self.css.index(".server-step-card button.danger-action {"):][:220]
        self.assertIn("var(--danger)", danger)
        self.assertNotIn("var(--accent-warm)", danger)

    def test_a_disabled_button_reads_as_disabled(self):
        """Inte som en blek variant av sig själv: en halvgenomskinlig röd knapp
        ser fortfarande farlig ut."""
        block = self.css[self.css.index(".server-step-card button:disabled {"):][:260]
        self.assertIn("opacity: 1", block)
        self.assertIn("var(--ink-locked)", block)
        self.assertIn("var(--surface-muted)", block)
        self.assertIn("cursor: not-allowed", block)

    def test_reduced_motion_stops_the_button_transition(self):
        """Fokusringen ärver den globala 200ms-övergången. Under
        reduced-motion ska den komma direkt."""
        self.assertIn(".server-step-card button { transition: none; }", self.css)

    def _panel(self, element_id: str) -> str:
        start = self.html.index(f'id="{element_id}"')
        return self.html[start:self.html.index("</section>", start)]


class CSPTests(unittest.TestCase):
    """Serverns egen CSP är style-src 'self'.

    En HTML-sträng med style="..." är ett inline-attribut och avvisas. Det låg
    tyst i konsolen och gjorde att staplarna i översikten aldrig fick sin
    bredd. CSSOM (element.style.width) omfattas inte och är vägen framåt.
    """

    @staticmethod
    def _code_only(text: str) -> str:
        """Utan radkommentarer.

        En kommentar som *förklarar* att style="..." är förbjudet innehåller
        style="..." och skulle annars fälla testet. Samma fälla som en
        commit-text om en versionsmarkör.
        """
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith(("//", "*", "/*"))
        )

    def test_no_inline_style_attribute_is_built_in_a_markup_string(self):
        js = self._code_only((WEB / "app.js").read_text(encoding="utf-8"))
        self.assertNotIn('style="width:', js)
        self.assertNotIn("style='width:", js)
        self.assertNotIn('setAttribute("style"', js)

    def test_no_inline_style_attribute_in_the_markup(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('style="', html)


if __name__ == "__main__":
    unittest.main()
