/**
 * HostCore Dashboard - small helpers shared by the newer sections (VM operations, security watchers,
 * service catalog, compliance): elements, API calls, a form builder and confirm buttons.
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const box = document.getElementById('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 5000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname)}${location.hash}`; throw new Error('Nicht angemeldet.'); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      const e = new Error(json.detail || `Fehler ${res.status}`);
      e.reauth = !!json.reauth;
      throw e;
    }
    return json;
  }

  const date = ts => (ts ? new Date(ts * 1000).toLocaleDateString(LOCALE) : '–');
  const time = ts => (ts ? new Date(ts * 1000).toLocaleString(LOCALE, { dateStyle: 'short', timeStyle: 'short' }) : '–');

  function size(n) {
    if (n == null) return '–';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toLocaleString(LOCALE, { maximumFractionDigits: 1 })} ${units[i]}`;
  }

  const tag = (level, text) => el('span', `health-tag ${level || ''}`, t(text));

  function button(label, cls, onClick, perm) {
    const b = el('button', cls || 'text-btn', t(label));
    b.type = 'button';
    if (perm && !can(perm)) b.hidden = true;
    b.addEventListener('click', e => { e.stopPropagation(); onClick(b); });
    return b;
  }

  // Second click within 4 seconds confirms
  function confirmButton(label, cls, onClick, perm, question = 'Sicher?') {
    return button(label, cls, b => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = t(question);
        setTimeout(() => { if (b.isConnected) { b.classList.remove('confirming'); b.textContent = t(label); } }, 4000);
        return;
      }
      b.classList.remove('confirming');
      b.textContent = t(label);
      onClick(b);
    }, perm);
  }

  // An action with an error toast; returns the result or undefined
  async function run(fn, success) {
    try {
      const r = await fn();
      if (success) toast(t(success), 'success');
      return r;
    } catch (e) {
      toast(t(e.message), 'error');
      return undefined;
    }
  }

  /** spec: [{key, label, type: text|textarea|number|date|datetime|time|select|check|checks, full, options: [[value, label]], rows, placeholder}] */
  function form(spec, values, submitLabel, onSubmit, onCancel) {
    const f = el('form', 'inline-grid');
    f.autocomplete = 'off';
    const get = {};
    const toLocal = ts => { if (!ts) return ''; const d = new Date(ts * 1000); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
    spec.forEach(s => {
      const v = values[s.key];
      if (s.type === 'check') {
        const l = el('label', 'toggle-label full');
        const i = el('input');
        i.type = 'checkbox';
        i.checked = v === undefined ? !!s.value : !!v;
        l.append(i, ' ', el('span', '', t(s.label)));
        f.appendChild(l);
        get[s.key] = () => i.checked;
        return;
      }
      if (s.type === 'checks') {
        const box = el('div', 'login-field full');
        box.appendChild(el('span', '', t(s.label)));
        const wrap = el('div', 'inc-targets pb-checks');
        const chosen = new Set((v || []).map(String));
        s.options.forEach(([val, label]) => {
          const l = el('label', 'toggle-label');
          const i = el('input');
          i.type = 'checkbox';
          i.value = val;
          i.checked = chosen.has(String(val));
          l.append(i, ' ', raw('span', '', t(label)));
          wrap.appendChild(l);
        });
        if (!s.options.length) wrap.appendChild(el('span', 'field-hint', t('Keine vorhanden.')));
        box.appendChild(wrap);
        f.appendChild(box);
        get[s.key] = () => [...wrap.querySelectorAll('input:checked')].map(i => i.value);
        return;
      }
      const l = el('label', `login-field${s.full || s.type === 'textarea' ? ' full' : ''}`);
      l.appendChild(el('span', '', t(s.label)));
      let input;
      if (s.type === 'textarea') {
        input = el('textarea');
        input.rows = s.rows || 3;
        input.value = v || '';
      } else if (s.type === 'select') {
        input = el('select');
        s.options.forEach(([val, label]) => { const o = raw('option', '', t(label)); o.value = val; input.appendChild(o); });
        const pick = v === undefined || v === null ? s.value : v;
        if (pick !== undefined && [...input.options].some(o => o.value === String(pick))) input.value = String(pick);
      } else {
        input = el('input');
        input.type = s.type === 'datetime' ? 'datetime-local' : (s.type || 'text');
        input.value = s.type === 'datetime' ? toLocal(v) : (v === undefined || v === null ? (s.value ?? '') : v);
      }
      if (s.placeholder) input.placeholder = t(s.placeholder);
      input.spellcheck = false;
      l.appendChild(input);
      f.appendChild(l);
      get[s.key] = () => {
        if (s.type === 'datetime') return input.value ? Math.floor(new Date(input.value).getTime() / 1000) : 0;
        return s.type === 'number' ? (input.value === '' ? 0 : Number(input.value)) : input.value;
      };
    });
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t(submitLabel));
    save.type = 'submit';
    actions.appendChild(save);
    if (onCancel) actions.appendChild(button('Abbrechen', 'btn-secondary', onCancel));
    f.appendChild(actions);
    f.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      const data = {};
      Object.entries(get).forEach(([k, g]) => { data[k] = g(); });
      try { await onSubmit(data); } catch (err) { toast(t(err.message), 'error'); }
      save.disabled = false;
    });
    return f;
  }

  // Show a form in a wrapper element, with a heading
  function showForm(wrap, title, spec, values, label, onSubmit) {
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(title)));
    const close = () => { wrap.hidden = true; wrap.innerHTML = ''; };
    wrap.appendChild(form(spec, values, label, async d => { await onSubmit(d); close(); }, close));
    wrap.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return close;
  }

  function tiles(box, items) {
    box.innerHTML = '';
    items.forEach(([label, value, level]) => {
      const d = el('div', `hb-tile ${level || ''}`);
      d.append(raw('strong', '', String(value)), el('span', '', t(label)));
      box.appendChild(d);
    });
  }

  function table(box, heads, rows, empty) {
    box.innerHTML = '';
    const thead = box.createTHead().insertRow();
    heads.forEach(h => thead.appendChild(el('th', '', t(h))));
    const tb = box.createTBody();
    if (!rows.length) {
      const c = tb.insertRow().insertCell();
      c.colSpan = heads.length;
      c.className = 'field-hint';
      c.textContent = t(empty);
    }
    rows.forEach(cells => {
      const r = tb.insertRow();
      cells.forEach(v => {
        const c = r.insertCell();
        if (v instanceof Node) c.appendChild(v);
        else if (Array.isArray(v)) v.forEach(x => x && c.appendChild(x));
        else { c.textContent = v == null ? '' : String(v); c.setAttribute('data-no-i18n', ''); }
      });
    });
  }

  // Replaces a row of actions with one text input, for a reason or a note
  function ask(row, label, submit, onDone) {
    row.innerHTML = '';
    const i = el('input');
    i.type = 'text';
    i.placeholder = t(label);
    i.setAttribute('aria-label', t(label));
    row.append(i, button(submit, 'text-btn', () => onDone(i.value)));
    i.focus();
  }

  function lines(text) { return String(text || '').split('\n').map(s => s.trim()).filter(Boolean); }

  window.OpsUI = { t, LOCALE, can, el, raw, toast, api, date, time, size, tag, button, confirmButton, run, form, showForm, tiles, table, lines, ask };
})();
