"""Choosing an operating UI must never mutate EU/US runtime data."""
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src/tmbox_gateway"


class NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_picker = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" and attrs.get("class") == "meet-type-nav":
            self.in_picker = True
        elif self.in_picker and tag == "a":
            self.links.append(attrs)

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_picker = False


class MeetTypeNavigationTests(unittest.TestCase):
    def test_both_views_offer_same_window_links_without_runtime_commands(self):
        for relative, selected in (("web/index.html", "/"), ("us_web/index.html", "/us/dispatcher")):
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_text()
                parser = NavigationParser()
                parser.feed(source)
                self.assertEqual(["/", "/us/dispatcher"], [a["href"] for a in parser.links])
                self.assertEqual([selected], [a["href"] for a in parser.links if a.get("aria-current")])
                self.assertTrue(all("target" not in a and "onclick" not in a for a in parser.links))
                self.assertIn("/assets/meet-type.css", source)
                self.assertLess(source.index("i18n-messages.js"), source.index("meet-type-messages.js"))
                self.assertIn("Switching views does not change or stop an operating session.", source)

    def test_us_language_scope_is_preserved(self):
        self.assertIn('data-i18n-scope="us"', (ROOT / "us_web/index.html").read_text())
        self.assertNotIn('data-i18n-scope="us"', (ROOT / "web/index.html").read_text())
