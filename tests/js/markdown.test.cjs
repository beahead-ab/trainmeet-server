// Träffens texter (web/markdown.js): samma Markdown-delmängd på servern, i
// Cloud, i TKL och i iPhone-appen. Kör: node --test tests/js/markdown.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const md = require(path.join(__dirname, '../../src/tmbox_gateway/web/markdown.js'));

const kinds = (blocks) => blocks.map((block) => block.type === 'heading' ? `h${block.level}` : block.type);
const flat = (nodes) => nodes.map((node) => node.type === 'text' || node.type === 'code' ? node.text : flat(node.children)).join('');

test('blocks: headings, paragraphs, lists, rules', () => {
  const text = '## Allmänt\nTrafiken rullar\nkontinuerligt.\n\n- 07:30 Frukost\n- 12:00 Lunch\n\n1. Först\n2. Sedan\n---\nSlut';
  const blocks = md.parse(text);
  assert.deepEqual(kinds(blocks), ['h2', 'paragraph', 'list', 'list', 'rule', 'paragraph']);
  assert.equal(flat(blocks[1].children), 'Trafiken rullar kontinuerligt.');
  assert.deepEqual(blocks[2].items.map(flat), ['07:30 Frukost', '12:00 Lunch']);
  assert.equal(blocks[2].ordered, false);
  assert.equal(blocks[3].ordered, true);
  assert.deepEqual(blocks[3].items.map(flat), ['Först', 'Sedan']);
});

test('a list line ends a paragraph, and an indented line continues the item above', () => {
  const blocks = md.parse('Fasta tider:\n- 07:30 Frukost\n  serveras i matsalen\n• Middag');
  assert.deepEqual(kinds(blocks), ['paragraph', 'list']);
  assert.deepEqual(blocks[1].items.map(flat), ['07:30 Frukost serveras i matsalen', 'Middag']);
});

test('inline: bold, italic, code and links; only safe links become links', () => {
  const [paragraph] = md.parse('**Viktigt:** ring *bara* om `116` är sen. [Läs](https://example.se) [inte](javascript:alert(1))');
  const types = paragraph.children.map((node) => node.type);
  // Den osäkra länken blir bara sin text; resten av raden står kvar som text.
  assert.deepEqual(types, ['strong', 'text', 'em', 'text', 'code', 'text', 'link', 'text']);
  assert.equal(paragraph.children[6].href, 'https://example.se');
  assert.equal(flat(paragraph.children), 'Viktigt: ring bara om 116 är sen. Läs inte)');
  assert.ok(!JSON.stringify(paragraph).includes('javascript'));
});

test('times and dashes in a programme stay text, not lists or emphasis', () => {
  const blocks = md.parse('- 07:30 - 08:30 Frukost serveras (ej torsdag)\nTåg 2*3 är inte kursivt');
  assert.deepEqual(kinds(blocks), ['list', 'paragraph']);
  assert.equal(flat(blocks[0].items[0]), '07:30 - 08:30 Frukost serveras (ej torsdag)');
  assert.equal(flat(blocks[1].children), 'Tåg 2*3 är inte kursivt');
});

test('plain text for previews', () => {
  assert.equal(md.plain('# Rubrik\n\nEtt **fett** ord.\n\n- a\n- b'), 'Rubrik\n\nEtt fett ord.\n\na\nb');
  assert.equal(md.plain(''), '');
  assert.equal(md.plain(null), '');
});

test('pick: the reader\'s language, then the meet\'s, then Swedish, then any', () => {
  const texts = { sv: 'Hej', da: 'Hej (da)', de: '' };
  assert.deepEqual(md.pick(texts, 'da', 'sv'), { language: 'da', text: 'Hej (da)' });
  assert.deepEqual(md.pick(texts, 'de', 'da'), { language: 'da', text: 'Hej (da)' });
  assert.deepEqual(md.pick(texts, 'en', 'nb'), { language: 'sv', text: 'Hej' });
  assert.deepEqual(md.pick({ de: 'Hallo' }, 'en', 'sv'), { language: 'de', text: 'Hallo' });
  assert.equal(md.pick({}, 'sv', 'sv'), null);
  assert.equal(md.pick(null, 'sv', 'sv'), null);
});

test('render builds elements with textContent only, shifted heading levels and safe links', async () => {
  // Ett minimalt DOM räcker: render använder createElement, createTextNode,
  // append och replaceChildren.
  class Node { constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.classList = { add: (c) => { this.className = c; } }; }
    append(...nodes) { this.children.push(...nodes); } replaceChildren() { this.children = []; } }
  const doc = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ text }) };
  const container = new Node('div'); container.ownerDocument = doc;
  md.render(container, '# Titel\nText med [länk](https://a.se) och **fet**.\n\n- ett\n- två', { headingBase: 3 });
  assert.equal(container.className, 'tm-md');
  assert.deepEqual(container.children.map((node) => node.tagName), ['h3', 'p', 'ul']);
  const link = container.children[1].children.find((node) => node.tagName === 'a');
  assert.equal(link.href, 'https://a.se'); assert.equal(link.target, '_blank'); assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(container.children[2].children.length, 2);
  const source = fs.readFileSync(path.join(__dirname, '../../src/tmbox_gateway/web/markdown.js'), 'utf8');
  assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|outerHTML|document\.write/);
});
