/* Strata Profile Studio: the request panel.  One request, wherever it was clicked: a live sample (Now) or a report's
   request (Runs, Compare, Tune).  A timeline of the prompt read by phase and the decode by window, where the experts
   came from, the phases, and the same request in every other run. */
'use strict';

(function () {
  const { el, isNum } = S;

  // the prompt read split into its groups (GPU timeline) plus host time, scaled to the read's own ms
  S.promptParts = function promptParts(r) {
    const total = r.prompt_ms;
    if (!isNum(total) || total <= 0) return [];
    const g = r.prompt_groups_ms;
    if (!g) return [{ key: 'not split', value: total }];
    const gsum = S.sum(Object.values(g));
    const k = gsum > total ? total / gsum : 1;
    const out = S.PROMPT_GROUPS.filter((name) => isNum(g[name])).map((name) => ({ key: name, value: g[name] * k }));
    const host = total - gsum * k;
    if (host > 0.5) out.push({ key: 'host', value: host });
    return out;
  };
  // the decode split into window parts, scaled to the decode's own ms
  S.decodeParts = function decodeParts(r) {
    const total = r.decode_ms;
    if (!isNum(total) || total <= 0) return [];
    const w = r.window_ms;
    if (!w || !isNum(r.ms_per_window) || r.ms_per_window <= 0) return [{ key: 'not split', value: total, label: 'decode' }];
    const wsum = S.sum(S.DECODE_PARTS.map((p) => w[p]));
    return S.DECODE_PARTS.filter((p) => isNum(w[p]) && w[p] > 0).map((p) => ({ key: p, value: (w[p] / wsum) * total }));
  };
  // where the routed experts came from: of all routed, PCIe = pcie_share; the rest were looked up in VRAM
  S.expertSources = function expertSources(r) {
    if (!isNum(r.hit_rate)) return null;
    const pcie = isNum(r.pcie_share) ? r.pcie_share : 0;
    return { vram: r.hit_rate * (1 - pcie), pcie, cpu: (1 - r.hit_rate) * (1 - pcie) };
  };

  function timeline(r) {
    const pms = isNum(r.prompt_ms) ? r.prompt_ms : 0, dms = isNum(r.decode_ms) ? r.decode_ms : 0;
    const total = pms + dms;
    if (total <= 0) return el('p', { class: 'empty-line', text: 'No timing for this request.' });
    const box = el('div', { class: 'tl' });
    const axis = el('div', { class: 'tl-axis' });
    for (const t of S.niceTicks(0, total, 5)) {
      const tk = el('span', { class: 'tl-tick', text: S.ms(t) });
      tk.style.left = `${(t / total) * 100}%`;
      axis.appendChild(tk);
    }
    const row = (name, note, start, parts, len, windows) => {
      const track = el('div', { class: 'tl-track' });
      const span = el('div', { class: 'tl-span' });
      span.style.left = `${(start / total) * 100}%`;
      span.style.width = `${(len / total) * 100}%`;
      const inner = el('div', { class: 'tl-parts' });
      for (const p of parts) {
        const s = el('i', { class: `tl-part ${S.PART_CLASS[p.key] || ''}` });
        s.style.flexGrow = String(p.value);
        S.hover(s, [S.PART_LABEL[p.key] || p.key, ['ms', S.ms(p.value)], ['share of the ' + name, S.pct(p.value / len, 1)], S.PART_ABOUT[p.key] || '']);
        inner.appendChild(s);
      }
      span.appendChild(inner);
      if (windows > 1 && windows <= 400) {
        const ticks = el('i', { class: 'tl-windows' });
        ticks.style.backgroundSize = `${100 / windows}% 100%`;
        span.appendChild(ticks);
      }
      track.appendChild(span);
      return el('div', { class: 'tl-row' }, el('div', { class: 'tl-name' }, el('b', { text: name }), el('small', { text: note })), track);
    };
    const pp = S.promptParts(r);
    const windows = isNum(r.windows) ? r.windows : (isNum(r.ms_per_window) && r.ms_per_window > 0 ? Math.round(dms / r.ms_per_window) : 0);
    box.append(axis,
      row('prompt read', `${S.ms(pms)}${r.prompt_groups_ms ? '' : ''}`, 0, pp, pms, 0),
      row('decode', `${S.ms(dms)} · ${windows ? `${windows} windows` : ''}`, pms, S.decodeParts(r), dms, windows));
    return box;
  }

  function factsGrid(pairs) {
    return el('dl', { class: 'facts' }, pairs.filter((p) => p && p[1] !== null && p[1] !== undefined && p[1] !== '–').map(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })]));
  }

  // ------------------------------------------------------------------------------------------- render
  const P = (S.panel = { req: null, ctx: null });

  P.close = function close() {
    const a = document.getElementById('panel');
    a.hidden = true;
    document.body.classList.remove('panel-open');
    P.req = null;
    if (P.ctx && P.ctx.onclose) P.ctx.onclose();
  };

  // ctx: {list: [requests in display order], hash(req) -> the route of a request, where: 'live'|'report', report}
  P.open = function open(req, ctx) {
    P.req = req;
    P.ctx = ctx || {};
    const a = document.getElementById('panel');
    a.hidden = false;
    document.body.classList.add('panel-open');
    render(a, req, P.ctx);
    a.scrollTop = 0;
  };

  function nav(ctx, req, d) {
    const list = ctx.list || [];
    const i = list.indexOf(req);
    const j = i + d;
    if (i < 0 || j < 0 || j >= list.length) return null;
    return () => { if (ctx.hash) S.go(ctx.hash(list[j])); else P.open(list[j], ctx); };
  }

  function render(a, r, ctx) {
    const reports = (S.app && S.app.reports) || [];
    const title = r.label || (isNum(r.seq) ? `Request #${r.seq}` : 'Request');
    const where = [];
    if (ctx.where === 'report' && ctx.report) where.push(el('a', { href: `#runs/${ctx.report}`, text: S.runName(ctx.report, reports) }));
    if (ctx.where === 'live') {
      if (r.report_request) where.push(el('a', { href: `#runs/${r.report_request.report}/r/${r.report_request.id}`, text: `${r.report_request.label} of ${S.runName(r.report_request.report, reports)}` }));
      else if (r.run && r.run.report) where.push(el('a', { href: `#runs/${r.run.report}`, text: `during ${S.runName(r.run.report, reports)}` }));
      if (r.run && r.run.kind === 'tune') where.push(S.chip('during the calibration', 'tune'));
      if (r.run && r.run.kind === 'reload') where.push(S.chip('the reload after a tune', 'tune'));
      if (r.after_load) where.push(S.chip('first after a model load', 'warn'));
    }
    const prev = nav(ctx, r, -1), next = nav(ctx, r, 1);
    const head = el('header', { class: 'panel-head' },
      el('div', { class: 'panel-title' },
        S.kindIcon(r.kind), el('h2', { text: title })),
      el('div', { class: 'panel-sub' },
        el('span', { text: S.KIND_LABEL[r.kind] || r.kind || '' }),
        isNum(r.seen_at) ? el('span', { text: r.late ? 'before the Studio started' : S.clock(r.seen_at, 'sec') }) : null,
        ...where),
      el('div', { class: 'panel-tools' },
        S.button('◀', prev, 'icon', { title: 'Previous request', disabled: !prev, 'aria-label': 'Previous request' }),
        S.button('▶', next, 'icon', { title: 'Next request', disabled: !next, 'aria-label': 'Next request' }),
        S.button('✕', () => (ctx.closeHash ? S.go(ctx.closeHash) : P.close()), 'icon', { title: 'Close (Esc)', 'aria-label': 'Close' })));

    const total = (r.prompt_ms || 0) + (r.decode_ms || 0);
    const tiles = el('div', { class: 'tiles small' },
      S.tile('decode', S.tps(r.decode_tok_s), 'tok/s', isNum(r.generated) ? `${S.int(r.generated)} tokens out` : ''),
      S.tile('prompt read', S.tps(r.prompt_tok_s), 'tok/s', isNum(r.prompt_tokens) ? `${S.int(r.prompt_tokens)} in${r.reused ? `, ${S.int(r.reused)} reused` : ''}` : ''),
      S.tile('total', S.ms(total), '', isNum(r.ttft_s) ? `first token after ${S.num(r.ttft_s, 2)} s` : ''),
      S.tile('expert cache hit', S.pct(r.hit_rate), '', isNum(r.pcie_share) ? `${S.pct(r.pcie_share, 1)} over PCIe` : ''));

    const body = [tiles];
    body.push(S.card({ q: "Where did this request's time go?", sub: 'ms on one time axis: the prompt read by phase (in proportion, not in time order), then the decode by window part', cls: 'flat' },
      timeline(r), S.legend([...new Set([...S.promptParts(r), ...S.decodeParts(r)].map((p) => p.key))])));

    if (r.window_ms && isNum(r.ms_per_window)) {
      const parts = S.DECODE_PARTS.map((k) => ({ key: k, value: r.window_ms[k] }));
      const top = parts.filter((p) => p.key !== 'other').sort((x, y) => (y.value || 0) - (x.value || 0))[0];
      body.push(S.card({ q: 'What is one decode window made of?', sub: `ms per window, average of ${isNum(r.windows) ? S.int(r.windows) : 'its'} windows · lower is better`, cls: 'flat' },
        S.stack(parts, { total: `${S.num(r.ms_per_window, 1)} ms`, hl: top && top.key, fmt: (v) => S.num(v, 1) }),
        factsGrid([
          ['tokens per window', S.num(r.tokens_per_window, 2)],
          ['drafts accepted', isNum(r.drafts_offered) ? `${S.int(r.drafts_accepted)} of ${S.int(r.drafts_offered)}${r.drafts_offered ? ` (${S.pct(r.drafts_accepted / r.drafts_offered)})` : ''}` : null],
          ['CPU experts per layer-window', isNum(r.cpu_experts) ? `${S.num(r.cpu_experts, 2)}${isNum(r.cpu_entries) ? ` (${S.num(r.cpu_entries, 2)} entries)` : ''}` : null],
          ['VRAM hits per layer-window', isNum(r.vram_hits) ? S.num(r.vram_hits, 2) : null],
          ['PCIe reads per layer-window', isNum(r.pcie_per_window) ? S.num(r.pcie_per_window, 2) : null],
          ['adaptive tier swaps per window', isNum(r.swaps) ? S.num(r.swaps, 1) : null],
        ])));
    }

    const src = S.expertSources(r);
    if (src) {
      body.push(S.card({ q: 'Where did the experts come from?', sub: `share of the routed experts${isNum(r.routed) ? ` (${S.int(r.routed)})` : ''} · more from VRAM is faster`, cls: 'flat' },
        S.bars([
          { label: 'VRAM expert cache', value: src.vram, cls: 'k-gpu', text: S.pct(src.vram, 1) },
          { label: 'computed by the CPU', value: src.cpu, cls: 'k-cpu', text: S.pct(src.cpu, 1) },
          { label: 'read over PCIe', value: src.pcie, cls: 'k-pcie', text: S.pct(src.pcie, 1) },
        ], { max: 1, sort: false }),
        factsGrid([
          ['cache hit rate', isNum(r.hits) ? `${S.pct(r.hit_rate, 1)} (${S.int(r.hits)} of ${S.int(r.lookups)} lookups)` : S.pct(r.hit_rate, 1)],
          ['KV reads from VRAM', isNum(r.kv_hit) ? `${S.pct(r.kv_hit, 1)}${isNum(r.kv_block_reads) ? ` of ${S.int(r.kv_block_reads)} block reads` : ''}${isNum(r.kv_ram_mib) ? `, ${S.num(r.kv_ram_mib, 1)} MiB from RAM` : ''}` : null],
        ])));
    }

    const phases = r.phases ? Object.entries(r.phases) : (r.prompt_top || []).filter((t) => isNum(t[1]));
    if (phases.length) {
      const gpu = r.prompt_gpu_ms || S.sum(phases.map((p) => p[1]));
      body.push(S.card({ q: 'Which prompt phases took longest?', sub: `ms of GPU time${r.phases ? '' : ' (the top five that /profile/last reports)'} · lower is better`, cls: 'flat' },
        S.bars(phases.filter((p) => p[1] > 0).map(([name, ms]) => ({ label: name, value: ms, cls: S.PART_CLASS[S.PHASE_GROUP[name] || 'other'], text: `${S.ms(ms)} · ${S.pct(ms / gpu)}` })), { fmt: S.ms })));
    } else if (r.prompt_top && r.prompt_top.length) {
      body.push(el('p', { class: 'note', text: `Largest prompt group: ${r.prompt_top[0][0]} (the history keeps no more).` }));
    }

    if (r.prompt_host) {
      const h = r.prompt_host;
      body.push(S.card({ q: 'What did the host do around the prompt read?', sub: 'ms', cls: 'flat' }, factsGrid([
        ['host staging', isNum(r.host_staging_ms) ? S.ms(r.host_staging_ms) : null], ['chunk setup', S.ms(h.chunk_setup_ms)],
        ['waiting for each chunk', S.ms(h.wait_chunk_ms)], ['after each chunk', S.ms(h.after_chunk_ms)], ['PLE', S.ms(h.ple_ms)]])));
    }

    if (r.gpu_stages && (r.gpu_stages.gdn || r.gpu_stages.qsa)) {
      const rows = [];
      for (const [layer, stages] of [['GDN', r.gpu_stages.gdn || {}], ['QSA', r.gpu_stages.qsa || {}]]) for (const [n, ms] of Object.entries(stages)) rows.push({ layer, n, ms });
      body.push(S.card({ q: 'Which decode stage takes the card longest?', sub: 'GPU ms per window (STRATA_VERIFY_PROFILE)', cls: 'flat' },
        S.bars(rows.map((x) => ({ label: `${x.layer} ${x.n}`, value: x.ms })), { fmt: (v) => S.num(v, 2), unit: 'ms' })));
    }

    // this request in every run
    const sameId = ctx.where === 'report' ? r.id : (r.report_request && r.report_request.id);
    if (sameId) {
      const holder = el('div', { class: 'strip-holder' }, el('p', { class: 'empty-line', text: 'Loading the other runs…' }));
      body.push(S.card({ q: 'Is this request as fast in the other runs?', sub: `decode tok/s of "${r.label || (r.report_request && r.report_request.label)}" in every report · the spread is this PC's noise`, cls: 'flat' }, holder));
      S.app.allReports().then((docs) => {
        const cur = ctx.where === 'report' ? ctx.report : r.report_request.report;
        const items = docs.map((d) => {
          const q = (d.requests || []).find((x) => x.id === sameId);
          return q ? { value: q.decode_tok_s, label: S.runName(d.id, reports), short: `run ${S.runNo(d.id, reports)}`, hl: d.id === cur, labelled: true, onclick: () => S.go(`#runs/${d.id}/r/${sameId}`) } : null;
        }).filter(Boolean);
        S.fill(holder, S.strip(items, { unit: 'tok/s' }));
      }).catch(() => S.fill(holder, el('p', { class: 'empty-line', text: 'The other runs could not be read.' })));
    }

    const raw = el('details', { class: 'raw' }, el('summary', { text: 'All fields of this request' }),
      S.table([{ label: 'field', key: 'k' }, { label: 'value', key: 'v' }],
        Object.keys(r).filter((k) => !['run', 'report_request'].includes(k)).map((k) => ({ k, v: typeof r[k] === 'object' && r[k] !== null ? JSON.stringify(r[k]) : r[k] }))));
    body.push(raw);
    S.fill(a, head, el('div', { class: 'panel-body' }, body));
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && P.req && !document.getElementById('dialog').open) {
      if (P.ctx && P.ctx.closeHash) S.go(P.ctx.closeHash); else P.close();
    }
  });
})();
