#!/usr/bin/env node
// Audits that every authored, user-visible UI string exists in all five
// languages (en, sv, da, nb, de) in the catalogues the page actually loads.
//
//   node tools/i18n-audit.mjs            summary; exit code 1 on any gap
//   node tools/i18n-audit.mjs --list     every gap with file:line
//   node tools/i18n-audit.mjs --json     machine-readable result
//   node tools/i18n-audit.mjs --tkl DIR  audit this TrainMeet TKL checkout
//                                        (default: ../trainmeet-tkl when present)
//
// Three kinds of finding, all of which fail:
//   missing    an authored message has no row, or a row without all five
//              languages. Authored means what web/i18n.js translates:
//              t("…") (also a ? "…" : "…", {k: "…"}[key] and t(TABLE[key]) over
//              a const table, and local helpers that pass a parameter on to
//              t()), new Error("…") (shown with setMessage), html`…` text and
//              title/placeholder/aria-label/alt, data-tm-text, and the text
//              nodes and those attributes of the authored HTML pages;
//   unwrapped  text put on the page without any lookup: textContent/title/…
//              = "…", setAttribute("aria-label", "…"), alert/confirm("…"),
//              t(`…${x}…`) whose key is built at run time, JSX text and JSX
//              attributes in TKL. It stays in the source language whatever
//              the catalogue says;
//   literal    any other prose-like string literal in UI code (a sentence, or
//              a word with å/ä/ö). It usually reaches t() through a variable,
//              so it needs a row too, unless IGNORE below says why not.
// Static analysis cannot follow every value; the three rules together are
// meant to make a new untranslated text hard to add by accident.
// No dependency is needed for Server. TKL is audited by its own
// scripts/i18n-audit.mjs (TypeScript parser), included here when present.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath, pathToFileURL} from 'node:url';

export const LOCALES = ['en', 'sv', 'da', 'nb', 'de'];
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pkg = path.join(repo, 'src/tmbox_gateway');

// ── What is language at all ───────────────────────────────────────────────
// Product names, codes and units read the same in every language.
const NEUTRAL_WORDS = new Set(['TrainMeet', 'Server', 'TMBox', 'TMBoxes', 'TMBoxar', 'TMBoxen', 'TKL', 'Cloud', 'US', 'EU',
  'Wi-Fi', 'WiFi', 'MQTT', 'QR', 'ID', 'OK', 'TWC', 'MP', 'ESP32', 'ESP8266', 'iPhone', 'iOS', 'Android', 'Raspberry', 'Pi',
  'Inter', 'Linux', 'macOS', 'Windows', 'GitHub', 'HTTP', 'HTTPS', 'URL', 'SSID', 'LCD', 'PIN', 'API', 'JSON', 'CSV', 'PDF',
  'v1', 'v2', 'UTC', 'Train', 'Meet', 'SE', 'DK', 'DE', 'NO', 'GB', 'FastClock', 'TrainMeetMessages', 'build']);
