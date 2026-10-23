/**
 * PiCore Dashboard - fleet page (/flotte): this Pi and all remote Pis in one table,
 * apt update / upgrade for several of them at once.
 *
 * The update count of a Pi opens its pending packages (installed -> new version, security
 * updates marked) to upgrade single ones; with Pis selected, "Ausstehende Updates der Auswahl"
 * lists every pending package with the Pis that need it. "Protokoll" shows the output of the
 * running / last apt job of a Pi.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const COLS = 10;
  const selected = new Set();
  const openPkgs = new Set();          // Pis whose package list is open
  const openLogs = new Set();          // Pis whose apt output is open
  const picks = new Map();             // Pi id -> Set of package names (per-Pi list)
  const mergedPicks = new Set();       // package names (list of the selection)
  const logs = new Map();              // Pi id -> last job output
  let data = null;
  let timer = null;

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
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (res.status === 401) { location.href = '/login'; throw new Error('Nicht angemeldet.'); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const num = (v, unit, digits = 0) => (v == null ? '–' : `${Number(v).toLocaleString(LOCALE, { maximumFractionDigits: digits })}${unit}`);
  const busy = p => p.apt_running || p.pkg_running;
  const piName = id => { const p = data && data.pis.find(q => q.id === id); return p ? p.name : id; };

  function button(label, cls, onClick) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', e => { e.stopPropagation(); onClick(b); });
    return b;
  }

  function confirmClick(btn, question, action) {
    if (!btn.classList.contains('confirming')) {
      btn.classList.add('confirming');
      btn.dataset.label = btn.textContent;
      btn.textContent = question;
      setTimeout(() => { if (btn.isConnected && btn.classList.contains('confirming')) { btn.classList.remove('confirming'); btn.textContent = btn.dataset.label; } }, 4000);
      return;
    }
    btn.classList.remove('confirming');
    btn.textContent = btn.dataset.label;
    action();
  }

  function meter(value, unit, warn, crit, max = 100) {
    const c = el('td', 'fleet-meter');
    if (value == null) { c.textContent = '–'; return c; }
    const level = value >= crit ? 'danger' : value >= warn ? 'warn' : 'fill-emerald';
    const bar = el('div', 'progress-bar-bg mini');
    const fill = el('div', `progress-bar-fill ${level}`);
    fill.style.width = `${Math.min(100, (value / max) * 100)}%`;
    bar.appendChild(fill);
    c.append(el('span', 'fleet-value', num(value, unit, 1)), bar);
    return c;
  }

  function updateSelection() {
    const n = selected.size;
    $('fleet-selected').textContent = n ? t(`${n} ${n === 1 ? 'Pi' : 'Pis'} ausgewählt`) : t('Keine Pis ausgewählt');
    $('fleet-update').disabled = !n;
    $('fleet-upgrade').disabled = !n;
    renderPending();
  }

  function showResults(results, label) {
    const box = $('fleet-results');
    box.innerHTML = '';
    results.forEach(x => {
      const line = el('div', 'fleet-result');
      line.append(el('span', `health-tag ${x.ok ? 'good' : 'critical'}`, x.ok ? t(label || 'Gestartet') : t('Fehler')),
        raw('strong', '', piName(x.id)), el('span', 'lan-sub', t(x.message)));
      if (x.ok) {
        line.appendChild(button('Protokoll', 'text-btn', () => { openLogs.add(x.id); render(); loadLog(x.id); }));
        openLogs.add(x.id);
      }
      box.appendChild(line);
    });
    results.filter(x => x.ok).forEach(x => setTimeout(() => loadLog(x.id), 1500));
    setTimeout(load, 2000);
  }

  // ---------------------------------------------------------------------------
  // Package list of one Pi
  // ---------------------------------------------------------------------------
  function pkgTable(rows, pickSet, extraHead, extraCell) {
    const table = el('table', 'data-table fleet-pkg-table');
    const head = table.createTHead().insertRow();
    const all = el('input');
    all.type = 'checkbox';
    all.setAttribute('aria-label', t('Alle auswählen'));
    all.checked = rows.length > 0 && rows.every(r => pickSet.has(r.name));
    all.addEventListener('change', () => { rows.forEach(r => (all.checked ? pickSet.add(r.name) : pickSet.delete(r.name))); render(); });
    const th = el('th');
    if (can('updates.install')) th.appendChild(all);
    head.appendChild(th);
    ['Paket', 'Installiert', 'Neu', ...extraHead].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    rows.forEach(r => {
      const row = body.insertRow();
      const c = row.insertCell();
      if (can('updates.install')) {
        const box = el('input');
        box.type = 'checkbox';
        box.checked = pickSet.has(r.name);
        box.setAttribute('aria-label', r.name);
        box.addEventListener('change', () => { box.checked ? pickSet.add(r.name) : pickSet.delete(r.name); render(); });
        c.appendChild(box);
      }
      const name = row.insertCell();
      name.appendChild(raw('span', 'mono', r.name));
      if (r.security) { name.append(' '); name.appendChild(el('span', 'health-tag critical', t('Sicherheit'))); }
      row.insertCell().appendChild(raw('span', 'mono fleet-ver', r.old));
      row.insertCell().appendChild(raw('span', 'mono fleet-ver new', r.new));
      if (extraCell) extraCell(row, r);
    });
    return table;
  }

  function pkgPanel(p) {
    const box = el('div', 'fleet-pkgs');
    const pickSet = picks.get(p.id) || new Set();
    picks.set(p.id, pickSet);
    // Forget picks of packages that are no longer pending
    [...pickSet].forEach(n => { if (!p.packages.some(x => x.name === n)) pickSet.delete(n); });
    const head = el('div', 'fleet-pkg-head');
    const info = [`${p.updates} ${t(p.updates === 1 ? 'Paket' : 'Pakete')}`];
    if (p.security) info.push(`${p.security} ${t('Sicherheit')}`);
    if (p.lists_updated) info.push(`${t('Paketlisten vom')} ${new Date(p.lists_updated * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`);
    head.appendChild(raw('span', 'field-hint', info.join(' · ')));
    if (can('updates.install')) {
      const acts = el('div', 'fleet-pkg-actions');
      const sel = button(pickSet.size ? `${pickSet.size} ausgewählte aktualisieren` : 'Pakete auswählen', 'btn-secondary', b => {
        if (!pickSet.size) return;
        confirmClick(b, t('Wirklich aktualisieren?'), async () => {
          try { showResults((await api('/api/fleet/packages', { targets: { [p.id]: [...pickSet] } })).results); pickSet.clear(); }
          catch (err) { toast(err.message, 'error'); }
        });
      });
      sel.disabled = !pickSet.size || busy(p);
      const allBtn = button('Alle installieren', 'btn-primary', b => confirmClick(b, t('Upgrade starten?'), async () => {
        try { showResults((await api('/api/fleet/apt', { ids: [p.id], mode: 'upgrade' })).results); }
        catch (err) { toast(err.message, 'error'); }
      }));
      allBtn.disabled = busy(p);
      acts.append(sel, allBtn);
      head.appendChild(acts);
    }
    box.appendChild(head);
    if (!p.packages.length) box.appendChild(el('p', 'field-hint', t('Keine ausstehenden Pakete.')));
    else {
      const wrap = el('div', 'table-scroll fleet-pkg-scroll');
      wrap.appendChild(pkgTable(p.packages, pickSet, []));
      box.appendChild(wrap);
      if (p.packages.length >= 500) box.appendChild(el('p', 'field-hint', t('Nur die ersten 500 Pakete werden angezeigt.')));
    }
    return box;
  }

  // ---------------------------------------------------------------------------
  // apt output of one Pi
  // ---------------------------------------------------------------------------
  function logPanel(p) {
    const box = el('div', 'fleet-log');
    const j = logs.get(p.id);
    const head = el('div', 'fleet-pkg-head');
    if (!j) head.appendChild(el('span', 'field-hint', t('Lade Protokoll…')));
    else {
      const state = j.running ? ['warning', 'Läuft'] : j.state === 'done' ? ['good', 'Fertig'] : j.state === 'failed' ? ['critical', 'Fehlgeschlagen'] : ['', 'Kein Lauf'];
      head.append(el('span', `health-tag ${state[0]}`, t(state[1])), raw('code', 'fleet-job-title', j.title || ''));
      if (j.finished) head.appendChild(el('span', 'field-hint', `${t('beendet')} ${new Date(j.finished * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`));
    }
    head.appendChild(button('Schließen', 'text-btn', () => { openLogs.delete(p.id); render(); }));
    box.appendChild(head);
    const pre = raw('pre', 'fleet-log-pre', (j && j.log) || (j ? t('Noch keine Ausgabe.') : ''));
    box.appendChild(pre);
    requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
    return box;
  }

  async function loadLog(id) {
    try {
      logs.set(id, await api(`/api/fleet/job?id=${encodeURIComponent(id)}`));
    } catch (err) {
      logs.set(id, { title: '', log: err.message, state: 'failed' });
    }
    if (openLogs.has(id)) render();
  }

  // ---------------------------------------------------------------------------
  // Table
  // ---------------------------------------------------------------------------
  function detailRow(body, content) {
    const row = body.insertRow();
    row.className = 'fleet-detail-row';
    const c = row.insertCell();
    c.colSpan = COLS;
    c.appendChild(content);
  }

  function render() {
    const table = $('fleet-table');
    const scroll = window.scrollY;
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    const all = el('input');
    all.type = 'checkbox';
    all.setAttribute('aria-label', t('Alle auswählen'));
    const online = data.pis.filter(p => p.online);
    all.checked = online.length > 0 && online.every(p => selected.has(p.id));
    all.addEventListener('change', () => { online.forEach(p => (all.checked ? selected.add(p.id) : selected.delete(p.id))); render(); });
    const th0 = el('th');
    if (can('updates.install')) th0.appendChild(all);
    head.appendChild(th0);
    ['Pi', 'Status', 'Version', 'Updates', 'Temperatur', 'CPU', 'RAM', 'Speicher', 'Laufzeit'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    data.pis.forEach(p => {
      const row = body.insertRow();
      const c0 = row.insertCell();
      if (can('updates.install') && p.online) {
        const box = el('input');
        box.type = 'checkbox';
        box.checked = selected.has(p.id);
        box.setAttribute('aria-label', p.name);
        box.addEventListener('change', () => { box.checked ? selected.add(p.id) : selected.delete(p.id); updateSelection(); });
        c0.appendChild(box);
      }
      const name = row.insertCell();
      name.className = 'wrap';
      name.append(raw('span', 'lan-name', p.name),
        raw('span', 'lan-sub', [p.model, p.url ? p.url.replace(/^https?:\/\//, '') : t('Dieser Pi')].filter(Boolean).join(' · ')));
      const st = row.insertCell();
      st.appendChild(el('span', `health-tag ${p.online ? 'good' : 'critical'}`, p.online ? t('Online') : t('Nicht erreichbar')));
      if (busy(p)) {
        st.append(' ');
        const label = p.pkg_running ? 'Pakete werden aktualisiert…' : p.apt_mode === 'update' ? 'Paketlisten laden…' : 'Upgrade läuft…';
        const tag = el('button', 'health-tag warning fleet-tag-btn', t(label));
        tag.type = 'button';
        tag.title = t('Protokoll anzeigen');
        tag.addEventListener('click', () => { openLogs.add(p.id); render(); loadLog(p.id); });
        st.appendChild(tag);
      }
      if (!p.online) {
        row.classList.add('offline');
        for (let i = 0; i < COLS - 3; i++) row.insertCell().textContent = '–';
        return;
      }
      const ver = row.insertCell();
      ver.appendChild(raw('code', 'fleet-commit', p.commit || '–'));
      if (p.update_available) { ver.append(' '); ver.appendChild(el('span', 'health-tag warning', t('Update verfügbar'))); }
      const upd = row.insertCell();
      upd.className = 'fleet-upd';
      if (p.updates == null) upd.textContent = '–';
      else {
        const level = p.security ? 'critical' : p.updates ? 'warning' : 'good';
        const text = p.updates ? `${p.updates} ${t('Pakete')}${p.security ? ` · ${p.security} ${t('Sicherheit')}` : ''}` : t('Aktuell');
        if (p.updates) {
          const btn = el('button', `health-tag ${level} fleet-tag-btn${openPkgs.has(p.id) ? ' open' : ''}`, text);
          btn.type = 'button';
          btn.title = t('Pakete anzeigen');
          btn.setAttribute('aria-expanded', String(openPkgs.has(p.id)));
          btn.addEventListener('click', () => { openPkgs.has(p.id) ? openPkgs.delete(p.id) : openPkgs.add(p.id); render(); });
          upd.appendChild(btn);
        } else upd.appendChild(el('span', `health-tag ${level}`, text));
        if (p.reboot_required) { upd.append(' '); upd.appendChild(el('span', 'health-tag warning', t('Neustart nötig'))); }
        const logBtn = button('Protokoll', 'text-btn fleet-log-btn', () => {
          openLogs.has(p.id) ? openLogs.delete(p.id) : openLogs.add(p.id);
          render();
          if (openLogs.has(p.id)) loadLog(p.id);
        });
        upd.appendChild(logBtn);
      }
      row.appendChild(meter(p.temp, ' °C', 65, 75, 85));
      row.appendChild(meter(p.cpu, ' %', 75, 90));
      row.appendChild(meter(p.ram, ' %', 80, 92));
      row.appendChild(meter(p.disk, ' %', 80, 92));
      row.insertCell().textContent = p.uptime || '–';
      if (openPkgs.has(p.id) && p.updates) detailRow(body, pkgPanel(p));
      if (openLogs.has(p.id)) detailRow(body, logPanel(p));
    });
    $('fleet-checked').textContent = t(`Stand ${new Date(data.checked * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`);
    [...selected].forEach(id => { if (!data.pis.some(p => p.id === id && p.online)) selected.delete(id); });
    updateSelection();
    fitPanels();
    window.scrollTo(0, scroll);
  }

  // The table is wider than the screen on small displays: panels below a Pi stay in the visible part
  function fitPanels() {
    const wrap = $('fleet-table').parentElement;
    const inset = wrap.clientWidth < 600 ? 24 : 60;
    document.querySelectorAll('.fleet-pkgs, .fleet-log').forEach(p => { p.style.maxWidth = `${Math.max(200, wrap.clientWidth - inset)}px`; });
  }

  // ---------------------------------------------------------------------------
  // Pending updates of the selected Pis, merged by package
  // ---------------------------------------------------------------------------
  function renderPending() {
    const sec = $('fleet-pending');
    if (!sec || !data) return;
    const pis = data.pis.filter(p => selected.has(p.id) && p.online && p.packages && p.packages.length);
    sec.hidden = !can('updates.install') || !selected.size;
    if (sec.hidden) return;
    const merged = new Map();
    pis.forEach(p => p.packages.forEach(x => {
      const m = merged.get(x.name) || { name: x.name, security: false, olds: new Set(), news: new Set(), pis: [] };
      m.security = m.security || x.security;
      m.olds.add(x.old);
      m.news.add(x.new);
      m.pis.push(p.id);
      merged.set(x.name, m);
    }));
    [...mergedPicks].forEach(n => { if (!merged.has(n)) mergedPicks.delete(n); });
    const q = $('fp-q').value.trim().toLowerCase();
    const onlySec = $('fp-security').checked;
    const rows = [...merged.values()].map(m => ({ name: m.name, security: m.security, old: [...m.olds].join(' / '), new: [...m.news].join(' / '), pis: m.pis }))
      .filter(r => (!q || r.name.includes(q)) && (!onlySec || r.security))
      .sort((a, b) => (b.security - a.security) || (b.pis.length - a.pis.length) || a.name.localeCompare(b.name));
    const total = [...merged.values()];
    $('fp-summary').textContent = total.length
      ? t(`${total.length} Pakete auf ${pis.length} ${pis.length === 1 ? 'Pi' : 'Pis'} · ${total.filter(m => m.security).length} Sicherheit`)
      : t('Auf den ausgewählten Pis ist alles aktuell.');
    const tableBox = $('fp-table');
    tableBox.innerHTML = '';
    if (rows.length) {
      const tb = pkgTable(rows, mergedPicks, ['Pis'], (row, r) => {
        const c = row.insertCell();
        c.className = 'fleet-pis';
        r.pis.forEach(id => c.appendChild(raw('span', 'pg-chip', piName(id))));
      });
      tableBox.replaceWith(tb);
      tb.id = 'fp-table';
    } else if (total.length) {
      tableBox.appendChild(el('caption', 'field-hint', t('Keine Treffer.')));
    }
    const targets = {};
    mergedPicks.forEach(n => (merged.get(n) || { pis: [] }).pis.forEach(id => { (targets[id] = targets[id] || []).push(n); }));
    const nPis = Object.keys(targets).length;
    const run = $('fp-run');
    const blocked = pis.filter(p => busy(p) && targets[p.id]);
    run.disabled = !mergedPicks.size || blocked.length > 0;
    $('fp-busy').textContent = blocked.length ? t(`Auf ${blocked.map(p => p.name).join(', ')} läuft gerade apt – danach geht es weiter.`) : '';
    run.textContent = mergedPicks.size ? t(`${mergedPicks.size} ${mergedPicks.size === 1 ? 'Paket' : 'Pakete'} auf ${nPis} ${nPis === 1 ? 'Pi' : 'Pis'} aktualisieren`) : t('Ausgewählte aktualisieren');
    run.onclick = () => confirmClick(run, t('Wirklich aktualisieren?'), async () => {
      try { showResults((await api('/api/fleet/packages', { targets })).results); mergedPicks.clear(); }
      catch (err) { toast(err.message, 'error'); }
    });
  }

  async function load() {
    $('fleet-reload').disabled = true;
    try {
      data = await api('/api/fleet');
      render();
      // Follow running jobs closely, otherwise every 30 s
      const running = data.pis.some(busy);
      [...openLogs].forEach(id => { const p = data.pis.find(q => q.id === id); if (p && (busy(p) || !logs.has(id))) loadLog(id); });
      clearTimeout(timer);
      timer = setTimeout(tick, running ? 4000 : 30000);
    } catch (err) { toast(err.message, 'error'); }
    finally { $('fleet-reload').disabled = false; }
  }

  function tick() {
    if (document.hidden) { timer = setTimeout(tick, 5000); return; }
    load();
  }

  async function runApt(mode, btn) {
    confirmClick(btn, t(`Auf ${selected.size} ${selected.size === 1 ? 'Pi' : 'Pis'} starten?`), async () => {
      try { showResults((await api('/api/fleet/apt', { ids: [...selected], mode })).results); }
      catch (err) { toast(err.message, 'error'); }
    });
  }

  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = '/login'; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }
    $('fleet-reload').addEventListener('click', load);
    window.addEventListener('resize', fitPanels);
    $('fleet-update').addEventListener('click', e => runApt('update', e.currentTarget));
    $('fleet-upgrade').addEventListener('click', e => runApt('upgrade', e.currentTarget));
    $('fp-q').addEventListener('input', renderPending);
    $('fp-security').addEventListener('change', renderPending);
    $('fp-filters').addEventListener('submit', e => e.preventDefault());
    $('fp-all').addEventListener('click', () => {
      data.pis.filter(p => selected.has(p.id) && p.online).forEach(p => (p.packages || []).forEach(x => {
        if (!$('fp-security').checked || x.security) mergedPicks.add(x.name);
      }));
      renderPending();
    });
    $('fp-none').addEventListener('click', () => { mergedPicks.clear(); renderPending(); });
    load();
  }

  init();
})();
