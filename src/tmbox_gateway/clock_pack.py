"""Klockpaket: egna urtavlor som laddas upp till servern (Casper 2026-10-08).

Ett klockpaket (.tmclock) är en zip-fil med clock.json och urtavlans lager som
bilder: tavlan, tim-, minut- och sekundvisaren och ett valfritt lager ovanpå
(navet eller glaset). Alla lager är lika stora och kvadratiska, och visarna är
ritade pekande mot 12. Skärmen lägger lagren på varandra och vrider visarna
kring mitten; hur de går (jämnt, i hopp, sekundvisarens svep) står i
clock.json.

Paketet innehåller ingen kod, bara bilder och inställningar. SVG-lagren
kontrolleras här och visas som bilder, aldrig inne i sidan, så en uppladdad
klocka kan inte köra något i webbläsaren eller hämta något utifrån.

Samma kontroll används när servern tar emot ett paket och från kommandoraden
när någon gör ett eget:

    python -m tmbox_gateway.clock_pack example mitt-ur/
    python -m tmbox_gateway.clock_pack check mitt-ur/        (eller mitt-ur.tmclock)
    python -m tmbox_gateway.clock_pack build mitt-ur/ -o mitt-ur.tmclock

Formatet beskrivs i docs/clock-packs.md.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

FORMAT = 1
MANIFEST = "clock.json"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_LAYER_BYTES = 1024 * 1024
MAX_ENTRIES = 32
LAYERS = ("dial", "hour", "minute", "second", "top")
REQUIRED_LAYERS = ("dial", "hour", "minute")
MOTIONS = {"hour": ("smooth", "minute"), "minute": ("smooth", "jump"), "second": ("smooth", "tick", "sweep")}
DEFAULT_MOTION = {"hour": "smooth", "minute": "smooth", "minute_bounce": False, "second": "smooth", "sweep_seconds": 60.0}
# Hilfikers stationsur gör sekundvarvet på 58,5 s och väntar sedan vid 12.
DEFAULT_SWEEP_SECONDS = 58.5
SWEEP_RANGE = (30.0, 60.0)
PNG_SIZE_RANGE = (64, 4096)
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Element som kan köra kod, bädda in en annan sida, länka bort eller ändra
# bilden efter att den ritats. En urtavla behöver inget av dem.
_BLOCKED_ELEMENTS = {
    "script", "foreignobject", "iframe", "embed", "object", "audio", "video", "canvas", "a",
    "animate", "animatemotion", "animatetransform", "animatecolor", "set", "discard", "handler", "listener",
}
_DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,", re.IGNORECASE)
_EXTERNAL_URL = re.compile(r"url\(\s*['\"]?\s*(?!#|data:image/(?:png|jpeg|gif|webp);base64,)", re.IGNORECASE)


class ClockPackError(ValueError):
    """Ett klockpaket som inte går att använda. Texten säger vad som är fel."""


@dataclass(frozen=True)
class Layer:
    layer: str
    file: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ClockPack:
    id: str
    name: str
    version: str
    author: str
    motion: dict[str, Any]
    layers: dict[str, Layer]
    sha256: str

    def manifest(self) -> dict[str, Any]:
        """Det servern sparar om paketet, utan bilderna."""
        return {"format": FORMAT, "id": self.id, "name": self.name, "version": self.version, "author": self.author,
                "motion": dict(self.motion),
                "layers": {name: {"file": layer.file, "type": layer.content_type} for name, layer in self.layers.items()}}


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _text(manifest: dict[str, Any], key: str, limit: int, *, required: bool = False) -> str:
    value = manifest.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ClockPackError(f"clock.json: \"{key}\" ska vara en text på högst {limit} tecken.")
    return value.strip()


def slug(name: str) -> str:
    table = str.maketrans({"å": "a", "ä": "a", "ö": "o", "æ": "ae", "ø": "o", "ü": "u", "é": "e"})
    value = re.sub(r"[^a-z0-9]+", "-", name.lower().translate(table)).strip("-")
    return value[:40].strip("-") or "klocka"


def _motion(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ClockPackError("clock.json: \"motion\" ska vara ett objekt.")
    unknown = sorted(set(value) - set(DEFAULT_MOTION))
    if unknown:
        raise ClockPackError(f"clock.json: okänd inställning i \"motion\": {', '.join(unknown)}. "
                             f"Tillåtna är {', '.join(DEFAULT_MOTION)}.")
    motion = dict(DEFAULT_MOTION)
    for hand, choices in MOTIONS.items():
        if hand in value:
            if value[hand] not in choices:
                raise ClockPackError(f"clock.json: \"motion.{hand}\" ska vara {' eller '.join(choices)}.")
            motion[hand] = value[hand]
    if "minute_bounce" in value:
        if not isinstance(value["minute_bounce"], bool):
            raise ClockPackError("clock.json: \"motion.minute_bounce\" ska vara true eller false.")
        motion["minute_bounce"] = value["minute_bounce"]
    if motion["second"] == "sweep":
        sweep = value.get("sweep_seconds", DEFAULT_SWEEP_SECONDS)
        if isinstance(sweep, bool) or not isinstance(sweep, (int, float)) or not SWEEP_RANGE[0] <= sweep <= SWEEP_RANGE[1]:
            raise ClockPackError(f"clock.json: \"motion.sweep_seconds\" ska vara ett tal mellan {SWEEP_RANGE[0]:g} och {SWEEP_RANGE[1]:g}.")
        motion["sweep_seconds"] = float(sweep)
    elif "sweep_seconds" in value:
        raise ClockPackError("clock.json: \"motion.sweep_seconds\" gäller bara när \"motion.second\" är sweep.")
    if motion["minute_bounce"] and motion["minute"] != "jump":
        raise ClockPackError("clock.json: \"motion.minute_bounce\" gäller bara när \"motion.minute\" är jump.")
    return motion


def _check_css(file: str, css: str) -> None:
    lowered = css.lower()
    if "@import" in lowered or "expression(" in lowered or _EXTERNAL_URL.search(lowered):
        raise ClockPackError(f"{file}: stilen hämtar något utanför filen. Bara url(#…) och inbäddade bilder är tillåtna.")


def check_svg(file: str, data: bytes) -> None:
    """Godtar en SVG som bara ritar: inga skript, inga länkar ut, kvadratisk."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ClockPackError(f"{file}: SVG-filen ska vara sparad som UTF-8.") from error
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ClockPackError(f"{file}: DOCTYPE och ENTITY är inte tillåtna. Spara som vanlig SVG.")
    if "<?xml-stylesheet" in lowered:
        raise ClockPackError(f"{file}: en länkad stilmall är inte tillåten.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ClockPackError(f"{file}: SVG-filen går inte att läsa ({error}).") from error
    if _local(root.tag) != "svg":
        raise ClockPackError(f"{file}: filen är inte en SVG-bild.")
    for element in root.iter():
        tag = _local(element.tag)
        if tag.lower() in _BLOCKED_ELEMENTS:
            raise ClockPackError(f"{file}: <{tag}> är inte tillåtet i ett klockpaket.")
        for attribute, value in element.attrib.items():
            name = _local(attribute).lower()
            compact = re.sub(r"\s+", "", value).lower()
            if name.startswith("on"):
                raise ClockPackError(f"{file}: attributet {name} är inte tillåtet.")
            if "javascript:" in compact or "vbscript:" in compact:
                raise ClockPackError(f"{file}: en skriptlänk är inte tillåten.")
            if name == "href" and not (value.startswith("#") or _DATA_IMAGE.match(value)):
                raise ClockPackError(f"{file}: länkar till andra filer är inte tillåtna ({value[:40]}). "
                                     "Bädda in bilden eller rita den i samma fil.")
            if name == "style" or "url(" in compact:
                _check_css(file, value)
        if tag == "style":
            _check_css(file, element.text or "")
    view_box = (root.get("viewBox") or "").replace(",", " ").split()
    try:
        width, height = float(view_box[2]), float(view_box[3])
    except (IndexError, ValueError) as error:
        raise ClockPackError(f"{file}: viewBox saknas. Lagren ska ha samma kvadratiska viewBox, till exempel 0 0 200 200.") from error
    if width <= 0 or abs(width - height) > 0.01 * max(width, height):
        raise ClockPackError(f"{file}: bilden ska vara kvadratisk (viewBox {width:g} × {height:g}), så att visarna vrids kring mitten.")


def check_png(file: str, data: bytes) -> None:
    if not data.startswith(_PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise ClockPackError(f"{file}: filen är inte en PNG-bild.")
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    low, high = PNG_SIZE_RANGE
    if width != height:
        raise ClockPackError(f"{file}: bilden ska vara kvadratisk ({width} × {height} px), så att visarna vrids kring mitten.")
    if not low <= width <= high:
        raise ClockPackError(f"{file}: bilden ska vara mellan {low} och {high} px i sida ({width} px).")


def _layer(layer: str, file: str, data: bytes) -> Layer:
    if len(data) > MAX_LAYER_BYTES:
        raise ClockPackError(f"{file}: filen är större än {MAX_LAYER_BYTES // 1024} kB.")
    suffix = file.rsplit(".", 1)[-1].lower() if "." in file else ""
    if suffix == "svg":
        check_svg(file, data)
        return Layer(layer, file, "image/svg+xml", data)
    if suffix == "png":
        check_png(file, data)
        return Layer(layer, file, "image/png", data)
    raise ClockPackError(f"{file}: lagren ska vara SVG eller PNG.")


def _junk(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return name.startswith("__MACOSX/") or base in {".DS_Store", "Thumbs.db", "desktop.ini"} or base.startswith("._")


def parse(manifest_bytes: bytes, read_file, *, sha256: str) -> ClockPack:
    """Ett paket ur clock.json och en funktion som läser paketets filer."""
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClockPackError(f"clock.json går inte att läsa: {error}.") from error
    if not isinstance(manifest, dict):
        raise ClockPackError("clock.json ska vara ett JSON-objekt.")
    if manifest.get("format") != FORMAT:
        raise ClockPackError(f"clock.json: \"format\" ska vara {FORMAT}.")
    name = _text(manifest, "name", 60, required=True)
    pack_id = manifest.get("id", slug(name))
    if not isinstance(pack_id, str) or not ID_PATTERN.match(pack_id):
        raise ClockPackError("clock.json: \"id\" ska vara små bokstäver a–z, siffror och bindestreck, högst 40 tecken.")
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        raise ClockPackError("clock.json: \"layers\" saknas. Ange minst dial, hour och minute.")
    unknown = sorted(set(layers) - set(LAYERS))
    if unknown:
        raise ClockPackError(f"clock.json: okänt lager {', '.join(unknown)}. Lagren heter {', '.join(LAYERS)}.")
    missing = [layer for layer in REQUIRED_LAYERS if not layers.get(layer)]
    if missing:
        raise ClockPackError(f"clock.json: lagret {', '.join(missing)} saknas.")
    result: dict[str, Layer] = {}
    for layer in LAYERS:
        file = layers.get(layer)
        if file is None:
            continue
        if not isinstance(file, str) or not file or "/" in file or "\\" in file or file.startswith("."):
            raise ClockPackError(f"clock.json: lagret {layer} ska vara ett filnamn i paketet, utan mappar.")
        result[layer] = _layer(layer, file, read_file(file))
    return ClockPack(id=pack_id, name=name, version=_text(manifest, "version", 40), author=_text(manifest, "author", 80),
                     motion=_motion(manifest.get("motion")), layers=result, sha256=sha256)


def read_pack(data: bytes) -> ClockPack:
    """Läser och kontrollerar ett klockpaket (zip). Kastar ClockPackError."""
    if len(data) > MAX_PACKAGE_BYTES:
        raise ClockPackError(f"Klockpaketet är större än {MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ClockPackError("Filen är inte ett klockpaket. Ett klockpaket är en zip-fil med clock.json.") from error
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ClockPackError(f"Klockpaketet har fler än {MAX_ENTRIES} filer.")
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or ":" in name:
                raise ClockPackError(f"Klockpaketet har ett otillåtet filnamn: {name}.")
            if not info.is_dir() and not _junk(name):
                entries[name] = info
        # clock.json ligger överst, eller i en enda mapp (som när man packar en mapp).
        manifests = [name for name in entries if name.rsplit("/", 1)[-1] == MANIFEST]
        top = [name for name in manifests if "/" not in name]
        nested = [name for name in manifests if name.count("/") == 1]
        if top:
            prefix = ""
        elif len(nested) == 1:
            prefix = nested[0][: -len(MANIFEST)]
        else:
            raise ClockPackError("clock.json saknas i klockpaketet.")

        def read(file: str) -> bytes:
            info = entries.get(prefix + file)
            if info is None:
                raise ClockPackError(f"{file} finns inte i klockpaketet.")
            try:
                with archive.open(info) as handle:
                    content = handle.read(MAX_LAYER_BYTES + 1)
            except (RuntimeError, zipfile.BadZipFile, NotImplementedError, EOFError, OSError) as error:
                raise ClockPackError(f"{file} går inte att packa upp: {error}.") from error
            if len(content) > MAX_LAYER_BYTES:
                raise ClockPackError(f"{file}: filen är större än {MAX_LAYER_BYTES // 1024} kB.")
            return content

        return parse(read(MANIFEST), read, sha256=hashlib.sha256(data).hexdigest())


def build(directory: str | Path) -> bytes:
    """Packar en mapp till ett klockpaket och kontrollerar det. Bara clock.json
    och lagren som den nämner följer med, i samma ordning varje gång."""
    root = Path(directory)
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        raise ClockPackError(f"{MANIFEST} saknas i {root}.")
    manifest_bytes = manifest_path.read_bytes()
    try:
        layers = json.loads(manifest_bytes.decode("utf-8-sig")).get("layers") or {}
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as error:
        raise ClockPackError(f"clock.json går inte att läsa: {error}.") from error
    names = [MANIFEST, *sorted({file for file in layers.values() if isinstance(file, str)})]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            path = root / name
            if "/" in name or "\\" in name or not path.is_file():
                raise ClockPackError(f"{name} finns inte i {root}.")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    data = buffer.getvalue()
    read_pack(data)
    return data


def example_directory():
    """Exempelpaketet som följer med servern: en neutral stationsklocka."""
    return files("tmbox_gateway").joinpath("clock_pack_example")


def example_pack() -> bytes:
    source = example_directory()
    with _materialized(source) as directory:
        return build(directory)


class _materialized:
    """Exemplets mapp på disk, också när servern körs ur ett paket."""

    def __init__(self, source):
        self.source = source
        self._temporary = None

    def __enter__(self) -> Path:
        if isinstance(self.source, Path):
            return self.source
        import tempfile
        self._temporary = tempfile.TemporaryDirectory()
        target = Path(self._temporary.name)
        for item in self.source.iterdir():
            if item.is_file():
                (target / item.name).write_bytes(item.read_bytes())
        return target

    def __exit__(self, *_: Any) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()


def describe(pack: ClockPack) -> str:
    motion = pack.motion
    second = f"sweep {motion['sweep_seconds']:g} s" if motion["second"] == "sweep" else motion["second"]
    minute = motion["minute"] + (" med studs" if motion["minute_bounce"] else "")
    return (f"{pack.name}{' ' + pack.version if pack.version else ''} (id {pack.id})\n"
            f"  lager:  {', '.join(pack.layers)}\n"
            f"  visare: tim {motion['hour']}, minut {minute}, sekund {second}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tmbox_gateway.clock_pack",
                                     description="Gör och kontrollera klockpaket för TrainMeet-servern (docs/clock-packs.md).")
    commands = parser.add_subparsers(dest="command", required=True)
    example = commands.add_parser("example", help="skriv exempelpaketets filer till en ny mapp att börja från")
    example.add_argument("directory")
    check = commands.add_parser("check", help="kontrollera en mapp eller en .tmclock-fil med samma regler som servern")
    check.add_argument("path")
    pack = commands.add_parser("build", help="packa en mapp till en .tmclock-fil")
    pack.add_argument("directory")
    pack.add_argument("-o", "--output", help="filen att skriva (förval: mappens namn.tmclock)")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "example":
            target = Path(arguments.directory)
            if target.exists() and any(target.iterdir()):
                raise ClockPackError(f"{target} finns redan och är inte tom.")
            target.mkdir(parents=True, exist_ok=True)
            with _materialized(example_directory()) as source:
                for item in sorted(source.iterdir()):
                    if item.is_file():
                        shutil.copyfile(item, target / item.name)
            print(f"Exemplet ligger i {target}. Ändra bilderna och clock.json, och kör sedan check och build.")
            return 0
        if arguments.command == "check":
            path = Path(arguments.path)
            result = read_pack(build(path) if path.is_dir() else path.read_bytes())
            print("OK: " + describe(result))
            return 0
        directory = Path(arguments.directory)
        output = Path(arguments.output) if arguments.output else directory.with_suffix(".tmclock")
        data = build(directory)
        output.write_bytes(data)
        print(f"Skrev {output} ({len(data) // 1024 + 1} kB): " + describe(read_pack(data)))
        return 0
    except (ClockPackError, OSError) as error:
        print(f"Fel: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
