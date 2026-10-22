/* Strata Profile Studio: the tune with the standard test.  One card in the Standard test pane of the Tune view.
   The Studio replays one 6-turn conversation with each candidate setting, in turns, and keeps a change only
   when the change is larger than the noise of this PC.  Only settings that do not change the answers are tried.
   The settings that change the answers are in Settings. */
'use strict';

(function () {
  const { el, isNum } = S;
  const form = { quick: true, apply: false };
  let plan = null, planError = null, planFor = '', snap = null, timer = null, host = null;

  const STAGE_LABEL = { quiet: 'Strata is quiet', analyse: 'Read the requests', calibrate: 'Measure the prompt size', measure: 'Compare the settings', apply: 'Write the config', restart: 'Restart Strata', verify: 'Check after the restart' };
  const VERDICT_CLS = { better: 'good', worse: 'bad', 'no change': '', 'not enough rounds': 'warn' };

  const pc = (x) => S.pct(Math.abs(x) < 0.0005 ? 0 : x, 1);
  const minutes = (s) => (isNum(s) ? (s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`) : 'unknown time');

  async function loadPlan(A) {
    const key = `${form.quick}`;
    if (planFor === key && (plan || planError)) return;
    planFor = key;
    try {
      const q = new URLSearchParams({ quick: form.quick ? '1' : '0' });
      plan = await S.api(`/api/mytune/plan?${q}`);
      planError = null;
    } catch (e) {
      plan = null;
      planError = e.message;
    }
    redraw(A);
  }

  async function loadState(A) {
    try { snap = await S.api('/api/mytune'); } catch (e) { snap = null; }
    redraw(A);
    const running = snap && snap.state === 'running';
    if (running && !timer) timer = setInterval(() => loadState(A), 3000);
    if (!running && timer) { clearInterval(timer); timer = null; }
  }

  function redraw(A) {
    if (host && host.isConnected) S.fill(host, ...body(A));
  }

  async function start(A) {
    const mins = plan && plan.seconds ? `about ${minutes(plan.seconds)}` : 'some minutes';
    const writes = form.apply;
    const ok = await S.confirm({
      title: 'Start the tune with the standard test?',
      points: [
        `It sends test requests to Strata for ${mins}. Strata answers other clients slowly or not at all in that time.`,
        'The test is a conversation of 6 turns. It fills the conversation cache of Strata with its own conversations, so the next request of your own session may have to read its prompt again.',
        'Nobody else should use Strata while it runs. A round in which another request arrives is dropped.',
        'It tries only settings that do not change the answers. It compares the text of every try with the text of the current settings.',
        writes
          ? (plan && plan.restart_command
            ? 'It writes the best setting into the run config (after a dated copy of the file), restarts Strata, checks the result and puts the old config back if the new one is slower.'
            : 'It writes the best setting into the run config (after a dated copy of the file). You restart Strata yourself.')
          : 'It writes nothing. It shows what it found.'],
      ok: 'Start the tune', danger: writes,
    });
    if (!ok) return;
    const b = { confirm: true, quick: form.quick, apply: form.apply, apply_without_restart: form.apply && !(plan && plan.restart_command) };
    try {
      await S.api('/api/mytune', { body: b });
      S.toast('The tune started.');
      loadState(A);
    } catch (e) { S.toast(e.message, 'bad'); }
  }

  function planBlock(A) {
    const out = [];
    out.push(el('div', { class: 'view-head' },
      S.seg([[true, 'Quick'], [false, 'Full']], form.quick, (v) => { form.quick = v; planFor = ''; loadPlan(A); redraw(A); }, 'How many settings')));
    if (planError) {
      out.push(el('p', { class: 'note', text: planError }));
      return out;
    }
    if (!plan) {
      out.push(el('p', { class: 'note', text: 'Reading the plan…' }));
      return out;
    }
    const cur = plan.current || {};
    out.push(el('p', { class: 'note', text: plan.note }));
    out.push(el('p', { class: 'note', text: `Now: pcie-frac ${cur.pcie_frac ?? 'engine default'}, spec-min-p ${cur.spec_min_p ?? 'engine default'}. `
      + `It tries ${plan.arms.length} settings (${plan.arms.join(', ')}), ${plan.rounds} rounds each, and the current settings twice. `
      + `This takes ${isNum(plan.seconds) ? `about ${minutes(plan.seconds)}` : 'a time that the Studio cannot estimate'}.` }));
    if (!plan.config_readable) out.push(el('p', { class: 'note', text: 'The Studio cannot read the run config (start it with --config), so it cannot start the tune.' }));
    const check = el('input', { type: 'checkbox', id: 'mt-apply', onchange: (e) => { form.apply = e.target.checked; redraw(A); } });
    check.checked = form.apply;
    out.push(el('label', { class: 'check' }, check, ' ',
      plan.restart_command ? 'Write the result, restart Strata, check it, and go back if the new config is slower'
        : 'Write the result into the config (the Studio has no restart command: restart Strata yourself)'));
    const go = S.button('Start the tune', () => start(A), 'primary', { disabled: !plan.config_readable || A.readOnly() || !plan.shapes.length });
    out.push(go);
    return out;
  }

  function stagesBlock(s) {
    const rows = Object.entries(s.stages || {}).map(([k, v]) => ({ k, v }));
    const cols = [
      { label: 'Step', get: (r) => STAGE_LABEL[r.k] || r.k },
      { label: 'State', get: (r) => S.chip(r.v, r.v === 'done' ? 'good' : (r.v === 'failed' ? 'bad' : '')) },
    ];
    return S.table(cols, rows);
  }

  function resultBlock(res) {
    const out = [];
    const rows = Object.entries(res.arms || {}).map(([name, a]) => ({
      name, verdict: a.verdict, gain: isNum(a.gain) ? pc(a.gain) : '–',
      ci: a.ci ? `${pc(-a.ci[1])} to ${pc(-a.ci[0])}` : '–',
      same: a.same_output === null ? 'not checked' : (a.same_output ? 'same' : 'different'), ms: S.num(a.median_ms, 1),
    }));
    const cols = [
      { label: 'Setting', key: 'name' },
      { label: 'Result', get: (r) => S.chip(r.verdict, VERDICT_CLS[r.verdict] || '') },
      { label: 'Faster by', key: 'gain', num: true, title: 'The median change over the rounds. Positive: less time for the same work.' },
      { label: '95% interval', key: 'ci', num: true },
      { label: 'ms per token', key: 'ms', num: true, title: 'The median time that Strata spent for each generated token, over the 6 turns of one pass. Less is faster.' },
      { label: 'Text', key: 'same', title: 'Is the text of the reply the same as with the current settings?' },
    ];
    out.push(S.table(cols, rows));
    out.push(el('p', { class: 'note', text: `${res.rounds} clean rounds${res.spoiled ? `, ${res.spoiled} dropped` : ''}. `
      + `The noise of this PC (the median difference between two copies of the current settings): ${S.pct(res.floor, 1)}. A change must be larger than that and larger than 2%.`
      + (res.output_check === 'unreliable' ? ' The text of the two copies of the current settings differed, so the text check was not used.' : '') }));
    if (res.winner) {
      out.push(el('p', { class: 'verdict', text: `Best: ${res.winner.name}, ${S.pct(res.winner.gain, 1)} faster on the standard test.` }));
    }
    if (res.applied) {
      out.push(el('p', { class: 'note', text: `Written to the config. The copy of the old file: ${res.applied.backup}` }));
    }
    if (res.verify) {
      out.push(el('p', { class: 'note', text: `After the restart: ${res.verify.verdict === 'worse' ? 'the new config is faster than the old values' : res.verify.verdict === 'better' ? 'the old values are faster than the new config' : 'no difference larger than the noise'} (${res.verify.rounds} rounds).` }));
    }
    if (res.rolled_back) out.push(el('p', { class: 'note', text: 'The old config is back and Strata was restarted.' }));
    return out;
  }

  function body(A) {
    const kids = [];
    const running = snap && snap.state === 'running';
    if (running) {
      const p = snap.progress;
      kids.push(el('p', { class: 'note', text: snap.message || '' }));
      if (p) kids.push(el('p', { class: 'note', text: `${p.label}: round ${p.round} of ${p.rounds}${p.spoiled ? `, ${p.spoiled} dropped` : ''}.` }));
      kids.push(stagesBlock(snap));
      kids.push(S.button('Cancel', async () => { try { await S.api('/api/mytune/cancel', { body: {} }); S.toast('The tune stops after this request.'); } catch (e) { S.toast(e.message, 'bad'); } }));
      return kids;
    }
    if (snap && snap.state && snap.state !== 'idle') {
      const bad = snap.state === 'failed' || snap.state === 'cancelled' || snap.state === 'rolled_back';
      kids.push(el('p', { class: bad ? 'note warn' : 'note', text: snap.message || snap.state }));
      if (snap.result) kids.push(...resultBlock(snap.result));
      if (snap.log && snap.log.length) kids.push(el('p', { class: 'note small', text: snap.log.slice(-3).join(' ') }));
      kids.push(el('h4', { text: 'Run again' }));
    }
    kids.push(...planBlock(A));
    kids.push(el('p', { class: 'note small' }, 'Settings that change the answers (thinking budget, route tail skip) are not tried here. Set them in ',
      el('a', { href: '#settings', text: 'Settings' }), '.'));
    return kids;
  }

  S.mytune = {
    card(A) {
      host = el('div', { class: 'mytune' });
      if (!snap) loadState(A);
      loadPlan(A);
      const card = S.card({ q: 'Tune with the standard test', sub: 'Replays one 6-turn conversation with each setting, in turns, and keeps a change only when it is larger than the noise of this PC.' }, host);
      S.fill(host, ...body(A));
      return card;
    },
  };
})();
