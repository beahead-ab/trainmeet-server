"""Resterna av Cloud #100 på Server.

- Tåg som delar nummer (DSB IC 116 och SJ X 116) hålls isär: train_live
  räknar varje tåg för sig, och TMBox, TKL och skärmarna visar förkortningen.
- TMBox och TKL får trafikdagen, planens veckodag.
- B4/B5: ett tåg som inte kan köras körs manuellt. Det automatiseras inte,
  listas med skälen och stoppar varken klockan, publiceringen eller andra tåg.
  En publicering som var aktiv före uppdateringen kör som förut.
- B6: en Cloud-version som bara rättar tidtabellen läggs på de tåg den rör
  mitt under spelet, utan att nollställa något som pågår.
"""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from test_pending_revisions import CloudDeliveryFixture, cloud_package
from tmbox_gateway.legs import plan_legs
from tmbox_gateway.runtime import RuntimePublication
from tmbox_gateway.train_identity import manual_trains, shared_labels
from tmbox_gateway.train_live import train_live


def at(text: str) -> int:
    hours, minutes = (int(part) for part in text.split(":"))
    return hours * 3600 + minutes * 60


def two_116():
    """DSB IC 116 Skovborg 06:10 → Munkerød 06:26 och SJ X 116 Munkerød 15:40 → Nättelund 16:18."""
    services = [
        {"id": "service-116-dagl-dsb-ic", "train_number": "116", "service_key": "DSB IC", "stops": [
            {"station_id": "sbg", "departure_time": "06:10", "stop_order": 0},
            {"station_id": "mkd", "arrival_time": "06:26", "stop_order": 1}]},
        {"id": "service-116-dagl-sj-x", "train_number": "116", "service_key": "SJ X", "stops": [
            {"station_id": "mkd", "departure_time": "15:40", "stop_order": 0},
            {"station_id": "nlu", "arrival_time": "16:18", "stop_order": 1}]},
    ]
    rows = [
        {"id": "ic-sbg", "service_id": "service-116-dagl-dsb-ic", "station_id": "sbg", "departure_time": "06:10"},
        {"id": "ic-mkd", "service_id": "service-116-dagl-dsb-ic", "station_id": "mkd", "arrival_time": "06:26"},
        {"id": "x-mkd", "service_id": "service-116-dagl-sj-x", "station_id": "mkd", "departure_time": "15:40"},
        {"id": "x-nlu", "service_id": "service-116-dagl-sj-x", "station_id": "nlu", "arrival_time": "16:18"},
    ]
    return services, rows


class SharedNumberTests(unittest.TestCase):
    def test_train_live_counts_each_train_that_shares_a_number(self):
        services, rows = two_116()
        live = {"ic-sbg": {"departure": "departed", "departed_seconds": at("06:10")},
                "ic-mkd": {"arrival": "arrived", "arrived_seconds": at("06:31")},
                "x-mkd": {"departure": "departed", "departed_seconds": at("15:52")}}
        trains = train_live(services, rows, live, at("16:00"), {"116"})
        ic, x = trains["service-116-dagl-dsb-ic"], trains["service-116-dagl-sj-x"]
        self.assertEqual(("arrived", 5), (ic["state"], ic["delay_minutes"]), "the morning train came in five minutes late")
        self.assertEqual(("on_line", 12), (x["state"], x["delay_minutes"]), "the afternoon train is out, twelve late")
        self.assertIs(trains["116"], x, "the number means the train under way, not the longest one")

    def test_before_either_runs_the_number_means_the_next_one(self):
        services, rows = two_116()
        trains = train_live(services, rows, {}, at("05:00"))
        self.assertEqual("service-116-dagl-dsb-ic", trains["116"]["service_id"])
        self.assertEqual("not_departed", trains["service-116-dagl-sj-x"]["state"])

    def test_a_number_only_one_train_has_is_unchanged(self):
        services, rows = two_116()
        trains = train_live(services[:1], rows[:2], {}, at("05:00"), {"116"})
        self.assertEqual(trains["116"], trains["service-116-dagl-dsb-ic"])
        self.assertEqual("on_line", trains["116"]["state"], "a channel still says on the line for a train of its own")

    def test_labels_are_short_where_the_box_has_little_room(self):
        services, _ = two_116()
        labels = shared_labels(services)
        self.assertEqual({"service-116-dagl-dsb-ic": ("DSB IC 116", "IC 116"), "service-116-dagl-sj-x": ("SJ X 116", "X 116")},
                         {key: (value["label"], value["short_label"]) for key, value in labels.items()})
        pt = [{"id": "a", "train_number": "91", "service_key": "SJ Pt"}, {"id": "b", "train_number": "91", "service_key": "DSB Pt"}]
        self.assertEqual({"a": "SJ 91", "b": "DSB 91"}, {key: value["short_label"] for key, value in shared_labels(pt).items()},
                         "the same category: the company tells them apart")
        self.assertEqual({}, shared_labels(services[:1]), "a number nobody shares gets no label")


