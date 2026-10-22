/* Strata Profile Studio: Compare.  Any set of runs against a reference: by default the baseline, the newest run
   made with the engine defaults.  Charts across the runs (decode and prompt read speed with the noise band, the change
   against the reference, the settings matrix, the decode window, the agent turns); with exactly two runs selected,
   also the full two-run comparison (sentences, sign test, window, settings, noise sources).
   Reads only the reports on disk: it works while a tune runs.
   #compare                      the saved selection
   #compare/<a>/<b>              exactly these two runs (A, B) */
'use strict';

(function () {
  const { el, svg, isNum } = S;
  S.viewDefs = S.viewDefs || {};
  const WORKLOADS = ['chat', 'long context', 'agent turns'];
  const READS = ['2K', '16K', '32K'];
  const ui = { sel: S.store.get('compare.sel', []), ref: S.store.get('compare.ref', 'baseline'), table: false };
  let token = 0;
  const save = () => { S.store.set('compare.sel', ui.sel); S.store.set('compare.ref', ui.ref); };

  const ok = (A) => A.reports.filter((r) => r.ok);
  const shortOf = (r) => JSON.stringify(r.settings_short || {});
  // the runs around the newest settings change: in run order, the last pair whose settings differ
  function lastChange(A) {
    const rs = ok(A).slice().sort((a, b) => (a.mtime || 0) - (b.mtime || 0));
    for (let i = rs.length - 1; i > 0; i--) if (shortOf(rs[i]) !== shortOf(rs[i - 1])) return [rs[i - 1].id, rs[i].id];
    return null;
  }
  const baseline = (A) => ok(A).find((r) => r.defaults) || null;
  const tunePair = (A) => {
    const t = A.tunes.find((x) => x.before_report && x.after_report && ok(A).find((r) => r.id === x.before_report) && ok(A).find((r) => r.id === x.after_report));
    return t ? [t.before_report, t.after_report] : null;
  };
  const COLORS = 8;
  const colorOf = (i) => `c${(i % COLORS) + 1}`;
  // one colour per run on every chart: the reference dark, the others in the categorical order
  const colorFor = (id, refId, sel) => (id === refId ? 'cref' : colorOf(sel.filter((x) => x !== refId).indexOf(id)));

  // ------------------------------------------------------------------------------------- the head
  function head(A, sec, base, refId, sel) {
    const rs = ok(A);
    const def = A.defaults || {};
    const why = def.state === 'running' ? 'A profile run with the engine defaults is going.' : A.readOnly() ? 'This Studio runs with --read-only.' : null;
    const runDefaults = async () => {
      const okk = await S.confirm({
        title: 'Run a profile with the engine defaults?',
        points: ['The Studio unloads Strata\'s model. Then the profile tool starts its own server from a copy of the run config without the calibrated settings.',
          'The run takes about 3 to 5 minutes (one model load and 16 requests).',
          'A request to Strata during the run loads the model again. Then the run fails.',
          'The run config file does not change. The report goes to the results folder.'],
        ok: 'Run with the engine defaults', danger: true,
      });
      if (!okk) return;
      try { await S.api('/api/run-defaults', { body: {} }); S.toast('The run with the engine defaults started.'); A.poll('defaults', true); } catch (e) { S.toast(e.message, 'bad'); }
    };
    const baseLine = base
      ? el('span', { class: 'chip ref' }, `Baseline: ${S.runName(base.id, A.reports)} (engine defaults, ${S.clock(base.mtime, true)})`)
      : el('span', { class: 'chip warn' }, 'No baseline: no run used the engine defaults');
    const add = el('select', { 'aria-label': 'Add a run' }, el('option', { value: '', text: 'Add a run' }),
      rs.filter((r) => !sel.includes(r.id)).map((r) => el('option', { value: r.id, text: `${S.runName(r.id, A.reports)} · ${S.clock(r.mtime, true)}${r.defaults ? ' · engine defaults' : ''}` })));
    add.addEventListener('change', () => { if (add.value) { ui.sel = [...sel, add.value]; save(); render(A); } });
    const refSel = el('select', { 'aria-label': 'Reference' },
      base ? el('option', { value: 'baseline', text: `Reference: the baseline (${S.runName(base.id, A.reports)})`, selected: ui.ref === 'baseline' }) : null,
      sel.filter((id) => !base || id !== base.id).map((id) => el('option', { value: id, text: `Reference: ${S.runName(id, A.reports)}`, selected: ui.ref === id })));
    refSel.addEventListener('change', () => { ui.ref = refSel.value; save(); render(A); });
    const lc = lastChange(A), tp = tunePair(A);
    const chips = el('div', { class: 'run-chips' }, sel.map((id) => el('span', { class: `chip run-chip ${colorFor(id, refId, sel)} ${id === refId ? 'is-ref' : ''}` },
      el('i', { class: 'sw' }), S.runName(id, A.reports), id === refId ? ' (reference)' : '',
      el('button', { type: 'button', class: 'chip-x', 'aria-label': `Remove ${S.runName(id, A.reports)}`, text: '×', onclick: () => { ui.sel = sel.filter((x) => x !== id); save(); render(A); } }))));
    const quick = el('div', { class: 'row-buttons' },
      add,
      S.button('All runs', () => { ui.sel = rs.map((r) => r.id).reverse(); save(); render(A); }, 'small'),
      lc ? S.button(`Last settings change (${S.runName(lc[0], A.reports)}, ${S.runName(lc[1], A.reports)})`, () => { ui.sel = lc.slice(); ui.ref = lc[0]; save(); render(A); }, 'small') : null,
      tp ? S.button(`Tune before and after (${S.runName(tp[0], A.reports)}, ${S.runName(tp[1], A.reports)})`, () => { ui.sel = tp.slice(); ui.ref = tp[0]; save(); render(A); }, 'small') : null,
      S.button('Clear', () => { ui.sel = base ? [base.id] : []; ui.ref = 'baseline'; save(); render(A); }, 'small'),
      refSel);
    const defRow = el('div', { class: 'row-buttons' },
      baseLine,
      S.button(base ? 'Run a new baseline with the engine defaults' : 'Run a profile with the engine defaults', runDefaults, base ? 'small' : 'primary small', { disabled: !!why, title: why || '' }),
      def.state === 'running' ? el('span', { class: 'muted small', text: `Defaults run: ${S.dur(def.elapsed_s)}. ${(def.lines || []).slice(-1)[0] || ''}` }) : null,
      def.state === 'failed' ? el('span', { class: 'warnline', text: `The last defaults run failed: ${def.error}` }) : null);
    return el('div', { class: 'compare-head' }, el('div', { class: 'view-head' }, el('h2', { text: 'Compare' }),
      el('span', { class: 'muted', text: `${sel.length} run${sel.length === 1 ? '' : 's'} selected · reference: ${S.runName(refId, A.reports)}` })), defRow, chips, quick);
  }

  // --------------------------------------------------------------------------------- the charts
  // grouped horizontal bars: per group (workload), one bar per run; the noise band around the reference value
  function grouped(groups, runs, get, floorOf, fmt, unit) {
    const box = el('div', { class: 'gbars' });
    const max = Math.max(1e-9, ...groups.flatMap((g) => runs.list.map((r) => get(r, g) || 0)));
    for (const g of groups) {
      const refV = get(runs.ref, g);
      const fl = floorOf(g);
      const gr = el('div', { class: 'gbar-group' }, el('div', { class: 'gbar-title', text: g }));
      const lane = el('div', { class: 'gbar-lane' });
      if (isNum(refV) && isNum(fl)) {
        const band = el('i', { class: 'gbar-band' });
        band.style.left = `${(refV * (1 - fl) / max) * 100}%`;
        band.style.width = `${(refV * 2 * fl / max) * 100}%`;
        S.hover(band, [`noise band of ${g}`, ['reference', `${fmt(refV)} ${unit}`], ['noise floor', `±${S.num(fl * 100, 1)} %`]]);
        lane.appendChild(el('div', { class: 'gbar-bandlayer' }, band));
      }
      for (const r of runs.list) {
        const v = get(r, g);
        const bar = el('div', { class: `gbar ${r.color} ${r.id === runs.ref.id ? 'is-ref' : ''}` });
        bar.style.width = `${isNum(v) ? Math.max(0.5, (v / max) * 100) : 0}%`;
        const val = el('span', { class: 'gbar-val num', text: isNum(v) ? `${fmt(v)} ${unit}` : 'not measured' });
        const row = el('div', { class: 'gbar-row' }, el('span', { class: 'gbar-name', text: r.name }), el('div', { class: 'gbar-track' }, bar, val));
        val.style.left = `calc(${isNum(v) ? (v / max) * 100 : 0}% + 6px)`;
        S.hover(row, [`${r.name} · ${g}`, [unit, isNum(v) ? fmt(v) : '–'], ['against the reference', isNum(v) && isNum(refV) && refV ? S.signed((v - refV) / refV * 100, 1, ' %') : '–']]);
        lane.appendChild(row);
      }
      gr.appendChild(lane);
      box.appendChild(gr);
    }
    return box;
  }

  // the decode speed of every agent turn, one line per run
  function turnLines(runs) {
    const W = 560, H = 230, m = { l: 40, r: 70, t: 12, b: 28 };
    const all = runs.list.flatMap((r) => (r.doc.agent || []).map((t) => t.decode_tps)).filter(isNum);
    const s = svg('svg', { class: 'lines', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Decode speed per agent turn' });
    if (!all.length) { s.appendChild(svg('text', { x: W / 2, y: H / 2, class: 'empty-svg', 'text-anchor': 'middle', text: 'No agent turns in these runs.' })); return s; }
    const turns = Math.max(...runs.list.map((r) => (r.doc.agent || []).length));
    const y0 = Math.min(...all) * 0.9, y1 = Math.max(...all) * 1.05;
    const X = (i) => m.l + (i / Math.max(1, turns - 1)) * (W - m.l - m.r), Y = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);
    for (const t of S.niceTicks(y0, y1, 4)) {
      s.appendChild(svg('line', { class: 'grid', x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
      s.appendChild(svg('text', { class: 'tick', x: m.l - 5, y: Y(t) + 4, 'text-anchor': 'end', text: S.num(t, 0) }));
    }
    for (let i = 0; i < turns; i++) s.appendChild(svg('text', { class: 'tick', x: X(i), y: H - 8, 'text-anchor': 'middle', text: `turn ${i}` }));
    for (const r of runs.list) {
      const pts = (r.doc.agent || []).map((t, i) => [i, t.decode_tps]).filter((p) => isNum(p[1]));
      if (!pts.length) continue;
      const g = svg('g', { class: `line-run ${r.color} ${r.id === runs.ref.id ? 'is-ref' : ''}` });
      g.appendChild(svg('path', { class: 'line-path', d: pts.map((p, k) => `${k ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join('') }));
      for (const p of pts) S.hover(g.appendChild(svg('circle', { class: 'line-dot', cx: X(p[0]), cy: Y(p[1]), r: 3.5 })), [`${r.name}, turn ${p[0]}`, ['decode', `${S.num(p[1], 1)} tok/s`]]);
      const lp = pts[pts.length - 1];
      g.appendChild(svg('text', { class: 'line-label', x: X(lp[0]) + 6, y: Y(lp[1]) + 4, text: r.short }));
      s.appendChild(g);
    }
    return s;
  }

  // parameters as rows, runs as columns, cells that differ from the reference highlighted
  function matrix(runs) {
    const flags = [];
    for (const r of runs.list) for (const row of r.doc.settings.rows || []) if (!flags.includes(row.flag)) flags.push(row.flag);
    const val = (r, f) => { const row = (r.doc.settings.rows || []).find((x) => x.flag === f); return row ? (row.config ?? null) : undefined; };
    const loaded = (r, f) => { const row = (r.doc.settings.rows || []).find((x) => x.flag === f); return row ? row.loaded : null; };
    const t = el('table', { class: 'data matrix' });
    t.appendChild(el('thead', null, el('tr', null, el('th', { text: 'Setting' }), runs.list.map((r) => el('th', { class: `${r.color} ${r.id === runs.ref.id ? 'is-ref' : ''}` }, el('i', { class: 'sw' }), r.short)))));
    const tb = el('tbody');
    const CAL = ['--pcie-frac', '--spec-min-p', '--pool-workers', '--adapt-every', '--adapt-swaps', '--adapt-decay'];
    for (const f of [...CAL.filter((x) => flags.includes(x)), ...flags.filter((x) => !CAL.includes(x))]) {
      const ref = val(runs.ref, f);
      const differs = runs.list.some((r) => !S.same(val(r, f) ?? 'default', ref ?? 'default') && (val(r, f) ?? null) !== (ref ?? null));
      const tr = el('tr', { class: differs ? 'differs' : '' }, el('td', { class: 'mono' }, f, CAL.includes(f) ? el('span', { class: 'chip small', text: 'calibrated' }) : null));
      for (const r of runs.list) {
        const v = val(r, f);
        const diff = r !== runs.ref && !((v ?? null) === (ref ?? null) || S.same(v, ref));
        const l = loaded(r, f);
        tr.appendChild(el('td', { class: `${diff ? 'cell-diff' : ''} ${v === null || v === undefined ? 'cell-default' : ''}`, title: l !== null && l !== undefined ? `loaded: ${l}` : '' }, v === null || v === undefined ? 'engine default' : v));
      }
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    return el('div', { class: 'table-wrap' }, t);
  }

  async function multi(A, sec, sel, refId, my) {
    const ids = [...new Set([refId, ...sel])];
    let docs, cmps;
    try {
      docs = await Promise.all(ids.map((id) => A.report(id)));
      cmps = await Promise.all(ids.map((id) => (id === refId ? null : A.compare(refId, id))));
    } catch (e) { if (my === token) S.toast(e.message, 'bad'); return null; }
    if (my !== token) return null;
    const list = ids.map((id, i) => ({ id, doc: docs[i], cmp: cmps[i], name: S.runName(id, A.reports), short: `run ${S.runNo(id, A.reports)}`, color: colorFor(id, refId, sel) }));
    const runs = { list, ref: list[0] };
    const dec = (r, w) => { const d = (r.doc.decode || []).find((x) => x.kind === w); return d ? d.tps.median : null; };
    const read = (r, g) => { const p = (r.doc.prompts || []).find((x) => x.label.startsWith(g)); return p ? p.prompt_tps : null; };
    // the noise floor of a workload: the largest floor the comparisons with the reference used
    const floorOf = (group, key, metric) => {
      const f = list.map((r) => r.cmp && r.cmp.rows.find((x) => x.group === group && String(x.key).startsWith(key) && x.metric === metric)).filter(Boolean).map((x) => x.floor);
      return f.length ? Math.max(...f) : null;
    };
    const legend = el('div', { class: 'legend' }, list.map((r) => el('span', { class: `lg ${r.color}` }, el('i', { class: 'sw' }), `${r.name}${r === runs.ref ? ' (reference)' : ''}`)),
      el('span', { class: 'lg' }, el('i', { class: 'sw band' }), 'noise band around the reference'));
    const cards = [];
    cards.push(S.card({ q: 'Decode speed per workload', sub: 'median, tok/s · higher is better · grey band: the reference ± its noise floor', cls: 'span2' },
      grouped(WORKLOADS, runs, dec, (w) => floorOf('decode', w, 'decode tok/s'), (v) => S.num(v, 1), 'tok/s'), legend));
    cards.push(S.card({ q: 'Prompt read speed per prompt size', sub: 'tok/s · higher is better · grey band: the reference ± its noise floor' },
      grouped(READS, runs, read, (g) => floorOf('prompt', g, 'prompt tok/s'), S.int, 'tok/s')));
    // with two runs the two-run section below says it: the change per workload, the settings difference, the window change
    const twoRuns = sel.length === 2;
    if (!twoRuns) {
      // change against the reference
      const frows = [];
      for (const w of WORKLOADS) for (const r of list) {
        if (!r.cmp) continue;
        const row = r.cmp.rows.find((x) => x.group === 'decode' && x.key === w && x.metric === 'decode tok/s');
        if (row) frows.push({ group: w, label: r.name, pct: row.pct, floor: row.floor * 100, assumed: row.floor_assumed, state: row.state, ab: `${S.num(row.a, 1)} to ${S.num(row.b, 1)} tok/s`, cls: r.color });
      }
      for (const r of list) {
        if (!r.cmp || !r.cmp.sign || r.cmp.sign.turns < r.cmp.sign.min_turns) continue;
        const sg = r.cmp.sign;
        if (sg.p !== null && sg.p <= 0.05) frows.push({ group: 'agent turns, sign test', label: r.name, pct: null, floor: null, state: sg.slower > sg.faster ? 'worse' : 'better', valText: `${Math.max(sg.slower, sg.faster)} of ${sg.turns}`, note: `${Math.max(sg.slower, sg.faster)} of ${sg.turns} turns ${sg.slower > sg.faster ? 'slower' : 'faster'}, p = ${S.num(sg.p, 3)}` });
      }
      cards.push(S.card({ q: `Change against the reference (${runs.ref.name})`, sub: 'decode speed, % · grey band: the noise floor · right is faster', cls: 'span2' },
        frows.length ? S.forest(frows) : el('p', { class: 'empty-line', text: 'Add a second run to compare it with the reference.' })));
    }
    cards.push(S.card({ q: 'Decode speed per agent turn', sub: 'tok/s · one line per run' }, turnLines(runs)));
    if (!twoRuns) cards.push(S.card({ q: 'Settings per run', sub: `run config values · highlighted: different from the reference (${runs.ref.name})`, cls: 'span3' }, matrix(runs)));
    // window composition per run per workload
    const wmax = Math.max(1, ...list.flatMap((r) => (r.doc.decode || []).map((d) => d.ms_per_window || 0)));
    if (!twoRuns) cards.push(S.card({ q: 'Decode window composition per run', sub: 'ms per window · lower is better', cls: 'span3' },
      el('div', { class: 'win-grid' }, WORKLOADS.map((w) => el('div', { class: 'win-col' }, el('div', { class: 'gbar-title', text: w }),
        list.map((r) => {
          const d = (r.doc.decode || []).find((x) => x.kind === w);
          return el('div', { class: 'req-row' }, el('div', { class: `req-label ${r.color}` }, el('i', { class: 'sw' }), el('span', { text: r.short })),
            d ? S.stack(S.DECODE_PARTS.map((k) => ({ key: k, value: (d.window_ms || {})[k] })), { scale: wmax, labels: false, total: `${S.num(d.ms_per_window, 1)} ms` }) : el('span', { class: 'muted small', text: 'not measured' }));
        })))), S.legend(S.DECODE_PARTS)));
    return cards;
  }

  // ------------------------------------------------------------------------- the two-run comparison
  async function pair(A, a, b, my) {
    let c, ra, rb;
    try { [c, ra, rb] = await Promise.all([A.compare(a, b), A.report(a), A.report(b)]); } catch (e) { return [el('p', { class: 'warnline', text: e.message })]; }
    if (my !== token) return null;
    const na = S.runName(a, A.reports), nb = S.runName(b, A.reports);
    const row = (g, k, m) => c.rows.find((r) => r.group === g && String(r.key) === String(k) && r.metric === m);
    const sign = c.sign || {};
    const changed = c.settings.filter((s) => s.changed);
    const lines = c.verdicts.map((v) => el('li', { class: `v-${v.state}` }, el('b', { text: v.state === 'better' ? 'faster' : v.state === 'worse' ? 'slower' : v.state === 'missing' ? 'missing' : 'no change' }), ' ', v.text));
    if (sign.turns >= sign.min_turns && sign.p !== null) {
      lines.push(el('li', { class: sign.p <= 0.05 ? (sign.slower > sign.faster ? 'v-worse' : 'v-better') : 'v-same' }, el('b', { text: 'sign test' }),
        ` ${sign.slower} of ${sign.turns} agent turns are slower and ${sign.faster} are faster. A coin gives a split this uneven ${S.pct(sign.p, 1)} of the time (two-sided).${sign.p <= 0.05 ? ' The change is real, although each turn stays inside its own noise.' : ''}`));
    }
    const tile = (w) => {
      const r = row('decode', w, 'decode tok/s');
      const v = c.verdicts.find((x) => x.group === 'decode' && x.key === w);
      const st = v ? v.state : 'missing';
      return S.tile(`Decode speed, ${w}`, r && isNum(r.pct) ? S.signed(r.pct, 1) : '–', r && isNum(r.pct) ? '%' : '',
        r ? `${S.num(r.a, 1)} to ${S.num(r.b, 1)} tok/s · ${st === 'same' ? `inside the ±${(Math.floor(r.floor * 1000 + 1e-6) / 10).toFixed(1)} % noise floor` : st === 'better' ? 'faster, outside the noise' : st === 'worse' ? 'slower, outside the noise' : 'not in both'}` : 'not in both runs', `state-${st}`);
    };
    const tiles = el('div', { class: 'tiles span3' }, tile('chat'), tile('long context'), tile('agent turns'),
      S.tile('Agent turns in one direction', sign.turns ? `${Math.max(sign.faster, sign.slower)} of ${sign.turns}` : '–', '',
        sign.turns ? `${sign.slower > sign.faster ? 'slower' : 'faster'} · chance: 1 in ${sign.p ? Math.round(1 / sign.p) : '–'} (sign test)` : 'no agent turns in both', sign.turns && sign.p !== null && sign.p <= 0.05 ? (sign.slower > sign.faster ? 'state-worse' : 'state-better') : ''),
      S.tile('Settings changed', String(changed.length), '', changed.slice(0, 3).map((s) => s.flag.replace(/^--/, '')).join(', ')));
    const answer = S.card({ q: `Result: ${nb} against ${na}`, sub: 'a change counts only when it is larger than the noise floor of this PC', cls: 'span3' },
      c.warnings.length ? el('p', { class: 'warnline', text: `Different conditions: ${c.warnings.join(', ')}.` }) : null, el('ul', { class: 'verdicts' }, lines));
    const frows = [];
    for (const w of WORKLOADS) { const r = row('decode', w, 'decode tok/s'); if (r) frows.push({ group: 'decode', label: w, pct: r.pct, floor: r.floor * 100, assumed: r.floor_assumed, state: r.state, ab: `${S.num(r.a, 1)} to ${S.num(r.b, 1)} tok/s` }); }
    for (const r of c.rows.filter((x) => x.group === 'agent' && x.metric === 'decode tok/s')) frows.push({ group: 'agent turns', label: `turn ${r.key}`, pct: r.pct, floor: r.floor * 100, assumed: r.floor_assumed, state: r.state, ab: `${S.num(r.a, 1)} to ${S.num(r.b, 1)} tok/s`, onclick: () => S.go(`#runs/${b}/r/agent-${r.key}`) });
    for (const r of c.rows.filter((x) => x.group === 'prompt' && x.metric === 'prompt tok/s')) frows.push({ group: 'prompt reads', label: `${r.key}`, pct: r.pct, floor: r.floor * 100, assumed: r.floor_assumed, state: r.state, ab: `${S.int(r.a)} to ${S.int(r.b)} tok/s` });
    const forest = S.card({ q: `Change per workload: ${nb} against ${na}`, sub: 'tok/s, % · grey band: the noise floor · right is faster', cls: 'span2' }, S.forest(frows));
    const da = Object.fromEntries((ra.decode || []).map((d) => [d.kind, d])), db = Object.fromEntries((rb.decode || []).map((d) => [d.kind, d]));
    const wmax = Math.max(...[...ra.decode, ...rb.decode].map((d) => d.ms_per_window || 0), 1);
    const why = [];
    for (const w of WORKLOADS) {
      if (!da[w] || !db[w]) continue;
      const deltas = S.DECODE_PARTS.map((k) => [k, ((db[w].window_ms || {})[k] || 0) - ((da[w].window_ms || {})[k] || 0)]).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]));
      why.push(el('div', { class: 'why' },
        el('div', { class: 'why-head' }, el('b', { text: w }), el('span', { text: `window ${S.signed(db[w].ms_per_window - da[w].ms_per_window, 1, ' ms')} (${deltas.slice(0, 2).map(([k, d]) => `${S.PART_LABEL[k]} ${S.signed(d, 1, ' ms')}`).join(', ')}) · tokens per window ${S.num(da[w].tokens_per_window, 2)} to ${S.num(db[w].tokens_per_window, 2)}` })),
        el('div', { class: 'req-row' }, el('div', { class: 'req-label', text: 'A' }), S.stack(S.DECODE_PARTS.map((k) => ({ key: k, value: (da[w].window_ms || {})[k] })), { scale: wmax, short: true, fmt: (v) => S.num(v, 1), total: `${S.num(da[w].ms_per_window, 1)} ms` })),
        el('div', { class: 'req-row' }, el('div', { class: 'req-label', text: 'B' }), S.stack(S.DECODE_PARTS.map((k) => ({ key: k, value: (db[w].window_ms || {})[k] })), { scale: wmax, short: true, fmt: (v) => S.num(v, 1), total: `${S.num(db[w].ms_per_window, 1)} ms` }))));
    }
    const whyCard = S.card({ q: 'Decode window change', sub: 'ms per window, A above B · a longer window is not slower when it carries more tokens' }, why, S.legend(S.DECODE_PARTS));
    const setCard = S.card({ cls: 'span2', q: 'Settings difference', sub: `${changed.length} setting${changed.length === 1 ? '' : 's'} changed${c.warnings.length ? ' · ' + c.warnings.join(', ') : ''}` },
      changed.length ? el('div', { class: 'diffs' }, changed.map((s) => el('div', { class: 'diff' }, el('b', { text: s.flag }), el('span', { class: 'from', text: s.a ?? 'engine default' }), el('span', { text: 'to' }), el('span', { class: 'to', text: s.b ?? 'engine default' })))) : el('p', { class: 'empty-line', text: 'The settings are the same. B against A measures the noise.' }),
      el('p', { class: 'muted small', text: `Unchanged: ${c.settings.filter((s) => !s.changed && (s.a || s.b)).map((s) => `${s.flag} ${s.a}`).join(' · ')}` }));
    const noiseCard = S.card({ q: 'Noise floor sources', sub: c.floors.pairs.length ? `the largest difference between runs with the same settings, per metric, never below ${S.pct(c.floors.minimum)}` : 'no two runs have the same settings: the floors are assumed' },
      c.floors.pairs.length ? el('ul', { class: 'pairs' }, c.floors.pairs.map((p) => el('li', null, el('a', { href: `#compare/${p.a}/${p.b}`, text: p.label })))) :
        el('p', { class: 'empty-line', text: `Assumed: ${S.pct(c.floors.assumed.tps)} for a speed, ${c.floors.assumed.ms_part} ms for a window part, ${c.floors.assumed.hit_points * 100} points for a hit rate. Run the profile two times with the same settings to measure them.` }));
    const cols = [{ label: 'Group', key: 'group' }, { label: 'Item', key: 'key' }, { label: 'Metric', key: 'metric' }, { label: 'A', num: true, get: (r) => S.num(r.a, 2) }, { label: 'B', num: true, get: (r) => S.num(r.b, 2) },
      { label: 'Change', num: true, get: (r) => (isNum(r.pct) ? S.signed(r.pct, 1, ' %') : '–') }, { label: 'Noise floor', num: true, get: (r) => (r.floor_kind === 'rel' ? `±${S.num(r.floor * 100, 1)} %` : r.floor_kind === 'pts' ? `±${S.num(r.floor, 1)} points` : `±${S.num(r.floor, 1)} ms`) }, { label: 'Result', key: 'state' }];
    const more = el('div', { class: 'span3 more-row' }, S.button(ui.table ? 'Hide all numbers' : `All numbers (${c.rows.length} rows)`, () => { ui.table = !ui.table; render(A); }, 'more'));
    const table = ui.table ? S.card({ q: 'All numbers', sub: 'A, B, the change and the noise floor of each metric', cls: 'span3', tools: el('div', { class: 'card-tools' }, S.button('Download CSV', () => S.csv(`compare-${S.runNo(a, A.reports)}-${S.runNo(b, A.reports)}.csv`, cols, c.rows), 'small')) },
      S.table(cols, c.rows.map((r) => Object.assign({ _cls: `st-${r.state}` }, r)))) : null;
    return [el('h3', { class: 'section-title span3', text: `Two-run comparison: A = ${na}, B = ${nb}` }), tiles, answer, forest, whyCard, setCard, noiseCard, more, table];
  }

  async function render(A) {
    const my = ++token;
    const sec = document.getElementById('view-compare');
    const rs = ok(A);
    const r = A.route;
    if (r.parts[1] && r.parts[2] && rs.find((x) => x.id === r.parts[1]) && rs.find((x) => x.id === r.parts[2])) {
      ui.sel = [r.parts[1], r.parts[2]]; ui.ref = r.parts[1]; save();
      history.replaceState(null, '', '#compare');
      A.route = S.route();
    }
    if (!rs.length) {
      S.fill(sec, el('div', { class: 'view-head' }, el('h2', { text: 'Compare' })), el('section', { class: 'card empty-card' }, el('h3', { text: 'There is no run to compare yet.' }),
        el('p', { class: 'sub', text: 'Run a profile on the Runs page. A run with the engine defaults gives the baseline.' })));
      return;
    }
    const base = baseline(A);
    ui.sel = ui.sel.filter((id) => rs.find((x) => x.id === id));
    if (!ui.sel.length) {
      const tp = tunePair(A);
      ui.sel = [...new Set([...(base ? [base.id] : []), ...(tp || [rs[0].id])])];
      save();
    }
    if (ui.ref !== 'baseline' && !ui.sel.includes(ui.ref)) ui.ref = 'baseline';
    const refId = ui.ref === 'baseline' && base ? base.id : (ui.ref === 'baseline' ? ui.sel[0] : ui.ref);
    if (!sec.querySelector('.compare-head')) S.fill(sec, el('p', { class: 'loading', text: 'Reading the reports …' }));
    const top = head(A, sec, base, refId, ui.sel);
    const cards = await multi(A, sec, ui.sel, refId, my);
    if (!cards) return;
    const others = ui.sel.filter((id) => id !== refId || ui.sel.length === 2);
    let pairCards = [];
    if (ui.sel.length === 2) {
      const [x, y] = ui.sel.slice().sort((p, q) => ((rs.find((r2) => r2.id === p) || {}).mtime || 0) - ((rs.find((r2) => r2.id === q) || {}).mtime || 0));
      pairCards = (await pair(A, x, y, my)) || [];
      if (my !== token) return;
    }
    // two runs: the answer first (the two-run section), then the speeds of both runs; the rest of the cards are for 3 or more
    S.fill(sec, top, el('div', { class: 'grid' }, ...(pairCards.length ? [pairCards, cards] : [cards])));
    void others;
  }

  S.viewDefs.compare = { render };
})();
