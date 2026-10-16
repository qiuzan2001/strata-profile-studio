/* Strata Profile Studio: Settings.  Each speed setting in three layers (loaded now, next load, run config file) with
   the engine default, a control to edit the file value by hand, and "use the settings of a run".  Edits are staged in
   a list and written with a .bak and an explicit confirm.  Strata reads the file at its next start. */
'use strict';

(function () {
  const { el, isNum } = S;
  S.viewDefs = S.viewDefs || {};
  let token = 0;
  let preview = null, previewFor = null, picked = null;
  const edits = new Map();                               // setting id -> new file value (null: the engine default)
  let fileDoc = null;
  let stockDoc = null;                                   // stock mode (Strata without /profile routes): GET /api/stock
  const refs = {};

  const norm = (v) => (v === null || v === undefined ? null : String(v));
  const same = (a, b) => (a === null && b === null) || (a !== null && b !== null && (S.same(a, b) || a === b));

  function layersNote(r, file) {
    const now = norm(r.now), next = norm(r.next_load), cfg = norm(r.run_config);
    const notes = [];
    if (now !== null && next !== null && !same(now, next) && r.id !== 'expert_cache') notes.push(`The next load uses ${next}, not ${now}.`);
    if (!same(next, cfg)) notes.push(`${file || 'The file'} has ${cfg ?? 'no value (engine default)'}. Strata reads the file at its next start. Until then, this Strata process uses ${next ?? 'the engine default'}.`);
    return notes;
  }

  // ------------------------------------------------------------------------------------------ edits
  function stage(r, value) {
    const cur = norm(r.run_config);
    const v = value === 'default' ? null : norm(value);
    if (same(cur, v)) edits.delete(r.id); else edits.set(r.id, v);
    updateDiff();
    const row = refs.rows && refs.rows[r.id];
    if (row) S.setClass(row.tr, 'edited', edits.has(r.id));
  }
  function check(r, value) {
    const c = r.control;
    if (!c || value === null || value === 'default' || (c.specials || []).includes(value)) return null;
    if (c.control === 'text') return value ? null : 'Give a path, or select default.';
    const n = Number(value);
    if (!isFinite(n)) return `${value} is not a number.`;
    if (c.or_min && n !== 0 && n < c.or_min) return `Use 0 or ${c.or_min} or more.`;
    if (isNum(c.min) && n < c.min) return `The minimum is ${c.min}.`;
    if (isNum(c.max) && n > c.max) return `The maximum is ${c.max}.`;
    return null;
  }
  function control(r) {
    const c = r.control;
    if (!c) return el('span', { class: 'muted small', text: 'not editable' });
    const cur = edits.has(r.id) ? edits.get(r.id) : norm(r.run_config);
    const box = el('div', { class: 'ctl' });
    const err = el('span', { class: 'ctl-err', text: '' });
    const special = (cur !== null && (c.specials || []).includes(cur)) ? cur : null;
    let input;
    const commit = (v) => { const e = check(r, v); S.setText(err, e || ''); if (!e) stage(r, v); };
    if (c.control === 'range') {
      const num = el('input', { type: 'number', class: 'ctl-num', min: c.min, max: c.max, step: c.step, value: special || cur === null ? '' : cur, 'aria-label': `${r.name} value`, placeholder: special || 'default' });
      input = el('input', { type: 'range', min: c.min, max: c.max, step: c.step, value: special || cur === null ? (c.min + c.max) / 2 : cur, 'aria-label': r.name });
      if (special || cur === null) input.classList.add('unset');
      input.addEventListener('input', () => { num.value = input.value; input.classList.remove('unset'); commit(input.value); });
      num.addEventListener('change', () => { input.value = num.value; input.classList.remove('unset'); commit(num.value); });
      box.append(input, num);
    } else if (c.control === 'number') {
      input = el('input', { type: 'number', class: 'ctl-num wide', min: c.min, step: c.step, value: special || cur === null ? '' : cur, placeholder: special || 'default', 'aria-label': r.name });
      input.addEventListener('change', () => commit(input.value === '' ? 'default' : input.value));
      box.append(input);
    } else {
      const on = el('input', { type: 'checkbox', 'aria-label': `${r.name} on or off` });
      on.checked = cur !== null;
      input = el('input', { type: 'text', class: 'ctl-text', value: cur || '', placeholder: 'path of the learned profile', 'aria-label': r.name });
      input.disabled = !on.checked;
      on.addEventListener('change', () => { input.disabled = !on.checked; commit(on.checked ? (input.value || 'data/expert-profile-learned.bin') : 'default'); if (on.checked && !input.value) input.value = 'data/expert-profile-learned.bin'; });
      input.addEventListener('change', () => commit(input.value));
      box.append(el('label', { class: 'toggle' }, on, ' on'), input);
    }
    for (const sp of (c.specials || []).filter((x) => x !== 'default')) box.appendChild(S.button(sp, () => { commit(sp); render(window.S.app); }, `small ${cur === sp ? 'on' : ''}`));
    if (c.reset_to !== 'keep') {
      const to = c.reset_to === null || c.reset_to === undefined ? 'default' : c.reset_to;
      const title = c.group === 'setup' ? `Back to setup's value: ${to}.`
        : c.group === 'calibration' ? (to === 'default' ? `Back to the calibration's default: no value in the file, the engine chooses (${c.engine_default}).` : `Back to the calibration's default: ${to} (setup's value).`)
        : `Remove the value from the file: the engine default (${c.engine_default}) applies.`;
      box.appendChild(S.button('Reset', () => { commit(to); render(window.S.app); }, 'small', { title }));
    }
    box.appendChild(err);
    return box;
  }

  function updateDiff() {
    const d = refs.diff;
    if (!d) return;
    const rows = (window.S.app.settings && window.S.app.settings.rows) || [];
    const list = [...edits.entries()].map(([id, v]) => ({ r: rows.find((x) => x.id === id), v })).filter((x) => x.r);
    S.setText(d.title, list.length ? `${list.length} setting${list.length === 1 ? '' : 's'} changed` : 'No edit');
    S.fill(d.list, list.map(({ r, v }) => el('div', { class: 'diff' }, el('b', { text: r.name }), el('span', { class: 'from', text: r.run_config ?? 'engine default' }), el('span', { text: 'to' }), el('span', { class: 'to', text: v ?? `engine default (${r.control ? r.control.engine_default : ''})` }))));
    d.write.disabled = !list.length || window.S.app.readOnly() || !fileDoc || !!fileDoc.error;
    d.discard.disabled = !list.length;
  }

  async function writeEdits(A) {
    const rows = (A.settings && A.settings.rows) || [];
    const list = [...edits.entries()].map(([id, v]) => ({ r: rows.find((x) => x.id === id), id, v })).filter((x) => x.r);
    if (!list.length || !fileDoc) return;
    const ok = await S.confirm({
      title: `Write ${list.length} setting${list.length === 1 ? '' : 's'} to ${fileDoc.file}?`,
      points: [...list.map(({ r, v }) => `${r.name}: ${r.run_config ?? 'engine default'} to ${v ?? 'engine default (the Studio removes the value from the file)'}`),
        `The Studio copies the current file to ${fileDoc.backup} first. It replaces an older ${fileDoc.backup}.`,
        'Strata reads the file at its next start. Restart Strata to use the new values. The running engine keeps its settings until then.',
        'A tune writes the calibrated settings again when it ends.'],
      ok: `Write ${fileDoc.file}`, danger: true,
    });
    if (!ok) return;
    try {
      const res = await S.api('/api/settings/edit', { body: { edits: list.map(({ id, v }) => ({ id, value: v })), sha: fileDoc.sha, confirm: true } });
      S.toast(`The Studio wrote ${res.changes.length} setting${res.changes.length === 1 ? '' : 's'}. The old file is ${res.backup.split('/').pop()}. Restart Strata to use the new values.`);
      edits.clear();
      fileDoc = null;
      A.poll('settings', true);
      render(A);
    } catch (e) { S.toast(e.message, 'bad'); fileDoc = null; render(A); }
  }

  // ------------------------------------------------------------------------------- stock mode: the timing lines
  function stockCard(A) {
    const d = stockDoc || {};
    if (d.error) return S.card({ q: 'Timing lines', cls: 'span3 stock-card' }, el('p', { class: 'note', text: d.error }));
    const env = d.env || {}, log = d.log || {};
    const file = d.config ? d.config.split(/[\\/]/).pop() : 'the run config';
    const val = (k) => (env[k] === undefined ? 'no run config' : env[k] === null ? 'not set' : `"${env[k]}"`);
    const rows = [
      ['STRATA_DECODE_TIMING in "env"', val('STRATA_DECODE_TIMING')],
      ['STRATA_PREFILL_TIMING in "env"', val('STRATA_PREFILL_TIMING')],
      ['Engine log', log.path ? `${log.path} · ${log.readable ? 'the Studio can read it' : log.exists ? 'the Studio cannot read it' : 'no file there'}` : 'not known (start the Studio with --config or --engine-log)'],
      ['Timing lines in the log', !log.readable ? '–' : log.timing_lines ? 'yes' : 'no timing line yet'],
      ["Strata's tools/ (for a profile run)", d.strata_root || d.tools || '–'],
      ['Tune', d.tune || 'available: the Studio unloads the model, then runs setup.calibrate_config'],
    ];
    const notes = [];
    if (log.path && !log.exists) {
      notes.push(`There is no engine log at ${log.path}. Strata writes it when its engine starts. When Strata runs in another container or on another PC, the Studio cannot read it. Then the Studio shows the speeds from /metrics and the API, and runs without the breakdown columns.`);
    } else if (log.path && !log.readable) {
      notes.push(`The Studio cannot read the engine log at ${log.path}. Strata can run in another container or on another PC. The Studio still shows the speeds from /metrics and the API, and runs without the breakdown columns.`);
    } else if (!d.env_complete) {
      notes.push('The engine writes its timing lines only when the run config has the two switches. Add them, then restart Strata so the engine starts with them.');
    } else if (!log.timing_lines) {
      notes.push(`The run config has the two switches. ${d.restart} Then the log gets one timing line for each request.`);
    } else {
      notes.push('The engine writes its timing lines. Now shows the time per component of each request.');
    }
    const add = S.button(`Add the timing switches to ${file}`, async () => {
      const ok = await S.confirm({
        title: `Add the timing switches to ${file}?`,
        points: ['The Studio adds "STRATA_PREFILL_TIMING": "1" and "STRATA_DECODE_TIMING": "1" to "env". The other keys of "env" stay.',
          `The Studio copies the current file to ${file}.bak first.`, d.restart],
        ok: `Write ${file}`,
      });
      if (!ok) return;
      try {
        const res = await S.api('/api/stock/timing', { body: {} });
        S.toast(`The Studio wrote ${file}. ${res.message}`);
      } catch (e) { S.toast(e.message, 'bad'); }
      stockDoc = null;
      render(A);
    }, 'primary', { disabled: !!(d.env_complete || !d.config || A.readOnly()) });
    const again = S.button('Read again', () => { stockDoc = null; render(A); }, 'small');
    return S.card({ q: 'Timing lines', sub: 'Strata without the /profile routes (0.1.44.1 as released): the Studio reads the time per component from the engine log', cls: 'span3 stock-card' },
      el('table', { class: 'stock-table' }, el('tbody', null, rows.map(([k, v]) => el('tr', null, el('th', { text: k }), el('td', { text: v }))))),
      notes.map((n) => el('p', { class: 'note', text: n })),
      el('div', { class: 'row-buttons' }, add, again));
  }

  // ------------------------------------------------------------------------------- settings of a run
  async function loadPreview(A, rid) {
    previewFor = rid;
    preview = null;
    try { preview = await S.api(`/api/settings/apply-preview?report=${encodeURIComponent(rid)}`); } catch (e) { preview = { error: e.message }; }
    picked = new Set(((preview && preview.rows) || []).filter((r) => r.changed && r.calibrated).map((r) => r.flag));
    render(A);
  }
  async function writeRun(A) {
    const rows = preview.rows.filter((r) => picked.has(r.flag) && r.changed);
    if (!rows.length) { S.toast('Select at least one setting with a different value.', 'bad'); return; }
    const ok = await S.confirm({
      title: `Write ${preview.file}?`,
      points: [...rows.map((r) => `${r.flag}: ${r.file ?? 'engine default'} to ${r.run ?? 'engine default (the Studio removes the value from the file)'}`),
        `The Studio copies the current file to ${preview.backup} first. It replaces an older ${preview.backup}.`,
        'Strata reads the file at its next start. Restart Strata to use the new values. The running engine keeps its settings until then.',
        'Only the selected settings change. All other keys and arguments of the file stay the same.'],
      ok: `Write ${preview.file}`, danger: true,
    });
    if (!ok) return;
    try {
      const res = await S.api('/api/settings/apply', { body: { report: previewFor, flags: rows.map((r) => r.flag), sha: preview.sha, confirm: true } });
      S.toast(`The Studio wrote ${res.changes.length} setting${res.changes.length === 1 ? '' : 's'}. The old file is ${res.backup.split('/').pop()}. Restart Strata to use the new values.`);
      fileDoc = null;
      A.poll('settings', true);
      loadPreview(A, previewFor);
    } catch (e) { S.toast(e.message, 'bad'); loadPreview(A, previewFor); }
  }
  function runCard(A) {
    const rs = A.reports.filter((r) => r.ok);
    const cfgPath = A.state && A.state.studio && A.state.studio.config_path;
    if (!cfgPath) return S.card({ q: 'Settings of a run', sub: 'writes the run config file', cls: 'span3' }, el('p', { class: 'empty-line', text: 'The Studio has no --config. It does not know the run config file. Start it again with --config <Strata folder>/strata-<model>.json.' }));
    if (!rs.length) return S.card({ q: 'Settings of a run', cls: 'span3' }, el('p', { class: 'empty-line', text: 'There is no run on disk.' }));
    let rid = previewFor || S.store.get('settings.report', null);
    if (!rs.find((r) => r.id === rid)) { const t = A.tunes.find((x) => rs.find((r) => r.id === x.before_report)); rid = t ? t.before_report : rs[0].id; }
    if (previewFor !== rid) loadPreview(A, rid);
    const sel = el('select', { 'aria-label': 'Run' }, rs.map((r) => el('option', { value: r.id, selected: r.id === rid, text: `${S.runName(r.id, A.reports)} · ${S.clock(r.mtime, true)} · chat ${S.num(r.chat_tps, 1)} tok/s${r.defaults ? ' · engine defaults' : ''}${r.tune ? ` · ${r.tune.role} tune` : ''}` })));
    sel.addEventListener('change', () => { S.store.set('settings.report', sel.value); loadPreview(A, sel.value); });
    const body = [el('div', { class: 'pick-row' }, el('span', { text: 'Use the settings of' }), sel)];
    if (!preview) body.push(el('p', { class: 'loading', text: 'Reading the file …' }));
    else if (preview.error) body.push(el('p', { class: 'warnline', text: preview.error }));
    else {
      const diff = preview.rows.filter((r) => r.changed);
      body.push(el('p', { class: 'muted small', text: `${diff.length} of ${preview.rows.length} recorded settings are different in ${preview.file} and ${S.runName(previewFor, A.reports)}. The calibrated settings are selected.` }),
        el('div', { class: 'apply-rows' }, preview.rows.filter((r) => r.changed || r.calibrated).map((r) => {
          const box = el('input', { type: 'checkbox', disabled: !r.changed || A.readOnly(), 'aria-label': r.flag });
          box.checked = picked.has(r.flag) && r.changed;
          box.addEventListener('change', () => { if (box.checked) picked.add(r.flag); else picked.delete(r.flag); render(A); });
          return el('label', { class: `apply-row ${r.changed ? 'changed' : 'same'}` }, box, el('b', { text: r.flag }), el('span', { class: 'from', text: r.file ?? 'engine default' }), el('span', { text: r.changed ? 'to' : '=' }),
            el('span', { class: 'to', text: r.run ?? 'engine default' }), r.calibrated ? S.chip('calibrated', 'small') : null);
        })));
      const n = preview.rows.filter((r) => r.changed && picked.has(r.flag)).length;
      const why = A.readOnly() ? 'This Studio runs with --read-only.' : preview.busy || null;
      body.push(el('div', { class: 'row-buttons' }, S.button(`Write ${n} setting${n === 1 ? '' : 's'} to ${preview.file}`, () => writeRun(A), 'danger', { disabled: !n || !!why, title: why || '' }),
        el('span', { class: 'muted small', text: `${preview.path} · the Studio keeps a copy in ${preview.backup}` })), why ? el('p', { class: 'note', text: why }) : null);
      if (!diff.length) body.push(el('p', { class: 'note', text: `${preview.file} has the settings of ${S.runName(previewFor, A.reports)} already. Strata uses them after its next start.` }));
    }
    return S.card({ q: 'Settings of a run', sub: 'writes the values of a run into the run config file · the only file that the Studio writes, other than its state file', cls: 'span3' }, body);
  }

  // ------------------------------------------------------------------------------------------ the page
  async function render(A) {
    const my = ++token;
    const sec = document.getElementById('view-settings');
    const st = A.settings;
    const head = el('div', { class: 'view-head' }, el('h2', { text: 'Settings' }),
      el('span', { class: 'muted', text: st && st.file ? `run config file: ${st.file} · Strata reads the file at its next start` : '' }));
    if (!st) { S.fill(sec, head, el('p', { class: 'loading', text: 'Reading the settings …' })); return; }
    if (!fileDoc && A.state && A.state.studio && A.state.studio.config_path) {
      try { fileDoc = await S.api('/api/settings/file'); } catch (e) { fileDoc = { error: e.message }; }
      if (my !== token) return;
    }
    const stock = !!(A.state && A.state.strata && A.state.strata.stock);
    if (stock && !stockDoc) {
      try { stockDoc = await S.api('/api/stock'); } catch (e) { stockDoc = { error: e.message }; }
      if (my !== token) return;
    }
    const rows = st.rows || [];
    const disagree = rows.filter((r) => layersNote(r, st.file).length);
    const tiles = el('div', { class: 'tiles' },
      S.tile('Settings with different layers', String(disagree.length), '', disagree.map((r) => r.name.replace(/^--/, '')).join(' · ') || 'all layers have the same value', disagree.length ? 'state-worse' : 'state-better'),
      S.tile('Source', { 'profile/settings': 'Strata', metrics: 'Strata /metrics', config: 'the file only', none: 'none' }[st.source] || st.source, '', st.note || (st.loaded ? 'the engine is loaded' : 'the engine is not loaded: no loaded values')),
      S.tile('Edits to write', String(edits.size), '', 'staged on this page, not written yet'));
    refs.rows = {};
    const t = el('table', { class: 'data settings-table' });
    t.appendChild(el('thead', null, el('tr', null, ['Setting', 'Loaded now', 'Next load', st.file || 'Config file', 'Engine default', 'Edit the file value'].map((h) => el('th', { text: h })))));
    const tb = el('tbody');
    for (const r of rows) {
      const notes = layersNote(r, st.file);
      const c = r.control;
      const tr = el('tr', { class: `${notes.length ? 'disagree-row' : ''} ${edits.has(r.id) ? 'edited' : ''}` },
        el('td', null, el('div', { class: 'mono', text: r.name }), r.calibrated ? S.chip('calibrated', 'small', 'Tune this PC measures this setting.') : null,
          c && c.group === 'setup' ? S.chip('set by setup', 'small', 'setup.py wrote this flag for this install. The Studio does not remove it.') : null,
          el('div', { class: 'about', text: r.about }), notes.map((n) => el('div', { class: 'disagree', text: `⚠ ${n}` }))),
        el('td', { class: 'num', text: r.now ?? '–' }),
        el('td', { class: 'num', text: r.next_load ?? (r.now !== null ? 'engine default' : '–') }),
        el('td', { class: 'num', text: r.run_config ?? '–' }),
        el('td', { class: 'small', text: c ? c.engine_default : '–' }),
        el('td', null, control(r), c ? el('div', { class: 'hint', text: `${c.source}. Applies at the next start of Strata.${c.reset_to === 'keep' ? ' Setup chose this value for this PC: to choose it again, run setup.' : ''}` }) : null));
      refs.rows[r.id] = { tr };
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    const d = refs.diff = { title: el('h4', { text: '' }), list: el('div', { class: 'diffs' }) };
    d.write = S.button(`Write the edits to ${st.file || 'the file'}`, () => writeEdits(A), 'danger');
    d.discard = S.button('Discard the edits', () => { edits.clear(); render(A); }, 'small');
    // the calibrated knobs only, to the calibration's own defaults; setup's flags stay (a native pack needs them)
    const resetAll = S.button('Reset the calibrated settings', () => {
      for (const r of rows) {
        const c = r.control;
        if (!c || c.group !== 'calibration') continue;
        const to = c.reset_to === undefined ? null : c.reset_to;
        if (!same(norm(r.run_config), to)) edits.set(r.id, to);
      }
      render(A);
    }, 'small', { title: 'The settings Tune this PC writes go back to the calibration\'s defaults. The flags that setup wrote stay.' });
    const diffCard = S.card({ q: 'Edits', sub: fileDoc && !fileDoc.error ? `the Studio writes ${fileDoc.path} and keeps a copy in ${fileDoc.backup}` : (fileDoc && fileDoc.error) || 'the run config file is not known', cls: 'span3 edits-card' },
      d.title, d.list, el('div', { class: 'row-buttons' }, d.write, d.discard, resetAll),
      A.readOnly() ? el('p', { class: 'note', text: 'This Studio runs with --read-only. It writes no file.' }) : null);
    const table = S.card({ q: 'Settings per layer', sub: 'loaded now, next load, run config file and engine default · ⚠: the layers have different values · an edit changes the file value', cls: 'span3' },
      el('div', { class: 'table-wrap' }, t));
    S.fill(sec, head, tiles, el('div', { class: 'grid' }, stock ? stockCard(A) : null, diffCard, table, runCard(A)));
    updateDiff();
  }

  S.viewDefs.settings = { render };
})();