def broken(package, *, untimed=False, flagged=False):
    """Paketet med ett tredje tåg, 303, som inte kan köras som planen säger."""
    package = copy.deepcopy(package)
    package["trains"].append({**copy.deepcopy(package["trains"][0]), "id": "movement-303-a", "train_number": "303",
                              "service_id": "service-303", "departure_time": None if untimed else "11:00",
                              "arrival_time": None, "sort_time": "?" if untimed else "11:00", "track_id": None})
    service = {"id": "service-303", "train_number": "303", "days": "Dagl", "train_type": "person", "stops": [] if untimed else [
        {"station_id": "station-a", "stop_order": 0, "arrival_time": None, "departure_time": "11:00", "service_day_offset": 0, "service_minute": 660}]}
    if flagged:
        service.update(manual=True, manual_reason="Okänd del av banan vid Fullerup")
    package["services"].append(service)
    return package


class ManualTrainTests(CloudDeliveryFixture):
    def test_detection_names_the_reason(self):
        package = cloud_package()
        self.assertEqual({}, manual_trains(package), "a sound plan has no manual trains")
        self.assertEqual({"service-303": ["Tid saknas vid Charlottendahl"]}, manual_trains(broken(package, untimed=True)))
        self.assertEqual({"service-303": ["Okänd del av banan vid Fullerup"]}, manual_trains(broken(package, flagged=True)))
        unknown = copy.deepcopy(package)
        unknown["connections"] = []
        self.assertIn("Sträckan Charlottendahl–Lekeberg finns inte på banan", manual_trains(unknown)["service-101-Dagl"])
        backwards = copy.deepcopy(package)
        backwards["services"][0]["stops"][1]["arrival_time"] = "09:10"
        self.assertEqual({"service-101-Dagl": ["Negativ gångtid Charlottendahl–Lekeberg"]}, manual_trains(backwards))

    def test_a_publication_with_a_broken_train_is_published_and_only_that_train_is_left_to_people(self):
        self.offered = broken(cloud_package(), untimed=True)
        self.application.control_clock(self.client, {"action": "start", "time": "09:00:00", "speed": 1})
        result = self.application.auto_sync_cloud_runtime()
        self.assertTrue(result["activated"], result)
        self.assertIn("Publicerad med 1 kända avvikelser", result["message"])
        self.assertEqual(1, result["message_values"]["count"])
        publication = self.runtime.active()
        self.assertEqual({"service-303": ["Tid saknas vid Charlottendahl"]}, self.runtime.manual_trains(publication))
        legs, errors = plan_legs(publication, self.runtime.active_day(), self.runtime.manual_trains(publication))
        self.assertEqual({"101"}, {leg["train_number"] for leg in legs.values()}, "every other train is automated")
        self.assertIn("Tåg 303 körs manuellt: Tid saknas vid Charlottendahl.", errors)
        snapshot = self.application.display_snapshot()
        self.assertEqual([{"service_id": "service-303", "train_number": "303", "days": "Dagl", "label": "303",
                           "reasons": ["Tid saknas vid Charlottendahl"]}], snapshot["manual_trains"])
        row = next(row for row in snapshot["trains"] if row["id"] == "movement-303-a")
        self.assertEqual((True, ["Tid saknas vid Charlottendahl"]), (row["manual"], row["manual_reasons"]))
        self.assertTrue(self.operations.clock_status()["running"], "the clock never stops for it")
        context = self.application.tkl_context(self.client, "station-a")
        self.assertEqual({"movement-303-a": ["Tid saknas vid Charlottendahl"]}, context["manual_movements"])

    def test_a_meet_running_before_the_update_keeps_its_behaviour_until_it_is_published_again(self):
        publication = self.runtime.install(broken(cloud_package("cloud-before"), untimed=True))
        # Som en träff som aktiverades av en äldre server: inget märke.
        self.runtime._connection.execute("DELETE FROM runtime_settings WHERE key = ?",
                                         (self.runtime.ISOLATION_KEY + publication.publication_id,))
        self.runtime._manual_cache = None
        self.assertEqual({}, self.runtime.manual_trains(self.runtime.active()))
        self.assertEqual([], self.application.display_snapshot()["manual_trains"])
        self.runtime.install(broken(cloud_package("cloud-after"), untimed=True))
        self.assertEqual(["service-303"], list(self.runtime.manual_trains(self.runtime.active())))

    def test_an_older_package_without_the_new_fields_works_as_before(self):
        snapshot = self.application.display_snapshot()
        self.assertEqual([], snapshot["manual_trains"])
        self.assertFalse(any("train_label" in row or "manual" in row for row in snapshot["trains"]))
        self.assertEqual(self.runtime.active().timetable(active_day=snapshot["active_day"])["services"], snapshot["services"])


