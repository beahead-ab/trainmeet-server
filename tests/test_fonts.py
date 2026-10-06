"""Typsnitten: Inter och JetBrains Mono, paketerade med servern, på varje sida.

Servern kör utan internet på träffen, och TV:n, mobilen och Raspberry Pi:n har
olika systemtypsnitt. Allt som inte uttryckligen pekar på de två paketerade
familjerna ser därför olika ut beroende på apparat. De här testerna håller
fast vid att varje sida laddar samma fonts.css och att ingen regel faller
tillbaka på ett systemtypsnitt."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway"
WEB = PACKAGE / "web"
FONTS = WEB / "fonts"

# terminal16_web (the virtual TMBox) is deliberately not here: its page moves to
# the UI kit in its own change (docs/TMBOX-WEBBKLIENT-DESIGN-2026-09-27.md), and
# the box itself – LCD and keys – keeps the physical TMBox's look, fonts included.
STYLESHEETS = [
    WEB / "app.css", WEB / "server-design.css", WEB / "server-ui.css", WEB / "meet-type.css",
    WEB / "kontrollrummet.css", WEB / "skarmar.css", WEB / "deltagare.css", PACKAGE / "us_web" / "style.css",
]


def first_family(declaration: str) -> str:
    value = declaration.split(":", 1)[1]
    # font shorthand: the family list follows the size (…px[/lh])
    if declaration.strip().lower().startswith("font:"):
        match = re.search(r"\d(?:px|rem|em|%)(?:/\S+)?\s+(.+)$", value.strip().rstrip(";"))
        if not match:
            return ""
        value = match.group(1)
    return value.split(",")[0].strip().strip("\"'").lower()


class ShippedFontsTests(unittest.TestCase):
    def test_every_face_in_fonts_css_is_shipped(self):
        css = (FONTS / "fonts.css").read_text(encoding="utf-8")
        files = re.findall(r'url\("([^"]+)"\)', css)
        self.assertTrue(files)
        for name in files:
            with self.subTest(font=name):
                self.assertTrue((FONTS / name).is_file(), name)

    def test_every_weight_the_pages_ask_for_is_shipped(self):
        css = (FONTS / "fonts.css").read_text(encoding="utf-8")
        for family, weights in {"Inter": (400, 500, 600, 700), "JetBrains Mono": (400, 500, 600, 700)}.items():
            for weight in weights:
                with self.subTest(family=family, weight=weight):
                    self.assertRegex(css, rf'font-family: "{family}"; font-style: normal; font-weight: {weight};')

    def test_no_rule_starts_with_a_system_font(self):
        for sheet in STYLESHEETS:
            css = sheet.read_text(encoding="utf-8")
            for match in re.finditer(r"font(?:-family)?\s*:[^;{}]+;", css):
                declaration = match.group(0)
                if "var(--font" in declaration or "var(--kr-sans)" in declaration or "var(--kr-mono)" in declaration or "var(--kr-num)" in declaration or "inherit" in declaration:
                    continue
                family = first_family(declaration)
                if not family:
                    continue
                with self.subTest(sheet=sheet.name, declaration=declaration[:80]):
                    self.assertIn(family, {"inter", "jetbrains mono"})

    def test_kontrollrummet_font_tokens_start_with_the_shipped_fonts(self):
        # Kontrollrummet sets every font through three tokens; if a token started
        # with a system font, every rule that uses it would too.
        css = (WEB / "kontrollrummet.css").read_text(encoding="utf-8")
        for token, family in {"--kr-sans": "inter", "--kr-mono": "jetbrains mono", "--kr-num": "inter"}.items():
            with self.subTest(token=token):
                match = re.search(rf"{token}\s*:\s*([^;]+);", css)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1).split(",")[0].strip().strip("\"'").lower(), family)

    def test_every_page_loads_the_shared_fonts(self):
        self.assertIn('@import "fonts/fonts.css"', (WEB / "server-design.css").read_text(encoding="utf-8").replace("url(", "").replace(")", "").replace("'", '"'))
        self.assertIn('href="/assets/fonts/fonts.css"', (PACKAGE / "us_web" / "index.html").read_text(encoding="utf-8"))

    def test_fonts_are_declared_once(self):
        # app.css used to declare Inter a second time from other files, which
        # made every page download the same four fonts twice.
        self.assertNotIn("@font-face", (WEB / "app.css").read_text(encoding="utf-8"))
        self.assertNotIn("@font-face", (PACKAGE / "us_web" / "style.css").read_text(encoding="utf-8"))

    def test_svg_text_uses_the_shipped_fonts(self):
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('font-family="sans-serif"', script)
        self.assertRegex(script, r'class="clock-numeral[ "]')
        self.assertIn(".clock-numeral { font-family: var(--font); }", (WEB / "server-ui.css").read_text(encoding="utf-8"))

    def test_scripts_do_not_pick_a_system_font(self):
        # The simulation banner is styled from JavaScript on every page that
        # shows it; it names Inter first like the stylesheets do.
        banner = (WEB / "simulation-banner.js").read_text(encoding="utf-8")
        self.assertRegex(banner, r"font:\s*600 15px Inter,")



class NumbersTests(unittest.TestCase):
    """Tider och nummer står i Inter med tabellsiffror, inte i JetBrains Mono (#122).

    Monospace-nollan med prick går på håll ihop med en åtta. Koder, adresser
    och tangenter behåller monospace; det här gäller det man läser som tal.
    """

    RULES = [
        (PACKAGE / "terminal16_web" / "style.css", ".box-timetable tbody th {"),  # tågnumret i boxens tidtabell
        (WEB / "server-ui.css", ".topology-train .train-number {"),               # tågmärket på ruttkartan
        (WEB / "server-ui.css", ".us-diagram-meta {"),                            # milstolparna i US-diagrammet
    ]

    def test_train_numbers_and_mileposts(self):
        for sheet, selector in self.RULES:
            css = sheet.read_text(encoding="utf-8")
            body = css[css.index(selector): css.index("}", css.index(selector))]
            with self.subTest(selector=selector):
                self.assertNotIn("mono", body)
                self.assertIn("var(--font)", body)
                self.assertIn("tabular-nums", body)

    def test_the_drift_clock_and_the_minutes_field(self):
        markup = (WEB / "index.html").read_text(encoding="utf-8")
        for element in ('id="overview-clock"', 'id="web-client-ttl"', 'id="software-version"'):
            tag = markup[markup.index(element): markup.index(">", markup.index(element))]
            with self.subTest(element=element):
                self.assertIn("kr-num", tag)
                self.assertNotRegex(tag, r"class=\"[^\"]*\b(kr-mono|mono)\b")

    def test_version_numbers_under_programuppdatering(self):
        # Programuppdatering: 3.4.0 och versionerna under Vad är nytt är tal.
        # Byggets id (26ac80b3) är en kod som läses upp och behåller monospace.
        app_js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('number.className = "kr-num"; number.textContent = entry.version;', app_js)
        self.assertNotIn('number.className = "kr-mono"', app_js)
        self.assertIn('buildId.className = "kr-mono"', app_js)
        settings_js = (WEB / "settings.js").read_text(encoding="utf-8")
        self.assertIn('setVersion: (version, build = "")', settings_js)
        css = (WEB / "kontrollrummet.css").read_text(encoding="utf-8")
        navver = css[css.index(".kr-navver {"):][:240].split("}")[0]
        self.assertIn("var(--kr-num)", navver)
        self.assertIn("tabular-nums", navver)
        self.assertNotIn("kr-mono", navver)

if __name__ == "__main__":
    unittest.main()
