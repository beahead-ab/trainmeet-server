const test = require('node:test');
const assert = require('node:assert/strict');
const {EntryBuffer} = require('../../src/tmbox_gateway/terminal16_web/terminal.js');
const frame = {lines:['                ','Nr# C/D    12:34'],entry:{context:'station-cda',max_length:5,
  lines:['TAG: _____      ','#Sok B:Del 12:34'],row:0,column:5,commit:'#',cancel:'*',erase:'B'}};
test('digits never create server commands; # submits the complete value',()=>{
  const buffer = new EntryBuffer();
  assert.equal(buffer.press('3',frame).local,true);
  assert.equal(buffer.press('9',frame).local,true);
  assert.deepEqual(buffer.press('#',frame),{local:false,train_number:'39',entry_context:'station-cda'});
  assert.equal(buffer.lines(frame)[0],'TAG: 39___      ');
});
test('cancel and erase are local, and do not release traffic',()=>{
  const buffer = new EntryBuffer();buffer.press('3',frame);buffer.press('9',frame);
  assert.equal(buffer.press('B',frame).local,true);assert.equal(buffer.digits,'3');
  assert.equal(buffer.press('*',frame).local,true);assert.equal(buffer.digits,'');
});
test('idle top row stays blank and cancelling entry restores it with the clock',()=>{
  const buffer = new EntryBuffer();
  assert.deepEqual(buffer.lines(frame), [' '.repeat(16), 'Nr# C/D    12:34']);
  buffer.press('3',frame);buffer.press('9',frame);
  assert.equal(buffer.lines(frame)[0],'TAG: 39___      ');
  buffer.press('*',frame);
  assert.deepEqual(buffer.lines(frame), [' '.repeat(16), 'Nr# C/D    12:34']);
});
test('star clears typed digits locally even when server offers withdrawal or rejection',()=>{
  for (const label of ['Återta begäran…','Återta klartecken…','Neka begäran…']) {
    const buffer = new EntryBuffer();
    const actionable = {...frame,keys:{'*':{label}}};
    buffer.press('3',actionable);buffer.press('9',actionable);
    assert.deepEqual(buffer.press('*',actionable),{local:true});
    assert.equal(buffer.digits,'');
    assert.deepEqual(buffer.lines(actionable),actionable.lines);
    assert.deepEqual(buffer.press('*',actionable),{local:false});
  }
});
test('function keys cannot act on traffic while typing',()=>{
  const buffer = new EntryBuffer();buffer.press('3',frame);
  for(const key of ['A','C','D']) assert.equal(buffer.press(key,frame).local,true);
});
test('incoming request preserves entry; hash still searches rather than approving',()=>{
  const buffer = new EntryBuffer(); buffer.press('3',frame); buffer.press('9',frame);
  const request = {...frame,lines:['MUN?93       1/1','#Ja *Nej   12:34'],keys:{'#':{label:'Ge klart'}},
    entry:{...frame.entry,shortcut:'A'}};
  assert.equal(buffer.lines(request)[0],'TAG: 39___      ');
  assert.deepEqual(buffer.press('#',request),{local:false,train_number:'39',entry_context:'station-cda'});
  assert.equal(buffer.digits,'39');
});
test('explicit server-defined queue shortcut leaves entry without sending its digits',()=>{
  const buffer = new EntryBuffer(); buffer.press('3',frame); buffer.press('9',frame);
  assert.deepEqual(buffer.press('A',{...frame,entry:{...frame.entry,shortcut:'A'}}),{local:false});
  assert.equal(buffer.digits,'');
});
test('# without digits delegates confirmation, but typed digits always form a lookup',()=>{
  const buffer = new EntryBuffer();
  const actionable = {...frame,keys:{'#':{label:'Rapportera avgång'}}};
  assert.deepEqual(buffer.press('#',actionable),{local:false});
  buffer.press('9',actionable);buffer.press('3',actionable);
  assert.deepEqual(buffer.press('#',actionable),{local:false,train_number:'93',entry_context:'station-cda'});
});
test('five digit limit and leading zeroes',()=>{
  const buffer = new EntryBuffer();for(const key of '001239') buffer.press(key,frame);
  assert.equal(buffer.digits,'00123');
});
test('clock and unrelated frames do not erase local input',()=>{
  const buffer = new EntryBuffer();buffer.press('3',frame);
  const newer = {...frame,entry:{...frame.entry,lines:['TAG: _____      ','#Sok B:Del 12:35']}};
  assert.equal(buffer.lines(newer)[0],'TAG: 3____      ');
  assert.ok(buffer.lines(newer)[1].endsWith('12:35'));
});
test('reset or reassignment invalidates the old input context',()=>{
  const buffer = new EntryBuffer();buffer.press('3',frame);
  buffer.sync({...frame,entry:{...frame.entry,context:'station-va'}});
  assert.equal(buffer.digits,'');
});
test('server language characters survive local entry without shifting the clock',()=>{
  const buffer = new EntryBuffer();
  const translated = {...frame,entry:{...frame.entry,lines:['TÅG: _____      ','#Sök B:Del 12:34']}};
  buffer.press('9', translated);
  assert.equal(buffer.lines(translated)[0], 'TÅG: 9____      ');
  assert.equal(buffer.lines(translated)[1], '#Sök B:Del 12:34');
  for (const alphabet of ['ÅÄÖ åäö', 'ÆØÅ æøå', 'ÄÖÜẞ äöüß']) {
    buffer.clear();
    const display = {...frame,lines:[alphabet.padEnd(16), 'Ready      12:34']};
    assert.deepEqual(buffer.lines(display), display.lines);
    assert.equal([...buffer.lines(display)[0]].length, 16);
  }
});
