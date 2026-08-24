"""Driften överlever att Cloud publicerar om.

Cloud äger tidtabellen och skriver över den. Men vilket spår ett tåg faktiskt
fick, om det ankommit, och vad tågklareraren antecknat är inte planen — det är
vad som hände. Det försvann tidigare vid varje ny publicering, eftersom
driftläget nycklas mot publikationen.

Rörelserna paras ihop på tågnummer, station och besöksordning. Inte på id:
Cloud myntar nya id vid varje omimport av ett stationsblad, så ett id säger
ingenting om det är samma rörelse.
"""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from runtime_fixture import runtime_package_v3
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublication


def _publication(package: dict) -> RuntimePublication:
    return RuntimePublication.parse(package)


class OperationalStateSurvivesRepublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = SQLiteOperationsStore(Path(self._dir.name) / "ops.db")
        self.addCleanup(self.store.close)

        self.first = runtime_package_v3(publication_id="publication-ett")
        self.movement = self.first["trains"][0]
        self.day = self.first["meet"]["active_day"]

    def _republish(self, *, new_ids: bool = True, publication_id: str = "publication-tva") -> dict:
        """Samma träff, ny publicering. Med nya id, som en omimport ger."""

        package = deepcopy(self.first)
        package["publication_id"] = publication_id
        if new_ids:
            for index, row in enumerate(package["trains"]):
                row["id"] = f"omimporterad-{index}"
        return package

    def _record(self, publication: dict, movement_id: str, **kwargs) -> None:
        defaults = dict(
            arrival="arrived", departure="none", actual_track="3",
            updated_by="tkl", shift_id=None, event_type="test",
        )
        defaults.update(kwargs)
        self.store.update_tkl_movement(
            publication["publication_id"], self.day,
            str(self.movement["station_id"]), movement_id, **defaults,
        )

    def _state(self, publication: dict, movement_id: str) -> dict | None:
        snapshot = self.store.tkl_station_state(
            publication["publication_id"], self.day, str(self.movement["station_id"])
        )
        return snapshot["movements"].get(movement_id)

    def test_the_actual_track_survives_a_republish(self) -> None:
        """Det som gick förlorat: spåret tågklareraren faktiskt valde."""

        self.store.ensure_publication(_publication(self.first))
        self._record(self.first, str(self.movement["id"]), actual_track="3")

        second = self._republish()
        self.store.ensure_publication(_publication(second))

        carried = self._state(second, "omimporterad-0")
        self.assertIsNotNone(carried, "rörelsen hittades inte i den nya publikationen")
        self.assertEqual("3", carried["actualTrack"])
        self.assertEqual("arrived", carried["arrival"])

    def test_the_operator_note_survives_a_republish(self) -> None:
        self.store.ensure_publication(_publication(self.first))
        self._record(self.first, str(self.movement["id"]), operator_note="Väntar på lokförare")

        second = self._republish()
        self.store.ensure_publication(_publication(second))

        self.assertEqual("Väntar på lokförare", self._state(second, "omimporterad-0")["operatorNote"])

    def test_a_movement_that_left_the_plan_loses_its_state(self) -> None:
        """Rätt beteende: rörelsen finns inte längre."""

        self.store.ensure_publication(_publication(self.first))
        self._record(self.first, str(self.movement["id"]))

        second = self._republish()
        second["trains"] = [row for row in second["trains"] if row["id"] != "omimporterad-0"]
        self.store.ensure_publication(_publication(second))

        self.assertIsNone(self._state(second, "omimporterad-0"))

    def test_a_new_movement_starts_clean(self) -> None:
        self.store.ensure_publication(_publication(self.first))
        self._record(self.first, str(self.movement["id"]), actual_track="3")

        second = self._republish()
        self.store.ensure_publication(_publication(second))

        others = [row["id"] for row in second["trains"] if row["id"] != "omimporterad-0"]
        for movement_id in others:
            with self.subTest(movement=movement_id):
                self.assertIsNone(self._state(second, movement_id))

    def test_a_train_that_turns_back_keeps_its_two_visits_apart(self) -> None:
        """Fallet nyckeln har besöksordningen för.

        Traditionellt går jämna tågnummer åt ett håll och udda åt det andra,
        så ett tåg som vänder och kommer tillbaka till samma station samma dag
        är ovanligt. Men en nyckel som stämmer nästan alltid sviker
        obegripligt, så ordningen är med.

        Publikationerna listar raderna i olika ordning — det är så en omimport
        beter sig. Paras besöken på radordning i stället för på tid byter
        spåren plats mellan förmiddag och eftermiddag.
        """

        station = str(self.movement["station_id"])
        first = deepcopy(self.first)
        first["trains"] = [
            {**self.movement, "id": "eftermiddag", "sort_time": "15:00", "departure_time": "15:00"},
            {**self.movement, "id": "morgon", "sort_time": "09:00", "departure_time": "09:00"},
        ]
        self.store.ensure_publication(_publication(first))
        for movement_id, track in (("morgon", "1"), ("eftermiddag", "2")):
            self.store.update_tkl_movement(
                first["publication_id"], self.day, station, movement_id,
                arrival="arrived", departure="none", actual_track=track,
                updated_by="tkl", shift_id=None, event_type="test",
            )

        second = deepcopy(first)
        second["publication_id"] = "publication-tva"
        second["trains"] = [
            {**self.movement, "id": "ny-morgon", "sort_time": "09:00", "departure_time": "09:00"},
            {**self.movement, "id": "ny-eftermiddag", "sort_time": "15:00", "departure_time": "15:00"},
        ]
        self.store.ensure_publication(_publication(second))

        states = self.store.tkl_station_state(second["publication_id"], self.day, station)["movements"]
        self.assertEqual("1", states["ny-morgon"]["actualTrack"], "morgonens spår")
        self.assertEqual("2", states["ny-eftermiddag"]["actualTrack"], "eftermiddagens spår")

    def test_the_first_publication_starts_from_nothing(self) -> None:
        """Ingen tidigare publicering att bära över från."""

        self.store.ensure_publication(_publication(self.first))

        self.assertIsNone(self._state(self.first, str(self.movement["id"])))


