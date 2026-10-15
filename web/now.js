/* Strata Profile Studio: Now.  How fast is it, where does the time go, which part of the PC is the limit, what is
   trending.  The PC diagram is built once and updated every second; the rest is drawn again when a request ends. */
'use strict';

(function () {
  const { el, isNum } = S;
  S.viewDefs = S.viewDefs || {};

  const ui = {
    scope: S.store.get('now.scope', 'last'),            // last | window
    n: S.store.get('now.n', 50),                         // the window: 30 | 50 | 100 | 300
    x: S.store.get('now.x', 'order'),
    y: S.store.get('now.y', 'decode'),
    table: false,
  };
  const Y = {
    decode: { label: 'decode tok/s', get: (s) => (s.generated > 1 ? s.decode_tok_s : null), fmt: (v) => S.num(v, 0), better: 'higher' },
    prompt: { label: 'prompt read tok/s', get: (s) => s.prompt_tok_s, fmt: (v) => S.int(v), better: 'higher' },
    window: { label: 'ms per decode window', get: (s) => (s.generated > 1 ? s.ms_per_window : null), fmt: (v) => S.num(v, 0), better: 'lower' },
    hit: { label: 'expert cache hit rate', get: (s) => (isNum(s.hit_rate) ? s.hit_rate * 100 : null), fmt: (v) => `${S.num(v, 0)}%`, better: 'higher' },
  };
  const X = {
    order: { label: 'requests in order (gaps over 5 min marked)', get: (s) => s._i },
    time: { label: 'time', get: (s) => s.seen_at },
    context: { label: 'prompt tokens (log)', get: (s) => s.prompt_tokens, log: true },
    hit: { label: 'expert cache hit rate %', get: (s) => (isNum(s.hit_rate) ? s.hit_rate * 100 : null) },
  };

  let built = false, diagramNode = null;
  const holders = {};

  // the calibration's own requests (and the Studio's reload request) are not the user's: drawn, never counted
  const isCalib = (s) => !!(s.run && (s.run.kind === 'tune' || s.run.kind === 'reload'));

  function windowSamples(all) {
    return ui.n >= 300 ? all : all.slice(-ui.n);
  }
  function newest(all) {
    for (let i = all.length - 1; i >= 0; i--) if (isNum(all[i].decode_ms) || isNum(all[i].prompt_ms)) return all[i];
    return null;
  }
  function biggestPart(s) {
    if (!s || !s.window_ms) return null;
    const top = Object.entries(s.window_ms).filter(([k]) => k !== 'other').sort((a, b) => b[1] - a[1])[0];
    return top && isNum(s.ms_per_window) ? { key: top[0], ms: top[1], share: top[1] / s.ms_per_window } : null;
  }
  function spanLabel(sp, reports) {
    if (sp.kind === 'profile') return sp.report ? `profile ${S.runName(sp.report, reports)}` : 'profile run';
    if (sp.kind === 'tune') return 'calibration (model unloaded)';
    if (sp.kind === 'reload') return 'reload after the tune';
    return sp.kind;
  }
  function workersNote(A) {
    const t = (A.tunes || []).find((x) => x.candidates && x.candidates.steps);
    const st = t && t.candidates.steps.find((s) => s.key === 'pool_workers');
    if (!st) return null;
    const vals = st.values.filter((v) => isNum(v.median));
    const top = Math.max(...vals.map((v) => v.median));
    const near = vals.filter((v) => v.median >= top * 0.97);
    if (near.length < 2) return null;
    return `the calibration measured ${near.map((v) => v.value).join(', ')} workers all within 3% (${S.num(Math.min(...near.map((v) => v.median)), 1)}–${S.num(top, 1)} tok/s)`;
  }

  // the icicle levels of one request, or of a set of requests summed
  function icicleLevels(list, title) {
    let prompt = 0, decode = 0;
    const pp = {}, dp = {};
    for (const s of list) {
      for (const p of S.promptParts(s)) { pp[p.key] = (pp[p.key] || 0) + p.value; prompt += p.value; }
      for (const p of S.decodeParts(s)) { dp[p.key] = (dp[p.key] || 0) + p.value; decode += p.value; }
    }
    const total = prompt + decode;
    if (total <= 0) return null;
    const order = (o, keys) => keys.filter((k) => o[k] > 0).map((k) => ({ key: k, value: o[k] }));
    let at = 0;
    const l2 = [];
    for (const p of order(pp, [...S.PROMPT_GROUPS, 'host', 'not split'])) { l2.push({ ...p, start: at }); at += p.value; }
    for (const p of order(dp, [...S.DECODE_PARTS, 'not split'])) { l2.push({ ...p, start: at, label: p.key === 'not split' ? 'decode, not split' : undefined }); at += p.value; }
    const max = l2.reduce((m, s) => (s.value > m.value ? s : m), l2[0]);
    if (max) max.hl = true;
    return {
      total,
      levels: [
        [{ key: 'all', label: title, value: total, start: 0, cls: 'k-all', pct: false, about: '' }],
        [{ key: 'prompt', label: 'prompt read', value: prompt, start: 0, cls: 'k-phase', about: 'reading the prompt into the KV cache' },
          { key: 'decode', label: 'decode', value: decode, start: prompt, cls: 'k-phase', about: 'generating the answer, window by window' }],
        l2,
      ],
      prompt, decode,
    };
  }

  function tiles(A, all, win, last) {
    const box = el('div', { class: 'tiles' });
    if (!last) return box;
    const med = S.median(win.filter((s) => s.generated > 1).map((s) => s.decode_tok_s));
    const d = isNum(last.decode_tok_s) && isNum(med) && last.generated > 1 ? (last.decode_tok_s - med) / med * 100 : null;
    const big = biggestPart(last);
    const src = S.expertSources(last);
    const lv = icicleLevels(win, '');
    box.append(
      S.tile('decode · last request', last.generated > 1 ? S.tps(last.decode_tok_s) : '–', 'tok/s',
        isNum(d) ? `${S.signed(d, 0, '%')} against the median ${S.num(med, 1)} of the last ${win.length}` : `${S.int(last.generated)} token out`, 'hero'),
      S.tile('prompt read · last request', S.tps(last.prompt_tok_s), 'tok/s',
        isNum(last.prompt_tokens) ? `${S.int(last.prompt_tokens)} tokens${last.reused ? `, ${S.int(last.reused)} reused` : ''}` : ''),
      S.tile('expert cache hit', S.pct(last.hit_rate), '', src ? `CPU ${S.pct(src.cpu)} · PCIe ${S.pct(src.pcie)}` : ''),
      S.tile('the limit · last decode', big ? S.PART_LABEL[big.key] : '–', '', big ? `${S.pct(big.share)} of the ${S.num(last.ms_per_window, 1)} ms window` : 'no window split', big ? `limit ${S.PART_CLASS[big.key]}` : ''),
      S.tile(`engine time · last ${win.length}`, lv ? S.ms(lv.total) : '–', '', lv ? `prompt reads ${S.pct(lv.prompt / lv.total)} · decode ${S.pct(lv.decode / lv.total)}` : ''));
    return box;
  }

  function trendCard(A, win, all) {
    const host = el('div', { class: 'chart-host' });
    if (!X[ui.x]) ui.x = 'order';
    const yo = Y[ui.y], xo = X[ui.x];
    const reports = A.reports;
    win.forEach((s, i) => { s._i = i; s._cut = i > 0 && isNum(s.seen_at) && isNum(win[i - 1].seen_at) && s.seen_at - win[i - 1].seen_at > 300; });
    const pts = win.map((s) => ({ s, x: xo.get(s), y: yo.get(s) })).filter((p) => isNum(p.x) && isNum(p.y));
    pts.forEach((p, i) => { p.cut = ui.x === 'order' && i > 0 && isNum(p.s.seen_at) && isNum(pts[i - 1].s.seen_at) && p.s.seen_at - pts[i - 1].s.seen_at > 300; });
    // outliers: beyond the 10th (or 90th) percentile of their own kind, with at least 8 of that kind
    const byKind = {};
    for (const p of pts) if (!isCalib(p.s)) (byKind[p.s.kind] = byKind[p.s.kind] || []).push(p.y);
    const nCalib = pts.filter((p) => isCalib(p.s)).length;
    const lim = {};
    for (const [k, v] of Object.entries(byKind)) if (v.length >= 8) lim[k] = yo.better === 'higher' ? S.quantile(v, 0.1) : S.quantile(v, 0.9);
    const last = [...all].reverse().find((s) => !isCalib(s));
    const points = pts.map((p) => ({
      x: p.x, y: p.y, cut: p.cut, kind: p.s.kind, hl: p.s === last, calib: isCalib(p.s),
      out: !isCalib(p.s) && isNum(lim[p.s.kind]) && (yo.better === 'higher' ? p.y < lim[p.s.kind] : p.y > lim[p.s.kind]),
      run: !!p.s.run,
      onclick: () => S.go(`#now/r/${p.s.seq}`),
      tip: () => [`${S.KIND_LABEL[p.s.kind]} · ${p.s.late ? 'before the Studio started' : S.clock(p.s.seen_at, 'sec')}`,
        [yo.label, yo.fmt(p.y)], ['prompt', `${S.int(p.s.prompt_tokens)} tokens`], ['hit rate', S.pct(p.s.hit_rate)],
        p.s.report_request ? `${p.s.report_request.label} of ${S.runName(p.s.report_request.report, reports)}` : (p.s.run ? spanLabel(p.s.run, reports) : ''), 'click to open'],
    }));
    const outs = points.filter((p) => p.out).length;
    const short = (sp) => (sp.kind === 'profile' && sp.report ? `run ${S.runNo(sp.report, reports)}` : sp.kind === 'tune' ? 'calibration' : sp.kind === 'reload' ? 'reload' : '');
    let bands = [], gaps = [], xTicks = null;
    if (ui.x === 'time') bands = (A.spans || []).map((sp) => ({ ...sp, label: spanLabel(sp, reports), short: short(sp) }));
    if (ui.x === 'order') {
      xTicks = win.filter((s, i) => i === 0 || s._cut).map((s) => ({ x: s._i, label: s.late ? 'before start' : S.clock(s.seen_at) }));
      for (const sp of A.spans || []) {
        const idx = win.filter((s) => !s.late && s.seen_at >= sp.from && s.seen_at <= sp.to).map((s) => s._i);
        if (idx.length) bands.push({ from: Math.min(...idx) - 0.45, to: Math.max(...idx) + 0.45, kind: sp.kind, report: sp.report, label: spanLabel(sp, reports), short: short(sp) });
        else if (sp.kind === 'tune') {
          const before = win.filter((s) => !s.late && s.seen_at < sp.from).map((s) => s._i);
          const after = win.filter((s) => !s.late && s.seen_at > sp.to).map((s) => s._i);
          if (before.length && after.length) gaps.push({ x: Math.max(...before) + 0.5, kind: 'tune', label: `calibration · ${S.dur(sp.to - sp.from)}`, about: 'the model was unloaded: no requests' });
        }
      }
      // one band per stretch of requests: a report's span wins over an unnamed one it overlaps
      bands = bands.filter((b, i) => !bands.some((o, j) => j !== i && o.from < b.to && b.from < o.to &&
        ((o.report && !b.report) || (!!o.report === !!b.report && (o.to - o.from > b.to - b.from || (o.to - o.from === b.to - b.from && j < i))))));
    }
    const tools = el('div', { class: 'card-tools' },
      S.seg([['decode', 'decode'], ['prompt', 'prompt'], ['window', 'window'], ['hit', 'hit rate']], ui.y, (v) => { ui.y = v; S.store.set('now.y', v); draw(A); }, 'y axis'),
      el('span', { class: 'tools-sep', text: 'against' }),
      S.seg([['order', 'order'], ['time', 'clock'], ['context', 'context'], ['hit', 'hit rate']], ui.x, (v) => { ui.x = v; S.store.set('now.x', v); draw(A); }, 'x axis'));
    const legend = el('div', { class: 'legend' },
      S.KINDS.map((k) => el('span', { class: 'lg', title: S.KIND_ABOUT[k] }, S.kindIcon(k), `${S.KIND_LABEL[k]} (${(byKind[k] || []).length})`)),
      el('span', { class: 'lg' }, el('i', { class: 'sw ring' }), `outlier for its kind (${outs})`),
      nCalib ? el('span', { class: 'lg', title: 'requests the calibration sent while it measured: not counted anywhere else on this page' }, el('i', { class: 'sw calib-mark' }), `calibration's own (${nCalib})`) : null,
      ui.x === 'time' || ui.x === 'order' ? el('span', { class: 'lg' }, el('i', { class: 'sw band-profile' }), 'profile run') : null,
      ui.x === 'time' ? el('span', { class: 'lg' }, el('i', { class: 'sw band-tune' }), 'calibration') : ui.x === 'order' && gaps.length ? el('span', { class: 'lg' }, el('i', { class: 'sw gap' }), 'calibration (no requests)') : null,
      el('span', { class: 'lg' }, el('i', { class: 'sw median' }), 'rolling median'));
    const q = { decode: 'How fast does each request decode?', prompt: 'How fast does each prompt read?', window: 'How long is each decode window?', hit: 'How often do the experts hit the VRAM cache?' }[ui.y];
    const card = S.card({ q: ui.x === 'time' || ui.x === 'order' ? q : ui.x === 'context' ? `${q.replace('?', '')} by context size?` : `Does the ${yo.label} follow the hit rate?`,
      sub: `${yo.label} · ${yo.better} is better · the last ${pts.filter((p) => !isCalib(p.s)).length} requests${nCalib ? `, and the calibration's ${nCalib} among them` : ''} · click a mark to open it`, tools, cls: 'span2' }, host, legend);
    requestAnimationFrame(() => S.scatter(host, points, { xLog: xo.log, xTime: ui.x === 'time', bands, gaps, xTicks, median: ui.x === 'time' || ui.x === 'order', yFmt: yo.fmt, xFmt: ui.x === 'hit' ? (v) => `${v}%` : S.int, xLabel: xo.label, height: 280, y0: 0 }));
    return card;
  }

  function kindCard(A, win) {
    const rows = S.KINDS.filter((k) => k !== 'prompt only').map((k) => {
      const v = win.filter((s) => s.kind === k && s.generated > 1).map((s) => s.decode_tok_s).filter(isNum);
      return { label: S.KIND_LABEL[k], icon: S.kindIcon(k), value: S.median(v), dots: v, note: `${v.length} requests`, title: S.KIND_ABOUT[k] };
    });
    return S.card({ q: 'Which kind of request decodes slowest?', sub: 'median decode tok/s per kind, each dot one request · higher is better' },
      S.bars(rows, { sort: 'asc', fmt: (v) => S.num(v, 1), unit: 'tok/s' }));
  }

  function windowCard(A, scopeList, last) {
    let parts, title, sub;
    if (ui.scope === 'last') {
      parts = last && last.window_ms ? S.DECODE_PARTS.map((k) => ({ k, v: last.window_ms[k] })) : [];
      title = last && isNum(last.ms_per_window) ? `${S.num(last.ms_per_window, 1)} ms per window, ${S.num(last.tokens_per_window, 2)} tokens each` : '';
    } else {
      const ws = scopeList.filter((s) => s.window_ms && isNum(s.ms_per_window));
      parts = S.DECODE_PARTS.map((k) => ({ k, v: ws.length ? S.sum(ws.map((s) => s.window_ms[k])) / ws.length : null }));
      title = ws.length ? `mean of ${ws.length} requests: ${S.num(S.sum(ws.map((s) => s.ms_per_window)) / ws.length, 1)} ms per window` : '';
    }
    const top = parts.filter((p) => isNum(p.v) && p.k !== 'other').sort((a, b) => b.v - a.v)[0];
    sub = `${title || 'ms per window'} · lower is better`;
    return S.card({ q: 'What is the decode window made of?', sub },
      S.bars(parts.map((p) => ({ label: S.PART_LABEL[p.k], value: p.v, cls: S.PART_CLASS[p.k], hl: top && p.k === top.k, title: S.PART_ABOUT[p.k],
        tip: [S.PART_LABEL[p.k], ['ms per window', S.num(p.v, 2)], S.PART_ABOUT[p.k]] })), { fmt: (v) => S.num(v, 1), unit: 'ms', empty: 'No decode window split (profiling off, or no request yet).' }));
  }

  function expertCard(A, scopeList, last) {
    let src, n = 1;
    if (ui.scope === 'last') src = last && S.expertSources(last);
    else {
      const xs = scopeList.map(S.expertSources).filter(Boolean);
      n = xs.length;
      src = xs.length ? { vram: S.sum(xs.map((x) => x.vram)) / n, cpu: S.sum(xs.map((x) => x.cpu)) / n, pcie: S.sum(xs.map((x) => x.pcie)) / n } : null;
    }
    return S.card({ q: 'Where did the experts come from?', sub: `share of the routed experts${ui.scope === 'last' ? ', last request' : `, mean of ${n} requests`} · more from VRAM is faster` },
      src ? S.bars([
        { label: 'VRAM expert cache', value: src.vram, cls: 'k-gpu', text: S.pct(src.vram, 1) },
        { label: 'computed by the CPU', value: src.cpu, cls: 'k-cpu', text: S.pct(src.cpu, 1) },
        { label: 'read over PCIe', value: src.pcie, cls: 'k-pcie', text: S.pct(src.pcie, 1) }], { max: 1, sort: false })
        : el('p', { class: 'empty-line', text: 'No hit rate yet.' }));
  }

  function slowCard(A, win) {
    const byKind = {};
    for (const s of win) if (s.generated > 1 && isNum(s.decode_tok_s)) (byKind[s.kind] = byKind[s.kind] || []).push(s.decode_tok_s);
    const med = Object.fromEntries(Object.entries(byKind).map(([k, v]) => [k, S.median(v)]));
    const rows = win.filter((s) => s.generated > 1 && isNum(s.decode_tok_s) && isNum(med[s.kind]))
      .map((s) => ({ s, gap: (s.decode_tok_s - med[s.kind]) / med[s.kind] * 100 }))
      .sort((a, b) => a.gap - b.gap).slice(0, 6);
    return S.card({ q: 'Which requests were slowest for their kind?', sub: 'decode tok/s against the median of the same kind · click to see why', cls: 'span2' },
      S.bars(rows.map(({ s, gap }) => ({
        label: s.report_request ? `${s.report_request.label} · run ${S.runNo(s.report_request.report, A.reports)}` : `#${s.seq} · ${S.clock(s.seen_at)}`,
        icon: S.kindIcon(s.kind), value: s.decode_tok_s, text: `${S.num(s.decode_tok_s, 1)} tok/s · ${S.signed(gap, 0, '%')}`,
        note: `hit ${S.pct(s.hit_rate)} · ${S.int(s.prompt_tokens)} tokens`, cls: 'k-bar', onclick: () => S.go(`#now/r/${s.seq}`),
      })), { sort: false, empty: 'No decoded request yet.' }));
  }

  function tableCard(A, win) {
    const cols = [
      { label: '#', key: 'seq', num: true }, { label: 'time', get: (s) => (s.late ? 'before start' : S.clock(s.seen_at, 'sec')), raw: (s) => s.seen_at },
      { label: 'kind', get: (s) => S.KIND_LABEL[s.kind] }, { label: 'run', get: (s) => (s.report_request ? `${s.report_request.label}, run ${S.runNo(s.report_request.report, A.reports)}` : s.run ? s.run.kind : '') },
      { label: 'prompt tokens', key: 'prompt_tokens', num: true, get: (s) => S.int(s.prompt_tokens), raw: (s) => s.prompt_tokens },
      { label: 'reused', num: true, get: (s) => S.int(s.reused), raw: (s) => s.reused },
      { label: 'prompt tok/s', num: true, get: (s) => S.tps(s.prompt_tok_s), raw: (s) => s.prompt_tok_s },
      { label: 'prompt ms', num: true, get: (s) => S.int(s.prompt_ms), raw: (s) => s.prompt_ms },
      { label: 'out', num: true, get: (s) => S.int(s.generated), raw: (s) => s.generated },
      { label: 'decode tok/s', num: true, get: (s) => S.tps(s.decode_tok_s), raw: (s) => s.decode_tok_s },
      { label: 'ms/window', num: true, get: (s) => S.num(s.ms_per_window, 1), raw: (s) => s.ms_per_window },
      ...S.DECODE_PARTS.map((k) => ({ label: k, num: true, get: (s) => (s.window_ms ? S.num(s.window_ms[k], 1) : '–'), raw: (s) => (s.window_ms ? s.window_ms[k] : '') })),
      { label: 'hit', num: true, get: (s) => S.pct(s.hit_rate, 1), raw: (s) => s.hit_rate },
      { label: 'PCIe', num: true, get: (s) => S.pct(s.pcie_share, 1), raw: (s) => s.pcie_share },
      { label: 'KV VRAM', num: true, get: (s) => S.pct(s.kv_hit, 1), raw: (s) => s.kv_hit },
      { label: 'drafts', num: true, get: (s) => (isNum(s.drafts_offered) ? `${s.drafts_accepted}/${s.drafts_offered}` : '–'), raw: (s) => s.drafts_accepted },
    ];
    const rows = win.slice().reverse().map((s) => Object.assign({}, s, { _onclick: () => S.go(`#now/r/${s.seq}`) }));
    return S.card({ q: `All numbers: the last ${win.length} requests`, sub: 'newest first · click a row to open it', cls: 'span3',
      tools: el('div', { class: 'card-tools' }, S.button('Download CSV', () => S.csv('studio-requests.csv', cols, win), 'small')) }, S.table(cols, rows));
  }

  function empty(A) {
    const why = A.cannotRun();
    const s = A.strata();
    return el('section', { class: 'card empty-card span3' },
      el('h3', { text: s.status === 'unreachable' ? 'No requests: Strata does not answer.' : 'No requests since Strata started.' }),
      el('p', { class: 'sub', text: s.status === 'unreachable' ? 'The PC diagram and this view fill in when Strata is back.' : 'Send a chat to Strata, or run a profile (about 50 s, 16 requests): each request appears here as it ends.' }),
      el('div', { class: 'row-buttons' },
        S.button('Run a profile', () => A.startProfile(), 'primary', { disabled: !!why, title: why || '' }),
        S.button('Open the runs on disk', () => S.go('#runs'))),
      why ? el('p', { class: 'note', text: why }) : null);
  }

  function draw(A) {
    const every = A.samples();
    const all = every.filter((s) => !isCalib(s));
    const win = windowSamples(all);
    // the trend covers the same stretch of time as the window, with the calibration's requests in it
    const winAll = ui.n >= 300 ? every : every.slice(win.length ? every.indexOf(win[0]) : Math.max(0, every.length - ui.n));
    const last = newest(all);
    const scopeList = ui.scope === 'last' ? (last ? [last] : []) : win;
    S.fill(holders.tiles, tiles(A, all, win, last));
    const grid = holders.grid;
    S.clear(grid);
    if (!all.length && !every.length) { grid.appendChild(empty(A)); return; }
    if (!last) { grid.append(empty(A), trendCard(A, winAll, every)); return; }
    const lv = icicleLevels(scopeList, ui.scope === 'last' ? `${last.report_request ? `${last.report_request.label}, run ${S.runNo(last.report_request.report, A.reports)}` : `request #${last.seq}`}` : `${scopeList.length} requests`);
    const scopeSeg = S.seg([['last', 'last request'], ['window', `last ${win.length}`]], ui.scope, (v) => { ui.scope = v; S.store.set('now.scope', v); draw(A); }, 'scope');
    const nSeg = S.seg([[30, '30'], [50, '50'], [100, '100'], [300, 'all']], ui.n, (v) => { ui.n = v; S.store.set('now.n', v); draw(A); }, 'window size');
    const ici = S.card({ q: ui.scope === 'last' ? 'Where did the last request\'s time go?' : `Where did the time of the last ${scopeList.length} requests go?`,
      sub: 'engine time in ms · the widest block is the biggest cost · hover for details, click to open the request',
      tools: el('div', { class: 'card-tools' }, scopeSeg, el('span', { class: 'tools-sep', text: 'window' }), nSeg), cls: 'span2' },
      lv ? S.icicle(lv.levels, { total: lv.total }) : el('p', { class: 'empty-line', text: 'No timing yet.' }),
      lv ? S.legend([...new Set(lv.levels[2].map((s) => s.key))]) : null,
      ui.scope === 'last' && last ? el('p', { class: 'verdict' }, el('a', { href: `#now/r/${last.seq}`, text: 'Open this request: its timeline, phases and the same request in other runs →' })) : null);
    if (ui.scope === 'last' && last) ici.querySelector('.icicle').addEventListener('click', () => S.go(`#now/r/${last.seq}`));
    grid.append(ici, windowCard(A, scopeList, last), trendCard(A, winAll, every), kindCard(A, win), slowCard(A, win), expertCard(A, scopeList, last));
    const more = S.button(ui.table ? 'Hide all numbers' : `All numbers (${winAll.length} requests)`, () => { ui.table = !ui.table; draw(A); }, 'more');
    grid.appendChild(el('div', { class: 'span3 more-row' }, more));
    if (ui.table) grid.appendChild(tableCard(A, winAll));
  }

  S.viewDefs.now = {
    render(A, route, full) {
      const sec = document.getElementById('view-now');
      if (!built) {
        holders.tiles = el('div', { class: 'tiles-holder' });
        diagramNode = S.diagram.build();
        holders.diagram = S.card({ q: 'Which part of the PC is the limit right now?', sub: 'live, every second · a link\'s fill is its throughput against its ceiling · the limit is the part with the largest share of the last decode window', cls: 'pc-card' }, diagramNode);
        holders.grid = el('div', { class: 'grid' });
        S.fill(sec, holders.tiles, holders.diagram, holders.grid);
        built = true;
      }
      draw(A);
      if (A.hardware) S.diagram.update(A.hardware, { workers: workersNote(A) });
    },
    tick(A) {
      if (A.hardware) S.diagram.update(A.hardware, { workers: workersNote(A) });
    },
  };
})();
