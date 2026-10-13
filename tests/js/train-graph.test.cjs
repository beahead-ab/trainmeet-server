// Den grafiska tidtabellen (train-graph.js): banan som linjer, avstånden ur
// gångtiderna, spårraderna, tågens delsträckor och minuter, och skärmens
// höjd. Rena funktioner över /v1/display-bilden, utan webbläsare.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const graph = require(path.resolve(__dirname, '../../src/tmbox_gateway/web/train-graph.js'));

// En bana med en huvudlinje a–b–c–d, en gren från b över e till f och ett
// stickspår g från d. Flest tåg kör a–d.
const S = [['a', 'A', 'Alvesta'], ['b', 'B', 'Bjärnum'], ['c', 'C', 'Charlottenberg'], ['d', 'D', 'Dalby'], ['e', 'E', 'Eslöv'], ['f', 'F', 'Falun'], ['g', 'G', 'Gnesta']];
const stations = S.map(([id, code, name]) => ({ id, code, name }));
const links = [['a', 'b'], ['b', 'c'], ['c', 'd'], ['b', 'e'], ['e', 'f'], ['d', 'g']];
const connections = links.map(([a, b], i) => ({ id: `c${i}`, station_a_id: a, station_b_id: b, track_type: 'single' }));
const tracks = [['a', '1'], ['a', '2'], ['b', '1'], ['b', '2'], ['b', '3'], ['c', '1'], ['d', '1'], ['d', '2']].map(([station, label], i) => ({ id: `t${i}`, station_id: station, display_label: label, sort_order: i, active: true }));

function stop(station_id, order, arrival, departure, extra = {}) {
  return { station_id, stop_order: order, arrival_time: arrival, departure_time: departure, service_day_offset: 0, ...extra };
}
function service(number, stops, extra = {}) {
  return { id: `s-${number}`, train_number: String(number), days: 'Dagl', train_type: 'person', stops, ...extra };
}
const services = [
  service(1, [stop('a', 0, null, '10:00'), stop('b', 1, '10:10', '10:12'), stop('c', 2, '10:20', '10:20'), stop('d', 3, '10:35', null)]),
  service(2, [stop('d', 0, null, '10:05'), stop('c', 1, '10:20', '10:21'), stop('b', 2, '10:29', '10:30'), stop('a', 3, '10:40', null)], { train_type: 'goods' }),
  service(3, [stop('a', 0, null, '11:00'), stop('b', 1, '11:10', '11:11'), stop('e', 2, '11:16', '11:16'), stop('f', 3, '11:24', null)]),
  service(4, [stop('a', 0, null, '12:00'), stop('b', 1, '12:12', '12:14'), stop('c', 2, '12:22', '12:22'), stop('d', 3, '12:38', null)], { train_type: 'work' }),
  // Lämnar huvudlinjen i b, kommer tillbaka i d: ritas som två delar på huvudlinjen.
  service(5, [stop('a', 0, null, '13:00'), stop('b', 1, '13:10', '13:11'), stop('e', 2, '13:16', '13:17'), stop('d', 3, '13:40', '13:42'), stop('g', 4, '13:50', null)]),
  // Nattåget över midnatt.
  service(9, [stop('a', 0, null, '23:50'), stop('b', 1, '00:00', '00:02', { service_day_offset: 1 }), stop('c', 2, '00:10', null, { service_day_offset: 1 })]),
];
const trains = [
  { id: 'r1', service_id: 's-1', station_id: 'b', train_number: '1', track: '3' },
  { id: 'r2', service_id: 's-2', station_id: 'b', train_number: '2', track: '1' },
  { id: 'r3', service_id: 's-4', station_id: 'b', train_number: '4', track: '2' },
];
function snapshot(overrides = {}) {
  return { stations, connections, tracks, trains, services, clock: { time: '10:15:00', running: false, speed: 1 },
    display: { graph_station_order: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], topology_mainline_station_ids: ['a', 'b', 'c', 'd'], topology_branch_station_ids: ['e', 'f', 'g'] }, ...overrides };
}
const ids = (line) => line.stations.map((station) => station.id);

