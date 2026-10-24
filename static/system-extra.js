/**
 * HostCore Dashboard - system page sections "Auffälligkeiten" (anomaly detection), "Kapazitätsplanung",
 * "DNS-Überwachung" and the IP conflicts below "Geräte im Netzwerk".
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
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
  const fmtDur = s => (s < 3600 ? `${Math.max(1, Math.round(s / 60))} Min.` : s < 86400 ? `${(s / 3600).toFixed(1).replace('.', ',')} Std.` : `${Math.round(s / 86400)} Tage`);

  function table(id, heads, rows, empty) {
    const tb = $(id);
    tb.innerHTML = '';
    const head = tb.createTHead().insertRow();
    heads.forEach(h => head.appendChild(el('th', '', t(h))));
    const body = tb.createTBody();
    if (!rows.length && empty) {
      const c = body.insertRow().insertCell();
      c.colSpan = heads.length;
      c.className = 'docs-empty';
      c.textContent = t(empty);
    }
    return body;
  }

  // ---------------------------------------------------------------------------
  // Anomalies
  // ---------------------------------------------------------------------------
  async function loadAnomalies() {
    let d;
    try { d = await api('/api/anomalies'); } catch (e) { return; }
    const state = $('an-state');
    const open = d.events.filter(e => e.open).length;
    const learning = d.metrics.every(m => m.learning);
    state.className = `health-tag ${!d.config.enabled ? '' : open ? 'warning' : learning ? '' : 'good'}`;
    state.textContent = t(!d.config.enabled ? 'Aus' : open ? `${open} offen` : learning ? 'Lernt noch' : 'Alles im üblichen Bereich');
    $('an-enabled').checked = d.config.enabled;
    $('an-sens').value = d.config.sensitivity;
    const box = $('an-metrics');
    box.innerHTML = '';
    Object.entries(d.available).forEach(([k, label]) => {
      const l = el('label', 'toggle-label');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.value = k;
      cb.checked = d.config.metrics.includes(k);
      l.append(cb, document.createTextNode(` ${t(label)}`));
      box.appendChild(l);
    });
    const body = table('an-table', ['Messwert', 'Jetzt (15 Min.)', 'Üblich um diese Zeit', 'Auffällig ab', 'Zustand'], d.metrics);
    d.metrics.forEach(m => {
      const r = body.insertRow();
      r.insertCell().textContent = t(m.label);
      r.insertCell().appendChild(raw('span', 'mono', m.now));
      r.insertCell().appendChild(raw('span', 'mono', m.usual));
      r.insertCell().appendChild(raw('span', 'mono', m.limit));
      const st = r.insertCell();
      if (!m.watched) st.appendChild(el('span', 'health-tag', t('Nicht überwacht')));
      else if (m.learning) st.appendChild(el('span', 'health-tag', t(`Lernt (${m.samples} von ${d.min_baseline} Werten)`)));
      else st.appendChild(el('span', `health-tag ${m.state === 'high' ? 'warning' : 'good'}`, t(m.state === 'high' ? 'Auffällig' : 'Normal')));
    });
    const ev = table('an-events', ['Beginn', 'Messwert', 'Spitze', 'Üblich', 'Dauer'], d.events, 'Keine Auffälligkeiten.');
    d.events.forEach(e => {
      const r = ev.insertRow();
      r.insertCell().textContent = fmtTs(e.start);
      const c = r.insertCell();
      c.textContent = t(e.label);
      if (e.open) { c.append(' '); c.appendChild(el('span', 'health-tag warning', t('läuft'))); }
      r.insertCell().appendChild(raw('span', 'mono', e.peak));
      r.insertCell().appendChild(raw('span', 'mono', e.usual));
      r.insertCell().textContent = t(fmtDur(e.duration));
    });
  }

  // ---------------------------------------------------------------------------
  // Capacity
  // ---------------------------------------------------------------------------
  function capTile(label, f, unit, extra) {
    const b = el('div', 'bench-value');
    b.appendChild(el('span', 'label', t(label)));
    const level = f.days_left == null ? '' : f.days_left < 30 ? 'critical' : f.days_left < 90 ? 'warn' : '';
    b.appendChild(raw('span', `value ${level}`, f.current == null ? '–' : `${f.current} ${unit}`));
    const trend = f.slope == null ? t('Trend ab 7 Tagen') : f.slope > 0.05 ? t(`steigt um ${f.slope.toFixed(2)} pro Tag`) : f.slope < -0.05 ? t('sinkt') : t('gleichbleibend');
    b.appendChild(el('span', 'bench-sub', trend));
    b.appendChild(el('span', 'bench-sub', f.days_left == null ? t(extra || 'keine Grenze in Sicht') : f.days_left === 0 ? t('Grenze erreicht') : t(`eng in ~${f.days_left} Tagen`)));
    return b;
  }

  async function loadCapacity() {
    let d;
    try { d = await api('/api/capacity'); } catch (e) { return; }
    const tiles = $('cap-tiles');
    tiles.innerHTML = '';
    tiles.appendChild(capTile('Arbeitsspeicher (Tagesschnitt)', d.ram, '%', `${Math.round(d.ram.total_mb / 1024 * 10) / 10} GB gesamt`));
    tiles.appendChild(capTile('CPU (Tagesschnitt)', d.cpu, '%', `${d.cpu.cores} Kerne`));
    d.disks.filter(x => x.mount).slice(0, 4).forEach(x => {
      tiles.appendChild(capTile(`Speicher ${x.mount}`, { current: x.percent, slope: null, days_left: x.days_left }, '%',
        x.growth_per_day ? `+${(x.growth_per_day / 1048576).toFixed(0)} MB pro Tag` : 'wächst nicht nennenswert'));
    });
    const chart = $('cap-chart');
    chart.innerHTML = '';
    const days = d.series.slice(-60);
    if (days.length < 2) { chart.appendChild(el('p', 'field-hint', t(`Bisher ${d.days} ${d.days === 1 ? 'Tag' : 'Tage'} erfasst – das Diagramm erscheint ab zwei Tagen.`))); return; }
    const ns = 'http://www.w3.org/2000/svg';
    const W = 900, H = 180, pad = 48;
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('class', 'cap-svg');
    const x = i => pad + (i / (days.length - 1)) * (W - pad - 8);
    const y = v => H - 20 - (v / 100) * (H - 30);
    [0, 50, 100].forEach(v => {
      const l = document.createElementNS(ns, 'line');
      Object.entries({ x1: pad, x2: W - 8, y1: y(v), y2: y(v), class: 'grid-line' }).forEach(([k, val]) => l.setAttribute(k, val));
      svg.appendChild(l);
      const tx = document.createElementNS(ns, 'text');
      Object.entries({ x: pad - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'axis-label' }).forEach(([k, val]) => tx.setAttribute(k, val));
      tx.textContent = `${v}%`;
      svg.appendChild(tx);
    });
    [['ram_avg', 'cap-line-ram'], ['cpu_avg', 'cap-line-cpu'], ['cpu_p95', 'cap-line-cpu95']].forEach(([k, cls]) => {
      const pts = days.map((p, i) => [i, p[k]]).filter(p => p[1] != null);
      if (pts.length < 2) return;
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' '));
      path.setAttribute('class', cls);
      svg.appendChild(path);
    });
    [0, Math.floor(days.length / 2), days.length - 1].forEach(i => {
      const tx = document.createElementNS(ns, 'text');
      Object.entries({ x: x(i), y: H - 4, 'text-anchor': i === 0 ? 'start' : i === days.length - 1 ? 'end' : 'middle', class: 'axis-label' })
        .forEach(([k, val]) => tx.setAttribute(k, val));
      tx.textContent = new Date(`${days[i].day}T12:00:00`).toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit' });
      svg.appendChild(tx);
    });
    chart.appendChild(svg);
  }

  // ---------------------------------------------------------------------------
  // DNS monitoring
  // ---------------------------------------------------------------------------
  const DW_STATE = { ok: ['good', 'OK'], error: ['critical', 'Fehler'], mismatch: ['warning', 'Abweichung'], unknown: ['', 'Noch nicht geprüft'] };
  let dw = null;

  function renderDns() {
    $('dw-summary').textContent = dw.items.length ? t(`${dw.items.length} Einträge${dw.problems ? ` · ${dw.problems} mit Problem` : ''}`) : '';
    $('dw-check').disabled = dw.running || !dw.items.length;
    const body = table('dw-table', ['Name', 'Antwort', 'Erwartet', 'Zustand', ''], dw.items, 'Noch keine Einträge überwacht.');
    dw.items.forEach(i => {
      const r = body.insertRow();
      const n = r.insertCell();
      n.className = 'wrap';
      n.append(raw('span', 'lan-name', i.name), raw('span', 'lan-sub', `${i.type} · ${i.server === 'system' ? t('System-DNS') : i.server}${i.note ? ` · ${i.note}` : ''}`));
      const a = r.insertCell();
      a.className = 'wrap';
      a.appendChild(raw('span', 'mono', i.values.join(', ') || '–'));
      if (i.changed) a.appendChild(raw('span', 'lan-sub', `${t('geändert')} ${fmtTs(i.changed)} · ${t('vorher')} ${i.previous.join(', ')}`));
      r.insertCell().appendChild(raw('span', 'mono', i.expected || '–'));
      const st = r.insertCell();
      const [cls, label] = DW_STATE[i.status];
      st.appendChild(el('span', `health-tag ${cls}`, t(label)));
      if (i.error) st.appendChild(raw('span', 'lan-sub warn', i.error));
      if (i.checked) st.appendChild(el('span', 'lan-sub', `${fmtTs(i.checked)}${i.ms != null ? ` · ${i.ms} ms` : ''}`));
      const act = el('div', 'docs-row-actions');
      r.insertCell().appendChild(act);
      if (can('settings.manage')) {
        const ed = el('button', 'text-btn', t('Bearbeiten'));
        ed.type = 'button';
        ed.addEventListener('click', () => dnsForm(i));
        const del = el('button', 'text-btn danger', t('Entfernen'));
        del.type = 'button';
        del.addEventListener('click', async () => {
          if (!del.classList.contains('confirming')) {
            del.classList.add('confirming');
            del.textContent = t('Sicher?');
            setTimeout(() => { del.classList.remove('confirming'); del.textContent = t('Entfernen'); }, 4000);
            return;
          }
          try { dw = await api(`/api/dns-watch/${i.id}/delete`, {}); renderDns(); } catch (err) { toast(err.message, 'error'); }
        });
        act.append(ed, del);
      }
    });
    const evBox = $('dw-events');
    evBox.innerHTML = '';
    if (dw.events.length) {
      evBox.appendChild(el('h3', 'subhead', t('Letzte Änderungen')));
      const list = el('div', 'dw-event-list');
      dw.events.slice(0, 10).forEach(e => {
        const line = el('div', 'fleet-result');
        line.append(el('span', `health-tag ${e.level === 'good' ? 'good' : 'warning'}`, t(e.level === 'good' ? 'OK' : 'Warnung')),
          raw('span', 'mono', fmtTs(e.ts)), el('span', '', t(e.title)), raw('span', 'lan-sub', e.detail || ''));
        list.appendChild(line);
      });
      evBox.appendChild(list);
    }
  }

  function dnsForm(item) {
    const wrap = $('dw-form-wrap');
    const v = item || { type: 'A', server: 'system' };
    wrap.innerHTML = '';
    wrap.hidden = false;
    wrap.appendChild(el('h3', 'subhead', t(item ? 'Eintrag bearbeiten' : 'DNS-Eintrag überwachen')));
    const form = el('form', 'inline-grid');
    form.autocomplete = 'off';
    const field = (label, input, full) => { const w = el('label', `login-field${full ? ' full' : ''}`); w.append(el('span', '', t(label)), input); form.appendChild(w); return input; };
    const inp = (val, ph) => { const i = el('input'); i.type = 'text'; i.value = val || ''; if (ph) i.placeholder = ph; i.spellcheck = false; return i; };
    const name = field('Name', inp(v.name, 'nas.example.de'));
    const type = el('select');
    dw.types.filter(x => x !== 'PTR').forEach(x => { const o = el('option', '', x); o.value = x; type.appendChild(o); });
    type.value = v.type;
    field('Typ', type);
    const server = inp(v.server, 'system, cloudflare, google, quad9 oder IP');
    field('DNS-Server', server);
    const expected = field('Erwartete Antwort (optional, mehrere mit Komma)', inp(v.expected, '192.168.178.10'), true);
    const note = field('Notiz', inp(v.note), true);
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    const cancel = el('button', 'btn-secondary', t('Abbrechen'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => { wrap.hidden = true; wrap.innerHTML = ''; });
    actions.append(save, cancel);
    form.appendChild(actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      try {
        dw = await api(item ? `/api/dns-watch/${item.id}` : '/api/dns-watch',
          { name: name.value.trim(), type: type.value, server: server.value.trim() || 'system', expected: expected.value, note: note.value });
        wrap.hidden = true;
        wrap.innerHTML = '';
        renderDns();
        setTimeout(loadDns, 2500);
      } catch (err) { toast(err.message, 'error'); save.disabled = false; }
    });
    wrap.appendChild(form);
    setTimeout(() => name.focus(), 30);
  }

  async function loadDns() {
    try { dw = await api('/api/dns-watch'); renderDns(); } catch (e) { /* no permission */ }
  }

  // ---------------------------------------------------------------------------
  // IP conflicts & address changes (under "Geräte im Netzwerk")
  // ---------------------------------------------------------------------------
  async function loadIpEvents() {
    let d;
    try { d = await api('/api/tools/lan'); } catch (e) { return; }
    const evs = d.ip_events || [];
    $('lan-ip-events').hidden = !evs.length;
    if (!evs.length) return;
    const body = table('lan-ip-table', ['Zeit', 'Adresse', 'Vorher', 'Jetzt', 'Art'], evs);
    evs.forEach(e => {
      const r = body.insertRow();
      r.insertCell().textContent = fmtTs(e.ts);
      r.insertCell().appendChild(raw('span', 'mono', e.ip));
      const o = r.insertCell();
      o.append(raw('span', '', e.old_name), raw('span', 'lan-sub mono', e.old_mac));
      const n = r.insertCell();
      n.append(raw('span', '', e.new_name), raw('span', 'lan-sub mono', e.new_mac));
      r.insertCell().appendChild(el('span', `health-tag ${e.kind === 'conflict' ? 'critical' : 'warning'}`, t(e.kind === 'conflict' ? 'IP-Konflikt' : 'Anderes Gerät')));
    });
  }

  async function init() {
    if (!$('auffaelligkeiten')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    $('an-form').addEventListener('submit', async e => {
      e.preventDefault();
      try {
        await api('/api/anomalies/settings', { enabled: $('an-enabled').checked, sensitivity: $('an-sens').value,
          metrics: [...$('an-metrics').querySelectorAll('input:checked')].map(c => c.value) });
        toast(t('Gespeichert.'), 'success');
        loadAnomalies();
      } catch (err) { toast(err.message, 'error'); }
    });
    $('dw-add').addEventListener('click', () => dnsForm(null));
    $('dw-check').addEventListener('click', async () => {
      $('dw-check').disabled = true;
      try { dw = await api('/api/dns-watch/check', {}); renderDns(); } catch (err) { toast(err.message, 'error'); }
    });
    loadAnomalies();
    loadCapacity();
    loadDns();
    loadIpEvents();
    setInterval(() => { if (!document.hidden) { loadAnomalies(); loadIpEvents(); } }, 120000);
  }

  init();
})();
