"""Route identification cannot invent a destination or declare a departure."""
from copy import deepcopy
import unittest

from runtime_fixture import runtime_package_v3
from tmbox_gateway.train_routes import resolve_departure, describe_departure, RouteResolutionError


class TrainRouteTests(unittest.TestCase):
    def setUp(self):
        self.package = runtime_package_v3()

    def resolve(self, movement="movement-101-a", station="station-a", day="Lör"):
        return resolve_departure(self.package, day, station, movement)

    def refused(self, reason, **kwargs):
        with self.assertRaises(RouteResolutionError) as error:
            self.resolve(**kwargs)
        self.assertEqual(error.exception.reason, reason)

    def test_exact_leg_contains_both_movements_and_service_visit_identity(self):
        before = deepcopy(self.package)
        result = self.resolve()
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["from_movement_id"], "movement-101-a")
        self.assertEqual(result["to_movement_id"], "movement-101-b")
        self.assertEqual(result["to_station_code"], "LEK")
        self.assertEqual(result["connection_id"], "connection-a-b")
        self.assertEqual(result["service_id"], "service-101-Dagl")
        self.assertEqual((result["from_stop_order"], result["to_stop_order"]), (0, 1))
        self.assertEqual(result["active_day"], "Lör")
        self.assertEqual(result["publication_id"], before["publication_id"])
        self.assertEqual(self.package, before, "reading a route must not mutate the publication")
        self.assertNotIn("allowed_actions", result, "planned route is not a clearance")

    def test_terminal_does_not_propose_return_to_previous_station(self):
        result = self.resolve("movement-101-b", "station-b")
        self.assertEqual(result["status"], "terminal")
        self.assertNotIn("connection_id", result)

    def test_unknown_and_other_station_are_rejected(self):
        self.refused("unknown_or_duplicate_movement", movement="unknown")
        self.refused("movement_not_at_station", station="station-b")

    def test_no_wrong_day_and_no_service_day_fallback(self):
        self.refused("movement_not_on_active_day", movement="movement-202-a")
        self.package["services"][0]["days"] = "Sön"
        self.refused("service_movement_mismatch")

    def test_missing_service_is_not_inferred_from_matching_train_number(self):
        self.package["trains"][0]["service_id"] = "no-such-service"
        self.refused("missing_or_duplicate_service")

    def test_train_number_identity_preserves_leading_zeroes(self):
        for row in [self.package["services"][0], *self.package["trains"][:2]]:
            row["train_number"] = "00101"
        self.assertEqual(self.resolve()["train_number"], "00101")
        self.package["trains"][0]["train_number"] = "101"
        self.refused("service_movement_mismatch")

    def test_stop_order_not_array_order_decides_next_station(self):
        self.package["services"][0]["stops"].reverse()
        self.assertEqual(self.resolve()["to_station_id"], "station-b")

    def test_duplicate_or_invalid_order_cannot_choose_arbitrarily(self):
        self.package["services"][0]["stops"][1]["stop_order"] = 0
        self.refused("duplicate_stop_order")
        self.package["services"][0]["stops"][1]["stop_order"] = 1.5
        self.refused("invalid_stop_order")

    def test_midnight_uses_order_instead_of_clock_proximity(self):
        for row in [self.package["services"][0]["stops"][0], self.package["trains"][0]]:
            row["departure_time"] = "23:55"
        for row in [self.package["services"][0]["stops"][1], self.package["trains"][1]]:
            row["arrival_time"] = "00:15"
        self.package["services"][0]["stops"][1]["service_day_offset"] = 1
        self.package["services"][0]["stops"][1]["service_minute"] = 1455
        self.assertEqual(self.resolve()["to_station_id"], "station-b")

    def test_inconsistent_row_time_is_not_guessed(self):
        self.package["trains"][0]["departure_time"] = "09:21"
        self.refused("movement_visit_mismatch")

    def test_repeated_visit_requires_exact_identity(self):
        stops = self.package["services"][0]["stops"]
        stops.append({**stops[0], "stop_order": 2})
        self.refused("ambiguous_visit")
        self.package["trains"][0]["stop_order"] = 0
        self.assertEqual(self.resolve()["to_stop_order"], 1)

    def test_repeated_visit_can_be_identified_by_distinct_times(self):
        stops = self.package["services"][0]["stops"]
        stops.append({**stops[0], "stop_order": 2, "arrival_time": "10:00", "departure_time": None})
        self.assertEqual(self.resolve()["from_stop_order"], 0)

    def test_no_skip_over_an_unconnected_or_unstaffed_station(self):
        stops = self.package["services"][0]["stops"]
        self.package["stations"].append({"id": "intermediate", "code": "MID", "name": "Intermediate", "is_autonomous": True})
        stops[1]["stop_order"] = 2
        stops.insert(1, {**stops[1], "stop_order": 1, "station_id": "intermediate"})
        self.refused("no_published_connection")

    def test_parallel_connections_require_explicit_route_support(self):
        self.package["connections"].append({**self.package["connections"][0], "id": "second-way"})
        self.refused("ambiguous_connection")

    def test_missing_or_duplicate_receiving_row_is_rejected(self):
        self.package["trains"].append({**self.package["trains"][1], "id": "duplicate-arrival"})
        self.refused("ambiguous_receiving_movement")
        self.package["trains"] = [self.package["trains"][0]]
        self.refused("missing_receiving_movement")

    def test_other_run_with_same_number_does_not_become_receiver(self):
        self.package["trains"][1]["service_id"] = "other-run"
        self.refused("missing_receiving_movement")

    def test_other_runs_keep_their_own_routes(self):
        other = deepcopy(self.package["services"][0])
        other["id"] = "second-run-101"
        self.package["services"].append(other)
        self.package["trains"].extend([{**row, "id": row["id"] + "-later", "service_id": other["id"]}
                                      for row in self.package["trains"][:2]])
        self.assertEqual(self.resolve()["to_movement_id"], "movement-101-b")
        self.assertEqual(self.resolve("movement-101-a-later")["to_movement_id"], "movement-101-b-later")

    def test_published_direct_mode_is_read_from_server_configuration(self):
        self.package["connections"][0]["dispatch_mode_override"] = "direct"
        self.assertEqual(self.resolve()["dispatch_mode"], "direct")
        self.assertNotIn("departed", self.resolve().values())

    def test_failed_route_metadata_does_not_hide_the_train(self):
        self.package["connections"] = []
        result = describe_departure(self.package, "Lör", "station-a", "movement-101-a")
        self.assertEqual(result, {"status": "unresolved", "reason": "no_published_connection"})

    def test_last_visit_with_departure_is_inconsistent_not_a_terminal(self):
        self.package["trains"][1]["departure_time"] = "09:40"
        self.package["services"][0]["stops"][1]["departure_time"] = "09:40"
        self.refused("departure_without_next_visit", movement="movement-101-b", station="station-b")
