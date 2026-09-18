/* TrainMeet UI localization. Vendored unchanged into Cloud and TKL.
 * Only developer-authored messages are translated. Domain data and issued
 * authorities are deliberately not passed through this API. No network calls.
 */
(() => {
  const languages = [
    ['sv', 'Svenska'], ['da', 'Dansk'], ['nb', 'Norsk (bokmål)'],
    ['en', 'English'], ['de', 'Deutsch'],
  ];
  // A US operating view must not inherit an EU/admin language choice. Only
  // an explicit choice made in the US scope overrides its English default.
  const usScope = globalThis.document?.documentElement?.dataset?.i18nScope === 'us';
  const key = usScope ? 'trainmeet.language.us' : 'trainmeet.language';
  const normalize = (value) => {
    const base = String(value || '').toLowerCase().split(/[-_]/)[0];
    return ['no', 'nn'].includes(base) ? 'nb' : languages.some(([id]) => id === base) ? base : null;
  };
  const stored = () => { try { return localStorage.getItem(key) || (!usScope && localStorage.getItem('tkl-language')); } catch { return null; } };
  let language = normalize(stored()) || (usScope ? 'en' : (globalThis.navigator?.languages || []).map(normalize).find(Boolean) || normalize(globalThis.navigator?.language) || 'sv');
  const subscribers = new Set();
  const missing = new Set();
  function t(source, values = {}) {
    // Line wrapping in authored HTML is not part of a message's identity.
    // Unknown text and interpolated domain values still remain byte-exact.
    const row = globalThis.TrainMeetMessages?.[source]
      || globalThis.TrainMeetMessages?.[String(source).trim().replace(/\s+/g, ' ')];
    const translated = row?.[language] || row?.en || source;
    if (!row?.[language] && source && /[a-zåäöæøü]/i.test(source)) missing.add(source);
    // Function replacement: values containing $&, $1 etc. remain literal.
    return translated.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(values, name) ? String(values[name]) : match);
  }
  function subscribe(callback) { subscribers.add(callback); return () => subscribers.delete(callback); }
  function setLanguage(value, persist = true) {
    const next = normalize(value);
    if (!next) return;
    if (persist) { try { localStorage.setItem(key, next); } catch { /* private browser: session only */ } }
    language = next;
    if (globalThis.document) {
      document.documentElement.lang = next;
      updateMarked(document);
      document.querySelectorAll('[data-language-picker]').forEach((select) => { select.value = next; });
    }
    subscribers.forEach((callback) => callback());
  }
  const escape = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const protectedTags = 'script,style,pre,code,textarea,[data-no-i18n]';
  function localizeText(source) {
    const text = source.trim();
    return text ? source.replace(text, () => t(text)) : source;
  }
  // This is called ONLY on authored HTML, before interpolated data is inserted.
  // Never call it on a populated application subtree or on server responses.
  function annotate(root) {
    const walker = document.createTreeWalker(root, 4);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const parent = node.parentElement;
      if (!parent || parent.closest(protectedTags) || parent.closest('[data-tm-text]')) continue;
      const original = node.textContent;
      if (!/[a-zåäöæøü]/i.test(original.replace(/__TM_ARG_\d+__/g, ''))) continue;
      // HTML templates can contain dynamic fragments. Translate only literal
      // text, never dynamic fragments or the content inside them.
      const parts = original.split(/(__TM_ARG_\d+__)/g);
      if (['OPTION', 'TITLE', 'TEXT'].includes(parent.tagName.toUpperCase()) && parts.length === 1) {
        if (parent.tagName.toUpperCase() === 'OPTION' && !parent.hasAttribute('value')) parent.setAttribute('value', original.trim());
        parent.dataset.tmText = original;
        node.textContent = localizeText(original);
      } else if (parent.namespaceURI !== 'http://www.w3.org/2000/svg' && parent.tagName !== 'OPTION') {
        const fragment = document.createDocumentFragment();
        for (const part of parts) {
          if (/__TM_ARG_\d+__/.test(part) || !/[a-zåäöæøü]/i.test(part)) fragment.append(document.createTextNode(part));
          else {
            const span = document.createElement('tm-text');
            span.style.display = 'contents';
            span.dataset.tmText = part;
            span.textContent = localizeText(part);
            fragment.append(span);
          }
        }
        node.replaceWith(fragment);
      }
    }
    root.querySelectorAll('*').forEach((element) => {
      if (element.closest(protectedTags)) return;
      for (const attr of ['title', 'placeholder', 'aria-label', 'alt']) {
        const source = element.getAttribute(attr);
        if (!source || source.includes('__TM_ARG_') || !/[a-zåäöæøü]/i.test(source)) continue;
        element.setAttribute('data-tm-' + attr, source);
        element.setAttribute(attr, t(source));
      }
    });
  }
  function updateMarked(root) {
    root.querySelectorAll('[data-tm-text]').forEach((element) => { element.textContent = localizeText(element.dataset.tmText); });
    for (const attr of ['title', 'placeholder', 'aria-label', 'alt']) {
      root.querySelectorAll('[data-tm-' + attr + ']').forEach((element) => { element.setAttribute(attr, t(element.getAttribute('data-tm-' + attr))); });
    }
  }
  const templates = new WeakMap();
  function html(strings, ...values) {
    // Do not parse/serialize the entire template: conditional attribute
    // interpolations (e.g. ${disabled ? 'disabled' : ''}) must stay byte-exact.
    let parts = templates.get(strings);
    if (!parts) {
      parts = strings.map((part, index) => part + (index < strings.length - 1 ? `__TM_ARG_${index}__` : '')).join('').split(/(<[^>]*>)/g);
      templates.set(strings, parts);
    }
    const stack = [];
    const decode = (source) => { const element = document.createElement('textarea'); element.innerHTML = source; return element.value; };
    const result = parts.map((part, partIndex) => {
      if (part.startsWith('<')) {
        const tag = part.match(/^<\/?([a-z][\w:-]*)/i)?.[1]?.toLowerCase();
        if (part.startsWith('</')) { const index = stack.lastIndexOf(tag); if (index !== -1) stack.splice(index); }
        else if (tag && !/\/>$/.test(part) && !['input','img','br','hr','meta','link','wbr'].includes(tag)) stack.push(tag);
        if (tag === 'option' && !part.startsWith('</')) {
          const label = parts[partIndex + 1] || '';
          if (!label.includes('__TM_ARG_') && !label.includes('<')) {
            const source = decode(label);
            const value = /\bvalue\s*=/.test(part) ? '' : ` value="${escape(source)}"`;
            part = part.replace(/>$/, `${value} data-tm-text="${escape(source)}">`);
          }
        }
        if (stack.some((tag) => ['pre','code','script','style','textarea'].includes(tag))) return part;
        return part.replace(/\b(title|placeholder|aria-label|alt)=(['"])(.*?)\2/g, (whole, attr, quote, source) => {
          if (source.includes('__TM_ARG_')) return whole;
          const decoded = decode(source);
          return `${attr}="${escape(t(decoded))}" data-tm-${attr}="${escape(decoded)}"`;
        });
      }
      if (stack.at(-1) === 'option' && !part.includes('__TM_ARG_')) return escape(localizeText(decode(part)));
      if (stack.some((tag) => ['pre','code','script','style','textarea','svg','text','option','title'].includes(tag))) return part;
      return part.split(/(__TM_ARG_\d+__)/g).map((source) => {
        if (/__TM_ARG_\d+__/.test(source) || !/[a-zåäöæøü]/i.test(source)) return source;
        const decoded = decode(source);
        return `<tm-text data-tm-text="${escape(decoded)}">${escape(localizeText(decoded))}</tm-text>`;
      }).join('');
    }).join('');
    // Keep the original application's escaping boundary. An interpolation is
    // already escaped text or trusted HTML; translating it would corrupt data.
    return result.replace(/__TM_ARG_(\d+)__/g, (_, index) => String(values[Number(index)] ?? ''));
  }
  function initializeStatic() {
    // All static strings are annotated in the shipped source by the migration
    // tool. No observation/translation of user-generated DOM is performed.
    document.documentElement.lang = language;
    updateMarked(document);
    document.querySelectorAll('[data-language-picker]').forEach((select) => {
      select.innerHTML = languages.map(([id, label]) => `<option value="${id}">${escape(label)}</option>`).join('');
      select.value = language;
      select.addEventListener('change', () => setLanguage(select.value));
    });
  }
  globalThis.TrainMeetI18n = {languages, normalize, t, html, subscribe, setLanguage, getLanguage: () => language, getLocale: () => ({sv:'sv-SE',da:'da-DK',nb:'nb-NO',en:usScope?'en-US':'en-GB',de:'de-DE'})[language], missing: () => [...missing].sort(), annotate, initializeStatic};
  if (globalThis.document) document.documentElement.lang = language;
  globalThis.addEventListener?.('storage', (event) => { if (event.key === key && event.newValue) setLanguage(event.newValue, false); });
})();
