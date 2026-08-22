"""KÖR och BYGG: två lägen i stället för tolv menypunkter.

Designpaketet är bindande för struktur. Dessa tester håller fast det som är
lätt att råka ändra tillbaka: att flikarna och stegen finns, att de heter rätt
saker, och att TMBox-simuleringen faktiskt är borta - inte bara dold.

De läser den serverade markupen, eftersom det är den en webbläsare får.
"""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
