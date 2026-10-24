/**
 * PiCore Dashboard - compliance section "Grundschutz-Check": requirements per area with automatic
 * rating and evidence, manual assessment (state, note, evidence), fulfilment per area.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('bl-areas')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const CLS = { ok: 'good', partial: 'warning', fail: 'critical', na: '', open: '' };
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

  const fmtDate = ts => new Date(ts * 1000).toLocaleDateString(LOCALE);

  function assessForm(item) {
    const form = el('form', 'bl-assess');
    form.autocomplete = 'off';
    const state = el('select');
    [['', '– automatisch / offen –'], ['ok', 'Erfüllt'], ['partial', 'Teilweise'], ['fail', 'Nicht erfüllt'], ['na', 'Entbehrlich']]
      .forEach(([k, l]) => { const o = el('option', '', t(l)); o.value = k; state.appendChild(o); });
    state.value = item.manual ? item.manual.state : '';
    const note = el('input');
    note.type = 'text';
    note.placeholder = t('Notiz / Begründung');
    note.value = item.manual ? item.manual.note : '';
    const ev = el('input');
    ev.type = 'text';
    ev.placeholder = t('Nachweis (Dokument, Ablageort, Datum)');
    ev.value = item.manual ? item.manual.evidence : '';
    const save = el('button', 'btn-secondary', t('Bewerten'));
    save.type = 'submit';
    form.append(state, note, ev, save);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      try { data = await api('/api/compliance/baseline/assess', { id: item.id, state: state.value, note: note.value, evidence: ev.value }); render(); toast(t('Gespeichert.'), 'success'); }
      catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    return form;
  }

  function row(item) {
    const r = el('div', `bl-item ${item.state}`);
    const head = el('div', 'bl-head');
    head.append(raw('span', 'bl-id', item.id), el('span', `health-tag ${CLS[item.state]}`, t(item.state_label)), el('strong', 'bl-title', t(item.title)),
      raw('span', 'bl-module', item.module));
    if (item.conflict) head.appendChild(el('span', 'health-tag critical', t('Prüfung widerspricht')));
    const toggle = el('button', 'text-btn', t(open.has(item.id) ? 'Weniger' : 'Details'));
    toggle.type = 'button';
    toggle.addEventListener('click', () => { open.has(item.id) ? open.delete(item.id) : open.add(item.id); render(); });
    head.appendChild(toggle);
    r.appendChild(head);
    if (item.auto_evidence) {
      const ev = el('div', 'bl-evidence');
      ev.append(el('span', 'field-hint', `${t('Automatisch')}: `), el('span', '', t(item.auto_evidence)));
      r.appendChild(ev);
    } else if (!item.automatic) {
      r.appendChild(el('div', 'bl-evidence field-hint', t('Nur von Hand zu bewerten.')));
    }
    if (item.manual) {
      const m = item.manual;
      r.appendChild(raw('div', 'bl-evidence', `${t('Von Hand')} (${m.user}, ${fmtDate(m.ts)}): ${[m.note, m.evidence].filter(Boolean).join(' · ') || '–'}`));
    }
    if (open.has(item.id)) {
      r.appendChild(el('p', 'field-hint', t(item.description)));
      if (can('settings.manage')) r.appendChild(assessForm(item));
    }
    return r;
  }

  function render() {
    $('bl-checked').textContent = data.checked ? `${t('Bewertet')} ${new Date(data.checked * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' })}` : '';
    const sum = $('bl-summary');
    sum.innerHTML = '';
    const tile = (label, value, level) => { const d = el('div', `hb-tile ${level || ''}`); d.append(raw('strong', '', value), el('span', '', t(label))); sum.appendChild(d); };
    tile('Erfüllungsgrad', data.score == null ? '–' : `${data.score} %`, data.score == null ? '' : data.score >= 80 ? 'good' : data.score >= 50 ? 'warning' : 'critical');
    tile('Erfüllt', String(data.counts.ok), 'good');
    tile('Teilweise', String(data.counts.partial), data.counts.partial ? 'warning' : '');
    tile('Nicht erfüllt', String(data.counts.fail), data.counts.fail ? 'critical' : 'good');
    tile('Offen', String(data.counts.open));
    const areas = $('bl-areas');
    areas.innerHTML = '';
    data.areas.forEach(a => {
      const box = el('div', 'bl-area');
      const head = el('div', 'bl-area-head');
      head.append(el('h3', 'subhead', t(a.name)));
      const bar = el('div', 'bl-bar');
      const fill = el('span', a.score == null ? '' : a.score >= 80 ? 'good' : a.score >= 50 ? 'warning' : 'critical');
      fill.style.width = `${a.score || 0}%`;
      bar.appendChild(fill);
      head.append(bar, raw('span', 'bl-score', a.score == null ? '–' : `${a.score} %`));
      box.appendChild(head);
      data.items.filter(i => i.area === a.name).forEach(i => box.appendChild(row(i)));
      areas.appendChild(box);
    });
  }

  async function load(refresh) {
    try { data = await api(`/api/compliance/baseline${refresh ? '?refresh=1' : ''}`); render(); } catch (e) { /* no access */ }
  }

  $('bl-refresh').addEventListener('click', async () => {
    const b = $('bl-refresh');
    b.disabled = true;
    await load(true);
    b.disabled = false;
  });
  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('system.view')) return;
    load(false);
  })();
})();
