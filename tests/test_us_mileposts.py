"""Runtime-v2 contract, explicit topology, safe sync and operational lifecycle."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4

import test_us_http
from tmbox_gateway.us import USStore, USError, validate_package, validate_path, paths_conflict
from tmbox_gateway.us_topology import validate_position
from tmbox_gateway.cloud_config import CloudConfiguration


def package():
    return json.loads(Path(__file__).with_name('us_runtime_v2_package.json').read_text())


def leg(segment='ab', start='a', end='b'):
    return dict(segment_id=segment, from_node=start, to_node=end)


def parallel(p):
    p['nodes'] += [dict(n, id=n['id']+'2', name=n['name']+' parallel') for n in list(p['nodes'])]
    p['segments'].append(dict(id='parallel', name='Independent parallel', from_node='a2', to_node='b2'))
    return p


class MilepostValidationTests(unittest.TestCase):
    def test_cloud_contract_is_preserved_without_station_mp_or_inferred_connections(self):
        p = package(); before = copy.deepcopy(p)
        validated = validate_package(p)
        self.assertEqual(before, validated)
        self.assertEqual(40, validated['mileposts'][0]['value'])
        self.assertNotIn('mp', validated['nodes'][0])
        self.assertEqual('yard1', validated['runs'][0]['schedule'][0]['location_id'])
        self.assertEqual(('us', 'meet', p['publication_id'], p['name']), CloudConfiguration.describe(p))
        validated['locations'][0]['name'] = 'Local edit'
        self.assertEqual(before, p)

    def test_same_mp_crossovers_reverse_and_cross_territory_paths_are_explicit(self):
        p = package()
        self.assertEqual([leg()], validate_path(p, [leg()]))
        self.assertEqual([leg(start='b', end='a')], validate_path(p, [leg(start='b', end='a')]))
        p['territories'].append(dict(id='sp', name='SP'))
        p['nodes'][1]['territory_id'] = 'sp'
        p['locations'][1]['territory_id'] = 'sp'
        p['mp_systems'].append(dict(id='sp-main', name='SP mileage', territory_id='sp'))
        p['mileposts'][1]['mp_system_id'] = 'sp-main'
        self.assertEqual(p, validate_package(p))
        self.assertEqual([leg()], validate_path(p, [leg()]))

    def test_mp_labels_and_drawing_positions_do_not_create_connected_paths(self):
        p = parallel(package())
        p['mileposts'].append(dict(p['mileposts'][0], id='m-parallel', node_id='a2'))
        validate_package(p)
        self.assertFalse(paths_conflict(p, [leg()], [leg('parallel', 'a2', 'b2')]))
        with self.assertRaisesRegex(USError, 'not connected'):
            validate_path(p, [leg(), leg('parallel', 'a2', 'b2')])
        p['segments'].append(dict(id='bc', name='Real switch link', from_node='b', to_node='a2'))
        validate_path(p, [leg(), leg('bc', 'b', 'a2'), leg('parallel', 'a2', 'b2')])
        self.assertTrue(paths_conflict(p, [leg()], [leg('bc', 'b', 'a2')]))

    def test_crossing_resource_protects_unconnected_tracks_without_creating_a_route(self):
        p = parallel(package())
        for segment in p['segments']:
            segment['conflict_resources'] = ['diamond']
        self.assertTrue(paths_conflict(p, [leg()], [leg('parallel', 'a2', 'b2')]))
        with self.assertRaises(USError):
            validate_path(p, [leg(), leg('parallel', 'a2', 'b2')])

    def test_malformed_or_mixed_model_packages_fail_closed(self):
        mutations = [lambda p: p.update(schema='trainmeet.us.runtime/1'),
            lambda p: p.update(profile='tm-us-planning-v2'),
            lambda p: p.update(schema='trainmeet.us.runtime/99'),
            lambda p: p['nodes'][0].update(mp=40),
            lambda p: p['nodes'][0].update(territory_id=[]),
            lambda p: p['segments'][0].update(from_node=[]),
            lambda p: p['mileposts'][0].update(value=float('nan')),
            lambda p: p['mileposts'][0].update(value=10**1000),
            lambda p: p['mileposts'][0].update(mp_system_id='missing'),
            lambda p: p['locations'][0].update(node_ids=['missing']),
            lambda p: p['locations'][0].update(node_ids='a'),
            lambda p: p['limits'][0].pop('node_id'),
            lambda p: p['limits'][0].update(milepost_id='m2'),
            lambda p: p['runs'][0]['schedule'][0].update(node_id='a'),
            lambda p: p['runs'][0]['schedule'][0].update(time='25:00'),
            lambda p: p['runs'][0]['schedule'][0].update(time='09:00'),
            lambda p: p['runs'][0]['schedule'][0].update(day_offset=1)]
        for mutate in mutations:
            p = package(); mutate(p)
            with self.subTest(mutate=mutate), self.assertRaises(USError): validate_package(p)

    def test_a_multi_track_place_cannot_teleport_a_schedule(self):
        p = parallel(package())
        p['locations'][1]['node_ids'] = ['b', 'a2']
        p['runs'][0]['schedule'].append(dict(node_id='b2', time='06:30'))
        with self.assertRaisesRegex(USError, 'through all timetable'):
            validate_package(p)

    def test_paths_reject_partial_mp_intervals_unknown_endpoints_and_repeated_segments(self):
        for path in ([], [dict(segment_id='ab', from_mp=40, to_mp=41)],
                     [leg(start='yard1')], [leg(start=' a')], [leg(start='a', end='a')], [leg(), leg(start='b', end='a')],
                     [dict(leg(), ignored='extra')], [dict(leg(), from_node=[])], [leg('missing')]):
            with self.subTest(path=path), self.assertRaises(USError): validate_path(package(), path)

    def test_reports_require_track_specific_id_not_number_or_unlinked_label(self):
        p = parallel(package())
        p['mileposts'] += [dict(id='unlinked', name='Loose MP', mp_system_id='main', value=40),
                          dict(id='interior', name='Interior MP', mp_system_id='main', value=41, segment_id='ab')]
        validate_package(p)
        for position in (dict(segment_id='ab', node_id='a'), dict(segment_id='ab', milepost_id='m1'),
                         dict(segment_id='ab', milepost_id='interior')):
            self.assertEqual(position, validate_position(p, position))
        for position in (dict(segment_id='ab', mp=40), dict(segment_id='ab', milepost_id='unlinked'),
                         dict(segment_id='parallel', milepost_id='m1'), dict(segment_id='parallel', milepost_id='interior'),
                         dict(segment_id='ab', node_id='a', milepost_id='m1')):
            with self.subTest(position=position), self.assertRaises(USError): validate_position(p, position)


class MilepostRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'us.db'
        self.store = USStore(self.path); self.addCleanup(lambda: self.store.close())
        self.p = parallel(package())
        self.p['runs'].append(dict(id='run2', symbol='104', direction='east', schedule=[]))
        self.call('create_session', package=self.p)
        self.r1, self.r2 = [r['id'] for r in self.current()['runs']]
        for run, crew in ((self.r1, 'crew1'), (self.r2, 'crew2')):
            self.call('assign', run_id=run, conductor_id=crew, conductor_name=crew)

    def current(self): return self.store.context('dispatcher', True)['session']

    def call(self, action, actor='dispatcher', **payload):
        current = self.current()
        command = dict(command_id=str(uuid4()), **payload)
        if current: command.update(session_id=current['id'], expected_revision=current['revision'])
        return self.store.execute(actor, actor=='dispatcher', action, command, '06:15')

    def prepare(self, run, crew, path):
        w = self.call('draft', run_id=run, kind='proceed', path=path)['target_id']
        self.call('transmit', warrant_id=w)
        self.call('receive', crew, warrant_id=w)
        self.call('readback', crew, warrant_id=w, confirmed=True)
        return w

    def test_full_lifecycle_parallel_conflict_release_reports_and_restart(self):
        first = self.prepare(self.r1, 'crew1', [leg()])
        second = self.prepare(self.r2, 'crew2', [leg('parallel', 'a2', 'b2')])
        self.call('activate', warrant_id=first, confirmed=True)
        self.call('activate', warrant_id=second, confirmed=True)
        third = self.prepare(self.r2, 'crew2', [leg(start='b', end='a')])
        for stage in ('active', 'release_requested'):
            with self.assertRaisesRegex(USError, 'Conflict'): self.call('activate', warrant_id=third, confirmed=True)
            if stage == 'active': self.call('request_release', 'crew1', warrant_id=first, confirmed=True)
        self.call('report', 'crew1', run_id=self.r1, kind='position', message='At marker', position=dict(segment_id='ab', milepost_id='m1'))
        self.assertEqual('release_requested', self.current()['warrants'][0]['status'])
        self.call('close_warrant', warrant_id=first, confirmed=True)
        self.call('activate', warrant_id=third, confirmed=True)
        before = self.current(); self.store.close(); self.store = USStore(self.path)
        self.assertEqual(before, self.current())
        self.assertIn('ATSF / Main 1 east switch [a]', before['warrants'][0]['text'])
        self.assertIn('entire segment', before['warrants'][0]['text'])
        self.assertNotIn('MP 40 to MP 40', before['warrants'][0]['text'])
        self.assertEqual([self.r1], [r['id'] for r in self.store.context('crew1', False)['session']['runs']])

    def test_active_authorities_block_semantic_reference_changes_but_allow_diagram_movement(self):
        self.prepare(self.r1, 'crew1', [leg()])
        for collection, field, value in [('mileposts', 'value', 41), ('locations', 'name', 'Renamed'),
                                          ('limits', 'kind', 'authority_limit'), ('mp_systems', 'name', 'Changed')]:
            p = copy.deepcopy(self.p); p['publication_id'] = 'new'; p[collection][0][field] = value
            self.assertTrue(self.store.config_update_blockers(p), collection)
        p = copy.deepcopy(self.p); p['publication_id'] = 'diagram'
        for key in ('nodes', 'locations', 'mileposts'): p[key][0]['x'] = 15
        before = self.current()
        self.store.stage_package(p)
        self.store.adopt_package(p)
        after = self.current()
        for key in ('id', 'clock', 'warrants', 'runs'): self.assertEqual(before[key], after[key])

    def test_reported_position_also_blocks_reinterpreting_marker_associations(self):
        self.call('report', 'crew1', run_id=self.r1, kind='position', message='Stopped', position=dict(segment_id='ab', node_id='a'))
        p = copy.deepcopy(self.p); p['publication_id'] = 'changed'; p['mileposts'][0]['value'] = 41
        self.assertTrue(self.store.config_update_blockers(p))
        self.assertEqual(self.p['publication_id'], self.current()['package']['publication_id'])

    def test_two_connections_cannot_activate_conflicting_v2_warrants(self):
        warrants = [self.prepare(self.r1, 'crew1', [leg()]), self.prepare(self.r2, 'crew2', [leg(start='b', end='a')])]
        current = self.current(); barrier = threading.Barrier(2); results = []
        def activate(warrant):
            store = USStore(self.path)
            try:
                barrier.wait(timeout=5)
                store.execute('dispatcher', True, 'activate', dict(command_id=str(uuid4()),
                    session_id=current['id'], expected_revision=current['revision'], warrant_id=warrant, confirmed=True), '06:20')
                results.append('active')
            except USError as error:
                results.append(error.status)
            finally:
                store.close()
        threads = [threading.Thread(target=activate, args=(w,)) for w in warrants]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertCountEqual(['active', 409], results)
        remaining = next(w for w in self.current()['warrants'] if w['status'] != 'active')
        with self.assertRaisesRegex(USError, 'Conflict'):
            self.call('activate', warrant_id=remaining['id'], confirmed=True)

    def test_work_authority_and_profile_change_preserve_existing_text_and_state(self):
        w = self.call('draft', run_id=self.r1, kind='work', path=[leg()])['target_id']
        before = self.current()
        self.assertIn('either direction', before['warrants'][0]['text'])
        self.assertEqual(w, before['warrants'][0]['id'])
        legacy = copy.deepcopy(self.p)
        legacy.update(schema='trainmeet.us.runtime/1', profile='tm-us-twc-manual-v1', publication_id='legacy')
        for collection in ('mp_systems', 'mileposts', 'locations', 'limits'): legacy.pop(collection)
        for index, node in enumerate(legacy['nodes']): node['mp'] = index
        for run in legacy['runs']: run['schedule'] = []
        self.assertTrue(self.store.config_update_blockers(legacy))
        with self.assertRaises(USError): self.store.adopt_package(legacy)
        self.assertEqual(before, self.current())


class MilepostHTTPTests(test_us_http.USHTTPFixture):
    select_us = True
    def published_package(self): return package()

    def test_cloud_v2_can_be_selected_started_offline_and_previewed_without_eu_mutation(self):
        self.assertIsNone(self.runtime.active())
        self.assertEqual('us', self.app.lifecycle.selected()['region'])
        self.app.runtime_fetcher = lambda *_: self.fail('Offline start must not fetch Cloud')
        status, result = self.request('/v1/us/commands', self.start_command(), 'admin-token')
        self.assertEqual(200, status, result)
        status, context = self.request('/v1/us/context', token='admin-token')
        self.assertEqual(package(), context['session']['package'])
        self.assertEqual(401, self.request('/v1/us/context')[0])
        self.assertEqual(403, self.request('/v1/us/context', token='eu-token')[0])
        p = dict(package(), schema='trainmeet.us.runtime/99', publication_id='future')
        before = self.store.current_session()
        with self.assertRaises(USError): self.store.stage_package(p)
        self.assertEqual(before, self.store.current_session())
