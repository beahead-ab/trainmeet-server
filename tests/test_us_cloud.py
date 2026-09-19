"""Cloud-only US selection and safe config adoption preserve operational state."""
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
from tmbox_gateway.central_sync import CentralRuntimeDownload, CentralRuntimeManifest, CentralSyncError
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
        self.assertIn('SP 834 · Morning', warrant_text(self.p, warrant, {**r, 'service': 'Morning'}))
        self.assertIn('SP 834 · Evening', warrant_text(self.p, warrant, {**r, 'service': 'Evening'}))
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


class USCloudHTTPTests(test_us_http.USHTTPFixture):
    def setUp(self):
        super().setUp()
        self.offered = cloud_package()
        self.downloads = []
        self.linked_requests = []
        def fetch(code, url):
            self.downloads.append((code, url))
            return CentralRuntimeDownload(copy.deepcopy(self.offered), 'us-link-secret')
        def linked(token, url, manifest):
            self.linked_requests.append((token, url, manifest))
            if manifest:
                encoded = json.dumps(self.offered, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
                return CentralRuntimeManifest(self.offered['publication_id'], self.offered.get('published_at', ''), hashlib.sha256(encoded).hexdigest())
            return CentralRuntimeDownload(copy.deepcopy(self.offered), token)
        self.app.runtime_fetcher = fetch
        self.app.linked_runtime_fetcher = linked

    def connect(self, **extra):
        return self.request('/v1/runtime/sync', {'sync_code':'123456', 'central_url':'https://us.example/config', **extra}, 'admin-token')

    def start(self):
        status, result = self.request('/v1/us/commands', self.start_command(), 'admin-token')
        self.assertEqual(200, status, result)
        return self.request('/v1/us/context', token='admin-token')[1]

    def test_cloud_selection_is_shared_and_offline_start_needs_no_cloud(self):
        status, downloaded = self.connect()
        self.assertEqual(201, status, downloaded)
        selected = self.app.lifecycle.selected()
        self.assertEqual('us', selected['region'])
        self.assertEqual('meet-us', selected['meet_id'])
        self.assertIsNone(self.runtime.active())
        self.assertEqual({}, self.app.engine.config.panels)
        self.assertEqual('us-link-secret', self.runtime.link_token())
        self.assertTrue(self.runtime.cloud_auto_sync_enabled())
        detail = '/v1/us/package?publication_id=' + selected['publication_id']
        self.assertEqual(401, self.request(detail)[0])
        self.assertEqual(403, self.request(detail, token='eu-token')[0])
        self.assertEqual(cloud_package(), self.request(detail, token='admin-token')[1]['package'])
        self.app.runtime_fetcher = lambda *_: (_ for _ in ()).throw(CentralSyncError('Offline'))
        self.app.linked_runtime_fetcher = self.app.runtime_fetcher
        context = self.start()
        self.assertEqual('05:30:00', context['clock']['time'])
        self.assertFalse(context['clock']['running'])
        self.assertEqual('us', context['clock']['scope'])
        self.assertNotIn('us-link-secret', json.dumps(context))
        self.assertEqual(selected, self.app.lifecycle.selected())

    def test_cloud_selection_completes_setup_without_starting_traffic(self):
        self.runtime.begin_installation()
        self.assertTrue(self.runtime.installation_required())
        status, response = self.connect()
        self.assertEqual(201, status, response)
        self.assertFalse(self.runtime.installation_required())
        self.assertIsNone(self.store.context('admin', True)['session'])
        self.assertIsNone(self.runtime.active())
        self.assertEqual('us', self.app.lifecycle.selected()['region'])

    def test_manual_check_and_automatic_poll_share_safe_adoption(self):
        self.connect()
        before = self.start()
        self.offered = {**self.offered, 'publication_id':'newer', 'name':'Updated display name'}
        status, result = self.request('/v1/config/check', {}, 'admin-token')
        self.assertEqual(200, status, result)
        self.assertFalse(result['pending'])
        after = self.request('/v1/us/context', token='admin-token')[1]
        self.assertEqual('newer', after['session']['package']['publication_id'])
        self.assertEqual(before['session']['id'], after['session']['id'])
        self.assertEqual(before['session']['runs'], after['session']['runs'])
        self.assertEqual(before['clock'], after['clock'])
        self.assertEqual('newer', self.app.lifecycle.selected()['publication_id'])
        self.assertEqual(1, len(self.downloads))
        self.assertEqual([True, False], [entry[2] for entry in self.linked_requests])
        self.assertFalse(self.app.auto_sync_cloud_runtime()['pending'])

    def test_active_warrant_defers_topology_update_without_resetting_clock_or_session(self):
        self.connect()
        current = self.start()['session']
        status, drafted = self.request('/v1/us/commands', {
            'action':'draft', 'command_id':str(uuid4()), 'session_id':current['id'],
            'expected_revision':current['revision'], 'run_id':current['runs'][0]['id'],
            'kind':'proceed', 'path':[{'segment_id':'main','from_mp':10,'to_mp':20}]}, 'admin-token')
        self.assertEqual(200, status, drafted)
        before = self.store.context('admin', True)['session']
        self.offered = copy.deepcopy(self.offered)
        self.offered['publication_id'] = 'topology-v2'
        self.offered['nodes'][1]['mp'] = 25
        result = self.app.auto_sync_cloud_runtime()
        self.assertTrue(result['pending'])
        self.assertEqual('waiting', result['state'])
        self.assertEqual(before, self.store.context('admin', True)['session'])
        self.assertEqual('test-terms', self.app.lifecycle.selected()['publication_id'])
        self.assertEqual(2, len(self.store.package_catalogue()))

    def test_a_different_meet_needs_confirmation_and_cannot_replace_running_session(self):
        self.connect()
        before = self.start()['session']
        self.offered = {**self.offered, 'publication_id':'other-meet', 'session':{**self.offered['session'], 'id':'other'}}
        for extra in ({}, {'confirm_meet_change':True}):
            status, result = self.connect(**extra)
            self.assertEqual(409, status, result)
        self.assertEqual(before, self.store.context('admin', True)['session'])
        self.assertEqual('meet-us', self.app.lifecycle.selected()['meet_id'])

    def test_checksum_failure_and_bad_package_preserve_selection_and_link(self):
        self.connect()
        before = self.app.lifecycle.selected()
        self.offered = {**self.offered, 'publication_id':'bad-checksum'}
        self.app.linked_runtime_fetcher = lambda token, url, manifest: (
            CentralRuntimeManifest('bad-checksum', '', 'incorrect') if manifest else CentralRuntimeDownload(self.offered, token))
        status, result = self.request('/v1/config/check', {}, 'admin-token')
        self.assertEqual(409, status, result)
        self.assertEqual(before, self.app.lifecycle.selected())
        self.assertEqual(1, len(self.store.package_catalogue()))
        self.app.runtime_fetcher = lambda *_: CentralRuntimeDownload({}, 'wrong-link')
        self.assertEqual(409, self.connect()[0])
        self.assertEqual('us-link-secret', self.runtime.link_token())
        self.assertEqual(before, self.app.lifecycle.selected())

    def test_download_auth_and_removed_file_import_do_not_expose_cloud_keys(self):
        self.identities.register_client('crew-test', 'Crew', DeviceKind.US_CONDUCTOR, 'crew-token', ())
        for token, expected in [(None,401),('crew-token',403),('eu-token',403)]:
            for path, body in [('/v1/runtime/sync',{'sync_code':'123456'}),
                               ('/v1/config/check',{}),('/v1/us/packages',{'package':cloud_package()})]:
                self.assertEqual(expected, self.request(path,body,token)[0])
        self.assertEqual([],self.downloads)
        self.connect()
        self.assertEqual(410,self.request('/v1/us/packages',{'package':cloud_package()},'admin-token')[0])
        context=self.request('/v1/us/context',token='crew-token')[1]
        self.assertNotIn('cloud',context)
        self.assertNotIn('packages',context)
        self.assertNotIn('us-link-secret',json.dumps(context))
        self.assertEqual(403,self.request('/v1/us/package?publication_id=test-terms',token='crew-token')[0])

    def test_invalid_url_and_code_are_rejected_before_network_access(self):
        for url in ['ftp://host/config','https://user:password@host/config','https://host/config#token']:
            self.assertEqual(409,self.connect(central_url=url)[0])
        for code in ['12345','1234567','abcdef','１２３４５６']:
            self.assertEqual(409,self.connect(sync_code=code)[0])
        self.assertEqual([],self.downloads)
