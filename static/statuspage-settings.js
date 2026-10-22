/**
 * PiCore Dashboard - settings for the public status page (/status):
 * on/off, title, which services / web services / stacks appear and under which name.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const KIND = { service: 'Dienst', health: 'Web-Dienst', stack: 'Compose-Stack' };
  if (!$('sp-form')) return;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const n = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  function render(d) {
    const cfg = d.config;
    $('sp-enabled').checked = cfg.enabled;
    $('sp-title').value = cfg.title === 'Status' ? '' : cfg.title;
    $('sp-desc').value = cfg.description;
    $('sp-history').checked = cfg.show_history;
    $('sp-url').textContent = `${location.origin}/status`;

    const chosen = new Map(cfg.items.map(i => [`${i.kind}:${i.id}`, i]));
    // Chosen items first (in their order), then the rest
    const all = [...cfg.items.map(i => d.available.find(a => a.kind === i.kind && a.id === i.id)).filter(Boolean),
                 ...d.available.filter(a => !chosen.has(`${a.kind}:${a.id}`))];
    const list = $('sp-items');
    list.innerHTML = '';
    if (!all.length) list.appendChild(el('p', 'field-hint', t('Noch nichts zum Anzeigen – lege zuerst Web-Dienste an oder starte Dienste.')));
    all.forEach(a => {
      const row = el('label', 'sp-item');
      const box = el('input');
      box.type = 'checkbox';
      box.checked = chosen.has(`${a.kind}:${a.id}`);
      box.dataset.kind = a.kind;
      box.dataset.id = a.id;
      const name = el('span');
      const n = el('b', '', a.name);
      n.setAttribute('data-no-i18n', '');
      name.append(n, el('small', '', ` · ${t(KIND[a.kind])}`));
      const label = el('input');
      label.type = 'text';
      label.maxLength = 60;
      label.value = (chosen.get(`${a.kind}:${a.id}`) || {}).label || a.name;
      label.setAttribute('aria-label', t('Angezeigter Name'));
      label.placeholder = t('Angezeigter Name');
      row.append(box, name, label);
      list.appendChild(row);
    });
  }

  async function load() {
    try {
      const res = await fetch('/api/settings/status-page');
      if (res.ok) render(await res.json());
    } catch (e) { /* offline */ }
  }

  $('sp-form').addEventListener('submit', async e => {
    e.preventDefault();
    const items = [...document.querySelectorAll('#sp-items .sp-item')]
      .filter(r => r.querySelector('input[type=checkbox]').checked)
      .map(r => {
        const box = r.querySelector('input[type=checkbox]');
        return { kind: box.dataset.kind, id: box.dataset.id, label: r.querySelector('input[type=text]').value };
      });
    try {
      const res = await fetch('/api/settings/status-page', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: $('sp-enabled').checked, title: $('sp-title').value, description: $('sp-desc').value,
                               show_history: $('sp-history').checked, items })
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
      toast(json.enabled ? t('Statusseite gespeichert – sie ist jetzt öffentlich erreichbar.') : t('Statusseite gespeichert (ausgeschaltet).'), 'success');
      load();
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (can('settings.manage')) load();
  })();
})();
