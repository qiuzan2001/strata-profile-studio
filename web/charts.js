/* charts.js: the Studio's one palette and the chart factories (Chart.js 4.4.1, vendored).
 *
 * Everything the page draws goes through create(canvas, kind, options). A chart handle keeps its own data and
 * colour decisions; repaintAll() re-reads the CSS custom properties, so a theme change recolours every chart.
 * Kinds:
 *   line   one series, optional per-point colour tokens           setData({labels, values, tokens, tips})
 *   stack  stacked bars from {group: value} rows (null = empty)    setData({labels, groups, rows, tips})
 *   group  grouped bars, one dataset per series                    setData({labels, series: [{name, token, values}], tips})
 *   dots   dot chart: rounds in grey, median in blue, rings        setData({labels, rounds, medians, rings, tips})
 * `tips` is an array (one entry per label) of arrays of plain text lines; the first line is the tooltip title.
 */
(function () {
  'use strict';

  if (typeof Chart === 'undefined') {
    window.StudioCharts = null;
    return;
  }

  const DECODE_GROUPS = ['GPU', 'CPU experts', 'draft', 'adapt wait', 'other'];
  const PROMPT_GROUPS = ['experts over PCIe', 'dequant', 'expert GEMM', 'attention', 'GDN', 'other'];
  // A group keeps its colour in every chart (design: "Chart.js decisions").
  const GROUP_TOKEN = {
    'GPU': 'series1', 'CPU experts': 'series2', 'draft': 'series3', 'adapt wait': 'series4', 'other': 'other',
    'experts over PCIe': 'series1', 'dequant': 'series2', 'expert GEMM': 'series3', 'attention': 'series4',
    'GDN': 'series5',
  };
  const CSS_VAR = {
    series1: '--series-1', series2: '--series-2', series3: '--series-3', series4: '--series-4',
    series5: '--series-5', other: '--series-other', good: '--good', critical: '--critical',
    ink: '--ink', muted: '--ink-muted', line: '--line-soft', surface: '--surface',
    seq1: '--seq-1', seq2: '--seq-2', seq3: '--seq-3', seq4: '--seq-4',
  };
  // Prompt-size buckets for the Live decode chart: < 2K, 2K-16K, 16K-64K, >= 64K tokens.
  const BUCKETS = [
    { token: 'seq1', label: 'under 2K', below: 2000 },
    { token: 'seq2', label: '2K to 16K', below: 16000 },
    { token: 'seq3', label: '16K to 64K', below: 64000 },
    { token: 'seq4', label: '64K and more', below: Infinity },
  ];

  function bucketToken(tokens) {
    const n = Number(tokens);
    if (!isFinite(n)) return BUCKETS[0].token;
    return (BUCKETS.find((b) => n < b.below) || BUCKETS[3]).token;
  }

  function palette() {
    const cs = getComputedStyle(document.documentElement);
    const pal = {};
    for (const k of Object.keys(CSS_VAR)) pal[k] = cs.getPropertyValue(CSS_VAR[k]).trim() || '#888888';
    pal.font = getComputedStyle(document.body).fontFamily;
    return pal;
  }

  function withAlpha(color, a) {
    const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color);
    if (!m) return color;
    let hex = m[1];
    if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('');
    const n = parseInt(hex, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }

  const reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (reduceMotion) Chart.defaults.animation = false;
  Chart.defaults.font.size = 12;
  Chart.defaults.responsive = true;
  Chart.defaults.maintainAspectRatio = false;

  const handles = new Set();

  // ---- rings on the T1 dot charts (kept = solid, best-but-not-kept = dashed) --------------------------------
  const ringPlugin = {
    id: 'studioRings',
    afterDatasetsDraw(chart) {
      const rings = chart.$rings;
      const pal = chart.$pal;
      if (!rings || !rings.length || !pal) return;
      const meta = chart.getDatasetMeta(0);          // dataset 0 is always the median dots
      const ctx = chart.ctx;
      ctx.save();
      for (const ring of rings) {
        const pt = meta.data[ring.index];
        if (!pt || pt.skip) continue;
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, 11, 0, Math.PI * 2);
        ctx.lineWidth = 3;
        if (ring.kind === 'kept') {
          ctx.strokeStyle = pal.good;
          ctx.setLineDash([]);
        } else {
          ctx.strokeStyle = pal.ink;
          ctx.lineWidth = 2;
          ctx.setLineDash([4, 3]);
        }
        ctx.stroke();
        if (ring.kind === 'kept') {
          ctx.setLineDash([]);
          ctx.fillStyle = pal.ink;
          ctx.font = `600 11px ${pal.font}`;
          ctx.textAlign = 'center';
          ctx.fillText('kept', pt.x, pt.y - 16);
        }
      }
      ctx.restore();
    },
  };

  // ---- options --------------------------------------------------------------------------------------------
  function baseOptions(h) {
    const o = h.opts;
    const stacked = h.kind === 'stack';
    const y = {
      stacked,
      beginAtZero: h.kind !== 'dots',
      grace: h.kind === 'dots' ? '10%' : 0,
      title: { display: !!o.yTitle, text: o.yTitle || '' },
      ticks: {},
      grid: {},
      border: {},
    };
    if (o.percent) {
      y.max = 100;
      y.ticks.callback = (v) => `${v} %`;
    }
    if (o.yMax != null) y.max = o.yMax;
    const x = {
      stacked,
      title: { display: !!o.xTitle, text: o.xTitle || '' },
      grid: { display: false },
      border: {},
      ticks: {
        autoSkip: true,
        maxRotation: 0,
        callback(value, index) {
          const label = this.getLabelForValue(value);
          if (h.tickLines) return h.tickLines(String(label), index);
          return label;
        },
      },
    };
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: h.kind === 'line' ? 'index' : 'nearest', intersect: false },
      layout: { padding: h.kind === 'dots' ? { top: 22, right: 10 } : { right: 6 } },
      scales: { x, y },
      plugins: {
        legend: {
          display: h.kind !== 'line',
          position: 'bottom',
          labels: { usePointStyle: true, boxWidth: 8, filter: (item) => item.text !== '' },
        },
        tooltip: {
          displayColors: false,
          padding: 10,
          callbacks: {
            title(items) {
              const t = h.tips && h.tips[items[0].dataIndex];
              return t && t.length ? t[0] : items[0].label;
            },
            label(item) {
              const t = h.tips && h.tips[item.dataIndex];
              return t ? t.slice(1) : String(item.formattedValue);
            },
          },
        },
      },
    };
  }

  // ---- painting: every colour comes from the tokens -------------------------------------------------------
  function paint(h) {
    const pal = palette();
    const c = h.chart;
    c.$pal = pal;
    Chart.defaults.color = pal.muted;
    const o = c.options;
    for (const axis of Object.values(o.scales)) {
      axis.ticks.color = pal.muted;
      axis.grid.color = pal.line;
      axis.border.color = pal.line;
      axis.title.color = pal.muted;
    }
    o.plugins.legend.labels.color = pal.ink;
    const tt = o.plugins.tooltip;
    tt.backgroundColor = pal.surface;
    tt.titleColor = pal.ink;
    tt.bodyColor = pal.ink;
    tt.borderColor = pal.muted;
    tt.borderWidth = 1;
    tt.titleFont = { weight: '700' };

    for (const ds of c.data.datasets) {
      const st = ds.studio || {};
      if (h.kind === 'line') {
        ds.borderColor = pal[st.token];
        ds.backgroundColor = pal[st.token];
        ds.pointBackgroundColor = st.pointTokens ? st.pointTokens.map((t) => pal[t]) : pal[st.token];
        ds.pointBorderColor = pal.surface;
        ds.pointBorderWidth = 1;
      } else if (h.kind === 'dots') {
        ds.backgroundColor = pal[st.token];
        ds.borderColor = pal[st.token];
        ds.pointBackgroundColor = pal[st.token];
        ds.pointBorderColor = pal.surface;
        ds.pointBorderWidth = 1;
      } else {
        // The ghost dataset marks an empty slot with a faint stub, so the slot can still be hovered.
        ds.backgroundColor = st.ghost ? withAlpha(pal.muted, 0.22) : pal[st.token];
        ds.borderColor = pal.surface;
        ds.borderWidth = h.kind === 'stack' && !st.ghost ? 2 : 0;
      }
    }
  }

  // ---- datasets per kind ----------------------------------------------------------------------------------
  function buildLine(spec) {
    return [{
      label: spec.seriesLabel || '',
      data: spec.values,
      studio: { token: 'series1', pointTokens: spec.tokens || null },
      borderWidth: 2,
      pointRadius: 4,
      pointHoverRadius: 6,
      tension: 0,
      spanGaps: false,
    }];
  }

  function buildStack(spec, percent) {
    const sets = spec.groups.map((g) => ({
      label: g,
      data: spec.rows.map((r) => {
        if (!r || r[g] == null) return null;
        if (!percent) return r[g];
        const sum = spec.groups.reduce((a, k) => a + (Number(r[k]) || 0), 0);
        return sum > 0 ? (Number(r[g]) / sum) * 100 : null;
      }),
      studio: { token: GROUP_TOKEN[g] || 'other' },
      stack: 's',
    }));
    const total = spec.rows.map((r) => (r ? spec.groups.reduce((a, k) => a + (Number(r[k]) || 0), 0) : 0));
    const top = percent ? 100 : Math.max(0, ...total);
    const empty = spec.rows.map((r, i) => (total[i] > 0 ? null : (percent ? 100 : top * 0.04 || 1)));
    if (empty.some((v) => v != null)) {
      sets.push({ label: '', data: empty, studio: { ghost: true }, stack: 's' });
    }
    return sets;
  }

  function buildGroup(spec) {
    return spec.series.map((s) => ({
      label: s.name,
      data: s.values,
      studio: { token: s.token },
      borderRadius: 3,
    }));
  }

  function buildDots(spec) {
    const sets = [{
      label: 'median', data: spec.medians, order: 0, showLine: false, pointRadius: 6, pointHoverRadius: 8,
      studio: { token: 'series1' },
    }];
    const maxRounds = Math.max(0, ...spec.rounds.map((r) => r.length));
    for (let r = 0; r < maxRounds; r++) {
      sets.push({
        label: r === 0 ? 'each round' : '',
        data: spec.rounds.map((v) => (v[r] == null ? null : v[r])),
        order: 1, showLine: false, pointRadius: 3, pointHoverRadius: 5,
        studio: { token: 'muted' },
      });
    }
    return sets;
  }

  function tableOf(h, labels, datasets) {
    const named = datasets.filter((d) => d.label !== '' && !(d.studio && d.studio.ghost));
    return {
      head: [''].concat(named.map((d) => d.label || 'value')),
      rows: labels.map((l, i) => [String(l)].concat(named.map((d) => d.data[i]))),
    };
  }

  // ---- public ---------------------------------------------------------------------------------------------
  function create(canvas, kind, opts) {
    const h = { kind, canvas, opts: opts || {}, chart: null, tips: null, table: null, tickLines: null };
    h.tickLines = h.opts.tickLines || null;
    h.chart = new Chart(canvas, {
      type: kind === 'line' || kind === 'dots' ? 'line' : 'bar',
      data: { labels: [], datasets: [] },
      options: baseOptions(h),
      plugins: kind === 'dots' ? [ringPlugin] : [],
    });
    h.setData = function (spec, animate) {
      const labels = spec.labels || [];
      h.tips = spec.tips || null;
      h.chart.data.labels = labels;
      if (kind === 'line') h.chart.data.datasets = buildLine(spec);
      else if (kind === 'stack') h.chart.data.datasets = buildStack(spec, !!h.opts.percent);
      else if (kind === 'group') h.chart.data.datasets = buildGroup(spec);
      else h.chart.data.datasets = buildDots(spec);
      h.chart.$rings = spec.rings || null;
      h.table = tableOf(h, labels, h.chart.data.datasets);
      paint(h);
      h.chart.update(animate ? undefined : 'none');
    };
    h.repaint = function () {
      paint(h);
      h.chart.update('none');
    };
    h.destroy = function () {
      handles.delete(h);
      h.chart.destroy();
    };
    handles.add(h);
    paint(h);
    return h;
  }

  function repaintAll() {
    for (const h of Array.from(handles)) {
      if (!h.canvas.isConnected) {
        h.destroy();
        continue;
      }
      h.repaint();
    }
  }

  window.StudioCharts = {
    create, repaintAll, palette, bucketToken,
    DECODE_GROUPS, PROMPT_GROUPS, GROUP_TOKEN, BUCKETS,
  };
})();
