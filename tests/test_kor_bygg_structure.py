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

    def test_the_build_steps_are_numbered_and_named(self):
        """Fyra steg, inte fem.

        Steg 5 hette Server och samlade serveradministrationen. Den ligger nu i
        ett eget läge: att administrera servern är varken drift eller bygge, och
        låg tidigare bakom knappen "Bygg om träffen" - alltså bakom ett flöde
        som handlar om något annat. Se docs/DESIGNPAKET-DOD.md, avvikelse 7.
        """
        steps = re.findall(r'data-build-step="([a-z]+)"', self.html)
        self.assertEqual(["kalla", "bana", "tid", "boxar"], steps)
        for name in ("Träffen", "Stationer och sträckor", "Tidtabell", "TMBoxar"):
            self.assertIn(name, self.html)
        self.assertNotIn('data-build-step="server"', self.html)
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


class ServerSettingsTests(unittest.TestCase):
    """Inställningar - serveradministrationen, i ett eget läge.

    Innehållet är paketets DEL 3.11 plus Cloud-kopplingen. Det låg i BYGG steg
    5 och nås nu utan att gå via "Bygg om träffen": det flödet handlar om
    träffens innehåll, och serveradministration hör inte hemma bakom det.
    Programuppdateringen var fyra klick bort och är ett.

    Testerna håller fast att allt som flyttade kom fram, i paketets ordning,
    och att de sju uppdateringsstegen fortfarande kommer ur servern.
    """

    def setUp(self):
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.js = (WEB / "app.js").read_text(encoding="utf-8")
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_the_settings_view_gathers_the_old_menu_points(self):
        """Användare och åtkomst, Programuppdatering, Server och nollställning
        - plus Cloud-kopplingen, som satt i steg 1 tillsammans med källvalet
        fast den är serveradministration.

        `users` kom till när servern fick fler än en användare: listan över
        vilka som har tillgång hör hemma bredvid åtkomstkortet, inte i ett
        byggsteg."""
        self.assertIn(
            'const SETTINGS_SECTIONS = ["identity", "access", "users", "software", "cloud", "system"];',
            self.js,
        )

    def test_the_cloud_card_left_the_source_step(self):
        """Steg 1 svarar på var träffen kommer ifrån. Kopplingen och
        parkopplingen av lådor är något annat."""
        self.assertIn('kalla: ["runtime", "local", "import"]', self.js)

    def test_the_mode_helpers_know_all_three_modes(self):
        """currentMode() svarade `kor` för allt utom `bygg`.

        Den hade noll anropsställen, så felet syntes aldrig där. Men samma
        tvåvägsval fanns på startraden: setMode *skriver* `installningar` till
        localStorage, och starten kunde inte läsa tillbaka det - lämnade man
        appen i Inställningar och laddade om hamnade man i KÖR.

        Båda läser numera MODES, så ett fjärde läge behöver bara läggas där.
        """
        for helper in ("function currentMode()", "function storedMode()"):
            block = self.js.split(helper, 1)[1][:260]
            with self.subTest(helper=helper):
                self.assertIn("MODES.includes(", block)
                self.assertNotIn('=== "bygg" ? "bygg" : "kor"', block)

    def test_nothing_reads_the_mode_past_the_helpers(self):
        """Ett andra ställe som tolkar `data-mode` är ett andra ställe som kan
        glömma ett läge."""
        reads = [line for line in self.js.splitlines()
                 if "dataset.mode" in line and "dataset.mode =" not in line]
        self.assertEqual(1, len(reads), f"fler än ett ställe läser läget: {reads}")

    def test_the_boot_line_uses_the_helper(self):
        self.assertIn("document.body.dataset.mode = storedMode();", self.js)

    def test_settings_is_its_own_mode(self):
        self.assertIn('const MODES = ["kor", "bygg", "installningar"];', self.js)
        self.assertIn("function showSettings()", self.js)

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

    def test_the_view_has_its_own_heading(self):
        self.assertIn('id="settings-heading"', self.html)
        self.assertIn("<h2>Inställningar</h2>", self.html)
        self.assertNotIn('data-build-panel="server"', self.html)

    def test_the_dark_bar_belongs_to_build_mode_alone(self):
        """Den mörka listen betyder "det här är inte träffen som kör".

        Inställningar lånade den och lovade en fara som inte finns: ingenting
        stageas, ingenting behöver aktiveras, träffen rullar på. Priset var två
        klistrade lister på varandra och en rubrik som sa samma sak två gånger.

        Vägen tillbaka ligger i applocket i stället, bredvid kugghjulet som
        redan lyser i läget.
        """
        self.assertNotIn('id="settings-chrome"', self.html)
        self.assertEqual(1, self.html.count(' class="build-chrome hidden"'))
        topbar = self.html[self.html.index('<header class="topbar"'):]
        topbar = topbar[: topbar.index("</header>")]
        self.assertIn('id="leave-settings"', topbar)
        self.assertIn('body[data-mode="installningar"] .leave-settings { display: inline-flex', self.css)
        hidden = self.css[self.css.index(".leave-settings {"):][:60]
        self.assertIn("display: none;", hidden)

    def test_the_topbar_stays_one_line_on_a_phone(self):
        """Uppmätt i Chromium på 360px: "TrainMeet Server" och "Inga boxar"
        bröts till två rader var och locket blev dubbelt så högt. Namnet kortas
        med ellips i stället, och i Inställningar - där knappen tillbaka
        konkurrerar om samma rad - faller det bort helt under 480px."""
        narrow = self.css[self.css.index("@media (max-width: 680px) {", self.css.index(".leave-settings {")):][:900]
        self.assertIn("text-overflow: ellipsis", narrow)
        self.assertIn(".app-devices { white-space: nowrap; }", narrow)
        self.assertIn('body[data-mode="installningar"] .topbar-right { flex: 0 0 auto; }', narrow)
        tiny = self.css[self.css.index("@media (max-width: 480px) {"):][:200]
        self.assertIn('body[data-mode="installningar"] .topbar .brand-lockup h1 { display: none; }', tiny)

    def test_the_build_bar_stays_one_line_on_a_phone(self):
        """Uppmätt i Chromium på 360px: förklaringen radbröts till 194px höjd -
        en fjärdedel av skärmen för en mening man läser en gång."""
        rule = self.css.index(".build-chrome-note { display: none; }")
        media = self.css.rindex("@media (max-width: 720px) {", 0, rule)
        self.assertLess(rule - media, 120, "regeln ligger inte i telefonbrytpunkten")

    def test_the_gear_opens_settings_and_not_the_source_step(self):
        """Knappen hette "Öppna administration" men landade i BYGG steg 1, som
        handlar om var träffen kommer ifrån. Det är felet ärendet beskriver."""
        self.assertIn('else setMode("installningar");', self.js)
        self.assertNotIn('else { setMode("bygg"); selectBuildStep("kalla"); }', self.js)

    def test_the_settings_heading_does_not_follow_into_the_other_modes(self):
        """Rubriken syntes ovanför BYGG steg 1 tills det här fångades.

        showSettings() visade den, och ingenting dolde den igen. Testerna
        kontrollerade läge och steg och gick igenom - felet syntes bara på en
        skärmbild.
        """
        for view in ("function selectRunTab(tab) {", "function selectBuildStep(step) {"):
            block = self.js.split(view, 1)[1][:400]
            with self.subTest(view=view):
                self.assertIn('#settings-heading', block)
                self.assertIn('classList.add("hidden")', block)

    def test_settings_is_reachable_from_the_top_bar(self):
        """Utan en permanent ingång måste man gå via "Bygg om träffen" - alltså
        via ett flöde som handlar om något annat."""
        self.assertIn('id="open-settings"', self.html)
        self.assertIn('setMode("installningar")', self.js)

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

    def test_the_login_card_is_three_columns_with_a_chip(self):
        """3.11.2. Kortet hette "Extern admininloggning" så länge inloggningen
        bara gällde utifrån. Nu gäller den överallt, och namnet med."""
        panel = self._panel("admin-access-settings")
        self.assertIn("<h2>Inloggning</h2>", panel)
        self.assertIn("Inloggning krävs överallt, också på serverdatorn", panel)
        for field in ("admin-username", "admin-password", "admin-password-confirm"):
            self.assertIn(f'id="{field}"', panel)
        self.assertIn('id="access-mode"', panel)
        # Tre kolumner när det finns plats, färre när det inte gör det.
        #
        # Testet pinnade tidigare strängen `repeat(3, minmax(180px, 1fr))`.
        # Den formen kräver 598px och svämmade över byggstegets innehållsyta,
        # som är 572px vid paketets 924px - sidofältet tar resten. Medie-
        # förfrågan som skulle fällt ihop den lyssnar på fönstret, inte på
        # ytan, så den slog aldrig till.
        #
        # Det som ska hålla är alltså inte antalet kolumner utan att formuläret
        # lägger om i stället för att sticka ut.
        self.assertIn(".access-grid { grid-template-columns: repeat(auto-fit,", self.css)
        self.assertIn("minmax(min(180px, 100%), 1fr)); }", self.css)

    def test_the_access_chip_says_where_this_browser_stands(self):
        """Chippet sa förr hur man var inne, och svaret var alltid detsamma som
        var man stod: på maskinen slapp man logga in.

        Servern kräver numera inloggning överallt, så den frågan är besvarad
        innan chippet ritas. Kvar är platsen, och den betyder fortfarande något:
        den avgör om nollställningen tar hela servern eller bara träffdata.
        Texten kommer ur serverns svar, inte ur en gissning i webbläsaren."""
        self.assertIn('state.authStatus?.at_the_machine === true', self.js)
        self.assertIn('"Vid servern" : "Över nätet"', self.js)
        self.assertNotIn("access_mode", self.js)

    def test_the_way_out_belongs_to_being_logged_in(self):
        """Utloggningsknappen doldes när man var inne utan inloggning. Nu finns
        inget sådant läge kvar utom under installationen."""
        self.assertIn('logoutButton.classList.toggle("hidden", !state.authStatus?.authenticated)', self.js)

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
        # Alla korten i steget bär klassen, annars faller något utanför.
        self.assertEqual(5, self.html.count("server-step-card"))
        for element_id in ("server-identity-settings", "admin-access-settings",
                           "admin-users-settings",
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
