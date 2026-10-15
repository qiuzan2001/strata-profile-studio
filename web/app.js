/* app.js: the Studio page. Three views (Live, Compare, Tune), a Data toggle per view, a settings drawer.
 * Talks only to studio.py (/api/...); draws with charts.js. Every piece of server text goes through
 * textContent / createElement (el() below); there is no markup parsing.
 */
(function () {
  'use strict';

  const SC = window.StudioCharts;
  const VIEWS = ['live', 'compare', 'tune'];
  const NAMES_DECODE = ['card', 'CPU experts', 'draft', 'adapt wait', 'other'];
  const NAMES_PROMPT = ['PCIe + dequant', 'compute', 'other'];
  const STEP_DEFS = [
    ['pcie_frac', 'PCIe share'], ['spec_min_p', 'draft floor'], ['pool_workers', 'CPU workers'], ['adapt', 'expert tier'],
  ];
  const isNarrow = () => !!(window.matchMedia && window.matchMedia('(max-width: 600px)').matches);

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
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      node.append(k instanceof Node ? k : String(k));
    }
    return node;
  }

  function clear(node) { node.replaceChildren(); }
  // Chart.js sizes a canvas from its parent, so every canvas sits in a box whose size CSS decides.
  function cv(canvas, cls) { return el('div', { class: `cv ${cls}` }, canvas); }
  const $ = (id) => document.getElementById(id);
  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const num = (v, d) => (isNum(v) ? v.toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }) : '—');
  const one = (v) => (isNum(v) ? v.toFixed(1) : '—');
  const text = (v, fb) => (v == null || v === '' ? (fb == null ? '—' : fb) : String(v));
  const signed = (v, d) => (isNum(v) ? `${v < 0 ? '−' : '+'}${Math.abs(v).toFixed(d == null ? 1 : d)}` : '—');

  function mmss(sec) {
    if (!isNum(sec)) return '—';
    const s = Math.max(0, Math.round(sec));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function dateTime(ts) {
    if (!isNum(ts)) return '—';
    const d = new Date(ts * 1000);
    const hm = [d.getHours(), d.getMinutes()].map((n) => String(n).padStart(2, '0')).join(':');
    return `${d.getDate()} ${MONTHS[d.getMonth()]} ${hm}`;
  }
  function clock(ts) {
    if (!isNum(ts)) return '—';
    const d = new Date(ts * 1000);
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
  }

  function sameValue(a, b) {
    if (a == null || b == null) return false;
    const x = Number(a);
    const y = Number(b);
    if (isFinite(x) && isFinite(y) && String(a).trim() !== '' && String(b).trim() !== '') return Math.abs(x - y) < 1e-9;
    return String(a) === String(b);
  }

  function toast(message, bad) {
    const t = el('div', { class: bad ? 'toast bad' : 'toast', text: message });
    $('toasts').append(t);
    setTimeout(() => t.remove(), bad ? 8000 : 4000);
  }

  // Tables exist only inside the Data view and the settings drawer.
  function table(head, rows, numCols) {
    const nums = new Set(numCols || []);
    const tbody = el('tbody');
    for (const r of rows) {
      tbody.append(el('tr', { class: r.pending ? 'pending' : null }, (r.cells || r).map((c, i) => {
        const cell = c && typeof c === 'object' && !(c instanceof Node) ? c : { v: c };
        const body = cell.v instanceof Node ? cell.v : (cell.v == null || cell.v === '' ? '—' : String(cell.v));
        return el('td', { class: [nums.has(i) ? 'num' : null, cell.mono ? 'mono' : null].filter(Boolean).join(' ') || null, title: cell.title }, body);
      })));
    }
    return el('div', { class: 'table-wrap' }, el('table', null,
      el('thead', null, el('tr', null, head.map((h, i) => el('th', { scope: 'col', class: nums.has(i) ? 'num' : null, text: h })))), tbody));
  }

  function csvOf(sections) {
    const q = (v) => {
      const s = v == null ? '' : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const out = [];
    for (const s of sections) {
      out.push(q(s.title));
      out.push(s.head.map(q).join(','));
      for (const r of s.rows) out.push(r.map(q).join(','));
      out.push('');
    }
    return out.join('\n');
  }

  function download(name, mime, body) {
    const url = URL.createObjectURL(new Blob([body], { type: mime }));
    const a = el('a', { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ================================================================== theme
  const THEMES = ['system', 'light', 'dark'];
  function applyTheme(mode) {
    const root = document.documentElement;
    if (mode === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);
    $('theme-btn').setAttribute('aria-label', `Theme: ${mode}`);
    $('theme-btn').title = `Theme: ${mode}`;
    if (SC) SC.repaintAll();
  }
  function initTheme() {
    let mode = 'system';
    try { mode = THEMES.includes(localStorage.getItem('studio.theme')) ? localStorage.getItem('studio.theme') : 'system'; } catch (e) { /* blocked */ }
    applyTheme(mode);
    $('theme-btn').addEventListener('click', () => {
      mode = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length];
      try { localStorage.setItem('studio.theme', mode); } catch (e) { /* blocked */ }
      applyTheme(mode);
    });
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (mode === 'system' && SC) SC.repaintAll(); });
    }
  }

  // ================================================================== api, dialogs
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  const S = {
    state: null, studioDown: false, keyWait: false,
    samples: [], lastSeq: 0, max: 300, window: 60, showTune: false,
    reports: null, tunes: [], docs: new Map(), docSig: new Map(),
    flow: null, flowAt: 0, runInfo: null, settings: null,
    route: { view: 'live', params: {} },
    data: {},
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
    if (!res.ok) throw new ApiError(res.status, data && data.error && data.error.message ? data.error.message : `Request failed (${res.status}).`);
    return data;
  }

  function askKey(wrong) {
    const dlg = $('key-dialog');
    if (dlg.open) return;
    S.keyWait = true;
    $('key-error').hidden = !wrong;
    $('key-error').textContent = wrong ? 'That key was not accepted.' : '';
    $('key-input').value = '';
    dlg.showModal();
    $('key-input').focus();
  }

  function initKeyDialog() {
    const dlg = $('key-dialog');
    dlg.addEventListener('cancel', (e) => e.preventDefault());
    $('key-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = $('key-input').value.trim();
      if (!v) return;
      try { sessionStorage.setItem('studio.key', v); } catch (err) { /* this load only */ }
      S.keyWait = false;
      dlg.close();
      pollNow();
    });
  }

  function confirmDialog(title, paragraphs, okLabel) {
    return new Promise((resolve) => {
      const dlg = $('confirm-dialog');
      $('confirm-title').textContent = title;
      $('confirm-text').textContent = paragraphs.join('\n\n');
      $('confirm-ok').textContent = okLabel;
      const done = () => { dlg.removeEventListener('close', done); resolve(dlg.returnValue === 'ok'); };
      dlg.returnValue = 'cancel';
      dlg.addEventListener('close', done);
      dlg.showModal();
    });
  }

  // ================================================================== state and polling
  const strata = () => (S.state && S.state.strata) || {};
  const runState = () => (strata().run || {}).state || 'idle';
  const tuneInfo = () => strata().tune || {};
  const flowRunning = () => (S.state && S.state.flow && S.state.flow.state === 'running') || (S.flow && S.flow.state === 'running');

  function busyReason() {
    const st = strata();
    if (!S.state || S.studioDown) return 'Studio not answering';
    if (st.status === 'unreachable') return 'Strata not answering';
    if (st.status === 'key_needed') return 'Strata needs an API key';
    if (st.status === 'profile_off') return 'Profiling is off';
    if (st.status !== 'ok') return 'Strata not ready';
    if (flowRunning()) return 'A tune is running';
    if (tuneInfo().state === 'running') return 'Strata is tuning';
    if (runState() === 'running') return 'A profile run is going';
    return null;
  }

  async function pollState() {
    try {
      const st = await api('/api/state');
      S.studioDown = false;
      const prevRun = S.state && S.state.strata && S.state.strata.run ? S.state.strata.run.state : null;
      const prevFlow = S.state && S.state.flow ? S.state.flow.state : null;
      S.state = st;
      if (prevRun === 'running' && runState() !== 'running') pollReports();
      if (prevFlow !== (st.flow && st.flow.state)) pollFlow();
      renderChrome();
      renderCurrent();
    } catch (e) {
      if (e.status === 0) { S.studioDown = true; renderChrome(); }
    }
  }

  async function pollLive() {
    try {
      const data = await api(`/api/live?since=${S.lastSeq}`);
      if (isNum(data.last_seq) && data.last_seq < S.lastSeq) { S.samples = []; S.lastSeq = 0; }
      S.max = data.max || S.max;
      const bySeq = new Map(S.samples.map((s) => [s.seq, s]));
      for (const s of data.samples || []) bySeq.set(s.seq, s);
      S.samples = Array.from(bySeq.values()).sort((a, b) => a.seen_at - b.seen_at || a.seq - b.seq).slice(-S.max);
      if (isNum(data.last_seq)) S.lastSeq = Math.max(S.lastSeq, data.last_seq);
      if (S.route.view === 'live') renderCurrent();
    } catch (e) { /* the pill says what is wrong */ }
  }

  async function pollFlow() {
    try {
      const f = await api('/api/tune-flow');
      const prev = S.flow ? S.flow.state : null;
      S.flow = f;
      S.flowAt = Date.now();
      if (prev === 'running' && f.state !== 'running') {
        pollReports();
        pollTunes();
        if (f.state === 'failed') toast(`Tune failed: ${text(f.error, 'see Data')}`, true);
      }
      renderChrome();
      renderCurrent();
    } catch (e) { /* ignore */ }
  }

  async function pollReports() {
    try {
      const data = await api('/api/reports');
      const sig = (list) => (list || []).map((r) => `${r.id}:${(r.order || []).join(',')}:${r.ok}:${r.note ? r.note.length : 0}`).join('|');
      const changed = sig(S.reports && S.reports.reports) !== sig(data.reports);
      S.reports = data;
      for (const r of data.reports || []) {
        const s = (r.order || []).join(',');
        if (S.docSig.get(r.id) !== s) { S.docs.delete(r.id); S.docSig.set(r.id, s); }
      }
      if (changed) renderCurrent();
    } catch (e) { /* ignore */ }
  }

  async function pollTunes() {
    try {
      S.tunes = (await api('/api/tunes')).tunes || [];
      renderCurrent();
    } catch (e) { /* ignore */ }
  }

  async function pollSettings() {
    try {
      S.settings = await api('/api/settings');
      if (S.route.view === 'live') renderCurrent();
      if ($('drawer').open) renderDrawer();
    } catch (e) { /* ignore */ }
  }

  function loop(fn, ms) {
    let timer = null;
    const run = async () => {
      timer = null;
      if (!S.keyWait) { try { await fn(); } catch (e) { /* never stops the loop */ } }
      timer = setTimeout(run, ms() * (document.hidden ? 5 : 1));
    };
    run();
    return () => { if (timer) { clearTimeout(timer); run(); } };
  }

  let kicks = [];
  const pollNow = () => kicks.forEach((k) => k());

  function startPolling() {
    let tick = 0;
    kicks = [
      loop(pollState, () => 2000),
      loop(pollLive, () => (S.route.view === 'live' ? 1000 : 5000)),
      loop(async () => {
        tick += 1;
        if (flowRunning() || S.route.view === 'tune' || tick % 10 === 0 || !S.flow) await pollFlow();
      }, () => 1000),
      loop(async () => { if (S.route.view !== 'live' || !S.reports) await pollReports(); }, () => 10000),
      loop(async () => { if (S.route.view !== 'live' || !S.tunes.length) await pollTunes(); }, () => 10000),
      loop(pollSettings, () => 10000),
    ];
  }

  // ================================================================== chrome
  const BANNER = {
    unreachable: 'Strata not answering',
    key_needed: 'Strata needs an API key',
    profile_off: 'Profiling is off in Strata',
  };

  function tuneProgress() {
    const f = S.flow || {};
    const stages = (f.stages || []).filter((s) => ['before', 'tune', 'reload', 'after'].includes(s.key));
    const idx = stages.findIndex((s) => s.state === 'running');
    return idx >= 0 ? `${idx + 1}/${stages.length}` : null;
  }

  function renderChrome() {
    const st = strata();
    let label = 'Connecting';
    let cls = 'pill-idle';
    if (S.studioDown) { label = 'Studio not answering'; cls = 'pill-bad'; }
    else if (S.state) {
      if (flowRunning() && tuneProgress()) { label = `Tuning · ${tuneProgress()}`; cls = 'pill-warn'; }
      else if (st.status === 'ok') {
        if (st.loaded) { label = text(st.model, 'Connected'); cls = 'pill-ok'; }
        else { label = 'Unloaded'; cls = 'pill-warn'; }
      } else if (st.status === 'unreachable') { label = 'Strata unreachable'; cls = 'pill-bad'; }
      else if (st.status === 'key_needed') { label = 'Key needed'; cls = 'pill-warn'; }
      else if (st.status === 'profile_off') { label = 'Profiling off'; cls = 'pill-warn'; }
    }
    $('pill').className = `pill ${cls}`;
    $('pill-text').textContent = label;

    const banner = $('banner');
    let msg = null;
    if (S.studioDown) msg = 'Studio not answering';
    else if (BANNER[st.status]) msg = BANNER[st.status];
    else if (st.message) msg = 'Reports folder differs from Strata’s';
    else if (tuneInfo().state === 'running' || (flowRunning() && S.flow && S.flow.stage === 'tune')) msg = 'Model unloaded · requests get 503';
    banner.hidden = !msg;
    banner.textContent = msg || '';
    banner.title = st.message || '';
    $('view-live').classList.toggle('dim', st.status === 'unreachable' || S.studioDown);
  }

  setInterval(() => {
    if (flowRunning() && S.route.view === 'tune' && !S.data.tune) renderTune();
    if (runState() === 'running' && S.route.view === 'compare' && !S.data.compare) updateMeasure();
  }, 1000);

  function renderCurrent() {
    const v = S.route.view;
    if (S.data[v]) return renderData(v);
    if (v === 'live') return renderLive();
    if (v === 'compare') return renderCompare();
    if (v === 'tune') return renderTune();
    return null;
  }

  // ================================================================== Live
  const L = { built: false };

  function pendingSettings() {
    const rows = (S.settings && S.settings.rows) || [];
    return rows.filter((r) => r.next_load != null && r.now != null && isFinite(Number(r.next_load)) && isFinite(Number(r.now))
      && !sameValue(r.next_load, r.now));
  }

  function nextLoadChip() {
    const n = pendingSettings().length;
    if (!n) return null;
    return el('button', { class: 'chip chip-warn', type: 'button', text: `${n} change at next load`, onclick: openDrawer });
  }

  function keyRow() {
    const k = (cls, name) => el('span', null, el('i', { class: cls }), name);
    return el('div', { class: 'keyrow', 'aria-hidden': 'true' }, k('k-card', 'card'), k('k-cpu', 'CPU experts'),
      k('k-draft', 'draft'), k('k-wait', 'adapt wait'), k('k-other', 'other'), k('k-pcie', 'PCIe + dequant'), k('k-card', 'compute'));
  }

  function buildLive() {
    const root = $('view-live');
    L.pulse = el('span', { class: 'pulse' });
    L.headChips = el('span');
    L.head = el('div', { class: 'view-head' }, el('div', { class: 'grow' }, L.pulse, el('span', { text: 'Last request' })), L.headChips);

    L.value = el('span', { class: 'hero-value empty', text: '–' });
    L.delta = el('span', { class: 'delta delta-same' });
    L.sparkCanvas = el('canvas', { class: 'spark', role: 'img', 'aria-label': 'Decode speed, recent requests' });
    L.ringCanvas = el('canvas', { class: 'ring', role: 'img', 'aria-label': 'Expert sources' });
    L.promptCanvas = el('canvas', { class: 'bar-prompt', role: 'img', 'aria-label': 'Prompt read' });
    L.promptCanvas2 = el('canvas', { class: 'bar-prompt', role: 'img', 'aria-label': 'Prompt read' });
    L.hero = el('div', { class: 'hero block' },
      el('div', { class: 'hero-stat' },
        el('div', { class: 'hero-line' }, L.value, el('span', { class: 'hero-unit', text: 'tok/s' }), L.delta),
        cv(L.sparkCanvas, 'cv-spark')),
      cv(L.ringCanvas, 'cv-ring'),
      el('div', { class: 'hero-prompt' }, cv(L.promptCanvas, 'cv-prompt')));
    L.decodeCanvas = el('canvas', { class: 'bar-decode', role: 'img', 'aria-label': 'Decode window' });
    L.promptRow = el('div', { class: 'prompt-row' }, cv(L.promptCanvas2, 'cv-prompt'));
    L.multiCanvases = [0, 1, 2].map((i) => el('canvas', { class: 'multi', role: 'img', 'aria-label': ['Decode speed', 'Hit rate', 'Window split'][i] }));
    L.main = el('div', null, L.hero,
      el('div', { class: 'block' }, cv(L.decodeCanvas, 'cv-decode'), L.promptRow, keyRow()),
      el('div', { class: 'multiples' }, L.multiCanvases.map((c) => cv(c, 'cv-multi'))));
    root.append(L.head, L.main);

    if (SC) {
      L.spark = SC.spark(L.sparkCanvas);
      L.ring = SC.ring(L.ringCanvas);
      L.prompt = SC.hbar(L.promptCanvas, { thickness: 24, labelTop: 0, dominantLabel: false, totalSize: 14, rightPad: 96 });
      L.prompt2 = SC.hbar(L.promptCanvas2, { thickness: 20, labelTop: 0, dominantLabel: false, totalSize: 14, rightPad: 88 });
      L.decode = SC.hbar(L.decodeCanvas, { thickness: 40, labelTop: 36, totalAbove: () => isNarrow() });
      L.multis = SC.multiples(L.multiCanvases);
    }
    L.built = true;
  }

  function visibleSamples() {
    let a = S.samples;
    if (!S.showTune) a = a.filter((s) => s.tag !== 'tune');
    return a.slice(-S.window);
  }

  function lastWith(list, key) {
    for (let i = list.length - 1; i >= 0; i--) if (list[i][key] != null) return list[i];
    return null;
  }

  function decodeSpec(windowMs, total) {
    const w = {};
    for (const [k, v] of Object.entries(windowMs || {})) {
      const name = SC.DECODE_FROM_ENGINE[k] || k;
      w[name] = (w[name] || 0) + (isNum(v) ? v : 0);
    }
    const segments = NAMES_DECODE.map((name) => ({ name, value: w[name] || 0 }));
    const sum = segments.reduce((a, s) => a + s.value, 0);
    const tot = isNum(total) && total > 0 ? total : sum;
    segments.forEach((s) => { s.tip = `${one(s.value)} ms · ${tot ? Math.round((s.value / tot) * 100) : 0} %`; });
    let dom = 0;
    segments.forEach((s, i) => { if (s.value > segments[dom].value) dom = i; });
    return { segments, dominant: dom, totalText: `${one(tot)} ms`, label: segments.map((s) => `${s.name} ${one(s.value)} ms`).join(', ') };
  }

  function promptSpec(groups, tokS) {
    const g = {};
    for (const [k, v] of Object.entries(groups || {})) {
      const name = SC.PROMPT_FROM_ENGINE[k] || 'other';
      g[name] = (g[name] || 0) + (isNum(v) ? v : 0);
    }
    const segments = NAMES_PROMPT.map((name) => ({ name, value: g[name] || 0, small: `${num(g[name] || 0)} ms` }));
    const sum = segments.reduce((a, s) => a + s.value, 0) || 1;
    segments.forEach((s) => { s.tip = `${num(s.value)} ms · ${Math.round((s.value / sum) * 100)} %`; });
    let dom = 0;
    segments.forEach((s, i) => { if (s.value > segments[dom].value) dom = i; });
    return { segments, dominant: dom, totalText: isNum(tokS) ? `${num(tokS)} tok/s` : '',
      label: segments.map((s) => `${s.name} ${num(s.value)} ms`).join(', ') };
  }

  function renderLive() {
    if (!L.built || !SC) return;
    const vis = visibleSamples();
    const profileOff = strata().status === 'profile_off';
    clear(L.headChips);
    const chip = nextLoadChip();
    if (chip) L.headChips.append(chip);
    const last = vis.length ? vis[vis.length - 1] : null;
    L.pulse.className = runState() === 'running' || (last && last.open) ? 'pulse on' : 'pulse';

    // hero: newest decode speed, its change against the recent median, the sparkline
    const speeds = vis.map((s) => s.decode_tok_s).filter(isNum);
    const newest = lastWith(vis, 'decode_tok_s');
    L.value.textContent = newest ? one(newest.decode_tok_s) : '–';
    L.value.className = newest ? 'hero-value' : 'hero-value empty';
    const q = SC.quartiles(speeds);
    const pq = SC.quartiles(speeds.slice(0, -1));
    let deltaText = '';
    L.delta.className = 'delta delta-same';
    if (newest && pq && pq.median > 0) {
      const d = ((newest.decode_tok_s - pq.median) / pq.median) * 100;
      if (Math.abs(d) < 5) deltaText = `≈ ${Math.abs(d).toFixed(0)} %`;
      else if (d > 0) { deltaText = `▲ ${d.toFixed(0)} %`; L.delta.className = 'delta delta-up'; }
      else { deltaText = `▼ ${Math.abs(d).toFixed(0)} %`; L.delta.className = 'delta delta-down'; }
    }
    L.delta.textContent = deltaText;
    L.spark.update({ values: speeds, band: q ? [q.q1, q.q3] : null });

    // ring: where the experts came from in the newest request
    const hitS = lastWith(vis, 'hit_rate');
    if (hitS) {
      const hit = Math.min(1, Math.max(0, hitS.hit_rate));
      const pcie = Math.min(1 - hit, Math.max(0, isNum(hitS.pcie_share) ? hitS.pcie_share : 0));
      const cpu = Math.max(0, 1 - hit - pcie);
      const hits = vis.map((s) => s.hit_rate).filter(isNum);
      const hq = hits.length >= 3 ? SC.quartiles(hits) : null;
      L.ring.update({
        parts: [{ name: 'hit', value: hit }, { name: 'PCIe', value: pcie }, { name: 'CPU', value: cpu }],
        tick: hq ? hq.median : null, centre: `${Math.round(hit * 100)} %`, caption: 'hit',
        label: `Expert cache hit ${Math.round(hit * 100)} %, PCIe ${Math.round(pcie * 100)} %, CPU ${Math.round(cpu * 100)} %`,
      }, true);
    } else {
      L.ring.update({ parts: [], centre: '–', caption: 'hit', label: 'No hit rate yet' });
    }

    // decode bar and prompt bar
    const empty = profileOff ? 'needs profiling' : 'waiting for a request';
    const ws = lastWith(vis, 'window_ms');
    if (ws) L.decode.update(decodeSpec(ws.window_ms, ws.ms_per_window), true);
    else L.decode.update({ segments: [], empty, label: 'Decode window: no data' });
    const ps = lastWith(vis, 'prompt_groups_ms');
    let pspec;
    if (last && last.read === 0 && last.reused > 0) pspec = { segments: [], empty: 'reused', label: 'Prompt fully reused' };
    else if (ps) pspec = promptSpec(ps.prompt_groups_ms, ps.prompt_tok_s);
    else pspec = { segments: [], empty, label: 'Prompt read: no data' };
    L.prompt.update(pspec, true);
    L.prompt2.update(pspec, true);

    // three small multiples on one x
    const hits = vis.map((s) => s.hit_rate).filter(isNum);
    L.multis.update({
      labels: vis.map((s) => `#${s.seq}`),
      tps: vis.map((s) => (isNum(s.decode_tok_s) ? s.decode_tok_s : null)),
      hit: vis.map((s) => (isNum(s.hit_rate) ? s.hit_rate * 100 : null)),
      hitMedian: hits.length >= 3 ? SC.quartiles(hits).median * 100 : null,
      windows: vis.map((s) => {
        if (!s.window_ms) return null;
        const w = {};
        for (const [k, v] of Object.entries(s.window_ms)) w[SC.DECODE_FROM_ENGINE[k] || k] = v;
        return w;
      }),
    });
  }

  // ================================================================== Compare
  const C = { built: false, token: 0 };

  const report = (id) => S.reports && (S.reports.reports || []).find((r) => r.id === id);
  const okReports = () => ((S.reports && S.reports.reports) || []).filter((r) => r.ok);

  async function getDoc(id) {
    if (S.docs.has(id)) return S.docs.get(id);
    const doc = await api(`/api/reports/${encodeURIComponent(id)}`);
    S.docs.set(id, doc);
    return doc;
  }

  function defaultPair() {
    const ok = okReports();                                    // newest first
    const t = S.tunes.find((x) => x.before_report && x.after_report && report(x.before_report) && report(x.after_report));
    if (t) return { a: t.before_report, b: t.after_report };
    if (ok.length >= 2) return { a: ok[1].id, b: ok[0].id };
    if (ok.length === 1) return { a: ok[0].id, b: null };
    return { a: null, b: null };
  }

  function currentPair() {
    const p = S.route.params;
    return p.a || p.b ? { a: p.a || null, b: p.b || null } : defaultPair();
  }

  function pickLabel(r) {
    const s = r.settings_short || {};
    const tier = s.adapt === 'engine default' || s.adapt == null ? 'default' : s.adapt;
    const when = isNum(r.order && r.order[4]) ? dateTime(r.order[4]) : r.date;
    return `${when} · ${isNum(r.chat_tps) ? `${one(r.chat_tps)} tok/s` : '—'} · PCIe ${text(s.pcie_frac)} · tier ${tier}`;
  }

  function setPair(a, b) {
    const q = [];
    if (a) q.push(`a=${encodeURIComponent(a)}`);
    if (b) q.push(`b=${encodeURIComponent(b)}`);
    location.hash = `#compare${q.length ? `?${q.join('&')}` : ''}`;
  }

  // The compare picture (headline, bullet groups, where panel, chips): used by Compare and, in place, by Tune.
  function compareBlock() {
    const b = { el: el('div', { class: 'cmp' }) };
    const mk = (name) => {
      const canvas = el('canvas', { class: 'bullets', role: 'img', 'aria-label': name });
      const box = cv(canvas, 'cv-bullets');
      return { canvas, box, handle: SC ? SC.bullets(canvas) : null, wrap: el('div', null, el('div', { class: 'group-name', text: name }), box) };
    };
    b.headline = el('div', { class: 'headline' });
    b.groups = [mk('decode tok/s'), mk('prompt tok/s'), mk('hit rate')];
    b.whereCanvas = el('canvas', { class: 'where', role: 'img', 'aria-label': 'Decode window, A over B' });
    b.whereBox = cv(b.whereCanvas, 'cv-where');
    b.where = SC ? SC.where(b.whereCanvas) : null;
    b.chips = el('div', { class: 'chips' });
    b.empty = el('div', { class: 'empty-note', text: 'two reports needed' });
    b.body = el('div', { class: 'block' }, b.groups.map((g) => g.wrap),
      el('div', { class: 'group-name', text: 'where the time goes' }), b.whereBox, keyRow());
    b.el.append(b.empty, b.headline, b.body, b.chips);
    b.update = (cmp, docA, docB) => updateCompare(b, cmp, docA, docB);
    return b;
  }

  function updateCompare(b, cmp, docA, docB) {
    const have = !!docA;
    b.empty.hidden = have;
    b.headline.hidden = b.body.hidden = b.chips.hidden = !have;
    if (!have) return;
    const pair = !!(cmp && docB);
    const rows = pair ? cmp.rows : [];
    const verdictOf = (group, key) => (cmp ? cmp.verdicts.find((v) => v.group === group && v.key === key) : null);

    // headline: the first decode kind that moved, else chat
    clear(b.headline);
    if (pair) {
      const dec = rows.filter((r) => r.group === 'decode' && r.metric === 'decode tok/s' && r.state !== 'missing');
      const pick = dec.find((r) => r.state === 'better' || r.state === 'worse') || dec.find((r) => r.key === 'chat') || dec[0];
      if (pick) {
        const v = verdictOf('decode', pick.key);
        const assumed = !v || !v.floor_note;
        const cls = pick.state === 'better' ? 'up' : pick.state === 'worse' ? 'down' : 'same';
        const lead = pick.state === 'better' ? '▲' : pick.state === 'worse' ? '▼' : '≈';
        const val = isNum(pick.pct) ? (cls === 'same' ? `${Math.abs(pick.pct).toFixed(1)} %` : `${signed(pick.pct)} %`) : '–';
        b.headline.append(el('span', { class: `headline-value ${cls}`, text: `${lead} ${val}` }),
          el('span', { class: 'headline-line', text: `${pick.key} decode, ${one(pick.a)} → ${one(pick.b)} tok/s` }),
          el('span', { class: 'small muted', text: `noise ±${(pick.floor * 100).toFixed(1)} % ${assumed ? 'assumed' : 'measured'}` }));
      }
    } else {
      const chat = (docA.decode || []).find((d) => d.kind === 'chat') || (docA.decode || [])[0];
      if (chat) {
        b.headline.append(el('span', { class: 'headline-value same', text: `${one(chat.tps && chat.tps.median)} tok/s` }),
          el('span', { class: 'headline-line', text: `${chat.kind} decode` }));
      }
    }

    // bullets
    const toRow = (r) => {
      const v = verdictOf(r.group, r.key);
      const assumed = !v || !v.floor_note;
      const pts = r.floor_kind === 'pts';
      const floor = isNum(r.floor) ? (pts ? r.floor : r.a * r.floor) : null;
      let delta = '–';
      if (r.state !== 'missing') {
        if (r.state === 'same') delta = pts ? `≈ ${Math.abs(r.delta).toFixed(1)} pts` : `≈ ${Math.abs(r.pct == null ? 0 : r.pct).toFixed(1)} %`;
        else delta = `${r.state === 'better' ? '▲' : '▼'} ${pts ? `${signed(r.delta)} pts` : `${signed(r.pct)} %`}`;
      }
      return { label: String(r.key), a: r.a, b: r.b, floor, assumed, delta, state: r.state,
        tip: `${num(r.a, 1)} → ${num(r.b, 1)}${pts ? ' %' : ' tok/s'}` };
    };
    const single = (rowsOf) => rowsOf.filter((x) => isNum(x.b));
    const fromDoc = (kind) => {
      if (kind === 'decode') return (docA.decode || []).map((d) => ({ label: d.kind, a: null, b: d.tps && d.tps.median, floor: null, delta: `${one(d.tps && d.tps.median)} tok/s`, state: 'same', tip: '' }));
      if (kind === 'prompt') return (docA.prompts || []).map((p) => ({ label: p.label, a: null, b: p.prompt_tps, floor: null, delta: `${num(p.prompt_tps)} tok/s`, state: 'same', tip: '' }));
      return (docA.decode || []).filter((d) => isNum(d.hit_rate)).map((d) => ({ label: d.kind, a: null, b: d.hit_rate * 100, floor: null, delta: `${one(d.hit_rate * 100)} %`, state: 'same', tip: '' }));
    };
    const defs = [
      ['decode', (r) => r.group === 'decode' && r.metric === 'decode tok/s'],
      ['prompt', (r) => r.group === 'prompt' && r.metric === 'prompt tok/s'],
      ['hit', (r) => r.group === 'decode' && r.metric.startsWith('expert cache hit rate')],
    ];
    defs.forEach(([kind, pred], i) => {
      const g = b.groups[i];
      const rs = pair ? rows.filter(pred).map(toRow) : single(fromDoc(kind));
      g.wrap.hidden = rs.length === 0;
      if (!rs.length || !g.handle) return;
      const maxv = Math.max(...rs.flatMap((r) => [r.a, r.b, isNum(r.a) && isNum(r.floor) ? r.a + r.floor : null].filter(isNum)), 1);
      g.box.style.height = `${rs.length * 28 + 18}px`;
      const mag = Math.pow(10, Math.floor(Math.log10(maxv * 1.1)));
      const step = mag / 5;
      g.handle.update({ rows: rs, max: Math.ceil((maxv * 1.1) / step) * step, showTick: pair });
    });

    // where panel: A over B per decode kind on one ms scale
    const dm = (doc) => Object.fromEntries((doc.decode || []).map((d) => [d.kind, d]));
    const ma = dm(docA);
    const mb = docB ? dm(docB) : {};
    const kinds = [];
    for (const k of Object.keys(ma).concat(Object.keys(mb))) if (!kinds.includes(k)) kinds.push(k);
    const fold = (d) => {
      if (!d || !d.window_ms) return null;
      const w = {};
      for (const [k, v] of Object.entries(d.window_ms)) w[SC.DECODE_FROM_ENGINE[k] || k] = v;
      return w;
    };
    const wk = kinds.map((k) => ({ name: k, a: fold(ma[k]), b: fold(mb[k]), aTotal: ma[k] && ma[k].ms_per_window, bTotal: mb[k] && mb[k].ms_per_window }));
    const shown = isNarrow() ? wk.slice(0, 1) : wk;
    const tot = shown.flatMap((k) => [k.aTotal, k.bTotal]).filter(isNum);
    if (b.where) {
      const nrows = shown.reduce((a, k) => a + (k.a ? 1 : 0) + (k.b ? 1 : 0), 0) + Math.max(0, shown.length - 1);
      b.whereBox.style.height = `${Math.max(1, nrows) * 18 + 8}px`;
      b.where.update({ kinds: shown, max: tot.length ? Math.max(...tot) * 1.02 : 1 });
    }

    // chips: the settings that differ
    clear(b.chips);
    for (const s of (cmp && cmp.settings) || []) {
      if (!s.changed) continue;
      b.chips.append(el('span', { class: 'chip', text: `${String(s.flag).replace(/^--/, '')} ${text(s.a, 'default')}→${text(s.b, 'default')}` }));
    }
  }

  function buildCompare() {
    const root = $('view-compare');
    C.selA = el('select', { class: 'pick', 'aria-label': 'Report A (before)' });
    C.selB = el('select', { class: 'pick', 'aria-label': 'Report B (after)' });
    C.swap = el('button', { class: 'btn btn-icon', type: 'button', 'aria-label': 'Swap A and B', text: '⇄' });
    C.measure = el('button', { class: 'btn btn-primary', type: 'button', text: 'Measure' });
    C.head = el('div', { class: 'view-head' }, C.selA, C.swap, C.selB, el('span', { class: 'grow' }), C.measure);
    C.block = compareBlock();
    root.append(C.head, C.block.el);
    C.selA.addEventListener('change', () => setPair(C.selA.value || null, C.selB.value || null));
    C.selB.addEventListener('change', () => setPair(C.selA.value || null, C.selB.value || null));
    C.swap.addEventListener('click', () => setPair(C.selB.value || null, C.selA.value || null));
    C.measure.addEventListener('click', measure);
    C.built = true;
  }

  async function measure() {
    C.measure.disabled = true;
    try {
      await api('/api/run-profile', { method: 'POST', body: { long: null } });
      pollState();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function fillSelect(sel, value, withNone) {
    const ok = okReports();
    const sig = ok.map((r) => `${r.id}|${pickLabel(r)}`).join('\n');
    if (sel.dataset.sig !== sig) {
      clear(sel);
      if (withNone) sel.append(el('option', { value: '', text: 'none' }));
      for (const r of ok) sel.append(el('option', { value: r.id, text: pickLabel(r) }));
      sel.dataset.sig = sig;
    }
    sel.value = value || '';
  }

  function updateMeasure() {
    const reason = busyReason();
    const running = runState() === 'running';
    C.measure.disabled = !!reason;
    C.measure.textContent = running ? `Measuring ${mmss((strata().run || {}).elapsed_s)}` : 'Measure';
    C.measure.title = reason || '';
  }

  async function renderCompare() {
    if (!C.built || !SC) return;
    const { a, b } = currentPair();
    fillSelect(C.selA, a, false);
    fillSelect(C.selB, b, true);
    updateMeasure();
    const token = ++C.token;
    if (!a) { C.block.update(null, null, null); return; }
    try {
      const docA = await getDoc(a);
      const docB = b ? await getDoc(b) : null;
      const cmp = b ? await api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`) : null;
      if (token !== C.token) return;
      C.cmp = cmp;
      C.docs = { a: docA, b: docB };
      C.block.update(cmp, docA, docB);
    } catch (e) {
      if (token === C.token) C.block.update(null, null, null);
    }
  }

  // ================================================================== Tune
  const T = { built: false, token: 0, cands: [] };

  function tuneSource() {
    const f = S.flow;
    if (f && f.state && f.state !== 'idle') return f;
    const rec = S.tunes[0];
    if (!rec) return f || { state: 'idle', stages: [] };
    return { state: rec.outcome, id: rec.id, stages: rec.stages || [], candidates: rec.candidates,
      compare: rec.before_report && rec.after_report ? { a: rec.before_report, b: rec.after_report } : null,
      error: rec.error, lines: rec.lines_tail || [], elapsed_s: null, can_cancel: false, can_retry_after: false };
  }

  function buildTune() {
    const root = $('view-tune');
    T.steps = el('ol', { class: 'stepper' });
    T.btn = el('button', { class: 'btn btn-primary', type: 'button', text: 'Tune this PC' });
    T.head = el('div', { class: 'view-head' }, T.steps, T.btn);
    T.cands = STEP_DEFS.map(([key, name]) => {
      const canvas = el('canvas', { class: 'cand', role: 'img', 'aria-label': name });
      return { key, name, canvas, handle: SC ? SC.candidates(canvas) : null,
        wrap: el('div', null, el('div', { class: 'cand-name', text: name }), cv(canvas, 'cv-cand')) };
    });
    T.candRow = el('div', { class: 'cands block' }, T.cands.map((c) => c.wrap));
    T.block = compareBlock();
    root.append(T.head, T.candRow, T.block.el);
    T.btn.addEventListener('click', onTuneButton);
    T.built = true;
  }

  async function onTuneButton() {
    const f = S.flow || {};
    if (f.state === 'running' && f.can_cancel) {
      try { await api('/api/tune-flow/cancel', { method: 'POST', body: {} }); pollFlow(); } catch (e) { toast(e.message, true); }
      return;
    }
    if (f.can_retry_after) {
      try { await api('/api/tune-flow/retry-after', { method: 'POST', body: {} }); pollFlow(); } catch (e) { toast(e.message, true); }
      return;
    }
    const est = S.state && S.state.estimates && S.state.estimates.tune_s;
    const mins = isNum(est) ? `${Math.max(1, Math.round(est / 60))} min` : null;
    const ok = await confirmDialog('Tune this PC?', [
      `This takes a while: ${mins ? `last time here it took about ${mins}; ` : ''}setup’s estimate is 15–30 minutes, longer on a slow card. The PC is busy meanwhile.`,
      'The model is unloaded while Strata’s calibration measures the PCIe share, the draft floor, the CPU workers and the expert tier. Requests get 503 until it ends.',
      'The calibration rewrites the run config. A value an earlier tune set can go back to the default: a new value is kept only when it beats the default by 3% in the confirm step.',
    ], 'Tune this PC');
    if (!ok) return;
    T.btn.disabled = true;
    try {
      await api('/api/tune-flow', { method: 'POST', body: { long: null } });
      pollFlow();
      pollState();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function stepSpec(step) {
    const vals = step.values || [];
    const labels = vals.map((v) => String(v.value));
    const keptIdx = step.kept == null ? -1 : vals.findIndex((v) => sameValue(v.value, step.kept));
    const tagOf = (value) => {
      if (sameValue(value, step.default)) return 'default';
      if (step.was != null && sameValue(value, step.was)) return 'was';
      return '';
    };
    const maxRounds = Math.max(0, ...vals.map((v) => (v.rounds || []).length));
    const rounds = [];
    for (let k = 0; k < maxRounds; k++) rounds.push(vals.map((v) => ((v.rounds || [])[k] != null ? v.rounds[k] : null)));
    return {
      labels: labels.map((l) => (step.key === 'adapt' && l === 'default' ? 'engine default' : l)),
      tags: labels.map((l) => (step.key === 'adapt' && l === 'default' ? '' : tagOf(l))),
      rounds, medians: vals.map((v) => (isNum(v.median) ? v.median : null)),
      keptIndex: keptIdx,
      bestIndex: step.best == null ? -1 : vals.findIndex((v) => sameValue(v.value, step.best)),
      tips: vals.map((v) => `${step.label}: ${v.value}`),
      tipLines: vals.map((v, i) => [`median ${one(v.median)} tok/s`,
        isNum(v.min) && isNum(v.max) ? `range ${one(v.min)}–${one(v.max)}` : '', i === keptIdx ? 'kept' : ''].filter(Boolean)),
    };
  }

  function renderTune() {
    if (!T.built || !SC) return;
    const f = tuneSource();
    const est = S.state && S.state.estimates;
    const delta = (Date.now() - S.flowAt) / 1000;
    const running = f.state === 'running';
    const stageBy = Object.fromEntries((f.stages || []).map((s) => [s.key, s]));
    const defs = [['before', 'Measure', est && est.profile_s], ['tune', 'Tune', est && est.tune_s], ['reload', 'Load', null], ['after', 'Measure', est && est.profile_s]];
    clear(T.steps);
    defs.forEach(([key, name, expect], i) => {
      const s = stageBy[key];
      const state = s ? s.state : 'pending';
      const cls = state === 'done' ? 'step-done' : state === 'running' ? 'step-run' : state === 'failed' ? 'step-fail' : '';
      let time = '';
      if (s && state === 'done') time = mmss(s.elapsed_s);
      else if (s && state === 'running') time = `${mmss((s.elapsed_s || 0) + (S.flow === f ? delta : 0))}${isNum(expect) ? ` / ~${mmss(expect)}` : ''}`;
      else if (s && state === 'failed') time = 'failed';
      T.steps.append(el('li', { class: `step ${cls}`, title: s && s.message ? s.message : '' },
        el('span', { class: 'step-dot', text: state === 'done' ? '✓' : state === 'failed' ? '✕' : String(i + 1) }),
        el('span', { class: 'step-name', text: name }),
        el('span', { class: 'step-time', text: time })));
    });

    // the one button: Tune this PC, Cancel, or Measure again
    const reason = busyReason();
    const external = tuneInfo().state === 'running' && !tuneInfo().started_by_studio && !flowRunning();
    if (running && f.can_cancel) { T.btn.textContent = 'Cancel'; T.btn.disabled = false; T.btn.title = ''; }
    else if (f.can_retry_after) { T.btn.textContent = 'Measure again'; T.btn.disabled = !!reason; T.btn.title = reason || ''; }
    else if (running) { T.btn.textContent = 'Tuning'; T.btn.disabled = true; T.btn.title = ''; }
    else if (external) { T.btn.textContent = `Tuning elsewhere ${mmss(tuneInfo().elapsed_s)}`; T.btn.disabled = true; T.btn.title = ''; }
    else {
      let why = reason;
      if (!why && !tuneInfo().available) why = text(tuneInfo().unavailable_reason, 'Tuning not available');
      T.btn.textContent = 'Tune this PC';
      T.btn.disabled = !!why;
      T.btn.title = why || '';
    }

    // candidates fill in as the calibration prints
    const steps = (f.candidates && f.candidates.steps) || [];
    let anyCand = false;
    for (const c of T.cands) {
      const step = steps.find((s) => s.key === c.key);
      const has = !!(step && (step.values || []).length);
      anyCand = anyCand || has;
      c.wrap.hidden = !has;
      if (has && c.handle) c.handle.update(stepSpec(step));
    }
    T.candRow.hidden = !anyCand;

    // the compare opens in place under the stepper
    const cmpRef = f.compare && f.compare.a && f.compare.b ? f.compare : null;
    T.block.el.hidden = !cmpRef;
    if (cmpRef) {
      const key = `${cmpRef.a}|${cmpRef.b}`;
      if (T.key !== key) {
        T.key = key;
        const token = ++T.token;
        Promise.all([getDoc(cmpRef.a), getDoc(cmpRef.b), api(`/api/compare?a=${encodeURIComponent(cmpRef.a)}&b=${encodeURIComponent(cmpRef.b)}`)])
          .then(([da, db, cmp]) => { if (token === T.token) { T.cmp = cmp; T.block.update(cmp, da, db); } })
          .catch(() => { T.key = null; });
      }
    } else {
      T.key = null;
    }
  }

  // ================================================================== Data (per view)
  function setDataFlag(view, on) {
    if (on) S.data[view] = true; else delete S.data[view];
    try {
      if (on) sessionStorage.setItem(`studio.data.${view}`, '1'); else sessionStorage.removeItem(`studio.data.${view}`);
    } catch (e) { /* blocked */ }
  }

  function noteEditor(kind, id, current) {
    const ta = el('textarea', { rows: 2, maxlength: 2000, 'aria-label': 'Note' });
    ta.value = current || '';
    const status = el('span', { class: 'small muted' });
    const btn = el('button', {
      class: 'btn', type: 'button', text: 'Save note',
      onclick: async () => {
        btn.disabled = true;
        try { await api('/api/notes', { method: 'POST', body: { kind, id, text: ta.value } }); status.textContent = 'Saved'; } catch (e) { status.textContent = e.message; }
        btn.disabled = false;
      },
    });
    return el('div', null, ta, el('div', { class: 'data-bar' }, btn, status));
  }

  function dataSections(view) {
    const s = [];
    if (view === 'live') {
      const rows = S.samples.filter((x) => S.showTune || x.tag !== 'tune').slice(-20).reverse();
      s.push({ title: 'Last 20 requests', head: ['time', 'context', 'read', 'reused', 'prompt tok/s', 'decode tok/s', 'hit %', 'ms per window', 'tag', 'source'],
        rows: rows.map((x) => [clock(x.seen_at), x.prompt_tokens, x.read, x.reused, x.prompt_tok_s, x.decode_tok_s,
          isNum(x.hit_rate) ? Number((x.hit_rate * 100).toFixed(1)) : null, x.ms_per_window, x.tag, x.source]), num: [1, 2, 3, 4, 5, 6, 7] });
    } else if (view === 'compare') {
      const cmp = C.cmp;
      if (cmp) {
        s.push({ title: 'Every number', head: ['group', 'key', 'metric', 'A', 'B', 'change', 'change %', 'floor', 'state'],
          rows: cmp.rows.map((r) => [r.group, r.key, r.metric, r.a, r.b, r.delta, r.pct, r.floor, r.state]), num: [3, 4, 5, 6, 7] });
        s.push({ title: 'Settings', head: ['setting', 'A', 'B', 'changed'], rows: cmp.settings.map((x) => [x.flag, x.a, x.b, x.changed ? 'yes' : '']) });
        s.push({ title: 'Noise floor', head: ['from', 'A', 'B'], rows: (cmp.floors.pairs || []).map((p) => [p.label, p.a, p.b]) });
      }
      const list = (S.reports && S.reports.reports) || [];
      s.push({ title: 'Reports', head: ['report', 'date', 'chat tok/s', 'PCIe', 'draft floor', 'workers', 'tier', 'ok'],
        rows: list.map((r) => [r.id, r.date, r.chat_tps, (r.settings_short || {}).pcie_frac, (r.settings_short || {}).spec_min_p,
          (r.settings_short || {}).pool_workers, (r.settings_short || {}).adapt, r.ok ? 'yes' : r.error]), num: [2] });
      const docs = C.docs || {};
      for (const k of ['a', 'b']) {
        const d = docs[k];
        if (d) {
          s.push({ title: `Report ${k.toUpperCase()}`, head: ['field', 'value'],
            rows: [['path', d.path], ['gpu', d.machine && d.machine.gpu], ['cpu', d.machine && d.machine.cpu], ['model', d.machine && d.machine.model],
              ['context', d.machine && d.machine.context], ['strata', d.machine && d.machine.strata_version], ['elapsed s', d.elapsed_s],
              ['long prompt', d.params && d.params.long]] });
        }
      }
    } else if (view === 'tune') {
      const f = tuneSource();
      s.push({ title: 'Stages', head: ['stage', 'state', 'seconds', 'message'], rows: (f.stages || []).map((x) => [x.label || x.key, x.state, x.elapsed_s, x.message]), num: [2] });
      s.push({ title: 'Past tunes', head: ['id', 'outcome', 'tok/s', 'tune s', 'summary'], rows: S.tunes.map((t) => [t.id, t.outcome, t.tok_s, t.tune_s, t.summary || t.error]), num: [2, 3] });
      const steps = (f.candidates && f.candidates.steps) || [];
      s.push({ title: 'Candidates', head: ['step', 'value', 'median tok/s', 'min', 'max', 'rounds', 'kept'],
        rows: steps.flatMap((st) => (st.values || []).map((v) => [st.label, v.value, v.median, v.min, v.max, (v.rounds || []).join(' '), sameValue(v.value, st.kept) ? 'kept' : ''])), num: [2, 3, 4] });
    }
    return s;
  }

  function renderData(view) {
    const host = $(`view-${view}`);
    const old = host.querySelector('.data-panel');
    if (old) old.remove();
    const sections = dataSections(view);
    const panel = el('div', { class: 'data-panel', id: `data-${view}` });
    const bar = el('div', { class: 'data-bar' },
      el('button', { class: 'btn', type: 'button', text: 'CSV', onclick: () => download(`strata-studio-${view}.csv`, 'text/csv', csvOf(sections)) }),
      el('button', { class: 'btn', type: 'button', text: 'JSON', onclick: () => download(`strata-studio-${view}.json`, 'application/json',
        JSON.stringify(Object.fromEntries(sections.map((x) => [x.title, x.rows.map((r) => Object.fromEntries(x.head.map((h, i) => [h, r[i]])))])), null, 1)) }));
    if (view === 'live') {
      const box = el('input', { type: 'checkbox', id: 'live-tune' });
      box.checked = S.showTune;
      box.addEventListener('change', () => { S.showTune = box.checked; renderData('live'); });
      bar.append(el('label', null, box, ' calibration requests'));
    }
    panel.append(bar);
    for (const sec of sections) {
      panel.append(el('div', { class: 'data-title', text: sec.title }));
      panel.append(sec.rows.length ? table(sec.head, sec.rows, sec.num) : el('div', { class: 'empty-note', text: 'nothing yet' }));
    }
    if (view === 'compare') {
      const docs = C.docs || {};
      for (const k of ['a', 'b']) {
        if (docs[k]) panel.append(el('div', { class: 'data-title', text: `Note ${k.toUpperCase()}` }), noteEditor('report', docs[k].id, docs[k].note));
      }
    }
    if (view === 'tune') {
      const f = tuneSource();
      panel.append(el('div', { class: 'data-title', text: 'Output' }), el('pre', { class: 'log', text: (f.lines || []).join('\n') || 'no output yet' }));
      for (const t of S.tunes.slice(0, 5)) panel.append(el('div', { class: 'data-title', text: `Note · ${dateTime(t.started_at)}` }), noteEditor('tune', t.id, t.note));
    }
    host.append(panel);
  }

  // Hides a view's chart body while its Data panel shows, and back again with no reload.
  function showDataState() {
    const v = S.route.view;
    const on = !!S.data[v];
    $('data-btn').setAttribute('aria-pressed', String(on));
    const bodies = { live: [L.head, L.main], compare: [C.head, C.block && C.block.el], tune: [T.head, T.candRow, T.block && T.block.el] };
    for (const view of VIEWS) {
      const show = !(view === v && on);
      for (const n of bodies[view]) if (n) n.dataset.dataHidden = show ? '' : '1';
      const host = $(`view-${view}`);
      const panel = host.querySelector('.data-panel');
      if (panel && (view !== v || !on)) panel.remove();
    }
    applyDataHidden();
    renderCurrent();
  }

  function applyDataHidden() {
    document.querySelectorAll('[data-data-hidden]').forEach((n) => {
      const hide = n.dataset.dataHidden === '1';
      n.classList.toggle('data-hidden', hide);
    });
  }

  // ================================================================== drawer
  function openDrawer() {
    const d = $('drawer');
    if (!d.open) d.show();
    renderDrawer();
    pollSettings();
  }

  function renderDrawer() {
    const d = $('drawer');
    clear(d);
    d.append(el('div', { class: 'drawer-head' }, el('h2', { text: 'Settings' }),
      el('button', { class: 'btn btn-icon', type: 'button', 'aria-label': 'Close', text: '✕', onclick: () => d.close() })));
    const s = S.settings;
    const rows = ((s && s.rows) || []).map((r) => {
      const pending = r.next_load != null && r.now != null && String(r.next_load) !== String(r.now);
      return { pending, cells: [{ v: r.name, mono: true, title: r.about }, text(r.now), text(r.next_load)] };
    });
    d.append(rows.length ? table(['Setting', 'Loaded', 'Next load'], rows) : el('div', { class: 'empty-note', text: 'no settings to show' }));
    d.append(el('div', { class: 'foot', text: 'Profiling slows an 8K prompt read by 3.9–4.7 % on an RTX 5070 Ti (Swift 1.5 IQ3_S); decode stays within its noise. Speeds here were measured with the timing switches on.' }));
    if (s) d.append(el('div', { class: 'foot', text: `${text(s.source)}${s.file ? ` · ${s.file}` : ''}` }));
  }

  // ================================================================== router and start
  function parseHash() {
    const raw = location.hash.replace(/^#/, '');
    const [name, query] = raw.split('?');
    const alias = { reports: 'compare', settings: 'live' };
    const view = VIEWS.includes(name) ? name : (alias[name] || 'live');
    const params = {};
    for (const [k, v] of new URLSearchParams(query || '')) params[k] = v;
    return { view, params, settings: name === 'settings' };
  }

  function route() {
    const r = parseHash();
    S.route = r;
    for (const v of VIEWS) $(`view-${v}`).hidden = v !== r.view;
    document.querySelectorAll('.tab').forEach((a) => {
      if (a.dataset.view === r.view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    $('range').hidden = r.view !== 'live';
    try { if (sessionStorage.getItem(`studio.data.${r.view}`) === '1') S.data[r.view] = true; } catch (e) { /* blocked */ }
    renderChrome();
    showDataState();
    document.title = `${r.view[0].toUpperCase()}${r.view.slice(1)} · Strata Profile Studio`;
    if (r.settings) openDrawer();
  }

  function buildRange() {
    const sizes = isNarrow() ? [20, 60, 300] : [30, 60, 300];
    const btns = sizes.map((n) => el('button', {
      type: 'button', 'aria-pressed': String(n === S.window), text: String(n),
      onclick: () => { S.window = n; btns.forEach((b, i) => b.setAttribute('aria-pressed', String(sizes[i] === n))); renderCurrent(); },
    }));
    $('range').append(...btns);
  }

  function start() {
    initTheme();
    initKeyDialog();
    buildRange();
    if (SC) { buildLive(); buildCompare(); buildTune(); }
    $('data-btn').addEventListener('click', () => {
      const v = S.route.view;
      setDataFlag(v, !S.data[v]);
      showDataState();
    });
    $('settings-btn').addEventListener('click', () => { const d = $('drawer'); if (d.open) d.close(); else openDrawer(); });
    $('drawer').addEventListener('keydown', (e) => { if (e.key === 'Escape') $('drawer').close(); });
    window.addEventListener('hashchange', route);
    route();
    startPolling();
    document.addEventListener('visibilitychange', () => { if (!document.hidden) pollNow(); });
  }

  start();
})();
