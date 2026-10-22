/**
 * PiCore Dashboard - public status page (/status), no login.
 * Shows the services the admin picked, with up/down and (optionally) 30 days of uptime.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const BANNER = { up: 'Alle Systeme laufen', partial: 'Teilweise Störung', down: 'Störung', unknown: 'Status unbekannt' };
  const STATE = { up: 'Läuft', degraded: 'Eingeschränkt', down: 'Ausgefallen', unknown: 'Unbekannt' };

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
      head.appendChild(el('span', 'status-state', t(STATE[i.state] || STATE.unknown)));
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
