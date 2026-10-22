/* Strata Profile Studio: Tune.  The tune flow (profile before, calibration, reload, profile after, comparison) in
   detail: the flow on a time axis, every measurement of the calibration in order with the engine starts, the rounds
   per setting, the measurement log, the calibration's objective against your workloads, the decision, the history.
   While a tune runs, the page changes in place; it is built again only when a new measurement or stage arrives. */
'use strict';

(function () {
  const { el, svg, isNum } = S;
  S.viewDefs = S.viewDefs || {};
  let token = 0, builtKey = null;
  const live = {};                                   // the nodes the in-place update touches
  const ui = { filter: '', kind: 'measure' };

  const STAGE_LABEL = { check: 'Preconditions', before: 'Profile before', tune: 'Calibration', reload: 'Model load', after: 'Profile after', compare: 'Comparison' };
  const STEP_NAME = { pcie_frac: 'PCIe share', spec_min_p: 'Draft floor', pool_workers: 'CPU workers', adapt: 'Expert tier' };
  const STEP_FLAG = { pcie_frac: '--pcie-frac', spec_min_p: '--spec-min-p', pool_workers: '--pool-workers', adapt: '--adapt-every, --adapt-swaps, --adapt-decay' };
  const PARAM_CLASS = { pcie_frac: 'c1', spec_min_p: 'c3', pool_workers: 'c2', adapt: 'c7' };
  // what the calibration measures (Strata's tools/calibrate.py)
  const OBJECTIVE = {
    prompts: ['Write a Python function that merges two sorted lists into one sorted list, with a docstring and two tests.',
      'Explain in two paragraphs how a refrigerator moves heat from inside to outside.',
      'List twelve European capitals with one sentence about each.'],
    tokens: 128, tierTokens: 512, rounds: 3, minGain: 3,
  };

  // the tune to show: #tune/<id>, else the running or last flow of this Studio, else the newest record
  const routeId = (route) => (route.parts[1] === 'pc' ? null : route.parts[1]);
  function current(A, id) {
    const flow = A.flow;
    if (id) {
      if (flow && flow.id === id) return { kind: 'flow', t: flow };
      const rec = A.tunes.find((x) => x.id === id);
      return rec ? { kind: 'record', t: rec } : null;
    }
    if (flow && flow.state === 'running') return { kind: 'flow', t: flow };
    const s = A.strata();
    if (s.tune && s.tune.state === 'running' && flow && flow.state === 'idle' && flow.candidates) return { kind: 'elsewhere', t: flow };
    if (A.tunes.length) {
      const rec = A.tunes[0];
      if (flow && flow.id === rec.id) return { kind: 'flow', t: Object.assign({}, rec, flow, { candidates: rec.candidates || flow.candidates }) };
      return { kind: 'record', t: rec };
    }
    if (flow && flow.id) return { kind: 'flow', t: flow };
    return null;
  }
  const linesOf = (t) => t.lines_tail || t.tune_lines || t.lines || [];

  // every measurement of the calibration in the order it was made, from its output lines
  function sequence(lines) {
    const out = [];
    let starts = 0, pendingStart = false, tier = null, worker = null;
    for (const raw of lines) {
      const line = String(raw);
      if (/\[strata\] starting the engine/.test(line)) { starts++; pendingStart = true; continue; }
      let m = /^\s*round (\d+)\/(\d+) (PCIe share|draft floor): (.*)$/.exec(line);
      if (m) {
        const r = Number(m[1]), key = m[3] === 'PCIe share' ? 'pcie_frac' : 'spec_min_p';
        const pairs = [...m[4].matchAll(/(\S+) ([\d.]+)/g)].map((p) => ({ value: p[1], rate: Number(p[2]) }));
        const ordered = r % 2 === 1 ? pairs : pairs.slice().reverse();       // calibrate.py: every other round reversed
        ordered.forEach((p, i) => { out.push({ key, value: p.value, rate: p.rate, round: r, order: i + 1, start: pendingStart }); pendingStart = false; });
        continue;
      }
      m = /^\s*Measuring with (\d+) CPU workers/.exec(line);
      if (m) { worker = m[1]; continue; }
      m = /^\s*(\d+) workers: ([\d.]+) tok\/s/.exec(line);
      if (m) { out.push({ key: 'pool_workers', value: m[1], rate: Number(m[2]), round: 1, order: 1, start: pendingStart }); pendingStart = false; worker = null; continue; }
      m = /Measuring the expert tier (?:\(the engine default\)|every (\S+), (\S+) swaps, decay (\S+))/.exec(line);
      if (m) { tier = m[1] ? `${m[1]}/${m[2]}/${m[3]}` : 'default'; continue; }
      m = /^\s*([\d.]+) tok\/s\s*$/.exec(line);
      if (m && tier) { out.push({ key: 'adapt', value: tier, rate: Number(m[1]), round: 1, order: 1, start: pendingStart }); pendingStart = false; tier = null; }
    }
    return { points: out, starts };
  }

  // ------------------------------------------------------------------------------------------ cards
  function timelineCard(t) {
    const stages = t.stages || [];
    const total = S.sum(stages.map((s) => s.elapsed_s || 0)) || 1;
    const gantt = el('div', { class: 'gantt' });
    live.gantt = [];
    let at = 0;
    for (const st of stages) {
      const seg = el('div', { class: `gantt-seg st-${st.state} g-${st.key}` }, el('span', { class: 'seg-text' }, el('b', { text: STAGE_LABEL[st.key] || st.key }), ' ', el('span', { class: 'num', text: isNum(st.elapsed_s) ? S.dur(st.elapsed_s) : '' })));
      seg.style.left = `${(at / total) * 100}%`;
      seg.style.width = `${((st.elapsed_s || 0) / total) * 100}%`;
      S.hover(seg, [STAGE_LABEL[st.key] || st.key, ['time', isNum(st.elapsed_s) ? S.dur(st.elapsed_s) : '–'], ['state', st.state], st.message || '']);
      live.gantt.push({ key: st.key, seg, val: seg.querySelector('.num') });
      gantt.appendChild(seg);
      at += st.elapsed_s || 0;
    }
    const axis = el('div', { class: 'tl-axis gantt-axis' });
    for (const v of S.niceTicks(0, total, 6)) { const tk = el('span', { class: 'tl-tick', text: S.dur(v) }); tk.style.left = `${(v / total) * 100}%`; axis.appendChild(tk); }
    const stepper = el('ol', { class: 'stepper' });
    live.steps = [];
    for (const st of stages) {
      const dot = el('span', { class: 'step-dot', 'aria-hidden': 'true' });
      const time = el('span', { class: 'step-time num', text: '' });
      const msg = el('span', { class: 'step-msg', text: '' });
      const li = el('li', { class: 'step' }, dot, el('span', { class: 'step-label', text: STAGE_LABEL[st.key] || st.key }), time, msg);
      live.steps.push({ key: st.key, li, dot, time, msg });
      stepper.appendChild(li);
    }
    live.log = el('pre', { class: 'log', text: '' });
    return S.card({ q: 'Tune flow timeline', sub: 'each stage on one time axis · the calibration unloads the model: Strata answers 503 meanwhile', cls: 'span3' },
      stepper, axis, gantt, el('h4', { text: 'Newest output' }), live.log);
  }
  function updateTimeline(t, running) {
    const stages = t.stages || [];
    for (const s of live.steps || []) {
      const st = stages.find((x) => x.key === s.key) || {};
      S.setAttr(s.li, 'class', `step ${st.state || 'pending'}`);
      S.setText(s.dot, { done: '✓', failed: '✕', skipped: '–' }[st.state] || '');
      S.setText(s.time, st.state === 'running' && isNum(st.elapsed_s) ? `${S.dur(st.elapsed_s)}${st.timeout_s ? ` of ${S.dur(st.timeout_s)} at most` : ''}` : isNum(st.elapsed_s) ? S.dur(st.elapsed_s) : '');
      S.setText(s.msg, st.state === 'failed' && st.message ? st.message : '');
    }
    const total = S.sum(stages.map((s) => s.elapsed_s || 0)) || 1;
    let at = 0;
    for (const g of live.gantt || []) {
      const st = stages.find((x) => x.key === g.key) || {};
      S.setStyle(g.seg, 'left', `${((at / total) * 100).toFixed(3)}%`);
      S.setStyle(g.seg, 'width', `${(((st.elapsed_s || 0) / total) * 100).toFixed(3)}%`);
      S.setAttr(g.seg, 'class', `gantt-seg st-${st.state || 'pending'} g-${g.key}`);
      S.setText(g.val, isNum(st.elapsed_s) ? S.dur(st.elapsed_s) : '');
      at += st.elapsed_s || 0;
    }
    if (live.log) S.setText(live.log, (t.lines || linesOf(t)).slice(-8).join('\n') || (running ? 'waiting for output …' : ''));
  }

  function sequenceCard(t, seq) {
    const W = 900, H = 250, m = { l: 40, r: 12, t: 14, b: 30 };
    const P = seq.points;
    const s = svg('svg', { class: 'seqchart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Calibration measurements in order' });
    if (!P.length) s.appendChild(svg('text', { x: W / 2, y: H / 2, class: 'empty-svg', 'text-anchor': 'middle', text: 'No measurement yet.' }));
    else {
      const y0 = Math.min(...P.map((p) => p.rate)) * 0.95, y1 = Math.max(...P.map((p) => p.rate)) * 1.03;
      const X = (i) => m.l + ((i + 0.5) / P.length) * (W - m.l - m.r), Y = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);
      for (const v of S.niceTicks(y0, y1, 5)) {
        s.appendChild(svg('line', { class: 'grid', x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
        s.appendChild(svg('text', { class: 'tick', x: m.l - 5, y: Y(v) + 4, 'text-anchor': 'end', text: S.num(v, 0) }));
      }
      let prevKey = null, prevRound = null;
      P.forEach((p, i) => {
        if (p.start) {
          s.appendChild(svg('line', { class: 'restart-line', x1: X(i) - (W - m.l - m.r) / P.length / 2, x2: X(i) - (W - m.l - m.r) / P.length / 2, y1: m.t, y2: H - m.b }));
        }
        if (p.key !== prevKey || p.round !== prevRound) {
          s.appendChild(svg('text', { class: 'seq-label', x: X(i) - 3, y: H - m.b + 14, text: p.key === prevKey ? `R${p.round}` : `${STEP_NAME[p.key]}${p.key === 'pcie_frac' || p.key === 'spec_min_p' ? ` R${p.round}` : ''}` }));
        }
        prevKey = p.key; prevRound = p.round;
        const c = svg('circle', { class: `seq-dot ${PARAM_CLASS[p.key]}`, cx: X(i), cy: Y(p.rate), r: 4 });
        S.hover(c, [`${STEP_NAME[p.key]} ${p.value}`, ['decode', `${S.num(p.rate, 1)} tok/s`], ['measurement', `${i + 1} of ${P.length}`], p.key === 'pcie_frac' || p.key === 'spec_min_p' ? ['round', `${p.round}, place ${p.order}${p.round % 2 === 0 ? ' (reverse order)' : ''}`] : ['engine start', 'one start per value']]);
        s.appendChild(c);
      });
    }
    return S.card({ q: 'Calibration measurements in order', sub: `decode speed per measurement, tok/s · dashed lines: engine starts (${seq.starts}) · round 2 of a sweep runs in reverse order`, cls: 'span2' },
      s, el('div', { class: 'legend' }, Object.entries(STEP_NAME).map(([k, n]) => el('span', { class: `lg ${PARAM_CLASS[k]}` }, el('i', { class: 'sw' }), n)),
        el('span', { class: 'lg' }, el('i', { class: 'sw restart' }), 'engine start')));
  }

  function logCard(t) {
    const lines = linesOf(t).map(String);
    const pre = el('pre', { class: 'log tall' });
    const input = el('input', { type: 'search', placeholder: 'Filter the lines', 'aria-label': 'Filter the log', value: ui.filter });
    const apply = () => {
      const f = ui.filter.toLowerCase();
      const keep = lines.filter((ln) => {
        if (ui.kind === 'measure' && !/round \d|median of|workers:|^\s*[\d.]+ tok\/s|tuned for this PC|\[!\]|Measuring/.test(ln)) return false;
        if (ui.kind === 'engine' && !/\[strata\]/.test(ln)) return false;
        return !f || ln.toLowerCase().includes(f);
      });
      S.setText(pre, keep.join('\n') || 'No line matches.');
    };
    input.addEventListener('input', () => { ui.filter = input.value; apply(); });
    const seg = () => S.seg([['measure', 'measurements'], ['engine', 'engine starts'], ['all', 'all lines']], ui.kind, (v) => { ui.kind = v; S.fill(segBox, seg()); apply(); }, 'lines');
    const segBox = el('span', null, seg());
    apply();
    return S.card({ q: 'Calibration log', sub: `${lines.length} lines of the calibration's output`, tools: el('div', { class: 'card-tools' }, segBox) }, input, pre);
  }

  // per setting: the rounds as thin lines, the median as dots with min-max, kept and best marked
  function sweepCards(t, seq, running) {
    const cand = t.candidates;
    const cards = [];
    for (const st of (cand && cand.steps) || []) {
      const host = el('div', { class: 'chart-host' });
      const kept = st.kept, best = st.best;
      const keptV = st.values.find((v) => S.same(v.value, kept)), bestV = st.values.find((v) => S.same(v.value, best));
      let verdict = '';
      if (kept && best && !S.same(kept, best) && keptV && bestV && isNum(bestV.median) && isNum(keptV.median)) verdict = `Best ${best} (${S.num(bestV.median, 1)} tok/s) is not kept: ${S.signed((bestV.median - keptV.median) / keptV.median * 100, 1, ' %')} against ${kept} is less than the ${OBJECTIVE.minGain} % the calibration needs.`;
      else if (kept) verdict = `Kept: ${kept === 'default' ? 'the engine default' : kept}${st.was && !S.same(st.was, kept) ? ` (before the tune: ${st.was})` : ''}.`;
      else if (running && st.best && bestV && isNum(bestV.median)) verdict = `Best so far: ${st.best} (${S.num(bestV.median, 1)} tok/s). The calibration gives the kept value when it ends.`;
      else if (running && st.values.length) verdict = 'Measurement in progress.';
      const rounds = seq.points.filter((p) => p.key === st.key);
      const card = S.card({ q: `${STEP_NAME[st.key] || st.label} candidates`, sub: `${STEP_FLAG[st.key] || ''} · decode speed, tok/s · ${st.kind === 'sweep' ? `${OBJECTIVE.rounds} rounds in one engine start` : 'one engine start per value'}`, verdict, cls: 'sweep-card' }, host);
      requestAnimationFrame(() => {
        S.sweep(host, st, { empty: running ? 'not measured yet' : 'not measured' });
        // round lines behind the medians (sweeps only)
        const svgEl = host.querySelector('svg');
        if (!svgEl || st.kind !== 'sweep' || !rounds.length) return;
        const cx = [...svgEl.querySelectorAll('g.cand')].map((g) => Number((g.querySelector('.med') || {}).getAttribute ? g.querySelector('.med').getAttribute('cx') : NaN));
        const yOf = (() => { const meds = [...svgEl.querySelectorAll('g.cand .med')]; const vals = st.values.map((v) => v.median); if (meds.length < 2) return null; const a = meds[0], b = meds[meds.length - 1]; const va = vals[0], vb = vals[vals.length - 1]; const ya = Number(a.getAttribute('cy')), yb = Number(b.getAttribute('cy')); return va === vb ? null : (v) => ya + (v - va) * (yb - ya) / (vb - va); })();
        if (!yOf) return;
        const g = svg('g', { class: 'round-lines' });
        for (let r = 1; r <= OBJECTIVE.rounds; r++) {
          const pts = st.values.map((v, i) => { const p = rounds.find((q) => q.round === r && S.same(q.value, v.value)); return p && isNum(cx[i]) ? [cx[i], yOf(p.rate)] : null; }).filter(Boolean);
          if (pts.length > 1) g.appendChild(svg('path', { class: `round-line r${r}`, d: pts.map((p, k) => `${k ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join('') }));
        }
        svgEl.insertBefore(g, svgEl.firstChild.nextSibling);
      });
      cards.push(card);
    }
    return cards;
  }

  function objectiveCard() {
    return S.card({ q: 'Calibration objective against your workloads', sub: 'what tools/calibrate.py measures, and what it does not measure', cls: 'span3 objective' },
      el('div', { class: 'two-col' },
        el('div', null,
          el('h4', { text: 'The calibration measures' }),
          el('ul', { class: 'facts-list' },
            el('li', { text: `Decode speed only, on ${OBJECTIVE.prompts.length} short chat prompts.` }),
            el('li', { text: `${OBJECTIVE.tokens} tokens per answer for the PCIe share, the draft floor and the CPU workers.` }),
            el('li', { text: `${OBJECTIVE.tierTokens} tokens per answer for the expert tier (the tier needs longer answers to show an effect).` }),
            el('li', { text: `${OBJECTIVE.rounds} rounds per swept value in one engine start. It keeps a value only when it beats the default by more than ${OBJECTIVE.minGain} %.` })),
          el('h4', { text: 'Its prompts' }),
          el('ol', { class: 'facts-list prompts' }, OBJECTIVE.prompts.map((p) => el('li', { text: p })))),
        el('div', null,
          el('h4', { text: 'It does not measure' }),
          el('ul', { class: 'facts-list' },
            el('li', { text: 'Prompt reads (2K, 16K and 32K tokens). The prompt path streams every expert, whatever these settings say.' }),
            el('li', { text: 'Long-context decode (32K tokens of context).' }),
            el('li', { text: 'Agent turns: follow-up requests with 3K to 15K tokens of context and a reused prompt.' })),
          el('p', { class: 'note', text: 'The profile before and after the tune measures these workloads. The table below compares them.' }))));
  }

  async function gapCard(A, t, gains) {
    const box = el('div', { class: 'gap' }, el('p', { class: 'loading', text: 'Reading the profile before and after the tune …' }));
    const card = S.card({ q: 'Tune result per workload', sub: 'the calibration\'s own measure against the profile before and after the tune', cls: 'span3 gap-card' }, box);
    if (!t.before_report || !t.after_report) {
      S.fill(box, el('p', { class: 'empty-line', text: t.state === 'running' ? 'The profile after the tune has not run yet.' : 'This tune has no profile before and after.' }));
      return card;
    }
    A.compare(t.before_report, t.after_report).then((c) => {
      const row = (w) => c.rows.find((r) => r.group === 'decode' && r.key === w && r.metric === 'decode tok/s');
      const g = gains && gains.length ? gains.reduce((a, b) => (Math.abs(b.pct) > Math.abs(a.pct) ? b : a)) : null;
      const sign = c.sign || {};
      const lines = [
        { w: 'short chat', calib: g ? `${S.num(g.from.median, 1)} to ${S.num(g.to.median, 1)} tok/s (${S.signed(g.pct, 1, ' %')}, ${STEP_NAME[g.step.key]} ${g.from.value} to ${g.to.value})` : 'no change kept', r: row('chat') },
        { w: 'long context', calib: 'not measured', r: row('long context') },
        { w: 'agent turns', calib: 'not measured', r: row('agent turns'), sign },
      ];
      const lim = Math.max(5, ...lines.map((l) => Math.abs((l.r && l.r.pct) || 0)), ...lines.map((l) => (l.r ? l.r.floor * 100 : 0)), g ? Math.abs(g.pct) : 0) * 1.15;
      const x = (v) => 50 + (v / lim) * 50;
      const grid = el('div', { class: 'gap-grid' }, el('div', { class: 'gh', text: 'Workload' }), el('div', { class: 'gh', text: 'Calibration measure' }), el('div', { class: 'gh', text: 'Profile before and after (grey: noise floor)' }), el('div', { class: 'gh', text: 'Result' }));
      for (const l of lines) {
        const track = el('div', { class: 'f-track' });
        const zero = el('i', { class: 'f-zero' }); zero.style.left = '50%'; track.appendChild(zero);
        if (l.r) {
          const band = el('i', { class: `f-band ${l.r.floor_assumed ? 'assumed' : ''}` });
          band.style.left = `${x(-l.r.floor * 100)}%`; band.style.width = `${x(l.r.floor * 100) - x(-l.r.floor * 100)}%`; track.appendChild(band);
          const dot = el('i', { class: `f-dot ${l.r.state}` }); dot.style.left = `${x(l.r.pct)}%`; track.appendChild(dot);
        }
        if (l.w === 'short chat' && g) { const cd = el('i', { class: 'f-dot calib', title: 'calibration measure' }); cd.style.left = `${x(g.pct)}%`; track.appendChild(cd); }
        const signHit = l.sign && l.sign.turns >= (l.sign.min_turns || 5) && l.sign.p !== null && l.sign.p <= 0.05;
        let verdict = !l.r ? 'not in both runs' : l.r.state === 'better' ? 'faster' : l.r.state === 'worse' ? 'slower' : 'no measurable change';
        let vcls = l.r ? l.r.state : 'missing';
        if (signHit) { verdict = `${Math.max(l.sign.slower, l.sign.faster)} of ${l.sign.turns} turns ${l.sign.slower > l.sign.faster ? 'slower' : 'faster'} (sign test, p = ${S.num(l.sign.p, 3)})`; vcls = l.sign.slower > l.sign.faster ? 'worse' : 'better'; }
        grid.append(el('div', { class: 'gw', text: l.w }), el('div', { class: 'gc', text: l.calib }),
          el('div', { class: 'gp' }, track, el('span', { class: 'gpv num', text: l.r ? `${S.num(l.r.a, 1)} to ${S.num(l.r.b, 1)} tok/s (${S.signed(l.r.pct, 1, ' %')}, noise floor ±${S.num(l.r.floor * 100, 1)} %)` : '–' })),
          el('div', { class: `gv v-${vcls}`, text: verdict }));
      }
      const worse = lines.some((l) => (l.r && l.r.state === 'worse') || (l.sign && l.sign.turns >= 5 && l.sign.p <= 0.05 && l.sign.slower > l.sign.faster));
      const before = A.reports.find((r) => r.id === t.before_report);
      const decide = el('div', { class: 'decide' },
        el('span', { class: 'decide-q', text: worse ? 'Some workloads are slower after the tune. Keep the tuned settings?' : 'Keep the tuned settings?' }),
        S.button('Keep the tuned settings', async () => {
          try { await S.api('/api/notes', { body: { kind: 'tune', id: t.id, text: `${(t.note || '').replace(/^Kept\..*$/m, '').trim()}\nKept.`.trim() } }); S.toast('Recorded: kept.'); A.poll('tunes', true); } catch (e) { S.toast(e.message, 'bad'); }
        }, 'small', { disabled: A.readOnly() || !A.tunes.find((x) => x.id === t.id) }),
        S.button(`Use the settings of ${before ? S.runName(before.id, A.reports) : 'the run before'}`, () => { S.store.set('settings.report', t.before_report); S.go('#settings'); }, worse ? 'primary small' : 'small'),
        el('a', { href: `#compare/${t.before_report}/${t.after_report}`, text: 'Full comparison' }));
      S.fill(box, grid, el('div', { class: 'legend' },
        el('span', { class: 'lg' }, el('i', { class: 'sw dot calib' }), 'calibration measure'), el('span', { class: 'lg' }, el('i', { class: 'sw dot same' }), 'profile, inside the noise'),
        el('span', { class: 'lg' }, el('i', { class: 'sw dot worse' }), 'slower'), el('span', { class: 'lg' }, el('i', { class: 'sw dot better' }), 'faster')), decide);
    }).catch((e) => S.fill(box, el('p', { class: 'empty-line', text: e.message })));
    return card;
  }

  function calibrationGain(cand) {
    if (!cand || !cand.steps) return [];
    const out = [];
    for (const st of cand.steps) {
      if (!st.kept || !st.values.length) continue;
      const kept = st.values.find((v) => S.same(v.value, st.kept)), def = st.values.find((v) => S.same(v.value, st.default));
      if (kept && def && !S.same(kept.value, def.value) && isNum(kept.median) && isNum(def.median)) out.push({ step: st, from: def, to: kept, pct: (kept.median - def.median) / def.median * 100 });
    }
    return out;
  }

  function settingsCard(t) {
    const rows = (t.settings && t.settings.rows) || [];
    if (!rows.length) return null;
    return S.card({ q: 'Settings before and after the tune', sub: `the calibrated flags of the run config (source: ${t.settings.source === 'tune.report' ? 'Strata\'s tune report' : t.settings.source})` },
      el('div', { class: 'diffs' }, rows.map((r) => el('div', { class: `diff ${r.changed ? '' : 'same'}` },
        el('b', { text: r.flag }), el('span', { class: 'from', text: r.before ?? 'engine default' }),
        r.changed ? el('span', { text: 'to' }) : null, r.changed ? el('span', { class: 'to', text: r.after ?? 'engine default' }) : el('span', { class: 'muted small', text: 'unchanged' })))));
  }

  function historyCards(A, shownId) {
    const tunes = A.tunes.slice().reverse();                 // oldest first for the chart
    const spark = (vals) => { const sp = S.sparkline('hist'); sp.update(vals); return sp; };
    const gains = tunes.map((t) => { const g = calibrationGain(t.candidates); return g.length ? Math.max(...g.map((x) => x.pct)) : 0; });
    const cols = [
      { label: 'Started', get: (t) => S.clock(t.started_at, true) }, { label: 'Result', get: (t) => (t.outcome === 'failed' ? `failed at ${t.failed_stage}` : t.outcome) },
      { label: 'Duration', get: (t) => S.dur(t.ended_at - t.started_at) },
      { label: 'Calibration gain', get: (t) => { const g = calibrationGain(t.candidates); return g.length ? `${S.signed(Math.max(...g.map((x) => x.pct)), 1, ' %')}` : 'none kept'; } },
      { label: 'Best rate', num: true, get: (t) => (isNum(t.tok_s) ? `${S.num(t.tok_s, 1)} tok/s` : '–'), title: 'Decode speed of the best candidate, short chat' },
      { label: 'Gain over tunes', get: () => spark(gains) },
      { label: 'Settings changed', num: true, get: (t) => String(((t.settings && t.settings.rows) || []).filter((r) => r.changed).length) },
      { label: 'Kept', get: (t) => t.summary || '–' },
      { label: 'Profile runs', get: (t) => (t.before_report && t.after_report ? `${S.runName(t.before_report, A.reports)} to ${S.runName(t.after_report, A.reports)}` : '–') },
      { label: 'Note', get: (t) => t.note || '' },
    ];
    return [
      S.card({ q: 'Tune history', sub: `${A.tunes.length} tune${A.tunes.length === 1 ? '' : 's'} recorded by the Studio · select a tune to open it`, cls: 'span3' },
        A.tunes.length ? S.table(cols, A.tunes.map((t) => Object.assign({}, t, { _onclick: () => S.go(`#tune/${t.id}`), _cls: t.id === shownId ? 'sel' : '' }))) : el('p', { class: 'empty-line', text: 'No tune recorded yet.' })),
    ];
  }

  async function confirmTune(A) {
    const why = A.cannotTune();
    if (why) { S.toast(why, 'bad'); return; }
    const est = A.state && A.state.estimates;
    const cfg = A.state && A.state.studio && A.state.studio.config_path ? A.state.studio.config_path.split('/').pop() : 'strata-<model>.json';
    const ok = await S.confirm({
      title: 'Tune this PC now?',
      points: [
        `First a profile run (${est && est.profile_s ? `about ${S.dur(est.profile_s)}` : 'about 1 min'}). Then the calibration (${est && est.tune_s ? `about ${S.dur(est.tune_s)} the last time` : '15 to 30 min'}), a model load and a second profile run.`,
        'The calibration unloads the model. Strata answers requests with 503 until the calibration ends.',
        `Strata writes the values that it keeps into ${cfg}. It keeps a value only when the value beats the default by more than 3 %.`,
        'The calibration measures 128-token decodes of 3 short prompts. The profile before and after shows the effect on long and agent requests.',
        'You can cancel the tune only before the calibration starts.',
      ],
      check: 'Nobody needs Strata for the next 30 min.',
      ok: 'Tune this PC', danger: true,
    });
    if (!ok) return;
    try { await S.api('/api/tune-flow', { body: {} }); S.toast('The tune started.'); A.poll('flow', true); A.poll('state', true); S.go('#tune/pc'); } catch (e) { S.toast(e.message, 'bad'); }
  }

  function tick(A) {
    const cur = current(A, routeId(A.route));
    if (!cur || !live.steps) return;
    const t = cur.t;
    const seq = sequence(linesOf(t));
    const key = JSON.stringify([t.id, t.state, cur.kind, seq.points.length, (t.stages || []).map((s) => s.state), (t.candidates && t.candidates.outcome)]);
    if (key !== builtKey) { render(A, A.route, true); return; }
    updateTimeline(t, t.state === 'running' || cur.kind === 'elsewhere');
    if (live.elapsed) S.setText(live.elapsed, `${S.dur(cur.kind === 'elsewhere' ? (A.strata().tune || {}).elapsed_s : t.elapsed_s)} so far`);
  }

  function render(A, route, full) {
    const sec = document.getElementById('view-tune');
    // #tune is the workload pane; #tune/pc and #tune/<id> are the calibration of this PC
    const pane = route.parts[1] === undefined ? 'workload' : 'pc';
    const paneSeg = () => S.seg([['workload', 'My workload'], ['pc', 'This PC (calibration)']], pane,
      (v) => S.go(v === 'workload' ? '#tune' : '#tune/pc'), 'What to tune for');
    if (pane === 'workload') {
      live.steps = null; builtKey = null;                              // the calibration nodes are gone: tick does nothing
      const had = sec.dataset.pane === 'workload' && sec.querySelector('.workload-host');
      if (had && !full) return;                                        // the pane paints itself
      sec.dataset.pane = 'workload';
      const host = el('div', { class: 'workload-host' });
      const running = (A.flow && A.flow.state === 'running') || (A.strata().tune && A.strata().tune.state === 'running');
      S.fill(sec, el('div', { class: 'view-head' }, el('h2', { text: 'Tune' }), paneSeg(),
        el('span', { class: 'muted', text: 'replay your own requests with each setting, and keep only a change that beats the noise of this PC' })),
        running ? el('p', { class: 'note', text: 'A calibration of this PC is running. Open "This PC" to follow it.' }) : null, host);
      S.workload.mount(A, host);
      return;
    }
    const fresh = sec.dataset.pane !== 'pc';
    sec.dataset.pane = 'pc';
    const cur = current(A, routeId(route));
    if (!full && !fresh && cur && live.steps && sec.firstChild) {             // the same tune and the same measurements: in place
      const t0 = cur.t, sq = sequence(linesOf(t0));
      const k = JSON.stringify([t0.id, t0.state, cur.kind, sq.points.length, (t0.stages || []).map((s) => s.state), (t0.candidates && t0.candidates.outcome)]);
      if (k === builtKey) { tick(A); return; }
    }
    const my = ++token;
    const why = A.cannotTune();
    const running = A.flow && A.flow.state === 'running';
    const head = el('div', { class: 'view-head' }, el('h2', { text: 'Tune' }), paneSeg(),
      el('span', { class: 'muted', text: 'profile before, calibration, model load, profile after, comparison' }), el('span', { class: 'grow' }),
      S.button(running ? 'Tune in progress' : 'Tune this PC', () => confirmTune(A), 'primary', { disabled: !!why, title: why || 'The calibration unloads the model.' }));
    const parts = [head];
    if (why && !running && !(A.strata().tune && A.strata().tune.state === 'running')) parts.push(el('p', { class: 'note', text: `Tune not possible now: ${why}` }));
    if (!cur) {
      builtKey = null;
      parts.push(el('section', { class: 'card empty-card' }, el('h3', { text: 'No tune yet.' }),
        el('p', { class: 'sub', text: 'Tune this PC measures the PCIe share, the draft floor, the CPU workers and the expert tier. It keeps a value only when it beats the default by more than 3 %. A profile runs before and after the calibration.' })));
      parts.push(el('div', { class: 'grid' }, objectiveCard(), ...historyCards(A, null)));
      S.fill(sec, ...parts);
      return;
    }
    const t = cur.t;
    const seq = sequence(linesOf(t));
    builtKey = JSON.stringify([t.id, t.state, cur.kind, seq.points.length, (t.stages || []).map((s) => s.state), (t.candidates && t.candidates.outcome)]);
    const gains = calibrationGain(t.candidates);
    const isRunning = cur.kind === 'elsewhere' || t.state === 'running';
    const changedRows = ((t.settings && t.settings.rows) || []).filter((r) => r.changed);
    const top = gains.length ? gains.reduce((a, b) => (Math.abs(b.pct) > Math.abs(a.pct) ? b : a)) : null;
    live.elapsed = el('div', { class: 'tile-note num', text: '' });
    parts.push(el('div', { class: 'tiles' },
      el('div', { class: 'tile' }, el('div', { class: 'tile-label', text: cur.kind === 'elsewhere' ? 'Tune started on Strata\'s page' : isRunning ? 'Tune in progress' : 'Tune' }),
        el('div', { class: 'tile-value' }, el('span', { class: 'v', text: isNum(t.started_at) ? S.clock(t.started_at, true) : 'now' })),
        isRunning ? live.elapsed : el('div', { class: 'tile-note', text: `${t.outcome || t.state || ''}${t.ended_at && t.started_at ? ` · ${S.dur(t.ended_at - t.started_at)}` : ''}` })),
      S.tile('Calibration gain', top ? S.signed(top.pct, 1) : '–', top ? '%' : '', top ? `${STEP_NAME[top.step.key]} ${top.from.value} to ${top.to.value} · ${S.num(top.from.median, 1)} to ${S.num(top.to.median, 1)} tok/s` : (t.candidates && t.candidates.outcome === 'defaults' ? 'the defaults were already the fastest' : ''), 'hero'),
      S.tile('Settings changed', String(changedRows.length), '', changedRows.map((r) => `${r.flag.replace(/^--/, '')} ${r.before ?? 'default'} to ${r.after ?? 'default'}`).join(' · ')),
      S.tile('Measurements', String(seq.points.length), '', `${seq.starts} engine starts`)));
    const tools = el('div', { class: 'card-tools' },
      t.can_cancel ? S.button('Cancel', async () => { try { await S.api('/api/tune-flow/cancel', { body: {} }); S.toast('The tune stops after the profile run.'); A.poll('flow', true); } catch (e) { S.toast(e.message, 'bad'); } }, 'small') : null,
      t.can_retry_after ? S.button('Run the profile after again', async () => { try { await S.api('/api/tune-flow/retry-after', { body: {} }); A.poll('flow', true); } catch (e) { S.toast(e.message, 'bad'); } }, 'small primary') : null);
    const grid = el('div', { class: 'grid' });
    if (t.stages && t.stages.length) { const tc = timelineCard(t); tc.querySelector('.card-head').appendChild(tools); grid.appendChild(tc); }
    else if (cur.kind === 'elsewhere') {
      live.log = el('pre', { class: 'log', text: '' }); live.steps = []; live.gantt = [];
      grid.appendChild(S.card({ q: 'Tune started on Strata\'s page', sub: 'read only: the Studio shows its output', cls: 'span3' }, live.log));
    }
    grid.append(sequenceCard(t, seq), logCard(t));
    grid.appendChild(el('div', { class: 'sweeps span3' }, sweepCards(t, seq, isRunning)));
    grid.appendChild(el('div', { class: 'span3 legend sweep-legend' },
      el('span', { class: 'lg' }, el('i', { class: 'sw ring-kept' }), 'kept'), el('span', { class: 'lg' }, el('i', { class: 'sw ring-best' }), 'best, not kept'),
      el('span', { class: 'lg' }, el('i', { class: 'sw med' }), 'median'), el('span', { class: 'lg' }, el('i', { class: 'sw round' }), 'one round'),
      el('span', { class: 'lg' }, el('i', { class: 'sw round-l' }), 'one line per round'),
      el('span', { class: 'lg', text: 'was: the value before this tune · default: the value the calibration compares with' })));
    grid.appendChild(objectiveCard());
    S.fill(sec, ...parts, grid);
    updateTimeline(t, isRunning);
    if (live.elapsed) S.setText(live.elapsed, `${S.dur(cur.kind === 'elsewhere' ? (A.strata().tune || {}).elapsed_s : t.elapsed_s)} so far`);
    (async () => {
      const gap = await gapCard(A, t, gains);
      if (my !== token) return;
      grid.appendChild(gap);
      const sc = settingsCard(t);
      if (sc) grid.appendChild(sc);
      for (const c of historyCards(A, t.id)) grid.appendChild(c);
    })();
  }

  S.viewDefs.tune = { render, tick };
})();
