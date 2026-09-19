from __future__ import annotations

import tempfile
import unittest
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.models import Command, ConnectionState, DispatchMode
from tmbox_gateway.storage import ConfigurationMismatchError, SQLiteStateStore, session_config_fingerprint


def command_for(
    engine: TrafficEngine,
    panel_id: str,
    key: str,
    command_id: str,
    *,
    client_id: str | None = None,
) -> Command:
    now = datetime.now(timezone.utc)
    return Command(
        command_id=command_id,
        client_id=client_id or f"client-{panel_id}",
        traffic_session_id="test-session",
        panel_id=panel_id,
        expected_revision=engine.revision,
        key=key,
        sent_at=now,
        expires_at=now + timedelta(seconds=5),
    )


def press(engine: TrafficEngine, panel_id: str, key: str, sequence: int) -> None:
    engine.press(command_for(engine, panel_id, key, f"setup-{sequence}"))


class PersistenceTests(unittest.TestCase):
    def test_software_upgrade_preserves_legacy_and_190_run_without_rewriting(self):
        config = sample_session(DispatchMode.CLEARANCE)
        for explicit_defaults in (False, True):
            with self.subTest(explicit_defaults=explicit_defaults), tempfile.TemporaryDirectory() as directory:
                store = SQLiteStateStore(Path(directory) / 'trainmeet.db')
                try:
                    engine = TrafficEngine(config)
                    for sequence, key in enumerate(['A', '3', '9', '#'], start=1):
                        press(engine, 'panel-a', key, sequence)
                    value = asdict(config)
                    if not explicit_defaults:
                        for panel in value['panels'].values():
                            panel.pop('slot_layout')
                    fingerprint = hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
                    store.save(config.id, fingerprint, engine.revision, engine.export_state())
                    before = store._connection.execute('SELECT * FROM engine_state').fetchall()
                    restored = TrafficEngine(config, state_store=store)
                    self.assertEqual(restored.export_state(), engine.export_state())
                    self.assertEqual(restored.snapshot('panel-a'), engine.snapshot('panel-a'))
                    self.assertEqual(store._connection.execute('SELECT * FROM engine_state').fetchall(), before)
                    self.assertEqual(restored.press(command_for(restored, 'panel-a', '#', 'setup-4')).status, 'duplicate')
                    for changed in (replace(config, name='changed'), replace(config, panels={key: replace(panel, slot_layout='columns') for key, panel in config.panels.items()})):
                        with self.assertRaises(ConfigurationMismatchError):
                            TrafficEngine(changed, state_store=store)
                finally:
                    store.close()

    def test_nondefault_display_layout_still_changes_configuration_identity(self):
        original = sample_session(DispatchMode.CLEARANCE)
        changed = replace(original, panels={key: replace(panel, slot_layout='columns') for key, panel in original.panels.items()})
        self.assertNotEqual(session_config_fingerprint(original), session_config_fingerprint(changed))

    def test_pending_clearance_request_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trainmeet.db"
            first_store = SQLiteStateStore(path)
            engine = TrafficEngine(
                sample_session(DispatchMode.CLEARANCE),
                state_store=first_store,
            )
            for sequence, key in enumerate(["A", "2", "1", "2", "3", "#"], start=1):
                press(engine, "panel-a", key, sequence)
            expected_revision = engine.revision
            expected_sender_display = engine.snapshot("panel-a")["display"]
            expected_receiver_display = engine.snapshot("panel-b")["display"]
            first_store.close()

            second_store = SQLiteStateStore(path)
            try:
                restored = TrafficEngine(
                    sample_session(DispatchMode.CLEARANCE),
                    state_store=second_store,
                )
                self.assertEqual(restored.revision, expected_revision)
                self.assertEqual(
                    restored.connections["connection-a-b"].state,
                    ConnectionState.REQUESTED,
                )
                self.assertEqual(restored.snapshot("panel-a")["display"], expected_sender_display)
                self.assertEqual(restored.snapshot("panel-b")["display"], expected_receiver_display)
                self.assertEqual(
                    restored.snapshot("panel-b")["interaction"]["mode"],
                    "idle",
                )
            finally:
                second_store.close()

    def test_occupied_line_and_command_id_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trainmeet.db"
            first_store = SQLiteStateStore(path)
            engine = TrafficEngine(
                sample_session(DispatchMode.DIRECT),
                state_store=first_store,
            )
            for sequence, key in enumerate(["A", "7", "7", "#", "A", "A"], start=1):
                press(engine, "panel-a", key, sequence)
            departure = command_for(
                engine,
                "panel-a",
                "A",
                "departure-persisted-once",
            )
            first_ack = engine.press(departure)
            expected_revision = engine.revision
            first_store.close()

            second_store = SQLiteStateStore(path)
            try:
                restored = TrafficEngine(
                    sample_session(DispatchMode.DIRECT),
                    state_store=second_store,
                )
                self.assertEqual(
                    restored.connections["connection-a-b"].state,
                    ConnectionState.OCCUPIED,
                )
                self.assertEqual(restored.revision, expected_revision)
                duplicate = restored.press(departure)
                self.assertEqual(duplicate.status, "duplicate")
                self.assertEqual(duplicate.revision, first_ack.revision)
                self.assertEqual(restored.revision, expected_revision)
                self.assertEqual(len(restored.audit), expected_revision)
            finally:
                second_store.close()

    def test_reserved_departure_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trainmeet.db"
            first_store = SQLiteStateStore(path)
            engine = TrafficEngine(
                sample_session(DispatchMode.DIRECT),
                state_store=first_store,
            )
            for sequence, key in enumerate(["A", "4", "2", "#"], start=1):
                press(engine, "panel-a", key, sequence)
            expected_display = engine.snapshot("panel-a")["display"]
            first_store.close()

            second_store = SQLiteStateStore(path)
            try:
                restored = TrafficEngine(
                    sample_session(DispatchMode.DIRECT),
                    state_store=second_store,
                )
                self.assertEqual(
                    restored.connections["connection-a-b"].state,
                    ConnectionState.RESERVED,
                )
                self.assertEqual(
                    restored.snapshot("panel-a")["interaction"]["mode"],
                    "idle",
                )
                self.assertEqual(restored.snapshot("panel-a")["display"], expected_display)
            finally:
                second_store.close()

    def test_changed_configuration_cannot_open_active_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trainmeet.db"
            original = sample_session(DispatchMode.CLEARANCE)
            first_store = SQLiteStateStore(path)
            engine = TrafficEngine(original, state_store=first_store)
            press(engine, "panel-a", "A", 1)
            first_store.close()

            changed = replace(original, name="Another published configuration")
            second_store = SQLiteStateStore(path)
            try:
                with self.assertRaises(ConfigurationMismatchError):
                    TrafficEngine(changed, state_store=second_store)
            finally:
                second_store.close()

    def test_failed_disk_write_rolls_back_memory_transition(self):
        class FailingStore:
            def load(self, session_id, config_fingerprint):
                return None

            def save(self, session_id, config_fingerprint, revision, state):
                raise OSError("simulated disk failure")

        engine = TrafficEngine(
            sample_session(DispatchMode.CLEARANCE),
            state_store=FailingStore(),
        )
        with self.assertRaises(OSError):
            engine.press(command_for(engine, "panel-a", "A", "must-not-commit"))

        self.assertEqual(engine.revision, 0)
        self.assertEqual(engine.snapshot("panel-a")["interaction"]["mode"], "idle")
        self.assertNotIn("must-not-commit", engine.processed_commands)


if __name__ == "__main__":
    unittest.main()
