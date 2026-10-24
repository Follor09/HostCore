/**
 * PiCore Dashboard - long pages (System-Werkzeuge, Einstellungen, Container, Dokumentation) split into groups.
 *
 * The page starts with an overview: one tile per group with its state (warnings from the status summary
 * plus a few key figures) and links to its sections. Tabs switch between the groups; only the sections of
 * the open group are shown. The address says what is open, so old deep links keep working:
 *   #start          overview
 *   #g-<group>      a group
 *   #<section id>   the group that contains the section, scrolled to it
 * The page's own section list (.settings-nav, mirrored in the sidebar) becomes the list of groups.
 * Must be loaded before sidebar.js.
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LEVELS = { good: 0, info: 1, warning: 2, critical: 3 };

  async function get(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  const n = (v, one, many) => `${v} ${v === 1 ? one : many}`;

  // ---------------------------------------------------------------------------
  // Groups per page. facts(): [{level, text}] for the tile (each failing request is simply left out)
  // ---------------------------------------------------------------------------
  const PAGES = {
    '/system': {
      title: 'System-Werkzeuge',
      groups: [
        { id: 'zustand', label: 'Zustand', desc: 'Alarme, Ereignisse, Verfügbarkeit und Auslastung', sections: ['alarme', 'ereignisse', 'auffaelligkeiten', 'verfuegbarkeit', 'ressourcen', 'kapazitaet'],
          facts: async () => {
            const [e, al] = await Promise.all([get('/api/events?days=1'), get('/api/alerts').catch(() => null)]);
            const c = e.counts || {};
            const open = al && al.counts ? al.counts.open : 0;
            if (open) return [{ level: 'critical', text: `${n(open, 'Alarm', 'Alarme')} nicht quittiert` }];
            return [c.critical ? { level: 'critical', text: `${n(c.critical, 'kritisches Ereignis', 'kritische Ereignisse')} (24 Std.)` }
              : c.warning ? { level: 'warning', text: `${n(c.warning, 'Warnung', 'Warnungen')} (24 Std.)` }
                : { level: 'good', text: 'Keine Warnungen in 24 Std.' }];
          } },
        { id: 'netzwerk', label: 'Netzwerk', desc: 'Schnittstellen, Geräte, Datenverbrauch, DNS und Ports', sections: ['netzwerk', 'geraete', 'datenverbrauch', 'dns', 'dns-ueberwachung', 'port-check', 'netbox'],
          facts: async () => {
            const lan = await get('/api/tools/lan');
            const devs = lan.devices || [];
            const fresh = devs.filter(d => d.new).length;
            return [{ level: 'info', text: `${devs.filter(d => d.online).length} von ${devs.length} Geräten online` },
              ...(fresh ? [{ level: 'info', text: n(fresh, 'neues Gerät', 'neue Geräte') }] : [])];
          } },
        { id: 'sicherheit', label: 'Sicherheit', desc: 'Firewall, Verbindungen, SSH-Anmeldungen, Härtung, Aktivität', sections: ['firewall', 'firewall-traffic', 'verbindungen', 'ssh', 'haertung', 'aktivitaet'], perm: 'security.view',
          facts: async () => {
            const out = [];
            const [fw, ssh, hd] = await Promise.allSettled([get('/api/tools/firewall'), get('/api/tools/ssh'), get('/api/hardening')]);
            if (hd.status === 'fulfilled' && hd.value.score != null) {
              const h = hd.value;
              out.push({ level: h.critical ? 'critical' : h.failed ? 'warning' : 'good', text: `Härtung ${h.score} %` });
            }
            if (fw.status === 'fulfilled') {
              const f = fw.value;
              out.push(!f.installed ? { level: 'info', text: 'Firewall (ufw) nicht installiert' }
                : f.active ? { level: 'good', text: `Firewall aktiv · ${n((f.rules || []).length, 'Regel', 'Regeln')}` }
                  : { level: 'warning', text: 'Firewall ausgeschaltet' });
            }
            if (ssh.status === 'fulfilled' && ssh.value.supported) {
              const day = Date.now() / 1000 - 86400;
              const fails = (ssh.value.events || []).filter(e => e.kind === 'fail' && e.ts >= day).reduce((s, e) => s + (e.attempts || 1), 0);
              out.push({ level: fails >= 20 ? 'warning' : 'info', text: `${n(fails, 'fehlgeschlagene SSH-Anmeldung', 'fehlgeschlagene SSH-Anmeldungen')} (24 Std.)` });
            }
            return out;
          } },
        { id: 'hardware', label: 'Hardware & Strom', desc: 'Sensoren, Stromverbrauch, Drosselung, Lüfter, CPU-Modus', sections: ['hardware', 'sensoren', 'strom', 'drosselung', 'luefterkurve', 'cpu-modus', 'leistungstest'],
          facts: async () => {
            const s = await get('/api/system');
            const out = [];
            const temp = s.temperature || {};
            if (temp.celsius != null && !temp.is_simulated) out.push({ level: temp.celsius >= 80 ? 'critical' : temp.celsius >= 70 ? 'warning' : 'good', text: `CPU ${Math.round(temp.celsius)} °C` });
            if (s.power && s.power.available && s.power.board_watt != null) out.push({ level: 'info', text: `${s.power.board_watt.toFixed(1)} W` });
            if (s.cpu) out.push({ level: 'info', text: `CPU-Last ${Math.round(s.cpu.percent)} %` });
            if (s.throttled && (s.throttled.undervoltage || s.throttled.throttled)) out.push({ level: 'critical', text: s.throttled.undervoltage ? 'Unterspannung' : 'Gedrosselt' });
            return out;
          } },
        { id: 'speicher', label: 'Speicher & Backups', desc: 'Füllstände, Prognose und überwachte Backups', sections: ['speicherprognose', 'backups'],
          facts: async () => {
            const out = [];
            const [drives, bk] = await Promise.allSettled([get('/api/drives'), get('/api/backups')]);
            if (drives.status === 'fulfilled') {
              const root = (drives.value.drives || []).find(d => d.mount === '/');
              if (root) out.push({ level: root.percent >= 95 ? 'critical' : root.percent >= 85 ? 'warning' : 'good', text: `/ zu ${Math.round(root.percent)} % belegt` });
            }
            if (bk.status === 'fulfilled' && bk.value.jobs.length) {
              const p = bk.value.problems;
              out.push(p ? { level: 'warning', text: `${n(p, 'Backup', 'Backups')} mit Problem` } : { level: 'good', text: `${n(bk.value.jobs.length, 'Backup', 'Backups')} aktuell` });
            }
            return out;
          } },
        { id: 'logs', label: 'Logs', desc: 'Fehler-Übersicht und Log-Suche', sections: ['fehler', 'logs'], perm: 'logs.view',
          facts: async () => {
            const d = await get('/api/tools/errors?days=1');
            if (!d.supported) return [];
            const total = (d.groups || []).reduce((s, g) => s + g.count, 0);
            return [{ level: total ? 'warning' : 'good', text: total ? `${n(total, 'Fehlermeldung', 'Fehlermeldungen')} (24 Std.)` : 'Keine Fehler in 24 Std.' }];
          } },
        { id: 'wartung', label: 'Pakete & Wartung', desc: 'Paketverwaltung und Speicher aufräumen', sections: ['pakete', 'aufraeumen'],
          facts: async () => {
            const u = (await get('/api/homelab')).updates || {};
            if (!u.supported) return [];
            return [u.security_count ? { level: 'warning', text: `${n(u.security_count, 'Sicherheitsupdate', 'Sicherheitsupdates')}` }
              : u.count ? { level: 'info', text: `${n(u.count, 'Update', 'Updates')} verfügbar` } : { level: 'good', text: 'System aktuell' },
            ...(u.reboot_required ? [{ level: 'info', text: 'Neustart nötig' }] : [])];
          } },
        { id: 'software', label: 'Schwachstellen & Support', desc: 'Bekannte Sicherheitslücken, Support-Ende und Software-Inventar', sections: ['schwachstellen', 'lebenszyklus', 'software-inventar'],
          facts: async () => {
            const out = [];
            const [vu, eol] = await Promise.allSettled([get('/api/vulns'), get('/api/eol')]);
            if (vu.status === 'fulfilled' && vu.value.checked) {
              const v = vu.value;
              const serious = v.packages.serious_fixable + v.images.reduce((s, i) => s + (i.serious_fixable || 0), 0);
              out.push(serious ? { level: 'warning', text: n(serious, 'schwere Lücke mit Update', 'schwere Lücken mit Update') }
                : { level: 'good', text: 'Keine schweren Lücken mit Update' });
            }
            if (eol.status === 'fulfilled' && eol.value.checked) {
              const items = eol.value.items;
              const ended = items.filter(i => i.state === 'expired').length;
              const soon = items.filter(i => i.state === 'critical' || i.state === 'warning').length;
              out.push(ended ? { level: 'critical', text: n(ended, 'Komponente ohne Support', 'Komponenten ohne Support') }
                : soon ? { level: 'warning', text: n(soon, 'Support-Ende in 90 Tagen', 'Support-Enden in 90 Tagen') }
                  : { level: 'good', text: 'Alles mit Support' });
            }
            return out;
          } }
      ]
    },
    '/settings': {
      title: 'Einstellungen',
      groups: [
        { id: 'konto', label: 'Konto & Zugang', desc: 'Eigenes Konto, Zwei-Faktor, Passkeys, Sitzungen, Benutzer, Berechtigungen, Sicherheit und Freigabe-Links',
          sections: ['konto', 'zwei-faktor', 'passkeys', 'sitzungen', 'benutzer', 'berechtigungen', 'sicherheit', 'freigaben'],
          facts: async () => {
            const out = [];
            const [tf, sec] = await Promise.allSettled([get('/api/auth/2fa'), get('/api/security')]);
            if (tf.status === 'fulfilled') out.push(tf.value.enabled ? { level: 'good', text: 'Zwei-Faktor aktiv' } : { level: tf.value.required ? 'critical' : 'info', text: 'Zwei-Faktor nicht eingerichtet' });
            if (sec.status === 'fulfilled') {
              const open = sec.value.checklist.filter(c => c.state === 'warning').length;
              out.push(open ? { level: 'warning', text: n(open, 'offener Punkt für öffentlichen Betrieb', 'offene Punkte für öffentlichen Betrieb') } : { level: 'good', text: 'Bereit für öffentlichen Betrieb' });
            }
            return out;
          } },
        { id: 'dashboard', label: 'Dashboard', desc: 'Sprache, Darstellung, Wetter, Dienste, Links und Wake-on-LAN', sections: ['sprache', 'darstellung', 'wetter', 'dienste', 'links', 'wake-on-lan'] },
        { id: 'ueberwachung', label: 'Überwachung', desc: 'Web-Dienste, Alarmierung, Wartungsfenster und öffentliche Statusseite', sections: ['web-dienste', 'alarmierung', 'wartung', 'statusseite'],
          facts: async () => {
            const h = await get('/api/health');
            const checks = h.checks || [];
            const down = checks.filter(c => c.last && !c.last.ok).length;
            return checks.length ? [{ level: down ? 'warning' : 'good', text: down ? `${down} von ${checks.length} Web-Diensten nicht erreichbar` : `${n(checks.length, 'Web-Dienst', 'Web-Dienste')} erreichbar` }] : [];
          } },
        { id: 'verbindungen', label: 'Verbindungen', desc: 'Remote-Pis, NetBox, MCP-Server, Webhooks und E-Mail', sections: ['remote', 'netbox', 'mcp', 'mcp-aktivitaet', 'webhooks', 'email'],
          facts: async () => {
            const out = [];
            const [mcp, mail, ap] = await Promise.allSettled([get('/api/settings/mcp'), get('/api/settings/email'), get('/api/mcp/approvals')]);
            if (mcp.status === 'fulfilled' && mcp.value.enabled) out.push(mcp.value.running ? { level: 'good', text: 'MCP-Server läuft' } : { level: 'warning', text: 'MCP-Server gestört' });
            if (ap.status === 'fulfilled' && ap.value.pending) out.push({ level: 'warning', text: `${n(ap.value.pending, 'MCP-Freigabe wartet', 'MCP-Freigaben warten')}` });
            if (mail.status === 'fulfilled' && mail.value.enabled) out.push(mail.value.last_error ? { level: 'warning', text: 'E-Mail-Versand gestört' } : { level: 'good', text: 'E-Mail aktiv' });
            return out;
          } },
        { id: 'system', label: 'System & Daten', desc: 'Auto-Update, Verlauf, Datenaufbewahrung, Selbstdiagnose, API und Systeminfo', sections: ['auto-update', 'verlauf', 'datenaufbewahrung', 'selbstdiagnose', 'api', 'system'],
          facts: async () => {
            const d = await get('/api/selftest');
            if (!d.checked) return [];
            const c = d.counts;
            return [c.critical ? { level: 'critical', text: `Selbstdiagnose: ${n(c.critical, 'Problem', 'Probleme')}` }
              : c.warning ? { level: 'warning', text: `Selbstdiagnose: ${n(c.warning, 'Warnung', 'Warnungen')}` }
                : { level: 'good', text: 'Selbstdiagnose ohne Befund' }];
          } }
      ]
    },
    '/containers': {
      title: 'Container',
      overview: ['uebersicht'],   // stays visible on the overview below the tiles
      groups: [
        { id: 'docker', label: 'Docker', desc: 'Container, Compose-Stacks, Live-Logs, Volumes und Aufräumen', sections: ['docker', 'stacks', 'live-logs', 'docker-volumes', 'docker-aufraeumen'],
          facts: async () => {
            const o = await get('/api/containers/overview');
            if (!o.docker || !o.docker.available) return [{ level: 'info', text: 'Docker nicht verfügbar' }];
            const cs = o.docker.containers || [];
            const running = cs.filter(c => c.state === 'running').length;
            return [{ level: running < cs.length ? 'info' : 'good', text: `${running} von ${cs.length} Containern laufen` },
              { level: 'info', text: n((o.stacks || []).length, 'Stack', 'Stacks') }];
          } },
        { id: 'datenbanken', label: 'Datenbanken', desc: 'PostgreSQL, MySQL und MariaDB in Containern: Zustand, Größe und Dumps', sections: ['datenbanken'],
          facts: async () => {
            const d = await get('/api/containers/databases');
            const dbs = d.databases || [];
            if (!dbs.length) return [{ level: 'info', text: 'Keine Datenbank-Container gefunden' }];
            const probs = dbs.flatMap(x => x.problems || []);
            const worst = probs.some(p => p.level === 'critical') ? 'critical' : probs.length ? 'warning' : 'good';
            return [{ level: worst, text: probs.length ? `${n(probs.length, 'Problem', 'Probleme')} bei ${n(dbs.length, 'Datenbank', 'Datenbanken')}` : `${n(dbs.length, 'Datenbank', 'Datenbanken')} in Ordnung` }];
          } },
        { id: 'kubernetes', label: 'Kubernetes', desc: 'Workloads, Pods, Services und Nodes', sections: ['kubernetes'],
          facts: async () => {
            const o = await get('/api/containers/overview');
            if (!o.kube || !o.kube.available) return [{ level: 'info', text: 'Kein Cluster erreichbar' }];
            const pods = o.kube.pods || [];
            const bad = pods.filter(p => !['Running', 'Succeeded'].includes(p.phase || p.status)).length;
            return [{ level: bad ? 'warning' : 'good', text: bad ? `${bad} von ${pods.length} Pods nicht bereit` : `${n(pods.length, 'Pod', 'Pods')} laufen` }];
          } },
        { id: 'images', label: 'Image-Updates', desc: 'Neue Versionen und Versionshinweise', sections: ['images'],
          facts: async () => {
            const im = (await get('/api/containers/overview')).images || {};
            if (!im.checked) return [{ level: 'info', text: 'Noch nicht geprüft' }];
            return [im.updates ? { level: 'warning', text: `${n(im.updates, 'Update', 'Updates')} verfügbar` } : { level: 'good', text: 'Alle Images aktuell' }];
          } },
        { id: 'vorlagen', label: 'Stack-Vorlagen', desc: 'Eigene Compose-Vorlagen auf beliebigen Pis anlegen', sections: ['stack-vorlagen'] },
        { id: 'statistik', label: 'Statistik', desc: 'CPU, RAM und Netzwerk pro Container', sections: ['statistik'] },
        { id: 'apps', label: 'App-Store', desc: 'Apps als Compose-Stack einrichten', sections: ['apps'] }
      ]
    },
    '/doku': {
      title: 'Dokumentation',
      groups: [
        { id: 'bestand', label: 'Bestand', desc: 'Hardware-Inventar und Steckbriefe der Dienste', sections: ['inventar', 'steckbriefe'], alias: /^#steckbrief=/,
          facts: async () => {
            const inv = await get('/api/docs/inventory');
            const items = inv.items || [];
            const warr = items.filter(i => i.warranty_days != null && i.warranty_days >= 0 && i.warranty_days <= 30).length;
            return [{ level: 'info', text: n(items.length, 'Gerät', 'Geräte') }, ...(warr ? [{ level: 'info', text: `${n(warr, 'Garantie endet', 'Garantien enden')} bald` }] : [])];
          } },
        { id: 'betrieb', label: 'Betrieb', desc: 'Störungen, Betriebstagebuch, Runbooks und Befehle', sections: ['stoerungen', 'tagebuch', 'runbooks', 'befehle'],
          facts: async () => {
            const [jr, rb] = await Promise.allSettled([get('/api/docs/journal?limit=1'), get('/api/docs/runbooks')]);
            const out = [];
            if (jr.status === 'fulfilled') out.push({ level: 'info', text: n(jr.value.total || 0, 'Tagebucheintrag', 'Tagebucheinträge') });
            if (rb.status === 'fulfilled') {
              const running = rb.value.runbooks.filter(r => r.running).length;
              out.push({ level: running ? 'warning' : 'info', text: running ? `${n(running, 'Runbook läuft', 'Runbooks laufen')}` : n(rb.value.runbooks.length, 'Runbook', 'Runbooks') });
            }
            return out;
          } },
        { id: 'vertraege', label: 'Verträge & Kosten', desc: 'Verträge, Domains und Homelab-Kosten', sections: ['vertraege', 'domains', 'kosten'],
          facts: async () => {
            const [ct, dm] = await Promise.allSettled([get('/api/docs/contracts'), get('/api/docs/domains')]);
            const out = [];
            if (ct.status === 'fulfilled') {
              const c = ct.value;
              out.push(c.notice_soon ? { level: 'warning', text: `${n(c.notice_soon, 'Kündigungsfrist', 'Kündigungsfristen')} in 30 Tagen` }
                : { level: 'info', text: `${n(c.items.length, 'Vertrag', 'Verträge')} · ${c.monthly_total.toLocaleString((window.I18N && window.I18N.locale) || 'de-DE', { style: 'currency', currency: 'EUR' })} / Monat` });
            }
            if (dm.status === 'fulfilled' && dm.value.domains.length) {
              out.push(dm.value.warnings ? { level: 'warning', text: `${n(dm.value.warnings, 'Domain läuft', 'Domains laufen')} bald ab` } : { level: 'good', text: `${n(dm.value.domains.length, 'Domain', 'Domains')} in Ordnung` });
            }
            return out;
          } },
        { id: 'aenderungen', label: 'Änderungen', desc: 'Versionsverlauf, Vergleich und gelöschte Einträge', sections: ['aenderungen'],
          facts: async () => {
            const [rc, dl] = await Promise.allSettled([get('/api/versions/recent?limit=200'), get('/api/versions/deleted')]);
            const out = [];
            if (rc.status === 'fulfilled') {
              const week = Date.now() / 1000 - 7 * 86400;
              out.push({ level: 'info', text: n(rc.value.items.filter(i => i.ts >= week).length, 'Änderung in 7 Tagen', 'Änderungen in 7 Tagen') });
            }
            if (dl.status === 'fulfilled' && dl.value.items.length) out.push({ level: 'info', text: n(dl.value.items.length, 'gelöschter Eintrag', 'gelöschte Einträge') });
            return out;
          } }
      ]
    }
  };

  const cfg = PAGES[location.pathname.replace(/\/$/, '')];
  const container = document.querySelector('.settings-sections');
  if (!cfg || !container) return;

  const sections = [...container.querySelectorAll(':scope > .settings-section')];
  const byId = Object.fromEntries(sections.map(s => [s.id, s]));
  const groups = cfg.groups.map(g => ({ ...g, sections: g.sections.filter(id => byId[id]) }));
  const assigned = new Set(groups.flatMap(g => g.sections).concat(cfg.overview || []));
  const rest = sections.filter(s => !assigned.has(s.id)).map(s => s.id);
  if (rest.length) groups.push({ id: 'weitere', label: 'Weitere', desc: '', sections: rest });
  const groupOf = id => groups.find(g => g.sections.includes(id));
  const title = id => {
    const h = byId[id] && byId[id].querySelector('h2');
    return h ? h.textContent.trim() : id;
  };
  // A section is available unless permissions / capabilities hid it (they use the hidden attribute)
  const available = id => byId[id] && !byId[id].hidden;
  const groupAvailable = g => g.sections.some(available);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // ---------------------------------------------------------------------------
  // Building blocks: tabs, overview, group head; the page's section list becomes the group list
  // ---------------------------------------------------------------------------
  document.body.classList.add('pg-grouped');
  const tabs = el('nav', 'pg-tabs');
  tabs.setAttribute('aria-label', t('Gruppen'));
  const overview = el('div', 'pg-overview');
  const head = el('div', 'pg-head');
  container.prepend(tabs, overview, head);
  // The tabs stick right below the (sticky) page header
  const header = document.querySelector('.app-header');
  const stick = () => {
    const sticky = header && getComputedStyle(header).position === 'sticky';
    tabs.style.setProperty('--pg-top', `${sticky ? header.offsetHeight + 8 : 8}px`);
  };
  stick();
  window.addEventListener('resize', stick);

  const nav = document.querySelector('.settings-nav');
  if (nav) {
    nav.innerHTML = '';
    [{ id: 'start', label: 'Übersicht' }, ...groups].forEach(g => {
      const a = el('a', '', t(g.label));
      a.href = g.id === 'start' ? '#start' : `#g-${g.id}`;
      a.dataset.group = g.id;
      nav.appendChild(a);
    });
  }
  [{ id: 'start', label: 'Übersicht' }, ...groups].forEach(g => {
    const a = el('a', 'pg-tab', t(g.label));
    a.href = g.id === 'start' ? '#start' : `#g-${g.id}`;
    a.dataset.group = g.id;
    tabs.appendChild(a);
  });

  const tiles = {};
  function buildOverview() {
    overview.innerHTML = '';
    const grid = el('div', 'pg-grid');
    groups.forEach(g => {
      const tile = el('a', 'pg-tile');
      tile.href = `#g-${g.id}`;
      tile.dataset.group = g.id;
      const top = el('div', 'pg-tile-top');
      top.append(el('span', 'pg-tile-title', t(g.label)), el('span', 'pg-tile-dot'));
      tile.appendChild(top);
      if (g.desc) tile.appendChild(el('span', 'pg-tile-desc', t(g.desc)));
      const facts = el('ul', 'pg-facts');
      tile.appendChild(facts);
      const links = el('div', 'pg-tile-links');
      g.sections.filter(available).forEach(id => {
        const l = el('span', 'pg-chip', title(id));
        l.dataset.target = id;
        links.appendChild(l);
      });
      tile.appendChild(links);
      tiles[g.id] = { tile, facts, warnings: [], extra: [] };
      grid.appendChild(tile);
    });
    overview.appendChild(grid);
    // Section chips jump straight to the section
    grid.addEventListener('click', e => {
      const chip = e.target.closest('.pg-chip');
      if (!chip) return;
      e.preventDefault();
      location.hash = chip.dataset.target;
    });
  }

  function renderTile(g) {
    const tl = tiles[g.id];
    if (!tl) return;
    tl.tile.hidden = !groupAvailable(g);
    const items = [...tl.warnings, ...tl.extra];
    tl.facts.innerHTML = '';
    items.slice(0, 4).forEach(f => {
      const li = el('li', `pg-fact ${f.level || 'info'}`, t(f.text));
      tl.facts.appendChild(li);
    });
    const worst = items.reduce((m, f) => Math.max(m, LEVELS[f.level] || 0), 0);
    tl.tile.dataset.level = ['good', 'info', 'warning', 'critical'][worst] || 'info';
    if (!items.length) tl.tile.dataset.level = '';
    // Section chips: only those the user may see
    tl.tile.querySelectorAll('.pg-chip').forEach(c => { c.hidden = !available(c.dataset.target); });
  }

  let factsLoaded = 0;
  async function loadFacts() {
    if (Date.now() - factsLoaded < 60000) return;
    factsLoaded = Date.now();
    // Warnings of the status summary that point to a section of this page
    try {
      const sum = await get('/api/status-summary');
      groups.forEach(g => { if (tiles[g.id]) tiles[g.id].warnings = []; });
      (sum.items || []).forEach(it => {
        const m = (it.link || '').match(/^([^#]*)#(.+)$/);
        if (!m || (m[1] && m[1].replace(/\/$/, '') !== location.pathname.replace(/\/$/, ''))) return;
        const g = groupOf(m[2]);
        if (g && tiles[g.id] && it.level !== 'good') tiles[g.id].warnings.push({ level: it.level, text: it.text });
      });
    } catch (e) { /* not logged in or offline */ }
    groups.forEach(renderTile);
    await Promise.all(groups.map(async g => {
      if (!g.facts || !tiles[g.id] || !groupAvailable(g) || (g.perm && window.PiAuth && !window.PiAuth.can(g.perm))) return;
      try { tiles[g.id].extra = await g.facts(); } catch (e) { tiles[g.id].extra = []; }
      renderTile(g);
    }));
  }

  function showHead(g) {
    head.innerHTML = '';
    if (!g) return;
    const row = el('div', 'pg-head-row');
    row.appendChild(el('h2', 'pg-head-title', t(g.label)));
    if (g.desc) row.appendChild(el('span', 'pg-head-desc', t(g.desc)));
    head.appendChild(row);
    const list = g.sections.filter(available);
    if (list.length > 1) {
      const chips = el('div', 'pg-head-links');
      list.forEach(id => {
        const a = el('a', 'pg-chip', title(id));
        a.href = `#${id}`;
        chips.appendChild(a);
      });
      head.appendChild(chips);
    }
  }

  // ---------------------------------------------------------------------------
  // Routing
  // ---------------------------------------------------------------------------
  let current = null;
  let userScrolled = false;
  ['wheel', 'touchmove', 'keydown', 'mousedown'].forEach(ev => window.addEventListener(ev, () => { userScrolled = true; }, { passive: true }));
  function show(groupId, scrollTo) {
    const g = groups.find(x => x.id === groupId) || null;
    current = g ? g.id : 'start';
    document.body.classList.toggle('pg-on-overview', !g);
    const visible = new Set(g ? g.sections : (cfg.overview || []));
    sections.forEach(s => s.classList.toggle('pg-off', !visible.has(s.id)));
    overview.hidden = !!g;
    head.hidden = !g;
    showHead(g);
    // "pg-active", not "active": the pages' scroll observers reset "active" on the links of .settings-nav
    [...tabs.children, ...(nav ? nav.children : [])].forEach(a => {
      const on = a.dataset.group === current;
      a.classList.toggle('pg-active', on);
      if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
    if (!g) loadFacts();
    window.dispatchEvent(new Event('resize'));   // charts in sections that were hidden measure again
    const target = scrollTo && byId[scrollTo];
    requestAnimationFrame(() => {
      if (target) target.scrollIntoView({ block: 'start' });
      else window.scrollTo({ top: 0 });
    });
    // Sections above the target fill up after loading and push it down: follow it a few times,
    // unless the user scrolled on their own in between
    if (target) {
      userScrolled = false;
      [400, 1200, 2500].forEach(ms => setTimeout(() => { if (!userScrolled && current === g.id) target.scrollIntoView({ block: 'start' }); }, ms));
    }
    const active = tabs.querySelector('.pg-active');
    if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function route() {
    const h = location.hash;
    if (!h || h === '#' || h === '#start') return show(null);
    if (h.startsWith('#g-')) return show(h.slice(3));
    const id = decodeURIComponent(h.slice(1));
    const g = groupOf(id);
    if (g) return show(g.id, id);
    if ((cfg.overview || []).includes(id)) return show(null, id);
    const alias = groups.find(x => x.alias && x.alias.test(h));
    if (alias) return show(alias.id);
    if (current === null) show(null);   // other hashes (#do=…) belong to the page's own code
  }

  // Links to a section of a hidden group: open the group first (also for clicks that don't change the hash)
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    const id = decodeURIComponent(a.getAttribute('href').slice(1));
    const g = groupOf(id);
    if (g && current !== g.id) show(g.id, id);
  }, true);

  window.addEventListener('hashchange', route);
  buildOverview();
  route();
  // Permissions and capabilities hide sections a moment later: refresh tiles and chips
  const refresh = () => { groups.forEach(renderTile); tabs.querySelectorAll('.pg-tab').forEach(a => { const g = groups.find(x => x.id === a.dataset.group); if (g) a.hidden = !groupAvailable(g); });
    if (nav) nav.querySelectorAll('a').forEach(a => { const g = groups.find(x => x.id === a.dataset.group); if (g) a.hidden = !groupAvailable(g); });
    if (current && current !== 'start') showHead(groups.find(x => x.id === current)); };
  if (window.PiAuth && window.PiAuth.ready) window.PiAuth.ready.then(() => { refresh(); if (current === 'start') { factsLoaded = 0; loadFacts(); } });
  setTimeout(refresh, 1500);
  sections.forEach(s => new MutationObserver(refresh).observe(s, { attributes: true, attributeFilter: ['hidden'] }));
  setInterval(() => { if (!document.hidden && current === 'start') loadFacts(); }, 60000);
})();