class BoxAndTklTests(CloudDeliveryFixture):
    def shared(self, publication_id="cloud-shared"):
        package = cloud_package(publication_id)
        twin = copy.deepcopy(package["services"][0])
        twin.update(id="service-101-Dagl-sj-x", service_key="SJ X")
        package["services"][0]["service_key"] = "DSB IC"
        for stop in twin["stops"]:
            stop["departure_time"] = "15:40" if stop["departure_time"] else None
            stop["arrival_time"] = "15:55" if stop["arrival_time"] else None
        package["services"].append(twin)
        for row in [row for row in package["trains"] if row["train_number"] == "101"]:
            copy_row = {**copy.deepcopy(row), "id": row["id"] + "-x", "service_id": twin["id"]}
            copy_row["departure_time"] = "15:40" if row.get("departure_time") else None
            copy_row["arrival_time"] = "15:55" if row.get("arrival_time") else None
            copy_row["sort_time"] = copy_row["departure_time"] or copy_row["arrival_time"]
            package["trains"].append(copy_row)
        return self.runtime.install(package)

    def test_the_box_timetable_and_tkl_tell_the_two_trains_apart_and_say_the_day(self):
        self.shared()
        rows = [{"movement_id": "movement-101-a", "train_number": "101", "time": "09:20"},
                {"movement_id": "movement-101-a-x", "train_number": "101", "time": "15:40"}]
        table = self.application._timetable_with_deviations({"rows": rows})
        self.assertEqual(["IC 101", "X 101"], [row["train_label"] for row in table["rows"]])
        self.assertEqual(["DSB IC", "SJ X"], [row["service_key"] for row in table["rows"]])
        self.assertEqual(self.runtime.active_day(), table["traffic_day"]["day"])
        self.assertEqual(1, table["traffic_day"]["day_number"])
        context = self.application.tkl_context(self.client, "station-a")
        self.assertEqual({"movement-101-a": "IC 101", "movement-101-a-x": "X 101"}, context["train_labels"])
        self.assertEqual(self.runtime.active_day(), context["traffic_day"]["day"])
        snapshot = self.application.display_snapshot()
        self.assertEqual({"DSB IC 101", "SJ X 101"}, {row["train_label"] for row in snapshot["trains"] if row["train_number"] == "101"})


    def test_the_train_panel_asks_for_one_of_two_trains_that_share_a_number(self):
        # Drift frågar efter det valda tåget med dess nyckel; utan nyckel gäller
        # numret som förut, med båda tågens turer.
        self.shared()
        both = self.application.train_detail(self.client, "101")
        self.assertEqual(2, len({item["service_id"] for item in both["services"]}))
        self.assertNotIn("service_key", both)
        x = self.application.train_detail(self.client, "101", "SJ X")
        self.assertEqual(["service-101-Dagl-sj-x"], [item["service_id"] for item in x["services"]])
        self.assertEqual("SJ X", x["service_key"])
        self.assertEqual("15:40", next(stop for stop in x["services"][0]["stops"] if stop["departure_time"])["departure_time"])
        ic = self.application.train_detail(self.client, "101", "  DSB   IC ")
        self.assertEqual(1, len(ic["services"]))
        self.assertNotEqual(x["services"][0]["service_id"], ic["services"][0]["service_id"])
        from tmbox_gateway.http_server import HTTPAPIError
        with self.assertRaises(HTTPAPIError):
            self.application.train_detail(self.client, "101", "NSB L")

    def test_the_box_screen_shows_the_short_name_where_it_fits(self):
        from tmbox_gateway.engine import TrafficEngine
        from tmbox_gateway.terminal16 import Terminal16Lab
        publication = self.shared()
        payload = copy.deepcopy(publication.payload)
        payload["meet"]["active_day"] = self.runtime.active_day()
        lab = Terminal16Lab(TrafficEngine(publication.session_config()), payload, {"box": "station-a"})
        self.assertEqual({"movement-101-a": "IC 101", "movement-101-a-x": "X 101"},
                         {key: leg.get("train_label") for key, leg in lab.legs.items()})
        lab.terminals["box"].screen = "browse"
        lab.terminals["box"].selected = "movement-101-a-x"
        self.assertTrue(lab.frame("box")["lines"][0].startswith("X 101 AVG"), lab.frame("box")["lines"])
        self.assertEqual("101", lab._train_text(lab.legs["movement-101-a"], 3), "the number when the name does not fit")


