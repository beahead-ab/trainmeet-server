"""Runtime day selection is scoped and must not discard active work."""
from http import HTTPStatus

from test_pending_revisions import CloudDeliveryFixture, dispatch_request
from tmbox_gateway.identity import DeviceKind, PairedClient
from tmbox_gateway.models import InteractionMode


class RuntimeDayTests(CloudDeliveryFixture):
    def change(self, day="Sön", **extra):
        return dispatch_request(self.application, self.client, "/v1/runtime/active-day", {
            "active_day": day,
            "meet_generation": self.application.lifecycle.selected()["generation"], **extra})

    def test_paused_idle_day_change_keeps_clock_config_and_history(self):
        publication = self.runtime.active()
        movement = publication.payload["trains"][0]
        self.operations.set_train_readiness(publication.publication_id, publication.active_day,
            movement["station_id"], movement["id"], None,
            action="ready", actor="tkl", actor_role="tkl", shift_id=None)
        clock = self.clock_record()
        version, revision = self.runtime.config_version(), self.application.engine.revision
        generation = self.application.lifecycle.selected()["generation"]
        status, body = self.change()
        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(body["changed"])
        self.assertEqual("Sön", self.runtime.active_day())
        self.assertEqual(clock, self.clock_record())
        self.assertEqual(publication.payload, self.runtime.active().payload)
        self.assertEqual(generation + 1, body["meet_generation"])
        self.assertEqual(version + 1, self.runtime.config_version())
        self.assertGreater(self.application.engine.revision, revision)
        self.assertEqual("true", self.runtime._setting("require_scoped_commands"))
        self.assertTrue(self.operations.train_readiness(publication.publication_id,
            publication.active_day, movement["station_id"]))
        self.assertIsNone(self.application.lifecycle.transition())

    def test_current_day_is_a_no_op_even_when_clock_is_running(self):
        self.operations.start_clock()
        before = self.application.lifecycle.selected()
        status, body = self.change(self.runtime.active_day())
        self.assertEqual(HTTPStatus.OK, status)
        self.assertFalse(body["changed"])
        self.assertEqual(before, self.application.lifecycle.selected())

    def test_running_clock_blocks_without_stopping_it(self):
        self.operations.start_clock()
        before = self.clock_record()
        status, body = self.change()
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("active_day_busy", body["code"])
        self.assertEqual(before, self.clock_record())
        self.assertNotEqual("Sön", self.runtime.active_day())

    def test_stopped_clock_still_blocks_occupied_line_or_panel_input(self):
        self.busy()
        self.assertEqual(HTTPStatus.CONFLICT, self.change()[0])
        self.free()
        panel = next(iter(self.application.engine.panels.values()))
        panel.mode = InteractionMode.ENTER_TRAIN
        self.assertEqual(HTTPStatus.CONFLICT, self.change()[0])
        panel.mode = InteractionMode.IDLE
        self.assertEqual(HTTPStatus.OK, self.change()[0])

    def test_persisted_clearance_blocks_even_if_engine_is_idle(self):
        publication = self.runtime.active()
        self.operations.request_clearance(publication.publication_id, publication.active_day,
            clearance_id="day-clearance", movement_id=publication.payload["trains"][0]["id"],
            connection_id="connection-a-b", channel_id="ab", from_station_id="station-a",
            to_station_id="station-b", track_id=None, requested_by="tkl", ttl_seconds=60)
        self.assertEqual(HTTPStatus.CONFLICT, self.change()[0])
        self.operations.settle_clearance("day-clearance", "cancelled", "tkl")
        self.assertEqual(HTTPStatus.OK, self.change()[0])

    def test_scope_required_and_old_commands_cannot_cross_day_boundary(self):
        for value in (None, -1, True):
            status, body = self.change(meet_generation=value)
            self.assertEqual(HTTPStatus.CONFLICT, status)
            self.assertEqual("stale_meet_context", body["code"])
        generation = self.application.lifecycle.selected()["generation"]
        self.assertEqual(HTTPStatus.OK, self.change()[0])
        status, body = dispatch_request(self.application, self.client, "/v1/clock", {
            "action": "start", "meet_generation": generation})
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertFalse(self.operations.clock_status()["running"])

    def test_non_admin_and_other_region_cannot_change_day(self):
        original = self.client
        self.client = PairedClient("tkl", "tkl", DeviceKind.TKL_TERMINAL, ("panel-a",))
        self.assertEqual(HTTPStatus.FORBIDDEN, self.change()[0])
        self.client = original
        self.application.lifecycle.select("us", "other", "us-package", allow_switch=True)
        self.assertEqual(HTTPStatus.CONFLICT, self.change()[0])

    def test_invalid_day_does_not_start_a_transition(self):
        for day in ("", " ", None, ["Sön"], "x" * 41):
            self.assertEqual(HTTPStatus.BAD_REQUEST, self.change(day)[0])
            self.assertIsNone(self.application.lifecycle.transition())

    def test_repeated_setup_completion_cannot_bypass_day_guard_during_traffic(self):
        self.runtime.save_server_name("Runtime test")
        self.operations.start_clock()
        self.busy()
        original_day = self.runtime.active_day()
        clock, selected = self.clock_record(), self.application.lifecycle.selected()
        for _ in range(2):
            status, body = dispatch_request(self.application, self.client, "/v1/setup/complete", {"active_day": "Sön"})
            self.assertEqual(HTTPStatus.OK, status)
            self.assertTrue(body["completed"])
            self.assertFalse(body["restart_required"])
            self.assertEqual(original_day, body["active_day"])
            self.assertEqual(original_day, self.runtime.active_day())
            self.assertEqual(clock, self.clock_record())
            self.assertEqual(selected, self.application.lifecycle.selected())
