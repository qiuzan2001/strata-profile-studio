/* Strata cards: the filters of the front page and the comparison page.  No library and no HTML strings: each node
   is made with createElement, and text is set with textContent. */
'use strict';

(function () {
  const SVG = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) { if (k === 'text') n.textContent = v; else n.setAttribute(k, v); }
    for (const k of kids.flat()) if (k != null) n.append(typeof k === 'string' ? document.createTextNode(k) : k);
    return n;
  };
  const sv = (tag, attrs, text) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    return n;
  };
  const fmt = (v, n = 1) => (v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: n, maximumFractionDigits: n }));

  // ---- the front page: filters and the compare button
  const table = document.getElementById('cards');
  if (table) {
    const sels = [...document.querySelectorAll('select[data-filter]')];
    const rows = [...table.querySelectorAll('tr[data-gpu]')];
    const shown = document.getElementById('shown');
    const apply = () => {
      let n = 0;
      for (const r of rows) {
        const ok = sels.every((s) => !s.value || r.dataset[s.dataset.filter] === s.value);
        r.hidden = !ok;
        n += ok;
      }
      shown.textContent = `${n} of ${rows.length} entries`;
    };
    sels.forEach((s) => s.addEventListener('change', apply));
    const btn = document.getElementById('compare');
    const boxes = [...table.querySelectorAll('input[type=checkbox]')];
    const update = () => {
      const ids = boxes.filter((b) => b.checked).map((b) => b.value);
      btn.disabled = ids.length < 2;
      btn.textContent = ids.length < 2 ? 'Compare the selected cards' : `Compare the ${ids.length} selected cards`;
    };
    boxes.forEach((b) => b.addEventListener('change', update));
    btn.addEventListener('click', () => {
      const ids = boxes.filter((b) => b.checked).map((b) => b.value);
      location.href = `compare.html?ids=${ids.map(encodeURIComponent).join(',')}`;
    });
  }

  // ---- the comparison page
  const forest = document.getElementById('cmp-forest');
  if (forest) {
    const ids = (new URLSearchParams(location.search).get('ids') || '').split(',').filter((x) => /^[a-z2-7]{10}$/.test(x)).slice(0, 8);
    if (ids.length < 2) return;
    Promise.all(ids.map((id) => fetch(`data/${id}.json`).then((r) => (r.ok ? r.json() : null)))).then((cards) => {
      cards = cards.filter(Boolean);
      if (cards.length < 2) { document.getElementById('cmp-sub').textContent = 'A selected card is not on this site.'; return; }
      const [a] = cards;
      document.getElementById('cmp-title').textContent = `${cards.length} cards compared`;
      document.getElementById('cmp-sub').textContent = `reference: ${a.id} · a difference counts only when it is larger than the larger noise floor of the two cards`;
      const metrics = [];
      for (const d of a.decode) metrics.push({ label: d.kind, get: (c) => (c.decode.find((x) => x.kind === d.kind) || null) });
      for (const p of a.prefill) metrics.push({ label: `read ${p.label}`, get: (c) => (c.prefill.find((x) => x.label === p.label) || null) });
      // forest plot: one block per other card
      const W = 1100, mid = 560, scale = 1100;
      const X = (f) => mid + Math.max(-0.45, Math.min(0.45, f)) * scale;
      for (const b of cards.slice(1)) {
        let y = 24;
        const s = sv('svg', { viewBox: `0 0 ${W} ${metrics.length * 28 + 34}`, width: '100%', role: 'img', 'aria-label': `Change from ${a.id} to ${b.id}` });
        s.append(sv('text', { x: 0, y: 12, 'font-size': 12.5, 'font-weight': 600 }, `${a.id} to ${b.id}${b.name ? ` (${b.name})` : ''}`));
        for (const m of metrics) {
          const ma = m.get(a), mb = m.get(b);
          y += 28;
          s.append(sv('text', { x: 0, y: y + 4, 'font-size': 12.5 }, m.label));
          if (!ma || !mb) { s.append(sv('text', { x: W - 10, y: y + 4, 'font-size': 12.5, 'text-anchor': 'end', fill: '#85847d' }, 'not in both cards')); continue; }
          const pct = (mb.tps - ma.tps) / ma.tps, fl = Math.max(ma.floor, mb.floor);
          const col = Math.abs(pct) <= fl ? '#85847d' : pct > 0 ? '#2f8a3e' : '#c8382e';
          s.append(sv('rect', { x: X(-fl), y: y - 9, width: X(fl) - X(-fl), height: 18, fill: 'rgba(110,108,100,.18)' }));
          s.append(sv('line', { x1: mid, x2: mid, y1: y - 11, y2: y + 11, stroke: 'rgba(110,108,100,.55)' }));
          s.append(sv('circle', { cx: X(pct), cy: y, r: 5.5, fill: col }));
          s.append(sv('text', { x: W - 10, y: y + 4, 'font-size': 12.5, 'text-anchor': 'end', 'font-weight': 600, fill: col },
            `${pct >= 0 ? '+' : ''}${(pct * 100).toFixed(1)} %${Math.abs(pct) <= fl ? ' (noise)' : ''}`));
        }
        forest.append(s);
      }
      // speeds
      const head = el('tr', null, el('th', { text: 'Workload' }), cards.map((c) => el('th', { class: 'num' }, el('a', { href: `c/${c.id}/`, text: c.id }))));
      const body = metrics.map((m) => el('tr', null, el('td', { text: m.label }), cards.map((c) => {
        const v = m.get(c);
        return el('td', { class: 'num', text: v ? fmt(v.tps, m.label.startsWith('read') ? 0 : 1) : '–' });
      })));
      const pcRow = el('tr', null, el('td', { text: 'PC' }), cards.map((c) => el('td', { class: 'num small', text: `${c.pc.gpu.name.replace('NVIDIA GeForce ', '')} · ${c.pc.cpu.name.replace('AMD ', '')}` })));
      document.getElementById('cmp-speeds').append(el('table', null, head, pcRow, body));
      // settings that differ
      const flags = [...new Set(cards.flatMap((c) => c.settings.map((s) => s.flag)))].sort();
      const val = (c, f) => { const s = c.settings.find((x) => x.flag === f); return s ? s.value : '–'; };
      const diff = flags.filter((f) => new Set(cards.map((c) => val(c, f))).size > 1);
      const st = document.getElementById('cmp-settings');
      if (!diff.length) st.append(el('p', { class: 'small muted', text: 'The cards have the same settings.' }));
      else st.append(el('table', null, el('tr', null, el('th', { text: 'Setting' }), cards.map((c) => el('th', { class: 'num', text: c.id }))),
        diff.map((f) => el('tr', null, el('td', { class: 'mono', text: f }), cards.map((c) => el('td', { class: 'num mono', text: val(c, f) }))))));
    });
  }
})();
