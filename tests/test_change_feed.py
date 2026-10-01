"""Allt uppdateras direkt: GET /v1/events säger att något ändrats.

Casper (2026-10-01): "Alla UI ska uppdateras när det sker en ändring
någonstans, t.ex. trafik." Drift, skärmarna och deltagarvyn frågade servern
var femte sekund (skärmarna varje sekund). Nu säger servern till direkt vad
som ändrats – trafik, klocka, boxar, träffen eller simuleringen – och sidan
hämtar om just det. Strömmen bär bara ämnesnamn, aldrig data, så den kräver
ingen inloggning.
"""
from contextlib import contextmanager
import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

import test_shared_traffic
import test_simulation
import test_us_http
from tmbox_gateway import change_feed, http_server
from tmbox_gateway.change_feed import ChangeFeed
from tmbox_gateway.http_server import HTTPAPIError, TrainMeetHTTPServer, TrainMeetRequestHandler
from tmbox_gateway.identity import DeviceKind, PairedClient


class ChangeFeedTests(unittest.TestCase):
    def test_topics_since_a_point_merge_and_stay_apart_from_older_ones(self):
        feed = ChangeFeed()
        feed.notify("traffic")
        first = feed.seq
        feed.notify("clock")
        feed.notify("traffic", "devices")
        self.assertEqual((3, ["clock", "devices", "traffic"]), feed.wait(first, 0))
        self.assertEqual((3, ["clock", "devices", "traffic"]), feed.wait(first, 0), "reading takes nothing away")
        self.assertEqual((3, ["devices", "traffic"]), feed.wait(2, 0))
        self.assertEqual((3, []), feed.wait(3, 0))

    def test_waiting_ends_on_a_change_or_after_the_timeout(self):
        feed = ChangeFeed()
        started = time.monotonic()
        self.assertEqual((0, []), feed.wait(0, 0.2))
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        results = []
        waiters = [threading.Thread(target=lambda: results.append(feed.wait(0, 10))) for _ in range(3)]
        for waiter in waiters:
            waiter.start()
        time.sleep(0.1)
        started = time.monotonic()
        feed.notify("runtime")  # never waits for the pages that are waiting
        self.assertLess(time.monotonic() - started, 0.05)
        for waiter in waiters:
            waiter.join(2)
        self.assertEqual([(1, ["runtime"])] * 3, results)

    def test_only_known_topics(self):
        feed = ChangeFeed()
        for topics in ((), ("trafik",), ("traffic", "secret")):
            with self.assertRaises(ValueError):
                feed.notify(*topics)
        self.assertEqual(0, feed.seq)

    def test_closing_wakes_every_stream(self):
        feed = ChangeFeed()
        done = []
        waiter = threading.Thread(target=lambda: done.append(feed.wait(0, 10)))
        waiter.start()
        time.sleep(0.05)
        feed.close()
        waiter.join(2)
        self.assertEqual([(0, [])], done)
        self.assertTrue(feed.closed)

    def test_streams_per_address_and_in_total(self):
        feed = ChangeFeed()
        self.assertEqual((48, 6), (change_feed.STREAMS_TOTAL, change_feed.STREAMS_PER_ADDRESS))
        for _ in range(6):
            self.assertTrue(feed.admit("10.0.0.2"))
        self.assertFalse(feed.admit("10.0.0.2"))
        feed.release("10.0.0.2")
        self.assertTrue(feed.admit("10.0.0.2"))
        for index in range(42):
            self.assertTrue(feed.admit(f"10.0.1.{index}"))
        self.assertFalse(feed.admit("10.0.2.1"), "48 in all")
        feed.release("10.0.1.0")
        self.assertTrue(feed.admit("10.0.2.1"))
        for _ in range(6):
            feed.release("10.0.0.2")
        self.assertNotIn("10.0.0.2", feed._streams, "a released address is forgotten")