test('lines: the heaviest path is the main line, each branch starts at its junction', () => {
  const lines = graph.lines(snapshot());
  assert.deepEqual(lines.map(ids), [['a', 'b', 'c', 'd'], ['b', 'e', 'f'], ['d', 'g']]);
  assert.equal(lines[0].name, 'Alvesta–Dalby');
  assert.equal(lines[1].name, 'Bjärnum–Falun');
  assert.equal(lines[0].id, 'a..d');
});

test('lines: traffic beats length, and without trains the given main line is used', () => {
  // Fler tåg a–b–e–f än a–b–c–d: huvudlinjen följer tågen.
  const busy = snapshot({ services: [services[2], { ...services[2], id: 's-30', train_number: '30' }, { ...services[2], id: 's-31', train_number: '31' }, services[0]] });
  assert.deepEqual(graph.lines(busy).map(ids), [['a', 'b', 'e', 'f'], ['b', 'c', 'd', 'g']], 'the rest is one branch: nothing runs d–g, so it is just the longest path');
  const quiet = snapshot({ services: [] });
  assert.deepEqual(graph.lines(quiet).map(ids), [['a', 'b', 'c', 'd'], ['b', 'e', 'f'], ['d', 'g']]);
});

test('lines: a loop is covered too, and a station without stretches is still a line', () => {
  const ring = snapshot({ connections: [...connections, { id: 'ring', station_a_id: 'f', station_b_id: 'c', track_type: 'single' }] });
  const lines = graph.lines(ring).map(ids);
  // Varje sträcka står i någon linje som två grannar, och varje station finns med.
  const covered = new Set(lines.flatMap((line) => line.slice(1).map((id, i) => [line[i], id].sort().join('|'))));
  for (const [a, b] of [...links, ['f', 'c']]) assert.ok(covered.has([a, b].sort().join('|')), `${a}–${b} in ${JSON.stringify(lines)}`);
  assert.deepEqual([...new Set(lines.flat())].sort(), stations.map((s) => s.id).sort());
  const lonely = snapshot({ stations: [...stations, { id: 'h', code: 'H', name: 'Hedemora' }] });
  assert.deepEqual(graph.lines(lonely).map(ids).at(-1), ['h']);
  assert.deepEqual(graph.lines(null), []);
  assert.deepEqual(graph.lines({ stations: [{ id: 'x', code: 'X', name: 'X-stad' }] }).map(ids), [['x']]);
});

test('run times: the median over the trains that run the stretch, else the median of the line, else ten', () => {
  const run = graph.runTimes(snapshot());
  assert.equal(run.of('a', 'b'), 10, 'a→b 10, 11, 12 and 10 min, d→… not counted');
  assert.equal(run.of('c', 'd'), 15);
  assert.equal(run.of('d', 'g'), 8);
  assert.equal(run.of('b', 'e'), 5);
  assert.equal(run.of('zz', 'yy'), run.fallback, 'an unknown pair gets the fallback');
  assert.equal(graph.runTimes(snapshot({ services: [] })).of('a', 'b'), 10, 'no trains: ten minutes');
  // Ett tåg som hoppar över en station räknas inte som gångtid för någon sträcka.
  const skip = snapshot({ services: [service(7, [stop('a', 0, null, '09:00'), stop('c', 1, '09:30', null)])] });
  assert.equal(graph.runTimes(skip).typical.size, 0);
});

test('tracks: the catalogue in its order, plus tracks only the timetable knows', () => {
  const byStation = graph.stationTracks(snapshot({ trains: [...trains, { id: 'r9', service_id: 's-9', station_id: 'c', train_number: '9', track: '4' }] }));
  assert.deepEqual(byStation.get('b'), ['1', '2', '3']);
  assert.deepEqual(byStation.get('c'), ['1', '4']);
  assert.equal(byStation.get('e'), undefined);
  assert.equal(graph.trackOf(snapshot()).get('s-1|b'), '3');
});

test('layout: stations lie at their running time apart, never closer than the gap', () => {
  const g = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615, line: 'a..d', pxPerMinute: 4, gap: 22 });
  const y = Object.fromEntries(g.panels[0].stations.map((entry) => [entry.station.id, entry.y]));
  assert.equal(g.panels.length, 1);
  assert.equal(y.b - y.a, 40, 'ten minutes at four pixels a minute');
  assert.equal(y.c - y.b, 32, 'eight minutes');
  assert.equal(y.d - y.c, 60, 'fifteen minutes');
  const tight = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615, line: 'a..d', pxPerMinute: 1, gap: 22 });
  const ty = Object.fromEntries(tight.panels[0].stations.map((entry) => [entry.station.id, entry.y]));
  assert.equal(ty.b - ty.a, 22, 'the gap is the floor');
  assert.equal(g.height, Math.round(g.top + 40 + 32 + 60 + g.bottom), 'as tall as it needs to be');
});