const LETTER = /[A-Za-zÅÄÖåäöÆØæøÜüßÉé]/;
const WORD = /[A-Za-zÅÄÖåäöÆØæøÜüßÉé0-9-]+/g;
export function isLanguage(text) {
  const stripped = String(text).replace(/__TM_ARG_\d+__/g, ' ').replace(/\{\w+\}/g, ' ');
  if (!LETTER.test(stripped) || /^\s*\/[\w/.-]*\s*$/.test(stripped) || /^\s*https?:\/\/\S+\s*$/.test(stripped)) return false; // a path or URL
  // Codes such as station signatures (CDA), track numbers (1a) and units (km/h).
  return (stripped.match(WORD) || []).some((word) => LETTER.test(word) && !NEUTRAL_WORDS.has(word)
    && !/^[A-ZÅÄÖ]{1,4}\d*$/.test(word) && !/^\d+[a-z]{0,2}$/.test(word) && !/^(km|h|min|s|ms|px|kB|MB|GB)$/.test(word)
    && !/^[a-z]$/.test(word));
}
// Prose: a word with å/ä/ö, or a capitalised phrase of at least two words.
// Selectors, class lists, identifiers, URLs, markup and CSS are not prose.
export function isProse(text) {
  const value = String(text).trim();
  if (!isLanguage(value) || /[<>]|=>|^[#.[/@]|https?:|[{};]\s*$|^\w+:\s|\b(?:var|calc|rgba?)\(/.test(value)) return false;
  if (/^[\w$.-]+$/.test(value) && !/[åäöÅÄÖ]/.test(value)) return false; // one identifier-like word
  if (/^[MmLlHhVvCcSsQqTtAaZz][\d\s.,MmLlHhVvCcSsQqTtAaZz-]*$/.test(value)) return false; // SVG path data
  if (value === value.toUpperCase()) return false; // LCD lines and codes: a box's text, checked in Python
  if (/^[a-z][\w-]*(?: [a-z][\w-]*)*$/.test(value) && !/[åäö]/.test(value)) return false; // class list / lowercase code
  return /[åäöÅÄÖæøÆØüÜß]/.test(value) || /^[A-ZÅÄÖ][^\s]*\s+\S/.test(value);
}
const normalizeKey = (text) => String(text).trim().replace(/\s+/g, ' ');

// ── Catalogues, loaded the way the pages load them ────────────────────────
export function loadCatalog(files) {
  const context = {TrainMeetI18n: {annotate() {}}, document: {body: {}}, console};
  context.globalThis = context;
  vm.createContext(context);
  for (const file of files) vm.runInContext(fs.readFileSync(file, 'utf8'), context, {filename: file});
  return context.TrainMeetMessages || {};
}
export const lookup = (catalog, source) => catalog[source] || catalog[normalizeKey(source)] || null;
export const missingLocales = (row) => LOCALES.filter((locale) => !row?.[locale]);

// ── A small JavaScript lexer: strings, templates, regex, comments ─────────
const KEYWORDS_BEFORE_EXPRESSION = new Set(['return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void', 'throw',
  'case', 'do', 'else', 'yield', 'await', 'export', 'default']);
function cook(raw) {
  return raw.replace(/\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r?\n|[\s\S])/g, (match, escape) => {
    if (escape[0] === 'u') return String.fromCodePoint(parseInt(escape.replace(/[u{}]/g, ''), 16));
    if (escape[0] === 'x') return String.fromCharCode(parseInt(escape.slice(1), 16));
    if (escape === '\n' || escape === '\r\n') return '';
    return {n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', v: '\v', 0: '\0'}[escape] ?? escape;
  });
}
// Tokens: {type: str|tpl|ident|num|regex|punct}. A template with ${} is one
// 'tpl' token holding its cooked chunks (quasis); the tokens of its
// expressions follow it, closed by a punct '`'.
export function lexJs(source) {
  const tokens = [], stack = [];
  let i = 0, line = 1;
  const push = (token) => { tokens.push(token); return token; };
  const regexAllowed = () => {
    const prev = tokens[tokens.length - 1];
    if (!prev) return true;
    if (prev.type === 'ident') return KEYWORDS_BEFORE_EXPRESSION.has(prev.value);
    if (prev.type === 'punct') return ![')', ']', '`'].includes(prev.value);
    return false;
  };
  const readQuasi = () => {
    let raw = '';
    while (i < source.length) {
      const c = source[i];
      if (c === '\\') { raw += c + source[i + 1]; if (source[i + 1] === '\n') line++; i += 2; continue; }
      if (c === '`') { i++; return [raw, true]; }
      if (c === '$' && source[i + 1] === '{') { i += 2; return [raw, false]; }
      if (c === '\n') line++;
      raw += c; i++;
    }
    return [raw, true];
  };
  while (i < source.length) {
    const c = source[i];
    if (c === '\n') { line++; i++; continue; }
    if (/\s/.test(c)) { i++; continue; }
    if (c === '/' && source[i + 1] === '/') { while (i < source.length && source[i] !== '\n') i++; continue; }
    if (c === '/' && source[i + 1] === '*') {
      const end = source.indexOf('*/', i + 2), stop = end < 0 ? source.length : end + 2;
      line += (source.slice(i, stop).match(/\n/g) || []).length; i = stop; continue;
    }
    if (c === '"' || c === "'") {
      const start = line; let raw = ''; i++;
      while (i < source.length && source[i] !== c) {
        if (source[i] === '\\') { raw += source[i] + source[i + 1]; if (source[i + 1] === '\n') line++; i += 2; continue; }
        if (source[i] === '\n') { line++; break; }
        raw += source[i]; i++;
      }
      i++;
      push({type: 'str', value: cook(raw), line: start});
      continue;
    }
    if (c === '`') {
      const prev = tokens[tokens.length - 1];
      const token = push({type: 'tpl', quasis: [], line, tag: prev?.type === 'ident' ? prev.value : null});
      i++;
      const [raw, ended] = readQuasi();
      token.quasis.push(cook(raw));
      if (!ended) stack.push({kind: 'tpl', token});
      continue;
    }
    if (c === '{') { stack.push({kind: 'brace'}); push({type: 'punct', value: c, line}); i++; continue; }
    if (c === '}') {
      const top = stack.pop();
      if (top?.kind === 'tpl') {
        i++;
        const [raw, ended] = readQuasi();
        top.token.quasis.push(cook(raw));
        if (!ended) stack.push(top); else push({type: 'punct', value: '`', line});
        continue;
      }
      push({type: 'punct', value: c, line}); i++; continue;
    }
    if (c === '/' && regexAllowed()) {
      let inClass = false; i++;
      while (i < source.length) {
        const ch = source[i];
        if (ch === '\\') { i += 2; continue; }
        if (ch === '[') inClass = true; else if (ch === ']') inClass = false;
        else if ((ch === '/' && !inClass) || ch === '\n') break;
        i++;
      }
      i++;
      while (/[a-z]/i.test(source[i] || '')) i++;
      push({type: 'regex', line});
      continue;
    }
    if (/[A-Za-z_$]/.test(c)) {
      let j = i + 1; while (/[\w$]/.test(source[j] || '')) j++;
      push({type: 'ident', value: source.slice(i, j), line}); i = j; continue;
    }
    if (/[0-9]/.test(c)) {
      let j = i + 1; while (/[\w.]/.test(source[j] || '')) j++;
      push({type: 'num', value: source.slice(i, j), line}); i = j; continue;
    }
    push({type: 'punct', value: c, line}); i++;
  }
  for (const token of tokens) if (token.type === 'tpl') token.static = token.quasis.length === 1;
  return tokens;
}

// ── HTML: entities, a forgiving tokenizer and the annotate() rules ────────
const ENTITIES = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', mdash: '—', ndash: '–', hellip: '…',
  rarr: '→', larr: '←', middot: '·', times: '×', copy: '©', laquo: '«', raquo: '»', rsquo: '’', lsquo: '‘', ldquo: '“', rdquo: '”', bull: '•'};
export const decodeEntities = (text) => String(text).replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (match, name) => name[0] === '#'
  ? String.fromCodePoint(name[1].toLowerCase() === 'x' ? parseInt(name.slice(2), 16) : Number(name.slice(1))) : ENTITIES[name] ?? match);
