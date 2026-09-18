"""US Cloud packages are staged, never swapped into an operating session."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import test_us_http
from test_us_terminology import package
from runtime_fixture import runtime_package_v3
from tmbox_gateway.central_sync import CentralRuntimeDownload, CentralSyncError
from tmbox_gateway.identity import DeviceKind
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.us import USStore, USError, warrant_text


def cloud_package():
    p = package()
    p.update(operating_region='us', default_language='en', published_at='2026-09-18T15:00:00Z',
             session={'id': 'meet-us', 'clock_time': '05:30', 'clock_speed': 4, 'timezone': 'America/Chicago'},
             planning={'dispatcher_districts': [{'id': 'd', 'name': 'West desk', 'instructions': 'Ask before crossing', 'segment_ids': ['main']}],
                       'source_instructions': [{'id': 'n', 'text': 'Entire train clear'}], 'runtime_enforced': False})
    return p


class USCloudStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'db.sqlite'
        self.store = USStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.p = cloud_package()

    def start(self, item=None, **extra):
        item = item or self.store.stage_package(self.p)
        return self.store.execute('admin', True, 'create_session', {
            'command_id': str(uuid4()), 'publication_id': item['publication_id'],
            'package_checksum': item['checksum'], 'confirmed': True, **extra}, '09:15')

    def test_staging_is_immutable_persistent_and_does_not_start_traffic(self):
        item = self.store.stage_package(self.p, url='https://example.test/config', token='secret-link')
        expected = hashlib.sha256(json.dumps(self.p, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(expected, item['checksum'])
        self.assertIsNone(self.store.context('admin', True)['session'])
        self.assertEqual(item, self.store.stage_package(self.p))
        changed = {**self.p, 'name': 'Different'}
        with self.assertRaisesRegex(USError, 'publication ID'):
            self.store.stage_package(changed, url='https://other.test/config', token='bad-token')
        self.store.close(); self.store = USStore(self.path)
        self.assertEqual(('https://example.test/config', 'secret-link'), self.store.cloud_link())
        self.assertEqual([item], self.store.package_catalogue())
        self.assertNotIn('secret-link', json.dumps(self.store.package_catalogue()))
        self.assertEqual(self.p, self.store.saved_package(self.p['publication_id']))
        with self.assertRaises(USError):
            self.store.saved_package('missing')

    def test_one_off_config_download_clears_old_link_but_file_import_does_not(self):
        self.store.stage_package(self.p, url='https://old.test/config', token='old-token')
        self.store.stage_package(self.p)
        self.assertIsNotNone(self.store.cloud_link())
        self.store.stage_package(self.p, url='https://new.test/config')
        self.assertIsNone(self.store.cloud_link())

    def test_cloud_train_identity_is_retained_in_new_warrant_text(self):
        r = {**self.p['runs'][0], 'railroad': 'SP', 'symbol': '834'}
        warrant = {'number': '1', 'kind': 'proceed', 'path': [], 'notes': ''}
        self.assertIn('SP 834', warrant_text(self.p, warrant, r))
        self.assertNotIn('SP SP', warrant_text(self.p, warrant, {**r, 'symbol': 'SP 834'}))
        for field, value in [('railroad', []), ('service', 0)]:
            bad = copy.deepcopy(self.p)
            bad['runs'][0][field] = value
            with self.assertRaises(USError):
                self.store.stage_package(bad)

    def test_bad_or_unreviewed_package_cannot_change_link_or_start_session(self):
        for value in [{}, runtime_package_v3(), {**self.p, 'session': {'clock_speed': 0}},
                      {**self.p, 'session': {'clock_speed': True}},
                      {**self.p, 'session': {'clock_time': '25:00'}},
                      {**self.p, 'planning': {'runtime_enforced': True}}]:
            with self.subTest(value=value), self.assertRaises(USError):
                self.store.stage_package(value, token='bad', url='https://example.test')
        self.assertIsNone(self.store.cloud_link())
        for extra in [{'confirmed': False}, {'package_checksum': 'wrong'}]:
            with self.assertRaises(USError):
                self.start(**extra)
        self.assertIsNone(self.store.context('admin', True)['session'])

    def test_start_is_idempotent_and_new_download_cannot_replace_running_session(self):
        item = self.store.stage_package(self.p)
        command = {'command_id': 'start-once', 'publication_id': item['publication_id'], 'package_checksum': item['checksum'], 'confirmed': True}
        first = self.store.execute('admin', True, 'create_session', command, '09:15')
        self.assertEqual(first, self.store.execute('admin', True, 'create_session', command, '09:16'))
        before = self.store.context('admin', True)['session']
        newer = self.store.stage_package({**self.p, 'publication_id': 'v2', 'name': 'New plan'})
        with self.assertRaisesRegex(USError, 'Finish the current session'):
            self.start(newer)
        self.assertEqual(before, self.store.context('admin', True)['session'])

    def test_link_race_rejects_a_download_for_an_obsolete_connection(self):
        self.store.stage_package(self.p, url='https://a.test/config', token='a')
        old = self.store.cloud_link()
        self.store.stage_package(self.p, url='https://b.test/config', token='b')
        with self.assertRaisesRegex(USError, 'connection changed'):
            self.store.stage_package({**self.p, 'publication_id': 'old-link-v2'}, expected_link=old)
        self.assertEqual(1, len(self.store.package_catalogue()))

    def test_us_clock_uses_cloud_proposal_starts_paused_and_survives_restart(self):
        with patch('tmbox_gateway.us_clock.time.time', return_value=1000):
            self.start()
        before = self.store.context('admin', True)
        self.assertEqual('05:30:00', before['clock']['time'])
        self.assertEqual(4, before['clock']['speed'])
        self.assertFalse(before['clock']['running'])
        command = {'command_id': 'clock-once', 'session_id': before['session']['id'],
                   'expected_revision': before['session']['revision'], 'clock_time': '05:30:00',
                   'clock_speed': 4, 'running': True, 'confirmed': True}
        with patch('tmbox_gateway.us_clock.time.time', return_value=1000):
            receipt = self.store.execute('admin', True, 'clock', command, '09:15')
        self.store.close(); self.store = USStore(self.path)
        with patch('tmbox_gateway.us_clock.time.time', return_value=1015):
            self.assertEqual('05:31:00', self.store.context('crew', False)['clock']['time'])
            self.assertEqual(receipt, self.store.execute('admin', True, 'clock', command, '09:15'))
        with self.assertRaises(USError):
            self.store.execute('crew', False, 'clock', {**command, 'command_id': 'bad', 'expected_revision': receipt['revision']}, '00:00')


class USCloudHTTPTests(test_us_http.USHTTPTests):
    def setUp(self):
        super().setUp()
        self.app = self.server.application
        self.eu = SQLiteRuntimeStore(Path(self.temp.name) / 'eu.db')
        self.addCleanup(self.eu.close)
        self.eu.install(runtime_package_v3())
        self.eu.save_link_token('keep-eu-link')
        self.eu.save_central_url('https://eu.example/config')
        self.app.runtime_store = self.eu
        self.ops = SQLiteOperationsStore(Path(self.temp.name) / 'eu.db')
        self.addCleanup(self.ops.close)
        self.app.operations_store = self.ops
        self.downloads = []
        def fetch(code, url):
            self.downloads.append((code, url))
            return CentralRuntimeDownload(cloud_package(), 'us-link-secret')
        self.app.runtime_fetcher = fetch
        self.app.linked_runtime_fetcher = lambda token, url, manifest: CentralRuntimeDownload({**cloud_package(), 'publication_id': 'newer'}, token)

    def connect(self, **extra):
        return self.request('/v1/us/cloud/download', {'sync_code': '123456', 'central_url': 'https://us.example/config', **extra}, 'admin-token')

    def test_cloud_download_then_offline_start_preserves_eu_clock_link_and_engine(self):
        before = (self.eu.summary(), self.ops.clock_status(), self.app.engine.export_state())
        status, downloaded = self.connect()
        self.assertEqual(200, status)
        p = downloaded['package']
        detail = '/v1/us/package?publication_id=' + p['publication_id']
        self.assertEqual(401, self.request(detail)[0])
        self.assertEqual(403, self.request(detail, token='eu-token')[0])
        self.assertEqual(cloud_package(), self.request(detail, token='admin-token')[1]['package'])
        status, context = self.request('/v1/us/context', token='admin-token')
        self.assertIsNone(context['session'])
        self.assertNotIn('us-link-secret', json.dumps(context))
        self.app.runtime_fetcher = lambda *_: (_ for _ in ()).throw(CentralSyncError('Offline'))
        self.app.linked_runtime_fetcher = self.app.runtime_fetcher
        status, started = self.request('/v1/us/commands', {'action': 'create_session', 'command_id': str(uuid4()),
            'publication_id': p['publication_id'], 'package_checksum': p['checksum'], 'confirmed': True}, 'admin-token')
        self.assertEqual(200, status)
        context = self.request('/v1/us/context', token='admin-token')[1]
        self.assertEqual('05:30:00', context['clock']['time'])
        self.assertEqual('us', context['clock']['scope'])
        self.assertEqual(before, (self.eu.summary(), self.ops.clock_status(), self.app.engine.export_state()))
        self.assertEqual('keep-eu-link', self.eu.link_token())

    def test_regular_setup_sync_routes_us_before_mutating_eu_settings(self):
        before = self.eu.summary()
        status, response = self.request('/v1/runtime/sync', {'sync_code': '123456', 'central_url': 'https://us.example/config'}, 'admin-token')
        self.assertEqual(201, status)
        self.assertEqual('us', response['operating_region'])
        self.assertFalse(response['restart_required'])
        self.assertEqual(before, self.eu.summary())
        self.assertEqual('keep-eu-link', self.eu.link_token())
        self.assertEqual('https://eu.example/config', self.eu.central_url())

    def test_fresh_installation_is_completed_only_on_explicit_us_start(self):
        fresh = SQLiteRuntimeStore(Path(self.temp.name) / 'fresh.db')
        self.addCleanup(fresh.close)
        fresh.save_server_name('US test server')
        fresh.begin_installation()
        self.app.runtime_store = fresh
        _, download = self.connect()
        self.assertTrue(fresh.installation_required())
        item = download['package']
        status, _ = self.request('/v1/us/commands', {'action': 'create_session', 'command_id': str(uuid4()),
            'publication_id': item['publication_id'], 'package_checksum': item['checksum'], 'confirmed': True}, 'admin-token')
        self.assertEqual(200, status)
        self.assertFalse(fresh.installation_required())
        self.assertIsNone(fresh.active())

    def test_linked_update_stages_without_requiring_original_code(self):
        self.connect()
        status, response = self.request('/v1/us/cloud/download', {}, 'admin-token')
        self.assertEqual(200, status)
        self.assertEqual('newer', response['package']['publication_id'])
        self.assertEqual(2, len(self.store.package_catalogue()))
        self.assertIsNone(self.store.context('admin', True)['session'])

    def test_download_auth_and_bad_package_leave_existing_link_untouched(self):
        self.identities.register_client('crew-test', 'Crew', DeviceKind.US_CONDUCTOR, 'crew-token', ())
        for token, expected in [(None, 401), ('crew-token', 403), ('eu-token', 403)]:
            for path, body in [('/v1/us/cloud/download', {'sync_code': '123456'}), ('/v1/us/packages', {'package': cloud_package()})]:
                self.assertEqual(expected, self.request(path, body, token)[0])
        self.assertEqual([], self.downloads)
        self.connect()
        link = self.store.cloud_link()
        self.app.runtime_fetcher = lambda *_: CentralRuntimeDownload(runtime_package_v3(), 'wrong-link')
        self.assertEqual(400, self.connect()[0])
        self.assertEqual(link, self.store.cloud_link())
        context = self.request('/v1/us/context', token='crew-token')[1]
        self.assertNotIn('cloud', context); self.assertNotIn('packages', context)
        self.assertEqual(403, self.request('/v1/us/package?publication_id=test-terms', token='crew-token')[0])

    def test_bad_url_is_rejected_before_network_access(self):
        for url in ['ftp://host/config', 'https://user:password@host/config', 'https://host/config#token']:
            self.assertEqual(400, self.connect(central_url=url)[0])
        for code in ['12345', '1234567', 'abcdef', '１２３４５６']:
            self.assertEqual(400, self.connect(sync_code=code)[0])
        self.assertEqual([], self.downloads)