test('layout: every line on top of the other, the branch with its junction', () => {
  const g = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615 });
  assert.deepEqual(g.panels.map((panel) => panel.title), ['Alvesta–Dalby', 'Bjärnum–Falun', 'Dalby–Gnesta']);
  assert.deepEqual(g.panels[1].stations.map((entry) => entry.station.id), ['b', 'e', 'f']);
  assert.ok(g.panels[1].y0 >= g.panels[0].y1, 'the second line starts after the first');
  const single = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615, line: 'a..d' });
  assert.equal(single.panels[0].title, '', 'one line needs no title');
});

test('layout: the screen is filled exactly, however many rows', () => {
  for (const height of [400, 760, 1000]) {
    const g = graph.layout(snapshot(), { width: 1440, height, windowMinutes: 180, now: 615, gap: 15, top: 40, bottom: 56 });
    assert.equal(g.height, height);
    assert.ok(Math.abs(g.panels.at(-1).y1 - (height - 56)) < 0.6, `${height}: the last row ends at the bottom (${g.panels.at(-1).y1})`);
    assert.ok(g.panels[0].stations[0].y >= 40, 'and the first starts at the top');
    const gaps = g.panels.flatMap((panel) => panel.stations.slice(1).map((entry, i) => entry.y - panel.stations[i].y));
    assert.ok(gaps.every((gap) => gap >= 14.9), `no row closer than the gap: ${gaps.map((v) => v.toFixed(1))}`);
  }
});

test('layout: a folded-out station gets a row per track, and the train stands on its track', () => {
  const folded = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615, line: 'a..d', tracks: new Set(['b']), trackStep: 14 });
  const b = folded.panels[0].stations.find((entry) => entry.station.id === 'b');
  assert.deepEqual(b.tracks.map((track) => [track.label, track.y - b.y]), [['1', 0], ['2', 14], ['3', 28]]);
  const a = folded.panels[0].stations.find((entry) => entry.station.id === 'a');
  assert.deepEqual(a.tracks, [], 'the others stay one row');
  const one = folded.trains.find((train) => train.number === '1');
  assert.equal(one.dwells.length, 1);
  assert.equal(one.dwells[0].y, b.y + 28, 'train 1 waits on track 3');
  const two = folded.trains.find((train) => train.number === '2');
  assert.equal(two.dwells.find((dwell) => dwell.station === 'b').y, b.y, 'train 2 on track 1');
  assert.equal(two.dwells.find((dwell) => dwell.station === 'c').y, folded.panels[0].stations.find((entry) => entry.station.id === 'c').y, 'and in c, not folded out, on the station row');
  const all = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615, line: 'a..d', tracks: 'all' });
  assert.equal(all.panels[0].stations.find((entry) => entry.station.id === 'a').tracks.length, 2);
  assert.ok(all.height > folded.height);
});

test('layout: legs carry the minutes and the number, dwells are horizontal, leaving the line breaks the run', () => {
  const g = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615, line: 'a..d' });
  const one = g.trains.find((train) => train.number === '1');
  assert.deepEqual(one.legs.map((leg) => [leg.from, leg.to, leg.dep, leg.arr]), [['a', 'b', '00', '10'], ['b', 'c', '12', '20'], ['c', 'd', '20', '35']]);
  assert.equal(one.type, 'person');
  assert.equal(one.runs.length, 1);
  assert.equal(one.dwells.length, 1, 'a two-minute stop in b; the passage in c is no dwell');
  assert.ok(one.dwells[0].x2 > one.dwells[0].x1);
  const five = g.trains.find((train) => train.number === '5');
  assert.equal(five.runs.length, 2, 'a–b, then d alone is one point: only a–b is a run');
  assert.deepEqual(five.legs.map((leg) => [leg.from, leg.to]), [['a', 'b']], 'no leg from b to d across the branch');
  const types = Object.fromEntries(g.trains.map((train) => [train.number, train.type]));
  assert.deepEqual([types['2'], types['4']], ['goods', 'work']);
  // Grenens diagram har tåg 3 och 5 från b, och inte tåg 1.
  const branch = graph.layout(snapshot(), { width: 1000, windowMinutes: 0, now: 615, line: 'b..f' });
  assert.deepEqual(branch.trains.map((train) => train.number).sort(), ['3', '5']);
  assert.deepEqual(branch.trains.find((train) => train.number === '3').legs.map((leg) => [leg.from, leg.to]), [['b', 'e'], ['e', 'f']]);
});

