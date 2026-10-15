/* app.js: the Studio page. Talks only to studio.py (/api/...), draws with charts.js.
 * Every piece of server text goes through textContent / createElement (el() below); there is no markup parsing.
 *
 * Sections: helpers · state and polling · chrome (header, banner, strip) · Live · Reports · Compare · Tune ·
 * Settings · router · start.
 */
(function () {
  'use strict';

  const SC = window.StudioCharts;
  const VIEWS = ['live', 'reports', 'compare', 'tune', 'settings'];
  const STAGE_COUNT = 6;

  // ================================================================== helpers
  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (k === 'style') throw new Error('inline styles are not allowed');
      else node.setAttribute(k, v === true ? '' : String(v));
    }
    append(node, kids);
    return node;
  }

  function append(node, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      node.append(k instanceof Node ? k : String(k));
    }
  }

  function clear(node) { node.replaceChildren(); }
  const $ = (id) => document.getElementById(id);

  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  function num(v, d) {
    if (!isNum(v)) return '—';
    const dd = d == null ? 0 : d;
    return v.toLocaleString('en-US', { minimumFractionDigits: dd, maximumFractionDigits: dd });
  }

  // 2 decimals below 100, 1 above; trailing zeros dropped ("75.7", "1,118.1", "0.55").
  function smart(v) {
    if (!isNum(v)) return '—';
    const d = Math.abs(v) >= 100 ? 1 : 2;
    return Number(v.toFixed(d)).toLocaleString('en-US', { maximumFractionDigits: d });
  }

  function pct(frac, d) { return isNum(frac) ? `${(frac * 100).toFixed(d == null ? 1 : d)}%` : '—'; }
  function one(v) { return isNum(v) ? v.toFixed(1) : '—'; }
  function signed(v, d) {
    if (!isNum(v)) return '—';
    const s = Math.abs(v).toFixed(d == null ? 1 : d);
    return `${v < 0 ? '−' : '+'}${s}`;
  }

  function clock(ts) {
    if (!isNum(ts)) return '—';
    const d = new Date(ts * 1000);
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
  }

  function mmss(sec) {
    if (!isNum(sec)) return '—';
    const s = Math.max(0, Math.round(sec));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  function duration(sec) {
    if (!isNum(sec)) return null;
    const s = Math.round(sec);
    if (s < 90) return `${s} s`;
    return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  }

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function tuneStamp(id) {
    const m = /^tune-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(String(id || ''));
    if (!m) return String(id || '');
    return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] || ''} ${m[4]}:${m[5]}`;
  }

  function dateTime(ts) {
    if (!isNum(ts)) return '—';
    const d = new Date(ts * 1000);
    return `${d.getDate()} ${MONTHS[d.getMonth()]} ${clock(ts).slice(0, 5)}`;
  }

  function text(v, fallback) { return v == null || v === '' ? (fallback == null ? '—' : fallback) : String(v); }

  function asLine(v) {
    if (v == null) return '';
    if (typeof v === 'string') return v;
    if (v && typeof v === 'object' && !Array.isArray(v)) return Object.values(v).map(asLine).join(': ');
    if (Array.isArray(v)) return v.map(asLine).join(', ');
    try { return JSON.stringify(v); } catch (e) { return String(v); }
  }

  // Cells: string | number | Node | {text, class, num, title}. Rows: array | {group} | {cells, class}.
  function table(head, rows, opts) {
    const o = opts || {};
    const numCols = new Set(o.num || []);
    const trh = el('tr');
    head.forEach((hc, i) => trh.append(el('th', { scope: 'col', class: numCols.has(i) ? 'num' : null, text: hc })));
    const tbody = el('tbody');
    for (const r of rows) {
      if (r && !Array.isArray(r) && r.group != null) {
        tbody.append(el('tr', { class: 'group-row' }, el('th', { colspan: head.length, scope: 'colgroup', text: r.group })));
        continue;
      }
      const cells = Array.isArray(r) ? r : r.cells;
      const tr = el('tr', { class: Array.isArray(r) ? null : r.class });
      cells.forEach((c, i) => {
        const cls = numCols.has(i) ? 'num' : null;
        if (c instanceof Node) tr.append(el('td', { class: cls }, c));
        else if (c && typeof c === 'object') tr.append(el('td', { class: [cls, c.class, c.num ? 'num' : null].filter(Boolean).join(' ') || null, title: c.title }, c.node || String(c.text)));
        else tr.append(el('td', { class: cls, text: c == null ? '—' : String(c) }));
      });
      tbody.append(tr);
    }
    return el('div', { class: 'table-wrap' }, el('table', { class: o.class }, el('thead', null, trh), tbody));
  }

  function toast(message, bad) {
    const t = el('div', { class: bad ? 'toast bad' : 'toast', text: message });
    $('toasts').append(t);
    setTimeout(() => t.remove(), bad ? 8000 : 4500);
  }

  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }

  // A card with a canvas, an optional legend host, a "Table" toggle and a slot for a replacement message.
  function chartCard(title, kind, opts) {
    const o = opts || {};
    const canvas = el('canvas', { role: 'img', 'aria-label': title });
    const box = el('div', { class: `chart-box ${o.size || ''}` }, canvas);
    const tableHost = el('div', { class: 'table-wrap' });
    tableHost.hidden = true;
    const noteHost = el('p', { class: 'chart-note' });
    noteHost.hidden = true;
    const legend = el('div', { class: 'legend' });
    legend.hidden = true;
    const toggle = el('button', { class: 'btn btn-mini', type: 'button', 'aria-pressed': 'false', text: 'Table' });
    const root = el('section', { class: 'card' },
      el('div', { class: 'card-head' }, el('h3', { text: title }), toggle),
      o.caption ? el('p', { class: 'muted small', text: o.caption }) : null,
      legend, box, tableHost, noteHost);
    const card = {
      el: root, canvas, legend, handle: null, showTable: false,
      sync() {
        if (!card.showTable || !card.handle || !card.handle.table) return;
        const t = card.handle.table;
        clear(tableHost);
        tableHost.append(table(t.head, t.rows.map((r) => r.map((c, i) => (i === 0 ? c : (isNum(c) ? smart(c) : '—')))),
          { num: t.head.map((_, i) => i).slice(1) }));
      },
      set(spec, animate) {
        card.handle.setData(spec, animate);
        card.sync();
      },
      note(message) {
        const on = message != null;
        noteHost.textContent = on ? message : '';
        noteHost.hidden = !on;
        box.hidden = on || card.showTable;
        tableHost.hidden = on || !card.showTable;
        toggle.hidden = on;
        legend.hidden = on || legend.childNodes.length === 0;
      },
    };
    toggle.addEventListener('click', () => {
      card.showTable = !card.showTable;
      toggle.setAttribute('aria-pressed', String(card.showTable));
      box.hidden = card.showTable;
      tableHost.hidden = !card.showTable;
      card.sync();
    });
    card.handle = SC ? SC.create(canvas, kind, o) : null;
    return card;
  }

  function noteEditor(kind, id, current) {
    const ta = el('textarea', { rows: 3, maxlength: 2000, 'aria-label': 'Note', placeholder: 'A note, kept in the Studio’s state file' });
    ta.value = current || '';
    const status = el('span', { class: 'muted small' });
    const btn = el('button', {
      class: 'btn', type: 'button', text: 'Save note',
      onclick: async () => {
        btn.disabled = true;
        status.textContent = 'Saving…';
        try {
          await api('/api/notes', { method: 'POST', body: { kind, id, text: ta.value } });
          status.textContent = 'Saved';
        } catch (e) {
          status.textContent = e.message;
        }
        btn.disabled = false;
      },
    });
    return el('div', { class: 'note-editor' }, ta, el('div', { class: 'row' }, btn, status));
  }

  // ================================================================== theme
  const THEMES = ['system', 'light', 'dark'];

  function loadTheme() {
    try {
      const v = localStorage.getItem('studio.theme');
      return THEMES.includes(v) ? v : 'system';
    } catch (e) { return 'system'; }
  }

  function applyTheme(mode) {
    const root = document.documentElement;
    if (mode === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);
    const btn = $('theme-btn');
    btn.textContent = `Theme: ${mode}`;
    btn.setAttribute('aria-label', `Theme: ${mode}. Click to change.`);
    if (SC) SC.repaintAll();
  }

  function initTheme() {
    let mode = loadTheme();
    applyTheme(mode);
    $('theme-btn').addEventListener('click', () => {
      mode = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length];
      try { localStorage.setItem('studio.theme', mode); } catch (e) { /* storage may be blocked */ }
      applyTheme(mode);
    });
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (mode === 'system') SC && SC.repaintAll(); });
    }
  }

  // ================================================================== api
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  const S = {
    state: null, studioDown: false, keyWait: false,
    samples: [], lastSeq: 0, max: 300, liveWindow: 60, showTune: false, liveLoaded: false,
    reports: null, reportsAt: 0, tunes: [], docs: new Map(), docSig: new Map(),
    selected: null, checked: [],
    flow: null, flowAt: 0, prevFlow: null,
    runInfo: null, runInfoOk: true,
    settings: null,
    route: { view: 'live', params: {} },
    compare: { key: '', token: 0 },
  };

  function getKey() {
    try { return sessionStorage.getItem('studio.key') || ''; } catch (e) { return ''; }
  }

  async function api(path, opts) {
    const o = opts || {};
    const headers = { Accept: 'application/json' };
    const key = getKey();
    if (key) headers.Authorization = `Bearer ${key}`;
    if (o.body !== undefined) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(path, {
        method: o.method || 'GET', headers, cache: 'no-store',
        body: o.body !== undefined ? JSON.stringify(o.body) : undefined,
      });
    } catch (e) {
      throw new ApiError(0, 'The Studio does not answer.');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (res.status === 401) askKey(!!key);
    if (!res.ok) {
      const msg = data && data.error && data.error.message ? data.error.message : `Request failed (${res.status}).`;
      throw new ApiError(res.status, msg);
    }
    return data;
  }

  function askKey(wrong) {
    const dlg = $('key-dialog');
    if (dlg.open) return;
    S.keyWait = true;
    $('key-error').hidden = !wrong;
    $('key-error').textContent = wrong ? 'That key was not accepted. Try again.' : '';
    $('key-input').value = '';
    dlg.showModal();
    $('key-input').focus();
  }

  function initKeyDialog() {
    const dlg = $('key-dialog');
    dlg.addEventListener('cancel', (e) => e.preventDefault());   // the key is required: Escape does not close it
    $('key-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = $('key-input').value.trim();
      if (!v) return;
      try { sessionStorage.setItem('studio.key', v); } catch (err) { /* works for this load only */ }
      S.keyWait = false;
      dlg.close();
      $('key-input').value = '';
      pollNow();
    });
  }

  function confirmDialog(title, message, okLabel) {
    return new Promise((resolve) => {
      const dlg = $('confirm-dialog');
      $('confirm-title').textContent = title;
      $('confirm-text').textContent = Array.isArray(message) ? message.join('\n\n') : message;
      $('confirm-ok').textContent = okLabel;
      const done = () => {
        dlg.removeEventListener('close', done);
        resolve(dlg.returnValue === 'ok');
      };
      dlg.returnValue = 'cancel';
      dlg.addEventListener('close', done);
      dlg.showModal();
    });
  }

  // ================================================================== state and polling
  function strata() { return (S.state && S.state.strata) || {}; }
  function runState() { return (strata().run || {}).state || 'idle'; }
  function tuneInfo() { return strata().tune || {}; }
  function flowRunning() {
    return (S.state && S.state.flow && S.state.flow.state === 'running') || (S.flow && S.flow.state === 'running');
  }

  // Why the Run and Tune buttons are disabled, or null when they may be used.
  function busyReason() {
    const st = strata();
    if (!S.state) return 'Waiting for the Studio.';
    if (S.studioDown) return 'The Studio does not answer.';
    if (st.status === 'unreachable') return 'Strata does not answer.';
    if (st.status === 'key_needed') return 'Strata needs an API key.';
    if (st.status === 'profile_off') return 'Profiling is off in Strata.';
    if (st.status !== 'ok') return 'Strata is not ready.';
    if (flowRunning()) return 'A tune flow is running.';
    if (tuneInfo().state === 'running') return 'Strata is tuning.';
    if (runState() === 'running') return 'A profile run is going.';
    return null;
  }

  async function pollState() {
    try {
      const st = await api('/api/state');
      S.studioDown = false;
      const prevRun = S.state && S.state.strata && S.state.strata.run ? S.state.strata.run.state : null;
      const prevFlow = S.state && S.state.flow ? S.state.flow.state : null;
      S.state = st;
      if (prevRun === 'running' && runState() !== 'running') pollReports(true);
      if (prevFlow !== (st.flow && st.flow.state)) pollFlow(true);
      renderChrome();
      renderCurrent('state');
    } catch (e) {
      if (e.status === 0) {
        S.studioDown = true;
        renderChrome();
      }
    }
  }

  async function pollLive() {
    try {
      const data = await api(`/api/live?since=${S.lastSeq}`);
      if (isNum(data.last_seq) && data.last_seq < S.lastSeq) {      // the Studio restarted: start again
        S.samples = [];
        S.lastSeq = 0;
      }
      S.max = data.max || S.max;
      const bySeq = new Map(S.samples.map((s) => [s.seq, s]));
      for (const s of data.samples || []) bySeq.set(s.seq, s);
      S.samples = Array.from(bySeq.values()).sort((a, b) => a.seq - b.seq).slice(-S.max);
      if (isNum(data.last_seq)) S.lastSeq = Math.max(S.lastSeq, data.last_seq);
      S.liveLoaded = true;
      renderCurrent('live');
    } catch (e) { /* the pill already says what is wrong */ }
  }

  async function pollFlow(force) {
    try {
      const f = await api('/api/tune-flow');
      const prev = S.flow ? S.flow.state : null;
      S.flow = f;
      S.flowAt = Date.now();
      if (prev === 'running' && f.state !== 'running') {
        pollReports(true);
        pollTunes();
        if (f.state === 'succeeded') {
          toast('Tuning finished. Opening the comparison.');
          if (f.compare && f.compare.a && f.compare.b) location.hash = `#compare?a=${encodeURIComponent(f.compare.a)}&b=${encodeURIComponent(f.compare.b)}`;
        } else if (f.state === 'failed') {
          toast(`Tuning failed: ${text(f.error, 'see the Tune view')}`, true);
        } else if (f.state === 'cancelled') {
          toast('Tuning was cancelled.');
        }
      }
      renderChrome();
      renderCurrent('flow');
    } catch (e) { /* ignore */ }
  }

  function reportsSignature(list) {
    return (list || []).map((r) => `${r.id}:${(r.order || []).join(',')}:${r.ok}:${r.note ? r.note.length : 0}`).join('|');
  }

  async function pollReports(force) {
    try {
      const data = await api('/api/reports');
      const before = reportsSignature(S.reports && S.reports.reports);
      S.reports = data;
      S.reportsAt = Date.now();
      // A report whose file changed is read again.
      for (const r of data.reports || []) {
        const sig = (r.order || []).join(',');
        if (S.docSig.get(r.id) !== sig) {
          S.docs.delete(r.id);
          S.docSig.set(r.id, sig);
        }
      }
      if (force || before !== reportsSignature(data.reports)) renderCurrent('reports');
    } catch (e) { /* ignore */ }
  }

  async function pollTunes() {
    try {
      const data = await api('/api/tunes');
      S.tunes = data.tunes || [];
      renderCurrent('tunes');
    } catch (e) { /* ignore */ }
  }

  async function pollRun() {
    if (!S.runInfoOk) return;
    try {
      S.runInfo = await api('/api/run-profile');
      renderRunPanel();
    } catch (e) {
      if (e.status === 404 || e.status === 405) S.runInfoOk = false;   // studio.py has no output route: state only
    }
  }

  async function pollSettings() {
    try {
      S.settings = await api('/api/settings');
      renderCurrent('settings');
    } catch (e) { /* ignore */ }
  }

  // Self-rescheduling loops; they wait while the key dialog is open and slow down in a hidden tab.
  function loop(fn, ms) {
    let timer = null;
    const run = async () => {
      timer = null;
      if (!S.keyWait) {
        try { await fn(); } catch (e) { /* a poll never stops the loop */ }
      }
      const slow = document.hidden ? 5 : 1;
      timer = setTimeout(run, ms() * slow);
    };
    run();
    return () => { if (timer) { clearTimeout(timer); run(); } };
  }

  let kicks = [];
  function pollNow() { kicks.forEach((k) => k()); }

  function startPolling() {
    let tick = 0;
    kicks = [
      loop(pollState, () => 2000),
      loop(pollLive, () => (S.route.view === 'live' ? 1000 : 5000)),
      loop(async () => {
        tick += 1;
        const due = flowRunning() ? true : (S.route.view === 'tune' ? tick % 5 === 0 : (tick % 15 === 0 || !S.flow));
        if (due) await pollFlow();
      }, () => 1000),
      loop(async () => {
        const v = S.route.view;
        if (v === 'reports' || v === 'compare' || v === 'tune' || !S.reports) await pollReports();
      }, () => 10000),
      loop(async () => {
        if (S.route.view === 'tune' || S.route.view === 'reports' || !S.tunes.length) await pollTunes();
      }, () => 10000),
      loop(async () => {
        if (runState() === 'running' || (S.route.view === 'reports' && S.runInfo && S.runInfo.state === 'running')) await pollRun();
      }, () => 2000),
      loop(async () => { if (S.route.view === 'settings') await pollSettings(); }, () => 10000),
    ];
  }

  // ================================================================== chrome
  function renderChrome() {
    const st = strata();
    const pill = $('pill');
    let label = 'Connecting…';
    let cls = 'pill-idle';
    if (S.studioDown) {
      label = 'Studio not answering';
      cls = 'pill-bad';
    } else if (S.state) {
      switch (st.status) {
        case 'ok':
          if (st.loaded) { label = `Connected · ${text(st.model, 'model')} · loaded`; cls = 'pill-ok'; }
          else { label = 'Unloaded'; cls = 'pill-warn'; }
          break;
        case 'unreachable':
          label = S.route.view === 'live' && S.liveLoaded && S.samples.length ? 'Disconnected · retrying' : 'Strata unreachable';
          cls = 'pill-bad';
          break;
        case 'key_needed': label = 'Key needed'; cls = 'pill-warn'; break;
        case 'profile_off': label = 'Profiling off'; cls = 'pill-warn'; break;
        default: label = text(st.status, 'Unknown'); cls = 'pill-idle';
      }
    }
    pill.className = `pill ${cls}`;
    $('pill-text').textContent = label;

    // banner: the studio's own sentence for the current degraded state
    const banner = $('banner');
    const msg = S.studioDown ? 'The Studio does not answer; retrying.' : (st.message || null);
    banner.hidden = !msg;
    if (msg && banner.textContent !== msg) banner.textContent = msg;

    // flow strip
    const strip = $('flow-strip');
    if (flowRunning()) {
      const f = S.flow || {};
      const sf = (S.state && S.state.flow) || {};
      const stages = f.stages || [];
      const idx = Math.max(0, stages.findIndex((s) => s.key === (f.stage || sf.stage)));
      const cur = stages[idx];
      const elapsed = isNum(f.elapsed_s) ? f.elapsed_s + (Date.now() - S.flowAt) / 1000 : null;
      strip.hidden = false;
      strip.textContent = `Tuning · stage ${idx + 1}/${stages.length || STAGE_COUNT} · ${cur ? cur.label : text(sf.stage)} · ${mmss(elapsed)}`;
    } else {
      strip.hidden = true;
    }
    const live = $('view-live');
    if (live) live.classList.toggle('dim', st.status === 'unreachable' || S.studioDown);
  }

  // One-second tick: running times in the strip and in the stage table.
  setInterval(() => {
    if (flowRunning()) {
      renderChrome();
      if (S.route.view === 'tune') renderStages();
    }
  }, 1000);

  function renderCurrent(what) {
    const v = S.route.view;
    if (v === 'live' && (what === 'live' || what === 'state')) renderLive();
    else if (v === 'reports' && ['reports', 'state', 'tunes'].includes(what)) renderReports();
    else if (v === 'compare' && ['reports', 'tunes'].includes(what)) renderCompare();
    else if (v === 'tune' && ['state', 'flow', 'reports', 'tunes'].includes(what)) renderTune();
    else if (v === 'settings' && ['settings', 'state'].includes(what)) renderSettings();
    if (v === 'reports' && what === 'flow') renderRunPanel();
  }

  // ================================================================== Live
  const L = {};

  function tag(s) {
    switch (s.tag) {
      case 'profile': return 'profile run';
      case 'profile-before': return 'profile (before)';
      case 'profile-after': return 'profile (after)';
      case 'tune': return 'calibration';
      case 'reload': return 'reload';
      default: return '';
    }
  }

  function buildLive() {
    const root = $('view-live');
    L.tiles = {};
    const tileDefs = [
      ['decode', 'Last decode', 'tok/s'], ['hit', 'Expert cache hit rate', ''],
      ['prompt', 'Last prompt read', 'tok/s'], ['window', 'ms per window', 'ms'],
    ];
    const tiles = el('div', { class: 'tiles' });
    for (const [key, label, unit] of tileDefs) {
      const value = el('span', { class: 'tile-value', text: '—' });
      const sub = el('div', { class: 'tile-sub' });
      const unitEl = el('span', { class: 'tile-unit', text: unit });
      L.tiles[key] = { value, sub, unitEl };
      tiles.append(el('div', { class: 'tile' }, el('div', { class: 'tile-label', text: label }), el('div', null, value, unit ? unitEl : null), sub));
    }

    const winBtns = [30, 60, 300].map((n) => el('button', {
      type: 'button', 'aria-pressed': String(n === S.liveWindow), text: `Last ${n}`,
      onclick: () => {
        S.liveWindow = n;
        winBtns.forEach((b, i) => b.setAttribute('aria-pressed', String([30, 60, 300][i] === n)));
        renderLive();
      },
    }));
    const tuneBox = el('input', { type: 'checkbox', id: 'live-tune' });
    tuneBox.addEventListener('change', () => { S.showTune = tuneBox.checked; renderLive(); });
    const controls = el('div', { class: 'row' },
      el('div', { class: 'segmented', role: 'group', 'aria-label': 'Samples shown' }, winBtns),
      el('label', { class: 'check', for: 'live-tune' }, tuneBox, 'Show the calibration’s requests'));

    L.empty = el('p', { class: 'empty', text: 'No request with its breakdown yet. Send one to Strata (chat, API or a profile run); it appears here within a second.' });
    L.charts = el('div', { class: 'stack' });

    L.l1 = chartCard('Decode speed', 'line', { yTitle: 'tok/s', size: 'short' });
    L.l1.legend.append(...SC.BUCKETS.map((b, i) => el('span', null, el('i', { class: `swatch seq-${i + 1}` }), `context ${b.label}`)));
    L.l1.legend.hidden = false;
    L.l2 = chartCard('Expert cache hit rate', 'line', { yTitle: 'hit rate %', yMax: 100, size: 'short' });
    L.l3 = chartCard('Decode window', 'stack', { yTitle: 'ms per window' });
    L.l4 = chartCard('Prompt read', 'stack', { yTitle: 'share of GPU time', percent: true });

    L.table = el('section', { class: 'card' }, el('div', { class: 'card-head' }, el('h3', { text: 'Last 20 requests' })));
    L.tableHost = el('div');
    L.table.append(L.tableHost);

    L.charts.append(L.l1.el, L.l2.el, el('div', { class: 'grid-2' }, L.l3.el, L.l4.el), L.table);
    root.append(tiles, controls, L.empty, L.charts);
    L.built = true;
  }

  function sampleTitle(s) { return `#${s.seq} · ${clock(s.seen_at)}${s.after_load ? ' · model loaded again' : ''}`; }

  function renderLive() {
    if (!L.built || !SC) return;
    let list = S.samples;
    if (!S.showTune) list = list.filter((s) => s.tag !== 'tune');
    const vis = list.slice(-S.liveWindow);
    const newest = vis[vis.length - 1] || null;
    const profileOff = strata().status === 'profile_off' || strata().profile === false;

    const t = L.tiles;
    t.decode.value.textContent = newest ? one(newest.decode_tok_s) : '—';
    t.hit.value.textContent = newest && isNum(newest.hit_rate) ? (newest.hit_rate * 100).toFixed(1) : (newest && newest.open ? '…' : '—');
    t.hit.unitEl.textContent = newest && isNum(newest.hit_rate) ? '%' : '';
    t.hit.sub.textContent = newest && isNum(newest.pcie_share) && newest.pcie_share > 0 ? `+${(newest.pcie_share * 100).toFixed(1)}% read over PCIe` : '';
    t.prompt.value.textContent = newest ? one(newest.prompt_tok_s) : '—';
    t.prompt.sub.textContent = newest ? `${num(newest.read)} read, ${num(newest.reused)} reused` : '';
    t.window.value.textContent = newest ? one(newest.ms_per_window) : '—';
    t.window.sub.textContent = newest && isNum(newest.tokens_per_window) ? `${newest.tokens_per_window.toFixed(2)} tokens per window` : '';

    setHidden(L.empty, vis.length > 0);
    setHidden(L.charts, vis.length === 0);
    if (vis.length === 0) return;

    const labels = vis.map((s) => `${s.after_load ? '↻' : ''}#${s.seq}`);
    const ctxLine = (s) => `context ${num(s.prompt_tokens)} · ${num(s.generated)} generated`;
    const tagLine = (s) => (tag(s) ? [tag(s)] : []);

    L.l1.set({
      seriesLabel: 'decode tok/s', labels, values: vis.map((s) => (isNum(s.decode_tok_s) ? s.decode_tok_s : null)),
      tokens: vis.map((s) => SC.bucketToken(s.prompt_tokens)),
      tips: vis.map((s) => [sampleTitle(s), ctxLine(s), `${one(s.decode_tok_s)} tok/s · hit ${pct(s.hit_rate)}`, ...tagLine(s)]),
    }, true);
    L.l2.set({
      seriesLabel: 'hit rate %', labels, values: vis.map((s) => (isNum(s.hit_rate) ? s.hit_rate * 100 : null)),
      tips: vis.map((s) => [sampleTitle(s), `hit ${pct(s.hit_rate)}`, isNum(s.pcie_share) ? `${pct(s.pcie_share)} read over PCIe` : null].filter(Boolean).concat(tagLine(s))),
    }, true);

    if (profileOff) {
      const msg = 'The window split and prompt phases need profiling on in Strata (see the banner).';
      L.l3.note(msg);
      L.l4.note(msg);
    } else {
      L.l3.note(null);
      L.l4.note(null);
      L.l3.set({
        labels, groups: SC.DECODE_GROUPS, rows: vis.map((s) => s.window_ms || null),
        tips: vis.map((s) => {
          if (!s.window_ms) return [sampleTitle(s), 'no decode timing line'];
          const lines = SC.DECODE_GROUPS.map((g) => `${g}: ${one(s.window_ms[g])} ms`);
          return [sampleTitle(s), ...lines, `total ${one(s.ms_per_window)} ms per window · ${isNum(s.tokens_per_window) ? s.tokens_per_window.toFixed(2) : '—'} tokens per window`];
        }),
      }, true);
      L.l4.set({
        labels, groups: SC.PROMPT_GROUPS, rows: vis.map((s) => s.prompt_groups_ms || null),
        tips: vis.map((s) => {
          if (!s.prompt_groups_ms) {
            let why = 'no prefill timing line';
            if (s.read === 0) why = 'whole prompt reused';
            else if (s.source === 'history') why = 'no phase split (from /profile/history)';
            return [sampleTitle(s), why];
          }
          const lines = SC.PROMPT_GROUPS.map((g) => `${g}: ${one(s.prompt_groups_ms[g])} ms`);
          return [sampleTitle(s), ...lines, `GPU time ${one(s.prompt_gpu_ms)} ms · ${num(s.read)} tokens read`];
        }),
      }, true);
    }

    const rows = list.slice(-20).reverse().map((s) => [
      clock(s.seen_at), { text: num(s.prompt_tokens), num: true }, { text: num(s.read), num: true }, { text: num(s.reused), num: true },
      { text: one(s.prompt_tok_s), num: true }, { text: one(s.decode_tok_s), num: true },
      s.open && !isNum(s.hit_rate) ? '…' : pct(s.hit_rate), { text: one(s.ms_per_window), num: true },
      tag(s) || '—', text(s.source),
    ]);
    clear(L.tableHost);
    L.tableHost.append(table(['Time', 'Context', 'Read', 'Reused', 'Prompt tok/s', 'Decode tok/s', 'Hit', 'ms/window', 'Tag', 'Source'], rows, { num: [1, 2, 3, 4, 5, 7] }));
  }

  // ================================================================== Reports
  const R = {};

  function buildReports() {
    const root = $('view-reports');
    R.runBtn = el('button', { class: 'btn btn-primary', type: 'button', text: 'Run a profile', onclick: startRun });
    R.runCaption = el('p', { class: 'muted small' });
    R.runState = el('p', { class: 'small' });
    R.runLog = el('pre', { class: 'log short mono' });
    R.runLog.hidden = true;
    R.compareBtn = el('button', { class: 'btn', type: 'button', text: 'Compare these two', disabled: true, onclick: () => {
      const order = (R.reports || []).map((r) => r.id);
      const picked = S.checked.slice().sort((a, b) => order.indexOf(b) - order.indexOf(a));   // older first
      location.hash = `#compare?a=${encodeURIComponent(picked[0])}&b=${encodeURIComponent(picked[1])}`;
    } });
    R.list = el('div', { class: 'report-list' });
    R.empty = el('p', { class: 'empty' });
    R.detail = el('div', { class: 'stack' });

    R.head = el('section', { class: 'card' });
    R.r1 = chartCard('Prompt read, by phase', 'stack', { percent: true, yTitle: 'share of GPU time', tickLines: (l) => l.split(' · ') });
    R.r2 = chartCard('Decode window, by kind of request', 'stack', { yTitle: 'ms per window', tickLines: (l) => l.split(' · ') });
    R.r3a = chartCard('Agent session: hit rate per turn', 'line', { yTitle: 'hit rate %', yMax: 100, size: 'short', tickLines: (l) => l.split(' · ') });
    R.r3b = chartCard('Agent session: decode speed per turn', 'group', { yTitle: 'tok/s', size: 'short', tickLines: (l) => l.split(' · ') });
    R.findings = el('section', { class: 'card' });
    R.detail.append(R.head, R.r1.el, R.r2.el, R.r3a.el, R.r3b.el, R.findings);
    R.detailEmpty = el('p', { class: 'empty', text: 'Select a report to see its charts.' });

    root.append(el('div', { class: 'split' },
      el('div', { class: 'stack' },
        el('section', { class: 'card' },
          el('h2', { text: 'Profile reports' }),
          el('div', { class: 'row' }, R.runBtn, R.compareBtn),
          R.runCaption, R.runState, R.runLog),
        R.empty, R.list),
      el('div', null, R.detailEmpty, R.detail)));
    R.built = true;
  }

  async function startRun() {
    R.runBtn.disabled = true;
    try {
      await api('/api/run-profile', { method: 'POST', body: { long: null } });
      toast('Profile run started.');
      pollState();
      pollRun();
    } catch (e) {
      toast(e.message, true);
    }
    renderRunPanel();
  }

  function renderRunPanel() {
    if (!R.built) return;
    const reason = busyReason();
    R.runBtn.disabled = !!reason;
    const est = S.state && S.state.estimates && S.state.estimates.profile_s;
    R.runCaption.textContent = reason || (isNum(est) ? `about ${Math.round(est)} s here` : 'about a minute on a fast card');
    const run = strata().run || {};
    const info = S.runInfo || {};
    const state = run.state || info.state || 'idle';
    const lines = info.lines || [];
    if (state === 'running') R.runState.textContent = `Running · ${mmss(run.elapsed_s != null ? run.elapsed_s : info.elapsed_s)}`;
    else if (state === 'done') R.runState.textContent = 'Last run: done.';
    else if (state === 'failed') R.runState.textContent = 'Last run: failed. Its output says why.';
    else R.runState.textContent = '';
    const show = (state === 'running' || state === 'failed') && lines.length > 0;
    R.runLog.hidden = !show;
    if (show) {
      const stick = R.runLog.scrollTop + R.runLog.clientHeight >= R.runLog.scrollHeight - 6;
      R.runLog.textContent = lines.join('\n');
      if (stick) R.runLog.scrollTop = R.runLog.scrollHeight;
    }
  }

  function chipsFor(r) {
    const s = r.settings_short || {};
    const out = [];
    if (s.pcie_frac != null) out.push(`PCIe ${s.pcie_frac}`);
    if (s.spec_min_p != null) out.push(`floor ${s.spec_min_p}`);
    if (s.pool_workers != null) out.push(`${s.pool_workers} workers`);
    if (s.adapt != null) out.push(`tier: ${s.adapt}`);
    return out;
  }

  function tuneBadge(r) {
    if (!r.tune || !r.tune.id) return null;
    const when = tuneStamp(r.tune.id);
    return r.tune.role === 'after' ? `after the tune of ${when}` : `before the tune of ${when}`;
  }

  function renderReports() {
    if (!R.built) return;
    renderRunPanel();
    const data = S.reports;
    R.reports = (data && data.reports) || [];
    clear(R.list);
    R.empty.hidden = true;
    if (!data) {
      R.empty.hidden = false;
      R.empty.textContent = 'Loading reports…';
    } else if (!data.exists) {
      R.empty.hidden = false;
      R.empty.textContent = `The folder ${data.dir} does not exist. Start the Studio with --results pointing at Strata's bench/results.`;
    } else if (R.reports.length === 0) {
      R.empty.hidden = false;
      R.empty.textContent = `No profile reports in ${data.dir} yet. Run one with the button above (needs profiling on) or with python tools/strata_profile.py in Strata's folder.`;
    }
    const ids = new Set(R.reports.map((r) => r.id));
    S.checked = S.checked.filter((id) => ids.has(id));
    const want = S.route.params.id;
    if (want && ids.has(want)) S.selected = want;
    if (!S.selected || !ids.has(S.selected)) {
      const first = R.reports.find((r) => r.ok);
      S.selected = first ? first.id : null;
    }
    for (const r of R.reports) R.list.append(reportCard(r));
    R.compareBtn.disabled = S.checked.length !== 2;
    loadSelectedDetail();
  }

  function reportCard(r) {
    const box = el('input', {
      type: 'checkbox', 'aria-label': `Select ${r.id} for comparison`, disabled: !r.ok,
      onchange: (e) => {
        if (e.target.checked) {
          S.checked.push(r.id);
          if (S.checked.length > 2) S.checked.shift();
        } else {
          S.checked = S.checked.filter((x) => x !== r.id);
        }
        renderReports();
      },
    });
    box.checked = S.checked.includes(r.id);
    const badge = tuneBadge(r);
    const title = el('button', {
      class: 'rcard-title', type: 'button', disabled: !r.ok,
      onclick: () => { S.selected = r.id; renderReports(); },
    }, `${text(r.date)}${(r.order && r.order[3] > 1) ? ` (run ${r.order[3]})` : ''}${r.ok ? ` · ${text(r.gpu)}` : ''}`);
    const body = el('div', { class: 'rcard-body' },
      title,
      r.ok ? el('div', { class: 'small muted', text: `${text(r.model)} · ${text(r.quant)} · context ${num(r.context)} · Strata ${text(r.strata_version)}` })
           : el('div', { class: 'small muted mono', text: r.id }),
      r.ok ? el('div', { class: 'rcard-tps', text: isNum(r.chat_tps) ? `${one(r.chat_tps)} tok/s chat` : 'no chat speed' })
           : el('div', { class: 'error-text small', text: text(r.error, 'results.json could not be read') }),
      r.ok ? el('div', { class: 'chips' }, chipsFor(r).map((c) => el('span', { class: 'chip', text: c }))) : null,
      badge ? el('div', null, el('span', { class: 'badge', text: badge })) : null,
      r.note ? el('div', { class: 'note-snippet', text: r.note.length > 90 ? `${r.note.slice(0, 90)}…` : r.note }) : null);
    return el('div', { class: `rcard${r.ok ? '' : ' bad'}${S.selected === r.id ? ' selected' : ''}` }, el('div', { class: 'pick' }, box), body);
  }

  async function getReport(id) {
    if (S.docs.has(id)) return S.docs.get(id);
    const p = api(`/api/reports/${encodeURIComponent(id)}`);
    S.docs.set(id, p);
    try {
      return await p;
    } catch (e) {
      S.docs.delete(id);
      throw e;
    }
  }

  async function loadSelectedDetail() {
    const id = S.selected;
    setHidden(R.detail, !id);
    setHidden(R.detailEmpty, !!id);
    if (!id) return;
    if (R.shownId === id && R.shownSig === (S.docSig.get(id) || '')) return;
    try {
      const doc = await getReport(id);
      if (S.selected !== id) return;
      R.shownId = id;
      R.shownSig = S.docSig.get(id) || '';
      renderReportDetail(doc);
    } catch (e) {
      clear(R.head);
      R.head.append(el('p', { class: 'error-text', text: e.message }));
    }
  }

  function settingsTable(rows, caption) {
    const body = (rows || []).map((r) => [
      { text: r.flag, class: 'mono' }, text(r.config, 'engine default'), text(r.loaded, '—'),
    ]);
    return el('div', null,
      body.length ? table(['Setting', 'Run config', 'Loaded'], body) : el('p', { class: 'muted small', text: 'This report has no settings record.' }),
      caption ? el('p', { class: 'muted small', text: caption }) : null);
  }

  function renderReportDetail(doc) {
    const m = doc.machine || {};
    const p = doc.params || {};
    clear(R.head);
    const machineLine = [
      Array.isArray(m.gpu) ? m.gpu.join(', ') : m.gpu, isNum(m.gpu_vram_mib) ? `${num(m.gpu_vram_mib / 1024, 1)} GiB` : null,
      m.cpu, isNum(m.ram_gib) ? `${one(m.ram_gib)} GiB RAM` : null,
      m.model ? `${m.model}${m.quant ? ` · ${m.quant}` : ''}` : null,
      isNum(m.context) ? `context ${num(m.context)}` : null,
      m.strata_version ? `Strata ${m.strata_version}` : null,
    ].filter(Boolean).join(' · ');
    const src = doc.settings && doc.settings.source;
    R.head.append(
      el('h2', { text: `Report ${text(doc.date, '')}` }),
      el('p', { class: 'mono small', text: text(doc.path) }),
      el('p', { class: 'small', text: `Took ${isNum(doc.elapsed_s) ? `${Math.round(doc.elapsed_s)} s` : '—'} · long prompt ${p.long != null ? `${num(p.long)} tokens` : '—'} · ${machineLine}` }),
      el('h3', { text: 'Settings in effect' }),
      settingsTable(doc.settings && doc.settings.rows, src === 'captured' ? 'Captured from Strata when the Studio saw the run end.' : null),
      el('h3', { text: 'Note' }),
      noteEditor('report', doc.id, doc.note));

    // R1: prompt phases
    const prompts = doc.prompts || [];
    if (prompts.length === 0) R.r1.note('This report has no prompt reads.');
    else {
      R.r1.note(null);
      R.r1.set({
        labels: prompts.map((x) => `${x.label} · ${num(x.prompt_tps)} tok/s`),
        groups: SC.PROMPT_GROUPS, rows: prompts.map((x) => x.groups_ms || x.groups_share || null),
        tips: prompts.map((x) => [`${x.label} · ${num(x.tokens)} tokens`, ...SC.PROMPT_GROUPS.map((g) => `${g}: ${one((x.groups_ms || {})[g])} ms`), `GPU time ${one(x.gpu_ms)} ms`]),
      });
    }

    // R2: decode windows
    const decode = (doc.decode || []).filter((d) => d.window_ms);
    if (decode.length === 0) R.r2.note('This report has no decode window split.');
    else {
      R.r2.note(null);
      R.r2.set({
        labels: decode.map((d) => `${d.kind} · ${one((d.tps || {}).median)} tok/s · ${isNum(d.hit_rate) ? `${Math.round(d.hit_rate * 100)} % hit` : '— hit'}`),
        groups: SC.DECODE_GROUPS, rows: decode.map((d) => d.window_ms),
        tips: decode.map((d) => [`${d.kind} · context ${num(d.context)}`, ...SC.DECODE_GROUPS.map((g) => `${g}: ${one(d.window_ms[g])} ms`),
          `total ${one(d.ms_per_window)} ms per window · ${isNum(d.tokens_per_window) ? d.tokens_per_window.toFixed(2) : '—'} tokens per window`]),
      });
    }

    // R3a / R3b: the agent session
    const agent = doc.agent || [];
    if (agent.length === 0) {
      R.r3a.note('This report has no agent session.');
      R.r3b.note('This report has no agent session.');
    } else {
      R.r3a.note(null);
      R.r3b.note(null);
      const labels = agent.map((a) => `turn ${a.turn} · ${num(a.prompt_tokens)} ctx`);
      const tips = agent.map((a) => [`turn ${a.turn} · ${num(a.prompt_tokens)} ctx`, `${num(a.read)} read, ${num(a.reused)} reused`,
        `hit ${pct(a.hit_rate)} · decode ${one(a.decode_tps)} tok/s`, `prompt ${one(a.prompt_tps)} tok/s · ${one(a.ms_per_window)} ms per window`]);
      R.r3a.set({ seriesLabel: 'hit rate %', labels, values: agent.map((a) => (isNum(a.hit_rate) ? a.hit_rate * 100 : null)), tips });
      R.r3b.set({ labels, series: [{ name: 'decode tok/s', token: 'series1', values: agent.map((a) => (isNum(a.decode_tps) ? a.decode_tps : null)) }], tips });
    }

    // findings, skipped, failures
    clear(R.findings);
    R.findings.append(el('h3', { text: 'Findings' }));
    const findings = doc.findings || [];
    if (findings.length) R.findings.append(el('ul', { class: 'findings' }, findings.map((f) => el('li', { text: asLine(f) }))));
    else R.findings.append(el('p', { class: 'muted', text: 'No findings in this report.' }));
    const skipped = Object.entries(doc.skipped || {});
    if (skipped.length) {
      R.findings.append(el('h3', { text: 'Skipped' }), el('ul', { class: 'findings' },
        skipped.map(([k, v]) => el('li', { text: `${/^\d+K$/i.test(k) ? `${k} read` : k}: skipped (${asLine(v)})` }))));
    }
    if ((doc.failures || []).length) {
      R.findings.append(el('h3', { text: 'Failures' }), el('ul', { class: 'findings' }, doc.failures.map((f) => el('li', { text: asLine(f) }))));
    }
  }

  // ================================================================== Compare
  const C = {};

  function buildCompare() {
    const root = $('view-compare');
    C.a = el('select', { id: 'cmp-a', 'aria-label': 'A (before)' });
    C.b = el('select', { id: 'cmp-b', 'aria-label': 'B (after)' });
    const go = () => { location.hash = `#compare?a=${encodeURIComponent(C.a.value)}&b=${encodeURIComponent(C.b.value)}`; };
    C.a.addEventListener('change', go);
    C.b.addEventListener('change', go);
    C.swap = el('button', { class: 'btn', type: 'button', text: 'Swap', onclick: () => {
      location.hash = `#compare?a=${encodeURIComponent(C.b.value)}&b=${encodeURIComponent(C.a.value)}`;
    } });
    C.controls = el('section', { class: 'card' }, el('div', { class: 'row' },
      el('label', { class: 'field', for: 'cmp-a' }, 'A (before)', C.a),
      el('label', { class: 'field', for: 'cmp-b' }, 'B (after)', C.b), C.swap));
    C.empty = el('p', { class: 'empty', text: 'Two reports are needed to compare. Run a profile (Reports) or a tune (Tune).' });
    C.error = el('p', { class: 'error-text' });
    C.warn = el('div', { class: 'banner' });
    C.warn.hidden = true;
    C.verdicts = el('section', { class: 'card' });
    C.c1 = chartCard('Decode tok/s by request kind', 'group', { yTitle: 'tok/s' });
    C.c2 = chartCard('Prompt tok/s by size', 'group', { yTitle: 'tok/s' });
    C.c3 = chartCard('Decode window split', 'stack', { yTitle: 'ms per window' });
    C.c4 = chartCard('Expert cache hit rate by request kind', 'group', { yTitle: 'hit rate %', yMax: 100 });
    C.rows = el('section', { class: 'card' });
    C.settings = el('section', { class: 'card' });
    C.body = el('div', { class: 'stack' }, C.warn, C.verdicts,
      el('div', { class: 'grid-2' }, C.c1.el, C.c2.el), el('div', { class: 'grid-2' }, C.c3.el, C.c4.el), C.rows, C.settings);
    root.append(C.controls, C.empty, C.error, C.body);
    C.built = true;
  }

  function okReports() { return ((S.reports && S.reports.reports) || []).filter((r) => r.ok); }

  function reportOption(r) {
    const run = r.order && r.order[3] > 1 ? ` (run ${r.order[3]})` : '';
    const s = r.settings_short || {};
    const pcie = s.pcie_frac != null ? ` · PCIe ${s.pcie_frac}` : '';
    const badge = r.tune && r.tune.role ? ` · ${r.tune.role} tune` : '';
    return `${text(r.date)}${run} · ${isNum(r.chat_tps) ? one(r.chat_tps) : '—'} tok/s${pcie}${badge}`;
  }

  function defaultPair(list) {
    const ids = new Set(list.map((r) => r.id));
    for (const t of S.tunes) {
      if (t.before_report && t.after_report && ids.has(t.before_report) && ids.has(t.after_report)) return [t.before_report, t.after_report];
    }
    return [list[1].id, list[0].id];            // the two newest, older first
  }

  function renderCompare() {
    if (!C.built) return;
    const list = okReports();
    if (list.length < 2) {
      setHidden(C.controls, true);
      setHidden(C.body, true);
      setHidden(C.empty, false);
      C.error.textContent = '';
      return;
    }
    setHidden(C.empty, true);
    setHidden(C.controls, false);
    const ids = new Set(list.map((r) => r.id));
    let a = S.route.params.a;
    let b = S.route.params.b;
    if (!ids.has(a) || !ids.has(b)) [a, b] = defaultPair(list);
    for (const sel of [C.a, C.b]) {
      clear(sel);
      for (const r of list) sel.append(el('option', { value: r.id, text: reportOption(r) }));
    }
    C.a.value = a;
    C.b.value = b;
    const key = `${a}|${b}|${reportsSignature(list)}`;
    if (C.key === key) return;
    C.key = key;
    drawCompare(a, b);
  }

  async function drawCompare(a, b) {
    const token = ++S.compare.token;
    C.error.textContent = '';
    try {
      const [cmp, da, db] = await Promise.all([
        api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`), getReport(a), getReport(b)]);
      if (token !== S.compare.token) return;
      setHidden(C.body, false);
      renderCompareBody(cmp, da, db);
    } catch (e) {
      if (token !== S.compare.token) return;
      setHidden(C.body, true);
      C.error.textContent = e.message;
    }
  }

  const ICON = { better: '▲', worse: '▼', same: '●', missing: '○' };


  function renderCompareBody(cmp, da, db) {
    // warnings
    const warns = cmp.warnings || [];
    const kinds = [];
    if (warns.includes('different GPU')) kinds.push('GPUs');
    if (warns.includes('different model')) kinds.push('models');
    if (warns.includes('different context')) kinds.push('context sizes');
    const info = warns.includes('different Strata version');
    C.warn.hidden = kinds.length === 0 && !info;
    C.warn.textContent = '';
    if (!C.warn.hidden) {
      const parts = [];
      if (kinds.length) parts.push(`These reports come from different ${kinds.join(' / ')}: the deltas compare different things.`);
      if (info) parts.push('They were made by different Strata versions.');
      C.warn.textContent = parts.join(' ');
    }

    // verdicts
    clear(C.verdicts);
    C.verdicts.append(el('h2', { text: 'Verdict' }));
    const vs = cmp.verdicts || [];
    C.verdicts.append(vs.length ? el('ul', { class: 'verdicts' }, vs.map((v) => el('li', { class: `v-${v.state}` },
      el('span', { class: 'vicon', title: v.state, 'aria-label': v.state, text: ICON[v.state] || '●' }),
      el('span', null, el('span', { text: v.text }),
        v.floor_note ? el('span', { class: 'floor-note', text: v.floor_note }) : null)))) :
      el('p', { class: 'muted', text: 'Nothing to compare in these two reports.' }));

    // C1/C2/C3/C4 from the two reports
    const kinds2 = [];
    for (const d of [da, db]) for (const k of d.decode || []) if (!kinds2.includes(k.kind)) kinds2.push(k.kind);
    const find = (doc, kind) => (doc.decode || []).find((k) => k.kind === kind) || null;
    const tps = (d) => (d && d.tps && isNum(d.tps.median) ? d.tps.median : null);
    C.c1.set({
      labels: kinds2,
      series: [{ name: 'A', token: 'series1', values: kinds2.map((k) => tps(find(da, k))) },
               { name: 'B', token: 'series2', values: kinds2.map((k) => tps(find(db, k))) }],
      tips: kinds2.map((k) => [k, `A: ${one(tps(find(da, k)))} tok/s`, `B: ${one(tps(find(db, k)))} tok/s`]),
    });
    const sizes = [];
    for (const d of [da, db]) for (const p of d.prompts || []) if (!sizes.includes(p.label)) sizes.push(p.label);
    const pf = (doc, l) => (doc.prompts || []).find((p) => p.label === l) || null;
    const ptps = (p) => (p && isNum(p.prompt_tps) ? p.prompt_tps : null);
    C.c2.set({
      labels: sizes,
      series: [{ name: 'A', token: 'series1', values: sizes.map((l) => ptps(pf(da, l))) },
               { name: 'B', token: 'series2', values: sizes.map((l) => ptps(pf(db, l))) }],
      tips: sizes.map((l) => [`${l} read`, `A: ${num(ptps(pf(da, l)), 1)} tok/s`, `B: ${num(ptps(pf(db, l)), 1)} tok/s`]),
    });
    const l3 = [];
    const rows3 = [];
    const tips3 = [];
    for (const k of kinds2) {
      for (const [who, doc] of [['A', da], ['B', db]]) {
        const d = find(doc, k);
        l3.push(`${k} ${who}`);
        rows3.push(d && d.window_ms ? d.window_ms : null);
        tips3.push(d && d.window_ms
          ? [`${k} · ${who}`, ...SC.DECODE_GROUPS.map((g) => `${g}: ${one(d.window_ms[g])} ms`), `total ${one(d.ms_per_window)} ms per window`]
          : [`${k} · ${who}`, 'no decode timing line']);
      }
    }
    C.c3.set({ labels: l3, groups: SC.DECODE_GROUPS, rows: rows3, tips: tips3 });
    const hit = (d) => (d && isNum(d.hit_rate) ? d.hit_rate * 100 : null);
    C.c4.set({
      labels: kinds2,
      series: [{ name: 'A', token: 'series1', values: kinds2.map((k) => hit(find(da, k))) },
               { name: 'B', token: 'series2', values: kinds2.map((k) => hit(find(db, k))) }],
      tips: kinds2.map((k) => [k, `A: ${one(hit(find(da, k)))} %`, `B: ${one(hit(find(db, k)))} %`]),
    });

    // the table of every row, grouped
    clear(C.rows);
    C.rows.append(el('h2', { text: 'Every number' }));
    const GROUP_NAME = { decode: 'Decode', prompt: 'Prompt read', agent: 'Agent turn' };
    const order = { decode: 0, prompt: 1, agent: 2 };
    const sorted = (cmp.rows || []).map((r, i) => [r, i]).sort((x, y) => (order[x[0].group] ?? 9) - (order[y[0].group] ?? 9) || x[1] - y[1]).map((x) => x[0]);
    const body = [];
    let lastKey = null;
    for (const r of sorted) {
      const key = `${r.group}|${r.key}`;
      if (key !== lastKey) {
        body.push({ group: `${GROUP_NAME[r.group] || r.group}: ${r.key}` });
        lastKey = key;
      }
      body.push(compareRow(r));
    }
    C.rows.append(body.length ? table(['Metric', 'A', 'B', 'Change', 'Change %'], body, { num: [1, 2, 3, 4] }) : el('p', { class: 'muted', text: 'No rows.' }));

    // settings diff
    clear(C.settings);
    C.settings.append(el('h2', { text: 'Settings, A and B' }));
    const srows = (cmp.settings || []).map((s) => ({
      class: s.changed ? 'changed' : null,
      cells: [{ text: s.flag, class: 'mono' }, text(s.a, 'engine default'), text(s.b, 'engine default'), s.changed ? 'changed' : ''],
    }));
    C.settings.append(srows.length ? table(['Setting', 'A', 'B', ''], srows) : el('p', { class: 'muted', text: 'No settings were recorded.' }));
  }

  function compareRow(r) {
    const frac = r.floor_kind === 'pts';              // rates and shares arrive in percent, their delta in points
    const fmt = (v) => (isNum(v) ? `${smart(v)}${frac ? '%' : ''}` : '—');
    const state = r.state || 'same';
    let delta = '—';
    if (isNum(r.delta)) delta = `${ICON[state] || '●'} ${signed(r.delta, Math.abs(r.delta) >= 10 ? 1 : 2)}${frac ? ' pts' : ''}`;
    else delta = `${ICON.missing} not in both`;
    return [
      text(r.metric),
      { text: fmt(r.a), num: true },
      { text: fmt(r.b), num: true },
      { node: el('span', { class: `delta delta-${state}`, text: delta, title: state }), num: true },
      { text: isNum(r.pct) ? `${signed(r.pct, 1)}%` : '—', num: true },
    ];
  }

  // ================================================================== Tune
  const T = {};
  const STEP_DEFS = [
    ['pcie_frac', 'PCIe share'], ['spec_min_p', 'Draft floor'], ['pool_workers', 'CPU workers'], ['adapt', 'Expert tier'],
  ];

  function buildTune() {
    const root = $('view-tune');
    T.btn = el('button', { class: 'btn btn-primary btn-big', type: 'button', text: 'Tune this PC', onclick: startTune });
    T.caption = el('p', { class: 'muted small' });
    T.explain = el('p');
    T.top = el('section', { class: 'card' }, el('h2', { text: 'Tune this PC' }), T.explain, el('div', { class: 'row' }, T.btn), T.caption);
    T.status = el('p', { class: 'small' });
    T.cancel = el('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: () => flowAction('cancel') });
    T.retry = el('button', { class: 'btn', type: 'button', text: 'Run "after" again', onclick: () => flowAction('retry-after') });
    T.stageHost = el('div');
    T.log = el('pre', { class: 'log mono', tabindex: '0', 'aria-label': 'Output of the current stage' });
    T.stages = el('section', { class: 'card' }, el('h3', { text: 'Stages' }), T.status, T.stageHost,
      el('div', { class: 'row' }, T.cancel, T.retry), el('h3', { text: 'Output' }), T.log);
    T.cards = STEP_DEFS.map(([key, title]) => ({
      key, card: chartCard(title, 'dots', { yTitle: 'tok/s', size: 'short' }),
    }));
    T.failed = el('p', { class: 'muted small' });
    T.t1 = el('section', { class: 'card' }, el('h2', { text: 'Calibration candidates' }),
      el('p', { class: 'muted small', text: 'Grey dots are single rounds, the blue dot is the median. A green ring marks the value the tune kept (from its result line). A dashed ring marks the best of the sweep when it was not kept: the confirm step found no 3% gain, so the engine’s default stayed.' }),
      el('p', { class: 'muted small', text: '“(default)” is the product default; “(was)” is the value the engine ran with before this tune.' }),
      el('div', { class: 'grid-2' }, T.cards.map((c) => c.card.el)), T.failed);
    T.summary = el('section', { class: 'card' });
    T.settings = el('section', { class: 'card' });
    T.tunes = el('section', { class: 'card' });
    root.append(T.top, T.stages, T.t1, T.summary, T.settings, T.tunes);
    T.built = true;
  }

  async function startTune() {
    const est = S.state && S.state.estimates && S.state.estimates.tune_s;
    const ok = await confirmDialog('Tune this PC now?', [
      `This takes a while: ${isNum(est) ? `last time here it took ${duration(est)}; ` : ''}setup’s estimate is 15–30 minutes, longer on a slow card. The PC is busy meanwhile.`,
      'The model is unloaded while Strata’s calibration measures the PCIe share, the draft floor, the CPU workers and the expert tier. Requests get 503 until it ends.',
      'The calibration rewrites the run config. A value an earlier tune set can go back to the default: a new value is kept only when it beats the default by 3% in the confirm step.',
    ], 'Tune this PC');
    if (!ok) return;
    T.btn.disabled = true;
    try {
      await api('/api/tune-flow', { method: 'POST', body: { long: null } });
      toast('The tune flow started.');
      pollState();
      pollFlow(true);
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function flowAction(which) {
    try {
      await api(`/api/tune-flow/${which}`, { method: 'POST', body: {} });
      pollFlow(true);
    } catch (e) {
      toast(e.message, true);
    }
  }

  function stageIcon(s) {
    switch (s.state) {
      case 'running': return el('span', null, el('span', { class: 'spin', 'aria-hidden': 'true' }), ' running');
      case 'done': return el('span', { class: 'st-done', text: '✓ done' });
      case 'failed': return el('span', { class: 'st-failed', text: '✕ failed' });
      case 'skipped': return el('span', { class: 'st-muted', text: '– skipped' });
      default: return el('span', { class: 'st-muted', text: '○ pending' });
    }
  }

  function sameValue(a, b) {
    if (a == null || b == null) return false;
    const x = parseFloat(a);
    const y = parseFloat(b);
    if (isFinite(x) && isFinite(y) && /^[\d.]+$/.test(String(a)) && /^[\d.]+$/.test(String(b))) return x === y;
    return String(a) === String(b);
  }

  // A candidate chart: rings come from the tune's result (`kept`), never from the sweep's winner alone.
  function stepSpec(step) {
    const vals = step.values || [];
    const labels = vals.map((v) => String(v.value));
    const keptIdx = step.kept == null ? -1 : vals.findIndex((v) => sameValue(v.value, step.kept));
    const bestIdx = vals.findIndex((v) => sameValue(v.value, step.best));
    const bestNotKept = keptIdx >= 0 && bestIdx >= 0 && bestIdx !== keptIdx;
    const rings = [];
    if (keptIdx >= 0) rings.push({ index: keptIdx, kind: 'kept' });
    if (bestNotKept) rings.push({ index: bestIdx, kind: 'best' });
    const tagOf = (value) => {
      if (sameValue(value, step.default)) return step.key === 'adapt' ? null : '(default)';
      if (step.was != null && sameValue(value, step.was)) return '(was)';
      return null;
    };
    const tips = vals.map((v, i) => {
      const t = tagOf(v.value);
      const lines = [`${step.label}: ${v.value}${t ? ` ${t}` : ''}`, `median ${one(v.median)} tok/s`];
      if (isNum(v.min) && isNum(v.max)) lines.push(`range ${one(v.min)}–${one(v.max)} tok/s`);
      lines.push(`${(v.rounds || []).length || 1} round${(v.rounds || []).length > 1 ? 's' : ''}`);
      if (i === keptIdx) lines.push('kept');
      else if (bestNotKept && i === bestIdx) lines.push('best of the sweep, not kept: the confirm step found no 3% gain');
      return lines;
    });
    return {
      labels, rounds: vals.map((v) => v.rounds || []), medians: vals.map((v) => (isNum(v.median) ? v.median : null)), rings, tips,
      tickLines(label, i) {
        let shown = label;
        if (step.key === 'adapt' && label === 'default') shown = 'engine default';
        else {
          const t = tagOf(label);
          if (t) shown = `${label} ${t}`;
        }
        return bestNotKept && i === bestIdx ? [shown, 'best'] : [shown];
      },
    };
  }

  function renderStages() {
    if (!T.built) return;
    const f = S.flow || { state: 'idle' };
    const delta = (Date.now() - S.flowAt) / 1000;
    const running = f.state === 'running';
    const stages = f.stages || [];
    clear(T.stageHost);
    if (stages.length === 0) {
      T.stageHost.append(el('p', { class: 'muted', text: 'No tune has been started from here yet.' }));
    } else {
      const rows = stages.map((s, i) => {
        const el_s = s.state === 'running' && isNum(s.elapsed_s) ? s.elapsed_s + delta : s.elapsed_s;
        return [`${i + 1}. ${s.label}`, stageIcon(s), { text: s.state === 'pending' ? '—' : mmss(el_s), num: true },
          { text: isNum(s.timeout_s) ? (s.timeout_s >= 120 ? `${Math.round(s.timeout_s / 60)} min` : `${s.timeout_s} s`) : '—', num: true },
          text(s.message, '')];
      });
      T.stageHost.append(table(['Stage', 'State', 'Time', 'Timeout', 'Message'], rows, { num: [2, 3] }));
    }
    const elsewhere = tuneInfo().state === 'running' && !tuneInfo().started_by_studio;
    let status = '';
    if (running) status = `Tuning · ${mmss(isNum(f.elapsed_s) ? f.elapsed_s + delta : null)}`;
    else if (elsewhere) status = `Strata is tuning (started elsewhere) · ${mmss(tuneInfo().elapsed_s)}`;
    else if (f.state === 'succeeded') status = 'The last tune succeeded.';
    else if (f.state === 'failed') status = `The last tune failed: ${text(f.error, 'see the stage table')}`;
    else if (f.state === 'cancelled') status = 'The last tune was cancelled.';
    T.status.textContent = status;
    setHidden(T.cancel, !f.can_cancel);
    setHidden(T.retry, !f.can_retry_after);
  }

  function renderTune() {
    if (!T.built) return;
    const f = S.flow || { state: 'idle' };
    const est = S.state && S.state.estimates && S.state.estimates.tune_s;
    T.explain.textContent = 'Runs a profile, then Strata’s calibration (setup’s --calibrate; the model is unloaded and requests get 503 meanwhile), '
      + 'loads the model again with one short request, runs a second profile and opens the comparison. '
      + `Last time here: ${isNum(est) ? duration(est) : 'not run from here yet'}; setup’s estimate is 15–30 minutes.`;
    let reason = busyReason();
    if (!reason && !tuneInfo().available) reason = text(tuneInfo().unavailable_reason, 'Tuning is not available here.');
    T.btn.disabled = !!reason;
    T.caption.textContent = reason || 'Ready.';
    renderStages();

    // output: autoscroll unless the user scrolled up
    const lines = f.lines || [];
    const stick = T.log.scrollTop + T.log.clientHeight >= T.log.scrollHeight - 6;
    const joined = lines.join('\n');
    if (T.log.textContent !== joined) {
      T.log.textContent = joined || 'No output yet.';
      if (stick) T.log.scrollTop = T.log.scrollHeight;
    }

    // T1
    const steps = (f.candidates && f.candidates.steps) || [];
    for (const c of T.cards) {
      const step = steps.find((s) => s.key === c.key);
      if (!step || !(step.values || []).length) {
        c.card.note('Waiting for this step’s numbers.');
        continue;
      }
      c.card.note(null);
      const spec = stepSpec(step);
      c.card.handle.tickLines = spec.tickLines;
      c.card.set(spec);
    }
    const failed = (f.candidates && f.candidates.failed_starts) || [];
    T.failed.textContent = failed.length ? `The engine did not start for: ${failed.map(asLine).join('; ')}. Those candidates were dropped.` : '';

    renderTuneSummary(f);
    renderTunes();
  }

  async function renderTuneSummary(f) {
    clear(T.summary);
    clear(T.settings);
    const done = f.state === 'succeeded' || (f.summary && f.state !== 'running');
    setHidden(T.summary, !done);
    setHidden(T.settings, !done || !(f.settings && (f.settings.rows || []).length));
    if (!done) return;
    const tuneStage = (f.stages || []).find((s) => s.key === 'tune');
    T.summary.append(el('h2', { text: 'Result' }),
      el('div', { class: 'row' }, isNum(f.tok_s) ? el('div', null, el('div', { class: 'big-number', text: `${one(f.tok_s)} tok/s` }), el('div', { class: 'muted small', text: 'speed measured by the calibration' })) : null,
        el('div', null, el('p', { text: text(f.summary, 'No summary was printed.') }),
          tuneStage && isNum(tuneStage.elapsed_s) ? el('p', { class: 'muted small', text: `The tune took ${duration(tuneStage.elapsed_s)}.` }) : null)));
    const reload = f.reload && f.reload.sample;
    const after = reload && reload.window_ms ? reload.window_ms['adapt wait'] : null;
    const line = el('p', { class: 'small' });
    if (isNum(after)) {
      line.textContent = `First request after the tune: adapt wait ${one(after)} ms per window.`;
      T.summary.append(line);
      if (f.before_report) {
        try {
          const doc = await getReport(f.before_report);
          const chat = (doc.decode || []).find((d) => d.kind === 'chat');
          const before = chat && chat.window_ms ? chat.window_ms['adapt wait'] : null;
          if (isNum(before)) line.textContent = `First request after the tune: adapt wait ${one(after)} ms per window (before profile’s chat: ${one(before)} ms).`;
        } catch (e) { /* the line stays without the comparison */ }
      }
    }
    if (f.compare && f.compare.a && f.compare.b) {
      T.summary.append(el('p', null, el('a', { href: `#compare?a=${encodeURIComponent(f.compare.a)}&b=${encodeURIComponent(f.compare.b)}`, text: 'Open the before/after comparison' })));
    }
    const rows = ((f.settings && f.settings.rows) || []).map((r) => ({
      class: r.changed ? 'changed' : null,
      cells: [{ text: r.flag, class: 'mono' }, text(r.before, 'engine default'), text(r.after, 'engine default'), r.changed ? '✓ changed' : ''],
    }));
    if (rows.length) {
      T.settings.append(el('h2', { text: 'Settings before and after' }), table(['Setting', 'Before', 'After', 'Changed'], rows),
        el('p', { class: 'muted small', text: `Source: ${text(f.settings.source)}` }));
    }
  }

  function renderTunes() {
    clear(T.tunes);
    T.tunes.append(el('h2', { text: 'Past tunes' }));
    if (S.tunes.length === 0) {
      T.tunes.append(el('p', { class: 'muted', text: 'No tunes recorded by this Studio yet.' }));
      return;
    }
    T.tunes.append(el('ul', { class: 'tunes' }, S.tunes.map((t) => el('li', null,
      el('div', { class: 'row' },
        el('strong', { text: dateTime(t.started_at) }),
        el('span', { class: `badge ${t.outcome === 'succeeded' ? 'badge-good' : 'badge-soft'}`, text: text(t.outcome) }),
        isNum(t.tok_s) ? el('span', { text: `${one(t.tok_s)} tok/s` }) : null,
        isNum(t.tune_s) ? el('span', { class: 'muted small', text: `tune took ${duration(t.tune_s)}` }) : null),
      el('div', { text: text(t.summary || t.error, 'No summary.') }),
      el('div', { class: 'row small' },
        t.before_report && t.after_report ? el('a', { href: `#compare?a=${encodeURIComponent(t.before_report)}&b=${encodeURIComponent(t.after_report)}`, text: 'Compare' }) : null,
        t.after_report ? el('a', { href: `#reports?id=${encodeURIComponent(t.after_report)}`, text: 'Reports' }) : null),
      noteEditor('tune', t.id, t.note)))));
  }

  // ================================================================== Settings
  const ST = {};

  function buildSettings() {
    const root = $('view-settings');
    ST.host = el('section', { class: 'card' });
    root.append(ST.host);
    ST.built = true;
  }

  function renderSettings() {
    if (!ST.built) return;
    clear(ST.host);
    const s = S.settings;
    ST.host.append(el('h2', { text: 'Speed settings' }));
    if (!s || !(s.rows || []).length) {
      ST.host.append(el('p', { class: 'empty', text: s ? 'No settings to show: Strata is unreachable and no --config was given.' : 'Loading settings…' }));
      if (s && s.note) ST.host.append(el('p', { class: 'muted small', text: s.note }));
      return;
    }
    const rows = s.rows.map((r) => {
      const pending = r.next_load != null && r.now != null && String(r.next_load) !== String(r.now);
      return {
        class: pending ? 'pending' : null,
        cells: [{ text: r.name, class: 'mono' }, text(r.now, '—'),
          pending ? `${r.next_load}: the model loads with the new value on the next request` : text(r.next_load, '—'),
          text(r.run_config, '—'), r.calibrated ? el('span', { class: 'badge badge-soft', text: 'calibrated' }) : '', text(r.about, '')],
      };
    });
    ST.host.append(table(['Setting', 'Now', 'Next load', 'Run config', '', 'What it does'], rows),
      el('p', { class: 'muted small', text: `${s.note ? `${s.note} ` : ''}Source: ${text(s.source)}${s.file ? ` · ${s.file}` : ''}.` }));
  }

  // ================================================================== router
  function parseHash() {
    const raw = location.hash.replace(/^#/, '');
    const [name, query] = raw.split('?');
    const view = VIEWS.includes(name) ? name : 'live';
    const params = {};
    for (const [k, v] of new URLSearchParams(query || '')) params[k] = v;
    return { view, params };
  }

  function route() {
    S.route = parseHash();
    const view = S.route.view;
    for (const v of VIEWS) {
      const sec = $(`view-${v}`);
      if (sec) sec.hidden = v !== view;
    }
    document.querySelectorAll('.tab').forEach((a) => {
      if (a.dataset.view === view) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    if (view === 'reports') R.shownId = null;
    renderChrome();
    renderCurrent('state');
    renderCurrent('reports');
    renderCurrent('tunes');
    renderCurrent('flow');
    renderCurrent('live');
    if (view === 'reports' || view === 'compare' || view === 'tune') { pollReports(true); pollTunes(); }
    if (view === 'tune') pollFlow(true);
    if (view === 'settings') pollSettings();
    if (view === 'reports') pollRun();
    document.title = `${view[0].toUpperCase()}${view.slice(1)} · Strata Profile Studio`;
  }

  // ================================================================== start
  function start() {
    if (!SC) {
      $('main').prepend(el('p', { class: 'error-text', text: 'Chart.js did not load, so charts cannot be drawn.' }));
    }
    initTheme();
    initKeyDialog();
    if (SC) {
      buildLive();
      buildReports();
      buildCompare();
      buildTune();
      buildSettings();
    }
    window.addEventListener('hashchange', route);
    route();
    startPolling();
    document.addEventListener('visibilitychange', () => { if (!document.hidden) pollNow(); });
  }

  start();
})();
