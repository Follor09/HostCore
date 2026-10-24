/**
 * HostCore Dashboard - system page section "Alarme": open, acknowledged and closed alarms with
 * their history; acknowledge (with a note) or close them.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('al-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const STATUS = { open: ['critical', 'Offen'], acked: ['warning', 'Quittiert'], resolved: ['good', 'Erledigt'] };
  let data = null;
  let filter = 'active';

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
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  function button(label, cls, onClick) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => onClick(b));
    return b;
  }

  function ackForm(card, a) {
    if (card.querySelector('form')) return;
    const f = el('form', 'inline-form alarm-ack');
    const note = el('input');
    note.type = 'text';
    note.maxLength = 300;
    note.placeholder = t('Notiz (optional), z. B. „Schaue ich mir an“');
    const ok = el('button', 'btn-primary', t('Quittieren'));
    ok.type = 'submit';
    f.append(note, ok, button('Abbrechen', 'btn-secondary', () => f.remove()));
    f.addEventListener('submit', async e => {
      e.preventDefault();
      ok.disabled = true;
      try { data = await api(`/api/alerts/${a.id}/ack`, { note: note.value }); render(); toast(t('Alarm quittiert.'), 'success'); } catch (err) { toast(err.message, 'error'); ok.disabled = false; }
    });
    card.appendChild(f);
    note.focus();
  }

  function card(a) {
    const c = el('div', `alarm-card ${a.status} ${a.level}`);
    const head = el('div', 'backup-head');
    const [cls, label] = STATUS[a.status];
    const title = a.link ? raw('a', 'backup-name', t(a.title)) : raw('span', 'backup-name', t(a.title));
    if (a.link) title.href = a.link;
    head.append(el('span', `health-tag ${cls}`, t(label)), title);
    if (a.repeats) head.appendChild(el('span', 'field-hint', t(`${a.repeats}× erneut`)));
    c.appendChild(head);
    if (a.detail) c.appendChild(raw('div', 'field-hint', a.detail));

    const facts = el('dl', 'backup-facts');
    const add = (k, v) => { if (v) facts.append(el('dt', '', t(k)), raw('dd', '', v)); };
    add('Ausgelöst', fmtTs(a.ts));
    if (a.status === 'open') add('Stufe', a.step >= 0 ? `${a.step + 1}` : t('noch nicht benachrichtigt'));
    if (a.acked_at) add('Quittiert', `${fmtTs(a.acked_at)} · ${a.acked_by}${a.ack_note ? ` – ${a.ack_note}` : ''}`);
    if (a.resolved_at) add('Erledigt', `${fmtTs(a.resolved_at)}${a.resolved_by ? ` · ${a.resolved_by}` : ` · ${t('von selbst')}`}`);
    c.appendChild(facts);

    if ((a.history || []).length) {
      const det = el('details', 'alarm-history');
      det.appendChild(el('summary', '', t('Verlauf')));
      const ul = el('ul');
      a.history.slice().reverse().forEach(h => {
        const li = el('li');
        li.append(raw('span', 'field-hint', fmtTs(h.ts)), document.createTextNode(' '), raw('span', '', t(h.text)));
        ul.appendChild(li);
      });
      det.appendChild(ul);
      c.appendChild(det);
    }

    if (can('system.view') && a.status !== 'resolved') {
      const actions = el('div', 'docs-row-actions');
      if (a.status === 'open') actions.appendChild(button('Quittieren', 'text-btn', () => ackForm(c, a)));
      actions.appendChild(button('Erledigt', 'text-btn', async b => {
        b.disabled = true;
        try { data = await api(`/api/alerts/${a.id}/resolve`, {}); render(); } catch (err) { toast(err.message, 'error'); b.disabled = false; }
      }));
      c.appendChild(actions);
    }
    return c;
  }

  function render() {
    const cfg = data.config;
    const st = $('al-state');
    const open = data.counts.open || 0;
    st.className = `health-tag ${open ? 'critical' : cfg.enabled ? 'good' : ''}`;
    st.textContent = open ? t(open === 1 ? '1 offen' : `${open} offen`) : cfg.enabled ? t('Aktiv') : t('Aus');
    const list = $('al-list');
    list.innerHTML = '';
    if (!cfg.enabled && !data.alerts.length) {
      list.appendChild(el('p', 'field-hint', t('Die Alarmierung ist ausgeschaltet. In den Einstellungen unter Alarmierung einschalten und eine Eskalationskette festlegen.')));
      return;
    }
    if (cfg.enabled && !cfg.email_ready) list.appendChild(el('p', 'field-hint warn', t('Der E-Mail-Versand ist nicht eingerichtet – Alarme erscheinen nur hier.')));
    const shown = data.alerts.filter(a => filter === 'all' || a.status !== 'resolved');
    if (!shown.length) {
      list.appendChild(el('p', 'field-hint', t(filter === 'all' ? 'Noch keine Alarme.' : 'Keine offenen Alarme.')));
      return;
    }
    shown.forEach(a => list.appendChild(card(a)));
  }

  async function load() {
    try { data = await api('/api/alerts'); render(); } catch (e) { /* offline */ }
  }

  document.querySelectorAll('#al-filter button').forEach(b => b.addEventListener('click', () => {
    filter = b.dataset.f;
    document.querySelectorAll('#al-filter button').forEach(x => x.classList.toggle('active', x === b));
    if (data) render();
  }));

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    await load();
    // Not while a note is being typed (the list would be rebuilt under it)
    setInterval(() => { if (!document.hidden && !document.querySelector('.alarm-ack')) load(); }, 30000);
  })();
})();