test('layout: the window is the model\'s on whole hours, or the one the screen gives', () => {
  const g = graph.layout(snapshot(), { width: 1000, windowMinutes: 180, now: 615, line: 'a..d' });
  assert.equal(g.start % 60, 0);
  assert.equal(g.end - g.start, 180);
  assert.equal(g.now, 615);
  const own = graph.layout(snapshot(), { width: 1000, windowMinutes: 180, start: 585, end: 765, now: 615, line: 'a..d' });
  assert.deepEqual([own.start, own.end, own.now], [585, 765, 615]);
  assert.ok(Math.abs(own.x(585) - own.left) < 0.01 && Math.abs(own.x(765) - (own.width - own.right)) < 0.01);
  const outside = graph.layout(snapshot(), { width: 1000, windowMinutes: 180, start: 585, end: 765, now: 60, line: 'a..d' });
  assert.equal(outside.now, null, 'the now line is only drawn inside the window');
  assert.equal(outside.nowX, null);
});

test('layout: a train is drawn only where it is in the window, and the night train stays whole', () => {
  const day = graph.layout(snapshot(), { width: 1000, windowMinutes: 120, now: 615, line: 'a..d' });
  assert.deepEqual([day.start, day.end], [540, 660]);
  assert.deepEqual(day.trains.map((train) => train.number).sort(), ['1', '2', '3'], 'only trains touching 09:00–11:00; train 3 leaves at 11:00');
  const night = graph.layout(snapshot({ clock: { time: '00:05:00' } }), { width: 1000, windowMinutes: 120, now: 5, line: 'a..d' });
  const nine = night.trains.find((train) => train.number === '9');
  assert.ok(nine, 'the night train is in the window around midnight');
  assert.deepEqual(nine.legs.map((leg) => [leg.dep, leg.arr]), [['50', '00'], ['02', '10']]);
  assert.ok(nine.legs[0].x1 < nine.legs[0].x2 && nine.legs[1].x1 < nine.legs[1].x2, 'and runs left to right across midnight');
});

test('layout: the train out on the line is lit on its stretch and stands on the now line', () => {
  const live = snapshot({ connection_states: [{ id: 'c1', state: 'occupied', channels: [{ train_number: '1', state: 'occupied', from_station_id: 'b', to_station_id: 'c', departed_seconds: 10 * 3600 + 12 * 60 }] }] });
  const g = graph.layout(live, { width: 1000, windowMinutes: 180, now: 616, line: 'a..d', train: '2' });
  const one = g.trains.find((train) => train.number === '1'), two = g.trains.find((train) => train.number === '2');
  assert.equal(one.lit, true);
  assert.deepEqual(one.legs.map((leg) => leg.lit), [false, true, false]);
  assert.ok(Math.abs(one.at.progress - 0.5) < 0.01, 'four of eight minutes since 10:12');
  assert.equal(one.at.x, g.x(616));
  assert.equal(one.tag.cleared, false);
  assert.equal(two.selected, true);
  assert.equal(two.tag.cleared, false);
  assert.ok(two.tag, 'the chosen train gets its tag at its start');
});

test('the label along a line is never upside down', () => {
  assert.equal(graph.angle({ x1: 0, y1: 0, x2: 10, y2: 10 }), 45);
  assert.equal(graph.angle({ x1: 10, y1: 10, x2: 0, y2: 0 }), 45, 'the same line read the other way');
  assert.equal(graph.angle({ x1: 0, y1: 10, x2: 10, y2: 0 }), -45);
  assert.equal(graph.angle({ x1: 0, y1: 0, x2: 10, y2: 0 }), 0);
  assert.equal(graph.minuteText(615), '15');
  assert.equal(graph.minuteText(1440 + 2), '02');
  assert.equal(graph.clockLabel(1500), '01:00');
});
