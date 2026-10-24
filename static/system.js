/**
 * HostCore Dashboard - System tools page
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
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`;
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
      btn.dataset.perm = 'system.config';
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
    b.dataset.perm = 'network.tools';
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
      if (d.self) name.append(' ', el('span', 'health-tag', 'Dieser Server'));
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
        if (d.monitor) {
          const m = d.monitor;
          const label = m.ok == null ? 'Überwacht' : `${m.ok ? 'Erreichbar' : 'Nicht erreichbar'}${m.uptime != null ? ` · ${String(m.uptime).replace('.', LOCALE.startsWith('de') ? ',' : '.')} %` : ''}`;
          const tag = el('span', `health-tag ${m.ok == null ? '' : m.ok ? 'good' : 'critical'}`, label);
          tag.title = 'Wird alle 5 Minuten angepingt – Verlauf in der Web-Dienste-Karte des Dashboards.';
          actions.append(' ', tag);
        } else {
          actions.append(' ', lanAction('Überwachen', 'btn-secondary', async () => {
            try {
              const r = await api(`/api/tools/lan/${encodeURIComponent(d.mac)}/monitor`, {});
              toast(r.message, 'success');
              cache.lan = await api('/api/tools/lan');
              renderLan(cache.lan);
            } catch (err) { toast(err.message, 'error'); }
          }));
        }
        const forget = el('button', 'btn-danger', 'Vergessen');
        forget.dataset.perm = 'network.tools';
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

  // Countries: flag emoji + name in the page language (Intl), no lookup table needed
  let regionNames = null;
  try { regionNames = new Intl.DisplayNames([LOCALE], { type: 'region' }); } catch (e) { /* old browser */ }
  const flag = cc => cc && /^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map(c => 0x1F1A5 + c.charCodeAt(0))) : '';
  const countryName = cc => { try { return (regionNames && regionNames.of(cc)) || cc; } catch (e) { return cc; } };

  function countryTag(cc, extraClass = '') {
    const tag = el('span', `health-tag country-tag ${extraClass}`, `${flag(cc)} ${countryName(cc)}`);
    tag.title = cc;
    tag.setAttribute('data-no-i18n', '');
    return tag;
  }

  // Scope tag; for internet addresses with a known country: flag + country instead
  function originTag(scope, cc) {
    if (scope === 'internet' && cc) return countryTag(cc, 'warning');
    return scopeTag(scope);
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

  async function firewallAction(action, params = {}, msgId = 'fw-msg') {
    const msg = $(msgId);
    document.querySelectorAll('#firewall button').forEach(b => { b.disabled = true; });
    msg.className = 'form-msg';
    msg.textContent = 'Wird ausgeführt…';
    try {
      cache.firewall = await api('/api/tools/firewall', { action, params });
      msg.textContent = '';
      document.querySelectorAll('#firewall button').forEach(b => { b.disabled = false; });
      toast('Firewall aktualisiert.', 'success');
      renderFirewall(cache.firewall);
      if (action === 'rule' || action === 'logging') load('fwTraffic');
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
      if (r.iface) {
        const tag = el('span', 'health-tag', `über ${r.iface}`);
        to.append(' ', tag);
      }
      if (r.log) to.append(' ', el('span', 'health-tag', 'protokolliert'));
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
      del.dataset.perm = 'firewall.manage';
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

    const iface = $('fw-iface');
    const keep = iface.value;
    iface.innerHTML = '';
    iface.appendChild(Object.assign(el('option', '', 'Alle'), { value: 'any' }));
    (f.interfaces || []).forEach(i => {
      const o = el('option', '', i);
      o.value = i;
      o.setAttribute('data-no-i18n', '');
      iface.appendChild(o);
    });
    iface.value = [...iface.options].some(o => o.value === keep) ? keep : 'any';
    updateRulePreview();
  }

  function ruleParams() {
    return {
      action: $('fw-action').value,
      direction: $('fw-direction').value,
      iface: $('fw-iface').value,
      proto: $('fw-proto').value,
      from: $('fw-from').value.trim(),
      sport: $('fw-sport').value.trim(),
      to: $('fw-to').value.trim(),
      dport: $('fw-port').value.trim(),
      position: $('fw-position').value,
      log: $('fw-log').checked,
      comment: $('fw-comment').value.trim()
    };
  }

  // The ufw command the form turns into - shows exactly what will happen
  function updateRulePreview() {
    const p = ruleParams();
    const any = v => !v || /^(any|alle|\*)$/i.test(v);
    const parts = ['ufw'];
    if (p.position === 'top') parts.push('insert 1');
    parts.push(p.action, p.direction);
    if (p.iface !== 'any') parts.push('on', p.iface);
    if (p.log) parts.push('log');
    if (p.proto !== 'any') parts.push('proto', p.proto);
    parts.push('from', any(p.from) ? 'any' : p.from);
    if (!any(p.sport)) parts.push('port', p.sport.replace(/\s/g, ''));
    parts.push('to', any(p.to) ? 'any' : p.to);
    if (!any(p.dport)) parts.push('port', p.dport.replace(/\s/g, ''));
    if (p.comment) parts.push('comment', `'${p.comment}'`);
    $('fw-preview').textContent = parts.join(' ');
    $('fw-port').required = false;
  }

  async function addFirewallRule(e) {
    e.preventDefault();
    const ok = await firewallAction('rule', ruleParams(), 'fw-form-msg');
    if (ok) {
      ['fw-from', 'fw-sport', 'fw-to', 'fw-port', 'fw-comment'].forEach(id => { $(id).value = ''; });
      $('fw-log').checked = false;
      updateRulePreview();
    }
  }

  // Fill the form from the traffic analyser and jump to it
  function prepareRule(values) {
    const defaults = { action: 'allow', direction: 'in', iface: 'any', proto: 'tcp', from: '', sport: '', to: '', port: '', position: 'end', log: false, comment: '' };
    const v = { ...defaults, ...values };
    ['action', 'direction', 'iface', 'proto', 'from', 'sport', 'to', 'port', 'position', 'comment'].forEach(k => { $(`fw-${k}`).value = v[k]; });
    if (!$('fw-iface').value) $('fw-iface').value = 'any';
    $('fw-log').checked = v.log;
    $('fw-form-msg').textContent = '';
    updateRulePreview();
    $('fw-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
    $('fw-comment').focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------------------
  // Firewall traffic (what ufw logged)
  // ---------------------------------------------------------------------------
  const FWT_ACTION = { block: ['critical', 'Blockiert'], limit: ['warning', 'Begrenzt'], allow: ['good', 'Erlaubt'] };
  const FWT_DIR = { in: 'Eingehend', out: 'Ausgehend', fwd: 'Weitergeleitet' };
  let fwtShown = 25;

  function fwtUrl() {
    const range = document.querySelector('#fwt-range button.active').dataset.range;
    const q = new URLSearchParams({ range, action: $('fwt-action').value, direction: $('fwt-direction').value, q: $('fwt-query').value.trim() });
    return `/api/tools/firewall/traffic?${q}`;
  }

  function fmtEndpoint(ip, port) {
    if (port == null) return ip;
    return ip.includes(':') ? `[${ip}]:${port}` : `${ip}:${port}`;
  }

  function fwtBucketLabel(ts, bucket) {
    const d = new Date(ts * 1000);
    if (bucket >= 21600) return d.toLocaleString(LOCALE, { weekday: 'short', day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' });
    return d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
  }

  function drawTrafficChart(r) {
    const box = $('fwt-chart'), svg = box.querySelector('svg'), tip = box.querySelector('.history-tooltip');
    svg.innerHTML = '';
    tip.hidden = true;
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !r) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const data = r.timeline, n = data.length;
    const max = Math.max(...data.map(d => d.internet + d.local), 1);
    const raw = max / 4, exp = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / exp;
    const step = Math.max(1, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp);
    const top = Math.ceil(max / step) * step;
    const pad = { l: 44, r: 8, t: 10, b: 24 }, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const y = v => pad.t + ih - v / top * ih;
    for (let t = 0; t <= top + step / 2; t += step) {
      svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + iw, y1: y(t), y2: y(t), class: 'grid-line' }));
      const l = svgEl('text', { x: pad.l - 8, y: y(t) + 3, 'text-anchor': 'end', class: 'axis-label' });
      l.textContent = fmtNum(t);
      svg.appendChild(l);
    }
    const slot = iw / n, bw = Math.max(2, Math.min(18, slot - 3));
    const labelEvery = Math.ceil(n / Math.max(2, Math.floor(iw / 70)));
    data.forEach((d, i) => {
      const x = pad.l + i * slot + (slot - bw) / 2;
      // Stacked: Internet at the bottom, local on top, 2px surface gap between
      const h1 = d.internet ? Math.max(1, y(0) - y(d.internet)) : 0;
      const h2 = d.local ? Math.max(1, y(0) - y(d.local)) : 0;
      const seg = (y0, h, color, roundTop) => {
        if (!h) return;
        const rad = roundTop ? Math.min(3, bw / 2, h) : 0;
        svg.appendChild(svgEl('path', { d: `M${x},${y0 + h}V${y0 + rad}Q${x},${y0} ${x + rad},${y0}H${x + bw - rad}Q${x + bw},${y0} ${x + bw},${y0 + rad}V${y0 + h}Z`, fill: color, class: 'bar', 'data-i': i }));
      };
      seg(y(0) - h1, h1, 'var(--chart-down)', !h2);
      seg(y(0) - h1 - h2 - (h1 && h2 ? 2 : 0), h2, 'var(--chart-up)', true);
      if ((n - 1 - i) % labelEvery === 0) {
        const l = svgEl('text', { x: x + bw / 2, y: H - 6, 'text-anchor': 'middle', class: 'axis-label' });
        l.textContent = r.bucket >= 21600
          ? new Date(d.ts * 1000).toLocaleDateString(LOCALE, { weekday: 'short' })
          : new Date(d.ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
        svg.appendChild(l);
      }
    });
    svg.onpointermove = evt => {
      const i = Math.floor((evt.clientX - svg.getBoundingClientRect().left - pad.l) / slot);
      const d = data[i];
      if (!d) { svg.onpointerleave(); return; }
      svg.querySelectorAll('.bar').forEach(b => b.classList.toggle('dim', +b.dataset.i !== i));
      tip.innerHTML = '';
      tip.appendChild(el('div', 'tt-time', `${fwtBucketLabel(d.ts, r.bucket)} – ${fwtBucketLabel(d.ts + r.bucket, r.bucket)}`));
      [['Internet', d.internet, 'var(--chart-down)'], ['LAN, VPN & lokal', d.local, 'var(--chart-up)']].forEach(([label, v, c]) => {
        const row = el('div', 'tt-row');
        const key = el('span', 'line-key');
        key.style.setProperty('--key-color', c);
        row.append(key, el('b', '', fmtNum(v)), document.createTextNode(label));
        tip.appendChild(row);
      });
      tip.hidden = false;
      const cx = pad.l + (i + 0.5) * slot;
      tip.style.left = `${cx + 14 + tip.offsetWidth > W ? cx - 14 - tip.offsetWidth : cx + 14}px`;
    };
    svg.onpointerleave = () => { svg.querySelectorAll('.bar.dim').forEach(b => b.classList.remove('dim')); tip.hidden = true; };
  }

  function setTrafficQuery(value) {
    $('fwt-query').value = value;
    load('fwTraffic');
  }

  function renderTrafficRecent(r) {
    const t = $('fwt-recent');
    t.innerHTML = '';
    const head = t.createTHead().insertRow();
    ['Zeit', 'Aktion', 'Richtung', 'Von', 'Nach', 'Protokoll'].forEach(h => head.appendChild(el('th', '', h)));
    const body = t.createTBody();
    r.recent.slice(0, fwtShown).forEach(e => {
      const row = body.insertRow();
      row.insertCell().textContent = new Date(e.ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const [level, text] = FWT_ACTION[e.action];
      row.insertCell().appendChild(el('span', `health-tag ${level}`, text));
      const dir = row.insertCell();
      dir.textContent = FWT_DIR[e.direction];
      if (e.iface) dir.append(' ', el('span', 'health-tag', e.iface));
      [[e.src, e.sport], [e.dst, e.dport]].forEach(([ip, port]) => {
        const c = row.insertCell();
        c.className = 'mono';
        c.textContent = fmtEndpoint(ip, port);
        if (e.country && ip === e.remote) { c.prepend(`${flag(e.country)} `); c.title = countryName(e.country); }
      });
      const proto = row.insertCell();
      proto.textContent = e.proto.toUpperCase();
      if (e.syn) proto.append(' ', el('span', 'health-tag', 'Verbindungsversuch'));
    });
    if (!r.recent.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.className = 'wrap';
      c.textContent = 'Keine Einträge im gewählten Zeitraum.';
    }
    const more = $('fwt-more');
    more.hidden = r.recent.length <= fwtShown;
  }

  function renderTraffic(r) {
    const fw = r.firewall || {};
    const logOff = !fw.logging || /^off/.test(fw.logging);
    const notice = $('fwt-notice');
    let text = '';
    if (!fw.installed) text = 'ufw ist nicht installiert – ohne Firewall gibt es nichts auszuwerten.';
    else if (!fw.active) text = 'Die Firewall ist aus, sie blockiert gerade nichts.';
    else if (logOff) text = 'Die Protokollierung der Firewall ist aus – blockierte Verbindungen werden nicht aufgezeichnet.';
    notice.hidden = !text;
    $('fwt-notice-text').textContent = text;
    $('fwt-enable-log').hidden = !(fw.installed && fw.active && logOff);

    const s = r.summary;
    const top = s.top_port;
    benchValues($('fwt-stats'), [
      [$('fwt-action').value === 'allow' ? 'Erlaubte Pakete' : 'Blockierte Pakete', fmtNum(s.total)],
      ['Pro Stunde', String(s.per_hour).replace('.', LOCALE.startsWith('de') ? ',' : '.')],
      ['Verschiedene Absender', fmtNum(s.sources)],
      ['Aus dem Internet', s.total ? `${Math.round(s.internet / s.total * 100)} %` : '–'],
      ['Häufigster Port', top ? (top.port ? `${top.port}/${top.proto}` : top.proto.toUpperCase()) : '–']
    ]);
    $('fwt-chart-title').textContent = r.bucket === 300 ? 'Pakete pro 5 Minuten' : r.bucket === 3600 ? 'Pakete pro Stunde' : 'Pakete pro 6 Stunden';
    drawTrafficChart(r);

    const canManage = !window.PiAuth || window.PiAuth.can('firewall.manage');
    // Top sources
    const st = $('fwt-sources');
    st.innerHTML = '';
    let head = st.createTHead().insertRow();
    ['Adresse', 'Herkunft', 'Pakete', 'Ports', 'Zuletzt', ''].forEach(h => head.appendChild(el('th', '', h)));
    let body = st.createTBody();
    r.top_sources.forEach(x => {
      const row = body.insertRow();
      const ip = row.insertCell();
      const link = el('button', 'text-btn mono', x.ip);
      link.type = 'button';
      link.title = 'Nur diese Adresse anzeigen';
      link.setAttribute('data-no-i18n', '');
      link.addEventListener('click', () => setTrafficQuery(x.ip));
      ip.appendChild(link);
      if (x.name) {
        const n = el('span', 'lan-sub', x.name);
        n.setAttribute('data-no-i18n', '');
        ip.appendChild(n);
      }
      row.insertCell().appendChild(originTag(x.scope, x.country));
      row.insertCell().textContent = fmtNum(x.count);
      const ports = row.insertCell();
      ports.className = 'wrap mono';
      ports.textContent = x.ports.join(', ');
      row.insertCell().textContent = fmtTime(x.last);
      const cell = row.insertCell();
      cell.className = 'row-action';
      if (canManage && x.scope !== 'local' && x.directions.includes('in')) {
        const btn = el('button', 'btn-danger', 'IP sperren');
        btn.title = 'Alles von dieser Adresse blockieren (Regel ganz oben)';
        confirmClick(btn, 'IP sperren', 'Sicher?', async () => {
          if (await firewallAction('rule', { action: 'deny', direction: 'in', from: x.ip, position: 'top', proto: 'any', comment: 'Blocked via HostCore' })) toast(`${x.ip} gesperrt.`, 'success');
        });
        cell.appendChild(btn);
      }
    });
    if (!r.top_sources.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 6;
      c.textContent = 'Keine Einträge.';
    }

    // Top destination ports
    const pt = $('fwt-ports');
    pt.innerHTML = '';
    head = pt.createTHead().insertRow();
    ['Port', 'Dienst', 'Pakete', 'Absender', ''].forEach(h => head.appendChild(el('th', '', h)));
    body = pt.createTBody();
    r.top_ports.forEach(x => {
      const row = body.insertRow();
      const port = row.insertCell();
      port.className = 'mono';
      port.textContent = x.port ? `${x.port}/${x.proto}` : x.proto.toUpperCase();
      const svc = row.insertCell();
      if (x.service) {
        const n = el('span', '', x.service);
        n.setAttribute('data-no-i18n', '');
        svc.appendChild(n);
      } else svc.textContent = '–';
      if (x.listening) {
        const tag = el('span', 'health-tag warning', x.program ? `${x.program} lauscht` : 'läuft hier');
        tag.title = 'Auf diesem Port lauscht ein Programm auf dem Server – vielleicht soll er erreichbar sein?';
        svc.append(' ', tag);
      }
      row.insertCell().textContent = fmtNum(x.count);
      row.insertCell().textContent = fmtNum(x.sources);
      const cell = row.insertCell();
      cell.className = 'row-action';
      if (canManage && x.port && (x.proto === 'tcp' || x.proto === 'udp') && x.directions.includes('in')) {
        const btn = el('button', 'btn-secondary', 'Freigeben…');
        btn.title = 'Regel im Formular vorbereiten';
        btn.addEventListener('click', () => prepareRule({ action: 'allow', direction: 'in', proto: x.proto, port: String(x.port), comment: (x.program || x.service || '').replace(/[^A-Za-z0-9 ._()-]/g, '').slice(0, 40) }));
        cell.appendChild(btn);
      }
    });
    if (!r.top_ports.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 5;
      c.textContent = 'Keine Einträge.';
    }

    const cbox = $('fwt-countries');
    cbox.innerHTML = '';
    $('fwt-countries-wrap').hidden = !(r.top_countries || []).length;
    const blocked = new Set((cache.geoblock && cache.geoblock.countries) || []);
    (r.top_countries || []).forEach(c => {
      const chip = el('div', 'fwt-country');
      const name = el('span', 'fwt-country-name', `${flag(c.cc)} ${countryName(c.cc)}`);
      name.setAttribute('data-no-i18n', '');
      chip.append(name, el('span', 'fwt-country-count', `${fmtNum(c.count)} · ${fmtNum(c.sources)} IP`));
      if (blocked.has(c.cc)) chip.appendChild(el('span', 'health-tag critical', 'gesperrt'));
      else if (canManage && cache.geoblock && cache.geoblock.db.installed) {
        const b = el('button', 'text-btn', 'Land sperren…');
        b.addEventListener('click', () => { geoAdd(c.cc); $('firewall').querySelector('#geo-box').scrollIntoView({ behavior: 'smooth', block: 'center' }); });
        chip.appendChild(b);
      }
      cbox.appendChild(chip);
    });
    if (!r.geoip && r.summary.internet) {
      $('fwt-countries-wrap').hidden = false;
      cbox.appendChild(el('p', 'field-hint', 'Herkunftsländer anzeigen: unter Firewall → Länder sperren die Länder-Datenbank laden.'));
    }

    if (cache.geoblock) renderGeoSuggest();
    fwtShown = 25;
    renderTrafficRecent(r);
    const hints = [];
    if (r.error) hints.push(r.error);
    if (r.hint) hints.push(r.hint);
    if (r.truncated) hints.push('Sehr viele Einträge – nur die neuesten 50.000 wurden ausgewertet.');
    if (!r.error && !r.hint && fw.active && !logOff) hints.push('Die Firewall protokolliert gleiche Pakete nur begrenzt oft – echte Zahlen können höher sein. Erlaubte Verbindungen erscheinen nur bei Regeln mit „Treffer protokollieren“.');
    $('fwt-hint').textContent = hints.join(' ');
  }

  // ---------------------------------------------------------------------------
  // Country blocking (ipset) + GeoIP database
  // ---------------------------------------------------------------------------
  const geo = { selected: new Set(), polling: null };

  function geoAdd(cc) {
    geo.selected.add(cc);
    renderGeoSelected();
    $('geo-msg').className = 'form-msg warning';
    $('geo-msg').textContent = 'Noch nicht übernommen.';
  }

  function renderGeoSelected() {
    const box = $('geo-selected');
    box.innerHTML = '';
    if (!geo.selected.size) box.appendChild(el('span', 'field-hint', 'Kein Land ausgewählt.'));
    [...geo.selected].sort((a, b) => countryName(a).localeCompare(countryName(b), LOCALE)).forEach(cc => {
      const chip = el('span', 'geo-chip');
      const label = el('span', '', `${flag(cc)} ${countryName(cc)}`);
      label.setAttribute('data-no-i18n', '');
      const x = el('button', 'geo-chip-x', '×');
      x.type = 'button';
      x.title = `${countryName(cc)} entfernen`;
      x.setAttribute('data-no-i18n', '');
      x.addEventListener('click', () => {
        geo.selected.delete(cc);
        renderGeoSelected();
        $('geo-msg').className = 'form-msg warning';
        $('geo-msg').textContent = 'Noch nicht übernommen.';
      });
      chip.append(label, x);
      box.appendChild(chip);
    });
    const avail = new Map(((cache.geoblock && cache.geoblock.available) || []).map(c => [c.cc, c.ranges]));
    const ranges = [...geo.selected].reduce((n, cc) => n + (avail.get(cc) || 0), 0);
    if (geo.selected.size) box.appendChild(el('span', 'field-hint geo-count', `${geo.selected.size} ${geo.selected.size === 1 ? 'Land' : 'Länder'} · ca. ${fmtNum(ranges)} Adressbereiche`));
  }

  function renderGeoResults() {
    const q = $('geo-search').value.trim().toLowerCase();
    const box = $('geo-results');
    box.innerHTML = '';
    if (!q) { box.hidden = true; return; }
    const hits = ((cache.geoblock && cache.geoblock.available) || [])
      .map(c => ({ ...c, name: countryName(c.cc) }))
      .filter(c => c.cc.toLowerCase() === q || c.name.toLowerCase().includes(q))
      .map(c => ({ ...c, rank: c.cc.toLowerCase() === q ? 0 : c.name.toLowerCase().startsWith(q) ? 1 : c.name.toLowerCase().split(/[\s-]+/).some(w => w.startsWith(q)) ? 2 : 3 }))
      .sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name, LOCALE))
      .slice(0, 8);
    hits.forEach(c => {
      const b = el('button', 'geo-result');
      b.type = 'button';
      b.setAttribute('data-no-i18n', '');
      b.append(el('span', '', `${flag(c.cc)} ${c.name}`), el('span', 'geo-result-cc', geo.selected.has(c.cc) ? '✓' : c.cc));
      b.addEventListener('click', () => { geoAdd(c.cc); $('geo-search').value = ''; renderGeoResults(); $('geo-search').focus(); });
      box.appendChild(b);
    });
    if (!hits.length) box.appendChild(el('div', 'geo-none', 'Kein Land gefunden.'));
    box.hidden = false;
  }

  function renderGeoDb(db) {
    const box = $('geo-db');
    box.innerHTML = '';
    const canManage = !window.PiAuth || window.PiAuth.can('firewall.manage');
    const line = el('span', 'geo-db-line');
    if (db.running) line.textContent = `Länder-Datenbank wird geladen… ${db.progress || 0} %`;
    else if (db.installed) line.textContent = `Länder-Datenbank: Stand ${db.month} · ${fmtNum(db.rows)} Bereiche · ${fmtBytes(db.size)}`;
    else line.textContent = 'Für Länder (Flaggen bei IPs, Länder sperren) wird die kostenlose Länder-Datenbank von DB-IP gebraucht (ca. 5 MB Download, ~35 MB auf der Platte, monatlich aktualisiert).';
    box.appendChild(line);
    if (db.error) box.appendChild(el('span', 'form-msg error', db.error));
    if (canManage && !db.running) {
      const btn = el('button', db.installed ? 'btn-secondary' : 'btn-primary', db.installed ? 'Jetzt aktualisieren' : 'Länder-Datenbank laden');
      btn.type = 'button';
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try { renderGeoDb(await api('/api/tools/geoip/download', {})); pollGeoDb(); } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
      });
      box.appendChild(btn);
    }
    if (db.installed || db.running) {
      const a = el('a', 'geo-attr', db.attribution.text);
      a.href = db.attribution.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.setAttribute('data-no-i18n', '');
      box.appendChild(a);
    }
  }

  function pollGeoDb() {
    clearInterval(geo.polling);
    geo.polling = setInterval(async () => {
      try {
        const db = await api('/api/tools/geoip');
        renderGeoDb(db);
        if (!db.running) {
          clearInterval(geo.polling);
          if (db.installed && !db.error) {
            toast('Länder-Datenbank geladen.', 'success');
            load('geoblock'); load('fwTraffic'); load('ssh');
          }
        }
      } catch (e) { clearInterval(geo.polling); }
    }, 1000);
  }

  function renderGeoblock(g) {
    const status = $('geo-status');
    status.innerHTML = '';
    let level = '', text;
    if (g.error) { level = 'critical'; text = g.error; }
    else if (g.active) {
      level = 'good';
      text = `Aktiv: ${g.countries.length} ${g.countries.length === 1 ? 'Land' : 'Länder'} gesperrt (${fmtNum(g.loaded.v4 + g.loaded.v6)} Netze)`;
    } else if (g.countries.length && !g.rules) { level = 'warning'; text = 'Gespeichert, aber die Firewall-Regel fehlt – „Übernehmen“ erneut ausführen.'; }
    else text = 'Keine Länder gesperrt.';
    status.appendChild(el('span', `health-tag ${level}`, text));
    if (g.countries.length) {
      const list = el('span', 'geo-status-list');
      g.countries.forEach(cc => list.appendChild(countryTag(cc)));
      status.appendChild(list);
    }
    if (!g.ipset && !g.error) status.appendChild(el('span', 'field-hint', 'ipset fehlt – wird beim nächsten Update von install.sh installiert (oder: sudo apt install ipset).'));
    renderGeoDb(g.db);
    if (g.db.running) pollGeoDb();
    $('geo-editor').hidden = !g.db.installed;
    geo.selected = new Set(g.countries);
    $('geo-msg').textContent = '';
    renderGeoSelected();
    renderGeoSuggest();
  }

  // Suggestions: countries that showed up most in blocked traffic
  function renderGeoSuggest() {
    const top = ((cache.fwTraffic && cache.fwTraffic.top_countries) || []).filter(c => !geo.selected.has(c.cc)).slice(0, 6);
    const sug = $('geo-suggest');
    sug.innerHTML = '';
    sug.hidden = !top.length;
    if (top.length) {
      sug.appendChild(el('span', 'field-hint', 'Häufig im Firewall-Traffic:'));
      top.forEach(c => {
        const b = el('button', 'geo-sugg', `${flag(c.cc)} ${countryName(c.cc)} (${fmtNum(c.count)})`);
        b.type = 'button';
        b.setAttribute('data-no-i18n', '');
        b.addEventListener('click', () => { geoAdd(c.cc); b.remove(); });
        sug.appendChild(b);
      });
    }
  }

  async function applyGeoblock(countries) {
    const msg = $('geo-msg');
    ['geo-apply', 'geo-clear'].forEach(id => { $(id).disabled = true; });
    msg.className = 'form-msg';
    msg.textContent = 'Wird übernommen… (kann bei vielen Ländern einige Sekunden dauern)';
    try {
      cache.geoblock = await api('/api/tools/geoblock', { countries });
      renderGeoblock(cache.geoblock);
      toast(countries.length ? 'Länder-Sperre aktualisiert.' : 'Alle Länder freigegeben.', 'success');
      load('fwTraffic');
    } catch (err) {
      msg.className = 'form-msg error';
      msg.textContent = err.message;
    } finally {
      ['geo-apply', 'geo-clear'].forEach(id => { $(id).disabled = false; });
    }
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
        const cc = (f.countries || {})[ip];
        if (cc) row.querySelector('.iface-tags').appendChild(countryTag(cc));
        const btn = el('button', 'btn-secondary', 'Entsperren');
        btn.dataset.perm = 'firewall.manage';
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
      row.insertCell().appendChild(originTag(e.scope, e.country));
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
      row.insertCell().appendChild(originTag(p.scope, p.country));
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
        input.readOnly = !(window.PiAuth && window.PiAuth.can('system.config'));
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
        ? `Gespeichert, aber noch nicht aktiv (aktuell: ${f.active_thresholds.map(t => `${t} °C`).join(' / ')}). Nach dem nächsten Neustart des Servers gilt die neue Kurve.`
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

  // Hash chain of the activity log: intact, or where it breaks
  async function verifyActivity() {
    const box = $('activity-integrity');
    const btn = $('activity-verify');
    btn.disabled = true;
    box.hidden = false;
    box.className = 'audit-verify';
    box.textContent = 'Prüfe…';
    try {
      const v = await api('/api/tools/activity/verify');
      box.innerHTML = '';
      box.classList.add(v.ok ? 'ok' : 'broken');
      const head = el('div', 'audit-verify-head');
      head.append(el('span', `health-tag ${v.ok ? 'good' : 'critical'}`, v.ok ? 'Kette intakt' : 'Kette beschädigt'),
        el('span', '', `${v.checked.toLocaleString(LOCALE)} Einträge geprüft${v.since ? ` · seit ${new Date(v.since * 1000).toLocaleString(LOCALE)}` : ''}`));
      box.appendChild(head);
      if (v.head) {
        const h = el('code', 'audit-hash', v.head);
        h.setAttribute('data-no-i18n', '');
        box.append(el('span', 'field-hint', 'Aktueller Stand der Kette:'), h);
      }
      if (v.legacy) box.appendChild(el('span', 'field-hint', `${v.legacy} ältere Einträge von vor der Verkettung (nicht prüfbar).`));
      v.problems.forEach(p => {
        const line = el('span', 'audit-problem', `#${p.id} · ${new Date(p.ts * 1000).toLocaleString(LOCALE)} – ${p.text}`);
        box.appendChild(line);
      });
    } catch (err) {
      box.textContent = err.message;
    }
    btn.disabled = false;
  }

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
      const ipCell = row.insertCell();
      ipCell.textContent = e.ip || '–';
      if (e.country) { ipCell.prepend(`${flag(e.country)} `); ipCell.title = countryName(e.country); }
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
      ? `${r.minutes} Messungen · CPU in % der gesamten CPU (alle Kerne) · CPU-Zeit = Minuten volle Auslastung eines ganzen Servers`
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
      $('sensor-hint').textContent = 'Keine Sensoren gefunden (nur auf Linux).';
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
  // Live connections
  // ---------------------------------------------------------------------------
  const CONN_STATE = { 'ESTAB': 'Verbunden', 'SYN-SENT': 'Baut auf', 'SYN-RECV': 'Baut auf', 'TIME-WAIT': 'Wird beendet', 'CLOSE-WAIT': 'Wird beendet',
    'FIN-WAIT-1': 'Wird beendet', 'FIN-WAIT-2': 'Wird beendet', 'LAST-ACK': 'Wird beendet', 'CLOSING': 'Wird beendet' };
  let connTimer = null;

  function renderConnections(r) {
    const s = r.summary;
    benchValues($('conn-stats'), [
      ['Offen', fmtNum(s.total)], ['Eingehend', fmtNum(s.incoming)], ['Ausgehend', fmtNum(s.outgoing)],
      ['Ins Internet', fmtNum(s.internet)], ['Länder', s.countries ? fmtNum(s.countries) : '–']
    ]);
    const progs = $('conn-programs');
    progs.innerHTML = '';
    r.programs.slice(0, 10).forEach(p => {
      const b = el('button', 'conn-prog');
      b.type = 'button';
      b.append(raw(p.name), el('span', 'conn-prog-n', String(p.count)));
      b.addEventListener('click', () => { $('conn-q').value = p.name === '?' ? '' : p.name; renderConnTable(); });
      progs.appendChild(b);
    });
    renderConnTable();
    const hints = [];
    if (r.note) hints.push(r.note);
    if (s.internal) hints.push(`${s.internal} interne Verbindungen (localhost, Container-Netze) ausgeblendet.`);
    if (r.truncated) hints.push('Nur die ersten 1500 Verbindungen.');
    $('conn-hint').textContent = hints.join(' ');
  }

  function raw(text) {
    const span = document.createElement('span');
    span.textContent = text;
    span.setAttribute('data-no-i18n', '');
    return span;
  }

  function renderConnTable() {
    const r = cache.connections;
    if (!r) return;
    const dir = $('conn-dir').value, scope = $('conn-scope').value, q = $('conn-q').value.trim().toLowerCase();
    const showInternal = $('conn-internal').checked;
    const items = r.connections.filter(c => (showInternal || !c.internal) && (!dir || c.direction === dir) && (!scope || c.scope === scope)
      && (!q || [c.program, c.remote, c.local, String(c.rport), String(c.lport), c.name].some(v => (v || '').toLowerCase().includes(q))));
    const t = $('conn-table');
    t.innerHTML = '';
    const head = t.createTHead().insertRow();
    ['Programm', 'Richtung', 'Gegenstelle', 'Herkunft', ''].forEach(h => head.appendChild(el('th', '', h)));
    const body = t.createTBody();
    const canBlock = !window.PiAuth || window.PiAuth.can('firewall.manage');
    items.slice(0, 400).forEach(c => {
      const row = body.insertRow();
      const prog = row.insertCell();
      prog.appendChild(raw(c.program || '?'));
      prog.appendChild(el('span', 'lan-sub', `${c.pid ? `PID ${c.pid} · ` : ''}${c.proto.toUpperCase()} · Port ${c.lport || '–'}`));
      const dirCell = row.insertCell();
      dirCell.appendChild(el('span', `health-tag ${c.direction === 'in' ? 'warning' : ''}`, c.direction === 'in' ? 'Eingehend' : 'Ausgehend'));
      if (c.state !== 'ESTAB') dirCell.appendChild(el('span', 'lan-sub', CONN_STATE[c.state] || c.state));
      const remote = row.insertCell();
      remote.className = 'mono';
      remote.textContent = fmtEndpoint(c.remote, c.rport);
      if (c.name) remote.appendChild(el('span', 'lan-sub', c.name));
      row.insertCell().appendChild(originTag(c.scope, c.country));
      const act = row.insertCell();
      act.className = 'row-action';
      if (canBlock && c.direction === 'in' && c.scope === 'internet') {
        const b = el('button', 'btn-danger', 'IP sperren');
        confirmClick(b, 'IP sperren', 'Sicher?', async () => {
          if (await firewallAction('rule', { action: 'deny', direction: 'in', from: c.remote, position: 'top', proto: 'any', comment: 'Blocked via HostCore' })) {
            toast(`${c.remote} gesperrt.`, 'success');
            load('connections');
          }
        });
        act.appendChild(b);
      }
    });
    if (!items.length) {
      const cell = body.insertRow().insertCell();
      cell.colSpan = 5;
      cell.textContent = 'Keine passenden Verbindungen.';
    }
  }

  // ---------------------------------------------------------------------------
  // Error digest
  // ---------------------------------------------------------------------------
  let errDays = 7;

  function errBars(perDay) {
    const w = 84, h = 22, n = perDay.length, max = Math.max(...perDay, 1);
    const svg = svgEl('svg', { viewBox: `0 0 ${w} ${h}`, class: 'err-bars', role: 'img', 'aria-label': 'Verlauf pro Tag' });
    const bw = Math.max(1, w / n - 1);
    perDay.forEach((v, i) => {
      if (!v) return;
      const bh = Math.max(2, v / max * (h - 2));
      svg.appendChild(svgEl('rect', { x: i * (w / n), y: h - bh, width: bw, height: bh, rx: 1, fill: 'var(--accent-rose)' }));
    });
    return svg;
  }

  function renderErrors(r) {
    const hint = $('err-hint');
    hint.textContent = '';
    if (!r.supported) { hint.textContent = 'Kein systemd-Journal auf diesem System.'; return; }
    benchValues($('err-stats'), [
      ['Fehler', fmtNum(r.total)], ['Letzte 24 Std.', fmtNum(r.last_24h)], ['Verschiedene', fmtNum(r.group_count)],
      ['Neu (24 Std.)', fmtNum(r.groups.filter(g => g.new).length)]
    ]);
    renderErrorList();
    const hints = [];
    if (r.error) hints.push(r.error);
    if (r.hint) hints.push(r.hint);
    if (r.truncated) hints.push('Sehr viele Meldungen – nur die neuesten 30.000 wurden ausgewertet.');
    hint.textContent = hints.join(' ');
  }

  function renderErrorList() {
    const r = cache.errors;
    if (!r || !r.groups) return;
    const q = $('err-q').value.trim().toLowerCase();
    let items = r.groups.filter(g => !q || g.source.toLowerCase().includes(q) || g.sample.toLowerCase().includes(q));
    if ($('err-sort').value === 'last') items = [...items].sort((a, b) => b.last - a.last);
    const box = $('err-list');
    box.innerHTML = '';
    items.slice(0, 100).forEach(g => {
      const item = el('details', 'err-item');
      const sum = el('summary', 'err-sum');
      const main = el('div', 'err-main');
      const src = el('span', 'err-source');
      src.appendChild(raw(g.source));
      if (g.new) src.appendChild(el('span', 'health-tag warning', 'neu'));
      if (g.ongoing) src.appendChild(el('span', 'health-tag critical', 'gerade eben'));
      const msg = el('span', 'err-msg');
      msg.appendChild(raw(g.sample));
      main.append(src, msg);
      const side = el('div', 'err-side');
      side.append(el('span', 'err-count', `${fmtNum(g.count)}×`), errBars(g.per_day), el('span', 'err-when', fmtTime(g.last)));
      sum.append(main, side);
      item.appendChild(sum);
      const det = el('div', 'err-detail');
      det.append(el('div', 'err-detail-row', `Zuerst: ${fmtTime(g.first)} · Zuletzt: ${fmtTime(g.last)}${g.unit ? ` · Dienst: ${g.unit}` : ''}`));
      const pat = el('code', 'err-pattern');
      pat.appendChild(raw(g.pattern));
      det.appendChild(pat);
      const b = el('button', 'btn-secondary', 'Im Log suchen');
      b.dataset.perm = 'logs.view';
      b.addEventListener('click', () => {
        // longest literal piece of the message (without placeholders, quotes, brackets)
        const words = g.pattern.split(/<[a-z]+>|["'()[\]{}]/).map(w => w.replace(/^[\s:;,.=\/-]+|[\s:;,.=\/-]+$/g, '')).sort((a, c) => c.length - a.length)[0] || '';
        const unitSel = $('log-unit');
        const unit = g.source === 'kernel' ? 'kernel' : g.unit;
        unitSel.value = [...unitSel.options].some(o => o.value === unit) ? unit : '';
        $('log-priority').value = 'err';
        $('log-since').value = errDays <= 1 ? '24h' : '7d';
        $('log-query').value = words.slice(0, 60);
        searchLogs();
        $('logs').scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      det.appendChild(b);
      item.appendChild(det);
      box.appendChild(item);
    });
    if (!items.length) box.appendChild(el('p', 'field-hint', r.total ? 'Keine passenden Meldungen.' : 'Keine Fehler im Zeitraum – sehr gut.'));
  }

  // ---------------------------------------------------------------------------
  // Packages (apt)
  // ---------------------------------------------------------------------------
  let pkgTimer = null;

  async function searchPackages(e) {
    if (e) e.preventDefault();
    const q = $('pkg-q').value.trim();
    if (q.length < 2) return;
    const t = $('pkg-results');
    t.innerHTML = '';
    $('pkg-hint').textContent = 'Suche…';
    try {
      const r = await api(`/api/packages/search?q=${encodeURIComponent(q)}`);
      $('pkg-hint').textContent = r.total > r.results.length ? `${r.total} Treffer – die ersten ${r.results.length} werden gezeigt.` : r.total ? '' : 'Nichts gefunden.';
      const head = t.createTHead().insertRow();
      ['Paket', 'Beschreibung', ''].forEach(h => head.appendChild(el('th', '', h)));
      const body = t.createTBody();
      r.results.forEach(p => body.appendChild(pkgRow(p)));
    } catch (err) { $('pkg-hint').textContent = err.message; }
  }

  function pkgRow(p, installedView = false) {
    const row = document.createElement('tr');
    const name = row.insertCell();
    const b = el('button', 'text-btn mono', p.name);
    b.type = 'button';
    b.setAttribute('data-no-i18n', '');
    b.addEventListener('click', () => showPackage(p.name, row));
    name.appendChild(b);
    if (p.version) name.appendChild(el('span', 'lan-sub', p.version));
    const d = row.insertCell();
    d.className = 'wrap';
    d.appendChild(raw(p.summary || ''));
    if (installedView) {
      row.insertCell().textContent = fmtBytes(p.size);
    }
    const act = row.insertCell();
    act.className = 'row-action';
    const canChange = !window.PiAuth || window.PiAuth.can('updates.install');
    if (p.installed || installedView) {
      if (!installedView) act.appendChild(el('span', 'health-tag good', 'installiert'));
      if (canChange && !p.base) act.appendChild(button('Entfernen…', 'btn-secondary', () => planPackages('remove', [p.name])));
      if (p.base) act.appendChild(el('span', 'health-tag', 'Basis'));
    } else if (canChange) {
      act.appendChild(button('Installieren…', 'btn-primary', () => planPackages('install', [p.name])));
    }
    return row;
  }

  function button(label, cls, onClick) {
    const b = el('button', cls, label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  async function showPackage(name, row) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains('pkg-detail-row')) { next.remove(); return; }
    try {
      const d = await api(`/api/packages/show?name=${encodeURIComponent(name)}`);
      const tr = el('tr', 'pkg-detail-row');
      const td = el('td');
      td.colSpan = row.cells.length;
      const kv = el('dl', 'kv-grid');
      renderKv(kv, [['Version', d.version], ['Installiert', d.installed ? d.installed_version : 'nein'], ['Größe', fmtBytes(d.size)],
        ['Download', fmtBytes(d.download)], ['Bereich', d.section], ['Braucht', d.depends || '–']]);
      td.appendChild(kv);
      if (d.description) { const p = el('p', 'pkg-desc'); p.appendChild(raw(d.description)); td.appendChild(p); }
      if (d.homepage) { const a = el('a', 'text-btn', d.homepage); a.href = d.homepage; a.target = '_blank'; a.rel = 'noopener'; td.appendChild(a); }
      tr.appendChild(td);
      row.after(tr);
    } catch (err) { toast(err.message, 'error'); }
  }

  async function planPackages(action, names) {
    const box = $('pkg-plan');
    box.hidden = false;
    box.innerHTML = '';
    box.appendChild(el('p', 'field-hint', 'Probelauf…'));
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    try {
      const sim = await api('/api/packages/simulate', { action, names });
      box.innerHTML = '';
      const tr = x => (window.I18N && window.I18N.t ? window.I18N.t(x) : x);
      const title = el('h3', 'subhead');
      title.setAttribute('data-no-i18n', '');
      title.textContent = `${tr(action === 'install' ? 'Installieren' : 'Entfernen')}: ${names.join(', ')}`;
      box.appendChild(title);
      if (sim.error) { box.appendChild(el('p', 'form-msg error', sim.error)); return; }
      const lists = [['install', 'Wird installiert'], ['upgrade', 'Wird aktualisiert'], ['remove', 'Wird entfernt']];
      lists.forEach(([k, label]) => {
        if (!sim[k].length) return;
        const line = el('div', 'pkg-plan-line');
        line.appendChild(el('span', `health-tag ${k === 'remove' ? 'critical' : k === 'upgrade' ? 'warning' : 'good'}`, `${label} (${sim[k].length})`));
        line.appendChild(raw(sim[k].join(', ')));
        box.appendChild(line);
      });
      if (sim.space) box.appendChild(el('p', 'field-hint', sim.frees ? `Gibt ${sim.space} frei.` : `Braucht ${sim.space} zusätzlich.`));
      if (sim.blocked.length) { box.appendChild(el('p', 'form-msg error', `Würde Basis-Pakete entfernen (${sim.blocked.join(', ')}) – abgebrochen.`)); return; }
      if (!sim.install.length && !sim.remove.length && !sim.upgrade.length) { box.appendChild(el('p', 'field-hint', 'Nichts zu tun.')); return; }
      const actions = el('div', 'form-actions');
      actions.appendChild(button('Abbrechen', 'btn-secondary', () => { box.hidden = true; }));
      const go = button(action === 'install' ? 'Jetzt installieren' : 'Jetzt entfernen', action === 'install' ? 'btn-primary' : 'btn-danger', async () => {
        go.disabled = true;
        try {
          const st = await api('/api/packages/run', { action, names });
          box.hidden = true;
          renderPkgRun(st);
          pollPkgRun();
        } catch (err) { toast(err.message, 'error'); go.disabled = false; }
      });
      actions.appendChild(go);
      box.appendChild(actions);
    } catch (err) {
      box.innerHTML = '';
      box.appendChild(el('p', 'form-msg error', err.message));
    }
  }

  function renderPkgRun(st) {
    if (!st || !st.state) return;
    const box = $('pkg-run');
    box.hidden = false;
    const tr = x => (window.I18N && window.I18N.t ? window.I18N.t(x) : x);
    const label = `apt ${st.action} ${st.packages}: ${st.running ? tr('läuft…') : st.state === 'done' ? tr('fertig') : `${tr('fehlgeschlagen')} (Code ${st.exit_code})`}`;
    $('pkg-run-state').className = `health-tag ${st.running ? 'warning' : st.state === 'done' ? 'good' : 'critical'}`;
    $('pkg-run-state').textContent = label;
    $('pkg-run-state').setAttribute('data-no-i18n', '');
    const log = $('pkg-run-log');
    log.textContent = st.log || '…';
    log.scrollTop = log.scrollHeight;
  }

  function pollPkgRun() {
    clearInterval(pkgTimer);
    pkgTimer = setInterval(async () => {
      try {
        const st = await api('/api/packages/status');
        renderPkgRun(st);
        if (!st.running) {
          clearInterval(pkgTimer);
          toast(st.state === 'done' ? 'Paket-Aktion fertig.' : 'Paket-Aktion fehlgeschlagen.', st.state === 'done' ? 'success' : 'error');
          cache.packages = null;
          if (!$('pkg-installed-tab').hidden) loadInstalled();
          if ($('pkg-q').value.trim()) searchPackages();
        }
      } catch (e) { clearInterval(pkgTimer); }
    }, 1500);
  }

  async function loadInstalled() {
    try {
      cache.packages = cache.packages || await api('/api/packages');
      renderInstalled();
    } catch (err) { $('pkg-hint').textContent = err.message; }
  }

  function renderInstalled() {
    const r = cache.packages;
    if (!r) return;
    if (!r.supported) { $('pkg-hint').textContent = 'Kein apt/dpkg auf diesem System.'; return; }
    benchValues($('pkg-stats'), [['Installiert', fmtNum(r.count)], ['Selbst installiert', fmtNum(r.manual)], ['Belegt', fmtBytes(r.total_size)]]);
    const q = $('pkg-filter').value.trim().toLowerCase(), all = $('pkg-all').checked;
    const items = r.packages.filter(p => (all || p.manual) && (!q || p.name.includes(q) || (p.summary || '').toLowerCase().includes(q)));
    const t = $('pkg-installed');
    t.innerHTML = '';
    const head = t.createTHead().insertRow();
    ['Paket', 'Beschreibung', 'Größe', ''].forEach(h => head.appendChild(el('th', '', h)));
    const body = t.createTBody();
    items.slice(0, 300).forEach(p => body.appendChild(pkgRow(p, true)));
    $('pkg-hint').textContent = items.length > 300 ? `${items.length} Pakete – die 300 größten werden gezeigt, Filter eingrenzen.` : '';
  }

  // ---------------------------------------------------------------------------
  // Power use and cost
  // ---------------------------------------------------------------------------
  function fmtMoney(v, cur) {
    return v == null ? '–' : `${v.toLocaleString(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}`;
  }

  const fmtKwh = v => v == null ? '–' : `${v.toLocaleString(LOCALE, { maximumFractionDigits: v < 10 ? 2 : 1 })} kWh`;

  function renderPower(p) {
    $('power-body').hidden = !p.available;
    $('power-hint').textContent = p.available ? '' : 'Keine Leistungsdaten – die Messung gibt es nur auf dem Raspberry Pi 5 (vcgencmd pmic_read_adc). Die ersten Werte erscheinen nach ein paar Minuten.';
    const cfg = p.settings;
    if (document.activeElement.closest && !document.activeElement.closest('#power-form')) {
      $('power-price').value = cfg.price_ct;
      $('power-extra').value = cfg.extra_w;
      $('power-currency').value = cfg.currency;
    }
    if (!p.available) return;
    const cur = cfg.currency;
    benchValues($('power-stats'), [
      ['Jetzt', p.now_w == null ? '–' : `${p.now_w.toLocaleString(LOCALE, { maximumFractionDigits: 1 })} W`],
      ['Heute', p.today ? `${fmtKwh(p.today.kwh)} · ${fmtMoney(p.today.cost, cur)}` : '–'],
      ['Dieser Monat', `${fmtKwh(p.month.kwh)} · ${fmtMoney(p.month.cost, cur)}`],
      ['Hochgerechnet Monat', fmtMoney(p.month.projected_cost, cur)],
      ['Hochgerechnet Jahr', `${fmtKwh(p.year.kwh)} · ${fmtMoney(p.year.cost, cur)}`],
      ['Ø Leistung', `${p.avg_w.toLocaleString(LOCALE, { maximumFractionDigits: 1 })} W`]
    ]);
    drawPowerChart(p);
  }

  function drawPowerChart(p) {
    const box = $('power-chart'), svg = box.querySelector('svg'), tip = box.querySelector('.history-tooltip');
    svg.innerHTML = '';
    tip.hidden = true;
    const W = box.clientWidth, H = box.clientHeight;
    if (!W) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const n = 30;
    const byDay = new Map(p.days.map(d => [d.day, d]));
    const days = [];
    for (let i = n - 1; i >= 0; i--) {
      const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - i);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      days.push({ key, date: d, ...(byDay.get(key) || {}) });
    }
    const max = Math.max(...days.map(d => d.kwh || 0), 0.01);
    const raw = max / 4, exp = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / exp;
    const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
    const top = Math.ceil(max / step) * step;
    const pad = { l: 58, r: 8, t: 10, b: 24 }, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const y = v => pad.t + ih - v / top * ih;
    for (let v = 0; v <= top + step / 2; v += step) {
      svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + iw, y1: y(v), y2: y(v), class: 'grid-line' }));
      const l = svgEl('text', { x: pad.l - 8, y: y(v) + 3, 'text-anchor': 'end', class: 'axis-label' });
      l.textContent = v ? `${+v.toFixed(3)} kWh` : '0';
      svg.appendChild(l);
    }
    const slot = iw / n, bw = Math.max(2, Math.min(16, slot - 3));
    days.forEach((d, i) => {
      const x = pad.l + i * slot + (slot - bw) / 2;
      if (d.kwh) {
        const h = Math.max(1, y(0) - y(d.kwh)), rad = Math.min(3, bw / 2, h), y0 = y(0) - h;
        svg.appendChild(svgEl('path', { d: `M${x},${y0 + h}V${y0 + rad}Q${x},${y0} ${x + rad},${y0}H${x + bw - rad}Q${x + bw},${y0} ${x + bw},${y0 + rad}V${y0 + h}Z`,
          fill: 'var(--accent-amber)', class: `bar${d.coverage < 0.9 ? ' partial' : ''}`, 'data-i': i }));
      }
      if ((n - 1 - i) % 5 === 0) {
        const l = svgEl('text', { x: x + bw / 2, y: H - 6, 'text-anchor': 'middle', class: 'axis-label' });
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
      const rows = d.kwh == null ? [['–', 'keine Messung']] : [
        [fmtKwh(d.kwh), fmtMoney(d.cost, p.settings.currency)], [`Ø ${d.avg_w.toLocaleString(LOCALE)} W`, `max. ${d.max_w.toLocaleString(LOCALE)} W`]];
      if (d.coverage != null && d.coverage < 0.9) rows.push([`${Math.round(d.coverage * 100)} %`, 'des Tages gemessen']);
      rows.forEach(([a, b]) => {
        const row = el('div', 'tt-row');
        row.append(el('b', '', a), document.createTextNode(b));
        tip.appendChild(row);
      });
      tip.hidden = false;
      const cx = pad.l + (i + 0.5) * slot;
      tip.style.left = `${cx + 14 + tip.offsetWidth > W ? cx - 14 - tip.offsetWidth : cx + 14}px`;
    };
    svg.onpointerleave = () => { svg.querySelectorAll('.bar.dim').forEach(b => b.classList.remove('dim')); tip.hidden = true; };
  }

  async function savePower(e) {
    e.preventDefault();
    const price = parseFloat(String($('power-price').value).replace(',', '.'));
    const extra = parseFloat(String($('power-extra').value || '0').replace(',', '.'));
    if (!isFinite(price) || !isFinite(extra)) { toast('Bitte Preis und Zuschlag als Zahl angeben.', 'error'); return; }
    try {
      cache.power = await api('/api/power/settings', {
        price_ct: price,
        extra_w: extra,
        currency: $('power-currency').value.trim() || '€'
      });
      document.activeElement.blur();
      renderPower(cache.power);
      toast('Strompreis gespeichert.', 'success');
    } catch (err) { toast(err.message, 'error'); }
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
  // Events (full list)
  // ---------------------------------------------------------------------------
  async function loadAllEvents() {
    const days = $('events-days').value, level = $('events-level').value, cat = $('events-category').value;
    try {
      const r = await api(`/api/events?days=${days}`);
      const list = $('events-all');
      list.innerHTML = '';
      const shown = r.events.filter(e => (!cat || e.category === cat)
        && (!level || (level === 'critical' ? e.level === 'critical' : ['warning', 'critical'].includes(e.level))));
      shown.forEach(e => list.appendChild(window.PiOverview.eventItem(e)));
      if (!shown.length) list.appendChild(el('li', 'notes-empty', 'Keine Ereignisse für diesen Filter.'));
      const c = r.counts;
      $('events-summary').textContent = `${r.total} Ereignisse · ${c.critical} kritisch · ${c.warning} Warnungen · ${c.good} erholt`;
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Disk forecast
  // ---------------------------------------------------------------------------
  function renderForecast(f) {
    const list = $('forecast-list');
    list.innerHTML = '';
    f.mounts.forEach(m => {
      const card = el('div', 'forecast-card');
      const head = el('div', 'forecast-head');
      head.appendChild(el('span', 'workload-name', m.mount === '/' ? 'System (/)' : m.mount));
      let tag;
      if (m.days_left != null) {
        const lvl = m.days_left < 30 ? 'critical' : m.days_left < 180 ? 'warning' : 'good';
        tag = el('span', `health-tag ${lvl}`, m.days_left > 3650 ? 'voll in > 10 Jahren' : `voll in ~${m.days_left} Tagen`);
      } else if (m.per_day != null) {
        tag = el('span', 'health-tag good', 'kein nennenswerter Zuwachs');
      } else {
        tag = el('span', 'health-tag', `Sammle Daten (${m.days_tracked}/3 Tage)`);
      }
      head.appendChild(tag);
      card.appendChild(head);
      const bar = el('div', 'progress-bar-bg');
      const fill = el('div', `progress-bar-fill fill-emerald ${m.percent > 85 ? 'danger' : m.percent > 70 ? 'warn' : ''}`);
      fill.style.width = `${m.percent || 0}%`;
      bar.appendChild(fill);
      card.appendChild(bar);
      const sub = [`${fmtBytes(m.used)} von ${fmtBytes(m.total)} belegt (${m.percent}%)`, `${fmtBytes(m.free)} frei`];
      if (m.per_day != null) sub.push(m.per_day >= 0 ? `+${fmtBytes(m.per_day)} pro Tag` : `${fmtBytes(-m.per_day)} weniger pro Tag`);
      card.appendChild(el('span', 'workload-sub', sub.join(' · ')));
      if (m.history.length > 1) {
        card.appendChild(sparkLine(m.history.map(h => ({ ts: Date.parse(`${h.day}T12:00:00`) / 1000, avg: h.used })),
          'var(--accent-cyan)', `${m.mount}: Füllstand`));
      }
      list.appendChild(card);
    });
    if (!f.mounts.length) list.appendChild(emptyRow('Noch keine Daten – der erste Tageswert wird innerhalb einer Stunde gespeichert.'));

    const growth = $('forecast-growth');
    growth.innerHTML = '';
    const folderTable = (title, rows, withDelta) => {
      growth.appendChild(el('h3', 'subhead', title));
      const wrap = el('div', 'table-scroll');
      const t = el('table', 'data-table');
      const head = t.createTHead().insertRow();
      ['Ordner', 'Größe', ...(withDelta ? ['Zuwachs'] : [])].forEach(h => head.appendChild(el('th', '', h)));
      const body = t.createTBody();
      rows.forEach(r => {
        const row = body.insertRow();
        const p = row.insertCell();
        p.textContent = r.path;
        p.setAttribute('data-no-i18n', '');
        row.insertCell().textContent = fmtBytes(r.kb * 1024);
        if (withDelta) {
          const d = row.insertCell();
          d.appendChild(el('span', `health-tag ${r.delta_kb > 0 ? 'warning' : 'good'}`,
            `${r.delta_kb > 0 ? '+' : '−'}${fmtBytes(Math.abs(r.delta_kb) * 1024)}${r.new ? ' (neu)' : ''}`));
        }
      });
      wrap.appendChild(t);
      growth.appendChild(wrap);
    };
    if (f.growth.week && f.growth.week.folders.length) folderTable('Am stärksten gewachsen – letzte 7 Tage', f.growth.week.folders, true);
    if (f.growth.month && f.growth.month.folders.length) folderTable('Am stärksten gewachsen – letzte 30 Tage', f.growth.month.folders, true);
    if (f.top_folders.length) folderTable('Größte Ordner', f.top_folders, false);
    $('forecast-hint').textContent = f.folders_day
      ? `Ordnergrößen vom ${new Date(`${f.folders_day}T12:00:00`).toLocaleDateString(LOCALE)} (nur Ordner ab 50 MB, ohne Inhalte, die dein Benutzer nicht lesen darf).`
      : 'Ordnergrößen werden das erste Mal innerhalb einer Stunde gemessen.';
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
    fwTraffic: ['/api/tools/firewall/traffic', renderTraffic],
    geoblock: ['/api/tools/geoblock', renderGeoblock],
    power: ['/api/power', renderPower],
    connections: ['/api/tools/connections', renderConnections],
    errors: ['/api/tools/errors?days=7', renderErrors],
    ssh: ['/api/tools/ssh', renderSsh],
    uptime: ['/api/monitor/uptime?days=7', renderUptime],
    throttle: ['/api/monitor/throttle?days=30', renderThrottle],
    lan: ['/api/tools/lan', renderLan],
    ports: ['/api/tools/ports', renderPorts],
    fan: ['/api/tools/fan-curve', renderFanCurve],
    hogs: ['/api/monitor/hogs?hours=24', renderHogs],
    usage: ['/api/monitor/usage', renderUsage],
    forecast: ['/api/disk-forecast', renderForecast]
  };

  const LOADER_PERMS = { ssh: 'security.view', firewall: 'security.view', fwTraffic: 'security.view', geoblock: 'security.view', connections: 'security.view', errors: 'logs.view', logUnits: 'logs.view' };

  const loadSeq = {};

  async function load(key) {
    if (LOADER_PERMS[key] && window.PiAuth && !window.PiAuth.can(LOADER_PERMS[key])) return;
    if (key === 'fwTraffic') LOADERS.fwTraffic[0] = fwtUrl();
    const [url, render] = LOADERS[key];
    const seq = loadSeq[key] = (loadSeq[key] || 0) + 1;
    try {
      const data = await api(url);
      if (seq !== loadSeq[key]) return;  // a newer request (other filter) is on its way
      cache[key] = data;
      render(cache[key]);
    } catch (err) {
      toast(`${key}: ${err.message}`, 'error');
    }
  }

  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
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
    ['conn-dir', 'conn-scope', 'conn-internal'].forEach(id => $(id).addEventListener('change', renderConnTable));
    $('conn-q').addEventListener('input', renderConnTable);
    $('conn-filters').addEventListener('submit', e => e.preventDefault());
    $('conn-live').addEventListener('change', () => {
      clearInterval(connTimer);
      if ($('conn-live').checked) connTimer = setInterval(() => { if (!document.hidden) load('connections'); }, 5000);
    });
    document.querySelectorAll('#err-range button').forEach(b => b.addEventListener('click', () => {
      errDays = +b.dataset.days;
      document.querySelectorAll('#err-range button').forEach(x => x.classList.toggle('active', x === b));
      LOADERS.errors[0] = `/api/tools/errors?days=${errDays}`;
      load('errors');
    }));
    $('err-q').addEventListener('input', renderErrorList);
    $('err-sort').addEventListener('change', renderErrorList);
    $('err-filters').addEventListener('submit', e => e.preventDefault());
    $('pkg-search-form').addEventListener('submit', searchPackages);
    document.querySelectorAll('#pkg-tabs button').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('#pkg-tabs button').forEach(x => x.classList.toggle('active', x === b));
      $('pkg-search-tab').hidden = b.dataset.tab !== 'search';
      $('pkg-installed-tab').hidden = b.dataset.tab !== 'installed';
      $('pkg-hint').textContent = '';
      if (b.dataset.tab === 'installed') loadInstalled();
    }));
    $('pkg-filter').addEventListener('input', renderInstalled);
    $('pkg-all').addEventListener('change', renderInstalled);
    $('pkg-installed-form').addEventListener('submit', e => e.preventDefault());
    $('pkg-run-hide').addEventListener('click', () => { $('pkg-run').hidden = true; });
    api('/api/packages/status').then(st => { if (st.running) { renderPkgRun(st); pollPkgRun(); } }).catch(() => {});
    $('power-form').addEventListener('submit', savePower);
    new ResizeObserver(() => cache.power && drawPowerChart(cache.power)).observe($('power-chart'));
    $('fw-form').addEventListener('submit', addFirewallRule);
    ['input', 'change'].forEach(ev => $('fw-form').addEventListener(ev, updateRulePreview));
    updateRulePreview();
    document.querySelectorAll('#fwt-range button').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('#fwt-range button').forEach(x => x.classList.toggle('active', x === b));
      load('fwTraffic');
    }));
    ['fwt-action', 'fwt-direction'].forEach(id => $(id).addEventListener('change', () => load('fwTraffic')));
    let fwtTimer = null;
    $('fwt-query').addEventListener('input', () => { clearTimeout(fwtTimer); fwtTimer = setTimeout(() => load('fwTraffic'), 400); });
    $('fwt-filters').addEventListener('submit', e => { e.preventDefault(); load('fwTraffic'); });
    $('fwt-more').addEventListener('click', () => { fwtShown += 100; renderTrafficRecent(cache.fwTraffic); });
    $('fwt-enable-log').addEventListener('click', () => firewallAction('logging', { level: 'low' }));
    $('geo-search').addEventListener('input', renderGeoResults);
    $('geo-search').addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); const first = $('geo-results').querySelector('.geo-result'); if (first) first.click(); }
      if (e.key === 'Escape') { $('geo-search').value = ''; renderGeoResults(); }
    });
    document.addEventListener('click', e => { if (!e.target.closest('.geo-picker')) $('geo-results').hidden = true; });
    $('geo-apply').addEventListener('click', () => applyGeoblock([...geo.selected]));
    confirmClick($('geo-clear'), 'Alle freigeben', 'Wirklich alle freigeben?', () => applyGeoblock([]));
    new ResizeObserver(() => cache.fwTraffic && drawTrafficChart(cache.fwTraffic)).observe($('fwt-chart'));
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
    $('activity-verify').addEventListener('click', verifyActivity);
    if (!window.PiAuth || window.PiAuth.can('security.view')) loadActivity();
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
    ['events-days', 'events-level', 'events-category'].forEach(id => $(id).addEventListener('change', loadAllEvents));
    loadAllEvents();
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
    if (!window.PiAuth || window.PiAuth.can('logs.view')) searchLogs();
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
