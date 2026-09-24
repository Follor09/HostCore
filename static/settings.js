/**
 * PiCore Dashboard - Settings page
 * Account, public view, weather location, visible services, auto-update, history, system info.
 */
(function () {
  'use strict';

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
      location.href = '/login';
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
    return ts ? new Date(ts * 1000).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) : '–';
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

    renderKv($('history-info'), [
      ['Speicherplatz', fmtBytes(history.size_bytes)],
      ['Messpunkte', history.samples.toLocaleString('de-DE')],
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
    $('btn-logout').addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST' });
      location.href = '/login';
    });

    $('account-form').addEventListener('submit', async e => {
      e.preventDefault();
      const msg = $('account-msg');
      const newPw = $('acc-new').value;
      if (newPw !== $('acc-new2').value) return setMsg(msg, 'Die neuen Passwörter stimmen nicht überein.', 'error');
      if (!$('acc-current').value) return setMsg(msg, 'Bitte das aktuelle Passwort eingeben.', 'error');

      $('account-save').disabled = true;
      setMsg(msg, 'Speichere…');
      try {
        const res = await api('/api/settings/account', {
          current_password: $('acc-current').value,
          username: $('acc-username').value.trim(),
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
