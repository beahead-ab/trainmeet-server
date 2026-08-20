from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.models import (
    Command,
    ConnectionConfig,
    ConnectionState,
    DispatchMode,
    PanelConfig,
    SessionConfig,
    StationConfig,
)


class EngineDriver:
    def __init__(self, mode: DispatchMode = DispatchMode.CLEARANCE):
        self.engine = TrafficEngine(sample_session(mode))
        self.sequence = 0

    def press(self, panel_id: str, key: str, client_id: str | None = None, command_id: str | None = None):
        self.sequence += 1
        now = datetime.now(timezone.utc)
        command = Command(
            command_id=command_id or f"command-{self.sequence}",
            client_id=client_id or f"client-{panel_id}",
            traffic_session_id="test-session",
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


class MultiConnectionDriver(EngineDriver):
    def __init__(self):
        stations = {
            "center": StationConfig("center", "CDA", "Charlottendal"),
            "left": StationConfig("left", "LEK", "Lekby"),
            "right": StationConfig("right", "VST", "Vagnsta"),
        }
        connections = {
            "left-center": ConnectionConfig("left-center", "left", "center"),
            "center-right": ConnectionConfig("center-right", "center", "right"),
        }
        panels = {
            "panel-center": PanelConfig(
                "panel-center",
                "center",
                "Charlottendal",
                {"A": "left-center", "B": "center-right", "C": None, "D": None},
            ),
            "panel-left": PanelConfig(
                "panel-left",
                "left",
                "Lekby",
                {"A": "left-center", "B": None, "C": None, "D": None},
            ),
            "panel-right": PanelConfig(
                "panel-right",
                "right",
                "Vagnsta",
                {"A": "center-right", "B": None, "C": None, "D": None},
            ),
        }
        self.engine = TrafficEngine(SessionConfig(
            id="test-session",
            name="Flera samtidiga tåg",
            default_dispatch_mode=DispatchMode.CLEARANCE,
            stations=stations,
            connections=connections,
            panels=panels,
            clock_time="12:34",
        ))
        self.sequence = 0

    def enter_train(self, panel_id: str, slot: str = "A", train_number: str = "2123"):
        self.press(panel_id, slot)
        for digit in train_number:
            self.press(panel_id, digit)
        return self.press(panel_id, "#")


class TrafficEngineTests(unittest.TestCase):
    def test_clearance_full_flow(self):
        driver = EngineDriver()

        request_ack = driver.enter_train("panel-a")
        self.assertEqual(request_ack.status, "accepted")
        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.REQUESTED)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "idle")
        self.assertEqual(driver.engine.snapshot("panel-a")["display"]["line1"], "A~2123          ")
        self.assertEqual(driver.engine.snapshot("panel-b")["display"]["line1"], "A!2123          ")

        driver.press("panel-b", "A")
        driver.press("panel-b", "#")
        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.RESERVED)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        self.assertEqual(driver.engine.snapshot("panel-a")["slots"]["A"]["action"], "depart")
        self.assertEqual(driver.engine.snapshot("panel-a")["attention"], {"count": 1, "slots": ["A"]})

        driver.press("panel-a", "A")
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
        driver.press("panel-b", "A")
        driver.press("panel-b", "#")
        self.assertEqual(driver.engine.snapshot("panel-a")["display"], {
            "line1": "A!2123          ",
            "line2": "           12:34",
        })

        driver.press("panel-a", "A")
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
        driver.press("panel-b", "A")
        driver.press("panel-b", "*")

        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.FREE)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "idle")

    def test_sender_can_cancel_pending_request(self):
        driver = EngineDriver()
        driver.enter_train("panel-a", "42")
        driver.press("panel-a", "A")
        driver.press("panel-a", "*")

        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.FREE)
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "idle")

    def test_direct_mode_reserves_without_receiver_confirmation(self):
        driver = EngineDriver(DispatchMode.DIRECT)
        driver.enter_train("panel-a", "77")

        self.assertEqual(driver.engine.connections["connection-a-b"].state, ConnectionState.RESERVED)
        self.assertEqual(driver.engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        self.assertEqual(driver.engine.snapshot("panel-b")["interaction"]["mode"], "idle")

        blocked = driver.press("panel-b", "A")
        self.assertEqual(blocked.status, "rejected")
        self.assertEqual(blocked.reason, "connection_busy")

    def test_duplicate_departure_command_does_not_duplicate_transition(self):
        driver = EngineDriver(DispatchMode.DIRECT)
        driver.enter_train("panel-a", "77")
        driver.press("panel-a", "A")
        driver.press("panel-a", "A")

        now = datetime.now(timezone.utc)
        command = Command(
            command_id="departure-once",
            client_id="client-panel-a",
            traffic_session_id="test-session",
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
            traffic_session_id="test-session",
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
            traffic_session_id="test-session",
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

    def test_approval_does_not_interrupt_another_train_entry(self):
        driver = MultiConnectionDriver()
        driver.enter_train("panel-center", "A", "101")

        driver.press("panel-center", "B")
        driver.press("panel-center", "2")
        driver.press("panel-left", "A")
        driver.press("panel-left", "#")

        center = driver.engine.snapshot("panel-center")
        self.assertEqual(center["interaction"]["mode"], "enter_train")
        self.assertEqual(center["interaction"]["selected_slot"], "B")
        self.assertEqual(center["interaction"]["train_number"], "2")
        self.assertEqual(center["slots"]["A"]["action"], "depart")

        driver.press("panel-center", "0")
        driver.press("panel-center", "2")
        driver.press("panel-center", "#")
        center = driver.engine.snapshot("panel-center")
        self.assertEqual(center["interaction"]["mode"], "idle")
        self.assertEqual(center["slots"]["A"]["state"], "reserved")
        self.assertEqual(center["slots"]["B"]["state"], "requested")

    def test_incoming_request_waits_in_slot_without_taking_over_input(self):
        driver = MultiConnectionDriver()
        driver.press("panel-center", "B")
        driver.press("panel-center", "9")

        driver.enter_train("panel-left", "A", "303")

        center = driver.engine.snapshot("panel-center")
        self.assertEqual(center["interaction"]["mode"], "enter_train")
        self.assertEqual(center["interaction"]["selected_slot"], "B")
        self.assertEqual(center["interaction"]["train_number"], "9")
        self.assertEqual(center["slots"]["A"]["action"], "answer_request")
        self.assertEqual(center["attention"], {"count": 1, "slots": ["A"]})

        driver.press("panel-center", "*")
        driver.press("panel-center", "A")
        self.assertEqual(
            driver.engine.snapshot("panel-center")["interaction"]["mode"],
            "incoming_request",
        )

    def test_panel_snapshot_shows_the_meeting_clock_not_the_publication_time(self):
        driver = EngineDriver()
        driver.engine.set_clock_source(
            lambda: {
                "configured": True,
                "time": "14:32:07",
                "running": True,
                "stopped_reason": None,
            }
        )

        snapshot = driver.engine.snapshot("panel-a")
        self.assertEqual(snapshot["clock"]["time"], "14:32")
        self.assertTrue(snapshot["clock"]["running"])
        self.assertEqual(snapshot["clock"]["source"], "meeting_clock")
        # The idle row carries the same meeting time, not the 12:34 start
        # time baked into the publication.
        self.assertEqual(snapshot["display"]["line2"], "           14:32")

    def test_a_stopped_meeting_clock_is_never_reported_as_running(self):
        driver = EngineDriver()
        driver.engine.set_clock_source(
            lambda: {
                "configured": True,
                "time": "14:32:07",
                "running": False,
                "stopped_reason": "Tekniskt stopp",
            }
        )

        clock = driver.engine.snapshot("panel-a")["clock"]
        self.assertFalse(clock["running"])
        self.assertEqual(clock["stopped_reason"], "Tekniskt stopp")

    def test_publication_start_time_is_never_claimed_to_be_running(self):
        driver = EngineDriver()

        clock = driver.engine.snapshot("panel-a")["clock"]
        self.assertEqual(clock["time"], "12:34")
        self.assertFalse(clock["running"])
        self.assertFalse(clock["configured"])
        self.assertEqual(clock["source"], "publication_start_time")

    def test_an_unreadable_clock_never_takes_the_snapshot_down(self):
        driver = EngineDriver()

        def broken_clock() -> dict:
            raise RuntimeError("clock database is locked")

        driver.engine.set_clock_source(broken_clock)

        clock = driver.engine.snapshot("panel-a")["clock"]
        self.assertEqual(clock["time"], "12:34")
        self.assertFalse(clock["running"])

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
