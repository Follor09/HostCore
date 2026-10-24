/**
 * HostCore Dashboard - weekly report (last 7 days), printable.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const fmtBytes = b => {
    if (b == null) return '–';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)} ${u[i]}`;
  };
  const fmtDate = (ts, opts = { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) =>
    new Date(ts * 1000).toLocaleString(LOCALE, opts);
  const num = (v, unit = '', digits = 1) => (v == null ? '–' : `${Number(v).toLocaleString(LOCALE, { maximumFractionDigits: digits })}${unit}`);

  function section(title, desc) {
    const s = el('section', 'glass-card report-section');
    s.appendChild(el('h2', '', title));
    if (desc) s.appendChild(el('p', 'section-desc', desc));
    return s;
  }

  function tiles(items) {
    const box = el('div', 'report-tiles');
    items.forEach(([label, value, sub, level]) => {
      const t = el('div', 'report-tile');
      t.appendChild(el('span', 'label', label));
      t.appendChild(el('span', `value ${level || ''}`, value));
      if (sub) t.appendChild(el('span', 'sub', sub));
      box.appendChild(t);
    });
    return box;
  }

  function table(headers, rows, noI18nCols = []) {
    const wrap = el('div', 'table-scroll');
    const t = el('table', 'data-table');
    const h = t.createTHead().insertRow();
    headers.forEach(x => h.appendChild(el('th', '', x)));
    const body = t.createTBody();
    rows.forEach(r => {
      const tr = body.insertRow();
      r.forEach((v, i) => {
        const c = tr.insertCell();
        if (v instanceof Node) c.appendChild(v); else c.textContent = v;
        if (noI18nCols.includes(i)) c.setAttribute('data-no-i18n', '');
      });
    });
    if (!rows.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = headers.length;
      c.textContent = 'Keine Daten.';
    }
    wrap.appendChild(t);
    return wrap;
  }

  const tag = (level, text) => el('span', `health-tag ${level}`, text);
  const uptimeTag = u => (u == null ? tag('', 'Keine Daten') : tag(u >= 99.5 ? 'good' : u >= 95 ? 'warning' : 'critical', `${num(u, '%', 2)}`));

  function render(r) {
    const root = $('report');
    root.innerHTML = '';
    const from = fmtDate(r.from, { day: '2-digit', month: '2-digit', year: 'numeric' });
    const to = fmtDate(r.to, { day: '2-digit', month: '2-digit', year: 'numeric' });

    const head = el('div', 'report-head');
    head.appendChild(el('h2', '', `${from} – ${to}`));
    const st = r.status;
    head.appendChild(tag(st.level === 'good' ? 'good' : st.level === 'critical' ? 'critical' : 'warning',
      st.items.length ? `Jetzt: ${st.items.length} Hinweise` : 'Jetzt: alles in Ordnung'));
    root.appendChild(head);
    if (st.items.length) {
      const ul = el('ul', 'report-status');
      st.items.forEach(i => ul.appendChild(el('li', i.level, i.text)));
      root.appendChild(ul);
    }

    // Key figures
    const m = r.metrics, ev = r.events;
    const k = section('Auf einen Blick');
    k.appendChild(tiles([
      ['Ø CPU-Auslastung', num(m.cpu_avg, '%'), `Spitze ${num(m.cpu_max, '%', 0)}`],
      ['Höchste CPU-Temperatur', num(m.temp_max, ' °C'), m.temp_max_ts ? fmtDate(m.temp_max_ts) : '', m.temp_max >= 80 ? 'critical' : m.temp_max >= 70 ? 'warning' : ''],
      ['Ø Arbeitsspeicher', num(m.ram_avg, '%'), `Spitze ${num(m.ram_max, '%', 0)}`],
      ['Daten empfangen', fmtBytes(r.traffic.rx), `gesendet ${fmtBytes(r.traffic.tx)}`],
      ['Anmeldungen', String(r.logins.ok), `${r.logins.failed} fehlgeschlagen${r.logins.ssh_failed ? ` · ${r.logins.ssh_failed} SSH-Fehlversuche` : ''}`, r.logins.failed ? 'warning' : ''],
      ['Ereignisse', String((ev.critical || 0) + (ev.warning || 0)), `${ev.critical || 0} kritisch · ${ev.warning || 0} Warnungen`, ev.critical ? 'critical' : ev.warning ? 'warning' : 'good'],
      ['Neustarts', String(r.reboots.length), r.reboots.length ? r.reboots.map(ts => fmtDate(ts)).join(', ') : 'keine'],
      ['Ø Leistung', m.power_avg != null ? num(m.power_avg, ' W', 2) : '–', m.nvme_temp_max != null ? `NVMe max. ${num(m.nvme_temp_max, ' °C')}` : '']
    ]));
    root.appendChild(k);

    // Availability
    const a = section('Verfügbarkeit', 'Dienste auf dem Server und deine Web-Dienste.');
    a.appendChild(table(['Dienst', 'Verfügbar', 'Ausfälle'],
      r.services.filter(s => !s.never_up).map(s => [s.name, uptimeTag(s.uptime), String(s.outages)])));
    if (r.web_services.length) {
      a.appendChild(el('h3', 'subhead', 'Web-Dienste'));
      a.appendChild(table(['Dienst', 'Erreichbar', 'Ø Antwortzeit'],
        r.web_services.map(w => [w.name, uptimeTag(w.uptime), w.avg_ms != null ? `${Math.round(w.avg_ms)} ms` : '–']), [0]));
    }
    root.appendChild(a);

    // Hardware
    const hw = section('Drosselung & Speicher');
    const tc = r.throttle;
    hw.appendChild(tiles([
      ['Unterspannung', String(tc.undervoltage || 0), '', tc.undervoltage ? 'critical' : 'good'],
      ['Hitze-Drosselung', String(tc.throttled || 0), '', tc.throttled ? 'critical' : 'good'],
      ['Takt begrenzt', String(tc.freq_capped || 0), '', tc.freq_capped ? 'warning' : 'good'],
      ['Temperaturgrenze', String(tc.soft_temp || 0), '', tc.soft_temp ? 'warning' : 'good']
    ]));
    hw.appendChild(table(['Laufwerk', 'Belegt', 'Zuwachs pro Tag', 'Prognose'], r.disk.mounts.map(d => [
      d.mount, `${fmtBytes(d.used)} / ${fmtBytes(d.total)} (${num(d.percent, '%', 0)})`,
      d.per_day != null ? `${d.per_day >= 0 ? '+' : '−'}${fmtBytes(Math.abs(d.per_day))}` : '–',
      d.days_left != null ? tag(d.days_left < 30 ? 'critical' : d.days_left < 180 ? 'warning' : 'good', d.days_left > 3650 ? 'voll in > 10 Jahren' : `voll in ~${d.days_left} Tagen`)
        : d.per_day != null ? tag('good', 'kein nennenswerter Zuwachs') : tag('', 'Sammle Daten')
    ]), [0]));
    root.appendChild(hw);

    // Programs
    const p = section('Ressourcen-Verbrauch', 'Die Programme mit dem größten Verbrauch in der Woche.');
    const grid = el('div', 'report-two');
    const cpuBox = el('div');
    cpuBox.appendChild(el('h3', 'subhead', 'CPU'));
    cpuBox.appendChild(table(['Programm', 'Ø CPU', 'CPU-Zeit'], r.top_cpu.map(x => [x.label, num(x.cpu_avg, '%'),
      x.cpu_minutes >= 60 ? `${num(x.cpu_minutes / 60)} Std.` : `${num(x.cpu_minutes)} Min.`]), [0]));
    const ramBox = el('div');
    ramBox.appendChild(el('h3', 'subhead', 'Arbeitsspeicher'));
    ramBox.appendChild(table(['Programm', 'Maximum'], r.top_ram.map(x => [x.label, fmtBytes(x.rss_max * 1048576)]), [0]));
    grid.append(cpuBox, ramBox);
    p.appendChild(grid);
    root.appendChild(p);

    // Changes
    const c = section('Änderungen & Neues');
    const changes = [
      ...r.updates.map(u => [fmtDate(u.ts), 'Dashboard aktualisiert', u.detail]),
      ...r.changes.map(x => [fmtDate(x.ts), x.action, [x.detail, x.user].filter(Boolean).join(' · ')])
    ];
    c.appendChild(table(['Zeit', 'Was', 'Details'], changes, [2]));
    if (r.new_devices.length) {
      c.appendChild(el('h3', 'subhead', 'Neue Geräte im Netzwerk'));
      const ul = el('ul', 'report-list');
      r.new_devices.forEach(d => { const li = el('li', '', d); li.setAttribute('data-no-i18n', ''); ul.appendChild(li); });
      c.appendChild(ul);
    }
    root.appendChild(c);

    if ((r.journal && r.journal.length) || (r.maintenance && r.maintenance.length)) {
      const j = section('Betriebstagebuch & Wartung', 'Eigene Einträge und Wartungsfenster der Woche.');
      const rows = [
        ...(r.maintenance || []).map(w => [w.start, 'Wartung', `${w.title} · bis ${fmtDate(w.end)}`]),
        ...(r.journal || []).map(e => [e.ts, e.category, [e.title, e.text, e.user].filter(Boolean).join(' · ')])
      ].sort((a, b) => b[0] - a[0]).map(([ts, ...rest]) => [fmtDate(ts), ...rest]);
      j.appendChild(table(['Zeit', 'Art', 'Eintrag'], rows, [2]));
      root.appendChild(j);
    }

    root.appendChild(el('p', 'field-hint report-foot', `Erstellt ${new Date().toLocaleString(LOCALE)} · Daten: letzte 7 Tage (Messwerte werden 7 Tage aufbewahrt).`));
  }

  async function load() {
    try {
      const res = await fetch('/api/report');
      if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
      render(data);
    } catch (err) {
      $('report').innerHTML = '';
      $('report').appendChild(el('p', 'field-hint', err.message));
    }
  }

  $('report-print').addEventListener('click', () => window.print());
  load();
})();
