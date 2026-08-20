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
        "trains": [
            {
                "id": "movement-101-a",
                "train_number": "101",
                "station_id": "station-a",
                "station": "CDA",
                "track": "1",
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
                "track": "1",
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
                "track": "2",
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
    return runtime_package_v2()


def runtime_package_v2(*, publication_id: str = "publication-2026-08-11-a") -> dict:
    package = deepcopy(_runtime_package_base())
    package["schema_version"] = 2
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
