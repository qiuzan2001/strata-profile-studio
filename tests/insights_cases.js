'use strict';
// Cases for web/insights.js: run with `node tests/insights_cases.js` (tests/test_insights.py does).
const assert = require('assert');
const path = require('path');
const SI = require(path.join(__dirname, '..', 'web', 'insights.js'));

let n = 0;
function ok(cond, msg) { assert.ok(cond, msg); n += 1; }
function eq(a, b, msg) { assert.strictEqual(a, b, msg); n += 1; }
const text = (r) => r.lines.join(' ');

const win = (gpu, cpu, draft, adapt, other, extra) => Object.assign({
  window_ms: { GPU: gpu, 'CPU experts': cpu, draft, 'adapt wait': adapt, other },
  ms_per_window: gpu + cpu + draft + adapt + other, hit_rate: 0.77, decode_tok_s: 60, tag: null, source: 'last',
}, extra || {});

// ---- live
{
  const r = SI.live([], {});
  ok(/No request has been seen yet/.test(text(r)), 'empty live');
  const s = win(9, 18, 2, 1, 1);
  const t = text(SI.live([s, s, s], {}));
  ok(/Most of each decode window waits for experts computed on the CPU \(18 of 31 ms\)/.test(t), t);
  ok(/the expert cache hit rate is 77%/.test(t), t);
  ok(/the card itself takes 9 ms/.test(t), t);
  ok(/expert_profile_save/.test(t), 'what would change it');
  // 15 of 31 ms is under half: the careful wording
  ok(/^The biggest part of each decode window is waiting for experts computed on the CPU \(15 of 31 ms\)/.test(text(SI.live([win(9, 15, 2, 4, 1)], {}))), 'not a majority');
  const flat = win(8, 9, 7, 6, 5);
  const t2 = text(SI.live([flat], {}));
  ok(/^The biggest part of each decode window is waiting for experts computed on the CPU \(9 of 35 ms\)/.test(t2), t2);
  // GPU biggest: no "the card itself takes" clause, the card is the limit
  const t3 = text(SI.live([win(20, 5, 1, 1, 1)], {}));
  ok(/Most of each decode window is the card itself computing \(20 of 28 ms\)/.test(t3), t3);
  ok(!/the card itself takes/.test(t3), t3);
  ok(/The card is the limit/.test(t3), t3);
  // adapt wait / draft / other
  ok(/adaptive expert tier/.test(text(SI.live([win(3, 3, 1, 12, 1)], {}))), 'adapt');
  ok(/--spec-min-p/.test(text(SI.live([win(3, 3, 12, 1, 1)], {}))), 'draft');
  ok(/Nothing stands out/.test(text(SI.live([win(3, 3, 1, 1, 12)], {}))), 'other');
  // tagged samples are ignored when plain ones exist
  const tuned = win(1, 1, 1, 30, 1, { tag: 'tune' });
  const t4 = text(SI.live([s, tuned], {}));
  ok(/18 of 31 ms/.test(t4), t4);
  ok(/adaptive/.test(text(SI.live([tuned], {}))), 'only tagged: use them');
  // only the newest ten count
  const many = [win(1, 1, 1, 1, 1)].concat(Array.from({ length: 10 }, () => s));
  ok(/18 of 31 ms/.test(text(SI.live(many, {}))), 'newest ten');
  // prompt phase sentence
  const p = Object.assign({}, s, { read: 2000, prompt_groups_ms: { 'experts over PCIe': 43, dequant: 15, 'expert GEMM': 18, attention: 5, GDN: 13, other: 6 } });
  ok(/In the newest prompt read, experts over PCIe took 43% of the card’s time\./.test(text(SI.live([p], {}))), 'prompt');
  // profile off
  const off = text(SI.live([{ decode_tok_s: 63.04, hit_rate: 0.7, window_ms: null }], { profileOff: true }));
  ok(/63\.0 tok\/s/.test(off) && /needs profiling on/.test(off), off);
  // nulls
  ok(/no decode timing line/.test(text(SI.live([{ window_ms: null, ms_per_window: null }], {}))), 'no window');
  ok(!/NaN|undefined|null/.test(text(SI.live([win(9, 18, 2, 1, 1, { hit_rate: null })], {}))), 'null hit rate');
}

