const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const web = path.resolve(__dirname, '../../src/tmbox_gateway/web');

test('Cloud-only shell messages have all five languages and matching interpolation parameters', () => {
  let annotated = false;
  const body = {};
  const context = { document: { body }, TrainMeetI18n: { annotate(root) { assert.equal(root, body); annotated = true; } } };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(web, 'shell-messages.js'), 'utf8'), context);
  assert.equal(annotated, true);
  const rows = Object.entries(context.TrainMeetMessages);
  assert.ok(rows.length >= 50);
  const parameters = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
  for (const [source, row] of rows) {
    for (const language of ['sv', 'da', 'nb', 'en', 'de']) {
      assert.ok(row[language]?.trim(), source + ': ' + language);
      assert.deepEqual(parameters(row[language]), parameters(source), source + ': ' + language);
    }
  }
});

test('static shell translation runs before app data and cannot restore removed authoring controls', () => {
  const document = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  assert.ok(document.indexOf('/assets/i18n.js') < document.indexOf('/assets/shell-messages.js'));
  assert.ok(document.indexOf('/assets/shell-messages.js') < document.indexOf('/assets/i18n-init.js'));
  assert.ok(document.indexOf('/assets/shell-messages.js') < document.indexOf('/assets/app.js'));
  assert.doesNotMatch(document, /id="(?:build-sidebar|build-view|meet-type-nav)"|data-operating-mode/);
});
