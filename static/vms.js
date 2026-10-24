/**
 * HostCore Dashboard - virtual machines (/vms), modelled on Cockpit's "Virtual machines".
 *
 * Overview of the host (what running VMs take), the list of VMs with live CPU / memory /
 * IP, a details sheet per VM (power, settings, disks and ISO, network interfaces,
 * snapshots, rename / clone / delete), the "VM erstellen" dialog (cloud image with
 * cloud-init, ISO, existing disk, PXE - runs as a task), images and ISOs, virtual
 * networks, storage pools and the setup checks. The console opens in its own tab (/vm-konsole).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const state = { data: null, catalog: null, nets: null, pools: null, detail: null, tab: 'overview', openPools: new Set(), createPick: '' };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const n = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(n);
    setTimeout(() => n.remove(), 5000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname)}${location.hash}`; throw new Error('Nicht angemeldet.'); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  const enc = encodeURIComponent;
  const vmPath = name => `/api/vms/vm/${enc(name)}`;

  function fmtBytes(n) {
    if (n == null) return '–';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${(i ? n.toFixed(n < 10 ? 1 : 0) : String(Math.round(n))).replace('.', LOCALE.startsWith('de') ? ',' : '.')} ${units[i]}`;
  }
  const fmtMb = mb => (mb == null ? '–' : fmtBytes(mb * 1048576));
  const fmtRate = b => (b == null ? '–' : `${fmtBytes(b)}/s`);
  const fmtNum = n => Number(n).toLocaleString(LOCALE, { maximumFractionDigits: 1 });

  function tag(level, text, title) {
    const e = el('span', `health-tag ${level || ''}`, t(text));
    if (title) e.title = title;
    return e;
  }

  function button(label, cls, onClick, perm) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    if (perm) b.dataset.perm = perm;
    if (perm && !can(perm)) b.hidden = true;
    b.addEventListener('click', e => { e.stopPropagation(); onClick(b); });
    return b;
  }

  // Second click within 4 s confirms
  function confirmButton(label, question, cls, onClick, perm) {
    return button(label, cls, b => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.dataset.label = b.textContent;
        b.textContent = t(question);
        setTimeout(() => { if (b.isConnected && b.classList.contains('confirming')) { b.classList.remove('confirming'); b.textContent = b.dataset.label; } }, 4000);
        return;
      }
      b.classList.remove('confirming');
      b.textContent = b.dataset.label;
      onClick(b);
    }, perm);
  }

  const ICONS = {
    start: '<polygon points="7 4 20 12 7 20 7 4"></polygon>',
    stop: '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path><line x1="12" y1="2" x2="12" y2="12"></line>',
    restart: '<path d="M23 4v6h-6"></path><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>',
    pause: '<rect x="6" y="5" width="4" height="14" rx="1"></rect><rect x="14" y="5" width="4" height="14" rx="1"></rect>',
    console: '<rect x="2" y="3" width="20" height="14" rx="2"></rect><path d="M8 21h8M12 17v4"></path><polyline points="6 8 9 10.5 6 13"></polyline>',
    details: '<circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle><circle cx="5" cy="12" r="1"></circle>'
  };

  function iconBtn(icon, label, onClick, perm, confirmLabel, danger) {
    const b = el('button', `ctr-icon${danger ? ' danger' : ''}`);
    b.type = 'button';
    b.title = t(label);
    b.setAttribute('aria-label', t(label));
    if (perm && !can(perm)) b.hidden = true;
    b.innerHTML = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[icon]}</svg>`;
    const lbl = el('span', 'ctr-icon-label');
    b.appendChild(lbl);
    b.addEventListener('click', e => {
      e.stopPropagation();
      if (confirmLabel && !b.classList.contains('confirming')) {
        b.classList.add('confirming');
        lbl.textContent = t(confirmLabel);
        setTimeout(() => { b.classList.remove('confirming'); lbl.textContent = ''; }, 4000);
        return;
      }
      b.classList.remove('confirming');
      lbl.textContent = '';
      onClick(b);
    });
    return b;
  }

  function head(table, labels) {
    table.innerHTML = '';
    const r = table.createTHead().insertRow();
    labels.forEach(l => r.appendChild(el('th', '', t(l))));
    table.createTBody();
    return table.tBodies[0];
  }

  function emptyRow(table, cols, text) {
    const c = table.tBodies[0].insertRow().insertCell();
    c.colSpan = cols;
    c.className = 'wrap empty-cell';
    c.textContent = t(text);
  }

  function nameCell(row, name, sub) {
    const c = row.insertCell();
    c.className = 'ctr-name';
    c.appendChild(raw('span', 'ctr-main', name));
    if (sub) c.appendChild(raw('span', 'ctr-sub', sub));
    return c;
  }

  function meter(used, total) {
    const wrap = el('div', 'vm-meter');
    if (!total) { wrap.textContent = '–'; return wrap; }
    const pct = Math.min(100, (used / total) * 100);
    const bar = el('div', 'progress-bar-bg mini');
    const fill = el('div', `progress-bar-fill ${pct >= 90 ? 'danger' : pct >= 75 ? 'warn' : 'fill-emerald'}`);
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    wrap.append(bar);
    return wrap;
  }

  async function run(btn, path, body, after) {
    if (btn) btn.disabled = true;
    try {
      const r = await api(path, body || {});
      if (r.id && r.state) {   // a task
        toast(t('Läuft als Aufgabe – Fortschritt unten in der Aufgabenleiste.'), 'success');
        if (window.PiTasks) window.PiTasks.open(r.id);
      } else toast(t(r.message || 'Erledigt.'), r.ok === false ? 'error' : 'success');
      if (after) await after(r);
      return r;
    } catch (err) {
      toast(t(err.message), 'error');
      return null;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Overview
  // ---------------------------------------------------------------------------
  function statTile(label, value, sub, level) {
    const box = el('div', 'bench-value');
    box.append(el('span', 'label', t(label)), raw('span', `value ${level || ''}`, value));
    if (sub) box.appendChild(el('span', 'vm-stat-sub', sub));
    return box;
  }

  function renderStats() {
    const d = state.data, h = d.host, st = d.status;
    const box = $('vm-stats');
    box.innerHTML = '';
    box.hidden = !st.connected;
    if (!st.connected) return;
    box.appendChild(statTile('VMs laufen', `${h.running}/${h.vms}`));
    box.appendChild(statTile('vCPUs zugewiesen', `${h.vcpus_used}/${h.cpus}`, `${t('Alle VMs:')} ${h.vcpus_defined}`, h.vcpus_used > h.cpus ? 'warning' : ''));
    const memPct = h.memory_mb ? h.memory_used_mb / h.memory_mb : 0;
    box.appendChild(statTile('Arbeitsspeicher zugewiesen', `${fmtMb(h.memory_used_mb)}`, `${t('von')} ${fmtMb(h.memory_mb)} · ${t('frei')} ${fmtMb(h.memory_available_mb)}`,
      memPct > 0.9 ? 'critical' : memPct > 0.75 ? 'warning' : ''));
    box.appendChild(statTile('Hypervisor', st.kvm ? 'KVM' : t('Emuliert'), st.version || st.arch, st.kvm ? '' : 'warning'));
  }

  function renderBanner() {
    const st = state.data.status;
    const box = $('vm-banner');
    box.innerHTML = '';
    const lines = [];
    if (!st.supported) lines.push(['critical', `Auf ${st.machine} werden VMs nicht unterstützt (nur x86_64 und arm64).`]);
    else if (!st.virsh || st.missing.length) lines.push(['warning', 'Virtualisierung ist noch nicht eingerichtet – unten unter „Einrichtung“ in zwei Schritten erledigt.']);
    else if (st.group.member && !st.group.active) lines.push(['warning', 'Der Dashboard-Benutzer ist in der Gruppe libvirt, das Dashboard muss dafür aber neu starten (Einrichtung → Zugriff einrichten).']);
    else if (!st.connected) lines.push(['critical', st.error || 'libvirt ist nicht erreichbar.']);
    if (st.supported && !st.kvm) {
      lines.push(['warning', st.host_virt
        ? `Kein /dev/kvm: dieser Host ist selbst eine VM (${st.host_virt}). Verschachtelte Virtualisierung im Hypervisor aktivieren, sonst laufen VMs nur emuliert (sehr langsam).`
        : st.virt_flag ? 'Kein /dev/kvm, obwohl die CPU Virtualisierung kann – Kernel-Modul kvm_intel / kvm_amd laden.'
          : 'Kein /dev/kvm: im BIOS/UEFI Intel VT-x bzw. AMD-V einschalten. Ohne KVM laufen VMs nur emuliert (sehr langsam).']);
    }
    lines.forEach(([level, text]) => {
      const p = el('p', 'vm-banner-line');
      p.append(tag(level, level === 'critical' ? 'Fehler' : 'Hinweis'), el('span', '', t(text)));
      box.appendChild(p);
    });
    box.hidden = !lines.length;
  }

  function renderJobs() {
    const box = $('vm-jobs');
    box.innerHTML = '';
    (state.data.jobs || []).forEach(j => {
      const line = el('div', 'vm-job');
      line.append(tag('warning', 'Läuft'), raw('span', '', j.title));
      line.appendChild(button('Protokoll', 'text-btn', () => window.PiTasks && window.PiTasks.open(j.id)));
      box.appendChild(line);
    });
  }

  function ipText(vm) {
    return (vm.ips || []).filter(a => a.family === 'ipv4').map(a => a.address).concat((vm.ips || []).filter(a => a.family === 'ipv6').map(a => a.address)).slice(0, 2).join(', ');
  }

  function renderTable() {
    const d = state.data;
    const table = $('vm-table'), hint = $('vm-hint');
    hint.textContent = '';
    if (!d.status.connected) { table.innerHTML = ''; hint.textContent = t('Noch keine Verbindung zu libvirt.'); return; }
    const q = $('vm-q').value.trim().toLowerCase();
    const items = d.vms.filter(v => !q || [v.name, v.title, v.os, ipText(v)].some(x => (x || '').toLowerCase().includes(q)));
    const body = head(table, ['VM', 'Status', 'CPU', 'Arbeitsspeicher', 'IP-Adresse', 'Laufwerk', '']);
    items.forEach(v => {
      const row = body.insertRow();
      row.className = 'vm-row';
      row.tabIndex = 0;
      row.addEventListener('click', () => openDetail(v.name));
      row.addEventListener('keydown', e => { if (e.key === 'Enter') openDetail(v.name); });
      nameCell(row, v.name, [v.title, v.os, `${v.vcpus} vCPU`].filter(Boolean).join(' · '));
      const st = row.insertCell();
      st.appendChild(tag(v.level, v.state_label));
      if (v.autostart) st.appendChild(el('span', 'vm-flag', t('Autostart')));
      const cpu = row.insertCell();
      cpu.className = 'mono';
      cpu.textContent = v.cpu == null ? '–' : `${fmtNum(v.cpu)} %`;
      const mem = row.insertCell();
      mem.className = 'vm-mem';
      mem.appendChild(raw('span', 'mono', v.memory_used_mb != null ? `${fmtMb(v.memory_used_mb)} / ${fmtMb(v.memory_mb)}` : fmtMb(v.memory_mb)));
      if (v.memory_used_mb != null) mem.appendChild(meter(v.memory_used_mb, v.memory_mb));
      const ip = row.insertCell();
      ip.className = 'mono wrap';
      ip.setAttribute('data-no-i18n', '');
      ip.textContent = v.state === 'running' ? (ipText(v) || (v.agent ? t('wartet …') : '–')) : '–';
      const disk = row.insertCell();
      disk.className = 'mono';
      disk.textContent = v.disk_bytes ? fmtBytes(v.disk_bytes) : (v.disks ? `${v.disks}×` : '–');
      const actions = row.insertCell();
      actions.className = 'row-action ctr-actions';
      powerIcons(v).forEach(b => actions.appendChild(b));
    });
    if (!items.length) emptyRow(table, 7, d.vms.length ? 'Keine Treffer.' : 'Noch keine VMs – oben rechts „VM erstellen“.');
  }

  function powerIcons(v) {
    const out = [];
    const act = (a, b) => run(b, `${vmPath(v.name)}/${a}`, {}, () => setTimeout(load, 1200));
    if (v.state === 'running' && (v.vnc || v.serial)) out.push(iconBtn('console', 'Konsole', () => openConsole(v.name), 'vms.manage'));
    if (v.state === 'shutoff' || v.state === 'crashed') out.push(iconBtn('start', 'Starten', b => act('start', b), 'vms.control'));
    if (v.state === 'paused') out.push(iconBtn('start', 'Fortsetzen', b => act('resume', b), 'vms.control'));
    if (v.state === 'running') {
      out.push(iconBtn('restart', 'Neu starten', b => act('reboot', b), 'vms.control', 'Neu starten?'));
      out.push(iconBtn('stop', 'Herunterfahren', b => act('shutdown', b), 'vms.control', 'Herunterfahren?', true));
    }
    out.push(iconBtn('details', 'Details', () => openDetail(v.name)));
    return out;
  }

  function openConsole(name) {
    window.open(`/vm-konsole?name=${enc(name)}`, '_blank', 'noopener');
  }

  // ---------------------------------------------------------------------------
  // Setup checks
  // ---------------------------------------------------------------------------
  function renderChecks() {
    const st = state.data.status;
    const box = $('vm-checks');
    box.innerHTML = '';
    const check = (ok, label, detail, warnOnly) => {
      const line = el('div', 'vm-check');
      line.append(tag(ok ? 'good' : warnOnly ? 'warning' : 'critical', ok ? 'OK' : warnOnly ? 'Hinweis' : 'Fehlt'), el('strong', '', t(label)));
      if (detail) line.appendChild(raw('span', 'lan-sub', t(detail)));
      box.appendChild(line);
    };
    check(st.kvm, 'Hardware-Virtualisierung (/dev/kvm)', st.kvm ? (st.kvm_access ? 'verfügbar' : 'vorhanden, aber für den Dashboard-Benutzer noch nicht freigegeben') :
      st.host_virt ? `Host ist selbst eine VM (${st.host_virt}) – verschachtelte Virtualisierung aktivieren` : 'VT-x / AMD-V im BIOS einschalten', true);
    check(!st.missing.length, 'Pakete', st.missing.length ? st.missing.join(', ') : `QEMU, libvirt, virt-install (${st.arch})`);
    check(st.group.active, 'Gruppe libvirt für den Dashboard-Benutzer', st.group.active ? '' : st.group.member ? 'eingetragen – Dashboard-Neustart nötig' : 'noch nicht eingetragen');
    check(st.connected, 'libvirt erreichbar (qemu:///system)', st.connected ? st.version : st.error);
    check(st.virt_install, 'virt-install', st.virt_install ? '' : 'Paket virtinst');
    $('setup-packages').disabled = !st.missing.length || !st.supported;
    $('setup-access').disabled = !st.virsh || (st.connected && st.group.active);
  }

  async function setupPackages() {
    const btn = $('setup-packages');
    btn.disabled = true;
    $('setup-msg').textContent = t('Pakete werden installiert …');
    try {
      await api('/api/vms/setup/packages', {});
      pollPackages();
    } catch (err) {
      $('setup-msg').textContent = t(err.message);
      btn.disabled = false;
    }
  }

  async function pollPackages() {
    const log = $('setup-log');
    log.hidden = false;
    try {
      const s = await api('/api/packages/status');
      log.textContent = s.log || '';
      log.scrollTop = log.scrollHeight;
      if (s.running) { setTimeout(pollPackages, 2000); return; }
      $('setup-msg').textContent = t(s.state === 'done' ? 'Pakete installiert – jetzt „Zugriff einrichten“.' : 'Installation fehlgeschlagen – siehe Protokoll.');
    } catch (err) {
      $('setup-msg').textContent = t(err.message);
    }
    load();
  }

  async function setupAccess() {
    const btn = $('setup-access');
    btn.disabled = true;
    try {
      const r = await api('/api/vms/setup/access', {});
      $('setup-msg').textContent = t(r.message);
      $('setup-log').hidden = false;
      $('setup-log').textContent = r.output || '';
      // The dashboard restarts for the new group: wait until it answers again
      setTimeout(function wait() {
        fetch('/api/vms').then(res => (res.ok ? res.json() : Promise.reject())).then(d => {
          state.data = d;
          renderAll();
          if (!d.status.connected) setTimeout(wait, 3000);
          else toast(t('Virtualisierung ist eingerichtet.'), 'success');
        }).catch(() => setTimeout(wait, 3000));
      }, 6000);
    } catch (err) {
      $('setup-msg').textContent = t(err.message);
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Images, ISOs, networks, pools
  // ---------------------------------------------------------------------------
  async function loadCatalog() {
    try {
      state.catalog = await api('/api/vms/catalog');
    } catch (err) {
      state.catalog = null;
    }
    renderImages();
  }

  function renderImages() {
    const c = state.catalog;
    const table = $('img-table'), iso = $('iso-table');
    if (!c) { table.innerHTML = ''; iso.innerHTML = ''; return; }
    $('img-arch').textContent = c.arch;
    const body = head(table, ['Image', 'Stand', '']);
    c.images.forEach(img => {
      const row = body.insertRow();
      nameCell(row, img.name, img.url);
      const st = row.insertCell();
      st.appendChild(img.downloaded ? tag('good', 'Heruntergeladen') : tag('', 'Nicht vorhanden'));
      if (img.downloaded) st.appendChild(raw('span', 'ctr-sub', `${fmtBytes(img.downloaded.allocation)} · ${img.downloaded.pool}`));
      const a = row.insertCell();
      a.className = 'row-action';
      a.appendChild(button(img.downloaded ? 'Aktualisieren' : 'Herunterladen', 'btn-secondary', b =>
        run(b, '/api/vms/images/fetch', { id: img.id, pool: $('iso-pool').value || 'default' }, () => setTimeout(loadCatalog, 1500)), 'vms.manage'));
      a.appendChild(button('VM erstellen', 'btn-secondary', () => openCreate(img.id), 'vms.manage'));
    });
    const ib = head(iso, ['ISO', 'Pool', 'Größe', 'Benutzt von', '']);
    c.isos.forEach(v => {
      const row = ib.insertRow();
      nameCell(row, v.name, v.path);
      row.insertCell().appendChild(raw('span', '', v.pool));
      row.insertCell().textContent = fmtBytes(v.capacity);
      row.insertCell().appendChild(raw('span', '', v.used_by.join(', ') || '–'));
      const a = row.insertCell();
      a.className = 'row-action';
      if (!v.used_by.length) a.appendChild(confirmButton('Löschen', 'Wirklich löschen?', 'text-btn danger', b =>
        run(b, `/api/vms/pool/${enc(v.pool)}/volumes/${enc(v.name)}/delete`, {}, loadCatalog), 'vms.manage'));
    });
    if (!c.isos.length) emptyRow(iso, 5, 'Noch keine ISOs.');
  }

  async function loadNets() {
    try {
      state.nets = await api('/api/vms/networks');
    } catch (err) {
      state.nets = null;
      $('net-table').innerHTML = '';
      return;
    }
    const table = $('net-table');
    const body = head(table, ['Netzwerk', 'Modus', 'Adressen', 'Status', '']);
    const MODES = { nat: 'NAT', isolated: 'Isoliert', route: 'Geroutet', bridge: 'Bridge', open: 'Offen', hostdev: 'SR-IOV' };
    state.nets.networks.forEach(n => {
      const row = body.insertRow();
      nameCell(row, n.name, n.bridge ? `Bridge ${n.bridge}` : '');
      row.insertCell().textContent = t(MODES[n.mode] || n.mode);
      const ad = row.insertCell();
      ad.className = 'mono wrap';
      ad.appendChild(raw('span', '', n.ip || '–'));
      if (n.dhcp) ad.appendChild(raw('span', 'ctr-sub', `DHCP ${n.dhcp}`));
      const st = row.insertCell();
      st.appendChild(tag(n.active ? 'good' : '', n.active ? 'Aktiv' : 'Inaktiv'));
      if (n.autostart) st.appendChild(el('span', 'vm-flag', t('Autostart')));
      const a = row.insertCell();
      a.className = 'row-action';
      const act = (x, b) => run(b, `/api/vms/net/${enc(n.name)}/${x}`, {}, loadNets);
      if (n.active) a.appendChild(confirmButton('Stoppen', 'VMs verlieren das Netz!', 'text-btn', b => act('stop', b), 'vms.manage'));
      else a.appendChild(button('Starten', 'text-btn', b => act('start', b), 'vms.manage'));
      a.appendChild(button(n.autostart ? 'Autostart aus' : 'Autostart an', 'text-btn', b => act(n.autostart ? 'noautostart' : 'autostart', b), 'vms.manage'));
      a.appendChild(confirmButton('Löschen', 'Wirklich löschen?', 'text-btn danger', b => act('delete', b), 'vms.manage'));
    });
    if (!state.nets.networks.length) emptyRow(table, 5, 'Keine virtuellen Netzwerke.');
  }

  async function loadPools() {
    try {
      state.pools = await api('/api/vms/pools');
    } catch (err) {
      state.pools = null;
      $('pool-table').innerHTML = '';
      return;
    }
    fillPoolSelects();
    const table = $('pool-table');
    const body = head(table, ['Pool', 'Ordner', 'Belegt', 'Status', '']);
    state.pools.pools.forEach(p => {
      const row = body.insertRow();
      row.className = 'vm-row';
      row.addEventListener('click', () => { state.openPools.has(p.name) ? state.openPools.delete(p.name) : state.openPools.add(p.name); loadPools(); });
      nameCell(row, p.name, `${p.type} · ${p.volumes.length} ${t('Volumes')}`);
      row.insertCell().appendChild(raw('span', 'mono', p.path || '–'));
      const use = row.insertCell();
      use.className = 'vm-mem';
      use.appendChild(raw('span', 'mono', p.capacity ? `${fmtBytes(p.allocation)} / ${fmtBytes(p.capacity)}` : '–'));
      if (p.capacity) use.appendChild(meter(p.allocation, p.capacity));
      const st = row.insertCell();
      st.appendChild(tag(p.active ? 'good' : '', p.active ? 'Aktiv' : 'Inaktiv'));
      if (p.autostart) st.appendChild(el('span', 'vm-flag', t('Autostart')));
      const a = row.insertCell();
      a.className = 'row-action';
      const act = (x, b) => run(b, `/api/vms/pool/${enc(p.name)}/${x}`, {}, loadPools);
      if (p.active) {
        a.appendChild(button('Neu einlesen', 'text-btn', b => act('refresh', b), 'vms.manage'));
        a.appendChild(confirmButton('Stoppen', 'Wirklich stoppen?', 'text-btn', b => act('stop', b), 'vms.manage'));
      } else a.appendChild(button('Starten', 'text-btn', b => act('start', b), 'vms.manage'));
      a.appendChild(button(p.autostart ? 'Autostart aus' : 'Autostart an', 'text-btn', b => act(p.autostart ? 'noautostart' : 'autostart', b), 'vms.manage'));
      a.appendChild(confirmButton('Entfernen', 'Dateien bleiben – entfernen?', 'text-btn danger', b => act('delete', b), 'vms.manage'));
      if (state.openPools.has(p.name)) {
        const sub = body.insertRow().insertCell();
        sub.colSpan = 5;
        sub.className = 'vm-subtable';
        const vt = el('table', 'data-table');
        const vb = head(vt, ['Volume', 'Größe', 'Belegt', 'Benutzt von', '']);
        p.volumes.forEach(v => {
          const r = vb.insertRow();
          nameCell(r, v.name, v.base ? t('Cloud-Image (Vorlage)') : v.iso ? 'ISO' : v.type);
          r.insertCell().textContent = fmtBytes(v.capacity);
          r.insertCell().textContent = fmtBytes(v.allocation);
          r.insertCell().appendChild(raw('span', '', v.used_by.join(', ') || '–'));
          const va = r.insertCell();
          va.className = 'row-action';
          if (!v.used_by.length) va.appendChild(confirmButton('Löschen', 'Wirklich löschen?', 'text-btn danger', b =>
            run(b, `/api/vms/pool/${enc(p.name)}/volumes/${enc(v.name)}/delete`, {}, loadPools), 'vms.manage'));
        });
        if (!p.volumes.length) emptyRow(vt, 5, p.active ? 'Leer.' : 'Pool ist gestoppt.');
        sub.appendChild(vt);
      }
    });
    if (!state.pools.pools.length) emptyRow(table, 5, 'Keine Speicher-Pools – unter „Einrichtung“ wird „default“ angelegt.');
  }

  function fillPoolSelects() {
    const pools = (state.pools && state.pools.pools.filter(p => p.active)) || [];
    document.querySelectorAll('.vm-pool-select').forEach(sel => {
      const cur = sel.value || 'default';
      sel.innerHTML = '';
      pools.forEach(p => {
        const o = raw('option', '', `${p.name}${p.available ? ` (${fmtBytes(p.available)} ${t('frei')})` : ''}`);
        o.value = p.name;
        sel.appendChild(o);
      });
      if (pools.some(p => p.name === cur)) sel.value = cur;
    });
  }

  function networkOptions(sel, current) {
    sel.innerHTML = '';
    const add = (value, label) => { const o = raw('option', '', t(label)); o.value = value; sel.appendChild(o); };
    const n = state.nets || { networks: [], host_interfaces: { bridges: [], nics: [] } };
    n.networks.filter(x => x.active).forEach(x => add(`network:${x.name}`, `${x.mode === 'isolated' ? 'Isoliert' : 'NAT'}: ${x.name}${x.ip ? ` (${x.ip})` : ''}`));
    n.host_interfaces.bridges.forEach(b => add(`bridge:${b}`, `Bridge ${b} (eigene Adresse im Netz)`));
    n.host_interfaces.nics.forEach(d => add(`direct:${d}`, `Direkt an ${d} (macvtap)`));
    add('none', 'Kein Netzwerk');
    if (current && [...sel.options].some(o => o.value === current)) sel.value = current;
  }

  // ---------------------------------------------------------------------------
  // Details sheet
  // ---------------------------------------------------------------------------
  function openModal(id) {
    const m = $(id);
    m.classList.add('open');
    m.setAttribute('aria-hidden', 'false');
  }

  function closeModal(id) {
    const m = $(id);
    m.classList.remove('open');
    m.setAttribute('aria-hidden', 'true');
  }

  async function openDetail(name) {
    state.tab = state.detail && state.detail.name === name ? state.tab : 'overview';
    $('vm-modal-title').textContent = name;
    $('vm-modal-sub').textContent = t('Lädt …');
    $('vm-tab-body').innerHTML = '';
    $('vm-power').innerHTML = '';
    openModal('vm-modal');
    await refreshDetail(name);
    if (!state.nets) loadNets();
    if (!state.pools) loadPools();
    if (!state.catalog) loadCatalog();
  }

  async function refreshDetail(name) {
    try {
      state.detail = await api(vmPath(name));
    } catch (err) {
      $('vm-modal-sub').textContent = t(err.message);
      return;
    }
    renderDetail();
  }

  function renderDetail() {
    const d = state.detail;
    $('vm-modal-title').textContent = d.title ? `${d.name} – ${d.title}` : d.name;
    $('vm-modal-sub').textContent = [d.os, d.arch, d.machine, d.firmware === 'efi' ? 'UEFI' : 'BIOS'].filter(Boolean).join(' · ');
    const power = $('vm-power');
    power.innerHTML = '';
    power.appendChild(tag(d.level, d.state_label));
    const act = (a, b) => run(b, `${vmPath(d.name)}/${a}`, {}, () => setTimeout(() => { refreshDetail(d.name); load(); }, 1200));
    if (d.state === 'shutoff' || d.state === 'crashed') power.appendChild(button('Starten', 'btn-primary', b => act('start', b), 'vms.control'));
    if (d.state === 'running') {
      power.appendChild(button('Herunterfahren', 'btn-secondary', b => act('shutdown', b), 'vms.control'));
      power.appendChild(button('Neu starten', 'btn-secondary', b => act('reboot', b), 'vms.control'));
      power.appendChild(button('Anhalten', 'btn-secondary', b => act('suspend', b), 'vms.control'));
      power.appendChild(confirmButton('Zurücksetzen', 'Hart neu starten?', 'btn-secondary', b => act('reset', b), 'vms.manage'));
    }
    if (d.state === 'paused') power.appendChild(button('Fortsetzen', 'btn-primary', b => act('resume', b), 'vms.control'));
    if (['running', 'paused', 'shutdown', 'crashed', 'pmsuspended'].includes(d.state)) {
      power.appendChild(confirmButton('Ausschalten', 'Wie Stecker ziehen – sicher?', 'btn-secondary danger', b => act('forceoff', b), 'vms.manage'));
    }
    if (d.console.vnc || d.console.serial) power.appendChild(button('Konsole', 'btn-secondary', () => openConsole(d.name), 'vms.manage'));
    (d.jobs || []).forEach(j => power.appendChild(button(`${t('Aufgabe läuft:')} ${j.title}`, 'text-btn', () => window.PiTasks && window.PiTasks.open(j.id))));
    document.querySelectorAll('#vm-tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === state.tab));
    const body = $('vm-tab-body');
    body.innerHTML = '';
    ({ overview: tabOverview, disks: tabDisks, nics: tabNics, snapshots: tabSnapshots })[state.tab](body, d);
  }

  function infoGrid(pairs) {
    const dl = el('dl', 'vm-info');
    pairs.forEach(([k, v, rawValue]) => {
      dl.appendChild(el('dt', '', t(k)));
      dl.appendChild(rawValue ? raw('dd', '', v) : el('dd', '', v));
    });
    return dl;
  }

  function field(label, input, hint) {
    const l = el('label', 'login-field');
    const s = el('span', '', t(label));
    if (hint) s.appendChild(el('em', '', ` (${t(hint)})`));
    l.append(s, input);
    return l;
  }

  function input(type, value, attrs = {}) {
    const i = el(type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') i.type = type;
    i.value = value == null ? '' : value;
    Object.entries(attrs).forEach(([k, v]) => i.setAttribute(k, v));
    return i;
  }

  function tabOverview(body, d) {
    const agent = d.agent === 'connected' ? 'verbunden' : d.agent === 'disconnected' ? 'eingerichtet, antwortet nicht' : d.agent ? 'eingerichtet' : 'nicht eingerichtet';
    body.appendChild(infoGrid([
      ['vCPUs', String(d.vcpus)], ['Arbeitsspeicher', fmtMb(d.memory_mb)],
      ['IP-Adressen', (d.ips || []).map(a => a.address).join(', ') || '–', true],
      ['Gast-Agent', t(agent)], ['CPU-Modell', d.cpu_mode || '–', true], ['Virtualisierung', d.virt_type === 'kvm' ? 'KVM' : t('Emuliert (QEMU)')],
      ['UUID', d.uuid, true]
    ]));
    const auto = el('label', 'toggle-label');
    const cb = input('checkbox', '');
    cb.checked = d.autostart;
    cb.disabled = !can('vms.manage');
    cb.addEventListener('change', () => run(null, `${vmPath(d.name)}/autostart`, { enabled: cb.checked }, () => load()));
    auto.append(cb, document.createTextNode(` ${t('Mit dem Host starten (Autostart)')}`));
    body.appendChild(auto);

    if (!can('vms.manage')) {
      if (d.description) body.appendChild(raw('p', 'vm-desc', d.description));
      return;
    }
    const off = d.state === 'shutoff';
    const form = el('form', 'vm-inline-form');
    form.appendChild(el('h3', '', t('Einstellungen')));
    const grid = el('div', 'form-grid');
    const vcpus = input('number', d.vcpus, { min: 1 });
    const mem = input('number', d.memory_mb, { min: 128, step: 128 });
    vcpus.disabled = mem.disabled = !off;
    const title = input('text', d.title, { maxlength: 120 });
    const desc = input('textarea', d.description, { rows: 3, maxlength: 4000 });
    grid.append(field('vCPUs', vcpus, off ? '' : 'nur ausgeschaltet'), field('Arbeitsspeicher (MB)', mem, off ? '' : 'nur ausgeschaltet'), field('Titel', title));
    const dl = field('Beschreibung', desc, 'Zweck, Ansprechpartner, Besonderheiten');
    dl.classList.add('vm-span-2');
    grid.appendChild(dl);
    form.appendChild(grid);
    const actions = el('div', 'form-actions');
    const save = el('button', 'btn-primary', t('Speichern'));
    save.type = 'submit';
    actions.appendChild(save);
    form.appendChild(actions);
    form.addEventListener('submit', e => {
      e.preventDefault();
      run(save, `${vmPath(d.name)}/settings`, {
        vcpus: off ? Number(vcpus.value) : null, memory_mb: off ? Number(mem.value) : null, title: title.value, description: desc.value
      }, () => { refreshDetail(d.name); load(); });
    });
    body.appendChild(form);

    const more = el('div', 'vm-inline-form');
    more.appendChild(el('h3', '', t('Verwalten')));
    const row = el('div', 'vm-manage-row');
    const newName = input('text', '', { placeholder: t('Neuer Name'), spellcheck: 'false', 'aria-label': t('Neuer Name') });
    row.append(newName,
      button('Umbenennen', 'btn-secondary', b => {
        if (!off) return toast(t('Umbenennen geht nur bei ausgeschalteter VM.'), 'error');
        run(b, `${vmPath(d.name)}/rename`, { name: newName.value.trim() }, () => { closeModal('vm-modal'); load(); });
      }),
      button('Klonen', 'btn-secondary', b => {
        if (!off) return toast(t('Klonen geht nur bei ausgeschalteter VM.'), 'error');
        run(b, `${vmPath(d.name)}/clone`, { name: newName.value.trim() }, () => load());
      }));
    more.appendChild(row);
    const del = el('div', 'vm-manage-row');
    const withDisks = el('label', 'toggle-label');
    const wd = input('checkbox', '');
    withDisks.append(wd, document.createTextNode(` ${t('Laufwerke mitlöschen (ISOs bleiben)')}`));
    del.append(withDisks, confirmButton('VM löschen', 'Endgültig löschen?', 'btn-secondary danger', b =>
      run(b, `${vmPath(d.name)}/delete`, { enabled: wd.checked }, () => { closeModal('vm-modal'); load(); loadPools(); })));
    more.appendChild(del);
    body.appendChild(more);
  }

  function tabDisks(body, d) {
    const table = el('table', 'data-table');
    const tb = head(table, ['Laufwerk', 'Quelle', 'Größe', '']);
    d.disks.forEach(disk => {
      const row = tb.insertRow();
      nameCell(row, disk.target, `${disk.device === 'cdrom' ? 'CD/DVD' : t('Festplatte')} · ${disk.bus}${disk.format ? ` · ${disk.format}` : ''}${disk.readonly ? ` · ${t('nur lesen')}` : ''}`);
      const src = row.insertCell();
      src.className = 'mono wrap';
      src.appendChild(raw('span', '', disk.source || (disk.device === 'cdrom' ? t('leer') : '–')));
      row.insertCell().textContent = disk.capacity ? `${fmtBytes(disk.capacity)}${disk.allocation ? ` (${fmtBytes(disk.allocation)} ${t('belegt')})` : ''}` : '–';
      const a = row.insertCell();
      a.className = 'row-action';
      if (disk.device === 'cdrom' && disk.source) a.appendChild(button('Auswerfen', 'text-btn', b => run(b, `${vmPath(d.name)}/media`, { path: '' }, () => refreshDetail(d.name)), 'vms.manage'));
      a.appendChild(confirmButton('Entfernen', 'Entfernen?', 'text-btn', b => run(b, `${vmPath(d.name)}/disks/${enc(disk.target)}/detach`, { enabled: false }, () => refreshDetail(d.name)), 'vms.manage'));
      if (disk.device === 'disk') a.appendChild(confirmButton('Löschen', 'Daten weg – sicher?', 'text-btn danger', b => run(b, `${vmPath(d.name)}/disks/${enc(disk.target)}/detach`, { enabled: true }, () => { refreshDetail(d.name); loadPools(); }), 'vms.manage'));
    });
    if (!d.disks.length) emptyRow(table, 4, 'Keine Laufwerke.');
    body.appendChild(el('div', 'table-scroll')).appendChild(table);
    if (!can('vms.manage')) return;

    const add = el('form', 'vm-inline-form');
    add.appendChild(el('h3', '', t('Laufwerk hinzufügen')));
    const grid = el('div', 'form-grid');
    const pool = el('select', 'vm-pool-select');
    const size = input('number', 20, { min: 1, max: 16384 });
    const bus = el('select');
    [['virtio', 'VirtIO (schnell)'], ['scsi', 'SCSI'], ['sata', 'SATA (für Windows ohne Treiber)']].forEach(([v, l]) => { const o = el('option', '', t(l)); o.value = v; bus.appendChild(o); });
    grid.append(field('Speicher-Pool', pool), field('Größe (GB)', size), field('Bus', bus));
    add.appendChild(grid);
    const go = el('button', 'btn-secondary', t('Hinzufügen'));
    go.type = 'submit';
    const fa = el('div', 'form-actions');
    fa.appendChild(go);
    add.appendChild(fa);
    add.addEventListener('submit', e => { e.preventDefault(); run(go, `${vmPath(d.name)}/disks`, { pool: pool.value, size_gb: Number(size.value), bus: bus.value }, () => { refreshDetail(d.name); loadPools(); }); });
    body.appendChild(add);
    fillPoolSelects();

    const media = el('form', 'vm-inline-form');
    media.appendChild(el('h3', '', t('ISO einlegen')));
    const iso = el('select');
    ((state.catalog && state.catalog.isos) || []).forEach(v => { const o = raw('option', '', `${v.name} (${v.pool})`); o.value = v.path; iso.appendChild(o); });
    const row = el('div', 'vm-manage-row');
    const ins = el('button', 'btn-secondary', t('Einlegen'));
    ins.type = 'submit';
    ins.disabled = !iso.options.length;
    row.append(iso, ins);
    media.appendChild(row);
    if (!iso.options.length) media.appendChild(el('p', 'field-hint', t('Noch keine ISOs – unter „Images & ISOs“ herunterladen.')));
    media.addEventListener('submit', e => { e.preventDefault(); run(ins, `${vmPath(d.name)}/media`, { path: iso.value }, () => refreshDetail(d.name)); });
    body.appendChild(media);
  }

  function tabNics(body, d) {
    const table = el('table', 'data-table');
    const tb = head(table, ['Netzwerkkarte', 'Verbunden mit', 'IP-Adressen', 'Kabel', '']);
    d.interfaces.forEach(n => {
      const row = tb.insertRow();
      nameCell(row, n.mac, `${n.model || '–'}${n.dev ? ` · ${n.dev}` : ''}`);
      row.insertCell().appendChild(raw('span', '', `${n.type === 'network' ? t('Netzwerk') : n.type === 'bridge' ? 'Bridge' : n.type === 'direct' ? t('Direkt') : n.type} ${n.source}`));
      const ips = row.insertCell();
      ips.className = 'mono wrap';
      ips.appendChild(raw('span', '', (d.ips || []).filter(a => (a.mac || '').toLowerCase() === n.mac.toLowerCase()).map(a => a.address).join(', ') || '–'));
      const link = row.insertCell();
      link.appendChild(tag(n.link === 'down' ? 'warning' : 'good', n.link === 'down' ? 'Gezogen' : 'Eingesteckt'));
      const a = row.insertCell();
      a.className = 'row-action';
      a.appendChild(button(n.link === 'down' ? 'Einstecken' : 'Kabel ziehen', 'text-btn', b =>
        run(b, `${vmPath(d.name)}/interfaces/${enc(n.mac)}/${n.link === 'down' ? 'up' : 'down'}`, {}, () => refreshDetail(d.name)), 'vms.manage'));
      a.appendChild(confirmButton('Entfernen', 'Entfernen?', 'text-btn danger', b =>
        run(b, `${vmPath(d.name)}/interfaces/${enc(n.mac)}/delete`, {}, () => refreshDetail(d.name)), 'vms.manage'));
    });
    if (!d.interfaces.length) emptyRow(table, 5, 'Keine Netzwerkkarten.');
    body.appendChild(el('div', 'table-scroll')).appendChild(table);
    if (!can('vms.manage')) return;
    const add = el('form', 'vm-inline-form');
    add.appendChild(el('h3', '', t('Netzwerkkarte hinzufügen')));
    const grid = el('div', 'form-grid');
    const src = el('select');
    networkOptions(src, 'network:default');
    [...src.options].filter(o => o.value === 'none').forEach(o => o.remove());
    const model = el('select');
    [['virtio', 'VirtIO (schnell)'], ['e1000e', 'Intel e1000e (für Windows ohne Treiber)'], ['rtl8139', 'Realtek RTL8139']].forEach(([v, l]) => { const o = el('option', '', t(l)); o.value = v; model.appendChild(o); });
    grid.append(field('Verbinden mit', src), field('Modell', model));
    add.appendChild(grid);
    const go = el('button', 'btn-secondary', t('Hinzufügen'));
    go.type = 'submit';
    const fa = el('div', 'form-actions');
    fa.appendChild(go);
    add.appendChild(fa);
    add.addEventListener('submit', e => {
      e.preventDefault();
      const [kind, source] = src.value.split(/:(.*)/);
      run(go, `${vmPath(d.name)}/interfaces`, { kind, source, model: model.value }, () => refreshDetail(d.name));
    });
    body.appendChild(add);
  }

  function tabSnapshots(body, d) {
    body.appendChild(el('p', 'field-hint', t('Ein Snapshot einer laufenden VM enthält auch den Arbeitsspeicher – Zurücksetzen bringt sie genau in diesen Moment zurück. Snapshots ersetzen keine Sicherung: sie liegen auf demselben Laufwerk.')));
    const table = el('table', 'data-table');
    const tb = head(table, ['Snapshot', 'Erstellt', 'Status', '']);
    d.snapshots.slice().reverse().forEach(s => {
      const row = tb.insertRow();
      nameCell(row, s.name + (s.current ? ` (${t('aktuell')})` : ''), s.description);
      row.insertCell().appendChild(raw('span', 'mono', s.created));
      row.insertCell().textContent = t(s.state === 'running' ? 'Lief (mit Arbeitsspeicher)' : s.state === 'shutoff' ? 'Ausgeschaltet' : s.state);
      const a = row.insertCell();
      a.className = 'row-action';
      a.appendChild(confirmButton('Zurücksetzen', 'Spätere Änderungen gehen verloren!', 'text-btn', b =>
        run(b, `${vmPath(d.name)}/snapshots/${enc(s.name)}/revert`, {}, () => { refreshDetail(d.name); load(); }), 'vms.manage'));
      a.appendChild(confirmButton('Löschen', 'Wirklich löschen?', 'text-btn danger', b =>
        run(b, `${vmPath(d.name)}/snapshots/${enc(s.name)}/delete`, {}, () => refreshDetail(d.name)), 'vms.manage'));
    });
    if (!d.snapshots.length) emptyRow(table, 4, 'Noch keine Snapshots.');
    body.appendChild(el('div', 'table-scroll')).appendChild(table);
    if (!can('vms.manage')) return;
    const add = el('form', 'vm-inline-form');
    add.appendChild(el('h3', '', t('Snapshot erstellen')));
    const grid = el('div', 'form-grid');
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const name = input('text', `snap-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`, { spellcheck: 'false' });
    const desc = input('text', '', { maxlength: 1000, placeholder: t('z. B. vor dem Update auf …') });
    const dl = field('Beschreibung', desc);
    dl.classList.add('vm-span-2');
    grid.append(field('Name', name), dl);
    add.appendChild(grid);
    const go = el('button', 'btn-secondary', t('Erstellen'));
    go.type = 'submit';
    const fa = el('div', 'form-actions');
    fa.appendChild(go);
    add.appendChild(fa);
    add.addEventListener('submit', e => { e.preventDefault(); run(go, `${vmPath(d.name)}/snapshots`, { name: name.value.trim(), description: desc.value }, () => refreshDetail(d.name)); });
    body.appendChild(add);
  }

  // ---------------------------------------------------------------------------
  // Create dialog
  // ---------------------------------------------------------------------------
  async function openCreate(imageId) {
    state.createPick = imageId || state.createPick || '';
    $('create-msg').textContent = '';
    openModal('create-modal');
    await Promise.all([loadCatalog(), state.nets ? null : loadNets(), state.pools ? null : loadPools()]);
    fillCreate();
    setTimeout(() => $('c-name').focus(), 50);
  }

  function fillCreate() {
    const c = state.catalog || { images: [], isos: [], arch: '' };
    const img = $('c-image');
    img.innerHTML = '';
    c.images.forEach(i => { const o = raw('option', '', `${i.name}${i.downloaded ? '' : ` – ${t('wird zuerst heruntergeladen')}`}`); o.value = i.id; o.dataset.user = i.user; img.appendChild(o); });
    const known = new Set(c.images.map(i => i.volume));
    ((state.pools && state.pools.pools) || []).forEach(p => p.volumes.filter(v => v.base && !known.has(v.name)).forEach(v => {
      const o = raw('option', '', `${v.name} (${t('eigenes Image')})`);
      o.value = v.name;
      img.appendChild(o);
    }));
    if (state.createPick && [...img.options].some(o => o.value === state.createPick)) img.value = state.createPick;
    const iso = $('c-iso');
    iso.innerHTML = '';
    c.isos.forEach(v => { const o = raw('option', '', `${v.name} (${v.pool})`); o.value = v.path; iso.appendChild(o); });
    fillVolumes();
    networkOptions($('c-network'), $('c-network').value || 'network:default');
    const host = state.data ? state.data.host : { cpus: 4, memory_mb: 4096 };
    $('c-vcpus').max = host.cpus;
    $('c-memory').max = host.memory_mb;
    $('c-firmware-wrap').hidden = c.arch === 'arm64';
    $('c-hint').textContent = state.data && !state.data.status.kvm ? t('Ohne KVM läuft die VM emuliert – sehr langsam.') : '';
    syncSource();
  }

  function fillVolumes() {
    const sel = $('c-volume');
    sel.innerHTML = '';
    const pool = ((state.pools && state.pools.pools) || []).find(p => p.name === $('c-pool').value);
    (pool ? pool.volumes : []).filter(v => !v.iso && !v.base && !v.used_by.length).forEach(v => {
      const o = raw('option', '', `${v.name} (${fmtBytes(v.capacity)})`);
      o.value = v.name;
      sel.appendChild(o);
    });
  }

  function syncSource() {
    const src = $('c-source').value;
    document.querySelectorAll('#create-form .vm-src').forEach(e => { e.hidden = !e.classList.contains(`vm-src-${src}`); });
    $('c-disk').required = src !== 'disk';
    const opt = $('c-image').selectedOptions[0];
    if (src === 'cloud' && opt && opt.dataset.user && (!$('c-user').value || $('c-user').dataset.auto === '1')) {
      $('c-user').value = opt.dataset.user;
      $('c-user').dataset.auto = '1';
    }
  }

  async function submitCreate(e) {
    e.preventDefault();
    const btn = $('create-submit');
    const body = {
      name: $('c-name').value.trim(), source: $('c-source').value, vcpus: Number($('c-vcpus').value), memory_mb: Number($('c-memory').value),
      disk_gb: Number($('c-disk').value), pool: $('c-pool').value || 'default', network: $('c-network').value, firmware: $('c-firmware').value,
      autostart: $('c-autostart').checked, description: $('c-description').value.trim(),
      image: $('c-image').value, iso: $('c-iso').value, volume: $('c-volume').value, osinfo: $('c-osinfo').value.trim(),
      user: $('c-user').value.trim(), hostname: $('c-hostname').value.trim(), password: $('c-password').value, ssh_keys: $('c-keys').value,
      agent: $('c-agent').checked, upgrade: $('c-upgrade').checked, ssh_password: $('c-sshpw').checked
    };
    btn.disabled = true;
    $('create-msg').textContent = '';
    try {
      const task = await api('/api/vms/create', body);
      $('c-password').value = '';
      closeModal('create-modal');
      toast(t('VM wird erstellt – Fortschritt in der Aufgabenleiste.'), 'success');
      if (window.PiTasks) window.PiTasks.open(task.id);
      load();
    } catch (err) {
      $('create-msg').textContent = t(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------
  function renderAll() {
    renderBanner();
    renderStats();
    renderTable();
    renderJobs();
    renderChecks();
    const when = new Date().toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    $('vm-updated').textContent = `${t('Stand')} ${when}`;
    $('vm-create').disabled = !state.data.status.ready;
  }

  async function load() {
    try {
      state.data = await api('/api/vms');
    } catch (err) {
      $('vm-hint').textContent = t(err.message);
      return;
    }
    renderAll();
    if (state.data.status.connected && !state.nets) {
      loadNets();
      loadPools();
      loadCatalog();
    }
  }

  function init() {
    $('vm-reload').addEventListener('click', () => { load(); loadNets(); loadPools(); loadCatalog(); });
    $('net-reload').addEventListener('click', loadNets);
    $('pool-reload').addEventListener('click', loadPools);
    $('vm-q').addEventListener('input', () => state.data && renderTable());
    $('vm-create').addEventListener('click', () => openCreate(''));
    $('vm-modal-close').addEventListener('click', () => { closeModal('vm-modal'); state.detail = null; });
    $('create-close').addEventListener('click', () => closeModal('create-modal'));
    ['vm-modal', 'create-modal'].forEach(id => $(id).addEventListener('click', e => { if (e.target === $(id)) closeModal(id); }));
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      ['create-modal', 'vm-modal'].forEach(id => { if ($(id).classList.contains('open')) closeModal(id); });
    });
    $('vm-tabs').addEventListener('click', e => {
      const b = e.target.closest('button[data-tab]');
      if (!b || !state.detail) return;
      state.tab = b.dataset.tab;
      renderDetail();
    });
    $('c-source').addEventListener('change', syncSource);
    $('c-image').addEventListener('change', syncSource);
    $('c-user').addEventListener('input', () => { $('c-user').dataset.auto = '0'; });
    $('c-pool').addEventListener('change', fillVolumes);
    $('create-form').addEventListener('submit', submitCreate);
    $('setup-packages').addEventListener('click', setupPackages);
    $('setup-access').addEventListener('click', setupAccess);

    $('iso-form').addEventListener('submit', async e => {
      e.preventDefault();
      const r = await run(e.submitter, '/api/vms/images/fetch', { url: $('iso-url').value.trim(), sha: $('iso-sha').value.trim(), kind: $('iso-kind').value, pool: $('iso-pool').value || 'default' });
      if (r) { $('iso-url').value = ''; $('iso-sha').value = ''; }
    });
    $('net-form').addEventListener('submit', async e => {
      e.preventDefault();
      const r = await run(e.submitter, '/api/vms/networks', { name: $('net-name').value.trim(), cidr: $('net-cidr').value.trim(), mode: $('net-mode').value, dhcp: $('net-dhcp').checked }, loadNets);
      if (r) $('net-form').reset();
    });
    $('pool-form').addEventListener('submit', async e => {
      e.preventDefault();
      const r = await run(e.submitter, '/api/vms/pools', { name: $('pool-name').value.trim(), path: $('pool-path').value.trim() }, loadPools);
      if (r) $('pool-form').reset();
    });

    // Highlight the section in view
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));

    load();
    setInterval(() => {
      if (document.hidden || $('create-modal').classList.contains('open')) return;
      if ($('vm-modal').classList.contains('open')) {
        // Not while typing in a form or with a button waiting for its confirming second click
        const busy = document.activeElement.closest('#vm-tab-body') || $('vm-modal').querySelector('.confirming');
        if (state.detail && !busy) refreshDetail(state.detail.name);
      } else load();
    }, 5000);
  }

  init();
})();
