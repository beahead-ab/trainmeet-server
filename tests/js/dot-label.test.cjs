// Produktetiketterna i TMBox-displayens 5×7-punkter (tools/dot-label.mjs):
// filerna i web/etikett är exakt vad generatorn ger, och bokstäverna är hela.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

test('the committed labels are what the generator draws', async () => {
  const { dotLabel, dotLabelSize, LABELS, TARGETS, GLYPHS } = await import(path.join(root, 'tools/dot-label.mjs'));
  for (const [name, folders] of Object.entries(TARGETS)) {
    for (const folder of folders.filter((item) => !item.startsWith('..'))) {
      const file = fs.readFileSync(path.join(root, folder, `${name}.svg`), 'utf8');
      assert.equal(file, dotLabel(LABELS[name]), `${folder}/${name}.svg: run node tools/dot-label.mjs`);
    }
  }
  assert.deepEqual(dotLabelSize('SERVER'), { width: 105, height: 21 }, 'the server header is sized for it');
  assert.deepEqual(dotLabelSize('TMBOX'), { width: 87, height: 21 });
  for (const [letter, rows] of Object.entries(GLYPHS)) {
    assert.equal(rows.length, 7, letter);
    for (const row of rows) assert.match(row, /^[01]{5}$/, letter);
  }
  assert.notDeepEqual(GLYPHS.V, GLYPHS.U, 'V reads as V, not U');
  const dots = (svg) => (svg.match(/<circle /g) || []).length;
  assert.equal(dots(dotLabel('SERVER')), ['S', 'E', 'R', 'V', 'E', 'R'].reduce((sum, l) => sum + GLYPHS[l].join('').split('1').length - 1, 0));
});

test('the pages that show a label load it and size it like the file', () => {
  const css = fs.readFileSync(path.join(root, 'src/tmbox_gateway/web/kontrollrummet.css'), 'utf8');
  assert.match(css, /mask-image: url\("\/assets\/etikett\/server\.svg"\)/);
  assert.match(css, /#app-chrome \.tm-dotlabel \{[^}]*width: 105px; height: 21px;/);
  const tmbox = fs.readFileSync(path.join(root, 'src/tmbox_gateway/terminal16_web/style.css'), 'utf8');
  assert.match(tmbox, /mask-image: url\("\/assets\/etikett\/tmbox\.svg"\)/);
  assert.match(tmbox, /\.tm-dotlabel \{[^}]*width: 87px; height: 21px;/);
  for (const page of ['live.html', 'index.html', 'floden.html']) {
    assert.match(fs.readFileSync(path.join(root, 'src/tmbox_gateway/terminal16_web', page), 'utf8'), /tm-top__brand[^>]*aria-label="TrainMeet TMBox"/, page);
  }
});
