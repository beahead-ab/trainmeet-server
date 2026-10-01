"""Klienter visar om varje box lever.

Benny kunde inte se om en box var vid liv. Han tog bort alla för att "starta
om" dem och fick dem sedan inte tillbaka, eftersom en borttagen box
spärras. Boxen pingar var femte sekund. Servern noterar det i minnet och visar
den som Online, som "ingen kontakt" så fort den tystnar, och som offline efter
en kvart. En borttagen box som fortsätter försöka ansluta visas med en knapp
för att släppa in den igen.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from runtime_fixture import runtime_package_v3
from tmbox_gateway.central_sync import CentralRuntimeDownload
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import IdentityStore, PairingService
from tmbox_gateway.local_server import attach_terminal_gateway
from tmbox_gateway.models import unconfigured_session
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.us import USStore

BOX = "esp8266-a1b2c3d4e5f6"
# Decided with Casper: online up to 20 s after the last ping, offline after a quarter of an hour.
ONLINE_SECONDS = 20
OFFLINE_SECONDS = 15 * 60


class DeviceConnectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "test.db"
        self.runtime = SQLiteRuntimeStore(path)
        self.operations = SQLiteOperationsStore(path)
        self.identities = IdentityStore(path)
        self.us = USStore(path)
        for store in (self.runtime, self.operations, self.identities, self.us):
            self.addCleanup(store.close)
        package = runtime_package_v3(publication_id="first")
        self.app = TrainMeetHTTPApplication(TrafficEngine(unconfigured_session()), self.identities,
            PairingService(self.identities, set()), HTTPServerConfig(), runtime_store=self.runtime,
            operations_store=self.operations, us_store=self.us,
            runtime_fetcher=lambda code, url: CentralRuntimeDownload(copy.deepcopy(package), "test-link"))
        self.addCleanup(self.app.lifecycle.close)
        self.admin = self.app.local_admin()
        self.app.sync_runtime(self.admin, {"sync_code": "123456"})
        self.clock = 1_800_000_000.0
        self.app.wall_clock = lambda: self.clock
        # The same wiring as the running server.
        self.published = []
        transport = SimpleNamespace(publish=lambda topic, body, retain: self.published.append((topic, body)))
        self.gateway = attach_terminal_gateway(self.app, transport, SimpleNamespace(subscribe=lambda callback: None))
        self.assertIs(self.gateway, transport.terminal_gateway)

    def message(self, leaf, **body):
        self.gateway.receive(self.gateway.PREFIX + BOX + "/" + leaf,
                             json.dumps({"boot": "boot-1", **body}).encode())

    def box(self, device_id=BOX):
        devices = self.app.devices(self.admin)
        return next(d for d in devices["devices"] if d["device_id"] == device_id)

    def test_a_pinging_box_is_online_then_lost_then_offline(self):
        self.message("hello", device_code="TBX-A1B2C3")
        self.identities.record_discovery(BOX, "TBX-A1B2C3")  # what the worker does on hello
        self.assertEqual({"state": "online", "last_seen": None}, self.box()["connection"])
        for _ in range(4):  # a ping every five seconds keeps it online
            self.clock += 5
            self.message("presence", nonce="n")
        heard = self.clock
        self.assertEqual("online", self.box()["connection"]["state"])
        self.clock = heard + ONLINE_SECONDS
        self.assertEqual("online", self.box()["connection"]["state"])
        self.clock = heard + ONLINE_SECONDS + 1
        lost = self.box()["connection"]
        self.assertEqual("lost", lost["state"])
        self.assertEqual(datetime.fromtimestamp(heard, timezone.utc).isoformat(), lost["last_seen"])
        self.clock = heard + OFFLINE_SECONDS
        self.assertEqual("lost", self.box()["connection"]["state"], "still 'no contact' at fifteen minutes")
        self.clock = heard + OFFLINE_SECONDS + 1
        self.assertEqual("offline", self.box()["connection"]["state"])
        self.message("presence", nonce="back")  # same session: welcome back
        self.assertEqual("online", self.box()["connection"]["state"])

    def test_a_ping_from_an_unknown_session_does_not_count(self):
        self.identities.record_discovery(BOX, "TBX-A1B2C3", now=datetime.fromtimestamp(self.clock - 3600, timezone.utc))
        self.message("presence", nonce="n")  # after a restart: the box must say hello first
        self.assertEqual("offline", self.box()["connection"]["state"])

    def test_after_a_restart_the_stored_connect_time_counts_until_a_ping(self):
        # Connected five seconds before the restart: still not "online"
        # until it has pinged this server process.
        self.identities.record_discovery(BOX, "TBX-A1B2C3", now=datetime.fromtimestamp(self.clock - 5, timezone.utc))
        connection = self.box()["connection"]
        self.assertEqual("lost", connection["state"], "never 'online' without a ping since start")
        self.assertEqual(datetime.fromtimestamp(self.clock - 5, timezone.utc).isoformat(), connection["last_seen"])

    def test_a_removed_box_that_keeps_trying_is_offered_back(self):
        self.message("hello", device_code="TBX-A1B2C3")
        self.identities.record_discovery(BOX, "TBX-A1B2C3")
        self.identities.remove_discovered_device(BOX)
        devices = self.app.devices(self.admin)
        self.assertNotIn(BOX, [d["device_id"] for d in devices["devices"]])
        self.assertEqual([BOX], [d["device_id"] for d in devices["removed_trying"]])
        self.assertEqual("TBX-A1B2C3", devices["removed_trying"][0]["device_code"])
        # A box removed long ago and silent since is not offered.
        self.clock += OFFLINE_SECONDS + 1
        self.assertEqual([], self.app.devices(self.admin)["removed_trying"])
        # Reconnecting by its code brings it back into the list proper.
        self.clock -= OFFLINE_SECONDS + 1
        station = next(iter(self.app.engine.config.stations))
        self.identities.assign_discovered_device("TBX-A1B2C3", station_id=station)
        devices = self.app.devices(self.admin)
        self.assertIn(BOX, [d["device_id"] for d in devices["devices"]])
        self.assertEqual([], devices["removed_trying"])

    def frames(self):
        return [body["frame"] for topic, body in self.published if topic == self.gateway.PREFIX + BOX + "/frame"]

    def test_a_box_is_sent_its_new_station_and_language_at_once(self):
        """A 16x2 box learns its station, side and language only from its
        frame (2.0.0: no tmbox/v2 assignment any more). Set in Klienter, the
        new frame goes out at once, not at the box's next ping."""
        hello = self.gateway.receive(self.gateway.PREFIX + BOX + "/hello",
                                     json.dumps({"boot": "boot-1", "device_code": "TBX-A1B2C3"}).encode())
        self.gateway.handle(hello)
        self.assertEqual(1, len(self.frames()), "the box is shown a frame when it connects")
        station = next(iter(self.app.engine.config.stations.values()))
        self.app.assign_device(self.admin, {"device_code": "TBX-A1B2C3", "station_id": station.id})
        self.assertEqual(2, len(self.frames()))
        self.assertEqual(station.code, self.frames()[-1]["station_code"])
        self.app.set_device_language(self.admin, {"device_id": BOX, "language": "en"})
        self.assertEqual(3, len(self.frames()))
        self.assertEqual("en", self.frames()[-1]["language"])
        # Nothing changed, nothing sent: the nudge is not a flood.
        self.app.on_clock_changed()
        self.app.on_config_applied()
        self.assertEqual(3, len(self.frames()))


if __name__ == "__main__":
    unittest.main()
