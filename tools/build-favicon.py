"""Bygger web/ikon/favicon.ico av ikonens PNG-filer (16, 32 och 64 px).

ICO-filen bär PNG-bilderna oförändrade, vilket alla dagens webbläsare läser.
Kör igen när ikonen ändras: python3 tools/build-favicon.py
"""

from pathlib import Path
import struct

IKON = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web" / "ikon"
SIZES = (16, 32, 64)


def build() -> bytes:
    images = [(IKON / "png" / f"trainmeet-ikon-{size}.png").read_bytes() for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    for size, image in zip(SIZES, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    return header + entries + b"".join(images)


if __name__ == "__main__":
    (IKON / "favicon.ico").write_bytes(build())
