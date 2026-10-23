/**
 * PiCore Dashboard - settings section "Datenaufbewahrung": limits per kind of data and the space each store takes.
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
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  function msg(text, level) {
    const m = $('ret-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  function fmtBytes(b) {
    if (!b) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
    return `${(b / 1024 ** i).toLocaleString(LOCALE, { maximumFractionDigits: i ? 1 : 0 })} ${u[i]}`;
  }

  function render(d) {
    const box = $('ret-items');
    box.innerHTML = '';
    d.items.forEach(it => {
      const row = el('label', 'retention-item');
      const text = el('div', 'retention-text');
      text.append(el('span', 'retention-label', t(it.label)), el('span', 'field-hint', t(it.description)));
      const ctl = el('div', 'retention-ctl');
      const inp = el('input');
      inp.type = 'number';
      inp.min = it.min;
      inp.max = it.max;
      inp.value = it.value;
      inp.dataset.key = it.key;
      ctl.append(inp, el('span', 'retention-unit', t(it.unit === 'days' ? 'Tage' : 'Einträge')));
      if (it.value !== it.default) {
        const def = el('span', 'field-hint', `${t('Standard')}: ${it.default}`);
        def.setAttribute('data-no-i18n', '');
        ctl.appendChild(def);
      }
      row.append(text, ctl);
      box.appendChild(row);
    });
    const table = $('ret-stores');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Ablage', 'Inhalt', 'Größe'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    d.stores.forEach(s => {
      const r = body.insertRow();
      const name = r.insertCell();
      name.appendChild(el('span', '', t(s.label)));
      const file = el('span', 'ctr-sub mono', s.file);
      file.setAttribute('data-no-i18n', '');
      name.appendChild(file);
      const c = r.insertCell();
      if (s.description) c.appendChild(el('span', 'field-hint', t(s.description)));
      if (s.tables && s.tables.length) {
        const list = el('span', 'ctr-sub mono', s.tables.filter(x => x.rows).map(x => `${x.name}: ${x.rows.toLocaleString(LOCALE)}`).join(' · '));
        list.setAttribute('data-no-i18n', '');
        c.appendChild(list);
      }
      const size = r.insertCell();
      size.className = 'mono';
      size.textContent = fmtBytes(s.bytes);
    });
    $('ret-total').textContent = `${t('Zusammen')}: ${fmtBytes(d.total_bytes)}`;
  }

  async function init() {
    if (!$('ret-form')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('settings.manage')) return;
    try { render(await api('/api/settings/retention')); } catch (e) { msg(e.message, 'error'); }
    $('ret-form').addEventListener('submit', async e => {
      e.preventDefault();
      const values = {};
      $('ret-items').querySelectorAll('input[data-key]').forEach(i => { values[i.dataset.key] = Number(i.value); });
      try {
        render(await api('/api/settings/retention', values));
        msg(t('Gespeichert – kürzere Zeiträume gelten ab dem nächsten Aufräumen (spätestens in einer Stunde).'), 'success');
      } catch (err) { msg(err.message, 'error'); }
    });
    $('ret-cleanup').addEventListener('click', async () => {
      const b = $('ret-cleanup');
      b.disabled = true;
      msg(t('Räume auf…'));
      try {
        render(await api('/api/settings/retention/cleanup', {}));
        msg(t('Aufgeräumt.'), 'success');
      } catch (err) { msg(err.message, 'error'); } finally { b.disabled = false; }
    });
  }

  init();
})();
