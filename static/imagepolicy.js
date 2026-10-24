/**
 * PiCore Dashboard - container page section "Image-Richtlinien": rules for container images
 * (registries, fixed versions, digests, block list, age, exceptions) and the running containers
 * checked against them.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('ip-table')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
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

  function renderForm() {
    const c = data.config;
    const form = $('ip-form');
    form.innerHTML = '';
    const field = (label, input, full, hint) => {
      const w = el('label', `login-field${full ? ' full' : ''}`);
      w.append(el('span', '', t(label)), input);
      if (hint) w.appendChild(el('span', 'field-hint', t(hint)));
      form.appendChild(w);
      return input;
    };
    const mode = el('select');
    Object.entries(data.modes).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; mode.appendChild(o); });
    mode.value = c.mode;
    field('Modus', mode);
    const age = el('input');
    age.type = 'number';
    age.min = 0;
    age.max = 3650;
    age.value = c.max_age_days;
    field('Höchstalter der Images (Tage, 0 = aus)', age, false, 'Nur gemeldet, nie blockiert.');
    const regs = el('input');
    regs.type = 'text';
    regs.value = c.registries.join(', ');
    regs.placeholder = 'docker.io, ghcr.io, lscr.io';
    regs.setAttribute('data-no-i18n', '');
    field('Erlaubte Registries (leer = alle)', regs, true, `${t('Im Einsatz')}: ${data.counts.registries.join(', ') || '–'}`);
    const denied = el('textarea');
    denied.rows = 2;
    denied.value = c.denied.join('\n');
    denied.placeholder = '*:nightly\n*/watchtower*';
    denied.setAttribute('data-no-i18n', '');
    field('Gesperrte Images (Muster, eines pro Zeile)', denied, true);
    const exc = el('input');
    exc.type = 'text';
    exc.value = c.exceptions.join(', ');
    exc.placeholder = 'testlab, spielwiese';
    exc.setAttribute('data-no-i18n', '');
    field('Ausnahmen (Stack- oder Container-Namen)', exc, true);
    const toggle = (label, checked) => { const l = el('label', 'toggle-label full'); const i = el('input'); i.type = 'checkbox'; i.checked = checked; l.append(i, ` ${t(label)}`); form.appendChild(l); return i; };
    const tag = toggle('Feste Version verlangen (kein „latest“, kein fehlendes Tag)', c.require_tag);
    const digest = toggle('Fixierung per Digest verlangen (@sha256:…)', c.require_digest);
    if (can('settings.manage')) {
      const actions = el('div', 'form-actions');
      const save = el('button', 'btn-primary', t('Speichern'));
      save.type = 'submit';
      actions.appendChild(save);
      form.appendChild(actions);
      form.onsubmit = async e => {
        e.preventDefault();
        save.disabled = true;
        try {
          data = await api('/api/containers/image-policy', { mode: mode.value, max_age_days: Number(age.value), registries: regs.value,
            denied: denied.value.split('\n'), exceptions: exc.value, require_tag: tag.checked, require_digest: digest.checked });
          render();
          toast(t('Gespeichert.'), 'success');
        } catch (err) { toast(err.message, 'error'); save.disabled = false; }
      };
    } else {
      form.querySelectorAll('input, select, textarea').forEach(x => { x.disabled = true; });
    }
  }

  function renderTable() {
    const c = data.counts;
    const tag = $('ip-mode');
    tag.className = `health-tag ${data.config.mode === 'block' ? 'good' : data.config.mode === 'warn' ? 'warning' : ''}`;
    tag.textContent = t(data.modes[data.config.mode]);
    $('ip-counts').textContent = data.config.mode === 'off'
      ? t('Richtlinien sind aus – die Tabelle zeigt, was beim Einschalten auffallen würde.')
      : `${c.violating} ${t(c.violating === 1 ? 'Container verstößt' : 'Container verstoßen')} · ${c.containers} ${t('geprüft')}${c.excepted ? ` · ${c.excepted} ${t('ausgenommen')}` : ''}`;
    const table = $('ip-table');
    table.innerHTML = '';
    if (!data.containers.length) {
      const tr = el('tr');
      tr.appendChild(el('td', 'empty', t('Keine Container gefunden.')));
      table.appendChild(tr);
      return;
    }
    const head = el('tr');
    ['Container', 'Stack', 'Image', 'Ergebnis'].forEach(h => head.appendChild(el('th', '', t(h))));
    table.appendChild(head);
    data.containers.forEach(r => {
      const tr = el('tr');
      const resTd = el('td');
      const res = el('div', 'ip-result');
      resTd.appendChild(res);
      if (!r.violations.length) res.appendChild(el('span', 'health-tag good', t('OK')));
      else if (r.excepted) res.appendChild(el('span', 'health-tag', t('Ausgenommen')));
      r.violations.forEach(v => {
        const line = el('div', 'ip-violation');
        line.append(el('span', `health-tag ${r.excepted ? '' : v.block ? 'warning' : ''}`, t(data.rules[v.rule])), el('span', 'field-hint', t(v.text)));
        res.appendChild(line);
      });
      tr.append(raw('td', '', r.container), raw('td', '', r.stack || '–'), raw('td', 'mono ip-image', r.image), resTd);
      table.appendChild(tr);
    });
  }

  function render() {
    renderForm();
    renderTable();
  }

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    try { data = await api('/api/containers/image-policy'); render(); } catch (e) { /* no access */ }
  })();
})();
