from __future__ import annotations

from copy import deepcopy


def _runtime_package_base() -> dict:
    return {
        "schema_version": 1,
        "publication_id": "publication-2026-08-11-a",
        "published_at": "2026-08-11T08:30:00Z",
        "meet": {
            "id": "meet-a",
            "name": "Sommarträffen",
            "slug": "sommartraffen",
            "active_day": "Lör",
            "timezone": "Europe/Stockholm",
            "default_dispatch_mode": "clearance",
            "clock_time": "09:15",
        },
        "stations": [
            {"id": "station-a", "code": "CDA", "name": "Charlottendahl"},
            {"id": "station-b", "code": "LEK", "name": "Lekeberg"},
        ],
        "connections": [
            {
                "id": "connection-a-b",
                "station_a_id": "station-a",
                "station_b_id": "station-b",
                "track_type": "single",
            }
        ],
        "panels": [
            {
                "id": "panel-a",
                "station_id": "station-a",
                "name": "CDA TMBox",
                "slots": {"A": "connection-a-b", "B": None, "C": None, "D": None},
            },
            {
                "id": "panel-b",
                "station_id": "station-b",
                "name": "LEK TMBox",
                "slots": {"A": "connection-a-b", "B": None, "C": None, "D": None},
            },
        ],
        "tracks": [
            {
                "id": "track-station-a-1",
                "display_label": "1",
                "station_id": "station-a",
                "operating_point_id": None,
                "active": True,
                "sort_order": 10,
            },
            {
                "id": "track-station-a-2",
                "display_label": "2",
                "station_id": "station-a",
                "operating_point_id": None,
                "active": True,
                "sort_order": 20,
            },
            {
                "id": "track-station-b-1",
                "display_label": "1",
                "station_id": "station-b",
                "operating_point_id": None,
                "active": True,
                "sort_order": 10,
            },
        ],
        "trains": [
            {
                "id": "movement-101-a",
                "train_number": "101",
                "station_id": "station-a",
                "station": "CDA",
                "track_id": "track-station-a-1",
                "days": "Dagl",
                "arrival_time": None,
                "departure_time": "09:20",
                "arrival_from": None,
                "departure_to": "LEK",
                "sort_time": "09:20",
                "no_stop": False,
                "note": None,
                "manual_sort_order": 0,
            },
            {
                "id": "movement-101-b",
                "train_number": "101",
                "station_id": "station-b",
                "station": "LEK",
                "track_id": "track-station-b-1",
                "days": "Dagl",
                "arrival_time": "09:35",
                "departure_time": None,
                "arrival_from": "CDA",
                "departure_to": None,
                "sort_time": "09:35",
                "no_stop": False,
                "note": None,
                "manual_sort_order": 0,
            },
            {
                "id": "movement-202-a",
                "train_number": "202",
                "station_id": "station-a",
                "station": "CDA",
                "track_id": "track-station-a-2",
                "days": "Sön",
                "arrival_time": "10:10",
                "departure_time": "10:15",
                "arrival_from": "LEK",
                "departure_to": "LEK",
                "sort_time": "10:10",
                "no_stop": False,
                "note": None,
                "manual_sort_order": 0,
            },
        ],
        "routes": [
            {
                "id": "route-101-a",
                "train_number": "101",
                "station_id": "station-a",
                "station_name": "CDA",
                "stop_order": 0,
                "arrival_time": None,
                "departure_time": "09:20",
            },
            {
                "id": "route-101-b",
                "train_number": "101",
                "station_id": "station-b",
                "station_name": "LEK",
                "stop_order": 1,
                "arrival_time": "09:35",
                "departure_time": None,
            },
            {
                "id": "route-202-a",
                "train_number": "202",
                "station_id": "station-a",
                "station_name": "CDA",
                "stop_order": 0,
                "arrival_time": "10:10",
                "departure_time": "10:15",
            },
        ],
    }


def runtime_package() -> dict:
    return runtime_package_v3()


