/**
 * HostCore Dashboard - compliance sections: NIS-2 reporting deadlines, policies with acknowledgement,
 * supplier register and the internal audit programme.
 */
(function () {
  'use strict';

  const { t, can, el, raw, toast, api, date, time, tag, button, confirmButton, run, form, showForm, ask, lines } = window.OpsUI;
  const $ = id => document.getElementById(id);
  if (!$('n2-list')) return;
  const edit = 'notes.edit';

  function card(cls, status, level, title, sub, isOpen, toggle) {
    const c = el('div', `inc-card ${cls}`);
    const head = el('div', 'inc-head');
    head.append(tag(level, status), raw('strong', 'inc-title', title), raw('span', 'field-hint', sub), button(isOpen ? 'Weniger' : 'Details', 'text-btn', toggle));
    c.appendChild(head);
    return c;
  }

  function detail(c, label, text) {
    if (!text) return;
    const d = el('div', 'chg-detail');
    d.append(el('strong', '', t(label)), raw('p', 'inc-summary', text));
    c.appendChild(d);
  }

  function timeline(c, items) {
    const tl = el('ol', 'inc-timeline');
    [...(items || [])].reverse().forEach(u => {
      const li = el('li');
      li.append(raw('span', 'inc-when', `${time(u.ts)} · ${u.user}`), raw('span', '', u.text));
      tl.appendChild(li);
    });
    c.appendChild(tl);
  }

  function left(sec) {
    const abs = Math.abs(sec);
    const text = abs >= 172800 ? `${Math.round(abs / 86400)} ${t('Tage')}` : `${Math.round(abs / 3600)} ${t('Std.')}`;
    return sec < 0 ? `${t('überfällig seit')} ${text}` : `${t('noch')} ${text}`;
  }

  // ---------------------------------------------------------------------------
  // NIS-2
  // ---------------------------------------------------------------------------
  let n2 = null;
  const n2Open = new Set();

  function caseForm(c, preset) {
    const incidents = [['', '– keine –'], ...n2.incidents.map(i => [i.id, `${i.title} (${date(i.started)})`])];
    showForm($('n2-form'), c ? 'Fall bearbeiten' : 'Fall anlegen', [
      { key: 'title', label: 'Titel', full: true },
      { key: 'aware', label: 'Kenntnis seit (Beginn der Frist)', type: 'datetime' },
      { key: 'status', label: 'Einstufung', type: 'select', options: [['pruefung', 'In Prüfung'], ['meldepflichtig', 'Meldepflichtig'], ['nicht_meldepflichtig', 'Nicht meldepflichtig']], value: 'pruefung' },
      { key: 'incident_id', label: 'Zugehörige Störung', type: 'select', options: incidents },
      { key: 'handled', label: 'Bearbeitung abgeschlossen am', type: 'datetime' },
      { key: 'criteria', label: 'Warum erheblich?', type: 'checks', options: Object.entries(n2.criteria) },
      { key: 'reason', label: 'Begründung der Einstufung', type: 'textarea', rows: 2 },
      { key: 'malicious', label: 'Verdacht auf rechtswidrige oder böswillige Handlung', type: 'check' },
      { key: 'cross_border', label: 'Grenzüberschreitende Auswirkungen möglich', type: 'check' },
      { key: 'personal_data', label: 'Personenbezogene Daten betroffen (Art. 33 DSGVO)', type: 'check' },
      { key: 'description', label: 'Beschreibung', type: 'textarea' },
      { key: 'impact', label: 'Schweregrad und Auswirkungen', type: 'textarea', rows: 2 },
      { key: 'indicators', label: 'Kompromittierungsindikatoren', type: 'textarea', rows: 2 },
      { key: 'cause', label: 'Ursache', type: 'textarea', rows: 2 },
      { key: 'measures', label: 'Abhilfemaßnahmen', type: 'textarea', rows: 2 }
    ], c || preset || { aware: Math.floor(Date.now() / 1000) }, 'Speichern', async d => {
      n2 = await api(c ? `/api/nis2/${c.id}` : '/api/nis2', d);
      if (n2.saved) n2Open.add(n2.saved);
      renderNis2();
    });
  }

  function reportForm(box, c, d) {
    box.innerHTML = '';
    box.appendChild(form([
      { key: 'submitted', label: 'Eingereicht am', type: 'datetime' },
      { key: 'reference', label: 'Referenz / Aktenzeichen' },
      { key: 'note', label: 'Notiz', full: true }
    ], { submitted: Math.floor(Date.now() / 1000) }, 'Eintragen', async v => {
      n2 = await api(`/api/nis2/${c.id}/report`, { ...v, stage: d.stage });
      renderNis2();
    }, () => renderNis2()));
  }

  function renderCase(c) {
    const isOpen = n2Open.has(c.id);
    const level = c.status === 'meldepflichtig' ? 'critical' : c.status === 'pruefung' ? 'warning' : '';
    const box = card(c.open ? 'open schwer' : 'resolved', c.status_label, level, c.title, `${t('Kenntnis')} ${time(c.aware)}`, isOpen, () => { isOpen ? n2Open.delete(c.id) : n2Open.add(c.id); renderNis2(); });
    if (c.deadlines.length) {
      const ul = el('ul', 'go-commits');
      c.deadlines.forEach(d => {
        const li = el('li');
        const state = d.submitted ? tag(d.late ? 'warning' : 'good', d.late ? 'Verspätet eingereicht' : 'Eingereicht') : tag(d.state === 'overdue' ? 'critical' : d.state === 'soon' ? 'warning' : '', left(d.left));
        li.append(state, el('span', 'go-subject', t(d.label)), raw('span', 'go-meta', d.submitted ? `${time(d.submitted)} · ${d.reference || d.by}` : `${t('Frist')} ${time(d.due)}`));
        const form2 = el('div', 'full');
        if (can(edit)) {
          const a = el('span', 'ops-actions');
          const link = el('a', 'text-btn', t('Entwurf'));
          link.href = `/api/nis2/${c.id}/draft/${d.stage}`;
          link.target = '_blank';
          link.rel = 'noopener';
          a.appendChild(link);
          if (!d.submitted && c.open) a.appendChild(button('Eingereicht', 'text-btn', () => reportForm(form2, c, d)));
          li.appendChild(a);
        }
        li.appendChild(form2);
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    if (isOpen) {
      if (c.criteria.length) detail(box, 'Warum erheblich?', c.criteria.map(x => n2.criteria[x]).join(', '));
      [['Begründung der Einstufung', c.reason], ['Beschreibung', c.description], ['Schweregrad und Auswirkungen', c.impact], ['Kompromittierungsindikatoren', c.indicators], ['Ursache', c.cause], ['Abhilfemaßnahmen', c.measures]].forEach(([l, v]) => detail(box, l, v));
      timeline(box, c.timeline);
      if (can(edit)) {
        const row = el('div', 'docs-row-actions inc-row-actions');
        if (c.status !== 'abgeschlossen') {
          row.append(button('Bearbeiten', 'text-btn', () => caseForm(c)),
            button('Fall abschließen', 'text-btn', async () => { const r = await run(() => api(`/api/nis2/${c.id}/close`, {})); if (r) { n2 = r; renderNis2(); } }));
        }
        if (!Object.keys(c.reports || {}).length) row.appendChild(confirmButton('Löschen', 'text-btn danger', async () => { const r = await run(() => api(`/api/nis2/${c.id}/delete`, {})); if (r) { n2 = r; renderNis2(); } }));
        box.appendChild(row);
      }
    }
    return box;
  }

  function renderNis2() {
    const box = $('n2-list');
    box.innerHTML = '';
    n2.cases.forEach(c => box.appendChild(renderCase(c)));
    if (!n2.cases.length) box.appendChild(el('p', 'field-hint', t('Keine Fälle erfasst.')));
    const p = $('n2-profile');
    if (!p.childElementCount) {
      const f = form([
        { key: 'entity', label: 'Einstufung', type: 'select', options: Object.entries(n2.entities) },
        { key: 'sector', label: 'Sektor' },
        { key: 'contact', label: 'Ansprechstelle' },
        { key: 'phone', label: 'Telefon (24/7)' }
      ], n2, 'Speichern', async d => {
        n2 = await api('/api/nis2/profile', d);
        toast(t('Gespeichert.'), 'success');
      });
      p.appendChild(f);
    }
  }

  // ---------------------------------------------------------------------------
  // Policies
  // ---------------------------------------------------------------------------
  let po = null;
  const poOpen = new Map();

  async function poForm(p) {
    let full = p;
    if (p) { full = await run(() => api(`/api/policies/${p.id}`)); if (!full) return; }
    showForm($('po-form'), p ? 'Neue Fassung bearbeiten' : 'Richtlinie anlegen', [
      { key: 'title', label: 'Titel', full: true },
      { key: 'category', label: 'Kategorie', type: 'select', options: po.categories.map(c => [c, c]) },
      { key: 'owner', label: 'Verantwortlich' },
      { key: 'review_months', label: 'Prüfintervall (Monate, 0 = keines)', type: 'number', value: 12 },
      { key: 'groups', label: 'Gilt für (keine Auswahl = alle)', type: 'checks', options: po.groups.map(g => [g.id, g.name]) },
      { key: 'draft', label: 'Text (Markdown)', type: 'textarea', rows: 12 },
      { key: 'draft_note', label: 'Was hat sich geändert?', full: true }
    ], full ? { ...full, draft: full.draft || full.text } : {}, 'Entwurf speichern', async d => {
      po = await api(p ? `/api/policies/${p.id}` : '/api/policies', d);
      renderPolicies();
    });
  }

  function policyAction(p, act, body = {}, ok) {
    return run(async () => { po = await api(`/api/policies/${p.id}/${act}`, body); poOpen.delete(p.id); renderPolicies(); }, ok);
  }

  function renderPolicy(p) {
    const isOpen = poOpen.has(p.id);
    const level = p.review_overdue ? 'warning' : p.state === 'freigegeben' ? 'good' : p.state === 'entwurf' ? 'warning' : '';
    const label = p.state === 'freigegeben' ? `Version ${p.version}` : p.state === 'entwurf' ? 'Entwurf' : 'Archiviert';
    const box = card(p.mine ? 'open mittel' : 'resolved', label, level, p.title, `${t(p.category)} · ${p.acked} / ${p.audience} ${t('Kenntnisnahmen')}`, isOpen, async () => {
      if (isOpen) poOpen.delete(p.id);
      else { const full = await run(() => api(`/api/policies/${p.id}`)); if (full) poOpen.set(p.id, full); }
      renderPolicies();
    });
    if (p.submitted) box.appendChild(el('div', 'db-problem warning', t('Neue Fassung wartet auf Freigabe.')));
    if (p.mine) {
      const d = el('div', 'db-problem warning');
      d.append(el('span', '', `${t('Bitte lesen und bestätigen')}: `), button('Lesen', 'text-btn', async () => { poOpen.set(p.id, await api(`/api/policies/${p.id}`)); renderPolicies(); }));
      box.appendChild(d);
    }
    const full = poOpen.get(p.id);
    if (full) {
      if (full.text) box.appendChild(raw('p', 'inc-summary', full.text));
      if (full.mine) box.appendChild(button('Gelesen und zur Kenntnis genommen', 'btn-primary', () => policyAction(p, 'acknowledge', {}, 'Bestätigt.')));
      if (full.sha256) box.appendChild(raw('div', 'lan-sub mono', `SHA-256 ${full.sha256}`));
      if (full.approved_by) box.appendChild(el('div', 'lan-sub', `${t('Freigabe')}: ${full.approved_by}, ${date(full.approved_at)}${full.self_approval ? ` (${t('Selbstfreigabe')})` : ''}${full.review_due ? ` · ${t('Nächste Prüfung')}: ${date(full.review_due)}` : ''}`));
      if (full.missing.length) box.appendChild(raw('div', 'lan-sub', `${t('Offen')}: ${full.missing.join(', ')}`));
      if (can(edit)) {
        const row = el('div', 'docs-row-actions inc-row-actions');
        row.appendChild(button('Bearbeiten', 'text-btn', () => poForm(p)));
        if (full.draft && !full.submitted) row.appendChild(button('Zur Freigabe einreichen', 'text-btn', () => policyAction(p, 'submit')));
        if (full.submitted && po.can_approve) row.appendChild(button('Freigeben', 'text-btn', () => policyAction(p, 'release', {}, 'Freigegeben.')));
        if (p.state === 'freigegeben') row.append(button('Geprüft, keine Änderung', 'text-btn', () => policyAction(p, 'review', {}, 'Prüfung vermerkt.')), button('Archivieren', 'text-btn', () => policyAction(p, 'retire', { on: true })));
        if (p.state === 'archiviert') row.appendChild(button('Wieder in Kraft setzen', 'text-btn', () => policyAction(p, 'retire', { on: false })));
        if (!p.version) row.appendChild(confirmButton('Löschen', 'text-btn danger', () => policyAction(p, 'delete')));
        box.appendChild(row);
      }
    }
    return box;
  }

  function renderPolicies() {
    const box = $('po-list');
    box.innerHTML = '';
    po.policies.forEach(p => box.appendChild(renderPolicy(p)));
    if (!po.policies.length) box.appendChild(el('p', 'field-hint', t('Noch keine Richtlinien.')));
  }

  // ---------------------------------------------------------------------------
  // Suppliers
  // ---------------------------------------------------------------------------
  let sp = null;
  const spOpen = new Set();

  function supplierForm(s) {
    showForm($('su2-form'), s ? 'Dienstleister bearbeiten' : 'Dienstleister erfassen', [
      { key: 'name', label: 'Name', full: true },
      { key: 'service', label: 'Leistung' },
      { key: 'contact', label: 'Kontakt' },
      { key: 'criticality', label: 'Kritikalität', type: 'select', options: Object.entries(sp.criticality), value: 'mittel' },
      { key: 'location', label: 'Standort' },
      { key: 'personal_data', label: 'Verarbeitet personenbezogene Daten im Auftrag', type: 'check' },
      { key: 'third_country', label: 'Datenverarbeitung außerhalb der EU', type: 'check' },
      { key: 'dpa_signed', label: 'AV-Vertrag vom', type: 'date' },
      { key: 'dpa_until', label: 'AV-Vertrag bis', type: 'date' },
      { key: 'certificates', label: 'Zertifikate', placeholder: 'ISO 27001, SOC 2' },
      { key: 'cert_until', label: 'Zertifikat gültig bis', type: 'date' },
      { key: 'next_review', label: 'Nächste Prüfung (sonst nach Kritikalität)', type: 'date' },
      { key: 'sub_processors', label: 'Unterauftragnehmer', type: 'textarea', rows: 2 },
      { key: 'exit_plan', label: 'Ausstiegsplan', type: 'textarea', rows: 2 },
      { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
      { key: 'services', label: 'Abhängige Dienste', type: 'checks', options: sp.targets.map(x => [x.target, x.name]) }
    ], s || {}, 'Speichern', async d => {
      sp = await api(s ? `/api/suppliers/${s.id}` : '/api/suppliers', d);
      renderSuppliers();
    });
  }

  function renderSupplier(s) {
    const isOpen = spOpen.has(s.id);
    const worst = s.issues.some(i => i.level === 'critical') ? 'critical' : s.issues.some(i => i.level === 'warning') ? 'warning' : '';
    const box = card(worst ? 'open mittel' : 'resolved', s.criticality_label, worst, s.name, `${s.service || ''}${s.service ? ' · ' : ''}${t('Nächste Prüfung')} ${s.next_review_date}`, isOpen, () => { isOpen ? spOpen.delete(s.id) : spOpen.add(s.id); renderSuppliers(); });
    s.issues.forEach(i => box.appendChild(el('div', `db-problem ${i.level === 'info' ? '' : i.level}`, t(i.text))));
    if (s.service_names.length) {
      const w = el('div', 'fleet-pis');
      s.service_names.forEach(n => w.appendChild(raw('span', 'pg-chip', n)));
      box.appendChild(w);
    }
    if (isOpen) {
      [['Kontakt', s.contact], ['Standort', s.location], ['AV-Vertrag', s.dpa_signed && `${s.dpa_signed}${s.dpa_until ? ` – ${s.dpa_until}` : ''}`], ['Zertifikate', s.certificates && `${s.certificates}${s.cert_until ? ` (${s.cert_until})` : ''}`],
        ['Unterauftragnehmer', s.sub_processors], ['Ausstiegsplan', s.exit_plan], ['Notizen', s.notes]].forEach(([l, v]) => detail(box, l, v));
      if (s.reviews.length) {
        const ul = el('ul', 'go-commits');
        [...s.reviews].reverse().forEach(r => {
          const li = el('li');
          li.append(tag(r.result === 'ok' ? 'good' : r.result === 'kritisch' ? 'critical' : 'warning', r.result_label), raw('span', 'go-subject', r.note || '–'), raw('span', 'go-meta', `${r.date} · ${r.user}`));
          ul.appendChild(li);
        });
        box.appendChild(ul);
      }
      if (can(edit)) {
        const holder = el('div');
        const row = el('div', 'docs-row-actions inc-row-actions');
        row.append(button('Bearbeiten', 'text-btn', () => supplierForm(s)),
          button('Prüfung eintragen', 'text-btn', () => {
            holder.innerHTML = '';
            holder.appendChild(form([
              { key: 'date', label: 'Datum', type: 'date', value: new Date().toISOString().slice(0, 10) },
              { key: 'result', label: 'Ergebnis', type: 'select', options: Object.entries(sp.results), value: 'ok' },
              { key: 'note', label: 'Notiz', full: true }
            ], {}, 'Speichern', async d => { sp = await api(`/api/suppliers/${s.id}/review`, d); renderSuppliers(); }, () => { holder.innerHTML = ''; }));
          }),
          confirmButton('Löschen', 'text-btn danger', async () => { const r = await run(() => api(`/api/suppliers/${s.id}/delete`, {})); if (r) { sp = r; renderSuppliers(); } }));
        box.append(row, holder);
      }
    }
    return box;
  }

  function renderSuppliers() {
    const box = $('su2-list');
    box.innerHTML = '';
    sp.suppliers.forEach(s => box.appendChild(renderSupplier(s)));
    if (!sp.suppliers.length) box.appendChild(el('p', 'field-hint', t('Noch keine Dienstleister erfasst.')));
  }

  // ---------------------------------------------------------------------------
  // Internal audits
  // ---------------------------------------------------------------------------
  let au = null;
  const auOpen = new Set();
  const RESULT_LEVEL = { offen: '', konform: 'good', beobachtung: '', gering: 'warning', schwer: 'critical', na: '' };

  function auditForm(a) {
    showForm($('au-form'), a ? 'Audit bearbeiten' : 'Audit planen', [
      { key: 'title', label: 'Titel', full: true },
      { key: 'planned', label: 'Termin', type: 'date' },
      { key: 'standard', label: 'Grundlage', placeholder: 'ISO 27001, BSI IT-Grundschutz' },
      { key: 'auditor', label: 'Auditor' },
      { key: 'responsible', label: 'Verantwortlich für den Bereich' },
      { key: 'scope', label: 'Umfang', type: 'textarea' }
    ], a || {}, 'Speichern', async d => {
      au = await api(a ? `/api/internal-audits/${a.id}` : '/api/internal-audits', d);
      if (au.saved) auOpen.add(au.saved);
      renderAudits();
    });
  }

  const auCall = (path, body, ok) => run(async () => { au = await api(path, body); renderAudits(); }, ok);

  function renderCheck(a, c) {
    const row = el('div', 'chg-detail');
    row.appendChild(raw('p', 'inc-summary', c.text));
    if (c.ref) row.appendChild(raw('span', 'lan-sub', c.ref));
    if (a.status === 'abgeschlossen' || !can(edit)) {
      row.appendChild(tag(RESULT_LEVEL[c.result], au.results[c.result]));
      if (c.note) row.appendChild(raw('p', 'lan-sub', c.note));
      return row;
    }
    const f = el('div', 'vm-manage-row');
    const sel = el('select');
    sel.setAttribute('aria-label', t('Ergebnis'));
    Object.entries(au.results).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; sel.appendChild(o); });
    sel.value = c.result;
    const note = el('input');
    note.type = 'text';
    note.value = c.note || '';
    note.placeholder = t('Notiz oder Nachweis');
    f.append(sel, note, button('Speichern', 'text-btn', () => auCall(`/api/internal-audits/${a.id}/checks/${c.id}`, { result: sel.value, note: note.value })));
    row.appendChild(f);
    return row;
  }

  function renderFinding(a, f) {
    const box = el('div', 'db-problem ' + (f.state === 'wirksam' ? '' : f.severity === 'schwer' ? 'critical' : 'warning'));
    box.append(tag(f.overdue ? 'critical' : '', f.state_label), raw('strong', '', ` ${f.title}`), raw('p', 'lan-sub', f.description || ''));
    if (f.state === 'wirksam') {
      box.appendChild(raw('p', 'lan-sub', `${f.action} · ${f.verified_by}: ${f.verified_note}`));
      return box;
    }
    if (!can(edit)) return box;
    box.appendChild(form([
      { key: 'owner', label: 'Verantwortlich' },
      { key: 'due', label: 'Frist', type: 'date' },
      { key: 'action', label: 'Korrekturmaßnahme', type: 'textarea', rows: 2 }
    ], f, 'Speichern', async d => { au = await api(`/api/internal-audits/${a.id}/findings/${f.id}`, d); renderAudits(); }));
    const row = el('div', 'docs-row-actions');
    row.appendChild(button('Wirksamkeit bestätigen', 'text-btn', () => ask(row, 'Woran ist die Wirksamkeit erkennbar?', 'Schließen', note => auCall(`/api/internal-audits/${a.id}/findings/${f.id}/close`, { note }))));
    box.appendChild(row);
    return box;
  }

  function renderAudit(a) {
    const isOpen = auOpen.has(a.id);
    const level = a.status === 'abgeschlossen' ? 'good' : a.overdue ? 'warning' : '';
    const box = card(a.status === 'abgeschlossen' ? 'resolved' : 'open mittel', a.status_label, level, a.title, `${a.planned} · ${a.auditor} · ${a.rated} / ${a.total} · ${a.open_findings} ${t('offen')}`, isOpen, () => { isOpen ? auOpen.delete(a.id) : auOpen.add(a.id); renderAudits(); });
    if (a.conflict) box.appendChild(el('div', 'db-problem warning', t('Auditor prüft den eigenen Bereich – die Unabhängigkeit ist nicht gegeben.')));
    if (isOpen) {
      detail(box, 'Grundlage', a.standard);
      detail(box, 'Umfang', a.scope);
      detail(box, 'Fazit', a.summary);
      a.checks.forEach(c => box.appendChild(renderCheck(a, c)));
      if (a.findings.length) box.appendChild(el('h4', 'subhead', t('Feststellungen')));
      a.findings.forEach(f => box.appendChild(renderFinding(a, f)));
      const row = el('div', 'docs-row-actions inc-row-actions');
      const report = el('a', 'text-btn', t('Bericht'));
      report.href = `/api/internal-audits/markdown?id=${a.id}`;
      report.target = '_blank';
      report.rel = 'noopener';
      row.appendChild(report);
      if (a.status !== 'abgeschlossen' && can(edit)) {
        row.append(button('Bearbeiten', 'text-btn', () => auditForm(a)),
          button('Prüfpunkte aus dem Grundschutz-Check', 'text-btn', () => auCall(`/api/internal-audits/${a.id}/checks`, { baseline: true })),
          button('Prüfpunkte hinzufügen', 'text-btn', () => ask(row, 'Prüfpunkte, durch ; getrennt', 'Hinzufügen', v => auCall(`/api/internal-audits/${a.id}/checks`, { texts: lines(v.replace(/;/g, '\n')) }))),
          button('Audit abschließen', 'text-btn', () => ask(row, 'Fazit', 'Abschließen', v => auCall(`/api/internal-audits/${a.id}/finish`, { summary: v }))),
          confirmButton('Löschen', 'text-btn danger', () => auCall(`/api/internal-audits/${a.id}/delete`, {})));
      }
      box.appendChild(row);
    }
    return box;
  }

  function renderAudits() {
    const box = $('au-list');
    box.innerHTML = '';
    au.audits.forEach(a => box.appendChild(renderAudit(a)));
    if (!au.audits.length) box.appendChild(el('p', 'field-hint', t('Noch keine Audits geplant.')));
  }

  async function load() {
    if (window.PiAuth) await window.PiAuth.ready;
    const [a, b, c, d] = await Promise.allSettled([api('/api/nis2'), api('/api/policies'), api('/api/suppliers'), api('/api/internal-audits')]);
    if (a.status === 'fulfilled') { n2 = a.value; renderNis2(); }
    if (b.status === 'fulfilled') { po = b.value; renderPolicies(); }
    if (c.status === 'fulfilled') { sp = c.value; renderSuppliers(); }
    if (d.status === 'fulfilled') { au = d.value; renderAudits(); }
  }

  $('n2-add').addEventListener('click', () => caseForm(null));
  $('po-add').addEventListener('click', () => poForm(null));
  $('su2-add').addEventListener('click', () => supplierForm(null));
  $('au-add').addEventListener('click', () => auditForm(null));
  load();
})();
