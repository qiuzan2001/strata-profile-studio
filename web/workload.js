/* Strata Profile Studio: the workload pane of the Tune view.  What the requests of this PC look like (read from the
   engine log) and ideas for them.  The tune card (mytune.js) sits in the same pane.  The counts only read: they write
   no file. */
'use strict';

(function () {
  const { el, isNum } = S;
  const opts = { last: 100, minPrompt: 2000 };
  let doc = null, error = null, loading = false, host = null;

  async function load(A) {
    if (loading) return;
    loading = true;
    try {
      doc = await S.api(`/api/workload?last=${opts.last}&min_prompt=${opts.minPrompt}`);
      error = null;
    } catch (e) {
      doc = null;
      error = e.message;
    } finally {
      loading = false;
      paint(A);
    }
  }

  const tok = (x) => (isNum(x) ? `${S.int(x)} tokens` : '–');

  function splitBar(share, plain) {
    if (!isNum(share)) return el('span', { class: 'muted small', text: 'not available' });
    const a = Math.round(share * 100), b = 100 - a;
    return el('div', { class: 'split', role: 'img', 'aria-label': `Prompt read ${a} percent, reply ${b} percent` },
      el('span', { class: 'split-a', style: { width: `${a}%` }, text: !plain && a >= 30 ? `Prompt ${a}%` : '' }),
      el('span', { class: 'split-b', style: { width: `${b}%` }, text: !plain && b >= 30 ? `Reply ${b}%` : '' }));
  }

  function controls(A) {
    return el('div', { class: 'view-head' }, el('h3', { text: 'Your workload' }),
      S.seg([[50, 'Last 50'], [100, 'Last 100'], [300, 'Last 300']], opts.last, (v) => { opts.last = v; load(A); }, 'Number of requests'),
      S.seg([[0, 'All requests'], [2000, 'Prompts of 2,000 tokens or more']], opts.minPrompt, (v) => { opts.minPrompt = v; load(A); }, 'Which requests'),
      S.button('Refresh', () => load(A), 'small'));
  }

  function tiles(m) {
    return el('div', { class: 'tiles' },
      S.tile('Requests counted', S.int(m.requests), '', 'from the engine log'),
      S.tile('Prompt', S.int(m.prompt_tokens.median), 'tokens', `median · 90% are below ${S.int(m.prompt_tokens.p90)}`),
      S.tile('Reused prompt', S.pct(m.reused), '', 'the share that an earlier request already read'),
      S.tile('Reply', S.int(m.generated.median), 'tokens', `median · 90% are below ${S.int(m.generated.p90)} · thinking is included`),
      S.tile('Prompt read speed', S.tps(m.prompt_tok_s), 'tok/s', 'new tokens only'),
      S.tile('Reply speed', S.tps(m.decode_tok_s), 'tok/s', isNum(m.drafts_accepted) ? `${S.pct(m.drafts_accepted)} of the drafts accepted` : 'no draft counted'));
  }

  function timeCard(m) {
    const verdict = isNum(m.prompt_share)
      ? (m.prompt_share >= 0.5 ? 'The prompt read takes most of the time.' : 'The reply takes most of the time.')
      : '';
    return S.card({ q: 'Where does the time go?', sub: `${S.num(m.prompt_s, 0)} s in the prompt read and ${S.num(m.decode_s, 0)} s in the reply, for ${S.int(m.requests)} requests`, verdict },
      splitBar(m.prompt_share));
  }

  function leverCard(d) {
    if (!d.levers.length) return null;
    const rows = d.levers.map((l) => ({ kind: l.kind, text: l.text, basis: l.basis }));
    const cols = [
      { label: 'Kind', get: (r) => S.chip(r.kind, r.kind === 'changes output' ? 'warn' : ''), title: 'Same output: the answer does not change. Changes output: check the results. Client: change the program that sends the requests.' },
      { label: 'Idea', key: 'text' },
      { label: 'Based on', key: 'basis' },
    ];
    return S.card({ q: 'Ideas for this workload', sub: 'These are ideas. None of them is a measured gain on this PC.' }, S.table(cols, rows));
  }

  function paint(A) {
    if (!host || !host.isConnected) return;
    const body = [controls(A)];
    if (error) {
      body.push(el('p', { class: 'note', text: error }));
    } else if (!doc) {
      body.push(el('p', { class: 'note', text: 'Reading the engine log…' }));
    } else if (!doc.mine) {
      body.push(el('p', { class: 'note', text: doc.note || 'No request is available.' }), S.mytune.card(A));
    } else {
      body.push(tiles(doc.mine), timeCard(doc.mine), S.mytune.card(A), leverCard(doc));
      body.push(el('p', { class: 'note', text: `The log holds ${S.int(doc.found)} requests in its last 8 MiB. The log may include requests from the profile and the tune.` }));
    }
    S.fill(host, ...body);
  }

  // the Tune view calls this with the node to fill
  S.workload = {
    mount(A, node) {
      host = node;
      if (doc === null && error === null && !loading) load(A);
      else paint(A);
    },
  };
})();
