/**
 * HostCore Dashboard - documentation section "Änderungsanträge": planned changes with risk, plan,
 * rollback plan and check; four-eyes approval, carrying out and result with timeline.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('chg-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const RISK = { gering: '', mittel: 'warning', hoch: 'critical' };
  const STATUS_CLS = { entwurf: '', beantragt: 'warning', genehmigt: 'good', abgelehnt: 'critical', umsetzung: 'warning', abgeschlossen: 'good', abgebrochen: '' };
  const open = new Set();
  let data = null;
  let me = '';

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

  const fmtTs = ts => (ts ? new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');
  const toLocal = ts => { if (!ts) return ''; const d = new Date(ts * 1000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
  const fromLocal = v => (v ? Math.floor(new Date(v).getTime() / 1000) : null);

  function button(label, cls, fn) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => fn(b));
    return b;
  }

  // ---------------------------------------------------------------------------
  // Form
  // ---------------------------------------------------------------------------
  function openForm(c) {
    const wrap = $('chg-form-wrap');
    const v = c || {};
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(c ? 'Änderung bearbeiten' : 'Änderung beantragen')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const field = (label, input, full) => { const w = el('label', `login-field${full ? ' full' : ''}`); w.append(el('span', '', t(label)), input); form.appendChild(w); return input; };
    const inp = (val, ph, type = 'text') => { const i = el('input'); i.type = type; i.value = val || ''; if (ph) i.placeholder = t(ph); return i; };
    const area = (val, rows, ph) => { const a = el('textarea'); a.rows = rows; a.value = val || ''; if (ph) a.placeholder = t(ph); return a; };
    const title = field('Titel', inp(v.title, 'z. B. Nextcloud auf Version 30 aktualisieren'), true);
    const risk = el('select');
    Object.entries(data.risks).forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; risk.appendChild(o); });
    risk.value = v.risk || 'mittel';
    field('Risiko', risk);
    const start = field('Geplanter Beginn', inp(toLocal(v.start), '', 'datetime-local'));
    const end = field('Geplantes Ende', inp(toLocal(v.end), '', 'datetime-local'));
    const reason = field('Begründung', area(v.reason, 2, 'Warum ist die Änderung nötig?'), true);
    const desc = field('Beschreibung', area(v.description, 3, 'Was genau wird geändert?'), true);
    const plan = field('Ablauf', area(v.plan, 4, 'Schritte der Umsetzung, eine Zeile pro Schritt'), true);
    const rollback = field('Rückfallplan', area(v.rollback, 3, 'Wie wird zurückgerollt, wenn es schiefgeht?'), true);
    const verify = field('Prüfung', area(v.verify, 2, 'Woran sieht man, dass alles funktioniert?'), true);
    const svcBox = el('div', 'login-field full');
    svcBox.appendChild(el('span', '', t('Betroffene Dienste')));
    const targets = el('div', 'inc-targets');
    const chosen = new Set(v.services || []);
    data.targets.forEach(tg => {
      const l = el('label', 'toggle-label');
      const i = el('input');
      i.type = 'checkbox';
      i.value = tg.target;
      i.checked = chosen.has(tg.target);
      l.append(i, ' ', raw('span', '', tg.name));
      targets.appendChild(l);
    });
    svcBox.appendChild(targets);
    form.appendChild(svcBox);
    const toggle = (label, checked) => { const l = el('label', 'toggle-label full'); const i = el('input'); i.type = 'checkbox'; i.checked = !!checked; l.append(i, ` ${t(label)}`); form.appendChild(l); return i; };
    const maint = toggle('Bei der Freigabe ein Wartungsfenster für Zeitraum und Dienste anlegen', v.maintenance !== false);
    const standard = toggle('Standardänderung (nur bei geringem Risiko: vorab genehmigt)', v.standard);
    const syncStd = () => { standard.disabled = risk.value !== 'gering'; if (standard.disabled) standard.checked = false; };
    risk.addEventListener('change', syncStd);
    syncStd();
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Als Entwurf speichern'));
    save.type = 'submit';
    const submit = el('button', 'btn-secondary', t('Speichern & beantragen'));
    submit.type = 'button';
    const cancel = el('button', 'btn-secondary', t('Abbrechen'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => { wrap.hidden = true; wrap.innerHTML = ''; });
    actions.append(save, submit, cancel);
    form.appendChild(actions);
    const payload = () => ({ title: title.value, risk: risk.value, start: fromLocal(start.value), end: fromLocal(end.value), reason: reason.value,
      description: desc.value, plan: plan.value, rollback: rollback.value, verify: verify.value, maintenance: maint.checked, standard: standard.checked,
      services: [...targets.querySelectorAll('input:checked')].map(i => i.value) });
    const store = async andSubmit => {
      save.disabled = submit.disabled = true;
      try {
        let d = await api(c ? `/api/changes/${c.id}` : '/api/changes', payload());
        if (andSubmit) {
          const id = d.saved;
          d = await api(`/api/changes/${id}/action`, { action: 'submit' });
          open.add(id);
        }
        data = d;
        wrap.hidden = true;
        wrap.innerHTML = '';
        render();
        toast(t(andSubmit ? 'Freigabe beantragt.' : 'Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = submit.disabled = false; }
    };
    form.addEventListener('submit', e => { e.preventDefault(); store(false); });
    submit.addEventListener('click', () => store(true));
    wrap.appendChild(form);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    title.focus();
  }

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------
  async function act(c, action, extra = {}) {
    try {
      data = await api(`/api/changes/${c.id}/action`, { action, ...extra });
      render();
    } catch (err) { toast(err.message, 'error'); }
  }

  function noteForm(c, label, action, opts = {}) {
    const form = el('form', 'inc-update');
    let result = null;
    if (opts.results) {
      result = el('select');
      Object.entries(data.results).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; result.appendChild(o); });
      form.appendChild(result);
    }
    const text = el('input');
    text.type = 'text';
    text.placeholder = t(opts.placeholder || 'Notiz (optional)');
    const go = el('button', opts.cls || 'btn-primary', t(label));
    go.type = 'submit';
    form.append(text, go);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      go.disabled = true;
      await act(c, action, { note: text.value, result: result ? result.value : '' });
      go.disabled = false;
    });
    return form;
  }

  function actionsFor(c) {
    const box = el('div', 'inc-actions');
    const edit = can('notes.edit');
    const mine = c.user === me;
    if (c.status === 'beantragt' && data.can_approve) {
      const selfOnly = mine && data.approvers.some(a => a !== me);
      if (selfOnly) box.appendChild(el('p', 'field-hint', `${t('Vier-Augen-Prinzip: freigeben kann')} ${data.approvers.filter(a => a !== me).join(', ')}.`));
      else {
        box.appendChild(noteForm(c, 'Genehmigen', 'approve', { placeholder: 'Anmerkung zur Freigabe (optional)' }));
        box.appendChild(noteForm(c, 'Ablehnen', 'reject', { placeholder: 'Begründung der Ablehnung', cls: 'btn-danger' }));
      }
    } else if (c.status === 'beantragt') {
      box.appendChild(el('p', 'field-hint', t('Wartet auf Freigabe durch eine Person mit dem Recht „Änderungen freigeben“.')));
    }
    if (!edit) return box;
    if (c.status === 'genehmigt') box.appendChild(noteForm(c, 'Umsetzung beginnen', 'start'));
    if (c.status === 'umsetzung') box.appendChild(noteForm(c, 'Abschließen', 'complete', { results: true, placeholder: 'Was ist passiert? (Pflicht bei Problemen)' }));
    if (c.open) box.appendChild(noteForm(c, 'Kommentar', 'comment', { placeholder: 'Kommentar', cls: 'btn-secondary' }));
    const row = el('div', 'docs-row-actions inc-row-actions');
    if (['entwurf', 'abgelehnt'].includes(c.status)) {
      row.append(button('Bearbeiten', 'text-btn', () => openForm(c)), button('Beantragen', 'text-btn', () => act(c, 'submit')));
    }
    const md = el('a', 'text-btn', t('Markdown'));
    md.href = `/api/changes/${c.id}/markdown`;
    row.appendChild(md);
    if (c.open && c.status !== 'entwurf') row.appendChild(confirm('Abbrechen', 'text-btn danger', () => act(c, 'cancel')));
    if (['entwurf', 'abgelehnt'].includes(c.status)) {
      row.appendChild(confirm('Löschen', 'text-btn danger', async () => {
        try { data = await api(`/api/changes/${c.id}/delete`, {}); render(); } catch (err) { toast(err.message, 'error'); }
      }));
    }
    box.appendChild(row);
    return box;
  }

  function confirm(label, cls, fn) {
    return button(label, cls, b => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = t('Sicher?');
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = t(label); }, 4000);
        return;
      }
      b.classList.remove('confirming');
      b.textContent = t(label);
      fn();
    });
  }

  // ---------------------------------------------------------------------------
  // List
  // ---------------------------------------------------------------------------
  function detail(label, text) {
    if (!text) return null;
    const d = el('div', 'chg-detail');
    d.append(el('strong', '', t(label)), raw('p', 'inc-summary', text));
    return d;
  }

  function card(c) {
    const cardEl = el('div', `inc-card chg-card ${c.open ? 'open' : 'resolved'} ${c.risk === 'hoch' ? 'schwer' : c.risk === 'mittel' ? 'mittel' : ''}`);
    const head = el('div', 'inc-head');
    head.append(el('span', `health-tag ${STATUS_CLS[c.status]}`, t(c.status_label) + (c.result_label ? ` · ${t(c.result_label)}` : '')),
      raw('strong', 'inc-title', c.title), el('span', `health-tag ${RISK[c.risk]}`, `${t('Risiko')} ${t(c.risk_label).toLowerCase()}`));
    if (c.standard) head.appendChild(el('span', 'field-hint', t('Standardänderung')));
    if (c.overdue) head.appendChild(el('span', 'health-tag critical', t('Überfällig')));
    head.appendChild(button(open.has(c.id) ? 'Weniger' : 'Details', 'text-btn', () => { open.has(c.id) ? open.delete(c.id) : open.add(c.id); render(); }));
    cardEl.appendChild(head);
    cardEl.appendChild(el('span', 'lan-sub', `${t('Geplant')} ${fmtTs(c.start)} – ${fmtTs(c.end)} · ${t('beantragt von')} ${c.user}${c.approved_by ? ` · ${t('genehmigt von')} ${c.approved_by}` : ''}`));
    if (c.service_names.length) {
      const svc = el('div', 'fleet-pis');
      c.service_names.forEach(s => svc.appendChild(raw('span', 'pg-chip', s)));
      cardEl.appendChild(svc);
    }
    if (c.conflicts.length) {
      cardEl.appendChild(el('div', 'db-problem warning', `${t('Überschneidet sich mit')}: ${c.conflicts.map(x => `${x.title} (${fmtTs(x.start)})`).join(', ')}`));
    }
    if (open.has(c.id)) {
      [['Begründung', c.reason], ['Beschreibung', c.description], ['Ablauf', c.plan], ['Rückfallplan', c.rollback], ['Prüfung', c.verify]]
        .forEach(([l, v]) => { const d = detail(l, v); if (d) cardEl.appendChild(d); });
      const tl = el('ol', 'inc-timeline');
      [...(c.timeline || [])].reverse().forEach(u => {
        const li = el('li');
        li.append(el('span', 'inc-when', `${fmtTs(u.ts)} · ${u.comment ? t('Kommentar') : t(data.statuses[u.status] || u.status)}${u.user ? ` · ${u.user}` : ''}`), raw('span', '', u.text));
        tl.appendChild(li);
      });
      cardEl.appendChild(tl);
    }
    if (c.open || open.has(c.id)) cardEl.appendChild(actionsFor(c));
    return cardEl;
  }

  function render() {
    const list = $('chg-list');
    list.innerHTML = '';
    $('chg-summary').textContent = [data.waiting ? `${data.waiting} ${t(data.waiting === 1 ? 'wartet auf Freigabe' : 'warten auf Freigabe')}` : '',
      data.open ? `${data.open} ${t('offen')}` : ''].filter(Boolean).join(' · ');
    const items = data.changes.filter(c => $('chg-filter').value !== 'open' || c.open);
    if (!items.length) list.appendChild(el('p', 'docs-empty', t(data.changes.length ? 'Keine offenen Änderungen.' : 'Noch keine Änderung beantragt.')));
    items.forEach(c => list.appendChild(card(c)));
  }

  async function load() {
    try { data = await api('/api/changes'); render(); } catch (e) { /* no access */ }
  }

  $('chg-add').addEventListener('click', () => openForm(null));
  $('chg-filter').addEventListener('change', render);
  (async () => {
    if (window.PiAuth) { const st = await window.PiAuth.ready; me = (st && st.user) || ''; }
    load();
  })();
})();
