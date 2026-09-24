/**
 * PiCore Dashboard - System tools page
 * Network details and hardware info (more tools are added as sections).
 */
(function () {
  'use strict';

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
  // Loading
  // ---------------------------------------------------------------------------
  const LOADERS = {
    network: ['/api/tools/network', renderNetwork],
    hardware: ['/api/tools/hardware', renderHardware]
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

    // Highlight the section currently in view
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));

    Object.keys(LOADERS).forEach(load);
  }

  init();
})();
