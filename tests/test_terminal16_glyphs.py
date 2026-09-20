from pathlib import Path
import re
import unittest

from tmbox_gateway.terminal16 import row
from tmbox_gateway.terminal16_glyphs import GLYPHS, encode_lcd, language_samples
from tmbox_gateway.terminal16_demo import demo_lab


class Terminal16GlyphTests(unittest.TestCase):
    def test_each_pattern_fits_five_by_eight_dots(self):
        for char, pattern in GLYPHS.items():
            with self.subTest(char=char):
                self.assertEqual(len(pattern), 8)
                self.assertTrue(all(0 <= bits < 32 for bits in pattern))

    def test_all_five_languages_round_trip_without_transliteration(self):
        samples = language_samples("12:34")
        self.assertEqual([s["language"] for s in samples], ["sv", "da", "nb", "en", "de"])
        for sample in samples:
            frame = sample["lcd"]
            table = {item["slot"]: item["character"] for item in frame["glyphs"]}
            decoded = ["".join(table[cell] if cell < 8 else chr(cell) for cell in line) for line in frame["cells"]]
            self.assertEqual(decoded, sample["lines"])
            self.assertEqual([len(line) for line in decoded], [16, 16])
            self.assertTrue(decoded[1].endswith("12:34"))

    def test_swedish_letters_and_both_arrows_fit_eight_slots(self):
        frame = encode_lcd([row("ÅÄÖ åäö ◀▶"), row("TÅG", "12:34")])
        self.assertEqual(len(frame["glyphs"]), 8)
        self.assertEqual({g["slot"] for g in frame["glyphs"]}, set(range(8)))
        self.assertIn(0, frame["cells"][0])  # NUL slot: firmware must write raw bytes, not a C string.

    def test_decomposed_text_takes_one_cell_per_letter(self):
        self.assertEqual(row("A\u030aA\u0308O\u0308", "12:34"), row("ÅÄÖ", "12:34"))
        self.assertEqual(encode_lcd(["A\u030a" + " " * 15, " " * 16]),
                         encode_lcd(["Å" + " " * 15, " " * 16]))

    def test_repeated_letters_share_a_slot_and_plain_text_needs_no_bank(self):
        self.assertEqual(len(encode_lcd([row("ÅÅÅ"), row("ååå")])["glyphs"]), 2)
        self.assertEqual(encode_lcd([row("TRAIN"), row("READY")])["glyphs"], [])
        self.assertEqual({g["character"] for g in encode_lcd([row("\\~"), row()])["glyphs"]}, {"\\", "~"})

    def test_no_silent_overflow_unknown_character_or_clipping(self):
        for lines in ([row("ÅÄÖåäöÆØÜ"), row()], [row("🚂"), row()], ["x" * 17, row()]):
            with self.assertRaises(ValueError): encode_lcd(lines)

    def test_pilot_and_entry_frames_carry_server_owned_glyphs(self):
        frame = demo_lab().frame("DEMO-CDA")
        self.assertEqual(frame["lcd"]["encoding"], "hd44780-5x8-cgram-v1")
        self.assertIn("TÅG", frame["entry"]["lines"][0])
        self.assertIn("#Sök", frame["entry"]["lines"][1])
        self.assertEqual({g["character"] for g in frame["entry"]["lcd"]["glyphs"]}, {"Å", "ö"})

    def test_original_client_palette_is_preserved(self):
        root = Path(__file__).resolve().parents[1] / "src/tmbox_gateway"
        original = (root / "web/app.css").read_text()
        pilot = (root / "terminal16_web/style.css").read_text()
        for name in ("case", "lcd-blue", "lcd-text", "key-panel", "key"):
            pattern = rf"--{name}:\s*(#[0-9a-f]+)"
            self.assertEqual(re.search(pattern, pilot)[1], re.search(pattern, original)[1])


if __name__ == "__main__":
    unittest.main()