class WhatChangesTests(unittest.TestCase):
    """Each kind of change says so, after it is done."""

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app, self.admin = self.fixture.app, self.fixture.admin
        self.generation = lambda: self.app.lifecycle.selected()["generation"]

    @contextmanager
    def announces(self, *expected):
        since = self.app.changes.seq
        yield
        _, topics = self.app.changes.wait(since, 0)
        for topic in expected:
            self.assertIn(topic, topics)

    def test_traffic_from_a_box_and_from_tkl(self):
        with self.announces("traffic"):
            case = self.fixture.request_v1()
        with self.announces("traffic"):
            self.fixture.approve(case)

    def test_traffic_is_announced_only_once_committed(self):
        seen = []
        self.app.station_service.subscribe(lambda: seen.append(self.app.changes.seq))
        before = self.app.changes.seq
        with self.app.operations_store.atomic_command():
            self.fixture.request_v1()
            self.assertEqual(before, self.app.changes.seq, "nothing before the commit")
        self.assertGreater(self.app.changes.seq, before)

    def test_the_clock_started_stopped_and_set(self):
        for payload in ({"action": "start", "time": "09:00:00", "speed": 2}, {"action": "speed", "speed": 4},
                        {"action": "stop"}, {"action": "appearance", "style": "digital", "show_seconds": False}):
            with self.subTest(action=payload["action"]), self.announces("clock"):
                self.app.control_clock(self.admin, {**payload, "meet_generation": self.generation()})

    def test_boxes_assigned_removed_and_their_language(self):
        ids = self.fixture.ids
        ids.record_discovery("box-new", "TBX-NEWBOX", protocol_version=2)
        with self.announces("devices"):
            self.app.assign_device(self.admin, {"device_code": "TBX-NEWBOX", "station_id": "station-a"})
        with self.announces("devices"):
            self.app.set_device_language(self.admin, {"device_id": "box-new", "language": "en"})
        with self.announces("devices"):
            self.app.remove_device(self.admin, {"device_id": "box-new"})

    def test_a_new_browser_box_and_a_box_that_enrolls(self):
        with self.announces("devices"):
            self.app.create_browser_client({"workspace": "tmbox"}, "10.0.0.7")
        ids = self.fixture.ids
        ids.record_discovery("esp8266-0a1b2c3d4e5f", "TBX-ENROLL", protocol_version=1)
        ids.assign_discovered_device("TBX-ENROLL", station_id="station-a")
        code = ids.issue_pairing_code(["panel-a"], code="654321", allowed_kinds=[DeviceKind.ESP32_PANEL])
        with self.announces("devices"):
            self.app.enroll_tmbox({"client_id": "esp8266-0a1b2c3d4e5f", "pairing_code": code}, "10.0.0.8")

    def test_a_box_choosing_its_own_language(self):
        box = PairedClient(client_id="esp32", display_name="esp32", kind=DeviceKind.ESP32_PANEL, panel_ids=())
        with self.announces("devices"):
            self.app.tmbox_preferences(box, {"language": "de"})
        since = self.app.changes.seq
        self.app.tmbox_preferences(box)
        self.assertEqual(since, self.app.changes.seq, "reading the preferences changes nothing")

    def test_a_rejected_change_announces_nothing(self):
        since = self.app.changes.seq
        with self.assertRaises(HTTPAPIError):
            self.app.control_clock(self.admin, {"action": "rewind", "meet_generation": self.generation()})
        with self.assertRaises(HTTPAPIError):
            self.app.set_device_language(self.admin, {"device_id": "no-such-box", "language": "en"})
        self.assertEqual(since, self.app.changes.seq)

    def test_left_and_right_on_a_station(self):
        placement = self.app.cloud_presentation(self.admin)
        station = placement["stations"][0]
        sides = {station["connections"][0]["connection_id"]: "right"}
        with self.announces("runtime", "traffic"):
            self.app.save_display_placement(self.admin, {"publication_id": placement["publication_id"],
                "config_version": placement["config_version"], "station_id": station["station_id"], "sides": sides})

    def test_a_box_going_quiet_and_coming_back_once_each(self):
        now = [1_800_000_000.0]
        self.app.wall_clock = lambda: now[0]
        feed = self.app.changes

        def check():
            since = feed.seq
            self.app.check_device_liveness()
            return feed.wait(since, 0)[1]

        self.assertEqual([], check(), "no box heard yet")
        self.app.note_device_seen("box-1")
        self.assertEqual(["devices"], check(), "online")
        for _ in range(3):
            now[0] += 5
            self.app.note_device_seen("box-1")
            self.assertEqual([], check(), "a ping changes nothing")
        now[0] += http_server.DEVICE_ONLINE_SECONDS
        self.assertEqual([], check(), "still online at the limit")
        now[0] += 1
        self.assertEqual(["devices"], check(), "lost")
        self.assertEqual([], check())
        now[0] += http_server.DEVICE_OFFLINE_SECONDS
        self.assertEqual(["devices"], check(), "offline")
        self.app.note_device_seen("box-1")
        self.assertEqual(["devices"], check(), "back online")

    def test_a_new_day_and_a_new_cloud_version(self):
        with self.announces("runtime", "traffic"):
            self.app.set_active_day(self.admin, {"active_day": "Sön", "meet_generation": self.generation()})
        package = test_shared_traffic.runtime_package_v3(publication_id="the-next-one")
        with self.announces("runtime", "traffic", "clock"):
            self.fixture.install(package)

    def test_cloud_auto_sync_switched(self):
        self.app.runtime_store.save_link_token("test-link")
        with self.announces("runtime"):
            self.app.configure_cloud_auto_sync(self.admin, {"enabled": False})


class SimulationChangesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_simulation.SimulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app = self.fixture.app

    def topics(self, action):
        since = self.app.changes.seq
        action()
        return self.app.changes.wait(since, 0)[1]

    def test_start_and_pause(self):
        self.assertEqual({"clock", "runtime", "simulation", "traffic"},
                         set(self.topics(lambda: self.fixture.command("start", confirmed=True, profile="timetable", time="09:17", speed=1))))
        self.assertIn("simulation", self.topics(lambda: self.fixture.command("pause")))

    def test_a_request_the_simulator_lets_expire(self):
        """A box asks, nobody answers, the simulator lets the request expire
        after five game minutes: no command, so the simulator says so."""
        sim, service = self.fixture.sim, self.fixture.service
        asking, answering = self.fixture.register("box-a", "station-a"), self.fixture.register()
        self.fixture.start()
        for box in (asking, answering):
            service.observe_operator(box)  # both stations manual: the simulator acts for neither
        self.fixture.advance(9 * 3600 + 18 * 60)
        service.execute_station_command(asking, "station-a", "clearance.request",
            {"movement_id": "movement-101-a", "connection_id": "connection-a-b"})
        sim.tick()
        case = service.open_cases(None)[0]
        self.assertEqual("waiting", case["status"])
        self.assertIn("traffic", self.topics(lambda: self.fixture.advance(9 * 3600 + 24 * 60)))
        self.assertEqual("expired", self.fixture.ops.clearance(case["clearance_id"])["status"])
        self.assertEqual([], service.open_cases(None), "and asks nothing new")


    def test_a_train_the_simulator_stables(self):
        self.fixture.start(time="09:25")
        self.fixture.advance(9 * 3600 + 35 * 60 + 1)
        self.assertIn("traffic", self.topics(lambda: self.fixture.advance(9 * 3600 + 40 * 60 + 2)))
        self.assertIn("movement-101-a", self.fixture.sim.run["stabled"])


class USChangesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_us_http.USHTTPTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_a_us_command(self):
        app = self.fixture.app
        since = app.changes.seq
        app.us_command(app.local_admin(), self.fixture.start_command())
        self.assertEqual(["clock", "traffic"], app.changes.wait(since, 0)[1])


