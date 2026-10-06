"""Ett trafikläge för hela trafikspelet: clearance eller direct.

Tidigare kunde en sträcka ha ett eget läge. TKL läste ändå bara träffens, så
TKL och servern kunde säga olika saker, och Cloud skrev ord som servern inte
godtog ("automatic", och "" för "Träffens standard"). Hela publiceringen
avvisades då. Proven visar att servern nu läser ett läge för hela
trafikspelet, att gamla paket går att installera och att alla vägar - motorn,
TMBox v2, 16x2 och tågets rutt - följer samma läge.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime_fixture import fictional_runtime_package, runtime_package_v3
from test_protocol_v2 import ProtocolV2Base
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.identity import IdentityStore
from tmbox_gateway.models import ConnectionConfig, ConnectionState, DispatchMode, dispatch_mode
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.protocol_v2 import TMBoxStationService
from tmbox_gateway.runtime import RuntimePublication, RuntimePublicationError, SQLiteRuntimeStore
from tmbox_gateway.train_routes import describe_departure


class ReadingTests(unittest.TestCase):
    def test_one_word_per_mode_and_clouds_older_word(self):
        self.assertEqual([DispatchMode.CLEARANCE, DispatchMode.DIRECT, DispatchMode.DIRECT, DispatchMode.CLEARANCE, DispatchMode.CLEARANCE],
                         [dispatch_mode(v) for v in ("clearance", "direct", "automatic", None, "")])
        with self.assertRaises(ValueError):
            dispatch_mode("fast")

    def test_a_section_has_no_mode_of_its_own(self):
        self.assertNotIn("dispatch_mode_override", ConnectionConfig.__dataclass_fields__)


class OldPackageTests(unittest.TestCase):
    """Paket som Cloud redan har publicerat, med orden servern förut avvisade."""

    def package(self, meet_mode, *section_modes):
        package = runtime_package_v3(publication_id="old-cloud")
        package["meet"]["default_dispatch_mode"] = meet_mode
        for connection, mode in zip(package["connections"], section_modes):
            connection["dispatch_mode_override"] = mode
        return package

    def test_clouds_automatic_and_empty_section_modes_install(self):
        """Regression: "automatic" och "" gav "Driftpaketet innehåller ogiltigt …" och ingen träff alls."""
        config = RuntimePublication.parse(self.package("automatic", "")).session_config()
        self.assertEqual(DispatchMode.DIRECT, config.default_dispatch_mode)

    def test_a_section_mode_in_an_old_package_is_not_followed(self):
        """Trafikspelet säger begär och bekräfta; en sträcka som en gång sattes till direct följer trafikspelet."""
        config = RuntimePublication.parse(self.package("clearance", "direct")).session_config()
        engine = TrafficEngine(config)
        connection = next(iter(config.connections.values()))
        ok, _ = engine.perform(station_id=connection.station_a_id, connection_id=connection.id, action="request", train_number="101")
        self.assertTrue(ok)
        self.assertEqual(ConnectionState.REQUESTED, engine.connections[connection.id].state)
        self.assertEqual("clearance", describe_departure(self.package("clearance", "direct"), "Lör", "station-a", "movement-101-a")["dispatch_mode"])

    def test_an_unknown_meet_mode_is_still_refused(self):
        with self.assertRaisesRegex(RuntimePublicationError, "ogiltigt default_dispatch_mode"):
            RuntimePublication.parse(self.package("fast"))


class BoxConfigTests(unittest.TestCase):
    """TMBox v2 får trafikspelets läge på varje sträcka, som schemat kräver fältet."""

    def test_every_section_carries_the_games_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime.db"
            runtime, operations, identities = SQLiteRuntimeStore(root), SQLiteOperationsStore(root), IdentityStore(root)
            try:
                package = fictional_runtime_package()
                package["meet"]["default_dispatch_mode"] = "direct"
                package["connections"][0]["dispatch_mode_override"] = "clearance"
                operations.ensure_publication(runtime.install(package))
                service = TMBoxStationService(runtime, operations, identities)
                stations = {connection[key] for connection in package["connections"] for key in ("station_a_id", "station_b_id")}
                modes = {row["dispatch_mode"] for station in stations for row in service.config_payload(station)["connections"]}
                self.assertEqual({"direct"}, modes)
            finally:
                identities.close(); operations.close(); runtime.close()


class BoxRequestTests(ProtocolV2Base):
    """En TMBox v2 som begär sträckan: trafikspelets läge avgör, inte sträckans gamla eget."""

    def install(self, meet_mode, section_mode):
        package = fictional_runtime_package()
        package["publication_id"] = f"box-{meet_mode}"
        package["meet"]["default_dispatch_mode"] = meet_mode
        for connection in package["connections"]:
            connection["dispatch_mode_override"] = section_mode
        self.operations_store.ensure_publication(self.runtime_store.install(package))

    def request(self):
        self._send("command", {"protocol_version": 2, "message_id": "req-direct", "device_id": "TMBOX-7A42F1",
                               "action": "clearance.request",
                               "payload": {"movement_id": "movement-421-cda", "connection_id": "connection-cda-vst"}})
        return self._acks()[-1]["snapshot"]["active_clearances"][0]["status"]

    def test_a_direct_game_grants_the_box_at_once(self):
        self.install("direct", "clearance")
        self.assertEqual("approved", self.request())

    def test_a_clearance_game_waits_for_the_neighbour(self):
        self.install("clearance", "direct")
        self.assertEqual("waiting", self.request())


if __name__ == "__main__":
    unittest.main()
