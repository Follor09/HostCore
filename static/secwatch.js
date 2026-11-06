/**
 * HostCore Dashboard - security watchers in System-Werkzeuge: file integrity, sudo log, malware
 * scan and container runtime protection (Falco).
 */
(function () {
  'use strict';

  const { t, can, el, raw, toast, api, time, tag, button, confirmButton, run, form, tiles, table, lines } = window.OpsUI;
  const $ = id => document.getElementById(id);
  if (!$('fim-table')) return;
  const WEEK = [[0, 'Montag'], [1, 'Dienstag'], [2, 'Mittwoch'], [3, 'Donnerstag'], [4, 'Freitag'], [5, 'Samstag'], [6, 'Sonntag']];
  const HOURS = [...Array(24).keys()].map(h => [h, `${String(h).padStart(2, '0')}:00`]);
  const openTask = task => { if (window.PiTasks && task && task.id) window.PiTasks.open(task.id); };

  // A row action asking for a reason, as the finding has to stay explainable afterwards
  const closeWith = (cell, onDone) => window.OpsUI.ask(cell, 'Begründung', 'Schließen', onDone);

  // ---------------------------------------------------------------------------
  // File integrity
  // ---------------------------------------------------------------------------
  let fimData = null;

  function renderFim() {
    const d = fimData;
    const c = d.config;
    $('fim-checked').textContent = c.last_scan ? `${t('Geprüft')}: ${time(c.last_scan)}` : t('Noch nicht geprüft');
    tiles($('fim-tiles'), [['Überwachte Dateien', d.watched.toLocaleString()], ['Unerklärt, sensibel', d.critical, d.critical ? 'critical' : 'good'],
      ['Unerklärt, sonstige', d.warning, d.warning ? 'warning' : 'good'], ['Zeitplan', c.enabled ? `${String(c.hour).padStart(2, '0')}:00` : t('Aus'), c.enabled ? 'good' : '']]);
    table($('fim-table'), ['Datei', 'Änderung', 'Ursache', 'Erkannt', ''], d.findings.map(f => {
      const a = el('div', 'ops-actions');
      if (f.state === 'open' && can('settings.manage')) {
        a.appendChild(button('In Ordnung', 'text-btn', () => closeWith(a, async note => {
          const r = await run(() => api('/api/fim/close', { ids: [f.id], note }));
          if (r) { fimData = r; renderFim(); }
        })));
      } else if (f.state !== 'open') a.appendChild(raw('span', 'lan-sub', f.note || f.detail || ''));
      return [raw('span', 'mono', f.path), tag(f.state === 'open' ? f.level : '', f.kind_label), tag(f.cause ? 'good' : f.state === 'open' ? 'warning' : '', f.cause_label), time(f.ts), a];
    }), c.last_scan ? 'Keine Änderungen gefunden.' : 'Der erste Lauf legt die Referenz an.');
    const box = $('fim-settings');
    if (!box.childElementCount) {
      box.appendChild(form([
        { key: 'enabled', label: 'Täglich prüfen', type: 'check' },
        { key: 'hour', label: 'Uhrzeit', type: 'select', options: HOURS },
        { key: 'paths', label: 'Überwachte Ordner (einer pro Zeile)', type: 'textarea', rows: 6 },
        { key: 'exclude', label: 'Ausnahmen als regulärer Ausdruck (einer pro Zeile)', type: 'textarea' }
      ], { ...c, paths: c.paths.join('\n'), exclude: c.exclude.join('\n') }, 'Speichern', async v => {
        fimData = await api('/api/fim/settings', { ...v, hour: Number(v.hour), paths: lines(v.paths), exclude: lines(v.exclude) });
        renderFim();
        toast(t('Gespeichert.'), 'success');
      }));
    }
  }

  async function loadFim() { try { fimData = await api('/api/fim'); renderFim(); } catch (e) { /* no permission */ } }
  $('fim-run').addEventListener('click', async () => { openTask(await run(() => api('/api/fim/scan', {}), 'Prüfung läuft – Fortschritt in der Aufgabenleiste.')); });

  // ---------------------------------------------------------------------------
  // sudo log
  // ---------------------------------------------------------------------------
  let suData = null;

  function renderSudoRows(rows, fleet) {
    const heads = fleet ? ['Zeit', 'Server', 'Benutzer', 'Befehl', 'Bewertung'] : ['Zeit', 'Benutzer', 'Befehl', 'Bewertung'];
    table($('su-table'), heads, rows.map(e => {
      const state = !e.ok ? tag('critical', e.reason || 'Abgelehnt') : e.level ? tag(e.level, e.rule) : tag('good', 'OK');
      const cmd = raw('span', 'mono', e.command);
      cmd.title = `${e.cwd || ''} · ${e.tty || ''} · ${e.runas || 'root'}`;
      return fleet ? [time(e.ts), e.server, raw('strong', '', e.user), cmd, state] : [time(e.ts), raw('strong', '', e.user), cmd, state];
    }), 'Keine Einträge im Zeitraum.');
  }

  async function loadSudo() {
    const q = new URLSearchParams({ days: $('su-days').value, user: $('su-user').value, q: $('su-q').value.trim(), flagged: $('su-flagged').checked ? 1 : 0 });
    const fleet = $('su-fleet').checked;
    try {
      const r = await api(`/api/sudo-log/${fleet ? 'fleet' : 'entries'}?${q}`);
      renderSudoRows(r.entries, fleet);
      $('su-errors').textContent = (r.errors || []).map(x => `${x.name}: ${t(x.error)}`).join(' · ');
    } catch (e) { /* no permission */ }
  }

  function renderRules() {
    const box = $('su-rules');
    box.innerHTML = '';
    box.appendChild(el('p', 'field-hint', `${t('Eingebaut')}: ${suData.builtin.map(b => t(b.name)).join(', ')}`));
    box.appendChild(form([
      { key: 'rules', label: 'Eigene Regeln: Name | regulärer Ausdruck | warning oder critical (eine pro Zeile)', type: 'textarea', rows: 4, placeholder: 'Docker-Zugriff | docker exec | warning' }
    ], { rules: suData.rules.map(r => `${r.name} | ${r.pattern} | ${r.level}`).join('\n') }, 'Speichern', async v => {
      const rules = lines(v.rules).map(l => { const p = l.split('|').map(x => x.trim()); return { name: p[0], pattern: p.slice(1, Math.max(2, p.length - 1)).join('|'), level: p.length > 2 ? p[p.length - 1] : 'warning' }; });
      suData = await api('/api/sudo-log/rules', { rules });
      renderSudo();
      toast(t('Gespeichert.'), 'success');
    }));
  }

  function renderSudo() {
    const d = suData;
    const top = d.users.slice(0, 2).map(u => [`${u.user}: ${u.denied ? `${u.denied} ${t('abgelehnt')}` : t('Befehle')}`, u.n, u.denied ? 'warning' : '']);
    tiles($('su-tiles'), [['Kritisch, ungeprüft', d.open.critical, d.open.critical ? 'critical' : 'good'], ['Auffällig, ungeprüft', d.open.warning, d.open.warning ? 'warning' : 'good'], ...top]);
    const sel = $('su-user');
    const cur = sel.value;
    sel.length = 1;
    d.users.forEach(u => { const o = raw('option', '', u.user); o.value = u.user; sel.appendChild(o); });
    sel.value = cur;
    renderRules();
    loadSudo();
  }

  async function loadSudoAll() { try { suData = await api('/api/sudo-log'); renderSudo(); } catch (e) { /* no permission */ } }
  ['su-days', 'su-user', 'su-flagged', 'su-fleet'].forEach(id => $(id).addEventListener('change', loadSudo));
  let suTimer;
  $('su-q').addEventListener('input', () => { clearTimeout(suTimer); suTimer = setTimeout(loadSudo, 300); });
  $('su-form').addEventListener('submit', e => e.preventDefault());
  $('su-reviewed').addEventListener('click', async () => {
    const r = await run(() => api('/api/sudo-log/reviewed', {}), 'Als geprüft markiert.');
    if (r) { suData = r; renderSudo(); }
  });

  // ---------------------------------------------------------------------------
  // Malware scan
  // ---------------------------------------------------------------------------
  let mwData = null;

  function renderMalware() {
    const d = mwData;
    const c = d.config;
    $('mw-checked').textContent = c.last_scan ? `${t('Geprüft')}: ${time(c.last_scan)}` : t('Noch nicht geprüft');
    tiles($('mw-tiles'), [['Offene Funde', d.open, d.open ? 'critical' : 'good'], ['Zeitplan', c.enabled ? t(WEEK[c.weekday][1]) : t('Aus'), c.enabled ? 'good' : '']]);
    const inst = $('mw-install');
    inst.innerHTML = '';
    Object.entries(d.scanners).forEach(([id, name]) => {
      if (d.installed[id] || !d.supported) return;
      inst.appendChild(button(`${name} installieren`, 'btn-secondary', async () => { await run(() => api(`/api/malware/install/${id}`, {}), 'Installation gestartet.'); }, 'users.manage'));
    });
    if (d.installed.rkhunter) inst.appendChild(button('rkhunter-Referenz aktualisieren', 'text-btn', async () => { openTask(await run(() => api('/api/malware/baseline', {}))); }, 'settings.manage'));
    table($('mw-table'), ['Scanner', 'Fund', 'Datei', 'Zuletzt gesehen', ''], d.findings.map(f => {
      const a = el('div', 'ops-actions');
      if (f.state === 'open' && can('settings.manage')) {
        a.appendChild(button('Fehlalarm', 'text-btn', () => closeWith(a, async note => {
          const r = await run(() => api('/api/malware/close', { ids: [f.id], note }));
          if (r) { mwData = r; renderMalware(); }
        })));
      } else if (f.state !== 'open') a.appendChild(raw('span', 'lan-sub', f.note));
      return [f.scanner, raw('span', '', f.name), raw('span', 'mono', f.path), time(f.last_seen), a];
    }), 'Keine Funde.');
    const box = $('mw-settings');
    if (!box.childElementCount) {
      box.appendChild(form([
        { key: 'enabled', label: 'Wöchentlich scannen', type: 'check' },
        { key: 'weekday', label: 'Wochentag', type: 'select', options: WEEK },
        { key: 'hour', label: 'Uhrzeit', type: 'select', options: HOURS },
        { key: 'scanners', label: 'Scanner', type: 'checks', options: Object.entries(d.scanners) },
        { key: 'paths', label: 'Ordner für ClamAV (einer pro Zeile)', type: 'textarea', rows: 6 }
      ], { ...c, paths: c.paths.join('\n') }, 'Speichern', async v => {
        mwData = await api('/api/malware/settings', { ...v, weekday: Number(v.weekday), hour: Number(v.hour), paths: lines(v.paths) });
        renderMalware();
        toast(t('Gespeichert.'), 'success');
      }));
    }
  }

  async function loadMalware() { try { mwData = await api('/api/malware'); renderMalware(); } catch (e) { /* no permission */ } }
  $('mw-run').addEventListener('click', async () => { openTask(await run(() => api('/api/malware/scan', {}), 'Scan läuft – Fortschritt in der Aufgabenleiste.')); });

  // ---------------------------------------------------------------------------
  // Falco
  // ---------------------------------------------------------------------------
  let faData = null;

  function renderFalco() {
    const d = faData;
    const s = d.status;
    $('fa-state').textContent = t(!s.supported ? 'Nur unter Linux' : !s.installed ? 'Nicht eingerichtet' : s.running ? 'Falco läuft' : 'Falco läuft nicht');
    $('fa-setup').hidden = !s.supported || s.running || !can('users.manage');
    table($('fa-table'), ['Zeit', 'Regel', 'Container', 'Prozess', 'Anzahl', ''], d.events.map(e => {
      const a = el('div', 'ops-actions');
      if (e.state === 'open' && can('settings.manage')) {
        a.append(button('Erledigt', 'text-btn', async () => { const r = await run(() => api('/api/falco/close', { ids: [e.id] })); if (r) { faData = r; renderFalco(); } }),
          confirmButton('Stummschalten', 'text-btn', async () => { const r = await run(() => api('/api/falco/mute', { rule: e.rule, container: e.container })); if (r) { faData = r; renderFalco(); } }));
      }
      const rule = el('div');
      rule.append(tag(e.state === 'open' ? e.level : '', e.priority), raw('span', '', ` ${e.rule}`));
      rule.title = e.output;
      return [time(e.last_ts), rule, raw('span', 'mono', e.container), raw('span', 'mono', e.process), e.count, a];
    }), s.running ? 'Keine Ereignisse.' : 'Falco ist noch nicht aktiv.');
    table($('fa-muted'), ['Regel', 'Container', ''], d.muted.map(m => [raw('span', '', m.rule), m.container || t('Alle'),
      button('Aufheben', 'text-btn', async () => { const r = await run(() => api('/api/falco/mute', { rule: m.rule, container: m.container, undo: true })); if (r) { faData = r; renderFalco(); } }, 'settings.manage')]), 'Nichts stummgeschaltet.');
  }

  async function loadFalco() { try { faData = await api('/api/falco'); renderFalco(); } catch (e) { /* no permission */ } }
  $('fa-setup').addEventListener('click', async () => { openTask(await run(() => api('/api/falco/setup', {}), 'Einrichtung läuft – Fortschritt in der Aufgabenleiste.')); });

  function loadAll() { loadFim(); loadSudoAll(); loadMalware(); loadFalco(); }
  (window.PiAuth ? window.PiAuth.ready : Promise.resolve()).then(() => {
    if (!can('security.view')) return;
    loadAll();
    setInterval(() => { if (!document.hidden && !document.activeElement.closest('#datei-integritaet, #sudo-protokoll, #malware-scan, #laufzeitschutz')) loadAll(); }, 60000);
  });
})();
