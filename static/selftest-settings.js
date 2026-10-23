/**
 * PiCore Dashboard - settings section "Selbstdiagnose": checks of the dashboard's own installation
 * with the fix for each problem (a command to copy, or a repair button where no root is needed).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const STATE = { critical: ['critical', 'Problem'], warning: ['warning', 'Warnung'], info: ['', 'Hinweis'], ok: ['good', 'OK'], skip: ['', 'Übersprungen'] };
  const ORDER = { critical: 0, warning: 1, info: 2, ok: 3, skip: 4 };
  const REPAIRS = { perms: 'Rechte setzen', vacuum: 'Verdichten' };

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
  // Commands are shown as code; fixes like "Einstellungen → E-Mail" are plain text (and translated)
  const isCommand = s => /^(sudo |chmod |cd |ls |journalctl |mkdir |\/)/.test(s);

  function fixLine(r) {
    const box = el('div', 'st-fix');
    if (r.fix) {
      if (isCommand(r.fix)) {
        box.appendChild(raw('code', 'mono st-cmd', r.fix));
        const copy = el('button', 'text-btn', t('Kopieren'));
        copy.type = 'button';
        copy.addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(r.fix); toast(t('Befehl kopiert.'), 'success'); } catch (e) { window.prompt(t('Befehl:'), r.fix); }
        });
        box.appendChild(copy);
      } else {
        box.appendChild(el('span', 'field-hint', `${t('Beheben')}: ${t(r.fix)}`));
      }
    }
    if (r.repair) {
      const b = el('button', 'btn-secondary', t(REPAIRS[r.repair] || 'Reparieren'));
      b.type = 'button';
      b.addEventListener('click', async () => {
        b.disabled = true;
        try { render(await api('/api/selftest/repair', { id: r.repair })); toast(t('Erledigt.'), 'success'); } catch (err) { toast(err.message, 'error'); b.disabled = false; }
      });
      box.appendChild(b);
    }
    return box;
  }

  function render(d) {
    const c = d.counts || {};
    const sum = [];
    if (d.running) sum.push(t('Prüfe…'));
    else if (d.checked) {
      sum.push(`${t('Geprüft')} ${fmtTs(d.checked)}`);
      const probs = (c.critical || 0) + (c.warning || 0);
      sum.push(probs ? t(`${probs} ${probs === 1 ? 'Problem' : 'Probleme'}`) : t('Alles in Ordnung'));
    }
    $('st-summary').textContent = sum.join(' · ');
    $('st-run').disabled = d.running;
    const box = $('st-groups');
    box.innerHTML = '';
    if (!d.results.length) {
      box.appendChild(el('p', 'field-hint', t(d.running ? 'Prüfe…' : 'Noch nicht geprüft.')));
    }
    d.groups.forEach(g => {
      const rows = d.results.filter(r => r.group === g).sort((a, b) => ORDER[a.state] - ORDER[b.state]);
      if (!rows.length) return;
      const sec = el('div', 'st-group');
      sec.appendChild(el('h3', 'subhead', t(g)));
      const list = el('ul', 'st-list');
      rows.forEach(r => {
        const li = el('li', `st-item ${r.state}`);
        const [cls, label] = STATE[r.state] || STATE.info;
        const head = el('div', 'st-head');
        head.append(el('span', `health-tag ${cls}`, t(label)), el('span', 'st-title', t(r.title)));
        li.appendChild(head);
        if (r.detail) li.appendChild(el('span', 'st-detail', t(r.detail)));
        if (r.fix || r.repair) li.appendChild(fixLine(r));
        list.appendChild(li);
      });
      sec.appendChild(list);
      box.appendChild(sec);
    });
    if (d.running) setTimeout(load, 2500);
  }

  async function load() {
    try { render(await api('/api/selftest')); } catch (e) { /* no permission or offline */ }
  }

  async function init() {
    if (!$('st-groups')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('settings.manage')) return;
    $('st-run').addEventListener('click', async () => {
      try { render(await api('/api/selftest/run', {})); } catch (err) { toast(err.message, 'error'); }
    });
    await load();
  }

  init();
})();
