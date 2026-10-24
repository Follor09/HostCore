/**
 * PiCore Dashboard - documentation section "Notfallhandbuch": readiness, general part (contacts,
 * where credentials are kept, where the copy lies) and per service the generated restore steps
 * with priority, backups, own steps and a check. Export: print view and Markdown.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('hb-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const PRIO_CLS = { 1: 'critical', 2: 'warning', 3: '' };
  const STATE = { ok: 'OK', overdue: 'überfällig', error: 'Fehler', unknown: 'nicht geprüft' };
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

  const fmtTs = ts => (ts ? new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');

  function field(box, label, input, full) {
    const w = el('label', `login-field${full ? ' full' : ''}`);
    w.append(el('span', '', t(label)), input);
    box.appendChild(w);
    return input;
  }

  function textInput(value, ph) {
    const i = el('input');
    i.type = 'text';
    i.value = value || '';
    if (ph) i.placeholder = t(ph);
    return i;
  }

  function area(value, rows, ph) {
    const a = el('textarea');
    a.rows = rows;
    a.value = value || '';
    if (ph) a.placeholder = t(ph);
    return a;
  }

  // ---------------------------------------------------------------------------
  // Summary and general part
  // ---------------------------------------------------------------------------
  function renderSummary() {
    const box = $('hb-summary');
    box.innerHTML = '';
    const s = data.summary;
    const tile = (label, value, level) => {
      const d = el('div', `hb-tile ${level || ''}`);
      d.append(raw('strong', '', value), el('span', '', t(label)));
      box.appendChild(d);
    };
    tile('Bereitschaft', s.score == null ? '–' : `${s.score} %`, s.score == null ? '' : s.score >= 90 ? 'good' : s.score >= 60 ? 'warning' : 'critical');
    tile('Dienste vollständig', `${s.complete} / ${s.services}`);
    tile('Offene Lücken', String(s.gaps), s.gaps ? 'warning' : 'good');
    tile('Zuletzt exportiert', data.exported && data.exported.ts ? fmtTs(data.exported.ts) : t('nie'), data.exported && data.exported.ts ? '' : 'warning');
    if (data.general_gaps.length) {
      const ul = el('ul', 'hb-gaps full');
      data.general_gaps.forEach(g => ul.appendChild(el('li', '', t(g))));
      box.appendChild(ul);
    }
  }

  function contactRow(list, c) {
    const row = el('div', 'hb-contact');
    const inputs = [['name', 'Name'], ['role', 'Rolle'], ['phone', 'Telefon'], ['email', 'E-Mail']].map(([k, ph]) => {
      const i = textInput(c[k], ph);
      i.dataset.key = k;
      i.setAttribute('aria-label', t(ph));
      return i;
    });
    const rm = el('button', 'btn-secondary', '×');
    rm.type = 'button';
    rm.title = t('Entfernen');
    rm.addEventListener('click', () => row.remove());
    row.append(...inputs, rm);
    list.appendChild(row);
  }

  function renderGeneral() {
    const wrap = $('hb-general-form');
    wrap.innerHTML = '';
    const g = data.general || {};
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const editable = can('notes.edit');
    const contacts = el('div', 'hb-contacts full');
    contacts.appendChild(el('span', 'hb-label', t('Notfallkontakte')));
    const list = el('div', 'hb-contact-list');
    contacts.appendChild(list);
    (g.contacts && g.contacts.length ? g.contacts : [{}]).forEach(c => contactRow(list, c));
    const add = el('button', 'btn-secondary', t('+ Kontakt'));
    add.type = 'button';
    add.addEventListener('click', () => contactRow(list, {}));
    contacts.appendChild(add);
    form.appendChild(contacts);
    const cred = field(form, 'Wo liegen die Zugangsdaten? (Passwort-Manager, Tresor … – keine Passwörter hier eintragen)', area(g.credentials, 3, 'z. B. Vaultwarden, Sammlung „Homelab“; Notfallzugriff über …'), true);
    const loc = field(form, 'Ablage des gedruckten Handbuchs / der Offline-Kopie', textInput(g.location, 'z. B. Ordner im Büroschrank, USB-Stick im Tresor'), true);
    const notes = field(form, 'Allgemeine Hinweise', area(g.notes, 4, 'z. B. Router-Neustart zuerst, Strom über USV …'), true);
    if (editable) {
      const actions = el('div', 'form-actions');
      const save = el('button', 'btn-primary', t('Speichern'));
      save.type = 'submit';
      actions.appendChild(save);
      form.appendChild(actions);
      form.addEventListener('submit', async e => {
        e.preventDefault();
        save.disabled = true;
        const contactsData = [...list.querySelectorAll('.hb-contact')].map(r => Object.fromEntries([...r.querySelectorAll('input')].map(i => [i.dataset.key, i.value.trim()])));
        try {
          data = await api('/api/docs/handbook/general', { contacts: contactsData, credentials: cred.value, location: loc.value, notes: notes.value });
          render();
          toast(t('Gespeichert.'), 'success');
        } catch (err) { toast(err.message, 'error'); }
        save.disabled = false;
      });
    } else {
      form.querySelectorAll('input, textarea, button').forEach(x => { x.disabled = true; });
    }
    wrap.appendChild(form);
  }

  // ---------------------------------------------------------------------------
  // Services
  // ---------------------------------------------------------------------------
  function restoreList(s) {
    const ol = el('ol', 'hb-steps');
    s.restore.forEach(step => {
      const li = el('li', step.own ? 'own' : '');
      li.appendChild(raw('span', '', step.text));
      if (step.cmd) li.appendChild(raw('pre', 'hb-cmd', step.cmd));
      ol.appendChild(li);
    });
    if (!s.restore.length) ol.appendChild(el('li', 'field-hint', t('Keine Schritte – unten eigene Schritte eintragen oder ein Runbook wählen.')));
    return ol;
  }

  function facts(s) {
    const dl = el('dl', 'backup-facts');
    const add = (k, v) => { if (v) dl.append(el('dt', '', t(k)), raw('dd', '', v)); };
    const p = s.profile;
    add('Zweck', p.purpose);
    add('Adresse', p.url);
    add('Zuständig', p.owner);
    if (s.stack) {
      add('Ordner', s.stack.dir);
      add('Volumes', s.stack.volumes.join(', '));
      add('Host-Ordner', s.stack.folders.join(', '));
    }
    if (s.gitops) add('Git', `${s.gitops.url} · ${s.gitops.branch}${s.gitops.path ? ` · ${s.gitops.path}` : ''}`);
    s.databases.forEach(db => add(`Datenbank ${db.name}`, `${db.engine} · ${db.last ? `${t('letzter Dump')} ${fmtTs(db.last.ts)}` : t('kein Dump')}`));
    s.backups.forEach(b => add('Sicherung', `${b.name} (${b.kind}) · ${t(STATE[b.state] || b.state)}${b.last ? ` · ${fmtTs(b.last)}` : ''}${b.auto ? ` · ${t('erkannt')}` : ''}`));
    if (s.runbook) add('Runbook', s.runbook.title);
    add('Abhängig von', s.depends);
    return dl;
  }

  function editForm(s) {
    const set = s.settings || {};
    const form = el('form', 'inline-grid hb-edit');
    form.autocomplete = 'off';
    const prio = el('select');
    Object.entries(data.priorities).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; prio.appendChild(o); });
    prio.value = String(s.priority);
    field(form, 'Priorität beim Wiederanlauf', prio);
    const rb = el('select');
    const none = el('option', '', t('– kein Runbook –'));
    none.value = '';
    rb.appendChild(none);
    data.runbooks.forEach(r => { const o = el('option', '', r.title); o.value = r.id; rb.appendChild(o); });
    rb.value = set.runbook || '';
    field(form, 'Runbook', rb);
    const backups = el('div', 'hb-backups full');
    backups.appendChild(el('span', 'hb-label', t('Sicherungen, die diesen Dienst abdecken')));
    const chosen = new Set(set.backups || []);
    if (!data.backups.length) backups.appendChild(el('p', 'field-hint', t('Noch keine Sicherung unter System-Werkzeuge → Backups eingetragen.')));
    data.backups.forEach(b => {
      const l = el('label', 'toggle-label');
      const i = el('input');
      i.type = 'checkbox';
      i.value = b.id;
      i.checked = chosen.has(b.id);
      l.append(i, ` ${b.name}`);
      backups.appendChild(l);
    });
    const noBackup = el('label', 'toggle-label');
    const nb = el('input');
    nb.type = 'checkbox';
    nb.checked = !!set.no_backup_needed;
    noBackup.append(nb, ` ${t('Braucht keine Sicherung (keine eigenen Daten)')}`);
    backups.appendChild(noBackup);
    form.appendChild(backups);
    const depends = field(form, 'Abhängig von', textInput(set.depends, 'z. B. Datenbank-Stack, NAS, DNS'), true);
    const steps = field(form, 'Eigene Schritte (eine Zeile pro Schritt)', area(set.steps, 4, 'z. B. Lizenzschlüssel im Admin-Bereich neu eintragen'), true);
    const verify = field(form, 'Prüfung nach der Wiederherstellung', textInput(set.verify, 'z. B. Anmelden und ein Dokument öffnen'), true);
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    actions.appendChild(save);
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      try {
        data = await api('/api/docs/handbook/service', {
          target: s.target, priority: Number(prio.value), runbook: rb.value, depends: depends.value, steps: steps.value, verify: verify.value,
          no_backup_needed: nb.checked, backups: [...backups.querySelectorAll('input[value]')].filter(i => i.checked && i !== nb).map(i => i.value)
        });
        render();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    return form;
  }

  function row(s) {
    const det = el('details', `hb-service${s.gaps.length ? ' has-gaps' : ''}`);
    det.open = open.has(s.target);
    det.addEventListener('toggle', () => (det.open ? open.add(s.target) : open.delete(s.target)));
    const sum = el('summary', 'hb-head');
    sum.append(raw('span', 'backup-name', s.name), el('span', 'backup-kind', t({ stack: 'Stack', service: 'Dienst', health: 'Web-Dienst', custom: 'Eigener' }[s.kind] || s.kind)),
      el('span', `health-tag ${PRIO_CLS[s.priority]}`, t(data.priorities[s.priority])),
      el('span', `health-tag ${s.gaps.length ? 'warning' : 'good'}`, s.gaps.length ? `${s.gaps.length} ${t(s.gaps.length === 1 ? 'Lücke' : 'Lücken')}` : t('Vollständig')));
    det.appendChild(sum);
    const body = el('div', 'hb-body');
    if (s.gaps.length) {
      const ul = el('ul', 'hb-gaps');
      s.gaps.forEach(g => ul.appendChild(el('li', '', t(g))));
      body.appendChild(ul);
    }
    body.appendChild(facts(s));
    body.appendChild(el('h4', 'go-sub', t('Wiederherstellen')));
    body.appendChild(restoreList(s));
    if (can('notes.edit')) {
      const ed = el('details', 'db-details');
      ed.appendChild(el('summary', '', t('Ergänzen: Priorität, Sicherungen, eigene Schritte')));
      ed.addEventListener('toggle', () => { if (ed.open && ed.children.length === 1) ed.appendChild(editForm(s)); }, { once: false });
      body.appendChild(ed);
    }
    det.appendChild(body);
    return det;
  }

  function render() {
    renderSummary();
    renderGeneral();
    const list = $('hb-list');
    list.innerHTML = '';
    if (!data.services.length) list.appendChild(el('p', 'field-hint', t('Noch keine Dienste – Compose-Stacks und Steckbriefe erscheinen hier von selbst.')));
    data.services.forEach(s => list.appendChild(row(s)));
  }

  $('hb-md').addEventListener('click', () => setTimeout(load, 1500));
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    try { data = await api('/api/docs/handbook'); render(); } catch (e) { $('hb-list').appendChild(el('p', 'form-msg error', e.message)); }
  })();

  async function load() {
    try { data = await api('/api/docs/handbook'); render(); } catch (e) { /* keep the old view */ }
  }
})();
