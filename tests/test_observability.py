from __future__ import annotations

import io
import logging
import tempfile
import unittest
from pathlib import Path

from runtime_fixture import fictional_runtime_package
from tmbox_gateway.identity import IdentityStore
from tmbox_gateway.observability import (
    REDACTED,
    StructuredFormatter,
    correlation_id,
    log_event,
    use_correlation,
)
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import TMBoxStationService
from tmbox_gateway.runtime import SQLiteRuntimeStore


class StructuredLoggingTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(StructuredFormatter())
        self.logger = logging.getLogger("test.observability")
        self.logger.handlers = [handler]
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)

    def written(self) -> str:
        return self.stream.getvalue()

    def test_a_line_is_parsable_key_by_key(self):
        with use_correlation("msg-42"):
            log_event(
                self.logger,
                "command.accepted",
                device_id="TMBOX-7A42F1",
                action="train.departed",
            )

        line = self.written().strip()
        self.assertIn("correlation_id=msg-42", line)
        self.assertIn("event=command.accepted", line)
        self.assertIn("device_id=TMBOX-7A42F1", line)
        self.assertIn("action=train.departed", line)

    def test_values_with_spaces_stay_one_field(self):
        log_event(self.logger, "runtime.installed", reason="spåret finns inte")

        self.assertIn('reason="spåret finns inte"', self.written())

    def test_a_secret_never_reaches_the_log(self):
        # Redaction is by field name, so a careless caller cannot leak one by
        # handing it to a log call.
        log_event(
            self.logger,
            "pairing.completed",
            client_id="tkl-1",
            pairing_code="123456",
            access_token="s3cr3t-token",
            device_token="box-token",
            password="hunter2",
        )

        line = self.written()
        self.assertNotIn("123456", line)
        self.assertNotIn("s3cr3t-token", line)
        self.assertNotIn("box-token", line)
        self.assertNotIn("hunter2", line)
        self.assertEqual(line.count(REDACTED), 4)
        self.assertIn("client_id=tkl-1", line)

    def test_the_correlation_id_does_not_leak_out_of_its_block(self):
        with use_correlation("inner"):
            self.assertEqual(correlation_id(), "inner")
        self.assertEqual(correlation_id(), "")


class AuditTrailTests(unittest.TestCase):
    def setUp(self):
        # These tests deliberately provoke refusals; the warnings they log are
        # the point of the feature, not output the test run needs to show.
        self._quiet = logging.getLogger("tmbox_gateway.protocol_v2")
        self._previous_level = self._quiet.level
        self._quiet.setLevel(logging.CRITICAL)
        self.addCleanup(lambda: self._quiet.setLevel(self._previous_level))
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name) / "runtime.db"
        self.runtime_store = SQLiteRuntimeStore(root)
        self.operations_store = SQLiteOperationsStore(root)
        self.identities = IdentityStore(root)
        publication = self.runtime_store.install(fictional_runtime_package())
        self.operations_store.ensure_publication(publication)
        self.identities.record_discovery("TMBOX-7A42F1", "TMBOX-7A42F1")
        self.identities.assign_discovered_device("TMBOX-7A42F1", station_id="st-cda")
        self.service = TMBoxStationService(
            self.runtime_store, self.operations_store, self.identities
        )

    def tearDown(self):
        self.identities.close()
        self.operations_store.close()
        self.runtime_store.close()
        self.directory.cleanup()

    def _command(self, message_id: str, action: str, body: dict) -> dict:
        return self.service.handle_command(
            "TMBOX-7A42F1",
            {
                "protocol_version": 2,
                "message_id": message_id,
                "device_id": "TMBOX-7A42F1",
                "action": action,
                "payload": body,
            },
        )

    def test_one_command_is_one_traceable_trail(self):
        self._command("trace-1", "train.position.set", {"movement_id": "movement-421-cda"})

        trail = self.operations_store.audit_trail("trace-1")
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["source"], "tmbox")
        self.assertEqual(trail[0]["actor"], "TMBOX-7A42F1")
        self.assertEqual(trail[0]["action"], "train.position.set")
        self.assertEqual(trail[0]["outcome"], "accepted")
        self.assertEqual(trail[0]["movement_id"], "movement-421-cda")
        self.assertEqual(trail[0]["station_id"], "st-cda")

    def test_a_refusal_records_why(self):
        self._command("trace-2", "train.teleport", {"movement_id": "movement-421-cda"})

        trail = self.operations_store.audit_trail("trace-2")
        self.assertEqual(trail[0]["outcome"], "rejected")
        self.assertEqual(trail[0]["reason"], "unknown_action")

    def test_a_repeated_command_is_recorded_as_the_question_it_is(self):
        body = {"movement_id": "movement-421-cda"}
        self._command("trace-3", "train.position.set", body)
        self._command("trace-3", "train.position.set", body)

        outcomes = [entry["outcome"] for entry in self.operations_store.audit_trail("trace-3")]
        self.assertEqual(outcomes, ["accepted", "duplicate"])

    def test_a_clearance_keeps_its_own_case_history_beside_the_trail(self):
        self._command(
            "trace-4",
            "clearance.request",
            {"movement_id": "movement-421-cda", "connection_id": "connection-cda-vst"},
        )

        trail = self.operations_store.audit_trail("trace-4")
        self.assertEqual(trail[0]["action"], "clearance.request")
        clearance_id = trail[0]["detail"].get("clearance_id") or next(
            case["clearance_id"]
            for case in self.operations_store.open_clearances_for_station(
                self.runtime_store.active().publication_id, "Dagl", "st-cda"
            )
        )
        history = self.operations_store.clearance_history(clearance_id)
        self.assertEqual([entry["event_type"] for entry in history], ["requested"])


if __name__ == "__main__":
    unittest.main()
