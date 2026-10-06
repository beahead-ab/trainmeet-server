"""Tidtabellens kärna, kopierad från TrainMeet Cloud.

När en tidtabell ändras på plats ska servern bygga om tjänster och rutter
precis som Cloud gör. Annars skulle en ändring som Cloud godtar ge andra tåg
här. Därför kör servern Clouds egen kod, i en byte-identisk kopia under
src/tmbox_gateway/timetable_core/. Proven låser kopian och visar att den ger
exakt tjänsterna och rutterna i ett paket som Cloud faktiskt har byggt.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

from tmbox_gateway.timetable_core import build_services, dispatch_mode

CORE = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "timetable_core"
PACKAGE = Path(__file__).resolve().parent / "cloud_runtime_package.json"


def core_digest(directory: Path) -> str:
    """Filnamn och innehåll för kärnans .py-filer, i namnordning. Samma som i Cloud."""
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


class SharedCodeTests(unittest.TestCase):
    """Kopian är Clouds, byte för byte.

    Cloud äger kärnan. När den ändras där: kör `node tools/sync-timetable-core.mjs`
    här, med trainmeet-cloud bredvid, och flytta digesten i båda repona. Ändra
    aldrig filerna bara här.
    """

    #: Samma värde som CORE_DIGEST i trainmeet-cloud/tests/test_timetable_core.py.
    CORE_DIGEST = "814bf1b8cd799bbfad1fb0568f29d54170be482cdc1f3c568a5358f83710b9ad"

    def test_the_copy_is_clouds(self):
        self.assertEqual(self.CORE_DIGEST, core_digest(CORE),
                         "timetable_core skiljer sig från Clouds - kör tools/sync-timetable-core.mjs")

    def test_the_core_needs_nothing_but_the_standard_library(self):
        for path in sorted(CORE.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertIn(name.split(".")[0], sys.stdlib_module_names)


class CloudPackageTests(unittest.TestCase):
    """Guldfilen är byggd av Clouds build_runtime_package; se test_cloud_package_contract."""

    def test_the_package_rows_rebuild_exactly_the_package_services(self):
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        names = {station["id"]: station["name"] for station in package["stations"]}
        services, routes, service_ids = build_services(copy.deepcopy(package["trains"]), names)
        self.assertEqual(package["services"], services)
        self.assertEqual(package["routes"], routes)
        self.assertEqual({row["id"]: row["service_id"] for row in package["trains"]}, service_ids)

    def test_the_cores_mode_words_are_the_servers(self):
        from tmbox_gateway.models import dispatch_mode as server_mode
        for word in ("clearance", "direct", "automatic", None, ""):
            with self.subTest(word=word):
                self.assertEqual(server_mode(word).value, dispatch_mode(word))


if __name__ == "__main__":
    unittest.main()
