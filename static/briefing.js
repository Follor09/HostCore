/**
 * PiCore Dashboard - night summary card: what happened between 22:00 and 07:00
 * (status level, one sentence, the notable events) and what is due today.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const card = $('briefing-card');
  if (!card) return;
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
  const LEVEL_TAG = { good: 'good', warning: 'warning', critical: 'critical', info: '' };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function render(b) {
    const tag = $('briefing-level');
    tag.className = `health-tag ${LEVEL_TAG[b.level] || ''}`;
    tag.textContent = b.title;
    $('briefing-sub').textContent = b.running ? `${t('Seit')} ${fmtTime(b.start)} (${t('läuft')})` : `${fmtTime(b.start)}–${fmtTime(b.end)}`;
    $('briefing-sentence').textContent = b.sentence;
    const list = $('briefing-list');
    list.innerHTML = '';
    b.items.forEach(i => {
      const li = el('li', `briefing-item ${i.level}`);
      const text = el(i.link ? 'a' : 'span', 'briefing-text', i.text);
      if (i.link) text.href = i.link;
      li.appendChild(text);
      list.appendChild(li);
    });
  }

  // What's due today comes from this dashboard's own planner (also with a remote Pi selected)
  window.addEventListener('pi:planner', e => {
    const d = e.detail;
    const parts = [];
    if (d.remindersDue.length) parts.push(d.remindersDue.length === 1 ? `${t('Erinnerung')}: ${d.remindersDue[0].title}` : `${d.remindersDue.length} ${t('Erinnerungen')}`);
    if (d.kanbanDue.length) parts.push(`${d.kanbanDue.length} ${d.kanbanDue.length === 1 ? t('Aufgabe fällig') : t('Aufgaben fällig')}`);
    const today = $('briefing-today');
    today.hidden = !parts.length;
    today.textContent = parts.length ? `${t('Heute')}: ${parts.join(' · ')}` : '';
  });
  $('briefing-today').addEventListener('click', () => window.PiPlanner && window.PiPlanner.openReminders());

  async function load() {
    try {
      const res = await fetch('/api/briefing');
      if (!res.ok) { if (res.status === 401 || res.status === 403) card.classList.add('planner-locked'); return; }
      render(await res.json());
    } catch (e) { /* offline */ }
  }

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (!window.PiAuth || window.PiAuth.can('system.view')) load();
  })();
  setInterval(() => { if (!document.hidden) load(); }, 10 * 60 * 1000);
})();
