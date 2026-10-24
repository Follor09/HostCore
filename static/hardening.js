/**
 * HostCore Dashboard - system page section "Härtung": security basics of the server with rating and fix.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const ORDER = { fail: 0, info: 1, ok: 2, skip: 3 };
  const WEIGHT = { critical: 'Schwer', warning: 'Mittel', info: 'Empfehlung' };

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

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const isCommand = s => /^(sudo |echo |printf |ssh-copy-id |chmod )/.test(s);

  function tag(r) {
    if (r.state === 'ok') return el('span', 'health-tag good', t('OK'));
    if (r.state === 'skip') return el('span', 'health-tag', t('Übersprungen'));
    if (r.state === 'info') return el('span', 'health-tag', t('Hinweis'));
    return el('span', `health-tag ${r.weight === 'critical' ? 'critical' : r.weight === 'warning' ? 'warning' : ''}`, t(WEIGHT[r.weight] || 'Mangel'));
  }

  function fixLine(fix) {
    const box = el('div', 'st-fix');
    if (isCommand(fix)) {
      box.appendChild(raw('code', 'mono st-cmd', fix));
      const copy = el('button', 'text-btn', t('Kopieren'));
      copy.type = 'button';
      copy.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(fix); toast(t('Befehl kopiert.'), 'success'); } catch (e) { window.prompt(t('Befehl:'), fix); }
      });
      box.appendChild(copy);
    } else {
      box.appendChild(el('span', 'field-hint', `${t('Beheben')}: ${t(fix)}`));
    }
    return box;
  }

  function render(d) {
    $('hd-summary').textContent = d.running ? t('Prüfe…') : d.checked ? `${t('Geprüft')} ${fmtTs(d.checked)}` : '';
    $('hd-run').disabled = d.running;
    const tiles = $('hd-tiles');
    tiles.innerHTML = '';
    if (d.checked) {
      [['Bewertung', d.score == null ? '–' : `${d.score} %`, d.critical ? 'critical' : d.failed ? 'warning' : ''],
        ['Schwere Mängel', String(d.critical), d.critical ? 'critical' : ''],
        ['Mängel gesamt', String(d.failed), d.failed ? 'warning' : ''],
        ['Geprüfte Punkte', String(d.results.filter(r => r.state !== 'skip').length), '']].forEach(([label, value, cls]) => {
        const box = el('div', 'bench-value');
        box.append(el('span', 'label', t(label)), raw('span', `value ${cls}`, value));
        tiles.appendChild(box);
      });
    }
    const box = $('hd-groups');
    box.innerHTML = '';
    if (!d.results.length) box.appendChild(el('p', 'field-hint', t(d.running ? 'Prüfe…' : 'Noch nicht geprüft.')));
    d.groups.forEach(g => {
      const rows = d.results.filter(r => r.group === g)
        .sort((a, b) => ORDER[a.state] - ORDER[b.state] || (a.weight === 'critical' ? -1 : 0) - (b.weight === 'critical' ? -1 : 0));
      if (!rows.length) return;
      const sec = el('div', 'st-group');
      sec.appendChild(el('h3', 'subhead', t(g)));
      const list = el('ul', 'st-list');
      rows.forEach(r => {
        const failClass = r.state === 'fail' ? (r.weight === 'critical' ? 'critical' : r.weight === 'warning' ? 'warning' : '') : '';
        const li = el('li', `st-item ${failClass}`);
        const head = el('div', 'st-head');
        head.append(tag(r), el('span', 'st-title', t(r.title)));
        li.appendChild(head);
        if (r.detail) li.appendChild(el('span', 'st-detail', t(r.detail)));
        if (r.fix && r.state === 'fail') li.appendChild(fixLine(r.fix));
        list.appendChild(li);
      });
      sec.appendChild(list);
      box.appendChild(sec);
    });
    if (d.running) setTimeout(load, 2500);
  }

  async function load() {
    try { render(await api('/api/hardening')); } catch (e) { /* no permission or offline */ }
  }

  async function init() {
    if (!$('hd-groups')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('security.view')) return;
    $('hd-run').addEventListener('click', async () => {
      try { render(await api('/api/hardening/run', {})); } catch (err) { toast(err.message, 'error'); }
    });
    await load();
  }

  init();
})();
