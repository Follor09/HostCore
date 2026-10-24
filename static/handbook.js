/**
 * HostCore Dashboard - print view of the emergency handbook (/notfallhandbuch): the whole handbook
 * as one document, laid out for paper; "Drucken" records the export.
 */
(function () {
  'use strict';

  const doc = document.getElementById('hb-doc');
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const STATE = { ok: 'OK', overdue: 'überfällig', error: 'Fehler', unknown: 'nicht geprüft' };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };
  const fmt = ts => (ts ? new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');

  function table(head, rows) {
    const tb = el('table', 'hb-table');
    const tr = el('tr');
    head.forEach(h => tr.appendChild(el('th', '', t(h))));
    tb.appendChild(tr);
    rows.forEach(r => {
      const row = el('tr');
      r.forEach(c => row.appendChild(raw('td', '', c == null ? '' : String(c))));
      tb.appendChild(row);
    });
    return tb;
  }

  function kv(pairs) {
    const dl = el('dl', 'hb-kv');
    pairs.filter(p => p[1]).forEach(([k, v]) => dl.append(el('dt', '', t(k)), raw('dd', '', v)));
    return dl;
  }

  function render(d) {
    doc.innerHTML = '';
    const h = d.host;
    const g = d.general || {};
    doc.appendChild(raw('h1', '', `${t('Notfallhandbuch')} – ${h.hostname}`));
    doc.appendChild(el('p', 'hb-meta', `${t('Stand')}: ${fmt(d.generated)} · ${d.summary.services} ${t('Dienste')} · ${d.summary.complete} ${t('ohne Lücken')}`));
    doc.appendChild(el('p', 'hb-note', t('Dieses Handbuch enthält keine Passwörter. Es beschreibt, wo Daten, Sicherungen und Zugangsdaten liegen und in welcher Reihenfolge die Dienste zurückkommen.')));

    doc.appendChild(el('h2', '', t('Notfallkontakte')));
    doc.appendChild(g.contacts && g.contacts.length ? table(['Name', 'Rolle', 'Telefon', 'E-Mail'], g.contacts.map(c => [c.name, c.role, c.phone, c.email]))
      : el('p', 'hb-gap', t('Keine eingetragen.')));
    doc.appendChild(el('h2', '', t('Zugangsdaten')));
    doc.appendChild(raw('p', 'hb-pre', g.credentials || t('Nicht festgehalten.')));
    if (g.location) doc.appendChild(kv([['Ablage dieses Handbuchs', g.location]]));
    if (g.notes) { doc.appendChild(el('h2', '', t('Hinweise'))); doc.appendChild(raw('p', 'hb-pre', g.notes)); }

    doc.appendChild(el('h2', '', t('Dieser Server')));
    doc.appendChild(kv([['Hostname', h.hostname], ['Modell', h.model], ['System', `${h.os} · Kernel ${h.kernel}`], ['Adresse', h.ip],
      ['Schnittstellen', Object.entries(h.interfaces || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ')],
      ['Dashboard', h.dashboard_dir]]));
    doc.appendChild(el('h3', '', t('Dashboard neu installieren')));
    doc.appendChild(raw('pre', 'hb-cmd', `git clone ${h.repo || '<Repository>'} ~/pi-dashboard\n~/pi-dashboard/install.sh`));
    doc.appendChild(el('p', '', `${t('Danach settings.json, auth.json, history.db und die Ordner gitops-env/ und db-dumps/ aus der Sicherung zurückholen und den Dienst neu starten')}: sudo systemctl restart raspberry-dashboard`));

    doc.appendChild(el('h2', '', t('Wiederanlauf-Reihenfolge')));
    const ol = el('ol');
    Object.entries(d.priorities).forEach(([p, label]) => {
      const names = d.services.filter(s => String(s.priority) === p).map(s => s.name);
      if (names.length) { const li = el('li'); li.append(el('strong', '', `${t(label)}: `), raw('span', '', names.join(', '))); ol.appendChild(li); }
    });
    doc.appendChild(ol);

    d.services.forEach(s => {
      const sec = el('section', 'hb-svc');
      sec.appendChild(raw('h2', '', s.name));
      const p = s.profile;
      const pairs = [['Priorität', t(d.priorities[s.priority])], ['Abhängig von', s.depends], ['Zweck', p.purpose], ['Adresse', p.url], ['Ports', p.ports],
        ['Zuständig', p.owner], ['Daten', p.data], ['Sicherung (Steckbrief)', p.backup], ['Dokumentation', p.docs_url], ['Notizen', p.notes]];
      if (s.stack) pairs.push(['Ordner', s.stack.dir], ['Images', s.stack.images.join(', ')], ['Volumes', s.stack.volumes.join(', ')], ['Host-Ordner', s.stack.folders.join(', ')]);
      if (s.gitops) pairs.push(['Git', `${s.gitops.url} · ${s.gitops.branch}${s.gitops.path ? ` · ${s.gitops.path}` : ''}${s.gitops.deployed ? ` · ${s.gitops.deployed.slice(0, 8)}` : ''}`]);
      s.databases.forEach(db => pairs.push([`Datenbank ${db.name}`, `${db.engine} · ${db.last ? `${db.last.name} (${fmt(db.last.ts)})` : t('kein Dump')} · ${db.folder}`]));
      s.backups.forEach(b => pairs.push(['Sicherung', `${b.name} (${b.kind}) · ${b.target} · ${t(STATE[b.state] || b.state)} · ${fmt(b.last)}`]));
      sec.appendChild(kv(pairs));
      sec.appendChild(el('h3', '', t('Wiederherstellen')));
      const steps = el('ol', 'hb-steps');
      s.restore.forEach(st => {
        const li = el('li');
        li.appendChild(raw('span', '', st.text));
        if (st.cmd) li.appendChild(raw('pre', 'hb-cmd', st.cmd));
        steps.appendChild(li);
      });
      sec.appendChild(steps);
      if (s.runbook) {
        sec.appendChild(raw('h3', '', `Runbook „${s.runbook.title}“`));
        const ul = el('ul', 'hb-check');
        s.runbook.steps.forEach(x => ul.appendChild(raw('li', '', x)));
        sec.appendChild(ul);
      }
      if (s.gaps.length) sec.appendChild(el('p', 'hb-gap', `${t('Lücken')}: ${s.gaps.map(t).join('; ')}`));
      doc.appendChild(sec);
    });

    if (d.backups.length) {
      doc.appendChild(el('h2', '', t('Sicherungen')));
      doc.appendChild(table(['Name', 'Art', 'Ziel', 'Letzte', 'Zustand'], d.backups.map(b => [b.name, b.kind, b.target, fmt(b.last_backup), t(STATE[b.state] || b.state)])));
    }
    if (d.contracts.length) {
      doc.appendChild(el('h2', '', t('Anbieter & Verträge')));
      doc.appendChild(table(['Name', 'Anbieter', 'Art', 'Kundenkonto', 'Notizen'], d.contracts.map(c => [c.name, c.provider, c.category, c.link, c.notes])));
    }
    if (d.domains.length) {
      doc.appendChild(el('h2', '', t('Domains')));
      doc.appendChild(table(['Domain', 'Registrar', 'Läuft ab', 'Nameserver'], d.domains.map(x => [x.unicode, x.registrar, x.expiry, (x.nameservers || []).join(', ')])));
    }
    if (d.inventory.length) {
      doc.appendChild(el('h2', '', t('Hardware')));
      doc.appendChild(table(['Gerät', 'Kategorie', 'Modell', 'Seriennummer', 'Standort', 'Garantie bis'],
        d.inventory.map(x => [x.name, x.category, [x.manufacturer, x.model].filter(Boolean).join(' '), x.serial, x.location, x.warranty_until])));
    }
  }

  document.getElementById('hb-do-print').addEventListener('click', async () => {
    try { await fetch('/api/docs/handbook/exported', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch (e) { /* print anyway */ }
    window.print();
  });

  fetch('/api/docs/handbook?refresh=1').then(r => r.json()).then(render).catch(() => { doc.textContent = t('Laden fehlgeschlagen.'); });
})();
