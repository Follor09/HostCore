/**
 * PiCore Dashboard - command palette (Ctrl+K / ⌘K, or the search button in the header)
 * Search pages, page sections and actions. Dashboard actions run directly on the
 * dashboard; from other pages they navigate there with #do=<action>.
 * Labels are German (the source language) and pass through I18N.t for display and search.
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const onDashboard = location.pathname === '/';
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const RECENT_KEY = 'paletteRecent';

  // ---------------------------------------------------------------------------
  // Commands
  // ---------------------------------------------------------------------------
  const click = sel => () => { const el = document.querySelector(sel); if (el) el.click(); };
  const scrollTo = sel => { const el = document.querySelector(sel); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); };

  // Dashboard actions: run(), or navigate to the dashboard and run there
  const DASH_ACTIONS = {
    updates: ['System-Updates öffnen', 'apt upgrade pakete', click('#updates-card')],
    nvme: ['NVMe-Verlauf öffnen', 'ssd verschleiß temperatur', click('#nvme-history-btn')],
    files: ['Dateien durchsuchen', 'explorer speicher analyse ordner', click('.drive-open')],
    processes: ['Prozess suchen', 'kill beenden pid', () => {
      document.getElementById('tab-btn-processes')?.click();
      scrollTo('[data-widget=processes]');
      setTimeout(() => document.getElementById('proc-search')?.focus(), 350);
    }],
    diagnostics: ['Diagnose & Server-Tools', 'ping test', () => {
      document.getElementById('tab-btn-diagnostics')?.click();
      scrollTo('[data-widget=processes]');
    }],
    weather: ['Wetter-Ort ändern', 'stadt city', () => { scrollTo('[data-widget=weather]'); click('#weather-city-btn')(); }],
    customize: ['Dashboard anpassen', 'layout karten ausblenden reihenfolge', () => window.PiLayout && window.PiLayout.edit()],
    fullscreen: ['Vollbild', 'kiosk fullscreen', click('#btn-fullscreen')]
  };
  const HISTORY_METRICS = [['cpu', 'CPU'], ['temp', 'Temperatur'], ['ram', 'RAM'], ['net', 'Netzwerk'], ['fan', 'Lüfter'], ['power', 'Leistung'], ['nvme', 'NVMe']];

  function runDashboardAction(id) {
    if (id.startsWith('history-')) {
      const metric = id.slice(8);
      document.querySelector(`#history-metric [data-metric="${metric}"]`)?.click();
      scrollTo('#history-card');
      return;
    }
    const action = DASH_ACTIONS[id];
    if (action) action[2]();
  }

  function dashboardCommand(id, label, group, keywords) {
    return {
      id: `dash:${id}`, label, group, keywords,
      run: () => (onDashboard ? runDashboardAction(id) : location.assign(`/#do=${encodeURIComponent(id)}`))
    };
  }

  // What a command needs (checked against PiAuth); unlisted = everyone
  const NEEDS = {
    'page:system': 'system.view', 'page:report': 'system.view', 'page:terminal': 'terminal', 'page:containers': 'system.view',
    'sec:/system#logs': 'logs.view', 'sec:/system#ssh': 'security.view', 'sec:/system#aktivitaet': 'security.view',
    'sec:/system#firewall': 'security.view', 'sec:/system#dns': 'network.tools',
    'sec:/settings#wetter': 'settings.manage', 'sec:/settings#dienste': 'settings.manage', 'sec:/settings#wake-on-lan': 'settings.manage',
    'sec:/settings#web-dienste': 'settings.manage', 'sec:/settings#links': 'settings.manage', 'sec:/settings#auto-update': 'settings.manage',
    'sec:/settings#verlauf': 'settings.manage', 'sec:/settings#freigaben': 'files.share',
    'sec:/settings#benutzer': 'users.manage', 'sec:/settings#berechtigungen': 'users.manage',
    'dash:files': 'files.read', 'dash:diagnostics': 'system.view', 'dash:weather': 'settings.manage'
  };
  const allowed = c => {
    const auth = window.PiAuth;
    if (!auth || !auth.loaded) return true;
    const need = NEEDS[c.id] || (c.id.startsWith('sec:/system#') || c.id.startsWith('sec:/containers#') ? 'system.view' : c.id.startsWith('act:') ? 'actions.run'
      : c.id.startsWith('wol:') ? 'network.tools' : null);
    return !need || auth.can(need);
  };

  function buildCommands() {
    const cmds = [];
    const page = (id, label, href, keywords) => cmds.push({ id, label, group: 'Seiten', keywords, href });
    page('page:dashboard', 'Dashboard', '/', 'start übersicht home');
    page('page:system', 'System-Werkzeuge', '/system', 'tools');
    page('page:containers', 'Container', '/containers', 'docker kubernetes k3s pods compose stacks images');
    page('page:settings', 'Einstellungen', '/settings', 'settings konfiguration');
    page('page:terminal', 'Web-Terminal', '/terminal', 'shell ssh konsole bash');

    const section = (path, prefix, list) => list.forEach(([hash, label, keywords]) =>
      cmds.push({ id: `sec:${path}#${hash}`, label: `${t(prefix)} › ${t(label)}`, raw: true, group: 'Bereiche', keywords, href: `${path}#${hash}` }));
    section('/system', 'System-Werkzeuge', [
      ['ereignisse', 'Ereignisse', 'events timeline ausfall feed'], ['netzwerk', 'Netzwerk', 'ip wlan gateway dns interfaces'], ['datenverbrauch', 'Datenverbrauch', 'traffic monat tag daten usage'],
      ['dns', 'DNS & Traceroute', 'nslookup dig mx txt route hops'], ['geraete', 'Geräte im Netzwerk', 'lan scan netzwerk geräte mac'],
      ['port-check', 'Port-Check', 'ports offen erreichbar test'], ['firewall', 'Firewall', 'ufw regeln port blockieren'],
      ['ssh', 'SSH-Anmeldungen', 'login fail2ban angriffe gesperrt'], ['hardware', 'Hardware', 'modell seriennummer bootloader pcie usb'],
      ['speicherprognose', 'Speicherprognose', 'disk voll forecast ordner wachstum'], ['sensoren', 'Sensoren', 'temperatur spannung pmic rp1 volt'], ['ressourcen', 'Ressourcen-Verbrauch', 'prozesse cpu ram programme hogs'],
      ['verfuegbarkeit', 'Verfügbarkeit', 'uptime ausfall dienste'], ['drosselung', 'Drosselung', 'throttling unterspannung hitze netzteil'],
      ['luefterkurve', 'Lüfterkurve', 'fan lüfter temperatur leise'], ['cpu-modus', 'CPU-Modus', 'governor takt leistung sparsam'],
      ['leistungstest', 'Leistungstest', 'benchmark speed test nvme'], ['logs', 'Log-Suche', 'journal journalctl fehler'],
      ['aufraeumen', 'Speicher aufräumen', 'cleanup cache platz'], ['aktivitaet', 'Aktivität', 'audit protokoll wer was log']
    ]);
    section('/containers', 'Container', [
      ['kubernetes', 'Kubernetes', 'k3s pods deployments services nodes kubectl'], ['docker', 'Docker-Container', 'docker container start stop logs'],
      ['stacks', 'Compose-Stacks', 'docker compose stack yaml aktualisieren'], ['images', 'Image-Updates', 'image update neu version registry'],
      ['statistik', 'Container-Statistik', 'cpu ram verlauf container stats']
    ]);
    section('/system', 'System-Werkzeuge', [['firewall-traffic', 'Firewall-Traffic', 'blockiert traffic ufw log länder country']]);
    section('/settings', 'Einstellungen', [
      ['konto', 'Konto', 'passwort sitzung account'], ['benutzer', 'Benutzer', 'users user accounts konten anlegen'],
      ['berechtigungen', 'Berechtigungen', 'rechte gruppen permissions roles rollen admin'], ['sprache', 'Sprache', 'language english deutsch'],
      ['darstellung', 'Darstellung', 'theme hell dunkel light dark design'], ['wetter', 'Wetter', 'ort stadt'],
      ['dienste', 'Dienste', 'services ausblenden'], ['wake-on-lan', 'Wake-on-LAN', 'wol geräte mac'], ['web-dienste', 'Web-Dienste', 'health check url erreichbar'], ['links', 'Eigene Links', 'links lesezeichen bookmarks'], ['freigaben', 'Freigabe-Links', 'share teilen download link'], ['auto-update', 'Auto-Update', 'github token git'],
      ['verlauf', 'Verlauf', 'history löschen datenbank'], ['system', 'System', 'info version']
    ]);

    Object.entries(DASH_ACTIONS).forEach(([id, [label, keywords]]) => cmds.push(dashboardCommand(id, label, 'Aktionen', keywords)));
    HISTORY_METRICS.forEach(([metric, label]) =>
      cmds.push({ ...dashboardCommand(`history-${metric}`, `${t('Verlauf')}: ${t(label)}`, 'Verlauf', 'diagramm chart 24h 7 tage'), raw: true }));

    const other = window.I18N && window.I18N.lang === 'en' ? 'de' : 'en';
    cmds.push({ id: 'lang', label: other === 'en' ? 'Sprache: English' : 'Language: Deutsch', raw: true, group: 'Aktionen',
      keywords: 'sprache language english deutsch', run: () => window.I18N && window.I18N.setLang(other) });
    [['auto', 'Design: Automatisch'], ['light', 'Design: Hell'], ['dark', 'Design: Dunkel']].forEach(([mode, label]) =>
      cmds.push({ id: `theme:${mode}`, label, group: 'Aktionen', keywords: 'theme design farbe hell dunkel light dark modus',
        run: () => window.PiTheme && window.PiTheme.set(mode) }));
    ((window.PiLinks && window.PiLinks.list()) || []).forEach(l => cmds.push({
      id: `link:${l.id}`, label: `${t('Link')}: ${l.name}`, raw: true, group: 'Links', keywords: `link ${l.url}`,
      run: () => window.open(l.url, '_blank', 'noopener')
    }));
    cmds.push({ id: 'page:report', label: 'Wochenbericht', group: 'Seiten', keywords: 'report bericht woche zusammenfassung drucken', href: '/report' });
    cmds.push({ id: 'logout', label: 'Abmelden', group: 'Aktionen', keywords: 'logout ausloggen',
      run: async () => { try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ } location.href = '/login'; } });

    // Live items that only exist on the dashboard: services and Wake-on-LAN devices (user names stay as-is)
    if (onDashboard) {
      document.querySelectorAll('.service-tile.clickable').forEach(tile => {
        const name = tile.dataset.name || tile.textContent.trim().split('\n')[0];
        cmds.push({ id: `svc:${name}`, label: `${t('Dienst')}: ${t(name)}`, raw: true, group: 'Dienste',
          keywords: 'service starten stoppen neustart log', run: () => tile.click() });
      });
      ((window.PiActions && window.PiActions.list()) || []).forEach(a => {
        cmds.push({ id: `act:${a.id}`, label: `${t('Schnellaktion')}: ${a.name}`, raw: true, group: 'Aktionen',
          keywords: `skript script ${a.id} ${a.description || ''}`, run: () => window.PiActions.run(a.id) });
      });
      document.querySelectorAll('.wol-item').forEach(item => {
        const name = item.querySelector('.workload-name')?.textContent || '';
        const btn = item.querySelector('.btn-wake');
        if (name && btn) cmds.push({ id: `wol:${name}`, label: `${t('Wecken')}: ${name}`, raw: true, group: 'Wake-on-LAN', keywords: 'wol aufwecken starten', run: () => btn.click() });
      });
    }
    return cmds.filter(allowed);
  }

  // ---------------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------------
  const norm = s => String(s).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ß/g, 'ss');

  function score(cmd, tokens) {
    const label = norm(cmd.raw ? cmd.label : `${cmd.label} ${t(cmd.label)}`);
    const hay = `${label} ${norm(cmd.group)} ${norm(t(cmd.group))} ${norm(cmd.keywords || '')}`;
    let s = 0;
    for (const tok of tokens) {
      if (hay.includes(tok)) {
        s += label.startsWith(tok) ? 6 : new RegExp(`(^|[\\s›:&(/-])${tok.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`).test(label) ? 4 : label.includes(tok) ? 2 : 1;
      } else if (tok.length >= 3 && label.split(/[\s›:&(/-]+/).some(word => isSubsequence(tok, word))) {
        s += 0.5;  // typo-tolerant: letters in order within one word ("updt" -> "updates")
      } else {
        return -1;
      }
    }
    return s;
  }

  function isSubsequence(needle, word) {
    if (!word || needle[0] !== word[0]) return false;
    let i = 0;
    for (const ch of word) if (ch === needle[i]) i++;
    return i === needle.length;
  }

  function recent() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch (e) { return []; }
  }

  function remember(id) {
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...recent().filter(x => x !== id)].slice(0, 5))); } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------------------
  // UI
  // ---------------------------------------------------------------------------
  let root, input, list, commands = [], shown = [], active = 0, lastFocus = null;
  const GROUP_ORDER = ['Zuletzt verwendet', 'Aktionen', 'Links', 'Seiten', 'Bereiche', 'Dienste', 'Wake-on-LAN', 'Verlauf'];

  function build() {
    root = document.createElement('div');
    root.className = 'palette-backdrop';
    root.hidden = true;
    root.innerHTML = `
      <div class="palette" role="dialog" aria-modal="true" aria-label="${t('Befehle & Suche')}">
        <div class="palette-search">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.35-4.35"></path></svg>
          <input type="text" id="palette-input" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="palette-list" aria-autocomplete="list">
          <kbd>Esc</kbd>
        </div>
        <div class="palette-list" id="palette-list" role="listbox" data-no-i18n></div>
        <div class="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> ${t('auswählen')}</span><span><kbd>Enter</kbd> ${t('ausführen')}</span><span><kbd>Esc</kbd> ${t('schließen')}</span></div>
      </div>`;
    document.body.appendChild(root);
    input = root.querySelector('#palette-input');
    input.placeholder = t('Seite, Bereich oder Aktion suchen…');
    list = root.querySelector('#palette-list');
    input.addEventListener('input', () => { active = 0; render(); });
    input.addEventListener('keydown', onKey);
    root.addEventListener('mousedown', e => { if (e.target === root) close(); });
  }

  function render() {
    const q = input.value.trim();
    const tokens = norm(q).split(/\s+/).filter(Boolean);
    list.innerHTML = '';
    if (tokens.length) {
      shown = commands.map(c => [c, score(c, tokens)]).filter(([, s]) => s >= 0)
        .sort((a, b) => b[1] - a[1] || GROUP_ORDER.indexOf(a[0].group) - GROUP_ORDER.indexOf(b[0].group)).slice(0, 40).map(([c]) => ({ ...c, shownGroup: c.group }));
    } else {
      const rec = recent().map(id => commands.find(c => c.id === id)).filter(Boolean)
        .map(c => ({ ...c, shownGroup: 'Zuletzt verwendet' }));
      const rest = commands.filter(c => !rec.some(r => r.id === c.id)).map(c => ({ ...c, shownGroup: c.group }))
        .sort((a, b) => GROUP_ORDER.indexOf(a.shownGroup) - GROUP_ORDER.indexOf(b.shownGroup));
      shown = [...rec, ...rest];
    }
    active = Math.min(active, Math.max(0, shown.length - 1));
    let group = null;
    shown.forEach((c, i) => {
      // Group headings only without a query; with a query the group shows on the right
      if (!tokens.length && c.shownGroup !== group) {
        group = c.shownGroup;
        const h = document.createElement('div');
        h.className = 'palette-group';
        h.textContent = t(group);
        list.appendChild(h);
      }
      const row = document.createElement('div');
      row.className = `palette-item${i === active ? ' active' : ''}`;
      row.id = `palette-opt-${i}`;
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', String(i === active));
      const label = document.createElement('span');
      label.className = 'palette-label';
      label.textContent = c.raw ? c.label : t(c.label);
      row.appendChild(label);
      if (tokens.length) {
        const g = document.createElement('span');
        g.className = 'palette-kind';
        g.textContent = t(c.group);
        row.appendChild(g);
      }
      row.addEventListener('mousemove', () => { if (active !== i) { active = i; highlight(); } });
      row.addEventListener('click', () => execute(i));
      list.appendChild(row);
    });
    if (!shown.length) {
      const empty = document.createElement('div');
      empty.className = 'palette-empty';
      empty.textContent = t('Nichts gefunden.');
      list.appendChild(empty);
    }
    input.setAttribute('aria-activedescendant', shown.length ? `palette-opt-${active}` : '');
  }

  function highlight() {
    list.querySelectorAll('.palette-item').forEach((row, i) => {
      row.classList.toggle('active', i === active);
      row.setAttribute('aria-selected', String(i === active));
    });
    input.setAttribute('aria-activedescendant', `palette-opt-${active}`);
    list.querySelector('.palette-item.active')?.scrollIntoView({ block: 'nearest' });
  }

  function onKey(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!shown.length) return;
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length;
      highlight();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (shown.length) execute(active);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }

  function execute(i) {
    const c = shown[i];
    if (!c) return;
    remember(c.id);
    close(false);
    if (c.href) {
      const url = new URL(c.href, location.origin);
      if (url.pathname === location.pathname && url.hash) {
        history.replaceState(null, '', url.hash);
        document.querySelector(url.hash)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        location.assign(c.href);
      }
    } else {
      c.run();
    }
  }

  function open() {
    if (!root) build();
    if (!root.hidden) return;
    lastFocus = document.activeElement;
    commands = buildCommands();
    input.value = '';
    active = 0;
    render();
    root.hidden = false;
    document.body.classList.add('palette-open');
    input.focus();
  }

  function close(restoreFocus = true) {
    if (!root || root.hidden) return;
    root.hidden = true;
    document.body.classList.remove('palette-open');
    if (restoreFocus && lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // ---------------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------------
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
      // Inside the web terminal Ctrl+K belongs to the shell (kill to end of line)
      if (e.ctrlKey && !e.metaKey && e.target.closest && e.target.closest('.xterm')) return;
      e.preventDefault();
      root && !root.hidden ? close() : open();
    }
  });

  const btn = document.getElementById('btn-palette');
  if (btn) {
    btn.title = `${t('Suchen & Befehle')} (${isMac ? '⌘K' : t('Strg+K')})`;
    btn.addEventListener('click', open);
  }

  // Arrived from another page with an action: run it once the dashboard is filled
  if (onDashboard) {
    const m = location.hash.match(/^#do=([\w-]+)$/);
    if (m) {
      history.replaceState(null, '', location.pathname + location.search);
      setTimeout(() => runDashboardAction(decodeURIComponent(m[1])), 1200);
    }
  }

  window.PiPalette = { open, close };
})();