class NightTrainTimetableTests(CloudDeliveryFixture):
    """Boxens tidtabell säger tjänstedygnet per rad (trainmeet-tmbox-ios #51):
    ett tåg 23:50 som kommer fram 00:05 ligger efter midnatt, inte före."""

    def night(self):
        package = cloud_package("cloud-night")
        rows = {row["id"]: row for row in package["trains"]}
        rows["movement-101-a"].update(departure_time="23:50", sort_time="23:50")
        rows["movement-101-b"].update(arrival_time="00:05", sort_time="00:05")
        first, second = package["services"][0]["stops"]
        first.update(departure_time="23:50", service_minute=1430)
        second.update(arrival_time="00:05", service_day_offset=1, service_minute=1445)
        return self.runtime.install(package)

    def test_each_row_carries_its_service_day_and_the_answer_the_clock_in_seconds(self):
        from tmbox_gateway.engine import TrafficEngine
        from tmbox_gateway.terminal16 import Terminal16Lab
        publication = self.night()
        payload = copy.deepcopy(publication.payload)
        payload["meet"]["active_day"] = self.runtime.active_day()
        lab = Terminal16Lab(TrafficEngine(publication.session_config()), payload, {"a": "station-a", "b": "station-b"})
        [departure] = lab.station_timetable("a")["rows"]
        [arrival] = lab.station_timetable("b")["rows"]
        self.assertEqual(("departure", "23:50", 0), (departure["kind"], departure["time"], departure["service_day_offset"]))
        self.assertEqual(("arrival", "00:05", 1), (arrival["kind"], arrival["time"], arrival["service_day_offset"]))
        self.application.control_clock(self.client, {"action": "start", "time": "23:55:00", "speed": 1})
        table = self.application._timetable_with_deviations({"rows": [dict(arrival)]})
        self.assertGreaterEqual(table["clock_elapsed_seconds"], at("23:55"))
        self.assertTrue(table["clock_time"].startswith("23:5"))
        self.assertEqual(1, table["rows"][0]["service_day_offset"], "the field survives the deviation pass")


