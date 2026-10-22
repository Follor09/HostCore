/**
 * PiCore Dashboard - system page section "NetBox": comparison with the LAN scan,
 * free addresses, prefixes, IP addresses, devices & VMs; creating IP addresses in NetBox.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  let data = null;
  let tab = 'compare';
  let openForm = null;  // key of the row whose "add to NetBox" form is open

  const KIND = {
    ip_mismatch: ['critical', 'IP weicht ab'],
    mac_mismatch: ['critical', 'Anderes Gerät auf der IP'],
    status: ['warning', 'Reserviert, aber belegt'],
    ip_missing: ['warning', 'IP nicht eingetragen'],
    undocumented: ['', 'Nicht dokumentiert'],
    offline: ['warning', 'Dokumentiert, nicht erreichbar']
  };
  const STATUS = {
    active: 'Aktiv', reserved: 'Reserviert', deprecated: 'Veraltet', dhcp: 'DHCP', slaac: 'SLAAC',
    container: 'Container', planned: 'Geplant', staged: 'Bereitgestellt', failed: 'Fehlerhaft',
    inventory: 'Inventar', decommissioning: 'Wird stillgelegt', offline: 'Offline'
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const t = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(t);
    setTimeout(() => { t.style.transition = 'all 0.3s ease'; t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (res.status === 401) { location.href = '/login'; throw new Error('Nicht angemeldet.'); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const fmtTime = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const fmtNum = n => Number(n).toLocaleString(LOCALE);
  const statusLabel = s => STATUS[s] || s || '–';
  const statusTag = s => el('span', `health-tag ${s === 'active' ? 'good' : s === 'reserved' || s === 'dhcp' ? 'warning' : ''}`, statusLabel(s));

  function nbLink(href, text) {
    if (!href) return el('span', '', text);
    const a = el('a', 'nb-link', text);
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.setAttribute('data-no-i18n', '');
    return a;
  }

  function cellStack(row, main, sub, mono) {
    const c = row.insertCell();
    c.className = mono ? 'mono' : 'wrap';
    const m = typeof main === 'string' ? el('span', 'lan-name', main || '–') : main;
    if (typeof main === 'string') m.setAttribute('data-no-i18n', '');
    c.appendChild(m);
    if (sub) {
      c.appendChild(el('span', 'lan-sub', sub));
    }
    return c;
  }

  const matches = (q, values) => !q || values.some(v => String(v || '').toLowerCase().includes(q));

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------
  function renderSummary() {
    const s = data.summary;
    const box = $('nb-summary');
    box.innerHTML = '';
    const issues = Object.entries(data.issue_counts).filter(([k]) => k !== 'undocumented').reduce((n, [, v]) => n + v, 0);
    [['Präfixe', fmtNum(s.prefixes)], ['IP-Adressen', fmtNum(s.ips)], ['Hardware-Geräte', fmtNum(s.devices)], ['Virtuelle Maschinen', fmtNum(s.vms)],
     ['Nicht dokumentiert', fmtNum(data.issue_counts.undocumented || 0)], ['Abweichungen', fmtNum(issues)]]
      .forEach(([k, v]) => {
        const tile = el('div', 'nb-stat');
        tile.append(el('span', 'nb-stat-value', v), el('span', 'nb-stat-label', k));
        box.appendChild(tile);
      });
    const scan = data.last_scan ? `Letzter Geräte-Scan ${fmtTime(data.last_scan.ts)}` : 'Noch kein Geräte-Scan – für den Abgleich oben unter „Geräte im Netzwerk“ scannen.';
    $('nb-status').textContent = `NetBox ${data.version} · gelesen ${fmtTime(data.fetched)} · ${scan}`;
  }

  function headRow(table, cols) {
    const head = table.createTHead().insertRow();
    cols.forEach(h => head.appendChild(el('th', '', h)));
  }

  function emptyRow(body, cols, text) {
    const c = body.insertRow().insertCell();
    c.colSpan = cols;
    c.textContent = text;
  }

  function addForm(body, key, cols, defaults) {
    const row = body.insertRow();
    row.className = 'nb-form-row';
    const c = row.insertCell();
    c.colSpan = cols;
    const form = el('form', 'nb-form');
    form.autocomplete = 'off';
    const field = (label, input) => { const l = el('label', 'fw-field'); l.append(el('span', '', label), input); return l; };
    const dns = el('input');
    dns.type = 'text'; dns.maxLength = 255; dns.value = defaults.dns_name || ''; dns.placeholder = 'z.B. nas.home.arpa'; dns.spellcheck = false;
    const desc = el('input');
    desc.type = 'text'; desc.maxLength = 200; desc.value = defaults.description || '';
    const status = el('select');
    ['active', 'reserved', 'dhcp'].forEach(v => {
      const o = el('option', '', statusLabel(v));
      o.value = v;
      status.appendChild(o);
    });
    status.value = defaults.status || 'active';
    const save = el('button', 'btn-primary', `${defaults.ip} anlegen`);
    save.type = 'submit';
    const cancel = el('button', 'btn-secondary', 'Abbrechen');
    cancel.type = 'button';
    cancel.addEventListener('click', () => { openForm = null; render(); });
    const actions = el('div', 'nb-form-actions');
    actions.append(cancel, save);
    form.append(field('DNS-Name', dns), field('Beschreibung', desc), field('Status', status), actions);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      save.disabled = true;
      try {
        const r = await api('/api/netbox/ip', { ip: defaults.ip, status: status.value, dns_name: dns.value.trim(), description: desc.value.trim() });
        toast(`${r.address} in NetBox angelegt.`, 'success');
        openForm = null;
        await load(true);
      } catch (err) {
        toast(err.message, 'error');
        save.disabled = false;
      }
    });
    c.appendChild(form);
    if (key === openForm) setTimeout(() => dns.focus(), 0);
    else row.hidden = true;
  }

  function addButton(cell, key, label) {
    if (!can('netbox.write')) return;
    const b = el('button', 'btn-secondary', label);
    b.addEventListener('click', () => { openForm = openForm === key ? null : key; render(); });
    cell.appendChild(b);
  }

  function issueDetail(x) {
    const nb = x.netbox || {};
    switch (x.kind) {
      case 'ip_mismatch': return `NetBox: ${nb.name}${nb.iface ? ` (${nb.iface})` : ''} hat ${nb.ips.join(', ')} – im Netz ${x.ip}`;
      case 'ip_missing': return `MAC gehört in NetBox zu ${nb.name}${nb.iface ? ` (${nb.iface})` : ''}, aber ${x.ip} ist dort nicht eingetragen`;
      case 'mac_mismatch': return `${x.ip} gehört in NetBox zu ${nb.name} (${nb.macs.join(', ')}) – im Netz antwortet ${x.mac}`;
      case 'status': return `In NetBox „${statusLabel(nb.status)}“${nb.name ? ` (${nb.name})` : ''}, antwortet aber im Netz`;
      case 'offline': return `In NetBox aktiv${nb.iface ? ` (${nb.iface})` : ''}, beim letzten Scan keine Antwort`;
      default: return [x.hostname, x.vendor].filter(Boolean).join(' · ') || 'Weder IP noch MAC in NetBox';
    }
  }

  function renderCompare(table, q) {
    const kind = $('nb-kind').value;
    const list = data.issues.filter(x => (!kind || x.kind === kind)
      && matches(q, [x.name, x.ip, x.mac, x.vendor, x.hostname, (x.netbox || {}).name]));
    headRow(table, ['Befund', 'Gerät', 'IP / MAC', 'Details', '']);
    const body = table.createTBody();
    list.forEach(x => {
      const row = body.insertRow();
      const [cls, label] = KIND[x.kind];
      const st = row.insertCell();
      st.appendChild(el('span', `health-tag ${cls}`, label));
      if (x.kind !== 'offline') st.appendChild(el('span', 'lan-sub', x.online ? 'Online' : 'Offline'));
      const name = cellStack(row, x.name || '–', x.self ? 'Dieser Pi' : '');
      name.className = 'wrap';
      cellStack(row, el('span', 'lan-ip', x.ip), x.mac, true);
      const d = row.insertCell();
      d.className = 'wrap nb-detail';
      d.textContent = issueDetail(x);
      const act = row.insertCell();
      act.className = 'row-action';
      if (x.netbox && x.netbox.link) act.appendChild(nbLink(x.netbox.link, 'Öffnen'));
      const key = `issue:${x.ip}:${x.mac}`;
      if (x.kind === 'undocumented') {
        addButton(act, key, 'In NetBox eintragen');
        addForm(body, key, 5, { ip: x.ip, ...(x.suggest || {}) });
      }
    });
    if (!list.length) {
      emptyRow(body, 5, !data.last_scan ? 'Noch kein Geräte-Scan – erst unter „Geräte im Netzwerk“ scannen.'
        : data.issues.length ? 'Keine Treffer für diesen Filter.' : 'NetBox und Netzwerk stimmen überein.');
    }
    $('nb-hint').textContent = `Abgeglichen werden die Netze dieses Pi: ${data.subnets.join(', ') || '–'}. Erkannt über IP und MAC-Adresse (Schnittstellen in NetBox).`;
  }

  function renderFree(q) {
    const box = $('nb-free');
    box.innerHTML = '';
    $('nb-hint').textContent = 'Frei heißt: nicht in NetBox, in keinem IP-Bereich (z.B. DHCP-Pool) und in den letzten 7 Tagen im Netz nicht gesehen.';
    if (!data.free.length) {
      box.appendChild(el('p', 'field-hint', 'Kein IPv4-Netz dieses Pi gefunden.'));
      return;
    }
    data.free.forEach(f => {
      const card = el('div', 'nb-free-net');
      const head = el('div', 'nb-free-head');
      const title = el('span', 'lan-name', f.network);
      title.setAttribute('data-no-i18n', '');
      head.appendChild(title);
      head.appendChild(f.prefix ? nbLink(f.prefix_link, `NetBox-Präfix ${f.prefix}`) : el('span', 'health-tag warning', 'Kein Präfix in NetBox'));
      head.appendChild(el('span', 'lan-sub', `${fmtNum(f.count)} frei${f.excluded_ranges.length ? ` · ohne Bereiche ${f.excluded_ranges.join(', ')}` : ''}`));
      card.appendChild(head);
      const table = el('table', 'data-table');
      const body = table.createTBody();
      f.first.filter(ip => matches(q, [ip])).forEach(ip => {
        const row = body.insertRow();
        cellStack(row, el('span', 'lan-ip', ip), '', true);
        const act = row.insertCell();
        act.className = 'row-action';
        const key = `free:${ip}`;
        const copy = el('button', 'btn-secondary', 'Kopieren');
        copy.addEventListener('click', () => navigator.clipboard.writeText(ip).then(() => toast(`${ip} kopiert.`, 'success'), () => {}));
        act.append(copy, ' ');
        addButton(act, key, 'Reservieren');
        addForm(body, key, 2, { ip, status: 'reserved' });
      });
      if (f.count > f.first.length) emptyRow(body, 2, `… und ${fmtNum(f.count - f.first.length)} weitere`);
      if (!f.count) emptyRow(body, 2, 'Keine freie Adresse.');
      const wrap = el('div', 'table-scroll');
      wrap.appendChild(table);
      card.appendChild(wrap);
      box.appendChild(card);
    });
  }

  function renderPrefixes(table, q) {
    const list = data.prefixes.filter(p => matches(q, [p.prefix, p.description, p.vlan, p.scope, p.vrf]));
    headRow(table, ['Präfix', 'Status', 'Auslastung', 'VLAN / Standort', '']);
    const body = table.createTBody();
    list.forEach(p => {
      const row = body.insertRow();
      cellStack(row, el('span', 'lan-ip', p.prefix), [p.description, p.vrf ? `VRF ${p.vrf}` : '', p.in_lan ? 'Netz dieses Pi' : ''].filter(Boolean).join(' · '), true);
      row.insertCell().appendChild(statusTag(p.status));
      const u = row.insertCell();
      u.className = 'nb-util';
      if (p.pct != null) {
        const bar = el('div', 'progress-bar-bg mini');
        const fill = el('div', `progress-bar-fill ${p.pct >= 90 ? 'danger' : p.pct >= 75 ? 'warn' : 'fill-emerald'}`);
        fill.style.width = `${p.pct}%`;
        bar.appendChild(fill);
        u.append(bar, el('span', 'lan-sub', p.status === 'container'
          ? `${String(p.pct).replace('.', LOCALE.startsWith('de') ? ',' : '.')} % in Präfixen · ${p.children} Unterpräfixe`
          : `${fmtNum(p.used)} / ${fmtNum(p.size)} · ${String(p.pct).replace('.', LOCALE.startsWith('de') ? ',' : '.')} %`));
      } else {
        u.appendChild(el('span', 'lan-sub', p.used === 1 ? '1 Adresse' : `${fmtNum(p.used)} Adressen`));
      }
      cellStack(row, p.vlan || '–', p.scope);
      row.insertCell().appendChild(nbLink(p.link, 'Öffnen'));
    });
    if (!list.length) emptyRow(body, 5, data.prefixes.length ? 'Keine Treffer für diesen Filter.' : 'Keine Präfixe in NetBox.');
    $('nb-hint').textContent = 'Auslastung: eingetragene IP-Adressen (und als belegt markierte Bereiche) im Präfix; bei Containern der Anteil, den Unterpräfixe abdecken.';
  }

  function renderIps(table, q) {
    const list = data.ips.filter(i => matches(q, [i.address, i.dns_name, i.description, i.tenant, (i.owner || {}).name, (i.lan || {}).mac]));
    headRow(table, ['Adresse', 'Status', 'Zugeordnet', 'Im Netz', '']);
    const body = table.createTBody();
    list.slice(0, 1000).forEach(i => {
      const row = body.insertRow();
      cellStack(row, el('span', 'lan-ip', i.address), [i.dns_name, i.description].filter(Boolean).join(' · '), true);
      const st = row.insertCell();
      st.appendChild(statusTag(i.status));
      if (i.role) st.append(' ', el('span', 'health-tag', i.role.toUpperCase()));
      cellStack(row, i.owner ? i.owner.name : '–', i.owner ? [i.owner.kind === 'vm' ? 'VM' : 'Gerät', i.owner.iface].filter(Boolean).join(' · ') : '');
      const lan = row.insertCell();
      if (!i.in_lan || !data.last_scan) lan.textContent = '–';
      else {
        lan.appendChild(el('span', `health-tag ${i.lan.online ? 'good' : ''}`, i.lan.online ? 'Online' : 'Offline'));
        if (i.lan.mac) { const s = el('span', 'lan-sub', i.lan.mac); s.setAttribute('data-no-i18n', ''); lan.appendChild(s); }
      }
      row.insertCell().appendChild(nbLink(i.link, 'Öffnen'));
    });
    if (!list.length) emptyRow(body, 5, data.ips.length ? 'Keine Treffer für diesen Filter.' : 'Keine IP-Adressen in NetBox.');
    $('nb-hint').textContent = list.length > 1000 ? `${fmtNum(list.length)} Treffer – die ersten 1000 werden angezeigt.` : '';
  }

  function renderObjects(table, q) {
    const list = data.objects.filter(o => matches(q, [o.name, o.role, o.site, o.cluster, o.model, o.platform, o.serial, o.tenant, ...o.ips]));
    headRow(table, ['Name', 'Status', 'Rolle / Typ', 'Primäre IP', 'Im Netz', '']);
    const body = table.createTBody();
    list.forEach(o => {
      const row = body.insertRow();
      cellStack(row, o.name, [o.kind === 'vm' ? 'VM' : 'Gerät', o.kind === 'vm' ? o.cluster : o.site, o.tenant].filter(Boolean).join(' · '));
      row.insertCell().appendChild(statusTag(o.status));
      cellStack(row, o.role || '–', [o.model, o.platform].filter(Boolean).join(' · '));
      cellStack(row, el('span', 'lan-ip', o.primary_ip || '–'), o.ips.filter(ip => ip !== o.primary_ip).slice(0, 3).join(', '), true);
      const lan = row.insertCell();
      if (o.lan) lan.appendChild(el('span', `health-tag ${o.lan === 'online' ? 'good' : o.status === 'active' ? 'warning' : ''}`, o.lan === 'online' ? 'Online' : 'Offline'));
      else lan.textContent = '–';
      row.insertCell().appendChild(nbLink(o.link, 'Öffnen'));
    });
    if (!list.length) emptyRow(body, 6, data.objects.length ? 'Keine Treffer für diesen Filter.' : 'Keine Geräte oder VMs in NetBox.');
    $('nb-hint').textContent = '„Im Netz“ nur für Geräte mit IP oder MAC in einem Netz dieses Pi.';
  }

  function render() {
    if (!data) return;
    const q = $('nb-q').value.trim().toLowerCase();
    $('nb-kind').hidden = tab !== 'compare';
    $('nb-free').hidden = tab !== 'free';
    $('nb-table-wrap').hidden = tab === 'free';
    const table = $('nb-table');
    table.innerHTML = '';
    if (tab === 'compare') renderCompare(table, q);
    else if (tab === 'free') renderFree(q);
    else if (tab === 'prefixes') renderPrefixes(table, q);
    else if (tab === 'ips') renderIps(table, q);
    else renderObjects(table, q);
  }

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------
  async function load(refresh) {
    const btn = $('nb-refresh');
    btn.disabled = true;
    if (!data) $('nb-status').textContent = 'Lese NetBox…';
    try {
      data = await api(`/api/netbox/data${refresh ? '?refresh=1' : ''}`);
      $('nb-open').href = data.url;
      $('nb-open').hidden = false;
      renderSummary();
      render();
    } catch (err) {
      $('nb-status').textContent = err.message;
      if (refresh) toast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  async function init() {
    if (!$('netbox')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (!can('system.view')) return;
    let st;
    try { st = await api('/api/netbox/status'); } catch (e) { return; }
    $('nb-setup').hidden = st.configured;
    $('nb-main').hidden = !st.configured;
    $('nb-refresh').hidden = !st.configured;
    if (!st.configured) return;

    document.querySelectorAll('#nb-tabs button').forEach(b => b.addEventListener('click', () => {
      tab = b.dataset.tab;
      openForm = null;
      document.querySelectorAll('#nb-tabs button').forEach(x => x.classList.toggle('active', x === b));
      render();
    }));
    $('nb-q').addEventListener('input', render);
    $('nb-kind').addEventListener('change', render);
    $('nb-filters').addEventListener('submit', e => e.preventDefault());
    $('nb-refresh').addEventListener('click', () => load(true));
    // A new LAN scan changes the comparison: rebuild it (NetBox itself stays cached)
    const scan = $('lan-scan');
    if (scan) scan.addEventListener('click', () => {
      const wait = setInterval(() => { if (!scan.disabled) { clearInterval(wait); load(false); } }, 1000);
    });
    load(false);
  }

  init();
})();
