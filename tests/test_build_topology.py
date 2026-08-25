"""BYGG steg 2: banan som API och som vy.

Steget svarar på en enda fråga - hur ser banan ut? - och det ska den göra
likadant vare sig innehållet kommer från en Cloud-publicering eller ett lokalt
utkast. Skillnaden mellan de två är ett `locked`, inte två kodvägar.

`locked` kommer från servern. Testerna nedan finns för att ingen ska frestas
att räkna ut det i webbläsaren i stället: en vy som gissar rätt nio gånger av
tio erbjuder till slut redigering av ett paket som Cloud äger.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from runtime_fixture import runtime_package_v3
from session_fixture import sample_session
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPAPIError, HTTPServerConfig, TrainMeetHTTPApplication
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairedClient, PairingService
from tmbox_gateway.local_config import SQLiteLocalConfigurationStore
from tmbox_gateway.models import DispatchMode
from tmbox_gateway.runtime import SQLiteRuntimeStore

WEB = Path(__file__).resolve().parents[1] / "src" / "tmbox_gateway" / "web"


class BuildTopologyAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        database = root / "runtime.db"
        self.identities = IdentityStore(root / "identity.db")
        self.runtime = SQLiteRuntimeStore(database)
        self.local = SQLiteLocalConfigurationStore(database)
        engine = TrafficEngine(sample_session(DispatchMode.CLEARANCE))
        self.application = TrainMeetHTTPApplication(
            engine,
            self.identities,
            PairingService(self.identities, set(engine.config.panels)),
            HTTPServerConfig(local_development=True),
            runtime_store=self.runtime,
            local_configuration_store=self.local,
        )
        self.client = self.application.local_admin()

    def tearDown(self):
        self.local.close()
        self.runtime.close()
        self.directory.cleanup()

    def _cloud_linked_with_package(self, package: dict | None = None) -> dict:
        self.runtime.install(package or runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "cloud-linked"})
        return self.application.build_topology(self.client)

    # ------------------------------------------------- låst läge (Cloud)

    def test_a_cloud_package_comes_back_locked(self):
        topology = self._cloud_linked_with_package()
        self.assertTrue(topology["locked"])
        self.assertEqual("cloud", topology["source"])

    def test_the_cloud_package_stations_are_the_ones_returned(self):
        topology = self._cloud_linked_with_package()
        self.assertEqual(
            [("station-a", "CDA"), ("station-b", "LEK")],
            [(station["id"], station["code"]) for station in topology["stations"]],
        )

    def test_stations_come_back_in_the_order_cloud_drew_them(self):
        """diagram_order, not insertion order.

        The list *is* the line, so a package that arrives with its stations in
        some other order must still be read from one end of the line to the
        other. Reversing the payload must not reverse the view.
        """
        package = runtime_package_v3()
        package["stations"][0]["diagram_order"] = 5
        package["stations"][1]["diagram_order"] = 1
        topology = self._cloud_linked_with_package(package)
        self.assertEqual(
            ["station-b", "station-a"],
            [station["id"] for station in topology["stations"]],
        )
        self.assertEqual([1, 2], [station["order"] for station in topology["stations"]])

    def test_connections_and_panels_come_back_too(self):
        topology = self._cloud_linked_with_package()
        self.assertEqual(["connection-a-b"], [c["id"] for c in topology["connections"]])
        self.assertEqual(
            ["panel-a", "panel-b"], [panel["id"] for panel in topology["panels"]]
        )

    def test_a_panel_slot_carries_the_connection_id_not_a_station(self):
        """The two-step lookup the view has to do, pinned.

        A slot points at a *link*; the neighbour is that link's far end seen
        from the panel's own station. If this ever became a station id the
        view would still render - it would just quietly stop agreeing with
        the box on the table.
        """
        topology = self._cloud_linked_with_package()
        panel = next(p for p in topology["panels"] if p["id"] == "panel-a")
        self.assertEqual("connection-a-b", panel["slots"]["A"])
        self.assertIsNone(panel["slots"]["B"])
        link = topology["connections"][0]
        self.assertIn(panel["station_id"], (link["station_a_id"], link["station_b_id"]))

    def test_every_slot_letter_is_present_even_when_empty(self):
        topology = self._cloud_linked_with_package()
        for panel in topology["panels"]:
            self.assertEqual(["A", "B", "C", "D"], sorted(panel["slots"]))

    # ------------------------------------------- öppet läge (lokalt utkast)

    def test_a_local_draft_comes_back_unlocked(self):
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        topology = self.application.build_topology(self.client)
        self.assertFalse(topology["locked"])
        self.assertEqual("lokal", topology["source"])

    def test_an_empty_local_draft_is_empty_not_the_cloud_package(self):
        """The open view must not fall back to showing the active package.

        That would be the worst kind of wrong: fields that look editable,
        holding rows that belong to somebody else.
        """
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        topology = self.application.build_topology(self.client)
        self.assertEqual([], topology["stations"])
        self.assertEqual([], topology["connections"])

    def test_the_seeded_draft_is_what_the_open_view_shows(self):
        self.runtime.install(runtime_package_v3())
        self.application.set_operating_mode(self.client, {"mode": "offline-meet"})
        self.application.seed_local_configuration(self.client)
        topology = self.application.build_topology(self.client)
        self.assertEqual(
            ["station-a", "station-b"],
            [station["id"] for station in topology["stations"]],
        )
        self.assertFalse(topology["locked"])

    # -------------------------------------------------------------- åtkomst

    def test_reading_the_topology_needs_an_admin(self):
        """A TMBox on the table must not be able to read the build view."""
        panel = PairedClient(
            client_id="tmbox-1",
            display_name="CDA TMBox",
            kind=DeviceKind.ESP32_PANEL,
            panel_ids=("panel-a",),
        )
        with self.assertRaises(HTTPAPIError) as raised:
            self.application.build_topology(panel)
        self.assertEqual(HTTPStatus.FORBIDDEN, raised.exception.status)


class BuildTopologyViewTests(unittest.TestCase):
    """The markup and script side of the same step."""

    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "app.js").read_text(encoding="utf-8")
        cls.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_the_step_has_the_three_numbered_sections(self):
        self.assertIn('data-build-panel="bana"', self.html)
        for heading in ("1 · Stationer i ordning", "2 · Sträckor", "3 · TMBox-paneler A–D"):
            self.assertIn(heading, self.html)

    def test_the_three_sections_live_in_one_card(self):
        """Paketets 3.8: ett kort, inte tre formulär."""
        panel = self.html.split('data-build-panel="bana"')[1].split("</section>\n          </section>")[0]
        self.assertEqual(1, panel.count("topology-card"))

    def test_the_badge_says_both_things_and_the_view_picks(self):
        self.assertIn("🔒 Låst av Cloud", self.js)
        self.assertIn("✎ Redigerbar", self.js)
        self.assertIn("topology.locked", self.js)

    def test_the_locked_note_points_back_to_step_one(self):
        self.assertIn("steg 1", self.js)

    def test_the_view_never_decides_locked_for_itself(self):
        """No client-side reconstruction of the rule.

        `locked` is a server answer. Anything here that recomputed it from a
        mode string would be a second source of truth, and the two would
        disagree on the day it mattered.
        """
        renderer = self.js.split("BYGG steg 2: Bana")[1]
        self.assertNotIn('=== "cloud-linked"', renderer)
        self.assertNotIn('=== "cloud"', renderer)

    def test_a_slot_is_resolved_through_its_connection(self):
        self.assertIn("function slotNeighbour", self.js)
        self.assertIn("station_a_id === panel.station_id", self.js)

    def test_rows_are_built_as_elements_not_html_strings(self):
        """CSP forbids inline styles, and innerHTML invites them.

        The whole step is built with createElement for that reason. A single
        innerHTML here would work in review and fail silently in production.
        """
        renderer = self.js.split("BYGG steg 2: Bana")[1]
        self.assertNotIn("innerHTML", renderer)

    def test_the_locked_field_colours_are_the_packages(self):
        self.assertIn(".topology-field[data-locked=\"true\"]", self.css)
        self.assertIn("--surface-muted: #f7f5f0", self.css)
        self.assertIn("--ink-locked: #8a857a", self.css)

    def test_the_signature_field_is_the_width_the_package_gives(self):
        signature = self.css.split(".topology-field.signature")[1].split("}")[0]
        self.assertIn("width: 76px", signature)
        self.assertIn("monospace", signature)


if __name__ == "__main__":
    unittest.main()


class TwelveMenuLeftoverTests(unittest.TestCase):
    """The old structure's page heading, gone.

    Under twelve menu points one shared header named whichever page you were
    on. The KÖR/BYGG steps carry their own headings, so the shared one printed
    "DRIFT · Aktiv träff" above every build step - static text that was wrong
    on four steps out of five.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "app.js").read_text(encoding="utf-8")

    def test_the_shared_page_heading_is_gone(self):
        for leftover in (
            'id="admin-section-title"',
            'id="admin-section-eyebrow"',
            'id="admin-section-description"',
            'id="admin-section-state"',
        ):
            self.assertNotIn(leftover, self.html)

    def test_nothing_writes_to_the_heading_that_no_longer_exists(self):
        """The crash this pairs with is not hypothetical.

        Removing an element while a script still assigns to it kills the whole
        file at that line - every feature below it stops working, silently.
        """
        self.assertNotIn("admin-section-state", self.js)


