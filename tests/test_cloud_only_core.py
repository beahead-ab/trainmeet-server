"""Cloud config adoption is not an operating-session reset."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from runtime_fixture import runtime_package_v3
from test_us_terminology import package as us_package
from tmbox_gateway.lifecycle import MeetLifecycleError, SQLiteMeetLifecycle, us_meet_id
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import RuntimePublication, RuntimePublicationError, SQLiteRuntimeStore
from tmbox_gateway.us import USStore, USError


class CoreStoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "runtime.sqlite"

    def store(self, cls):
        value = cls(self.path)
        self.addCleanup(value.close)
        return value


class CloudOnlyCoreTests(CoreStoreCase):
    def test_one_meet_selection_is_durable_and_region_exclusive(self):
        lifecycle = self.store(SQLiteMeetLifecycle)
        first = lifecycle.select("eu", "meet-a", "v1", meet_name="A")
        second_connection = self.store(SQLiteMeetLifecycle)
        self.assertEqual(first, second_connection.selected())
        for region, meet in (("us", "meet-a"), ("eu", "meet-b")):
            with self.assertRaises(MeetLifecycleError):
                lifecycle.select(region, meet, "v2")
        updated = lifecycle.select("eu", "meet-a", "v2", expected_generation=1)
        self.assertEqual(2, updated["generation"])
        with self.assertRaises(MeetLifecycleError):
            lifecycle.assert_selected("eu", publication_id="v1")
        with self.assertRaises(MeetLifecycleError):
            lifecycle.select("us", "meet-b", "v3", expected_generation=1, allow_switch=True)
        lifecycle.select("us", "meet-b", "v3", expected_generation=2, allow_switch=True)
        with self.assertRaises(MeetLifecycleError):
            lifecycle.assert_selected("eu")

    def test_interrupted_transition_fails_closed_across_connections(self):
        lifecycle = self.store(SQLiteMeetLifecycle)
        old = lifecycle.select("eu", "meet-a", "v1")
        ticket = lifecycle.begin_transition("eu", "meet-a", "v2")
        restarted = self.store(SQLiteMeetLifecycle)
        self.assertEqual(old, restarted.selected())
        for action in (lambda: restarted.assert_selected("eu"), restarted.bootstrap,
                       lambda: restarted.select("us", "another", "v3", allow_switch=True)):
            with self.assertRaises(MeetLifecycleError):
                action()
        with self.assertRaises(MeetLifecycleError):
            restarted.complete_transition("stale-ticket")
        restarted.complete_transition(ticket)
        self.assertEqual("v2", lifecycle.assert_selected("eu")["publication_id"])

    def test_two_connections_cannot_begin_overlapping_transitions(self):
        lifecycle = self.store(SQLiteMeetLifecycle)
        lifecycle.select("eu", "meet-a", "v1")
        barrier = threading.Barrier(2)
        results = []
        def select(region):
            connection = SQLiteMeetLifecycle(self.path)
            try:
                barrier.wait(5)
                connection.begin_transition(region, "meet-a", "v2", allow_switch=True)
                results.append("started")
            except MeetLifecycleError:
                results.append("blocked")
            finally:
                connection.close()
        workers = [threading.Thread(target=select, args=(r,)) for r in ("eu", "us")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertCountEqual(["started", "blocked"], results)

    def test_legacy_ambiguity_is_not_resolved_by_paused_or_closed_status(self):
        lifecycle = self.store(SQLiteMeetLifecycle)
        eu = RuntimePublication.parse(runtime_package_v3())
        us = {"status": "closed", "package": us_package()}
        with self.assertRaisesRegex(MeetLifecycleError, "Både EU"):
            lifecycle.bootstrap(eu, us)
        self.assertIsNone(lifecycle.selected())
        self.assertEqual("us", lifecycle.bootstrap(us_session=us)["region"])
        self.assertNotEqual(us_meet_id(us_package()), us_meet_id({**us_package(), "publication_id": "other"}))

    def test_download_is_not_a_clock_or_selected_meet_change(self):
        store = self.store(SQLiteRuntimeStore)
        operations = self.store(SQLiteOperationsStore)
        lifecycle = self.store(SQLiteMeetLifecycle)
        first = store.install(runtime_package_v3())
        operations.ensure_publication(first)
        lifecycle.bootstrap(first)
        operations.start_clock(time_value="11:12:13")
        clock_before = operations._connection.execute("SELECT * FROM runtime_clock").fetchone()
        selected_before = lifecycle.selected()
        next_package = deepcopy(first.payload)
        next_package["publication_id"] = "v2"
        next_package["clock"].update(start_time="01:00", speed=20)
        store.stage_pending(next_package)
        self.assertEqual(clock_before, operations._connection.execute("SELECT * FROM runtime_clock").fetchone())
        self.assertEqual(selected_before, lifecycle.selected())
        self.assertEqual(first.publication_id, store.active().publication_id)

    def test_eu_adoption_preserves_live_clock_exactly_and_remaps_readiness(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        base = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
        operations.start_clock(time_value="11:12:13", now=base)
        movement = first.payload["trains"][0]
        operations.set_train_readiness(first.publication_id, first.active_day,
                                       movement["station_id"], movement["id"], None,
                                       action="ready", actor="Benny", actor_role="tkl", shift_id=None)
        before = operations.clock_status(now=base + timedelta(seconds=20))
        newer = deepcopy(first.payload)
        newer["publication_id"] = "v2"
        newer["clock"].update(start_time="01:00", speed=25)
        for i, row in enumerate(newer["trains"]):
            row["id"] = f"new-{i}"
        second = RuntimePublication.parse(newer)
        operations.adopt_publication(first, second)
        after = operations.clock_status(now=base + timedelta(seconds=20))
        self.assertEqual({**before, "publication_id": "v2"}, after)
        readiness = operations.train_readiness("v2", first.active_day, movement["station_id"])
        self.assertEqual("new-0", readiness[0]["movement_id"])
        self.assertEqual("Benny", readiness[0]["prepared_by"])
        # Reapplying exactly the current publication does not alter the clock.
        operations.adopt_publication(second, second)
        self.assertEqual(after, operations.clock_status(now=base + timedelta(seconds=20)))
        self.assertEqual(readiness, operations.train_readiness("v2", first.active_day, movement["station_id"]))

    def test_eu_open_clearance_blocks_even_if_clock_is_stopped(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        next_value = deepcopy(first.payload)
        next_value["publication_id"] = "v2"
        second = RuntimePublication.parse(next_value)
        operations.request_clearance(first.publication_id, first.active_day,
            clearance_id="c1", movement_id=first.payload["trains"][0]["id"],
            connection_id="connection-a-b", channel_id="ab", from_station_id="station-a",
            to_station_id="station-b", track_id=None, requested_by="tkl", ttl_seconds=60)
        self.assertTrue(operations.config_update_blockers(first, first))
        with self.assertRaises(RuntimePublicationError):
            operations.adopt_publication(first, second)
        self.assertEqual(first.publication_id, operations.clock_status()["publication_id"])
        operations.settle_clearance("c1", "cancelled", "tkl")
        operations.adopt_publication(first, second)
        self.assertEqual("v2", operations.clock_status()["publication_id"])

    def test_reactivated_publication_uses_current_readiness_not_old_snapshot(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        movement = first.payload["trains"][0]
        operations.set_train_readiness(first.publication_id, first.active_day,
            movement["station_id"], movement["id"], None,
            action="ready", actor="crew", actor_role="ranger", shift_id=None)
        newer = deepcopy(first.payload)
        newer["publication_id"] = "readiness-v2"
        newer["trains"][0]["id"] = "reimported-movement"
        second = RuntimePublication.parse(newer)
        operations.adopt_publication(first, second)
        operations.set_train_readiness(second.publication_id, first.active_day,
            movement["station_id"], "reimported-movement", None,
            action="revoke", actor="crew", actor_role="ranger", shift_id=None)
        events_before = operations._connection.execute("SELECT COUNT(*) FROM tkl_events").fetchone()[0]
        operations.adopt_publication(second, first)
        readiness = operations.train_readiness(first.publication_id, first.active_day, movement["station_id"])
        self.assertEqual("revoked", readiness[0]["status"])
        self.assertEqual(movement["id"], readiness[0]["movement_id"])
        self.assertEqual("crew", readiness[0]["revoked_by"])
        self.assertEqual(events_before, operations._connection.execute("SELECT COUNT(*) FROM tkl_events").fetchone()[0])

    def test_reactivated_publication_does_not_resurrect_absent_readiness(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        movement = first.payload["trains"][0]
        operations.set_train_readiness(first.publication_id, first.active_day,
            movement["station_id"], movement["id"], None,
            action="ready", actor="crew", actor_role="ranger", shift_id=None)
        second = RuntimePublication.parse({**deepcopy(first.payload), "publication_id": "readiness-v2"})
        operations.adopt_publication(first, second)
        with operations._connection:
            operations._connection.execute("DELETE FROM train_readiness WHERE publication_id=?", (second.publication_id,))
        operations.adopt_publication(second, first)
        self.assertEqual([], operations.train_readiness(first.publication_id, first.active_day, movement["station_id"]))

    def test_eu_paused_clock_and_reason_survive_new_config_defaults(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        operations.start_clock(time_value="12:34:56")
        operations.stop_clock("Lunch")
        before = operations.clock_status()
        newer = deepcopy(first.payload)
        newer["publication_id"] = "v2"
        newer["clock"].update(start_time="01:00", speed=99)
        operations.adopt_publication(first, RuntimePublication.parse(newer))
        later = operations.clock_status(now=datetime.now(timezone.utc) + timedelta(days=2))
        self.assertEqual({**before, "publication_id": "v2"}, later)

    def test_reactivated_publication_replaces_current_movement_state_including_absence(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        movement = first.payload["trains"][0]
        operations.update_tkl_movement(first.publication_id, first.active_day,
            movement["station_id"], movement["id"], arrival="arrived", departure="none",
            actual_track="2", updated_by="tkl", shift_id=None, event_type="test")
        second = RuntimePublication.parse({**deepcopy(first.payload), "publication_id": "movement-v2"})
        operations.adopt_publication(first, second)
        operations.update_tkl_movement(second.publication_id, first.active_day,
            movement["station_id"], movement["id"], arrival="arrived", departure="departed",
            actual_track="1", updated_by="current-tkl", shift_id=None, event_type="test")
        operations.adopt_publication(second, first)
        current = operations.tkl_station_state(first.publication_id, first.active_day, movement["station_id"])["movements"]
        self.assertEqual("departed", current[movement["id"]]["departure"])
        self.assertEqual("1", current[movement["id"]]["actualTrack"])
        operations.adopt_publication(first, second)
        with operations._connection:
            operations._connection.execute("DELETE FROM tkl_movement_states WHERE publication_id=?", (second.publication_id,))
        events = operations._connection.execute("SELECT COUNT(*) FROM tkl_events").fetchone()[0]
        operations.adopt_publication(second, first)
        self.assertEqual({}, operations.tkl_station_state(first.publication_id, first.active_day, movement["station_id"])["movements"])
        self.assertEqual(events, operations._connection.execute("SELECT COUNT(*) FROM tkl_events").fetchone()[0])

    def test_new_operating_selection_cannot_resurrect_archived_publication(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.start_meet(first)
        movement = first.payload["trains"][0]
        operations.update_tkl_movement(first.publication_id, first.active_day,
            movement["station_id"], movement["id"], arrival="arrived", departure="none",
            actual_track="2", updated_by="tkl", shift_id=None, event_type="test")
        newer = deepcopy(first.payload)
        newer["publication_id"] = "other-meet-publication"
        newer["meet"]["id"] = "other-meet"
        second = RuntimePublication.parse(newer)
        operations.start_meet(second)
        clock_before = operations.clock_status()
        archived_before = operations.tkl_station_state(first.publication_id, first.active_day, movement["station_id"])
        self.assertTrue(operations.start_meet_blockers(first))
        self.assertEqual([], operations.start_meet_blockers(RuntimePublication.parse({**first.payload, "publication_id": "fresh-publication"})))
        with self.assertRaisesRegex(RuntimePublicationError, "Publicera en ny configversion"):
            operations.start_meet(first)
        self.assertEqual(clock_before, operations.clock_status())
        self.assertEqual(archived_before, operations.tkl_station_state(first.publication_id, first.active_day, movement["station_id"]))

    def test_same_meet_default_day_change_preserves_operating_day_and_state(self):
        runtime = self.store(SQLiteRuntimeStore)
        operations = self.store(SQLiteOperationsStore)
        first = runtime.install(runtime_package_v3())
        operations.ensure_publication(first)
        runtime.set_active_day("Sön")
        movement = first.payload["trains"][0]
        operations.set_train_readiness(first.publication_id, "Sön", movement["station_id"],
            movement["id"], None, action="ready", actor="tkl", actor_role="tkl", shift_id=None)
        newer = deepcopy(first.payload)
        newer["publication_id"] = "new-day-default"
        newer["meet"]["active_day"] = "Mån"
        second = runtime.install(newer, activate=False)
        self.assertEqual([], operations.config_update_blockers(first, second))
        operations.adopt_publication(first, second)
        runtime.activate(second.publication_id, preserve_active_day=True)
        self.assertEqual("Sön", runtime.active_day())
        self.assertEqual("acknowledged", operations.train_readiness(second.publication_id, "Sön", movement["station_id"])[0]["status"])
        runtime.activate(first.publication_id)
        self.assertEqual(first.active_day, runtime.active_day())

    def test_eu_removed_actual_track_is_blocked(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        movement = first.payload["trains"][0]
        operations.update_tkl_movement(first.publication_id, first.active_day,
            movement["station_id"], movement["id"], arrival="arrived", departure="none",
            actual_track="2", updated_by="tkl", shift_id=None, event_type="test")
        newer = deepcopy(first.payload)
        newer["publication_id"] = "v2"
        newer["tracks"] = [track for track in newer["tracks"] if track["id"] != "track-station-a-2"]
        for row in newer["trains"]:
            if row.get("track_id") == "track-station-a-2":
                row["track_id"] = "track-station-a-1"
        with self.assertRaisesRegex(RuntimePublicationError, "spår med registrerat"):
            operations.adopt_publication(first, RuntimePublication.parse(newer))

    def test_eu_removed_operational_movement_is_not_silently_lost(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        movement = first.payload["trains"][0]
        operations.set_train_readiness(first.publication_id, first.active_day,
            movement["station_id"], movement["id"], None, action="ready", actor="tkl", actor_role="tkl", shift_id=None)
        newer = deepcopy(first.payload)
        newer.update(publication_id="v2", trains=newer["trains"][1:])
        with self.assertRaisesRegex(RuntimePublicationError, "driftuppgifter"):
            operations.adopt_publication(first, RuntimePublication.parse(newer))

    def test_deactivate_preserves_publication_and_history(self):
        runtime = self.store(SQLiteRuntimeStore)
        first = runtime.install(runtime_package_v3())
        runtime.deactivate()
        self.assertIsNone(runtime.active())
        self.assertEqual(first, runtime.publication(first.publication_id))

    def test_explicit_eu_meet_switch_does_not_carry_over_same_station_train_ids(self):
        operations = self.store(SQLiteOperationsStore)
        first = RuntimePublication.parse(runtime_package_v3())
        operations.ensure_publication(first)
        operations._upsert_position("101", status="station", station_id="station-a")
        movement = first.payload["trains"][0]
        operations.update_tkl_movement(first.publication_id, first.active_day,
            movement["station_id"], movement["id"], arrival="arrived", departure="none",
            actual_track="2", updated_by="tkl", shift_id=None, event_type="test")
        newer = deepcopy(first.payload)
        newer["publication_id"] = "other-meet-publication"
        newer["meet"]["id"] = "other-meet"
        operations.start_meet(RuntimePublication.parse(newer))
        self.assertEqual([], operations.positions())
        self.assertFalse(operations.clock_status()["running"])
        self.assertEqual({}, operations.tkl_station_state(newer["publication_id"], first.active_day, "station-a")["movements"])
        self.assertTrue(operations.tkl_station_state(first.publication_id, first.active_day, "station-a")["movements"])
        self.assertEqual(1, operations._connection.execute("SELECT COUNT(*) FROM runtime_meet_archives").fetchone()[0])


class USConfigAdoptionTests(CoreStoreCase):
    def setUp(self):
        super().setUp()
        self.us = self.store(USStore)
        self.package = us_package()
        self.package["session"] = {"id": "meet-us", "clock_time": "05:30", "clock_speed": 4}
        self.us.stage_package(self.package)
        self.command("create_session", package=self.package)

    def current(self):
        return self.us.current_session()

    def command(self, action, actor="dispatcher", **data):
        current = self.current()
        if current:
            data.update(session_id=current["id"], expected_revision=current["revision"])
        return self.us.execute(actor, actor == "dispatcher", action,
                               {"command_id": str(uuid4()), **data}, "05:30")

    def newer(self):
        value = deepcopy(self.package)
        value["publication_id"] = "v2"
        return value

    def test_us_adoption_preserves_assignment_run_ids_clock_and_extras(self):
        run_id = self.current()["runs"][0]["id"]
        self.command("assign", run_id=run_id, conductor_id="crew", conductor_name="Benny")
        self.command("extra", symbol="Extra 99", direction="west")
        self.command("ready", actor="crew", run_id=run_id)
        before = self.current()
        newer = self.newer()
        newer["session"]["clock_time"] = "12:00"
        newer["runs"][0]["schedule"] = [{"node_id": "a", "time": "08:00"}]
        newer["runs"].append({"id": "second", "symbol": "835", "direction": "west", "schedule": []})
        self.us.stage_package(newer)
        self.us.adopt_package(newer, expected_revision=before["revision"])
        after = self.current()
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["clock"], after["clock"])
        self.assertEqual(run_id, after["runs"][0]["id"])
        self.assertEqual("crew", after["runs"][0]["conductor_id"])
        self.assertTrue(after["runs"][0]["ready"])
        self.assertEqual(before["runs"][1], after["runs"][2])
        with self.assertRaisesRegex(USError, "session changed"):
            self.us.adopt_package(newer, expected_revision=before["revision"])

    def test_us_warrant_blocks_topology_but_allows_diagram_coordinates(self):
        run = self.current()["runs"][0]["id"]
        self.command("draft", run_id=run, kind="proceed", path=[{"segment_id": "main", "from_mp": 10, "to_mp": 20}])
        before = self.current()
        newer = self.newer()
        newer["nodes"][0]["mp"] = 9
        self.us.stage_package(newer)
        with self.assertRaisesRegex(USError, "track warrants"):
            self.us.adopt_package(newer)
        self.assertEqual(before, self.current())
        newer = self.newer()
        newer["publication_id"] = "diagram-v2"
        newer["nodes"][0]["x"] = 70
        self.us.stage_package(newer)
        self.us.adopt_package(newer)
        self.assertEqual(before["warrants"], self.current()["warrants"])
        self.assertEqual(before["clock"], self.current()["clock"])

    def test_us_different_meet_or_removal_of_assigned_train_is_blocked(self):
        run = self.current()["runs"][0]["id"]
        self.command("assign", run_id=run, conductor_id="crew", conductor_name="Benny")
        for variant in ("another-meet", "missing-run"):
            newer = self.newer()
            if variant == "another-meet":
                newer["session"]["id"] = "other"
            else:
                newer["runs"] = []
            self.assertTrue(self.us.config_update_blockers(newer))

    def test_us_download_never_modifies_session_and_old_session_cannot_command(self):
        before = self.current()
        self.us.stage_package(self.newer())
        self.assertEqual(before, self.current())
        self.us.deactivate()
        self.assertIsNone(self.current())
        with self.assertRaisesRegex(USError, "previous operating session"):
            self.us.execute("dispatcher", True, "clock", {"command_id": "stale", "session_id": before["id"],
                "expected_revision": before["revision"], "confirmed": True, "running": True}, "05:30")
        self.assertEqual(before, self.us._load(before["id"]))
