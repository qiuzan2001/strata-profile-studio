/* Strata Profile Studio: Now.  Decode speed, time per request, the limiting component and the trend.
   Every card is built once; new data only changes text, attributes and single style properties (see charts.js,
   "Live components").  A card is built again only when the user changes its options. */
'use strict';

(function () {
  const { el, isNum } = S;
  S.viewDefs = S.viewDefs || {};

  const ui = {
    scope: S.store.get('now.scope', 'last'),
    n: S.store.get('now.n', 50),
    x: S.store.get('now.x', 'order'),
    y: S.store.get('now.y', 'decode'),
    table: false,
  };
  const Y = {
    decode: { label: 'decode speed', unit: 'tok/s', get: (s) => (s.generated > 1 ? s.decode_tok_s : null), fmt: (v) => S.num(v, 0), better: 'higher' },
    prompt: { label: 'prompt read speed', unit: 'tok/s', get: (s) => s.prompt_tok_s, fmt: (v) => S.int(v), better: 'higher' },
    window: { label: 'decode window length', unit: 'ms', get: (s) => (s.generated > 1 ? s.ms_per_window : null), fmt: (v) => S.num(v, 0), better: 'lower' },
    hit: { label: 'expert cache hit rate', unit: '%', get: (s) => (isNum(s.hit_rate) ? s.hit_rate * 100 : null), fmt: (v) => `${S.num(v, 0)} %`, better: 'higher' },
  };
  const X = {
    order: { label: 'requests in order (gaps over 5 min marked)', get: (s) => s._i },
    time: { label: 'clock time', get: (s) => s.seen_at },
    context: { label: 'prompt tokens (log scale)', get: (s) => s.prompt_tokens, log: true },
    hit: { label: 'expert cache hit rate, %', get: (s) => (isNum(s.hit_rate) ? s.hit_rate * 100 : null) },
  };
  const isCalib = (s) => !!(s.run && (s.run.kind === 'tune' || s.run.kind === 'reload'));
  const C = {};                                     // the built cards and their live parts
  let built = false;

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
    if (!vals.length) return null;
    const top = Math.max(...vals.map((v) => v.median));
    const near = vals.filter((v) => v.median >= top * 0.97);
    if (near.length < 2) return null;
    return `The calibration measured ${near.map((v) => v.value).join(', ')} workers within 3 % of each other (${S.num(Math.min(...near.map((v) => v.median)), 1)} to ${S.num(top, 1)} tok/s)`;
  }

  // the icicle's levels for one request or a set of requests (sums)
  const L2 = [...S.PROMPT_GROUPS, 'host', 'not split'].map((k) => ({ key: `p:${k}`, part: k })).concat([...S.DECODE_PARTS, 'not split'].map((k) => ({ key: `d:${k}`, part: k })));
  function icicleLevels(list, title) {
    let prompt = 0, decode = 0;
    const pp = {}, dp = {};
    for (const s of list) {
      for (const p of S.promptParts(s)) { pp[p.key] = (pp[p.key] || 0) + p.value; prompt += p.value; }
      for (const p of S.decodeParts(s)) { dp[p.key] = (dp[p.key] || 0) + p.value; decode += p.value; }
    }
    const total = prompt + decode;
    if (total <= 0) return null;
    let at = 0;
    const l2 = [];
    for (const k of [...S.PROMPT_GROUPS, 'host', 'not split']) if (pp[k] > 0) { l2.push({ key: `p:${k}`, value: pp[k], start: at, label: k === 'not split' ? 'prompt read, no split' : S.PART_LABEL[k], about: S.PART_ABOUT[k] }); at += pp[k]; }
    for (const k of [...S.DECODE_PARTS, 'not split']) if (dp[k] > 0) { l2.push({ key: `d:${k}`, value: dp[k], start: at, label: k === 'not split' ? 'decode, no split' : S.PART_LABEL[k], about: S.PART_ABOUT[k] }); at += dp[k]; }
    const max = l2.reduce((m, s) => (s.value > m.value ? s : m), l2[0]);
    if (max) max.hl = true;
    return { total, prompt, decode, levels: [
      [{ key: 'all', label: title, value: total, start: 0, pct: false }],
      [{ key: 'prompt', label: 'prompt read', value: prompt, start: 0, about: 'The engine reads the prompt into the KV cache.' },
        { key: 'decode', label: 'decode', value: decode, start: prompt, about: 'The engine generates the answer, one decode window at a time.' }],
      l2] };
  }

  // ---------------------------------------------------------------------------------------------- build
  function tile(key, label) {
    const t = C.tiles[key] = { v: el('span', { class: 'v num', text: '–' }), u: el('span', { class: 'u', text: '' }), note: el('div', { class: 'tile-note', text: '' }) };
    t.node = el('div', { class: `tile tile-${key}` }, el('div', { class: 'tile-label', text: label }), el('div', { class: 'tile-value' }, t.v, t.u), t.note);
    return t.node;
  }
  function build(A, sec) {
    C.tiles = {};
    const tiles = el('div', { class: 'tiles' }, tile('decode', 'Decode speed, last request'), tile('prompt', 'Prompt read speed, last request'),
      tile('hit', 'Expert cache hit rate'), tile('limit', 'Limiting component, last decode'), tile('time', 'Engine time'));
    C.tiles.decode.node.classList.add('hero');
    const pc = S.card({ q: 'PC components and links', sub: 'live, 1 s · link fill: throughput against the ceiling · highlighted: the component with the largest share of the last decode window', cls: 'span3 pc-card' }, S.pc.build());
    C.empty = el('section', { class: 'card empty-card span3', hidden: true },
      C.emptyTitle = el('h3', { text: '' }), C.emptySub = el('p', { class: 'sub', text: '' }),
      el('div', { class: 'row-buttons' }, C.emptyRun = S.button('Run a profile', () => A.startProfile(), 'primary'), S.button('Open the runs on disk', () => S.go('#runs'))),
      C.emptyWhy = el('p', { class: 'note', text: '' }));
    // the icicle
    C.ici = S.LiveIcicle([[{ key: 'all', cls: 'k-all' }], [{ key: 'prompt', cls: 'k-phase' }, { key: 'decode', cls: 'k-phase' }], L2.map((k) => ({ key: k.key, cls: S.PART_CLASS[k.part] }))]);
    C.ici.node.addEventListener('click', () => { if (ui.scope === 'last' && C.last) S.go(`#now/r/${C.last.seq}`); });
    C.iciTitle = el('span', { text: '' });
    C.iciTools = el('div', { class: 'card-tools' });
    C.iciCard = S.card({ q: 'Time per request', sub: 'engine time, ms · the widest block is the largest cost · select a block to open the request', cls: 'span2', tools: C.iciTools },
      C.ici.node, S.legend([...S.PROMPT_GROUPS, 'host', ...S.DECODE_PARTS]), el('p', { class: 'verdict' }, C.iciLink = el('a', { href: '#now', text: 'Open the request: timeline, phases, the same request in other runs' })));
    // the decode window
    C.win = S.LiveBars(S.DECODE_PARTS, { fmt: (v) => S.num(v, 1), unit: 'ms' });
    C.winCard = S.card({ q: 'Decode window composition', sub: '', cls: '' }, C.win.node);
    C.winSub = C.winCard.querySelector('.sub');
    // the trend
    C.trend = S.LiveScatter({ height: 280 });
    C.trendTools = el('div', { class: 'card-tools' });
    C.trendLegend = el('div', { class: 'legend' });
    C.trendCard = S.card({ q: 'Decode speed per request', sub: '', cls: 'span2', tools: C.trendTools }, C.trend.node, C.trendLegend);
    C.trendQ = C.trendCard.querySelector('h3');
    C.trendSub = C.trendCard.querySelector('.sub');
    C.lg = {};
    for (const k of [...S.KINDS, 'out', 'calib', 'band', 'gap', 'median']) {
      const t = el('span', { text: '' });
      const sw = S.KINDS.includes(k) ? S.kindIcon(k) : el('i', { class: `sw ${{ out: 'ring', calib: 'calib-mark', band: 'band-profile', gap: 'gap', median: 'median' }[k]}` });
      C.lg[k] = { node: el('span', { class: 'lg', title: S.KIND_ABOUT[k] || '' }, sw, t), t };
      C.trendLegend.appendChild(C.lg[k].node);
    }
    // the kinds, the slowest requests, the experts
    C.kinds = S.LiveBars(['short', 'follow-up', 'long prompt'], { fmt: (v) => S.num(v, 1), unit: 'tok/s' });
    C.kindCard = S.card({ q: 'Decode speed per request kind', sub: 'median, tok/s · band: lowest to highest request · higher is better' }, C.kinds.node);
    C.slow = S.LiveBars(['s0', 's1', 's2', 's3', 's4', 's5'], { fmt: (v) => S.num(v, 1) });
    C.slowCard = S.card({ q: 'Slowest requests per kind', sub: 'decode speed against the median of the same kind · select a request to open it', cls: 'span2' }, C.slow.node);
    C.exp = S.LiveBars(['vram', 'cpu', 'pcie'], { max: 1 });
    C.expCard = S.card({ q: 'Expert sources', sub: '' }, C.exp.node);
    C.expSub = C.expCard.querySelector('.sub');
    C.more = S.button('', () => { ui.table = !ui.table; C.tableSig = null; draw(A); }, 'more');
    C.tableHost = el('div', { class: 'span3' });
    C.mapCard = S.expertMap.build();
    C.grid = el('div', { class: 'grid' }, C.empty, C.iciCard, C.winCard, C.trendCard, C.kindCard, C.slowCard, C.expCard, C.mapCard,
      el('div', { class: 'span3 more-row' }, C.more), C.tableHost);
    S.fill(sec, tiles, el('div', { class: 'grid pc-grid' }, pc), C.grid);
    tools(A);
  }

  function tools(A) {
    S.fill(C.iciTools,
      S.seg([['last', 'last request'], ['window', 'window']], ui.scope, (v) => { ui.scope = v; S.store.set('now.scope', v); tools(A); draw(A); }, 'scope'),
      el('span', { class: 'tools-sep', text: 'window size' }),
      S.seg([[30, '30'], [50, '50'], [100, '100'], [300, 'all']], ui.n, (v) => { ui.n = v; S.store.set('now.n', v); tools(A); draw(A); }, 'window size'));
    S.fill(C.trendTools,
      S.seg([['decode', 'decode'], ['prompt', 'prompt read'], ['window', 'window'], ['hit', 'hit rate']], ui.y, (v) => { ui.y = v; S.store.set('now.y', v); tools(A); draw(A); }, 'value'),
      el('span', { class: 'tools-sep', text: 'against' }),
      S.seg([['order', 'order'], ['time', 'clock'], ['context', 'context'], ['hit', 'hit rate']], ui.x, (v) => { ui.x = v; S.store.set('now.x', v); tools(A); draw(A); }, 'axis'));
  }

  // ---------------------------------------------------------------------------------------------- update
  function draw(A) {
    const every = A.samples();
    const all = every.filter((s) => !isCalib(s));
    const win = ui.n >= 300 ? all : all.slice(-ui.n);
    const winAll = ui.n >= 300 ? every : every.slice(win.length ? every.indexOf(win[0]) : Math.max(0, every.length - ui.n));
    const last = C.last = newest(all);
    const scopeList = ui.scope === 'last' ? (last ? [last] : []) : win;
    // empty state
    const s = A.strata();
    const why = A.cannotRun();
    S.show(C.empty, !last);
    S.setText(C.emptyTitle, s.status === 'unreachable' ? 'No requests: Strata does not answer.' : 'No requests since Strata started.');
    S.setText(C.emptySub, s.status === 'unreachable' ? 'This view fills when Strata answers again.' : 'Send a chat to Strata, or run a profile (16 requests, about 50 s). Each request shows here when it ends.');
    C.emptyRun.disabled = !!why;
    S.setText(C.emptyWhy, why || '');
    for (const c of [C.iciCard, C.winCard, C.kindCard, C.slowCard, C.expCard]) S.show(c, !!last);
    S.show(C.trendCard, every.length > 0);
    // tiles
    const T = C.tiles;
    const med = S.median(win.filter((x) => x.generated > 1).map((x) => x.decode_tok_s));
    const lv = icicleLevels(win, '');
    if (last) {
      const d = isNum(last.decode_tok_s) && isNum(med) && last.generated > 1 ? (last.decode_tok_s - med) / med * 100 : null;
      S.setText(T.decode.v, last.generated > 1 ? S.tps(last.decode_tok_s) : '–'); S.setText(T.decode.u, 'tok/s');
      S.setText(T.decode.note, isNum(d) ? `${S.signed(d, 0, ' %')} against the median (${S.num(med, 1)} tok/s) of the last ${win.length}` : `${S.int(last.generated)} token generated`);
      S.setText(T.prompt.v, S.tps(last.prompt_tok_s)); S.setText(T.prompt.u, 'tok/s');
      S.setText(T.prompt.note, isNum(last.prompt_tokens) ? `${S.int(last.prompt_tokens)} tokens${last.reused ? `, ${S.int(last.reused)} reused` : ''}` : '');
      const src = S.expertSources(last);
      S.setText(T.hit.v, S.pct(last.hit_rate)); S.setText(T.hit.note, src ? `CPU ${S.pct(src.cpu)} · PCIe ${S.pct(src.pcie)}` : '');
      const big = biggestPart(last);
      S.setText(T.limit.v, big ? S.PART_LABEL[big.key] : '–');
      S.setText(T.limit.note, big ? `${S.pct(big.share)} of the ${S.num(last.ms_per_window, 1)} ms decode window` : 'no decode window split');
      S.setAttr(T.limit.node, 'class', `tile tile-limit ${big ? `limit ${S.PART_CLASS[big.key]}` : ''}`);
      S.setText(T.time.v, lv ? S.ms(lv.total) : '–');
      S.setText(T.time.note, lv ? `last ${win.length} requests · prompt reads ${S.pct(lv.prompt / lv.total)}, decode ${S.pct(lv.decode / lv.total)}` : '');
    }
    if (!last) { trend(A, winAll, all, every); return; }
    // the icicle
    const title = ui.scope === 'last' ? (last.report_request ? `${last.report_request.label}, run ${S.runNo(last.report_request.report, A.reports)}` : `request ${last.seq}`) : `${scopeList.length} requests`;
    const il = icicleLevels(scopeList, title);
    if (il) C.ici.update(il.levels, il.total);
    S.setText(C.iciCard.querySelector('h3'), ui.scope === 'last' ? 'Time per request: last request' : `Time per request: last ${scopeList.length} requests (sum)`);
    S.setAttr(C.iciLink, 'href', `#now/r/${last.seq}`);
    S.show(C.iciLink.parentElement, ui.scope === 'last');
    // the decode window
    let parts, sub;
    if (ui.scope === 'last') {
      parts = last.window_ms ? S.DECODE_PARTS.map((k) => ({ k, v: last.window_ms[k] })) : [];
      sub = isNum(last.ms_per_window) ? `${S.num(last.ms_per_window, 1)} ms per window, ${S.num(last.tokens_per_window, 2)} tokens per window` : 'ms per window';
    } else {
      const ws = scopeList.filter((x) => x.window_ms && isNum(x.ms_per_window));
      parts = S.DECODE_PARTS.map((k) => ({ k, v: ws.length ? S.sum(ws.map((x) => x.window_ms[k])) / ws.length : null }));
      sub = ws.length ? `mean of ${ws.length} requests: ${S.num(S.sum(ws.map((x) => x.ms_per_window)) / ws.length, 1)} ms per window` : 'ms per window';
    }
    const top = parts.filter((p) => isNum(p.v) && p.k !== 'other').sort((a, b) => b.v - a.v)[0];
    S.setText(C.winSub, `${sub} · lower is better`);
    C.win.update(parts.filter((p) => isNum(p.v)).map((p) => ({ key: p.k, label: S.PART_LABEL[p.k], value: p.v, cls: S.PART_CLASS[p.k], hl: top && p.k === top.k, tip: [S.PART_LABEL[p.k], ['ms per window', S.num(p.v, 2)], S.PART_ABOUT[p.k]] })));
    trend(A, winAll, all, every);
    // the kinds
    C.kinds.update(['short', 'follow-up', 'long prompt'].map((k) => {
      const v = win.filter((x) => x.kind === k && x.generated > 1).map((x) => x.decode_tok_s).filter(isNum);
      return v.length ? { key: k, label: S.KIND_LABEL[k], iconKind: k, value: S.median(v), spread: [Math.min(...v), Math.max(...v)], note: `${v.length} requests, ${S.num(Math.min(...v), 0)} to ${S.num(Math.max(...v), 0)} tok/s` } : null;
    }).filter(Boolean));
    // the slowest per kind
    const byKind = {};
    for (const x of win) if (x.generated > 1 && isNum(x.decode_tok_s)) (byKind[x.kind] = byKind[x.kind] || []).push(x.decode_tok_s);
    const medK = Object.fromEntries(Object.entries(byKind).map(([k, v]) => [k, S.median(v)]));
    const rows = win.filter((x) => x.generated > 1 && isNum(x.decode_tok_s) && isNum(medK[x.kind]))
      .map((x) => ({ x, gap: (x.decode_tok_s - medK[x.kind]) / medK[x.kind] * 100 })).sort((a, b) => a.gap - b.gap).slice(0, 6);
    C.slow.update(rows.map(({ x, gap }, i) => ({ key: `s${i}`, iconKind: x.kind,
      label: x.report_request ? `${x.report_request.label}, run ${S.runNo(x.report_request.report, A.reports)}` : `request ${x.seq} · ${S.clock(x.seen_at)}`,
      value: x.decode_tok_s, text: `${S.num(x.decode_tok_s, 1)} tok/s · ${S.signed(gap, 0, ' %')}`,
      note: `hit rate ${S.pct(x.hit_rate)} · ${S.int(x.prompt_tokens)} tokens`, onclick: () => S.go(`#now/r/${x.seq}`) })));
    // the expert sources
    let src, n = 1;
    if (ui.scope === 'last') src = S.expertSources(last);
    else { const xs = scopeList.map(S.expertSources).filter(Boolean); n = xs.length; src = n ? { vram: S.sum(xs.map((q) => q.vram)) / n, cpu: S.sum(xs.map((q) => q.cpu)) / n, pcie: S.sum(xs.map((q) => q.pcie)) / n } : null; }
    S.setText(C.expSub, `share of the routed experts, ${ui.scope === 'last' ? 'last request' : `mean of ${n} requests`} · more from VRAM is faster`);
    C.exp.update(src ? [{ key: 'vram', label: 'VRAM expert cache', value: src.vram, cls: 'k-gpu', text: S.pct(src.vram, 1) },
      { key: 'cpu', label: 'CPU pool', value: src.cpu, cls: 'k-cpu', text: S.pct(src.cpu, 1) },
      { key: 'pcie', label: 'read over PCIe', value: src.pcie, cls: 'k-pcie', text: S.pct(src.pcie, 1) }] : []);
    // all numbers (a details table: built again only when a new request arrives while it is open)
    S.setText(C.more, ui.table ? 'Hide all numbers' : `All numbers (${winAll.length} requests)`);
    const sig = ui.table ? `${winAll.length}:${(winAll[winAll.length - 1] || {}).seq}` : 'closed';
    if (sig !== C.tableSig) { C.tableSig = sig; S.fill(C.tableHost, ui.table ? tableCard(A, winAll) : null); }
    if (A.experts) S.expertMap.update(A.experts);
  }

  function trend(A, win, all, every) {
    const yo = Y[ui.y], xo = X[ui.x] || X.order;
    const reports = A.reports;
    win.forEach((s, i) => { s._i = i; });
    const pts = win.map((s) => ({ s, x: xo.get(s), y: yo.get(s) })).filter((p) => isNum(p.x) && isNum(p.y));
    pts.forEach((p, i) => { p.cut = ui.x === 'order' && i > 0 && isNum(p.s.seen_at) && isNum(pts[i - 1].s.seen_at) && p.s.seen_at - pts[i - 1].s.seen_at > 300; });
    const byKind = {};
    for (const p of pts) if (!isCalib(p.s)) (byKind[p.s.kind] = byKind[p.s.kind] || []).push(p.y);
    const nCalib = pts.filter((p) => isCalib(p.s)).length;
    const lim = {};
    for (const [k, v] of Object.entries(byKind)) if (v.length >= 8) lim[k] = yo.better === 'higher' ? S.quantile(v, 0.1) : S.quantile(v, 0.9);
    const last = C.last;
    const points = pts.map((p) => ({
      x: p.x, y: p.y, cut: p.cut, kind: p.s.kind, hl: p.s === last, calib: isCalib(p.s),
      out: !isCalib(p.s) && isNum(lim[p.s.kind]) && (yo.better === 'higher' ? p.y < lim[p.s.kind] : p.y > lim[p.s.kind]),
      onclick: () => S.go(`#now/r/${p.s.seq}`),
      tip: () => [`${S.KIND_LABEL[p.s.kind]} · ${p.s.late ? 'before the Studio started' : S.clock(p.s.seen_at, 'sec')}`,
        [yo.label, `${yo.fmt(p.y)}${yo.unit === '%' ? '' : ' ' + yo.unit}`], ['prompt', `${S.int(p.s.prompt_tokens)} tokens`], ['hit rate', S.pct(p.s.hit_rate)],
        p.s.report_request ? `${p.s.report_request.label}, ${S.runName(p.s.report_request.report, reports)}` : (p.s.run ? spanLabel(p.s.run, reports) : ''), 'Select to open.'],
    }));
    const short = (sp) => (sp.kind === 'profile' && sp.report ? `run ${S.runNo(sp.report, reports)}` : sp.kind === 'tune' ? 'calibration' : sp.kind === 'reload' ? 'reload' : '');
    let bands = [], gaps = [], xTicks = null;
    if (ui.x === 'time') bands = (A.spans || []).map((sp) => ({ ...sp, label: spanLabel(sp, reports), short: short(sp) }));
    if (ui.x === 'order') {
      xTicks = win.filter((s, i) => i === 0 || (isNum(s.seen_at) && isNum(win[i - 1].seen_at) && s.seen_at - win[i - 1].seen_at > 300)).map((s) => ({ x: s._i, label: s.late ? 'before start' : S.clock(s.seen_at) }));
      for (const sp of A.spans || []) {
        const idx = win.filter((s) => !s.late && s.seen_at >= sp.from && s.seen_at <= sp.to).map((s) => s._i);
        if (idx.length) bands.push({ from: Math.min(...idx) - 0.45, to: Math.max(...idx) + 0.45, kind: sp.kind, report: sp.report, label: spanLabel(sp, reports), short: short(sp) });
        else if (sp.kind === 'tune') {
          const before = win.filter((s) => !s.late && s.seen_at < sp.from).map((s) => s._i);
          const after = win.filter((s) => !s.late && s.seen_at > sp.to).map((s) => s._i);
          if (before.length && after.length) gaps.push({ x: Math.max(...before) + 0.5, label: `calibration, ${S.dur(sp.to - sp.from)}` });
        }
      }
      bands = bands.filter((b, i) => !bands.some((o, j) => j !== i && o.from < b.to && b.from < o.to &&
        ((o.report && !b.report) || (!!o.report === !!b.report && (o.to - o.from > b.to - b.from || (o.to - o.from === b.to - b.from && j < i))))));
    }
    const name = { decode: 'Decode speed per request', prompt: 'Prompt read speed per request', window: 'Decode window length per request', hit: 'Expert cache hit rate per request' }[ui.y];
    S.setText(C.trendQ, ui.x === 'context' ? `${name}, against context size` : ui.x === 'hit' ? `${name}, against hit rate` : name);
    S.setText(C.trendSub, `${yo.unit} · ${yo.better} is better · ${pts.length - nCalib} requests${nCalib ? ` and ${nCalib} calibration requests` : ''} · select a mark to open the request`);
    C.trend.update(points, { xLog: xo.log, xTime: ui.x === 'time', bands, gaps, xTicks, median: ui.x === 'time' || ui.x === 'order', yFmt: yo.fmt, xFmt: ui.x === 'hit' ? (v) => `${v} %` : S.int, xLabel: xo.label, y0: 0 });
    const outs = points.filter((p) => p.out).length;
    for (const k of S.KINDS) S.setText(C.lg[k].t, `${S.KIND_LABEL[k]} (${(byKind[k] || []).length})`);
    S.setText(C.lg.out.t, `outlier for its kind (${outs})`);
    S.setText(C.lg.calib.t, `calibration request, not in any statistic (${nCalib})`); S.show(C.lg.calib.node, nCalib > 0);
    S.setText(C.lg.band.t, 'profile run'); S.show(C.lg.band.node, ui.x === 'time' || ui.x === 'order');
    S.setText(C.lg.gap.t, 'calibration (no requests)'); S.show(C.lg.gap.node, gaps.length > 0);
    S.setText(C.lg.median.t, 'rolling median'); S.show(C.lg.median.node, ui.x === 'time' || ui.x === 'order');
  }

  function tableCard(A, win) {
    const cols = [
      { label: 'Request', key: 'seq', num: true }, { label: 'Time', get: (s) => (s.late ? 'before start' : S.clock(s.seen_at, 'sec')), raw: (s) => s.seen_at },
      { label: 'Kind', get: (s) => S.KIND_LABEL[s.kind] }, { label: 'Run', get: (s) => (s.report_request ? `${s.report_request.label}, run ${S.runNo(s.report_request.report, A.reports)}` : s.run ? s.run.kind : '') },
      { label: 'Prompt tokens', num: true, get: (s) => S.int(s.prompt_tokens), raw: (s) => s.prompt_tokens },
      { label: 'Reused', num: true, get: (s) => S.int(s.reused), raw: (s) => s.reused },
      { label: 'Prompt read, tok/s', num: true, get: (s) => S.tps(s.prompt_tok_s), raw: (s) => s.prompt_tok_s },
      { label: 'Prompt read, ms', num: true, get: (s) => S.int(s.prompt_ms), raw: (s) => s.prompt_ms },
      { label: 'Generated', num: true, get: (s) => S.int(s.generated), raw: (s) => s.generated },
      { label: 'Decode, tok/s', num: true, get: (s) => S.tps(s.decode_tok_s), raw: (s) => s.decode_tok_s },
      { label: 'Window, ms', num: true, get: (s) => S.num(s.ms_per_window, 1), raw: (s) => s.ms_per_window },
      ...S.DECODE_PARTS.map((k) => ({ label: `${S.PART_LABEL[k]}, ms`, num: true, get: (s) => (s.window_ms ? S.num(s.window_ms[k], 1) : '–'), raw: (s) => (s.window_ms ? s.window_ms[k] : '') })),
      { label: 'Hit rate', num: true, get: (s) => S.pct(s.hit_rate, 1), raw: (s) => s.hit_rate },
      { label: 'PCIe share', num: true, get: (s) => S.pct(s.pcie_share, 1), raw: (s) => s.pcie_share },
      { label: 'KV from VRAM', num: true, get: (s) => S.pct(s.kv_hit, 1), raw: (s) => s.kv_hit },
      { label: 'Drafts accepted', num: true, get: (s) => (isNum(s.drafts_offered) ? `${s.drafts_accepted} of ${s.drafts_offered}` : '–'), raw: (s) => s.drafts_accepted },
    ];
    return S.card({ q: `All numbers: ${win.length} requests`, sub: 'newest first · select a row to open the request', cls: 'span3',
      tools: el('div', { class: 'card-tools' }, S.button('Download CSV', () => S.csv('studio-requests.csv', cols, win), 'small')) },
    S.table(cols, win.slice().reverse().map((s) => Object.assign({}, s, { _onclick: () => S.go(`#now/r/${s.seq}`) }))));
  }

  function tick(A) {
    const every = A.samples().filter((s) => !isCalib(s));
    S.pc.update(A.hardware, A.host, { workers: workersNote(A), experts: A.experts, hits: every.slice(-60).map((s) => (isNum(s.hit_rate) ? s.hit_rate * 100 : null)) });
  }

  S.viewDefs.now = {
    render(A, route, full) {
      const sec = document.getElementById('view-now');
      if (!built) { build(A, sec); built = true; }
      draw(A);
      tick(A);
    },
    tick,
  };
})();
