"""Ett byggsteg som inte visar någonting ser ut som en sida som laddar.

BYGG hade fyra steg i knappraden och två paneler. Steg 3, Tidtabell, hade
varken panel eller administrationssektion: `selectBuildStep` dolde allt annat
och lämnade en tom yta. Rapporterat från drift som "tar lååång tid att ladda
om det ens går" - vilket är precis vad tomhet ser ut som när man väntar.

Ingenting var långsamt. Alla fyra stegen svarade på en halv sekund; det fanns
bara inget att rita.

Testet läser markupen och `app.js` i stället för att köra en webbläsare: det
som gick fel var att ett steg saknade sin panel, och det syns i filerna.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "tmbox_gateway" / "web"


def _markup() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


def _script() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


def _build_steps() -> list[str]:
    """Stegen som app.js känner till, i sin ordning."""

    match = re.search(r"const BUILD_STEPS = \[([^\]]*)\]", _script())
    assert match, "hittade inte BUILD_STEPS"
    return re.findall(r'"([a-z]+)"', match.group(1))


def _step_sections() -> dict[str, list[str]]:
    body = _script()
    start = body.index("const STEP_SECTIONS = {")
    end = body.index("};", start)
    found = {}
    for line in body[start:end].splitlines():
        match = re.match(r"\s*([a-z]+):\s*\[([^\]]*)\]", line)
        if match:
            found[match.group(1)] = re.findall(r'"([a-z]+)"', match.group(2))
    return found


class EveryBuildStepRendersSomethingTests(unittest.TestCase):
    def test_the_steps_in_the_markup_are_the_steps_the_script_knows(self) -> None:
        in_markup = set(re.findall(r'data-build-step="([a-z]+)"', _markup()))

        self.assertEqual(set(_build_steps()), in_markup)

    def test_no_step_leaves_the_page_empty(self) -> None:
        """Varje steg måste ha antingen en egen panel eller en sektion.

        Det ena eller det andra räcker: steg 4 har ingen panel men visar
        enhetssektionen, och det är inte tomt.
        """

        panels = set(re.findall(r'data-build-panel="([a-z]+)"', _markup()))
        sections = _step_sections()

        empty = [
            step
            for step in _build_steps()
            if step not in panels and not sections.get(step)
        ]
        self.assertEqual([], empty, "steg utan något att visa")

    def test_the_timetable_step_says_the_view_is_missing(self) -> None:
        """Så länge vyn inte är byggd ska steget säga det, inte tiga.

        Kraven står i docs/DESIGNPAKET-DOD.md 3.9. När vyn byggs ersätts den
        här texten, och då faller testet och påminner om att ta bort den.
        """

        markup = _markup()
        start = markup.index('data-build-panel="tid"')
        end = markup.index("</section>", start)
        panel = markup[start:end]

        self.assertIn("inte byggd", panel)
        self.assertIn("Trafik", panel, "peka läsaren dit rörelserna faktiskt syns")

    def test_the_timetable_step_is_not_described_as_read_only(self) -> None:
        """Designpaketet är uttryckligt: bygg inte steget som skrivskyddat.

        En platshållare får säga att vyn saknas, men den får inte lova att
        tidtabellen bara går att titta på - den ska bli redigerbar även i
        Cloud-läge, till skillnad från stationer och sträckor.
        """

        markup = _markup()
        start = markup.index('data-build-panel="tid"')
        panel = markup[start:markup.index("</section>", start)]

        self.assertIn("redigerbar", panel)
        for forbidden in ("skrivskyddad", "endast läsning", "bara läsa"):
            self.assertNotIn(forbidden, panel.lower())


if __name__ == "__main__":
    unittest.main()
