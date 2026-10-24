/**
 * HostCore Dashboard - compliance section "Nachweis-Paket": choose a period, build the ZIP for
 * auditors (downloaded straight away), list of earlier exports with their SHA-256.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('ev-form')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 5000);
  }

  const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const fmtDate = ts => new Date(ts * 1000).toLocaleDateString(LOCALE);

  function setRange(kind) {
    const now = new Date();
    if (kind === 'year') {
      const from = new Date(now);
      from.setFullYear(now.getFullYear() - 1);
      $('ev-since').value = iso(from);
      $('ev-until').value = iso(now);
    } else {
      const q = Math.floor(now.getMonth() / 3);
      const start = new Date(now.getFullYear(), (q - 1) * 3, 1);
      const end = new Date(now.getFullYear(), q * 3, 0);
      $('ev-since').value = iso(start);
      $('ev-until').value = iso(end);
    }
  }

  async function loadHistory() {
    const table = $('ev-history');
    table.innerHTML = '';
    let d;
    try { d = await (await fetch('/api/compliance/evidence')).json(); } catch (e) { return; }
    if (!d.exports || !d.exports.length) {
      const tr = el('tr');
      tr.appendChild(el('td', 'empty', t('Noch kein Paket erstellt.')));
      table.appendChild(tr);
      return;
    }
    const head = el('tr');
    ['Erstellt', 'Von', 'Zeitraum', 'Dateien', 'Größe', 'SHA-256'].forEach(h => head.appendChild(el('th', '', t(h))));
    table.appendChild(head);
    d.exports.forEach(x => {
      const tr = el('tr');
      const sha = raw('td', 'mono', x.sha256.slice(0, 16));
      sha.title = x.sha256;
      tr.append(el('td', '', new Date(x.ts * 1000).toLocaleString(LOCALE)), raw('td', '', x.user || '–'), el('td', '', `${fmtDate(x.since)} – ${fmtDate(x.until - 1)}`),
        el('td', '', String(x.files)), el('td', '', `${Math.max(1, Math.round(x.size / 1024))} KB`), sha);
      table.appendChild(tr);
      if (x.problems && x.problems.length) {
        const pr = el('tr');
        const td = el('td', 'field-hint');
        td.colSpan = 6;
        td.textContent = `${t('Nicht enthalten')}: ${x.problems.join('; ')}`;
        pr.appendChild(td);
        table.appendChild(pr);
      }
    });
  }

  document.querySelectorAll('#ev-form [data-range]').forEach(b => b.addEventListener('click', () => setRange(b.dataset.range)));
  $('ev-form').addEventListener('submit', async e => {
    e.preventDefault();
    const since = Math.floor(new Date(`${$('ev-since').value}T00:00:00`).getTime() / 1000);
    const until = Math.floor(new Date(`${$('ev-until').value}T00:00:00`).getTime() / 1000) + 86400;
    const b = $('ev-build');
    b.disabled = true;
    b.textContent = t('Erstelle…');
    try {
      const res = await fetch('/api/compliance/evidence', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ since, until }) });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `Fehler ${res.status}`); }
      const blob = await res.blob();
      const name = ((res.headers.get('content-disposition') || '').match(/filename="([^"]+)"/) || [])[1] || 'nachweise.zip';
      const a = el('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      toast(t('Nachweis-Paket erstellt.'), 'success');
      loadHistory();
    } catch (err) { toast(err.message, 'error'); }
    b.disabled = false;
    b.textContent = t('Paket erstellen');
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('users.manage')) return;
    setRange('quarter');
    loadHistory();
  })();
})();