def runtime_package_v3(*, publication_id: str = "publication-2026-08-11-a") -> dict:
    package = deepcopy(_runtime_package_base())
    package["schema_version"] = 3
    package["publication_id"] = publication_id
    package["clock"] = {
        "source": "local",
        "start_time": "09:15",
        "speed": 4,
        "show_seconds": True,
        "available_styles": [
            "swiss",
            "swedish",
            "norwegian",
            "danish",
            "german",
            "finnish",
            "polish",
            "dutch",
            "french",
            "italian",
            "american",
            "digital",
        ],
        "stop_reasons": [{"key": "technical", "label": "Tekniskt stopp"}],
    }
    package["stations"] = [
        {**station, "diagram_order": index, "is_autonomous": False, "is_topology_branch": False}
        for index, station in enumerate(package["stations"])
    ]
    package["connections"] = [
        {
            **connection,
            "display_side_a": "right",
            "display_side_b": "left",
            "display_order_a": 0,
            "display_order_b": 0,
            "tambox_key_a": "A",
            "tambox_key_b": "A",
        }
        for connection in package["connections"]
    ]
    package["autonomous_links"] = []
    package["services"] = [
        {
            "id": "service-101-Dagl",
            "train_number": "101",
            "days": "Dagl",
            "train_type": "person",
            "stops": [
                {
                    "station_id": "station-a",
                    "station_name": "CDA",
                    "stop_order": 0,
                    "arrival_time": None,
                    "departure_time": "09:20",
                    "service_day_offset": 0,
                    "service_minute": 560,
                },
                {
                    "station_id": "station-b",
                    "station_name": "LEK",
                    "stop_order": 1,
                    "arrival_time": "09:35",
                    "departure_time": None,
                    "service_day_offset": 0,
                    "service_minute": 575,
                },
            ],
        },
        {
            "id": "service-202-Sön",
            "train_number": "202",
            "days": "Sön",
            "train_type": "person",
            "stops": [
                {
                    "station_id": "station-a",
                    "station_name": "CDA",
                    "stop_order": 0,
                    "arrival_time": "10:10",
                    "departure_time": "10:15",
                    "service_day_offset": 0,
                    "service_minute": 610,
                }
            ],
        },
    ]
    for train in package["trains"]:
        train["service_id"] = (
            "service-101-Dagl" if train["train_number"] == "101" else "service-202-Sön"
        )
    for route in package["routes"]:
        route["service_id"] = (
            "service-101-Dagl" if route["train_number"] == "101" else "service-202-Sön"
        )
        route["days"] = "Dagl" if route["train_number"] == "101" else "Sön"
        route["service_day_offset"] = 0
        route["service_minute"] = 560 + int(route["stop_order"]) * 15
    package["display"] = {
        "graph_station_order": ["station-a", "station-b"],
        "topology_branch_station_ids": [],
        "default_theme": "dark",
    }
    return package


