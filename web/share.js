/* Strata Profile Studio: the Share dialog of a run.  It shows exactly the card and the JSON that can leave the PC,
   with the list of what is not in them, and three destinations:
     1. a GitHub issue in the cards repository, with gh on this PC (when gh is logged in);
     2. a link that opens the filled issue form in the browser (at most 8,000 characters);
     3. only the files on this PC.
   Nothing leaves the PC before the main button.  The check of the cards site answers in the issue with the card's
   address: for destination 1 the Studio reads that answer and shows the address. */
'use strict';

(function () {
  const { el } = S;
  const NAME_RE = /^[A-Za-z0-9 _-]{0,12}$/;
  const NAME_RULE = 'The name has at most 12 characters: letters, digits, space, - and _.';
  const link = (href, text) => el('a', { href, target: '_blank', rel: 'noopener', text: text || href });

  S.shareRun = async function shareRun(A, id) {
    if (A.readOnly()) return;
    const dlg = document.getElementById('dialog');
    const name = S.runName(id, A.reports);
    const img = el('img', { class: 'share-img', alt: `The card of ${name}, 1200 x 630` });
    const imgNote = el('p', { class: 'muted small', text: '' });
    const pre = el('pre', { class: 'share-json', text: 'Making the card ...' });
    const size = el('h3', { text: 'The JSON' });
    const err = el('p', { class: 'ctl-err', text: '' });
    const result = el('div', { class: 'share-result' });
    const input = el('input', { type: 'text', class: 'ctl-text', maxlength: 12, placeholder: 'no name', 'aria-label': 'Name on the card' });
    let targets = null, dest = 'files', busy = false, shownId = null;
    const dests = {};
    const mkDest = (value, label) => {
      const r = el('input', { type: 'radio', name: 'share-dest', value });
      r.checked = value === dest;
      r.addEventListener('change', () => { if (r.checked) { dest = value; update(); } });
      const note = el('small', { class: 'share-why', text: '' });
      const text = el('span', { text: label });
      const row = el('label', { class: 'share-dest' }, r, ' ', text, note);
      dests[value] = { r, note, row, text };
      return row;
    };
    const go = S.button('Write the files', () => act(), 'primary');
    const say = (...nodes) => S.fill(result, nodes.map((n) => (typeof n === 'string' ? el('span', { text: n }) : n)));

    function update() {
      const t = targets || {};
      const ghOk = !!(t.gh && t.gh.ok), linkOk = !!(t.link && t.link.url);
      const set = (k, ok, why) => {
        const d = dests[k];
        d.r.disabled = !ok;
        S.setClass(d.row, 'off', !ok);
        S.setText(d.note, ok ? '' : ` (${why})`);
      };
      set('gh', ghOk, targets ? (t.gh && t.gh.reason) || 'gh is not ready' : 'asking gh ...');
      set('form', linkOk, targets ? (t.link && t.link.reason) || 'no link' : 'making the link ...');
      if ((dest === 'gh' && !ghOk) || (dest === 'form' && !linkOk)) { dest = 'files'; dests.files.r.checked = true; }
      S.setText(go, dest === 'gh' ? 'Share this card on GitHub' : dest === 'form' ? 'Open the filled form' : 'Write the files');
      go.disabled = busy;
    }

    async function act() {
      if (!NAME_RE.test(input.value)) { S.setText(err, NAME_RULE); return; }
      S.setText(err, '');
      const body = { report: id, name: input.value.trim() };
      if (dest === 'files') {
        busy = true; update();
        say('Writing the files ...');
        try {
          const r = await S.api('/api/card/files', { body });
          say(`The card files are in ${r.dir}: ${r.files.join(', ')}.${r.png ? '' : ` ${r.renderer}`} Nothing left the PC.`);
        } catch (e) { say(''); S.setText(err, e.message); }
        busy = false; update();
      } else if (dest === 'form') {
        window.open(targets.link.url, '_blank', 'noopener');
        say('The issue form is open in a new tab. Read it, then select Create. ',
          `The card address comes in the issue after about one minute. All cards: `, link(targets.site));
      } else {
        busy = true; update();
        say(`Opening an issue in ${targets.repo} ...`);
        try {
          await S.api('/api/share/gh', { body: Object.assign({ id: shownId }, body) });
          await follow();
        } catch (e) { say(''); S.setText(err, e.message); }
        busy = false; update();
      }
    }

    async function follow() {                            // the gh share: the issue, then the check's answer
      for (;;) {
        let j;
        try { j = await S.api('/api/share/gh'); } catch (e) { S.setText(err, e.message); return; }
        const issue = j.issue_url ? link(j.issue_url, `the issue ${j.issue_url.split('/').pop()}`) : null;
        if (j.state === 'done') { say('The card is on the site: ', link(j.card_url), '. Issue: ', issue); return; }
        if (j.state === 'timeout') { say(`${j.message} `, issue); return; }
        if (j.state === 'refused') { say('The cards site did not add the card. Its answer is in ', issue, `: ${j.message}`); return; }
        if (j.state === 'failed') { say(''); S.setText(err, j.message); return; }
        say(`${j.message} `, issue);
        await new Promise((r) => setTimeout(r, 2000));
      }
    }

    S.fill(dlg,
      el('h2', { text: `Share ${name}` }),
      el('p', { class: 'muted small', text: 'Only this card and this JSON can leave the PC, and only when you select the button below.' }),
      el('div', { class: 'share-grid' },
        el('div', null, img, imgNote,
          el('h3', { text: 'Not in the card or the JSON' }),
          el('ul', { class: 'share-excl', text: '' }),
          el('h3', { text: 'Name on the card' }),
          el('div', { class: 'share-name' }, input, el('small', { class: 'muted', text: 'optional · at most 12 characters: letters, digits, space, - and _' })),
          el('h3', { text: 'Destination' }),
          mkDest('gh', 'A GitHub issue in the cards repository, with gh'),
          mkDest('form', 'A link that opens the filled issue form in the browser'),
          mkDest('files', 'Only the files on this PC: card.html, card.svg, card.json and card.png'),
          err, result),
        el('div', null, size, pre)),
      el('div', { class: 'dlg-buttons' }, S.button('Close', () => dlg.close()), go));
    update();
    dlg.classList.add('share');
    dlg.addEventListener('close', () => dlg.classList.remove('share'), { once: true });
    dlg.showModal();
    const excl = dlg.querySelector('.share-excl');
    const show = (doc, withImage) => {
      S.setText(size, `The JSON (${S.int(doc.bytes)} bytes)`);
      S.setText(pre, JSON.stringify(doc.card, null, 1));
      shownId = doc.card.id;
      if (withImage && doc.image) {
        img.src = `data:image/${doc.image.type === 'png' ? 'png' : 'svg+xml'};base64,${doc.image.data}`;
        S.setText(imgNote, doc.image.type === 'png' ? '' : doc.renderer);
      }
      if (!excl.childElementCount) for (const x of doc.excluded) excl.appendChild(el('li', { text: x }));
    };
    const loadTargets = async () => {
      try { targets = await S.api(`/api/share/targets?report=${encodeURIComponent(id)}&name=${encodeURIComponent(input.value.trim())}`); }
      catch (e) { targets = { gh: { ok: false, reason: e.message }, link: { url: null, reason: e.message } }; }
      if (targets.repo) S.setText(dests.gh.text, `A GitHub issue in ${targets.repo}, with gh`);
      update();
    };
    try {
      show(await S.api(`/api/card?report=${encodeURIComponent(id)}`), true);
      await loadTargets();
    } catch (e) {
      S.setText(pre, '');
      S.setText(err, e.message);
      go.disabled = true;
    }
    let timer = null;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      if (!NAME_RE.test(input.value)) { S.setText(err, NAME_RULE); return; }
      S.setText(err, '');
      timer = setTimeout(async () => {
        try { show(await S.api(`/api/card?report=${encodeURIComponent(id)}&name=${encodeURIComponent(input.value.trim())}&image=0`), false); await loadTargets(); } catch (e) { S.setText(err, e.message); }
      }, 250);
    });
  };
})();
