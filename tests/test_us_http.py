"""Real HTTP boundaries: EU clients cannot become US dispatchers or conductors."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from test_us_terminology import package
from tmbox_gateway.central_sync import CentralRuntimeDownload
from tmbox_gateway.engine import TrafficEngine
from tmbox_gateway.http_server import HTTPServerConfig, TrainMeetHTTPApplication, TrainMeetHTTPServer
from tmbox_gateway.identity import DeviceKind, IdentityStore, PairingService
from tmbox_gateway.models import unconfigured_session
from tmbox_gateway.operations import SQLiteOperationsStore
from tmbox_gateway.runtime import SQLiteRuntimeStore
from tmbox_gateway.us import USStore


class USHTTPFixture(unittest.TestCase):
    select_us = False

    def published_package(self):
        return {**package(), 'session': {'id': 'http-meet-us', 'clock_time': '06:00', 'clock_speed': 1}}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.identities = IdentityStore(Path(self.temp.name) / 'identity.db')
        self.addCleanup(self.identities.close)
        self.store = USStore(Path(self.temp.name) / 'us.db')
        self.addCleanup(self.store.close)
        self.runtime = SQLiteRuntimeStore(Path(self.temp.name) / 'runtime.db')
        self.addCleanup(self.runtime.close)
        self.ops = SQLiteOperationsStore(Path(self.temp.name) / 'runtime.db')
        self.addCleanup(self.ops.close)
        self.runtime.save_server_name('HTTP test server')
        engine = TrafficEngine(unconfigured_session())
        pairing = PairingService(self.identities, set(engine.config.panels))
        self.identities.register_client('admin-test', 'Test dispatcher', DeviceKind.WEB_ADMIN, 'admin-token', ())
        self.identities.register_client('eu-test', 'EU panel', DeviceKind.TKL_TERMINAL, 'eu-token', ('panel-a',))
        app = TrainMeetHTTPApplication(engine, self.identities, pairing,
            HTTPServerConfig(force_external_auth=True), us_store=self.store,
            runtime_store=self.runtime, operations_store=self.ops,
            runtime_fetcher=lambda _code, _url: CentralRuntimeDownload(self.published_package(), 'test-cloud-link'))
        self.app = app
        self.addCleanup(app.lifecycle.close)
        self.server = TrainMeetHTTPServer(('127.0.0.1', 0), app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        if self.select_us:
            status, response = self.request('/v1/runtime/sync', {'sync_code': '123456', 'central_url': 'https://test.example/config'}, 'admin-token')
            self.assertEqual(201, status, response)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)

    def request(self, path, payload=None, token=None):
        request = Request(self.base + path, data=None if payload is None else json.dumps(payload).encode(),
            headers={**({'Authorization': 'Bearer ' + token} if token else {}), 'Content-Type': 'application/json'})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def start_command(self):
        item = self.store.package_catalogue()[0]
        return {'command_id': str(uuid4()), 'action': 'create_session', 'publication_id': item['publication_id'],
                'package_checksum': item['checksum'], 'confirmed': True}


class USHTTPTests(USHTTPFixture):
    select_us = True

    def test_operational_api_requires_auth_and_eu_terminal_is_not_dispatcher(self):
        self.assertEqual(401, self.request('/v1/us/context')[0])
        self.assertEqual(403, self.request('/v1/us/context', token='eu-token')[0])
        self.assertEqual(403, self.request('/v1/us/conductor-code', {}, token='eu-token')[0])
        self.assertEqual('dispatcher', self.request('/v1/us/context', token='admin-token')[1]['role'])

    def test_one_time_conductor_pairing_does_not_grant_admin_or_eu_panels(self):
        status, code = self.request('/v1/us/conductor-code', {}, token='admin-token')
        self.assertEqual(201, status)
        status, client = self.request('/v1/pair', {'pairing_code': code['code'], 'device_kind': 'us_conductor', 'display_name': 'Sam'})
        self.assertEqual(201, status)
        self.assertEqual([], client['assigned_panel_ids'])
        token = client['access_token']
        self.assertEqual('conductor', self.request('/v1/us/context', token=token)[1]['role'])
        self.assertEqual(403, self.request('/v1/us/conductor-code', {}, token=token)[0])
        self.assertEqual(403, self.request('/v1/us/commands', self.start_command(), token=token)[0])
        self.assertEqual(401, self.request('/v1/pair', {'pairing_code': code['code'], 'device_kind': 'us_conductor', 'display_name': 'Other'})[0])

    def test_dispatcher_starts_only_selected_cloud_config_and_retries_are_idempotent(self):
        command = self.start_command()
        status, receipt = self.request('/v1/us/commands', command, token='admin-token')
        self.assertEqual(200, status)
        status, context = self.request('/v1/us/context', token='admin-token')
        self.assertEqual(receipt['session_id'], context['session']['id'])
        self.assertEqual('SP 834', context['session']['runs'][0]['symbol'])
        status, duplicate = self.request('/v1/us/commands', command, token='admin-token')
        self.assertEqual(receipt, duplicate)
        self.assertEqual(receipt, self.request('/v1/us/command-status?command_id=' + command['command_id'], token='admin-token')[1]['result'])

    def test_inline_and_unselected_packages_cannot_bypass_cloud_selection(self):
        before = self.app.lifecycle.selected()
        status, result = self.request('/v1/us/packages', {'package': package()}, 'admin-token')
        self.assertEqual(410, status)
        self.assertEqual('cloud_authoring_only', result['error'])
        self.assertEqual(409, self.request('/v1/us/commands', {'command_id': str(uuid4()), 'action':'create_session', 'package':package()}, 'admin-token')[0])
        other = self.store.stage_package({**self.published_package(), 'publication_id':'unselected-history'})
        command = {**self.start_command(), 'publication_id':other['publication_id'], 'package_checksum':other['checksum']}
        self.assertEqual(409, self.request('/v1/us/commands', command, 'admin-token')[0])
        self.assertIsNone(self.store.context('admin', True)['session'])
        self.assertEqual(before, self.app.lifecycle.selected())

    def test_server_context_is_single_selected_meet_and_hides_secrets(self):
        status, context = self.request('/v1/server-context', token='admin-token')
        self.assertEqual(200, status)
        self.assertEqual('us', context['operating_region'])
        self.assertEqual('http-meet-us', context['selected_meet']['id'])
        self.assertNotIn('tkl', context['available_workspaces'])
        self.assertNotIn('test-cloud-link', json.dumps(context))

    def test_us_assets_are_served_locally_without_cloud(self):
        for path in ['/us/dispatcher', '/us/conductor', '/us/app.js', '/us/style.css',
                     '/us/workspace-messages.js']:
            with self.subTest(path=path), urlopen(self.base + path, timeout=3) as response:
                self.assertEqual(200, response.status)
                content = response.read().decode()
                self.assertNotIn('https://cloud.trainmeet.app', content)
