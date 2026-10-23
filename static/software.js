/**
 * PiCore Dashboard - system page sections "Schwachstellen" (CVEs in packages and container images),
 * "Lebenszyklus" (end of security support of the installed software) and "Software-Inventar"
 * (searchable SBOM of this Pi or the whole fleet, export as CycloneDX / CSV).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const SEV = { critical: ['critical', 'Kritisch'], high: ['critical', 'Hoch'], medium: ['warning', 'Mittel'], low: ['', 'Niedrig'], unknown: ['', 'Unbewertet'] };
  const PAGE = 50;
  let vu = null;
  let eolData = null;
  let shown = PAGE;
  const open = new Set();

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const fmtDate = iso => new Date(`${iso}T12:00:00`).toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });

  function button(label, cls, onClick) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => onClick(b));
    return b;
  }

  function sevTag(sev) {
    const [cls, label] = SEV[sev] || SEV.unknown;
    return el('span', `health-tag ${cls}`, t(label));
  }

  function cveLink(v) {
    const url = v.url || (/^CVE-/.test(v.id) ? `https://security-tracker.debian.org/tracker/${v.id}` : '');
    const a = raw(url ? 'a' : 'span', 'mono', v.id);
    if (url) { a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    return a;
  }

  // ---------------------------------------------------------------------------
  // Accept a CVE (with a reason) / take it back
  // ---------------------------------------------------------------------------
  function acceptControl(v) {
    const box = el('span', 'vu-accept');
    if (!can('settings.manage')) return box;
    if (v.ignored) {
      box.appendChild(button('Wieder zählen', 'text-btn', b => setIgnored(v.id, false, '', b)));
      return box;
    }
    box.appendChild(button('Akzeptieren', 'text-btn', () => {
      box.innerHTML = '';
      const note = el('input');
      note.type = 'text';
      note.maxLength = 300;
      note.placeholder = t('Begründung, z.B. nicht erreichbar von außen');
      note.setAttribute('aria-label', t('Begründung'));
      box.append(note, button('Speichern', 'text-btn', b => setIgnored(v.id, true, note.value.trim(), b)),
        button('Abbrechen', 'text-btn', () => renderVulns()));
      note.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); setIgnored(v.id, true, note.value.trim()); } });
      note.focus();
    }));
    return box;
  }

  async function setIgnored(id, on, note, b) {
    if (b) b.disabled = true;
    try {
      vu = await api('/api/vulns/ignore', { id, note, ignore: on });
      renderVulns();
    } catch (err) { toast(err.message, 'error'); if (b) b.disabled = false; }
  }

  // ---------------------------------------------------------------------------
  // Vulnerabilities
  // ---------------------------------------------------------------------------
  function renderTiles() {
    const p = vu.packages;
    const imgSerious = vu.images.reduce((s, i) => s + (i.serious_fixable || 0), 0);
    const tiles = [
      ['Schwer, mit Update', String(p.serious_fixable + imgSerious), p.serious_fixable + imgSerious ? 'warning' : ''],
      ['Pakete: mit Update', String(p.fixable), ''],
      ['Pakete: alle Lücken', String(p.total), ''],
      ['Images geprüft', vu.tools.trivy ? String(vu.images.length) : '–', '']
    ];
    const box = $('vu-tiles');
    box.innerHTML = '';
    tiles.forEach(([label, value, cls]) => {
      const tile = el('div', 'bench-value');
      tile.append(el('span', 'label', t(label)), raw('span', `value ${cls}`, value));
      box.appendChild(tile);
    });
  }

  function packageRows() {
    const show = $('vu-show').value;
    const q = $('vu-query').value.trim().toLowerCase();
    return vu.packages.groups.filter(g => {
      if (show === 'fixable' && !g.fixable) return false;
      if (show === 'serious' && !g.serious) return false;
      if (!q) return true;
      return g.package.toLowerCase().includes(q) || g.cves.some(c => c.id.toLowerCase().includes(q));
    });
  }

  function renderPackages() {
    const box = $('vu-packages');
    box.innerHTML = '';
    const p = vu.packages;
    if (!p.available) {
      box.appendChild(el('p', 'field-hint', t('debsecan ist nicht installiert – Pakete werden nicht geprüft.')));
      const cmd = raw('code', 'mono', 'sudo apt install debsecan');
      box.appendChild(cmd);
      return;
    }
    if (p.error) box.appendChild(el('p', 'form-msg error', p.error));
    if (!p.checked) { box.appendChild(el('p', 'field-hint', t('Noch nicht geprüft.'))); return; }
    const rows = packageRows();
    if (!rows.length) {
      box.appendChild(el('p', 'field-hint', t($('vu-show').value === 'all' && !$('vu-query').value ? 'Keine bekannten Lücken.' : 'Keine Treffer.')));
      return;
    }
    const wrap = el('div', 'table-scroll tall');
    const table = el('table', 'data-table vu-table');
    const head = table.createTHead().insertRow();
    ['Paket', 'Lücken', 'Mit Update', 'Schwerste', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    rows.slice(0, shown).forEach(g => {
      const r = body.insertRow();
      r.insertCell().appendChild(raw('span', 'mono', g.package));
      r.insertCell().textContent = String(g.cves.filter(c => !c.ignored).length);
      const fx = r.insertCell();
      fx.textContent = String(g.fixable);
      if (g.fixable) fx.className = 'warn';
      r.insertCell().appendChild(sevTag(g.severity));
      const act = r.insertCell();
      act.className = 'row-action';
      const isOpen = open.has(g.package);
      act.appendChild(button(isOpen ? 'Zuklappen' : 'Details', 'text-btn', () => {
        if (open.has(g.package)) open.delete(g.package); else open.add(g.package);
        renderPackages();
      }));
      if (isOpen) {
        const dr = body.insertRow();
        dr.className = 'vu-detail-row';
        const c = dr.insertCell();
        c.colSpan = 5;
        const list = el('ul', 'vu-cves');
        g.cves.forEach(v => {
          const li = el('li', v.ignored ? 'ignored' : '');
          li.append(cveLink(v), sevTag(v.severity));
          if (v.fixed) li.appendChild(el('span', 'vu-flag fix', t('Update verfügbar')));
          if (v.remote) li.appendChild(el('span', 'vu-flag', t('aus dem Netz ausnutzbar')));
          if (v.ignored) li.appendChild(el('span', 'vu-flag', t('akzeptiert')));
          li.appendChild(acceptControl(v));
          list.appendChild(li);
        });
        c.appendChild(list);
      }
    });
    wrap.appendChild(table);
    box.appendChild(wrap);
    const foot = el('div', 'form-actions');
    foot.appendChild(raw('span', 'form-msg', `${Math.min(shown, rows.length)} / ${rows.length}`));
    if (rows.length > shown) foot.appendChild(button('Weitere anzeigen', 'btn-secondary', () => { shown += PAGE; renderPackages(); }));
    box.appendChild(foot);
  }

  function renderImages() {
    const box = $('vu-images');
    box.innerHTML = '';
    if (!vu.tools.trivy) {
      box.appendChild(el('p', 'field-hint', t('Trivy ist nicht installiert – Container-Images werden nicht geprüft. Trivy gibt es als Paket von Aqua Security (trivy.dev); nach der Installation prüft das Dashboard die Images automatisch mit.')));
      return;
    }
    if (!vu.images.length) { box.appendChild(el('p', 'field-hint', t(vu.checked ? 'Keine laufenden Container.' : 'Noch nicht geprüft.'))); return; }
    const list = el('div', 'backup-list');
    vu.images.forEach(i => {
      const card = el('div', `backup-card${i.serious_fixable ? ' overdue' : ''}${i.error && !i.counts ? ' error' : ''}`);
      const head = el('div', 'backup-head');
      head.appendChild(raw('span', 'backup-name', i.image));
      if (i.counts) {
        ['critical', 'high', 'medium'].forEach(s => {
          if (i.counts[s]) {
            const tag = sevTag(s);
            tag.textContent = `${i.counts[s]} ${t(SEV[s][1])}`;
            tag.setAttribute('data-no-i18n', '');
            head.appendChild(tag);
          }
        });
        if (!i.counts.critical && !i.counts.high && !i.counts.medium) head.appendChild(el('span', 'health-tag good', t('Keine schweren Lücken')));
      }
      card.appendChild(head);
      if (i.users && i.users.length) card.appendChild(raw('span', 'backup-target', i.users.join(', ')));
      const facts = el('dl', 'backup-facts');
      const add = (k, v, cls) => { if (v === '' || v == null) return; facts.append(el('dt', '', t(k)), raw('dd', cls || '', v)); };
      if (i.counts) {
        add('Lücken gesamt', String(i.total));
        add('Schwer, mit Fix', String(i.serious_fixable), i.serious_fixable ? 'warn' : '');
        if (i.ignored_count) add('Akzeptiert', String(i.ignored_count));
      }
      add('Geprüft', i.checked ? fmtTs(i.checked) : '');
      if (i.error) add('Fehler', i.error, 'warn');
      card.appendChild(facts);
      const vulns = (i.vulns || []).filter(v => v.severity === 'critical' || v.severity === 'high');
      if (vulns.length) {
        const det = el('details', 'vu-image-details');
        det.appendChild(el('summary', '', t('Schwere Lücken anzeigen')));
        const ul = el('ul', 'vu-cves');
        vulns.forEach(v => {
          const li = el('li', v.ignored ? 'ignored' : '');
          li.append(cveLink(v), sevTag(v.severity), raw('span', 'mono vu-pkg', `${v.package} ${v.installed}`));
          li.appendChild(v.fixed ? raw('span', 'vu-flag fix', `→ ${v.fixed_version}`) : el('span', 'vu-flag', t('noch kein Fix')));
          if (v.title) li.appendChild(raw('span', 'vu-title', v.title));
          li.appendChild(acceptControl(v));
          ul.appendChild(li);
        });
        det.appendChild(ul);
        if (i.truncated) det.appendChild(el('p', 'field-hint', t('Nur die wichtigsten Lücken werden gespeichert.')));
        card.appendChild(det);
      }
      list.appendChild(card);
    });
    box.appendChild(list);
  }

  function renderIgnored() {
    const wrap = $('vu-ignored-wrap');
    wrap.hidden = !vu.ignored.length;
    const table = $('vu-ignored');
    table.innerHTML = '';
    if (!vu.ignored.length) return;
    const head = table.createTHead().insertRow();
    ['Kennung', 'Begründung', 'Von', 'Seit', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    vu.ignored.forEach(v => {
      const r = body.insertRow();
      r.insertCell().appendChild(cveLink(v));
      const n = r.insertCell();
      n.className = 'wrap';
      n.appendChild(raw('span', '', v.note || '–'));
      r.insertCell().appendChild(raw('span', '', v.user || ''));
      r.insertCell().textContent = v.ts ? fmtTs(v.ts) : '';
      const a = r.insertCell();
      a.className = 'row-action';
      a.appendChild(acceptControl({ ...v, ignored: true }));
    });
  }

  function renderVulns() {
    const sum = [];
    if (vu.running) sum.push(t(vu.step ? `Prüfe ${vu.step}…` : 'Prüfe…'));
    else if (vu.checked) sum.push(`${t('Geprüft')} ${fmtTs(vu.checked)}`);
    $('vu-summary').textContent = sum.join(' · ');
    $('vu-scan').disabled = vu.running || (!vu.tools.debsecan && !vu.tools.trivy);
    renderTiles();
    renderPackages();
    renderImages();
    renderIgnored();
    const hint = [];
    if (vu.packages.suite) hint.push(`${t('Debian-Zweig')}: ${vu.packages.suite}`);
    if (vu.error) hint.push(vu.error);
    $('vu-hint').textContent = hint.join(' · ');
    if (vu.running) setTimeout(loadVulns, 4000);
  }

  async function loadVulns() {
    try { vu = await api('/api/vulns'); renderVulns(); } catch (e) { /* no permission or offline */ }
  }

  // ---------------------------------------------------------------------------
  // End of life
  // ---------------------------------------------------------------------------
  const EOL_STATE = {
    expired: ['critical', 'Beendet'], critical: ['critical', 'Endet bald'], warning: ['warning', 'Endet bald'],
    lts: ['warning', 'Nur Langzeit-Support'], ok: ['good', 'Unterstützt'], info: ['', 'Hinweis'],
    floating: ['', 'Ohne Version'], unknown: ['', 'Unbekannt']
  };

  function endText(end) {
    if (end === true) return t('beendet');
    if (!end) return t('kein Datum bekannt');
    return fmtDate(String(end).slice(0, 10));
  }

  function renderEol() {
    const d = eolData;
    $('eol-summary').textContent = d.running ? t('Prüfe…') : d.checked ? `${t('Geprüft')} ${fmtTs(d.checked)}` : '';
    $('eol-check').disabled = d.running;
    const table = $('eol-table');
    table.innerHTML = '';
    if (!d.items.length) {
      $('eol-hint').textContent = t(d.checked ? 'Nichts gefunden.' : 'Noch nicht geprüft.');
      if (d.running) setTimeout(loadEol, 3000);
      return;
    }
    const head = table.createTHead().insertRow();
    ['Komponente', 'Installiert', 'Zweig', 'Sicherheitsupdates bis', 'Status', 'Hinweis'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    d.items.forEach(i => {
      const r = body.insertRow();
      const name = r.insertCell();
      name.appendChild(raw('span', '', i.kind === 'image' ? i.image : t(i.label)));
      if (i.kind === 'image' && i.users && i.users.length) name.appendChild(raw('span', 'ctr-sub', i.users.join(', ')));
      const ver = r.insertCell();
      ver.className = 'mono';
      ver.appendChild(raw('span', '', i.kind === 'hardware' ? '' : i.version));
      r.insertCell().appendChild(raw('span', '', i.cycle || '–'));
      const end = r.insertCell();
      end.textContent = i.state === 'floating' || i.state === 'unknown' ? '–' : endText(i.eol);
      const st = r.insertCell();
      const [cls, label] = EOL_STATE[i.state] || EOL_STATE.unknown;
      const tag = el('span', `health-tag ${cls}`, t(label));
      if ((i.state === 'critical' || i.state === 'warning') && i.days != null) {
        tag.textContent = t(`noch ${i.days} Tage`);
      }
      st.appendChild(tag);
      const note = r.insertCell();
      note.className = 'wrap';
      const parts = [];
      if (i.note) parts.push(t(i.note));
      if (i.eoes && i.eoes !== true && i.state !== 'expired') parts.push(`${t('Erweiterter Support bis')} ${fmtDate(String(i.eoes).slice(0, 10))}`);
      if (i.update) parts.push(`${t('Neuere Version im Zweig')}: ${i.update}`);
      if (i.newest && i.cycle && i.newest !== i.cycle && !String(i.cycle).startsWith(i.newest) && i.kind !== 'hardware') parts.push(`${t('Neuester Zweig')}: ${i.newest}`);
      note.appendChild(raw('span', 'field-hint', parts.join(' · ')));
    });
    const hint = [`${t('Daten')}: endoflife.date`];
    if (d.error) hint.push(t(d.error));
    $('eol-hint').textContent = hint.join(' · ');
    if (d.running) setTimeout(loadEol, 3000);
  }

  async function loadEol() {
    try { eolData = await api('/api/eol'); renderEol(); } catch (e) { /* no permission or offline */ }
  }

  // ---------------------------------------------------------------------------
  // Software inventory
  // ---------------------------------------------------------------------------
  const SB_KIND = { deb: 'Systempaket', python: 'Python', image: 'Container' };
  let sbTimer = null;
  let sbSeq = 0;

  function renderSbomOverview(o) {
    const tiles = $('sb-tiles');
    tiles.innerHTML = '';
    [['Systempakete', o.counts.deb], ['Python-Pakete', o.counts.python], ['Container-Pakete', o.trivy ? o.counts.image : '–'],
      ['Ohne Lizenzangabe', o.unknown_license]].forEach(([label, value]) => {
      const box = el('div', 'bench-value');
      box.append(el('span', 'label', t(label)), raw('span', 'value', typeof value === 'number' ? value.toLocaleString(LOCALE) : value));
      tiles.appendChild(box);
    });
    const table = $('sb-licenses');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Lizenz', 'Pakete'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    o.licenses.forEach(l => {
      const r = body.insertRow();
      const name = r.insertCell();
      const link = el('button', 'text-btn', l.name === '(unbekannt)' ? t('(unbekannt)') : l.name);
      if (l.name !== '(unbekannt)') link.setAttribute('data-no-i18n', '');
      link.type = 'button';
      link.disabled = l.name === '(unbekannt)';
      link.addEventListener('click', () => { $('sb-query').value = l.name; searchSbom(); });
      name.appendChild(link);
      r.insertCell().textContent = l.count.toLocaleString(LOCALE);
    });
    const hint = [];
    if (!o.trivy) hint.push(t('Container-Pakete erst mit Trivy (siehe Schwachstellen).'));
    else if (o.images_checked) hint.push(`${t('Container-Pakete vom')} ${fmtTs(o.images_checked)}`);
    hint.push(t('Lizenzen von Systempaketen nur, wenn das Paket eine maschinenlesbare copyright-Datei hat.'));
    $('sb-hint').textContent = hint.join(' · ');
  }

  function sbTable(items, withPi) {
    const wrap = el('div', 'table-scroll tall');
    const table = el('table', 'data-table');
    const head = table.createTHead().insertRow();
    [...(withPi ? ['Pi'] : []), 'Name', 'Version', 'Art', 'Ort', 'Lizenz'].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    items.forEach(p => {
      const r = body.insertRow();
      if (withPi) r.insertCell().appendChild(raw('span', '', p.pi));
      r.insertCell().appendChild(raw('span', 'mono', p.name));
      const v = r.insertCell();
      v.className = 'mono';
      v.appendChild(raw('span', '', p.version));
      r.insertCell().textContent = t(SB_KIND[p.kind] || p.kind);
      const where = r.insertCell();
      where.appendChild(raw('span', '', p.kind === 'deb' ? (p.source && p.source !== p.name ? `${t('Quelle')}: ${p.source}` : '') : p.where || ''));
      if (p.users && p.users.length) where.appendChild(raw('span', 'ctr-sub', p.users.join(', ')));
      const lic = r.insertCell();
      lic.className = 'wrap';
      lic.appendChild(raw('span', 'field-hint', (p.licenses || []).join(', ')));
    });
    wrap.appendChild(table);
    return wrap;
  }

  async function searchSbom() {
    const q = $('sb-query').value.trim();
    const kind = $('sb-kind').value;
    const fleet = $('sb-fleet').checked;
    const box = $('sb-results');
    const seq = ++sbSeq;
    if (!q) {
      box.innerHTML = '';
      box.appendChild(el('p', 'field-hint', t('Suchbegriff eingeben, um Pakete zu finden.')));
      return;
    }
    box.innerHTML = '';
    box.appendChild(el('p', 'field-hint', t('Suche…')));
    try {
      const params = new URLSearchParams({ q, kind });
      if (fleet) {
        const d = await api(`/api/fleet/sbom?${params}`);
        if (seq !== sbSeq) return;
        box.innerHTML = '';
        const items = [];
        const summary = el('div', 'sb-fleet-summary');
        d.pis.forEach(pi => {
          summary.appendChild(raw('span', `pg-chip${pi.error ? ' warn' : ''}`, pi.error ? `${pi.pi}: ${t(pi.error)}` : `${pi.pi}: ${pi.total}`));
          pi.items.forEach(p => items.push({ ...p, pi: pi.pi }));
        });
        box.appendChild(summary);
        if (!items.length) box.appendChild(el('p', 'field-hint', t('Keine Treffer.')));
        else box.appendChild(sbTable(items, true));
        return;
      }
      const d = await api(`/api/sbom/search?${params}`);
      if (seq !== sbSeq) return;
      box.innerHTML = '';
      if (!d.items.length) { box.appendChild(el('p', 'field-hint', t('Keine Treffer.'))); return; }
      box.appendChild(sbTable(d.items, false));
      box.appendChild(raw('p', 'form-msg', d.truncated ? `${d.items.length} / ${d.total}` : String(d.total)));
    } catch (err) {
      if (seq === sbSeq) { box.innerHTML = ''; box.appendChild(el('p', 'form-msg error', err.message)); }
    }
  }

  async function loadSbom() {
    try { renderSbomOverview(await api('/api/sbom')); } catch (e) { /* no permission or offline */ }
    searchSbom();
  }

  async function init() {
    if (!$('vu-packages')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    $('vu-scan').addEventListener('click', async () => {
      try { vu = await api('/api/vulns/scan', {}); renderVulns(); toast(t('Prüfung gestartet – das kann einige Minuten dauern.'), 'success'); } catch (err) { toast(err.message, 'error'); }
    });
    $('eol-check').addEventListener('click', async () => {
      try { eolData = await api('/api/eol/check', {}); renderEol(); } catch (err) { toast(err.message, 'error'); }
    });
    $('vu-show').addEventListener('change', () => { shown = PAGE; if (vu) renderPackages(); });
    $('vu-query').addEventListener('input', () => { shown = PAGE; if (vu) renderPackages(); });
    $('vu-filter').addEventListener('submit', e => e.preventDefault());
    const later = () => { clearTimeout(sbTimer); sbTimer = setTimeout(searchSbom, 300); };
    $('sb-query').addEventListener('input', later);
    $('sb-kind').addEventListener('change', searchSbom);
    $('sb-fleet').addEventListener('change', searchSbom);
    $('sb-form').addEventListener('submit', e => { e.preventDefault(); searchSbom(); });
    await Promise.all([loadVulns(), loadEol(), loadSbom()]);
    if (['#schwachstellen', '#lebenszyklus', '#software-inventar'].includes(location.hash)) $(location.hash.slice(1)).scrollIntoView();
  }

  init();
})();
