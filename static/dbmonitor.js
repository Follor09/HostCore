/**
 * PiCore Dashboard - container page section "Datenbanken": PostgreSQL / MySQL / MariaDB containers
 * with connections, sizes and growth, long-running queries, largest tables and dumps.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('db-list')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  let data = null;
  const open = new Set();   // cards with the details unfolded

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

  function fmtBytes(b) {
    if (b == null) return '–';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = b ? Math.min(u.length - 1, Math.floor(Math.log(Math.abs(b)) / Math.log(1024))) : 0;
    return `${(b / 1024 ** i).toLocaleString(LOCALE, { maximumFractionDigits: i ? 1 : 0 })} ${u[i]}`;
  }
  const fmtNum = v => (v == null ? '–' : Number(v).toLocaleString(LOCALE));
  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  function fmtDur(s) {
    if (s < 120) return t(`${s} Sek.`);
    if (s < 7200) return t(`${Math.round(s / 60)} Min.`);
    if (s < 172800) return t(`${Math.round(s / 3600)} Std.`);
    return t(`${Math.round(s / 86400)} Tage`);
  }

  function button(label, cls, onClick) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => onClick(b));
    return b;
  }

  function table(head, rows) {
    const tbl = el('table', 'data-table');
    const h = tbl.createTHead().insertRow();
    head.forEach(x => h.appendChild(el('th', '', t(x))));
    const body = tbl.createTBody();
    rows.forEach(cells => {
      const r = body.insertRow();
      cells.forEach(c => {
        const td = r.insertCell();
        if (c instanceof Node) td.appendChild(c); else td.textContent = c;
      });
    });
    const wrap = el('div', 'table-scroll');
    wrap.appendChild(tbl);
    return wrap;
  }

  function connMeter(d) {
    const box = el('div', 'db-meter');
    if (!d.max_connections) return box;
    const pct = Math.min(100, Math.round(d.connections / d.max_connections * 100));
    const bar = el('div', 'progress-bar-bg mini');
    const fill = el('div', 'progress-bar-fill');
    fill.style.width = `${pct}%`;
    fill.style.background = pct >= data.thresholds.conn_warn ? 'var(--accent-amber)' : 'var(--accent-cyan)';
    bar.appendChild(fill);
    box.append(bar, raw('span', 'field-hint', `${d.connections} / ${d.max_connections} ${t('Verbindungen')}`));
    return box;
  }

  function growth(g) {
    if (g == null) return el('span', 'field-hint', '–');
    const s = raw('span', g > 0 ? 'db-up' : '', `${g > 0 ? '+' : g < 0 ? '−' : '±'}${fmtBytes(Math.abs(g))}`);
    return s;
  }

  function dumpBlock(d) {
    const box = el('div', 'db-dumps');
    const dm = d.dumps;
    const last = dm.files[0];
    const facts = el('dl', 'backup-facts');
    const add = (k, v, cls) => { facts.append(el('dt', '', t(k)), raw('dd', cls || '', v)); };
    add('Letzter Dump', last ? `${fmtTs(last.ts)} · ${fmtBytes(last.size)}` : t('noch keiner'));
    if (dm.error) add('Fehler', dm.error, 'warn');
    add('Zeitplan', d.settings.daily ? t(`täglich ab ${d.settings.hour}:00 Uhr, die letzten ${d.settings.keep} behalten`) : t('aus'));
    if (dm.files.length) add('Gespeichert', `${dm.files.length} · ${fmtBytes(dm.total)}`);
    add('Ordner', dm.folder);
    box.appendChild(facts);

    const actions = el('div', 'docs-row-actions');
    if (can('services.control') && d.state === 'running') {
      if (d.dumping) {
        actions.appendChild(el('span', 'field-hint', t(`Dump läuft… ${fmtBytes(d.dumping.bytes)}`)));
      } else {
        actions.appendChild(button('Dump erstellen', 'text-btn', async b => {
          b.disabled = true;
          try {
            const job = await api(`/api/containers/databases/${encodeURIComponent(d.name)}/dump`, {});
            toast(t('Dump gestartet.'));
            watchJob(job.id);
          } catch (err) { toast(err.message, 'error'); b.disabled = false; }
        }));
      }
    }
    if (can('settings.manage')) actions.appendChild(button('Zeitplan', 'text-btn', () => scheduleForm(box, d)));
    box.appendChild(actions);
    return box;
  }

  function scheduleForm(box, d) {
    if (box.querySelector('form')) return;
    const f = el('form', 'db-schedule');
    const daily = el('input'); daily.type = 'checkbox'; daily.checked = d.settings.daily;
    const lab = el('label', 'toggle-label'); lab.append(daily, document.createTextNode(` ${t('Täglich einen Dump erstellen')}`));
    const hour = el('input'); hour.type = 'number'; hour.min = 0; hour.max = 23; hour.value = d.settings.hour;
    const keep = el('input'); keep.type = 'number'; keep.min = 1; keep.max = 60; keep.value = d.settings.keep;
    const ignore = el('input'); ignore.type = 'checkbox'; ignore.checked = d.settings.ignore;
    const lab2 = el('label', 'toggle-label'); lab2.append(ignore, document.createTextNode(` ${t('Keine Warnungen für diese Datenbank')}`));
    const field = (label, input) => { const l = el('label', 'login-field'); l.append(el('span', '', t(label)), input); return l; };
    const grid = el('div', 'form-grid');
    grid.append(field('Ab Uhrzeit (Stunde)', hour), field('Dumps behalten', keep));
    const save = el('button', 'btn-primary', t('Speichern')); save.type = 'submit';
    const cancel = button('Abbrechen', 'btn-secondary', () => f.remove());
    const row = el('div', 'inline-form'); row.append(save, cancel);
    f.append(lab, grid, lab2, row);
    f.addEventListener('submit', async e => {
      e.preventDefault();
      try {
        data = await api(`/api/containers/databases/${encodeURIComponent(d.name)}/settings`,
          { daily: daily.checked, hour: Number(hour.value), keep: Number(keep.value), ignore: ignore.checked });
        render();
        toast(t('Gespeichert.'), 'success');
      } catch (err) { toast(err.message, 'error'); }
    });
    box.appendChild(f);
  }

  async function watchJob(id) {
    await load();
    for (let i = 0; i < 720; i++) {
      await new Promise(r => setTimeout(r, 2500));
      let job;
      try { job = await api(`/api/containers/databases/job/${id}`); } catch (e) { break; }
      if (job.state === 'running') { await load(); continue; }
      if (job.state === 'done') toast(t(`Dump gespeichert: ${job.file} (${fmtBytes(job.size)})`), 'success');
      else toast(job.error || t('Dump fehlgeschlagen.'), 'error');
      break;
    }
    load();
  }

  function card(d) {
    const probs = d.problems || [];
    const worst = probs.some(p => p.level === 'critical') ? 'error' : probs.length ? 'overdue' : '';
    const c = el('div', `backup-card db-card ${worst}`);
    const head = el('div', 'backup-head');
    head.append(raw('span', 'backup-name', d.name), el('span', 'backup-kind', `${d.engine_label}${d.version ? ' ' + d.version : ''}`));
    const tag = d.state !== 'running' ? ['critical', 'Gestoppt'] : d.error ? ['warning', 'Nicht abfragbar']
      : probs.length ? [worst === 'error' ? 'critical' : 'warning', probs.length === 1 ? '1 Problem' : `${probs.length} Probleme`] : ['good', 'In Ordnung'];
    head.appendChild(el('span', `health-tag ${tag[0]}`, t(tag[1])));
    c.appendChild(head);
    if (d.stack) c.appendChild(raw('span', 'backup-target', `${t('Stack')} ${d.stack} · ${d.image}`));
    else c.appendChild(raw('span', 'backup-target', d.image));

    probs.forEach(p => c.appendChild(raw('div', `db-problem ${p.level}`, t(p.text))));
    if (d.error && !probs.some(p => p.key === 'error')) c.appendChild(raw('div', 'db-problem warning', t(d.error)));

    if (d.state === 'running' && !d.error && d.checked) {
      c.appendChild(connMeter(d));
      const facts = el('dl', 'backup-facts');
      const add = (k, v) => { if (v !== '' && v != null) facts.append(el('dt', '', t(k)), raw('dd', '', v)); };
      const total = (d.databases || []).reduce((s, x) => s + x.size, 0);
      add('Größe', `${fmtBytes(total)} · ${(d.databases || []).length} ${t((d.databases || []).length === 1 ? 'Datenbank' : 'Datenbanken')}`);
      if (d.started) add('Läuft seit', fmtDur(Math.max(0, Math.round(Date.now() / 1000 - d.started))));
      if (d.cache_hit != null) add('Cache-Trefferquote', `${d.cache_hit.toLocaleString(LOCALE)} %`);
      if (d.slow_queries != null) add('Langsame Abfragen (gesamt)', fmtNum(d.slow_queries));
      add('Lange Abfragen', d.queries && d.queries.length ? String(d.queries.length) : t('keine'));
      c.appendChild(facts);

      const det = el('details', 'db-details');
      det.open = open.has(d.name);
      det.addEventListener('toggle', () => (det.open ? open.add(d.name) : open.delete(d.name)));
      det.appendChild(el('summary', '', t('Details')));
      det.appendChild(el('h4', 'db-sub', t('Datenbanken')));
      det.appendChild(table(['Name', 'Größe', '7 Tage', 'Verbindungen'],
        (d.databases || []).slice().sort((a, b) => b.size - a.size).map(x => [raw('span', 'mono', x.name), fmtBytes(x.size), growth(x.growth_7d), x.connections == null ? '–' : String(x.connections)])));
      if (d.queries && d.queries.length) {
        det.appendChild(el('h4', 'db-sub', t('Laufende Abfragen (über 30 Sek.)')));
        det.appendChild(table(['Dauer', 'Datenbank', 'Benutzer', 'Zustand', 'Abfrage'],
          d.queries.map(q => [fmtDur(q.seconds), raw('span', '', q.db), raw('span', '', q.user), q.state, raw('code', 'db-query', q.query)])));
      }
      if (d.tables && d.tables.length) {
        det.appendChild(el('h4', 'db-sub', d.tables_db ? `${t('Größte Tabellen in')} ${d.tables_db}` : t('Größte Tabellen')));
        const pg = d.engine === 'postgres';
        det.appendChild(table(pg ? ['Tabelle', 'Größe', 'Zeilen', 'Tote Zeilen'] : ['Tabelle', 'Größe', 'Zeilen (geschätzt)'],
          d.tables.map(x => {
            const row = [raw('span', 'mono', x.name), fmtBytes(x.size), fmtNum(x.rows)];
            if (pg) row.push(x.dead > 1000 && x.dead > x.rows * 0.2 ? raw('span', 'db-up', `${fmtNum(x.dead)} · ${t('VACUUM empfohlen')}`) : fmtNum(x.dead));
            return row;
          })));
      }
      c.appendChild(det);
    }
    c.appendChild(dumpBlock(d));
    return c;
  }

  function render() {
    const list = $('db-list');
    list.innerHTML = '';
    $('db-checked').textContent = data.checking ? t('Prüfe…') : data.checked ? `${t('Geprüft')} ${fmtTs(data.checked)}` : '';
    if (!data.databases.length) {
      list.appendChild(el('p', 'field-hint', t('Keine Datenbank-Container gefunden. Erkannt werden Images von PostgreSQL (auch PostGIS, TimescaleDB, pgvector), MySQL, Percona und MariaDB.')));
      return;
    }
    data.databases.forEach(d => list.appendChild(card(d)));
  }

  async function load() {
    try { data = await api('/api/containers/databases'); render(); } catch (e) { /* offline */ }
  }

  $('db-refresh').addEventListener('click', async e => {
    const b = e.currentTarget;
    b.disabled = true;
    $('db-checked').textContent = t('Prüfe…');
    try { data = await api('/api/containers/databases/refresh', {}); render(); } catch (err) { toast(err.message, 'error'); }
    b.disabled = false;
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    await load();
    // The first check after a restart runs in the background: show it once it is there
    if (data && !data.checked) setTimeout(load, 8000);
    const running = data && data.databases.find(d => d.dumping);
    if (running) watchJob(running.dumping.id);
    setInterval(() => { if (!document.hidden) load(); }, 60000);
  })();
})();
