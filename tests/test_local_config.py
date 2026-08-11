from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tambox_gateway.local_config import (
    ConfigurationRevisionConflict,
    LocalConfigurationError,
    SQLiteLocalConfigurationStore,
    empty_local_configuration,
    local_configuration_runtime_package,
    validate_local_configuration,
)


def local_configuration() -> dict:
    return {
        "schema_version": 1,
        "id": "hosttraff-2026",
        "name": "Höstträffen",
        "timezone": "Europe/Stockholm",
        "active_day": "Lör",
        "default_dispatch_mode": "clearance",
        "clock_time": "09:05",
        "stations": [
            {"id": "station-cda", "code": "cda", "name": "Charlottendahl"},
            {"id": "station-lek", "code": "LEK", "name": "Lekeberg"},
        ],
        "connections": [
            {
                "id": "connection-cda-lek",
                "station_a_id": "station-cda",
                "station_b_id": "station-lek",
                "track_type": "double",
                "dispatch_mode_override": "direct",
                "display_side_a": "right",
                "display_side_b": "left",
                "display_order_a": 0,
                "display_order_b": 0,
            }
        ],
        "panels": [
            {
                "id": "panel-cda",
                "station_id": "station-cda",
                "name": "CDA Tambox",
                "slots": {"A": "connection-cda-lek", "B": None, "C": None, "D": None},
            },
            {
                "id": "panel-lek",
                "station_id": "station-lek",
                "name": "LEK Tambox",
                "slots": {"A": "connection-cda-lek", "B": None, "C": None, "D": None},
            },
        ],
    }


class LocalConfigurationTests(unittest.TestCase):
    def test_empty_draft_is_editable_but_not_runnable(self):
        draft = validate_local_configuration(empty_local_configuration())
        self.assertEqual(draft["stations"], [])
        with self.assertRaisesRegex(LocalConfigurationError, "minst en station"):
            validate_local_configuration(draft, require_runnable=True)

    def test_runtime_package_uses_common_runtime_schema_without_timetable(self):
        package = local_configuration_runtime_package(
            local_configuration(),
            revision=3,
            published_at=datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(package["publication_id"], "local-hosttraff-2026-r3")
        self.assertEqual(package["meet"]["source"], "local")
        self.assertEqual(package["connections"][0]["track_type"], "double")
        self.assertEqual(package["trains"], [])
        self.assertEqual(package["routes"], [])

    def test_panel_slot_must_reach_its_station(self):
        value = local_configuration()
        value["panels"][0]["station_id"] = "station-lek"
        value["panels"][0]["name"] = "Annan panel"
        # This is still a valid endpoint. Add a third station to create a real mismatch.
        value["stations"].append({"id": "station-x", "code": "XXX", "name": "Extern"})
        value["panels"][0]["station_id"] = "station-x"
        with self.assertRaisesRegex(LocalConfigurationError, "inte når stationen"):
            validate_local_configuration(value)

    def test_store_keeps_revisions_and_rejects_stale_save(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteLocalConfigurationStore(Path(directory) / "tambox.db")
            try:
                first = store.save(local_configuration(), expected_revision=0)
                self.assertEqual(first["revision"], 1)
                changed = local_configuration()
                changed["name"] = "Uppdaterad träff"
                second = store.save(changed, expected_revision=1)
                self.assertEqual(second["revision"], 2)
                self.assertEqual(second["draft"]["name"], "Uppdaterad träff")
                with self.assertRaises(ConfigurationRevisionConflict):
                    store.save(changed, expected_revision=1)
                package = store.runtime_package(expected_revision=2)
                self.assertEqual(package["publication_id"], "local-hosttraff-2026-r2")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
