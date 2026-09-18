"""Acceptance checks for the isolated model-railroad TWC runtime."""
import copy
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from test_us_terminology import package
from tmbox_gateway.us import USStore, USError, validate_package, validate_path


class USRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'runtime.sqlite'
        self.store = USStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.package = package()
        self.package['segments'].append({'id': 'parallel', 'name': 'Main 2', 'from_node': 'a', 'to_node': 'b'})
        self.package['runs'].append({'id': 'plan-2', 'symbol': 'SP 835', 'direction': 'west', 'schedule': []})
        self.call('create_session', package=self.package)
        self.run1, self.run2 = [r['id'] for r in self.current()['runs']]
        for run, crew in [(self.run1, 'crew-a'), (self.run2, 'crew-b')]:
            self.call('assign', run_id=run, conductor_id=crew, conductor_name=crew)

    def current(self):
        return self.store.context('dispatcher', True)['session']

    def call(self, action, actor='dispatcher', **payload):
        current = self.current()
        command = {'command_id': str(uuid4()), **payload}
        if current:
            command.setdefault('session_id', current['id'])
            command.setdefault('expected_revision', current['revision'])
        return self.store.execute(actor, actor == 'dispatcher', action, command, '06:15')

    def draft(self, run=None, segment='main', start=10, end=20):
        return self.call('draft', run_id=run or self.run1, kind='proceed',
                         path=[{'segment_id': segment, 'from_mp': start, 'to_mp': end}])['target_id']

    def read_back(self, warrant, actor='crew-a'):
        self.call('transmit', warrant_id=warrant)
        self.call('receive', actor, warrant_id=warrant)
        self.call('readback', actor, warrant_id=warrant, confirmed=True)

    def active(self, run=None, segment='main', actor='crew-a'):
        warrant = self.draft(run, segment)
        self.read_back(warrant, actor)
        self.call('activate', warrant_id=warrant, confirmed=True)
        return warrant

    def test_parallel_tracks_can_be_active_at_the_same_mileposts(self):
        self.active()
        self.active(self.run2, 'parallel', 'crew-b')
        self.assertEqual(['active', 'active'], [w['status'] for w in self.current()['warrants']])

    def test_shared_crossing_resource_blocks_otherwise_separate_tracks(self):
        for segment in self.package['segments']:
            segment['conflict_resources'] = ['crossing']
        # Start a fresh, reviewed session; never change the active package.
        self.call('finish_session')
        self.call('create_session', package=self.package)
        self.run1, self.run2 = [r['id'] for r in self.current()['runs']]
        for run, crew in [(self.run1, 'crew-a'), (self.run2, 'crew-b')]:
            self.call('assign', run_id=run, conductor_id=crew, conductor_name=crew)
        self.active()
        second = self.draft(self.run2, 'parallel')
        self.read_back(second, 'crew-b')
        with self.assertRaisesRegex(USError, 'Conflict'):
            self.call('activate', warrant_id=second, confirmed=True)

    def test_releasing_still_reserves_limits_until_explicit_closure(self):
        first = self.active()
        second = self.draft(self.run2)
        self.read_back(second, 'crew-b')
        self.call('request_release', 'crew-a', warrant_id=first, confirmed=True)
        with self.assertRaisesRegex(USError, 'Conflict'):
            self.call('activate', warrant_id=second, confirmed=True)
        self.call('close_warrant', warrant_id=first, confirmed=True)
        self.call('activate', warrant_id=second, confirmed=True)

    def test_position_report_never_releases_authority(self):
        first = self.active()
        self.call('report', 'crew-a', run_id=self.run1, kind='position', message='Entire train arrived',
                  position={'segment_id': 'main', 'mp': 20})
        self.assertEqual('active', next(w for w in self.current()['warrants'] if w['id'] == first)['status'])

    def test_conductor_only_sees_and_controls_assigned_run(self):
        first = self.active()
        second = self.draft(self.run2)
        view = self.store.context('crew-a', False)['session']
        self.assertEqual([self.run1], [r['id'] for r in view['runs']])
        self.assertEqual([first], [w['id'] for w in view['warrants']])
        self.assertEqual([], view['package']['runs'])
        for action, values in [('ready', {'run_id': self.run2}), ('receive', {'warrant_id': second}),
                               ('draft', {'run_id': self.run1}), ('finish_session', {})]:
            with self.subTest(action=action), self.assertRaises(USError) as error:
                self.call(action, 'crew-a', **values)
            self.assertEqual(403, error.exception.status)

    def test_active_warrant_cannot_be_voided_reassigned_or_session_closed(self):
        first = self.active()
        for action, values in [('void', {'warrant_id': first, 'confirmed': True}),
                               ('assign', {'run_id': self.run1, 'conductor_id': 'crew-b', 'conductor_name': 'B'}),
                               ('finish_session', {})]:
            with self.subTest(action=action), self.assertRaises(USError):
                self.call(action, **values)

    def test_duplicate_command_is_idempotent_and_mismatched_reuse_is_rejected(self):
        command = {'command_id': 'once', 'session_id': self.current()['id'],
                   'expected_revision': self.current()['revision'], 'run_id': self.run1,
                   'kind': 'proceed', 'path': [{'segment_id': 'main', 'from_mp': 10, 'to_mp': 20}]}
        first = self.store.execute('dispatcher', True, 'draft', command, '06:15')
        second = self.store.execute('dispatcher', True, 'draft', command, '06:16')
        self.assertEqual(first, second)
        self.assertEqual(1, len(self.current()['warrants']))
        self.assertEqual(first, self.store.command_status('dispatcher', 'once'))
        self.assertIsNone(self.store.command_status('crew-a', 'once'))
        with self.assertRaisesRegex(USError, 'different data'):
            self.store.execute('dispatcher', True, 'draft', {**command, 'notes': 'different'}, '06:17')

    def test_two_connections_cannot_activate_conflicting_warrants_concurrently(self):
        first, second = self.draft(), self.draft(self.run2)
        self.read_back(first)
        self.read_back(second, 'crew-b')
        current = self.current()
        barrier = threading.Barrier(2)
        results = []
        def activate(warrant):
            store = USStore(self.path)
            try:
                barrier.wait(timeout=5)
                store.execute('dispatcher', True, 'activate', {'command_id': str(uuid4()),
                    'session_id': current['id'], 'expected_revision': current['revision'],
                    'warrant_id': warrant, 'confirmed': True}, '06:20')
                results.append('active')
            except USError as error:
                results.append(error.status)
            finally:
                store.close()
        threads = [threading.Thread(target=activate, args=(w,)) for w in (first, second)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertCountEqual(['active', 409], results)
        self.assertEqual(1, sum(w['status'] == 'active' for w in self.current()['warrants']))
        remaining = next(w for w in self.current()['warrants'] if w['status'] != 'active')
        with self.assertRaisesRegex(USError, 'Conflict'):
            self.call('activate', warrant_id=remaining['id'], confirmed=True)

    def test_restart_restores_authorities_reports_and_exact_event_order(self):
        self.active()
        self.call('report', 'crew-a', run_id=self.run1, kind='position', message='Stopped',
                  position={'segment_id': 'main', 'mp': 15})
        before = self.current()
        self.store.close()
        self.store = USStore(self.path)
        self.assertEqual(before, self.current())
        self.assertEqual(list(range(before['revision'], 0, -1)), [e['revision'] for e in before['events']])

    def test_package_is_frozen_and_next_session_has_new_run_ids(self):
        before = self.current()
        self.package['runs'][0]['symbol'] = 'changed later in Cloud'
        self.assertEqual(before['package'], self.current()['package'])
        with self.assertRaises(USError): self.call('create_session', package=self.package)
        self.call('finish_session')
        self.call('create_session', package=self.package)
        self.assertNotEqual(before['id'], self.current()['id'])
        self.assertTrue(set(r['id'] for r in before['runs']).isdisjoint(r['id'] for r in self.current()['runs']))

    def test_us_commands_do_not_modify_other_domains_in_the_shared_database(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE eu_fixture (state TEXT)')
            db.execute('INSERT INTO eu_fixture VALUES (?)', ('EU runtime remains unchanged',))
        self.active()
        with sqlite3.connect(self.path) as db:
            self.assertEqual([('EU runtime remains unchanged',)], db.execute('SELECT * FROM eu_fixture').fetchall())

    def test_invalid_profiles_paths_and_nonfinite_values_are_rejected(self):
        for changes in [{'profile': 'unreviewed'}, {'schema': 'trainmeet.runtime/3'}, {'nodes': []}]:
            with self.subTest(changes=changes), self.assertRaises(USError):
                validate_package({**self.package, **changes})
        for start, end in [(10, 10), (9, 15), (10, 21), (float('nan'), 20), (True, 20)]:
            with self.subTest(start=start, end=end), self.assertRaises(USError):
                validate_path(self.package, [{'segment_id': 'main', 'from_mp': start, 'to_mp': end}])
        detached = copy.deepcopy(self.package)
        detached['nodes'] += [{'id': 'c', 'territory_id': 't', 'name': 'C', 'mp': 30, 'x': 30, 'y': 200},
                              {'id': 'd', 'territory_id': 't', 'name': 'D', 'mp': 40, 'x': 30, 'y': 300}]
        detached['segments'].append({'id': 'detached', 'name': 'Detached', 'from_node': 'c', 'to_node': 'd'})
        with self.assertRaisesRegex(USError, 'not connected'):
            validate_path(detached, [{'segment_id': 'main', 'from_mp': 10, 'to_mp': 20},
                                     {'segment_id': 'detached', 'from_mp': 30, 'to_mp': 40}])

