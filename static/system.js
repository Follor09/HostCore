/**
 * PiCore Dashboard - System tools page
 * Network details and hardware info (more tools are added as sections).
 */
(function () {
  'use strict';

  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

  const $ = id => document.getElementById(id);
  const cache = {};

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  function toast(message, type = 'info') {
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = message;
    $('toast-container').appendChild(t);
    setTimeout(() => {
      t.style.transition = 'all 0.3s ease';
      t.style.opacity = '0';
      setTimeout(() => t.remove(), 300);
    }, 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (res.status === 401) {
      location.href = '/login';
      throw new Error('Nicht angemeldet.');
    }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function renderKv(target, rows) {
    target.innerHTML = '';
    rows.filter(r => r[1] !== undefined && r[1] !== null && r[1] !== '').forEach(([label, value, tag]) => {
      const dd = el('dd');
      if (tag !== undefined) dd.appendChild(el('span', `health-tag ${tag}`, value));
      else dd.textContent = value;
      target.append(el('dt', '', label), dd);
    });
  }

  function fmtBytes(b) {
    if (b == null) return '–';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)} ${units[i]}`;
  }

  // A list row: title + sub line on the left, tags on the right
  function infoRow(title, sub, tags) {
    const row = el('div', 'iface-row');
    const main = el('div', 'workload-main');
    main.append(el('span', 'workload-name', title));
    if (sub) main.append(el('span', 'workload-sub', sub));
    const side = el('div', 'iface-tags');
    (tags || []).forEach(([level, text, title]) => {
      const t = el('span', `health-tag ${level}`, text);
      if (title) t.title = title;
      side.appendChild(t);
    });
    row.append(main, side);
    return row;
  }

  function emptyRow(text) {
    return el('p', 'field-hint', text);
  }

  // ---------------------------------------------------------------------------
  // Network
  // ---------------------------------------------------------------------------
  const KIND_LABEL = { ethernet: 'LAN', wlan: 'WLAN', vpn: 'VPN', container: 'Container', loopback: 'Loopback', other: 'Sonstige' };

  function signalQuality(dbm) {
    if (dbm >= -55) return ['good', 'Sehr gut'];
    if (dbm >= -67) return ['good', 'Gut'];
    if (dbm >= -75) return ['warning', 'Mäßig'];
    return ['critical', 'Schwach'];
  }

  function renderNetwork(n) {
    renderKv($('net-summary'), [
      ['Hostname', n.hostname],
      ['Gateway', n.gateway || '–'],
      ['DNS-Server', (n.dns || []).join(', ') || '–']
    ]);

    const list = $('net-ifaces');
    list.innerHTML = '';
    const showVirtual = $('net-show-virtual').checked;
    n.interfaces
      .filter(i => showVirtual || !['container', 'loopback'].includes(i.kind))
      .forEach(i => {
        const addr = [...i.ipv4, ...i.ipv6.slice(0, 1)].join(' · ') || 'keine IP-Adresse';
        const sub = `${addr}${i.mac ? ` · MAC ${i.mac}` : ''} · ↓ ${fmtBytes(i.rx_bytes)} ↑ ${fmtBytes(i.tx_bytes)}`;
        const tags = [[i.up ? 'good' : '', i.up ? 'Verbunden' : 'Getrennt'], ['', KIND_LABEL[i.kind] || i.kind]];
        if (i.speed_mbps) tags.push(['', i.speed_mbps >= 1000 ? `${i.speed_mbps / 1000} Gbit/s` : `${i.speed_mbps} Mbit/s`]);
        if (i.wlan && i.wlan.signal_dbm !== undefined) {
          const [lvl, txt] = signalQuality(i.wlan.signal_dbm);
          tags.push([lvl, `${txt} (${i.wlan.signal_dbm} dBm)`, 'WLAN-Signalstärke']);
        }
        const title = i.wlan && i.wlan.ssid ? `${i.name} · ${i.wlan.ssid}${i.wlan.band ? ` (${i.wlan.band})` : ''}` : i.name;
        list.appendChild(infoRow(title, sub, tags));
      });
    if (!list.children.length) list.appendChild(emptyRow('Keine Schnittstellen gefunden.'));


  }

  // ---------------------------------------------------------------------------
  // Hardware
  // ---------------------------------------------------------------------------
  function renderHardware(h) {
    const eeprom = h.eeprom || {};
    const eepromTag = eeprom.status === 'update' ? 'warning' : eeprom.status === 'ok' ? 'good' : undefined;
    const eepromText = eeprom.status === 'update'
      ? `Update verfügbar (${eeprom.latest || 'neuer'})`
      : eeprom.status === 'ok' ? `Aktuell (${eeprom.current})` : eeprom.current;
    renderKv($('hw-summary'), [
      ['Modell', h.model],
      ['Prozessor', h.soc],
      ['CPU', `${h.cpu_cores} Kerne${h.cpu_max_mhz ? ` · max. ${(h.cpu_max_mhz / 1000).toFixed(1)} GHz` : ''}`],
      ['Arbeitsspeicher', fmtBytes(h.ram_bytes)],
      ['Revision', h.revision],
      ['Seriennummer', h.serial],
      ['Kernel', `${h.kernel} (${h.architecture})`],
      ['Firmware', h.firmware],
      ['Bootloader', eepromText, eepromTag]
    ]);

    const storage = $('hw-storage');
    storage.innerHTML = '';
    (h.nvme || []).forEach(n => {
      const tags = [];
      if (n.pcie_gen) tags.push([n.gen3_hint ? 'warning' : 'good', `PCIe Gen ${n.pcie_gen} ×${n.link_width || 1}`, n.link_speed]);
      storage.appendChild(infoRow(`${n.model || n.name}`, `${n.name} · Firmware ${n.firmware || '–'}`, tags));
      if (n.gen3_hint) {
        storage.appendChild(emptyRow('Tipp: Die SSD kann PCIe Gen 3 (~doppelt so schnell). Aktivieren mit „dtparam=pciex1_gen=3“ in /boot/firmware/config.txt und Neustart. Offiziell nicht zertifiziert, läuft aber meist stabil.'));
      }
    });
    (h.disks || []).filter(d => !d.name.startsWith('nvme')).forEach(d => {
      const kind = d.transport === 'usb' ? 'USB' : d.name.startsWith('mmcblk') ? 'SD-Karte' : d.transport.toUpperCase() || 'Laufwerk';
      storage.appendChild(infoRow(d.model || d.name, `${d.name} · ${fmtBytes(d.size)}`, [['', kind], ...(d.rotational ? [['', 'HDD']] : [])]));
    });
    if (!storage.children.length) storage.appendChild(emptyRow('Keine Laufwerksdetails verfügbar (nur auf Linux).'));

    const usb = $('hw-usb');
    usb.innerHTML = '';
    (h.usb || []).filter(u => !u.is_hub).forEach(u => {
      const speed = u.speed_mbps >= 5000 ? `USB 3 · ${u.speed_mbps / 1000} Gbit/s` : u.speed_mbps >= 480 ? 'USB 2' : u.speed_mbps ? 'USB 1' : '';
      usb.appendChild(infoRow(u.product, `${u.manufacturer ? `${u.manufacturer} · ` : ''}${u.id} · Port ${u.port}`, speed ? [['', speed]] : []));
    });
    if (!usb.children.length) usb.appendChild(emptyRow('Keine USB-Geräte angeschlossen.'));
  }

  // ---------------------------------------------------------------------------
  // CPU mode
  // ---------------------------------------------------------------------------
  const GOVERNORS = {
    powersave: ['Sparsam', 'Immer niedrigster Takt. Kühl und leise, aber spürbar langsamer.'],
    conservative: ['Sanft', 'Taktet langsam und schrittweise hoch. Spart etwas Strom.'],
    ondemand: ['Ausgewogen', 'Standard: taktet bei Last sofort hoch, im Leerlauf runter.'],
    schedutil: ['Ausgewogen (schedutil)', 'Moderne Variante von „Ausgewogen“, gesteuert vom Scheduler.'],
    performance: ['Leistung', 'Immer maximaler Takt. Schnellste Reaktion, mehr Wärme und Verbrauch.']
  };
  const GOVERNOR_ORDER = ['powersave', 'conservative', 'ondemand', 'schedutil', 'performance'];

  function renderCpuMode(c) {
    const grid = $('cpu-modes');
    grid.innerHTML = '';
    if (!c.available) {
      grid.appendChild(emptyRow('Auf diesem System nicht verfügbar (nur Linux mit cpufreq).'));
      $('cpu-live').textContent = '–';
      return;
    }
    $('cpu-live').textContent = `${c.cur_mhz} MHz`;
    $('cpu-hint').textContent = `Takt: ${c.min_mhz}–${c.max_mhz} MHz`;
    [...c.governors].sort((a, b) => GOVERNOR_ORDER.indexOf(a) - GOVERNOR_ORDER.indexOf(b)).forEach(g => {
      const [label, desc] = GOVERNORS[g] || [g, ''];
      const btn = el('button', `mode-card${g === c.governor ? ' active' : ''}`);
      btn.append(el('strong', '', label), el('span', '', desc || g));
      btn.setAttribute('aria-pressed', String(g === c.governor));
      btn.addEventListener('click', async () => {
        if (g === c.governor) return;
        grid.querySelectorAll('button').forEach(b => { b.disabled = true; });
        try {
          cache.cpu = await api('/api/tools/cpu-mode', { governor: g });
          toast(`CPU-Modus: ${label}`, 'success');
        } catch (err) {
          toast(err.message, 'error');
        }
        renderCpuMode(cache.cpu);
      });
      grid.appendChild(btn);
    });
  }

  // ---------------------------------------------------------------------------
  // Speed test
  // ---------------------------------------------------------------------------
  function benchValues(target, items) {
    target.innerHTML = '';
    items.forEach(([label, value, unit]) => {
      const box = el('div', 'bench-value');
      const v = el('span', 'value', value);
      if (unit) v.appendChild(el('span', 'unit', unit));
      box.append(el('span', 'label', label), v);
      target.appendChild(box);
    });
  }

  function fmtNum(n) {
    return n == null ? '–' : n >= 1000 ? Math.round(n).toLocaleString(LOCALE) : String(n);
  }

  function renderBenchmarks(b) {
    const select = $('bench-drive');
    const selected = select.value;
    select.innerHTML = '';
    b.drives.forEach(d => {
      const o = el('option', '', `${d.label}`);
      o.value = d.mount;
      select.appendChild(o);
    });
    if (selected) select.value = selected;

    const lastDisk = b.results.find(r => r.kind === 'disk' && r.target === select.value) || b.results.find(r => r.kind === 'disk');
    const lastCpu = b.results.find(r => r.kind === 'cpu');
    benchValues($('bench-disk-values'), lastDisk
      ? [['Schreiben', fmtNum(lastDisk.write_mbs), 'MB/s'], ['Lesen', fmtNum(lastDisk.read_mbs), 'MB/s'], ['4K zufällig', fmtNum(lastDisk.iops_4k), 'IOPS']]
      : [['Noch kein Ergebnis', '–']]);
    benchValues($('bench-cpu-values'), lastCpu
      ? [['1 Kern', fmtNum(lastCpu.single_mbs), 'MB/s'], [`${lastCpu.cores} Kerne`, fmtNum(lastCpu.multi_mbs), 'MB/s'], ['Skalierung', `${lastCpu.scaling}×`]]
      : [['Noch kein Ergebnis', '–']]);
    ['bench-disk', 'bench-cpu'].forEach(id => { $(id).disabled = b.running; });

    const table = $('bench-history');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Datum', 'Test', 'Ergebnis', 'Temperatur', 'CPU-Modus'].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    b.results.forEach(r => {
      const row = body.insertRow();
      const result = r.kind === 'disk'
        ? `↑ ${fmtNum(r.write_mbs)} MB/s · ↓ ${fmtNum(r.read_mbs)} MB/s · ${fmtNum(r.iops_4k)} IOPS`
        : `${fmtNum(r.single_mbs)} MB/s (1 Kern) · ${fmtNum(r.multi_mbs)} MB/s (${r.cores})`;
      [new Date(r.ts * 1000).toLocaleString(LOCALE, { dateStyle: 'short', timeStyle: 'short' }),
       r.kind === 'disk' ? `Laufwerk ${r.target}` : 'CPU', result,
       r.temp_c != null ? `${r.temp_c} °C` : '–', r.governor || '–'].forEach(v => { row.insertCell().textContent = v; });
    });
    if (!b.results.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 5;
      c.textContent = 'Noch keine Tests.';
    }
  }

  async function runBenchmark(kind) {
    const btn = $(kind === 'disk' ? 'bench-disk' : 'bench-cpu');
    const label = btn.textContent;
    ['bench-disk', 'bench-cpu'].forEach(id => { $(id).disabled = true; });
    btn.textContent = 'Läuft…';
    try {
      await api('/api/tools/benchmark', { kind, mount: $('bench-drive').value || '/' });
      toast('Test abgeschlossen.', 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.textContent = label;
      await load('bench');
    }
  }

  // ---------------------------------------------------------------------------
  // Log search
  // ---------------------------------------------------------------------------
  function highlight(target, text, query) {
    if (!query) { target.textContent = text; return; }
    const lower = text.toLowerCase(), q = query.toLowerCase();
    let pos = 0, idx;
    while ((idx = lower.indexOf(q, pos)) !== -1) {
      target.appendChild(document.createTextNode(text.slice(pos, idx)));
      target.appendChild(el('mark', '', text.slice(idx, idx + q.length)));
      pos = idx + q.length;
    }
    target.appendChild(document.createTextNode(text.slice(pos)));
  }

  function renderLogUnits(u) {
    const select = $('log-unit');
    if (!u.supported) {
      $('log-status').textContent = 'Kein systemd-Journal auf diesem System.';
      return;
    }
    u.units.forEach(name => {
      const o = el('option', '', name.replace(/\.service$/, ''));
      o.value = name;
      select.appendChild(o);
    });
  }

  async function searchLogs(e) {
    if (e) e.preventDefault();
    const query = $('log-query').value.trim();
    const params = new URLSearchParams({
      unit: $('log-unit').value, priority: $('log-priority').value, since: $('log-since').value, q: query
    });
    const status = $('log-status'), box = $('log-results');
    status.textContent = 'Suche…';
    try {
      const r = await api(`/api/tools/logs?${params}`);
      box.innerHTML = '';
      if (!r.supported) { status.textContent = 'Kein systemd-Journal auf diesem System.'; return; }
      r.entries.forEach(entry => {
        const line = el('div', `log-line${entry.priority <= 3 ? ' err' : entry.priority === 4 ? ' warn' : ''}`);
        const t = el('time', '', new Date(entry.ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }));
        const unit = el('span', 'log-unit', (entry.unit || '').replace(/\.service$/, ''));
        unit.title = entry.unit + (entry.pid ? ` (PID ${entry.pid})` : '');
        const msg = el('span', 'log-msg');
        highlight(msg, entry.message, query);
        line.append(t, unit, msg);
        box.appendChild(line);
      });
      status.textContent = r.entries.length
        ? `${r.entries.length}${r.entries.length >= r.limit ? '+' : ''} Einträge${r.hint ? ' · ' + r.hint : ''}`
        : `Keine Einträge gefunden.${r.hint ? ' ' + r.hint : ''}`;
    } catch (err) {
      status.textContent = err.message;
    }
  }

  // ---------------------------------------------------------------------------
  // Storage cleanup
  // ---------------------------------------------------------------------------
  function renderCleanup(c) {
    $('cleanup-free').textContent = `${fmtBytes(c.free_bytes)} frei`;
    const list = $('cleanup-items');
    list.innerHTML = '';
    if (!c.items.length) list.appendChild(emptyRow('Nichts zum Aufräumen gefunden.'));
    c.items.forEach(item => {
      const size = item.size == null ? 'unbekannt' : `${item.size_is_total ? 'gesamt ' : ''}${fmtBytes(item.size)}`;
      const row = infoRow(item.label, item.desc, [[item.size ? 'warning' : '', size]]);
      const btn = el('button', 'btn-secondary', 'Aufräumen');
      btn.disabled = item.size === 0;
      btn.dataset.item = item.id;
      btn.addEventListener('click', () => runCleanup([item.id], btn));
      row.querySelector('.iface-tags').appendChild(btn);
      list.appendChild(row);
    });
    $('cleanup-all').disabled = !c.items.some(i => i.size !== 0);
  }

  async function runCleanup(ids, btn) {
    const msg = $('cleanup-msg');
    const buttons = [...document.querySelectorAll('#cleanup-items button'), $('cleanup-all')];
    buttons.forEach(b => { b.disabled = true; });
    if (btn) btn.textContent = 'Läuft…';
    let freed = 0;
    const errors = [];
    for (const id of ids) {
      msg.textContent = `Räume auf: ${id}…`;
      msg.className = 'form-msg';
      try {
        freed += (await api('/api/tools/cleanup', { item: id })).freed;
      } catch (err) {
        errors.push(err.message);
      }
    }
    msg.textContent = errors.length ? errors.join(' · ') : `${fmtBytes(freed)} freigegeben.`;
    msg.className = `form-msg ${errors.length ? 'error' : 'success'}`;
    await load('cleanup');
  }

  // ---------------------------------------------------------------------------
  // LAN devices
  // ---------------------------------------------------------------------------
  const shortHost = h => (h || '').replace(/\.(fritz\.box|lan|local|home|localdomain|home\.arpa|speedport\.ip|box)$/i, '');

  function lanAction(label, cls, onClick) {
    const b = el('button', cls, label);
    b.addEventListener('click', onClick);
    return b;
  }

  async function lanPost(url, body) {
    try {
      cache.lan = await api(url, body || {});
      renderLan(cache.lan);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function startRename(cell, d) {
    cell.innerHTML = '';
    const input = el('input', 'inline-input');
    input.type = 'text';
    input.maxLength = 40;
    input.value = d.alias || shortHost(d.hostname);
    input.setAttribute('aria-label', 'Name');
    const save = () => lanPost(`/api/tools/lan/${encodeURIComponent(d.mac)}/rename`, { alias: input.value });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); save(); }
      if (e.key === 'Escape') renderLan(cache.lan);
    });
    input.addEventListener('blur', save);
    cell.appendChild(input);
    input.focus();
    input.select();
  }

  function renderLan(r) {
    const status = $('lan-status');
    const last = r.last_scan;
    status.textContent = r.running ? 'Scan läuft…'
      : last ? `Letzter Scan: ${fmtTime(last.ts)} · ${last.online} online · ${last.duration} s · ${(last.subnets || []).join(', ')}`
      : `Noch nicht gescannt. Netz: ${(r.subnets || []).join(', ') || '–'}`;
    $('lan-scan').disabled = r.running;

    const q = $('lan-filter').value.trim().toLowerCase();
    const show = $('lan-show').value;
    const list = r.devices.filter(d => (show === 'all' || (show === 'online' ? d.online : d.new))
      && (!q || [d.alias, d.hostname, d.ip, d.mac, d.vendor].some(v => (v || '').toLowerCase().includes(q))));

    const table = $('lan-devices');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Status', 'Gerät', 'IP / MAC', 'Zuletzt gesehen', ''].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    list.forEach(d => {
      const row = body.insertRow();
      const st = row.insertCell();
      st.appendChild(el('span', `health-tag ${d.online ? 'good' : ''}`, d.online ? 'Online' : 'Offline'));
      if (d.new) st.append(' ', el('span', 'health-tag warning', 'Neu'));

      const name = row.insertCell();
      name.className = 'wrap';
      const title = el('span', 'lan-name', d.alias || shortHost(d.hostname) || '–');
      title.setAttribute('data-no-i18n', '');
      name.appendChild(title);
      if (d.self) name.append(' ', el('span', 'health-tag', 'Dieser Pi'));
      const sub = el('span', 'lan-sub', [d.alias && d.hostname ? d.hostname : '', d.vendor].filter(Boolean).join(' · '));
      name.appendChild(sub);

      const addr = row.insertCell();
      addr.className = 'mono';
      addr.append(el('span', 'lan-ip', d.ip || '–'), el('span', 'lan-sub', d.mac));
      row.insertCell().textContent = d.last_seen ? fmtTime(d.last_seen) : '–';

      const actions = row.insertCell();
      actions.className = 'row-action';
      actions.append(lanAction('Umbenennen', 'btn-secondary', () => startRename(name, d)));
      if (!d.self) {
        if (d.in_wol) actions.append(' ', el('span', 'health-tag good', 'Wake-on-LAN'));
        else actions.append(' ', lanAction('Zu Wake-on-LAN', 'btn-secondary', () =>
          lanPost(`/api/tools/lan/${encodeURIComponent(d.mac)}/wol`, { name: d.alias || shortHost(d.hostname) || d.ip })
            .then(() => toast('Zu Wake-on-LAN hinzugefügt.', 'success'))));
        const forget = el('button', 'btn-danger', 'Vergessen');
        confirmClick(forget, 'Vergessen', 'Sicher?', () => lanPost(`/api/tools/lan/${encodeURIComponent(d.mac)}/forget`));
        actions.append(' ', forget);
      }
    });
    if (!list.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 5;
      c.textContent = r.devices.length ? 'Keine Geräte für diesen Filter.' : 'Noch keine Geräte – „Jetzt scannen“ drücken.';
    }
  }

  async function runLanScan() {
    const btn = $('lan-scan');
    btn.disabled = true;
    btn.textContent = 'Scanne…';
    $('lan-status').textContent = 'Scan läuft – dauert je nach Netzgröße 5–20 Sekunden…';
    try {
      cache.lan = await api('/api/tools/lan/scan', {});
      renderLan(cache.lan);
      const n = cache.lan.devices.filter(d => d.new).length;
      toast(n ? `Scan fertig – ${n} neue Geräte.` : 'Scan fertig.', 'success');
    } catch (err) {
      toast(err.message, 'error');
      $('lan-status').textContent = err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Jetzt scannen';
    }
  }

  // ---------------------------------------------------------------------------
  // Port check
  // ---------------------------------------------------------------------------
  const REACH = { open: ['warning', 'Erreichbar'], blocked: ['good', 'Blockiert'], local: ['', 'Nur lokal'] };

  function renderPorts(r) {
    const fw = $('ports-fw');
    fw.className = `health-tag ${r.firewall === 'active' ? 'good' : 'warning'}`;
    fw.textContent = r.firewall === 'active' ? 'Firewall aktiv' : r.firewall === 'inactive' ? 'Firewall aus' : 'Keine Firewall';
    const table = $('ports-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Port', 'Protokoll', 'Dienst', 'Adresse', 'LAN', 'VPN'].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    r.ports.forEach(p => {
      const row = body.insertRow();
      [p.port, p.proto, p.service || p.process || '–', p.address].forEach(v => { row.insertCell().textContent = v; });
      ['lan', 'vpn'].forEach(k => {
        const [level, text] = REACH[p[k]];
        const cell = row.insertCell();
        cell.appendChild(el('span', `health-tag ${level}`, text));
        if (p.docker && k === 'lan') cell.append(' ', el('span', 'health-tag', 'Docker'));
      });
    });
    if (!r.ports.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.textContent = 'Keine Daten.';
    }
    const hints = [];
    if (r.firewall !== 'active') hints.push('Ohne aktive Firewall ist jeder Port erreichbar, der nicht nur lokal lauscht.');
    if (r.ports.some(p => p.docker)) hints.push('Docker-Ports umgehen die Firewall.');
    if (r.hint) hints.push(r.hint);
    $('ports-hint').textContent = hints.join(' ');
  }

  const PORT_STATE = { open: ['good', 'Offen'], closed: ['', 'Geschlossen'], filtered: ['warning', 'Keine Antwort'], error: ['critical', 'Fehler'] };

  async function runPortTest(e) {
    e.preventDefault();
    const btn = e.submitter || $('port-test-form').querySelector('button');
    btn.disabled = true;
    try {
      const r = await api('/api/tools/port-test', { host: $('port-test-host').value, ports: $('port-test-ports').value });
      const table = $('port-test-results');
      table.innerHTML = '';
      const head = table.createTHead().insertRow();
      ['Port', 'Status', 'Dienst', 'Antwortzeit', 'Kennung'].forEach(h => head.appendChild(el('th', '', h)));
      const body = table.createTBody();
      r.results.forEach(p => {
        const row = body.insertRow();
        row.insertCell().textContent = p.port;
        const [level, text] = PORT_STATE[p.state];
        const st = row.insertCell();
        st.appendChild(el('span', `health-tag ${level}`, text));
        if (p.error) st.title = p.error;
        row.insertCell().textContent = p.service || '–';
        row.insertCell().textContent = p.ms != null ? `${p.ms} ms` : '–';
        const banner = row.insertCell();
        banner.className = 'wrap mono';
        banner.textContent = p.banner || '–';
        banner.setAttribute('data-no-i18n', '');
      });
      $('port-test-wrap').hidden = false;
      const open = r.results.filter(p => p.state === 'open').length;
      toast(`${r.host}${r.ip !== r.host ? ` (${r.ip})` : ''}: ${open} von ${r.results.length} offen`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Firewall (ufw)
  // ---------------------------------------------------------------------------
  const SCOPE = { lan: ['', 'LAN'], vpn: ['good', 'VPN'], local: ['', 'Lokal'], internet: ['warning', 'Internet'] };
  const FW_DEFAULT = { deny: 'Blockiert', reject: 'Abgelehnt', allow: 'Erlaubt', disabled: 'Aus' };
  const FW_ACTION = { allow: 'Erlauben', deny: 'Blockieren', reject: 'Ablehnen', limit: 'Begrenzen' };

  function fmtTime(ts) {
    return new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function scopeTag(scope) {
    const [level, text] = SCOPE[scope] || SCOPE.internet;
    return el('span', `health-tag ${level}`, text);
  }

  // Destructive buttons need a second click within 4 s
  function confirmClick(btn, label, confirmLabel, action) {
    btn.addEventListener('click', () => {
      if (!btn.classList.contains('confirming')) {
        btn.classList.add('confirming');
        btn.textContent = confirmLabel;
        setTimeout(() => { btn.classList.remove('confirming'); btn.textContent = label; }, 4000);
        return;
      }
      btn.classList.remove('confirming');
      btn.textContent = label;
      action();
    });
  }

  async function firewallAction(action, params = {}) {
    const msg = $('fw-msg');
    document.querySelectorAll('#firewall button').forEach(b => { b.disabled = true; });
    msg.className = 'form-msg';
    msg.textContent = 'Wird ausgeführt…';
    try {
      cache.firewall = await api('/api/tools/firewall', { action, params });
      msg.textContent = '';
      document.querySelectorAll('#firewall button').forEach(b => { b.disabled = false; });
      toast('Firewall aktualisiert.', 'success');
      renderFirewall(cache.firewall);
      return true;
    } catch (err) {
      msg.className = 'form-msg error';
      msg.textContent = err.message;
      document.querySelectorAll('#firewall button').forEach(b => { b.disabled = false; });
      return false;
    }
  }

  function renderFirewall(f) {
    const state = $('fw-state');
    $('fw-hint').textContent = '';
    if (!f.installed || f.error) {
      $('fw-body').hidden = true;
      state.className = `health-tag ${f.error ? 'critical' : ''}`;
      state.textContent = f.error ? 'Fehler' : 'Nicht installiert';
      $('fw-hint').textContent = f.hint || f.error;
      return;
    }
    $('fw-body').hidden = false;
    state.className = `health-tag ${f.active ? 'good' : 'warning'}`;
    state.textContent = f.active ? 'Aktiv' : 'Inaktiv';
    renderKv($('fw-summary'), [
      ['Eingehend', FW_DEFAULT[f.defaults.incoming] || f.defaults.incoming || '–'],
      ['Ausgehend', FW_DEFAULT[f.defaults.outgoing] || f.defaults.outgoing || '–'],
      ['Protokollierung', f.logging || '–'],
      ['Immer erreichbar', `SSH (Port ${f.ssh_port}) · Dashboard (Port ${f.dashboard_port})`]
    ]);

    // On/off: fresh button each render so old listeners don't stack up
    const old = $('fw-toggle');
    const toggle = old.cloneNode(false);
    old.replaceWith(toggle);
    toggle.hidden = false;
    toggle.disabled = false;
    if (f.active) {
      toggle.className = 'btn-danger';
      toggle.textContent = 'Firewall ausschalten';
      confirmClick(toggle, 'Firewall ausschalten', 'Wirklich ausschalten?', () => firewallAction('disable'));
    } else {
      toggle.className = 'btn-primary';
      toggle.textContent = 'Firewall einschalten';
      if (f.suggestions.length) {
        confirmClick(toggle, 'Firewall einschalten', 'Trotzdem einschalten?', () => firewallAction('enable'));
        toggle.addEventListener('click', () => {
          if (toggle.classList.contains('confirming')) {
            $('fw-msg').className = 'form-msg warning';
            $('fw-msg').textContent = `Erst die empfohlenen Regeln hinzufügen – sonst blockiert: ${f.suggestions.map(x => x.label).join(', ')}`;
          }
        });
      } else {
        toggle.addEventListener('click', () => firewallAction('enable'));
      }
    }

    $('fw-suggestions-wrap').hidden = !f.suggestions.length;
    const sugg = $('fw-suggestions');
    sugg.innerHTML = '';
    f.suggestions.forEach(x => {
      const row = infoRow(x.label, x.desc, []);
      const btn = el('button', 'btn-secondary', 'Hinzufügen');
      btn.addEventListener('click', () => firewallAction('preset', { preset: x.id }));
      row.querySelector('.iface-tags').appendChild(btn);
      sugg.appendChild(row);
    });

    const table = $('fw-rules');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['#', 'Ziel', 'Aktion', 'Von', 'Kommentar', ''].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    f.rules.forEach(r => {
      const row = body.insertRow();
      row.insertCell().textContent = r.num ?? '–';
      const to = row.insertCell();
      to.textContent = r.to.replace(' (v6)', '').replace(/^Anywhere/, 'Alles');
      if (r.v6) to.append(' ', el('span', 'health-tag', 'IPv6'));
      const act = row.insertCell();
      const level = r.action === 'allow' ? 'good' : r.action === 'limit' ? 'warning' : 'critical';
      act.appendChild(el('span', `health-tag ${level}`, `${FW_ACTION[r.action] || r.action}${r.direction === 'out' ? ' (ausgehend)' : ''}`));
      row.insertCell().textContent = r.from ? r.from.replace(' (v6)', '').replace(/^Anywhere/, 'Überall') : '–';
      const comment = row.insertCell();
      comment.className = 'wrap';
      comment.textContent = r.comment || '–';
      const cell = row.insertCell();
      cell.className = 'row-action';
      if (r.num == null) return;  // saved rule while ufw is off: no number to delete by
      const del = el('button', 'btn-danger', 'Löschen');
      confirmClick(del, 'Löschen', 'Sicher?', () => firewallAction('delete', { num: r.num }));
      cell.appendChild(del);
    });
    if (!f.rules.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.textContent = 'Keine Regeln.';
      c.className = 'wrap';
    }
    if (!f.active && f.rules.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.className = 'wrap note';
      c.textContent = 'Gespeicherte Regeln – sie gelten, sobald die Firewall eingeschaltet ist. Löschen geht nur bei eingeschalteter Firewall.';
    }
    $('fw-docker-hint').hidden = !f.docker;
  }

  async function addFirewallRule(e) {
    e.preventDefault();
    const ok = await firewallAction('rule', {
      action: $('fw-action').value,
      port: $('fw-port').value.trim(),
      proto: $('fw-proto').value,
      from: $('fw-from').value.trim() || 'any',
      comment: $('fw-comment').value.trim()
    });
    if (ok) ['fw-port', 'fw-from', 'fw-comment'].forEach(id => { $(id).value = ''; });
  }

  // ---------------------------------------------------------------------------
  // SSH logins + fail2ban
  // ---------------------------------------------------------------------------
  function bannedIps(f2b) {
    const set = new Set();
    ((f2b && f2b.jails) || []).forEach(j => j.banned.forEach(ip => set.add(ip)));
    return set;
  }

  function renderFail2ban(f) {
    const box = $('ssh-f2b');
    box.innerHTML = '';
    if (!f || !f.installed) {
      box.appendChild(emptyRow((f && f.hint) || 'fail2ban ist nicht installiert.'));
      return;
    }
    if (!f.active) {
      box.appendChild(infoRow('fail2ban', 'Installiert, aber nicht gestartet: sudo systemctl enable --now fail2ban', [['warning', 'Inaktiv']]));
      return;
    }
    if (f.error) {
      box.appendChild(infoRow('fail2ban', f.error, [['good', 'Aktiv']]));
      return;
    }
    f.jails.forEach(j => {
      box.appendChild(infoRow(`Jail „${j.name}“`, j.banned.length ? '' : 'Keine IP gesperrt',
        [['good', 'Aktiv'], [j.banned.length ? 'warning' : '', `${j.banned.length} gesperrt`]]));
      j.banned.forEach(ip => {
        const row = infoRow(ip, `Gesperrt in „${j.name}“`, []);
        row.classList.add('sub-row');
        const btn = el('button', 'btn-secondary', 'Entsperren');
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          try {
            const status = await api('/api/tools/fail2ban/unban', { ip });
            if (cache.ssh) cache.ssh.fail2ban = status;
            toast(`${ip} entsperrt.`, 'success');
            renderSsh(cache.ssh);
          } catch (err) {
            toast(err.message, 'error');
            btn.disabled = false;
          }
        });
        row.querySelector('.iface-tags').appendChild(btn);
        box.appendChild(row);
      });
    });
    if (!f.jails.length) box.appendChild(emptyRow('Keine Jails aktiv.'));
  }

  function renderSshEvents(s) {
    const filter = $('ssh-filter').value;
    const table = $('ssh-events');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Zeit', 'Ergebnis', 'Benutzer', 'IP-Adresse', 'Herkunft', 'Methode'].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    const events = (s.events || []).filter(e => filter === 'all' || e.kind === filter);
    events.forEach(e => {
      const row = body.insertRow();
      row.insertCell().textContent = fmtTime(e.ts);
      const ok = e.kind === 'ok';
      row.insertCell().appendChild(el('span', `health-tag ${ok ? 'good' : 'critical'}`,
        ok ? 'Erfolgreich' : e.attempts > 1 ? `Fehlgeschlagen (${e.attempts}×)` : 'Fehlgeschlagen'));
      row.insertCell().textContent = e.user;
      row.insertCell().textContent = e.ip;
      row.insertCell().appendChild(scopeTag(e.scope));
      row.insertCell().textContent = e.method || '–';
    });
    if (!events.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.textContent = 'Keine Anmeldungen in diesem Zeitraum.';
    }
  }

  function renderSsh(s) {
    const hint = [];
    if (!s.supported) {
      $('ssh-stats').innerHTML = '';
      hint.push('Kein systemd-Journal auf diesem System.');
    } else if (s.error) {
      hint.push(s.error);
    } else {
      const sum = s.summary;
      benchValues($('ssh-stats'), [
        ['Erfolgreich (24 Std.)', String(sum.ok_24h)],
        ['Fehlversuche (24 Std.)', String(sum.fail_24h)],
        ['Fehlversuche (7 Tage)', String(sum.fail_7d)],
        ['IPs mit Fehlversuchen', String(sum.fail_ips_7d)]
      ]);
      $('ssh-stats').querySelectorAll('.value').forEach((v, i) => {
        if (i === 1 && v.textContent !== '0') v.classList.add('warn');
      });
    }

    const active = $('ssh-active');
    active.innerHTML = '';
    (s.active || []).forEach(a => {
      const row = infoRow(a.user, `${a.host} · ${a.terminal || '–'} · seit ${fmtTime(a.started)}`, []);
      row.querySelector('.iface-tags').appendChild(scopeTag(a.scope));
      active.appendChild(row);
    });
    if (!active.children.length) active.appendChild(emptyRow('Niemand angemeldet.'));

    renderFail2ban(s.fail2ban);

    const banned = bannedIps(s.fail2ban);
    const table = $('ssh-top');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['IP-Adresse', 'Herkunft', 'Versuche', 'Benutzernamen', 'Zuletzt', 'Status'].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    (s.top_ips || []).forEach(p => {
      const row = body.insertRow();
      row.insertCell().textContent = p.ip;
      row.insertCell().appendChild(scopeTag(p.scope));
      row.insertCell().textContent = p.attempts;
      const users = row.insertCell();
      users.className = 'wrap';
      users.textContent = p.users.join(', ');
      row.insertCell().textContent = fmtTime(p.last);
      const st = row.insertCell();
      if (banned.has(p.ip)) st.appendChild(el('span', 'health-tag critical', 'Gesperrt'));
      else st.textContent = '–';
    });
    if (!(s.top_ips || []).length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.textContent = 'Keine Fehlversuche in den letzten 7 Tagen.';
    }

    renderSshEvents(s);
    if (s.hint) hint.push(s.hint);
    if (s.truncated) hint.push('Sehr viele Einträge – nur die neuesten wurden ausgewertet.');
    $('ssh-hint').textContent = hint.join(' ');
  }

  // ---------------------------------------------------------------------------
  // Service availability (uptime)
  // ---------------------------------------------------------------------------
  const STATUS_LABEL = { active: 'Aktiv', inactive: 'Inaktiv', error: 'Fehler' };
  let uptimeDays = 7;

  function fmtDuration(sec) {
    if (sec < 60) return '< 1 Min.';
    const m = Math.round(sec / 60);
    if (m < 60) return `${m} Min.`;
    const h = Math.floor(m / 60), rm = m % 60;
    if (h < 24) return rm ? `${h} Std. ${rm} Min.` : `${h} Std.`;
    const d = Math.floor(h / 24), rh = h % 24;
    return rh ? `${d} Tage ${rh} Std.` : `${d} Tage`;
  }

  function renderUptime(u) {
    const list = $('avail-list');
    list.innerHTML = '';
    const span = u.now - u.since;
    u.services.forEach(svc => {
      const row = el('div', 'avail-row');
      const head = el('div', 'avail-head');
      head.appendChild(el('span', 'workload-name', svc.name));
      const pct = svc.uptime;
      // Never active in the whole range: not installed / switched off on purpose, not an outage
      const neverUp = pct === 0 && svc.periods.every(p => p.status === 'inactive');
      const level = pct == null || neverUp ? '' : pct >= 99.5 ? 'good' : pct >= 95 ? 'warning' : 'critical';
      head.appendChild(el('span', `health-tag ${level}`, pct == null ? 'Keine Daten' : neverUp ? 'Nicht in Betrieb'
        : `${pct.toLocaleString(LOCALE, { maximumFractionDigits: 2 })}%`));
      row.appendChild(head);

      // Timeline: one segment per period, gaps show the "no data" background
      const bar = el('div', 'avail-bar');
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', `${svc.name}: ${pct == null ? 'Keine Daten' : pct + '%'}`);
      svc.periods.forEach(p => {
        const seg = el('span', `avail-seg ${p.status}`);
        seg.style.left = `${Math.max(0, (p.start - u.since) / span * 100)}%`;
        seg.style.width = `${Math.max(0.3, (p.end - p.start) / span * 100)}%`;
        seg.title = `${STATUS_LABEL[p.status] || p.status}: ${fmtTime(p.start)} – ${fmtTime(p.end)} (${fmtDuration(p.end - p.start)})`;
        bar.appendChild(seg);
      });
      row.appendChild(bar);

      const axis = el('div', 'avail-axis');
      axis.append(el('span', '', fmtTime(u.since)), el('span', '', 'Jetzt'));
      row.appendChild(axis);

      if (svc.outages.length && !neverUp) {
        const last = svc.outages[0];
        const det = el('details', 'avail-outages');
        det.appendChild(el('summary', '',
          `${svc.outages.length}× nicht aktiv · zuletzt ${fmtTime(last.start)} (${fmtDuration(last.duration)})`));
        svc.outages.slice(0, 10).forEach(o => {
          det.appendChild(el('div', 'avail-outage',
            `${STATUS_LABEL[o.status] || o.status}: ${fmtTime(o.start)} – ${fmtTime(o.end)} · ${fmtDuration(o.duration)}`));
        });
        row.appendChild(det);
      } else if (svc.measured) {
        row.appendChild(el('p', 'field-hint', 'Keine Ausfälle in diesem Zeitraum.'));
      }
      list.appendChild(row);
    });
    if (!u.services.length) list.appendChild(emptyRow('Keine Dienste.'));
  }

  // ---------------------------------------------------------------------------
  // Throttling log
  // ---------------------------------------------------------------------------
  const THROTTLE_KIND = {
    undervoltage: ['Unterspannung', 'critical', 'Netzteil liefert zu wenig – offizielles 27-W-Netzteil verwenden.'],
    throttled: ['Gedrosselt (Hitze)', 'critical', 'CPU wurde wegen Temperatur heruntergetaktet.'],
    freq_capped: ['Takt begrenzt', 'warning', 'Maximaltakt war begrenzt (Hitze oder Spannung).'],
    soft_temp: ['Temperaturgrenze', 'warning', 'Weiche Temperaturgrenze erreicht (ab ca. 80 °C).']
  };

  function renderThrottle(t) {
    const now = $('throttle-now');
    const cur = t.current || {};
    const active = cur.undervoltage || cur.throttled || cur.freq_capped || cur.soft_temp_limit;
    now.className = `health-tag ${!t.supported ? '' : active ? 'critical' : 'good'}`;
    now.textContent = !t.supported ? 'Nur auf Raspberry Pi' : active ? 'Gerade gedrosselt' : 'Keine Drosselung';

    benchValues($('throttle-stats'), Object.entries(THROTTLE_KIND).map(([k, [label]]) => [label, String(t.counts[k] || 0)]));
    $('throttle-stats').querySelectorAll('.value').forEach(v => { if (v.textContent !== '0') v.classList.add('warn'); });

    const table = $('throttle-events');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Beginn', 'Art', 'Dauer', 'CPU-Temp.'].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    t.events.forEach(ev => {
      const [label, level, desc] = THROTTLE_KIND[ev.kind] || [ev.kind, '', ''];
      const r = body.insertRow();
      r.insertCell().textContent = ev.since_boot ? `seit Neustart ${fmtTime(ev.start)}` : fmtTime(ev.start);
      const kind = r.insertCell();
      const tag = el('span', `health-tag ${level}`, label);
      tag.title = desc;
      kind.appendChild(tag);
      r.insertCell().textContent = ev.open ? 'läuft noch' : ev.since_boot ? 'unbekannt' : ev.short ? '< 1 Min.' : fmtDuration(ev.duration);
      r.insertCell().textContent = ev.temp != null ? `${ev.temp.toFixed(1)} °C` : '–';
    });
    if (!t.events.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 4;
      c.textContent = t.supported ? 'Keine Drosselung in den letzten 30 Tagen. 👍' : 'Nur auf dem Raspberry Pi verfügbar.';
    }
    $('throttle-hint').textContent = t.counts.undervoltage
      ? 'Unterspannung gefunden: Das Netzteil ist zu schwach oder das Kabel zu dünn. Der Pi 5 braucht 5 V / 5 A (27 W) für volle USB-Leistung.'
      : t.counts.throttled ? 'Hitze-Drosselung gefunden: Lüfter prüfen oder die Lüfterkurve kühler einstellen.' : '';
  }

  // ---------------------------------------------------------------------------
  // Fan curve
  // ---------------------------------------------------------------------------
  const svgNs = 'http://www.w3.org/2000/svg';
  const svgEl = (tag, attrs) => { const e = document.createElementNS(svgNs, tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); return e; };
  let fanDraft = null;  // {temps: [4], speeds: [4 %]}

  const pct = pwm => Math.round(pwm / 255 * 100);

  function drawFanChart() {
    const box = $('fan-chart'), svg = box.querySelector('svg');
    svg.innerHTML = '';
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !fanDraft) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const pad = { l: 40, r: 12, t: 12, b: 24 };
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const x = t => pad.l + (Math.min(90, Math.max(30, t)) - 30) / 60 * iw;
    const y = p => pad.t + ih - p / 100 * ih;
    [0, 25, 50, 75, 100].forEach(p => {
      svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + iw, y1: y(p), y2: y(p), class: 'grid-line' }));
      const l = svgEl('text', { x: pad.l - 6, y: y(p) + 3, 'text-anchor': 'end', class: 'axis-label' });
      l.textContent = `${p}%`;
      svg.appendChild(l);
    });
    [30, 40, 50, 60, 70, 80, 90].forEach(t => {
      const l = svgEl('text', { x: x(t), y: H - 6, 'text-anchor': 'middle', class: 'axis-label' });
      l.textContent = `${t}°`;
      svg.appendChild(l);
    });
    // Step line: off below step 1, then each step's speed until the next temperature
    const { temps, speeds } = fanDraft;
    let d = `M${x(30)},${y(0)}`;
    temps.forEach((t, i) => { d += `H${x(t)}V${y(speeds[i])}`; });
    d += `H${x(90)}`;
    svg.appendChild(svgEl('path', { d: `${d}V${y(0)}H${x(30)}Z`, fill: 'var(--accent-cyan)', 'fill-opacity': 0.08 }));
    svg.appendChild(svgEl('path', { d, fill: 'none', stroke: 'var(--accent-cyan)', 'stroke-width': 2, 'stroke-linejoin': 'round' }));
    temps.forEach((t, i) => {
      svg.appendChild(svgEl('circle', { cx: x(t), cy: y(speeds[i]), r: 4, fill: 'var(--accent-cyan)', class: 'hover-dot' }));
      const l = svgEl('text', { x: x(t) + 6, y: y(speeds[i]) - 6, class: 'series-label' });
      l.textContent = `${i + 1}`;
      svg.appendChild(l);
    });
    const now = cache.fan && cache.fan.temp;
    if (now != null) {
      svg.appendChild(svgEl('line', { x1: x(now), x2: x(now), y1: pad.t, y2: pad.t + ih, class: 'crosshair' }));
      const l = svgEl('text', { x: x(now), y: pad.t + 10, 'text-anchor': x(now) > W - 90 ? 'end' : 'start', class: 'series-label', dx: x(now) > W - 90 ? -6 : 6 });
      l.textContent = `Jetzt ${now.toFixed(1)} °C`;
      svg.appendChild(l);
    }
  }

  function renderFanSteps() {
    const box = $('fan-steps');
    box.innerHTML = '';
    fanDraft.temps.forEach((t, i) => {
      const row = el('div', 'fan-step');
      row.appendChild(el('span', 'fan-step-name', `Stufe ${i + 1}`));
      const mk = (value, unit, label, onInput, attrs) => {
        const wrap = el('label', 'fan-input');
        const input = el('input');
        input.type = 'number';
        Object.assign(input, attrs);
        input.value = value;
        input.setAttribute('aria-label', `Stufe ${i + 1}: ${label}`);
        input.addEventListener('input', () => { onInput(parseFloat(input.value)); markFanDirty(); drawFanChart(); });
        wrap.append(input, el('span', '', unit));
        return wrap;
      };
      row.append(
        mk(t, '°C', 'Temperatur', v => { fanDraft.temps[i] = v; }, { min: 30, max: 90, step: 0.5 }),
        mk(fanDraft.speeds[i], '%', 'Drehzahl', v => { fanDraft.speeds[i] = v; }, { min: 0, max: 100, step: 1 })
      );
      box.appendChild(row);
    });
  }

  function markFanDirty() {
    const f = cache.fan;
    const same = f && fanDraft.temps.every((t, i) => t === f.curve.temps[i]) && fanDraft.speeds.every((s, i) => s === pct(f.curve.speeds[i]));
    $('fan-save').disabled = same;
    $('fan-msg').className = 'form-msg';
    $('fan-msg').textContent = same ? '' : 'Nicht gespeichert';
    document.querySelectorAll('#fan-presets .mode-card').forEach(b => {
      const p = f.presets.find(x => x.id === b.dataset.id);
      const on = p && p.temps.every((t, i) => t === fanDraft.temps[i]) && p.speeds.every((s, i) => pct(s) === fanDraft.speeds[i]);
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }

  function renderFanCurve(f) {
    const state = $('fan-curve-state');
    if (!f.available) {
      state.className = 'health-tag';
      state.textContent = 'Nicht verfügbar';
      $('fan-hint').textContent = 'Nur auf einem Raspberry Pi 5 mit aktivem Lüfter.';
      ['fan-save', 'fan-reset'].forEach(id => { $(id).disabled = true; });
    } else {
      state.className = `health-tag ${f.reboot_pending ? 'warning' : 'good'}`;
      state.textContent = f.reboot_pending ? 'Neustart nötig' : f.custom ? 'Eigene Kurve aktiv' : 'Werkseinstellung aktiv';
      $('fan-hint').textContent = f.reboot_pending
        ? `Gespeichert, aber noch nicht aktiv (aktuell: ${f.active_thresholds.map(t => `${t} °C`).join(' / ')}). Nach dem nächsten Neustart des Pi gilt die neue Kurve.`
        : `Datei: ${f.config_path}`;
      $('fan-reset').disabled = !f.custom;
    }
    fanDraft = { temps: [...f.curve.temps], speeds: f.curve.speeds.map(pct) };

    const presets = $('fan-presets');
    presets.innerHTML = '';
    f.presets.forEach(p => {
      const b = el('button', 'mode-card');
      b.dataset.id = p.id;
      b.append(el('strong', '', p.label), el('span', '', p.desc));
      b.addEventListener('click', () => {
        fanDraft = { temps: [...p.temps], speeds: p.speeds.map(pct) };
        renderFanSteps();
        markFanDirty();
        drawFanChart();
      });
      presets.appendChild(b);
    });
    renderFanSteps();
    markFanDirty();
    drawFanChart();
  }

  async function saveFanCurve() {
    const msg = $('fan-msg');
    $('fan-save').disabled = true;
    msg.textContent = 'Speichere…';
    try {
      cache.fan = await api('/api/tools/fan-curve', { temps: fanDraft.temps, speeds: fanDraft.speeds });
      renderFanCurve(cache.fan);
      msg.className = 'form-msg success';
      msg.textContent = 'Gespeichert – gilt nach dem nächsten Neustart.';
    } catch (err) {
      msg.className = 'form-msg error';
      msg.textContent = err.message;
      $('fan-save').disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Activity log
  // ---------------------------------------------------------------------------
  const activity = { entries: [], categories: {} };

  async function loadActivity(more = false) {
    const params = new URLSearchParams({ q: $('activity-query').value.trim(), category: $('activity-category').value, limit: '200' });
    if (more && activity.entries.length) params.set('before', activity.entries[activity.entries.length - 1].id);
    try {
      const r = await api(`/api/tools/activity?${params}`);
      activity.entries = more ? [...activity.entries, ...r.entries] : r.entries;
      if (!Object.keys(activity.categories).length) {
        activity.categories = r.categories;
        Object.entries(r.categories).forEach(([id, label]) => {
          const o = el('option', '', label);
          o.value = id;
          $('activity-category').appendChild(o);
        });
      }
      renderActivity(r);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function renderActivity(r) {
    const table = $('activity-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Zeit', 'Benutzer', 'Aktion', 'Details', 'IP-Adresse', 'Ergebnis'].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    activity.entries.forEach(e => {
      const row = body.insertRow();
      row.insertCell().textContent = new Date(e.ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
      const user = row.insertCell();
      user.textContent = e.user || '–';
      user.setAttribute('data-no-i18n', '');
      row.insertCell().textContent = e.action;
      const detail = row.insertCell();
      detail.className = 'wrap mono';
      detail.textContent = e.detail || '–';
      detail.setAttribute('data-no-i18n', '');
      row.insertCell().textContent = e.ip || '–';
      row.insertCell().appendChild(el('span', `health-tag ${e.ok ? 'good' : 'critical'}`, e.ok ? 'OK' : `Fehler ${e.status}`));
    });
    if (!activity.entries.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.textContent = 'Keine Einträge.';
    }
    $('activity-more').hidden = !r.more;
    $('activity-count').textContent = `${activity.entries.length} von ${r.total} Einträgen`;
  }

  // ---------------------------------------------------------------------------
  // Small inline charts
  // ---------------------------------------------------------------------------
  // Bar sparkline: one bar per value (null = no data), scaled to max
  function sparkBars(values, color, max, title) {
    const w = 120, h = 26;
    const svg = document.createElementNS(svgNs, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('class', 'sparkline');
    svg.setAttribute('role', 'img');
    if (title) svg.setAttribute('aria-label', title);
    const top = max || Math.max(...values.filter(v => v != null), 1e-9);
    const bw = w / values.length;
    values.forEach((v, i) => {
      if (v == null || !top) return;
      const bh = Math.max(1, v / top * (h - 2));
      svg.appendChild(svgEl('rect', { x: i * bw + 0.5, y: h - bh, width: Math.max(1, bw - 1), height: bh, fill: color, rx: 1 }));
    });
    return svg;
  }

  function sparkLine(points, color, title) {
    const w = 140, h = 30;
    const svg = document.createElementNS(svgNs, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('class', 'sparkline');
    svg.setAttribute('role', 'img');
    if (title) svg.setAttribute('aria-label', title);
    const vals = points.map(p => p.avg);
    if (vals.length < 2) return svg;
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const t0 = points[0].ts, t1 = points[points.length - 1].ts || t0 + 1;
    const d = points.map((p, i) => `${i ? 'L' : 'M'}${((p.ts - t0) / (t1 - t0 || 1) * (w - 2) + 1).toFixed(1)},${(h - 3 - (p.avg - lo) / span * (h - 6)).toFixed(1)}`).join('');
    svg.appendChild(svgEl('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.6, 'stroke-linejoin': 'round' }));
    return svg;
  }

  // ---------------------------------------------------------------------------
  // Resource hogs
  // ---------------------------------------------------------------------------
  let hogHours = 24;

  function renderHogs(r) {
    const fmtMb = mb => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);
    const table = (id, items, cols, spark) => {
      const t = $(id);
      t.innerHTML = '';
      const head = t.createTHead().insertRow();
      ['Programm', ...cols.map(c => c[0]), 'Verlauf'].forEach(h => head.appendChild(el('th', '', h)));
      const body = t.createTBody();
      items.slice(0, 10).forEach(i => {
        const row = body.insertRow();
        const name = row.insertCell();
        name.className = 'wrap';
        name.textContent = i.label;
        name.setAttribute('data-no-i18n', '');
        if (i.processes > 1) name.append(' ', el('span', 'health-tag', `${i.processes}×`));
        cols.forEach(c => { row.insertCell().textContent = c[1](i); });
        row.insertCell().appendChild(spark(i));
      });
      if (!items.length) {
        const c = body.insertRow().insertCell();
        c.colSpan = cols.length + 2;
        c.textContent = 'Noch keine Messwerte – der erste kommt nach einer Minute.';
      }
    };
    table('hogs-cpu', r.by_cpu, [
      ['Ø CPU', i => `${i.cpu_avg.toFixed(1)}%`],
      ['Spitze', i => `${i.cpu_max.toFixed(0)}%`],
      ['CPU-Zeit', i => (i.cpu_minutes >= 60 ? `${(i.cpu_minutes / 60).toFixed(1)} Std.` : `${i.cpu_minutes.toFixed(1)} Min.`)]
    ], i => sparkBars(i.cpu_series, 'var(--accent-cyan)', null, 'CPU-Verlauf'));  // each row on its own scale: shows *when*
    table('hogs-ram', r.by_ram, [
      ['Ø RAM', i => fmtMb(i.rss_avg)],
      ['Maximum', i => fmtMb(i.rss_max)]
    ], i => sparkBars(i.rss_series, 'var(--accent-violet)', null, 'RAM-Verlauf'));
    $('hogs-hint').textContent = r.minutes
      ? `${r.minutes} Messungen · CPU in % der gesamten CPU (alle Kerne) · CPU-Zeit = Minuten volle Auslastung eines ganzen Pi`
      : '';
  }

  // ---------------------------------------------------------------------------
  // Sensors
  // ---------------------------------------------------------------------------
  const SENSOR_GROUPS = [['temp', 'Temperaturen'], ['volt', 'Spannungen & Ströme'], ['fan', 'Lüfter'], ['power', 'Leistung'], ['status', 'Status']];
  let sensorHistory = null;

  function renderSensors(r) {
    const box = $('sensor-groups');
    box.innerHTML = '';
    if (!r.supported) {
      $('sensor-hint').textContent = 'Keine Sensoren gefunden (nur auf Linux / Raspberry Pi).';
      $('sensors-live').hidden = true;
      return;
    }
    $('sensor-hint').textContent = '';
    SENSOR_GROUPS.forEach(([group, title]) => {
      const items = r.sensors.filter(x => x.group === group);
      if (!items.length) return;
      box.appendChild(el('h3', 'subhead', title));
      const grid = el('div', 'sensor-grid');
      items.forEach(x => {
        const card = el('div', 'sensor-card');
        const head = el('div', 'sensor-head');
        head.appendChild(el('span', 'sensor-label', x.label));
        let valueText, level = '';
        if (group === 'status') {
          valueText = x.value ? 'Ja' : 'Nein';
          level = x.value ? 'critical' : 'good';
        } else {
          valueText = `${String(x.value).replace('.', LOCALE.startsWith('de') ? ',' : '.')} ${x.unit}`;
          if (group === 'temp') level = x.value >= 80 ? 'critical' : x.value >= 70 ? 'warning' : '';
          if (x.rail === 'EXT5V') level = x.value < 4.8 ? 'critical' : x.value < 4.95 ? 'warning' : '';
        }
        const value = el('span', `sensor-value ${level}`, valueText);
        card.append(head, value);
        const extra = [];
        if (x.amp != null) extra.push(`${x.amp.toFixed(3)} A`);
        if (x.watt != null) extra.push(`${x.watt.toFixed(2)} W`);
        if (x.crit) extra.push(`kritisch ab ${x.crit} °C`);
        if (extra.length) card.appendChild(el('span', 'sensor-sub', extra.join(' · ')));
        const hist = sensorHistory && sensorHistory.series[x.id];
        if (hist && hist.length > 1) {
          const lo = Math.min(...hist.map(p => p.min)), hi = Math.max(...hist.map(p => p.max));
          card.appendChild(sparkLine(hist, group === 'temp' ? 'var(--accent-amber)' : 'var(--accent-cyan)', `${x.label}: 24 Std.`));
          card.appendChild(el('span', 'sensor-sub', `24 Std.: ${lo.toFixed(group === 'volt' ? 2 : 1)} – ${hi.toFixed(group === 'volt' ? 2 : 1)} ${x.unit}`));
        }
        grid.appendChild(card);
      });
      box.appendChild(grid);
    });
  }

  async function refreshSensors() {
    if (document.hidden) return;
    try {
      cache.sensors = await api('/api/sensors');
      renderSensors(cache.sensors);
    } catch (e) { /* keep last values */ }
  }

  // ---------------------------------------------------------------------------
  // Data usage
  // ---------------------------------------------------------------------------
  function drawUsageChart(r) {
    const box = $('usage-chart'), svg = box.querySelector('svg'), tip = box.querySelector('.history-tooltip');
    svg.innerHTML = '';
    tip.hidden = true;
    const W = box.clientWidth, H = box.clientHeight;
    if (!W) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const n = 30;
    const byDay = new Map(r.days.map(d => [d.day, d]));
    const days = [];
    for (let i = n - 1; i >= 0; i--) {
      const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - i);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      days.push({ key, date: d, ...(byDay.get(key) || { rx: null, tx: null }) });
    }
    const max = Math.max(...days.map(d => Math.max(d.rx || 0, d.tx || 0)), 1);
    // Round axis in the unit shown
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let u = 0; while (max / Math.pow(1024, u + 1) >= 1 && u < 4) u++;
    const div = Math.pow(1024, u);
    const raw = max / div / 4, exp = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / exp;
    const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
    const top = Math.ceil(max / div / step) * step;
    const pad = { l: 58, r: 8, t: 10, b: 24 }, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const y = v => pad.t + ih - v / div / top * ih;
    for (let t = 0; t <= top + step / 2; t += step) {
      svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + iw, y1: y(t * div), y2: y(t * div), class: 'grid-line' }));
      const l = svgEl('text', { x: pad.l - 8, y: y(t * div) + 3, 'text-anchor': 'end', class: 'axis-label' });
      l.textContent = t ? `${+t.toFixed(2)} ${units[u]}` : '0';
      svg.appendChild(l);
    }
    const slot = iw / n, bw = Math.max(1, Math.min(9, (slot - 3) / 2));
    const bar = (x, v, color, i) => {
      if (!v) return;
      const h = Math.max(1, y(0) - y(v)), rad = Math.min(3, bw / 2, h);
      const y0 = y(0) - h;
      svg.appendChild(svgEl('path', { d: `M${x},${y0 + h}V${y0 + rad}Q${x},${y0} ${x + rad},${y0}H${x + bw - rad}Q${x + bw},${y0} ${x + bw},${y0 + rad}V${y0 + h}Z`, fill: color, class: 'bar', 'data-i': i }));
    };
    days.forEach((d, i) => {
      const cx = pad.l + (i + 0.5) * slot;
      bar(cx - bw - 1, d.rx, 'var(--chart-down)', i);   // 2px gap between the pair
      bar(cx + 1, d.tx, 'var(--chart-up)', i);
      if ((n - 1 - i) % 5 === 0) {
        const l = svgEl('text', { x: cx, y: H - 6, 'text-anchor': 'middle', class: 'axis-label' });
        l.textContent = d.date.toLocaleDateString(LOCALE, { day: 'numeric', month: 'numeric' });
        svg.appendChild(l);
      }
    });
    svg.onpointermove = evt => {
      const i = Math.floor((evt.clientX - svg.getBoundingClientRect().left - pad.l) / slot);
      const d = days[i];
      if (!d) { svg.onpointerleave(); return; }
      svg.querySelectorAll('.bar').forEach(b => b.classList.toggle('dim', +b.dataset.i !== i));
      tip.innerHTML = '';
      tip.appendChild(el('div', 'tt-time', d.date.toLocaleDateString(LOCALE, { weekday: 'short', day: 'numeric', month: 'numeric' })));
      [['Empfangen', d.rx, 'var(--chart-down)'], ['Gesendet', d.tx, 'var(--chart-up)']].forEach(([label, v, c]) => {
        const row = el('div', 'tt-row');
        const key = el('span', 'line-key');
        key.style.setProperty('--key-color', c);
        row.append(key, el('b', '', v == null ? '–' : fmtBytes(v)), document.createTextNode(label));
        tip.appendChild(row);
      });
      tip.hidden = false;
      const cx = pad.l + (i + 0.5) * slot;
      tip.style.left = `${cx + 14 + tip.offsetWidth > W ? cx - 14 - tip.offsetWidth : cx + 14}px`;
    };
    svg.onpointerleave = () => { svg.querySelectorAll('.bar.dim').forEach(b => b.classList.remove('dim')); tip.hidden = true; };
  }

  function renderUsage(r) {
    const select = $('usage-iface');
    if (select.options.length === 1) {
      r.interfaces.forEach(i => { const o = el('option', '', i); o.value = i; select.appendChild(o); });
      select.value = r.iface;
    }
    benchValues($('usage-stats'), [
      ['Heute empfangen', fmtBytes(r.today.rx)], ['Heute gesendet', fmtBytes(r.today.tx)],
      ['Monat empfangen', fmtBytes(r.month.rx)], ['Monat gesendet', fmtBytes(r.month.tx)]
    ]);
    drawUsageChart(r);
    const t = $('usage-months');
    t.innerHTML = '';
    const head = t.createTHead().insertRow();
    ['Monat', 'Empfangen', 'Gesendet', 'Gesamt'].forEach(h => head.appendChild(el('th', '', h)));
    const body = t.createTBody();
    [...r.months].reverse().forEach(m => {
      const row = body.insertRow();
      const [yy, mm] = m.month.split('-');
      [new Date(+yy, +mm - 1, 1).toLocaleDateString(LOCALE, { month: 'long', year: 'numeric' }), fmtBytes(m.rx), fmtBytes(m.tx), fmtBytes(m.rx + m.tx)]
        .forEach(v => { row.insertCell().textContent = v; });
    });
    if (!r.months.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 4;
      c.textContent = 'Noch keine Daten – die Zählung beginnt jetzt.';
    }
    $('usage-hint').textContent = r.since ? `Gezählt seit ${new Date(`${r.since}T12:00:00`).toLocaleDateString(LOCALE)}.` : '';
  }

  // ---------------------------------------------------------------------------
  // DNS & traceroute
  // ---------------------------------------------------------------------------
  async function runDns(e) {
    e.preventDefault();
    const custom = $('dns-server').value === 'custom';
    const status = $('dns-status');
    status.textContent = 'Frage an…';
    try {
      const r = await api('/api/tools/dns', {
        name: $('dns-name').value, type: $('dns-type').value, server: custom ? $('dns-custom').value : $('dns-server').value
      });
      status.textContent = `${r.rcode} · ${r.ms} ms · Server ${r.server}${r.authoritative ? ' · autoritativ' : ''}`;
      status.className = `field-hint ${r.ok ? '' : 'warn-text'}`;
      const t = $('dns-results');
      t.innerHTML = '';
      const head = t.createTHead().insertRow();
      ['Typ', 'Wert', 'TTL', 'Name'].forEach(h => head.appendChild(el('th', '', h)));
      const body = t.createTBody();
      r.answers.forEach(a => {
        const row = body.insertRow();
        row.insertCell().textContent = a.type;
        const v = row.insertCell();
        v.className = 'wrap mono';
        v.textContent = a.value;
        v.setAttribute('data-no-i18n', '');
        row.insertCell().textContent = `${a.ttl} s`;
        const n = row.insertCell();
        n.textContent = a.name;
        n.setAttribute('data-no-i18n', '');
      });
      if (!r.answers.length) {
        const c = body.insertRow().insertCell();
        c.colSpan = 4;
        c.textContent = 'Keine Einträge dieses Typs.';
      }
      $('dns-wrap').hidden = false;
    } catch (err) {
      status.textContent = err.message;
      status.className = 'field-hint warn-text';
    }
  }

  async function runTrace(e) {
    e.preventDefault();
    const btn = e.submitter || $('trace-form').querySelector('button');
    const status = $('trace-status');
    btn.disabled = true;
    status.textContent = 'Läuft – das kann bis zu 30 Sekunden dauern…';
    try {
      const r = await api('/api/tools/traceroute', { host: $('trace-host').value });
      status.textContent = `${r.host}${r.target !== r.host ? ` (${r.target})` : ''} · ${r.hops.length} Stationen · ${r.reached ? 'Ziel erreicht' : 'Ziel nicht erreicht'} · ${r.seconds} s · ${r.tool}`;
      const t = $('trace-results');
      t.innerHTML = '';
      const head = t.createTHead().insertRow();
      ['#', 'Adresse', 'Name', 'Zeit'].forEach(h => head.appendChild(el('th', '', h)));
      const body = t.createTBody();
      r.hops.forEach(h => {
        const row = body.insertRow();
        row.insertCell().textContent = h.hop;
        row.insertCell().textContent = h.ip || '*';
        const n = row.insertCell();
        n.className = 'wrap';
        n.textContent = h.name || (h.ip ? '–' : 'keine Antwort');
        row.insertCell().textContent = h.ms != null ? `${h.ms} ms` : '–';
      });
      $('trace-wrap').hidden = false;
    } catch (err) {
      status.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------
  const LOADERS = {
    network: ['/api/tools/network', renderNetwork],
    hardware: ['/api/tools/hardware', renderHardware],
    cpu: ['/api/tools/cpu-mode', renderCpuMode],
    bench: ['/api/tools/benchmark', renderBenchmarks],
    logUnits: ['/api/tools/log-units', renderLogUnits],
    cleanup: ['/api/tools/cleanup', renderCleanup],
    firewall: ['/api/tools/firewall', renderFirewall],
    ssh: ['/api/tools/ssh', renderSsh],
    uptime: ['/api/monitor/uptime?days=7', renderUptime],
    throttle: ['/api/monitor/throttle?days=30', renderThrottle],
    lan: ['/api/tools/lan', renderLan],
    ports: ['/api/tools/ports', renderPorts],
    fan: ['/api/tools/fan-curve', renderFanCurve],
    hogs: ['/api/monitor/hogs?hours=24', renderHogs],
    usage: ['/api/monitor/usage', renderUsage]
  };

  async function load(key) {
    const [url, render] = LOADERS[key];
    try {
      cache[key] = await api(url);
      render(cache[key]);
    } catch (err) {
      toast(`${key}: ${err.message}`, 'error');
    }
  }

  async function init() {
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = '/login'; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }

    document.querySelectorAll('[data-reload]').forEach(btn =>
      btn.addEventListener('click', () => load(btn.dataset.reload)));
    $('net-show-virtual').addEventListener('change', () => cache.network && renderNetwork(cache.network));
    $('bench-disk').addEventListener('click', () => runBenchmark('disk'));
    $('bench-cpu').addEventListener('click', () => runBenchmark('cpu'));
    $('bench-drive').addEventListener('change', () => cache.bench && renderBenchmarks(cache.bench));
    $('log-form').addEventListener('submit', searchLogs);
    $('fw-form').addEventListener('submit', addFirewallRule);
    $('lan-scan').addEventListener('click', runLanScan);
    ['input', 'change'].forEach(ev => {
      $('lan-filter').addEventListener(ev, () => cache.lan && renderLan(cache.lan));
      $('lan-show').addEventListener(ev, () => cache.lan && renderLan(cache.lan));
    });
    $('port-test-form').addEventListener('submit', runPortTest);
    $('fan-save').addEventListener('click', saveFanCurve);
    confirmClick($('fan-reset'), 'Werkseinstellung', 'Wirklich zurücksetzen?', async () => {
      try {
        cache.fan = await api('/api/tools/fan-curve/reset', {});
        renderFanCurve(cache.fan);
        toast('Lüfterkurve zurückgesetzt – gilt nach dem nächsten Neustart.', 'success');
      } catch (err) { toast(err.message, 'error'); }
    });
    new ResizeObserver(() => drawFanChart()).observe($('fan-chart'));
    let activityTimer = null;
    $('activity-query').addEventListener('input', () => { clearTimeout(activityTimer); activityTimer = setTimeout(() => loadActivity(), 300); });
    $('activity-category').addEventListener('change', () => loadActivity());
    $('activity-form').addEventListener('submit', e => { e.preventDefault(); loadActivity(); });
    $('activity-more').addEventListener('click', () => loadActivity(true));
    loadActivity();
    // Sensors: history once, live values every 5 s while the page is open
    api('/api/sensors/history').then(h => { sensorHistory = h; return refreshSensors(); }).catch(() => refreshSensors());
    setInterval(refreshSensors, 5000);
    document.querySelectorAll('#hogs-range button').forEach(b => b.addEventListener('click', () => {
      hogHours = +b.dataset.hours;
      document.querySelectorAll('#hogs-range button').forEach(x => x.classList.toggle('active', x === b));
      LOADERS.hogs[0] = `/api/monitor/hogs?hours=${hogHours}`;
      load('hogs');
    }));
    $('usage-iface').addEventListener('change', () => {
      LOADERS.usage[0] = `/api/monitor/usage?iface=${encodeURIComponent($('usage-iface').value)}`;
      load('usage');
    });
    new ResizeObserver(() => cache.usage && drawUsageChart(cache.usage)).observe($('usage-chart'));
    $('dns-server').addEventListener('change', () => { $('dns-custom').hidden = $('dns-server').value !== 'custom'; });
    $('dns-form').addEventListener('submit', runDns);
    $('trace-form').addEventListener('submit', runTrace);
    document.querySelectorAll('#avail-range button').forEach(b => b.addEventListener('click', () => {
      uptimeDays = +b.dataset.days;
      document.querySelectorAll('#avail-range button').forEach(x => x.classList.toggle('active', x === b));
      LOADERS.uptime[0] = `/api/monitor/uptime?days=${uptimeDays}`;
      load('uptime');
    }));
    $('ssh-filter').addEventListener('change', () => cache.ssh && renderSshEvents(cache.ssh));
    ['log-unit', 'log-priority', 'log-since'].forEach(id => $(id).addEventListener('change', () => searchLogs()));
    // "Alles aufräumen" needs a second click
    $('cleanup-all').addEventListener('click', () => {
      const btn = $('cleanup-all');
      if (!btn.classList.contains('confirming')) {
        btn.classList.add('confirming');
        btn.textContent = 'Wirklich alles?';
        setTimeout(() => { btn.classList.remove('confirming'); btn.textContent = 'Alles aufräumen'; }, 5000);
        return;
      }
      btn.classList.remove('confirming');
      btn.textContent = 'Alles aufräumen';
      runCleanup((cache.cleanup.items || []).filter(i => i.size !== 0).map(i => i.id));
    });

    // Highlight the section currently in view
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));

    Object.keys(LOADERS).forEach(load);
    searchLogs();
    // Live clock speed while the page is open
    setInterval(async () => {
      if (document.hidden || !cache.cpu || !cache.cpu.available) return;
      try {
        const c = await api('/api/tools/cpu-mode');
        $('cpu-live').textContent = `${c.cur_mhz} MHz`;
      } catch (e) { /* ignore */ }
    }, 3000);
  }

  init();
})();
