from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tambox_gateway.demo import demo_session
from tambox_gateway.engine import TrafficEngine
from tambox_gateway.models import Command, ConnectionState, DispatchMode


class EngineDriver:
    def __init__(self, mode: DispatchMode = DispatchMode.CLEARANCE):
        self.engine = TrafficEngine(demo_session(mode))
        self.sequence = 0

    def press(self, panel_id: str, key: str, client_id: str | None = None, command_id: str | None = None):
        self.sequence += 1
        now = datetime.now(timezone.utc)
        command = Command(
            command_id=command_id or f"command-{self.sequence}",
            client_id=client_id or f"client-{panel_id}",
            traffic_session_id="demo-session",
            panel_id=panel_id,
            expected_revision=self.engine.revision,
            key=key,
            sent_at=now,
            expires_at=now + timedelta(seconds=5),
        )
        return self.engine.press(command, now=now)

    def enter_train(self, panel_id: str, train_number: str = "2123"):
        self.press(panel_id, "A")
        for digit in train_number:
            self.press(panel_id, digit)
        return self.press(panel_id, "#")


class TrafficEngineTests(unittest.TestCase):
    def test_clearance_full_flow(self):
        driver = EngineDriver()

        request_ack = driver.enter_train("panel-a")
        self.assertEqual(request_ack.status, "accepted")
        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.REQUESTED)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "awaiting_permission")
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "incoming_request")
        self.assertEqual(driver.engine.snapshot("panel-a")["display"]["line1"], "2123->LEK       ")
        self.assertEqual(driver.engine.snapshot("panel-a")["display"]["line2"], "Väntar svar*=Avb")

        driver.press("panel-b", "#")
        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.RESERVED)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "ready_departure")

        driver.press("panel-a", "A")
        departure = driver.press("panel-a", "#")
        self.assertEqual(departure.status, "accepted")
        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.OCCUPIED)

        driver.press("panel-b", "A")
        arrival = driver.press("panel-b", "#")
        self.assertEqual(arrival.status, "accepted")
        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.FREE)
        self.assertIn("A<LEK", driver.engine.snapshot("panel-a")["display"]["line1"])

    def test_lovable_compatible_golden_display_frames(self):
        driver = EngineDriver()
        self.assertEqual(driver.engine.snapshot("panel-a")["display"], {
            "line1": "A<LEK           ",
            "line2": "           12:34",
        })

        driver.press("panel-a", "A")
        self.assertEqual(driver.engine.snapshot("panel-a")["display"], {
            "line1": "Till: LEK   #=OK",
            "line2": "Tåg: _     *=Avb",
        })

        for digit in "2123":
            driver.press("panel-a", digit)
        self.assertEqual(driver.engine.snapshot("panel-a")["display"], {
            "line1": "Till: LEK   #=OK",
            "line2": "Tåg: 2123_ *=Avb",
        })

        driver.press("panel-a", "#")
        driver.press("panel-b", "#")
        self.assertEqual(driver.engine.snapshot("panel-a")["display"], {
            "line1": "2123◀LEK    KLAR",
            "line2": "A=Avg      *=Avb",
        })

        driver.press("panel-a", "A")
        self.assertEqual(driver.engine.snapshot("panel-a")["display"], {
            "line1": "2123◀LEK Tåg ut?",
            "line2": "#=Ja       *=Nej",
        })

    def test_rejected_request_releases_connection(self):
        driver = EngineDriver()
        driver.enter_train("panel-a", "42")
        driver.press("panel-b", "*")

        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.FREE)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "idle")

    def test_sender_can_cancel_pending_request(self):
        driver = EngineDriver()
        driver.enter_train("panel-a", "42")
        driver.press("panel-a", "*")

        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.FREE)
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "idle")

    def test_direct_mode_reserves_without_receiver_confirmation(self):
        driver = EngineDriver(DispatchMode.DIRECT)
        driver.enter_train("panel-a", "77")

        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.RESERVED)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "ready_departure")
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "idle")

        blocked = driver.press("panel-b", "A")
        self.assertEqual(blocked.status, "rejected")
        self.assertEqual(blocked.reason, "connection_busy")

    def test_duplicate_departure_command_does_not_duplicate_transition(self):
        driver = EngineDriver(DispatchMode.DIRECT)
        driver.enter_train("panel-a", "77")
        driver.press("panel-a", "A")

        now = datetime.now(timezone.utc)
        command = Command(
            command_id="departure-once",
            client_id="client-panel-a",
            traffic_session_id="demo-session",
            panel_id="panel-a",
            expected_revision=driver.engine.revision,
            key="#",
            sent_at=now,
            expires_at=now + timedelta(seconds=5),
        )
        first = driver.engine.press(command, now=now)
        duplicate = driver.engine.press(command, now=now + timedelta(seconds=1))

        self.assertEqual(first.status, "accepted")
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(first.revision, duplicate.revision)
        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.OCCUPIED)

    def test_expired_and_stale_commands_are_rejected(self):
        driver = EngineDriver()
        now = datetime.now(timezone.utc)
        expired = Command(
            command_id="expired",
            client_id="client-a",
            traffic_session_id="demo-session",
            panel_id="panel-a",
            expected_revision=0,
            key="A",
            sent_at=now - timedelta(seconds=10),
            expires_at=now - timedelta(seconds=5),
        )
        self.assertEqual(driver.engine.press(expired, now=now).reason, "expired_command")

        driver.press("panel-a", "A")
        stale = Command(
            command_id="stale",
            client_id="client-a",
            traffic_session_id="demo-session",
            panel_id="panel-a",
            expected_revision=0,
            key="1",
            sent_at=now,
            expires_at=now + timedelta(seconds=5),
        )
        self.assertEqual(driver.engine.press(stale, now=now).reason, "stale_revision")

    def test_second_client_cannot_mix_into_active_input(self):
        driver = EngineDriver()
        driver.press("panel-a", "A", client_id="iphone-admin")
        blocked = driver.press("panel-a", "2", client_id="physical-box")

        self.assertEqual(blocked.status, "rejected")
        self.assertEqual(blocked.reason, "interaction_owned")
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["train_number"], "")

    def test_display_rows_are_always_exactly_sixteen_characters(self):
        driver = EngineDriver()
        for panel_id in ("panel-a", "panel-b"):
            display = driver.engine.snapshot(panel_id)["display"]
            self.assertEqual(len(display["line1"]), 16)
            self.assertEqual(len(display["line2"]), 16)

        driver.enter_train("panel-a", "2123")
        for panel_id in ("panel-a", "panel-b"):
            display = driver.engine.snapshot(panel_id)["display"]
            self.assertEqual(len(display["line1"]), 16)
            self.assertEqual(len(display["line2"]), 16)


if __name__ == "__main__":
    unittest.main()
