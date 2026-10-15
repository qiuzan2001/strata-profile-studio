/* Strata Profile Studio: Settings.  Each speed setting as the engine runs it now, as the next load will start it
   and as the run config file has it; what it does; and a way back: write the file with the settings of a run
   (a .bak first, an explicit confirm; Strata reads the file at its next start). */
'use strict';

(function () {
  const { el, isNum } = S;
  S.viewDefs = S.viewDefs || {};
  let token = 0;
  let preview = null, previewFor = null, picked = null;

  const norm = (v) => (v === null || v === undefined ? null : String(v));
  const same = (a, b) => (a === null && b === null) || (a !== null && b !== null && (S.same(a, b) || a === b));

  function layersNote(r, file) {
    const now = norm(r.now), next = norm(r.next_load), cfg = norm(r.run_config);
    const notes = [];
    if (now !== null && next !== null && !same(now, next) && r.id !== 'expert_cache') notes.push(`the next load uses ${next}, not ${now}`);
    if (!same(next, cfg)) notes.push(`${file || 'the file'} says ${cfg ?? 'nothing (engine default)'}: Strata reads it at its next start; until then this Strata process keeps ${next ?? 'the engine default'}`);
    return notes;
  }

  async function loadPreview(A, rid) {
    previewFor = rid;
    preview = null;
    try { preview = await S.api(`/api/settings/apply-preview?report=${encodeURIComponent(rid)}`); } catch (e) { preview = { error: e.message }; }
    picked = new Set(((preview && preview.rows) || []).filter((r) => r.changed && r.calibrated).map((r) => r.flag));
    render(A);
  }

  async function write(A) {
    const rows = preview.rows.filter((r) => picked.has(r.flag) && r.changed);
    if (!rows.length) { S.toast('Tick at least one setting that differs.', 'bad'); return; }
    const ok = await S.confirm({
      title: `Write ${preview.file}?`,
      points: [
        ...rows.map((r) => `${r.flag}: ${r.file ?? 'engine default'} → ${r.run ?? 'engine default (removed from the file)'}`),
        `The file as it is now is copied to ${preview.backup} first (a previous .bak is replaced).`,
        'Strata reads the file at its next start: restart Strata to run with these values. The running engine keeps its settings until then.',
        'Only the ticked settings change; every other key and argument of the file stays.',
      ],
      ok: `Write ${preview.file}`, danger: true,
    });
    if (!ok) return;
    try {
      const res = await S.api('/api/settings/apply', { body: { report: previewFor, flags: rows.map((r) => r.flag), sha: preview.sha, confirm: true } });
      S.toast(`Written: ${res.changes.length} setting${res.changes.length === 1 ? '' : 's'}; the old file is ${res.backup.split('/').pop()}. Restart Strata to use them.`);
      A.poll('settings', true);
      loadPreview(A, previewFor);
    } catch (e) { S.toast(e.message, 'bad'); loadPreview(A, previewFor); }
  }

  function goBackCard(A) {
    const rs = A.reports.filter((r) => r.ok);
    const cfgPath = A.state && A.state.studio && A.state.studio.config_path;
    if (!cfgPath) {
      return S.card({ q: 'Go back to the settings of a run', sub: 'writes the run config file', cls: 'span3' },
        el('p', { class: 'empty-line', text: 'The Studio was started without --config, so it does not know the run config file. Restart it with --config <Strata folder>/strata-<model>.json.' }));
    }
    if (!rs.length) return S.card({ q: 'Go back to the settings of a run', cls: 'span3' }, el('p', { class: 'empty-line', text: 'There is no run on disk yet.' }));
    let rid = previewFor || S.store.get('settings.report', null);
    if (!rs.find((r) => r.id === rid)) {
      const t = A.tunes.find((x) => rs.find((r) => r.id === x.before_report));
      rid = t ? t.before_report : rs[0].id;
    }
    if (previewFor !== rid) { loadPreview(A, rid); }
    const sel = el('select', { 'aria-label': 'Run' }, rs.map((r) => el('option', { value: r.id, selected: r.id === rid, text: `${S.runName(r.id, A.reports)} · ${S.clock(r.mtime, true)} · chat ${S.num(r.chat_tps, 1)} tok/s${r.tune ? ` · ${r.tune.role} tune` : ''}` })));
    sel.addEventListener('change', () => { S.store.set('settings.report', sel.value); loadPreview(A, sel.value); });
    const body = [el('div', { class: 'pick-row' }, el('span', { text: 'Use the settings of' }), sel)];
    if (!preview) body.push(el('p', { class: 'loading', text: 'Reading the file…' }));
    else if (preview.error) body.push(el('p', { class: 'warnline', text: preview.error }));
    else {
      const diff = preview.rows.filter((r) => r.changed);
      const list = el('div', { class: 'apply-rows' }, preview.rows.filter((r) => r.changed || r.calibrated).map((r) => {
        const box = el('input', { type: 'checkbox', disabled: !r.changed || A.readOnly(), 'aria-label': r.flag });
        box.checked = picked.has(r.flag) && r.changed;
        box.addEventListener('change', () => { if (box.checked) picked.add(r.flag); else picked.delete(r.flag); render(A); });
        return el('label', { class: `apply-row ${r.changed ? 'changed' : 'same'}` }, box,
          el('b', { text: r.flag }), el('span', { class: 'from', text: r.file ?? 'engine default' }), el('span', { text: r.changed ? '→' : '=' }),
          el('span', { class: 'to', text: r.run ?? 'engine default' }), r.calibrated ? S.chip('calibrated', 'small') : null);
      }));
      const n = preview.rows.filter((r) => r.changed && picked.has(r.flag)).length;
      const why = A.readOnly() ? 'This Studio was started with --read-only.' : preview.busy || null;
      body.push(el('p', { class: 'muted small', text: `${diff.length} of ${preview.rows.length} recorded settings differ between ${preview.file} and ${S.runName(previewFor, A.reports)}. The calibrated ones are ticked.` }),
        list,
        el('div', { class: 'row-buttons' }, S.button(`Write ${n} setting${n === 1 ? '' : 's'} to ${preview.file}…`, () => write(A), 'danger', { disabled: !n || !!why, title: why || '' }),
          el('span', { class: 'muted small', text: `${preview.path} · a copy goes to ${preview.backup} · Strata reads the file at its next start` })),
        why ? el('p', { class: 'note', text: why }) : null);
      if (!diff.length) body.push(el('p', { class: 'note', text: `${preview.file} already has the settings of ${S.runName(previewFor, A.reports)}. Strata runs with them after its next start; until then the table above says what runs.` }));
    }
    return S.card({ q: 'Go back to the settings of a run', sub: 'writes the run config file with the values that run had · the only file the Studio ever writes besides its own state', cls: 'span3' }, body);
  }

  function render(A) {
    const my = ++token;
    const sec = document.getElementById('view-settings');
    const st = A.settings;
    const head = el('div', { class: 'view-head' }, el('h2', { text: 'Settings' }),
      el('span', { class: 'muted', text: st && st.file ? `run config: ${st.file} · Strata reads it at its next start` : '' }));
    if (!st) { S.fill(sec, head, el('p', { class: 'loading', text: 'Reading the settings…' })); return; }
    const rows = st.rows || [];
    const disagree = rows.filter((r) => layersNote(r, st.file).length);
    const loaded = rows.filter((r) => r.now !== null).length;
    const tiles = el('div', { class: 'tiles' },
      S.tile('settings where the layers disagree', String(disagree.length), '', disagree.map((r) => r.name.replace(/^--/, '')).join(' · ') || 'loaded, next load and file agree', disagree.length ? 'state-worse' : 'state-better'),
      S.tile('read from', { 'profile/settings': 'Strata', metrics: "Strata's /metrics", config: 'the file only', none: 'nowhere' }[st.source] || st.source, '', st.note || (st.loaded ? 'the engine is loaded' : 'the engine is not loaded: no "loaded now" values')),
      S.tile('loaded values known', `${loaded} of ${rows.length}`, '', 'the rest the engine does not report'));
    const cols = [
      { label: 'setting', get: (r) => el('span', { class: 'mono', text: r.name }) },
      { label: 'loaded now', get: (r) => r.now ?? '–' }, { label: 'next load', get: (r) => r.next_load ?? (r.now !== null ? 'the engine picks' : '–') },
      { label: st.file || 'file', get: (r) => r.run_config ?? '–' },
      { label: '', get: (r) => (r.calibrated ? S.chip('calibrated', 'small', 'Tune this PC measures it') : '') },
      { label: 'what it does', get: (r) => {
        const notes = layersNote(r, st.file);
        return el('div', { class: 'about' }, el('span', { text: r.about }), notes.map((n) => el('div', { class: 'disagree', text: `⚠ ${n}` })));
      } },
    ];
    const table = S.card({ q: 'What runs now, what the next load uses, and what the file says', sub: 'a row marked ⚠ has layers that disagree', cls: 'span3' },
      S.table(cols, rows.map((r) => Object.assign({ _cls: layersNote(r, st.file).length ? 'disagree-row' : '' }, r))));
    S.fill(sec, head, tiles, el('div', { class: 'grid' }, table, goBackCard(A)));
    if (my !== token) return;
  }

  S.viewDefs.settings = { render };
})();
