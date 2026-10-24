/**
 * HostCore Dashboard - documentation section "Probleme": recurring incidents bundled into a problem
 * with root cause, workaround (known error) and solution via a change request; suggestions and
 * workaround hints for open incidents.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('pb-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const STATUS_CLS = { neu: 'warning', analyse: 'warning', bekannt: 'critical', geloest: 'good', geschlossen: '' };
  const PRIO = { niedrig: '', mittel: 'mittel', hoch: 'schwer' };
  const open = new Set();
  let data = null;

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

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtDate = ts => (ts ? new Date(ts * 1000).toLocaleDateString(LOCALE) : '–');

  function checks(list, chosen, label) {
    const box = el('div', 'login-field full');
    box.appendChild(el('span', '', t(label)));
    const wrap = el('div', 'inc-targets pb-checks');
    list.forEach(([value, text]) => {
      const l = el('label', 'toggle-label');
      const i = el('input');
      i.type = 'checkbox';
      i.value = value;
      i.checked = chosen.has(value);
      l.append(i, ' ', raw('span', '', text));
      wrap.appendChild(l);
    });
    if (!list.length) wrap.appendChild(el('span', 'field-hint', t('Keine vorhanden.')));
    box.appendChild(wrap);
    return [box, () => [...wrap.querySelectorAll('input:checked')].map(i => i.value)];
  }

  function openForm(p, preset) {
    const wrap = $('pb-form-wrap');
    const v = p || preset || {};
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(p ? 'Problem bearbeiten' : 'Problem erfassen')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const field = (label, input, full) => { const w = el('label', `login-field${full ? ' full' : ''}`); w.append(el('span', '', t(label)), input); form.appendChild(w); return input; };
    const inp = (val, ph) => { const i = el('input'); i.type = 'text'; i.value = val || ''; if (ph) i.placeholder = t(ph); return i; };
    const area = (val, rows, ph) => { const a = el('textarea'); a.rows = rows; a.value = val || ''; if (ph) a.placeholder = t(ph); return a; };
    const title = field('Titel', inp(v.title, 'z. B. Nextcloud wird nachts langsam und bricht ab'), true);
    const prio = el('select');
    Object.entries(data.priorities).forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; prio.appendChild(o); });
    prio.value = v.priority || 'mittel';
    field('Priorität', prio);
    const chg = el('select');
    const none = el('option', '', t('– kein Änderungsantrag –'));
    none.value = '';
    chg.appendChild(none);
    data.changes.forEach(c => { const o = el('option', '', `${c.title} (${t(c.status_label)})`); o.value = c.id; chg.appendChild(o); });
    chg.value = v.change_id || '';
    field('Lösung über Änderungsantrag', chg);
    const desc = field('Beschreibung', area(v.description, 2, 'Was fällt auf, wie oft, welche Auswirkung?'), true);
    const [svcBox, svcVals] = checks(data.targets.map(x => [x.target, x.name]), new Set(v.services || []), 'Betroffene Dienste');
    form.appendChild(svcBox);
    const [incBox, incVals] = checks(data.incidents.map(i => [i.id, `${i.title} (${fmtDate(i.started)})`]), new Set(v.incidents || []), 'Zugehörige Störungen');
    form.appendChild(incBox);
    const cause = field('Ursache', area(v.cause, 3, 'Eigentliche Ursache – Ergebnis der Analyse'), true);
    const workaround = field('Workaround', area(v.workaround, 2, 'Was hilft sofort, bis die Ursache behoben ist?'), true);
    const solution = field('Lösung', area(v.solution, 2, 'Wie wird die Ursache dauerhaft beseitigt?'), true);
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    const cancel = el('button', 'btn-secondary', t('Abbrechen'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => { wrap.hidden = true; wrap.innerHTML = ''; });
    actions.append(save, cancel);
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      try {
        data = await api(p ? `/api/problems/${p.id}` : '/api/problems', { title: title.value, priority: prio.value, change_id: chg.value, description: desc.value,
          services: svcVals(), incidents: incVals(), cause: cause.value, workaround: workaround.value, solution: solution.value });
        if (data.saved) open.add(data.saved);
        wrap.hidden = true;
        wrap.innerHTML = '';
        render();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    wrap.appendChild(form);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    title.focus();
  }

  function statusForm(p) {
    const form = el('form', 'inc-update');
    const sel = el('select');
    Object.entries(data.statuses).forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; sel.appendChild(o); });
    sel.value = p.status;
    const note = el('input');
    note.type = 'text';
    note.placeholder = t('Notiz (optional)');
    const go = el('button', 'btn-primary', t('Status setzen'));
    go.type = 'submit';
    form.append(sel, note, go);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      go.disabled = true;
      try { data = await api(`/api/problems/${p.id}/status`, { status: sel.value, note: note.value }); render(); }
      catch (err) { toast(err.message, 'error'); go.disabled = false; }
    });
    return form;
  }

  function card(p) {
    const c = el('div', `inc-card ${p.open ? 'open' : 'resolved'} ${PRIO[p.priority]}`);
    const head = el('div', 'inc-head');
    head.append(el('span', `health-tag ${STATUS_CLS[p.status]}`, t(p.status_label)), raw('strong', 'inc-title', p.title),
      el('span', 'field-hint', `${t('Priorität')} ${t(p.priority_label).toLowerCase()} · ${p.recurrences} ${t(p.recurrences === 1 ? 'Störung' : 'Störungen')}`));
    const toggle = el('button', 'text-btn', t(open.has(p.id) ? 'Weniger' : 'Details'));
    toggle.type = 'button';
    toggle.addEventListener('click', () => { open.has(p.id) ? open.delete(p.id) : open.add(p.id); render(); });
    head.appendChild(toggle);
    c.appendChild(head);
    c.appendChild(el('span', 'lan-sub', `${t('Erfasst')} ${fmtDate(p.created)} · ${p.user}${p.solved ? ` · ${t('gelöst')} ${fmtDate(p.solved)}` : ''}`));
    if (p.service_names.length) {
      const svc = el('div', 'fleet-pis');
      p.service_names.forEach(s => svc.appendChild(raw('span', 'pg-chip', s)));
      c.appendChild(svc);
    }
    if (p.status === 'bekannt' && p.workaround) {
      const w = el('div', 'db-problem warning');
      w.append(el('strong', '', `${t('Workaround')}: `), raw('span', '', p.workaround));
      c.appendChild(w);
    }
    if (p.warning) c.appendChild(el('div', 'db-problem warning', t(p.warning)));
    if (open.has(p.id)) {
      [['Beschreibung', p.description], ['Ursache', p.cause], ['Workaround', p.workaround], ['Lösung', p.solution]].forEach(([l, v]) => {
        if (!v) return;
        const d = el('div', 'chg-detail');
        d.append(el('strong', '', t(l)), raw('p', 'inc-summary', v));
        c.appendChild(d);
      });
      if (p.change) c.appendChild(el('div', 'lan-sub', `${t('Änderungsantrag')}: ${p.change.title} – ${t(p.change.status_label)}${p.change.result_label ? ` (${t(p.change.result_label)})` : ''}`));
      if (p.incident_list.length) {
        const ul = el('ul', 'go-commits');
        p.incident_list.forEach(i => {
          const li = el('li');
          li.append(el('span', `health-tag ${i.open ? 'warning' : 'good'}`, t(i.open ? 'offen' : 'behoben')), raw('span', 'go-subject', i.title), el('span', 'go-meta', fmtDate(i.started)));
          ul.appendChild(li);
        });
        c.appendChild(ul);
      }
      const tl = el('ol', 'inc-timeline');
      [...(p.timeline || [])].reverse().forEach(u => {
        const li = el('li');
        li.append(el('span', 'inc-when', `${new Date(u.ts * 1000).toLocaleString(LOCALE)} · ${t(data.statuses[u.status] || u.status)} · ${u.user}`), raw('span', '', u.text));
        tl.appendChild(li);
      });
      c.appendChild(tl);
    }
    if (can('notes.edit') && (p.open || open.has(p.id))) {
      const act = el('div', 'inc-actions');
      act.appendChild(statusForm(p));
      const row = el('div', 'docs-row-actions inc-row-actions');
      const edit = el('button', 'text-btn', t('Bearbeiten'));
      edit.type = 'button';
      edit.addEventListener('click', () => openForm(p));
      row.appendChild(edit);
      if (!p.change_id) {
        const mk = el('button', 'text-btn', t('Änderungsantrag anlegen'));
        mk.type = 'button';
        mk.title = t('Legt einen Entwurf für die Lösung an und verknüpft ihn');
        mk.addEventListener('click', async () => {
          try { data = await api(`/api/problems/${p.id}/change`, {}); render(); toast(t('Entwurf unter Änderungsanträge angelegt.'), 'success'); }
          catch (err) { toast(err.message, 'error'); }
        });
        row.appendChild(mk);
      }
      const del = el('button', 'text-btn danger', t('Löschen'));
      del.type = 'button';
      del.addEventListener('click', async () => {
        if (!del.classList.contains('confirming')) {
          del.classList.add('confirming');
          del.textContent = t('Sicher?');
          setTimeout(() => { del.classList.remove('confirming'); del.textContent = t('Löschen'); }, 4000);
          return;
        }
        try { data = await api(`/api/problems/${p.id}/delete`, {}); render(); } catch (err) { toast(err.message, 'error'); }
      });
      row.appendChild(del);
      act.appendChild(row);
      c.appendChild(act);
    }
    return c;
  }

  function renderHints() {
    const box = $('pb-hints');
    box.innerHTML = '';
    data.known_error_hints.forEach(h => {
      const d = el('div', 'db-problem warning pb-hint');
      d.append(el('strong', '', `${t('Bekannter Fehler bei offener Störung')} „`), raw('span', '', h.incident_title), el('strong', '', '“: '), raw('span', '', h.workaround));
      box.appendChild(d);
    });
    data.suggestions.forEach(s => {
      const d = el('div', 'pb-suggest');
      d.append(el('span', '', `${s.count} ${t('Störungen in 90 Tagen bei')} `), raw('strong', '', s.name));
      if (can('notes.edit')) {
        const b = el('button', 'text-btn', t('Als Problem erfassen'));
        b.type = 'button';
        b.addEventListener('click', () => openForm(null, { title: `${t('Wiederkehrende Störungen')}: ${s.name}`, services: [s.service], incidents: s.incidents.map(i => i.id) }));
        d.appendChild(b);
      }
      box.appendChild(d);
    });
  }

  function render() {
    $('pb-summary').textContent = [data.open ? `${data.open} ${t('offen')}` : '', data.known_errors ? `${data.known_errors} ${t(data.known_errors === 1 ? 'bekannter Fehler' : 'bekannte Fehler')}` : '']
      .filter(Boolean).join(' · ');
    renderHints();
    const list = $('pb-list');
    list.innerHTML = '';
    if (!data.problems.length) list.appendChild(el('p', 'docs-empty', t('Noch kein Problem erfasst.')));
    data.problems.forEach(p => list.appendChild(card(p)));
  }

  $('pb-add').addEventListener('click', () => openForm(null));
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    try { data = await api('/api/problems'); render(); } catch (e) { /* no access */ }
  })();
})();
