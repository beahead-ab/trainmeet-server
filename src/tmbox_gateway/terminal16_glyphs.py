"""Server-owned Unicode -> HD44780 5x8 pilot frame, independent of ROM variant.

Only the characters required by this frame are allocated to the eight CGRAM
slots. Never transliterate, split a UTF-8 sequence, or silently reuse a slot.
The firmware consumer is not integrated yet; web keeps the Unicode lines.
"""
from unicodedata import normalize


GLYPHS = {
    # These ASCII positions differ between A00/A02 ROMs as well.
    "\\": (16, 8, 4, 2, 1, 0, 0, 0),
    "~": (0, 0, 8, 21, 2, 0, 0, 0),
    "Å": (4, 10, 4, 14, 17, 31, 17, 17),
    "å": (4, 10, 4, 14, 1, 15, 17, 15),
    "Ä": (10, 0, 14, 17, 31, 17, 17, 0),
    "ä": (10, 0, 14, 1, 15, 17, 15, 0),
    "Ö": (10, 0, 14, 17, 17, 17, 14, 0),
    "ö": (10, 0, 0, 14, 17, 17, 14, 0),
    "Æ": (7, 12, 20, 23, 28, 20, 23, 0),
    "æ": (0, 0, 26, 5, 15, 20, 15, 0),
    "Ø": (15, 19, 21, 21, 21, 25, 30, 0),
    "ø": (0, 0, 15, 19, 21, 25, 30, 0),
    "Ü": (10, 0, 17, 17, 17, 17, 14, 0),
    "ü": (10, 0, 0, 17, 17, 19, 13, 0),
    "ß": (6, 9, 9, 14, 9, 9, 22, 0),
    "ẞ": (14, 17, 18, 20, 18, 17, 22, 0),
    "◀": (1, 3, 7, 15, 7, 3, 1, 0),
    "▶": (16, 24, 28, 30, 28, 24, 16, 0),
}


def text_cells(value):
    # Composed and decomposed Å/Ä/Ö must take exactly one LCD cell.
    return normalize("NFC", value)


def encode_lcd(lines):
    normalized = [text_cells(line) for line in lines]
    if len(normalized) != 2 or any(len(line) != 16 for line in normalized):
        raise ValueError("Expected two complete 16-cell rows")
    custom = sorted({char for line in normalized for char in line
                     if char in GLYPHS or not 32 <= ord(char) <= 126})
    if any(char not in GLYPHS for char in custom):
        raise ValueError("Unsupported LCD character: " + " ".join(char for char in custom if char not in GLYPHS))
    if len(custom) > 8:
        raise ValueError("More than eight custom characters; the server must choose another presentation")
    slots = {char: slot for slot, char in enumerate(custom)}
    return {
        "encoding": "hd44780-5x8-cgram-v1",
        "cells": [[slots[char] if char in slots else ord(char) for char in line] for line in normalized],
        "glyphs": [{"slot": slots[char], "character": char, "rows": list(GLYPHS[char])} for char in custom],
    }


def language_samples(clock):
    samples = (
        ("sv", "Svenska", "ÅÄÖ åäö ◀▶", "Tåg: spår"),
        ("da", "Dansk", "ÆØÅ æøå ◀▶", "Kør til ø"),
        ("nb", "Norsk (bokmål)", "ÆØÅ æøå ◀▶", "Kjør tog"),
        ("en", "English", "TRAIN 39 ◀▶", "Ready"),
        ("de", "Deutsch", "ÄÖÜẞ äöüß", "Grüße"),
    )
    result = []
    for code, label, first, hint in samples:
        lines = [first.ljust(16), hint.ljust(11) + clock]
        result.append({"language": code, "label": label, "lines": lines, "lcd": encode_lcd(lines)})
    return result