// ---- compare
const row = (key, metric, a, b, pct, state, extra) => Object.assign({ group: 'decode', key, metric, a, b, delta: b - a, pct, better: 'higher', floor: 0.05, floor_kind: 'rel', state }, extra || {});
{
  const better = {
    rows: [row('chat', 'decode tok/s', 75.7, 81.9, 8.19, 'better'),
      row('chat', 'adapt wait (ms per window)', 1.4, 7.0, 392, 'worse', { floor_kind: 'ms' }),
      row('long context', 'decode tok/s', 82, 82.8, 1, 'same')],
    verdicts: [{ group: 'decode', key: 'chat', state: 'better', text: 'chat: 8.2% faster', floor_note: 'noise floor from runs 3 and 4, same settings' }],
    settings: [{ flag: '--pcie-frac', a: '0.55', b: '0.35', changed: true }, { flag: '--spec-min-p', a: '0.5', b: '0.5', changed: false }],
    warnings: [],
  };
  const t = text(SI.compare(better));
  ok(/Biggest change: chat adapt wait \(ms per window\): 1\.4 → 7\.0 \(\+391\.\d%\), worse/.test(t) || /Biggest change: chat adapt wait/.test(t), t);
  ok(/Keep the after settings: chat got faster/.test(t), t);
  ok(/Settings that differ: --pcie-frac 0\.55 → 0\.35/.test(t), t);
  const k = SI.keepSentence(better);
  eq(k.kind, 'keep');
  const worse = { rows: [row('chat', 'decode tok/s', 80, 70, -12.5, 'worse')], verdicts: [], settings: [], warnings: [] };
  eq(SI.keepSentence(worse).kind, 'revert');
  ok(/Go back to the before settings/.test(SI.keepSentence(worse).text), 'revert');
  const mixed = { rows: [row('chat', 'decode tok/s', 80, 90, 12, 'better'), row('2K', 'prompt tok/s', 1000, 800, -20, 'worse', { group: 'prompt' })], verdicts: [], settings: [], warnings: [] };
  eq(SI.keepSentence(mixed).kind, 'mixed');
  const none = { rows: [row('chat', 'decode tok/s', 80, 80.5, 0.6, 'same')], verdicts: [{ text: 'chat: no measurable change', floor_note: 'noise floor from runs 3 and 4, same settings' }], settings: [], warnings: [] };
  const kn = SI.keepSentence(none);
  eq(kn.kind, 'none');
  ok(/Nothing moved by more than the noise floor: the settings make no measurable difference on this PC \(noise floor from runs 3 and 4, same settings\)\./.test(kn.text), kn.text);
  ok(/Biggest change: none beyond the noise floor/.test(text(SI.compare(none))), 'no biggest change');
  const assumed = { rows: [row('chat', 'decode tok/s', 80, 90, 12, 'better')], verdicts: [{ text: 'chat: 12.5% faster (assumed floor, no same-settings pair measured yet)', floor_note: null }], settings: [], warnings: [] };
  ok(/provisional until two runs with the same settings exist/.test(SI.keepSentence(assumed).text), 'provisional');
  const sign = { rows: [], verdicts: [{ text: 'agent turns: no measurable change (-1.0%, inside the 5.0% floor); 6 of 6 agent turns slower', floor_note: null }], settings: [], warnings: ['different GPU'] };
  const ts = text(SI.compare(sign));
  ok(/6 of 6 agent turns slower/.test(ts), ts);
  ok(/Caution: 6 of 6 agent turns slower/.test(SI.keepSentence(sign).text), 'caution');
  ok(/different GPU/.test(ts), ts);
  eq(SI.compare(null).lines.length, 0);
}

// ---- tune
{
  eq(SI.tune(null, null).lines.length, 1);
  ok(/running/.test(text(SI.tune({ state: 'running', stages: [{ state: 'done' }, { state: 'running' }] }, null))), 'running');
  ok(/failed: boom/.test(text(SI.tune({ state: 'failed', error: 'boom' }, null))), 'failed');
  const flow = {
    state: 'succeeded', tok_s: 93.3,
    settings: { rows: [
      { flag: '--pcie-frac', before: '0.35', after: null, changed: true },
      { flag: '--adapt-swaps', before: '160', after: '80', changed: true },
      { flag: '--pool-workers', before: '7', after: null, changed: false }] },
  };
  const t = text(SI.tune(flow, null));
  ok(/measured 93\.3 tok\/s/.test(t), t);
  ok(/Changed: --adapt-swaps 160 → 80/.test(t), t);
  ok(/Went back to the engine default: --pcie-frac 0\.35 → engine default/.test(t), t);
  const cmp = { rows: [row('chat', 'decode tok/s', 79.7, 80, 0.4, 'same')], verdicts: [{ group: 'decode', key: 'chat', text: 'chat: no measurable change (+0.4%, inside the 3.0% noise floor)', floor_note: null }], settings: [], warnings: [] };
  const t2 = text(SI.tune(flow, cmp));
  ok(/Before and after, profiled: chat: no measurable change/.test(t2), t2);
  ok(/Nothing moved by more than the noise floor/.test(t2), t2);
  ok(/No setting changed/.test(text(SI.tune({ state: 'succeeded', settings: { rows: [] } }, null))), 'no change');
}

// ---- reports and settings
{
  eq(SI.reports([], null).lines.length, 0);
  const list = [{ ok: true, chat_tps: 80.2 }, { ok: true, chat_tps: 79.5 }, { ok: false }];
  const t = text(SI.reports(list, { prompts: [{ label: '2K', groups_share: { 'experts over PCIe': 0.43, dequant: 0.15 } }] }));
  ok(/2 profile reports \(1 could not be read\)\. The newest decodes chat at 80\.2 tok\/s\./.test(t), t);
  ok(/Compare these two/.test(t), t);
  ok(/2K prompt read, experts over PCIe took 43%/.test(t), t);
  ok(/1 profile report\./.test(text(SI.reports([{ ok: true, chat_tps: null }], null))), 'one report');
  const ms = text(SI.reports(list, { prompts: [{ label: '16K', groups_ms: { a: 30, b: 10 } }] }));
  ok(/a took 75%/.test(ms), ms);

  eq(SI.settings([]).lines.length, 0);
  eq(SI.settings(null).lines.length, 0);
  ok(/All values are the same/.test(text(SI.settings([{ name: '--pcie-frac', now: '0.55', next_load: '0.55', run_config: null, calibrated: true }]))), 'same');
  const d = text(SI.settings([{ name: '--pcie-frac', now: '0.55', next_load: '0.35', run_config: '0.35', calibrated: true },
    { name: '--spec-min-p', now: '0.5', next_load: '0.5', run_config: '0.7', calibrated: true }]));
  ok(/1 setting will change at the next load: --pcie-frac 0\.55 → 0\.35/.test(d), d);
  ok(/differs between what is loaded and the run config: --spec-min-p/.test(d), d);
}

console.log(`ok ${n} cases`);
