/* Strata Profile Studio: Runs.  Every profile report on disk as an experiment (#runs), one run (#runs/<id>), and
   the comparison of two runs against this PC's measured noise (#compare/<a>/<b>). */
'use strict';

(function () {
  const { el, isNum } = S;
  S.viewDefs = S.viewDefs || {};

  const WORKLOADS = [['chat', 'chat'], ['long context', 'long context'], ['agent turns', 'agent turns']];
  const SHORT_KEYS = [['pcie_frac', 'PCIe share'], ['spec_min_p', 'draft floor'], ['pool_workers', 'CPU workers'], ['adapt', 'expert tier']];
  const ui = { workload: S.store.get('runs.workload', 'chat'), read: S.store.get('runs.read', '16K'), a: null, b: null, table: false, cmpTable: false };
  let token = 0;                                 // a newer render wins over an older one still loading

  const okReports = (A) => A.reports.filter((r) => r.ok);
  const settingsKey = (r) => JSON.stringify([r.gpu, r.model, r.quant, r.context, SHORT_KEYS.map(([k]) => (r.settings_short || {})[k])]);
  function groups(A) {
    const by = new Map();
    for (const r of okReports(A)) {
      const k = settingsKey(r);
      if (!by.has(k)) by.set(k, []);
      by.get(k).push(r);
    }
    return [...by.values()].map((list) => list.sort((a, b) => a.order[3] - b.order[3]));
  }
  function differing(A) {
    const rs = okReports(A);
    return SHORT_KEYS.filter(([k]) => new Set(rs.map((r) => (r.settings_short || {})[k])).size > 1);
  }
  function settingsText(r, keys) {
    return keys.map(([k, label]) => `${label} ${(r.settings_short || {})[k] ?? '–'}`).join(' · ');
  }
  function tuneLabel(A, t) {
    if (!t) return '';
    const rec = A.tunes.find((x) => x.id === t.id);
    return `${t.role} the tune of ${rec ? S.clock(rec.started_at) : t.id.replace(/^tune-\d{8}-(\d\d)(\d\d).*/, '$1:$2')}`;
  }
  const decodeOf = (r, w) => (r.headline && r.headline.decode ? r.headline.decode[w] : null);
  const readOf = (r, label) => {
    const p = r.headline && r.headline.prompt;
    if (!p) return null;
    const k = Object.keys(p).find((x) => x.startsWith(label));
    return k ? p[k] : null;
  };
  function noiseSpread(A, w) {
    let best = null;
    for (const g of groups(A)) {
      if (g.length < 2) continue;
      const v = g.map((r) => decodeOf(r, w)).filter(isNum);
      if (v.length < 2) continue;
      const s = (Math.max(...v) - Math.min(...v)) / Math.min(...v);
      if (!best || g.length > best.g.length) best = { s, g };
    }
    return best;
  }

  // ------------------------------------------------------------------------------------------ the list
  function runProgress(A) {
    const run = A.run;
    const s = A.strata();
    if (!(s.run && s.run.state === 'running') || !run) return null;
    const lines = (run.lines || []).slice(-6);
    const est = A.state && A.state.estimates && A.state.estimates.profile_s;
    const bar = el('div', { class: 'progress' }, el('i'));
    if (est) bar.firstChild.style.transform = `scaleX(${Math.min(1, (run.elapsed_s || 0) / est).toFixed(3)})`;
    return el('section', { class: 'card span3 running' },
      el('header', { class: 'card-head' }, el('h3', { text: `A profile run is going · ${S.dur(run.elapsed_s)}${est ? ` of about ${S.dur(est)}` : ''}` })),
      bar, el('pre', { class: 'log' }, lines.join('\n')));
  }

  function listView(A, sec) {
    const rs = okReports(A);
    const why = A.cannotRun();
    const head = el('div', { class: 'view-head' },
      el('h2', { text: 'Runs' }),
      el('span', { class: 'muted', text: A.reportsDir ? `${rs.length} profile report${rs.length === 1 ? '' : 's'} in ${A.reportsDir}` : '' }),
      el('span', { class: 'grow' }),
      S.button('Run a profile', () => A.startProfile(), 'primary', { disabled: !!why, title: why || 'Strata sends 16 requests and writes a report.' }));
    if (!rs.length) {
      const bad = A.reports.filter((r) => !r.ok);
      S.fill(sec, head, runProgress(A), el('section', { class: 'card empty-card' },
        el('h3', { text: A.reportsExist ? 'No profile reports yet.' : 'The results folder does not exist.' }),
        el('p', { class: 'sub', text: A.reportsExist ? `A profile run writes a report into ${A.reportsDir}. A run takes about 1 min. Strata answers other requests during the run.` : `Start the Studio again with --results set to Strata's bench/results folder (now: ${A.reportsDir}).` }),
        el('div', { class: 'row-buttons' }, S.button('Run a profile', () => A.startProfile(), 'primary', { disabled: !!why, title: why || '' })),
        why ? el('p', { class: 'note', text: `Run not possible now: ${why}` }) : null,
        bad.length ? el('p', { class: 'note', text: `${bad.length} folder(s) could not be read: ${bad.map((b) => b.id).join(', ')}` }) : null));
      return;
    }
    // A and B: kept while they exist; default the newest two
    if (!rs.find((r) => r.id === ui.b) || !rs.find((r) => r.id === ui.a) || ui.a === ui.b) {
      const t = A.tunes.find((x) => rs.find((r) => r.id === x.before_report) && rs.find((r) => r.id === x.after_report));
      ui.a = t ? t.before_report : (rs[1] || rs[0]).id;
      ui.b = t ? t.after_report : rs[0].id;
    }
    const fastest = rs.reduce((m, r) => (isNum(decodeOf(r, 'chat')) && (!m || decodeOf(r, 'chat') > decodeOf(m, 'chat')) ? r : m), null);
    const nChat = noiseSpread(A, 'chat'), nAgent = noiseSpread(A, 'agent turns');
    const newest = rs[0];
    const tiles = el('div', { class: 'tiles' },
      S.tile('Profile runs', String(rs.length), '', `${groups(A).length} different settings`),
      S.tile('Fastest chat decode', fastest ? S.num(decodeOf(fastest, 'chat'), 1) : '–', 'tok/s', fastest ? S.runName(fastest.id, A.reports) : '', 'hero'),
      S.tile('Chat decode noise, same settings', nChat ? S.num(nChat.s * 100, 1) : '–', nChat ? '%' : '', nChat ? `agent turns ${nAgent ? S.pct(nAgent.s, 1) : '–'} · runs ${nChat.g.map((r) => r.order[3]).join(', ')}` : 'run the profile two times with the same settings'),
      S.tile('Newest run', S.clock(newest.mtime, true), '', `${S.runName(newest.id, A.reports)} · ${S.dur(newest.elapsed_s)}`));
    const sel = (id) => (id === ui.a ? 'A' : id === ui.b ? 'B' : '');
    const wl = ui.workload;
    const c1 = S.card({ q: 'Decode speed per run', sub: `${wl}, median, tok/s · higher is better · A and B: the selected runs`,
      tools: S.seg(WORKLOADS, wl, (v) => { ui.workload = v; S.store.set('runs.workload', v); render(A); }, 'workload'), cls: 'span2' },
      S.bars(rs.map((r) => ({ label: S.runName(r.id, A.reports), note: [sel(r.id), tuneLabel(A, r.tune)].filter(Boolean).join(' · '), value: decodeOf(r, wl), hl: !!sel(r.id),
        dots: wl === 'agent turns' ? (r.headline && r.headline.agent) || [] : [], onclick: () => S.go(`#runs/${r.id}`),
        tip: [S.runName(r.id, A.reports), [`${wl} decode`, `${S.num(decodeOf(r, wl), 1)} tok/s`], ['settings', settingsText(r, SHORT_KEYS)], 'click to open'] })), { fmt: (v) => S.num(v, 1), unit: 'tok/s' }));
    const c2 = S.card({ q: 'Prompt read speed per run', sub: `${ui.read} prompt, tok/s · higher is better`,
      tools: S.seg([['2K', '2K'], ['16K', '16K'], ['32K', '32K']], ui.read, (v) => { ui.read = v; S.store.set('runs.read', v); render(A); }, 'prompt size') },
      S.bars(rs.map((r) => ({ label: S.runName(r.id, A.reports), value: readOf(r, ui.read), hl: !!sel(r.id), onclick: () => S.go(`#runs/${r.id}`) })), { fmt: S.int, unit: 'tok/s' }));
    // noise: each same-settings group as dot strips per workload
    const noiseRows = [];
    for (const g of groups(A)) {
      if (g.length < 2) continue;
      noiseRows.push(el('div', { class: 'noise-group' }, WORKLOADS.map(([w]) => {
        const items = g.map((r) => ({ value: decodeOf(r, w), label: S.runName(r.id, A.reports), short: `${r.order[3]}`, labelled: true, onclick: () => S.go(`#runs/${r.id}`) }));
        return el('div', { class: 'noise-col' }, el('div', { class: 'noise-label' }, el('b', { text: w }), el('small', { text: `runs ${g.map((r) => r.order[3]).join(', ')}` })), S.strip(items, { unit: 'tok/s', fmt: (v) => S.num(v, 1), labelValues: false }));
      })));
    }
    const c3 = S.card({ q: 'Noise between same-settings runs', sub: 'decode speed, tok/s, of each run with the same settings (labels: run numbers) · a change must be larger than this spread', cls: 'span3' },
      noiseRows.length ? noiseRows : el('p', { class: 'empty-line', text: 'No two runs share their settings yet: run the profile twice without changing anything to measure the noise.' }));
    // the table
    const keys = differing(A);
    const radio = (side, id) => {
      const i = el('input', { type: 'radio', name: `pick-${side}`, value: id, 'aria-label': `${side} = ${id}` });
      i.checked = (side === 'A' ? ui.a : ui.b) === id;
      i.addEventListener('click', (e) => e.stopPropagation());
      i.addEventListener('change', () => { if (side === 'A') ui.a = id; else ui.b = id; render(A); });
      return i;
    };
    const gIndex = new Map();
    groups(A).filter((g) => g.length > 1).forEach((g) => g.forEach((r) => gIndex.set(r.id, `runs ${g.map((x) => x.order[3]).join(', ')}`)));
    const cols = [
      { label: 'A', get: (r) => radio('A', r.id) }, { label: 'B', get: (r) => radio('B', r.id) },
      { label: 'run', get: (r) => S.runName(r.id, A.reports) }, { label: 'ended', get: (r) => S.clock(r.mtime, true) },
      { label: 'chat', num: true, get: (r) => S.num(decodeOf(r, 'chat'), 1) }, { label: 'long', num: true, get: (r) => S.num(decodeOf(r, 'long context'), 1) },
      { label: 'agent', num: true, get: (r) => S.num(decodeOf(r, 'agent turns'), 1) }, { label: '16K read', num: true, get: (r) => S.int(readOf(r, '16K')) },
      ...keys.map(([k, label]) => ({ label, get: (r) => (r.settings_short || {})[k] ?? '–' })),
      { label: 'same settings', get: (r) => gIndex.get(r.id) || '', title: 'runs with identical settings share a group: their spread is the noise' },
      { label: 'tune', get: (r) => tuneLabel(A, r.tune) }, { label: 'note', get: (r) => r.note || '' },
    ];
    const rows = A.reports.map((r) => (r.ok ? Object.assign({}, r, { _onclick: () => S.go(`#runs/${r.id}`), _cls: sel(r.id) ? 'sel' : '' }) : Object.assign({}, r, { _cls: 'bad' })));
    const c4 = S.card({ q: 'All runs', sub: 'decode speed per workload, tok/s · only the settings that differ · select A and B, or a row', cls: 'span3',
      tools: el('div', { class: 'card-tools' }, S.button(`Compare A (${S.runName(ui.a, A.reports)}) and B (${S.runName(ui.b, A.reports)})`, () => S.go(`#compare/${ui.a}/${ui.b}`), 'primary small')) },
      S.table(cols, rows.filter((r) => r.ok)),
      A.reports.some((r) => !r.ok) ? el('p', { class: 'note', text: `Not readable: ${A.reports.filter((r) => !r.ok).map((r) => r.id).join(', ')}` }) : null);
    S.fill(sec, head, tiles, runProgress(A), el('div', { class: 'grid' }, c1, c2, c3, c4));
  }

  // ------------------------------------------------------------------------------------------- one run
  async function runView(A, sec, id) {
    const my = ++token;
    if (!sec.querySelector(`[data-run="${CSS.escape(id)}"]`)) S.fill(sec, el('p', { class: 'loading', text: 'Loading the report…' }));
    let doc;
    try { doc = await A.report(id); } catch (e) {
      if (my !== token) return;
      S.fill(sec, el('section', { class: 'card empty-card' }, el('h3', { text: e.message }), el('p', null, el('a', { href: '#runs', text: '← all runs' }))));
      return;
    }
    if (my !== token) return;
    const meta = A.reports.find((r) => r.id === id) || {};
    const name = S.runName(id, A.reports);
    const reqs = doc.requests || [];
    const dec = Object.fromEntries((doc.decode || []).map((d) => [d.kind, d]));
    const others = okReports(A).filter((r) => r.id !== id);
    const pick = el('select', { 'aria-label': 'Compare with' }, el('option', { value: '', text: 'Compare with' }), others.map((r) => el('option', { value: r.id, text: `${S.runName(r.id, A.reports)} · ${S.clock(r.mtime, true)}` })));
    pick.addEventListener('change', () => { if (pick.value) S.go(`#compare/${pick.value}/${id}`); });
    const head = el('div', { class: 'view-head', dataset: { run: id } },
      el('a', { href: '#runs', class: 'back', text: '← Runs' }),
      el('h2', { text: `${name[0].toUpperCase()}${name.slice(1)}` }),
      el('span', { class: 'muted', text: `${S.clock(meta.mtime, true)} · ${S.dur(doc.elapsed_s)}${meta.tune ? ` · ${tuneLabel(A, meta.tune)}` : ''}` }),
      el('span', { class: 'grow' }), pick, S.button('Save as image', () => shareCard(A, doc, name), 'small'));
    const agentV = (doc.agent || []).map((t) => t.decode_tps).filter(isNum);
    const tiles = el('div', { class: 'tiles' },
      S.tile('Chat decode', S.num(dec.chat && dec.chat.tps.median, 1), 'tok/s', dec.chat ? `median of ${dec.chat.tps.n} · ${S.num(dec.chat.tps.min, 1)}–${S.num(dec.chat.tps.max, 1)}` : '', 'hero'),
      S.tile('Long-context decode', S.num(dec['long context'] && dec['long context'].tps.median, 1), 'tok/s', dec['long context'] ? `at ${S.int(dec['long context'].context)} tokens` : ''),
      S.tile('Agent decode', S.num(dec['agent turns'] && dec['agent turns'].tps.median, 1), 'tok/s', agentV.length ? `turns: ${S.num(Math.min(...agentV), 0)} to ${S.num(Math.max(...agentV), 0)} tok/s` : ''),
      S.tile('16K prompt read', S.int(((doc.prompts || []).find((p) => p.label.startsWith('16K')) || {}).prompt_tps), 'tok/s', ''),
      S.tile('Expert cache hit rate, chat', S.pct(dec.chat && dec.chat.hit_rate), '', dec.chat ? `long ${S.pct(dec['long context'] && dec['long context'].hit_rate)} · agent ${S.pct(dec['agent turns'] && dec['agent turns'].hit_rate)}` : ''));
    // where each request's time went
    const maxT = Math.max(...reqs.map((q) => (q.prompt_ms || 0) + (q.decode_ms || 0)), 1);
    const rowsBox = el('div', { class: 'req-rows' }, reqs.map((q) => {
      const parts = [...S.promptParts(q), ...S.decodeParts(q).map((p) => (p.key === 'not split' ? { ...p, key: 'not split' } : p))];
      const t = (q.prompt_ms || 0) + (q.decode_ms || 0);
      const row = el('div', { class: 'req-row clickable', tabIndex: 0 },
        el('div', { class: 'req-label' }, S.kindIcon(q.kind), el('span', { text: q.label })),
        S.stack(parts, { scale: maxT, labels: false, total: `${S.ms(t)}${q.generated > 1 ? ` · ${S.num(q.decode_tok_s, 1)} tok/s` : ''}` }));
      row.addEventListener('click', () => S.go(`#runs/${id}/r/${q.id}`));
      row.addEventListener('keydown', (e) => { if (e.key === 'Enter') S.go(`#runs/${id}/r/${q.id}`); });
      return row;
    }));
    const keys = [...new Set(reqs.flatMap((q) => [...S.promptParts(q), ...S.decodeParts(q)].map((p) => p.key)))];
    const c1 = S.card({ q: 'Time per request', sub: 'ms · prompt read per phase, then decode per window part, on one scale · select a request to open it', cls: 'span2' }, rowsBox, S.legend(keys));
    const wmax = Math.max(...(doc.decode || []).map((d) => d.ms_per_window || 0), 1);
    const c2 = S.card({ q: 'Decode window composition per workload', sub: 'ms per window · lower is better' },
      el('div', { class: 'req-rows' }, (doc.decode || []).map((d) => el('div', { class: 'req-row' },
        el('div', { class: 'req-label' }, el('span', { text: d.kind })),
        S.stack(S.DECODE_PARTS.map((k) => ({ key: k, value: (d.window_ms || {})[k] })), { scale: wmax, short: true, fmt: (v) => S.num(v, 1), total: `${S.num(d.ms_per_window, 1)} ms · ${S.num(d.tokens_per_window, 2)} tok` })))),
      S.legend(S.DECODE_PARTS));
    const c3 = S.card({ q: 'Decode speed per request', sub: 'tok/s · higher is better' },
      S.bars(reqs.filter((q) => q.generated > 1).map((q) => ({ label: q.label, icon: S.kindIcon(q.kind), value: q.decode_tok_s, note: `hit ${S.pct(q.hit_rate)}`, onclick: () => S.go(`#runs/${id}/r/${q.id}`) })), { fmt: (v) => S.num(v, 1), unit: 'tok/s' }));
    const reads = reqs.filter((q) => q.group === 'prompt' && q.prompt_groups_ms);
    const c4 = S.card({ q: 'Prompt read composition', sub: 'share of the GPU time per phase group · the label shows the largest group' },
      el('div', { class: 'req-rows' }, reads.map((q) => el('div', { class: 'req-row clickable', onclick: () => S.go(`#runs/${id}/r/${q.id}`) },
        el('div', { class: 'req-label' }, el('span', { text: q.label.replace(' read', '') })),
        S.stack(S.PROMPT_GROUPS.map((k) => ({ key: k, value: q.prompt_groups_ms[k] })), { short: true, fmt: (v) => S.pct(v / S.sum(Object.values(q.prompt_groups_ms))), total: `${S.int(q.prompt_tok_s)} tok/s` })))),
      S.legend(S.PROMPT_GROUPS));
    const findings = S.card({ q: 'Report findings', sub: 'the text of the profile tool, without changes', cls: 'span2' },
      (doc.findings || []).length ? el('ul', { class: 'findings' }, doc.findings.map((f) => el('li', { text: f }))) : el('p', { class: 'empty-line', text: 'No findings in this report.' }));
    const note = el('textarea', { class: 'note-input', rows: 3, maxlength: 2000, placeholder: 'A note about this run (the Studio keeps it in its state file)', 'aria-label': 'Note' });
    note.value = doc.note || '';
    const save = S.button('Save the note', async () => {
      try { await S.api('/api/notes', { body: { kind: 'report', id, text: note.value } }); S.toast('Saved.'); A.poll('reports', true); A.reportDocs.delete(id); } catch (e) { S.toast(e.message, 'bad'); }
    }, 'small', { disabled: A.readOnly(), title: A.readOnly() ? 'read-only' : '' });
    const noteCard = S.card({ q: 'Note', sub: 'your note about this run' }, note, el('div', { class: 'row-buttons' }, save));
    const env = doc.environment || {};
    const m = doc.machine || {};
    const setRows = (doc.settings.rows || []).map((r) => ({ flag: r.flag, config: r.config, loaded: r.loaded }));
    const details = S.card({ q: 'Settings and environment of the run', sub: `settings: ${doc.settings.source === 'report' ? 'from the run config in the report' : doc.settings.source === 'captured' ? 'recorded by the Studio when the run ended' : 'not recorded in this report'}`, cls: 'span3' },
      el('div', { class: 'two-tables' },
        S.table([{ label: 'setting', key: 'flag' }, { label: 'run config file', key: 'config' }, { label: 'loaded', key: 'loaded' }], setRows),
        S.table([{ label: 'what', key: 'k' }, { label: 'value', key: 'v' }], [
          ['GPU', `${m.gpu || '–'}${m.gpu_vram_mib ? ` · ${S.int(m.gpu_vram_mib)} MiB` : ''}`], ['driver · CUDA', `${env.gpu_driver || '–'} · ${env.cuda || '–'}`],
          ['CPU', `${m.cpu || '–'}${env.cpu_cores ? ` · ${env.cpu_cores} cores, ${env.cpu_threads} threads` : ''}`], ['RAM', m.ram_gib ? `${m.ram_gib} GiB` : '–'],
          ['OS', env.os], ['model', `${m.model || '–'} (${m.quant || '–'}) · context ${S.int(m.context)}`],
          ['Strata', `${m.strata_version || '–'}${env.strata_commit ? ` (${env.strata_commit})` : ''} · engine ${m.engine_version || '–'}`],
          ['KV', `${env.kv || '–'} · ${S.int(env.kv_resident)} positions per layer in VRAM`], ['expert cache', env.expert_slots ? `${S.int(env.expert_slots)} experts · ${S.int(env.expert_cache_mib)} MiB` : '–'],
          ['VRAM free after load', isNum(env.vram_free_mib) ? `${S.int(env.vram_free_mib)} MiB` : '–'], ['draft', env.spec ? `spec ${env.spec} · MTP ${env.mtp_max}` : '–'],
          ['long prompt', doc.params && doc.params.long ? `${S.int(doc.params.long)} tokens` : '–'], ['verify profile', doc.params && doc.params.verify_profile ? 'on (stage timing, speeds a little lower)' : 'off'],
          ['folder', doc.path], ['launch', env.launch],
        ].map(([k, v]) => ({ k, v })))));
    findings.classList.replace('span2', 'span3');
    S.fill(sec, head, tiles, el('div', { class: 'grid' }, c1, c2, c3, c4, noteCard, findings, details));
  }

  // a shareable card of the run's headline numbers (ModelDock): drawn on a canvas, saved as PNG
  function shareCard(A, doc, name) {
    const c = document.createElement('canvas');
    const W = 1200, H = 630;
    c.width = W; c.height = H;
    const g = c.getContext('2d');
    const css = getComputedStyle(document.documentElement);
    const v = (n) => css.getPropertyValue(n).trim();
    g.fillStyle = v('--bg'); g.fillRect(0, 0, W, H);
    g.fillStyle = v('--ink'); g.font = '600 34px system-ui, sans-serif';
    g.fillText(`${doc.machine.gpu || 'GPU'} · ${doc.machine.model || ''}`, 60, 80);
    g.fillStyle = v('--ink-2'); g.font = '22px system-ui, sans-serif';
    g.fillText(`Strata ${doc.machine.strata_version || ''} · ${doc.machine.cpu || ''} · ${name} · ${doc.date}`, 60, 120);
    const dec = Object.fromEntries((doc.decode || []).map((d) => [d.kind, d]));
    const nums = [['chat decode', dec.chat && dec.chat.tps.median, 'tok/s'], ['long context', dec['long context'] && dec['long context'].tps.median, 'tok/s'],
      ['agent turns', dec['agent turns'] && dec['agent turns'].tps.median, 'tok/s'], ['16K prompt read', ((doc.prompts || []).find((p) => p.label.startsWith('16K')) || {}).prompt_tps, 'tok/s']];
    nums.forEach(([label, val, unit], i) => {
      const x = 60 + i * 280;
      g.fillStyle = v('--ink-2'); g.font = '22px system-ui, sans-serif'; g.fillText(label, x, 200);
      g.fillStyle = v('--ink'); g.font = '700 64px system-ui, sans-serif'; g.fillText(isNum(val) ? (val >= 1000 ? S.int(val) : S.num(val, 1)) : '–', x, 270);
      g.fillStyle = v('--ink-2'); g.font = '22px system-ui, sans-serif'; g.fillText(unit, x, 300);
    });
    const d = dec.chat;
    if (d && d.window_ms) {
      g.fillStyle = v('--ink'); g.font = '600 24px system-ui, sans-serif';
      g.fillText(`Chat decode window composition: ${S.num(d.ms_per_window, 1)} ms`, 60, 390);
      let x = 60;
      const total = S.sum(S.DECODE_PARTS.map((k) => d.window_ms[k]));
      const probe = document.createElement('i');
      document.body.appendChild(probe);
      for (const k of S.DECODE_PARTS) {
        const w = (d.window_ms[k] / total) * 1080;
        probe.className = `sw ${S.PART_CLASS[k]}`;
        g.fillStyle = getComputedStyle(probe).backgroundColor;
        g.fillRect(x, 410, Math.max(0, w - 3), 56);
        g.fillStyle = '#fff'; g.font = '600 20px system-ui, sans-serif';
        if (w > 120) g.fillText(`${S.PART_LABEL[k]} ${S.num(d.window_ms[k], 1)}`, x + 10, 446);
        x += w;
      }
      probe.remove();
    }
    g.fillStyle = v('--ink-2'); g.font = '20px system-ui, sans-serif';
    g.fillText('Strata Profile Studio · tools/strata_profile.py', 60, 590);
    c.toBlob((b) => {
      const a = el('a', { href: URL.createObjectURL(b), download: `${doc.id}.png` });
      document.body.appendChild(a); a.click(); a.remove();
    });
  }

  function render(A) {
    const r = A.route;
    const sec = document.getElementById('view-runs');
    if (r.parts[1]) { runView(A, sec, r.parts[1]); return; }
    token++;
    listView(A, sec);
  }

  S.viewDefs.runs = { render };
  S.runsUi = ui;
})();
