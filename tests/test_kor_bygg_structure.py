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
        self.assertIn('renderTraffic(snapshot)', self.js)
        self.assertNotIn('id="traffic-view"', self.html)
        self.assertIn('id="overview-traffic"', self.html)
        self.assertNotIn('data-build-step=', self.html)
        self.assertNotIn('id="tkl-frame"', self.html)
        for retired_selector in ('#tkl-frame', '.tkl-toolbar', '.tkl-frame-wrap', '.overview-action {'):
            self.assertNotIn(retired_selector, self.css)
        self.assertIn('path: "/tkl/"', self.js)
        self.assertIn('path: "/tmbox/"', self.js)
        self.assertIn('location.replace("/tmbox/")', self.js)

    def test_workspace_cards_have_local_decorative_icons_and_accessible_labels(self):
        self.assertIn('const WORKSPACE_ICONS', self.js)
        self.assertIn('button.setAttribute("aria-labelledby", title.id)', self.js)
        self.assertIn('button.setAttribute("aria-describedby", detail.id)', self.js)
        self.assertIn('icon.setAttribute("aria-hidden", "true")', self.js)
        self.assertIn('.workspace-option:focus-visible', self.css)

    def test_menu_is_single_settings_entry(self):
        self.assertEqual(1, self.html.count('id="open-settings"'))
        self.assertIn('id="application-menu"', self.html)
        for route in ("#settings", "#screens", "#workspaces"):
            self.assertIn(f'href="{route}"', self.html)
        self.assertNotIn('data-open-view="admin"', self.html)

    def test_home_preserves_workspace(self):
        self.assertIn("function workspaceHome()", self.js)
        self.assertIn("sessionStorage.getItem(WORKSPACE_KEY)", self.js)
        self.assertIn('id="workspace-home"', self.html)
        self.assertIn('id="workspace-picker"', self.html)

    def test_clock_actions_do_not_set_a_new_time(self):
        self.assertIn('controlLocalClock({ action: "start" })', self.js)
        self.assertIn('controlLocalClock({ action: "stop" })', self.js)
        self.assertIn('authorizedFetch("/v1/clock"', self.js)

    def test_settings_include_runtime_devices(self):
        sections = re.search(r"const SETTINGS_SECTIONS = \[([^\]]*)\]", self.js)
        self.assertIsNotNone(sections)
        for name in ("identity", "access", "users", "devices", "software", "cloud", "system"):
            self.assertIn(f'"{name}"', sections.group(1))

    def test_admin_forms_are_real_dialogs(self):
        for name in ("server-identity-form", "admin-access-form", "users-invite-form",
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
