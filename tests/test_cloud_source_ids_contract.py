"""Clouds fält för manuella tåg och planens id, som servern läser dem (Cloud #100 B4, #222).

Paketet nedan har samma form som Clouds (trainmeet-cloud
`cloud/manual_trains.py`): ett manuellt tåg har `manual: true`,
`manual_reason`, `manual_reasons` och `manual_codes`, och planens id står på
stationer, sträckor och tjänster. Servern ska läsa skälen utan att visa något
två gånger, strunta i koderna, och Tellurian-adaptern ska matcha tåget på
`source_train_id`. Fälten som servern inte använder ändrar ingenting.
"""
from __future__ import annotations

import copy
import unittest

from tmbox_gateway.integrations.tellurian import Section, match_trains
from tmbox_gateway.train_identity import manual_trains, shared_labels

PACKAGE = {
    "stations": [
        {"id": "station-charlottendal", "code": "CDA", "name": "Charlottendal", "source_station_id": "10"},
        {"id": "station-vagnsta", "code": "VST", "name": "Vagnsta", "source_station_id": "11"},
        {"id": "station-salsborg", "code": "SBG", "name": "Salsborg", "source_station_id": "12"},
    ],
    "connections": [
        {"id": "c1", "station_a_id": "station-charlottendal", "station_b_id": "station-vagnsta",
         "source_connection_id": "1", "source_dispatch_stretch_id": "1"},
        {"id": "c2", "station_a_id": "station-salsborg", "station_b_id": "station-vagnsta",
         "source_connection_id": "2"},
    ],
    "trains": [
        {"id": "r1", "service_id": "service-91-dagl", "station_id": "station-charlottendal", "train_number": "91",
         "departure_time": "06:20", "source_train_id": "1"},
        {"id": "r2", "service_id": "service-91-dagl", "station_id": "station-vagnsta", "train_number": "91",
         "arrival_time": "06:31", "departure_time": "06:32", "source_train_id": "1"},
        {"id": "r3", "service_id": "service-93-dagl", "station_id": "station-charlottendal", "train_number": "93",
         "departure_time": "23:40", "source_train_id": "3"},
        {"id": "r4", "service_id": "service-93-dagl", "station_id": "station-salsborg", "train_number": "93",
         "arrival_time": "00:05", "source_train_id": "3"},
    ],
    "services": [
        {"id": "service-91-dagl", "train_number": "91", "days": "Dagl", "source_train_id": "1", "stops": [
            {"station_id": "station-charlottendal", "departure_time": "06:20", "stop_order": 0, "service_day_offset": 0},
            {"station_id": "station-vagnsta", "arrival_time": "06:31", "departure_time": "06:32", "stop_order": 1, "service_day_offset": 0}]},
        {"id": "service-93-dagl", "train_number": "93", "days": "Dagl", "source_train_id": "3",
         "manual": True, "manual_reason": "Sträckan Charlottendal–Salsborg finns inte på banan",
         "manual_reasons": ["Sträckan Charlottendal–Salsborg finns inte på banan"], "manual_codes": ["unknown_connection"],
         "stops": [
            {"station_id": "station-charlottendal", "departure_time": "23:40", "stop_order": 0, "service_day_offset": 0},
            {"station_id": "station-salsborg", "arrival_time": "00:05", "stop_order": 1, "service_day_offset": 1}]},
    ],
}


class CloudSourceIdContractTests(unittest.TestCase):
    def test_clouds_reasons_and_the_servers_own_are_one_list(self):
        self.assertEqual({"service-93-dagl": ["Sträckan Charlottendal–Salsborg finns inte på banan"]}, manual_trains(PACKAGE))

    def test_a_cloud_reason_the_server_cannot_see_is_kept(self):
        package = copy.deepcopy(PACKAGE)
        package["services"][0].update(manual=True, manual_reason="Tid saknas vid Vagnsta",
                                      manual_reasons=["Tid saknas vid Vagnsta"], manual_codes=["missing_time"])
        self.assertEqual(["Tid saknas vid Vagnsta"], manual_trains(package)["service-91-dagl"])

    def test_the_ids_change_nothing_else(self):
        bare = copy.deepcopy(PACKAGE)
        for key in ("stations", "connections", "trains", "services"):
            for item in bare[key]:
                for field in [name for name in item if name.startswith("source_") or name == "manual_codes"]:
                    item.pop(field)
        self.assertEqual(manual_trains(bare), manual_trains(PACKAGE))
        self.assertEqual(shared_labels(bare["services"]), shared_labels(PACKAGE["services"]))

    def test_tellurian_matches_the_train_on_the_plans_id(self):
        sections = {"s1": Section(id="s1", train="SJ RC 9001", train_id="1", train_state="planned", state="planned",
                                  current=0, total=1, from_sig="CDA", to_sig="VST")}
        result = match_trains(PACKAGE["services"][:1], PACKAGE["stations"], sections)
        self.assertEqual([("91", "SJ RC 9001")], [(m["number"], m["tellurian"]) for m in result["matched"]])


if __name__ == "__main__":
    unittest.main()
