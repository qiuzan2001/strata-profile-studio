/* Strata Profile Studio: the chart kit.  Bars, stacks, icicles and strips are HTML boxes sized in percent (crisp at
   every width, themed by CSS classes); the scatter and the sweep plots are SVG drawn at the container's width and
   drawn again when it changes.  Every mark can carry a tooltip and a click. */
'use strict';

(function () {
  const { el, svg, isNum } = S;

  // ------------------------------------------------------------------------------------------ tooltip
  const tip = () => document.getElementById('tip');
  S.tipShow = function tipShow(ev, lines) {
    const t = tip();
    S.clear(t);
    for (const [i, line] of lines.entries()) {
      if (Array.isArray(line)) t.appendChild(el('div', { class: 'tip-row' }, el('span', { text: line[0] }), el('b', { text: line[1] })));
      else t.appendChild(el('div', { class: i === 0 ? 'tip-head' : 'tip-line', text: line }));
    }
    t.hidden = false;
    const r = t.getBoundingClientRect();
    let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - 14;
    t.style.left = `${Math.max(4, x)}px`;
    t.style.top = `${Math.max(4, y)}px`;
  };
  S.tipHide = function tipHide() { tip().hidden = true; };
  S.hover = function hover(node, lines) {
    node.addEventListener('mousemove', (e) => S.tipShow(e, typeof lines === 'function' ? lines() : lines));
    node.addEventListener('mouseleave', S.tipHide);
    return node;
  };

  // --------------------------------------------------------------------------------- ranked bars
  // items: [{label, value, text, cls, hl, note, dots: [values], onclick, tip: [lines], icon}]
  // opts: {max, fmt, sort: 'desc'|'asc'|false, unit, empty}
  S.bars = function bars(items, opts) {
    const o = Object.assign({ sort: 'desc', fmt: S.tps }, opts || {});
    let list = items.filter((i) => isNum(i.value) || (i.dots && i.dots.some(isNum)));
    if (!list.length) return el('p', { class: 'empty-line', text: o.empty || 'Nothing measured yet.' });
    if (o.sort) list = list.slice().sort((a, b) => (o.sort === 'desc' ? (b.value ?? -1) - (a.value ?? -1) : (a.value ?? 1e18) - (b.value ?? 1e18)));
    const max = o.max || Math.max(...list.map((i) => Math.max(i.value || 0, ...(i.dots || []).filter(isNum)))) || 1;
    const box = el('div', { class: `bars ${o.cls || ''}` });
    for (const it of list) {
      const w = isNum(it.value) ? Math.max(0.4, (it.value / max) * 100) : 0;
      const wText = Math.max(w, ...(it.dots || []).filter(isNum).map((d) => (d / max) * 100));
      const track = el('div', { class: 'bar-track' });
      const bar = el('div', { class: `bar ${it.cls || 'k-bar'} ${it.hl ? 'hl' : ''}` });
      bar.style.width = `${w}%`;
      track.appendChild(bar);
      for (const d of (it.dots || []).filter(isNum)) {
        const dot = el('i', { class: 'bar-dot' });
        dot.style.left = `${(d / max) * 100}%`;
        track.appendChild(dot);
      }
      const val = el('span', { class: 'bar-val', text: it.text !== undefined ? it.text : `${o.fmt(it.value)}${o.unit ? ' ' + o.unit : ''}` });
      track.appendChild(val);
      // the value sits after the bar; a long label on a long bar goes inside the bar's end instead
      const chars = String(val.textContent).length;
      if (wText > 100 - Math.min(60, chars * 2.2) && w >= wText - 0.5 && chars > 9) { val.classList.add('inside'); val.style.right = `${100 - w}%`; }
      else val.style.left = `calc(${wText}% + 7px)`;
      const row = el('div', { class: `bar-row ${it.hl ? 'hl' : ''} ${it.onclick ? 'clickable' : ''} ${it.note ? 'with-note' : ''}` },
        el('div', { class: 'bar-label', title: it.title || it.label }, it.icon || null,
          el('div', { class: 'bar-names' }, el('span', { text: it.label }), it.note ? el('small', { text: it.note }) : null)),
        track);
      if (it.onclick) { row.addEventListener('click', it.onclick); row.tabIndex = 0; row.addEventListener('keydown', (e) => { if (e.key === 'Enter') it.onclick(e); }); }
      if (it.tip) S.hover(row, it.tip);
      box.appendChild(row);
    }
    // after layout: a value inside a bar too short for it goes after the bar; one that would leave the card goes under
    requestAnimationFrame(() => {
      for (const val of box.querySelectorAll('.bar-val')) {
        const track = val.parentElement, bar = track.firstChild;
        if (val.classList.contains('inside') && val.offsetWidth > bar.offsetWidth - 4) {
          val.classList.remove('inside'); val.style.right = ''; val.style.left = `calc(${bar.style.width} + 7px)`;
        }
        const room = box.getBoundingClientRect().right;
        if (val.getBoundingClientRect().right > room + 1) val.classList.add('below');
      }
    });
    return box;
  };

  // ------------------------------------------------------------------------------------ one stacked bar
  // parts: [{key, value, label?}] in order; opts: {scale: the value the full width stands for, total text, min label px}
  S.stack = function stack(parts, opts) {
    const o = opts || {};
    const vals = parts.filter((p) => isNum(p.value) && p.value > 0);
    const total = S.sum(vals.map((p) => p.value));
    const scale = o.scale || total || 1;
    const box = el('div', { class: `stack ${o.cls || ''}` });
    const inner = el('div', { class: 'stack-inner' });
    inner.style.width = `${Math.min(100, (total / scale) * 100)}%`;
    for (const p of vals) {
      const share = p.value / total;
      const s = el('div', { class: `seg-part ${S.PART_CLASS[p.key] || p.cls || ''} ${o.hl === p.key ? 'hl' : ''}` });
      s.style.flexGrow = String(p.value);
      s.style.flexBasis = '0';
      const label = p.label || S.PART_LABEL[p.key] || p.key;
      if (o.labels !== false) s.appendChild(el('span', { class: 'seg-text', text: o.short ? (o.fmt || S.num)(p.value) : `${label} ${(o.fmt || S.num)(p.value)}` }));
      S.hover(s, [label, [o.unit || 'ms', (o.fmt || S.num)(p.value)], ['share', S.pct(share)], S.PART_ABOUT[p.key] || '']);
      inner.appendChild(s);
    }
    box.appendChild(inner);
    if (o.total !== undefined) box.appendChild(el('span', { class: 'stack-total', text: o.total }));
    // hide a label that does not fit its segment (after layout)
    requestAnimationFrame(() => {
      for (const s of inner.children) {
        const t = s.firstChild;
        if (t && t.scrollWidth > s.clientWidth - 4) t.classList.add('hide');
      }
    });
    return box;
  };

  // --------------------------------------------------------------------------------------- the icicle
  // levels: [[{key, value, label, cls, start}]...] where level 0 is the whole and each later level's segments lie
  // under their parent: give `start` (cumulative) and `value` in one unit for all levels.
  S.icicle = function icicle(levels, opts) {
    const o = opts || {};
    const total = o.total || S.sum(levels[0].map((s) => s.value)) || 1;
    const box = el('div', { class: 'icicle' });
    levels.forEach((level, li) => {
      const row = el('div', { class: `ici-row ici-l${li}` });
      for (const s of level) {
        if (!isNum(s.value) || s.value <= 0) continue;
        const n = el('div', { class: `ici-seg ${s.cls || S.PART_CLASS[s.key] || ''} ${s.hl ? 'hl' : ''}` });
        n.style.left = `${(s.start / total) * 100}%`;
        n.style.width = `${(s.value / total) * 100}%`;
        const name = s.label || S.PART_LABEL[s.key] || s.key;
        const t = el('span', { class: 'seg-text' }, el('b', { text: name }), ' ', el('span', { text: `${S.ms(s.value)}${s.pct !== false ? ' · ' + S.pct(s.value / total) : ''}` }));
        n.appendChild(t);
        S.hover(n, [name, ['time', S.ms(s.value)], ['share of the whole', S.pct(s.value / total, 1)], s.about || S.PART_ABOUT[s.key] || '']);
        if (s.onclick) { n.classList.add('clickable'); n.addEventListener('click', s.onclick); }
        row.appendChild(n);
      }
      box.appendChild(row);
    });
    requestAnimationFrame(() => {
      for (const t of box.querySelectorAll('.seg-text')) {
        const p = t.parentElement;
        if (t.scrollWidth > p.clientWidth - 6) {
          t.lastChild.textContent = '';
          if (t.scrollWidth > p.clientWidth - 6) t.classList.add('hide');
        }
      }
    });
    return box;
  };

  // -------------------------------------------------------------------------------------- forest plot
  // rows: [{label, pct, floor (percent), assumed, state, note, onclick, group}]
  S.forest = function forest(rows, opts) {
    const o = opts || {};
    const lim = Math.max(5, ...rows.map((r) => Math.abs(r.pct || 0)), ...rows.map((r) => r.floor || 0)) * 1.15;
    const x = (v) => 50 + (v / lim) * 50;
    const box = el('div', { class: 'forest' });
    const axis = el('div', { class: 'forest-row axis' }, el('div', { class: 'f-label' }), (() => {
      const tr = el('div', { class: 'f-track' });
      for (const v of [-lim, -lim / 2, 0, lim / 2, lim]) {
        const t = el('span', { class: 'f-tick', text: `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(Math.abs(v) < 10 ? 1 : 0)}%` });
        t.style.left = `${x(v)}%`;
        tr.appendChild(t);
      }
      return tr;
    })(), el('div', { class: 'f-val' }));
    let group = null;
    for (const r of rows) {
      if (r.group && r.group !== group) {
        group = r.group;
        box.appendChild(el('div', { class: 'forest-group', text: r.group }));
      }
      const tr = el('div', { class: 'f-track' });
      const zero = el('i', { class: 'f-zero' });
      zero.style.left = '50%';
      tr.appendChild(zero);
      if (isNum(r.floor)) {
        const band = el('i', { class: `f-band ${r.assumed ? 'assumed' : ''}` });
        band.style.left = `${x(-r.floor)}%`;
        band.style.width = `${x(r.floor) - x(-r.floor)}%`;
        tr.appendChild(band);
      }
      if (isNum(r.pct)) {
        const dot = el('i', { class: `f-dot ${r.state || 'same'}` });
        dot.style.left = `${x(r.pct)}%`;
        tr.appendChild(dot);
      }
      const row = el('div', { class: `forest-row ${r.onclick ? 'clickable' : ''} ${r.cls || ''}` },
        el('div', { class: 'f-label', text: r.label }), tr,
        el('div', { class: `f-val ${r.state || ''}`, text: isNum(r.pct) ? S.signed(r.pct, 1, '%') : 'not in both' }));
      S.hover(row, [r.label, ['change', isNum(r.pct) ? S.signed(r.pct, 1, '%') : '–'], ['A → B', r.ab || '–'],
        ['noise floor', isNum(r.floor) ? `±${r.floor.toFixed(1)}%${r.assumed ? ' (assumed)' : ''}` : '–'],
        r.state === 'better' ? 'outside the noise: better' : r.state === 'worse' ? 'outside the noise: worse' : 'inside the noise', r.note || '']);
      if (r.onclick) row.addEventListener('click', r.onclick);
      box.appendChild(row);
    }
    box.appendChild(axis);
    if (o.legend !== false) {
      box.appendChild(el('div', { class: 'legend' },
        el('span', { class: 'lg' }, el('i', { class: 'sw band' }), 'noise floor (measured)'),
        el('span', { class: 'lg' }, el('i', { class: 'sw band assumed' }), 'assumed floor'),
        el('span', { class: 'lg' }, el('i', { class: 'sw dot better' }), 'better, outside the noise'),
        el('span', { class: 'lg' }, el('i', { class: 'sw dot worse' }), 'worse, outside the noise'),
        el('span', { class: 'lg' }, el('i', { class: 'sw dot same' }), 'inside the noise')));
    }
    return box;
  };

  // ------------------------------------------------------------------------------------ a dot strip
  // items: [{value, label, hl, cls, tip}] on one axis; for "this request in other runs" and noise groups
  S.strip = function strip(items, opts) {
    const o = Object.assign({ fmt: S.tps }, opts || {});
    const vals = items.map((i) => i.value).filter(isNum);
    if (!vals.length) return el('p', { class: 'empty-line', text: 'No values.' });
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.15, hi * 0.02, 0.5);
    lo -= pad; hi += pad;
    const box = el('div', { class: 'strip' });
    const track = el('div', { class: 'strip-track' });
    const placed = [];                            // label rows: a label moves up a row when it would touch another
    const sortedItems = items.filter((i) => isNum(i.value)).sort((a, b) => a.value - b.value);
    for (const it of sortedItems) {
      const d = el('i', { class: `strip-dot ${it.hl ? 'hl' : ''} ${it.cls || ''}` });
      d.style.left = `${((it.value - lo) / (hi - lo)) * 100}%`;
      S.hover(d, it.tip || [it.label, [o.unit || 'value', o.fmt(it.value)]]);
      if (it.onclick) { d.classList.add('clickable'); d.addEventListener('click', it.onclick); }
      track.appendChild(d);
      if (it.hl || it.labelled) {
        const pos = ((it.value - lo) / (hi - lo)) * 100;
        const text = o.labelValues === false ? `${it.short || it.label}` : `${it.short || it.label} ${o.fmt(it.value)}`;
        const need = text.length * 1.6 + 2;
        let row = 0;
        while (placed.some((p) => p.row === row && Math.abs(p.pos - pos) < (p.need + need) / 2)) row++;
        placed.push({ pos, need, row });
        const t = el('span', { class: `strip-label ${it.hl ? 'hl' : ''}`, text });
        t.style.left = `${pos}%`;
        t.style.bottom = `${10 + row * 13}px`;
        track.appendChild(t);
      }
    }
    const ends = el('div', { class: 'strip-ends' }, el('span', { text: o.fmt(Math.min(...vals)) }), el('span', { text: `spread ${S.pct((Math.max(...vals) - Math.min(...vals)) / Math.min(...vals), 1)}` }), el('span', { text: o.fmt(Math.max(...vals)) }));
    box.append(track, ends);
    return box;
  };

  // ------------------------------------------------------------------------------------------ axes
  function niceTicks(lo, hi, n) {
    const span = hi - lo || 1;
    const step0 = span / n;
    const mag = 10 ** Math.floor(Math.log10(step0));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  S.niceTicks = niceTicks;

  // --------------------------------------------------------------------------------------- scatter
  // points: [{x, y, kind, band?, hl, out, onclick, tip}]; opts: {xLog, xLabel, yLabel, xFmt, yFmt, bands: [{from,to,kind,label}], median: true}
  S.scatter = function scatter(host, points, opts) {
    const o = Object.assign({ height: 260, xFmt: S.num, yFmt: S.num }, opts || {});
    const W = Math.max(280, host.clientWidth || 600), H = o.height;
    const m = { l: 44, r: 12, t: 10, b: 30 };
    const pts = points.filter((p) => isNum(p.x) && isNum(p.y) && (!o.xLog || p.x > 0));
    const s = svg('svg', { class: 'scatter', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': o.aria || '' });
    if (!pts.length) {
      s.appendChild(svg('text', { x: W / 2, y: H / 2, class: 'empty-svg', 'text-anchor': 'middle', text: o.empty || 'No requests to plot yet.' }));
      S.fill(host, s);
      return s;
    }
    let x0 = Math.min(...pts.map((p) => p.x)), x1 = Math.max(...pts.map((p) => p.x));
    for (const b of o.bands || []) { if (o.xTime) { x0 = Math.min(x0, b.from); x1 = Math.max(x1, b.to); } }
    if (o.xTime) { x0 = Math.max(x0, Math.min(...pts.map((p) => p.x)) - 60); }
    let y0 = Math.min(0, ...pts.map((p) => p.y)), y1 = Math.max(...pts.map((p) => p.y)) * 1.08 || 1;
    if (o.y0 !== undefined) y0 = o.y0;
    if (o.y1 !== undefined) y1 = Math.max(o.y1, y1);
    if (x1 === x0) { x0 -= 1; x1 += 1; }
    const lx = (v) => (o.xLog ? Math.log10(v) : v);
    const X = (v) => m.l + ((lx(v) - lx(x0)) / (lx(x1) - lx(x0) || 1)) * (W - m.l - m.r);
    const Y = (v) => H - m.b - ((v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);
    const g = svg('g', { class: 'axes' });
    for (const t of niceTicks(y0, y1, 5)) {
      g.appendChild(svg('line', { class: 'grid', x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
      g.appendChild(svg('text', { class: 'tick', x: m.l - 6, y: Y(t) + 4, 'text-anchor': 'end', text: o.yFmt(t) }));
    }
    let xt;
    if (o.xLog) { xt = []; for (let e = Math.floor(Math.log10(x0)); e <= Math.ceil(Math.log10(x1)); e++) for (const k of [1, 3]) { const v = k * 10 ** e; if (v >= x0 && v <= x1) xt.push(v); } }
    else if (o.xTime) { const span = x1 - x0; const step = [60, 300, 600, 900, 1800, 3600, 7200, 10800].find((s) => span / s <= 6) || 21600; xt = []; for (let v = Math.ceil(x0 / step) * step; v <= x1; v += step) xt.push(v); }
    else if (o.xTicks) xt = null;
    else xt = niceTicks(x0, x1, Math.max(3, Math.floor(W / 110)));
    if (xt) for (const t of xt) g.appendChild(svg('text', { class: 'tick', x: X(t), y: H - m.b + 16, 'text-anchor': 'middle', text: o.xTime ? S.clock(t) : o.xFmt(t) }));
    if (o.xTicks) {
      let lastX = -1e9;
      for (const t of o.xTicks) {
        const px = X(t.x);
        if (px - lastX < 46) continue;
        lastX = px;
        g.appendChild(svg('line', { class: 'tick-mark', x1: px, x2: px, y1: H - m.b, y2: H - m.b + 4 }));
        g.appendChild(svg('text', { class: 'tick', x: px, y: H - m.b + 16, 'text-anchor': px > W - 70 ? 'end' : 'start', text: t.label }));
      }
    }
    g.appendChild(svg('line', { class: 'axis', x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b }));
    if (o.xLabel) g.appendChild(svg('text', { class: 'axis-label', x: W - m.r, y: H - 2, 'text-anchor': 'end', text: o.xLabel }));
    if (o.yLabel) g.appendChild(svg('text', { class: 'axis-label', x: m.l, y: m.t + 2, text: '' }));
    s.appendChild(g);
    // bands (profile runs, tunes) behind the points
    for (const b of o.bands || []) {
      if (b.to < x0 || b.from > x1) continue;
      const a = X(Math.max(b.from, x0)), z = X(Math.min(b.to, x1));
      const r = svg('rect', { class: `band band-${b.kind}`, x: a, y: m.t, width: Math.max(2, z - a), height: H - m.t - m.b });
      S.hover(r, [b.label, ['from', S.clock(b.from, 'sec')], ['to', S.clock(b.to, 'sec')]]);
      s.appendChild(r);
      if (z - a > 40) s.appendChild(svg('text', { class: `band-label band-${b.kind}`, x: a + 3, y: m.t + 11, text: b.short || b.label }));
    }
    // markers between requests: something that took time but sent none (the calibration)
    for (const gm of o.gaps || []) {
      const px = X(gm.x);
      s.appendChild(svg('line', { class: `gap-line gap-${gm.kind}`, x1: px, x2: px, y1: m.t, y2: H - m.b }));
      const lab = svg('text', { class: 'gap-label', x: px + 4, y: m.t + 26, text: gm.label });
      S.hover(lab, [gm.label, gm.about || '']);
      s.appendChild(lab);
    }
    // the rolling median, oldest to newest; broken where the points have a gap (p.cut)
    if (o.median && pts.filter((p) => !p.calib).length >= 5) {
      const sorted = pts.filter((p) => !p.calib).sort((a, b) => a.x - b.x);
      const k = Math.max(3, Math.round(sorted.length / 10));
      let d = '';
      sorted.forEach((p, i) => {
        const win = sorted.slice(Math.max(0, i - k), i + 1).map((q) => q.y);
        d += `${i && !p.cut ? 'L' : 'M'}${X(p.x).toFixed(1)} ${Y(S.median(win)).toFixed(1)}`;
      });
      s.appendChild(svg('path', { class: 'median-line', d }));
    }
    for (const p of pts) {
      const gm = svg('g', { class: `pt ${p.hl ? 'hl' : ''} ${p.out ? 'out' : ''} ${p.run ? 'in-run' : ''} ${p.calib ? 'calib' : ''}` });
      if (p.out) gm.appendChild(svg('circle', { class: 'out-ring', cx: X(p.x), cy: Y(p.y), r: 8 }));
      gm.appendChild(S.kindMark(p.kind, X(p.x), Y(p.y), p.hl ? 5.5 : 4.2));
      gm.appendChild(svg('circle', { class: 'hit', cx: X(p.x), cy: Y(p.y), r: 9 }));
      if (p.tip) S.hover(gm, p.tip);
      if (p.onclick) { gm.addEventListener('click', p.onclick); gm.classList.add('clickable'); }
      if (p.label) gm.appendChild(svg('text', { class: 'pt-label', x: X(p.x) + 8, y: Y(p.y) - 7, text: p.label }));
      s.appendChild(gm);
    }
    S.fill(host, s);
    return s;
  };

  // ---------------------------------------------------------------------------------- the sweep plot
  // values: [{value, rounds:[...], median, min, max}] in order; marks: {kept, best, was, def}; opts: {height}
  S.sweep = function sweep(host, step, opts) {
    const o = Object.assign({ height: 210 }, opts || {});
    const W = Math.max(220, host.clientWidth || 300), H = o.height;
    const m = { l: 36, r: 8, t: 18, b: 54 };
    const vals = step.values || [];
    const ys = vals.flatMap((v) => [v.median, v.min, v.max, ...(v.rounds || [])]).filter(isNum);
    const s = svg('svg', { class: 'sweep', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': step.label });
    if (!vals.length || !ys.length) {
      const what = vals.length ? `measuring ${vals.map((v) => v.value).join(', ')}…` : (o.empty || 'not measured yet');
      s.appendChild(svg('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'empty-svg', text: what }));
      S.fill(host, s);
      return s;
    }
    let y0 = ys.length ? Math.min(...ys) : 0, y1 = ys.length ? Math.max(...ys) : 1;
    const pad = Math.max((y1 - y0) * 0.18, 1);
    y0 -= pad; y1 += pad;
    const n = vals.length;
    const X = (i) => m.l + ((i + 0.5) / n) * (W - m.l - m.r);
    const Y = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);
    for (const t of niceTicks(y0, y1, 4)) {
      s.appendChild(svg('line', { class: 'grid', x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
      s.appendChild(svg('text', { class: 'tick', x: m.l - 5, y: Y(t) + 4, 'text-anchor': 'end', text: S.num(t, 0) }));
    }
    vals.forEach((v, i) => {
      const cx = X(i);
      const isKept = S.same(v.value, step.kept), isBest = S.same(v.value, step.best), isWas = S.same(v.value, step.was), isDef = S.same(v.value, step.default);
      const g = svg('g', { class: `cand ${isKept ? 'kept' : ''} ${isBest && !isKept ? 'best' : ''}` });
      if (isNum(v.min) && isNum(v.max)) g.appendChild(svg('line', { class: 'whisker', x1: cx, x2: cx, y1: Y(v.min), y2: Y(v.max) }));
      (v.rounds || []).forEach((r, k) => { if (isNum(r)) g.appendChild(svg('circle', { class: 'round', cx: cx + (k - (v.rounds.length - 1) / 2) * 5, cy: Y(r), r: 2.2 })); });
      if (isNum(v.median)) {
        g.appendChild(svg('circle', { class: 'med', cx, cy: Y(v.median), r: 4.5 }));
        if (isKept) g.appendChild(svg('circle', { class: 'ring kept', cx, cy: Y(v.median), r: 9 }));
        else if (isBest) g.appendChild(svg('circle', { class: 'ring best', cx, cy: Y(v.median), r: 9 }));
        g.appendChild(svg('text', { class: `val ${isKept ? 'kept' : ''}`, x: cx, y: Y(v.max ?? v.median) - (isKept || isBest ? 13 : 8), 'text-anchor': 'middle', text: S.num(v.median, 1) }));
      }
      const lab = String(v.value).replace(/^(\d+)\/(\d+)\/([\d.]+)$/, '$1/$2/$3');
      g.appendChild(svg('text', { class: `xval ${isKept ? 'kept' : ''}`, x: cx, y: H - m.b + 15, 'text-anchor': 'middle', text: lab === 'default' ? 'engine default' : lab }));
      const tags = [isKept ? 'kept' : null, isBest && !isKept ? 'best' : null, isWas ? 'was' : null, isDef && !isWas ? 'default' : null].filter(Boolean);
      tags.forEach((tag, k) => g.appendChild(svg('text', { class: `xtag tag-${tag}`, x: cx, y: H - m.b + 28 + k * 12, 'text-anchor': 'middle', text: tag })));
      S.hover(g, [`${step.label} ${lab}`, ['median', isNum(v.median) ? `${S.num(v.median, 1)} tok/s` : '–'],
        ['rounds', (v.rounds || []).length ? v.rounds.map((r) => S.num(r, 1)).join(', ') : 'one measurement'],
        ['range', isNum(v.min) ? `${S.num(v.min, 1)}–${S.num(v.max, 1)}` : '–'], tags.join(', ')]);
      s.appendChild(g);
    });
    S.fill(host, s);
    return s;
  };
  S.same = function same(a, b) {
    if (a === null || a === undefined || b === null || b === undefined) return false;
    const fa = parseFloat(a), fb = parseFloat(b);
    if (isFinite(fa) && isFinite(fb) && /^[\d.]+$/.test(String(a)) && /^[\d.]+$/.test(String(b))) return Math.abs(fa - fb) < 1e-9;
    return String(a) === String(b);
  };

  // ------------------------------------------------------------------------------------- sparkline
  // built once; update(values) sets the path's d attribute
  S.sparkline = function sparkline(cls) {
    const s = svg('svg', { class: `spark ${cls || ''}`, viewBox: '0 0 100 24', preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    const area = svg('path', { class: 'spark-area', d: '' });
    const line = svg('path', { class: 'spark-line', d: '' });
    s.append(area, line);
    s.update = (vals, max) => {
      const v = (vals || []).map((x) => (isNum(x) ? x : null));
      const n = v.length;
      if (n < 2) { line.setAttribute('d', ''); area.setAttribute('d', ''); return; }
      const hi = max || Math.max(1e-9, ...v.filter(isNum));
      let d = '', started = false;
      v.forEach((x, i) => {
        if (x === null) return;
        const px = (i / (n - 1)) * 100, py = 23 - Math.min(1, x / hi) * 22;
        d += `${started ? 'L' : 'M'}${px.toFixed(2)} ${py.toFixed(2)}`;
        started = true;
      });
      line.setAttribute('d', d);
      area.setAttribute('d', d ? `${d}L100 24L0 24Z` : '');
    };
    return s;
  };
})();
