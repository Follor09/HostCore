/**
 * HostCore Dashboard - VM templates, backups, schedules (sections of /vms) and, in a VM's details,
 * "Sichern" and "Verschieben" (live migration with a check first).
 */
(function () {
  'use strict';

  const { t, can, el, raw, toast, api, time, size, tag, button, confirmButton, run, showForm, table } = window.OpsUI;
  const $ = id => document.getElementById(id);
  const page = window.VmPage;
  if (!$('tp-table') || !page) return;
  const DAYS = [[0, 'Mo'], [1, 'Di'], [2, 'Mi'], [3, 'Do'], [4, 'Fr'], [5, 'Sa'], [6, 'So']];
  const enc = encodeURIComponent;
  let data = null;

  const openTask = task => { if (window.PiTasks && task && task.id) window.PiTasks.open(task.id); };
  const poolOptions = () => page.pools().filter(p => p.active).map(p => [p.name, p.name]);
  const netOptions = () => page.networks().filter(n => n.active).map(n => [`network:${n.name}`, n.name]);
  const imageOptions = () => ((page.catalog() || {}).images || []).map(i => [i.id, i.name]);

  function templateForm(tpl) {
    const spec = [
      { key: 'name', label: 'Name', full: true },
      { key: 'description', label: 'Beschreibung', full: true },
      { key: 'source', label: 'Installationsquelle', type: 'select', options: [['cloud', 'Cloud-Image (empfohlen)'], ['iso', 'ISO-Installationsmedium'], ['pxe', 'Netzwerkstart (PXE)']], value: 'cloud' },
      { key: 'image', label: 'Betriebssystem', type: 'select', options: imageOptions() },
      { key: 'vcpus', label: 'vCPUs', type: 'number', value: 2 },
      { key: 'memory_mb', label: 'Arbeitsspeicher (MB)', type: 'number', value: 2048 },
      { key: 'disk_gb', label: 'Laufwerk (GB)', type: 'number', value: 20 },
      { key: 'pool', label: 'Speicher-Pool', type: 'select', options: poolOptions() },
      { key: 'network', label: 'Netzwerk', type: 'select', options: netOptions() },
      { key: 'firmware', label: 'Firmware', type: 'select', options: [['bios', 'BIOS'], ['uefi', 'UEFI']], value: 'bios' },
      { key: 'user', label: 'Benutzer', value: 'admin' },
      { key: 'ssh_keys', label: 'SSH-Schlüssel (einer pro Zeile)', type: 'textarea' },
      { key: 'agent', label: 'Gast-Agent installieren', type: 'check', value: true },
      { key: 'upgrade', label: 'Pakete beim ersten Start aktualisieren', type: 'check' },
      { key: 'autostart', label: 'Mit dem Host starten', type: 'check' }
    ];
    showForm($('tp-form'), tpl ? 'Vorlage bearbeiten' : 'Vorlage anlegen', spec, tpl || {}, 'Speichern', async d => {
      const r = await api(tpl ? `/api/vms/templates/${tpl.id}` : '/api/vms/templates', d);
      data.templates = r.templates;
      renderTemplates();
    });
  }

  function renderTemplates() {
    table($('tp-table'), ['Vorlage', 'Quelle', 'Größe', 'Netzwerk', ''], data.templates.map(p => {
      const name = el('div');
      name.append(raw('strong', '', p.name));
      if (p.description) name.appendChild(raw('div', 'lan-sub', p.description));
      const a = el('div', 'ops-actions');
      a.append(button('VM erstellen', 'text-btn', () => page.createFrom(p), 'vms.manage'),
        button('Bearbeiten', 'text-btn', () => templateForm(p), 'vms.manage'),
        confirmButton('Löschen', 'text-btn danger', async () => {
          const r = await run(() => api(`/api/vms/templates/${p.id}/delete`, {}));
          if (r) { data.templates = r.templates; renderTemplates(); }
        }, 'vms.manage'));
      return [name, p.source === 'cloud' ? (p.image || 'Cloud-Image') : p.source.toUpperCase(), `${p.vcpus} vCPU · ${size(p.memory_mb * 1048576)} · ${p.disk_gb} GB`,
        String(p.network || '').replace(/^network:/, ''), a];
    }), 'Noch keine Vorlagen.');
  }

  function renderBackups() {
    $('bk-summary').textContent = `${data.backups.length} ${t('Sicherungen')} · ${size(data.total)}`;
    table($('bk-table'), ['VM', 'Zeitpunkt', 'Größe', 'Art', 'Pool', ''], data.backups.map(b => {
      const a = el('div', 'ops-actions');
      a.append(button('Wiederherstellen', 'text-btn', () => restoreForm(b), 'vms.manage'),
        confirmButton('Löschen', 'text-btn danger', async () => {
          const r = await run(() => api(`/api/vms/backups/${b.id}/delete`, {}));
          if (r) { Object.assign(data, r); renderBackups(); }
        }, 'vms.manage'));
      return [raw('strong', '', b.vm), time(b.ts), size(b.size), tag('', b.schedule ? 'Zeitplan' : 'Von Hand'), b.pool, a];
    }), 'Noch keine Sicherungen. Eine VM sichern: Details der VM, Übersicht.');
    const sel = $('bk-pool');
    if (sel && !sel.dataset.done && sel.options.length) { sel.value = data.config.pool; sel.dataset.done = '1'; }
  }

  function restoreForm(b) {
    const taken = page.names().includes(b.vm);
    showForm($('bk-form'), `Sicherung von ${b.vm} wiederherstellen`, [
      { key: 'name', label: 'Name der neuen VM' },
      { key: 'pool', label: 'Speicher-Pool', type: 'select', options: poolOptions() }
    ], { name: taken ? `${b.vm}-restored` : b.vm }, 'Wiederherstellen', async d => {
      openTask(await api(`/api/vms/backups/${b.id}/restore`, d));
      toast(t('Wiederherstellung läuft – Fortschritt in der Aufgabenleiste.'), 'success');
    });
  }

  function scheduleForm(s) {
    showForm($('sc-form'), s ? 'Zeitplan bearbeiten' : 'Zeitplan anlegen', [
      { key: 'vm', label: 'VM', type: 'select', options: page.names().map(n => [n, n]) },
      { key: 'action', label: 'Aktion', type: 'select', options: Object.entries(data.actions), value: 'snapshot' },
      { key: 'time', label: 'Uhrzeit', type: 'time', value: '03:00' },
      { key: 'keep', label: 'Aufbewahren (Anzahl)', type: 'number', value: 7 },
      { key: 'days', label: 'Wochentage', type: 'checks', options: DAYS },
      { key: 'enabled', label: 'Aktiv', type: 'check', value: true }
    ], s || { days: ['0', '1', '2', '3', '4', '5', '6'] }, 'Speichern', async d => {
      const r = await api(s ? `/api/vms/schedules/${s.id}` : '/api/vms/schedules', d);
      data.schedules = r.schedules;
      renderSchedules();
    });
  }

  function renderSchedules() {
    const day = new Map(DAYS);
    table($('sc-table'), ['VM', 'Aktion', 'Wann', 'Aufbewahren', 'Letzter Lauf', ''], data.schedules.map(s => {
      const state = !s.enabled ? tag('', 'Aus') : s.last_state === 'error' ? tag('critical', 'Fehler') : s.last_state === 'running' ? tag('warning', 'Läuft') : s.last_state === 'ok' ? tag('good', 'OK') : tag('', 'Noch nicht');
      if (s.last_error) state.title = s.last_error;
      const a = el('div', 'ops-actions');
      a.append(button('Bearbeiten', 'text-btn', () => scheduleForm(s), 'vms.manage'),
        confirmButton('Löschen', 'text-btn danger', async () => {
          const r = await run(() => api(`/api/vms/schedules/${s.id}/delete`, {}));
          if (r) { data.schedules = r.schedules; renderSchedules(); }
        }, 'vms.manage'));
      const last = el('div');
      last.append(state, raw('span', 'lan-sub', ` ${time(s.last_run)}`));
      return [raw('strong', '', s.vm), el('span', '', t(data.actions[s.action])), `${s.days.length === 7 ? t('täglich') : s.days.map(d => t(day.get(d))).join(', ')} · ${s.time}`,
        ['snapshot', 'backup'].includes(s.action) ? s.keep : '–', last, a];
    }), 'Noch keine Zeitpläne.');
  }

  async function load() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      data = await api('/api/vms/ops');
    } catch (e) {
      return;   // the page itself shows why VMs are unavailable
    }
    renderTemplates();
    renderBackups();
    renderSchedules();
  }

  // ---------------------------------------------------------------------------
  // In a VM's details
  // ---------------------------------------------------------------------------
  function migrateBox(box, d) {
    const form = el('div', 'inline-grid');
    const target = el('select');
    const address = el('input');
    address.placeholder = t('Automatisch aus der Serveradresse');
    address.spellcheck = false;
    const shared = el('input');
    shared.type = 'checkbox';
    const l1 = el('label', 'login-field');
    l1.append(el('span', '', t('Zielserver')), target);
    const l2 = el('label', 'login-field');
    l2.append(el('span', '', t('Adresse des Ziels')), address);
    const l3 = el('label', 'toggle-label full');
    l3.append(shared, ' ', el('span', '', t('Gemeinsamer Speicher (Laufwerke nicht mitkopieren)')));
    form.append(l1, l2, l3);
    const out = el('div', 'full');
    const body = () => ({ vm: d.name, target: target.value, address: address.value.trim(), shared: shared.checked });
    const go = button('Jetzt verschieben', 'btn-primary', async b => {
      b.disabled = true;
      const task = await run(() => api('/api/vm-migration/start', body()), 'Migration läuft – Fortschritt in der Aufgabenleiste.');
      b.disabled = false;
      if (task) { page.closeDetail(); openTask(task); page.load(); }
    });
    go.hidden = true;
    const check = button('Prüfen', 'btn-secondary', async b => {
      b.disabled = true;
      go.hidden = true;
      out.innerHTML = '';
      const r = await run(() => api('/api/vm-migration/check', body()));
      b.disabled = false;
      if (!r) return;
      if (!address.value) address.value = r.address;
      r.blockers.forEach(x => out.appendChild(raw('div', 'db-problem critical', x)));
      r.warnings.forEach(x => out.appendChild(raw('div', 'db-problem warning', x)));
      if (r.ok) {
        out.appendChild(el('p', 'field-hint', `${t('Bereit')}: ${r.memory_mb} MB ${t('Arbeitsspeicher')}${r.copy_bytes ? ` · ${size(r.copy_bytes)} ${t('Laufwerke')}` : ''} → ${r.target}`));
        go.hidden = false;
      }
    });
    const row = el('div', 'form-actions');
    row.append(check, go);
    form.append(out, row);
    box.appendChild(form);
    api('/api/vm-migration/targets').then(r => {
      r.targets.forEach(x => { const o = raw('option', '', x.name); o.value = x.id; target.appendChild(o); });
      if (!r.targets.length) { box.innerHTML = ''; box.appendChild(el('p', 'field-hint', t('Kein weiterer Server eingerichtet (Einstellungen → Remote-Server).'))); }
    }).catch(e => toast(t(e.message), 'error'));
  }

  window.VmOps = {
    detail(body, d) {
      if (!can('vms.manage')) return;
      const box = el('div', 'vm-inline-form');
      box.appendChild(el('h3', '', t('Sichern und verschieben')));
      const row = el('div', 'vm-manage-row');
      row.appendChild(button('Jetzt sichern', 'btn-secondary', async b => {
        b.disabled = true;
        const task = await run(() => api(`/api/vms/vm/${enc(d.name)}/backup`, {}), 'Sicherung läuft – Fortschritt in der Aufgabenleiste.');
        b.disabled = false;
        openTask(task);
      }));
      const open = button('Auf anderen Server verschieben', 'btn-secondary', b => {
        b.hidden = true;
        const m = el('div');
        box.appendChild(m);
        migrateBox(m, d);
      });
      row.appendChild(open);
      box.appendChild(row);
      body.appendChild(box);
    }
  };

  $('tp-add').addEventListener('click', () => templateForm(null));
  $('sc-add').addEventListener('click', () => { if (!page.names().length) toast(t('Noch keine VM vorhanden.'), 'error'); else scheduleForm(null); });
  $('bk-config').addEventListener('submit', async e => {
    e.preventDefault();
    const r = await run(() => api('/api/vms/backups/config', { pool: $('bk-pool').value }), 'Gespeichert.');
    if (r) data.config = r;
  });
  load();
  setInterval(() => { if (!document.hidden && !document.querySelector('#tp-form:not([hidden]), #sc-form:not([hidden]), #bk-form:not([hidden])')) load(); }, 30000);
})();
