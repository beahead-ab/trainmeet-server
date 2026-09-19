const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { render } = require('../../src/tmbox_gateway/web/tmbox-render.js');

function client() {
  const source = fs.readFileSync(path.join(__dirname, '../../src/tmbox_gateway/web/app.js'), 'utf8');
  const code = source.slice(source.indexOf('async function refreshTMBoxV2()'), source.indexOf('function v2Now()'));
  const offered = { version: 1, failConfig: false, mismatchSnapshot: false, requests: [] };
  const scope = () => ({ meet_generation: offered.version, publication_id: `pub-${offered.version}` });
  const element = { value: '', replaceChildren() {}, classList: { remove() {} } };
  const state = { browser: { client_id: 'box' }, configFor: null,
    nav: { view: { screen: 'Identity' }, show(screen) { this.view.screen = screen; }, reconcile() {} },
    attention: { forget() {}, observe() {}, observeLink() {} } };
  const context = vm.createContext({ tmboxV2: state, v2Now: () => 0, v2El: () => element,
    v2Signal() {}, drawV2() {}, setMessage() {}, Option: class {},
    document: { querySelector: () => element },
    async boxFetch(url) {
      offered.requests.push(url.split('?')[0]);
      const data = url.includes('/assignment')
        ? { ...scope(), config_version: offered.version, status: 'assigned', station_id: 'same-station' }
        : url.includes('/config')
          ? { ...scope(), config_version: offered.version, station: { id: 'same-station', code: 'CDA' }, connections: [{ connection_id: 'line', other_station_code: 'MUN', display_side: offered.version === 1 ? 'left' : 'right' }] }
          : { ...scope(), publication_id: offered.mismatchSnapshot ? 'newer-publication' : scope().publication_id, revision: { config_version: offered.version }, movements: [], active_clearances: [] };
      return { ok: !(url.includes('/config') && offered.failConfig), status: 200, json: async () => data };
    } });
  vm.runInContext(code, context);
  return { state, offered, refresh: () => context.refreshTMBoxV2() };
}

test('same station gets new Cloud config without reloading or reassigning the box', async () => {
  const { state, offered, refresh } = client();
  await refresh();
  assert.equal(state.config.connections[0].display_side, 'left');
  state.nav.show('ConnectionPicker');
  await refresh();
  assert.equal(state.nav.view.screen, 'ConnectionPicker');
  assert.equal(offered.requests.filter(path => path.endsWith('/config')).length, 1);
  offered.version = 2;
  await refresh();
  assert.equal(state.config.connections[0].display_side, 'right');
  assert.equal(state.nav.view.screen, 'StationOverview', 'old picker selection must not survive changed ordering');
  assert.equal(offered.requests.filter(path => path.endsWith('/config')).length, 2);
});

test('failed config load and an activation racing the snapshot never leave old choices active', async () => {
  const { state, offered, refresh } = client();
  await refresh();
  offered.version = 2;
  offered.failConfig = true;
  await refresh();
  assert.equal(state.configFor, null);
  assert.equal(state.config.connections.length, 0);
  assert.equal(state.nav.view.screen, 'LoadingStation');
  offered.failConfig = false;
  offered.mismatchSnapshot = true;
  await refresh();
  assert.equal(state.configFor, null);
  assert.equal(state.nav.view.screen, 'LoadingStation');
  offered.mismatchSnapshot = false;
  await refresh();
  assert.equal(state.nav.view.screen, 'StationOverview');
});

test('station label follows Cloud side on all display sizes without remapping function keys', () => {
  for (const [cols, rows] of [[16, 2], [20, 2], [16, 4], [20, 4]]) {
    const config = { connections: [{ other_station_code: 'MUN', display_side: 'left' }] };
    const view = { screen: 'ConnectionPicker', selected_connection: 0 };
    const left = render({ cols, rows }, view, config, {});
    config.connections[0].display_side = 'right';
    const right = render({ cols, rows }, view, config, {});
    assert.ok(left[0].startsWith('MUN'));
    assert.ok(right[0].indexOf('MUN') > 0);
    assert.equal(left[1], right[1]);
    assert.ok([...left, ...right].every(line => line.length === cols));
  }
});
