"""Cloud-only one-meet shell, navigation and server administration contract."""
from __future__ import annotations
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "tmbox_gateway" / "web"

class ShellStructureTests(unittest.TestCase):
    def setUp(self):
        self.html = (WEB / "index.html").read_text()
        self.js = (WEB / "app.js").read_text()
        self.css = (WEB / "app.css").read_text()

    def test_single_overview_retires_the_operational_tabs(self):
        self.assertNotIn('data-run-tab=', self.html)
        self.assertNotIn('run-tabs', self.css)
        for retired in ('RUN_TABS', 'RUN_PANELS', 'selectRunTab', 'trafficTimer', 'renderTrafficView'):
            self.assertNotIn(retired, self.js)
        # Trafiken ritas av Drift (drift.js); app.js äger data och matar den.
        self.assertIn('pushDrift()', self.js)
        self.assertIn('function renderEvents()', (WEB / "drift.js").read_text())
        self.assertNotIn('id="traffic-view"', self.html)
        self.assertIn('id="overview-traffic"', self.html)
        self.assertNotIn('data-build-step=', self.html)
        self.assertNotIn('id="tkl-frame"', self.html)
        for retired_selector in ('#tkl-frame', '.tkl-toolbar', '.tkl-frame-wrap', '.overview-action {'):
            self.assertNotIn(retired_selector, self.css)
        # TKL has its own platform; the server no longer offers it as a workspace.
        self.assertNotIn('path: "/tkl/"', self.js)
        self.assertIn('path: "/tmbox/"', self.js)
        self.assertIn('location.replace("/tmbox/")', self.js)

    def test_participant_view_replaces_the_workspace_cards(self):
        # The start page is the participant view: readable without login,
        # one link to Drift, one button that starts a virtual TMBox.
        self.assertNotIn('const WORKSPACE_ICONS', self.js)
        self.assertNotIn('id="workspace-options"', self.html)
        self.assertIn('id="participant-view"', self.html)
        self.assertIn('id="pv-login"', self.html)
        self.assertIn('href="/tmbox/">Starta virtuell TMBox', self.html)
        self.assertIn('aria-label="Sök i tidtabellen"', self.html)
        self.assertIn('id="pv-topology"', self.html)

    def test_participant_view_is_the_designs_mobile_column(self):
        # Deltagare.dc: clock, the line right now, what comes next, the
        # timetable with a search, then the buttons. "Anslut din TMBox" is a
        # button that opens a sheet from the bottom, not a permanent card.
        start = self.html.index('id="participant-view"')
        view = self.html[start:self.html.index('class="server-admin-shell"', start)]
        order = [view.index(marker) for marker in ('id="pv-clock"', 'id="pv-track-card"', 'id="pv-events-card"', 'id="pv-timetable-card"', 'id="pv-connect-open"')]
        self.assertEqual(order, sorted(order))
        self.assertIn('<dialog id="pv-connect-card" class="pv-sheet"', view)
        self.assertNotIn(' style=', view)
        self.assertNotIn('tm-card', view)
        self.assertNotIn('pv-foot-login', self.html)
        for link in ('/assets/kontrollrummet.css', '/assets/skarmar.css', '/assets/deltagare.css'):
            self.assertIn(f'href="{link}"', self.html)
        css = (WEB / "deltagare.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b", "colours come from the --kr- tokens")
        self.assertNotIn("participant-view", (WEB / "server-ui.css").read_text(encoding="utf-8"))
        # The map is Drift's: the same options, so on a phone held upright the
        # line stands upright with each station's code, as it does logged in.
        # tests/js/participant-desktop.test.cjs compares the two drawings.
        participant = (WEB / "participant.js").read_text(encoding="utf-8")
        self.assertIn("kr: { width: available }, tv: true", participant)
        self.assertIn("kr: { width }, tv: true", (WEB / "drift.js").read_text(encoding="utf-8"))
        self.assertNotIn("wide: true", participant)
        self.assertNotIn("noCode: true", participant)

    def test_screens_share_one_toolbar_and_keep_their_own_choices(self):
        self.assertIn('id="display-toolbar"', self.html)
        self.assertIn('class="kr-btn sm display-back"', self.html)
        for element in ("display-switch", "display-clock-style", "display-clock-seconds", "display-graph-window", "display-theme", "display-fullscreen"):
            self.assertIn(f'id="{element}"', self.html)
        self.assertLess(self.html.index('href="/assets/server-ui.css"'), self.html.index('href="/assets/skarmar.css"'))
        # Helskärm hides the toolbar after four seconds; a window keeps it.
        self.assertIn("const DISPLAY_TOOLBAR_HIDE_MS = 4000;", self.js)
        self.assertIn("function displayIsFullscreen()", self.js)
        for key in ("trainmeet.displayTheme", "trainmeet.displayGraphWindow", "trainmeet.displayClockStyle", "trainmeet.displayClockSeconds"):
            self.assertIn(key, self.js)
        self.assertIn("function clockDigitParts(", self.js)
        self.assertNotIn("function renderScrollableGraph", self.js)

    def test_menu_is_single_settings_entry(self):
        self.assertEqual(1, self.html.count('id="open-settings"'))
        self.assertIn('id="application-menu"', self.html)
        for route in ("#settings", "#screens"):
            self.assertIn(f'href="{route}"', self.html)
        self.assertNotIn('href="#workspaces"', self.html)
        self.assertNotIn('data-open-view="admin"', self.html)

    def test_home_is_drift_when_signed_in_and_the_participant_view_otherwise(self):
        self.assertIn('function workspaceHome() { return state.authStatus?.authenticated ? "/drift" : "/"; }', self.js)
        self.assertIn('history.replaceState(null, "", "/drift");', self.js)
        self.assertIn('id="workspace-home"', self.html)
        self.assertIn('id="participant-view"', self.html)
        self.assertNotIn('id="workspace-picker"', self.html)

    def test_clock_actions_do_not_set_a_new_time(self):
        self.assertIn('controlLocalClock({ action: "start" })', self.js)
        self.assertIn('controlLocalClock({ action: "stop" })', self.js)
        self.assertIn('authorizedFetch("/v1/clock"', self.js)

    def test_settings_and_drift_have_separate_responsibilities(self):
        # Inställningar är nio avsnitt, ett i taget (settings.js). Boxarna och
        # stationerna hör till Drift och finns inte här.
        sections = re.search(r"const SECTIONS = \[([^\]]*)\]", (WEB / "settings.js").read_text())
        self.assertIsNotNone(sections)
        names = re.findall(r'"(\w+)"', sections.group(1))
        self.assertEqual(names, ["traff", "skarmar", "wifi", "obemannade", "server", "kod", "anvandare", "uppdatering", "sprak", "farozon"])
        for name in names:
            self.assertIn(f'<section id="{name}" class="kr-setsec" data-section="{name}"', self.html)
            self.assertIn(f'href="/installningar#{name}"', self.html)
        self.assertNotIn("devices", names)
        self.assertNotIn("SETTINGS_SECTIONS", self.js)

    def test_admin_forms_are_real_dialogs(self):
        for name in ("admin-access-form", "users-invite-form",
                     "device-form", "runtime-sync-form", "clock-control-form"):
            start = self.html.index(f'<dialog id="{name}-modal"')
            end = self.html.index("</dialog>", start)
            self.assertIn(f'<form id="{name}"', self.html[start:end])
            self.assertIn("data-close-modal", self.html[start:end])
        self.assertIn('dialog.showModal()', self.js)
        self.assertIn('modalOrigins.get(dialog)', self.js)
        self.assertIn('modalChanged(dialog)', self.js)
        self.assertIn('beginModalAction', self.js)
        self.assertIn('endModalAction', self.js)

    def test_what_is_edited_in_place_has_its_own_save_bar(self):
        """Inställningar: varje panel som går att ändra är ett eget formulär med
        Avbryt och Spara, släckta tills något skiljer sig från det sparade."""
        forms = re.findall(r'<form id="([\w-]+)" class="kr-panel kr-setform"', self.html)
        self.assertEqual(forms, ["cloud-auto-form", "automatic-form", "clock-appearance-form", "connection-wifi-form",
                                 "connection-badge-form", "server-identity-form", "connection-code-form", "language-form"])
        for name in forms:
            start = self.html.index(f'<form id="{name}"')
            block = self.html[start:self.html.index("</form>", start)]
            self.assertIn('data-savebar', block, name)
            self.assertRegex(block, r'<button[^>]*data-save-cancel[^>]*disabled', name)
            self.assertRegex(block, r'<button[^>]*type="submit"[^>]*data-save-submit[^>]*disabled', name)
        # The dialogs these forms replaced are gone; no form is both.
        for gone in ("server-identity-form-modal", "cloud-auto-modal", "clock-appearance-modal", "connection-badge-modal"):
            self.assertNotIn(f'<dialog id="{gone}"', self.html)

    def test_software_and_config_updates_remain_separate(self):
        self.assertIn('id="runtime-check-update"', self.html)
        self.assertIn('id="software-check"', self.html)
        self.assertIn('"/v1/config/check"', self.js)
        self.assertIn('"/v1/server/update"', self.js)
        self.assertIn("payload.steps || []", self.js)

    def test_old_meet_command_scope_is_preserved(self):
        self.assertIn("body.meet_generation = state.serverContext.selected_meet.generation", self.js)
        self.assertIn('id="server-context-warning"', self.html)

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
        # Exakt selektor, inte substräng: .kr-ev .t och .kr-stat b är olika regler.
        kr = (WEB / "kontrollrummet.css").read_text()
        for selector in (".kr-clock-time", ".kr-stat b", ".kr-ev .t", ".kr-badge", ".kr-trainno", ".kr-code"):
            index = kr.index(selector + " {")
            block = kr[index:index + 300]
            # Through the token, so every one of them is the shipped JetBrains Mono.
            self.assertIn("var(--kr-mono)", block, selector)
        self.assertIn("#app-chrome .app-clock", kr)
        self.assertIn('--kr-mono: "JetBrains Mono"', kr)
        self.assertNotRegex(kr, r"font-family:\s*ui-monospace")
        self.assertNotRegex(self.css, r"font-family:\s*ui-monospace")

    def test_motion_is_only_where_it_means_something(self):
        """DEL 7.7: blinkar allt betyder blinkandet ingenting."""
        css = "".join((WEB / name).read_text(encoding="utf-8") for name in ("app.css", "kontrollrummet.css", "server-ui.css"))
        # Every animation that is defined is used by something, and everything
        # that moves stops when the system asks for less motion.
        for name in re.findall(r"@keyframes ([\w-]+)", css):
            self.assertRegex(css, rf"animation:[^;]*\b{name}\b", name)
        self.assertIn("@keyframes update-pulse", css)
        self.assertRegex(self.css, r"@media \(prefers-reduced-motion: reduce\)[^}]*animation-duration")

    def test_no_external_font_is_reintroduced(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fonts.gstatic.com", html)


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
