/* insights.js - "what to look at first": plain sentences computed from the data.
 *
 * Pure functions, no DOM: each takes the JSON the page already has and returns {title, lines}, where `lines` is an
 * array of finished sentences (or [] when there is nothing to say).  The page puts them in one calm box at the top of
 * a view.  Loaded in the browser as window.StudioInsights and in node (tests/insights_cases.js) through require.
 *
 *   live(samples, opts)        opts: {profileOff}               what the decode window waits for
 *   compare(cmp)                                                biggest change, and whether to keep the after settings
 *   tune(flow, cmp)                                             the result of a tune in words
 *   reports(list, doc)                                          how many reports, the newest speed, the biggest phase
 *   settings(rows)                                              what differs between now, next load and the run config
 *   keepSentence(cmp)          -> {kind, text}                  the shared "keep or go back" rule
 */
(function (root) {
  'use strict';

  const DECODE_GROUPS = ['GPU', 'CPU experts', 'draft', 'adapt wait', 'other'];
  const RECENT = 10;                  // how many of the newest samples the live sentence averages

  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const round = (v) => Math.round(v);
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const pctText = (frac) => `${Math.round(frac * 100)}%`;
  const fix1 = (v) => (Math.round(v * 10) / 10).toFixed(1);

  // How each decode-window part reads in a sentence: [what the window does, what would change it].
  const PART = {
    GPU: ['is the card itself computing', 'The card is the limit here: a smaller context or a faster card would help; the settings here barely matter.'],
    'CPU experts': ['waits for experts computed on the CPU',
      'A higher hit rate would cut it: a bigger expert cache, a saved expert profile (expert_profile_save) or faster RAM.'],
    draft: ['goes to drafting the next tokens', 'The draft floor (--spec-min-p) sets how long the draft runs on; a higher floor drafts less.'],
    'adapt wait': ['waits for the adaptive expert tier to swap experts in',
      'The tier settings (--adapt-every, --adapt-swaps, --adapt-decay) decide this; the engine default swaps fewer experts, and a smaller --adapt-swaps costs less wait.'],
    other: ['goes to other work', 'Nothing stands out: no single part of the window dominates.'],
  };
  const BIGGEST_PHRASE = {
    GPU: 'is the card itself', 'CPU experts': 'is waiting for experts computed on the CPU', draft: 'is drafting the next tokens',
    'adapt wait': 'is waiting for the adaptive expert tier to swap experts in', other: 'is other work',
  };

  // ---------------------------------------------------------------------------------------------- live
  function live(samples, opts) {
    const o = opts || {};
    const title = 'What to look at first';
    const list = Array.isArray(samples) ? samples : [];
    if (list.length === 0) {
      return { title, lines: ['No request has been seen yet. Send one to Strata and this box says where its time went.'] };
    }
    if (o.profileOff) {
      const s = list.slice().reverse().find((x) => isNum(x.decode_tok_s));
      const lines = [];
      if (s) {
        lines.push(`The last request decoded at ${fix1(s.decode_tok_s)} tok/s${isNum(s.hit_rate) ? `, with an expert cache hit rate of ${pctText(s.hit_rate)}` : ''}.`);
      }
      lines.push('Where the time goes inside a decode window needs profiling on in Strata (see the banner above).');
      return { title, lines };
    }
    const withWin = list.filter((s) => s.window_ms && isNum(s.ms_per_window) && s.ms_per_window > 0);
    if (withWin.length === 0) {
      return { title, lines: ['The requests seen so far have no decode timing line yet, so the window split cannot be read.'] };
    }
    const plain = withWin.filter((s) => !s.tag);
    const use = (plain.length ? plain : withWin).slice(-RECENT);
    const avg = {};
    for (const g of DECODE_GROUPS) {
      const vals = use.map((s) => s.window_ms[g]).filter(isNum);
      avg[g] = vals.length ? mean(vals) : 0;
    }
    const total = mean(use.map((s) => s.ms_per_window));
    let top = DECODE_GROUPS[0];
    for (const g of DECODE_GROUPS) if (avg[g] > avg[top]) top = g;
    const share = total > 0 ? avg[top] / total : 0;
    const hits = use.map((s) => s.hit_rate).filter(isNum);
    const hit = hits.length ? mean(hits) : null;

    const lead = share > 0.5
      ? `Most of each decode window ${PART[top][0]} (${round(avg[top])} of ${round(total)} ms)`
      : `The biggest part of each decode window ${BIGGEST_PHRASE[top]} (${round(avg[top])} of ${round(total)} ms)`;
    const tail = [];
    if (hit != null) tail.push(`the expert cache hit rate is ${pctText(hit)}`);
    if (top !== 'GPU') tail.push(`the card itself takes ${round(avg.GPU)} ms`);
    const lines = [`${lead}${tail.length ? `; ${tail.join('; ')}` : ''}.`, PART[top][1]];

    const withSplit = list.slice().reverse().find((s) => s.prompt_groups_ms && s.source !== 'history' && isNum(s.read) && s.read > 0)
      || list.slice().reverse().find((s) => s.prompt_groups_ms && isNum(s.read) && s.read > 0);
    if (withSplit) {
      const g = withSplit.prompt_groups_ms;
      const names = Object.keys(g).filter((k) => isNum(g[k]));
      const sum = names.reduce((a, k) => a + g[k], 0);
      if (sum > 0) {
        const big = names.reduce((a, k) => (g[k] > g[a] ? k : a), names[0]);
        lines.push(`In the newest prompt read, ${big} took ${pctText(g[big] / sum)} of the card’s time.`);
      }
    }
    return { title, lines };
  }

  // -------------------------------------------------------------------------------------------- compare
  const WORSE_BETTER = { better: 'better', worse: 'worse' };

  function describeRow(r) {
    const unit = r.floor_kind === 'pts' ? '%' : '';
    const f = (v) => (isNum(v) ? `${Math.abs(v) >= 100 ? Math.round(v) : fix1(v)}${unit}` : '—');
    const what = `${r.key} ${r.metric}`;
    const chg = isNum(r.pct) ? ` (${r.pct >= 0 ? '+' : '−'}${fix1(Math.abs(r.pct))}%)` : '';
    return `${what}: ${f(r.a)} → ${f(r.b)}${chg}, ${WORSE_BETTER[r.state] || r.state}`;
  }

  // The rule for the keep-or-go-back sentence: decode and prompt speed rows that moved beyond their floor decide.
  function keepSentence(cmp) {
    const rows = (cmp && cmp.rows) || [];
    const verdicts = (cmp && cmp.verdicts) || [];
    const speed = rows.filter((r) => /tok\/s/.test(r.metric || '') && r.group !== 'agent');
    const better = speed.filter((r) => r.state === 'better');
    const worse = speed.filter((r) => r.state === 'worse');
    const names = (xs) => xs.map((r) => `${r.key}${r.group === 'prompt' ? ' read' : ''}`).join(', ');
    const assumed = verdicts.some((v) => /assumed floor/.test(v.text || ''));
    const note = verdicts.map((v) => v.floor_note).find(Boolean);
    let kind;
    let text;
    if (better.length && !worse.length) {
      kind = 'keep';
      text = `Keep the after settings: ${names(better)} got faster and nothing got slower beyond the noise floor.`;
    } else if (worse.length && !better.length) {
      kind = 'revert';
      text = `Go back to the before settings: ${names(worse)} got slower and nothing got faster beyond the noise floor.`;
    } else if (better.length && worse.length) {
      kind = 'mixed';
      text = `Mixed result: ${names(better)} got faster, ${names(worse)} got slower. Keep the after settings only if the faster part is what you use.`;
    } else {
      kind = 'none';
      text = `Nothing moved by more than the noise floor: the settings make no measurable difference on this PC${note ? ` (${note})` : assumed ? ' (assumed floor)' : ''}.`;
    }
    const sign = verdicts.map((v) => /(\d+ of \d+ [a-z ]*slower)/.exec(v.text || '')).find(Boolean);
    if (sign && (kind === 'keep' || kind === 'none')) text += ` Caution: ${sign[1]}, which is a sign of a real slowdown even inside the floor.`;
    if (assumed) text += ' This verdict is provisional until two runs with the same settings exist.';
    return { kind, text };
  }

  function compare(cmp) {
    const title = 'What to look at first';
    if (!cmp) return { title, lines: [] };
    const lines = [];
    const rows = ((cmp.rows) || []).filter((r) => (r.state === 'better' || r.state === 'worse') && isNum(r.pct));
    if (rows.length) {
      const big = rows.reduce((a, r) => (Math.abs(r.pct) > Math.abs(a.pct) ? r : a), rows[0]);
      lines.push(`Biggest change: ${describeRow(big)}.`);
    } else {
      lines.push('Biggest change: none beyond the noise floor.');
    }
    lines.push(keepSentence(cmp).text);
    const sign = ((cmp.verdicts) || []).map((v) => /(\d+ of \d+ [a-z ]*(?:slower|faster))/.exec(v.text || '')).find(Boolean);
    if (sign) lines.push(`Turn by turn: ${sign[1]}, even where each turn is inside the floor.`);
    const differs = ((cmp.settings) || []).filter((s) => s.changed);
    if (differs.length) {
      lines.push(`Settings that differ: ${differs.map((s) => `${s.flag} ${s.a == null ? 'engine default' : s.a} → ${s.b == null ? 'engine default' : s.b}`).join('; ')}.`);
    }
    const w = (cmp.warnings || []).filter((x) => x !== 'different Strata version');
    if (w.length) lines.push(`These reports show ${w.join(' and ')}: the change is not only the settings.`);
    return { title, lines };
  }

  // ----------------------------------------------------------------------------------------------- tune
  function tune(flow, cmp) {
    const title = 'The result';
    const f = flow || {};
    if (!f.state || f.state === 'idle') {
      return { title, lines: ['Nothing has been tuned from here yet. “Tune this PC” profiles, runs Strata’s calibration, loads the model again, profiles again and says whether it helped.'] };
    }
    if (f.state === 'running') {
      const done = (f.stages || []).filter((s) => s.state === 'done').length;
      return { title, lines: [`Tuning is running (${done} of ${(f.stages || []).length || 6} stages done). The model is unloaded for the calibration and requests get 503 meanwhile; the result appears here when it ends.`] };
    }
    if (f.state === 'failed' || f.state === 'cancelled') {
      return { title, lines: [`The last tune ${f.state === 'failed' ? 'failed' : 'was cancelled'}${f.error ? `: ${f.error}` : ''}.`,
        'Strata’s settings are as they were before, apart from anything the calibration had already written.'] };
    }
    const lines = [];
    if (isNum(f.tok_s)) lines.push(`The calibration measured ${fix1(f.tok_s)} tok/s with the settings it kept.`);
    const rows = ((f.settings && f.settings.rows) || []).filter((r) => r.changed);
    const back = rows.filter((r) => r.after == null || /^(engine default|default)$/i.test(String(r.after)));
    const set = rows.filter((r) => !back.includes(r));
    const v = (x) => (x == null ? 'engine default' : String(x));
    if (set.length) lines.push(`Changed: ${set.map((r) => `${r.flag} ${v(r.before)} → ${v(r.after)}`).join('; ')}.`);
    if (back.length) lines.push(`Went back to the engine default: ${back.map((r) => `${r.flag} ${v(r.before)} → ${v(r.after)}`).join('; ')}.`);
    if (!rows.length && f.settings) lines.push('No setting changed: the defaults were already the fastest here.');
    if (cmp) {
      const chat = (cmp.verdicts || []).find((x) => x.group === 'decode' && x.key === 'chat') || (cmp.verdicts || [])[0];
      if (chat) lines.push(`Before and after, profiled: ${chat.text}.`.replace(/\.\.$/, '.'));
      lines.push(keepSentence(cmp).text);
    } else {
      lines.push('The before/after comparison is loading.');
    }
    return { title, lines };
  }

  // --------------------------------------------------------------------------------------------- reports
  function reports(list, doc) {
    const title = 'What to look at first';
    const all = Array.isArray(list) ? list : [];
    if (all.length === 0) return { title, lines: [] };
    const ok = all.filter((r) => r.ok);
    const lines = [];
    const bad = all.length - ok.length;
    lines.push(`${ok.length} profile report${ok.length === 1 ? '' : 's'}${bad ? ` (${bad} could not be read)` : ''}.`
      + (ok[0] && isNum(ok[0].chat_tps) ? ` The newest decodes chat at ${fix1(ok[0].chat_tps)} tok/s.` : ''));
    if (ok.length >= 2) lines.push('To see whether a change helped, tick two reports and press “Compare these two”.');
    if (doc && Array.isArray(doc.prompts)) {
      const p = doc.prompts.filter((x) => x.groups_share || x.groups_ms).slice(-1)[0];
      if (p) {
        const g = p.groups_share || (() => {
          const sum = Object.values(p.groups_ms).reduce((a, b) => a + (isNum(b) ? b : 0), 0);
          const out = {};
          for (const k of Object.keys(p.groups_ms)) out[k] = sum ? p.groups_ms[k] / sum : 0;
          return out;
        })();
        const names = Object.keys(g).filter((k) => isNum(g[k]));
        if (names.length) {
          const big = names.reduce((a, k) => (g[k] > g[a] ? k : a), names[0]);
          lines.push(`In this report’s ${p.label} prompt read, ${big} took ${pctText(g[big])} of the card’s time.`);
        }
      }
    }
    return { title, lines };
  }

  // --------------------------------------------------------------------------------------------- settings
  function settings(rows) {
    const title = 'What to look at first';
    const list = Array.isArray(rows) ? rows : [];
    if (list.length === 0) return { title, lines: [] };
    const differs = (a, b) => a != null && b != null && String(a) !== String(b);
    const pending = list.filter((r) => differs(r.now, r.next_load));
    const config = list.filter((r) => differs(r.now, r.run_config) && !pending.includes(r));
    const lines = [];
    if (!pending.length && !config.length) {
      lines.push('All values are the same now, at the next load and in the run config.');
    } else {
      if (pending.length) lines.push(`${pending.length} setting${pending.length === 1 ? '' : 's'} will change at the next load: ${pending.map((r) => `${r.name} ${r.now} → ${r.next_load}`).join('; ')}.`);
      if (config.length) lines.push(`${config.length} differ${config.length === 1 ? 's' : ''} between what is loaded and the run config: ${config.map((r) => r.name).join(', ')}.`);
    }
    const cal = list.filter((r) => r.calibrated).length;
    if (cal) lines.push(`${cal} of these are the ones “Tune this PC” measures; the rest are set by hand in Strata’s config.`);
    return { title, lines };
  }

  const api = { live, compare, tune, reports, settings, keepSentence };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.StudioInsights = api;
})(typeof window !== 'undefined' ? window : null);
