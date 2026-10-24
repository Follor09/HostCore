/**
 * HostCore Dashboard - settings section "Webhooks": create / remove inbox addresses, recent messages.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const LEVEL = { good: ['good', 'OK'], info: ['', 'Hinweis'], warning: ['warning', 'Warnung'], critical: ['critical', 'Kritisch'] };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    return data;
  }

  function msg(text, level) {
    const m = $('webhook-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  const fmt = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  function render(data) {
    const list = $('webhook-list');
    list.innerHTML = '';
    if (!data.hooks.length) list.appendChild(el('p', 'field-hint', t('Noch keine Webhooks.')));
    data.hooks.forEach(h => {
      const url = `${location.origin}/hook/${h.token}`;
      const item = el('div', 'wol-item webhook-item');
      const main = el('div', 'workload-main');
      const name = el('span', 'workload-name', h.name);
      name.setAttribute('data-no-i18n', '');
      const sub = el('span', 'workload-sub', `${url}`);
      sub.setAttribute('data-no-i18n', '');
      const stats = el('span', 'workload-sub', h.count ? `${h.count}× ${t('empfangen')} · ${t('zuletzt')} ${fmt(h.last)}` : t('Noch keine Meldung empfangen'));
      main.append(name, sub, stats);
      (h.recent || []).forEach(e => {
        const line = el('div', 'webhook-event');
        const [cls, label] = LEVEL[e.level] || LEVEL.info;
        line.appendChild(el('span', `health-tag ${cls}`, t(label)));
        const text = el('span', 'workload-sub', `${fmt(e.ts)} · ${e.title}${e.detail ? ` – ${e.detail}` : ''}`);
        text.setAttribute('data-no-i18n', '');
        line.appendChild(text);
        main.appendChild(line);
      });
      const actions = el('div', 'item-actions');
      const copy = el('button', 'btn-secondary', t('Adresse kopieren'));
      copy.type = 'button';
      copy.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(url); msg(t('Adresse kopiert.'), 'success'); } catch (e) { window.prompt(t('Adresse:'), url); }
      });
      const del = el('button', 'btn-danger', t('Löschen'));
      del.type = 'button';
      del.addEventListener('click', async () => {
        if (!del.classList.contains('confirming')) {
          del.classList.add('confirming');
          del.textContent = t('Sicher?');
          setTimeout(() => { del.classList.remove('confirming'); del.textContent = t('Löschen'); }, 4000);
          return;
        }
        try { render(await api(`/api/settings/webhooks/${h.id}/delete`, {})); } catch (err) { msg(err.message, 'error'); }
      });
      actions.append(copy, del);
      item.append(main, actions);
      list.appendChild(item);
    });
  }

  async function init() {
    if (!$('webhook-form')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('settings.manage')) return;
    try { render(await api('/api/settings/webhooks')); } catch (e) { msg(e.message, 'error'); }
    $('webhook-form').addEventListener('submit', async e => {
      e.preventDefault();
      try {
        render(await api('/api/settings/webhooks', { name: $('wh-name').value.trim(), level: $('wh-level').value }));
        $('wh-name').value = '';
        msg(t('Webhook angelegt – Adresse kopieren und im Dienst eintragen.'), 'success');
      } catch (err) { msg(err.message, 'error'); }
    });
  }

  init();
})();
