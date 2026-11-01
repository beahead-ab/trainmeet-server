const {test} = require('node:test');
const assert = require('node:assert/strict');
const D = require('../../src/tmbox_gateway/web/lokforaren/duties.js');
const duties = [
  {id: 'a', number: '132', operator: 'CargoNet', vehicle: 'CN 02 CD312', start: '17:10', end: '21:02', trains: ['G 5453', 'G 5456'], route: ['Spiten', 'Kjøllstad']},
  {id: 'b', number: '7', operator: 'SJ', vehicle: 'Rc6', start: '18:00', end: '19:00', trains: ['P 101'], route: ['Åmål', 'Spiten']},
  {id: 'c', number: '12', operator: 'SJ', start: '23:40', end: '00:20', trains: ['N 1'], route: ['Åmål', 'B']},
  {id: 'd', number: '2', operator: 'SJ', start: '00:10', end: '01:00', route: ['B']},
];
const ids = (options) => D.filter(duties, options).map((duty) => duty.id);

test('the whole catalogue is visible and naturally sorted without modifying the source', () => {
  const catalogue = Array.from({length: 142}, (_, n) => ({id: String(n), number: String(142 - n)}));
  assert.equal(D.filter(catalogue).length, 142);
  assert.deepEqual(D.filter(catalogue).slice(0, 3).map((d) => d.number), ['1', '2', '3']);
  assert.equal(catalogue[0].number, '142');
});
test('smart search combines words across fields, folded accents and compact train numbers', () => {
  assert.deepEqual(ids({query: 'CARGONET kjollstad G5453 cd312'}), ['a']);
  assert.deepEqual(ids({query: 'amal sj'}), ['b', 'c']);
  assert.deepEqual(ids({query: '5456'}), ['a']);
  assert.deepEqual(ids({query: 'Spiten nonexistent'}), []);
});
test('all filters combine; station names are exact options and sorting can use start time', () => {
  assert.deepEqual(ids({query: 'sj', station: 'Åmål', operator: 'SJ', time: 'soon', now: 17 * 60 + 30}), ['b']);
  assert.deepEqual(ids({operator: 'CargoNet', station: 'Åmål'}), []);
  assert.deepEqual(ids({sort: 'start'}), ['d', 'a', 'b', 'c']);
});
test('an exact duty number comes before partial numeric matches', () => {
  const list = [{id: 'a', number: '132'}, {id: 'b', number: '13'}, {id: 'c', number: '2', trains: ['G 13']}];
  assert.deepEqual(D.filter(list, {query: '13'}).map((d) => d.number), ['13', '132', '2']);
});
test('time filters follow meet time, including midnight and precise boundaries', () => {
  assert.deepEqual(ids({time: 'soon', now: 17 * 60}), ['b', 'a']);
  assert.deepEqual(ids({time: 'soon', now: 17 * 60 + 10.1}), ['b']);
  assert.deepEqual(ids({time: 'ongoing', now: 18 * 60}), ['b', 'a']);
  assert.deepEqual(ids({time: 'ongoing', now: 19 * 60}), ['a']);
  assert.deepEqual(ids({time: 'soon', now: 23 * 60 + 50}), ['d']);
  assert.deepEqual(ids({time: 'ongoing', now: 5}), ['c']);
  assert.deepEqual(ids({time: 'ongoing', now: 20}), ['d']);
});
test('no time filter is inferred from a lost clock or missing times', () => {
  assert.deepEqual(ids({time: 'soon', now: null}), []);
  assert.deepEqual(ids({time: 'ongoing'}), []);
  assert.equal(D.filter([{id: 'x', number: '1'}], {time: 'ongoing', now: 0}).length, 0);
  assert.equal(ids({now: null}).length, 4);
});
