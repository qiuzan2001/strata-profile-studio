/* Strata Profile Studio: the expert map.  One cell per expert: layers (rows) x experts (columns), sizes from the profile header, coloured by
   the expert's rank in the expert profile (the order the engine fills its VRAM cache in).  The first `expert_slots`
   ranks are the experts resident at load.  A learned profile (the engine's expert_profile_save) shows where the
   adaptive tier moved experts, and two saves show what changed between them.

   Per-expert data: the profile files only (with their time).  The engine reports no expert ids per decode window,
   so the live flow is the per-window counts in the PC module, never a guess per expert.  Drawn on a canvas, only
   when the data or the view options change. */
'use strict';

(function () {
  const { el, isNum } = S;
  const NOT_RANKED = 65535;

  function decode(b64) {
    if (!b64) return null;
    const bin = atob(b64);
    const out = new Uint16Array(bin.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
    return out;
  }
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function hexToRgb(h) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(h);
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [128, 128, 128];
  }
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

  const M = (S.expertMap = { ui: { zoom: S.store.get('map.zoom', 2), order: S.store.get('map.order', 'id'), source: 'base', changes: true } });

  M.build = function build() {
    const canvas = el('canvas', { class: 'map-canvas', 'aria-label': 'Expert map' });
    const bars = el('canvas', { class: 'map-bars', 'aria-hidden': 'true' });
    const wrap = el('div', { class: 'map-wrap' }, canvas, bars);
    const info = el('div', { class: 'map-info muted small' });
    const tools = el('div', { class: 'card-tools' });
    const legend = el('div', { class: 'legend map-legend' });
    const card = S.card({ q: 'Expert map', sub: 'layers (rows) x experts (columns) · colour: the expert\'s rank in the profile · blue: resident in the VRAM cache at load', cls: 'span3 map-card', tools },
      info, wrap, legend);
    M.r = { canvas, bars, wrap, info, tools, legend, card };
    canvas.addEventListener('mousemove', (e) => {
      const d = M.data;
      if (!d) return;
      const r = canvas.getBoundingClientRect();
      const cw = r.width / d.experts, ch = r.height / d.layers;
      const layer = Math.floor((e.clientY - r.top) / ch), col = Math.floor((e.clientX - r.left) / cw);
      if (layer < 0 || layer >= d.layers || col < 0 || col >= d.experts) return;
      const e2 = M.ui.order === 'rank' ? d.byRank[layer][col] : col;
      const rank = d.ranks[layer * d.experts + e2];
      const lines = [`layer ${layer} · expert ${e2}`, ['rank', rank === NOT_RANKED ? 'not ranked' : S.int(rank)], ['resident at load', rank < d.slots ? 'yes' : 'no']];
      if (d.prev) { const p = d.prev[layer * d.experts + e2]; lines.push(['rank in the previous save', p === NOT_RANKED ? 'not ranked' : S.int(p)]); }
      S.tipShow(e, lines);
    });
    canvas.addEventListener('mouseleave', S.tipHide);
    return card;
  };

  function tools(doc) {
    const ui = M.ui;
    const have = { base: !!(doc.base && doc.base.ranks_b64), learned: !!(doc.learned && doc.learned.ranks_b64) };
    if (!have[ui.source]) ui.source = have.learned ? 'learned' : 'base';
    S.fill(M.r.tools,
      S.seg([['base', 'profile file'], ['learned', 'learned profile']].filter(([k]) => have[k]), ui.source, (v) => { ui.source = v; M.update(doc, true); }, 'source'),
      S.seg([['id', 'by expert id'], ['rank', 'by rank']], ui.order, (v) => { ui.order = v; S.store.set('map.order', v); M.update(doc, true); }, 'column order'),
      S.seg([[2, '2 px'], [3, '3 px'], [4, '4 px']], ui.zoom, (v) => { ui.zoom = v; S.store.set('map.zoom', v); M.update(doc, true); }, 'cell size'));
  }

  M.update = function update(doc, force) {
    if (!M.r || !doc) return;
    const key = JSON.stringify([doc.base && doc.base.mtime, doc.learned && doc.learned.mtime, doc.resident_slots, M.ui, document.documentElement.dataset.theme || '']);
    if (key === M._key && !force) return;
    M._key = key;
    tools(doc);
    const src = M.ui.source === 'learned' ? doc.learned : doc.base;
    if (!src || !src.ranks_b64) {
      S.fill(M.r.info, el('span', { text: doc.config ? (doc.base && doc.base.error ? `The expert profile cannot be read: ${doc.base.error}` : 'The run config names no --expert-profile.') : 'The Studio needs --config to find the expert profile.' }));
      S.show(M.r.wrap, false);
      return;
    }
    S.show(M.r.wrap, true);
    const ranks = decode(src.ranks_b64);
    const layers = src.layers, experts = src.experts;
    const slots = isNum(doc.resident_slots) ? doc.resident_slots : src.header_slots;
    const prev = M.ui.source === 'learned' && src.previous ? decode(src.previous.ranks_b64) : null;
    const byRank = [];
    for (let l = 0; l < layers; l++) {
      const ids = Array.from({ length: experts }, (_, e) => e);
      ids.sort((a, b) => ranks[l * experts + a] - ranks[l * experts + b]);
      byRank.push(ids);
    }
    M.data = { ranks, layers, experts, slots, prev, byRank };
    S.setText(M.r.card.querySelector('.sub'), `${layers} layers (rows) x ${experts} experts (columns), from the profile header · colour: the expert's rank in the profile · blue: resident in the VRAM cache at load`);
    // info: what is per-expert data, from where and when; what is not
    const learnedLine = doc.learned_configured
      ? (doc.learned && doc.learned.mtime ? `Learned profile ${doc.learned.path}: saved ${S.clock(doc.learned.mtime, true)} (every ${doc.learned_every_min} min).${doc.learned.previous ? ` Since the save of ${S.clock(doc.learned.previous.mtime, true)}: ${S.int(doc.learned.previous.entered)} experts entered the cache, ${S.int(doc.learned.previous.left)} left.` : ''}` : 'The learned profile is on, but the engine has not saved one yet.')
      : 'Learned profile: off. To see where the adaptive tier moves experts, add "expert_profile_save": "data/expert-profile-learned.bin" to the run config. The engine then saves it every 10 min.';
    S.fill(M.r.info,
      el('div', null, el('b', { text: 'Per-expert data: ' }), `${src.path}, ${S.clock(src.mtime, true)}. ${S.int(slots)} of ${S.int(layers * experts)} experts are resident at load (the engine fills its cache in rank order).`),
      el('div', null, el('b', { text: 'Not per expert: ' }), 'the live counts per decode window (cache hits, PCIe reads, CPU experts, swaps). The engine reports no expert ids per window. The PC module shows these counts as flows.'),
      el('div', null, learnedLine));
    // draw
    const z = Number(M.ui.zoom) || 2, ch = z * 3;
    const c = M.r.canvas, g = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    c.width = experts * z * dpr; c.height = layers * ch * dpr;
    c.style.width = `${experts * z}px`; c.style.height = `${layers * ch}px`;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const hot = hexToRgb(css('--k-gemm')), cool = hexToRgb(css('--k-gdn'));
    const cold = hexToRgb(css('--line-2')), coldEnd = hexToRgb(css('--surface-2'));
    const total = layers * experts;
    for (let l = 0; l < layers; l++) {
      for (let col = 0; col < experts; col++) {
        const e = M.ui.order === 'rank' ? byRank[l][col] : col;
        const r = ranks[l * experts + e];
        let rgb;
        if (r === NOT_RANKED) rgb = coldEnd;
        else if (r < slots) rgb = mix(hot, cool, r / Math.max(1, slots));
        else rgb = mix(cold, coldEnd, (r - slots) / Math.max(1, total - slots));
        g.fillStyle = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
        g.fillRect(col * z, l * ch, z, ch - (ch > 4 ? 1 : 0));
        if (prev && M.ui.changes) {
          const p = prev[l * experts + e];
          const was = p < slots, is = r < slots;
          if (was !== is) { g.fillStyle = is ? css('--good') : css('--bad'); g.fillRect(col * z, l * ch, z, ch - 1); }
        }
      }
    }
    // resident experts per layer, as bars to the right
    const b = M.r.bars, bg = b.getContext('2d');
    b.width = 120 * dpr; b.height = layers * ch * dpr; b.style.width = '120px'; b.style.height = `${layers * ch}px`;
    bg.setTransform(dpr, 0, 0, dpr, 0, 0);
    const per = [];
    for (let l = 0; l < layers; l++) { let n = 0; for (let e = 0; e < experts; e++) if (ranks[l * experts + e] < slots) n++; per.push(n); }
    const maxN = Math.max(1, ...per);
    bg.fillStyle = css('--k-gemm');
    per.forEach((n, l) => bg.fillRect(0, l * ch, (n / maxN) * 90, Math.max(1, ch - 1)));
    bg.fillStyle = css('--ink-3'); bg.font = '10px system-ui';
    if (ch >= 9) per.forEach((n, l) => bg.fillText(String(n), 94, l * ch + ch - 2));
    S.fill(M.r.legend,
      el('span', { class: 'lg' }, el('i', { class: 'sw grad-hot' }), `resident at load: rank 0 to ${S.int(slots - 1)}`),
      el('span', { class: 'lg' }, el('i', { class: 'sw grad-cold' }), `not resident: rank to ${S.int(total - 1)}`),
      el('span', { class: 'lg' }, el('i', { class: 'sw k-gemm' }), `bars: resident experts per layer (${S.int(Math.min(...per))} to ${S.int(maxN)})`),
      prev ? el('span', { class: 'lg' }, el('i', { class: 'sw dot better' }), 'entered the cache since the previous save') : null,
      prev ? el('span', { class: 'lg' }, el('i', { class: 'sw dot worse' }), 'left the cache') : null);
  };
})();
