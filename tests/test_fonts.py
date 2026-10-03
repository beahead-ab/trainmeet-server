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
    WEB / "kontrollrummet.css", PACKAGE / "us_web" / "style.css",
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
                if "var(--font" in declaration or "var(--kr-sans)" in declaration or "var(--kr-mono)" in declaration or "inherit" in declaration:
                    continue
                family = first_family(declaration)
                if not family:
                    continue
                with self.subTest(sheet=sheet.name, declaration=declaration[:80]):
                    self.assertIn(family, {"inter", "jetbrains mono"})

    def test_kontrollrummet_font_tokens_start_with_the_shipped_fonts(self):
        # Kontrollrummet sets every font through two tokens; if a token started
        # with a system font, every rule that uses it would too.
        css = (WEB / "kontrollrummet.css").read_text(encoding="utf-8")
        for token, family in {"--kr-sans": "inter", "--kr-mono": "jetbrains mono"}.items():
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
        self.assertIn('class="clock-numeral"', script)
        self.assertIn(".clock-numeral { font-family: var(--font); }", (WEB / "server-ui.css").read_text(encoding="utf-8"))

    def test_scripts_do_not_pick_a_system_font(self):
        # The simulation banner is styled from JavaScript on every page that
        # shows it; it names Inter first like the stylesheets do.
        banner = (WEB / "simulation-banner.js").read_text(encoding="utf-8")
        self.assertRegex(banner, r"font:\s*600 15px Inter,")


if __name__ == "__main__":
    unittest.main()
