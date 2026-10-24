/**
 * HostCore Dashboard - compliance sections "Verarbeitungstätigkeiten" (Art. 30 DSGVO) and "TOM"
 * (Art. 32 DSGVO): controller, processing activities with legal basis and systems, measures per
 * control area with the evidence the dashboard determines itself.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('pv-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
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
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtDate = ts => (ts ? new Date(ts * 1000).toLocaleDateString(LOCALE) : '–');

  function fieldIn(form, label, input, full) {
    const w = el('label', `login-field${full ? ' full' : ''}`);
    w.append(el('span', '', t(label)), input);
    form.appendChild(w);
    return input;
  }
  const inp = (val, ph) => { const i = el('input'); i.type = 'text'; i.value = val || ''; if (ph) i.placeholder = t(ph); return i; };
  const area = (val, rows, ph) => { const a = el('textarea'); a.rows = rows; a.value = val || ''; if (ph) a.placeholder = t(ph); return a; };

  // ---------------------------------------------------------------------------
  // Controller
  // ---------------------------------------------------------------------------
  function renderController() {
    const form = $('pv-controller');
    form.innerHTML = '';
    const c = data.controller || {};
    const name = fieldIn(form, 'Verantwortlicher (Firma/Name)', inp(c.name));
    const contact = fieldIn(form, 'Kontakt (Telefon, E-Mail)', inp(c.contact));
    const address = fieldIn(form, 'Anschrift', inp(c.address), true);
    const rep = fieldIn(form, 'Vertreter (Geschäftsführung)', inp(c.representative));
    const dpo = fieldIn(form, 'Datenschutzbeauftragte(r)', inp(c.dpo, 'Name, Kontakt – oder „nicht benannt (unter 20 Personen)“'));
    if (!can('notes.edit')) { form.querySelectorAll('input').forEach(i => { i.disabled = true; }); return; }
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    actions.appendChild(save);
    form.appendChild(actions);
    form.onsubmit = async e => {
      e.preventDefault();
      try {
        data = await api('/api/compliance/privacy/controller', { name: name.value, contact: contact.value, address: address.value, representative: rep.value, dpo: dpo.value });
        render();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); }
    };
  }

  // ---------------------------------------------------------------------------
  // Activities
  // ---------------------------------------------------------------------------
  function activityForm(a) {
    const wrap = $('pv-form-wrap');
    const v = a || {};
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(a ? 'Tätigkeit bearbeiten' : 'Verarbeitungstätigkeit erfassen')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const name = fieldIn(form, 'Bezeichnung', inp(v.name, 'z. B. Kundenverwaltung, Mitarbeiter-E-Mail, Nextcloud'));
    const owner = fieldIn(form, 'Verantwortlich intern', inp(v.owner));
    const purpose = fieldIn(form, 'Zweck der Verarbeitung', area(v.purpose, 2), true);
    const basesBox = el('div', 'login-field full');
    basesBox.appendChild(el('span', '', t('Rechtsgrundlage')));
    const bases = el('div', 'inc-targets');
    const chosen = new Set(v.legal_bases || []);
    Object.entries(data.legal_bases).forEach(([k, l]) => {
      const lab = el('label', 'toggle-label');
      const i = el('input');
      i.type = 'checkbox';
      i.value = k;
      i.checked = chosen.has(k);
      lab.append(i, ` ${t(l)}`);
      bases.appendChild(lab);
    });
    basesBox.appendChild(bases);
    form.appendChild(basesBox);
    const note = fieldIn(form, 'Erläuterung zur Rechtsgrundlage (z. B. berechtigtes Interesse)', inp(v.legal_note), true);
    const subjects = fieldIn(form, 'Betroffene Personen', inp(v.subjects, 'Kunden, Beschäftigte, Interessenten …'));
    const cats = fieldIn(form, 'Datenkategorien', inp(v.categories, 'Name, Kontaktdaten, Vertragsdaten …'));
    const special = el('label', 'toggle-label full');
    const sp = el('input');
    sp.type = 'checkbox';
    sp.checked = !!v.special;
    special.append(sp, ` ${t('Besondere Kategorien nach Art. 9 DSGVO (Gesundheit, Religion, Biometrie …)')}`);
    form.appendChild(special);
    const recipients = fieldIn(form, 'Empfänger', inp(v.recipients, 'intern: Buchhaltung; extern: Steuerberater'));
    const processors = fieldIn(form, 'Auftragsverarbeiter', inp(v.processors, 'Hoster, Mail-Anbieter … (mit AV-Vertrag)'));
    const third = fieldIn(form, 'Übermittlung in Drittländer', inp(v.third_country, 'keine / USA (EU-US Data Privacy Framework)'));
    const retention = fieldIn(form, 'Löschfristen', inp(v.retention, 'z. B. 10 Jahre nach Vertragsende (§ 147 AO)'));
    const review = el('input');
    review.type = 'date';
    review.value = v.review || '';
    fieldIn(form, 'Nächste Prüfung', review);
    const sysBox = el('div', 'login-field full');
    sysBox.appendChild(el('span', '', t('Beteiligte Systeme')));
    const systems = el('div', 'inc-targets');
    const sysChosen = new Set(v.systems || []);
    data.targets.forEach(tg => {
      const lab = el('label', 'toggle-label');
      const i = el('input');
      i.type = 'checkbox';
      i.value = tg.target;
      i.checked = sysChosen.has(tg.target);
      lab.append(i, ' ', raw('span', '', tg.name));
      systems.appendChild(lab);
    });
    sysBox.appendChild(systems);
    form.appendChild(sysBox);
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
        data = await api(a ? `/api/compliance/privacy/activities/${a.id}` : '/api/compliance/privacy/activities', {
          name: name.value, owner: owner.value, purpose: purpose.value, legal_note: note.value, subjects: subjects.value, categories: cats.value,
          special: sp.checked, recipients: recipients.value, processors: processors.value, third_country: third.value, retention: retention.value,
          review: review.value, legal_bases: [...bases.querySelectorAll('input:checked')].map(i => i.value),
          systems: [...systems.querySelectorAll('input:checked')].map(i => i.value)
        });
        wrap.hidden = true;
        wrap.innerHTML = '';
        render();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    wrap.appendChild(form);
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    name.focus();
  }

  function activityCard(a) {
    const card = el('div', `inc-card ${a.review_due ? 'open mittel' : 'resolved'}`);
    const head = el('div', 'inc-head');
    head.append(raw('strong', 'inc-title', a.name));
    if (a.special) head.appendChild(el('span', 'health-tag warning', t('Art. 9')));
    if (a.review_due) head.appendChild(el('span', 'health-tag warning', t('Prüfung fällig')));
    const toggle = el('button', 'text-btn', t(open.has(a.id) ? 'Weniger' : 'Details'));
    toggle.type = 'button';
    toggle.addEventListener('click', () => { open.has(a.id) ? open.delete(a.id) : open.add(a.id); render(); });
    head.appendChild(toggle);
    card.appendChild(head);
    card.appendChild(raw('p', 'inc-summary', a.purpose));
    card.appendChild(el('span', 'lan-sub', `${a.legal_labels.map(t).join(' · ')} · ${t('geändert')} ${fmtDate(a.updated)}${a.review ? ` · ${t('Prüfung')} ${new Date(a.review).toLocaleDateString(LOCALE)}` : ''}`));
    if (open.has(a.id)) {
      const dl = el('dl', 'backup-facts');
      [['Betroffene', a.subjects], ['Datenkategorien', a.categories], ['Empfänger', a.recipients], ['Auftragsverarbeiter', a.processors],
        ['Drittländer', a.third_country], ['Löschfristen', a.retention], ['Verantwortlich', a.owner], ['Systeme', a.system_names.join(', ')]]
        .forEach(([k, v2]) => { if (v2) dl.append(el('dt', '', t(k)), raw('dd', '', v2)); });
      card.appendChild(dl);
      if (can('notes.edit')) {
        const row = el('div', 'docs-row-actions inc-row-actions');
        const edit = el('button', 'text-btn', t('Bearbeiten'));
        edit.type = 'button';
        edit.addEventListener('click', () => activityForm(a));
        const del = el('button', 'text-btn danger', t('Löschen'));
        del.type = 'button';
        del.addEventListener('click', async () => {
          if (!del.classList.contains('confirming')) {
            del.classList.add('confirming');
            del.textContent = t('Sicher?');
            setTimeout(() => { del.classList.remove('confirming'); del.textContent = t('Löschen'); }, 4000);
            return;
          }
          try { data = await api(`/api/compliance/privacy/activities/${a.id}/delete`, {}); render(); } catch (err) { toast(err.message, 'error'); }
        });
        row.append(edit, del);
        card.appendChild(row);
      }
    }
    return card;
  }

  // ---------------------------------------------------------------------------
  // TOM
  // ---------------------------------------------------------------------------
  function tomCard(tm) {
    const card = el('div', 'tom-card');
    const head = el('div', 'inc-head');
    head.append(el('strong', 'inc-title', t(tm.label)), el('span', `health-tag ${tm.text ? 'good' : 'warning'}`, t(tm.text ? 'Beschrieben' : 'Fehlt')));
    card.appendChild(head);
    card.appendChild(el('p', 'field-hint', t(tm.hint)));
    const text = el('textarea', 'tom-text');
    text.rows = 4;
    text.value = tm.text || '';
    text.placeholder = t('Maßnahmen in diesem Bereich …');
    text.disabled = !can('notes.edit');
    card.appendChild(text);
    if (tm.evidence.length) {
      const ev = el('div', 'tom-evidence');
      ev.appendChild(el('span', 'hb-label', t('Vom System festgestellt')));
      const ul = el('ul');
      tm.evidence.forEach(e => ul.appendChild(el('li', '', t(e))));
      ev.appendChild(ul);
      card.appendChild(ev);
    }
    if (can('notes.edit')) {
      const save = el('button', 'btn-secondary', t('Speichern'));
      save.type = 'button';
      save.addEventListener('click', async () => {
        save.disabled = true;
        try { data = await api('/api/compliance/privacy/tom', { area: tm.id, text: text.value }); render(); toast(t('Gespeichert.'), 'success'); }
        catch (err) { toast(err.message, 'error'); save.disabled = false; }
      });
      card.appendChild(save);
    }
    return card;
  }

  function render() {
    renderController();
    const list = $('pv-list');
    list.innerHTML = '';
    if (!data.activities.length) list.appendChild(el('p', 'docs-empty', t('Noch keine Verarbeitungstätigkeit erfasst.')));
    data.activities.forEach(a => list.appendChild(activityCard(a)));
    const tom = $('tom-list');
    tom.innerHTML = '';
    data.tom.forEach(tm => tom.appendChild(tomCard(tm)));
  }

  $('pv-add').addEventListener('click', () => activityForm(null));
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    try { data = await api('/api/compliance/privacy'); render(); } catch (e) { /* no access */ }
  })();
})();