def fictional_runtime_package(
    *, publication_id: str = "publication-fiktiv-a"
) -> dict:
    """The constructed Cda/Lek/Vst/Kun topology from decision B5.

    Charlottendal is the real integration reference, but some things need a
    topology someone built on purpose: three neighbours from one station, and
    double track in both directions so directed channels have something to be
    tested against. The catalogue is written by hand in exactly the schema v3
    shape production data uses.
    """
    stations = [
        ("st-cda", "CDA", "Charlottendal"),
        ("st-lek", "LEK", "Lekby"),
        ("st-vst", "VST", "Vagnsta"),
        ("st-kun", "KUN", "Kungsfors"),
    ]
    tracks = []
    for station_id, _, _ in stations:
        for order, label in enumerate(("1A", "1B", "2A", "2B"), start=1):
            tracks.append(
                {
                    "id": f"track-{station_id.removeprefix('st-')}-{label.lower()}",
                    "display_label": label,
                    "station_id": station_id,
                    "operating_point_id": None,
                    "active": True,
                    "sort_order": order * 10,
                }
            )
    return {
        "schema_version": 3,
        "publication_id": publication_id,
        "published_at": "2026-08-20T07:00:00Z",
        "meet": {
            "id": "meet-fiktiv",
            "name": "Fiktiv fyrstationsträff",
            "slug": "fiktiv",
            "active_day": "Dagl",
            "timezone": "Europe/Stockholm",
            "default_dispatch_mode": "clearance",
            "clock_time": "09:00",
        },
        "clock": {
            "source": "local",
            "start_time": "09:00",
            "speed": 1,
            "show_seconds": True,
            "available_styles": ["swedish", "digital"],
            "stop_reasons": [{"key": "technical", "label": "Tekniskt stopp"}],
        },
        "stations": [
            {
                "id": station_id,
                "code": code,
                "name": name,
                "diagram_order": index,
                "is_autonomous": False,
                "is_topology_branch": False,
            }
            for index, (station_id, code, name) in enumerate(stations)
        ],
        "connections": [
            {
                "id": "connection-cda-lek",
                "station_a_id": "st-cda",
                "station_b_id": "st-lek",
                "track_type": "double",
            },
            {
                "id": "connection-cda-vst",
                "station_a_id": "st-cda",
                "station_b_id": "st-vst",
                "track_type": "double",
            },
            {
                "id": "connection-cda-kun",
                "station_a_id": "st-cda",
                "station_b_id": "st-kun",
                "track_type": "single",
            },
        ],
        "panels": [
            {
                "id": f"panel-{station_id.removeprefix('st-')}",
                "station_id": station_id,
                "name": f"{code} TMBox",
                "slots": {"A": None, "B": None, "C": None, "D": None},
            }
            for station_id, code, _ in stations
        ],
        "tracks": tracks,
        "trains": [
            {
                "id": "movement-421-cda",
                "train_number": "421",
                "station_id": "st-cda",
                "station": "CDA",
                "track_id": "track-cda-1b",
                "days": "Dagl",
                "arrival_time": None,
                "departure_time": "09:20",
                "arrival_from": None,
                "departure_to": "VST",
                "sort_time": "09:20",
                "no_stop": False,
                "note": None,
                "manual_sort_order": 0,
                "service_id": "service-421",
            },
            {
                "id": "movement-428-cda",
                "train_number": "428",
                "station_id": "st-cda",
                "station": "CDA",
                "track_id": "track-cda-2a",
                "days": "Dagl",
                "arrival_time": "09:41",
                "departure_time": None,
                "arrival_from": "VST",
                "departure_to": None,
                "sort_time": "09:41",
                "no_stop": False,
                "note": None,
                "manual_sort_order": 0,
                "service_id": "service-428",
            },
        ],
        "routes": [
            {
                "id": "route-421-cda",
                "train_number": "421",
                "station_id": "st-cda",
                "station_name": "CDA",
                "stop_order": 0,
                "arrival_time": None,
                "departure_time": "09:20",
            },
            {
                "id": "route-421-vst",
                "train_number": "421",
                "station_id": "st-vst",
                "station_name": "VST",
                "stop_order": 1,
                "arrival_time": "09:35",
                "departure_time": None,
            },
            {
                "id": "route-428-vst",
                "train_number": "428",
                "station_id": "st-vst",
                "station_name": "VST",
                "stop_order": 0,
                "arrival_time": None,
                "departure_time": "09:26",
            },
            {
                "id": "route-428-cda",
                "train_number": "428",
                "station_id": "st-cda",
                "station_name": "CDA",
                "stop_order": 1,
                "arrival_time": "09:41",
                "departure_time": None,
            },
        ],
        "services": [
            {
                "id": "service-421",
                "train_number": "421",
                "days": "Dagl",
                "train_type": "person",
                "stops": [
                    {
                        "station_id": "st-cda",
                        "station_name": "CDA",
                        "stop_order": 0,
                        "arrival_time": None,
                        "departure_time": "09:20",
                        "service_day_offset": 0,
                        "service_minute": 560,
                    },
                    {
                        "station_id": "st-vst",
                        "station_name": "VST",
                        "stop_order": 1,
                        "arrival_time": "09:35",
                        "departure_time": None,
                        "service_day_offset": 0,
                        "service_minute": 575,
                    },
                ],
            },
            {
                "id": "service-428",
                "train_number": "428",
                "days": "Dagl",
                "train_type": "person",
                "stops": [
                    {
                        "station_id": "st-vst",
                        "station_name": "VST",
                        "stop_order": 0,
                        "arrival_time": None,
                        "departure_time": "09:26",
                        "service_day_offset": 0,
                        "service_minute": 566,
                    },
                    {
                        "station_id": "st-cda",
                        "station_name": "CDA",
                        "stop_order": 1,
                        "arrival_time": "09:41",
                        "departure_time": None,
                        "service_day_offset": 0,
                        "service_minute": 581,
                    },
                ],
            },
        ],
        "autonomous_links": [],
        "display": {
            "graph_station_order": ["st-cda", "st-lek", "st-vst", "st-kun"],
            "topology_branch_station_ids": [],
            "default_theme": "dark",
        },
    }
