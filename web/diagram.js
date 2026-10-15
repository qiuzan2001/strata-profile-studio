/* Strata Profile Studio: the live PC diagram.  Disk -> RAM -> (memory bus) CPU, RAM -> PCIe -> VRAM -> GPU.

   Built once (S.diagram.build), then fed GET /api/hardware every second (S.diagram.update).  An update only sets
   text, a transform or a class on nodes that already exist; numbers are tweened in one requestAnimationFrame loop,
   meters and pipes move with CSS transforms (compositor only).  No node is created after the build, so the page
   stays flat for hours. */
'use strict';

(function () {
  const { el, isNum } = S;

  // ---------------------------------------------------------------------------------------- tweening
  const tweens = new Map();                    // node -> {from, to, t0, fmt}
  let raf = 0;
  function frame(now) {
    raf = 0;
    for (const [node, t] of tweens) {
      const k = Math.min(1, (now - t.t0) / 300);
      const e = 1 - (1 - k) * (1 - k);
      const v = t.from + (t.to - t.from) * e;
      const s = t.fmt(v);
      if (node.textContent !== s) node.textContent = s;
      if (k >= 1) tweens.delete(node);
    }
    if (tweens.size) raf = requestAnimationFrame(frame);
  }
  function setNum(node, value, fmt) {
    if (!isNum(value)) {
      tweens.delete(node);
      if (node.textContent !== '–') node.textContent = '–';
      node._v = null;
      return;
    }
    const from = isNum(node._v) ? node._v : value;
    node._v = value;
    if (from === value || document.hidden) {
      tweens.delete(node);
      const s = fmt(value);
      if (node.textContent !== s) node.textContent = s;
      return;
    }
    tweens.set(node, { from, to: value, t0: performance.now(), fmt });
    if (!raf) raf = requestAnimationFrame(frame);
  }
  function setText(node, s) {
    if (node.textContent !== s) node.textContent = s;
  }
  function setScale(node, f, axis) {          // a meter or pipe fill: 0..1
    const v = isNum(f) ? Math.max(0, Math.min(1, f)) : 0;
    const t = axis === 'y' ? `scaleY(${v.toFixed(4)})` : `scaleX(${v.toFixed(4)})`;
    if (node.style.transform !== t) node.style.transform = t;
  }
  function setClass(node, cls, on) {
    if (node.classList.contains(cls) !== !!on) node.classList.toggle(cls, !!on);
  }

  // ------------------------------------------------------------------------------------------ build
  const refs = {};
  function block(key, name) {
    const r = refs[key] = {};
    r.name = el('span', { class: 'blk-name', text: name });
    r.badge = el('span', { class: 'blk-badge', text: 'the limit' });
    r.big = el('span', { class: 'blk-big', text: '–' });
    r.unit = el('span', { class: 'blk-unit', text: '' });
    r.meterFill = el('i', { class: 'meter-fill' });
    r.meter = el('div', { class: 'meter' }, r.meterFill);
    r.l1 = el('div', { class: 'blk-line', text: '' });
    r.l2 = el('div', { class: 'blk-line', text: '' });
    r.spark = S.sparkline(`spark-${key}`);
    r.node = el('div', { class: `blk blk-${key}`, dataset: { block: key } },
      el('div', { class: 'blk-head' }, r.name, r.badge),
      el('div', { class: 'blk-value' }, r.big, r.unit),
      r.meter, r.l1, r.l2, r.spark);
    return r.node;
  }
  function link(key, dir) {
    const r = refs['l-' + key] = {};
    r.label = el('span', { class: 'lnk-label', text: '' });
    r.value = el('span', { class: 'lnk-value', text: '–' });
    r.ceil = el('span', { class: 'lnk-ceil', text: '' });
    r.fill = el('i', { class: 'pipe-fill' });
    r.flow = el('i', { class: 'pipe-flow' });
    r.pipe = el('div', { class: `pipe pipe-${dir}` }, r.fill, r.flow);
    r.node = el('div', { class: `lnk lnk-${key} lnk-${dir}`, dataset: { link: key } }, r.label, r.pipe, r.value, r.ceil);
    return r.node;
  }

  S.diagram = {
    build() {
      const sum = refs.sum = {};
      sum.state = el('span', { class: 'sum-state', text: 'waiting for Strata' });
      sum.tps = el('span', { class: 'sum-big', text: '–' });
      sum.tpsUnit = el('span', { class: 'sum-unit', text: 'tok/s' });
      sum.limit = el('div', { class: 'sum-limit', text: '' });
      sum.why = el('div', { class: 'sum-why', text: '' });
      sum.bar = el('div', { class: 'sum-bar' });
      sum.parts = {};
      for (const k of S.DECODE_PARTS) {
        const p = el('i', { class: `sum-part ${S.PART_CLASS[k]}`, title: k });
        S.hover(p, () => [k, ['ms per window', isNum(p._ms) ? S.num(p._ms, 1) : '–'], S.PART_ABOUT[k]]);
        sum.parts[k] = p;
        sum.bar.appendChild(p);
      }
      sum.legend = el('div', { class: 'sum-legend' }, S.DECODE_PARTS.map((k) =>
        el('span', { class: 'lg' }, el('i', { class: `sw ${S.PART_CLASS[k]}` }), `${S.PART_LABEL[k]} → ${{ 'GPU': 'GPU', 'CPU experts': 'CPU + RAM', 'draft': 'GPU', 'adapt wait': 'PCIe', 'other': '–' }[k]}`)));
      sum.node = el('div', { class: 'blk blk-sum' },
        el('div', { class: 'blk-head' }, sum.state),
        el('div', { class: 'blk-value' }, sum.tps, sum.tpsUnit),
        sum.limit, sum.bar, sum.why, sum.legend);
      // one row, in the order the experts travel: disk -> RAM -> CPU (or) -> PCIe -> VRAM -> GPU
      const grid = el('div', { class: 'pc' },
        block('disk', 'Disk'), link('disk', 'h'), block('ram', 'RAM'), link('bus', 'h'), block('cpu', 'CPU'),
        link('pcie', 'h'), block('vram', 'VRAM'), link('card', 'h'), block('gpu', 'GPU'), sum.node);
      refs['l-card'].value.textContent = '';
      refs['l-card'].label.textContent = '';
      refs['l-disk'].label.textContent = '';
      refs['l-bus'].label.textContent = 'memory bus';
      return grid;
    },

    // doc: GET /api/hardware; ctx: {workers: "7, 5 and 4 workers all within 1%" or null}
    update(doc, ctx) {
      if (!refs.ram) return;
      const now = doc.now || {}, st = doc.static || {}, eng = doc.engine || {}, hist = doc.history || {};
      const probe = (doc.log || {}).probe;
      const live = doc.live || {};
      const win = doc.window || null;
      const fresh = isNum(doc.at) && Date.now() / 1000 - doc.at < 15;
      // ---- disk
      setNum(refs.disk.big, now.disk_read_mb, (v) => (v >= 1000 ? (v / 1000).toFixed(1) : v.toFixed(v < 10 ? 1 : 0)));
      setText(refs.disk.unit, isNum(now.disk_read_mb) && now.disk_read_mb >= 1000 ? 'GB/s read' : 'MB/s read');
      setText(refs.disk.l1, isNum(now.disk_write_mb) ? `write ${S.mbps(now.disk_write_mb)}` : 'no disk counters');
      setText(refs.disk.l2, '');
      refs.disk.meter.hidden = true;
      refs.disk.spark.update(hist.disk_read_mb);
      setNum(refs['l-disk'].value, now.disk_read_mb, (v) => S.mbps(v));
      setScale(refs['l-disk'].fill, 0, 'y');                     // no ceiling is known for the disk: the value only
      setClass(refs['l-disk'].node, 'flowing', isNum(now.disk_read_mb) && now.disk_read_mb > 1);
      setText(refs['l-disk'].ceil, '');
      // ---- RAM
      const ramU = now.ram_used, ramT = now.ram_total;
      setNum(refs.ram.big, isNum(ramU) ? ramU / 2 ** 30 : null, (v) => v.toFixed(1));
      setText(refs.ram.unit, isNum(ramT) ? `of ${(ramT / 2 ** 30).toFixed(1)} GiB` : 'GiB');
      setScale(refs.ram.meterFill, isNum(ramU) && isNum(ramT) ? ramU / ramT : null);
      setText(refs.ram.l1, isNum(eng.arena_mib) ? `experts ${S.num(eng.arena_mib / 1024, 1)} GiB` : '');
      setText(refs.ram.l2, isNum(ramU) && isNum(ramT) ? `${S.num((ramT - ramU) / 2 ** 30, 1)} GiB available` : '');
      refs.ram.spark.update(hist.ram_used, ramT);
      // ---- CPU
      setText(refs.cpu.name, 'CPU');
      refs.cpu.node.title = st.cpu_name || '';
      setNum(refs.cpu.big, now.cpu, (v) => v.toFixed(0));
      setText(refs.cpu.unit, '% busy');
      setScale(refs.cpu.meterFill, isNum(now.cpu) ? now.cpu / 100 : null);
      setText(refs.cpu.l1, [st.cores ? `${st.cores} cores` : null, st.threads ? `${st.threads} threads` : null].filter(Boolean).join(' · '));
      setText(refs.cpu.l2, isNum(eng.pool_workers) ? `${eng.pool_workers} pool workers` : '');
      refs.cpu.spark.update(hist.cpu, 100);
      // ---- memory bus (the one estimate): CPU-expert share of the window x the pool's measured expert rate
      const generating = live.state === 'generating';
      const cpuShare = win && win.window_ms && isNum(win.ms_per_window) && win.ms_per_window > 0 ? (win.window_ms['CPU experts'] || 0) / win.ms_per_window : null;
      const busEst = probe && isNum(cpuShare) ? cpuShare * probe.cpu_gbs : null;
      setNum(refs['l-bus'].value, generating ? busEst : (isNum(busEst) ? 0 : null), (v) => `≈ ${v.toFixed(1)} GB/s`);
      setText(refs['l-bus'].ceil, probe ? `of ${S.num(probe.cpu_gbs, 1)} GB/s measured` : 'no probe line in the log');
      refs['l-bus'].node.title = probe ? `The one estimate on this page: the CPU-expert share of the last decode window × the ${S.num(probe.cpu_gbs, 1)} GB/s the CPU pool reads experts at (the engine's PCIe-share probe).${isNum(busEst) ? ` Last decode ≈ ${S.num(busEst, 1)} GB/s.` : ''}` : '';
      setScale(refs['l-bus'].fill, generating && probe ? busEst / probe.cpu_gbs : 0, 'y');
      setClass(refs['l-bus'].node, 'flowing', generating);
      // ---- PCIe
      const gen = now.gpu_pcie_gen, gmax = now.gpu_pcie_gen_max, width = now.gpu_pcie_width;
      setText(refs['l-pcie'].label, isNum(gen) ? `PCIe Gen${gen}${isNum(gmax) && gmax !== gen ? ` (max ${gmax})` : ''}${isNum(width) ? ` ×${width}` : ''}` : 'PCIe');
      const rx = now.gpu_pcie_rx_mb, tx = now.gpu_pcie_tx_mb;
      const nominal = isNum(gmax) && isNum(width) ? ({ 1: 0.25, 2: 0.5, 3: 0.985, 4: 1.969, 5: 3.938, 6: 7.563 }[gmax] || null) * width : null;
      const ceilGB = probe ? probe.link_gbs : nominal;
      setNum(refs['l-pcie'].value, isNum(rx) ? rx + (tx || 0) : null, (v) => `${S.mbps(v)}`);
      setText(refs['l-pcie'].ceil, ceilGB ? `of ${S.num(ceilGB, 1)} GB/s ${probe ? 'measured' : 'nominal'}` : '');
      refs['l-pcie'].node.title = `to the card ${S.mbps(rx)} · from the card ${S.mbps(tx)}`;
      setScale(refs['l-pcie'].fill, ceilGB && isNum(rx) ? (rx + (tx || 0)) / (ceilGB * 1000) : 0, 'y');
      setClass(refs['l-pcie'].node, 'flowing', isNum(rx) && rx > 200);
      // ---- VRAM
      const vu = now.gpu_mem_used, vt = now.gpu_mem_total;
      setNum(refs.vram.big, isNum(vu) ? vu / 2 ** 30 : null, (v) => v.toFixed(1));
      setText(refs.vram.unit, isNum(vt) ? `of ${(vt / 2 ** 30).toFixed(1)} GiB` : 'GiB');
      setScale(refs.vram.meterFill, isNum(vu) && isNum(vt) ? vu / vt : null);
      setText(refs.vram.l1, isNum(eng.expert_cache_mib) ? `experts ${S.num(eng.expert_cache_mib / 1024, 1)} GiB (${S.int(eng.expert_slots)})` : '');
      const lowVram = isNum(eng.vram_free_mib) && eng.vram_free_mib < 512;
      setText(refs.vram.l2, isNum(eng.vram_free_mib) ? `${S.int(eng.vram_free_mib)} MiB free${lowVram ? ' ⚠ under 512' : ''}` : '');
      refs.vram.l2.title = lowVram ? 'less than 512 MiB of VRAM free after the load: the driver can page GPU memory to RAM (Strata warns in its log)' : '';
      setClass(refs.vram.l2, 'warn', lowVram);
      refs.vram.spark.update(hist.gpu_mem_used, vt);
      // ---- GPU
      setText(refs.gpu.name, 'GPU');
      refs.gpu.node.title = st.gpu_name || '';
      setNum(refs.gpu.big, now.gpu_util, (v) => v.toFixed(0));
      setText(refs.gpu.unit, '% busy');
      setScale(refs.gpu.meterFill, isNum(now.gpu_util) ? now.gpu_util / 100 : null);
      setText(refs.gpu.l1, isNum(now.gpu_power) ? `${Math.round(now.gpu_power)} W${isNum(now.gpu_power_limit) ? ` of ${Math.round(now.gpu_power_limit)} W` : ''}` : '');
      setText(refs.gpu.l2, isNum(now.gpu_temp) ? `${Math.round(now.gpu_temp)} °C` : '');
      refs.gpu.spark.update(hist.gpu_util, 100);
      refs['l-card'].fill.style.transform = 'scaleY(0)';
      // ---- the limit: the owner of the largest share of the last decode window (or of a prompt read now)
      let owner = null, part = null, share = null;
      if (live.state === 'reading' && win && win.prompt_groups_ms) {
        const g = win.prompt_groups_ms;
        const top = Object.entries(g).filter(([k]) => k !== 'other').sort((a, b) => b[1] - a[1])[0];
        if (top) { part = top[0]; share = top[1] / S.sum(Object.values(g)); owner = part === 'experts over PCIe' ? 'pcie' : 'gpu'; }
      } else if (win && win.window_ms && isNum(win.ms_per_window)) {
        const top = Object.entries(win.window_ms).filter(([k]) => k !== 'other').sort((a, b) => b[1] - a[1])[0];
        if (top) {
          part = top[0]; share = top[1] / win.ms_per_window;
          owner = { 'CPU experts': 'cpu', 'GPU': 'gpu', 'draft': 'gpu', 'adapt wait': 'pcie' }[part] || null;
        }
      }
      for (const k of ['disk', 'ram', 'cpu', 'vram', 'gpu']) setClass(refs[k].node, 'limit', owner === k || (owner === 'cpu' && k === 'ram'));
      setClass(refs['l-pcie'].node, 'limit', owner === 'pcie');
      setClass(refs['l-bus'].node, 'limit', owner === 'cpu');
      setText(refs.ram.badge, owner === 'cpu' ? 'feeds the limit' : 'the limit');
      // ---- the summary block
      const sum = refs.sum;
      const state = !fresh ? 'no reading from Strata' : live.state === 'generating' ? 'decoding now' : live.state === 'reading' ? 'reading a prompt now' : 'idle · last request';
      setText(sum.state, state);
      const tps = live.state === 'generating' && isNum(live.tok_s) ? live.tok_s : (win ? win.decode_tok_s : null);
      setNum(sum.tps, tps, (v) => v.toFixed(1));
      setText(sum.limit, part ? `The limit: ${S.PART_LABEL[part] || part} · ${S.pct(share)} of the ${live.state === 'reading' ? 'prompt read' : 'window'}` : 'No request with a time split yet');
      setText(sum.why, owner === 'cpu' ? `RAM-bandwidth-bound${ctx && ctx.workers ? `: ${ctx.workers}` : probe ? `: the pool reads experts at ${S.num(probe.cpu_gbs, 1)} GB/s` : ''}` :
        owner === 'pcie' ? 'expert copies over PCIe' : owner === 'gpu' ? 'the card computes' : '');
      if (win && win.window_ms && isNum(win.ms_per_window)) {
        for (const k of S.DECODE_PARTS) {
          const v = win.window_ms[k] || 0;
          sum.parts[k]._ms = v;
          const g = `${Math.max(0, v).toFixed(3)}`;
          if (sum.parts[k].style.flexGrow !== g) sum.parts[k].style.flexGrow = g;
        }
      }
      setClass(sum.node, 'stale', !fresh);
      for (const k of ['disk', 'ram', 'cpu', 'vram', 'gpu']) setClass(refs[k].node, 'stale', !fresh);
    },
  };
})();