class CorrectionDuringPlayTests(CloudDeliveryFixture):
    def correction(self, change):
        self.offered = cloud_package()
        change(self.offered)
        return self.application.auto_sync_cloud_runtime()

    @staticmethod
    def retime_202(package):
        row = next(row for row in package["trains"] if row["id"] == "movement-202-a")
        row.update(arrival_time="10:05", sort_time="10:05")

    def test_a_correction_for_a_train_at_rest_applies_at_once_and_nothing_under_way_is_reset(self):
        self.busy()
        case = self.application.station_service.open_cases("station-a")[0]
        result = self.correction(self.retime_202)
        self.assertTrue(result["pending"], "the whole version still waits for calm traffic")
        self.assertEqual({"applied": True, "count": 1, "trains": ["202"]}, result["correction"])
        self.assertIn("Rättelsen från Cloud gäller nu för 1 tåg", result["message"])
        active = self.runtime.active()
        self.assertEqual(("cloud-first", 1), (active.publication_id, active.local_revision), "same publication, a layer on top")
        self.assertEqual("10:05", next(row for row in active.payload["trains"] if row["id"] == "movement-202-a")["arrival_time"])
        self.assertEqual("cloud", self.runtime.local_edits_summary(active)["source"])
        # Inställningar → Tidtabell får veta att lagret är Clouds rättelse, inte admins.
        self.assertEqual("cloud", self.application.meet_data(self.client)["local_edits"]["source"])
        self.assertEqual([case["clearance_id"]], [item["clearance_id"] for item in self.application.station_service.open_cases("station-a")],
                         "train 101's clearance is untouched")
        # Samma version igen gör ingenting nytt.
        again = self.application.auto_sync_cloud_runtime()
        self.assertEqual(1, self.runtime.active().local_revision)
        self.assertTrue(again["correction"]["already"])
        # När trafiken är lugn tas hela versionen utan att admin behöver välja.
        self.free()
        done = self.application.auto_sync_cloud_runtime()
        self.assertTrue(done["activated"], done)
        self.assertEqual(("cloud-second", 0), (self.runtime.active().publication_id, self.runtime.active().local_revision))
        self.assertEqual("10:05", next(row for row in self.runtime.active().payload["trains"] if row["id"] == "movement-202-a")["arrival_time"])

    def test_a_correction_for_a_train_with_an_open_clearance_waits_for_it(self):
        self.busy()

        def retime_101(package):
            row = next(row for row in package["trains"] if row["id"] == "movement-101-b")
            row.update(arrival_time="09:40", sort_time="09:40")
        result = self.correction(retime_101)
        self.assertFalse(result["correction"]["applied"])
        self.assertIn("Tåg 101 har ett öppet körtillstånd", result["message"])
        self.assertEqual(0, self.runtime.active().local_revision)

    def test_a_version_that_changes_more_than_the_timetable_waits_as_before(self):
        self.busy()
        result = self.correction(lambda package: package["stations"][0].update(name="Charlottendal C"))
        self.assertEqual("not_timetable_only", result["correction"]["reason"])
        self.assertEqual(0, self.runtime.active().local_revision)
        self.assertTrue(result["pending"])

    def test_admins_own_local_edits_are_never_replaced_here(self):
        layer = self.application.meet_data(self.client)
        draft = layer["draft"]
        next(row for row in draft["trains"] if row["id"] == "movement-202-a")["note"] = "Extra vagn"
        self.application.save_meet_data(self.client, {"draft": draft, "expected_revision": 0})
        self.busy()
        result = self.correction(self.retime_202)
        self.assertTrue(result.get("local_changes"), result)
        self.assertNotIn("correction", result)
        self.assertEqual("local", self.runtime.local_edits_summary(self.runtime.active())["source"])


if __name__ == "__main__":
    unittest.main()
