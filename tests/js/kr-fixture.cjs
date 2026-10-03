// Gemensam fixtur för Kontrollrummets webbläsartester (Drift, Inställningar): riktig webb-kod
// från repot med en strikt CSP, men API:et är en fixtur som liknar träffen i designen
// (Grimslöv 2027, elva stationer). Ingen server, ingen Cloud, inga användardata.
// open({ route, theme, width, height, sim, running, lang }) → { page, browser, errors, violations, state }
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

const web = path.resolve(__dirname, '../../src/tmbox_gateway/web');
const CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'";

const S = [ // id, code, name
  ['cst', 'CST', 'Stockholm C'], ['dev', 'DEV', 'Devsjö'], ['ac', 'AC', 'Alvesta C'], ['lek', 'LEK', 'Lekby'],
  ['cda', 'CDA', 'Charlottendal'], ['vag', 'VAG', 'Vagnsta'], ['mun', 'MUN', 'Munkeröd'], ['kru', 'KRU', 'Krutträsk'],
  ['kuf', 'KUF', 'Kungsfors'], ['eli', 'ELI', 'Elisabethstad'], ['vax', 'VÄX', 'Växjö'],
];
const stations = S.map(([id, code, name]) => ({ id, code, name }));
const links = [['cst', 'dev'], ['dev', 'ac'], ['ac', 'lek'], ['lek', 'cda'], ['cda', 'vag'], ['vag', 'mun'], ['ac', 'kru'], ['cda', 'kuf'], ['ac', 'eli'], ['eli', 'vax']];
const connections = links.map(([a, b], i) => ({ id: `c${i + 1}`, station_a_id: a, station_b_id: b, track_type: i === 1 || i === 3 ? 'double' : 'single' }));
const cid = (a, b) => (connections.find(c => (c.station_a_id === a && c.station_b_id === b) || (c.station_a_id === b && c.station_b_id === a)) || {}).id;

// tåg: [nummer, [[station, ank, avg], ...]]
const T = [
  ['102', [['cst', null, '05:10'], ['dev', '05:19', '05:20'], ['ac', '05:30', '05:32'], ['lek', '05:38', '05:39'], ['cda', '05:50', '05:52'], ['vag', '06:02', '06:03'], ['mun', '06:13', null]]],
  ['3902', [['kru', null, '05:05'], ['ac', '05:22', '05:25'], ['lek', '05:34', null]]],
  ['3581', [['cda', null, '05:05'], ['kuf', '05:18', '05:30']]],
  ['93', [['cda', null, '05:15'], ['lek', '05:25', '05:26'], ['ac', '05:37', '05:38'], ['dev', '05:48', '05:49'], ['cst', '05:55', null]]],
  ['94', [['vag', null, '05:27'], ['mun', '05:38', null]]],
  ['4720', [['cst', null, '05:23'], ['dev', '05:35', null]]],
  ['8282', [['vax', null, '05:25'], ['eli', '05:35', null]]],
  ['3511', [['mun', null, '05:25'], ['vag', '05:40', '05:41'], ['cda', '05:52', null]]],
  ['4152', [['cda', null, '05:30'], ['vag', '05:41', '05:42'], ['mun', '05:53', null]]],
  ['421', [['eli', null, '05:33'], ['ac', '05:50', '05:51'], ['dev', '06:02', '06:03'], ['cst', '06:12', null]]],
  ['5301', [['kuf', null, '06:20'], ['cda', '06:34', '06:35'], ['lek', '06:44', null]]],
  ['5302', [['lek', null, '04:30'], ['ac', '04:41', '04:42'], ['dev', '04:53', null]]],
];
const services = T.map(([n, stops]) => ({
  id: `s-${n}`, train_number: n, days: 'Dagl',
  stops: stops.map(([station_id, arrival_time, departure_time], i) => ({ station_id, stop_order: i, arrival_time, departure_time })),
}));
const routes = services.flatMap(s => s.stops.map(st => ({ train_number: s.train_number, station_id: st.station_id, arrival_time: st.arrival_time, departure_time: st.departure_time, service_id: s.id })));

