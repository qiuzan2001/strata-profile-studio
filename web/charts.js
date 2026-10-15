/* charts.js: the Studio's marks, drawn with Chart.js 4.4.1 (vendored) and one small inline label/tick plugin.
 *
 * Marks (redesign section 5.1):
 *   hbar        one stacked horizontal bar (decode window, prompt read); the dominant segment is full opacity with
 *               its value and name above it, the others at .55
 *   ring        doughnut of expert sources (hit, PCIe, CPU) with a reference tick
 *   spark       sparkline with an IQR band and the last point marked
 *   multiples   three small charts on one x, with a shared crosshair
 *   bullets     one row per metric: band (noise floor around A), bar (B), tick (A), label left, delta right
 *   where       A over B decode bars per kind on one ms scale
 *   candidates  dot plot of a tune step: rounds grey, median ink, kept ring
 * Colours come from CSS custom properties (--k-card ...), so repaintAll() follows the theme.
 * Each canvas carries data-* attributes with what it draws, for assistive text and for checks.
 */
(function () {
  'use strict';

  if (typeof Chart === 'undefined') {
    window.StudioCharts = null;
    return;
  }

  const DECODE_NAMES = ['card', 'CPU experts', 'draft', 'adapt wait', 'other'];
  const PROMPT_NAMES = ['PCIe + dequant', 'compute', 'other'];
  // the engine's names -> the shared names (the prompt's six groups fold into three)
  const DECODE_FROM_ENGINE = { 'GPU': 'card', 'CPU experts': 'CPU experts', 'draft': 'draft', 'adapt wait': 'adapt wait', 'other': 'other' };
  const PROMPT_FROM_ENGINE = {
    'experts over PCIe': 'PCIe + dequant', 'dequant': 'PCIe + dequant',
    'expert GEMM': 'compute', 'attention': 'compute', 'GDN': 'compute', 'other': 'other',
  };
  const KIND_TOKEN = {
    'card': '--k-card', 'compute': '--k-card', 'CPU experts': '--k-cpu', 'draft': '--k-draft',
    'PCIe + dequant': '--k-pcie', 'adapt wait': '--k-wait', 'other': '--k-other',
  };
  const TOKENS = ['--k-card', '--k-cpu', '--k-draft', '--k-pcie', '--k-wait', '--k-other', '--good', '--good-text',
    '--bad', '--noise', '--ink', '--ink-muted', '--surface', '--line'];
  const SMALL_SCREEN = '(max-width: 600px)';

  function pal() {
    const cs = getComputedStyle(document.documentElement);
    const p = {};
    for (const t of TOKENS) p[t] = cs.getPropertyValue(t).trim() || '#888888';
    p.font = getComputedStyle(document.body).fontFamily;
    return p;
  }

  function alpha(color, a) {
    const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color);
    if (!m) return color;
    let hex = m[1];
    if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('');
    const n = parseInt(hex, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }

  const reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  Chart.defaults.animation = false;                    // first paint, tooltips, theme changes: never animated
  Chart.defaults.responsive = true;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.font.size = 12;
  Chart.defaults.plugins.legend.display = false;

  const handles = new Set();
  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const fmt1 = (v) => (isNum(v) ? v.toFixed(1) : '—');
  const narrow = () => !!(window.matchMedia && window.matchMedia(SMALL_SCREEN).matches);

  function font(size, weight, p) { return `${weight} ${size}px ${p.font}`; }

  function register(handle) {
    handles.add(handle);
    return handle;
  }

  function animate(chart, on) {
    // only the decode/prompt bars and the ring move, 300-400 ms, and never on first paint or under reduced motion
    if (on && !reduceMotion) chart.update({ duration: 300, easing: 'easeOutQuart' });
    else chart.update('none');
  }

  // ===================================================================== hbar
  // spec: {names, segments: [{name, value, text}], total, totalText, empty: 'waiting for a request' | null}
  function hbar(canvas, opts) {
    const o = Object.assign({ thickness: 40, labelTop: 36 }, opts || {});
    const h = { canvas, spec: null, chart: null, o };
    const plugin = {
      id: 'studioBarLabels',
      afterDatasetsDraw(chart) {
        const spec = h.spec;
        if (!spec) return;
        const p = h.pal;
        const ctx = chart.ctx;
        const area = chart.chartArea;
        ctx.save();
        const mid = area.top + (area.bottom - area.top) / 2;
        const top = mid - h.o.thickness / 2;
        if (!spec.segments.length) {
          ctx.strokeStyle = p['--line'];
          ctx.lineWidth = 1;
          ctx.strokeRect(area.left + 0.5, top + 0.5, area.right - area.left - 1, h.o.thickness - 1);
          ctx.fillStyle = p['--ink-muted'];
          ctx.font = font(12, 400, p);
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText(spec.empty || '', area.left + 12, mid);
          ctx.restore();
          return;
        }
        const dom = spec.dominant;
        spec.segments.forEach((seg, i) => {
          const el = chart.getDatasetMeta(i).data[0];
          if (!el) return;
          const { x, base } = el.getProps(['x', 'base'], true);
          const left = Math.min(x, base);
          const width = Math.abs(x - base);
          if (width < 1) return;
          ctx.textBaseline = 'middle';
          if (i === dom && h.o.dominantLabel !== false) {
            const value = seg.text || `${fmt1(seg.value)} ms`;
            ctx.font = font(24, 600, p);
            const vw = ctx.measureText(value).width;
            ctx.font = font(12, 400, p);
            const nw = ctx.measureText(seg.name).width;
            const total = vw + 6 + nw;
            let x0 = left + width / 2 - total / 2;
            ctx.font = font(24, 600, p);
            const reserve = h.o.totalAboveNow && spec.totalText ? ctx.measureText(spec.totalText).width + 16 : 0;
            x0 = Math.max(area.left, Math.min(x0, chart.width - total - 2 - reserve));
            const y = top - 14;
            ctx.textAlign = 'left';
            ctx.fillStyle = p['--ink'];
            ctx.font = font(24, 600, p);
            ctx.fillText(value, x0, y);
            ctx.fillStyle = p['--ink-muted'];
            ctx.font = font(12, 400, p);
            ctx.fillText(seg.name, x0 + vw + 6, y + 3);
          } else if (width >= 56 && h.o.insideLabels !== false) {
            ctx.font = font(12, 400, p);
            ctx.fillStyle = p['--ink'];
            ctx.textAlign = 'center';
            const full = `${seg.short || seg.name} ${seg.small || fmt1(seg.value)}`;
            const label = ctx.measureText(full).width <= width - 8 ? full : (seg.small || fmt1(seg.value));
            if (ctx.measureText(label).width <= width - 6) ctx.fillText(label, left + width / 2, mid);
          }
        });
        if (spec.totalText) {
          ctx.fillStyle = p['--ink'];
          ctx.textBaseline = 'middle';
          if (h.o.totalAboveNow) {
            ctx.textAlign = 'right';
            ctx.font = font(h.o.totalSize || 24, 600, p);
            ctx.fillText(spec.totalText, area.right, top - 14);
          } else {
            ctx.textAlign = 'left';
            ctx.font = font(h.o.totalSize || 24, 600, p);
            ctx.fillText(spec.totalText, area.right + 10, mid);
          }
        }
        ctx.restore();
      },
    };
    h.chart = new Chart(canvas, {
      type: 'bar',
      data: { labels: [''], datasets: [] },
      options: {
        indexAxis: 'y',
        layout: { padding: { top: 0, right: 0 } },
        scales: {
          x: { stacked: true, display: false, min: 0 },
          y: { stacked: true, display: false },
        },
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => (items[0] ? items[0].dataset.label : ''),
              label: (item) => {
                const seg = h.spec && h.spec.segments[item.datasetIndex];
                return seg ? (seg.tip || `${fmt1(seg.value)} ms`) : '';
              },
            },
          },
        },
      },
      plugins: [plugin],
    });
    h.paint = function () { h.pal = pal(); h.apply(false); };
    h.apply = function (anim) {
      const spec = h.spec || { segments: [], total: 0 };
      const p = h.pal || (h.pal = pal());
      const segs = spec.segments;
      const total = segs.reduce((a, s) => a + (isNum(s.value) ? s.value : 0), 0) || 1;
      const totalAbove = h.o.totalAbove && h.o.totalAbove();
      h.o.totalAboveNow = totalAbove;
      const chart = h.chart;
      chart.options.layout.padding.right = !totalAbove && spec.totalText ? (h.o.rightPad || 118) : 0;
      chart.options.layout.padding.top = h.o.labelTop;
      chart.options.scales.x.max = total;
      chart.data.datasets = segs.map((seg, i) => {
        const base = p[KIND_TOKEN[seg.name]] || p['--k-other'];
        const dom = i === spec.dominant;
        const first = i === 0;
        const last = i === segs.length - 1;
        return {
          label: seg.name, data: [seg.value || 0], barThickness: h.o.thickness,
          backgroundColor: dom ? base : alpha(base, 0.55), borderColor: p['--surface'], borderWidth: 2,
          borderRadius: { topLeft: first ? 4 : 0, bottomLeft: first ? 4 : 0, topRight: last ? 4 : 0, bottomRight: last ? 4 : 0 },
          borderSkipped: false, stack: 's',
        };
      });
      canvas.dataset.segments = segs.map((s) => `${s.name}:${isNum(s.value) ? s.value.toFixed(2) : 0}`).join(',');
      canvas.dataset.dominant = spec.dominant != null && segs[spec.dominant] ? segs[spec.dominant].name : '';
      canvas.dataset.opacity = segs.map((s, i) => (i === spec.dominant ? '1' : '0.55')).join(',');
      if (spec.label) canvas.setAttribute('aria-label', spec.label);
      animate(chart, anim);
    };
    h.update = function (spec, anim) {
      const had = !!(h.spec && h.spec.segments.length);       // a new request landing animates; first paint never
      h.spec = spec;
      h.apply(!!anim && had);
    };
    h.pal = pal();
    return register(h);
  }

  // ===================================================================== ring
  // spec: {parts: [{name, value}], tick, centre, caption}
  function ring(canvas) {
    const h = { canvas, spec: null, chart: null };
    const plugin = {
      id: 'studioRing',
      afterDatasetsDraw(chart) {
        const spec = h.spec;
        if (!spec) return;
        const p = h.pal;
        const ctx = chart.ctx;
        const meta = chart.getDatasetMeta(0);
        const arc = meta.data[0];
        const cx = chart.chartArea.left + (chart.chartArea.right - chart.chartArea.left) / 2;
        const cy = chart.chartArea.top + (chart.chartArea.bottom - chart.chartArea.top) / 2;
        ctx.save();
        ctx.textAlign = 'center';
        ctx.fillStyle = p['--ink'];
        ctx.font = font(chart.width < 100 ? 20 : 24, 600, p);
        ctx.textBaseline = 'alphabetic';
        ctx.fillText(spec.centre || '–', cx, cy + 4);
        ctx.fillStyle = p['--ink-muted'];
        ctx.font = font(12, 400, p);
        ctx.fillText(spec.caption || '', cx, cy + 20);
        if (isNum(spec.tick) && arc) {
          const { innerRadius, outerRadius } = arc.getProps(['innerRadius', 'outerRadius'], true);
          const a = -Math.PI / 2 + spec.tick * Math.PI * 2;
          ctx.strokeStyle = p['--ink'];
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(cx + Math.cos(a) * (innerRadius - 3), cy + Math.sin(a) * (innerRadius - 3));
          ctx.lineTo(cx + Math.cos(a) * (outerRadius + 3), cy + Math.sin(a) * (outerRadius + 3));
          ctx.stroke();
        }
        ctx.restore();
      },
    };
    h.chart = new Chart(canvas, {
      type: 'doughnut',
      data: { labels: [], datasets: [{ data: [], borderWidth: 2 }] },
      options: {
        cutout: '82%', rotation: 0,
        plugins: { tooltip: { callbacks: { label: (item) => `${item.label} ${(item.raw * 100).toFixed(0)} %` } } },
      },
      plugins: [plugin],
    });
    h.apply = function (anim) {
      const spec = h.spec || { parts: [] };
      const p = h.pal || (h.pal = pal());
      const tok = { hit: '--k-card', PCIe: '--k-pcie', CPU: '--k-cpu' };
      const parts = spec.parts.length ? spec.parts : [{ name: 'none', value: 1, empty: true }];
      h.chart.data.labels = parts.map((q) => q.name);
      const ds = h.chart.data.datasets[0];
      ds.data = parts.map((q) => q.value);
      ds.backgroundColor = parts.map((q) => (q.empty ? p['--line'] : p[tok[q.name]] || p['--k-other']));
      ds.borderColor = p['--surface'];
      canvas.dataset.parts = spec.parts.map((q) => `${q.name}:${q.value.toFixed(3)}`).join(',');
      if (spec.label) canvas.setAttribute('aria-label', spec.label);
      animate(h.chart, anim);
    };
    h.paint = function () { h.pal = pal(); h.apply(false); };
    h.update = function (spec, anim) {
      const had = !!(h.spec && h.spec.parts.length);
      h.spec = spec;
      h.apply(!!anim && had);
    };
    h.pal = pal();
    return register(h);
  }

  // ===================================================================== spark
  // spec: {values: [number], band: [q1, q3] | null}
  function spark(canvas) {
    const h = { canvas, spec: null, chart: null };
    h.chart = new Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: {
        layout: { padding: { left: 4, right: 8, top: 6, bottom: 6 } },
        scales: { x: { display: false }, y: { display: false } },
        interaction: { mode: 'index', intersect: false },
        plugins: { tooltip: { callbacks: { title: () => '', label: (item) => (item.datasetIndex === 2 ? `${fmt1(item.raw)} tok/s` : '') } } },
      },
    });
    h.apply = function () {
      const spec = h.spec || { values: [] };
      const p = h.pal || (h.pal = pal());
      const vals = spec.values;
      const n = vals.length;
      h.chart.data.labels = vals.map((_, i) => i);
      const q = spec.band;
      const flat = (v) => vals.map(() => v);
      h.chart.data.datasets = [
        { data: q ? flat(q[0]) : [], borderWidth: 0, pointRadius: 0, fill: false, order: 3 },
        { data: q ? flat(q[1]) : [], borderWidth: 0, pointRadius: 0, backgroundColor: p['--noise'], fill: 0, order: 3 },
        {
          data: vals, borderColor: p['--ink-muted'], borderWidth: 2, tension: 0, order: 1, fill: false,
          pointRadius: (c) => (c.dataIndex === n - 1 ? 3 : 0), pointHoverRadius: 3,
          pointBackgroundColor: p['--ink'], pointBorderColor: p['--ink'],
        },
      ];
      canvas.dataset.points = String(n);
      canvas.dataset.band = q ? q.map((v) => v.toFixed(1)).join('-') : '';
      h.chart.update('none');
    };
    h.paint = h.apply;
    h.update = function (spec) { h.spec = spec; h.apply(); };
    h.pal = pal();
    return register(h);
  }

  // ===================================================================== multiples
  // three charts sharing one x; specs: {labels, tps: [..], hit: [..%], hitMedian, windows: [{card,..}|null]}
  function multiples(canvases, opts) {
    const o = Object.assign({}, opts || {});
    const h = { canvases, spec: null, charts: [], o };
    const sync = (index, from) => {
      for (const c of h.charts) {
        if (c === from) continue;
        const act = index == null ? [] : c.data.datasets.map((_, di) => ({ datasetIndex: di, index })).filter((e) => c.data.datasets[e.datasetIndex].data[index] != null).slice(0, 1);
        c.setActiveElements(act);
        if (c.tooltip) c.tooltip.setActiveElements(act, { x: 0, y: 0 });
        c.update('none');
      }
    };
    const axisFit = (scale) => { scale.width = 52; };
    const common = (showX) => ({
      layout: { padding: { top: 6, right: 8 } },
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { display: showX, grid: { display: false }, ticks: { maxTicksLimit: 6, color: '' }, border: { display: false } },
        y: { grid: { display: false }, ticks: { maxTicksLimit: 3 }, border: { display: false }, afterFit: axisFit },
      },
    });
    const onHover = (chartRef) => (evt, active, chart) => {
      sync(active.length ? active[0].index : null, chart);
    };
    // the name and unit of each small chart, 12 px muted, top-left inside the chart area
    const nameOf = (text) => ({
      id: 'studioName',
      afterDraw(chart) {
        const ctx = chart.ctx;
        ctx.save();
        ctx.font = font(12, 400, h.pal || pal());
        ctx.fillStyle = (h.pal || pal())['--ink-muted'];
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(text, chart.chartArea.left + 4, chart.chartArea.top + 2);
        ctx.restore();
      },
    });
    const mk = (canvas, type, showX, name) => {
      const options = common(showX);
      options.onHover = onHover();
      return new Chart(canvas, { type, data: { labels: [], datasets: [] }, options, plugins: [nameOf(name)] });
    };
    h.charts = [mk(canvases[0], 'line', false, 'tok/s'), mk(canvases[1], 'line', false, 'hit %'),
      mk(canvases[2], 'bar', true, 'ms / window')];
    h.charts[2].options.scales.x.stacked = true;
    h.charts[2].options.scales.y.stacked = true;
    h.charts[2].options.scales.y.beginAtZero = true;
    h.charts[0].options.scales.y.beginAtZero = true;
    h.charts[1].options.scales.y.suggestedMin = 0;
    h.charts[1].options.scales.y.suggestedMax = 100;
    h.apply = function () {
      const spec = h.spec || { labels: [], tps: [], hit: [], windows: [] };
      const p = h.pal || (h.pal = pal());
      const lines = (chart, label, data, unit) => {
        chart.data.labels = spec.labels;
        chart.data.datasets = [{
          label, data, borderColor: p['--ink'], borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, spanGaps: true,
          pointBackgroundColor: p['--ink'], tension: 0,
        }];
        chart.options.plugins.tooltip.callbacks = { label: (it) => `${label} ${fmt1(it.raw)} ${unit}` };
        for (const s of ['x', 'y']) {
          chart.options.scales[s].ticks.color = p['--ink-muted'];
        }
      };
      lines(h.charts[0], 'decode', spec.tps, 'tok/s');
      lines(h.charts[1], 'hit rate', spec.hit, '%');
      if (isNum(spec.hitMedian)) {
        h.charts[1].data.datasets.push({
          label: 'median', data: spec.labels.map(() => spec.hitMedian), borderColor: p['--ink-muted'], borderDash: [4, 4],
          borderWidth: 1, pointRadius: 0, pointHoverRadius: 0, order: 5,
        });
      }
      const bar = h.charts[2];
      bar.data.labels = spec.labels;
      bar.data.datasets = DECODE_NAMES.map((name) => ({
        label: name, stack: 's', data: spec.windows.map((w) => (w ? w[name] : null)),
        backgroundColor: p[KIND_TOKEN[name]], borderColor: p['--surface'], borderWidth: 1, barPercentage: 0.9, categoryPercentage: 1,
      }));
      bar.options.plugins.tooltip.callbacks = { label: (it) => `${it.dataset.label} ${fmt1(it.raw)} ms` };
      for (const s of ['x', 'y']) bar.options.scales[s].ticks.color = p['--ink-muted'];
      canvases[0].dataset.points = String(spec.tps.length);
      for (const c of h.charts) c.update('none');
    };
    h.paint = h.apply;
    h.update = function (spec) { h.spec = spec; h.apply(); };
    h.pal = pal();
    return register(h);
  }

  // ===================================================================== bullets
  // spec: {rows: [{label, a, b, floor (same unit as a), assumed, delta (text), state}], unit, max}
  function bullets(canvas, opts) {
    const o = Object.assign({ rowHeight: 32 }, opts || {});
    const h = { canvas, spec: null, chart: null, o };
    const geometry = (chart, i, a, f) => {
      const x = chart.scales.x;
      const y = chart.scales.y.getPixelForValue(i);
      const lo = x.getPixelForValue(Math.max(0, a - f));
      const hi = x.getPixelForValue(a + f);
      return { y, lo, hi: Math.max(hi, lo + 1), ax: x.getPixelForValue(a) };
    };
    const plugin = {
      id: 'studioBullets',
      beforeDatasetsDraw(chart) {
        const spec = h.spec;
        if (!spec) return;
        const p = h.pal;
        const ctx = chart.ctx;
        ctx.save();
        spec.rows.forEach((r, i) => {
          if (!isNum(r.a) || !isNum(r.floor)) return;
          const g = geometry(chart, i, r.a, r.floor);
          ctx.fillStyle = p['--noise'];
          ctx.fillRect(g.lo, g.y - 10, g.hi - g.lo, 20);
          if (r.assumed) {
            ctx.strokeStyle = p['--ink-muted'];
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.strokeRect(g.lo + 0.5, g.y - 9.5, Math.max(1, g.hi - g.lo - 1), 19);
          }
        });
        ctx.restore();
      },
      afterDatasetsDraw(chart) {
        const spec = h.spec;
        if (!spec) return;
        const p = h.pal;
        const ctx = chart.ctx;
        const area = chart.chartArea;
        ctx.save();
        spec.rows.forEach((r, i) => {
          const y = chart.scales.y.getPixelForValue(i);
          if (isNum(r.a) && spec.showTick !== false) {
            const ax = chart.scales.x.getPixelForValue(r.a);
            ctx.fillStyle = p['--ink'];
            ctx.fillRect(ax - 1, y - 10, 2, 20);
          }
          ctx.textBaseline = 'middle';
          ctx.textAlign = 'left';
          ctx.fillStyle = p['--ink'];
          ctx.font = font(14, 500, p);
          ctx.fillText(r.label, 0, y);
          ctx.textAlign = 'right';
          ctx.font = font(14, 600, p);
          ctx.fillStyle = r.state === 'better' ? p['--good-text'] : r.state === 'worse' ? p['--bad'] : p['--ink-muted'];
          ctx.fillText(r.delta || '', chart.width, y);
        });
        ctx.restore();
      },
    };
    h.chart = new Chart(canvas, {
      type: 'bar',
      data: { labels: [], datasets: [] },
      options: {
        indexAxis: 'y',
        layout: { padding: { left: 0, right: 0, top: 0, bottom: 0 } },
        scales: {
          y: { display: false, grid: { display: false } },
          x: { min: 0, grid: { display: false }, border: { display: false }, ticks: { count: 3 } },
        },
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => (items[0] ? h.spec.rows[items[0].dataIndex].label : ''),
              label: (item) => {
                const r = h.spec.rows[item.dataIndex];
                return r.tip || `${r.b}`;
              },
            },
          },
        },
      },
      plugins: [plugin],
    });
    h.apply = function () {
      const spec = h.spec || { rows: [] };
      const p = h.pal || (h.pal = pal());
      const c = h.chart;
      const small = narrow();
      c.options.layout.padding.left = small ? 64 : 120;
      c.options.layout.padding.right = small ? 72 : 120;
      c.options.scales.x.max = spec.max;
      c.options.scales.x.ticks.color = p['--ink-muted'];
      c.options.scales.x.afterFit = (s) => { s.height = 26; };
      c.data.labels = spec.rows.map((r) => r.label);
      c.data.datasets = [{
        data: spec.rows.map((r) => (isNum(r.b) ? r.b : null)), backgroundColor: alpha(p['--ink'], 0.85),
        barThickness: 8, borderWidth: 0,
      }];
      canvas.dataset.rows = JSON.stringify(spec.rows.map((r) => ({ label: r.label, a: r.a, b: r.b, band: isNum(r.floor) ? r.floor : null, state: r.state })));
      canvas.setAttribute('aria-label', spec.rows.map((r) => `${r.label} ${r.delta || ''}`).join('; '));
      c.update('none');
    };
    h.paint = h.apply;
    h.update = function (spec) { h.spec = spec; h.apply(); };
    h.pal = pal();
    return register(h);
  }

  // ===================================================================== where
  // spec: {kinds: [{name, a: {card:..}|null, b: {..}|null, aTotal, bTotal}], max}
  function where(canvas) {
    const h = { canvas, spec: null, chart: null };
    const rowsOf = (spec) => {
      const rows = [];
      spec.kinds.forEach((k, i) => {
        if (k.a) rows.push({ label: `${k.name} A`, w: k.a, total: k.aTotal, side: 'a', kind: i });
        if (k.b) rows.push({ label: `${k.name} B`, w: k.b, total: k.bTotal, side: 'b', kind: i });
        if (i < spec.kinds.length - 1) rows.push({ label: '', w: null, side: 'gap', kind: i });
      });
      return rows;
    };
    const plugin = {
      id: 'studioWhere',
      afterDatasetsDraw(chart) {
        if (!h.spec) return;
        const p = h.pal;
        const ctx = chart.ctx;
        ctx.save();
        ctx.textBaseline = 'middle';
        h.rows.forEach((r, i) => {
          if (r.side === 'gap') return;
          const y = chart.scales.y.getPixelForValue(i);
          ctx.textAlign = 'left';
          ctx.fillStyle = p['--ink'];
          ctx.font = font(14, 500, p);
          ctx.fillText(r.label, 0, y);
          ctx.textAlign = 'right';
          ctx.font = font(12, 400, p);
          ctx.fillStyle = p['--ink-muted'];
          ctx.fillText(isNum(r.total) ? `${fmt1(r.total)} ms` : '', chart.width, y);
        });
        ctx.restore();
      },
    };
    h.chart = new Chart(canvas, {
      type: 'bar',
      data: { labels: [], datasets: [] },
      options: {
        indexAxis: 'y',
        layout: { padding: { left: 96, right: 72 } },
        scales: { x: { stacked: true, display: false, min: 0 }, y: { stacked: true, display: false, grid: { display: false } } },
        plugins: {
          tooltip: {
            filter: (item) => isNum(item.raw),
            callbacks: { title: (items) => (items[0] ? h.rows[items[0].dataIndex].label : ''), label: (it) => `${it.dataset.label} ${fmt1(it.raw)} ms` },
          },
        },
      },
      plugins: [plugin],
    });
    h.apply = function () {
      const spec = h.spec || { kinds: [], max: 1 };
      const p = h.pal || (h.pal = pal());
      h.rows = rowsOf(spec);
      const c = h.chart;
      c.options.layout.padding.left = narrow() ? 76 : 112;
      c.options.scales.x.max = spec.max;
      c.data.labels = h.rows.map((r) => r.label);
      c.data.datasets = DECODE_NAMES.map((name) => {
        const base = p[KIND_TOKEN[name]];
        return {
          label: name, stack: 's', barThickness: 12,
          data: h.rows.map((r) => (r.w && isNum(r.w[name]) ? r.w[name] : null)),
          backgroundColor: h.rows.map((r) => (r.side === 'a' ? alpha(base, 0.55) : base)),
          borderColor: p['--surface'], borderWidth: 1, borderSkipped: false,
        };
      });
      canvas.dataset.kinds = spec.kinds.map((k) => k.name).join(',');
      c.update('none');
    };
    h.paint = h.apply;
    h.update = function (spec) { h.spec = spec; h.apply(); };
    h.pal = pal();
    return register(h);
  }

  // ===================================================================== candidates
  // spec: {labels, tags: [''|'default'|'was'], rounds: [[..]..], medians: [..], keptIndex, bestIndex}
  function candidates(canvas) {
    const h = { canvas, spec: null, chart: null };
    const plugin = {
      id: 'studioKept',
      afterDatasetsDraw(chart) {
        const spec = h.spec;
        if (!spec) return;
        const p = h.pal;
        const ctx = chart.ctx;
        const meta = chart.getDatasetMeta(chart.data.datasets.length - 1);
        ctx.save();
        const ring = (idx, dashed, label) => {
          const pt = meta.data[idx];
          if (!pt || !isNum(spec.medians[idx])) return;
          ctx.strokeStyle = p['--ink'];
          ctx.lineWidth = 2;
          ctx.setLineDash(dashed ? [3, 3] : []);
          ctx.beginPath();
          ctx.arc(pt.x, pt.y, 7, 0, Math.PI * 2);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = p['--ink'];
          ctx.font = font(12, 400, p);
          ctx.textAlign = 'center';
          ctx.textBaseline = 'alphabetic';
          ctx.fillText(label, pt.x, pt.y - 12);
        };
        if (spec.keptIndex != null && spec.keptIndex >= 0) ring(spec.keptIndex, false, 'kept');
        if (spec.bestIndex != null && spec.bestIndex >= 0 && spec.bestIndex !== spec.keptIndex && spec.keptIndex != null && spec.keptIndex >= 0) {
          ring(spec.bestIndex, true, 'best');
        }
        ctx.restore();
      },
    };
    h.chart = new Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: {
        layout: { padding: { top: 22, right: 12, left: 4, bottom: 0 } },
        showLine: false,
        scales: {
          x: { grid: { display: false }, ticks: { autoSkip: false, maxRotation: 0 }, border: { display: false } },
          y: { grid: { display: false }, ticks: { maxTicksLimit: 3 }, border: { display: false } },
        },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          tooltip: {
            callbacks: {
              title: (items) => (items[0] ? h.spec.tips[items[0].dataIndex] : ''),
              label: () => '',
              afterBody: (items) => (items[0] ? h.spec.tipLines[items[0].dataIndex] : []),
            },
          },
        },
      },
      plugins: [plugin],
    });
    h.apply = function () {
      const spec = h.spec || { labels: [], rounds: [], medians: [], tags: [], tips: [], tipLines: [] };
      const p = h.pal || (h.pal = pal());
      const c = h.chart;
      c.data.labels = spec.labels;
      const grey = alpha(p['--ink-muted'], 0.7);
      const sets = spec.rounds.map((r, k) => ({
        label: `round ${k + 1}`, data: r, showLine: false, pointRadius: 2, pointHoverRadius: 3, backgroundColor: grey, borderColor: grey,
      }));
      sets.push({
        label: 'median', data: spec.medians, showLine: false, pointRadius: 4, pointHoverRadius: 5,
        backgroundColor: p['--ink'], borderColor: p['--ink'],
      });
      c.data.datasets = sets;
      c.options.scales.x.ticks.color = p['--ink-muted'];
      c.options.scales.y.ticks.color = p['--ink-muted'];
      c.options.scales.x.ticks.callback = function (value) {
        const i = typeof value === 'number' ? value : Number(value);
        const label = spec.labels[i];
        const tag = spec.tags[i];
        return tag ? [label, `(${tag})`] : label;
      };
      canvas.dataset.kept = spec.keptIndex != null && spec.keptIndex >= 0 ? spec.labels[spec.keptIndex] : '';
      canvas.dataset.best = spec.bestIndex != null && spec.bestIndex >= 0 ? spec.labels[spec.bestIndex] : '';
      c.update('none');
    };
    h.paint = h.apply;
    h.update = function (spec) { h.spec = spec; h.apply(); };
    h.pal = pal();
    return register(h);
  }

  function repaintAll() {
    for (const h of handles) {
      try { h.pal = pal(); h.paint(); } catch (e) { /* a chart that is gone */ }
    }
  }

  function quartiles(values) {
    const v = values.filter(isNum).slice().sort((a, b) => a - b);
    if (v.length < 2) return null;
    const at = (q) => {
      const i = (v.length - 1) * q;
      const lo = Math.floor(i);
      return v[lo] + (v[Math.min(lo + 1, v.length - 1)] - v[lo]) * (i - lo);
    };
    return { q1: at(0.25), median: at(0.5), q3: at(0.75) };
  }

  window.StudioCharts = {
    DECODE_NAMES, PROMPT_NAMES, DECODE_FROM_ENGINE, PROMPT_FROM_ENGINE, KIND_TOKEN,
    hbar, ring, spark, multiples, bullets, where, candidates, repaintAll, quartiles,
    reduceMotion,
  };
})();
