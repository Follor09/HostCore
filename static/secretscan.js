/**
 * PiCore Dashboard - system tools section "Geheimnis-Scan": keys, tokens and passwords in stack
 * folders, scripts and the dashboard's documentation (masked), readable variable/key files;
 * findings can be accepted, extra folders added.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('ss-table')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const LEVEL = { critical: ['critical', 'Kritisch'], warning: ['warning', 'Warnung'], info: ['', 'Hinweis'] };
  let data = null;
  let showIgnored = false;

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
  const FIX = {
    permissions: 'Rechte auf 600 setzen (Befehl in der Spalte Fundstelle).',
    password_assignment: 'In eine .env-Datei verschieben und in der Compose-Datei als ${VARIABLE} verwenden; danach das Passwort ändern.',
    url_credentials: 'Zugangsdaten aus der Adresse in Variablen auslagern und das Passwort ändern.',
    private_key: 'Schlüssel aus dem Ordner entfernen oder nur für den Besitzer lesbar ablegen (chmod 600); wenn er in Git war: neuen Schlüssel erzeugen.'
  };

  function render() {
    $('ss-checked').textContent = data.running ? t('Scanne…') : data.checked ? `${t('Gescannt')} ${fmtTs(data.checked)}${data.duration != null ? ` · ${data.duration} s` : ''}` : '';
    const tiles = $('ss-tiles');
    tiles.innerHTML = '';
    const tile = (label, value, level) => { const d = el('div', `hb-tile ${level || ''}`); d.append(raw('strong', '', value), el('span', '', t(label))); tiles.appendChild(d); };
    const c = data.counts;
    tile('Kritisch', String(c.critical), c.critical ? 'critical' : 'good');
    tile('Warnungen', String(c.warning), c.warning ? 'warning' : 'good');
    tile('Hinweise', String(c.info));
    tile('Geprüfte Dateien', String((data.stats || {}).files || 0));
    if (data.ignored) {
      const b = el('button', 'text-btn', t(showIgnored ? 'Akzeptierte ausblenden' : `${data.ignored} akzeptierte zeigen`));
      b.type = 'button';
      b.addEventListener('click', () => { showIgnored = !showIgnored; render(); });
      tiles.appendChild(b);
    }
    if ((data.stats || {}).truncated) tiles.appendChild(el('p', 'field-hint full', t('Nicht alles geprüft: Grenze von 20 000 Dateien bzw. 150 MB erreicht – große Datenordner am besten nicht unter den geprüften Ordnern ablegen.')));

    const table = $('ss-table');
    table.innerHTML = '';
    const items = data.findings.filter(f => showIgnored || !f.ignored);
    if (!data.checked) { const tr = el('tr'); tr.appendChild(el('td', 'empty', t('Noch nicht gescannt.'))); table.appendChild(tr); }
    else if (!items.length) { const tr = el('tr'); tr.appendChild(el('td', 'empty', t('Nichts gefunden.'))); table.appendChild(tr); }
    else {
      const head = el('tr');
      ['Stufe', 'Art', 'Fundstelle', 'Auszug', ''].forEach(h => head.appendChild(el('th', '', t(h))));
      table.appendChild(head);
    }
    items.forEach(f => {
      const tr = el('tr', f.ignored ? 'ar-off' : '');
      const [cls, label] = LEVEL[f.level];
      const whereTd = el('td');
      const where = el('div', 'ss-where');
      whereTd.appendChild(where);
      where.appendChild(raw('span', 'mono', f.line ? `${f.location}:${f.line}` : f.location));
      where.appendChild(el('span', 'field-hint', t(f.kind || '')));
      if (f.fix) where.appendChild(raw('code', '', f.fix));
      else if (FIX[f.rule]) where.appendChild(el('span', 'field-hint', t(FIX[f.rule])));
      const act = el('td');
      if (can('settings.manage')) {
        const b = el('button', 'text-btn', t(f.ignored ? 'Wieder beachten' : 'Akzeptieren'));
        b.type = 'button';
        b.title = f.ignored ? '' : t('Falscher Alarm oder bewusst so – zählt nicht mehr');
        b.addEventListener('click', async () => {
          try { data = await api('/api/secret-scan/ignore', { fingerprint: f.fingerprint, ignore: !f.ignored }); render(); } catch (err) { toast(err.message, 'error'); }
        });
        act.appendChild(b);
      }
      const level = el('td');
      level.appendChild(el('span', `health-tag ${cls}`, t(label)));
      tr.append(level, el('td', '', t(f.label)), whereTd, raw('td', 'mono', f.excerpt), act);
      table.appendChild(tr);
    });

    const roots = $('ss-roots');
    roots.innerHTML = '';
    (data.roots || []).forEach(r => { const li = el('li'); li.append(raw('code', '', r.path), ` · ${t(r.kind)}`); roots.appendChild(li); });
    if (data.settings.docs) roots.appendChild(el('li', '', t('Dokumentation des Dashboards')));
    $('ss-folders').value = (data.settings.folders || []).join('\n');
    $('ss-docs').checked = !!data.settings.docs;
  }

  async function runScan() {
    const b = $('ss-run');
    b.disabled = true;
    $('ss-checked').textContent = t('Scanne…');
    try { data = await api('/api/secret-scan/run', {}); render(); } catch (err) { toast(err.message, 'error'); }
    b.disabled = false;
  }

  $('ss-run').addEventListener('click', runScan);
  $('ss-settings').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      data = await api('/api/secret-scan/settings', { folders: $('ss-folders').value.split('\n').map(x => x.trim()).filter(Boolean), docs: $('ss-docs').checked });
      render();
      toast(t('Gespeichert – beim nächsten Scan berücksichtigt.'), 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('security.view')) return;
    try { data = await api('/api/secret-scan'); } catch (e) { return; }
    if (!data.checked && !data.running) return runScan();
    render();
  })();
})();
