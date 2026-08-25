"""Dokumentationen namnger menyer som faktiskt finns.

README talade om **System → Programuppdatering** efter att den menypunkten
bytt namn. En operatör som följer en instruktion och letar efter något som
inte finns har fått fel hjälp, och det märks inte i något annat test.

Testet är avsiktligt snålt: det jagar bara namn som *fanns* och är borta. Att
kontrollera all prosa mot gränssnittet vore att uppfinna en dokumentationslint
som ingen orkar hålla.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "tmbox_gateway" / "web"

#: Menypunkter som har bytt namn eller flyttat. Nyckeln är det gamla namnet,
#: värdet är vad som gäller nu, så felmeddelandet säger vad man ska skriva.
RETIRED = {
    "System → Programuppdatering": "⚙ Inställningar → Programuppdatering",
    "System &rarr; Programuppdatering": "⚙ Inställningar → Programuppdatering",
}

DOCS = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))


class RetiredMenuNameTests(unittest.TestCase):
    def test_no_document_sends_an_operator_to_a_menu_that_is_gone(self):
        for path in DOCS:
            text = path.read_text(encoding="utf-8")
            for gone, now in RETIRED.items():
                with self.subTest(doc=path.name, name=gone):
                    self.assertNotIn(
                        gone, text,
                        f"{path.name} skickar operatören till '{gone}'. Numera: '{now}'.",
                    )

    def test_the_replacement_name_is_the_one_the_interface_uses(self):
        """Annars byter testet bara ett fel namn mot ett annat."""
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("<h2>Inställningar</h2>", html)
        self.assertIn("<h2>Programuppdatering</h2>", html)

    def test_the_readme_names_the_settings_menu_somewhere(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Inställningar → Programuppdatering", readme)


if __name__ == "__main__":
    unittest.main()


class RecoveryInstructionTests(unittest.TestCase):
    """Kommandot i README ska gå att klistra in.

    Det är den enda vägen tillbaka in i en server vars lösenord är borta. En
    felstavad modul eller en flagga som bytt namn upptäcks annars av den som
    behöver den, den dagen hen inte kommer in.
    """

    def setUp(self) -> None:
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_module_and_its_flags_exist(self) -> None:
        """Flaggorna provas genom att köra kommandot, inte genom att läsa det.

        En tom mapp är ingen installation, så kommandot avbryter - men det gör
        det efter att ha tolkat flaggorna, vilket är precis vad som prövas.
        """

        import tempfile
        from tmbox_gateway import recover

        self.assertIn("python -m tmbox_gateway.recover", self.readme)
        for flag in ("--state-dir", "--user"):
            self.assertIn(flag, self.readme)

        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(SystemExit) as missing:
                recover.main(["--state-dir", empty, "--user", "casper"])
            self.assertIn("Hittar ingen installation", str(missing.exception))

    def test_the_paths_are_the_ones_the_installation_uses(self) -> None:
        updater = (ROOT / "packaging" / "raspberry-pi" / "trainmeet-server-update").read_text(
            encoding="utf-8"
        )
        service = (ROOT / "packaging" / "raspberry-pi" / "trainmeet-server.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("STATE_DIR=/var/lib/trainmeet-server", updater)
        self.assertIn("--state-dir /var/lib/trainmeet-server", self.readme)
        self.assertIn("INSTALL_DIR=/opt/trainmeet-server", updater)
        self.assertIn("/opt/trainmeet-server/venv/bin/python", self.readme)
        user = next(line for line in service.splitlines() if line.startswith("User="))
        self.assertIn(f"sudo -u {user.split('=', 1)[1]}", self.readme)
