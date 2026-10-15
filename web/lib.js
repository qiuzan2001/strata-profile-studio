/* Strata Profile Studio: shared helpers (DOM and SVG builders, formatters, the API, the router).
   Every node is made with createElement / createElementNS and filled with textContent: no HTML string from the
   server is ever parsed. */
'use strict';

const S = (window.S = {});

// ------------------------------------------------------------------------------------------------ DOM
S.el = function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  S.attrs(n, attrs);
  S.append(n, kids);
  return n;
};

S.attrs = function attrs(n, a) {
  if (!a) return n;
  for (const [k, v] of Object.entries(a)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k === 'style') Object.assign(n.style, v);
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(n.dataset, v);
    else n.setAttribute(k, v === true ? '' : String(v));
  }
  return n;
};

S.append = function append(n, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    n.appendChild(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
  }
  return n;
};

S.clear = function clear(n) {
  while (n.firstChild) n.removeChild(n.firstChild);
  return n;
};

S.fill = function fill(n, ...kids) {
  S.clear(n);
  return S.append(n, kids);
};

const SVGNS = 'http://www.w3.org/2000/svg';
S.svg = function svg(tag, attrs, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'text') n.textContent = v;
      else if (k === 'class') n.setAttribute('class', v);
      else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, String(v));
    }
  }
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    n.appendChild(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
  }
  return n;
};

S.title = function title(node, text) {         // a native tooltip on an SVG mark
  node.appendChild(S.svg('title', { text }));
  return node;
};

// ------------------------------------------------------------------------------------- in-place updates
// Live regions never rebuild: they change a text node's value, an attribute or one style property, and only when the
// value is new.  A text change keeps the node (a characterData mutation, no layout of new nodes).
S.setText = function setText(node, s) {
  s = s === null || s === undefined ? '' : String(s);
  const t = node.firstChild;
  if (t && t.nodeType === 3 && !t.nextSibling) { if (t.nodeValue !== s) t.nodeValue = s; return node; }
  if (node.textContent !== s) node.textContent = s;
  return node;
};
S.setAttr = function setAttr(node, k, v) {
  const s = v === null || v === undefined ? null : String(v);
  if (s === null) { if (node.hasAttribute(k)) node.removeAttribute(k); } else if (node.getAttribute(k) !== s) node.setAttribute(k, s);
  return node;
};
S.setStyle = function setStyle(node, prop, v) {
  if (node.style[prop] !== v) node.style[prop] = v;
  return node;
};
S.setClass = function setClass(node, cls, on) {
  if (node.classList.contains(cls) !== !!on) node.classList.toggle(cls, !!on);
  return node;
};
S.show = function show(node, on) {
  if (node.hidden === !!on) node.hidden = !on;
  return node;
};

// ------------------------------------------------------------------------------------------- numbers
const isNum = (S.isNum = (x) => typeof x === 'number' && isFinite(x));

