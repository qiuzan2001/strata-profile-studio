/* Strata Profile Studio: the Share dialog of a run.  It shows exactly the card and the JSON that would leave the PC,
   with the list of what is not in them.  In this version only "the files on this PC" works: the two destinations
   that send the card stay disabled until the cards site is up.  Nothing leaves the PC. */
'use strict';

(function () {
  const { el } = S;
  const NAME_RE = /^[A-Za-z0-9 _-]{0,12}$/;
  const LATER = 'available when the cards site is up';

  S.shareRun = async function shareRun(A, id) {
    if (A.readOnly()) return;
    const dlg = document.getElementById('dialog');
    const name = S.runName(id, A.reports);
    const img = el('img', { class: 'share-img', alt: `The card of ${name}, 1200 x 630` });
    const imgNote = el('p', { class: 'muted small', text: '' });
    const pre = el('pre', { class: 'share-json', text: 'Making the card ...' });
    const size = el('h3', { text: 'The JSON' });
    const err = el('p', { class: 'ctl-err', text: '' });
    const result = el('p', { class: 'share-result', text: '' });
    const input = el('input', { type: 'text', class: 'ctl-text', maxlength: 12, placeholder: 'no name', 'aria-label': 'Name on the card' });
    const dest = (value, label, enabled) => {
      const r = el('input', { type: 'radio', name: 'share-dest', value });
      r.checked = value === 'files';
      r.disabled = !enabled;
      return el('label', { class: `share-dest ${enabled ? '' : 'off'}` }, r, ' ', label, enabled ? null : el('small', { text: ` (${LATER})` }));
    };
    const write = S.button('Write the files', async () => {
      if (!NAME_RE.test(input.value)) { S.setText(err, 'The name has at most 12 characters: letters, digits, space, - and _.'); return; }
      write.disabled = true;
      S.setText(result, 'Writing the files ...');
      try {
        const r = await S.api('/api/card/files', { body: { report: id, name: input.value.trim() } });
        S.setText(result, `The card files are in ${r.dir}: ${r.files.join(', ')}.${r.png ? '' : ` ${r.renderer}`} Nothing left the PC.`);
      } catch (e) {
        S.setText(result, '');
        S.setText(err, e.message);
      } finally {
        write.disabled = false;
      }
    }, 'primary');
    S.fill(dlg,
      el('h2', { text: `Share ${name}` }),
      el('p', { class: 'muted small', text: 'Only this card and this JSON can leave the PC. In this version, they stay on this PC as files.' }),
      el('div', { class: 'share-grid' },
        el('div', null, img, imgNote,
          el('h3', { text: 'Not in the card or the JSON' }),
          el('ul', { class: 'share-excl', text: '' }),
          el('h3', { text: 'Name on the card' }),
          el('div', { class: 'share-name' }, input, el('small', { class: 'muted', text: 'optional · at most 12 characters: letters, digits, space, - and _' })),
          el('h3', { text: 'Destination' }),
          dest('gh', 'A GitHub issue in the cards repository, with gh', false),
          dest('form', 'A link that opens the filled issue form in the browser', false),
          dest('files', 'Only the files on this PC: card.html, card.svg, card.json and card.png', true),
          err, result),
        el('div', null, size, pre)),
      el('div', { class: 'dlg-buttons' }, S.button('Close', () => dlg.close()), write));
    dlg.classList.add('share');
    dlg.addEventListener('close', () => dlg.classList.remove('share'), { once: true });
    dlg.showModal();
    const excl = dlg.querySelector('.share-excl');
    const show = (doc, withImage) => {
      S.setText(size, `The JSON (${S.int(doc.bytes)} bytes)`);
      S.setText(pre, JSON.stringify(doc.card, null, 1));
      if (withImage && doc.image) {
        img.src = `data:image/${doc.image.type === 'png' ? 'png' : 'svg+xml'};base64,${doc.image.data}`;
        S.setText(imgNote, doc.image.type === 'png' ? '' : doc.renderer);
      }
      if (!excl.childElementCount) for (const x of doc.excluded) excl.appendChild(el('li', { text: x }));
    };
    try {
      show(await S.api(`/api/card?report=${encodeURIComponent(id)}`), true);
    } catch (e) {
      S.setText(pre, '');
      S.setText(err, e.message);
      write.disabled = true;
    }
    let timer = null;
    input.addEventListener('input', () => {
      clearTimeout(timer);
      if (!NAME_RE.test(input.value)) { S.setText(err, 'The name has at most 12 characters: letters, digits, space, - and _.'); return; }
      S.setText(err, '');
      timer = setTimeout(async () => {
        try { show(await S.api(`/api/card?report=${encodeURIComponent(id)}&name=${encodeURIComponent(input.value.trim())}&image=0`), false); } catch (e) { S.setText(err, e.message); }
      }, 250);
    });
  };
})();
