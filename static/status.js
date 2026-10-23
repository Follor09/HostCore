/**
 * PiCore Dashboard - public status page (/status), no login.
 * Shows the services the admin picked, with up/down and (optionally) 30 days of uptime.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const BANNER = { up: 'Alle Systeme laufen', partial: 'Teilweise Störung', down: 'Störung', unknown: 'Status unbekannt', maintenance: 'Wartungsarbeiten' };
  const STATE = { up: 'Läuft', degraded: 'Eingeschränkt', down: 'Ausgefallen', unknown: 'Unbekannt', maintenance: 'Wartung' };
  const fmtWhen = ts => new Date(ts * 1000).toLocaleString(LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const fmtDay = d => new Date(`${d}T12:00:00`).toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit' });
  const fmtPct = v => `${v.toLocaleString(LOCALE, { maximumFractionDigits: 2 })} %`;

  function render(d) {
    document.title = d.title;
    $('sp-title').textContent = d.title;
    $('sp-desc').textContent = d.description;
    const banner = $('sp-banner');
    banner.className = `status-banner ${d.overall}`;
    banner.textContent = t(BANNER[d.overall] || BANNER.unknown);

    const list = $('sp-list');
    list.innerHTML = '';
    list.hidden = !d.items.length;
    d.items.forEach(i => {
      const item = el('div', 'status-item');
      const head = el('div', 'status-item-head');
      const name = el('b', '', i.label);
      name.setAttribute('data-no-i18n', '');
      head.append(el('span', `status-dot ${i.state}`), name);
      if (i.uptime != null) head.appendChild(el('span', 'status-state', fmtPct(i.uptime)));
      head.appendChild(el('span', 'status-state', i.state === 'maintenance' && i.maintenance_until
        ? `${t('Wartung bis')} ${new Date(i.maintenance_until * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' })}`
        : t(STATE[i.state] || STATE.unknown)));
      item.appendChild(head);
      if (d.show_history && i.bars.length) {
        const bars = el('div', 'status-bars');
        i.bars.forEach((b, idx) => {
          const bar = el('i', b == null ? '' : b >= 0.999 ? 'ok' : b >= 0.95 ? 'minor' : 'major');
          bar.title = `${fmtDay(d.days[idx])}: ${b == null ? t('keine Daten') : fmtPct(b * 100)}`;
          bars.appendChild(bar);
        });
        const legend = el('div', 'status-bars-legend');
        legend.append(el('span', '', `${d.days.length} ${t('Tage')}`), el('span', '', t('Heute')));
        item.append(bars, legend);
      }
      list.appendChild(item);
    });
    const planned = $('sp-planned');
    planned.innerHTML = '';
    planned.hidden = !(d.planned && d.planned.length);
    (d.planned || []).forEach(p => {
      const row = el('div', 'status-planned-item');
      const title = el('b', '', p.title);
      title.setAttribute('data-no-i18n', '');
      row.append(el('span', 'status-planned-kicker', t('Geplante Wartung')), title, el('span', '', `${fmtWhen(p.start)} – ${fmtWhen(p.end)}`));
      planned.appendChild(row);
    });
    const inc = $('sp-incidents');
    inc.innerHTML = '';
    inc.hidden = !(d.incidents && d.incidents.length);
    (d.incidents || []).forEach(i => {
      const box = el('div', `status-incident ${i.status === 'behoben' ? 'resolved' : i.severity}`);
      const head = el('div', 'status-incident-head');
      const title = el('b', '', i.title);
      title.setAttribute('data-no-i18n', '');
      head.append(el('span', 'status-planned-kicker', t(i.status === 'behoben' ? 'Behobene Störung' : 'Aktuelle Störung')), title,
        el('span', 'status-incident-state', t(i.status_label)));
      box.appendChild(head);
      i.updates.forEach(u => {
        const line = el('div', 'status-incident-update');
        const text = el('span', '', u.text);
        text.setAttribute('data-no-i18n', '');
        line.append(el('span', 'status-incident-when', `${fmtWhen(u.ts)} · ${t(u.status_label)}`), text);
        box.appendChild(line);
      });
      inc.appendChild(box);
    });
    $('sp-foot').textContent = `${t('Aktualisiert')} ${new Date(d.updated * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' })} · ${t('wird jede Minute neu geladen')}`;
  }

  async function load() {
    try {
      const res = await fetch('/api/public/status', { cache: 'no-store' });
      if (res.status === 404) {
        $('sp-banner').className = 'status-banner';
        $('sp-banner').textContent = t('Diese Statusseite ist ausgeschaltet.');
        $('sp-list').hidden = true;
        $('sp-foot').textContent = '';
        return;
      }
      if (res.ok) render(await res.json());
    } catch (e) {
      $('sp-foot').textContent = t('Keine Verbindung – neuer Versuch in einer Minute.');
    }
  }

  load();
  setInterval(load, 60000);
})();
