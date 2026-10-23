/**
 * PiCore Dashboard - Settings page
 * Account, public view, weather location, visible services, auto-update, history, system info.
 */
(function () {
  'use strict';

  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

  const $ = id => document.getElementById(id);
  let data = null;

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
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`;
      throw new Error('Nicht angemeldet.');
    }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  function setMsg(el, text, type) {
    el.textContent = text;
    el.className = `form-msg ${type || ''}`;
  }

  // Definition list rows; values go through textContent (never innerHTML)
  function renderKv(el, rows) {
    el.innerHTML = '';
    rows.forEach(([label, value, tag]) => {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      if (tag) {
        const span = document.createElement('span');
        span.className = `health-tag ${tag}`;
        span.textContent = value;
        dd.appendChild(span);
      } else {
        dd.textContent = value ?? '–';
      }
      el.append(dt, dd);
    });
  }

  function fmtBytes(b) {
    if (b >= 1048576) return `${(b / 1048576).toFixed(1)} MB`;
    if (b >= 1024) return `${Math.round(b / 1024)} KB`;
    return `${b} B`;
  }

  function fmtDate(ts) {
    return ts ? new Date(ts * 1000).toLocaleString(LOCALE, { dateStyle: 'medium', timeStyle: 'short' }) : '–';
  }

  // Two-click confirmation for destructive buttons
  function confirmClick(btn, label, action) {
    let timer = null;
    const original = btn.textContent;
    btn.addEventListener('click', async () => {
      if (!btn.classList.contains('confirming')) {
        btn.classList.add('confirming');
        btn.textContent = label;
        timer = setTimeout(() => { btn.classList.remove('confirming'); btn.textContent = original; }, 5000);
        return;
      }
      clearTimeout(timer);
      btn.classList.remove('confirming');
      btn.textContent = original;
      btn.disabled = true;
      try { await action(); } finally { btn.disabled = false; }
    });
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  function render() {
    const { account, weather, services, updater, history, system } = data;

    $('user-name').textContent = account.username;
    $('user-chip').hidden = false;
    $('acc-username').value = account.username;
    $('acc-group').value = account.group_name ? ((window.I18N && window.I18N.t) ? window.I18N.t(account.group_name) : account.group_name) : '';
    $('public-view').checked = account.public_view;

    $('weather-city-setting').value = weather.city;
    $('weather-coords').textContent = `Aktuell: ${weather.city} (${(+weather.lat).toFixed(2)}°, ${(+weather.lon).toFixed(2)}°)`;

    const checks = $('services-checks');
    checks.innerHTML = '';
    services.available.forEach(s => {
      const label = document.createElement('label');
      label.className = 'check-item';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = s.id;
      input.checked = !services.hidden.includes(s.id);
      input.addEventListener('change', saveServices);
      label.append(input, document.createTextNode(s.name));
      checks.appendChild(label);
    });

    renderUpdater(updater);
    renderWolSettings(data.wol_devices);

    renderKv($('history-info'), [
      ['Speicherplatz', fmtBytes(history.size_bytes)],
      ['Messpunkte', history.samples.toLocaleString(LOCALE)],
      ['Ältester Wert', fmtDate(history.oldest)],
      ['Aufbewahrung', history.retention_days ? `${history.retention_days} Tage, Messung jede Minute` : '–']
    ]);

    const ips = Object.entries((system.ip && system.ip.interfaces) || {})
      .map(([name, ip]) => `${name}: ${ip}`).join(' · ');
    renderKv($('system-info'), [
      ['Hostname', system.hostname],
      ['Modell', system.model],
      ['Betriebssystem', system.os],
      ['Kernel', `${system.kernel} (${system.architecture})`],
      ['Python', system.python],
      ['Laufzeit', system.uptime],
      ['Netzwerk', ips || (system.ip && system.ip.primary) || '–']
    ]);
  }

  function renderUpdater(u) {
    const timerLevel = { active: 'good', inactive: '', error: 'critical' }[u.timer.status] ?? '';
    const timerText = { active: 'Aktiv', inactive: 'Inaktiv', error: 'Fehler' }[u.timer.status] ?? u.timer.status;
    renderKv($('updater-info'), [
      ['Status', `${timerText} · ${u.timer.detail}`, timerLevel],
      ['Repository', u.remote_url || '–'],
      ['Branch', u.branch],
      ['Commit', u.commit_message ? `${u.current_commit} – ${u.commit_message}` : u.current_commit],
      ['GitHub-Token', u.token_set ? 'Hinterlegt' : 'Nicht hinterlegt (nur für private Repos nötig)', u.token_set ? 'good' : ''],
      ['Letzte Prüfung', fmtDate(u.last_check_timestamp)]
    ]);
  }

  function renderWolSettings(devices) {
    const list = $('wol-settings-list');
    list.innerHTML = '';
    if (!devices.length) {
      const p = document.createElement('p');
      p.className = 'field-hint';
      p.textContent = 'Noch keine Geräte.';
      list.appendChild(p);
    }
    devices.forEach(d => {
      const item = document.createElement('div');
      item.className = 'wol-item';
      const main = document.createElement('div');
      main.className = 'workload-main';
      const n = document.createElement('span');
      n.className = 'workload-name';
      n.textContent = d.name;
      const sub = document.createElement('span');
      sub.className = 'workload-sub';
      sub.textContent = `${d.mac} · Broadcast ${d.broadcast}`;
      main.append(n, sub);
      const del = document.createElement('button');
      del.className = 'btn-danger';
      del.textContent = 'Entfernen';
      confirmClick(del, 'Sicher?', async () => {
        try {
          await api(`/api/settings/wol/${encodeURIComponent(d.id)}/delete`, {});
          data.wol_devices = data.wol_devices.filter(x => x.id !== d.id);
          renderWolSettings(data.wol_devices);
          toast(`${d.name} entfernt.`, 'success');
        } catch (err) {
          toast(err.message, 'error');
        }
      });
      item.append(main, del);
      list.appendChild(item);
    });
  }

  // ---------------------------------------------------------------------------
  // Custom links + share links
  // ---------------------------------------------------------------------------
  function itemRow(title, sub, buttons) {
    const item = document.createElement('div');
    item.className = 'wol-item';
    const main = document.createElement('div');
    main.className = 'workload-main';
    const n = document.createElement('span');
    n.className = 'workload-name';
    n.textContent = title;
    n.setAttribute('data-no-i18n', '');
    const s = document.createElement('span');
    s.className = 'workload-sub';
    s.textContent = sub;
    s.setAttribute('data-no-i18n', '');
    main.append(n, s);
    const actions = document.createElement('div');
    actions.className = 'item-actions';
    buttons.forEach(b => actions.appendChild(b));
    item.append(main, actions);
    return item;
  }

  function smallButton(label, cls, onClick) {
    const b = document.createElement('button');
    b.className = cls;
    b.textContent = label;
    if (onClick) b.addEventListener('click', onClick);
    return b;
  }

  function renderLinks(links) {
    const list = $('links-list');
    list.innerHTML = '';
    if (!links.length) {
      const p = document.createElement('p');
      p.className = 'field-hint';
      p.textContent = 'Noch keine Links.';
      list.appendChild(p);
    }
    links.forEach((l, i) => {
      const post = async url => { try { renderLinks((await api(url, {})).links); } catch (err) { toast(err.message, 'error'); } };
      const up = smallButton('↑', 'btn-secondary', () => post(`/api/settings/links/${l.id}/move?direction=-1`));
      up.disabled = i === 0;
      up.title = 'Nach oben';
      const down = smallButton('↓', 'btn-secondary', () => post(`/api/settings/links/${l.id}/move?direction=1`));
      down.disabled = i === links.length - 1;
      down.title = 'Nach unten';
      const del = smallButton('Entfernen', 'btn-danger');
      confirmClick(del, 'Sicher?', () => post(`/api/settings/links/${l.id}/delete`));
      list.appendChild(itemRow(l.name, l.url, [up, down, del]));
    });
  }

  function renderShares(shares) {
    const list = $('shares-list');
    list.innerHTML = '';
    if (!shares.length) {
      const p = document.createElement('p');
      p.className = 'field-hint';
      p.textContent = 'Keine aktiven Freigaben.';
      list.appendChild(p);
    }
    shares.forEach(sh => {
      const until = new Date(sh.expires * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      const copy = smallButton('Link kopieren', 'btn-secondary', async () => {
        const url = `${location.origin}/s/${sh.token}`;
        try { await navigator.clipboard.writeText(url); toast('Link kopiert.', 'success'); } catch (e) { window.prompt('Link:', url); }
      });
      const end = smallButton('Beenden', 'btn-danger');
      confirmClick(end, 'Sicher?', async () => {
        try { renderShares((await api(`/api/files/shares/${sh.token}/delete`, {})).shares); } catch (err) { toast(err.message, 'error'); }
      });
      list.appendChild(itemRow(sh.name, `${sh.path} · gültig bis ${until} · ${sh.downloads}× geladen`, [copy, end]));
    });
  }

  // ---------------------------------------------------------------------------
  // Web services (health checks)
  // ---------------------------------------------------------------------------
  const HEALTH_TYPES = { http: 'Webseite', keyword: 'Text', json: 'JSON', tcp: 'TCP', ping: 'Ping', dns: 'DNS' };
  let healthEdit = null;

  function healthState(c) {
    if (!c.last) return 'Noch nicht geprüft';
    if (!c.last.ok) return c.last.error;
    const ms = c.last.ms != null ? `${Math.round(c.last.ms)} ms` : '';
    return c.last.status ? `OK (HTTP ${c.last.status}, ${ms})` : `OK${ms ? ` (${ms})` : ''}`;
  }

  function renderHealthSettings(h) {
    const list = $('health-settings-list');
    list.innerHTML = '';
    if (!h.checks.length) {
      const p = document.createElement('p');
      p.className = 'field-hint';
      p.textContent = 'Noch keine Dienste.';
      list.appendChild(p);
    }
    h.checks.forEach(c => {
      const item = document.createElement('div');
      item.className = 'wol-item';
      const main = document.createElement('div');
      main.className = 'workload-main';
      const n = document.createElement('span');
      n.className = 'workload-name';
      n.textContent = c.name;
      n.setAttribute('data-no-i18n', '');
      const kind = document.createElement('span');
      kind.className = 'health-tag';
      kind.textContent = HEALTH_TYPES[c.type || 'http'];
      n.append(' ', kind);
      const sub = document.createElement('span');
      sub.className = 'workload-sub';
      sub.textContent = `${c.target || c.url} · alle ${c.interval} Min.${c.verify_tls ? ' · Zertifikat geprüft' : ''}${c.mac ? ' · folgt der MAC' : ''} · ${healthState(c)}`;
      main.append(n, sub);
      const run = document.createElement('button');
      run.className = 'btn-secondary';
      run.textContent = 'Jetzt prüfen';
      run.addEventListener('click', async () => {
        run.disabled = true;
        try { renderHealthSettings(await api(`/api/settings/health/${encodeURIComponent(c.id)}/run`, {})); }
        catch (err) { toast(err.message, 'error'); run.disabled = false; }
      });
      const edit = document.createElement('button');
      edit.className = 'btn-secondary';
      edit.textContent = 'Bearbeiten';
      edit.addEventListener('click', () => editHealthCheck(c));
      const del = document.createElement('button');
      del.className = 'btn-danger';
      del.textContent = 'Entfernen';
      confirmClick(del, 'Sicher?', async () => {
        try {
          renderHealthSettings(await api(`/api/settings/health/${encodeURIComponent(c.id)}/delete`, {}));
          toast(`${c.name} entfernt.`, 'success');
        } catch (err) {
          toast(err.message, 'error');
        }
      });
      const actions = document.createElement('div');
      actions.className = 'item-actions';
      actions.append(run, edit, del);
      item.append(main, actions);
      list.appendChild(item);
    });
  }

  // Show only the fields of the chosen check type
  function syncHealthType() {
    const type = $('health-type').value;
    document.querySelectorAll('#health-form [data-types]').forEach(el => {
      el.hidden = !el.dataset.types.split(' ').includes(type);
    });
    document.querySelectorAll('#health-form [data-label-types]').forEach(el => {
      el.hidden = !el.dataset.labelTypes.split(' ').includes(type);
    });
  }

  const HEALTH_FIELDS = { url: 'health-url', keyword: 'health-keyword', json_path: 'health-json-path', host: 'health-host',
                          port: 'health-port', query: 'health-query' };

  function healthPayload() {
    const type = $('health-type').value;
    const body = { name: $('health-name').value, type, interval: +$('health-interval').value, verify_tls: $('health-tls').checked,
                   invert: $('health-invert').checked };
    Object.entries(HEALTH_FIELDS).forEach(([k, id]) => { body[k] = $(id).value; });
    body.expected = type === 'dns' ? $('health-dns-expected').value : $('health-json-expected').value;
    if (healthEdit && healthEdit.mac && type === 'ping') body.mac = healthEdit.mac;
    return body;
  }

  function resetHealthForm() {
    healthEdit = null;
    $('health-form').reset();
    $('health-interval').value = '5';
    $('health-form-title').textContent = 'Prüfung hinzufügen';
    $('health-submit').textContent = 'Hinzufügen';
    $('health-cancel').hidden = true;
    syncHealthType();
  }

  function editHealthCheck(c) {
    healthEdit = c;
    $('health-type').value = c.type || 'http';
    $('health-name').value = c.name;
    $('health-interval').value = String(c.interval);
    Object.entries(HEALTH_FIELDS).forEach(([k, id]) => { $(id).value = c[k] != null ? c[k] : ''; });
    $('health-tls').checked = !!c.verify_tls;
    $('health-invert').checked = !!c.invert;
    $('health-json-expected').value = c.type === 'json' ? c.expected || '' : '';
    $('health-dns-expected').value = c.type === 'dns' ? c.expected || '' : '';
    $('health-form-title').textContent = `Prüfung bearbeiten: ${c.name}`;
    $('health-submit').textContent = 'Speichern';
    $('health-cancel').hidden = false;
    syncHealthType();
    $('health-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  async function addHealthCheck(e) {
    e.preventDefault();
    const btn = $('health-submit');
    btn.disabled = true;
    try {
      const path = healthEdit ? `/api/settings/health/${encodeURIComponent(healthEdit.id)}/update` : '/api/settings/health';
      renderHealthSettings(await api(path, healthPayload()));
      toast(healthEdit ? 'Gespeichert und geprüft.' : 'Dienst hinzugefügt und geprüft.', 'success');
      resetHealthForm();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------
  async function saveServices() {
    const hidden = [...document.querySelectorAll('#services-checks input')]
      .filter(i => !i.checked).map(i => i.value);
    try {
      await api('/api/settings/services', { hidden });
      toast('Dienste gespeichert.', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function bind() {
    document.querySelectorAll('#lang-switch button').forEach(btn => {
      const current = (window.I18N && window.I18N.lang) || 'de';
      btn.classList.toggle('active', btn.dataset.lang === current);
      btn.setAttribute('aria-pressed', String(btn.dataset.lang === current));
      btn.addEventListener('click', () => {
        if (btn.dataset.lang !== current && window.I18N) window.I18N.setLang(btn.dataset.lang);
      });
    });

    $('btn-logout').addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST' });
      location.href = '/login';
    });

    $('account-form').addEventListener('submit', async e => {
      e.preventDefault();
      const msg = $('account-msg');
      const newPw = $('acc-new').value;
      if (!newPw) return setMsg(msg, 'Bitte ein neues Passwort eingeben.', 'error');
      if (newPw !== $('acc-new2').value) return setMsg(msg, 'Die neuen Passwörter stimmen nicht überein.', 'error');
      if (!$('acc-current').value) return setMsg(msg, 'Bitte das aktuelle Passwort eingeben.', 'error');

      $('account-save').disabled = true;
      setMsg(msg, 'Speichere…');
      try {
        const res = await api('/api/settings/account', {
          current_password: $('acc-current').value,
          username: '',
          new_password: newPw
        });
        setMsg(msg, res.message, 'success');
        $('user-name').textContent = res.user;
        ['acc-new', 'acc-new2', 'acc-current'].forEach(id => { $(id).value = ''; });
      } catch (err) {
        setMsg(msg, err.message, 'error');
      } finally {
        $('account-save').disabled = false;
      }
    });

    $('public-view').addEventListener('change', async e => {
      try {
        await api('/api/settings/public-view', { enabled: e.target.checked });
        toast(e.target.checked ? 'Ansehen ohne Login ist jetzt an.' : 'Ansehen ohne Login ist jetzt aus.', 'success');
      } catch (err) {
        e.target.checked = !e.target.checked;
        toast(err.message, 'error');
      }
    });

    confirmClick($('logout-others'), 'Wirklich alle abmelden?', async () => {
      try {
        const res = await api('/api/settings/logout-others', {});
        toast(res.message, 'success');
      } catch (err) {
        toast(err.message, 'error');
      }
    });

    $('weather-form').addEventListener('submit', async e => {
      e.preventDefault();
      try {
        const res = await api('/api/weather/location', { city: $('weather-city-setting').value.trim() });
        data.weather = { ...data.weather, city: res.city };
        const fresh = await api('/api/settings');
        data = fresh;
        render();
        toast(`Wetter-Ort: ${res.city}`, 'success');
      } catch (err) {
        toast(err.message, 'error');
      }
    });

    $('updater-check').addEventListener('click', async () => {
      const btn = $('updater-check'), msg = $('updater-msg');
      btn.disabled = true;
      setMsg(msg, 'Prüfe GitHub…');
      try {
        const res = await api('/api/updates/check', {});
        setMsg(msg, res.update_available
          ? `${res.commits_behind} neue${res.commits_behind === 1 ? 'r' : ''} Commit${res.commits_behind === 1 ? '' : 's'} – wird innerhalb einer Minute installiert.`
          : 'Alles aktuell.', res.update_available ? 'warning' : 'success');
        data = await api('/api/settings');
        renderUpdater(data.updater);
      } catch (err) {
        setMsg(msg, err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });

    confirmClick($('history-clear'), 'Wirklich löschen?', async () => {
      try {
        const res = await api('/api/settings/history/clear', {});
        setMsg($('history-msg'), res.message, 'success');
        data = await api('/api/settings');
        render();
      } catch (err) {
        setMsg($('history-msg'), err.message, 'error');
      }
    });

    $('health-form').addEventListener('submit', addHealthCheck);
    $('health-type').addEventListener('change', syncHealthType);
    $('health-cancel').addEventListener('click', resetHealthForm);
    syncHealthType();
    api('/api/links').then(r => renderLinks(r.links)).catch(() => {});
    api('/api/files/shares').then(r => renderShares(r.shares)).catch(() => {});
    $('links-form').addEventListener('submit', async e => {
      e.preventDefault();
      try {
        renderLinks((await api('/api/settings/links', { name: $('link-name').value, url: $('link-url').value })).links);
        ['link-name', 'link-url'].forEach(id => { $(id).value = ''; });
        toast('Link hinzugefügt.', 'success');
      } catch (err) {
        toast(err.message, 'error');
      }
    });
    const syncTheme = () => document.querySelectorAll('#theme-switch button').forEach(b => {
      const on = window.PiTheme && b.dataset.theme === window.PiTheme.choice();
      b.classList.toggle('active', on);
      b.setAttribute('aria-checked', String(on));
    });
    document.querySelectorAll('#theme-switch button').forEach(b => {
      b.setAttribute('role', 'radio');
      b.addEventListener('click', () => { window.PiTheme && window.PiTheme.set(b.dataset.theme); syncTheme(); });
    });
    syncTheme();
    const syncAccent = () => document.querySelectorAll('#accent-switch button').forEach(b => {
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(window.PiTheme && b.dataset.accent === window.PiTheme.accent()));
    });
    document.querySelectorAll('#accent-switch button').forEach(b =>
      b.addEventListener('click', () => { window.PiTheme && window.PiTheme.setAccent(b.dataset.accent); syncAccent(); }));
    syncAccent();
    api('/api/health').then(renderHealthSettings).catch(() => {});
    $('wol-form').addEventListener('submit', async e => {
      e.preventDefault();
      try {
        const res = await api('/api/settings/wol', {
          name: $('wol-name').value.trim(),
          mac: $('wol-mac').value.trim(),
          broadcast: $('wol-broadcast').value.trim()
        });
        data.wol_devices.push(res.device);
        renderWolSettings(data.wol_devices);
        ['wol-name', 'wol-mac', 'wol-broadcast'].forEach(id => { $(id).value = ''; });
        toast(`${res.device.name} hinzugefügt.`, 'success');
      } catch (err) {
        toast(err.message, 'error');
      }
    });

    // Highlight the section currently in view
    const links = [...document.querySelectorAll('.settings-nav a')];
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${entry.target.id}`));
      });
    }, { rootMargin: '-30% 0px -60% 0px' });
    document.querySelectorAll('.settings-section').forEach(s => observer.observe(s));
  }

  async function init() {
    bind();
    try {
      data = await api('/api/settings');
      render();
    } catch (err) {
      toast(`Einstellungen konnten nicht geladen werden: ${err.message}`, 'error');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
