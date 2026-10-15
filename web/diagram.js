/* Strata Profile Studio: the PC module.  Disk, RAM, CPU, PCIe, VRAM and GPU in one row with the links between them,
   the experts panel (where the experts come from, with the live flow) and the limit summary.

   Built once (S.pc.build).  S.pc.update runs once per second with GET /api/hardware (Strata's /metrics),
   GET /api/host (this PC: per-core CPU, nvidia-smi) and the newest request.  An update sets a text node's value, an
   attribute or one style property; nothing is added or removed.  The expert flow moves in one requestAnimationFrame
   loop that changes the dash offset of three paths. */
'use strict';

(function () {
  const { el, svg, isNum } = S;
  const R = {};                                   // every node the update touches
  const rings = {};                               // series the server keeps no history of: the last 60 readings
  const ring = (k, v) => { const a = rings[k] = rings[k] || []; a.push(isNum(v) ? v : null); if (a.length > 60) a.shift(); return a; };

  // ---------------------------------------------------------------------------------------------- tweening
  const tweens = new Map();
  let raf = 0;
  function frame(now) {
    raf = 0;
    for (const [node, t] of tweens) {
      const k = Math.min(1, (now - t.t0) / 300);
      const v = t.from + (t.to - t.from) * (1 - (1 - k) * (1 - k));
      S.setText(node, t.fmt(v));
      if (k >= 1) tweens.delete(node);
    }
    if (tweens.size) raf = requestAnimationFrame(frame);
  }
  function num(node, value, fmt) {
    if (!isNum(value)) { tweens.delete(node); node._v = null; S.setText(node, '–'); return; }
    const from = isNum(node._v) ? node._v : value;
    node._v = value;
    if (from === value || document.hidden) { tweens.delete(node); S.setText(node, fmt(value)); return; }
    tweens.set(node, { from, to: value, t0: performance.now(), fmt });
    if (!raf) raf = requestAnimationFrame(frame);
  }
  const scale = (node, f, axis) => S.setStyle(node, 'transform', `${axis === 'y' ? 'scaleY' : 'scaleX'}(${(isNum(f) ? Math.max(0, Math.min(1, f)) : 0).toFixed(4)})`);

  // ---------------------------------------------------------------------------------------------- builders
  function spark(label, unit) {
    const r = { s: S.sparkline(), v: el('span', { class: 'sp-val num', text: '–' }) };
    r.node = el('div', { class: 'sp' }, el('span', { class: 'sp-label', text: label }), r.v, r.s);
    r.unit = unit;
    return r;
  }
  function fact(label) {
    const r = { v: el('span', { class: 'fact-val num', text: '–' }) };
    r.node = el('div', { class: 'fact' }, el('span', { class: 'fact-label', text: label }), r.v);
    return r;
  }
  function meter(segments) {
    const fills = segments.map((c) => el('i', { class: `meter-seg ${c}` }));
    return { node: el('div', { class: 'meter' }, fills), fills };
  }
  function block(key, name, o) {
    const r = R[key] = { facts: {}, sparks: {} };
    r.name = el('span', { class: 'blk-name', text: name });
    r.badge = el('span', { class: 'blk-badge', text: 'limit' });
    r.big = el('span', { class: 'blk-big num', text: '–' });
    r.unit = el('span', { class: 'blk-unit', text: o.unit || '' });
    r.meter = meter(o.meter || ['m-main']);
    const facts = el('div', { class: 'facts-mini' }, (o.facts || []).map(([k, label]) => (r.facts[k] = fact(label)).node));
    const sparks = el('div', { class: 'sparks' }, (o.sparks || []).map(([k, label, unit]) => (r.sparks[k] = spark(label, unit)).node));
    r.node = el('div', { class: `blk blk-${key}`, dataset: { block: key } },
      el('div', { class: 'blk-head' }, r.name, r.badge),
      el('div', { class: 'blk-value' }, r.big, r.unit),
      o.noMeter ? null : r.meter.node, o.extra || null, facts, sparks);
    return r.node;
  }
  function link(key, label, o) {
    const r = R['l-' + key] = { sparks: {} };
    r.label = el('span', { class: 'lnk-label', text: label });
    r.value = el('span', { class: 'lnk-value num', text: '–' });
    r.ceil = el('span', { class: 'lnk-ceil', text: '' });
    r.fill = el('i', { class: 'pipe-fill' });
    r.pipe = el('div', { class: 'pipe' }, r.fill, el('i', { class: 'pipe-flow' }));
    const sparks = el('div', { class: 'sparks' }, ((o && o.sparks) || []).map(([k, l, u]) => (r.sparks[k] = spark(l, u)).node));
    r.node = el('div', { class: `lnk lnk-${key}` }, r.label, r.pipe, r.value, r.ceil, sparks);
    return r.node;
  }
  function coreGrid() {
    const g = R.cores = { cells: [], node: el('div', { class: 'cores' }) };
    for (let i = 0; i < 32; i++) {
      const fill = el('i', { class: 'core-fill' }), mhz = el('span', { class: 'core-mhz num', text: '' }), id = el('span', { class: 'core-id', text: String(i) });
      const c = el('div', { class: 'core', hidden: true, dataset: { cpu: i } }, fill, id, mhz);
      g.cells.push({ c, fill, mhz });
      g.node.appendChild(c);
    }
    g.node.addEventListener('mousemove', (e) => {
      const c = e.target.closest('.core');
      const d = c && c._d;
      if (d) S.tipShow(e, [`CPU ${d.id}${d.role ? ' · ' + d.role : ''}`, ['busy', isNum(d.busy) ? `${S.num(d.busy, 0)} %` : '–'], ['frequency', isNum(d.mhz) ? `${S.int(d.mhz)} MHz` : '–']]);
    });
    g.node.addEventListener('mouseleave', S.tipHide);
    return g.node;
  }
  function gpuRows() {
    const rows = R.gpuRows = {};
    const box = el('div', { class: 'gpu-rows' });
    for (const [k, label] of [['sm', 'SM clock'], ['mem', 'mem. clock'], ['mc', 'mem. controller'], ['pw', 'power'], ['temp', 'temperature'], ['fan', 'fan'], ['ps', 'P-state'], ['link', 'PCIe link'], ['why', 'clock limits']]) {
      const bar = el('i', { class: 'gr-fill' });
      const v = el('span', { class: 'gr-val num', text: '–' });
      rows[k] = { bar, v };
      box.appendChild(el('div', { class: 'gr' }, el('span', { class: 'gr-label', text: label }), el('span', { class: 'gr-bar' }, bar), v));
    }
    return box;
  }

  // the experts panel: the flow (SVG, fixed paths) and the figures
  function experts() {
    const X = R.exp = { paths: {}, labels: {}, facts: {} };
    const s = svg('svg', { class: 'flow', viewBox: '0 0 800 256', role: 'img', 'aria-label': 'Expert flow: RAM, CPU pool, PCIe, VRAM expert cache, GPU' });
    const node = (x, y, w, h, title, cls) => {
      const st = svg('text', { class: 'fn-sub num', x: x + 10, y: y + 38, text: '' });
      s.append(svg('rect', { class: `fn ${cls}`, x, y, width: w, height: h, rx: 8 }), svg('text', { class: 'fn-title', x: x + 10, y: y + 21, text: title }), st);
      return st;
    };
    const P = {
      computed: { d: 'M160 88 C240 88 250 46 330 46', cls: 'fp-cpu' },
      copied: { d: 'M160 112 C240 112 250 172 330 172 L480 172 C600 172 640 132 690 126', cls: 'fp-pcie' },
      swapped: { d: 'M160 124 C240 124 250 188 330 188 L480 188 C520 188 520 144 560 138', cls: 'fp-swap' },
      hit: { d: 'M650 108 L690 108', cls: 'fp-hit' },
    };
    for (const [k, p] of Object.entries(P)) {
      const base = svg('path', { class: `fp ${p.cls}`, d: p.d });
      const pulse = svg('path', { class: `fp-pulse ${p.cls}`, d: p.d });
      X.paths[k] = { base, pulse, speed: 0, off: 0 };
      s.append(base, pulse);
    }
    X.ramSub = node(10, 72, 150, 60, 'RAM', 'fn-ram');
    X.cpuSub = node(330, 20, 150, 52, 'CPU pool', 'fn-cpu');
    X.pcieSub = node(330, 154, 150, 52, 'PCIe', 'fn-pcie');
    X.vramSub = node(520, 80, 130, 58, 'VRAM cache', 'fn-vram');
    X.gpuSub = node(690, 80, 104, 58, 'GPU', 'fn-gpu');
    for (const [k, x, y, anchor] of [['computed', 490, 40, 'start'], ['copied', 405, 226, 'middle'], ['swapped', 405, 246, 'middle'], ['hit', 585, 66, 'middle']]) {
      X.labels[k] = svg('text', { class: `fl-label num fl-${k}`, x, y, 'text-anchor': anchor, text: '' });
      s.appendChild(X.labels[k]);
    }
    const share = meter(['k-gpu', 'k-pcie', 'k-cpu']);
    X.share = share;
    X.hitSpark = spark('hit rate per request', '%');
    const facts = el('div', { class: 'facts-mini cols' }, [
      ['slots', 'cache slots'], ['cache', 'cache size'], ['arena', 'experts in RAM'], ['cpuw', 'CPU experts per layer-window'],
      ['vramw', 'cache hits per layer-window'], ['pciew', 'PCIe reads per layer-window'], ['swaps', 'swaps per window'],
      ['adapt', 'adapt wait per window'], ['kv', 'KV reads from VRAM'], ['learned', 'learned profile']].map(([k, l]) => (X.facts[k] = fact(l)).node));
    X.node = el('div', { class: 'experts-panel' },
      el('div', { class: 'exp-head' }, el('b', { text: 'Experts' }), X.state = el('span', { class: 'muted small', text: '' })),
      el('div', { class: 'exp-body' }, el('div', { class: 'exp-flow' }, s),
        el('div', { class: 'exp-stats' }, el('div', { class: 'exp-share-label' }, el('span', { text: 'routed experts: from VRAM / over PCIe / by the CPU' }), X.shareText = el('span', { class: 'num', text: '' })), share.node, X.hitSpark.node, facts)));
    return X.node;
  }
  let flowRaf = 0, flowT = 0;
  function flowFrame(t) {
    flowRaf = 0;
    const dt = flowT ? Math.min(0.1, (t - flowT) / 1000) : 0;
    flowT = t;
    let moving = false;
    for (const p of Object.values(R.exp.paths)) {
      if (p.speed > 0) {
        moving = true;
        p.off = (p.off - p.speed * dt) % 1000;
        S.setAttr(p.pulse, 'stroke-dashoffset', p.off.toFixed(1));
      }
    }
    if (moving && !document.hidden) flowRaf = requestAnimationFrame(flowFrame); else flowT = 0;
  }

  S.pc = {
    build() {
      const sum = R.sum = {};
      sum.state = el('span', { class: 'sum-state', text: 'waiting for Strata' });
      sum.tps = el('span', { class: 'sum-big num', text: '–' });
      sum.limit = el('div', { class: 'sum-limit', text: '' });
      sum.why = el('div', { class: 'sum-why', text: '' });
      sum.bar = el('div', { class: 'sum-bar' });
      sum.parts = {};
      for (const k of S.DECODE_PARTS) {
        const p = el('i', { class: `sum-part ${S.PART_CLASS[k]}` });
        S.hover(p, () => [S.PART_LABEL[k], ['ms per window', isNum(p._ms) ? S.num(p._ms, 1) : '–'], S.PART_ABOUT[k]]);
        sum.parts[k] = p;
        sum.bar.appendChild(p);
      }
      sum.node = el('div', { class: 'pc-sum' },
        el('div', { class: 'sum-a' }, sum.state, el('div', { class: 'blk-value' }, sum.tps, el('span', { class: 'sum-unit', text: 'tok/s' }))),
        el('div', { class: 'sum-b' }, sum.limit, sum.why),
        el('div', { class: 'sum-c' }, sum.bar, el('div', { class: 'sum-legend' }, S.DECODE_PARTS.map((k) =>
          el('span', { class: 'lg' }, el('i', { class: `sw ${S.PART_CLASS[k]}` }), `${S.PART_LABEL[k]}: ${{ 'GPU': 'GPU', 'CPU experts': 'CPU and RAM', 'draft': 'GPU', 'adapt wait': 'PCIe', 'other': 'none' }[k]}`)))));
      const row = el('div', { class: 'pc' },
        block('disk', 'Disk', { unit: 'MB/s read', noMeter: true, facts: [['write', 'write']], sparks: [['read', 'read', 'MB/s'], ['write', 'write', 'MB/s']] }),
        link('disk', ''),
        block('ram', 'RAM', { unit: 'GiB used', facts: [['total', 'installed'], ['avail', 'available'], ['arena', 'experts held']], sparks: [['used', 'used', 'GiB'], ['avail', 'available', 'GiB']] }),
        link('bus', 'memory bus'),
        block('cpu', 'CPU', { unit: '% busy', extra: coreGrid(), facts: [['temp', 'package temperature'], ['power', 'package power'], ['pool', 'pool workers']], sparks: [['busy', 'busy', '%'], ['temp', 'temperature', '°C'], ['power', 'package power', 'W']] }),
        link('pcie', 'PCIe', { sparks: [['rx', 'to the GPU', 'MB/s'], ['tx', 'from the GPU', 'MB/s']] }),
        block('vram', 'VRAM', { unit: 'GiB used', meter: ['k-gemm', 'm-other', 'm-free'], facts: [['cache', 'expert cache'], ['free', 'free'], ['total', 'installed']], sparks: [['used', 'used', 'GiB'], ['free', 'free', 'MiB']] }),
        link('card', ''),
        block('gpu', 'GPU', { unit: '% busy', extra: gpuRows(), sparks: [['busy', 'busy', '%'], ['power', 'power', 'W'], ['temp', 'temperature', '°C'], ['sm', 'SM clock', 'MHz']] }));
      return el('div', { class: 'pc-module' }, row, experts(), sum.node);
    },

    // hw: /api/hardware, host: /api/host, ctx: {workers, hits: [hit rate % of recent requests], experts: /api/experts}
    update(hw, host, ctx) {
      if (!R.ram) return;
      hw = hw || {}; host = host || {}; ctx = ctx || {};
      const now = hw.now || {}, st = hw.static || {}, eng = hw.engine || {}, hist = hw.history || {};
      const probe = (hw.log || {}).probe, live = hw.live || {}, win = hw.window || null;
      const cpu = host.cpu || null, gpu = host.gpu || null, hh = host.history || {};
      const fresh = isNum(hw.at) && Date.now() / 1000 - hw.at < 15;
      const generating = live.state === 'generating';
      const setSpark = (r, vals, max, fmt) => { r.s.update(vals, max); const last = (vals || []).filter(isNum).slice(-1)[0]; S.setText(r.v, isNum(last) ? `${(fmt || ((v) => S.num(v, 0)))(last)} ${r.unit}` : '–'); };
      // ---- disk
      num(R.disk.big, now.disk_read_mb, (v) => v.toFixed(v < 10 ? 1 : 0));
      S.setText(R.disk.facts.write.v, isNum(now.disk_write_mb) ? `${S.num(now.disk_write_mb, 1)} MB/s` : 'no counters');
      setSpark(R.disk.sparks.read, hist.disk_read_mb, null, (v) => S.num(v, 1));
      setSpark(R.disk.sparks.write, ring('disk_w', now.disk_write_mb), null, (v) => S.num(v, 1));
      S.setText(R['l-disk'].value, '');
      // ---- RAM
      const ramU = now.ram_used, ramT = now.ram_total;
      num(R.ram.big, isNum(ramU) ? ramU / 2 ** 30 : null, (v) => v.toFixed(1));
      scale(R.ram.meter.fills[0], isNum(ramU) && ramT ? ramU / ramT : null);
      S.setText(R.ram.facts.total.v, isNum(ramT) ? `${S.num(ramT / 2 ** 30, 1)} GiB` : '–');
      S.setText(R.ram.facts.avail.v, isNum(ramT) && isNum(ramU) ? `${S.num((ramT - ramU) / 2 ** 30, 1)} GiB` : '–');
      S.setText(R.ram.facts.arena.v, isNum(eng.arena_mib) ? `${S.num(eng.arena_mib / 1024, 1)} GiB` : '–');
      setSpark(R.ram.sparks.used, (hist.ram_used || []).map((v) => (isNum(v) ? v / 2 ** 30 : null)), ramT ? ramT / 2 ** 30 : null, (v) => S.num(v, 1));
      setSpark(R.ram.sparks.avail, ring('ram_a', isNum(ramT) && isNum(ramU) ? (ramT - ramU) / 2 ** 30 : null), ramT ? ramT / 2 ** 30 : null, (v) => S.num(v, 1));
      // ---- CPU
      S.setAttr(R.cpu.node, 'title', st.cpu_name || '');
      const busy = cpu && isNum(cpu.busy) ? cpu.busy : now.cpu;
      num(R.cpu.big, busy, (v) => v.toFixed(0));
      scale(R.cpu.meter.fills[0], isNum(busy) ? busy / 100 : null);
      const pool = new Set(host.pool_cpus || []);
      const cores = (cpu && cpu.cores) || [];
      R.cores.cells.forEach((cell, i) => {
        const d = cores[i];
        S.show(cell.c, !!d);
        if (!d) return;
        const role = pool.has(d.id) ? 'pool worker' : d.id === host.host_cpu ? 'host thread' : '';
        cell.c._d = Object.assign({ role }, d);
        S.setAttr(cell.c, 'class', `core ${pool.has(d.id) ? 'pool' : ''} ${d.id === host.host_cpu ? 'host' : ''}`);
        scale(cell.fill, isNum(d.busy) ? d.busy / 100 : 0, 'y');
        S.setText(cell.mhz, isNum(d.mhz) ? (d.mhz / 1000).toFixed(1) : '');
      });
      S.setText(R.cpu.facts.temp.v, cpu && isNum(cpu.temp_c) ? `${S.num(cpu.temp_c, 1)} °C` : 'not readable');
      S.setText(R.cpu.facts.power.v, cpu && isNum(cpu.power_w) ? `${S.num(cpu.power_w, 1)} W` : 'not readable');
      S.setAttr(R.cpu.facts.power.node, 'title', cpu && cpu.power_hint ? cpu.power_hint : '');
      const pc = host.pool_cpus || [];
      S.setText(R.cpu.facts.pool.v, pc.length ? `${pc.length} on CPU ${pc[0]}-${pc[pc.length - 1]}` : (isNum(eng.pool_workers) ? `${eng.pool_workers}` : '–'));
      S.setAttr(R.cpu.facts.pool.node, 'title', pc.length ? `pool workers on CPUs ${pc.join(', ')}, the host thread on CPU ${host.host_cpu} (engine log)` : '');
      setSpark(R.cpu.sparks.busy, (hh.cpu || {}).busy || hist.cpu, 100);
      setSpark(R.cpu.sparks.temp, (hh.cpu || {}).temp_c, null, (v) => S.num(v, 1));
      setSpark(R.cpu.sparks.power, (hh.cpu || {}).power_w, null, (v) => S.num(v, 1));
      if (cpu && !isNum(cpu.power_w) && cpu.power_hint) S.setText(R.cpu.sparks.power.v, 'not readable');
      // ---- the memory bus: the one estimate (CPU-expert share of the window x the pool's measured expert rate)
      const cpuShare = win && win.window_ms && isNum(win.ms_per_window) && win.ms_per_window > 0 ? (win.window_ms['CPU experts'] || 0) / win.ms_per_window : null;
      const busEst = probe && isNum(cpuShare) ? cpuShare * probe.cpu_gbs : null;
      S.setText(R['l-bus'].value, generating && isNum(busEst) ? `≈ ${S.num(busEst, 1)} GB/s` : (isNum(busEst) ? '≈ 0 GB/s' : '–'));
      S.setText(R['l-bus'].ceil, probe ? `of ${S.num(probe.cpu_gbs, 1)} GB/s measured` : 'no probe line in the log');
      S.setAttr(R['l-bus'].node, 'title', probe ? `Estimate: the CPU-expert share of the last decode window x ${S.num(probe.cpu_gbs, 1)} GB/s (the CPU pool's expert rate from the engine's PCIe probe). Last decode: ≈ ${S.num(busEst, 1)} GB/s.` : '');
      scale(R['l-bus'].fill, generating && probe && isNum(busEst) ? busEst / probe.cpu_gbs : 0, 'y');
      S.setClass(R['l-bus'].node, 'flowing', generating);
      // ---- PCIe
      const gen = isNum(now.gpu_pcie_gen) ? now.gpu_pcie_gen : gpu && gpu.pcie_gen;
      const gmax = isNum(now.gpu_pcie_gen_max) ? now.gpu_pcie_gen_max : gpu && gpu.pcie_gen_max;
      const width = isNum(now.gpu_pcie_width) ? now.gpu_pcie_width : gpu && gpu.pcie_width;
      S.setText(R['l-pcie'].label, isNum(gen) ? `PCIe Gen ${gen} of ${gmax} · x${width}` : 'PCIe');
      const rx = now.gpu_pcie_rx_mb, tx = now.gpu_pcie_tx_mb;
      const nominal = isNum(gmax) && isNum(width) ? ({ 1: 0.25, 2: 0.5, 3: 0.985, 4: 1.969, 5: 3.938, 6: 7.563 }[gmax] || 0) * width : null;
      const ceilGB = probe ? probe.link_gbs : nominal;
      S.setText(R['l-pcie'].value, isNum(rx) ? S.mbps(rx + (tx || 0)) : '–');
      S.setText(R['l-pcie'].ceil, ceilGB ? `of ${S.num(ceilGB, 1)} GB/s ${probe ? 'measured' : 'nominal'}` : '');
      scale(R['l-pcie'].fill, ceilGB && isNum(rx) ? (rx + (tx || 0)) / (ceilGB * 1000) : 0, 'y');
      S.setClass(R['l-pcie'].node, 'flowing', isNum(rx) && rx > 200);
      setSpark(R['l-pcie'].sparks.rx, hist.gpu_pcie_rx_mb, null);
      setSpark(R['l-pcie'].sparks.tx, ring('pcie_tx', tx), null);
      // ---- VRAM
      const vu = isNum(now.gpu_mem_used) ? now.gpu_mem_used : gpu && isNum(gpu.mem_used_mib) ? gpu.mem_used_mib * 2 ** 20 : null;
      const vt = isNum(now.gpu_mem_total) ? now.gpu_mem_total : gpu && isNum(gpu.mem_total_mib) ? gpu.mem_total_mib * 2 ** 20 : null;
      num(R.vram.big, isNum(vu) ? vu / 2 ** 30 : null, (v) => v.toFixed(1));
      const cacheB = isNum(eng.expert_cache_mib) ? eng.expert_cache_mib * 2 ** 20 : 0;
      const segs = R.vram.meter.fills;
      if (vt) {
        S.setStyle(segs[0], 'width', `${((Math.min(cacheB, vu || 0) / vt) * 100).toFixed(2)}%`);
        S.setStyle(segs[1], 'width', `${(Math.max(0, (vu || 0) - cacheB) / vt * 100).toFixed(2)}%`);
        S.setStyle(segs[2], 'width', `${(Math.max(0, vt - (vu || 0)) / vt * 100).toFixed(2)}%`);
      }
      S.setText(R.vram.facts.cache.v, isNum(eng.expert_cache_mib) ? `${S.num(eng.expert_cache_mib / 1024, 2)} GiB` : '–');
      const lowVram = isNum(eng.vram_free_mib) && eng.vram_free_mib < 512;
      S.setText(R.vram.facts.free.v, isNum(eng.vram_free_mib) ? `${S.int(eng.vram_free_mib)} MiB${lowVram ? ' (under 512 MiB)' : ''}` : '–');
      S.setClass(R.vram.facts.free.node, 'warn', lowVram);
      S.setText(R.vram.facts.total.v, isNum(vt) ? `${S.num(vt / 2 ** 30, 1)} GiB` : '–');
      setSpark(R.vram.sparks.used, (hist.gpu_mem_used || []).map((v) => (isNum(v) ? v / 2 ** 30 : null)), vt ? vt / 2 ** 30 : null, (v) => S.num(v, 1));
      setSpark(R.vram.sparks.free, ring('vram_free', isNum(vt) && isNum(vu) ? (vt - vu) / 2 ** 20 : null), null, (v) => S.int(v));
      // ---- GPU
      S.setAttr(R.gpu.node, 'title', st.gpu_name || '');
      const gutil = isNum(now.gpu_util) ? now.gpu_util : gpu && gpu.util;
      num(R.gpu.big, gutil, (v) => v.toFixed(0));
      scale(R.gpu.meter.fills[0], isNum(gutil) ? gutil / 100 : null);
      const gr = R.gpuRows;
      const row = (k, frac, text) => { scale(gr[k].bar, frac); S.setText(gr[k].v, text); };
      const g = gpu || {};
      row('sm', isNum(g.sm_mhz) && g.sm_max_mhz ? g.sm_mhz / g.sm_max_mhz : null, isNum(g.sm_mhz) ? `${S.int(g.sm_mhz)} of ${S.int(g.sm_max_mhz)} MHz` : '–');
      row('mem', isNum(g.mem_mhz) && g.mem_max_mhz ? g.mem_mhz / g.mem_max_mhz : null, isNum(g.mem_mhz) ? `${S.int(g.mem_mhz)} of ${S.int(g.mem_max_mhz)} MHz` : '–');
      row('mc', isNum(g.mem_util) ? g.mem_util / 100 : null, isNum(g.mem_util) ? `${S.num(g.mem_util, 0)} %` : '–');
      const pw = isNum(g.power_w) ? g.power_w : now.gpu_power, pl = isNum(g.power_limit_w) ? g.power_limit_w : now.gpu_power_limit;
      row('pw', isNum(pw) && pl ? pw / pl : null, isNum(pw) ? `${S.num(pw, 0)} of ${S.num(pl, 0)} W` : '–');
      const tc = isNum(g.temp_c) ? g.temp_c : now.gpu_temp;
      row('temp', isNum(tc) ? tc / 100 : null, isNum(tc) ? `${S.num(tc, 0)} °C` : '–');
      row('fan', isNum(g.fan_pct) ? g.fan_pct / 100 : null, isNum(g.fan_pct) ? `${S.num(g.fan_pct, 0)} %` : '–');
      row('ps', null, g.pstate || '–');
      row('link', isNum(gen) && gmax ? gen / gmax : null, isNum(gen) ? `Gen ${gen} of ${gmax} · x${width}` : '–');
      row('why', null, g.reason_words ? (g.reason_words.length ? g.reason_words.join(', ') : 'none') : (host.gpu_error ? 'no nvidia-smi' : '–'));
      setSpark(R.gpu.sparks.busy, hist.gpu_util || (hh.gpu || {}).util, 100);
      setSpark(R.gpu.sparks.power, hist.gpu_power || (hh.gpu || {}).power_w, pl || null);
      setSpark(R.gpu.sparks.temp, hist.gpu_temp || (hh.gpu || {}).temp_c, null);
      setSpark(R.gpu.sparks.sm, (hh.gpu || {}).sm_mhz, g.sm_max_mhz || null, S.int);
      // ---- the limit: the owner of the largest share of the last decode window (or of the prompt read now)
      let owner = null, part = null, share = null;
      if (live.state === 'reading' && win && win.prompt_groups_ms) {
        const gg = win.prompt_groups_ms;
        const top = Object.entries(gg).filter(([k]) => k !== 'other').sort((a, b) => b[1] - a[1])[0];
        if (top) { part = top[0]; share = top[1] / S.sum(Object.values(gg)); owner = part === 'experts over PCIe' ? 'pcie' : 'gpu'; }
      } else if (win && win.window_ms && isNum(win.ms_per_window)) {
        const top = Object.entries(win.window_ms).filter(([k]) => k !== 'other').sort((a, b) => b[1] - a[1])[0];
        if (top) { part = top[0]; share = top[1] / win.ms_per_window; owner = { 'CPU experts': 'cpu', 'GPU': 'gpu', 'draft': 'gpu', 'adapt wait': 'pcie' }[part] || null; }
      }
      for (const k of ['disk', 'ram', 'cpu', 'vram', 'gpu']) S.setClass(R[k].node, 'limit', owner === k || (owner === 'cpu' && k === 'ram'));
      S.setClass(R['l-pcie'].node, 'limit', owner === 'pcie');
      S.setClass(R['l-bus'].node, 'limit', owner === 'cpu');
      S.setText(R.ram.badge, owner === 'cpu' ? 'feeds the limit' : 'limit');
      for (const k of ['disk', 'ram', 'cpu', 'vram', 'gpu']) S.setClass(R[k].node, 'stale', !fresh);
      // ---- the summary
      const sum = R.sum;
      S.setText(sum.state, !fresh ? 'no reading from Strata' : generating ? 'decode now' : live.state === 'reading' ? 'prompt read now' : 'idle: last request');
      num(sum.tps, generating && isNum(live.tok_s) ? live.tok_s : (win ? win.decode_tok_s : null), (v) => v.toFixed(1));
      S.setText(sum.limit, part ? `Limiting component: ${S.PART_LABEL[part] || part}, ${S.pct(share)} of the ${live.state === 'reading' ? 'prompt read' : 'decode window'}` : 'No request with a time split yet');
      S.setText(sum.why, owner === 'cpu' ? `The CPU pool is limited by RAM bandwidth.${ctx.workers ? ` ${ctx.workers}.` : ''}` : owner === 'pcie' ? 'Expert copies over PCIe.' : owner === 'gpu' ? 'GPU compute.' : '');
      if (win && win.window_ms) for (const k of S.DECODE_PARTS) { const v = win.window_ms[k] || 0; sum.parts[k]._ms = v; S.setStyle(sum.parts[k], 'flexGrow', Math.max(0, v).toFixed(3)); }
      // ---- the experts
      const X = R.exp;
      const src = win ? S.expertSources(win) : null;
      const slots = eng.expert_slots, total = ctx.experts && ctx.experts.base ? ctx.experts.base.layers * ctx.experts.base.experts : null;
      S.setText(X.state, win ? `${generating ? 'decode now' : 'last decode'} · per-window counts from the engine log, not per expert` : 'no decode yet');
      S.setText(X.facts.slots.v, isNum(slots) ? `${S.int(slots)}${total ? ` of ${S.int(total)} (${S.pct(slots / total, 1)})` : ''}` : '–');
      S.setText(X.facts.cache.v, isNum(eng.expert_cache_mib) ? `${S.int(eng.expert_cache_mib)} MiB` : '–');
      S.setText(X.facts.arena.v, isNum(eng.arena_mib) ? `${S.num(eng.arena_mib / 1024, 1)} GiB` : '–');
      S.setText(X.facts.cpuw.v, win && isNum(win.cpu_experts) ? S.num(win.cpu_experts, 2) : '–');
      S.setText(X.facts.vramw.v, win && isNum(win.vram_hits) ? S.num(win.vram_hits, 2) : '–');
      S.setText(X.facts.pciew.v, win && isNum(win.pcie_per_window) ? S.num(win.pcie_per_window, 2) : '–');
      S.setText(X.facts.swaps.v, win && isNum(win.swaps) ? S.num(win.swaps, 1) : '–');
      S.setText(X.facts.adapt.v, win && win.window_ms && isNum(win.window_ms['adapt wait']) ? `${S.num(win.window_ms['adapt wait'], 2)} ms` : '–');
      S.setText(X.facts.kv.v, win && isNum(win.kv_hit) ? S.pct(win.kv_hit, 1) : '–');
      const ex = ctx.experts || {};
      S.setText(X.facts.learned.v, ex.learned_configured ? (ex.learned && ex.learned.mtime ? `saved ${S.ago(ex.learned.mtime)}` : 'on, no save yet') : 'off');
      S.setText(X.shareText, src ? `${S.pct(src.vram)} / ${S.pct(src.pcie)} / ${S.pct(src.cpu)}` : '–');
      if (src) { S.setStyle(X.share.fills[0], 'width', `${(src.vram * 100).toFixed(2)}%`); S.setStyle(X.share.fills[1], 'width', `${(src.pcie * 100).toFixed(2)}%`); S.setStyle(X.share.fills[2], 'width', `${(src.cpu * 100).toFixed(2)}%`); }
      X.hitSpark.s.update(ctx.hits || [], 100);
      const lastHit = (ctx.hits || []).filter(isNum).slice(-1)[0];
      S.setText(X.hitSpark.v, isNum(lastHit) ? `${S.num(lastHit, 0)} %` : '–');
      S.setText(X.ramSub, isNum(eng.arena_mib) ? `${S.num(eng.arena_mib / 1024, 1)} GiB of experts` : '');
      S.setText(X.cpuSub, isNum(host.pool_cpus && host.pool_cpus.length) ? `${host.pool_cpus.length} workers` : (isNum(eng.pool_workers) ? `${eng.pool_workers} workers` : ''));
      S.setText(X.pcieSub, isNum(gen) ? `Gen ${gen} x${width}` : '');
      S.setText(X.vramSub, isNum(slots) ? `${S.int(slots)} slots` : '');
      S.setText(X.gpuSub, st.gpu_name ? st.gpu_name.replace(/^NVIDIA GeForce /, '') : '');
      const per = win && isNum(win.ms_per_window) && win.ms_per_window > 0 ? 1000 / win.ms_per_window : 0;   // windows per second
      const lines = { computed: win && win.cpu_experts, copied: win && win.pcie_per_window, hit: win && win.vram_hits, swapped: win && win.swaps };
      const shares = { computed: src && src.cpu, copied: src && src.pcie, hit: src && src.vram, swapped: null };
      for (const [k, p] of Object.entries(X.paths)) {
        const v = lines[k];
        const w = k === 'swapped' ? (isNum(v) ? 2 + Math.min(8, v / 15) : 1.5) : (isNum(shares[k]) ? 2 + 14 * shares[k] : 1.5);
        S.setAttr(p.base, 'stroke-width', w.toFixed(1));
        S.setAttr(p.pulse, 'stroke-width', Math.max(1.5, w * 0.45).toFixed(1));
        // pulse speed: proportional to the count per window and the window rate, only while a decode runs
        p.speed = generating && isNum(v) && per ? Math.min(400, 6 * v * per / 10) : 0;
      }
      S.setText(X.labels.computed, isNum(lines.computed) ? `computed: ${S.num(lines.computed, 2)} per layer-window` : '');
      S.setText(X.labels.copied, isNum(lines.copied) ? `read over PCIe: ${S.num(lines.copied, 2)} per layer-window` : '');
      S.setText(X.labels.swapped, isNum(lines.swapped) ? `swapped in: ${S.num(lines.swapped, 1)} per window` : '');
      S.setText(X.labels.hit, isNum(lines.hit) ? `hits: ${S.num(lines.hit, 2)} per layer-window` : '');
      if (!flowRaf && Object.values(X.paths).some((p) => p.speed > 0)) flowRaf = requestAnimationFrame(flowFrame);
    },
  };
})();
