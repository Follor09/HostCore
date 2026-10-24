/**
 * HostCore Dashboard - settings: exchange links (a public upload and/or download link for a folder).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    return data;
  }

  function msg(text, level) {
    const m = $('exchange-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  const fmtSize = b => (b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GB` : b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${Math.ceil(b / 1024)} KB`);

  async function copy(url) {
    try { await navigator.clipboard.writeText(url); msg(t('Link kopiert.'), 'success'); } catch (e) { window.prompt(t('Link:'), url); }
  }

  function render(links) {
    const list = $('exchange-list');
    list.innerHTML = '';
    if (!links.length) list.appendChild(el('p', 'field-hint', t('Keine aktiven Austausch-Links.')));
    links.forEach(x => {
      const url = `${location.origin}/x/${x.token}`;
      const until = new Date(x.expires * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      const item = el('div', 'wol-item');
      const main = el('div', 'workload-main');
      const name = el('span', 'workload-name', x.folder);
      name.setAttribute('data-no-i18n', '');
      const parts = [t(x.mode_label), `${t('gültig bis')} ${until}`];
      if (x.mode !== 'download') parts.push(`${fmtSize(x.used_bytes)} / ${fmtSize(x.max_bytes)} ${t('hochgeladen')}`);
      if (x.mode !== 'upload') parts.push(`${x.downloads}× ${t('geladen')}`);
      const sub = el('span', 'workload-sub', parts.join(' · '));
      sub.setAttribute('data-no-i18n', '');  // translated piece by piece above
      main.append(name, sub);
      if (x.note) { const n = el('span', 'workload-sub', x.note); n.setAttribute('data-no-i18n', ''); main.appendChild(n); }
      (x.uploads || []).slice(-3).reverse().forEach(u => {
        const line = el('span', 'workload-sub', `↑ ${u.name} · ${fmtSize(u.size)} · ${new Date(u.ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`);
        line.setAttribute('data-no-i18n', '');
        main.appendChild(line);
      });
      const actions = el('div', 'item-actions');
      const cp = el('button', 'btn-secondary', t('Link kopieren'));
      cp.type = 'button';
      cp.addEventListener('click', () => copy(url));
      const open = el('a', 'btn-secondary', t('Öffnen'));
      open.href = url;
      open.target = '_blank';
      open.rel = 'noopener';
      const end = el('button', 'btn-danger', t('Beenden'));
      end.type = 'button';
      end.addEventListener('click', async () => {
        if (!end.classList.contains('confirming')) {
          end.classList.add('confirming');
          end.textContent = t('Sicher?');
          setTimeout(() => { end.classList.remove('confirming'); end.textContent = t('Beenden'); }, 4000);
          return;
        }
        try { render((await api(`/api/exchange/${x.token}/delete`, {})).links); } catch (err) { msg(err.message, 'error'); }
      });
      actions.append(cp, open, end);
      item.append(main, actions);
      list.appendChild(item);
    });
  }

  async function load() {
    try { render((await api('/api/exchange')).links); } catch (e) { msg(e.message, 'error'); }
  }

  async function init() {
    if (!$('exchange-form')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('files.share')) return;
    // Upload links need "Dateien hochladen" as well
    if (window.PiAuth && !window.PiAuth.can('files.write')) {
      $('ex-mode').value = 'download';
      [...$('ex-mode').options].forEach(o => { o.disabled = o.value !== 'download'; });
    }
    const syncMax = () => { $('ex-max').disabled = $('ex-mode').value === 'download'; };
    $('ex-mode').addEventListener('change', syncMax);
    syncMax();
    load();
    $('exchange-form').addEventListener('submit', async e => {
      e.preventDefault();
      msg(t('Erstelle…'));
      try {
        const x = await api('/api/exchange', { folder: $('ex-folder').value.trim(), mode: $('ex-mode').value,
                                                hours: +$('ex-hours').value, max_mb: +$('ex-max').value || 500, note: $('ex-note').value.trim() });
        await copy(`${location.origin}/x/${x.token}`);
        msg(t('Link erstellt und kopiert.'), 'success');
        $('ex-note').value = '';
        load();
      } catch (err) { msg(err.message, 'error'); }
    });
  }

  init();
})();
