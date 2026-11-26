/**
 * HostCore Dashboard - status bar (top) and events card.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);

  const ICONS = {
    good: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    info: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    warning: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
    critical: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/></svg>'
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // Links: pages, or dashboard targets (#service:<id>, #updates, #nvme)
  function follow(link) {
    if (!link) return;
    if (link.startsWith('#service:')) {
      document.querySelector(`.service-tile[data-id="${link.slice(9)}"]`)?.click();
    } else if (link === '#updates') {
      document.getElementById('updates-card')?.click();
    } else if (link === '#nvme') {
      document.getElementById('nvme-history-btn')?.click();
    } else if (link === '#reminders') {
      window.PiPlanner && window.PiPlanner.openReminders();
    } else {
      location.href = link;
    }
  }

  // ---------------------------------------------------------------------------
  // Status bar
  // ---------------------------------------------------------------------------
  const canAck = () => !!(window.PiAuth && window.PiAuth.can && window.PiAuth.can('system.view'));
  const ACK_MODES = [['change', 'Bis sich etwas ändert'], ['24h', '24 Stunden'], ['7d', '7 Tage']];
  let showAcked = false;
  let lastStatus = null;

  async function ackCall(path, body) {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  // Small menu below a check button: for how long the hint counts as seen
  function ackMenu(anchor, body) {
    document.querySelectorAll('.status-ack-menu').forEach(m => m.remove());
    const menu = el('div', 'status-ack-menu');
    menu.appendChild(el('span', 'status-ack-title', t('Auf grün setzen')));
    ACK_MODES.forEach(([mode, label]) => {
      const b = el('button', 'text-btn', t(label));
      b.type = 'button';
      b.addEventListener('click', async e => {
        e.stopPropagation();
        menu.remove();
        try { renderStatus(await ackCall('/api/status-summary/ack', { ...body, mode })); } catch (err) { window.showToast ? window.showToast(err.message, 'error') : alert(err.message); }
      });
      menu.appendChild(b);
    });
    anchor.after(menu);
    setTimeout(() => document.addEventListener('click', function close(ev) {
      if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('click', close); }
    }), 0);
  }

  function renderStatus(s) {
    lastStatus = s;
    const bar = $('status-bar');
    bar.hidden = false;
    bar.className = `status-bar ${s.level}`;
    bar.innerHTML = '';
    const acked = s.acked || [];
    const head = el('span', 'status-head');
    head.innerHTML = ICONS[s.level] || ICONS.info;
    head.appendChild(el('span', '', s.items.length
      ? (s.level === 'critical' ? t('Achtung') : s.level === 'warning' ? t('Bitte ansehen') : t('Hinweise'))
      : t('Alles in Ordnung')));
    bar.appendChild(head);
    const list = el('div', 'status-items');
    if (!s.items.length) {
      list.appendChild(el('span', 'status-note', acked.length
        ? t(`${acked.length} ${acked.length === 1 ? 'Hinweis ist' : 'Hinweise sind'} auf grün gesetzt.`)
        : t('Dienste laufen, Speicher und Temperatur im grünen Bereich.')));
    }
    s.items.forEach(item => {
      const wrap = el('span', 'status-chip-wrap');
      const chip = el(item.link ? 'button' : 'span', `status-chip ${item.level}`);
      chip.innerHTML = ICONS[item.level] || '';
      chip.appendChild(el('span', '', item.text));
      if (item.link) {
        chip.type = 'button';
        chip.addEventListener('click', () => follow(item.link));
      }
      wrap.appendChild(chip);
      if (canAck()) {
        const ok = el('button', 'status-ack');
        ok.type = 'button';
        ok.title = t('Auf grün setzen');
        ok.setAttribute('aria-label', `${t('Auf grün setzen')}: ${item.text}`);
        ok.innerHTML = ICONS.good;
        ok.addEventListener('click', e => { e.stopPropagation(); ackMenu(wrap, { key: item.key }); });
        wrap.appendChild(ok);
      }
      list.appendChild(wrap);
    });
    if (canAck() && s.items.length > 1) {
      const all = el('button', 'status-chip status-ack-all', t('Alle auf grün'));
      all.type = 'button';
      all.addEventListener('click', e => { e.stopPropagation(); ackMenu(all, { all: true }); });
      list.appendChild(all);
    }
    if (acked.length) {
      const toggle = el('button', 'status-chip status-acked-toggle', `${acked.length} ${t('auf grün gesetzt')} ${showAcked ? '▴' : '▾'}`);
      toggle.type = 'button';
      toggle.addEventListener('click', () => { showAcked = !showAcked; renderStatus(lastStatus); });
      list.appendChild(toggle);
    }
    bar.appendChild(list);
    if (showAcked && acked.length) {
      const box = el('div', 'status-acked');
      acked.forEach(a => {
        const row = el('div', 'status-acked-row');
        const until = a.until ? `${t('bis')} ${new Date(a.until * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}` : t('bis sich etwas ändert');
        const text = el('span', '', a.text);
        const meta = el('span', 'status-acked-meta', [a.acked_by, until].filter(Boolean).join(' · '));
        meta.setAttribute('data-no-i18n', '');
        row.append(text, meta);
        if (canAck()) {
          const back = el('button', 'text-btn', t('wieder anzeigen'));
          back.type = 'button';
          back.addEventListener('click', async () => {
            try { renderStatus(await ackCall('/api/status-summary/unack', { key: a.key })); } catch (err) { /* offline */ }
          });
          row.appendChild(back);
        }
        box.appendChild(row);
      });
      bar.appendChild(box);
    }
  }

  async function loadStatus() {
    if (document.hidden) return;
    try {
      const res = await fetch('/api/status-summary');
      if (res.ok) renderStatus(await res.json());
    } catch (e) { /* offline: keep the last state */ }
  }

  // ---------------------------------------------------------------------------
  // Events card
  // ---------------------------------------------------------------------------
  function relTime(ts) {
    const diff = Date.now() / 1000 - ts;
    if (diff < 3600) return `vor ${Math.max(1, Math.round(diff / 60))} Min.`;
    if (diff < 86400) return `vor ${Math.round(diff / 3600)} Std.`;
    return new Date(ts * 1000).toLocaleString(LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function eventItem(e, compact) {
    const li = el('li', `event ${e.level}`);
    const dot = el('span', 'event-icon');
    dot.innerHTML = ICONS[e.level] || ICONS.info;
    const body = el('div', 'event-body');
    const title = el(e.link ? 'a' : 'span', 'event-title', e.title);
    if (e.link && !e.link.startsWith('#')) title.href = e.link;
    body.appendChild(title);
    const meta = el('span', 'event-meta');
    const when = el('time', '', relTime(e.ts));
    when.title = new Date(e.ts * 1000).toLocaleString(LOCALE);
    meta.appendChild(when);
    if (e.detail) {
      meta.appendChild(document.createTextNode(' · '));
      const d = el('span', 'event-detail', e.detail);
      d.setAttribute('data-no-i18n', '');   // user names, IPs, device names
      meta.appendChild(d);
    }
    body.appendChild(meta);
    li.append(dot, body);
    return li;
  }

  async function loadEvents() {
    const list = $('events-list');
    if (!list || document.hidden) return;
    try {
      const res = await fetch('/api/events?days=7');
      if (res.status === 401) {
        list.innerHTML = '';
        list.appendChild(el('li', 'notes-empty', t('Ereignisse sind nur nach der Anmeldung sichtbar.')));
        return;
      }
      const data = await res.json();
      list.innerHTML = '';
      data.events.slice(0, 8).forEach(e => list.appendChild(eventItem(e, true)));
      if (!data.events.length) list.appendChild(el('li', 'notes-empty', t('Keine Ereignisse in den letzten 7 Tagen.')));
      const c = data.counts;
      $('events-sub').textContent = data.total
        ? `${data.total} in 7 Tagen${c.critical ? ` · ${c.critical} kritisch` : ''}${c.warning ? ` · ${c.warning} Warnungen` : ''}`
        : 'Letzte 7 Tage';
    } catch (e) { /* offline */ }
  }

  window.PiOverview = { eventItem, relTime, icons: ICONS };

  if ($('status-bar')) {
    loadStatus();
    setInterval(() => { if (!document.hidden) loadStatus(); }, 30 * 1000);
  }
  if ($('events-list')) {
    loadEvents();
    setInterval(() => { if (!document.hidden) loadEvents(); }, 60 * 1000);
  }
})();
