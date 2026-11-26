/**
 * HostCore Dashboard - compliance section "Grundschutz-Check": requirements per area with automatic
 * rating and evidence, manual assessment (state, note, evidence), fulfilment per area.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('bl-areas')) return;
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

  // ---------------------------------------------------------------------------
  // Filter: what is shown. Default: what needs doing (not met, partly met, open, contradicted)
  // ---------------------------------------------------------------------------
  const FILTER_KEY = 'baselineFilter';
  const FILTERS = [
    ['todo', 'Handlungsbedarf', i => ['fail', 'partial', 'open'].includes(i.state) || i.conflict],
    ['fail', 'Nicht erfüllt', i => i.state === 'fail'],
    ['partial', 'Teilweise', i => i.state === 'partial'],
    ['open', 'Offen', i => i.state === 'open'],
    ['ok', 'Erfüllt', i => i.state === 'ok'],
    ['na', 'Entbehrlich', i => i.state === 'na'],
    ['all', 'Alle', () => true]
  ];
  let filter = 'todo';
  try { const f = localStorage.getItem(FILTER_KEY); if (FILTERS.some(x => x[0] === f)) filter = f; } catch (e) { /* storage blocked */ }
  const LEVEL = { ok: 'good', partial: 'warning', fail: 'critical', open: 'idle', na: 'idle' };

  function meter(score, cls = '') {
    const m = el('div', `blx-meter ${cls}`);
    const fill = el('span', score == null ? '' : score >= 80 ? 'good' : score >= 50 ? 'warning' : 'critical');
    fill.style.width = `${score || 0}%`;
    m.appendChild(fill);
    return m;
  }

  function row(item) {
    const isOpen = open.has(item.id);
    const r = el('div', `blx-row${isOpen ? ' open' : ''}`);
    const head = el('button', 'blx-head');
    head.type = 'button';
    head.setAttribute('aria-expanded', String(isOpen));
    head.append(el('span', `blx-led ${LEVEL[item.state] || 'idle'}`), raw('span', 'blx-id', item.id));
    const main = el('span', 'blx-main');
    const title = el('span', 'blx-title', t(item.title));
    main.appendChild(title);
    const line = item.manual
      ? `${t('Von Hand')}: ${[item.manual.note, item.manual.evidence].filter(Boolean).join(' · ') || t(item.state_label)}`
      : item.auto_evidence ? t(item.auto_evidence) : !item.automatic ? t('Nur von Hand zu bewerten.') : '';
    if (line) main.appendChild(raw('span', 'blx-evidence', line));
    head.appendChild(main);
    const state = el('span', `blx-state ${LEVEL[item.state] || ''}`, t(item.state_label));
    head.appendChild(state);
    if (item.conflict) head.appendChild(el('span', 'blx-flag', t('Prüfung widerspricht')));
    head.addEventListener('click', () => { isOpen ? open.delete(item.id) : open.add(item.id); render(); });
    r.appendChild(head);
    if (isOpen) {
      const det = el('div', 'blx-detail');
      const meta = el('div', 'blx-meta');
      meta.append(raw('span', '', `${t('Baustein')} ${item.module}`));
      if (item.automatic) meta.append(el('span', '', item.auto_state ? `${t('Automatisch')}: ${t(item.auto_evidence || '')}` : t('Automatisch nicht ermittelbar')));
      if (item.manual) meta.append(raw('span', '', `${t('Von Hand')} (${item.manual.user}, ${fmtDate(item.manual.ts)})`));
      det.append(el('p', 'blx-desc', t(item.description)), meta);
      if (can('settings.manage')) det.appendChild(assessForm(item));
      r.appendChild(det);
    }
    return r;
  }

  function render() {
    $('bl-checked').textContent = data.checked ? `${t('Bewertet')} ${new Date(data.checked * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' })}` : '';
    // Head: the reading, its meter, and the counts as filters
    const sum = $('bl-summary');
    sum.innerHTML = '';
    sum.className = 'blx-summary';
    const reading = el('div', 'blx-reading');
    reading.append(raw('strong', '', data.score == null ? '–' : `${data.score} %`), el('span', '', t('Erfüllungsgrad')), meter(data.score, 'big'));
    const chips = el('div', 'blx-filters');
    chips.setAttribute('role', 'group');
    chips.setAttribute('aria-label', t('Anzeigen'));
    FILTERS.forEach(([id, label, fn]) => {
      const n = data.items.filter(fn).length;
      if (!n && id !== 'todo' && id !== 'all' && id !== filter) return;
      const b = el('button', `blx-chip${id === filter ? ' active' : ''}${id === 'fail' && n ? ' critical' : ''}`);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(id === filter));
      b.append(el('span', '', t(label)), raw('b', '', String(n)));
      b.addEventListener('click', () => {
        filter = id;
        try { localStorage.setItem(FILTER_KEY, id); } catch (e) { /* storage blocked */ }
        render();
      });
      chips.appendChild(b);
    });
    sum.append(reading, chips);

    const fn = (FILTERS.find(x => x[0] === filter) || FILTERS[0])[2];
    const areas = $('bl-areas');
    areas.innerHTML = '';
    let shown = 0;
    data.areas.forEach(a => {
      const items = data.items.filter(i => i.area === a.name && fn(i));
      if (!items.length) return;
      shown += items.length;
      const box = el('div', 'blx-area');
      const head = el('div', 'blx-area-head');
      const facts = [a.failed ? `${a.failed} ${t('nicht erfüllt')}` : '', a.open ? `${a.open} ${t('offen')}` : ''].filter(Boolean).join(' · ') || t('nichts offen');
      head.append(el('h3', '', t(a.name)), raw('span', 'blx-area-facts', facts), meter(a.score), raw('span', 'blx-area-score', a.score == null ? '–' : `${a.score} %`));
      box.appendChild(head);
      items.forEach(i => box.appendChild(row(i)));
      areas.appendChild(box);
    });
    if (!shown) {
      const empty = el('div', 'blx-empty');
      empty.appendChild(el('p', '', filter === 'todo' ? t('Kein Handlungsbedarf – alle Anforderungen sind erfüllt oder entbehrlich.') : t('Keine Anforderung in dieser Auswahl.')));
      areas.appendChild(empty);
    } else if (filter !== 'all') {
      const rest = data.items.length - shown;
      if (rest) {
        const more = el('button', 'blx-more', `${rest} ${t(rest === 1 ? 'weitere Anforderung ausgeblendet – alle zeigen' : 'weitere Anforderungen ausgeblendet – alle zeigen')}`);
        more.type = 'button';
        more.addEventListener('click', () => { filter = 'all'; try { localStorage.setItem(FILTER_KEY, 'all'); } catch (e) { /* ignore */ } render(); });
        areas.appendChild(more);
      }
    }
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
