/* Strata Profile Studio: the shell.  Polls the Studio's API on a few cadences (only what the visible view needs,
   nothing while the tab is hidden), keeps the data, routes the hash to a view, and says what state Strata is in. */
'use strict';

(function () {
  const { el, isNum } = S;

  const A = (S.app = {
    state: null, studioDown: false,
    live: new Map(), lastSeq: 0, spans: [],
    reports: [], reportsDir: null, reportsExist: true, reportDocs: new Map(),
    tunes: [], flow: null, settings: null, hardware: null, run: null,
    route: S.route(),
    views: S.viewDefs || {},
    changed: new Set(),
  });

  // ------------------------------------------------------------------------------------ data access
  A.samples = function samples() {
    return [...A.live.values()].sort((a, b) => (a.seen_at - b.seen_at) || (a.seq - b.seq));
  };
  A.report = async function report(id) {
    const meta = A.reports.find((r) => r.id === id);
    const key = meta ? meta.mtime : null;
    const hit = A.reportDocs.get(id);
    if (hit && hit.key === key) return hit.doc;
    const doc = await S.api(`/api/reports/${encodeURIComponent(id)}`);
    A.reportDocs.set(id, { key, doc });
    return doc;
  };
  A.allReports = async function allReports() {
    const ok = A.reports.filter((r) => r.ok);
    const docs = await Promise.all(ok.map((r) => A.report(r.id).catch(() => null)));
    return docs.filter(Boolean);
  };
  const compareCache = new Map();
  A.compare = async function compare(a, b) {
    const ka = (A.reports.find((r) => r.id === a) || {}).mtime, kb = (A.reports.find((r) => r.id === b) || {}).mtime;
    const key = `${a}|${b}|${ka}|${kb}|${A.reports.length}`;
    if (compareCache.has(key)) return compareCache.get(key);
    const doc = await S.api(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
    compareCache.set(key, doc);
    return doc;
  };
  A.strata = () => (A.state && A.state.strata) || {};
  A.readOnly = () => !!(A.state && A.state.studio && A.state.studio.read_only);

  // why a profile run or a tune cannot start now (null: it can)
  A.cannotRun = function cannotRun() {
    const s = A.strata();
    if (A.studioDown) return 'The Studio does not answer.';
    if (A.readOnly()) return 'This Studio runs with --read-only.';
    if (s.status === 'unreachable') return 'Strata does not answer.';
    if (s.status === 'key_needed') return 'Strata needs an API key.';
    if (s.profile === false) return 'Profiling is off in Strata.';
    if (A.flow && A.flow.state === 'running') return 'A tune is running.';
    if (s.tune && s.tune.state === 'running') return 'Strata runs a tune.';
    if (s.run && s.run.state === 'running') return 'A profile run is in progress.';
    return null;
  };
  A.cannotTune = function cannotTune() {
    const r = A.cannotRun();
    if (r) return r;
    const s = A.strata();
    if (s.tune && !s.tune.available) return s.tune.unavailable_reason || 'Strata cannot tune on this PC.';
    return null;
  };

  A.startProfile = async function startProfile() {
    const why = A.cannotRun();
    if (why) { S.toast(why, 'bad'); return; }
    const est = A.state && A.state.estimates && A.state.estimates.profile_s;
    const ok = await S.confirm({
      title: 'Run a profile now?',
      points: [`Strata sends 16 requests through its own API (${est ? `about ${S.dur(est)}` : 'about 1 min'}).`,
        'Strata also answers your requests during the run. Their timings mix with the timings of the run.',
        'Strata writes the report into the results folder. The report shows on the Runs page.'],
      ok: 'Run the profile',
    });
    if (!ok) return;
    try {
      await S.api('/api/run-profile', { body: {} });
      S.toast('The profile run started.');
      poll('state', true); poll('run', true);
    } catch (e) { S.toast(e.message, 'bad'); }
  };

  // ------------------------------------------------------------------------------------------ polling
  const due = {};
  const EVERY = {
    state: () => 2000,
    live: () => 2000,
    hardware: () => (A.route.view === 'now' ? 1000 : null),
    host: () => (A.route.view === 'now' ? 1000 : null),
    experts: () => (A.route.view === 'now' ? 30000 : null),
    defaults: () => ((A.defaults && A.defaults.state === 'running') ? 2000 : (A.route.view === 'compare' ? 10000 : 60000)),
    reports: () => 10000,
    tunes: () => 15000,
    flow: () => (A.route.view === 'tune' || (A.flow && A.flow.state === 'running') ? 2000 : 10000),
    settings: () => (A.route.view === 'settings' || A.route.view === 'tune' ? 10000 : 60000),
    run: () => ((A.strata().run || {}).state === 'running' ? 2000 : (A.route.view === 'runs' ? 15000 : null)),
  };
  const FETCH = {
    async state() {
      const doc = await S.api('/api/state');
      // only what a view draws counts as a change: the clock fields move every poll
      const st = doc.strata || {};
      const sig = JSON.stringify([doc.studio, st.status, st.message, st.model, st.loaded, st.profile, st.run && st.run.state,
        st.tune && [st.tune.state, st.tune.available, st.tune.unavailable_reason], doc.flow, doc.estimates]);
      const changed = sig !== A._stateSig;
      A._stateSig = sig;
      A.state = doc;
      return changed;
    },
    async live() {
      const doc = await S.api(`/api/live?since=${A.lastSeq}`);
      if (doc.last_seq < A.lastSeq) A.live.clear();         // the Studio restarted
      for (const x of doc.samples) A.live.set(x.seq, x);
      const keep = new Set(doc.samples.map((x) => x.seq));
      while (A.live.size > doc.max) { const first = A.live.keys().next().value; if (keep.has(first)) break; A.live.delete(first); }
      const changed = doc.samples.length > 0 || JSON.stringify(doc.spans) !== JSON.stringify(A.spans);
      A.spans = doc.spans || [];
      A.lastSeq = doc.last_seq;
      return changed;
    },
    async hardware() { A.hardware = await S.api('/api/hardware'); return false; },
    async host() { A.host = await S.api('/api/host'); return false; },
    async experts() {
      const doc = await S.api('/api/experts');
      const sig = JSON.stringify([doc.base && doc.base.mtime, doc.learned && doc.learned.mtime, doc.resident_slots]);
      const changed = sig !== A._expSig;
      A._expSig = sig; A.experts = doc;
      return changed;
    },
    async defaults() {
      const doc = await S.api('/api/run-defaults');
      const sig = JSON.stringify([doc.state, (doc.lines || []).length]);
      const changed = sig !== A._defSig;
      A._defSig = sig; A.defaults = doc;
      return changed;
    },
    async reports() {
      const doc = await S.api('/api/reports');
      const sig = JSON.stringify(doc.reports.map((r) => [r.id, r.mtime, r.note, r.tune && r.tune.id]));
      const changed = sig !== A._repSig;
      A._repSig = sig;
      A.reports = doc.reports; A.reportsDir = doc.dir; A.reportsExist = doc.exists;
      return changed;
    },
    async tunes() {
      const doc = await S.api('/api/tunes');
      const sig = JSON.stringify(doc.tunes.map((t) => [t.id, t.outcome, t.note]));
      const changed = sig !== A._tuneSig;
      A._tuneSig = sig; A.tunes = doc.tunes;
      return changed;
    },
    async flow() {
      const doc = await S.api('/api/tune-flow');
      const sig = JSON.stringify([doc.id, doc.state, doc.stage, (doc.lines || []).length, doc.stages && doc.stages.map((s) => [s.state, Math.floor((s.elapsed_s || 0) / 5)])]);
      const changed = sig !== A._flowSig;
      const ended = A.flow && A.flow.state === 'running' && doc.state !== 'running';
      A._flowSig = sig; A.flow = doc;
      if (ended) { poll('tunes', true); poll('reports', true); }
      return changed;
    },
    async settings() {
      const doc = await S.api('/api/settings');
      const sig = JSON.stringify(doc);
      const changed = sig !== A._setSig;
      A._setSig = sig; A.settings = doc;
      return changed;
    },
    async run() {
      const doc = await S.api('/api/run-profile');
      const sig = JSON.stringify([doc.state, doc.lines.length]);
      const changed = sig !== A._runSig;
      A._runSig = sig; A.run = doc;
      return changed;
    },
  };
  const VIEW_NEEDS = {
    now: ['live', 'state', 'reports', 'tunes', 'experts'],
    runs: ['reports', 'state', 'run', 'tunes'],
    compare: ['reports', 'tunes', 'defaults', 'state'],
    tune: ['flow', 'tunes', 'state', 'reports', 'settings'],
    settings: ['settings', 'reports', 'state', 'tunes'],
  };
  const inflight = {};
  async function poll(name, force) {
    if (inflight[name]) return;
    inflight[name] = true;
    try {
      const changed = await FETCH[name]();
      if (name === 'state') {
        if (A.studioDown) { A.studioDown = false; }
        chrome();
      }
      if (changed !== false) A.changed.add(name);
      if ((name === 'hardware' || name === 'host') && A.views.now && A.route.view === 'now') A.views.now.tick(A);
      if (name === 'flow' && A.views.tune && A.route.view === 'tune' && A.views.tune.tick) A.views.tune.tick(A);
    } catch (e) {
      if (e.status === 0) { A.studioDown = true; chrome(); }
      else if (e.status === 401) { A.needKey = true; chrome(); }
    } finally {
      inflight[name] = false;
      due[name] = Date.now() + (EVERY[name]() || 60000);
      if (force) schedule();
    }
  }
  function tick() {
    if (document.hidden) return;
    const now = Date.now();
    for (const name of Object.keys(EVERY)) {
      const every = EVERY[name]();
      if (every === null) continue;
      if (!due[name] || now >= due[name]) poll(name);
    }
    schedule();
  }
  let renderQueued = false;
  function schedule() {
    if (renderQueued || !A.changed.size) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      const needs = VIEW_NEEDS[A.route.view] || [];
      const hit = needs.some((n) => A.changed.has(n));
      A.changed.clear();
      if (hit) renderView(false);
    });
  }
  setInterval(tick, 250);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });

  // ---------------------------------------------------------------------------------------- the chrome
  function chrome() {
    const s = A.strata();
    const pill = document.getElementById('status');
    const dot = pill.querySelector('.dot'), txt = pill.querySelector('.status-text');
    let cls = 'ok', text = '';
    if (A.studioDown) { cls = 'bad'; text = 'Studio not answering'; }
    else if (!A.state) { cls = 'idle'; text = 'connecting…'; }
    else if (s.status === 'unreachable') { cls = 'bad'; text = 'Strata not answering'; }
    else if (s.status === 'key_needed') { cls = 'warn'; text = 'API key needed'; }
    else {
      const bits = [s.model || 'Strata'];
      bits.push(s.loaded === false ? 'model unloaded' : 'loaded');
      if (s.profile === false) { cls = 'warn'; bits.push('profiling off'); }
      if (s.stock) bits.push('stock Strata');
      if (s.tune && s.tune.state === 'running') { cls = 'busy'; bits.push('tuning'); }
      else if (s.run && s.run.state === 'running') { cls = 'busy'; bits.push('profiling'); }
      if (s.loaded === false && cls === 'ok') cls = 'idle';
      text = bits.join(' · ');
    }
    S.setAttr(dot, 'class', `dot ${cls}`);
    S.setText(txt, text);
    S.setAttr(pill, 'title', s.message || '');
    S.show(document.getElementById('readonly'), A.readOnly());
    banner();
  }

  function banner() {
    const box = document.getElementById('banner');
    const s = A.strata();
    const st = A.state && A.state.studio;
    const items = [];
    const cfg = st && st.config_path ? st.config_path.split('/').pop() : 'strata-<model>.json';
    if (A.studioDown) {
      items.push(['bad', 'The Studio does not answer.', 'Make sure that studio.py runs. The page tries again every 2 s.', null]);
    } else if (A.needKey) {
      items.push(['warn', 'The Studio needs its key.', 'Give the key that you started the Studio with.', [['Enter the key', askKey]]]);
    } else if (s.status === 'unreachable') {
      items.push(['bad', `Strata does not answer at ${st ? st.strata_url : 'its address'}${s.checked_at ? ` (checked ${S.clock(s.checked_at, 'sec')})` : ''}.`,
        'Start Strata with ./start.sh or START-HERE.bat. The Studio tries again every 5 s. Runs, Compare and the settings file work without Strata.',
        [['Open Runs', () => S.go('#runs')]]]);
    } else if (s.status === 'key_needed') {
      items.push(['warn', 'Strata needs an API key.', 'Start the Studio again with STRATA_KEY=<key> python3 studio.py. Runs, Compare and the settings file work without the key.', [['Open Runs', () => S.go('#runs')]]]);
    } else if (s.status === 'profile_off') {
      items.push(['warn', 'Profiling is off in Strata. The Studio shows speeds, but not the time per component.', `Add "profile": true to ${cfg} and start Strata again.`, [['Settings', () => S.go('#settings')]]]);
    } else if (s.status === 'stock') {
      items.push(['idle', s.message || 'This Strata build has no profile routes.', '', A.route.view === 'settings' ? null : [['Settings', () => S.go('#settings')]]]);
    }
    if (s.status === 'ok' && s.message) items.push(['warn', s.message, '', null]);
    if (A.state && s.status !== 'unreachable' && s.loaded === false && !(s.tune && s.tune.state === 'running'))
      items.push(['idle', 'The model is not loaded.', 'Strata loads the model at the next request (about 30 s). A profile run or a tune also loads the model.', null]);
    if (A.flow && A.flow.state === 'running') {
      const stage = (A.flow.stages || []).find((x) => x.key === A.flow.stage);
      items.push(['busy', `Tune in progress: ${stage ? stage.label.toLowerCase() : 'start'} · ${S.dur(A.flow.elapsed_s)}.`,
        A.flow.stage === 'tune' ? 'The calibration unloads the model. Strata answers requests with 503.' : '', A.route.view === 'tune' ? null : [['Show the tune', () => S.go('#tune')]]]);
    } else if (s.tune && s.tune.state === 'running') {
      items.push(['busy', `Strata runs a tune (started on the page of Strata) · ${S.dur(s.tune.elapsed_s)}.`, 'Strata answers requests with 503 until the tune ends. The Studio shows the tune read only.', A.route.view === 'tune' ? null : [['Show the tune', () => S.go('#tune')]]]);
    }
    if (s.run && s.run.state === 'running' && !(A.flow && A.flow.state === 'running'))
      items.push(['busy', `Profile run in progress · ${S.dur(s.run.elapsed_s)}.`, 'Its requests show on the Now page when they end. The report shows on the Runs page.', A.route.view === 'runs' ? null : [['Runs', () => S.go('#runs')]]]);
    if (st && !st.results_dir_exists)
      items.push(['warn', `The results folder ${st.results_dir} does not exist.`, 'Start the Studio again with --results set to the bench/results folder of Strata.', null]);
    const sig = JSON.stringify(items.map((i) => i.slice(0, 3)));
    if (sig === box._sig) return;
    box._sig = sig;
    S.fill(box, items.map(([cls, head, more, actions]) => el('div', { class: `banner ${cls}`, role: cls === 'bad' ? 'alert' : 'status' },
      el('b', { text: head }), more ? el('span', { text: ' ' + more }) : null,
      actions ? el('span', { class: 'banner-actions' }, actions.map(([t, f]) => S.button(t, f, 'small'))) : null)));
  }

  function askKey() {
    const dlg = document.getElementById('dialog');
    const input = el('input', { type: 'password', autocomplete: 'off', 'aria-label': 'The Studio key' });
    S.fill(dlg, el('h2', { text: 'The Studio key' }), el('p', { text: 'The browser keeps the key for this tab only.' }), input,
      el('div', { class: 'dlg-buttons' }, S.button('Cancel', () => dlg.close()), S.button('Use it', () => {
        try { sessionStorage.setItem('studio.key', input.value.trim()); } catch (e) { /* ignore */ }
        A.needKey = false; dlg.close(); poll('state', true);
      }, 'primary')));
    dlg.showModal();
  }

  // ---------------------------------------------------------------------------------------- the router
  function renderView(full) {
    const r = A.route;
    for (const v of ['now', 'runs', 'compare', 'tune', 'settings']) S.show(document.getElementById(`view-${v}`), v === r.view);
    for (const a of document.querySelectorAll('nav.tabs a')) {
      const on = a.dataset.view === r.view;
      S.setClass(a, 'on', on);
      S.setAttr(a, 'aria-current', on ? 'page' : null);
    }
    const view = A.views[r.view];
    if (view) view.render(A, r, full);
    syncPanel();
  }

  async function syncPanel() {
    const r = A.route;
    if (!r.panel) { if (S.panel.req && !S.panel.ctx.sticky) S.panel.close(); return; }
    if (r.panel.where === 'live') {
      const list = A.samples();
      const req = A.live.get(Number(r.panel.id));
      if (!req) return;
      if (S.panel.req === req) return;
      S.panel.open(req, { where: 'live', list, hash: (x) => `#now/r/${x.seq}`, closeHash: '#now' });
    } else {
      try {
        const doc = await A.report(r.panel.report);
        const req = (doc.requests || []).find((x) => x.id === r.panel.id);
        if (!req) return;
        if (S.panel.req === req) return;
        S.panel.open(req, { where: 'report', report: doc.id, list: doc.requests, hash: (x) => `#runs/${doc.id}/r/${x.id}`, closeHash: `#runs/${doc.id}` });
      } catch (e) { S.toast(e.message, 'bad'); }
    }
  }

  window.addEventListener('hashchange', () => {
    const prev = A.route;
    A.route = S.route();
    if (A.route.view === 'now') { due.hardware = 0; due.host = 0; due.experts = 0; }
    const sameView = prev.view === A.route.view && prev.parts.slice(0, 2).join('/') === A.route.parts.slice(0, 2).join('/');
    if (sameView && (A.route.panel || prev.panel)) syncPanel();
    else { renderView(true); window.scrollTo(0, 0); }
    tick();
  });

  let resizeT = 0, lastW = window.innerWidth;
  window.addEventListener('resize', () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => { if (Math.abs(window.innerWidth - lastW) > 20) { lastW = window.innerWidth; renderView(false); } }, 200);
  });

  // ------------------------------------------------------------------------------------------- theme
  function theme(t) {
    if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
    S.store.set('theme', t || null);
  }
  document.getElementById('theme').addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    theme(cur === 'dark' ? 'light' : 'dark');
  });
  theme(S.store.get('theme', null));

  // ------------------------------------------------------------------------------------------- start
  document.addEventListener('DOMContentLoaded', () => {
    chrome();
    renderView(true);
    Promise.all(['state', 'reports', 'tunes', 'live', 'flow'].map((n) => poll(n))).then(() => { A.changed.add('state'); renderView(false); });
    tick();
  });
  A.poll = poll;
})();
