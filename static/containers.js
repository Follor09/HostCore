/**
 * PiCore Dashboard - Containers page: Kubernetes, Docker, compose stacks,
 * image updates and per-container statistics.
 */
(function () {
  'use strict';

  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const state = { data: null, stats: null, tab: 'workloads', statHours: 24, statKey: '', jobs: {}, log: null };

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  function toast(message, type = 'info') {
    const n = document.createElement('div');
    n.className = `toast ${type}`;
    n.textContent = message;
    $('toast-container').appendChild(n);
    setTimeout(() => { n.style.transition = 'all 0.3s ease'; n.style.opacity = '0'; setTimeout(() => n.remove(), 300); }, 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (res.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; throw new Error('Nicht angemeldet.'); }
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

  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function fmtBytes(b) {
    if (b == null) return '–';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    const n = b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1);
    return `${String(n).replace('.', LOCALE.startsWith('de') ? ',' : '.')} ${units[i]}`;
  }

  const fmtNum = n => n == null ? '–' : Number(n).toLocaleString(LOCALE, { maximumFractionDigits: 1 });
  const fmtCpu = v => v == null ? '–' : `${fmtNum(v < 10 ? Math.round(v * 10) / 10 : Math.round(v))} %`;

  function fmtAge(s) {
    if (s == null) return '–';
    if (s >= 86400) return Math.floor(s / 86400) === 1 ? '1 Tag' : `${Math.floor(s / 86400)} Tage`;
    if (s >= 3600) return `${Math.floor(s / 3600)} Std.`;
    if (s >= 60) return `${Math.floor(s / 60)} Min.`;
    return `${s} Sek.`;
  }

  const fmtTime = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  const LEVEL = { active: 'good', error: 'critical', warning: 'warning', inactive: '' };

  function tag(level, text, title) {
    const e = el('span', `health-tag ${LEVEL[level] !== undefined ? LEVEL[level] : level}`, text);
    if (title) e.title = title;
    return e;
  }

  function button(label, cls, onClick, perm) {
    const b = el('button', cls, label);
    b.type = 'button';
    if (perm) b.dataset.perm = perm;
    if (onClick) b.addEventListener('click', () => onClick(b));
    return b;
  }

  // Second click within 4 s confirms
  function confirmButton(label, confirmLabel, cls, action, perm) {
    const b = button(label, cls, null, perm);
    b.addEventListener('click', () => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = t(confirmLabel);
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = t(label); }, 4000);
        return;
      }
      b.classList.remove('confirming');
      b.textContent = t(label);
      action(b);
    });
    return b;
  }

  const ICONS = {
    restart: '<path d="M23 4v6h-6"></path><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"></rect>',
    start: '<polygon points="7 4 20 12 7 20 7 4"></polygon>',
    scale: '<path d="M8 20V4M8 4L4 8M8 4l4 4"></path><path d="M16 4v16M16 20l-4-4M16 20l4-4"></path>',
    logs: '<path d="M4 6h16M4 12h16M4 18h10"></path>',
    console: '<polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line>',
    stats: '<polyline points="3 17 9 11 13 15 21 7"></polyline>',
    recreate: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M21 3v5h-5"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path><path d="M3 21v-5h5"></path>'
  };

  // Compact icon button; with confirmLabel the first click only arms it (shows the question)
  function iconBtn(icon, label, onClick, perm, confirmLabel, danger) {
    const b = el('button', `ctr-icon${danger ? ' danger' : ''}`);
    b.type = 'button';
    b.title = t(label);
    b.setAttribute('aria-label', t(label));
    if (perm) b.dataset.perm = perm;
    b.innerHTML = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[icon]}</svg>`;
    const lbl = el('span', 'ctr-icon-label');
    b.appendChild(lbl);
    b.addEventListener('click', () => {
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

  async function act(btn, promise, reload = true) {
    if (btn) btn.disabled = true;
    try {
      const r = await promise;
      if (r && r.message) toast(r.message, 'success');
      if (reload) setTimeout(loadOverview, 1200);
      return r;
    } catch (err) {
      toast(err.message, 'error');
      return null;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function emptyRow(table, cols, text) {
    const c = table.tBodies[0].insertRow().insertCell();
    c.colSpan = cols;
    c.className = 'wrap empty-cell';
    c.textContent = text;
  }

  function head(table, labels) {
    table.innerHTML = '';
    const r = table.createTHead().insertRow();
    labels.forEach(l => r.appendChild(el('th', '', l)));
    table.createTBody();
    return table.tBodies[0];
  }

  function nameCell(row, name, sub) {
    const c = row.insertCell();
    c.className = 'ctr-name';
    c.appendChild(raw('span', 'ctr-main', name));
    if (sub) c.appendChild(raw('span', 'ctr-sub', sub));
    return c;
  }

  const shortImage = img => img.replace(/^(docker\.io\/)?(library\/)?/, '').replace(/@sha256:[0-9a-f]{12}[0-9a-f]*/, '@…');

  // ---------------------------------------------------------------------------
  // Sparkline (24 h CPU, 30-min steps)
  // ---------------------------------------------------------------------------
  const svgNs = 'http://www.w3.org/2000/svg';
  const svgEl = (name, attrs) => { const e = document.createElementNS(svgNs, name); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); return e; };

  function spark(values) {
    const w = 72, h = 20;
    const svg = svgEl('svg', { viewBox: `0 0 ${w} ${h}`, class: 'ctr-spark', role: 'img', 'aria-label': t('CPU der letzten 24 Std.') });
    const pts = values.map((v, i) => [i, v]).filter(p => p[1] != null);
    if (pts.length < 2) return svg;
    const hi = Math.max(5, ...pts.map(p => p[1]));
    const x = i => i / (values.length - 1) * (w - 2) + 1, y = v => h - 2 - v / hi * (h - 4);
    svg.appendChild(svgEl('path', { d: pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(''), fill: 'none', stroke: 'var(--accent-cyan)', 'stroke-width': 1.5, 'stroke-linejoin': 'round' }));
    return svg;
  }

  const statFor = (kind, name) => state.stats && state.stats.items.find(i => i.kind === kind && i.name === name);

  // ---------------------------------------------------------------------------
  // Overview
  // ---------------------------------------------------------------------------
  function statTile(label, value, level) {
    const box = el('div', 'bench-value');
    const v = el('span', `value ${level || ''}`, value);
    box.append(el('span', 'label', label), v);
    return box;
  }

  function renderOverview(d) {
    const box = $('ov-stats');
    box.innerHTML = '';
    const k = d.kube, dk = d.docker;
    if (k.available && !k.error) {
      const running = k.pods.filter(p => p.status === 'Running').length;
      const bad = k.pods.filter(p => p.level === 'error').length;
      box.appendChild(statTile('Pods laufen', `${running}/${k.pods.filter(p => p.status !== 'Succeeded').length}`, bad ? 'critical' : ''));
      const ready = k.workloads.filter(w => w.level === 'active').length;
      box.appendChild(statTile('Workloads bereit', `${ready}/${k.workloads.filter(w => w.desired > 0).length}`, k.workloads.some(w => w.level === 'error') ? 'critical' : ''));
    }
    if (dk.available && !dk.error) {
      box.appendChild(statTile('Docker-Container laufen', `${dk.containers.filter(c => c.state === 'running').length}/${dk.containers.length}`, dk.containers.some(c => c.level === 'error') ? 'critical' : ''));
    }
    if (d.stacks.length) box.appendChild(statTile('Compose-Stacks', String(d.stacks.length)));
    box.appendChild(statTile('Image-Updates', d.images.checked ? String(d.images.updates) : '–', d.images.updates ? 'warning' : ''));
    if (k.available && !k.error) {
      const recent = (k.events || []).filter(e => Date.now() / 1000 - e.ts < 86400).length;
      box.appendChild(statTile('Warnungen (24 Std.)', String(recent), recent ? 'warning' : ''));
    }

    // Problems: whatever is broken right now
    const probs = $('ov-problems');
    probs.innerHTML = '';
    const items = [];
    if (k.available && !k.error) {
      k.workloads.filter(w => w.level === 'error' || w.level === 'warning').forEach(w =>
        items.push([w.level, `${w.kind} ${w.namespace}/${w.name}`, `${w.ready}/${w.desired} ${t('bereit')}${w.message ? ' · ' + w.message : ''}`, '#kubernetes']));
      k.pods.filter(p => p.level === 'error').forEach(p =>
        items.push(['error', `Pod ${p.namespace}/${p.name}`, `${p.status} · ${p.restarts}× ${t('neu gestartet')}${p.oom ? ' · OOMKilled' : ''}`, '#kubernetes']));
    }
    if (dk.available) dk.containers.filter(c => c.level === 'error').forEach(c => items.push(['error', `Docker ${c.name}`, c.status, '#docker']));
    if (!dk.available && !k.available) {
      probs.appendChild(el('p', 'field-hint', 'Weder Docker noch Kubernetes (k3s) gefunden.'));
      return;
    }
    if (!items.length) {
      probs.appendChild(el('p', 'ov-ok', 'Alles läuft – keine Probleme gefunden.'));
      return;
    }
    probs.appendChild(el('h3', 'subhead', 'Probleme'));
    const list = el('div', 'iface-list');
    items.slice(0, 12).forEach(([level, title, sub, href]) => {
      const row = el('a', 'iface-row ov-problem');
      row.href = href;
      const main = el('div', 'workload-main');
      main.append(raw('span', 'workload-name', title), raw('span', 'workload-sub', sub));
      const side = el('div', 'iface-tags');
      side.appendChild(tag(level, level === 'error' ? 'Fehler' : 'Warnung'));
      row.append(main, side);
      list.appendChild(row);
    });
    probs.appendChild(list);
  }

  // ---------------------------------------------------------------------------
  // Kubernetes
  // ---------------------------------------------------------------------------
  function k8sFilter(items, fields) {
    const ns = $('k8s-ns').value, q = $('k8s-q').value.trim().toLowerCase();
    const sys = $('k8s-system').checked;
    return items.filter(i => (!ns || i.namespace === ns) && (ns || sys || !i.namespace || i.namespace !== 'kube-system')
      && (!q || fields(i).some(v => String(v || '').toLowerCase().includes(q))));
  }

  function renderKube() {
    const k = state.data.kube;
    const body = $('k8s-body'), hint = $('k8s-hint');
    hint.textContent = '';
    if (!k.available) {
      body.hidden = true;
      $('k8s-tabs').hidden = true;
      hint.textContent = t('Kein Kubernetes gefunden (kubectl / k3s).');
      return;
    }
    if (k.error) {
      body.hidden = true;
      $('k8s-tabs').hidden = true;
      hint.textContent = `${k.error}${k.hint ? ' – ' + k.hint : ''}`;
      return;
    }
    body.hidden = false;
    $('k8s-tabs').hidden = false;
    const sel = $('k8s-ns');
    const keep = sel.value;
    if (sel.options.length - 1 !== k.namespaces.length) {
      sel.innerHTML = '';
      sel.appendChild(Object.assign(el('option', '', t('Alle Namespaces')), { value: '' }));
      k.namespaces.forEach(ns => { const o = raw('option', '', ns); o.value = ns; sel.appendChild(o); });
      sel.value = k.namespaces.includes(keep) ? keep : '';
    }
    const table = $('k8s-table');
    ({ workloads: renderWorkloads, pods: renderPods, services: renderServices, nodes: renderNodes, events: renderEvents })[state.tab](table, k);
    if (!k.metrics && (state.tab === 'pods' || state.tab === 'nodes')) hint.textContent = t('CPU/RAM fehlen: metrics-server läuft nicht (bei k3s normalerweise dabei).');
  }

  function renderWorkloads(table, k) {
    const body = head(table, ['Name', 'Bereit', 'Images', '']);
    const items = k8sFilter(k.workloads, w => [w.name, w.namespace, w.kind, ...w.images]);
    items.forEach(w => {
      const row = body.insertRow();
      nameCell(row, w.name, `${w.namespace} · ${w.kind} · ${fmtAge(w.age)}`);
      const rc = row.insertCell();
      rc.appendChild(tag(w.level, w.desired === 0 ? t('gestoppt') : `${w.ready}/${w.desired}`, w.message));
      const img = row.insertCell();
      img.className = 'ctr-images';
      w.images.forEach(i => { const c = raw('span', 'ctr-image', shortImage(i)); c.title = i; img.appendChild(c); });
      const actions = row.insertCell();
      actions.className = 'row-action ctr-actions';
      const kind = w.kind.toLowerCase();
      const path = `/api/containers/kube/${kind}/${encodeURIComponent(w.namespace)}/${encodeURIComponent(w.name)}`;
      if (kind !== 'daemonset') {
        if (w.desired === 0) {
          const prev = (state.data.scaled || {})[`${kind}/${w.namespace}/${w.name}`] || 1;
          actions.appendChild(iconBtn('start', 'Starten', b => act(b, api(`${path}/scale`, { replicas: prev })), 'services.control'));
        } else {
          actions.appendChild(iconBtn('scale', 'Skalieren…', b => {
            const n = window.prompt(t('Anzahl Replikas (0–20)'), String(w.desired));
            if (n === null || n.trim() === '' || isNaN(+n)) return;
            act(b, api(`${path}/scale`, { replicas: +n }));
          }, 'services.control'));
          actions.appendChild(iconBtn('stop', 'Stoppen', b => act(b, api(`${path}/scale`, { replicas: 0 })), 'services.control', 'Stoppen?', true));
        }
      }
      if (w.desired > 0) actions.appendChild(iconBtn('restart', 'Neu starten', b => act(b, api(`${path}/restart`, {})), 'services.control', 'Neu starten?'));
    });
    if (!items.length) emptyRow(table, 4, t('Keine Workloads.'));
  }

  function renderPods(table, k) {
    const body = head(table, ['Pod', 'Status', 'Neustarts gesamt', 'CPU', 'RAM', '']);
    // "Neustarts" alone means reboots elsewhere in the translation: short German label, fixed text
    const rh = table.tHead.rows[0].cells[2];
    rh.textContent = LOCALE.startsWith('de') ? 'Neustarts' : 'Restarts';
    rh.setAttribute('data-no-i18n', '');
    const items = k8sFilter(k.pods, p => [p.name, p.namespace, p.status, p.node, ...p.containers.map(c => c.image)]);
    items.forEach(p => {
      const row = body.insertRow();
      nameCell(row, p.name, `${p.namespace}${p.owner.kind ? ` · ${p.owner.kind} ${p.owner.name}` : ''} · ${fmtAge(p.age)}`);
      const st = row.insertCell();
      st.className = 'ctr-status';
      st.append(tag(p.level, p.status), raw('span', 'ctr-ready', p.ready));
      const rs = row.insertCell();
      rs.textContent = p.restarts;
      if (p.oom) rs.append(' ', tag('error', 'OOMKilled', t('Zuletzt wegen Speichermangel beendet')));
      row.insertCell().textContent = p.cpu == null ? '–' : fmtCpu(p.cpu);
      row.insertCell().textContent = p.mem == null ? '–' : fmtBytes(p.mem);
      const actions = row.insertCell();
      actions.className = 'row-action ctr-actions';
      actions.appendChild(iconBtn('logs', 'Logs', () => openLogs({ kind: 'pod', namespace: p.namespace, name: p.name, containers: p.containers.map(c => c.name) }), 'logs.view'));
      if (['Deployment', 'StatefulSet', 'DaemonSet', 'Job'].includes(p.owner.kind)) {
        actions.appendChild(iconBtn('recreate', 'Neu erstellen', b => act(b, api(`/api/containers/kube/pod/${encodeURIComponent(p.namespace)}/${encodeURIComponent(p.name)}/delete`, {})), 'services.control', 'Neu erstellen?'));
      }
    });
    if (!items.length) emptyRow(table, 6, t('Keine Pods.'));
  }

  function renderServices(table, k) {
    const body = head(table, ['Service', 'Typ', 'Cluster-IP', 'Ports', 'Extern']);
    const items = k8sFilter(k.services, s => [s.name, s.namespace, s.type, s.cluster_ip, ...s.external]);
    items.forEach(s => {
      const row = body.insertRow();
      nameCell(row, s.name, s.namespace);
      row.insertCell().textContent = s.type;
      const ip = row.insertCell();
      ip.className = 'mono';
      ip.textContent = s.cluster_ip || '–';
      const ports = row.insertCell();
      ports.className = 'mono wrap';
      ports.textContent = s.ports.join(', ') || '–';
      const ext = row.insertCell();
      ext.className = 'mono';
      ext.textContent = s.external.join(', ') || '–';
    });
    if (!items.length) emptyRow(table, 5, t('Keine Services.'));
  }

  function renderNodes(table, k) {
    const body = head(table, ['Node', 'Status', 'Rollen', 'CPU', 'RAM', 'Version', 'System']);
    k.nodes.forEach(n => {
      const row = body.insertRow();
      nameCell(row, n.name, fmtAge(n.age));
      const st = row.insertCell();
      st.appendChild(tag(n.ready ? 'active' : 'error', n.ready ? 'Bereit' : 'Nicht bereit'));
      n.pressure.forEach(p => st.append(' ', tag('warning', p)));
      row.insertCell().textContent = n.roles.join(', ') || '–';
      row.insertCell().textContent = n.cpu == null ? `${fmtNum(n.cpu_cores)} ${t('Kerne')}` : `${fmtCpu(n.cpu)} / ${fmtNum(n.cpu_cores)} ${t('Kerne')}`;
      row.insertCell().textContent = n.mem == null ? fmtBytes(n.mem_bytes) : `${fmtBytes(n.mem)} / ${fmtBytes(n.mem_bytes)}`;
      const v = row.insertCell();
      v.className = 'mono';
      v.textContent = n.version;
      const os = row.insertCell();
      os.className = 'wrap';
      os.textContent = `${n.os} · ${n.arch}`;
      os.setAttribute('data-no-i18n', '');
    });
    if (!k.nodes.length) emptyRow(table, 7, t('Keine Nodes.'));
  }

  function renderEvents(table, k) {
    const body = head(table, ['Zeit', 'Objekt', 'Grund', 'Meldung', 'Anzahl']);
    const items = k8sFilter(k.events || [], e => [e.name, e.namespace, e.reason, e.message, e.kind]);
    items.forEach(e => {
      const row = body.insertRow();
      row.insertCell().textContent = e.ts ? fmtTime(e.ts) : '–';
      nameCell(row, `${e.kind} ${e.name}`, e.namespace);
      row.insertCell().appendChild(tag('warning', e.reason));
      const m = row.insertCell();
      m.className = 'wrap ctr-msg';
      m.textContent = e.message;
      m.setAttribute('data-no-i18n', '');
      row.insertCell().textContent = e.count;
    });
    if (!items.length) emptyRow(table, 5, t('Keine Warnungen – gut so.'));
  }

  // ---------------------------------------------------------------------------
  // Docker containers
  // ---------------------------------------------------------------------------
  function renderDocker() {
    const dk = state.data.docker;
    const table = $('dk-table'), hint = $('dk-hint');
    hint.textContent = '';
    if (!dk.available) { table.innerHTML = ''; hint.textContent = t('Docker ist nicht installiert.'); return; }
    if (dk.error) { table.innerHTML = ''; hint.textContent = `${dk.error}${dk.hint ? ' – ' + dk.hint : ''}`; return; }
    const body = head(table, ['Container', 'Status', 'CPU', 'RAM', 'Ports', '']);
    const items = dk.containers.filter(c => $('dk-stopped').checked || c.state === 'running');
    items.forEach(c => {
      const row = body.insertRow();
      nameCell(row, c.name, `${shortImage(c.image)}${c.stack ? ` · Stack ${c.stack}` : ''}`);
      const st = row.insertCell();
      st.appendChild(tag(c.level, c.state === 'running' ? (c.health === 'unhealthy' ? t('Ungesund') : t('Läuft')) : c.state === 'exited' ? t('Beendet') : c.state, c.status));
      const s = statFor('docker', c.name);
      const cpu = row.insertCell();
      cpu.className = 'ctr-cpu';
      if (s && c.state === 'running') {
        cpu.append(el('span', 'ctr-cpu-val', fmtCpu(s.cpu)), spark(s.spark));
      } else cpu.textContent = '–';
      row.insertCell().textContent = s && c.state === 'running' ? fmtBytes(s.mem) : '–';
      const ports = row.insertCell();
      ports.className = 'mono wrap ctr-ports';
      ports.textContent = (c.ports || '').replace(/0\.0\.0\.0:|\[::\]:|:::/g, '').split(', ').filter((p, i, a) => a.indexOf(p) === i).join(', ') || '–';
      const actions = row.insertCell();
      actions.className = 'row-action ctr-actions';
      const path = `/api/workloads/docker/${encodeURIComponent(c.name)}`;
      actions.appendChild(iconBtn('logs', 'Logs', () => openLogs({ kind: 'docker', name: c.name }), 'logs.view'));
      if (c.state === 'running') actions.appendChild(iconBtn('console', 'Konsole', () => { location.href = `/terminal?container=${encodeURIComponent(c.name)}`; }, 'terminal'));
      if (s) actions.appendChild(iconBtn('stats', 'Verlauf', () => showStats(`docker:${c.name}`)));
      if (c.state === 'running') {
        actions.appendChild(iconBtn('restart', 'Neu starten', b => dockerAction(b, path, 'restart'), 'services.control', 'Neu starten?'));
        actions.appendChild(iconBtn('stop', 'Stoppen', b => dockerAction(b, path, 'stop'), 'services.control', 'Stoppen?', true));
      } else {
        actions.appendChild(iconBtn('start', 'Starten', b => dockerAction(b, path, 'start'), 'services.control'));
      }
    });
    if (!items.length) emptyRow(table, 6, t('Keine Container.'));
  }

  async function dockerAction(btn, path, action) {
    btn.disabled = true;
    try {
      const r = await api(`${path}/${action}`, {});
      toast(r.message || t('Erledigt.'), r.ok === false ? 'error' : 'success');
      setTimeout(loadOverview, 800);
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Compose stacks
  // ---------------------------------------------------------------------------
  const STACK_ACTIONS = [
    ['up', 'Starten', false], ['restart', 'Neu starten', true], ['update', 'Aktualisieren', true], ['stop', 'Stoppen', true], ['down', 'Entfernen', true]
  ];

  function stackUpdates(name) {
    return (state.data.images.images || []).filter(i => i.status === 'update' && i.users.some(u => u.kind === 'stack' && u.name === name));
  }

  function renderStacks() {
    const grid = $('stack-grid'), hint = $('stack-hint');
    grid.innerHTML = '';
    hint.textContent = '';
    const stacks = state.data.stacks;
    if (!state.data.docker.available || state.data.docker.error) { hint.textContent = t('Ohne Docker keine Compose-Stacks.'); return; }
    if (!stacks.length) { hint.textContent = t('Keine Compose-Stacks gefunden. Stacks erscheinen, sobald sie mit docker compose up gestartet wurden.'); return; }
    stacks.forEach(s => {
      const card = el('div', `stack-card level-${s.level}`);
      const top = el('div', 'stack-head');
      const title = el('div', 'stack-title');
      title.append(raw('span', 'stack-name', s.name), raw('span', 'stack-dir', s.dir || t('Compose-Datei unbekannt')));
      const tags = el('div', 'iface-tags');
      tags.appendChild(tag(s.level, `${s.running}/${s.total} ${t('laufen')}`));
      const upd = stackUpdates(s.name);
      if (upd.length) {
        const u = tag('warning', `${upd.length} ${upd.length === 1 ? 'Update' : 'Updates'}`, upd.map(i => i.ref).join('\n'));
        u.setAttribute('data-no-i18n', '');
        tags.appendChild(u);
      }
      top.append(title, tags);
      card.appendChild(top);

      const list = el('div', 'stack-containers');
      s.containers.forEach(c => {
        const chip = raw('span', `stack-chip level-${c.level}`, c.service || c.name);
        chip.title = `${c.name} · ${c.image} · ${c.status}`;
        list.appendChild(chip);
      });
      card.appendChild(list);

      const actions = el('div', 'stack-actions');
      const running = Object.values(state.jobs).find(j => j.stack === s.name && j.status === 'running');
      STACK_ACTIONS.forEach(([action, label, confirm]) => {
        if (action === 'up' && s.running === s.total && s.total) return;
        if ((action === 'stop' || action === 'restart') && !s.running) return;
        if (action === 'down' && !s.total) return;
        const run = b => startStackJob(b, s.name, action);
        const b = confirm ? confirmButton(label, action === 'down' ? 'Container löschen?' : 'Sicher?', action === 'down' ? 'btn-danger' : action === 'update' && upd.length ? 'btn-primary' : 'btn-secondary', run, 'services.control')
          : button(label, 'btn-primary', run, 'services.control');
        b.disabled = !s.file_exists || !!running;
        if (!s.file_exists) b.title = t('Compose-Datei nicht gefunden');
        actions.appendChild(b);
      });
      if (s.files.length && window.PiEditor) {
        actions.appendChild(button('Compose-Datei', 'btn-secondary', () => window.PiEditor.open(s.files[0], { composeHint: false, onSaved: () => toast(t('Gespeichert – mit „Aktualisieren“ oder „Starten“ übernehmen.'), 'success') }), 'files.read'));
      }
      const doc = el('a', 'btn-secondary', t('Steckbrief'));
      doc.href = `/doku#steckbrief=${encodeURIComponent(`stack:${s.name}`)}`;
      actions.appendChild(doc);
      card.appendChild(actions);
      card.appendChild(stackExtras(s));

      const job = Object.values(state.jobs).filter(j => j.stack === s.name).sort((a, b) => b.started - a.started)[0];
      if (job) card.appendChild(jobPanel(job));
      grid.appendChild(card);
    });
  }

  // Auto-update schedule and resource limits of one stack
  const DAY_NAMES = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'];

  function stackExtras(s) {
    const box = el('div', 'stack-extras');
    const au = (state.data.autoupdate || { schedules: {}, log: [] }).schedules[s.name];
    const last = (state.data.autoupdate || { log: [] }).log.find(e => e.stack === s.name);

    // Auto-update
    const row = el('div', 'stack-extra-row');
    const label = el('label', 'toggle-label');
    const on = el('input');
    on.type = 'checkbox';
    on.checked = !!au;
    on.disabled = !can('services.control') || !s.file_exists;
    label.append(on, document.createTextNode(` ${t('Automatisch aktualisieren')}`));
    const time = el('input', 'inline-input stack-time');
    time.type = 'time';
    time.value = au ? au.time : '04:00';
    time.disabled = on.disabled;
    const days = el('select', 'inline-select');
    [['daily', 'täglich'], ...DAY_NAMES.map((d, i) => [`w${i}`, `${t('jeden')} ${t(d)}`])].forEach(([v, l]) => {
      const o = el('option', '', t(l));
      o.value = v;
      days.appendChild(o);
    });
    days.value = au ? (au.days === 'weekly' ? `w${au.weekday}` : 'daily') : 'daily';
    days.disabled = on.disabled;
    const save = async () => {
      const weekly = days.value.startsWith('w');
      try {
        state.data.autoupdate = await api(`/api/containers/autoupdate/${encodeURIComponent(s.name)}`,
          { enabled: on.checked, time: time.value || '04:00', days: weekly ? 'weekly' : 'daily', weekday: weekly ? +days.value.slice(1) : 6 });
        toast(on.checked ? `${s.name}: ${t('automatische Updates')} ${days.selectedOptions[0].textContent} ${time.value}` : `${s.name}: ${t('automatische Updates aus')}`, 'success');
      } catch (err) { toast(err.message, 'error'); on.checked = !on.checked; }
    };
    [on, time, days].forEach(x => x.addEventListener('change', save));
    row.append(label, time, days);
    if (au && can('services.control')) {
      const now = el('button', 'text-btn', t('Jetzt ausführen'));
      now.type = 'button';
      now.addEventListener('click', async () => {
        try { toast((await api(`/api/containers/autoupdate/${encodeURIComponent(s.name)}/run`, {})).message, 'success'); setTimeout(loadOverview, 1500); }
        catch (err) { toast(err.message, 'error'); }
      });
      row.appendChild(now);
    }
    box.appendChild(row);
    if (last) {
      const txt = `${t('Letztes automatisches Update')}: ${fmtTime(last.ts)} · ${last.status === 'ok' ? (last.changed ? t('aktualisiert') : t('schon aktuell')) : t('fehlgeschlagen')}`;
      const p = el('p', `field-hint${last.status === 'ok' ? '' : ' stack-warn'}`, txt);
      if (last.status !== 'ok' && last.summary) p.title = last.summary;
      box.appendChild(p);
    }

    // Resource limits
    if (s.services.length) {
      const lim = el('div', 'stack-limits');
      const summary = s.services.map(svc => {
        const l = s.limits[svc];
        return l ? `${svc}: ${l.cpus ? `${fmtNum(l.cpus)} CPU` : ''}${l.cpus && l.mem_mb ? ' · ' : ''}${l.mem_mb ? fmtBytes(l.mem_mb * 1048576) : ''}` : null;
      }).filter(Boolean);
      const head = el('div', 'stack-extra-row');
      head.appendChild(el('span', 'field-hint', summary.length ? `${t('Limits')}: ${summary.join(' · ')}` : t('Keine Ressourcen-Limits')));
      const toggle = button('Limits bearbeiten', 'text-btn', () => { form.hidden = !form.hidden; }, 'services.control');
      head.appendChild(toggle);
      lim.appendChild(head);
      const form = el('div', 'stack-limit-form');
      form.hidden = true;
      s.services.forEach(svc => {
        const l = s.limits[svc] || {};
        const r = el('div', 'stack-limit-row');
        r.appendChild(raw('b', '', svc));
        const cpu = el('input', 'inline-input');
        cpu.type = 'number';
        cpu.step = '0.1';
        cpu.min = '0.1';
        cpu.placeholder = t('CPU-Kerne');
        cpu.value = l.cpus || '';
        cpu.setAttribute('aria-label', `${svc} CPU`);
        const mem = el('input', 'inline-input');
        mem.type = 'number';
        mem.step = '64';
        mem.min = '32';
        mem.placeholder = t('RAM in MB');
        mem.value = l.mem_mb || '';
        mem.setAttribute('aria-label', `${svc} RAM`);
        const ok = button('Speichern', 'btn-secondary', async b => {
          b.disabled = true;
          try {
            const r2 = await api(`/api/containers/stacks/${encodeURIComponent(s.name)}/limits`,
              { service: svc, cpus: cpu.value ? +cpu.value : null, mem_mb: mem.value ? +mem.value : null });
            toast(r2.message, r2.applied ? 'success' : 'info');
            loadOverview();
          } catch (err) { toast(err.message, 'error'); }
          b.disabled = false;
        }, 'services.control');
        r.append(cpu, el('span', 'field-hint', 'CPU'), mem, el('span', 'field-hint', 'MB'), ok);
        form.appendChild(r);
      });
      form.appendChild(el('p', 'field-hint', t('Leer = ohne Limit. Gilt sofort und bleibt über eine Override-Datei (docker-compose.picore.yml) im Stack-Ordner auch nach Updates erhalten.')));
      lim.appendChild(form);
      box.appendChild(lim);
    }
    return box;
  }

  function jobPanel(job) {
    const box = el('div', `stack-job ${job.status}`);
    const head = el('div', 'stack-job-head');
    const label = job.status === 'running' ? `${t(job.label)} ${t('läuft…')}` : job.status === 'ok' ? `${t(job.label)}: ${t('fertig')}` : `${t(job.label)}: ${t('fehlgeschlagen')}`;
    head.append(tag(job.status === 'ok' ? 'active' : job.status === 'error' ? 'error' : 'warning', label));
    if (job.status !== 'running') {
      const close = button('Ausblenden', 'text-btn', () => { delete state.jobs[job.id]; renderStacks(); });
      head.appendChild(close);
    }
    const pre = raw('pre', 'stack-job-out', job.output || '…');
    box.append(head, pre);
    requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
    return box;
  }

  async function startStackJob(btn, name, action) {
    btn.disabled = true;
    try {
      const job = await api(`/api/containers/stacks/${encodeURIComponent(name)}/${action}`, {});
      state.jobs[job.id] = job;
      renderStacks();
      pollJob(job.id);
    } catch (err) {
      toast(err.message, 'error');
      btn.disabled = false;
    }
  }

  function pollJob(id) {
    const timer = setInterval(async () => {
      try {
        const job = await api(`/api/containers/jobs/${id}`);
        state.jobs[id] = job;
        if (job.status !== 'running') {
          clearInterval(timer);
          toast(`${job.stack}: ${t(job.label)} ${job.status === 'ok' ? t('fertig') : t('fehlgeschlagen')}`, job.status === 'ok' ? 'success' : 'error');
          loadOverview();
        } else {
          // only the output changed: update the <pre> in place
          const card = [...document.querySelectorAll('.stack-card')].find(c => c.querySelector('.stack-name').textContent === job.stack);
          const pre = card && card.querySelector('.stack-job-out');
          if (pre) { pre.textContent = job.output || '…'; pre.scrollTop = pre.scrollHeight; return; }
        }
        renderStacks();
      } catch (e) { clearInterval(timer); }
    }, 1000);
  }

  // ---------------------------------------------------------------------------
  // Image updates
  // ---------------------------------------------------------------------------
  const IMG_STATUS = {
    update: ['warning', 'Update verfügbar'], current: ['good', 'Aktuell'], unknown: ['', 'Unbekannt'],
    local: ['', 'Lokal gebaut'], pinned: ['', 'Fest (Digest)']
  };

  function renderImages() {
    const im = state.data.images;
    $('img-checked').textContent = im.running ? t('Prüfe…') : im.checked ? `${t('Zuletzt geprüft')}: ${fmtTime(im.checked)}` : t('Noch nicht geprüft');
    $('img-check').disabled = im.running;
    const table = $('img-table');
    const body = head(table, ['Image', 'Genutzt von', 'Status']);
    im.images.forEach(i => {
      const row = body.insertRow();
      nameCell(row, `${i.repo.replace(/^library\//, '')}:${i.tag}`, i.registry);
      const users = row.insertCell();
      users.className = 'ctr-images';
      i.users.forEach(u => {
        const label = u.kind === 'stack' ? `Stack ${u.name}` : u.kind === 'docker' ? u.name : `${u.namespace}/${u.name}`;
        const chip = raw('span', 'ctr-image', label);
        chip.title = label;
        users.appendChild(chip);
      });
      const [level, text] = IMG_STATUS[i.status] || ['', i.status];
      const st = row.insertCell();
      st.className = 'img-status';
      st.appendChild(el('span', `health-tag ${level}`, text));
      if (i.error) { st.appendChild(el('span', 'ctr-sub', i.error)); }
      const actions = el('div', 'img-actions');
      st.appendChild(actions);
      if (i.status !== 'local' && i.status !== 'pinned') {
        const notes = el('button', 'btn-secondary', i.status === 'update' ? 'Was ist neu?' : 'Versionshinweise');
        notes.type = 'button';
        notes.addEventListener('click', () => toggleReleases(row, i, notes));
        actions.appendChild(notes);
      }
      if (i.status === 'update') {
        i.users.filter(u => u.kind === 'stack').forEach(u => {
          const stack = state.data.stacks.find(s => s.name === u.name);
          if (stack && stack.file_exists) actions.appendChild(confirmButton(`${u.name} aktualisieren`, 'Sicher?', 'btn-primary', b => { startStackJob(b, u.name, 'update'); location.hash = '#stacks'; }, 'services.control'));
        });
        i.users.filter(u => ['deployment', 'statefulset', 'daemonset'].includes(u.kind)).forEach(u => {
          const b = confirmButton(`${u.name} neu starten`, 'Sicher?', 'btn-secondary',
            btn => act(btn, api(`/api/containers/kube/${u.kind}/${encodeURIComponent(u.namespace)}/${encodeURIComponent(u.name)}/restart`, {})), 'services.control');
          b.title = t('Holt das neue Image nur mit imagePullPolicy: Always (Standard beim Tag „latest“).');
          actions.appendChild(b);
        });
        if (i.users.some(u => u.kind === 'docker')) st.appendChild(el('span', 'ctr-sub', t('Ohne Compose: docker pull + Container neu erstellen')));
      }
    });
    if (!im.images.length) emptyRow(table, 3, im.checked ? t('Keine Images gefunden.') : t('Noch nicht geprüft – „Jetzt prüfen“.'));
    $('img-hint').textContent = im.error || '';
    if (im.running) setTimeout(loadOverview, 3000);
  }

  // Release notes of the image's GitHub project, below the image's row
  async function toggleReleases(row, image, btn, refresh) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains('img-notes-row') && !refresh) { next.remove(); return; }
    if (next && next.classList.contains('img-notes-row')) next.remove();
    const tr = row.parentNode.insertRow(row.sectionRowIndex + 1);
    tr.className = 'img-notes-row';
    const cell = tr.insertCell();
    cell.colSpan = 3;
    const box = el('div', 'img-notes', 'Lade Versionshinweise…');
    cell.appendChild(box);
    btn.disabled = true;
    let d;
    try {
      d = await api(`/api/containers/images/releases?ref=${encodeURIComponent(image.ref)}${refresh ? '&refresh=1' : ''}`);
    } catch (err) {
      box.textContent = err.message;
      btn.disabled = false;
      return;
    }
    btn.disabled = false;
    box.innerHTML = '';
    const head = el('div', 'img-notes-head');
    if (d.github) {
      const a = raw('a', 'img-notes-repo', `github.com/${d.github}`);
      a.href = `https://github.com/${d.github}/releases`;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      head.appendChild(a);
    }
    if (d.current) head.appendChild(raw('span', 'ctr-sub', `${t('Läuft')}: ${d.current}`));
    else if (d.created) head.appendChild(raw('span', 'ctr-sub', `${t('Lokales Image vom')} ${new Date(d.created).toLocaleDateString(LOCALE)}`));
    box.appendChild(head);
    if (d.error) box.appendChild(el('p', 'field-hint', d.error));
    const newer = d.releases.filter(r => r.newer);
    const shown = newer.length ? newer : d.releases.slice(0, 3);
    if (d.releases.length) {
      box.appendChild(el('p', 'field-hint', newer.length
        ? `${newer.length} ${t(newer.length === 1 ? 'Version ist neuer als das lokale Image' : 'Versionen sind neuer als das lokale Image')}`
        : t('Die letzten Versionen:')));
    }
    shown.forEach((r, n) => {
      const det = el('details', 'img-release');
      det.open = n === 0;
      const sum = el('summary');
      sum.appendChild(raw('span', 'img-release-name', r.name));
      if (r.name !== r.tag) sum.appendChild(raw('code', '', r.tag));
      if (r.prerelease) sum.appendChild(el('span', 'health-tag warning', 'Vorabversion'));
      if (r.current) sum.appendChild(el('span', 'health-tag good', 'Läuft'));
      if (r.published) sum.appendChild(raw('span', 'ctr-sub', new Date(r.published * 1000).toLocaleDateString(LOCALE)));
      det.appendChild(sum);
      const body = window.PiMarkdown ? window.PiMarkdown.render(r.body || '') : raw('pre', '', r.body || '');
      body.setAttribute('data-no-i18n', '');
      if (/breaking|BREAKING|migration|Migration|incompatib/i.test(r.body || '')) det.appendChild(el('p', 'img-breaking', 'Enthält Hinweise auf Breaking Changes oder Migration – vor dem Update lesen.'));
      det.appendChild(body);
      if (r.url) {
        const link = el('a', 'img-release-link', 'Auf GitHub öffnen');
        link.href = r.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        det.appendChild(link);
      }
      box.appendChild(det);
    });
    // Own source when the image doesn't name one (or names the wrong one)
    if (can('services.control')) {
      const form = el('form', 'img-source');
      const inp = raw('input');
      inp.type = 'text';
      inp.placeholder = 'owner/repository';
      inp.value = d.github || '';
      inp.spellcheck = false;
      const save = el('button', 'btn-secondary', 'Quelle speichern');
      save.type = 'submit';
      form.append(el('span', 'ctr-sub', 'GitHub-Projekt:'), inp, save);
      form.addEventListener('submit', async e => {
        e.preventDefault();
        try {
          await api('/api/containers/images/source', { repo: d.repo, github: inp.value.trim() });
          toggleReleases(row, image, btn, true);
        } catch (err) { toast(err.message, 'error'); }
      });
      box.appendChild(form);
    }
  }

  // ---------------------------------------------------------------------------
  // Statistics
  // ---------------------------------------------------------------------------
  function renderStatSelect() {
    const sel = $('st-select');
    const items = (state.stats && state.stats.items) || [];
    const keep = state.statKey || sel.value;
    sel.innerHTML = '';
    const groups = [['docker', 'Docker'], ['pod', 'Kubernetes']];
    groups.forEach(([kind, label]) => {
      const list = items.filter(i => i.kind === kind).sort((a, b) => (b.cpu || 0) - (a.cpu || 0));
      if (!list.length) return;
      const og = el('optgroup');
      og.label = label;
      list.forEach(i => { const o = raw('option', '', i.name); o.value = `${kind}:${i.name}`; og.appendChild(o); });
      sel.appendChild(og);
    });
    if (!items.length) {
      sel.appendChild(Object.assign(el('option', '', t('Noch keine Messwerte')), { value: '' }));
      $('st-hint').textContent = t('Die erste Messung folgt innerhalb einer Minute.');
    }
    if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
    state.statKey = sel.value;
  }

  function showStats(key) {
    state.statKey = key;
    renderStatSelect();
    loadStatHistory();
    $('statistik').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function loadStatHistory() {
    const key = $('st-select').value;
    if (!key) return;
    const [kind, ...rest] = key.split(':');
    const name = rest.join(':');
    try {
      const h = await api(`/api/containers/stats/history?kind=${kind}&name=${encodeURIComponent(name)}&hours=${state.statHours}`);
      state.history = h;
      state.historyAt = Date.now();
      renderStatHistory(h);
    } catch (err) {
      $('st-hint').textContent = err.message;
    }
  }

  function renderStatHistory(h) {
    const pts = h.points;
    const cpu = pts.map(p => p.cpu).filter(v => v != null), mem = pts.map(p => p.mem).filter(v => v != null);
    const box = $('st-stats');
    box.innerHTML = '';
    box.append(statTile('CPU Ø', fmtCpu(cpu.length ? cpu.reduce((a, b) => a + b, 0) / cpu.length : null)),
      statTile('CPU Spitze', fmtCpu(pts.length ? Math.max(...pts.map(p => p.cpu_max || 0)) : null)),
      statTile('RAM Ø', fmtBytes(mem.length ? mem.reduce((a, b) => a + b, 0) / mem.length : null)),
      statTile('RAM Spitze', fmtBytes(mem.length ? Math.max(...mem) : null)));
    $('st-hint').textContent = pts.length ? '' : t('Für diesen Zeitraum gibt es noch keine Messwerte.');
    lineChart($('st-cpu'), pts, [{ key: 'cpu', label: 'CPU', color: 'var(--accent-cyan)' }], fmtCpu, h.bucket);
    lineChart($('st-mem'), pts, [{ key: 'mem', label: 'RAM', color: 'var(--accent-cyan)' }], fmtBytes, h.bucket);
    const hasNet = h.kind === 'docker' && pts.some(p => p.rx != null);
    $('st-net-wrap').hidden = !hasNet;
    if (hasNet) lineChart($('st-net'), pts, [{ key: 'rx', label: 'Empfangen', color: 'var(--chart-down)' }, { key: 'tx', label: 'Gesendet', color: 'var(--chart-up)' }], v => `${fmtBytes(v)}/s`, h.bucket);
  }

  function niceTop(max) {
    if (max <= 0) return 1;
    const raw = max / 4, exp = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / exp;
    return Math.ceil(max / ((f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp)) * ((f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp);
  }

  function lineChart(box, pts, series, fmt, bucket) {
    const svg = box.querySelector('svg'), tip = box.querySelector('.history-tooltip');
    svg.innerHTML = '';
    tip.hidden = true;
    const W = box.clientWidth, H = box.clientHeight;
    if (!W || !pts.length) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const max = Math.max(...pts.flatMap(p => series.map(s => p[s.key] || 0)), 0);
    const top = niceTop(max * 1.05);
    const pad = { l: 64, r: 10, t: 10, b: 22 }, iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const t0 = pts[0].ts, t1 = Math.max(pts[pts.length - 1].ts, t0 + bucket);
    const x = ts => pad.l + (ts - t0) / (t1 - t0) * iw, y = v => pad.t + ih - v / top * ih;
    for (let i = 0; i <= 4; i++) {
      const v = top / 4 * i;
      svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + iw, y1: y(v), y2: y(v), class: 'grid-line' }));
      const l = svgEl('text', { x: pad.l - 8, y: y(v) + 3, 'text-anchor': 'end', class: 'axis-label' });
      l.textContent = v ? fmt(v) : '0';
      svg.appendChild(l);
    }
    const ticks = Math.max(2, Math.floor(iw / 90));
    for (let i = 0; i <= ticks; i++) {
      const ts = t0 + (t1 - t0) * i / ticks;
      const l = svgEl('text', { x: x(ts), y: H - 5, 'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle', class: 'axis-label' });
      l.textContent = (t1 - t0) > 2 * 86400 ? new Date(ts * 1000).toLocaleDateString(LOCALE, { weekday: 'short', day: 'numeric' })
        : new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
      svg.appendChild(l);
    }
    series.forEach(s => {
      let d = '', gap = true;
      pts.forEach((p, i) => {
        const v = p[s.key];
        // break the line where samples are missing (Pi off, container stopped)
        if (v == null || (i && p.ts - pts[i - 1].ts > bucket * 3)) gap = true;
        if (v == null) return;
        d += `${gap ? 'M' : 'L'}${x(p.ts).toFixed(1)},${y(v).toFixed(1)}`;
        gap = false;
      });
      svg.appendChild(svgEl('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    });
    const cross = svgEl('line', { y1: pad.t, y2: pad.t + ih, class: 'crosshair', visibility: 'hidden' });
    svg.appendChild(cross);
    const dots = series.map(s => { const c = svgEl('circle', { r: 4, fill: s.color, stroke: 'var(--surface-raised)', 'stroke-width': 2, visibility: 'hidden' }); svg.appendChild(c); return c; });
    svg.onpointermove = e => {
      const px = e.clientX - svg.getBoundingClientRect().left;
      const ts = t0 + (px - pad.l) / iw * (t1 - t0);
      let best = pts[0];
      pts.forEach(p => { if (Math.abs(p.ts - ts) < Math.abs(best.ts - ts)) best = p; });
      const cx = x(best.ts);
      cross.setAttribute('x1', cx); cross.setAttribute('x2', cx); cross.setAttribute('visibility', 'visible');
      tip.innerHTML = '';
      tip.appendChild(el('div', 'tt-time', new Date(best.ts * 1000).toLocaleString(LOCALE, { weekday: 'short', hour: '2-digit', minute: '2-digit' })));
      series.forEach((s, i) => {
        const v = best[s.key];
        if (v == null) { dots[i].setAttribute('visibility', 'hidden'); } else {
          dots[i].setAttribute('cx', cx); dots[i].setAttribute('cy', y(v)); dots[i].setAttribute('visibility', 'visible');
        }
        const row = el('div', 'tt-row');
        const key = el('span', 'line-key');
        key.style.setProperty('--key-color', s.color);
        row.append(key, el('b', '', v == null ? '–' : fmt(v)), document.createTextNode(t(s.label)));
        tip.appendChild(row);
      });
      tip.hidden = false;
      tip.style.left = `${cx + 14 + tip.offsetWidth > W ? cx - 14 - tip.offsetWidth : cx + 14}px`;
    };
    svg.onpointerleave = () => { cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); tip.hidden = true; };
  }

  // ---------------------------------------------------------------------------
  // Logs sheet (pods and docker containers)
  // ---------------------------------------------------------------------------
  let followTimer = null;

  function openLogs(target) {
    state.log = target;
    $('log-title').textContent = target.name;
    $('log-sub').textContent = target.kind === 'pod' ? `Pod · ${target.namespace}` : 'Docker-Container';
    const sel = $('log-container');
    sel.innerHTML = '';
    (target.containers || []).forEach(c => { const o = raw('option', '', c); o.value = c; sel.appendChild(o); });
    sel.hidden = !(target.containers && target.containers.length > 1);
    $('log-previous-wrap').hidden = target.kind !== 'pod';
    $('log-previous').checked = false;
    $('log-search').value = '';
    $('log-output').textContent = t('Lade…');
    const modal = $('log-modal');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    loadLogs(true);
  }

  function closeLogs() {
    $('log-modal').classList.remove('open');
    $('log-modal').setAttribute('aria-hidden', 'true');
    $('log-follow').checked = false;
    clearInterval(followTimer);
    state.log = null;
  }

  async function loadLogs(scrollDown) {
    const tg = state.log;
    if (!tg) return;
    const lines = $('log-lines').value;
    try {
      let text;
      if (tg.kind === 'pod') {
        const q = new URLSearchParams({ namespace: tg.namespace, name: tg.name, container: $('log-container').value || '', lines, previous: $('log-previous').checked });
        const r = await api(`/api/containers/kube/logs?${q}`);
        text = r.log || (r.ok ? t('(keine Ausgabe)') : '');
      } else {
        const r = await api(`/api/workloads/docker/${encodeURIComponent(tg.name)}/logs?lines=${lines}`);
        if (r.ok === false) throw new Error(r.message);
        text = r.log || t('(keine Ausgabe)');
      }
      state.logText = text;
      renderLogText(scrollDown);
    } catch (err) {
      $('log-output').textContent = err.message;
    }
  }

  function renderLogText(scrollDown) {
    const out = $('log-output');
    const q = $('log-search').value.trim().toLowerCase();
    const atBottom = out.parentElement.scrollHeight - out.parentElement.scrollTop - out.parentElement.clientHeight < 40;
    const text = state.logText || '';
    out.textContent = q ? text.split('\n').filter(l => l.toLowerCase().includes(q)).join('\n') || t('Keine Treffer') : text;
    if (scrollDown || atBottom) out.parentElement.scrollTop = out.parentElement.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // Loading
  // ---------------------------------------------------------------------------
  async function loadOverview() {
    try {
      const [d, s] = await Promise.all([api('/api/containers/overview'), api('/api/containers/stats').catch(() => null)]);
      state.data = d;
      if (s) state.stats = s;
      (d.jobs || []).forEach(j => {
        const known = state.jobs[j.id];
        state.jobs[j.id] = j;
        if (j.status === 'running' && !known) pollJob(j.id);
      });
      renderAll();
      $('ov-updated').textContent = `${t('Stand')} ${new Date().toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function renderAll() {
    const d = state.data;
    renderOverview(d);
    renderKube();
    renderDocker();
    // a job's output is updated in place while it runs; a full re-render would reset the scroll
    // ... and an open limits form or a focused field must not be wiped by the 15 s refresh
    const editing = document.querySelector('.stack-limit-form:not([hidden])') || $('stack-grid').contains(document.activeElement);
    if (!Object.values(state.jobs).some(j => j.status === 'running') && !editing) renderStacks();
    renderImages();
    const hadKey = state.statKey;
    renderStatSelect();
    if (!state.history || state.statKey !== hadKey || Date.now() - (state.historyAt || 0) > 60000) loadStatHistory();
  }

  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }

    $('ov-reload').addEventListener('click', loadOverview);
    document.querySelectorAll('#k8s-tabs button').forEach(b => b.addEventListener('click', () => {
      state.tab = b.dataset.tab;
      document.querySelectorAll('#k8s-tabs button').forEach(x => x.classList.toggle('active', x === b));
      renderKube();
    }));
    ['k8s-ns', 'k8s-system'].forEach(id => $(id).addEventListener('change', renderKube));
    $('k8s-q').addEventListener('input', renderKube);
    $('k8s-filters').addEventListener('submit', e => e.preventDefault());
    $('dk-stopped').addEventListener('change', renderDocker);
    $('img-check').addEventListener('click', async () => {
      $('img-check').disabled = true;
      try { state.data.images = await api('/api/containers/images/check', {}); renderImages(); } catch (err) { toast(err.message, 'error'); }
    });
    $('st-select').addEventListener('change', () => { state.statKey = $('st-select').value; loadStatHistory(); });
    document.querySelectorAll('#st-range button').forEach(b => b.addEventListener('click', () => {
      state.statHours = +b.dataset.hours;
      document.querySelectorAll('#st-range button').forEach(x => x.classList.toggle('active', x === b));
      loadStatHistory();
    }));
    ['st-cpu', 'st-mem', 'st-net'].forEach(id => new ResizeObserver(() => state.history && renderStatHistory(state.history)).observe($(id)));

    $('log-close').addEventListener('click', closeLogs);
    $('log-modal').addEventListener('click', e => { if (e.target.id === 'log-modal') closeLogs(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('log-modal').classList.contains('open') && !document.body.classList.contains('editor-open')) closeLogs(); });
    ['log-container', 'log-lines', 'log-previous'].forEach(id => $(id).addEventListener('change', () => loadLogs(true)));
    $('log-search').addEventListener('input', () => renderLogText(false));
    $('log-form').addEventListener('submit', e => e.preventDefault());
    $('log-follow').addEventListener('change', () => {
      clearInterval(followTimer);
      if ($('log-follow').checked) followTimer = setInterval(() => loadLogs(false), 3000);
    });

    // Highlight the section in view
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
    }), { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));

    await loadOverview();
    setInterval(() => { if (!document.hidden && !$('log-modal').classList.contains('open')) loadOverview(); }, 15000);
  }

  init();
})();
