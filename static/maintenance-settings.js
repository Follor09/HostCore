/**
 * HostCore Dashboard - settings for maintenance windows: plan a window (all services or
 * chosen ones), start one right away, end or cancel it, see past windows.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('mw-form')) return;
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const KIND = { service: 'Dienst', health: 'Web-Dienst', stack: 'Compose-Stack' };
  const STATE = { active: ['warning', 'Läuft'], planned: ['', 'Geplant'], past: ['', 'Beendet'] };
  let data = null;
  let mode = 'all';

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const n = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  const fmt = ts => new Date(ts * 1000).toLocaleString(LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const toLocalInput = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const fromInput = v => Math.round(new Date(v).getTime() / 1000);

  function renderScope() {
    const box = $('mw-scope');
    const checked = new Set([...box.querySelectorAll('input:checked')].map(i => i.value));
    box.innerHTML = '';
    data.available.forEach(a => {
      const row = el('label', 'sp-item mw-item');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = a.id;
      cb.checked = checked.has(a.id);
      const name = el('span');
      const n = el('b', '', a.name);
      n.setAttribute('data-no-i18n', '');
      name.append(n, el('small', '', ` · ${t(KIND[a.kind] || a.kind)}`));
      row.append(cb, name);
      box.appendChild(row);
    });
  }

  function renderList() {
    const list = $('mw-list');
    list.innerHTML = '';
    if (!data.windows.length) { list.appendChild(el('p', 'field-hint', t('Noch keine Wartungsfenster.'))); return; }
    const table = el('table', 'data-table');
    const head = table.createTHead().insertRow();
    ['Zeitraum', 'Titel', 'Betroffen', 'Status', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    data.windows.slice(0, 20).forEach(w => {
      const row = body.insertRow();
      row.insertCell().textContent = `${fmt(w.start)} – ${fmt(w.end)}`;
      const title = row.insertCell();
      const b = el('b', '', w.title);
      b.setAttribute('data-no-i18n', '');
      title.appendChild(b);
      if (w.note) title.appendChild(el('div', 'field-hint', w.note));
      const scope = row.insertCell();
      scope.textContent = w.scope === 'all' ? t('Alle Dienste') : w.scope_names.join(', ');
      const [level, label] = STATE[w.state];
      row.insertCell().appendChild(el('span', `health-tag ${level}`, t(label)));
      const act = row.insertCell();
      act.className = 'row-action';
      if (w.state !== 'past' && can('settings.manage')) {
        const btn = el('button', 'btn-secondary', w.state === 'active' ? t('Jetzt beenden') : t('Absagen'));
        btn.type = 'button';
        btn.addEventListener('click', async () => {
          try { data = await api(`/api/maintenance/${w.id}/end`, {}); render(); toast(t('Wartungsfenster aktualisiert.'), 'success'); } catch (err) { toast(err.message, 'error'); }
        });
        act.appendChild(btn);
      }
    });
    const wrap = el('div', 'table-scroll');
    wrap.appendChild(table);
    list.appendChild(wrap);
  }

  function render() {
    renderScope();
    renderList();
  }

  async function submit(start, end, title) {
    const scope = mode === 'all' ? 'all' : [...$('mw-scope').querySelectorAll('input:checked')].map(i => i.value);
    try {
      data = await api('/api/maintenance', { title, start, end, scope, note: $('mw-note').value });
      render();
      toast(t('Wartungsfenster gespeichert.'), 'success');
      $('mw-title').value = '';
      $('mw-note').value = '';
    } catch (err) { toast(err.message, 'error'); }
  }

  function resetTimes() {
    const start = new Date();
    start.setMinutes(0, 0, 0);
    start.setHours(start.getHours() + 1);
    $('mw-start').value = toLocalInput(start);
    $('mw-end').value = toLocalInput(new Date(start.getTime() + 3600000));
  }

  document.querySelectorAll('#mw-scope-mode button').forEach(b => b.addEventListener('click', () => {
    mode = b.dataset.mode;
    document.querySelectorAll('#mw-scope-mode button').forEach(x => x.classList.toggle('active', x === b));
    $('mw-scope').hidden = mode === 'all';
  }));
  $('mw-form').addEventListener('submit', e => {
    e.preventDefault();
    submit(fromInput($('mw-start').value), fromInput($('mw-end').value), $('mw-title').value);
  });
  $('mw-now').addEventListener('click', () => {
    const now = Math.round(Date.now() / 1000);
    submit(now, now + 3600, $('mw-title').value.trim() || t('Wartung'));
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (!can('settings.manage')) return;
    resetTimes();
    try { data = await api('/api/maintenance'); render(); } catch (e) { /* offline */ }
  })();
})();