S.int = (x) => (isNum(x) ? Math.round(x).toLocaleString('en-US') : '–');
S.num = (x, d = 1) => (isNum(x) ? x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '–');
S.tps = (x) => (isNum(x) ? (x >= 1000 ? S.int(x) : S.num(x, 1)) : '–');
S.pct = (f, d = 0) => (isNum(f) ? `${(f * 100).toFixed(d)}%` : '–');
S.signed = (x, d = 1, unit = '') => (isNum(x) ? `${x > 0 ? '+' : x < 0 ? '−' : '±'}${Math.abs(x).toFixed(d)}${unit}` : '–');
S.ms = function ms(x) {
  if (!isNum(x)) return '–';
  if (x >= 10000) return `${(x / 1000).toFixed(1)} s`;
  if (x >= 1000) return `${(x / 1000).toFixed(2)} s`;
  if (x >= 100) return `${Math.round(x)} ms`;
  if (x >= 10) return `${x.toFixed(1)} ms`;
  return `${x.toFixed(1)} ms`;
};
S.bytes = function bytes(b, unit = 'GiB', d = 1) {
  if (!isNum(b)) return '–';
  const div = unit === 'GiB' ? 2 ** 30 : unit === 'MiB' ? 2 ** 20 : 1;
  return `${(b / div).toFixed(d)} ${unit}`;
};
S.mbps = function mbps(mb) {
  if (!isNum(mb)) return '–';
  return mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB/s` : `${Math.round(mb)} MB/s`;
};
S.dur = function dur(s) {
  if (!isNum(s)) return '–';
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
};
S.clock = function clock(t, withDate) {
  if (!isNum(t)) return '–';
  const d = new Date(t * 1000);
  const hm = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: withDate === 'sec' ? '2-digit' : undefined });
  if (withDate === true) return `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })} ${hm}`;
  return hm;
};
S.ago = function ago(t) {
  if (!isNum(t)) return '';
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
S.median = function median(xs) {
  const v = xs.filter(isNum).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = v.length >> 1;
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
};
S.quantile = function quantile(xs, q) {
  const v = xs.filter(isNum).sort((a, b) => a - b);
  if (!v.length) return null;
  const i = (v.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
  return v[lo] + (v[hi] - v[lo]) * (i - lo);
};
S.sum = (xs) => xs.filter(isNum).reduce((a, b) => a + b, 0);

// ------------------------------------------------------------------------------------- the vocabulary
// which part of the machine a piece of time is spent on: one class per part, the colours are in style.css
S.DECODE_PARTS = ['GPU', 'CPU experts', 'draft', 'adapt wait', 'other'];
S.PROMPT_GROUPS = ['experts over PCIe', 'dequant', 'expert GEMM', 'attention', 'GDN', 'other'];
S.PART_CLASS = {
  'GPU': 'k-gpu', 'CPU experts': 'k-cpu', 'draft': 'k-draft', 'adapt wait': 'k-adapt', 'other': 'k-other',
  'experts over PCIe': 'k-pcie', 'dequant': 'k-dequant', 'expert GEMM': 'k-gemm', 'attention': 'k-attn', 'GDN': 'k-gdn',
  'host': 'k-host', 'not split': 'k-none',
};
S.PART_LABEL = {
  'GPU': 'GPU', 'CPU experts': 'CPU experts', 'draft': 'draft', 'adapt wait': 'adapt wait', 'other': 'other',
  'experts over PCIe': 'PCIe copies', 'dequant': 'dequant', 'expert GEMM': 'expert GEMM', 'attention': 'attention',
  'GDN': 'GDN', 'host': 'host', 'not split': 'not split',
};
S.PART_ABOUT = {
  'GPU': 'The host waits for the GPU to complete the window.',
  'CPU experts': 'The CPU pool computes the experts that are not in VRAM. RAM bandwidth limits it.',
  'draft': 'The draft layer proposes the next tokens.',
  'adapt wait': 'The adaptive tier swaps experts into VRAM.',
  'other': 'Commit, stage, and the time that the engine does not split.',
  'experts over PCIe': 'The GPU waits for expert copies over PCIe (wait copy, gather, host grouping).',
  'dequant': 'The GPU expands the copied experts.',
  'expert GEMM': 'The GPU multiplies the experts (gate/up, down, combine).',
  'attention': 'QSA projections, indexer, selection, attention and KV staging.',
  'GDN': 'The linear-attention layers.',
  'host': 'Prompt time outside the GPU timeline (host staging, PLE, chunk setup).',
  'not split': 'This request has no phase split (it comes from the history).',
};
S.PHASE_GROUP = {
  'wait copy': 'experts over PCIe', 'gather': 'experts over PCIe', 'host grouping': 'experts over PCIe',
  'dequant': 'dequant', 'gemm gate/up': 'expert GEMM', 'gemm down': 'expert GEMM', 'combine': 'expert GEMM',
  'qsa proj': 'attention', 'qsa indexer': 'attention', 'qsa select': 'attention', 'qsa attn': 'attention',
  'kv stage': 'attention', 'gdn': 'GDN', 'gdn conv+gates': 'GDN', 'gdn recurrence': 'GDN', 'gdn out proj': 'GDN',
};
S.KINDS = ['short', 'follow-up', 'long prompt', 'prompt only'];
S.KIND_LABEL = { 'short': 'short chat', 'follow-up': 'follow-up turn', 'long prompt': 'long prompt', 'prompt only': 'prompt only' };
S.KIND_ABOUT = {
  'short': 'A new prompt of less than 8K tokens.',
  'follow-up': 'The conversation cache reused a part of the prompt.',
  'long prompt': 'A new prompt of 8K tokens or more.',
  'prompt only': 'One token or no token generated.',
};

// the marker of a request kind, centred on (x, y) with radius r
S.kindMark = function kindMark(kind, x, y, r, cls) {
  const c = `mark ${cls || ''} kind-${(kind || 'short').replace(' ', '-')}`;
  if (kind === 'follow-up') return S.svg('path', { class: c, d: `M${x} ${y - r * 1.25}L${x + r * 1.25} ${y}L${x} ${y + r * 1.25}L${x - r * 1.25} ${y}Z` });
  if (kind === 'long prompt') return S.svg('path', { class: c, d: `M${x} ${y - r * 1.3}L${x + r * 1.2} ${y + r * 0.9}L${x - r * 1.2} ${y + r * 0.9}Z` });
  return S.svg('circle', { class: c + (kind === 'prompt only' ? ' hollow' : ''), cx: x, cy: y, r });
};
S.kindIcon = function kindIcon(kind) {
  const s = S.svg('svg', { class: 'kind-icon', viewBox: '-6 -6 12 12', width: 12, height: 12, 'aria-hidden': 'true' });
  s.appendChild(S.kindMark(kind, 0, 0, 3.6));
  return s;
};

// the run a report id belongs to, as people say it: "run 6"
S.runNo = function runNo(id, reports) {
  const r = (reports || []).find((x) => x.id === id);
  if (r && r.order) return r.order[3];
  const m = /-(\d+)$/.exec(id || '');
  return m ? Number(m[1]) : 1;
};
S.runName = function runName(id, reports) {
  const r = (reports || []).find((x) => x.id === id);
  if (!r) return id ? `run ${S.runNo(id)}` : '–';
  const sameDay = (reports || []).filter((x) => x.date === r.date).length;
  return `run ${r.order[3]}${sameDay === (reports || []).length ? '' : ` · ${r.date}`}`;
};

// -------------------------------------------------------------------------------------------- the API
S.api = async function api(path, opts) {
  const o = Object.assign({ headers: { Accept: 'application/json' } }, opts || {});
  if (o.body !== undefined && typeof o.body !== 'string') {
    o.body = JSON.stringify(o.body);
    o.method = o.method || 'POST';
    o.headers['Content-Type'] = 'application/json';
  }
  const key = S.studioKey();
  if (key) o.headers.Authorization = `Bearer ${key}`;
  let r;
  try {
    r = await fetch(path, o);
  } catch (e) {
    const err = new Error('The Studio does not answer. Make sure that studio.py runs.');
    err.status = 0;
    throw err;
  }
  let doc = null;
  try { doc = await r.json(); } catch (e) { doc = null; }
  if (!r.ok) {
    const err = new Error((doc && doc.error && doc.error.message) || `The Studio answered ${r.status}.`);
    err.status = r.status;
    throw err;
  }
  return doc;
};

S.store = {
  get(k, d) { try { const v = localStorage.getItem('studio.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('studio.' + k, JSON.stringify(v)); } catch (e) { /* private window */ } },
};
S.studioKey = () => { try { return sessionStorage.getItem('studio.key') || ''; } catch (e) { return ''; } };

// ------------------------------------------------------------------------------------------- the router
// #now  #now/r/<seq>  #runs  #runs/<id>  #runs/<id>/r/<req>  #compare/<a>/<b>  #tune  #tune/<id>  #settings[/<id>]
S.route = function route() {
  const h = decodeURIComponent((location.hash || '#now').slice(1));
  const parts = h.split('/').filter(Boolean);
  const view = ['now', 'runs', 'compare', 'tune', 'settings'].includes(parts[0]) ? parts[0] : 'now';
  const r = { view, parts, panel: null };
  const ri = parts.indexOf('r');
  if (ri >= 0 && parts[ri + 1]) r.panel = { where: view === 'runs' ? 'report' : 'live', report: view === 'runs' ? parts[1] : null, id: parts[ri + 1] };
  return r;
};
S.go = function go(hash) {
  if (location.hash !== hash) location.hash = hash;
};

// --------------------------------------------------------------------------------------------- widgets
// one chart card: the question as its title, the measure as its subtitle, the chart, an optional verdict line
S.card = function card(opts, ...body) {
  const head = S.el('header', { class: 'card-head' },
    S.el('h3', { text: opts.q }),
    S.el('p', { class: 'sub', text: opts.sub || '' }),
    opts.tools || null);
  return S.el('section', { class: `card ${opts.cls || ''}`, id: opts.id }, head, ...body,
    opts.verdict ? S.el('p', { class: 'verdict', text: opts.verdict }) : null);
};

// a headline tile: the big number, its unit, a short label and a note
S.tile = function tile(label, value, unit, note, cls) {
  return S.el('div', { class: `tile ${cls || ''}` },
    S.el('div', { class: 'tile-label', text: label }),
    S.el('div', { class: 'tile-value' }, S.el('span', { class: 'v', text: value }), unit ? S.el('span', { class: 'u', text: unit }) : null),
    note ? (typeof note === 'string' ? S.el('div', { class: 'tile-note', text: note }) : S.el('div', { class: 'tile-note' }, note)) : null);
};

S.chip = (text, cls, title) => S.el('span', { class: `chip ${cls || ''}`, text, title });

S.seg = function seg(options, value, onChange, label) {   // a segmented control
  const box = S.el('div', { class: 'seg', role: 'group', 'aria-label': label || '' });
  for (const [v, text] of options) {
    box.appendChild(S.el('button', {
      type: 'button', class: v === value ? 'on' : '', 'aria-pressed': v === value ? 'true' : 'false', text,
      onclick: () => onChange(v),
    }));
  }
  return box;
};

S.button = (text, onclick, cls, attrs) => S.el('button', Object.assign({ type: 'button', class: `btn ${cls || ''}`, text, onclick }, attrs || {}));

S.legend = function legend(keys, labels) {
  return S.el('div', { class: 'legend' }, keys.map((k) =>
    S.el('span', { class: 'lg', title: S.PART_ABOUT[k] || '' }, S.el('i', { class: `sw ${S.PART_CLASS[k] || ''}` }), (labels || S.PART_LABEL)[k] || k)));
};

// a plain table, for the details views only
S.table = function table(cols, rows, opts) {
  const o = opts || {};
  const t = S.el('table', { class: `data ${o.cls || ''}` });
  t.appendChild(S.el('thead', null, S.el('tr', null, cols.map((c) => S.el('th', { class: c.num ? 'num' : '', text: c.label, title: c.title })))));
  const tb = S.el('tbody');
  for (const r of rows) {
    const tr = S.el('tr', { class: r._cls || '' });
    if (r._onclick) { tr.classList.add('clickable'); tr.addEventListener('click', r._onclick); tr.tabIndex = 0; tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') r._onclick(e); }); }
    for (const c of cols) {
      const v = c.get ? c.get(r) : r[c.key];
      const td = S.el('td', { class: c.num ? 'num' : '' });
      if (v instanceof Node) td.appendChild(v); else td.textContent = v === null || v === undefined ? '–' : String(v);
      tr.appendChild(td);
    }
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  return S.el('div', { class: 'table-wrap' }, t);
};

S.csv = function csv(name, cols, rows) {
  const esc = (v) => { const s = v === null || v === undefined ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = [cols.map((c) => esc(c.label)).join(',')].concat(rows.map((r) => cols.map((c) => esc(c.raw ? c.raw(r) : (c.get ? c.get(r) : r[c.key]))).join(','))).join('\n');
  const a = S.el('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: name });
  document.body.appendChild(a); a.click(); a.remove();
};

S.toast = function toast(text, cls) {
  const box = document.getElementById('toasts');
  const t = S.el('div', { class: `toast ${cls || ''}`, role: 'status', text });
  box.appendChild(t);
  setTimeout(() => t.classList.add('out'), 4500);
  setTimeout(() => t.remove(), 5000);
};

// a confirm dialog with the consequences listed; resolves true or false
S.confirm = function confirm(opts) {
  return new Promise((resolve) => {
    const dlg = document.getElementById('dialog');
    const ok = S.button(opts.ok || 'Go ahead', () => { dlg.close(); resolve(true); }, opts.danger ? 'danger' : 'primary');
    const check = opts.check ? S.el('input', { type: 'checkbox', id: 'dlg-check' }) : null;
    if (check) { ok.disabled = true; check.addEventListener('change', () => { ok.disabled = !check.checked; }); }
    S.fill(dlg,
      S.el('h2', { text: opts.title }),
      S.el('ul', { class: 'consequences' }, (opts.points || []).map((p) => S.el('li', { text: p }))),
      check ? S.el('label', { class: 'check' }, check, ' ', opts.check) : null,
      S.el('div', { class: 'dlg-buttons' }, S.button('Cancel', () => { dlg.close(); resolve(false); }), ok));
    dlg.addEventListener('cancel', () => resolve(false), { once: true });
    dlg.showModal();
  });
};
