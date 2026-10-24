/**
 * PiCore Dashboard - container page section "Sicherheitscheck": risky settings per compose
 * stack with score, finding and fix; findings can be accepted (ignored) per stack.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('sc-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const LEVEL = { critical: ['critical', 'Kritisch'], warning: ['warning', 'Warnung'], info: ['', 'Hinweis'] };
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

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const scoreLevel = s => (s == null ? '' : s >= 90 ? 'good' : s >= 70 ? 'warning' : 'critical');

  function finding(stack, f) {
    const row = el('div', `sc-finding ${f.level}${f.ignored ? ' ignored' : ''}`);
    const head = el('div', 'sc-finding-head');
    const [cls, label] = LEVEL[f.level];
    head.append(el('span', `health-tag ${cls}`, t(label)), raw('span', 'sc-service', f.service), raw('span', 'sc-text', t(f.text)));
    if (can('settings.manage')) {
      const b = el('button', 'text-btn', t(f.ignored ? 'Wieder beachten' : 'Akzeptieren'));
      b.type = 'button';
      b.title = f.ignored ? '' : t('Bewusst so gewollt – zählt nicht mehr in die Bewertung');
      b.addEventListener('click', async () => {
        try { data = await api('/api/containers/stack-check/ignore', { stack, finding: f.id, ignore: !f.ignored }); render(); } catch (err) { toast(err.message, 'error'); }
      });
      head.appendChild(b);
    }
    row.append(head, raw('div', 'sc-fix', t(f.fix)));
    return row;
  }

  function card(s) {
    const c = el('div', 'backup-card sc-card');
    const head = el('div', 'backup-head');
    head.append(raw('span', 'backup-name', s.name));
    if (s.score != null) head.appendChild(el('span', `health-tag ${scoreLevel(s.score)}`, `${s.score} / 100`));
    else head.appendChild(el('span', 'health-tag', t('Nicht prüfbar')));
    c.appendChild(head);
    if (s.dir) c.appendChild(raw('span', 'backup-target', s.dir));
    if (s.error) { c.appendChild(raw('div', 'db-problem warning', t(s.error))); return c; }
    const k = s.counts;
    const sum = [k.critical ? t(`${k.critical} kritisch`) : '', k.warning ? t(`${k.warning} Warnungen`) : '', k.info ? t(`${k.info} Hinweise`) : ''].filter(Boolean);
    c.appendChild(el('div', 'field-hint', sum.length ? sum.join(' · ') : t('Keine Befunde.')));
    if (s.findings.length) {
      const det = el('details', 'db-details');
      det.open = open.has(s.name) || k.critical > 0;
      det.addEventListener('toggle', () => (det.open ? open.add(s.name) : open.delete(s.name)));
      det.appendChild(el('summary', '', t(`${s.findings.length} ${s.findings.length === 1 ? 'Befund' : 'Befunde'}`)));
      const list = el('div', 'sc-findings');
      s.findings.forEach(f => list.appendChild(finding(s.name, f)));
      det.appendChild(list);
      c.appendChild(det);
    }
    return c;
  }

  function render() {
    $('sc-checked').textContent = data.running ? t('Prüfe…') : data.checked ? `${t('Geprüft')} ${fmtTs(data.checked)}` : '';
    const list = $('sc-list');
    list.innerHTML = '';
    if (!data.checked && !data.running) { list.appendChild(el('p', 'field-hint', t('Noch nicht geprüft.'))); return; }
    if (!data.stacks.length) { list.appendChild(el('p', 'field-hint', t('Keine Compose-Stacks gefunden.'))); return; }
    data.stacks.forEach(s => list.appendChild(card(s)));
  }

  async function runCheck() {
    const b = $('sc-run');
    b.disabled = true;
    $('sc-checked').textContent = t('Prüfe…');
    try { data = await api('/api/containers/stack-check/run', {}); render(); } catch (err) { toast(err.message, 'error'); }
    b.disabled = false;
  }

  $('sc-run').addEventListener('click', runCheck);

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    try { data = await api('/api/containers/stack-check'); } catch (e) { return; }
    if (!data.checked && !data.running) return runCheck();   // first visit after a restart
    render();
  })();
})();
