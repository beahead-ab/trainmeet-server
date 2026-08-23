"""Server måste kunna ta emot det Cloud faktiskt bygger.

Serverns egna tester matar installeraren med paket den själv snickrar ihop.
De visar att installeraren fungerar, men inte att den fungerar mot Cloud - och
det är där två repon glider isär utan att någon svit blir röd.

Guldfilen bredvid är byggd av Clouds riktiga `build_runtime_package`, ur två
importerade stationsblad. Om Cloud ändrar paketets form faller det här testet,
och det är hela poängen.

Så här görs den om när Cloud ändras med flit - kör i trainmeet-cloud:

    python3 - <<'SLUT'
    import json, sys, tempfile
    from pathlib import Path
    sys.path.insert(0, "tests")
    from unittest.mock import patch
    from cloud import openai_import
    from cloud.domain import (apply_extraction, build_linear_topology,
                              build_runtime_package, validate_draft)
    from cloud.store import CloudStore
    from test_pdf_import import EXTRACTIONS
    from fixtures import minimal_pdf
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); store = CloudStore(root / "state")
        store.setup("casper", "ett-langt-losenord", "TrainMeet", "c@example.se")
        user = store.user_by_email("c@example.se")
        meet = store.create_meet("Kontraktsprovet", store.organization_of(user["id"]))
        draft = store.meet(meet["id"])
        for index, station in enumerate(EXTRACTIONS):
            path = root / f"{station}.pdf"; path.write_bytes(minimal_pdf(station))
            with patch.object(openai_import, "_request_extraction",
                              return_value=EXTRACTIONS[station]):
                extraction = openai_import.analyze_file(
                    path, path.name, "application/pdf", "k", "m")
            apply_extraction(draft, extraction, f"f{index}")
        build_linear_topology(draft)
        assert validate_draft(draft)["valid"]
        package = build_runtime_package(draft, publication_id="publication-kontraktsprov-1")
        package["published_at"] = "2026-09-05T08:00:00Z"
        package["meet"]["id"] = "meet-kontraktsprov"
        store.close()
    Path("../trainmeet-server/tests/cloud_runtime_package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\\n",
        encoding="utf-8")
    SLUT
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tmbox_gateway.runtime import SQLiteRuntimeStore

PACKAGE = Path(__file__).resolve().parent / "cloud_runtime_package.json"


class CloudPackageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = SQLiteRuntimeStore(Path(self._dir.name) / "runtime.db")
        self.addCleanup(self.store.close)
        self.package = json.loads(PACKAGE.read_text(encoding="utf-8"))

    def test_the_package_cloud_builds_can_be_parsed_and_installed(self) -> None:
        publication = self.store.install(self.package, activate=True)

        self.assertEqual("publication-kontraktsprov-1", publication.publication_id)
        self.assertEqual(3, self.package["schema_version"])

    def test_the_stations_and_trains_survive_the_crossing(self) -> None:
        self.store.install(self.package, activate=True)

        active = self.store.active()
        self.assertIsNotNone(active)
        names = {station["name"] for station in active.payload["stations"]}
        self.assertEqual({"Charlottendal", "Västerhamn"}, names)
        numbers = {service["train_number"] for service in active.payload["services"]}
        self.assertEqual({"421", "428"}, numbers)

    def test_every_service_keeps_both_of_its_stops(self) -> None:
        """Det som gick sönder när två stationsblad var oense om dagarna: ett
        tåg blev två turer med ett stopp var, och avgången fick aldrig någon
        ankomst i andra änden."""

        self.store.install(self.package, activate=True)

        for service in self.store.active().payload["services"]:
            with self.subTest(train=service["train_number"]):
                self.assertEqual(2, len(service["stops"]))

    def test_a_cloud_package_does_not_activate_itself(self) -> None:
        """T4: en ny revision från Cloud får aldrig slå igenom tyst."""

        self.store.install(self.package, activate=False)

        self.assertIsNone(self.store.active())

    def test_a_staged_package_can_be_activated_on_purpose(self) -> None:
        self.store.install(self.package, activate=False)

        activated = self.store.activate("publication-kontraktsprov-1")

        self.assertEqual("publication-kontraktsprov-1", activated.publication_id)
        self.assertIsNotNone(self.store.active())


if __name__ == "__main__":
    unittest.main()
