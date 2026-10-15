/* Strata Profile Studio: Tune.  The one-click flow (profile before, calibrate, reload, profile after, compare) with
   its progress; what the calibration measured per setting (kept, best, was); the gap between what the calibration
   optimises and what the profile saw on the other workloads; the decision; the history of tunes. */
'use strict';

(function () {
  const { el, isNum } = S;
  S.viewDefs = S.viewDefs || {};
  let token = 0;

  const STEP_Q = {
    pcie_frac: 'Which PCIe share did the calibration pick?',
    spec_min_p: 'Which draft floor did it pick?',
    pool_workers: 'How many CPU workers?',
    adapt: 'Which expert tier?',
  };
  const STAGE_LABEL = { check: 'Check Strata', before: 'Profile before', tune: 'Calibrate', reload: 'Load the model', after: 'Profile after', compare: 'Compare' };
  const STEP_FLAG = { pcie_frac: '--pcie-frac', spec_min_p: '--spec-min-p', pool_workers: '--pool-workers', adapt: '--adapt-*' };

  // the tune to show: #tune/<id>, else the running or last flow of this Studio, else the newest record
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

  // the calibration's own measure of what it changed: the default candidate against the kept one
  function calibrationGain(cand) {
    if (!cand || !cand.steps) return null;
    const out = [];
    for (const st of cand.steps) {
      if (!st.kept || !st.values.length) continue;
      const kept = st.values.find((v) => S.same(v.value, st.kept));
      const def = st.values.find((v) => S.same(v.value, st.default));
      if (kept && def && !S.same(kept.value, def.value) && isNum(kept.median) && isNum(def.median)) {
        out.push({ step: st, from: def, to: kept, pct: (kept.median - def.median) / def.median * 100 });
      }
    }
    return out;
  }

  async function confirmTune(A) {
    const why = A.cannotTune();
    if (why) { S.toast(why, 'bad'); return; }
    const est = A.state && A.state.estimates;
    const cfg = A.state && A.state.studio && A.state.studio.config_path ? A.state.studio.config_path.split('/').pop() : 'strata-<model>.json';
    const ok = await S.confirm({
      title: 'Tune this PC now?',
      points: [
        `First a profile run (${est && est.profile_s ? `about ${S.dur(est.profile_s)}` : 'about a minute'}), then the calibration (${est && est.tune_s ? `about ${S.dur(est.tune_s)} last time` : 'Strata says 15-30 minutes'}), a reload and a second profile run.`,
        'The model is unloaded for the whole calibration: Strata answers requests with 503 meanwhile.',
        `Strata writes the values it keeps into ${cfg} (it keeps a value only when it beats the default by 3%).`,
        'The calibration measures 128-token decodes of three short prompts; the profile before and after shows what it does to long and agent requests.',
        'It can be cancelled only before the calibration starts.',
      ],
      check: 'Nobody needs Strata for the next half hour.',
      ok: 'Tune this PC', danger: true,
    });
    if (!ok) return;
    try {
      await S.api('/api/tune-flow', { body: {} });
      S.toast('The tune started.');
      A.poll('flow', true); A.poll('state', true);
      S.go('#tune');
    } catch (e) { S.toast(e.message, 'bad'); }
  }

  function stepper(A, cur) {
    const t = cur.t;
    const stages = t.stages || [];
    const box = el('ol', { class: 'stepper' });
    for (const st of stages) {
      const icon = { done: '✓', running: '', failed: '✕', skipped: '–', pending: '' }[st.state] || '';
      const timeTxt = st.state === 'running' && isNum(st.elapsed_s) ? `${S.dur(st.elapsed_s)}${st.timeout_s ? ` of at most ${S.dur(st.timeout_s)}` : ''}` : isNum(st.elapsed_s) ? S.dur(st.elapsed_s) : '';
      box.appendChild(el('li', { class: `step ${st.state}` },
        el('span', { class: 'step-dot', text: icon, 'aria-hidden': 'true' }),
        el('span', { class: 'step-label', text: STAGE_LABEL[st.key] || st.label || st.key }),
        el('span', { class: 'step-time', text: timeTxt }),
        st.message && st.state === 'failed' ? el('span', { class: 'step-msg', text: st.message }) : null));
    }
    return box;
  }

  function sweepCards(A, cand, running) {
    const cards = [];
    for (const st of (cand && cand.steps) || []) {
      const host = el('div', { class: 'chart-host' });
      const kept = st.kept, best = st.best;
      const keptV = st.values.find((v) => S.same(v.value, kept)), bestV = st.values.find((v) => S.same(v.value, best));
      let verdict = '';
      if (kept && best && !S.same(kept, best) && keptV && bestV && isNum(bestV.median) && isNum(keptV.median)) verdict = `Best ${best} (${S.num(bestV.median, 1)}) not kept: ${S.signed((bestV.median - keptV.median) / keptV.median * 100, 1, '%')} over ${kept} is under the 3% the calibration asks for.`;
      else if (kept) verdict = `Kept ${kept === 'default' ? 'the engine default' : kept}${st.was && !S.same(st.was, kept) ? ` (was ${st.was})` : ''}.`;
      else if (running && st.values.length) verdict = 'measuring…';
      const card = S.card({ q: STEP_Q[st.key] || st.label, sub: `${STEP_FLAG[st.key] || ''} · decode tok/s of the calibration's chat · ${st.kind === 'sweep' ? 'dots: 3 rounds, bar: min–max' : 'one engine start each'}`, verdict, cls: `sweep-card ${st.kind}` }, host);
      requestAnimationFrame(() => S.sweep(host, st, { empty: running ? 'not measured yet' : 'not measured' }));
      cards.push(card);
    }
    return cards;
  }

  async function gapCard(A, t, gains) {
    const box = el('div', { class: 'gap' }, el('p', { class: 'loading', text: 'Comparing the profile before and after…' }));
    const card = S.card({ q: 'Did the tune help the requests you actually send?', sub: 'the calibration optimises 128-token decodes of three short prompts; the profile before and after measures the rest', cls: 'span3 gap-card' }, box);
    if (!t.before_report || !t.after_report) {
      S.fill(box, el('p', { class: 'empty-line', text: t.state === 'running' ? 'The profile after the tune has not run yet.' : 'This tune has no before and after profile to compare.' }));
      return card;
    }
    A.compare(t.before_report, t.after_report).then((c) => {
      const row = (w) => c.rows.find((r) => r.group === 'decode' && r.key === w && r.metric === 'decode tok/s');
      const g = gains && gains.length ? gains.reduce((a, b) => (Math.abs(b.pct) > Math.abs(a.pct) ? b : a)) : null;
      const sign = c.sign || {};
      const lines = [
        { w: 'short chat', calib: g ? `${S.num(g.from.median, 1)} → ${S.num(g.to.median, 1)} (${S.signed(g.pct, 1, '%')}, ${g.step.label} ${g.from.value} → ${g.to.value})` : 'no change kept', r: row('chat') },
        { w: 'long context', calib: 'not measured', r: row('long context') },
        { w: 'agent turns', calib: 'not measured', r: row('agent turns'), sign },
      ];
      const lim = Math.max(5, ...lines.map((l) => Math.abs((l.r && l.r.pct) || 0)), ...lines.map((l) => (l.r ? l.r.floor * 100 : 0)), g ? Math.abs(g.pct) : 0) * 1.15;
      const x = (v) => 50 + (v / lim) * 50;
      const grid = el('div', { class: 'gap-grid' },
        el('div', { class: 'gh', text: 'workload' }), el('div', { class: 'gh', text: "the calibration's own measure" }), el('div', { class: 'gh', text: 'your profile, before → after (grey: noise)' }), el('div', { class: 'gh', text: 'verdict' }));
      for (const l of lines) {
        const track = el('div', { class: 'f-track' });
        const zero = el('i', { class: 'f-zero' }); zero.style.left = '50%'; track.appendChild(zero);
        if (l.r) {
          const band = el('i', { class: `f-band ${l.r.floor_assumed ? 'assumed' : ''}` });
          band.style.left = `${x(-l.r.floor * 100)}%`; band.style.width = `${x(l.r.floor * 100) - x(-l.r.floor * 100)}%`; track.appendChild(band);
          const dot = el('i', { class: `f-dot ${l.r.state}` }); dot.style.left = `${x(l.r.pct)}%`; track.appendChild(dot);
        }
        if (l.w === 'short chat' && g) { const cd = el('i', { class: 'f-dot calib', title: "the calibration's measure" }); cd.style.left = `${x(g.pct)}%`; track.appendChild(cd); }
        const signHit = l.sign && l.sign.turns >= (l.sign.min_turns || 5) && l.sign.p !== null && l.sign.p <= 0.05;
        let verdict = !l.r ? 'not in both runs' : l.r.state === 'better' ? 'faster' : l.r.state === 'worse' ? 'slower' : 'no measurable change';
        let vcls = l.r ? l.r.state : 'missing';
        if (signHit) { verdict = `${Math.max(l.sign.slower, l.sign.faster)} of ${l.sign.turns} turns ${l.sign.slower > l.sign.faster ? 'slower' : 'faster'} (sign test, p ${S.num(l.sign.p, 3)})`; vcls = l.sign.slower > l.sign.faster ? 'worse' : 'better'; }
        grid.append(el('div', { class: 'gw', text: l.w }), el('div', { class: 'gc', text: l.calib }),
          el('div', { class: 'gp' }, track, el('span', { class: 'gpv', text: l.r ? `${S.num(l.r.a, 1)} → ${S.num(l.r.b, 1)} (${S.signed(l.r.pct, 1, '%')}, noise ±${S.num(l.r.floor * 100, 1)}%)` : '–' })),
          el('div', { class: `gv v-${vcls}`, text: verdict }));
      }
      const worse = lines.some((l) => (l.r && l.r.state === 'worse') || (l.sign && l.sign.turns >= 5 && l.sign.p <= 0.05 && l.sign.slower > l.sign.faster));
      const before = A.reports.find((r) => r.id === t.before_report);
      const decide = el('div', { class: 'decide' },
        el('span', { class: 'decide-q', text: worse ? 'Some of your workloads got slower. Keep the tuned settings?' : 'Keep the tuned settings?' }),
        S.button('Keep them', async () => {
          try { await S.api('/api/notes', { body: { kind: 'tune', id: t.id, text: `${(t.note || '').replace(/^Kept\..*$/m, '').trim()}\nKept.`.trim() } }); S.toast('Noted: kept.'); A.poll('tunes', true); } catch (e) { S.toast(e.message, 'bad'); }
        }, 'small', { disabled: A.readOnly() || !A.tunes.find((x) => x.id === t.id) }),
        S.button(`Go back to the settings of ${before ? S.runName(before.id, A.reports) : 'the run before'}…`, () => { S.store.set('settings.report', t.before_report); S.go('#settings'); }, worse ? 'primary small' : 'small'),
        el('a', { href: `#compare/${t.before_report}/${t.after_report}`, text: 'the full comparison →' }));
      S.fill(box, grid, el('div', { class: 'legend' },
        el('span', { class: 'lg' }, el('i', { class: 'sw dot calib' }), "calibration's measure"),
        el('span', { class: 'lg' }, el('i', { class: 'sw dot same' }), 'profile, inside the noise'),
        el('span', { class: 'lg' }, el('i', { class: 'sw dot worse' }), 'slower'), el('span', { class: 'lg' }, el('i', { class: 'sw dot better' }), 'faster')), decide);
    }).catch((e) => S.fill(box, el('p', { class: 'empty-line', text: e.message })));
    return card;
  }

  function settingsCard(t) {
    const rows = (t.settings && t.settings.rows) || [];
    if (!rows.length) return null;
    return S.card({ q: 'What did the tune change?', sub: `the run config's calibrated flags, before and after (from ${t.settings.source === 'tune.report' ? "Strata's tune report" : t.settings.source})` },
      el('div', { class: 'diffs' }, rows.map((r) => el('div', { class: `diff ${r.changed ? '' : 'same'}` },
        el('b', { text: r.flag }), el('span', { class: 'from', text: r.before ?? 'engine default' }),
        r.changed ? el('span', { text: '→' }) : null, r.changed ? el('span', { class: 'to', text: r.after ?? 'engine default' }) : el('span', { class: 'muted small', text: 'unchanged' })))));
  }

  function historyCard(A, shownId) {
    const cols = [
      { label: 'started', get: (t) => S.clock(t.started_at, true) }, { label: 'outcome', get: (t) => (t.outcome === 'failed' ? `failed at ${t.failed_stage}` : t.outcome) },
      { label: 'took', get: (t) => S.dur(t.ended_at - t.started_at) }, { label: 'kept', get: (t) => (t.summary || '–') },
      { label: 'calibration', num: true, get: (t) => (isNum(t.tok_s) ? `${S.num(t.tok_s, 1)} tok/s` : '–') },
      { label: 'before → after', get: (t) => (t.before_report && t.after_report ? `${S.runName(t.before_report, A.reports)} → ${S.runName(t.after_report, A.reports)}` : '–') },
      { label: 'note', get: (t) => t.note || '' },
    ];
    return S.card({ q: 'Every tune', sub: `${A.tunes.length} tune${A.tunes.length === 1 ? '' : 's'} recorded by the Studio · click one to open it`, cls: 'span2' },
      A.tunes.length ? S.table(cols, A.tunes.map((t) => Object.assign({}, t, { _onclick: () => S.go(`#tune/${t.id}`), _cls: t.id === shownId ? 'sel' : '' })))
        : el('p', { class: 'empty-line', text: 'No tune recorded yet.' }));
  }

  function render(A, route) {
    const my = ++token;
    const sec = document.getElementById('view-tune');
    const cur = current(A, route.parts[1]);
    const why = A.cannotTune();
    const running = A.flow && A.flow.state === 'running';
    const head = el('div', { class: 'view-head' },
      el('h2', { text: 'Tune' }),
      el('span', { class: 'muted', text: 'profile before → calibrate → reload → profile after → compare' }),
      el('span', { class: 'grow' }),
      S.button(running ? 'Tuning…' : 'Tune this PC…', () => confirmTune(A), 'primary', { disabled: !!why, title: why || 'unloads the model for the calibration' }));
    const parts = [head];
    if (why && !running && !(A.strata().tune && A.strata().tune.state === 'running')) parts.push(el('p', { class: 'note', text: `Cannot tune now: ${why}` }));
    if (!cur) {
      parts.push(el('section', { class: 'card empty-card' }, el('h3', { text: 'No tune yet.' }),
        el('p', { class: 'sub', text: 'Tune this PC measures the PCIe share, the draft floor, the CPU workers and the expert tier, keeps what beats the default by 3%, and profiles before and after so you can see what it did to your workloads.' })));
      parts.push(historyCard(A, null));
      S.fill(sec, ...parts);
      return;
    }
    const t = cur.t;
    const cand = t.candidates;
    const gains = calibrationGain(cand);
    const isRunning = cur.kind === 'elsewhere' || t.state === 'running';
    const ended = t.ended_at || null;
    const changedRows = ((t.settings && t.settings.rows) || []).filter((r) => r.changed);
    const top = gains && gains.length ? gains.reduce((a, b) => (Math.abs(b.pct) > Math.abs(a.pct) ? b : a)) : null;
    parts.push(el('div', { class: 'tiles' },
      S.tile(cur.kind === 'elsewhere' ? 'tune started from Strata\'s page' : isRunning ? 'tune running' : 'tune', isNum(t.started_at) ? S.clock(t.started_at, true) : (cur.kind === 'elsewhere' ? 'running' : '–'), '',
        isRunning ? `${S.dur(cur.kind === 'elsewhere' ? (A.strata().tune || {}).elapsed_s : t.elapsed_s)} so far` : `${t.outcome || t.state || ''}${ended && t.started_at ? ` · ${S.dur(ended - t.started_at)}` : ''}`),
      S.tile("calibration's gain", top ? S.signed(top.pct, 1) : '–', top ? '%' : '', top ? `${top.step.label} ${top.from.value} → ${top.to.value} · ${S.num(top.from.median, 1)} → ${S.num(top.to.median, 1)} tok/s` : (cand && cand.outcome === 'defaults' ? 'the defaults were already fastest' : ''), 'hero'),
      S.tile('settings changed', String(changedRows.length), '', changedRows.map((r) => `${r.flag.replace(/^--/, '')} ${r.before ?? 'default'} → ${r.after ?? 'default'}`).join(' · ')),
      S.tile('calibration rate', isNum(t.tok_s) ? S.num(t.tok_s, 1) : '–', 'tok/s', 'its best candidate, short chat decode')));
    if (t.stages && t.stages.length) {
      const tools = el('div', { class: 'card-tools' },
        t.can_cancel ? S.button('Cancel', async () => { try { await S.api('/api/tune-flow/cancel', { body: {} }); S.toast('It stops after the running profile.'); A.poll('flow', true); } catch (e) { S.toast(e.message, 'bad'); } }, 'small') : null,
        t.can_retry_after ? S.button('Run the profile after again', async () => { try { await S.api('/api/tune-flow/retry-after', { body: {} }); A.poll('flow', true); } catch (e) { S.toast(e.message, 'bad'); } }, 'small primary') : null);
      const card = S.card({ q: isRunning ? 'Where is the tune?' : 'How did the tune go?', sub: t.error ? `stopped: ${t.error}` : 'each step with its time', cls: 'span3', tools }, stepper(A, cur),
        isRunning && t.lines && t.lines.length ? el('pre', { class: 'log' }, t.lines.slice(-10).join('\n')) : null);
      parts.push(card);
    } else if (cur.kind === 'elsewhere') {
      parts.push(S.card({ q: 'Strata is tuning (started from its own page)', sub: 'read only: the Studio follows its output', cls: 'span3' }, el('pre', { class: 'log' }, (t.lines || []).slice(-10).join('\n'))));
    }
    const grid = el('div', { class: 'grid' });
    const sweeps = el('div', { class: 'sweeps span3' }, sweepCards(A, cand, isRunning));
    grid.appendChild(sweeps);
    grid.appendChild(el('div', { class: 'span3 legend sweep-legend' },
      el('span', { class: 'lg' }, el('i', { class: 'sw ring-kept' }), 'kept'), el('span', { class: 'lg' }, el('i', { class: 'sw ring-best' }), 'best, not kept'),
      el('span', { class: 'lg' }, el('i', { class: 'sw med' }), 'median'), el('span', { class: 'lg' }, el('i', { class: 'sw round' }), 'one round'),
      el('span', { class: 'lg', text: 'was: before this tune · default: what the calibration measured against' })));
    parts.push(grid);
    S.fill(sec, ...parts);
    (async () => {
      const gap = await gapCard(A, t, gains);
      if (my !== token) return;
      grid.appendChild(gap);
      const sc = settingsCard(t);
      if (sc) grid.appendChild(sc);
      grid.appendChild(historyCard(A, t.id));
    })();
  }

  S.viewDefs.tune = { render };
})();