const atStation = { cst: 11, dev: 9, ac: 18, lek: 11, cda: 14, vag: 14, mun: 13, kru: 5, kuf: 8, eli: 10, vax: 8 };
function positions() {
  const out = [];
  let n = 6000;
  for (const [st, count] of Object.entries(atStation)) for (let i = 0; i < count; i++) out.push({ train_number: String(n++), status: 'station', station_id: st });
  out.push({ train_number: '102', status: 'connection', connection_id: cid('cst', 'dev'), from_station_id: 'cst', to_station_id: 'dev' });
  out.push({ train_number: '3902', status: 'connection', connection_id: cid('kru', 'ac'), from_station_id: 'kru', to_station_id: 'ac' });
  out.push({ train_number: '3581', status: 'connection', connection_id: cid('cda', 'kuf'), from_station_id: 'cda', to_station_id: 'kuf' });
  return out;
}
function connectionStates() {
  const ch = (id, train, from, to, state) => ({ id, state: 'occupied', channels: [{ train_number: train, from_station_id: from, to_station_id: to, state }] });
  return [
    ch(cid('cst', 'dev'), '102', 'cst', 'dev', 'occupied'),
    ch(cid('kru', 'ac'), '3902', 'kru', 'ac', 'occupied'),
    ch(cid('cda', 'kuf'), '3581', 'cda', 'kuf', 'occupied'),
    ch(cid('lek', 'cda'), '93', 'cda', 'lek', 'reserved'),
    ch(cid('vag', 'mun'), '94', 'vag', 'mun', 'reserved'),
    ch(cid('dev', 'ac'), '4720', 'dev', 'ac', 'reserved'),
  ];
}

