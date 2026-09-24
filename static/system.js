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

    const table = $('net-ports');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Port', 'Protokoll', 'Adresse', 'Dienst', 'Prozess', 'Erreichbar'].forEach(h => head.appendChild(el('th', '', h)));
    const body = table.createTBody();
    n.ports.forEach(p => {
      const r = body.insertRow();
      [p.port, p.proto, p.address, p.service || '–', p.process || '–'].forEach(v => { r.insertCell().textContent = v; });
      const cell = r.insertCell();
      const local = p.address.startsWith('127.') || p.address === '::1';
      cell.appendChild(el('span', `health-tag ${local ? '' : 'warning'}`, local ? 'Nur lokal' : 'Netzwerk'));
    });
    if (!n.ports.length) {
      const r = body.insertRow();
      const c = r.insertCell();
      c.colSpan = 6;
      c.textContent = 'Keine Daten.';
    }
    $('net-ports-hint').textContent = n.hint || '';
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
    ssh: ['/api/tools/ssh', renderSsh]
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
