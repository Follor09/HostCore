/**
 * PiCore Dashboard - power budget card: a monthly budget (kWh) with consumption so far,
 * a pace marker ("where you should be by today"), the forecast for the month, the
 * result of past months and a few saving hints.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const card = $('energy-card');
  if (!card) return;
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const kwh = v => `${Number(v).toLocaleString(LOCALE, { minimumFractionDigits: v < 10 ? 2 : 1, maximumFractionDigits: v < 10 ? 2 : 1 })} kWh`;
  let data = null;
  let editing = false;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  function budgetForm(value) {
    const form = el('form', 'energy-form');
    const input = el('input');
    input.type = 'number';
    input.min = '0.1';
    input.max = '1000';
    input.step = '0.1';
    input.required = true;
    input.value = String(value);
    input.setAttribute('aria-label', t('Budget in kWh'));
    const unit = el('span', 'energy-unit', 'kWh');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    form.append(input, unit, save);
    if (data.budget_kwh) {
      const off = el('button', 'text-btn', t('Entfernen'));
      off.type = 'button';
      off.addEventListener('click', () => saveBudget(0));
      form.appendChild(off);
    }
    form.addEventListener('submit', e => { e.preventDefault(); saveBudget(+input.value); });
    return form;
  }

  async function saveBudget(v) {
    try {
      const res = await fetch('/api/power/budget', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kwh: v }) });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
      data = json;
      editing = false;
      render();
      toast(v ? `${t('Strombudget')}: ${kwh(v)}` : t('Strombudget entfernt.'), 'success');
    } catch (err) { toast(err.message, 'error'); }
  }

  function render() {
    const body = $('energy-body');
    body.innerHTML = '';
    const tag = $('energy-status');
    tag.hidden = true;
    if (!data.available) {
      body.appendChild(el('p', 'notes-empty', t('Noch keine Stromdaten – der Pi 5 misst jede Minute, nach einigen Stunden liegen Werte vor.')));
      return;
    }
    if (!data.budget_kwh || editing) {
      const intro = el('p', 'energy-intro', data.budget_kwh ? t('Neues Monatsbudget:')
        : `${t('Prognose für diesen Monat')}: ${kwh(data.projected_kwh)} (${t('bisher')} ${data.month_cost.toLocaleString(LOCALE, { maximumFractionDigits: 2 })} ${data.currency}). ${t('Monatsbudget festlegen')}:`);
      body.appendChild(intro);
      if (can('settings.manage')) body.appendChild(budgetForm(data.budget_kwh || data.suggested_kwh));
      else body.appendChild(el('p', 'field-hint', t('Ein Budget kann festlegen, wer Einstellungen ändern darf.')));
      return;
    }

    const over = data.month_kwh > data.budget_kwh;
    const status = over ? ['over', 'critical', t('Überschritten')] : data.on_track ? ['ok', 'good', t('Im Budget')] : ['warn', 'warning', t('Prognose über Budget')];
    card.dataset.energy = status[0];
    tag.hidden = false;
    tag.className = `health-tag ${status[1]}`;
    tag.textContent = status[2];

    const bar = el('div', 'energy-bar');
    const fill = el('i', 'energy-fill');
    fill.style.width = `${Math.min(100, data.progress * 100)}%`;
    const proj = el('i', 'energy-proj');
    proj.style.width = `${Math.min(100, (data.projected_kwh / data.budget_kwh) * 100)}%`;
    const pace = el('span', 'energy-pace');
    pace.style.left = `${data.pace * 100}%`;
    pace.title = t('So viel dürfte bis heute verbraucht sein');
    bar.append(proj, fill, pace);
    body.appendChild(bar);

    const legend = el('div', 'energy-legend');
    legend.append(el('span', '', `${kwh(data.month_kwh)} / ${kwh(data.budget_kwh)}`),
      el('span', '', `${t('Prognose')} ${kwh(data.projected_kwh)}`));
    body.appendChild(legend);

    const facts = el('p', 'energy-facts', data.ahead_kwh >= 0
      ? `${kwh(data.ahead_kwh)} ${t('Vorsprung')} · ${t('noch')} ${Math.ceil(data.days_left)} ${t('Tage')} · ${t('max.')} ${kwh(data.per_day_left)}/${t('Tag')}`
      : `${kwh(-data.ahead_kwh)} ${t('über Plan')} · ${t('ab jetzt max.')} ${kwh(data.per_day_left)}/${t('Tag')} (Ø ${data.avg_w.toLocaleString(LOCALE, { maximumFractionDigits: 1 })} W)`);
    body.appendChild(facts);

    if (data.tips && data.tips.length && !data.on_track) {
      const tips = el('ul', 'energy-tips');
      data.tips.slice(0, 2).forEach(tip => {
        const li = el('li');
        li.appendChild(el(tip.link ? 'a' : 'span', '', tip.text));
        if (tip.link) li.firstChild.href = tip.link;
        tips.appendChild(li);
      });
      body.appendChild(tips);
    }

    const foot = el('div', 'card-foot-actions');
    if (data.history && data.history.length) {
      const hist = el('span', 'energy-history');
      hist.title = `${data.won} / ${data.history.length} ${t('Monate im Budget')}`;
      data.history.slice(-6).forEach(m => {
        const dot = el('i', m.ok ? 'ok' : 'miss');
        dot.title = `${m.month}: ${kwh(m.kwh)} / ${kwh(m.budget)}`;
        hist.appendChild(dot);
      });
      foot.appendChild(hist);
    }
    if (can('settings.manage')) {
      const edit = el('button', 'text-btn', t('Budget ändern'));
      edit.type = 'button';
      edit.addEventListener('click', () => { editing = true; render(); });
      foot.appendChild(edit);
    }
    body.appendChild(foot);
  }

  async function load() {
    try {
      const res = await fetch('/api/power/budget');
      if (!res.ok) return;
      data = await res.json();
      if (!editing) render();
    } catch (e) { /* offline */ }
  }

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (!window.PiAuth || window.PiAuth.can('system.view')) load();
  })();
  setInterval(() => { if (!document.hidden) load(); }, 15 * 60 * 1000);
})();