class EventStreamTests(unittest.TestCase):
    """GET /v1/events on a running server."""

    def setUp(self):
        self.fixture = test_shared_traffic.SharedTrafficTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app = self.fixture.app
        self.server = TrainMeetHTTPServer(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.app.changes.close()
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)
        self.addCleanup(stop)

    def open(self, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("GET", "/v1/events", headers=headers or {})
        response = connection.getresponse()
        self.addCleanup(connection.close)
        return response

    def event(self, response):
        lines = []
        while True:
            line = response.readline().decode()
            if not line:
                return lines
            if line == "\n":
                if lines:
                    return lines
                continue
            lines.append(line.rstrip("\n"))

    def wait_streams(self, count):
        deadline = time.monotonic() + 3
        while sum(self.app.changes._streams.values()) != count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(count, sum(self.app.changes._streams.values()))

    def test_a_page_hears_a_traffic_command_at_once_without_signing_in(self):
        response = self.open()
        self.assertEqual(200, response.status)
        self.assertEqual("text/event-stream; charset=utf-8", response.getheader("Content-Type"))
        self.assertEqual("no-store", response.getheader("Cache-Control"))
        self.assertEqual("no", response.getheader("X-Accel-Buffering"))
        self.assertIsNone(response.getheader("Set-Cookie"))
        hello = self.event(response)
        self.assertEqual(["retry: 2000", "event: hello"], hello[:2])
        data = json.loads(hello[2][len("data: "):])
        self.assertEqual({"boot": self.app.changes.boot, "seq": 0}, data, "a new server is told apart from a restarted one")
        self.assertRegex(data["boot"], "^[0-9a-f]{8}$")
        started = time.monotonic()
        threading.Timer(0.1, self.fixture.request_v1).start()
        event = self.event(response)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual("event: change", event[0])
        data = json.loads(event[1][len("data: "):])
        self.assertEqual(["traffic"], data["topics"], "names only, never data")
        self.assertEqual({"seq", "topics"}, set(data))

    def test_a_quiet_stream_keeps_beating_and_ends_after_its_lifetime(self):
        self.assertEqual((15, 300, 20), (http_server.EVENT_HEARTBEAT_SECONDS, http_server.EVENT_STREAM_SECONDS,
                                         http_server.EVENT_WRITE_TIMEOUT_SECONDS))
        with patch.object(http_server, "EVENT_HEARTBEAT_SECONDS", 0.2), patch.object(http_server, "EVENT_STREAM_SECONDS", 1.0):
            response = self.open()
            self.event(response)
            self.assertEqual(": ping", response.readline().decode().rstrip("\n"))
            started = time.monotonic()
            while response.readline():
                pass
            self.assertLess(time.monotonic() - started, 2, "ends; the browser opens a new one")
        self.wait_streams(0)

    def test_six_per_address_then_429_and_a_closed_tab_frees_its_place(self):
        streams = [self.open() for _ in range(6)]
        for response in streams:
            self.assertEqual(200, response.status)
        refused = self.open()
        self.assertEqual(429, refused.status)
        self.assertEqual("30", refused.getheader("Retry-After"))
        self.assertEqual("too_many_streams", json.loads(refused.read())["error"])
        self.wait_streams(6)
        streams[0].fp.raw._sock.shutdown(socket.SHUT_RDWR)  # the tab closes
        self.wait_streams(5)
        self.assertEqual(200, self.open().status)

    def test_behind_the_proxy_each_page_counts_by_its_own_address(self):
        with patch.object(change_feed, "STREAMS_PER_ADDRESS", 1):
            self.assertEqual(200, self.open({"X-Forwarded-For": "203.0.113.9, 192.168.1.20"}).status)
            # The proxy appends the real address last; what a page sends first does not count.
            self.assertEqual(429, self.open({"X-Forwarded-For": "198.51.100.1, 192.168.1.20"}).status)
            self.assertEqual(200, self.open({"X-Forwarded-For": "192.168.1.21"}).status)
            self.assertEqual(200, self.open().status, "the proxy's own address is one more")

    def test_which_address_a_stream_counts_against(self):
        handler = TrainMeetRequestHandler.__new__(TrainMeetRequestHandler)
        for peer, forwarded, expected in (
                ("10.244.0.5", "203.0.113.9", "203.0.113.9"),   # an ingress in a cluster
                ("127.0.0.1", "203.0.113.9, 192.168.1.20", "192.168.1.20"),  # Caddy on the same host
                ("10.244.0.5", "", "10.244.0.5"),
                ("93.184.216.34", "198.51.100.1", "93.184.216.34")):  # a page on the Internet picks no address
            with self.subTest(peer=peer, forwarded=forwarded):
                handler.client_address = (peer, 40000)
                handler.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
                self.assertEqual(expected, handler._event_stream_address())

    def test_shutting_down_ends_every_stream(self):
        response = self.open()
        self.event(response)
        self.app.changes.close()
        started = time.monotonic()
        while response.readline():
            pass
        self.assertLess(time.monotonic() - started, 2)
        self.wait_streams(0)


if __name__ == "__main__":
    unittest.main()