const deviceRows = () => ([
  { device_id: 'esp-1a2b3c', device_code: 'TBX-1A2B3C', model: 'ESP8266', station_id: null, connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'web-iphone', device_code: 'WEB-K3M9', model: 'Virtuell · Caspers iPhone', station_id: null, connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' } },
  { device_id: 'esp-9f02', device_code: 'TBX-9F02', model: 'ESP8266', station_id: 'cst', station_side: 'both', connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'esp-3c11', device_code: 'TBX-3C11', model: 'ESP8266', station_id: 'dev', station_side: 'both', connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'esp-77a0', device_code: 'TBX-77A0', model: 'ESP8266', station_id: 'lek', station_side: 'both', connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'esp-1288', device_code: 'TBX-1288', model: 'ESP8266', station_id: 'cda', station_side: 'both', connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'esp-5d5d', device_code: 'TBX-5D5D', model: 'ESP8266', station_id: 'vag', station_side: 'both', connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'esp-a041', device_code: 'TBX-A041', model: 'ESP8266', station_id: 'mun', station_side: 'both', connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'esp-0b3e', device_code: 'TBX-0B3E', model: 'ESP8266', station_id: 'kuf', station_side: 'both', connection: { state: 'online', last_seen: '2026-10-03T05:12:00Z' }, language: 'sv' },
  { device_id: 'esp-4e4e', device_code: 'TBX-4E4E', model: 'ESP8266', station_id: 'eli', station_side: 'both', connection: { state: 'lost', last_seen: '2026-10-03T05:09:00Z' }, language: 'sv' },
]);

const sides = { // vänster · höger enligt Cloud-placeringen i designen
  cst: [[], ['DEV']], dev: [['CST'], ['AC']], ac: [['LEK'], ['KRU', 'DEV', 'ELI']], lek: [['AC'], ['CDA']],
  cda: [['LEK'], ['VAG', 'KUF']], vag: [['CDA'], ['MUN']], mun: [['VAG'], []], kru: [['AC'], []], kuf: [['CDA'], []], eli: [['AC'], ['VÄX']], vax: [['ELI'], []],
};
function presentation(findings) {
  return {
    supported: true, publication_id: 'pub-9', config_version: 9, findings,
    stations: S.map(([id, code, name]) => {
      const [l, r] = sides[id];
      const conns = [...l.map(c => ({ side: 'left', c })), ...r.map(c => ({ side: 'right', c }))].map(({ side, c }, i) => {
        const other = S.find(x => x[1] === c);
        return { connection_id: `${id}-${other[0]}`, other_station_code: c, other_station_name: other[2], default_side: side, side, overridden: false };
      });
      return { station_id: id, code, name, connections: conns };
    }),
  };
}

async function open(opts = {}) {
  const o = { theme: 'dark', width: 1440, height: 900, sim: false, running: true, route: '/drift', lang: 'sv', region: 'eu', findings: true, ...opts };
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  const context = await browser.newContext({ locale: o.lang === 'sv' ? 'sv-SE' : 'en-GB', viewport: { width: o.width, height: o.height }, deviceScaleFactor: o.dpr || 1 });
  if (o.theme) await context.addInitScript(([t, lang]) => { try { localStorage.setItem('trainmeet.theme', t); localStorage.setItem('trainmeet.language', lang); } catch {} }, [o.theme, o.lang]);
  const page = await context.newPage();
  const errors = [], violations = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (['error', 'warning'].includes(m.type())) { const t = m.text(); if (!/events|503|Failed to load resource/.test(t)) errors.push(m.type() + ': ' + t); } });
  const st = { running: o.running, sim: o.sim, devices: deviceRows(), time: o.time || '05:12:40', speed: 4 };
  const findings = o.findings ? Array.from({ length: 5 }, (_, i) => ({ level: 'observation', rule: 'C', message: `Tåg ${424 + i} följer en annan väg än sin tabell (${i + 1})` })) : [];
  const clock = () => ({ configured: true, running: st.running, time: st.time, speed: st.speed, source: 'internal', external_name: '', available: true, can_control: true, style: 'digital', show_seconds: true });
  const runtime = { configured: true, linked: true, cloud_auto_sync: true, meet_name: 'Grimslöv 2027', active_day: 'Dagl', publication_id: 'pub-9', server_name: 'Raspberry Pi – Grimslöv 2027', central_url: 'https://cloud.trainmeet.example', station_count: 11 };
  const ctx = () => ({ selected_meet: { id: 'meet-1', name: 'Grimslöv 2027', publication_id: 'pub-9', operating_region: o.region, generation: 7, version_number: 9 }, operating_region: o.region, available_workspaces: ['administration', 'tmbox'], public_clients_enabled: true, cloud_update: { linked: true, state: 'ok' } });
  const simulation = () => st.sim ? {
    supported: true, active: true, day: 'Dagl', seed: 'S-7', clock: { running: true, time: st.time.slice(0, 5), speed: 4 },
    stations: [{ id: 'ac', code: 'AC', name: 'Alvesta C', mode: 'automatic' }, { id: 'vax', code: 'VÄX', name: 'Växjö', mode: 'automatic' }, { id: 'cst', code: 'CST', name: 'Stockholm C', mode: 'manual', operator: 'TBX-9F02' }],
    trains: [{ train_number: '421', from_station_id: 'vax', to_station_id: 'eli', status: 'in_transit', reason: '' }, { train_number: '8282', from_station_id: 'eli', to_station_id: 'ac', status: 'waiting', reason: 'channel_occupied' }],
  } : { supported: true, active: false };
  let selectedTrainCalls = 0;
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith('/v1/')) {
      let data = {};
      switch (url.pathname) {
        case '/v1/setup': case '/v1/setup/status': data = { required: false, admin_configured: true, runtime }; break;
        case '/v1/auth/status': data = { authenticated: true, at_the_machine: false, username: 'admin' }; break;
        case '/v1/workspaces': case '/v1/server-context': data = ctx(); break;
        case '/v1/runtime': data = runtime; break;
        case '/v1/cloud/presentation': data = presentation(findings); break;
        case '/v1/info': data = { gateway_id: runtime.server_name, server_name: runtime.server_name, traffic_session_name: 'Grimslöv 2027', runtime }; break;
        case '/v1/devices': data = { devices: st.devices, terminals: [], stations, languages: [{ code: 'sv', name: 'Svenska' }, { code: 'en', name: 'English' }] }; break;
        case '/v1/devices/assign': { const b = JSON.parse(request.postData()); const d = st.devices.find(x => x.device_code === b.device_code); if (d) { d.station_id = b.station_id; d.station_side = b.side || 'both'; } data = { assigned: true }; break; }
        case '/v1/display/connection': data = { host: '192.168.1.20', port: 8787, code: '262-617', code_state: 'valid', screens: [], validity_hours: 0, wifi: { name: 'Grimslov2027', password: 'tagen2027' }, web_client_ttl_minutes: 30 }; break;
        case '/v1/admin/users': data = { role: 'owner', users: [{ user_id: 'u-1', username: 'admin', role: 'owner', invitation_pending: false }], user: { user_id: 'u-1', username: 'admin', role: 'owner' } }; break;
        case '/v1/clock': if (request.method() === 'POST') { const b = JSON.parse(request.postData() || '{}'); if (b.action === 'start') st.running = true; if (b.action === 'stop') st.running = false; } data = clock(); break;
        case '/v1/clock/source': data = { source: 'internal', clock_name: '', user: '', has_password: false, poll_interval: 2 }; break;
        case '/v1/display': data = { clock: clock(), meet: { id: 'meet-1', name: 'Grimslöv 2027' }, active_day: 'Dagl', publication_id: 'pub-9', stations, connections, routes, services, train_positions: positions(), connection_states: connectionStates(), connection: { screens: [] }, display: {} }; break;
        case '/v1/simulation': data = simulation(); break;
        case '/v1/train': {
          const n = url.searchParams.get('number'); const s = services.find(x => x.train_number === n); selectedTrainCalls++;
          data = { train_number: n, active_day: 'Dagl', services: s ? [{ service_id: s.id, train_type: 'Persontåg', delay_minutes: 0,
            now: n === '102' ? { state: 'on_line', from_station_id: 'cst', to_station_id: 'dev', since: '05:10' } : { state: 'none' },
            stops: s.stops.map(x => { const sx = S.find(y => y[0] === x.station_id); return { station_id: x.station_id, station_code: sx[1], station_name: sx[2], arrival_time: x.arrival_time, departure_time: x.departure_time, movement_id: `m-${n}-${x.stop_order}`, planned_track: '1', actual_track: null, arrival: 'none', departure: x.stop_order === 0 && n === '102' ? 'departed' : 'none' }; }) }] : [] };
          break;
        }
        case '/v1/events': return route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
        case '/v1/server/update': data = process.env.KR_UPDATE === 'running'
          ? { supported: true, status: 'running', installed_version: '2.2.0', installed_build: '4bd9c9a1e2f3', latest_version: '2.3.0', latest_build: '9c1d2e3f4a5b', update_available: true, message: 'Uppdaterar …', steps: [{ label: 'Kontrollera version', state: 'done' }, { label: 'Hämta ny version', state: 'done' }, { label: 'Säkerhetskopiera', state: 'active' }, { label: 'Installera', state: 'pending' }, { label: 'Starta om', state: 'pending' }, { label: 'Kontrollera', state: 'pending' }, { label: 'Klart', state: 'pending' }] }
          : { supported: true, status: 'idle', installed_version: '2.2.0', installed_build: '4bd9c9a1e2f3', latest_version: '2.3.0', latest_build: '9c1d2e3f4a5b', update_available: process.env.KR_UPDATE !== 'none', steps: [{ label: 'Kontrollera version', state: 'pending' }, { label: 'Hämta ny version', state: 'pending' }, { label: 'Säkerhetskopiera', state: 'pending' }, { label: 'Installera', state: 'pending' }, { label: 'Starta om', state: 'pending' }, { label: 'Kontrollera', state: 'pending' }, { label: 'Klart', state: 'pending' }] }; break;
        case '/v1/server/backups': data = { backups: [{ name: 'b1', usable: true, taken_at: '2026-10-02T21:14:00Z', version: '2.1.0' }] }; break;
        default: data = { backups: [], panels: [], message: 'Sparat' };
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
    }
    const name = url.pathname.startsWith('/assets/') ? url.pathname.slice(8) : url.pathname.endsWith('.png') ? 'trainmeet-logo.png' : 'index.html';
    const target = path.resolve(web, name);
    if (!target.startsWith(web + path.sep) || !fs.existsSync(target)) return route.fulfill({ status: 404 });
    const ext = path.extname(target);
    const contentType = { '.js': 'application/javascript', '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' }[ext] || 'application/octet-stream';
    return route.fulfill({ status: 200, contentType, headers: { 'Content-Security-Policy': CSP }, body: fs.readFileSync(target) });
  });
  page.on('console', m => { if (/Content Security Policy|Refused to/i.test(m.text())) violations.push(m.text()); });
  await page.goto('http://127.0.0.1:9999' + o.route);
  return { page, browser, errors, violations, state: st, o };
}

module.exports = { open, services, stations };