class OperatorNoteTests(unittest.TestCase):
    """Anteckningen hör till driften och ska inte kunna raderas av misstag."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = SQLiteOperationsStore(Path(self._dir.name) / "ops.db")
        self.addCleanup(self.store.close)
        self.package = runtime_package_v3(publication_id="publication-ett")
        self.store.ensure_publication(_publication(self.package))
        self.movement = self.package["trains"][0]
        self.day = self.package["meet"]["active_day"]

    def _write(self, **kwargs) -> dict:
        defaults = dict(
            arrival="none", departure="none", actual_track=None,
            updated_by="tkl", shift_id=None, event_type="test",
        )
        defaults.update(kwargs)
        return self.store.update_tkl_movement(
            self.package["publication_id"], self.day,
            str(self.movement["station_id"]), str(self.movement["id"]), **defaults,
        )

    def test_a_note_is_stored_and_returned(self) -> None:
        result = self._write(operator_note="Kort tåg, stannar vid stoppbocken")

        self.assertEqual("Kort tåg, stannar vid stoppbocken", result["operatorNote"])

    def test_a_later_write_without_a_note_leaves_it_alone(self) -> None:
        """Det farliga fallet: en TMBox byter spår och råkar radera texten."""

        self._write(operator_note="Väntar på lokförare")
        self._write(actual_track="2")

        snapshot = self.store.tkl_station_state(
            self.package["publication_id"], self.day, str(self.movement["station_id"])
        )
        kept = snapshot["movements"][str(self.movement["id"])]
        self.assertEqual("Väntar på lokförare", kept["operatorNote"])
        self.assertEqual("2", kept["actualTrack"])

    def test_a_note_of_only_spaces_counts_as_empty(self) -> None:
        """Annars ligger en osynlig anteckning kvar och ser ut som text."""

        self._write(operator_note="Väntar på lokförare")
        self._write(operator_note="   ")

        snapshot = self.store.tkl_station_state(
            self.package["publication_id"], self.day, str(self.movement["station_id"])
        )
        self.assertIsNone(snapshot["movements"][str(self.movement["id"])]["operatorNote"])

    def test_an_empty_note_clears_it(self) -> None:
        """Att ta bort en anteckning ska gå, och skilja sig från att låta bli."""

        self._write(operator_note="Väntar på lokförare")
        self._write(operator_note="")

        snapshot = self.store.tkl_station_state(
            self.package["publication_id"], self.day, str(self.movement["station_id"])
        )
        self.assertIsNone(snapshot["movements"][str(self.movement["id"])]["operatorNote"])


if __name__ == "__main__":
    unittest.main()