class EditableViewTests(unittest.TestCase):
    """The open mode: real fields, the shortcut, and the seed connection."""

    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "app.js").read_text(encoding="utf-8")
        cls.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_open_rows_use_inputs_and_locked_rows_do_not(self):
        renderer = self.js.split("BYGG steg 2: Bana")[1]
        self.assertIn("function topologyInput", renderer)
        self.assertIn("function topologySelect", renderer)
        self.assertIn("if (locked) {", renderer)

    def test_a_field_commits_on_change_not_on_every_keystroke(self):
        """Saving per keystroke would write "L", "Le", "Lek" as three revisions.

        Regeln gäller fält som skriver. Skivan tog tidigare allt efter
        steg 2-markören, vilket räckte så länge steg 2 var det sista som
        fanns - nu ligger steg 3 där, och dess sökruta filtrerar per
        tangenttryck och skriver ingenting. Skivan slutar därför vid nästa
        avsnitt i stället för vid filens slut.
        """
        renderer = self.js.split("BYGG steg 2: Bana")[1].split("BYGG steg 3")[0]
        self.assertIn('field.addEventListener("change", commit)', renderer)
        self.assertNotIn('addEventListener("input"', renderer)

    def test_the_shortcut_and_the_seed_box_both_exist(self):
        self.assertIn('id="bana-shortcut"', self.html)
        self.assertIn('id="bana-seed"', self.html)
        self.assertIn("Snabbaste vägen till en körbar träff", self.html)
        self.assertIn("Bygg från stationsordningen", self.html)

    def test_the_shortcut_is_hidden_when_it_could_not_do_anything(self):
        self.assertIn("topology.locked || !hasStations", self.js)
        self.assertIn("topology.locked || hasStations", self.js)

    def test_the_derivation_happens_on_the_server(self):
        """Two opinions about how an A-D panel fills would be worse than none."""
        self.assertIn('"/v1/local-configuration/build"', self.js)
        renderer = self.js.split("BYGG steg 2, redigering", 1)[1]
        self.assertNotIn("local-connection-", renderer)

    def test_the_dispatch_rule_keeps_all_three_values(self):
        """The screenshot shows two; the data has three.

        Rule 3 of the mandate: functionality may move but not disappear.
        Dropping "Direkt" from the picker would make an existing setting
        unreachable from the only view that shows it.
        """
        self.assertIn('["", "Ärver träffens läge"]', self.js)
        self.assertIn('["clearance", "Begär och bekräfta"]', self.js)
        self.assertIn('["direct", "Direkt"]', self.js)

    def test_an_input_may_shrink_below_its_intrinsic_width(self):
        """Without this the station row splits in two at the package's 924px.

        An <input> carries a built-in minimum of about twenty characters that
        a <div> does not, so the same row that fits when locked breaks when
        editable - which reads as two different designs for one screen.
        """
        self.assertIn(
            "input.topology-field,\nselect.topology-field {\n  min-width: 0;\n}",
            self.css,
        )
        self.assertIn("input.topology-field.grow {\n  flex: 1 1 0;\n}", self.css)


class PendingRevisionViewTests(unittest.TestCase):
    """T4's other half: the decision has to be possible to make."""

    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "app.js").read_text(encoding="utf-8")

    def test_the_card_exists_and_names_what_changes(self):
        self.assertIn('id="pending-revision"', self.html)
        self.assertIn('id="pending-changes"', self.html)
        self.assertIn('id="activate-pending"', self.html)

    def test_the_change_list_is_in_the_card_not_behind_a_link(self):
        """What takes an extra click does not get read."""
        self.assertIn("renderPendingChanges", self.js)
        self.assertNotIn("Visa vad som ändras", self.html)

    def test_activation_sends_the_revision_it_was_shown(self):
        self.assertIn("publication_id: card.dataset.publicationId", self.js)

    def test_a_truncated_list_says_it_is_truncated(self):
        self.assertIn("och ${group.more} till", self.js)

    def test_the_card_is_built_as_elements_not_html_strings(self):
        block = self.js.split("Väntande Cloud-revision (T4)")[1]
        self.assertNotIn("innerHTML", block)
