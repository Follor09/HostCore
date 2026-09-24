/**
 * PiCore Dashboard - Server & Homelab Edition
 * Frontend Controller & Real-Time Engine
 */

(function () {
  'use strict';

  // State Management
  const state = {
    ws: null,
    connected: false,
    reconnectAttempts: 0,
    theme: 'cyber',
    modalAction: null,
    history: {
      cpu: [],
      temp: [],
      netDown: [],
      netUp: [],
      netUp: [],
      maxPoints: 25
    }
  };

  // DOM Elements Cache
  const els = {
    // Header
    deviceModel: document.getElementById('device-model-badge'),
    wsStatusChip: document.getElementById('ws-status-chip'),
    wsStatusText: document.getElementById('ws-status-text'),
    primaryIp: document.getElementById('primary-ip'),
    btnFullscreen: document.getElementById('btn-fullscreen'),
    btnThemeToggle: document.getElementById('btn-theme-toggle'),
    btnRebootModal: document.getElementById('btn-reboot-modal'),
    btnShutdownModal: document.getElementById('btn-shutdown-modal'),

    // Top Widgets
    clockTime: document.getElementById('clock-time'),
    clockDate: document.getElementById('clock-date'),
    osNameTag: document.getElementById('os-name-tag'),
    uptimeVal: document.getElementById('uptime-val'),
    bootTimeVal: document.getElementById('boot-time-val'),

    // Weather
    weatherCity: document.getElementById('weather-city'),
    weatherCityBtn: document.getElementById('weather-city-btn'),
    weatherCityForm: document.getElementById('weather-city-form'),
    weatherCityInput: document.getElementById('weather-city-input'),
    weatherIcon: document.getElementById('weather-icon-container'),
    weatherHours: document.getElementById('weather-hours'),
    weatherTomorrow: document.getElementById('weather-tomorrow'),
    weatherWeek: document.getElementById('weather-week'),
    weatherTabs: document.querySelectorAll('.weather-tab'),
    weatherPanes: document.querySelectorAll('.weather-pane'),
    weatherTemp: document.getElementById('weather-temp'),
    weatherDesc: document.getElementById('weather-description'),
    weatherHumidity: document.getElementById('weather-humidity'),
    weatherWind: document.getElementById('weather-wind'),
    btnWeatherRefresh: document.getElementById('btn-weather-refresh'),

    // Server Services
    servicesActiveCount: document.getElementById('services-active-count'),
    servicesContainer: document.getElementById('services-container'),

    // CPU
    cpuCoreCount: document.getElementById('cpu-core-count'),
    cpuFreqVal: document.getElementById('cpu-freq-val'),
    cpuTotalPercent: document.getElementById('cpu-total-percent'),
    coresGridContainer: document.getElementById('cores-grid-container'),
    cpuChart: document.getElementById('cpu-history-chart'),

    // Temperature & Throttle
    cpuTempVal: document.getElementById('cpu-temp-val'),
    tempSourceBadge: document.getElementById('temp-source-badge'),
    throttleTag: document.getElementById('throttle-tag'),
    throttleDescription: document.getElementById('throttle-description'),
    tempChart: document.getElementById('temp-history-chart'),

    // RAM & Swap
    ramPercentVal: document.getElementById('ram-percent-val'),
    ramUsedSummary: document.getElementById('ram-used-summary'),
    ramProgressFill: document.getElementById('ram-progress-fill'),
    ramUsedTxt: document.getElementById('ram-used-txt'),
    ramAvailTxt: document.getElementById('ram-avail-txt'),
    swapVal: document.getElementById('swap-val'),
    swapProgressFill: document.getElementById('swap-progress-fill'),

    // Disk
    diskPercentVal: document.getElementById('disk-percent-val'),
    diskProgressFill: document.getElementById('disk-progress-fill'),
    diskUsedTxt: document.getElementById('disk-used-txt'),
    diskFreeTxt: document.getElementById('disk-free-txt'),
    diskTotalTxt: document.getElementById('disk-total-txt'),
    archVal: document.getElementById('arch-val'),

    // Network
    netDownRate: document.getElementById('net-down-rate'),
    diskType: document.getElementById('disk-type'),
    diskSummary: document.getElementById('disk-summary'),
    netUpRate: document.getElementById('net-up-rate'),
    netTotalRecv: document.getElementById('net-total-recv'),
    netTotalSent: document.getElementById('net-total-sent'),
    netChart: document.getElementById('network-history-chart'),

    // Drives Explorer
    drivesListContainer: document.getElementById('drives-list-container'),
    btnRefreshDrives: document.getElementById('btn-refresh-drives'),

    // Tabs
    tabBtnProcesses: document.getElementById('tab-btn-processes'),
    tabBtnDiagnostics: document.getElementById('tab-btn-diagnostics'),
    tabBtnLogs: document.getElementById('tab-btn-logs'),
    tabBtnUpdates: document.getElementById('tab-btn-updates'),
    paneProcesses: document.getElementById('pane-processes'),
    paneDiagnostics: document.getElementById('pane-diagnostics'),
    paneLogs: document.getElementById('pane-logs'),
    paneUpdates: document.getElementById('pane-updates'),

    // Git & Auto-Updates
    gitChip: document.getElementById('git-chip'),
    gitCommitTag: document.getElementById('git-commit-tag'),
    btnHeaderUpdate: document.getElementById('btn-header-update'),
    headerUpdateText: document.getElementById('header-update-text'),
    updatesStatusBox: document.getElementById('updates-status-box'),
    updateStatusIcon: document.getElementById('update-status-icon'),
    updateStatusTitle: document.getElementById('update-status-title'),
    updateStatusSub: document.getElementById('update-status-sub'),
    updateDetailsBox: document.getElementById('update-details-box'),
    updateIncomingMsg: document.getElementById('update-incoming-msg'),
    btnCheckUpdates: document.getElementById('btn-check-updates'),
    btnApplyUpdate: document.getElementById('btn-apply-update'),
    gitMetaCommit: document.getElementById('git-meta-commit'),
    gitMetaBranch: document.getElementById('git-meta-branch'),
    updateTerminalLog: document.getElementById('update-terminal-log'),

    // Processes, Diagnostics, Logs
    processesTbody: document.getElementById('processes-tbody'),
    terminalStatus: document.getElementById('terminal-status'),
    terminalOutput: document.getElementById('terminal-output'),
    logsOutput: document.getElementById('logs-output'),
    btnRefreshLogs: document.getElementById('btn-refresh-logs'),

    // Modal & Toasts
    modal: document.getElementById('action-modal'),
    modalTitle: document.getElementById('modal-title'),
    modalMessage: document.getElementById('modal-message'),
    btnModalCancel: document.getElementById('btn-modal-cancel'),
    btnModalConfirm: document.getElementById('btn-modal-confirm'),
    toastContainer: document.getElementById('toast-container')
  };

  // ---------------------------------------------------------------------------
  // Canvas Sparkline Chart Drawer
  // ---------------------------------------------------------------------------
  // opts.max: shared y-scale for several series; opts.overlay: draw on top instead of clearing
  function drawSparkline(canvas, dataSeries, color, fillColor, opts = {}) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;

    if (!opts.overlay) ctx.clearRect(0, 0, width, height);
    if (dataSeries.length < 2) return;

    const min = 0;
    const max = opts.max || Math.max(100, Math.max(...dataSeries) * 1.15);
    const step = width / (state.history.maxPoints - 1);

    ctx.beginPath();
    const startIndex = Math.max(0, state.history.maxPoints - dataSeries.length);

    dataSeries.forEach((val, i) => {
      const x = (startIndex + i) * step;
      const normalized = (val - min) / (max - min || 1);
      const y = height - (normalized * (height - 10)) - 5;

      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.strokeStyle = color;
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();

    if (fillColor) {
      const lastX = (startIndex + dataSeries.length - 1) * step;
      const firstX = startIndex * step;
      ctx.lineTo(lastX, height);
      ctx.lineTo(firstX, height);
      ctx.closePath();

      const gradient = ctx.createLinearGradient(0, 0, 0, height);
      gradient.addColorStop(0, fillColor);
      gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
      ctx.fillStyle = gradient;
      ctx.fill();
    }
  }

  // ---------------------------------------------------------------------------
  // Real-Time Clock & Date
  // ---------------------------------------------------------------------------
  function startClock() {
    function updateClock() {
      const now = new Date();
      const hours = String(now.getHours()).padStart(2, '0');
      const minutes = String(now.getMinutes()).padStart(2, '0');
      const seconds = String(now.getSeconds()).padStart(2, '0');

      if (els.clockTime) {
        els.clockTime.textContent = `${hours}:${minutes}:${seconds}`;
      }

      if (els.clockDate) {
        const options = { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };
        els.clockDate.textContent = now.toLocaleDateString('de-DE', options);
      }
    }
    updateClock();
    setInterval(updateClock, 1000);
  }

  // ---------------------------------------------------------------------------
  // Toast Notifications
  // ---------------------------------------------------------------------------
  function showToast(message, type = 'info') {
    if (!els.toastContainer) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;

    els.toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  // ---------------------------------------------------------------------------
  // Weather Widget Loader
  // ---------------------------------------------------------------------------
  // Inline SVG weather icons (no emoji font needed on Raspberry Pi OS)
  const WX_CLOUD = '<path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25"/>';
  const WX_ICONS = {
    sun: '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>',
    partly: '<circle cx="8" cy="8" r="3"/><path d="M8 2v1M2 8h1M3.76 3.76l.7.7M12.24 3.76l-.7.7"/><path d="M17.5 12h-.9A5.5 5.5 0 1 0 11 19.5h6.5a3.75 3.75 0 0 0 0-7.5z"/>',
    cloud: '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
    fog: '<path d="M3 8h18M5 12h14M3 16h18M7 20h10"/>',
    drizzle: WX_CLOUD + '<path d="M8 19v2M8 13v2M16 19v2M16 13v2M12 21v2M12 15v2"/>',
    rain: WX_CLOUD + '<path d="M16 13v8M8 13v8M12 15v8"/>',
    snow: WX_CLOUD + '<path d="M8 16h.01M8 20h.01M12 18h.01M12 22h.01M16 16h.01M16 20h.01"/>',
    thunder: '<path d="M19 16.9A5 5 0 0 0 18 7h-1.26a8 8 0 1 0-11.62 9"/><path d="M13 11l-4 6h6l-4 6"/>'
  };

  function wxKind(code) {
    if (code === 0) return 'sun';
    if (code <= 2) return 'partly';
    if (code === 3) return 'cloud';
    if (code <= 48) return 'fog';
    if (code <= 57) return 'drizzle';
    if (code <= 67 || (code >= 80 && code <= 82)) return 'rain';
    if (code <= 77 || code === 85 || code === 86) return 'snow';
    return 'thunder';
  }

  function wxIcon(code, size = 20) {
    const kind = wxKind(code);
    const cls = kind === 'partly' ? 'sun' : kind;
    return `<span class="wx-icon wx-${cls}"><svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${WX_ICONS[kind]}</svg></span>`;
  }

  function precipTag(prob) {
    return `<span class="weather-precip ${prob < 20 ? 'dry' : ''}">${prob}%</span>`;
  }

  function dayName(dateStr, index) {
    if (index === 0) return 'Heute';
    if (index === 1) return 'Morgen';
    return new Date(`${dateStr}T12:00:00`).toLocaleDateString('de-DE', { weekday: 'short' });
  }

  function renderHours(hours) {
    if (!els.weatherHours) return;
    if (!hours || !hours.length) {
      els.weatherHours.innerHTML = '<div class="weather-empty">Keine Stundenvorhersage verfügbar</div>';
      return;
    }
    els.weatherHours.innerHTML = hours.map((h, i) => `
      <div class="weather-hour ${i === 0 ? 'now' : ''}">
        <span class="weather-hour-time">${i === 0 ? 'Jetzt' : h.time}</span>
        ${wxIcon(h.weather_code, 20)}
        <span class="weather-hour-temp">${h.temp}°</span>
        ${precipTag(h.precip_prob)}
      </div>`).join('');
  }

  function renderTomorrow(day) {
    if (!els.weatherTomorrow) return;
    if (!day) {
      els.weatherTomorrow.innerHTML = '<div class="weather-empty">Keine Vorhersage für morgen verfügbar</div>';
      return;
    }
    const date = new Date(`${day.date}T12:00:00`).toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'numeric' });
    els.weatherTomorrow.innerHTML = `
      <div class="weather-tomorrow-head">
        <div>
          <div class="weather-tomorrow-temps">${day.temp_max}°<span class="min">/ ${day.temp_min}°</span></div>
          <div class="weather-tomorrow-sub">${day.description} <span class="date">· ${date}</span></div>
        </div>
        <div class="weather-icon-badge">${wxIcon(day.weather_code, 34)}</div>
      </div>
      <div class="weather-details-grid four">
        <div class="weather-detail-item"><span class="detail-label">Regen</span><span class="detail-val">${day.precip_prob}% · ${day.precip_sum} mm</span></div>
        <div class="weather-detail-item"><span class="detail-label">Wind max.</span><span class="detail-val">${day.wind_max} km/h</span></div>
        <div class="weather-detail-item"><span class="detail-label">Sonne</span><span class="detail-val">${day.sunrise} – ${day.sunset}</span></div>
        <div class="weather-detail-item"><span class="detail-label">UV-Index</span><span class="detail-val">${day.uv_max}</span></div>
      </div>`;
  }

  function renderWeek(days) {
    if (!els.weatherWeek) return;
    if (!days || !days.length) {
      els.weatherWeek.innerHTML = '<div class="weather-empty">Keine Wochenvorhersage verfügbar</div>';
      return;
    }
    // Bars share one scale so the days are comparable
    const lo = Math.min(...days.map(d => d.temp_min));
    const hi = Math.max(...days.map(d => d.temp_max));
    const span = Math.max(hi - lo, 1);
    els.weatherWeek.innerHTML = days.map((d, i) => `
      <div class="weather-day">
        <span class="weather-day-name">${dayName(d.date, i)}</span>
        ${wxIcon(d.weather_code, 16)}
        ${precipTag(d.precip_prob)}
        <span class="weather-day-min">${d.temp_min}°</span>
        <div class="weather-range"><span style="left:${(d.temp_min - lo) / span * 100}%;right:${(hi - d.temp_max) / span * 100}%"></span></div>
        <span class="weather-day-max">${d.temp_max}°</span>
      </div>`).join('');
  }

  function renderWeather(data) {
    if (els.weatherCity) els.weatherCity.textContent = data.city;
    if (els.weatherTemp) els.weatherTemp.textContent = data.temp;
    if (els.weatherDesc) els.weatherDesc.textContent = data.description;
    if (els.weatherHumidity) els.weatherHumidity.textContent = `${data.humidity}%`;
    if (els.weatherWind) els.weatherWind.textContent = `${data.wind_speed} km/h`;
    if (els.weatherIcon) els.weatherIcon.innerHTML = wxIcon(data.weather_code, 36);
    renderHours(data.hourly);
    renderTomorrow(data.daily && data.daily[1]);
    renderWeek(data.daily);
  }

  function selectWeatherTab(tab) {
    els.weatherTabs.forEach(b => {
      const active = b.dataset.tab === tab;
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', active);
    });
    els.weatherPanes.forEach(p => { p.hidden = p.dataset.pane !== tab; });
    try { localStorage.setItem('weatherTab', tab); } catch (e) { /* storage unavailable */ }
  }

  async function loadWeather() {
    try {
      const res = await fetch('/api/weather');
      if (res.ok) renderWeather(await res.json());
    } catch (e) {
      console.warn('Weather fetch failed:', e);
    }
  }

  // Inline city editor: click the city name, type, Enter saves, Esc cancels
  function toggleCityEditor(open) {
    els.weatherCityBtn.hidden = open;
    els.weatherCityForm.hidden = !open;
    if (open) {
      els.weatherCityInput.value = els.weatherCity.textContent;
      els.weatherCityInput.focus();
      els.weatherCityInput.select();
    }
  }

  async function saveWeatherCity(e) {
    e.preventDefault();
    const city = els.weatherCityInput.value.trim();
    if (!city || city === els.weatherCity.textContent) return toggleCityEditor(false);

    els.weatherCityInput.disabled = true;
    try {
      const res = await fetch('/api/weather/location', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ city })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Ort konnte nicht gesetzt werden.');
      renderWeather(data);
      toggleCityEditor(false);
      showToast(`Wetter-Ort: ${data.city}`, 'success');
    } catch (err) {
      showToast(err.message, 'error');
      els.weatherCityInput.focus();
    } finally {
      els.weatherCityInput.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Server Services Component
  // ---------------------------------------------------------------------------
  function renderServices(services) {
    if (!els.servicesContainer || !services) return;
    els.servicesContainer.innerHTML = '';

    const activeCount = services.filter(s => s.running).length;
    if (els.servicesActiveCount) {
      els.servicesActiveCount.textContent = `${activeCount} / ${services.length} Aktiv`;
    }

    services.forEach(srv => {
      const status = srv.status || (srv.running ? 'active' : 'inactive');
      const tile = document.createElement('div');
      tile.className = `service-tile ${status}`;

      tile.innerHTML = `
        <div class="service-info">
          <span class="service-name">${escapeHtml(srv.name)}</span>
          <span class="service-port">${escapeHtml(srv.detail || (srv.port ? `Port :${srv.port}` : ''))}</span>
        </div>
        <div class="service-status-pill ${status}">
          <span class="service-dot ${status}"></span>
          <span>${escapeHtml(srv.status_text)}</span>
        </div>
      `;

      els.servicesContainer.appendChild(tile);
    });
  }

  // ---------------------------------------------------------------------------
  // Drives Explorer Component
  // ---------------------------------------------------------------------------
  function renderDrives(drives) {
    if (!els.drivesListContainer || !drives) return;
    els.drivesListContainer.innerHTML = '';

    if (drives.length === 0) {
      els.drivesListContainer.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 20px;">Keine Partitionen gefunden.</div>';
      return;
    }

    drives.forEach(d => {
      const item = document.createElement('div');
      item.className = 'drive-item';

      let badgeClass = '';
      if (d.percent > 85) badgeClass = 'danger';
      else if (d.percent > 70) badgeClass = 'warn';

      item.innerHTML = `
        <div class="drive-header">
          <div class="drive-title-wrap">
            <span class="drive-label">${escapeHtml(d.label)}</span>
            <span class="drive-fstype">${escapeHtml(d.fstype)}</span>
          </div>
          <span class="drive-percent-badge ${badgeClass}">${d.percent}%</span>
        </div>
        <div class="progress-bar-bg">
          <div class="progress-bar-fill fill-emerald" style="width: ${d.percent}%;"></div>
        </div>
        <div class="drive-legend">
          <span>Belegt: <b>${d.used_gb} GB</b></span>
          <span>Frei: <b>${d.free_gb} GB</b></span>
          <span>Gesamt: <b>${d.total_gb} GB</b></span>
        </div>
      `;
      els.drivesListContainer.appendChild(item);
    });
  }

  async function loadDrives() {
    try {
      const res = await fetch('/api/drives');
      if (res.ok) {
        const data = await res.json();
        renderDrives(data.drives);
      }
    } catch (e) {
      console.warn('Failed to load drives:', e);
    }
  }

  // ---------------------------------------------------------------------------
  // System Logs Component
  // ---------------------------------------------------------------------------
  async function loadLogs() {
    if (!els.logsOutput) return;
    els.logsOutput.textContent = 'Lese System-Journal / dmesg...\n';
    try {
      const res = await fetch('/api/logs?lines=30');
      if (res.ok) {
        const data = await res.json();
        els.logsOutput.textContent = data.logs || 'Keine Logs vorhanden.';
      } else {
        els.logsOutput.textContent = 'Fehler beim Abrufen der Logs.';
      }
    } catch (e) {
      els.logsOutput.textContent = `Verbindungsfehler: ${e.message}`;
    }
  }

  // ---------------------------------------------------------------------------
  // Git Auto-Updater Component
  // ---------------------------------------------------------------------------
  function renderUpdater(u) {
    if (!u) return;

    if (els.gitCommitTag) {
      els.gitCommitTag.textContent = u.is_git_repo ? `git: ${u.current_commit}` : 'Kein Git-Repo';
    }
    if (els.gitMetaCommit) {
      els.gitMetaCommit.textContent = u.is_git_repo ? `${u.current_commit} (${u.commit_message || 'aktuell'})` : 'Nicht initialisiert';
    }
    if (els.gitMetaBranch) {
      els.gitMetaBranch.textContent = u.branch || 'main';
    }

    // Update banner & button state
    if (u.update_available) {
      if (els.btnHeaderUpdate) {
        els.btnHeaderUpdate.style.display = 'flex';
        if (els.headerUpdateText) els.headerUpdateText.textContent = `Update verfügbar (${u.commits_behind})`;
      }
      if (els.btnApplyUpdate) els.btnApplyUpdate.style.display = 'inline-flex';
      if (els.updateDetailsBox) {
        els.updateDetailsBox.style.display = 'block';
        if (els.updateIncomingMsg) {
          els.updateIncomingMsg.textContent = `${u.latest_remote_commit}: ${u.latest_remote_message}`;
        }
      }
      if (els.updatesStatusBox) els.updatesStatusBox.classList.add('has-update');
      if (els.updateStatusTitle) els.updateStatusTitle.textContent = '🚀 Neues Update auf GitHub verfügbar!';
      if (els.updateStatusSub) els.updateStatusSub.textContent = `${u.commits_behind} neuer Commit im Repository.`;
    } else {
      if (els.btnHeaderUpdate) els.btnHeaderUpdate.style.display = 'none';
      if (els.btnApplyUpdate) els.btnApplyUpdate.style.display = 'none';
      if (els.updateDetailsBox) els.updateDetailsBox.style.display = 'none';
      if (els.updatesStatusBox) els.updatesStatusBox.classList.remove('has-update');
      if (els.updateStatusTitle) els.updateStatusTitle.textContent = 'System ist auf dem neuesten Stand';
      if (els.updateStatusSub) {
        els.updateStatusSub.textContent = u.last_check_timestamp
          ? `Letzter Check: vor ${Math.round((Date.now() / 1000 - u.last_check_timestamp) / 60)} Min`
          : 'Regelmäßiger Check aktiv';
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Top Processes Table
  // ---------------------------------------------------------------------------
  function renderProcesses(procs) {
    if (!els.processesTbody) return;
    els.processesTbody.innerHTML = '';

    if (!procs || procs.length === 0) {
      els.processesTbody.innerHTML = '<tr><td colspan="5" class="table-loading">Keine Prozesse gefunden</td></tr>';
      return;
    }

    procs.forEach(p => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span style="color: var(--accent-cyan);">${p.pid}</span></td>
        <td><b>${escapeHtml(p.name)}</b></td>
        <td><span style="color: ${p.cpu_percent > 30 ? 'var(--accent-amber)' : 'inherit'};">${p.cpu_percent}%</span></td>
        <td>${p.memory_percent}%</td>
        <td><span style="color: var(--text-dim);">${escapeHtml(p.username || '-')}</span></td>
      `;
      els.processesTbody.appendChild(tr);
    });
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ---------------------------------------------------------------------------
  // Diagnostic Commands Execution
  // ---------------------------------------------------------------------------
  function initDiagnostics() {
    const btns = document.querySelectorAll('.diag-cmd-btn');
    btns.forEach(btn => {
      btn.addEventListener('click', async () => {
        const cmd = btn.dataset.cmd;
        if (!cmd) return;

        if (els.terminalStatus) els.terminalStatus.textContent = `Führe '${cmd}' aus...`;
        if (els.terminalOutput) els.terminalOutput.textContent = `$ pi-diagnostics run ${cmd}...\n`;

        try {
          const res = await fetch('/api/action/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: cmd })
          });
          const data = await res.json();
          if (res.ok) {
            if (els.terminalStatus) els.terminalStatus.textContent = `Befehl '${cmd}' abgeschlossen (Exit: ${data.returncode})`;
            if (els.terminalOutput) els.terminalOutput.textContent = `$ ${cmd}\n\n` + (data.stdout || data.error || 'Keine Ausgabe.');
          } else {
            if (els.terminalOutput) els.terminalOutput.textContent = `Fehler: ${data.detail || 'Unbekannter Fehler'}`;
          }
        } catch (e) {
          if (els.terminalOutput) els.terminalOutput.textContent = `Verbindungsfehler: ${e.message}`;
        }
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Update UI with System Snapshot
  // ---------------------------------------------------------------------------
  function updateSystemMetrics(sys) {
    if (els.deviceModel) {
      els.deviceModel.textContent = sys.model || 'Raspberry Pi Server';
    }
    if (els.osNameTag) {
      els.osNameTag.textContent = sys.os || 'Linux';
    }
    if (els.archVal) {
      els.archVal.textContent = sys.architecture || 'ARM64';
    }
    if (els.primaryIp && sys.network_ip) {
      els.primaryIp.textContent = sys.network_ip.primary_ip || '127.0.0.1';
    }

    if (sys.uptime) {
      if (els.uptimeVal) els.uptimeVal.textContent = sys.uptime.formatted;
      if (els.bootTimeVal) els.bootTimeVal.textContent = sys.uptime.boot_time;
    }

    // CPU Metrics
    if (sys.cpu) {
      if (els.cpuTotalPercent) els.cpuTotalPercent.textContent = `${sys.cpu.percent}%`;
      if (els.cpuCoreCount) els.cpuCoreCount.textContent = sys.cpu.count;
      if (els.cpuFreqVal) els.cpuFreqVal.textContent = sys.cpu.frequency_mhz;

      state.history.cpu.push(sys.cpu.percent);
      if (state.history.cpu.length > state.history.maxPoints) state.history.cpu.shift();
      drawSparkline(els.cpuChart, state.history.cpu, '#00f0ff', 'rgba(0, 240, 255, 0.25)');

      if (els.coresGridContainer && sys.cpu.cores) {
        els.coresGridContainer.innerHTML = '';
        sys.cpu.cores.forEach((coreVal, idx) => {
          const coreDiv = document.createElement('div');
          coreDiv.className = 'core-item';
          coreDiv.innerHTML = `
            <span class="core-label">K${idx + 1}</span>
            <span class="core-val">${coreVal}%</span>
          `;
          els.coresGridContainer.appendChild(coreDiv);
        });
      }
    }

    // Temperature & Throttling
    if (sys.temperature) {
      const temp = sys.temperature.celsius;
      if (els.cpuTempVal) {
        els.cpuTempVal.textContent = `${temp}°C`;
        els.cpuTempVal.className = 'metric-highlight-value temp-badge';
        if (temp < 52) {
          els.cpuTempVal.classList.add('optimal');
        } else if (temp > 75) {
          els.cpuTempVal.classList.add('hot');
        }
      }
      if (els.tempSourceBadge) {
        els.tempSourceBadge.textContent = sys.temperature.is_simulated
          ? 'Sensor: Dev-Simulation'
          : `Sensor: ${sys.temperature.source}`;
      }

      state.history.temp.push(temp);
      if (state.history.temp.length > state.history.maxPoints) state.history.temp.shift();
      drawSparkline(els.tempChart, state.history.temp, '#ffb703', 'rgba(255, 183, 3, 0.25)');
    }

    // Throttled Flags
    if (sys.throttled) {
      const th = sys.throttled;
      if (els.throttleTag) {
        els.throttleTag.textContent = th.undervoltage || th.throttled ? 'Warnung' : 'Optimal';
        els.throttleTag.className = `throttle-tag ${th.undervoltage || th.throttled ? 'warning' : 'normal'}`;
      }
      if (els.throttleDescription) {
        els.throttleDescription.textContent = th.summary;
      }
    }

    // Memory (RAM)
    if (sys.memory) {
      if (els.ramPercentVal) els.ramPercentVal.textContent = `${sys.memory.percent}%`;
      if (els.ramProgressFill) els.ramProgressFill.style.width = `${sys.memory.percent}%`;
      if (els.ramUsedSummary) els.ramUsedSummary.textContent = `${sys.memory.used_mb} MB von ${sys.memory.total_mb} MB`;
      if (els.ramUsedTxt) els.ramUsedTxt.textContent = `${sys.memory.used_mb} MB`;
      if (els.ramAvailTxt) els.ramAvailTxt.textContent = `${sys.memory.available_mb} MB`;
    }

    // Swap
    if (sys.swap) {
      if (els.swapVal) els.swapVal.textContent = `${sys.swap.percent}% (${sys.swap.used_mb} MB)`;
      if (els.swapProgressFill) els.swapProgressFill.style.width = `${sys.swap.percent}%`;
    }

    // Disk
    if (sys.disk) {
      if (els.diskPercentVal) els.diskPercentVal.textContent = `${sys.disk.percent}%`;
      if (els.diskProgressFill) els.diskProgressFill.style.width = `${sys.disk.percent}%`;
      if (els.diskUsedTxt) els.diskUsedTxt.textContent = `${sys.disk.used_gb} GB`;
      if (els.diskFreeTxt) els.diskFreeTxt.textContent = `${sys.disk.free_gb} GB`;
      if (els.diskTotalTxt) els.diskTotalTxt.textContent = `${sys.disk.total_gb} GB`;
      if (els.diskType && sys.disk.type) els.diskType.textContent = sys.disk.type;
      if (els.diskSummary && sys.disk.device) {
        els.diskSummary.textContent = sys.disk.model ? `${sys.disk.model} · /` : `${sys.disk.device} · /`;
        els.diskSummary.title = sys.disk.device;
      }
    }

    // Network
    if (sys.network) {
      const down = sys.network.download_kbps;
      const up = sys.network.upload_kbps;

      if (els.netDownRate) {
        els.netDownRate.textContent = down > 1024 ? `${(down / 1024).toFixed(2)} MB/s` : `${down} KB/s`;
      }
      if (els.netUpRate) {
        els.netUpRate.textContent = up > 1024 ? `${(up / 1024).toFixed(2)} MB/s` : `${up} KB/s`;
      }
      if (els.netTotalRecv) els.netTotalRecv.textContent = `${sys.network.total_recv_mb} MB`;
      if (els.netTotalSent) els.netTotalSent.textContent = `${sys.network.total_sent_mb} MB`;

      const h = state.history;
      h.netDown.push(down);
      h.netUp.push(up);
      if (h.netDown.length > h.maxPoints) h.netDown.shift();
      if (h.netUp.length > h.maxPoints) h.netUp.shift();
      // Both lines on one scale so upload isn't cut off or exaggerated
      const netMax = Math.max(100, ...h.netDown, ...h.netUp) * 1.15;
      drawSparkline(els.netChart, h.netDown, '#38bdf8', 'rgba(56, 189, 248, 0.25)', { max: netMax });
      drawSparkline(els.netChart, h.netUp, '#a855f7', 'rgba(168, 85, 247, 0.18)', { max: netMax, overlay: true });
    }
  }

  // ---------------------------------------------------------------------------
  // Real-Time WebSocket Streaming
  // ---------------------------------------------------------------------------
  function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws`;

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = function () {
      state.connected = true;
      state.reconnectAttempts = 0;
      if (els.wsStatusText) els.wsStatusText.textContent = 'Live WebSocket';
      if (els.wsStatusChip) {
        els.wsStatusChip.style.borderColor = 'rgba(0, 255, 163, 0.4)';
        els.wsStatusChip.style.color = 'var(--accent-emerald)';
      }
    };

    state.ws.onmessage = function (event) {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'live_update') {
          if (msg.system) updateSystemMetrics(msg.system);
          if (msg.processes) renderProcesses(msg.processes);
          if (msg.services) renderServices(msg.services);
          if (msg.drives) renderDrives(msg.drives);
          if (msg.updater) renderUpdater(msg.updater);
        }
      } catch (err) {
        console.error('Error parsing live update:', err);
      }
    };

    state.ws.onclose = function () {
      state.connected = false;
      if (els.wsStatusText) els.wsStatusText.textContent = 'Verbindung getrennt';
      if (els.wsStatusChip) {
        els.wsStatusChip.style.borderColor = 'rgba(255, 42, 95, 0.4)';
        els.wsStatusChip.style.color = 'var(--accent-rose)';
      }

      state.reconnectAttempts++;
      const delay = Math.min(5000, 1000 * state.reconnectAttempts);
      setTimeout(connectWebSocket, delay);
    };

    state.ws.onerror = function (err) {
      console.warn('WebSocket error:', err);
      state.ws.close();
    };
  }

  async function fetchSystemFallback() {
    if (state.connected) return;
    try {
      const res = await fetch('/api/system');
      if (res.ok) {
        const sys = await res.json();
        updateSystemMetrics(sys);
      }
    } catch (e) {
      // offline
    }
  }

  // ---------------------------------------------------------------------------
  // Action Modals (Reboot & Shutdown)
  // ---------------------------------------------------------------------------
  function openModal(action) {
    state.modalAction = action;
    if (action === 'reboot') {
      els.modalTitle.textContent = 'Raspberry Pi Neustart';
      els.modalMessage.textContent = 'Möchtest du den Raspberry Pi jetzt neu starten? Laufende Serverdienste werden beendet.';
      els.btnModalConfirm.className = 'btn-confirm';
      els.btnModalConfirm.style.background = 'var(--accent-amber)';
      els.btnModalConfirm.textContent = 'Jetzt Neustarten';
    } else if (action === 'shutdown') {
      els.modalTitle.textContent = 'Raspberry Pi Herunterfahren';
      els.modalMessage.textContent = 'Möchtest du das System sicher ausschalten? Danach muss die Stromversorgung getrennt bzw. wiederhergestellt werden.';
      els.btnModalConfirm.className = 'btn-confirm';
      els.btnModalConfirm.style.background = 'var(--accent-rose)';
      els.btnModalConfirm.textContent = 'Jetzt Ausschalten';
    }
    els.modal.classList.add('open');
  }

  function closeModal() {
    state.modalAction = null;
    els.modal.classList.remove('open');
  }

  async function executeModalAction() {
    const action = state.modalAction;
    closeModal();
    if (!action) return;

    try {
      const res = await fetch(`/api/action/${action}`, { method: 'POST' });
      const data = await res.json();
      showToast(data.message || 'Befehl ausgeführt', 'info');
    } catch (err) {
      showToast(`Fehler: ${err.message}`, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Tab Navigation & Header Controls
  // ---------------------------------------------------------------------------
  function initTabs() {
    function selectTab(btn, pane) {
      [els.tabBtnProcesses, els.tabBtnDiagnostics, els.tabBtnLogs, els.tabBtnUpdates].forEach(b => b && b.classList.remove('active'));
      [els.paneProcesses, els.paneDiagnostics, els.paneLogs, els.paneUpdates].forEach(p => p && p.classList.remove('active'));
      if (btn) btn.classList.add('active');
      if (pane) pane.classList.add('active');
    }

    if (els.tabBtnProcesses) {
      els.tabBtnProcesses.addEventListener('click', () => selectTab(els.tabBtnProcesses, els.paneProcesses));
    }
    if (els.tabBtnDiagnostics) {
      els.tabBtnDiagnostics.addEventListener('click', () => selectTab(els.tabBtnDiagnostics, els.paneDiagnostics));
    }
    if (els.tabBtnLogs) {
      els.tabBtnLogs.addEventListener('click', () => {
        selectTab(els.tabBtnLogs, els.paneLogs);
        loadLogs();
      });
    }
    if (els.tabBtnUpdates) {
      els.tabBtnUpdates.addEventListener('click', () => {
        selectTab(els.tabBtnUpdates, els.paneUpdates);
      });
    }
  }

  function initUpdaterControls() {
    // Header update pill -> jumps to updates tab
    if (els.btnHeaderUpdate) {
      els.btnHeaderUpdate.addEventListener('click', () => {
        if (els.tabBtnUpdates) els.tabBtnUpdates.click();
      });
    }

    // Check updates button
    if (els.btnCheckUpdates) {
      els.btnCheckUpdates.addEventListener('click', async () => {
        showToast('Suche nach Updates auf GitHub...', 'info');
        if (els.updateTerminalLog) els.updateTerminalLog.textContent = 'Suche nach neuen Commits auf GitHub (git fetch origin)...';
        try {
          const res = await fetch('/api/updates/check', { method: 'POST' });
          if (res.ok) {
            const data = await res.json();
            renderUpdater(data);
            if (data.update_available) {
              showToast(`Update verfügbar: ${data.commits_behind} neuer Commit`, 'warning');
              if (els.updateTerminalLog) els.updateTerminalLog.textContent = `Update gefunden!\nNeuester Commit: ${data.latest_remote_commit}\nNachricht: ${data.latest_remote_message}`;
            } else {
              showToast('System ist auf dem neuesten Stand', 'success');
              if (els.updateTerminalLog) els.updateTerminalLog.textContent = 'Bereits auf dem neuesten Stand von origin/' + (data.branch || 'main');
            }
          }
        } catch (e) {
          showToast('Update-Prüfung fehlgeschlagen', 'error');
        }
      });
    }

    // Apply update button
    if (els.btnApplyUpdate) {
      els.btnApplyUpdate.addEventListener('click', async () => {
        showToast('Installiere Update vom GitHub Repo...', 'info');
        if (els.updateTerminalLog) els.updateTerminalLog.textContent = 'Starte git pull und Abhängigkeiten-Prüfung...\n';
        els.btnApplyUpdate.disabled = true;

        try {
          const res = await fetch('/api/updates/apply', { method: 'POST' });
          const data = await res.json();
          if (res.ok && data.status === 'success') {
            if (els.updateTerminalLog) els.updateTerminalLog.textContent = data.logs + '\n\n' + data.message;
            showToast('Update installiert! Server wird neu gestartet...', 'success');
            // Give server 3 seconds to restart and reload page
            setTimeout(() => {
              location.reload();
            }, 3500);
          } else {
            showToast(`Update fehlgeschlagen: ${data.message || 'Fehler'}`, 'error');
            if (els.updateTerminalLog) els.updateTerminalLog.textContent = data.logs || data.message || 'Fehler beim Update.';
            els.btnApplyUpdate.disabled = false;
          }
        } catch (e) {
          showToast(`Netzwerkfehler: ${e.message}`, 'error');
          els.btnApplyUpdate.disabled = false;
        }
      });
    }
  }

  function initHeaderControls() {
    if (els.btnFullscreen) {
      els.btnFullscreen.addEventListener('click', () => {
        if (!document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(err => {
            showToast('Vollbildmodus konnte nicht aktiviert werden', 'warning');
          });
        } else {
          document.exitFullscreen();
        }
      });
    }

    if (els.btnThemeToggle) {
      els.btnThemeToggle.addEventListener('click', () => {
        if (state.theme === 'cyber') {
          document.body.classList.remove('theme-cyber');
          document.body.classList.add('theme-solar');
          state.theme = 'solar';
          showToast('Design: Solar Cyber aktiviert');
        } else {
          document.body.classList.remove('theme-solar');
          document.body.classList.add('theme-cyber');
          state.theme = 'cyber';
          showToast('Design: Neon Cyber aktiviert');
        }
      });
    }

    if (els.btnRebootModal) els.btnRebootModal.addEventListener('click', () => openModal('reboot'));
    if (els.btnShutdownModal) els.btnShutdownModal.addEventListener('click', () => openModal('shutdown'));
    if (els.btnModalCancel) els.btnModalCancel.addEventListener('click', closeModal);
    if (els.btnModalConfirm) els.btnModalConfirm.addEventListener('click', executeModalAction);

    els.weatherTabs.forEach(b => b.addEventListener('click', () => selectWeatherTab(b.dataset.tab)));
    try {
      const saved = localStorage.getItem('weatherTab');
      if (saved && document.querySelector(`.weather-pane[data-pane="${saved}"]`)) selectWeatherTab(saved);
    } catch (e) { /* storage unavailable */ }

    if (els.weatherCityBtn && els.weatherCityForm && els.weatherCityInput) {
      els.weatherCityBtn.addEventListener('click', () => toggleCityEditor(true));
      els.weatherCityForm.addEventListener('submit', saveWeatherCity);
      els.weatherCityInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') toggleCityEditor(false);
      });
      els.weatherCityInput.addEventListener('blur', () => {
        if (!els.weatherCityInput.disabled) toggleCityEditor(false);
      });
    }

    if (els.btnWeatherRefresh) {
      els.btnWeatherRefresh.addEventListener('click', () => {
        loadWeather();
        showToast('Wetterdaten werden aktualisiert...');
      });
    }

    if (els.btnRefreshDrives) {
      els.btnRefreshDrives.addEventListener('click', () => {
        loadDrives();
        showToast('Laufwerke aktualisiert');
      });
    }

    if (els.btnRefreshLogs) {
      els.btnRefreshLogs.addEventListener('click', () => {
        loadLogs();
        showToast('Logs aktualisiert');
      });
    }
  }

  // ---------------------------------------------------------------------------
  // App Bootstrapper
  // ---------------------------------------------------------------------------
  function init() {
    startClock();
    initHeaderControls();
    initUpdaterControls();
    initTabs();
    initDiagnostics();
    loadWeather();
    loadDrives();
    connectWebSocket();

    setInterval(loadWeather, 600000);
    setInterval(fetchSystemFallback, 3000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
