/**
 * PiCore Dashboard - fleet page (/flotte): this Pi and all remote Pis in one table,
 * apt update / upgrade for several of them at once.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const selected = new Set();
  let data = null;
  let timer = null;

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
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (res.status === 401) { location.href = '/login'; throw new Error('Nicht angemeldet.'); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const num = (v, unit, digits = 0) => (v == null ? '–' : `${Number(v).toLocaleString(LOCALE, { maximumFractionDigits: digits })}${unit}`);

  function meter(value, unit, warn, crit, max = 100) {
    const c = el('td', 'fleet-meter');
    if (value == null) { c.textContent = '–'; return c; }
    const level = value >= crit ? 'danger' : value >= warn ? 'warn' : 'fill-emerald';
    const bar = el('div', 'progress-bar-bg mini');
    const fill = el('div', `progress-bar-fill ${level}`);
    fill.style.width = `${Math.min(100, (value / max) * 100)}%`;
    bar.appendChild(fill);
    c.append(el('span', 'fleet-value', num(value, unit, 1)), bar);
    return c;
  }

  function updateSelection() {
    const n = selected.size;
    $('fleet-selected').textContent = n ? t(`${n} ${n === 1 ? 'Pi' : 'Pis'} ausgewählt`) : t('Keine Pis ausgewählt');
    $('fleet-update').disabled = !n;
    $('fleet-upgrade').disabled = !n;
  }

  function render() {
    const table = $('fleet-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    const all = el('input');
    all.type = 'checkbox';
    all.setAttribute('aria-label', t('Alle auswählen'));
    const online = data.pis.filter(p => p.online);
    all.checked = online.length > 0 && online.every(p => selected.has(p.id));
    all.addEventListener('change', () => { online.forEach(p => (all.checked ? selected.add(p.id) : selected.delete(p.id))); render(); });
    const th0 = el('th');
    if (can('updates.install')) th0.appendChild(all);
    head.appendChild(th0);
    ['Pi', 'Status', 'Version', 'Updates', 'Temperatur', 'CPU', 'RAM', 'Speicher', 'Laufzeit'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    data.pis.forEach(p => {
      const row = body.insertRow();
      const c0 = row.insertCell();
      if (can('updates.install') && p.online) {
        const box = el('input');
        box.type = 'checkbox';
        box.checked = selected.has(p.id);
        box.setAttribute('aria-label', p.name);
        box.addEventListener('change', () => { box.checked ? selected.add(p.id) : selected.delete(p.id); updateSelection(); });
        c0.appendChild(box);
      }
      const name = row.insertCell();
      name.className = 'wrap';
      const n = el('span', 'lan-name', p.name);
      n.setAttribute('data-no-i18n', '');
      const sub = el('span', 'lan-sub', [p.model, p.url ? p.url.replace(/^https?:\/\//, '') : t('Dieser Pi')].filter(Boolean).join(' · '));
      sub.setAttribute('data-no-i18n', '');
      name.append(n, sub);
      const st = row.insertCell();
      st.appendChild(el('span', `health-tag ${p.online ? 'good' : 'critical'}`, p.online ? t('Online') : t('Nicht erreichbar')));
      if (p.apt_running) { st.append(' '); st.appendChild(el('span', 'health-tag warning', t(p.apt_mode === 'update' ? 'Paketlisten laden…' : 'Upgrade läuft…'))); }
      if (!p.online) { row.classList.add('offline'); ['', '', '', '', '', '', ''].forEach(() => row.insertCell().textContent = '–'); return; }
      const ver = row.insertCell();
      ver.appendChild(el('code', 'fleet-commit', p.commit || '–'));
      if (p.update_available) { ver.append(' '); ver.appendChild(el('span', 'health-tag warning', t('Update verfügbar'))); }
      const upd = row.insertCell();
      if (p.updates == null) upd.textContent = '–';
      else {
        upd.appendChild(el('span', `health-tag ${p.security ? 'critical' : p.updates ? 'warning' : 'good'}`,
          p.updates ? `${p.updates} ${t('Pakete')}${p.security ? ` · ${p.security} ${t('Sicherheit')}` : ''}` : t('Aktuell')));
        if (p.reboot_required) { upd.append(' '); upd.appendChild(el('span', 'health-tag warning', t('Neustart nötig'))); }
      }
      row.appendChild(meter(p.temp, ' °C', 65, 75, 85));
      row.appendChild(meter(p.cpu, ' %', 75, 90));
      row.appendChild(meter(p.ram, ' %', 80, 92));
      row.appendChild(meter(p.disk, ' %', 80, 92));
      row.insertCell().textContent = p.uptime || '–';
    });
    $('fleet-checked').textContent = t(`Stand ${new Date(data.checked * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`);
    [...selected].forEach(id => { if (!data.pis.some(p => p.id === id && p.online)) selected.delete(id); });
    updateSelection();
  }

  async function load() {
    $('fleet-reload').disabled = true;
    try {
      data = await api('/api/fleet');
      render();
    } catch (err) { toast(err.message, 'error'); }
    finally { $('fleet-reload').disabled = false; }
  }

  async function runApt(mode, btn) {
    if (!btn.classList.contains('confirming')) {
      btn.classList.add('confirming');
      const label = btn.textContent;
      btn.dataset.label = label;
      btn.textContent = t(`Auf ${selected.size} ${selected.size === 1 ? 'Pi' : 'Pis'} starten?`);
      setTimeout(() => { btn.classList.remove('confirming'); btn.textContent = btn.dataset.label; }, 4000);
      return;
    }
    btn.classList.remove('confirming');
    btn.textContent = btn.dataset.label;
    try {
      const r = await api('/api/fleet/apt', { ids: [...selected], mode });
      const box = $('fleet-results');
      box.innerHTML = '';
      r.results.forEach(x => {
        const p = data.pis.find(q => q.id === x.id);
        const line = el('div', 'fleet-result');
        line.append(el('span', `health-tag ${x.ok ? 'good' : 'critical'}`, x.ok ? t('Gestartet') : t('Fehler')),
          el('strong', '', p ? p.name : x.id), el('span', 'lan-sub', x.message));
        line.querySelector('strong').setAttribute('data-no-i18n', '');
        box.appendChild(line);
      });
      setTimeout(load, 2000);
    } catch (err) { toast(err.message, 'error'); }
  }

  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = '/login'; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }
    $('fleet-reload').addEventListener('click', load);
    $('fleet-update').addEventListener('click', e => runApt('update', e.currentTarget));
    $('fleet-upgrade').addEventListener('click', e => runApt('upgrade', e.currentTarget));
    load();
    timer = setInterval(() => { if (!document.hidden) load(); }, 30000);
  }

  init();
})();