const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
const RAW = new Set(['script', 'style', 'textarea', 'title']);
const ATTRIBUTES = ['title', 'placeholder', 'aria-label', 'alt'];
function parseAttributes(text) {
  const attrs = {};
  for (const match of text.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
    attrs[match[1].toLowerCase()] = decodeEntities(match[2] ?? match[3] ?? match[4] ?? '');
  }
  return attrs;
}
export function* scanHtml(source) {
  let i = 0, line = 1;
  const count = (text) => (text.match(/\n/g) || []).length;
  while (i < source.length) {
    if (source.startsWith('<!--', i)) { const end = source.indexOf('-->', i), stop = end < 0 ? source.length : end + 3; line += count(source.slice(i, stop)); i = stop; continue; }
    const tag = source[i] === '<' && /^<(\/?)([a-zA-Z][\w:-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/.exec(source.slice(i, i + 20000));
    if (tag) {
      const name = tag[2].toLowerCase(), selfClosing = /\/\s*$/.test(tag[3]);
      if (tag[1]) yield {type: 'close', tag: name, line};
      else {
        yield {type: 'open', tag: name, attrs: parseAttributes(tag[3]), selfClosing: selfClosing || VOID.has(name), line};
        if (RAW.has(name) && !selfClosing) {
          const end = source.toLowerCase().indexOf('</' + name, i + tag[0].length), stop = end < 0 ? source.length : end;
          const text = source.slice(i + tag[0].length, stop);
          line += count(tag[0]);
          if (text) yield {type: 'text', value: text, line};
          line += count(text); i = stop; continue;
        }
      }
      line += count(tag[0]); i += tag[0].length; continue;
    }
    if (source[i] === '<' && source[i + 1] === '!') { const end = source.indexOf('>', i); line += count(source.slice(i, end + 1)); i = end + 1; continue; }
    let next = source.indexOf('<', i + 1); if (next < 0) next = source.length;
    const text = source.slice(i, next);
    yield {type: 'text', value: text, line};
    line += count(text); i = next;
  }
}
const PROTECTED = new Set(['script', 'style', 'pre', 'code', 'textarea']);
// An authored HTML page as annotate(document.body) in web/i18n.js sees it,
// plus data-tm-* markers anywhere (updateMarked covers the whole document).
export function htmlStrings(source) {
  const found = [], stack = [];
  let inBody = !/<body[\s>]/i.test(source);
  const isProtected = () => stack.some((el) => PROTECTED.has(el.tag) || 'data-no-i18n' in el.attrs);
  for (const event of scanHtml(source)) {
    if (event.type === 'open') {
      if (event.tag === 'body') inBody = true;
      if (isLanguage(event.attrs['data-tm-text'] ?? '')) found.push({source: event.attrs['data-tm-text'], line: event.line, kind: 'data-tm-text'});
      for (const attr of ATTRIBUTES) {
        const marked = event.attrs['data-tm-' + attr];
        if (marked !== undefined && isLanguage(marked)) found.push({source: marked, line: event.line, kind: 'data-tm-' + attr});
      }
      if (inBody && !isProtected() && !PROTECTED.has(event.tag) && !('data-no-i18n' in event.attrs)) {
        for (const attr of ATTRIBUTES) {
          const value = event.attrs[attr];
          if (value && isLanguage(value) && !(('data-tm-' + attr) in event.attrs)) found.push({source: value, line: event.line, kind: attr});
        }
      }
      if (!event.selfClosing) stack.push({tag: event.tag, attrs: event.attrs});
    } else if (event.type === 'close') {
      const index = stack.map((el) => el.tag).lastIndexOf(event.tag);
      if (index !== -1) stack.splice(index);
      if (event.tag === 'body') inBody = false;
    } else if (inBody) {
      const parent = stack.at(-1);
      if (!parent || isProtected() || stack.some((el) => 'data-tm-text' in el.attrs)) continue;
      const text = decodeEntities(event.value);
      if (!isLanguage(text)) continue;
      // annotate() skips SVG text, except <text> and <title> themselves.
      if (['option', 'title', 'text'].includes(parent.tag) || !stack.some((el) => el.tag === 'svg')) found.push({source: normalizeKey(text), line: event.line, kind: 'text'});
    }
  }
  return found;
}
// The html`…` tag from web/i18n.js, applied to a template's static chunks.
export function htmlTemplateStrings(quasis) {
  const found = [], stack = [];
  const parts = quasis.map((part, index) => part + (index < quasis.length - 1 ? `__TM_ARG_${index}__` : '')).join('').split(/(<[^>]*>)/g);
  parts.forEach((part, partIndex) => {
    if (part.startsWith('<')) {
      const tag = part.match(/^<\/?([a-z][\w:-]*)/i)?.[1]?.toLowerCase();
      if (part.startsWith('</')) { const index = stack.lastIndexOf(tag); if (index !== -1) stack.splice(index); }
      else if (tag && !/\/>$/.test(part) && !['input', 'img', 'br', 'hr', 'meta', 'link', 'wbr'].includes(tag)) stack.push(tag);
      if (tag === 'option' && !part.startsWith('</')) {
        const label = decodeEntities(parts[partIndex + 1] || '');
        if (!label.includes('__TM_ARG_') && !label.includes('<') && isLanguage(label)) found.push({source: normalizeKey(label), kind: 'html option'});
      }
      for (const match of part.matchAll(/\bdata-tm-text=(['"])(.*?)\1/g)) if (isLanguage(decodeEntities(match[2]))) found.push({source: decodeEntities(match[2]), kind: 'data-tm-text'});
      if (stack.some((name) => ['pre', 'code', 'script', 'style', 'textarea'].includes(name))) return;
      for (const match of part.matchAll(/\b(title|placeholder|aria-label|alt)=(['"])(.*?)\2/g)) {
        if (!match[3].includes('__TM_ARG_') && isLanguage(decodeEntities(match[3]))) found.push({source: decodeEntities(match[3]), kind: 'html ' + match[1]});
      }
      return;
    }
    if (stack.at(-1) === 'option') return; // counted with its tag above
    if (stack.some((name) => ['pre', 'code', 'script', 'style', 'textarea', 'svg', 'text', 'title'].includes(name))) return;
    for (const piece of part.split(/(__TM_ARG_\d+__)/g)) {
      if (!/__TM_ARG_\d+__/.test(piece) && isLanguage(decodeEntities(piece))) found.push({source: normalizeKey(decodeEntities(piece)), kind: 'html text'});
    }
  });
  return found;
}

// ── JavaScript sources ────────────────────────────────────────────────────
const isOpen = (token) => token?.type === 'punct' && '([{'.includes(token.value);
const isClose = (token) => token?.type === 'punct' && ')]}'.includes(token.value);
// Call/parameter list from the '(' at `open`: [[tokens of arg0], …] and the index after ')'.
function callArguments(tokens, open) {
  const args = [[]];
  let depth = 0, i = open + 1;
  for (; i < tokens.length; i++) {
    const token = tokens[i];
    if (isOpen(token) || (token.type === 'tpl' && !token.static)) depth++;
    if (isClose(token) || (token.type === 'punct' && token.value === '`')) { if (depth === 0) break; depth--; }
    if (depth === 0 && token.type === 'punct' && token.value === ',') { args.push([]); continue; }
    args.at(-1).push(token);
  }
  return [args, i + 1];
}
// The message candidates of an expression: "a", `b`, x ? "a" : "b", x || "a",
// ({a: "X"})[key]. Values inside calls, indexes, objects or template
// expressions are arguments or keys, not the message. Identifiers in the
// same places are returned too (for helpers that pass a parameter on).
function expressionParts(tokens, {templates = false} = {}) {
  const literals = [], idents = [], stack = [];
  const opaque = () => stack.some((kind) => kind !== 'group');
  const position = (index) => {
    const before = tokens[index - 1], after = tokens[index + 1];
    const okBefore = !before || (before.type === 'punct' && ['?', ':', '|', '('].includes(before.value));
    const okAfter = !after || (after.type === 'punct' && [':', '|', '?', ')'].includes(after.value) && !(after.value === '?' && tokens[index + 2]?.value !== '?'));
    return okBefore && okAfter;
  };
  tokens.forEach((token, index) => {
    const before = tokens[index - 1], after = tokens[index + 1];
    if (token.type === 'punct' && token.value === '(') { stack.push(before && ((before.type === 'ident' && !KEYWORDS_BEFORE_EXPRESSION.has(before.value)) || [')', ']'].includes(before.value)) ? 'call' : 'group'); return; }
    if (token.type === 'punct' && token.value === '{') {
      let depth = 0, end = index;
      for (; end < tokens.length; end++) { if (tokens[end].value === '{') depth++; if (tokens[end].value === '}' && --depth === 0) break; }
      stack.push(tokens[end + 1]?.value === '[' && !opaque() ? 'table' : 'object'); return;
    }
    if (token.type === 'punct' && token.value === '[') { stack.push('index'); return; }
    if (isClose(token) || (token.type === 'punct' && token.value === '`')) { stack.pop(); return; }
    if (token.type === 'tpl' && !token.static) {
      if (templates && !opaque() && !token.tag) literals.push({token, sources: token.quasis});
      stack.push('template'); return;
    }
    if (token.type === 'ident' && !opaque() && position(index) && before?.value !== '.' && after?.value !== '.') { idents.push(token); return; }
    if (!(token.type === 'str' || (token.type === 'tpl' && token.static && !token.tag))) return;
    const source = token.type === 'str' ? token.value : token.quasis[0];
    if (stack.at(-1) === 'table' && stack.slice(0, -1).every((kind) => kind === 'group')) {
      if (before?.value === ':' && [',', '}'].includes(after?.value)) literals.push({token, sources: [source]});
      return;
    }
    if (!opaque() && position(index)) literals.push({token, sources: [source]});
  });
  return {literals, idents};
}
// A whole message built as a template with ${}: the key exists only at run
// time and can never match a catalogue row.
function dynamicTemplate(tokens) {
  const first = tokens[0];
  if (first?.type !== 'tpl' || first.static || first.tag) return null;
  let depth = 0, end = 0;
  for (; end < tokens.length; end++) {
    if (tokens[end].type === 'tpl' && !tokens[end].static) depth++;
    if (tokens[end].type === 'punct' && tokens[end].value === '`' && --depth === 0) break;
  }
  return end === tokens.length - 1 && first.quasis.some(isLanguage) ? first : null;
}
// Top-level `function NAME(…) {…}` and `const NAME = …;`: [name, params, first, last] token ranges.
function definitions(tokens) {
  const result = [];
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    let name = null, start = i, paramsStart = -1;
    if (token.value === 'function' && tokens[i + 1]?.type === 'ident' && tokens[i + 2]?.value === '(') { name = tokens[i + 1].value; paramsStart = i + 2; if (tokens[i - 1]?.value === 'async') start = i - 1; }
    else if (['const', 'let', 'var'].includes(token.value) && tokens[i + 1]?.type === 'ident' && tokens[i + 2]?.value === '=') { name = tokens[i + 1].value; if (tokens[i + 3]?.value === '(' || tokens[i + 4]?.value === '(') paramsStart = tokens[i + 3]?.value === '(' ? i + 3 : i + 4; }
    if (!name) continue;
    let params = [], bodyStart = i + 3;
    if (paramsStart !== -1) {
      const [args, after] = callArguments(tokens, paramsStart);
      params = args.map((arg) => arg[0]?.type === 'ident' ? arg[0].value : null);
      bodyStart = after;
      if (token.value !== 'function' && !(tokens[after]?.value === '=' && tokens[after + 1]?.value === '>')) { params = []; bodyStart = i + 3; }
    }
    let end = bodyStart, depth = 0;
    const block = token.value === 'function';
    for (; end < tokens.length; end++) {
      const tk = tokens[end];
      if (isOpen(tk) || (tk.type === 'tpl' && !tk.static)) depth++;
      if (isClose(tk) || (tk.type === 'punct' && tk.value === '`')) { depth--; if (block && depth === 0 && tk.value === '}') break; if (depth < 0) break; }
      if (!block && depth === 0 && tk.type === 'punct' && tk.value === ';') break;
    }
    result.push({name, params, start, bodyStart, end});
  }
  return result;
}
const TRANSLATORS = new Set(['t']);
// Local helpers that hand a parameter to t() or dataset.tmText, transitively.
function findWrappers(tokens, defs) {
  const wrappers = new Map();
  const mark = (name, parameter) => {
    if (!wrappers.has(name)) wrappers.set(name, new Set());
    if (wrappers.get(name).has(parameter)) return false;
    wrappers.get(name).add(parameter); return true;
  };
  let changed = true;
  while (changed) {
    changed = false;
    for (const {name, params, bodyStart, end} of defs) {
      if (!params.length) continue;
      for (let k = bodyStart; k < end; k++) {
        const tk = tokens[k];
        if (tk.type === 'ident' && (TRANSLATORS.has(tk.value) || wrappers.has(tk.value)) && tokens[k + 1]?.value === '(' && tokens[k - 1]?.value !== '.') {
          const [args] = callArguments(tokens, k + 1);
          const indices = TRANSLATORS.has(tk.value) ? new Set([0]) : wrappers.get(tk.value);
          args.forEach((arg, index) => {
            if (!indices.has(index)) return;
            for (const ident of expressionParts(arg).idents) if (params.includes(ident.value)) changed = mark(name, params.indexOf(ident.value)) || changed;
          });
        }
        if (tk.value === 'tmText' && tokens[k + 1]?.value === '=' && tokens[k + 2]?.value !== '=' && params.includes(tokens[k + 2]?.value)) changed = mark(name, params.indexOf(tokens[k + 2].value)) || changed;
        // PARAM.forEach(item => … t(item) …) / PARAM.map(…): each element of an array argument is a message.
        if (params.includes(tk.value) && tokens[k + 1]?.value === '.' && ['forEach', 'map'].includes(tokens[k + 2]?.value) && tokens[k + 3]?.value === '('
            && tokens[k + 4]?.type === 'ident' && tokens[k + 5]?.value === '=' && tokens[k + 6]?.value === '>') {
          const [, after] = callArguments(tokens, k + 3), item = tokens[k + 4].value;
          for (let m = k + 7; m < after; m++) {
            if (TRANSLATORS.has(tokens[m].value) && tokens[m + 1]?.value === '(' && tokens[m + 2]?.value === item && [')', ','].includes(tokens[m + 3]?.value)) changed = mark(name, params.indexOf(tk.value) + ':array') || changed;
          }
        }
      }
    }
  }
  return wrappers;
}
// String values of `const NAME = {…}` tables, for t(NAME[key]) lookups.
function tableValues(tokens, defs, name) {
  const result = [];
  for (const def of defs.filter((item) => item.name === name && tokens[item.start + 3]?.value === '{')) {
    let depth = 0;
    for (let j = def.start + 3; j < def.end; j++) {
      const token = tokens[j];
      if (isOpen(token)) depth++;
      if (isClose(token)) depth--;
      if (depth === 1 && token.type === 'str' && tokens[j - 1]?.value === ':' && [',', '}'].includes(tokens[j + 1]?.value)) result.push(token);
    }
  }
  return result;
}
const DIRECT_PROPERTIES = new Set(['textContent', 'innerText', 'title', 'placeholder', 'alt', 'label']);
const DIRECT_CALLS = new Set(['alert', 'confirm', 'prompt']);
const CODE_LIKE = /^(?:[#.[]|[\w.-]+\(|https?:|\/|data:)|^[\w$.:/-]+$|^[a-z][\w-]*(?: [a-z][\w-]*)*$|^[A-Z][A-Z0-9_]*(?: [A-Z0-9_]+)*$/;
// skip: names of top-level definitions to leave out (unreachable code).
export function jsStrings(source, {file = '', skip = []} = {}) {
  const tokens = lexJs(source);
  const defs = definitions(tokens);
  for (const def of defs) if (skip.includes(def.name)) for (let k = def.start; k <= def.end; k++) tokens[k].skip = true;
  const wrappers = findWrappers(tokens, defs);
  const found = [];
  const add = (token, sources, kind) => {
    token.captured = true;
    for (const value of sources) if (isLanguage(value)) found.push({source: kind.startsWith('html') ? value : normalizeKey(value), line: token.line, kind, file});
  };
  const tables = new Set();
  tokens.forEach((token, i) => {
    if (token.skip) return;
    // console.*(…) is for developers, not the page.
    if (token.value === 'console' && tokens[i + 1]?.value === '.' && tokens[i + 3]?.value === '(') {
      const [, after] = callArguments(tokens, i + 3);
      for (let k = i; k < after; k++) tokens[k].captured = true;
    }
    if (token.type === 'ident' && tokens[i + 1]?.value === '(' && !['.', 'function'].includes(tokens[i - 1]?.value)
        && (TRANSLATORS.has(token.value) || wrappers.has(token.value))) {
      const [args] = callArguments(tokens, i + 1);
      const indices = TRANSLATORS.has(token.value) ? new Set([0]) : wrappers.get(token.value);
      args.forEach((arg, index) => {
        if (indices.has(index + ':array') && arg[0]?.value === '[') {
          let depth = 0;
          arg.forEach((element, position) => {
            if (isOpen(element)) depth++; if (isClose(element)) depth--;
            if (depth === 1 && element.type === 'str' && [',', '['].includes(arg[position - 1]?.value) && [',', ']'].includes(arg[position + 1]?.value)) add(element, [element.value], token.value + '([…])');
          });
        }
        if (!indices.has(index)) return;
        const kind = TRANSLATORS.has(token.value) ? 't()' : token.value + '()';
        for (const {token: literal, sources} of expressionParts(arg).literals) add(literal, sources, kind);
        const dynamic = dynamicTemplate(arg);
        if (dynamic) { dynamic.captured = true; found.push({source: dynamic.quasis.join('${…}'), line: dynamic.line, kind: 'unwrapped', file}); }
        // t(LABELS[key]) / t(LABELS.key): the table's values are messages.
        if (arg[0]?.type === 'ident' && ['[', '.', '?'].includes(arg[1]?.value)) tables.add(arg[0].value);
      });
    }
    // Error texts end up in setMessage(…, error.message), which translates.
    if (token.value === 'Error' && tokens[i - 1]?.value === 'new' && tokens[i + 1]?.value === '(') {
      const [args] = callArguments(tokens, i + 1);
      for (const {token: literal, sources} of expressionParts(args[0] || []).literals) if (sources.some(isProse)) add(literal, sources, 'Error()');
    }
    if (token.type === 'tpl' && token.tag === 'html') { token.captured = true; for (const item of htmlTemplateStrings(token.quasis)) found.push({...item, line: token.line, file}); }
    if (token.value === 'tmText' && tokens[i + 1]?.value === '=' && tokens[i + 2]?.value !== '=') {
      let end = i + 2; while (end < tokens.length && tokens[end].line === token.line && tokens[end].value !== ';') end++;
      for (const {token: literal, sources} of expressionParts(tokens.slice(i + 2, end)).literals) add(literal, sources, 'dataset.tmText');
    }
    if ((token.type === 'str' || token.type === 'tpl') && token.tag !== 'html') {
      for (const chunk of token.type === 'str' ? [token.value] : token.quasis) {
        for (const match of chunk.matchAll(/\bdata-tm-text=(['"])(.*?)\1/g)) if (isLanguage(decodeEntities(match[2]))) found.push({source: decodeEntities(match[2]), line: token.line, kind: 'data-tm-text', file});
      }
    }
  });
  for (const name of tables) for (const token of tableValues(tokens, defs, name)) if (!token.skip) add(token, [token.value], `t(${name}[…])`);
  // Unwrapped: literal text written straight into the page.
  tokens.forEach((token, i) => {
    if (token.skip) return;
    let parts = null;
    if (token.type === 'ident' && DIRECT_PROPERTIES.has(token.value) && tokens[i - 1]?.value === '.' && tokens[i + 1]?.value === '=' && tokens[i + 2]?.value !== '=') {
      let end = i + 2, depth = 0;
      for (; end < tokens.length; end++) {
        const tk = tokens[end];
        if (isOpen(tk)) depth++;
        if (isClose(tk) && --depth < 0) break;
        if (depth === 0 && tk.type === 'punct' && [';', ','].includes(tk.value)) break;
      }
      parts = expressionParts(tokens.slice(i + 2, end), {templates: true});
    }
    if (token.value === 'setAttribute' && tokens[i + 1]?.value === '(') {
      const [args] = callArguments(tokens, i + 1);
      if (args[0]?.[0]?.type === 'str' && ATTRIBUTES.includes(args[0][0].value)) parts = expressionParts(args[1] || [], {templates: true});
    }
    if (token.type === 'ident' && DIRECT_CALLS.has(token.value) && tokens[i + 1]?.value === '(' && tokens[i - 1]?.value !== 'function'
        && (tokens[i - 1]?.value !== '.' || tokens[i - 2]?.value === 'window')) {
      const [args] = callArguments(tokens, i + 1); parts = expressionParts(args[0] || [], {templates: true});
    }
    for (const {token: literal, sources} of parts?.literals || []) {
      const text = sources.filter((value) => isLanguage(value) && !CODE_LIKE.test(value.trim()));
      if (text.length) add(literal, text, 'unwrapped');
    }
  });
  // Literal: every other prose-like string in the code. Prose around ${}
  // in a plain template is built at run time and can never be looked up.
  for (const token of tokens) {
    if (token.skip || token.captured || token.tag === 'html' || !(token.type === 'str' || token.type === 'tpl')) continue;
    if (token.type === 'tpl' && !token.static && token.quasis.some(isProse)) { found.push({source: token.quasis.join('${…}'), line: token.line, kind: 'unwrapped', file}); continue; }
    for (const value of token.type === 'str' ? [token.value] : token.quasis) {
      if (isProse(value)) found.push({source: normalizeKey(value), line: token.line, kind: 'literal', file});
      // Not prose, but the key of a catalogue row: checked only if that row is incomplete.
      else if (/^[A-ZÅÄÖ][a-zåäö]{2,}$/.test(value)) found.push({source: value, line: token.line, kind: 'word', file});
    }
  }
  return found;
}

// ── Surfaces ──────────────────────────────────────────────────────────────
const web = (name) => path.join(pkg, 'web', name);
const t16 = (name) => path.join(pkg, 'terminal16_web', name);
// The in-app TMBox client and documentation (#tmbox-v2-view) is unreachable:
// the #tmbox route redirects to /tmbox/ and setMode("tmbox") is never called.
// Its scripts (tmbox-fixtures/-legacy-catalog/-guide/-render/-nav/-attention)
// are therefore not audited, and neither are these functions in app.js.
const UNREACHABLE_APP = ['v2El', 'v2Geometry', 'startTMBoxV2', 'stopTMBoxV2', 'buildV2Keypad', 'TMBOX_PANES', 'tmboxPane', 'paintFrame',
  'tmboxDocGeometry', 'tmboxLegacyDocs', 'documentationFlows', 'buildTMBoxGuide', 'buildScreenCatalog', 'replayTrace', 'showFlow',
  'buildFlowList', 'redrawTMBoxDocs', 'selectTMBoxPane', 'bindTMBoxPanes', 'bindV2Controls', 'loadV2Stations', 'connectBrowserTMBox',
  'boxFetch', 'openBoxLanguage', 'refreshTMBoxV2', 'v2NormaliseConfig', 'v2Now', 'drawV2', 'pressV2Key', 'V2_ATTENTION', 'v2Signal',
  'v2Beep', 'v2Payload'];
export const SURFACES = {
  'server web': {
    catalog: ['i18n-messages.js', 'meet-type-messages.js', 'shell-messages.js', 'participant-messages.js'].map(web),
    html: [web('index.html')],
    css: fs.readdirSync(path.join(pkg, 'web')).filter((name) => name.endsWith('.css')).map(web),
    js: ['app.js', 'drift.js', 'drift-model.js', 'live-events.js', 'participant.js', 'server-ui.js', 'settings.js', 'simulation-banner.js', 'day-change.js', 'clock-face.js', 'kr-theme.js', 'data-page.js'].map(web),
    skip: {'app.js': UNREACHABLE_APP},
  },
  'US pages': {
    catalog: [web('i18n-messages.js'), web('meet-type-messages.js'), web('us-cloud-messages.js'), path.join(pkg, 'us_web/workspace-messages.js')],
    html: [path.join(pkg, 'us_web/index.html')],
    css: [path.join(pkg, 'us_web/style.css')],
    js: [path.join(pkg, 'us_web/app.js')],
  },
  'terminal16': {
    catalog: [web('i18n-messages.js'), web('tmbox-messages.js')].filter((file) => fs.existsSync(file)),
    html: ['index.html', 'live.html', 'floden.html'].map(t16),
    css: ['style.css', 'flows.css'].map(t16),
    js: ['terminal.js', 'flows-page.js', 'lcd.js'].map(t16).concat([web('simulation-banner.js'), web('day-change.js')]),
  },
};
// Prose literals that are not UI text, each with the reason.
export const IGNORE = new Map([
  ['Mileposts', 'US railroad term kept in English in every language (see meet-type-messages.js).'],
]);

// TKL is a React app in its own repository; it owns the TSX rules in
// scripts/i18n-audit.mjs (run by its npm test). This report includes it when
// the TrainMeet TKL checkout sits next to this one and has its dependencies.
async function auditTkl(root) {
  const script = path.join(root, 'scripts/i18n-audit.mjs');
  if (!fs.existsSync(script)) return {skipped: 'no scripts/i18n-audit.mjs in ' + root};
  if (!fs.existsSync(path.join(root, 'node_modules/typescript'))) return {skipped: 'run npm install in ' + root};
  return (await import(pathToFileURL(script).href)).auditTkl({root});
}

export async function audit({tkl = path.join(repo, '..', 'trainmeet-tkl'), surfaces = null} = {}) {
  const result = {};
  for (const [name, surface] of Object.entries(SURFACES)) {
    if (surfaces && !surfaces.includes(name)) continue;
    const catalog = loadCatalog(surface.catalog);
    const items = [];
    for (const file of surface.html || []) items.push(...htmlStrings(fs.readFileSync(file, 'utf8')).map((item) => ({...item, file})));
    for (const file of surface.js || []) items.push(...jsStrings(fs.readFileSync(file, 'utf8'), {file, skip: surface.skip?.[path.basename(file)] || []}));
    // CSS content: "…" puts text on the page that no lookup ever sees.
    for (const file of surface.css || []) {
      fs.readFileSync(file, 'utf8').split('\n').forEach((line, index) => {
        for (const match of line.matchAll(/\bcontent:\s*(['"])(.*?)\1/g)) if (isLanguage(match[2])) items.push({source: match[2], line: index + 1, kind: 'unwrapped', file});
      });
    }
    const sources = new Map();
    for (const item of items) {
      if (item.kind === 'literal' && IGNORE.has(item.source)) continue;
      if (item.kind === 'word' && !lookup(catalog, item.source)) continue;
      if (item.kind === 'word') item.kind = 'literal';
      if (!sources.has(item.source)) sources.set(item.source, {source: item.source, places: [], kinds: new Set()});
      const entry = sources.get(item.source);
      entry.places.push(`${path.relative(path.dirname(repo), item.file)}:${item.line ?? '?'} ${item.kind}`);
      entry.kinds.add(item.kind === 'unwrapped' || item.kind === 'literal' ? item.kind : 'message');
    }
    const missing = [], unwrapped = [];
    for (const entry of sources.values()) {
      const gaps = missingLocales(lookup(catalog, entry.source));
      if (gaps.length && (entry.kinds.has('message') || entry.kinds.has('literal'))) missing.push({source: entry.source, missing: gaps, places: entry.places});
      if (entry.kinds.has('unwrapped')) unwrapped.push({source: entry.source, places: entry.places.filter((place) => place.endsWith(' unwrapped'))});
    }
    result[name] = {strings: sources.size, missing, unwrapped};
  }
  if (tkl && fs.existsSync(tkl) && (!surfaces || surfaces.includes('TKL'))) result.TKL = await auditTkl(tkl);
  return result;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2), tklIndex = args.indexOf('--tkl');
  const result = await audit(tklIndex !== -1 ? {tkl: path.resolve(args[tklIndex + 1])} : {});
  let failed = false;
  if (args.includes('--json')) console.log(JSON.stringify(result, null, 1));
  for (const [name, surface] of Object.entries(result)) {
    if (surface.skipped) { if (!args.includes('--json')) console.log(`${name}: skipped (${surface.skipped})`); continue; }
    failed ||= surface.missing.length + surface.unwrapped.length > 0;
    if (args.includes('--json')) continue;
    console.log(`${name}: ${surface.strings} texts, ${surface.missing.length} lacking a language, ${surface.unwrapped.length} written without a lookup`);
    if (!args.includes('--list')) continue;
    for (const entry of surface.missing) console.log(`  missing ${entry.missing.join(',')}: ${JSON.stringify(entry.source)}\n      ${entry.places.slice(0, 4).join('\n      ')}`);
    for (const entry of surface.unwrapped) console.log(`  unwrapped: ${JSON.stringify(entry.source)}\n      ${entry.places.slice(0, 4).join('\n      ')}`);
  }
  process.exitCode = failed ? 1 : 0;
}
