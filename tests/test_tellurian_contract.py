"""Kontraktstester för Tellurian Dispatch-adaptern mot simulatorn över riktig HTTP och SSE.

Allt här är SIMULERAT (TellurianSimulator på 127.0.0.1). Det verifierar
TrainMeets sida av kontraktet i SPECIFICATION.md, inte Stefans server.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from tmbox_gateway.integrations.framework import Outcome, ScopeMismatch, Unsupported
from tmbox_gateway.integrations.journal import SqliteCommandJournal
from tmbox_gateway.integrations.tellurian import (
    ActionNotOffered, AddressRejected, Endpoint, Mirror, Section, ShadowRunner, TellurianClient,
    TellurianCommands, TellurianError, Transport, check_address, deviations, match_stations,
    match_stretches, match_trains, offered, parse_dispatcher_view, parse_train_label, preflight,
    safe_href, station_map,
)
from tmbox_gateway.integrations.tellurian_sim import TellurianSimulator, default_scenario

STATIONS = [{"id": "st-cda", "code": "CDA", "name": "Charlottendal"},
            {"id": "st-vst", "code": "VST", "name": "Västerhamn"},
            {"id": "st-lek", "code": "LEK", "name": "Lekeberg"}]
CONNECTIONS = [{"id": "c1", "station_a_id": "st-cda", "station_b_id": "st-vst"},
               {"id": "c2", "station_a_id": "st-vst", "station_b_id": "st-lek"}]


def wait_for(condition, timeout=5.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()


class SimulatorCase(unittest.TestCase):
    def setUp(self):
        self.sim = TellurianSimulator(heartbeat=0.1)
        self.sim.__enter__()
        self.addCleanup(self.sim.__exit__, None, None, None)
        self.sim.model.man_all()
        self.endpoint = check_address(self.sim.base_url, test_instance=True)
        self.client = TellurianClient(self.endpoint, timeout=0.6)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal_path = Path(self.directory.name) / "commands.sqlite3"

    def commands(self, journal=None):
        journal = journal or SqliteCommandJournal(self.journal_path)
        self.addCleanup(journal.close)
        mirror = Mirror()
        mirror.set_dispatchers(self.client.dispatchers())
        match = match_stations(STATIONS, tuple(mirror.dispatchers.values()))
        return TellurianCommands(self.client, mirror, journal, meet_id="meet-a",
                                 connection_id="conn-test", stations=station_map(match))

    def section(self, number, origin):
        return str(self.sim.model.section_for(number, origin).id)


class ReadingTests(SimulatorCase):
    def test_dispatchers_and_views_follow_the_server_links(self):
        dispatchers = self.client.dispatchers()
        self.assertEqual(["CDA", "VST", "LEK"], [d.signature for d in dispatchers])
        view = self.client.view(dispatchers[0])
        departure = view.section(self.section(421, "CDA"))
        self.assertEqual(("CDA", "VST", "Manned", "None"),
                         (departure.from_sig, departure.to_sig, departure.train_state, departure.state))
        self.assertEqual(2, departure.total)
        self.assertTrue(all(a.href.startswith("/api/v1/train-sections/") for a in departure.actions))
        self.assertTrue(self.client.simulated, "simulatorns svar ska märkas som simulerade")

    def test_offered_handlings_come_only_from_the_dispatchers_actions(self):
        cda, vst = self.client.dispatchers()[:2]
        self.assertEqual({"request"}, {o.handling for o in offered(self.client.view(cda))})
        # Inget erbjuds VST förrän CDA har begärt.
        self.assertEqual([], [o for o in offered(self.client.view(vst)) if o.section_id == self.section(421, "CDA")])
        self.sim.act(421, "CDA", "request")
        vst_offer = {o.handling: o for o in offered(self.client.view(vst)) if o.section_id == self.section(421, "CDA")}
        self.assertEqual({"give_clear", "refuse"}, set(vst_offer))
        self.assertEqual(("#", "Ge klart"), (vst_offer["give_clear"].key, vst_offer["give_clear"].label))
        cda_offer = {o.handling for o in offered(self.client.view(cda))}
        self.assertIn("cancel_request", cda_offer)
        # Tågläge (inställt, ångra) bara för administratören.
        self.assertNotIn("cancel_train", cda_offer)
        self.assertIn("cancel_train", {o.handling for o in offered(self.client.view(cda), admin=True)})

    def test_revoke_means_cancel_request_or_withdraw_clear_depending_on_state(self):
        cda, vst = self.client.dispatchers()[:2]
        self.sim.act(421, "CDA", "request")
        self.sim.act(421, "CDA", "accept")
        self.assertIn("withdraw_clear", {o.handling for o in offered(self.client.view(cda))})
        self.assertIn("withdraw_clear", {o.handling for o in offered(self.client.view(vst))})

    def test_foreign_or_unsafe_hrefs_are_dropped_and_reported(self):
        dispatcher = self.client.dispatchers()[0]
        data = {"id": dispatcher.id, "departures": [{
            "id": 9, "train": "SJ RC 1", "state": "None", "trainState": "Manned",
            "actions": [{"name": "request", "href": "http://evil.example/api/v1/x"},
                        {"name": "accept", "href": "/api/v1/../admin"},
                        {"name": "depart", "href": self.sim.base_url + "/api/v1/train-sections/9/actions/depart"}]}]}
        view = parse_dispatcher_view(data, dispatcher, self.endpoint)
        self.assertEqual(["depart"], [a.name for a in view.departures[0].actions])
        self.assertEqual("/api/v1/train-sections/9/actions/depart", view.departures[0].actions[0].href)
        self.assertEqual(("accept (href)", "request (href)"), view.unknown_actions)

    def test_transport_faults_are_codes_without_provider_text(self):
        for fault, code in (("redirect", "redirect"), ("oversize", "too_large"), ("invalid_json", "bad_json"),
                            ("server_error", "server_error"), ("timeout", "timeout")):
            with self.subTest(fault=fault):
                self.sim.fail_next_read(fault)
                with self.assertRaises(TellurianError) as caught:
                    self.client.dispatchers()
                self.assertEqual(code, caught.exception.code)
                self.assertNotIn("secret", repr(caught.exception.args))
                self.assertNotIn("example.invalid", str(caught.exception))
        self.assertEqual(0, self.sim.posts())


class WriteTests(SimulatorCase):
    def test_write_is_confirmed_only_after_readback(self):
        commands = self.commands()
        result = commands.submit(station_id="st-cda", section_id=self.section(421, "CDA"),
                                 handling="request", command_id="cmd-1")
        self.assertEqual((Outcome.CONFIRMED, "confirmed"), (result.outcome, result.detail))
        self.assertEqual("Requested", self.sim.model.section_for(421, "CDA").state)
        # Återläsningen efter POST är en egen GET av stationens dispatcher.
        posts = [i for i, r in enumerate(self.sim.requests) if r[0] == "POST"]
        self.assertEqual(("GET", "/api/v1/dispatchers/1"), self.sim.requests[posts[0] + 1])
        again = commands.submit(station_id="st-cda", section_id=self.section(421, "CDA"),
                                handling="request", command_id="cmd-1")
        self.assertEqual((Outcome.CONFIRMED, "duplicate"), (again.outcome, again.detail))
        self.assertEqual(1, self.sim.posts())

    def test_lost_response_is_unknown_and_never_resent(self):
        commands = self.commands()
        section = self.section(421, "CDA")
        self.sim.lose_next_response()
        with self.assertLogs("tmbox_gateway.integrations.tellurian", logging.WARNING) as logs:
            result = commands.submit(station_id="st-cda", section_id=section, handling="request", command_id="lost-1")
        self.assertEqual((Outcome.UNKNOWN, "unknown"), (result.outcome, result.detail))
        self.assertEqual(1, self.sim.posts())
        self.assertNotIn("/api/v1/", "\n".join(logs.output))
        # Samma kommando igen: ingen ny POST, utfallet är fortfarande okänt.
        self.assertEqual(Outcome.UNKNOWN, commands.submit(
            station_id="st-cda", section_id=section, handling="request", command_id="lost-1").outcome)
        self.assertEqual(1, self.sim.posts())
        # Avstämning är bara läsning och ser effekten.
        self.assertEqual(Outcome.CONFIRMED, commands.reconcile("st-cda", "lost-1").outcome)
        self.assertEqual(1, self.sim.posts())

    def test_unknown_outcome_survives_restart_and_reconciles_without_writes(self):
        first = self.commands()
        self.sim.lose_next_response()
        with self.assertLogs("tmbox_gateway.integrations.tellurian", logging.WARNING):
            first.submit(station_id="st-cda", section_id=self.section(421, "CDA"), handling="request",
                         command_id="r-1")
        first.journal.close()
        restarted = self.commands()
        self.assertEqual([Outcome.CONFIRMED], [r.outcome for r in restarted.reconcile_pending("st-cda")])
        self.assertEqual(1, self.sim.posts())
        self.assertEqual((), restarted.journal.unresolved(restarted.scope("st-cda")))

    def test_unknown_stays_unknown_when_readback_does_not_show_the_effect(self):
        commands = self.commands()
        section = self.section(421, "CDA")
        with patch.object(self.client, "post", side_effect=TellurianError("connection_lost", sent=True)), \
                self.assertLogs("tmbox_gateway.integrations.tellurian", logging.WARNING):
            result = commands.submit(station_id="st-cda", section_id=section, handling="request", command_id="u-1")
        self.assertEqual((Outcome.UNKNOWN, "unknown"), (result.outcome, result.detail))
        self.assertEqual(Outcome.UNKNOWN, commands.reconcile("st-cda", "u-1").outcome)
        self.assertEqual(0, self.sim.posts())
        # Operatören kan göra handlingen igen, men det är ett nytt kommando.
        self.assertEqual(Outcome.CONFIRMED, commands.submit(
            station_id="st-cda", section_id=section, handling="request", command_id="u-2").outcome)

    def test_not_offered_action_is_refused_before_anything_is_reserved(self):
        commands = self.commands()
        with self.assertRaises(ActionNotOffered):
            commands.submit(station_id="st-cda", section_id=self.section(421, "CDA"), handling="departed",
                            command_id="early-1")
        self.assertIsNone(commands.journal.get(commands.scope("st-cda"), "early-1"))
        self.assertEqual(0, self.sim.posts())
        with self.assertRaises(Unsupported):
            commands.submit(station_id="st-cda", section_id=self.section(421, "CDA"), handling="queue",
                            command_id="queue-1")
        with self.assertRaises(Unsupported):
            commands.submit(station_id="st-cda", section_id=self.section(421, "CDA"), handling="cancel_train",
                            command_id="admin-1")

    def test_race_lost_to_another_client_is_unknown_not_resent(self):
        commands = self.commands()
        section = self.section(421, "CDA")
        original = self.client.post

        def other_client_first(href):
            self.sim.act(421, "CDA", "request")  # t.ex. Tellurians egen vy hinner före
            return original(href)

        with patch.object(self.client, "post", side_effect=other_client_first):
            result = commands.submit(station_id="st-cda", section_id=section, handling="request", command_id="race-1")
        self.assertEqual((Outcome.UNKNOWN, "refused"), (result.outcome, result.detail))
        # Effekten finns (någon annan gjorde den); avstämningen säger det, utan omsändning.
        self.assertEqual(Outcome.CONFIRMED, commands.reconcile("st-cda", "race-1").outcome)
        self.assertEqual(1, self.sim.posts())

    def test_full_run_through_two_stations_including_pass_and_arrive(self):
        commands = self.commands()
        section = self.section(421, "CDA")
        steps = [("st-cda", "request"), ("st-vst", "give_clear"), ("st-cda", "departed"),
                 ("st-cda", "pass"), ("st-vst", "arrived")]
        for index, (station, handling) in enumerate(steps):
            result = commands.submit(station_id=station, section_id=section, handling=handling,
                                     command_id=f"run-{index}")
            self.assertEqual(Outcome.CONFIRMED, result.outcome, handling)
        self.assertEqual("Arrived", self.sim.model.section_for(421, "CDA").state)
        self.assertEqual(5, self.sim.posts())

    def test_two_stations_work_in_parallel(self):
        commands = self.commands()
        # CDA begär 421; LEK begär 4711 mot VST, samtidigt.
        jobs = [("st-cda", self.section(421, "CDA")), ("st-lek", self.section(4711, "LEK"))]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda job: commands.submit(
                station_id=job[0], section_id=job[1], handling="request", command_id="same-id"), jobs))
        self.assertEqual([Outcome.CONFIRMED, Outcome.CONFIRMED], [r.outcome for r in results])
        # Samma kommando-id i två stationers scope är två olika kommandon.
        self.assertNotEqual(commands.scope("st-cda"), commands.scope("st-lek"))
        self.assertEqual(2, self.sim.posts())

    def test_a_box_can_never_act_for_another_stations_dispatcher(self):
        commands = self.commands()
        section = self.section(421, "CDA")
        commands.submit(station_id="st-cda", section_id=section, handling="request", command_id="own-1")
        # VST erbjuds Ge klart. CDA:s box känner till sektionen men får inte svara för VST.
        with self.assertRaises(ActionNotOffered):
            commands.submit(station_id="st-cda", section_id=section, handling="give_clear", command_id="foreign-1")
        # En station som inte är matchad mot en dispatcher har ingen väg alls.
        with self.assertRaises(ScopeMismatch):
            commands.submit(station_id="st-okand", section_id=section, handling="give_clear", command_id="foreign-2")
        self.assertEqual(1, self.sim.posts())
        self.assertEqual("Requested", self.sim.model.section_for(421, "CDA").state)
        # Inget av CDA:s erbjudanden pekar på VST:s åtgärder.
        cda = commands.mirror.dispatchers["1"]
        self.assertNotIn("accept", {o.href.rsplit("/", 1)[-1] for o in offered(self.client.view(cda))})


class EventStreamTests(SimulatorCase):
    def runner(self, cases=lambda: [], **options):
        runner = ShadowRunner(self.client, stations=lambda: STATIONS, trainmeet_cases=cases,
                              poll_interval=0.2, liveness_interval=0.3, contact_timeout=1.0,
                              sse_read_timeout=1.0, **options)
        runner.start()
        self.addCleanup(runner.stop)
        return runner

    def state_of(self, runner, number, origin):
        section = runner.mirror.sections().get(self.section(number, origin))
        return section.state if section else None

    def test_events_trigger_rereading_and_shadow_never_writes(self):
        runner = self.runner()
        self.assertTrue(wait_for(lambda: runner.status()["state"] == "connected"), runner.status())
        self.assertEqual(3, runner.status()["dispatchers"])
        self.sim.act(421, "CDA", "request")
        self.assertTrue(wait_for(lambda: self.state_of(runner, 421, "CDA") == "Requested"))
        self.assertTrue(runner.status()["simulated"])
        self.assertEqual(0, self.sim.posts())

    def test_dropped_stream_falls_back_to_polling_and_recovers(self):
        runner = self.runner()
        self.assertTrue(wait_for(lambda: runner.status()["state"] == "connected"))
        self.sim.refuse_event_streams(True)
        self.assertTrue(wait_for(lambda: runner.status()["state"] == "polling"), runner.status())
        self.sim.act(421, "CDA", "request")
        self.assertTrue(wait_for(lambda: self.state_of(runner, 421, "CDA") == "Requested"))
        self.sim.refuse_event_streams(False)
        self.assertTrue(wait_for(lambda: runner.status()["state"] == "connected", timeout=15), runner.status())
        self.sim.act(421, "CDA", "accept")
        self.assertTrue(wait_for(lambda: self.state_of(runner, 421, "CDA") == "Accepted"))
        self.assertEqual(0, self.sim.posts())

    def test_lost_contact_is_shown_and_the_mirror_resumes_after(self):
        runner = self.runner()
        self.assertTrue(wait_for(lambda: runner.status()["state"] == "connected"))
        self.sim.set_offline(True)
        self.assertTrue(wait_for(lambda: runner.status()["state"] == "no_contact", timeout=6), runner.status())
        self.assertIsNotNone(runner.status()["last_contact"])
        self.sim.act(421, "CDA", "request")
        self.sim.set_offline(False)
        self.assertTrue(wait_for(lambda: self.state_of(runner, 421, "CDA") == "Requested", timeout=15))
        self.assertIn(runner.status()["state"], ("connected", "polling"))

    def test_per_dispatcher_stream_only_carries_that_dispatchers_events(self):
        from threading import Event, Thread
        stop, seen = Event(), []

        def read():
            try:
                for kind, data in self.client.events(stop, dispatcher_id="3", read_timeout=1):
                    if kind:
                        seen.append(json.loads(data)["sectionId"])
            except TellurianError:
                pass

        thread = Thread(target=read, daemon=True)
        thread.start()
        self.assertTrue(wait_for(lambda: self.sim.stream_count() == 1))
        self.sim.act(421, "CDA", "request")      # CDA och VST
        self.sim.act(4711, "LEK", "request")     # LEK och VST
        self.assertTrue(wait_for(lambda: seen))
        stop.set()
        self.client.transport.abort_streams()
        thread.join(3)
        self.assertEqual([int(self.section(4711, "LEK"))], seen)

    def test_shadow_shows_deviations_against_trainmeets_own_clearance(self):
        cases = [{"from_station_id": "st-cda", "to_station_id": "st-vst", "train_number": "421", "state": "accepted"},
                 {"from_station_id": "st-vst", "to_station_id": "st-lek", "train_number": "100", "state": "requested"}]
        runner = self.runner(cases=lambda: cases)
        self.sim.act(421, "CDA", "request")
        self.sim.act(4711, "LEK", "request")
        self.assertTrue(wait_for(lambda: runner.status()["deviations"]["count"] == 3), runner.status())
        kinds = {(d["kind"], d["train"]) for d in runner.status()["deviations"]["items"]}
        self.assertEqual({("state_differs", "421"), ("only_trainmeet", "100"), ("only_tellurian", "4711")}, kinds)
        self.assertEqual(0, self.sim.posts())


class AddressTests(unittest.TestCase):
    def test_only_local_addresses_without_test_instance(self):
        for address, kind in (("192.168.1.20:5000", "private"), ("http://10.0.0.5", "private"),
                              ("http://172.20.1.1:8080/", "private"), ("169.254.10.2", "link_local"),
                              ("http://tellurian.local:5000", "mdns")):
            with self.subTest(address=address):
                self.assertEqual(kind, check_address(address).kind)
        self.assertEqual("http://192.168.1.20:5000", check_address("192.168.1.20:5000").origin)
        for address in ("http://8.8.8.8", "https://example.org", "http://127.0.0.1:5000", "http://localhost",
                        "http://[fd00::1]:80", "http://172.32.0.1", "http://100.64.0.1"):
            with self.subTest(address=address), self.assertRaises(AddressRejected) as caught:
                check_address(address)
            self.assertEqual("address_not_local", caught.exception.code)

    def test_test_instance_allows_public_but_never_cloud_or_odd_urls(self):
        self.assertEqual("public", check_address("https://tellurian.example.org", test_instance=True).kind)
        for address, code in (("https://server.trainmeet.app", "address_trainmeet_cloud"),
                              ("https://trainmeet.app", "address_trainmeet_cloud"),
                              ("ftp://192.168.1.2", "address_scheme"),
                              ("http://user:pw@192.168.1.2", "address_credentials"),
                              ("http://192.168.1.2/api/v1", "address_path"),
                              ("http://192.168.1.2/?x=1", "address_path"), ("", "address_required"),
                              ("http://192.168.1.2:99999", "address_invalid")):
            with self.subTest(address=address), self.assertRaises(AddressRejected) as caught:
                check_address(address, test_instance=True)
            self.assertEqual(code, caught.exception.code)

    def test_mdns_name_must_resolve_to_a_local_address(self):
        endpoint = check_address("http://tellurian.local:5000")
        local = Transport(endpoint, resolver=lambda *a: [(2, 1, 6, "", ("192.168.1.30", 5000))])
        self.assertEqual("192.168.1.30", local._address())
        public = Transport(endpoint, resolver=lambda *a: [(2, 1, 6, "", ("93.184.216.34", 5000))])
        with self.assertRaises(TellurianError) as caught:
            public._address()
        self.assertEqual(("address_not_local", False), (caught.exception.code, caught.exception.sent))

    def test_hrefs_must_stay_on_the_configured_origin(self):
        endpoint = Endpoint("http", "192.168.1.20", 5000, "private", False)
        self.assertEqual("/api/v1/train-sections/1/actions/depart",
                         safe_href("http://192.168.1.20:5000/api/v1/train-sections/1/actions/depart", endpoint))
        for href in ("http://192.168.1.21:5000/api/v1/x", "//evil/api/v1/x", "/admin", "/api/v1/a//b",
                     "/api/v1/%2e%2e/x", "/api/v1/x?y=1", "javascript:alert(1)", 42, None):
            with self.subTest(href=href):
                self.assertIsNone(safe_href(href, endpoint))


class MappingTests(unittest.TestCase):
    def setUp(self):
        sim = TellurianSimulator()
        sim.__enter__()
        self.addCleanup(sim.__exit__, None, None, None)
        client = TellurianClient(check_address(sim.base_url, test_instance=True), timeout=1)
        self.mirror = Mirror()
        self.mirror.set_dispatchers(client.dispatchers())
        self.mirror.refresh(client, None, 0)
        self.client = client

    def test_stations_match_exactly_on_signature(self):
        stations = STATIONS + [{"id": "st-x", "code": "cda", "name": "Liten bokstav"},
                               {"id": "st-y", "code": "HPL", "name": "Hultaplan"}]
        result = match_stations(stations, tuple(self.mirror.dispatchers.values()))
        self.assertEqual(["CDA", "VST", "LEK"], [m["code"] for m in result["matched"]])
        self.assertEqual({("cda", "case"), ("HPL", "missing")},
                         {(u["code"], u["reason"]) for u in result["unmatched_trainmeet"]})
        twice = match_stations(STATIONS + [{"id": "st-dup", "code": "VST", "name": "Dubblett"}],
                               tuple(self.mirror.dispatchers.values()))
        self.assertNotIn("VST", [m["code"] for m in twice["matched"]])
        self.assertIn("VST", [u["signature"] for u in twice["unmatched_tellurian"]])

    def test_stretches_are_matched_on_station_pairs_with_passes(self):
        stations = match_stations(STATIONS, tuple(self.mirror.dispatchers.values()))
        extra = CONNECTIONS + [{"id": "c3", "station_a_id": "st-cda", "station_b_id": "st-lek"}]
        result = match_stretches(extra, STATIONS, stations, self.mirror.sections())
        self.assertEqual({("CDA", "VST", 1), ("VST", "LEK", 0)},
                         {(m["from"], m["to"], m["passes"]) for m in result["matched"]})
        self.assertEqual(["c3"], [u["id"] for u in result["unseen"]])

    def test_trains_by_number_and_first_departure_never_guessed(self):
        services = [
            {"id": "s421", "train_number": "421", "stops": [{"station_id": "st-cda", "stop_order": 0}]},
            {"id": "s100", "train_number": "100", "stops": [{"station_id": "st-lek", "stop_order": 0}]},
            {"id": "s555", "train_number": "555", "stops": [{"station_id": "st-cda", "stop_order": 0}]},
        ]
        result = match_trains(services, STATIONS, self.mirror.sections())
        self.assertEqual({("421", "SJ RC 421"), ("100", "SJ RC 100")},
                         {(m["number"], m["tellurian"]) for m in result["matched"]})
        self.assertEqual(["555"], [u["number"] for u in result["unmatched_trainmeet"]])
        self.assertIn("TAB RC 100", result["unmatched_tellurian"])
        # Utan första avgång som skiljer dem åt är tåget tvetydigt, alltså omatchat.
        ambiguous = match_trains([{"id": "s100", "train_number": "100", "stops": []}], STATIONS,
                                 self.mirror.sections())
        self.assertEqual([], ambiguous["matched"])
        self.assertEqual(["SJ RC 100", "TAB RC 100"], ambiguous["ambiguous"][0]["candidates"])
        # Två TrainMeet-tåg med samma nummer samma dag: inget av dem gissas.
        twice = match_trains([services[0], {**services[0], "id": "s421b"}], STATIONS, self.mirror.sections())
        self.assertEqual([], twice["matched"])

    def test_source_train_id_wins_when_cloud_has_preserved_it(self):
        services = [{"id": "s1", "train_number": "999", "source_train_id": "3", "stops": []}]
        result = match_trains(services, STATIONS, self.mirror.sections())
        self.assertEqual([("999", "TAB RC 100")], [(m["number"], m["tellurian"]) for m in result["matched"]])

    def test_shared_number_is_told_apart_by_the_plans_id_or_operator(self):
        def section(sid, train, train_id=None):
            return Section(id=sid, train=train, train_id=train_id, train_state="planned", state="planned",
                           current=0, total=1, from_sig="CDA", to_sig="VST")
        stop = [{"station_id": "st-cda", "stop_order": 0}]
        # Planens id avgör, också när två tåg delar nummer (Cloud #112).
        by_id = {"a": section("a", "SJ X 116", "t1"), "b": section("b", "DSB IC 116", "t2")}
        services = [{"id": "x", "train_number": "116", "source_train_id": "t1", "service_key": "SJ X", "stops": stop},
                    {"id": "ic", "train_number": "116", "source_train_id": "t2", "service_key": "DSB IC", "stops": stop}]
        result = match_trains(services, STATIONS, by_id)
        self.assertEqual({("x", "SJ X 116"), ("ic", "DSB IC 116")},
                         {(m["service_id"], m["tellurian"]) for m in result["matched"]})
        self.assertEqual({"source_id"}, {m["by"] for m in result["matched"]})
        # Utan id: bolag och tågslag (service_key) mot Tellurians etikett.
        by_label = {"a": section("a", "SJ X 116"), "b": section("b", "DSB IC 116")}
        bare = [{k: v for k, v in service.items() if k != "source_train_id"} for service in services]
        result = match_trains(bare, STATIONS, by_label)
        self.assertEqual({("x", "SJ X 116"), ("ic", "DSB IC 116")},
                         {(m["service_id"], m["tellurian"]) for m in result["matched"]})
        # Samma id på två tjänster, eller samma bolag och tågslag: inget gissas.
        twice = [{**services[0], "id": "x2"}, services[0]]
        self.assertEqual([], match_trains(twice, STATIONS, by_id)["matched"])
        same_kind = [{**bare[0], "id": "x2"}, bare[0]]
        self.assertEqual([], match_trains(same_kind, STATIONS, by_label)["matched"])
        # Tellurians etikett utan bolag: som förut, tvetydigt.
        plain = {"a": section("a", "116"), "b": section("b", "116 ")}
        self.assertEqual([], match_trains(bare, STATIONS, plain)["matched"])

    def test_train_labels(self):
        self.assertEqual(("SJ", "IC", "456"), parse_train_label("SJ IC 456"))
        self.assertEqual(("", "IC", "7"), parse_train_label("IC 007"))
        self.assertEqual(("", "", None), parse_train_label("Rangering"))

    def test_preflight_report_is_marked_simulated_and_flags_clock_and_mode(self):
        services = [{"id": "s421", "train_number": "421", "stops": [{"station_id": "st-cda", "stop_order": 0}]}]
        report = preflight(self.client, stations=STATIONS + [{"id": "st-y", "code": "HPL", "name": "Hultaplan"}],
                           connections=CONNECTIONS, services=services, clock_source={"source": "internal"},
                           dispatch_mode="direct", region="eu")
        self.assertTrue(report["simulated"])
        self.assertEqual(("passed", 3), (report["api"]["status"], report["api"]["dispatchers"]))
        self.assertTrue(report["clock"]["warning"])
        self.assertFalse(report["dispatch_mode"]["ok"])
        self.assertEqual(["stations", "dispatch_mode"], report["activation"]["blockers"])
        self.assertFalse(report["activation"]["authority"])
        self.assertEqual(1, len(report["trains"]["matched"]))
        fast = preflight(self.client, stations=STATIONS, connections=CONNECTIONS, services=services,
                         clock_source={"source": "fastclock", "clock_name": "Träffklockan"},
                         dispatch_mode="clearance", region="eu")
        self.assertFalse(fast["clock"]["warning"])
        self.assertEqual([], fast["activation"]["blockers"])

    def test_unreachable_server_gives_a_short_report(self):
        endpoint = check_address("http://127.0.0.1:9", test_instance=True)
        report = preflight(TellurianClient(endpoint, timeout=0.5), stations=STATIONS, connections=[], services=[],
                           clock_source={}, dispatch_mode="clearance", region="eu")
        self.assertEqual(("failed", "unreachable"), (report["api"]["status"], report["api"]["error"]))
        self.assertEqual(["api"], report["activation"]["blockers"])


class DeviationTests(unittest.TestCase):
    def test_states_are_compared_per_station_pair_and_train(self):
        from tmbox_gateway.integrations.tellurian import Section
        sections = {
            "1": Section("1", "SJ RC 421", "1", "Running", "Departed", 0, 2, "CDA", "VST"),
            "2": Section("2", "GC G 4711", "2", "Manned", "Requested", 0, 1, "LEK", "VST"),
            "3": Section("3", "SJ RC 9", "3", "Manned", "Requested", 0, 1, "HPL", "VST"),
            "4": Section("4", "SJ RC 7", "4", "Manned", "None", 0, 1, "CDA", "VST"),
        }
        cases = [{"from_station_id": "st-cda", "to_station_id": "st-vst", "train_number": "421", "state": "departed"},
                 {"from_station_id": "st-lek", "to_station_id": "st-vst", "train_number": "4711", "state": "accepted"}]
        result = deviations(sections, STATIONS, cases)
        self.assertEqual(1, result["count"])
        self.assertEqual(("state_differs", "accepted", "requested"),
                         (result["items"][0]["kind"], result["items"][0]["trainmeet"], result["items"][0]["tellurian"]))
        self.assertEqual(1, result["unmapped"])


if __name__ == "__main__":
    unittest.main()
