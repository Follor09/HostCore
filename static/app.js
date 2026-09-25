/**
 * PiCore Dashboard - Server & Homelab Edition
 * Frontend Controller & Real-Time Engine
 */

(function () {
  'use strict';

  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';

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
      power: [],
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
    fanCard: document.querySelector('.fan-card'),
    fanSubtitle: document.getElementById('fan-subtitle'),
    fanLevelVal: document.getElementById('fan-level-val'),
    fanRotor: document.getElementById('fan-rotor'),
    fanLevels: document.getElementById('fan-levels'),
    fanLevelsLabel: document.getElementById('fan-levels-label'),
    fanRpm: document.getElementById('fan-rpm'),
    fanPwm: document.getElementById('fan-pwm'),
    fanThresholds: document.getElementById('fan-thresholds'),
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
        els.clockDate.textContent = now.toLocaleDateString(LOCALE, options);
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
    return new Date(`${dateStr}T12:00:00`).toLocaleDateString(LOCALE, { weekday: 'short' });
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
    const date = new Date(`${day.date}T12:00:00`).toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'numeric' });
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
      tile.className = `service-tile ${status}${srv.unit ? ' clickable' : ''}`;
      tile.dataset.name = srv.name;
      tile.dataset.id = srv.id;
      if (srv.unit) {
        tile.tabIndex = 0;
        tile.setAttribute('role', 'button');
        tile.title = `${srv.name}: steuern & Log ansehen`;
        tile.addEventListener('click', () => openServiceSheet(srv));
        tile.addEventListener('keydown', e => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openServiceSheet(srv); }
        });
      }
      if (serviceSheet.srv && serviceSheet.srv.id === srv.id) updateServiceSheet(srv);

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

  // ---- Service sheet: start / stop / restart + journal ----
  const serviceSheet = { srv: null };
  const SERVICE_WARNINGS = {
    ssh: 'Achtung: Wenn du SSH stoppst, kannst du dich nicht mehr per SSH verbinden – nur noch über dieses Dashboard.',
    docker: 'Stoppen oder Neustarten von Docker hält alle Container an.',
    netbird: 'Wenn du über NetBird verbunden bist, verlierst du beim Stoppen die Verbindung zum Dashboard.',
    kubernetes: 'Stoppen hält alle Pods auf diesem Node an.'
  };
  const SERVICE_ACTION_LABELS = { start: 'Starten', stop: 'Stoppen', restart: 'Neu starten', run: 'Update jetzt ausführen' };

  function updateServiceSheet(srv) {
    serviceSheet.srv = srv;
    $('service-modal-title').textContent = srv.name;
    $('service-modal-sub').textContent = `${srv.unit} · ${srv.detail}`;
    const levels = { active: 'good', inactive: '', error: 'critical' };
    setHealthTag($('service-modal-tag'), levels[srv.status] ?? '', srv.status_text);

    const warn = $('service-modal-warning');
    warn.hidden = !SERVICE_WARNINGS[srv.id];
    warn.textContent = SERVICE_WARNINGS[srv.id] || '';

    const box = $('service-modal-actions');
    const running = srv.status === 'active';
    if (box.dataset.for !== `${srv.id}:${running}`) {
      box.dataset.for = `${srv.id}:${running}`;
      box.innerHTML = '';
      srv.actions.forEach(action => {
        const btn = document.createElement('button');
        const destructive = action === 'stop' || action === 'restart';
        btn.className = destructive ? 'btn-danger' : 'btn-primary';
        btn.textContent = SERVICE_ACTION_LABELS[action];
        btn.disabled = (action === 'start' && running) || (action === 'stop' && !running);
        btn.addEventListener('click', async () => {
          // Stop / restart need a second click within 5 s
          if (destructive && !btn.classList.contains('confirming')) {
            btn.classList.add('confirming');
            btn.textContent = `Wirklich ${SERVICE_ACTION_LABELS[action].toLowerCase()}?`;
            setTimeout(() => { btn.classList.remove('confirming'); btn.textContent = SERVICE_ACTION_LABELS[action]; }, 5000);
            return;
          }
          btn.classList.remove('confirming');
          btn.textContent = SERVICE_ACTION_LABELS[action];
          box.querySelectorAll('button').forEach(b => { b.disabled = true; });
          try {
            const res = await fetch(`/api/services/${encodeURIComponent(srv.id)}/${action}`, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'Aktion fehlgeschlagen.');
            showToast(data.message, 'success');
          } catch (err) {
            showToast(err.message, 'error');
          } finally {
            box.dataset.for = '';  // rebuild with fresh state on the next live update
            setTimeout(loadServiceJournal, 2500);
          }
        });
        box.appendChild(btn);
      });
    }
  }

  async function loadServiceJournal() {
    const srv = serviceSheet.srv;
    if (!srv || !$('service-modal').classList.contains('open')) return;
    const log = $('service-modal-log'), hint = $('service-modal-hint');
    try {
      const res = await fetch(`/api/services/${encodeURIComponent(srv.id)}/journal`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Journal konnte nicht geladen werden.');
      log.textContent = data.log;
      log.scrollTop = log.scrollHeight;
      hint.hidden = !data.hint;
      hint.textContent = data.hint || '';
    } catch (err) {
      log.textContent = err.message;
    }
  }

  function openServiceSheet(srv) {
    $('service-modal-actions').dataset.for = '';
    updateServiceSheet(srv);
    $('service-modal-log').textContent = 'Lade…';
    loadServiceUptime(srv.id);
    const modal = $('service-modal');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    $('service-modal-close').focus();
    loadServiceJournal();
  }

  async function loadServiceUptime(id) {
    const box = $('service-modal-uptime');
    box.hidden = true;
    try {
      const res = await fetch('/api/monitor/uptime?days=7');
      if (!res.ok) return;
      const svc = (await res.json()).services.find(s => s.id === id);
      if (!svc || svc.uptime == null || !serviceSheet.srv || serviceSheet.srv.id !== id) return;
      box.innerHTML = '';
      const level = svc.uptime >= 99.5 ? 'good' : svc.uptime >= 95 ? 'warning' : 'critical';
      const tag = document.createElement('span');
      tag.className = `health-tag ${level}`;
      tag.textContent = `${svc.uptime.toLocaleString(LOCALE, { maximumFractionDigits: 2 })}%`;
      const text = document.createElement('span');
      text.textContent = svc.outages.length ? `Verfügbarkeit (7 Tage) · ${svc.outages.length}× nicht aktiv` : 'Verfügbarkeit (7 Tage)';
      const link = document.createElement('a');
      link.className = 'text-btn';
      link.href = '/system#verfuegbarkeit';
      link.textContent = 'Verlauf';
      box.append(text, tag, link);
      box.hidden = false;
    } catch (e) { /* optional */ }
  }

  function closeServiceSheet() {
    const modal = $('service-modal');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    serviceSheet.srv = null;
  }

  function initServiceSheet() {
    $('service-modal-close').addEventListener('click', closeServiceSheet);
    $('service-modal-refresh').addEventListener('click', loadServiceJournal);
    $('service-modal').addEventListener('click', e => { if (e.target.id === 'service-modal') closeServiceSheet(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && $('service-modal').classList.contains('open')) closeServiceSheet();
    });
  }

  // ---------------------------------------------------------------------------
  // Drives Explorer Component
  // ---------------------------------------------------------------------------
  // ---- Storage like lsblk: disks -> partitions, swap, other devices ----
  const DISK_KIND = { nvme: 'NVMe-SSD', sd: 'SD-Karte', usb: 'USB', sata: 'SATA', disk: 'Laufwerk' };

  // Same precision as lsblk (238.5G): one decimal, binary units
  function diskSize(b) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    return `${i === 0 ? b : b.toFixed(1)} ${units[i]}`;
  }

  function partLabel(p) {
    if (p.mount === '/') return 'System (/)';
    if (p.mount && p.mount.startsWith('/boot')) return `Boot (${p.mount})`;
    if (p.mount) return p.mount;
    return p.label || p.name;
  }

  function usageBlock(pct, used, free, total) {
    const level = pct > 85 ? 'danger' : pct > 70 ? 'warn' : '';
    return `
      <div class="progress-bar-bg"><div class="progress-bar-fill fill-emerald ${level}" style="width: ${pct}%;"></div></div>
      <div class="drive-legend">
        <span>Belegt: <b>${fmtSize(used)}</b></span>
        ${free != null ? `<span>Frei: <b>${fmtSize(free)}</b></span>` : ''}
        <span>Gesamt: <b>${fmtSize(total)}</b></span>
      </div>`;
  }

  function renderStorage(o) {
    const box = els.drivesListContainer;
    if (!box || !o) return;
    const html = [];
    o.disks.forEach(d => {
      if (d.name) {
        const kind = DISK_KIND[d.kind] || d.kind.toUpperCase();
        html.push(`<div class="disk-head"><span class="disk-name">${escapeHtml(d.model || kind)}</span>
          <span class="drive-fstype">${escapeHtml(kind)}</span><span class="disk-meta">${escapeHtml(d.name)} · ${diskSize(d.size)}</span></div>`);
      }
      if (!d.partitions.length) html.push('<div class="disk-empty">Keine Partitionen</div>');
      d.partitions.forEach(p => {
        const pct = p.percent ?? 0;
        const badge = pct > 85 ? 'danger' : pct > 70 ? 'warn' : '';
        const extra = p.extra_mounts
          ? `<span class="drive-fstype extra-mounts" title="${escapeHtml(p.extra_examples.join('\n'))}">+${p.extra_mounts} ${p.extra_kind === 'kubernetes' ? 'Kubernetes' : p.extra_kind === 'docker' ? 'Docker' : 'weitere'}</span>` : '';
        html.push(`<div class="drive-item${p.mount ? '' : ' unmounted'}">
          <div class="drive-header">
            <div class="drive-title-wrap">
              <span class="drive-label" title="${escapeHtml(p.path)}">${escapeHtml(partLabel(p))}</span>
              ${p.fstype ? `<span class="drive-fstype">${escapeHtml(p.fstype)}</span>` : ''}
              ${extra}
              ${p.readonly ? '<span class="drive-fstype">nur lesen</span>' : ''}
            </div>
            <div>
              ${p.mount ? `<button class="drive-open" data-mount="${escapeHtml(p.mount)}" title="Dateien durchsuchen & Speicher analysieren">Öffnen</button>
              <span class="drive-percent-badge ${badge}">${pct}%</span>` : '<span class="drive-fstype">nicht eingehängt</span>'}
            </div>
          </div>
          ${p.mount && p.total ? usageBlock(pct, p.used, p.free, p.total)
            : `<div class="drive-legend"><span>${escapeHtml(p.name)} · ${fmtSize(p.size)}${p.label ? ` · ${escapeHtml(p.label)}` : ''}</span></div>`}
        </div>`);
      });
    });
    if (o.swap.length) {
      html.push('<div class="disk-head"><span class="disk-name">Swap</span></div>');
      o.swap.forEach(sw => {
        const kind = sw.kind === 'zram' ? 'zram (komprimierter RAM)' : sw.kind === 'file' ? 'Datei' : 'Partition';
        html.push(`<div class="drive-item">
          <div class="drive-header">
            <div class="drive-title-wrap"><span class="drive-label">${escapeHtml(sw.name)}</span><span class="drive-fstype">${escapeHtml(kind)}</span></div>
            <div><span class="drive-percent-badge">${sw.percent ?? 0}%</span></div>
          </div>
          ${usageBlock(sw.percent ?? 0, sw.used, null, sw.total)}
        </div>`);
      });
    }
    if (o.other.length) {
      html.push(`<div class="disk-other">Weitere: ${o.other.map(x => `${escapeHtml(x.name)} (${x.type}, ${fmtSize(x.size)}${x.mount ? `, ${escapeHtml(x.mount)}` : ''})`).join(' · ')}</div>`);
    }
    box.innerHTML = html.join('') || '<div class="disk-empty">Keine Laufwerke gefunden.</div>';
  }

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
          <div>
            <button class="drive-open" data-mount="${escapeHtml(d.mount)}" title="Dateien durchsuchen & Speicher analysieren">Öffnen</button>
            <span class="drive-percent-badge ${badgeClass}">${d.percent}%</span>
          </div>
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

  // ---- File explorer + storage analysis ----
  const FILE_ICONS = {
    dir: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path>',
    other: '<circle cx="12" cy="12" r="9"></circle>'
  };
  const filesState = { path: null, data: null, sizes: null, mode: 'list' };

  function fmtSize(b) {
    if (b == null) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
    return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)} ${units[i]}`;
  }

  function fmtDate(ts) {
    return ts ? new Date(ts * 1000).toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit', year: '2-digit' }) : '';
  }

  function fileIcon(kind) {
    const span = document.createElement('span');
    span.className = 'icon';
    span.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${FILE_ICONS[kind] || FILE_ICONS.other}</svg>`;
    return span;
  }

  function fileRow(e, maxSize) {
    const row = document.createElement('div');
    row.className = `file-row ${e.type}${e.locked ? ' locked' : ''}`;
    row.appendChild(fileIcon(e.locked ? 'lock' : e.type === 'dir' ? 'dir' : e.type === 'file' ? 'file' : 'other'));

    let name;
    if (e.locked) {
      name = document.createElement('span');
      name.className = 'file-name';
      name.textContent = e.name;
      name.title = 'Aus Sicherheitsgründen gesperrt';
    } else if (e.type === 'dir') {
      name = document.createElement('button');
      name.className = 'file-name';
      name.textContent = e.name + '/';
      name.addEventListener('click', () => openFilesPath(e.path));
    } else if (e.type === 'file') {
      name = document.createElement('button');
      name.className = 'file-name';
      name.textContent = e.name;
      name.title = `${e.path}: Vorschau`;
      name.addEventListener('click', () => openPreview(e.path));
    } else {
      name = document.createElement('span');
      name.className = 'file-name';
      name.textContent = e.name;
    }
    const nameCell = document.createElement('div');
    nameCell.style.minWidth = '0';
    nameCell.appendChild(name);
    if (e.sub) {
      const sub = document.createElement('button');
      sub.className = 'file-name file-sub';
      sub.textContent = e.sub;
      sub.title = 'Ordner öffnen';
      sub.addEventListener('click', () => openFilesPath(e.sub));
      nameCell.appendChild(sub);
    }
    row.appendChild(nameCell);

    const barCell = document.createElement('div');
    barCell.className = 'file-bar-cell';
    const size = e.type === 'dir' ? e.dirSize : e.size;
    if (maxSize && size != null) {
      const bar = document.createElement('div');
      bar.className = 'file-bar';
      const fill = document.createElement('span');
      fill.style.width = `${Math.max(1, size / maxSize * 100)}%`;
      bar.appendChild(fill);
      barCell.appendChild(bar);
    }
    row.appendChild(barCell);

    const sz = document.createElement('span');
    sz.className = 'file-size';
    sz.textContent = fmtSize(size);
    const dt = document.createElement('span');
    dt.className = 'file-date';
    dt.textContent = fmtDate(e.mtime);
    row.append(sz, dt);
    return row;
  }

  function renderBreadcrumb(path, root) {
    const nav = $('files-breadcrumb');
    nav.innerHTML = '';
    const rel = path.slice(root.length).split('/').filter(Boolean);
    const crumbs = [[root, root]];
    let acc = root.replace(/\/$/, '');
    rel.forEach(part => { acc += '/' + part; crumbs.push([part, acc]); });
    crumbs.forEach(([label, target], i) => {
      // No extra separator right after a root that already ends in "/"
      if (i && !(i === 1 && root.endsWith('/'))) { const sep = document.createElement('span'); sep.textContent = '/'; nav.appendChild(sep); }
      const b = document.createElement('button');
      b.textContent = label;
      b.addEventListener('click', () => openFilesPath(target));
      nav.appendChild(b);
    });
  }

  function renderFiles() {
    const list = $('files-list'), d = filesState.data;
    list.innerHTML = '';
    if (filesState.mode === 'largest') {
      const files = filesState.largest.files;
      if (!files.length) list.innerHTML = '<div class="files-empty">Keine lesbaren Dateien gefunden.</div>';
      const max = files.length ? files[0].size : 0;
      files.forEach(f => list.appendChild(fileRow({ ...f, type: 'file', sub: f.dir }, max)));
      return;
    }
    let entries = d.entries.map(e => ({ ...e, dirSize: filesState.sizes ? filesState.sizes.sizes[e.name] : null }));
    let max = 0;
    if (filesState.sizes) {
      entries.sort((a, b) => ((b.type === 'dir' ? b.dirSize : b.size) || 0) - ((a.type === 'dir' ? a.dirSize : a.size) || 0));
      max = Math.max(0, ...entries.map(e => (e.type === 'dir' ? e.dirSize : e.size) || 0));
    }
    if (!entries.length) list.innerHTML = '<div class="files-empty">Ordner ist leer.</div>';
    entries.forEach(e => list.appendChild(fileRow(e, max)));
  }

  async function filesApi(endpoint, path) {
    const res = await fetch(`/api/files/${endpoint}?path=${encodeURIComponent(path)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Fehler beim Laden.');
    return data;
  }

  async function openFilesPath(path) {
    setFilesMsg('Lade…');
    try {
      const data = await filesApi('list', path);
      Object.assign(filesState, { path: data.path, data, sizes: null, mode: 'list' });
      renderBreadcrumb(data.path, data.root);
      $('files-up').disabled = !data.parent;
      renderFiles();
      setFilesMsg(data.truncated ? 'Nur die ersten 2000 Einträge werden angezeigt.' : '');
      $('files-list').scrollTop = 0;
    } catch (err) {
      setFilesMsg(err.message, 'error');
    }
  }

  function setFilesMsg(text, type) {
    const el = $('files-msg');
    el.textContent = text;
    el.className = `form-msg ${type || ''}`;
  }

  function openFilesModal(mount) {
    const modal = $('files-modal');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    $('files-modal-close').focus();
    openFilesPath(mount);
  }

  function closeFilesModal() {
    const modal = $('files-modal');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  // ---- File preview + share links ----
  const previewState = { path: null, text: '' };

  function previewHighlight() {
    const pre = document.querySelector('#preview-body pre');
    if (!pre) return;
    const q = $('preview-search').value;
    const text = previewState.text;
    pre.textContent = '';
    if (!q) { pre.textContent = text; $('preview-hits').textContent = ''; return; }
    const lower = text.toLowerCase(), needle = q.toLowerCase();
    let pos = 0, idx, hits = 0, first = null;
    while ((idx = lower.indexOf(needle, pos)) !== -1 && hits < 2000) {
      pre.appendChild(document.createTextNode(text.slice(pos, idx)));
      const m = document.createElement('mark');
      m.textContent = text.slice(idx, idx + q.length);
      pre.appendChild(m);
      if (!first) first = m;
      pos = idx + q.length;
      hits++;
    }
    pre.appendChild(document.createTextNode(text.slice(pos)));
    $('preview-hits').textContent = hits ? `${hits} Treffer` : 'Keine Treffer';
    if (first) first.scrollIntoView({ block: 'center' });
  }

  async function openPreview(path) {
    previewState.path = path;
    const modal = $('preview-modal');
    const body = $('preview-body');
    body.innerHTML = '<p class="field-hint">Lade…</p>';
    $('preview-title').textContent = path.split('/').pop();
    $('preview-sub').textContent = path;
    $('preview-download').href = `/api/files/download?path=${encodeURIComponent(path)}`;
    $('share-panel').hidden = true;
    $('share-result').hidden = true;
    $('preview-search-wrap').hidden = true;
    $('preview-search').value = '';
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    $('preview-close').focus();
    try {
      const p = await filesApi('preview', path);
      $('preview-sub').textContent = `${fmtSize(p.size)} · ${fmtDate(p.mtime)} · ${p.path}`;
      body.innerHTML = '';
      if (p.kind === 'image') {
        const img = document.createElement('img');
        img.src = `/api/files/raw?path=${encodeURIComponent(p.path)}`;
        img.alt = p.name;
        img.className = 'preview-image';
        body.appendChild(img);
      } else if (p.kind === 'text') {
        previewState.text = p.content;
        const pre = document.createElement('pre');
        pre.className = 'preview-text';
        pre.setAttribute('data-no-i18n', '');
        pre.textContent = p.content;
        body.appendChild(pre);
        $('preview-search-wrap').hidden = false;
        if (p.truncated) {
          const note = document.createElement('p');
          note.className = 'field-hint';
          note.textContent = 'Nur das erste MB wird angezeigt – für die ganze Datei herunterladen.';
          body.appendChild(note);
        }
      } else {
        body.innerHTML = '<p class="files-empty">Keine Vorschau für diese Datei (Binärdatei) – bitte herunterladen.</p>';
      }
    } catch (err) {
      body.innerHTML = '';
      const msg = document.createElement('p');
      msg.className = 'files-empty';
      msg.textContent = err.message;
      body.appendChild(msg);
    }
  }

  function closePreview() {
    const modal = $('preview-modal');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  async function createShare() {
    const hours = +(document.querySelector('#share-hours .active')?.dataset.hours || 24);
    const btn = $('share-create');
    btn.disabled = true;
    try {
      const res = await fetch('/api/files/share', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: previewState.path, hours })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
      $('share-url').value = `${location.origin}/s/${data.token}`;
      $('share-result').hidden = false;
      $('share-url').select();
      showToast(`Link gültig bis ${new Date(data.expires * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`);
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  // ---- Upload (streamed PUT per file, progress via XHR) ----
  function uploadOne(file, dir, overwrite, row) {
    return new Promise(resolve => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', `/api/files/upload?dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(file.name)}&overwrite=${overwrite ? 1 : 0}`);
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) row.querySelector('.upload-fill').style.width = `${e.loaded / e.total * 100}%`;
      };
      xhr.onload = () => {
        let detail = '';
        try { detail = JSON.parse(xhr.responseText).detail || ''; } catch (e) { /* ignore */ }
        resolve({ status: xhr.status, detail });
      };
      xhr.onerror = () => resolve({ status: 0, detail: 'Verbindung abgebrochen.' });
      xhr.send(file);
    });
  }

  async function uploadFiles(fileList) {
    const dir = filesState.path;
    if (!dir || !fileList.length) return;
    const box = $('files-uploads');
    box.hidden = false;
    for (const file of fileList) {
      const row = document.createElement('div');
      row.className = 'upload-row';
      row.innerHTML = '<span class="upload-name"></span><span class="upload-bar"><span class="upload-fill"></span></span><span class="upload-state">…</span>';
      row.querySelector('.upload-name').textContent = file.name;
      box.appendChild(row);
      let r = await uploadOne(file, dir, false, row);
      if (r.status === 409 && window.confirm(`„${file.name}“ gibt es dort schon. Überschreiben?`)) {
        r = await uploadOne(file, dir, true, row);
      }
      const ok = r.status === 200;
      const state = row.querySelector('.upload-state');
      state.textContent = ok ? `✓ ${fmtSize(file.size)}` : (r.status === 409 ? 'übersprungen' : r.detail || `Fehler ${r.status}`);
      row.classList.add(ok ? 'done' : 'failed');
    }
    openFilesPath(dir);
    setTimeout(() => { box.querySelectorAll('.upload-row.done').forEach(r => r.remove()); if (!box.children.length) box.hidden = true; }, 6000);
  }

  function initFilesModal() {
    // Drive list is rebuilt on every live update: delegate the "Öffnen" clicks
    els.drivesListContainer.addEventListener('click', e => {
      const btn = e.target.closest('.drive-open');
      if (btn) openFilesModal(btn.dataset.mount);
    });
    $('files-modal-close').addEventListener('click', closeFilesModal);
    $('files-modal').addEventListener('click', e => { if (e.target.id === 'files-modal') closeFilesModal(); });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      if ($('preview-modal').classList.contains('open')) closePreview();
      else if ($('files-modal').classList.contains('open')) closeFilesModal();
    });
    $('files-up').addEventListener('click', () => {
      if (filesState.data && filesState.data.parent) openFilesPath(filesState.data.parent);
    });
    // Upload: button or drag & drop onto the list
    $('files-upload-btn').addEventListener('click', () => $('files-upload-input').click());
    $('files-upload-input').addEventListener('change', e => { uploadFiles([...e.target.files]); e.target.value = ''; });
    const sheet = document.querySelector('#files-modal .files-sheet');
    let dragDepth = 0;
    sheet.addEventListener('dragenter', e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); dragDepth++; $('files-drop').hidden = false; } });
    sheet.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
    sheet.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('files-drop').hidden = true; });
    sheet.addEventListener('drop', e => {
      e.preventDefault();
      dragDepth = 0;
      $('files-drop').hidden = true;
      if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]);
    });
    // Preview + share
    $('preview-close').addEventListener('click', closePreview);
    $('preview-modal').addEventListener('click', e => { if (e.target.id === 'preview-modal') closePreview(); });
    $('preview-share-btn').addEventListener('click', () => { $('share-panel').hidden = !$('share-panel').hidden; });
    document.querySelectorAll('#share-hours button').forEach(b => b.addEventListener('click', () => {
      document.querySelectorAll('#share-hours button').forEach(x => x.classList.toggle('active', x === b));
    }));
    $('share-create').addEventListener('click', createShare);
    $('share-copy').addEventListener('click', async () => {
      try { await navigator.clipboard.writeText($('share-url').value); showToast('Link kopiert.'); }
      catch (e) { $('share-url').select(); document.execCommand('copy'); showToast('Link kopiert.'); }
    });
    let searchTimer = null;
    $('preview-search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(previewHighlight, 200); });
    $('files-sizes').addEventListener('click', async () => {
      const btn = $('files-sizes');
      btn.disabled = true;
      setFilesMsg('Berechne Ordnergrößen… (kann bei großen Laufwerken dauern)');
      try {
        filesState.sizes = await filesApi('sizes', filesState.path);
        filesState.mode = 'list';
        renderFiles();
        const total = filesState.sizes.total != null ? `Gesamt ${fmtSize(filesState.sizes.total)}` : '';
        setFilesMsg(`${total}${filesState.sizes.unreadable ? ` · ${filesState.sizes.unreadable} Ordner nicht lesbar` : ''}`, 'success');
      } catch (err) {
        setFilesMsg(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });
    $('files-largest').addEventListener('click', async () => {
      const btn = $('files-largest');
      btn.disabled = true;
      setFilesMsg('Suche größte Dateien…');
      try {
        filesState.largest = await filesApi('largest', filesState.path);
        filesState.mode = 'largest';
        renderFiles();
        setFilesMsg(`${filesState.largest.scanned.toLocaleString(LOCALE)} Dateien durchsucht${filesState.largest.timed_out ? ' (nach 60 s abgebrochen)' : ''}`, 'success');
      } catch (err) {
        setFilesMsg(err.message, 'error');
      } finally {
        btn.disabled = false;
      }
    });
  }

  async function loadDrives(refresh = false) {
    try {
      const res = await fetch(`/api/drives${refresh ? '?refresh=1' : ''}`);
      if (res.ok) {
        const data = await res.json();
        if (data.storage) renderStorage(data.storage); else renderDrives(data.drives);
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
  // While a search is active the live WebSocket list is ignored and search results refresh instead
  const procState = { query: '', timer: null };

  function renderProcesses(procs, fromSearch = false) {
    if (!els.processesTbody) return;
    if (procState.query && !fromSearch) return;
    // Don't wipe a pending "Sicher?" / "Erzwingen" confirmation with the next live update
    if (!fromSearch && els.processesTbody.querySelector('.proc-kill.confirming, .proc-kill[data-force="1"]')) return;
    els.processesTbody.innerHTML = '';

    if (!procs || procs.length === 0) {
      els.processesTbody.innerHTML = `<tr><td colspan="6" class="table-loading">${procState.query ? 'Kein passender Prozess' : 'Keine Prozesse gefunden'}</td></tr>`;
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
        <td></td>
      `;
      tr.lastElementChild.appendChild(processAction(p));
      els.processesTbody.appendChild(tr);
    });
  }

  function processAction(p) {
    if (!p.killable) {
      const lock = document.createElement('span');
      lock.className = 'proc-locked';
      lock.textContent = '–';
      lock.title = 'Gehört einem anderen Benutzer oder ist geschützt – nur über das Web-Terminal mit sudo beendbar.';
      return lock;
    }
    const btn = document.createElement('button');
    btn.className = 'proc-kill';
    btn.textContent = 'Beenden';
    btn.title = `${p.name} (${p.pid}) beenden`;
    btn.addEventListener('click', async () => {
      const force = btn.dataset.force === '1';
      if (!btn.classList.contains('confirming')) {
        btn.classList.add('confirming');
        btn.textContent = force ? 'Erzwingen?' : 'Sicher?';
        setTimeout(() => { btn.classList.remove('confirming'); btn.textContent = force ? 'Erzwingen' : 'Beenden'; }, 4000);
        return;
      }
      btn.classList.remove('confirming');
      btn.disabled = true;
      btn.textContent = '…';
      try {
        const res = await fetch(`/api/processes/${p.pid}/signal`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ create_time: p.create_time, force })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Beenden fehlgeschlagen.');
        showToast(data.message, data.gone ? 'success' : 'info');
        if (!data.gone) {
          // Ignored SIGTERM: offer SIGKILL on the same button
          btn.dataset.force = '1';
          btn.textContent = 'Erzwingen';
          btn.disabled = false;
          return;
        }
        if (procState.query) searchProcesses();
      } catch (err) {
        showToast(err.message, 'error');
        btn.disabled = false;
        btn.textContent = force ? 'Erzwingen' : 'Beenden';
      }
    });
    return btn;
  }

  async function searchProcesses() {
    clearTimeout(procState.timer);
    if (!procState.query) return;
    try {
      const res = await fetch(`/api/processes/search?q=${encodeURIComponent(procState.query)}`);
      if (res.ok) renderProcesses((await res.json()).processes, true);
    } catch (e) { /* keep last result */ }
    // Don't rebuild while the user is confirming a kill
    procState.timer = setTimeout(function refresh() {
      if (document.querySelector('#processes-tbody .proc-kill.confirming, #processes-tbody .proc-kill[data-force="1"]')) {
        procState.timer = setTimeout(refresh, 3000);
      } else {
        searchProcesses();
      }
    }, 3000);
  }

  function initProcessSearch() {
    const input = document.getElementById('proc-search');
    if (!input) return;
    let debounce = null;
    input.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        procState.query = input.value.trim();
        document.getElementById('proc-mode').textContent = procState.query ? 'Suchergebnis' : 'Top 6 nach CPU';
        if (procState.query) searchProcesses(); else clearTimeout(procState.timer);
      }, 250);
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
  function renderPower(pw) {
    const card = document.getElementById('power-card');
    if (!card) return;
    const set = (id, text) => { document.getElementById(id).textContent = text; };
    card.classList.toggle('unavailable', !pw.available);
    const tag = document.getElementById('power-tag');

    if (!pw.available) {
      set('power-watt', '--');
      set('power-subtitle', 'Nur auf Raspberry Pi 5');
      tag.className = 'health-tag';
      tag.textContent = 'Nicht verfügbar';
      return;
    }

    set('power-watt', `${pw.board_watt.toFixed(1)} W`);
    set('power-subtitle', 'Board, ohne USB-Geräte');
    set('power-volt', pw.input_volt != null ? `${pw.input_volt.toFixed(2)} V` : '--');
    set('power-psu', pw.psu_max_ma != null ? `${(pw.psu_max_ma / 1000).toFixed(1)} A` : '--');
    document.getElementById('power-psu').title = pw.psu_ok ? '' : 'Unter 5 A: der Pi begrenzt USB-Geräte auf 600 mA';
    const core = (pw.top_rails || []).find(r => r.name === 'VDD_CORE');
    set('power-core', core ? `${core.watt.toFixed(2)} W` : '--');

    // Status: live undervoltage > undervoltage since boot > weak PSU > ok
    let level = 'good', text = 'Spannung OK';
    if (pw.undervoltage_now) { level = 'critical'; text = 'Unterspannung!'; }
    else if (pw.undervoltage_since_boot) { level = 'warning'; text = 'Unterspannung seit Boot'; }
    else if (!pw.psu_ok) { level = 'warning'; text = 'Netzteil < 5 A'; }
    tag.className = `health-tag ${level}`;
    tag.textContent = text;

    const h = state.history;
    h.power.push(pw.board_watt);
    if (h.power.length > h.maxPoints) h.power.shift();
    drawSparkline(document.getElementById('power-chart'), h.power, '#ffb703', 'rgba(255, 183, 3, 0.2)',
      { max: Math.max(2, ...h.power) * 1.2 });
  }

  // Fan colors go from calm cyan to hot rose as the level rises
  const FAN_COLORS = ['#00f0ff', '#00ffa3', '#ffb703', '#ff2a5f'];

  function renderFan(fan) {
    if (!els.fanCard) return;
    els.fanCard.classList.toggle('unavailable', !fan.available);

    if (!fan.available) {
      els.fanLevelVal.textContent = '--';
      els.fanSubtitle.textContent = 'Kein Lüfter erkannt';
      els.fanRotor.classList.remove('spinning');
      els.fanLevelsLabel.textContent = 'Stufe – / –';
      return;
    }

    const { level, max_level: max } = fan;
    els.fanLevelVal.textContent = fan.level_name;
    els.fanSubtitle.textContent = fan.device === 'pwm-fan' ? 'Active Cooler (PWM)' : fan.device;
    els.fanLevelsLabel.textContent = `Stufe ${level} / ${max}`;

    // Higher level -> faster spin (full level ~0.3s per turn)
    els.fanRotor.classList.toggle('spinning', level > 0);
    if (level > 0) els.fanRotor.style.setProperty('--fan-speed', `${(1.8 - 1.5 * (level / max)).toFixed(2)}s`);

    if (els.fanLevels.childElementCount !== max) {
      els.fanLevels.innerHTML = Array.from({ length: max }, (_, i) =>
        `<div class="fan-level-bar" style="height:${30 + 70 * (i + 1) / max}%"></div>`).join('');
    }
    [...els.fanLevels.children].forEach((bar, i) => {
      bar.classList.toggle('on', i < level);
      bar.style.setProperty('--bar-color', FAN_COLORS[Math.min(FAN_COLORS.length - 1, Math.floor(i * FAN_COLORS.length / max))]);
    });

    els.fanRpm.textContent = fan.rpm != null ? `${fan.rpm} RPM` : '--';
    els.fanPwm.textContent = fan.pwm_percent != null ? `${fan.pwm_percent}%` : '--';
    els.fanThresholds.textContent = fan.thresholds && fan.thresholds.length
      ? `${fan.thresholds.map(t => Math.round(t)).join('/')}°C`
      : '--';
  }

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
      if (els.bootTimeVal) {
        // "2026-09-22 16:44:56" -> local short form, full value on hover
        const boot = new Date(String(sys.uptime.boot_time).replace(' ', 'T'));
        els.bootTimeVal.textContent = isNaN(boot) ? sys.uptime.boot_time
          : boot.toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        els.bootTimeVal.title = sys.uptime.boot_time;
      }
    }

    // CPU Metrics
    if (sys.cpu) {
      if (els.cpuTotalPercent) els.cpuTotalPercent.textContent = `${sys.cpu.percent}%`;
      if (els.cpuCoreCount) els.cpuCoreCount.textContent = sys.cpu.count;
      if (els.cpuFreqVal) els.cpuFreqVal.textContent = sys.cpu.frequency_mhz;

      state.history.cpu.push(sys.cpu.percent);
      if (state.history.cpu.length > state.history.maxPoints) state.history.cpu.shift();
      const accent = getComputedStyle(document.documentElement);
      drawSparkline(els.cpuChart, state.history.cpu, accent.getPropertyValue('--accent-cyan').trim() || '#00f0ff',
        `rgba(${accent.getPropertyValue('--accent-rgb').trim() || '0, 240, 255'}, 0.25)`);

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

    // Fan
    if (sys.fan) renderFan(sys.fan);

    // Power supply (Pi 5)
    if (sys.power) renderPower(sys.power);

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

    // Disk activity per physical drive
    const io = $('disk-io');
    if (io && Array.isArray(sys.disk_io)) {
      io.hidden = !sys.disk_io.length;
      io.innerHTML = '';
      sys.disk_io.forEach(d => {
        const row = document.createElement('div');
        row.className = 'disk-io-row';
        const name = document.createElement('span');
        name.className = 'disk-io-name';
        name.textContent = d.name;
        const r = document.createElement('span');
        r.className = 'disk-io-read';
        r.textContent = `↓ ${fmtRate(d.read_kbs)}`;
        r.title = 'Lesen';
        const w = document.createElement('span');
        w.className = 'disk-io-write';
        w.textContent = `↑ ${fmtRate(d.write_kbs)}`;
        w.title = 'Schreiben';
        row.append(name, r, w);
        io.appendChild(row);
      });
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
          if (msg.storage) renderStorage(msg.storage);
          else if (msg.drives) renderDrives(msg.drives);
          if (msg.updater) renderUpdater(msg.updater);
        }
      } catch (err) {
        console.error('Error parsing live update:', err);
      }
    };

    state.ws.onclose = function (evt) {
      if (evt && evt.code === 4401) { location.href = '/login'; return; }
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

    if (els.btnThemeToggle && window.PiTheme) {
      const THEME_LABEL = { auto: 'Design: Automatisch (wie das Gerät)', light: 'Design: Hell', dark: 'Design: Dunkel' };
      const syncThemeTitle = () => { els.btnThemeToggle.title = THEME_LABEL[window.PiTheme.choice()]; };
      syncThemeTitle();
      els.btnThemeToggle.addEventListener('click', () => {
        showToast(THEME_LABEL[window.PiTheme.cycle()]);
        syncThemeTitle();
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
        loadDrives(true);
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
  // Homelab Extras: Updates, NVMe health, Docker & Kubernetes
  // ---------------------------------------------------------------------------
  const $ = id => document.getElementById(id);

  function setHealthTag(el, level, text) {
    if (!el) return;
    el.className = `health-tag ${level}`;
    el.textContent = text;
  }

  function relTime(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 3600) return `vor ${Math.max(1, Math.round(s / 60))} Min.`;
    if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
    return `vor ${Math.round(s / 86400)} Tagen`;
  }

  const SHIELD_ICON = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>';

  let lastUpdates = null;

  function renderUpdates(up) {
    const count = $('updates-count'), tags = $('updates-tags'), list = $('updates-list'), footer = $('updates-footer');
    if (!count) return;
    lastUpdates = up;
    tags.innerHTML = '';
    list.innerHTML = '';
    if (aptRun.running) {
      const t = document.createElement('span');
      setHealthTag(t, 'warning', aptRun.mode === 'upgrade' ? 'Upgrade läuft…' : 'Update läuft…');
      tags.appendChild(t);
    }

    const addTag = (level, text) => {
      const t = document.createElement('span');
      setHealthTag(t, level, text);
      tags.appendChild(t);
    };
    const addEmpty = text => {
      const li = document.createElement('li');
      li.className = 'empty';
      li.textContent = text;
      list.appendChild(li);
    };

    if (!up || !up.supported) {
      count.textContent = '--';
      addEmpty('Nur auf Debian / Raspberry Pi OS verfügbar.');
      footer.textContent = '';
      return;
    }
    if (up.error) {
      count.textContent = '--';
      addEmpty(`Fehler: ${up.error}`);
      return;
    }

    count.textContent = String(up.count);
    $('updates-subtitle').textContent = up.lists_updated ? `Paketlisten ${relTime(up.lists_updated)}` : 'apt-Pakete';
    if (up.count === 0) addTag('good', 'Aktuell');
    if (up.security_count) addTag('warning', `${up.security_count} Sicherheit`);
    if (up.count && up.count > up.security_count) addTag('', `${up.count - up.security_count} weitere`);
    if (up.reboot_required) addTag('critical', 'Neustart nötig');

    const shown = 6;
    up.packages.slice(0, shown).forEach(pkg => {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'pkg';
      if (pkg.security) {
        const icon = document.createElement('span');
        icon.className = 'sec-icon';
        icon.title = 'Sicherheitsupdate';
        icon.innerHTML = SHIELD_ICON;
        name.appendChild(icon);
      }
      name.appendChild(document.createTextNode(pkg.name));
      const ver = document.createElement('span');
      ver.className = 'ver';
      ver.textContent = pkg.new;
      ver.title = `${pkg.old} → ${pkg.new}`;
      li.append(name, ver);
      list.appendChild(li);
    });
    if (up.count > shown) addEmpty(`+ ${up.count - shown} weitere Pakete`);
    if (up.count === 0) addEmpty('Alle Pakete sind auf dem neuesten Stand.');

    footer.textContent = 'Klicken für alle Pakete & Installation';
    if ($('updates-modal').classList.contains('open')) renderUpdatesSheet();
  }

  // ---- System-Updates sheet: full list + apt update / upgrade with live log ----
  const aptRun = { running: false, mode: null, state: 'idle', pollTimer: null, confirmTimer: null };

  function renderUpdatesSheet() {
    const up = lastUpdates;
    const table = $('updates-modal-table'), tags = $('updates-modal-tags');
    table.innerHTML = '';
    tags.innerHTML = '';
    // Reuse the card's tags so both always agree
    [...$('updates-tags').children].forEach(t => tags.appendChild(t.cloneNode(true)));

    $('updates-modal-sub').textContent = up && up.supported && !up.error
      ? `${up.count} Paket${up.count === 1 ? '' : 'e'} aktualisierbar${up.lists_updated ? ` · Paketlisten ${relTime(up.lists_updated)}` : ''}`
      : 'apt-Pakete';

    const head = table.createTHead().insertRow();
    ['Paket', 'Installiert', 'Neu'].forEach(h => {
      const th = document.createElement('th');
      th.textContent = h;
      head.appendChild(th);
    });
    const body = table.createTBody();
    const pkgs = (up && up.packages) || [];
    if (!pkgs.length) {
      const row = body.insertRow();
      row.className = 'empty-row';
      const td = row.insertCell();
      td.colSpan = 3;
      td.textContent = !up || !up.supported ? 'Nur auf Debian / Raspberry Pi OS verfügbar.' : 'Alle Pakete sind auf dem neuesten Stand.';
    }
    pkgs.forEach(p => {
      const row = body.insertRow();
      const name = row.insertCell();
      name.className = 'name';
      if (p.security) {
        const icon = document.createElement('span');
        icon.className = 'sec-icon';
        icon.title = 'Sicherheitsupdate';
        icon.innerHTML = SHIELD_ICON;
        name.appendChild(icon);
      }
      name.appendChild(document.createTextNode(p.name));
      const oldV = row.insertCell(); oldV.className = 'ver'; oldV.textContent = p.old;
      const newV = row.insertCell(); newV.className = 'ver'; newV.textContent = p.new;
    });

    const upgradeBtn = $('btn-apt-upgrade');
    const supported = up && up.supported;
    $('btn-apt-update').disabled = aptRun.running || !supported;
    upgradeBtn.disabled = aptRun.running || !supported;
    if (!upgradeBtn.classList.contains('confirming')) {
      upgradeBtn.textContent = up && up.count ? `Update & Upgrade installieren (${up.count})` : 'Update & Upgrade installieren';
    }
  }

  function renderAptRun(run) {
    const wrap = $('apt-log-wrap'), log = $('apt-log');
    if (!run || !run.supported || (!run.log && !run.running)) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    $('apt-log-title').textContent = run.mode === 'update' ? 'Ausgabe: apt update' : 'Ausgabe: apt update & full-upgrade';
    const labels = { running: ['warning', 'Läuft…'], done: ['good', 'Fertig'], failed: ['critical', `Fehler (Code ${run.exit_code})`] };
    const [level, text] = labels[run.state] || ['', run.state];
    setHealthTag($('apt-run-tag'), level, text);

    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
    log.textContent = run.log || 'Starte…';
    if (atBottom || run.running) log.scrollTop = log.scrollHeight;
  }

  async function pollAptRun() {
    clearTimeout(aptRun.pollTimer);
    let run = null;
    try {
      const res = await fetch('/api/updates/apt');
      if (res.ok) run = await res.json();
    } catch (e) { /* keep polling */ }

    const wasRunning = aptRun.running;
    if (run) {
      Object.assign(aptRun, { running: run.running, mode: run.mode, state: run.state });
      renderAptRun(run);
    }
    if (wasRunning && run && !run.running) {
      showToast(run.state === 'done' ? 'apt ist fertig. Paketliste wird aktualisiert.' : 'apt ist mit einem Fehler beendet.',
        run.state === 'done' ? 'success' : 'error');
      loadHomelab();  // fresh package counts (server drops its cache after a run)
    }
    renderUpdatesSheet();
    if (lastUpdates) renderUpdates(lastUpdates);

    const sheetOpen = $('updates-modal').classList.contains('open');
    if (aptRun.running || (sheetOpen && !run)) {
      aptRun.pollTimer = setTimeout(pollAptRun, aptRun.running ? 1500 : 5000);
    }
  }

  async function startAptRun(mode) {
    try {
      const res = await fetch('/api/updates/apt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Start fehlgeschlagen.');
      showToast(data.message, 'info');
      Object.assign(aptRun, { running: true, mode, state: 'running' });
      renderAptRun({ supported: true, running: true, state: 'running', mode, log: '' });
      renderUpdatesSheet();
      setTimeout(pollAptRun, 800);
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  function resetUpgradeConfirm() {
    const btn = $('btn-apt-upgrade');
    clearTimeout(aptRun.confirmTimer);
    btn.classList.remove('confirming');
    renderUpdatesSheet();
  }

  function openUpdatesSheet() {
    const modal = $('updates-modal');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    renderUpdatesSheet();
    pollAptRun();
    $('updates-modal-close').focus();
  }

  function closeUpdatesSheet() {
    const modal = $('updates-modal');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    resetUpgradeConfirm();
    $('updates-card').focus();
  }

  function initUpdatesSheet() {
    const card = $('updates-card');
    if (!card) return;
    card.addEventListener('click', openUpdatesSheet);
    card.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openUpdatesSheet(); }
    });
    $('updates-modal-close').addEventListener('click', closeUpdatesSheet);
    $('updates-modal').addEventListener('click', e => { if (e.target.id === 'updates-modal') closeUpdatesSheet(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && $('updates-modal').classList.contains('open')) closeUpdatesSheet();
    });

    $('btn-apt-update').addEventListener('click', () => startAptRun('update'));

    // Installing is a real change to the system: require a second click within 5s
    $('btn-apt-upgrade').addEventListener('click', () => {
      const btn = $('btn-apt-upgrade');
      if (btn.classList.contains('confirming')) {
        resetUpgradeConfirm();
        startAptRun('upgrade');
        return;
      }
      btn.classList.add('confirming');
      btn.textContent = 'Wirklich installieren? Nochmal klicken';
      aptRun.confirmTimer = setTimeout(resetUpgradeConfirm, 5000);
    });

    pollAptRun();  // pick up a run that is already in progress
  }

  function renderNvme(nvme) {
    const box = $('nvme-health');
    if (!box) return;
    const present = nvme && nvme.present;
    box.hidden = !present;
    document.querySelectorAll('[data-nvme]').forEach(el => { el.hidden = !present; });
    $('arch-item').hidden = !!present;
    if (!present) return;

    $('nvme-temp').textContent = nvme.temp != null ? `${nvme.temp} °C` : '--';
    const hint = $('nvme-hint');
    if (!nvme.smart) {
      setHealthTag($('nvme-health-tag'), '', 'Keine SMART-Daten');
      hint.hidden = false;
      hint.textContent = nvme.hint || '';
      return;
    }
    hint.hidden = true;

    const life = Math.max(0, 100 - (nvme.percentage_used ?? 0));
    $('nvme-life-fill').style.width = `${life}%`;
    $('nvme-life-txt').textContent = `${life}%`;
    $('nvme-spare-txt').textContent = nvme.available_spare != null ? `${nvme.available_spare}%` : '--';
    $('nvme-written').textContent = nvme.tb_written >= 1 ? `${nvme.tb_written} TB` : `${Math.round(nvme.tb_written * 1000)} GB`;
    const h = nvme.power_on_hours;
    $('nvme-hours').textContent = h == null ? '--' : h >= 48 ? `${Math.round(h / 24)} Tage` : `${h} Std.`;

    const bad = nvme.passed === false || nvme.critical_warning || nvme.media_errors;
    const worn = life < 20;
    setHealthTag($('nvme-health-tag'), bad ? 'critical' : worn ? 'warning' : 'good',
      bad ? 'Fehler' : worn ? 'Verschleiß hoch' : 'Gesund');
    $('nvme-health-tag').title = nvme.model || '';
  }

  function workloadItem(level, name, sub, sideTop, sideBottom, actions) {
    const item = document.createElement('div');
    item.className = `workload-item ${level}${actions ? ' has-actions' : ''}`;
    const dot = document.createElement('span');
    dot.className = `service-dot ${level}`;
    const main = document.createElement('div');
    main.className = 'workload-main';
    const n = document.createElement('span');
    n.className = 'workload-name';
    n.textContent = name;
    n.title = name;
    const sb = document.createElement('span');
    sb.className = 'workload-sub';
    sb.textContent = sub;
    sb.title = sub;
    main.append(n, sb);
    const side = document.createElement('div');
    side.className = 'workload-side';
    [sideTop, sideBottom].filter(Boolean).forEach(t => {
      const span = document.createElement('span');
      span.textContent = t;
      side.appendChild(span);
    });
    item.append(dot, main, side);
    if (actions) item.appendChild(actions);
    return item;
  }

  // ---- Docker container actions (start / stop / restart) + logs ----
  const WL_ICONS = {
    logs: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"></path>',
    start: '<polygon points="6 3 20 12 6 21 6 3"></polygon>',
    stop: '<rect x="5" y="5" width="14" height="14" rx="2"></rect>',
    restart: '<path d="M23 4v6h-6"></path><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>'
  };
  const WL_LABELS = { logs: 'Log anzeigen', start: 'Starten', stop: 'Stoppen', restart: 'Neu starten' };

  function containerActions(c) {
    const box = document.createElement('div');
    box.className = 'workload-actions';
    const running = c.state === 'running';
    const list = running ? ['logs', 'restart', 'stop'] : ['logs', 'start'];
    list.forEach(action => {
      const btn = document.createElement('button');
      btn.className = 'wl-btn';
      btn.title = `${WL_LABELS[action]}: ${c.name}`;
      btn.setAttribute('aria-label', btn.title);
      btn.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${WL_ICONS[action]}</svg>`;
      btn.addEventListener('click', () => {
        if (action === 'logs') return openContainerLogs(c.name);
        // Stop / restart interrupt a running service: second click within 4s confirms
        if (action !== 'start' && !btn.classList.contains('confirming')) {
          btn.classList.add('confirming');
          btn.title = `${WL_LABELS[action]}? Nochmal klicken`;
          setTimeout(() => { btn.classList.remove('confirming'); btn.title = `${WL_LABELS[action]}: ${c.name}`; }, 4000);
          return;
        }
        runContainerAction(c.name, action, box);
      });
      box.appendChild(btn);
    });
    return box;
  }

  async function runContainerAction(name, action, box) {
    box.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
      const res = await fetch(`/api/workloads/docker/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Aktion fehlgeschlagen.');
      showToast(data.message, 'success');
      loadHomelab();
    } catch (err) {
      showToast(err.message, 'error');
      box.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  }

  let logsContainer = null;

  async function loadContainerLogs() {
    const body = $('logs-modal-body');
    body.textContent = 'Lade…';
    try {
      const res = await fetch(`/api/workloads/docker/${encodeURIComponent(logsContainer)}/logs?lines=300`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Log konnte nicht geladen werden.');
      body.textContent = data.log || '(keine Ausgabe)';
      body.scrollTop = body.scrollHeight;
    } catch (err) {
      body.textContent = err.message;
    }
  }

  function openContainerLogs(name) {
    logsContainer = name;
    $('logs-modal-title').textContent = `Log: ${name}`;
    const modal = $('logs-modal');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    $('logs-modal-close').focus();
    loadContainerLogs();
  }

  function closeContainerLogs() {
    const modal = $('logs-modal');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  // ---- Wake-on-LAN ----
  function renderWol(devices) {
    const list = $('wol-list');
    if (!list) return;
    $('wol-count').textContent = String(devices.length);
    list.innerHTML = '';
    if (!devices.length) {
      const p = document.createElement('p');
      p.className = 'wol-empty';
      p.textContent = 'Noch keine Geräte. Füge PC, NAS & Co. in den Einstellungen hinzu.';
      list.appendChild(p);
      return;
    }
    devices.forEach(d => {
      const item = document.createElement('div');
      item.className = 'wol-item';
      const main = document.createElement('div');
      main.className = 'workload-main';
      const n = document.createElement('span');
      n.className = 'workload-name';
      n.textContent = d.name;
      const m = document.createElement('span');
      m.className = 'workload-sub';
      m.textContent = d.mac;
      main.append(n, m);
      const btn = document.createElement('button');
      btn.className = 'btn-wake';
      btn.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path><path d="M12 2v10"></path></svg>';
      btn.appendChild(document.createTextNode('Wecken'));
      btn.title = `${d.name} per Wake-on-LAN starten`;
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const res = await fetch(`/api/wol/${encodeURIComponent(d.id)}/wake`, { method: 'POST' });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || 'Senden fehlgeschlagen.');
          showToast(data.message, 'success');
        } catch (err) {
          showToast(err.message, 'error');
        } finally {
          setTimeout(() => { btn.disabled = false; }, 1500);
        }
      });
      item.append(main, btn);
      list.appendChild(item);
    });
  }

  async function loadWol() {
    try {
      const res = await fetch('/api/wol');
      if (res.ok) renderWol((await res.json()).devices);
    } catch (e) { /* ignore */ }
  }

  function workloadMessage(list, source, notInstalled) {
    const box = document.createElement('div');
    if (!source.available) {
      box.className = 'workload-empty';
      box.textContent = notInstalled;
    } else {
      box.className = 'workload-hint';
      box.textContent = source.error || '';
      if (source.hint) {
        box.appendChild(document.createElement('br'));
        const code = document.createElement('code');
        code.textContent = source.hint;
        box.appendChild(code);
      }
    }
    list.appendChild(box);
  }

  function summarize(items) {
    const running = items.filter(i => i.level === 'active').length;
    const errors = items.filter(i => i.level === 'error').length;
    return `${running} / ${items.length} laufen${errors ? ` · ${errors} Fehler` : ''}`;
  }

  let lastWorkloads = null;

  function renderWorkloads(w) {
    if (w) lastWorkloads = w;
    w = lastWorkloads;
    const dList = $('docker-list'), kList = $('kube-list');
    if (!w || !dList) return;
    dList.innerHTML = '';
    kList.innerHTML = '';

    const docker = w.docker || {};
    if (docker.containers) {
      $('docker-summary').textContent = summarize(docker.containers);
      if (!docker.containers.length) workloadMessage(dList, { available: false }, 'Keine Container vorhanden.');
      docker.containers.forEach(c => dList.appendChild(
        workloadItem(c.level, c.name, c.image, c.status, c.ports.split(',')[0], containerActions(c))));
    } else {
      $('docker-summary').textContent = '';
      workloadMessage(dList, docker, 'Docker ist nicht installiert.');
    }

    const kube = w.kubernetes || {};
    if (kube.pods) {
      const hideSystem = $('workloads-hide-system').checked;
      const pods = kube.pods.filter(p => !hideSystem || !/^kube-|^cattle-|^longhorn-system$/.test(p.namespace) || p.level === 'error');
      $('kube-summary').textContent = summarize(kube.pods);
      if (!pods.length) workloadMessage(kList, { available: false }, 'Keine Pods (System-Pods ausgeblendet).');
      pods.forEach(p => kList.appendChild(workloadItem(
        p.level, p.name, `${p.namespace}${p.node ? ` · ${p.node}` : ''}`,
        `${p.status} · ${p.ready}`, `${p.restarts} Neustarts · ${p.age}`)));
    } else {
      $('kube-summary').textContent = '';
      workloadMessage(kList, kube, 'kubectl / k3s ist nicht installiert.');
    }
  }

  async function loadHomelab() {
    try {
      const res = await fetch('/api/homelab');
      if (!res.ok) return;
      const data = await res.json();
      renderUpdates(data.updates);
      renderNvme(data.nvme);
      renderWorkloads(data.workloads);
    } catch (e) {
      console.warn('Homelab fetch failed:', e);
    }
  }

  function initHomelab() {
    const toggle = $('workloads-hide-system');
    if (toggle) {
      try { toggle.checked = localStorage.getItem('hideSystemPods') !== '0'; } catch (e) { /* ignore */ }
      toggle.addEventListener('change', () => {
        try { localStorage.setItem('hideSystemPods', toggle.checked ? '1' : '0'); } catch (e) { /* ignore */ }
        renderWorkloads();
      });
    }
    initUpdatesSheet();
    initServiceSheet();
    initProcessSearch();
    initFilesModal();
    loadHomelab();
    loadWol();
    setInterval(loadHomelab, 20000);
    setInterval(loadWol, 60000);

    $('logs-modal-close').addEventListener('click', closeContainerLogs);
    $('logs-modal-refresh').addEventListener('click', loadContainerLogs);
    $('logs-modal').addEventListener('click', e => { if (e.target.id === 'logs-modal') closeContainerLogs(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && $('logs-modal').classList.contains('open')) closeContainerLogs();
    });
  }

  // ---------------------------------------------------------------------------
  // Metrics History Chart (24h / 7 Tage)
  // ---------------------------------------------------------------------------
  const HISTORY_METRICS = {
    cpu:  { title: 'CPU-Auslastung', unit: '%', max: 100, series: [{ key: 'cpu', label: 'CPU', color: 'var(--accent-cyan)', area: true }], peak: 'cpu_max' },
    temp: { title: 'CPU-Temperatur', unit: '°C', series: [{ key: 'temp', label: 'Temperatur', color: 'var(--accent-amber)', area: true }], peak: 'temp_max' },
    ram:  { title: 'Arbeitsspeicher', unit: '%', max: 100, series: [{ key: 'ram', label: 'RAM', color: 'var(--accent-violet)', area: true }] },
    net:  { title: 'Netzwerk', unit: 'rate', series: [
      { key: 'net_down', label: 'Download', color: 'var(--chart-down)' },
      { key: 'net_up', label: 'Upload', color: 'var(--chart-up)', dashed: true }
    ] },
    fan:  { title: 'Lüfterstufe', unit: 'level', max: 4, series: [{ key: 'fan', label: 'Lüfter', color: 'var(--accent-emerald)', area: true }], peak: 'fan_max' },
    power: { title: 'Board-Leistung', unit: 'W', series: [{ key: 'power', label: 'Leistung', color: 'var(--accent-amber)', area: true }], peak: 'power_max' },
    disk: { title: 'Laufwerk-Aktivität', unit: 'rate', series: [
      { key: 'disk_read', label: 'Lesen', color: 'var(--chart-down)' },
      { key: 'disk_write', label: 'Schreiben', color: 'var(--chart-up)', dashed: true }
    ] },
    nvme: { title: 'NVMe-Temperatur', unit: '°C', series: [{ key: 'nvme_temp', label: 'NVMe', color: 'var(--accent-violet)', area: true }], peak: 'nvme_temp_max' }
  };

  const historyState = { range: '24h', metric: 'cpu', iface: '', data: null, layout: null };
  const hEls = {};

  function fmtRate(kb) {
    if (kb == null) return '–';
    return kb >= 1024 ? `${(kb / 1024).toFixed(kb >= 10240 ? 0 : 1)} MB/s` : `${kb.toFixed(kb >= 100 ? 0 : 1)} KB/s`;
  }

  function fmtValue(metric, v) {
    if (v == null) return '–';
    const unit = HISTORY_METRICS[metric].unit;
    if (unit === 'rate') return fmtRate(v);
    if (unit === 'level') return v < 0.05 ? 'Aus' : `Stufe ${v.toFixed(1).replace('.0', '')}`;
    if (unit === 'W') return `${v.toFixed(2)} W`;
    return `${v.toFixed(1)}${unit === '%' ? '%' : ' °C'}`;
  }

  function fmtTime(ts, withDay) {
    const d = new Date(ts * 1000);
    const time = d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
    return withDay ? `${d.toLocaleDateString(LOCALE, { weekday: 'short', day: 'numeric', month: 'numeric' })} ${time}` : time;
  }

  // Round axis ticks: step is 1, 2 or 5 x 10^n, axis snaps to whole steps
  function niceTicks(lo, hi, count = 4) {
    const raw = Math.max(hi - lo, 1e-9) / count;
    const exp = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / exp;
    const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
    const min = Math.floor(lo / step) * step;
    const max = Math.ceil(hi / step) * step;
    const ticks = [];
    for (let t = min; t <= max + step / 2; t += step) ticks.push(+t.toFixed(6));
    return { min, max, ticks };
  }

  const svgEl = (tag, attrs) => {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };

  function renderHistoryStats(points) {
    const cfg = HISTORY_METRICS[historyState.metric];
    hEls.stats.innerHTML = '';
    const add = (label, value, s) => {
      const div = document.createElement('div');
      div.className = 'history-stat';
      const l = document.createElement('span');
      l.className = 'label';
      if (s) {
        const key = document.createElement('span');
        key.className = `line-key ${s.dashed ? 'dashed' : ''}`;
        key.style.setProperty('--key-color', s.color);
        l.appendChild(key);
      }
      l.appendChild(document.createTextNode(label));
      const v = document.createElement('span');
      v.className = 'value';
      v.textContent = value;
      div.append(l, v);
      hEls.stats.appendChild(div);
    };

    cfg.series.forEach(s => {
      const vals = points.map(p => p[s.key]).filter(v => v != null);
      if (!vals.length) return;
      const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
      const prefix = cfg.series.length > 1 ? `${s.label} ` : '';
      add(`${prefix}Ø`, fmtValue(historyState.metric, avg), cfg.series.length > 1 ? s : null);
      const peaks = cfg.peak ? points.map(p => p[cfg.peak]).filter(v => v != null) : vals;
      add(`${prefix}Spitze`, fmtValue(historyState.metric, Math.max(...peaks)));
      if (cfg.series.length === 1) add('Minimum', fmtValue(historyState.metric, Math.min(...vals)));
    });
  }

  function renderHistoryChart() {
    const data = historyState.data;
    if (!data) return;
    const cfg = HISTORY_METRICS[historyState.metric];
    const points = data.points;
    const svg = hEls.svg;
    svg.innerHTML = '';
    hEls.tooltip.hidden = true;

    const hasData = points.some(p => cfg.series.some(s => p[s.key] != null));
    hEls.empty.hidden = hasData;
    renderHistoryStats(hasData ? points : []);
    if (!hasData) { historyState.layout = null; return; }

    const W = hEls.chart.clientWidth, H = hEls.chart.clientHeight;
    const pad = { l: 58, r: cfg.series.length > 1 ? 74 : 12, t: 10, b: 24 };
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

    const now = Math.floor(Date.now() / 1000);
    const span = data.range === '7d' ? 7 * 86400 : 86400;
    const x0 = now - span;
    const x = ts => pad.l + (ts - x0) / span * iw;

    const vals = points.flatMap(p => cfg.series.map(s => p[s.key])).filter(v => v != null);
    const dataMax = Math.max(...vals);

    // Y scale. Network ticks are computed in the unit shown (KB/s or MB/s) so labels stay round.
    let yMin = 0, yMax, ticks, tickLabel;
    if (cfg.unit === 'level') {
      yMax = 4; ticks = [0, 1, 2, 3, 4];
      tickLabel = t => (t === 0 ? 'Aus' : String(t));
    } else if (cfg.unit === 'rate') {
      const div = dataMax >= 1024 ? 1024 : 1;
      const nt = niceTicks(0, Math.max(dataMax / div, div === 1 ? 10 : 1));
      yMax = nt.max * div; ticks = nt.ticks.map(t => t * div);
      tickLabel = t => (t === 0 ? '0' : `${+(t / div).toFixed(2)} ${div === 1 ? 'KB/s' : 'MB/s'}`);
    } else if (cfg.unit === 'W') {
      const nt = niceTicks(0, Math.max(dataMax, 1));
      yMax = nt.max; ticks = nt.ticks;
      tickLabel = t => `${+t.toFixed(2)} W`;
    } else if (cfg.unit === '%') {
      yMax = 100; ticks = [0, 25, 50, 75, 100];
      tickLabel = t => `${t}%`;
    } else {
      const nt = niceTicks(Math.max(0, Math.min(...vals) - 5), dataMax + 5);
      yMin = nt.min; yMax = nt.max; ticks = nt.ticks;
      tickLabel = t => `${t}°`;
    }
    const y = v => pad.t + ih - (v - yMin) / (yMax - yMin) * ih;

    // Grid + y labels (recessive)
    ticks.forEach(t => {
      svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + iw, y1: y(t), y2: y(t), class: 'grid-line' }));
      const label = svgEl('text', { x: pad.l - 8, y: y(t) + 3, 'text-anchor': 'end', class: 'axis-label' });
      label.textContent = tickLabel(t);
      svg.appendChild(label);
    });

    // X labels: every 3h (24h) or at each midnight (7d)
    const xTicks = [];
    if (data.range === '7d') {
      const d = new Date(x0 * 1000); d.setHours(24, 0, 0, 0);
      const everyDays = iw < 420 ? 2 : 1;  // narrow (phone): every other day so labels don't collide
      for (let t = d.getTime() / 1000; t < now; t += 86400 * everyDays) xTicks.push([t, new Date(t * 1000).toLocaleDateString(LOCALE, { weekday: 'short', day: 'numeric' })]);
    } else {
      const everyHours = iw < 420 ? 6 : 3;
      const d = new Date(x0 * 1000); d.setMinutes(0, 0, 0); d.setHours(Math.ceil((d.getHours() + 1) / everyHours) * everyHours);
      for (let t = d.getTime() / 1000; t < now; t += everyHours * 3600) xTicks.push([t, fmtTime(t)]);
    }
    xTicks.forEach(([t, text]) => {
      const lbl = svgEl('text', { x: x(t), y: H - 6, 'text-anchor': 'middle', class: 'axis-label' });
      lbl.textContent = text;
      svg.appendChild(lbl);
    });

    // Series: break the line where values are missing or the server was off
    const gapLimit = data.bucket_seconds * 2.5;
    cfg.series.forEach(s => {
      const segments = [];
      let seg = [];
      points.forEach((p, i) => {
        const v = p[s.key];
        const broken = i > 0 && p.ts - points[i - 1].ts > gapLimit;
        if (v == null || broken) { if (seg.length) segments.push(seg); seg = []; }
        if (v != null) seg.push([x(p.ts + data.bucket_seconds / 2), y(Math.min(v, yMax))]);
      });
      if (seg.length) segments.push(seg);

      segments.forEach(pts => {
        if (pts.length === 1) pts.push([pts[0][0] + 2, pts[0][1]]);
        const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
        if (s.area) {
          const area = svgEl('path', { d: `${d}L${pts[pts.length - 1][0].toFixed(1)},${y(yMin)}L${pts[0][0].toFixed(1)},${y(yMin)}Z`, fill: s.color, 'fill-opacity': 0.1 });
          svg.appendChild(area);
        }
        svg.appendChild(svgEl('path', {
          d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
          ...(s.dashed ? { 'stroke-dasharray': '5 4' } : {})
        }));
      });

      // Direct label at the line end when there are several series
      if (cfg.series.length > 1 && segments.length) {
        const last = segments[segments.length - 1].slice(-1)[0];
        const lbl = svgEl('text', { x: last[0] + 8, y: last[1] + 4, class: 'series-label' });
        lbl.textContent = s.label;
        svg.appendChild(lbl);
      }
    });

    // De-overlap the two end labels
    const labels = svg.querySelectorAll('.series-label');
    if (labels.length === 2) {
      const [a, b] = [...labels];
      const ya = +a.getAttribute('y'), yb = +b.getAttribute('y');
      if (Math.abs(ya - yb) < 14) {
        const mid = (ya + yb) / 2;
        a.setAttribute('y', ya <= yb ? mid - 7 : mid + 7);
        b.setAttribute('y', ya <= yb ? mid + 7 : mid - 7);
      }
    }

    // Hover layer
    const hover = svgEl('g', { visibility: 'hidden' });
    hover.appendChild(svgEl('line', { y1: pad.t, y2: pad.t + ih, class: 'crosshair' }));
    cfg.series.forEach(s => hover.appendChild(svgEl('circle', { r: 4, fill: s.color, class: 'hover-dot' })));
    svg.appendChild(hover);
    const hit = svgEl('rect', { x: pad.l, y: pad.t, width: iw, height: ih, fill: 'transparent' });
    svg.appendChild(hit);

    historyState.layout = { x, y, yMax, pad, iw, hover, points, cfg };
  }

  function onHistoryHover(evt) {
    const L = historyState.layout;
    if (!L) return;
    const rect = hEls.svg.getBoundingClientRect();
    const px = evt.clientX - rect.left;
    if (px < L.pad.l || px > L.pad.l + L.iw) return hideHistoryHover();

    const half = historyState.data.bucket_seconds / 2;
    let best = null, bestDist = Infinity;
    L.points.forEach(p => {
      const d = Math.abs(L.x(p.ts + half) - px);
      if (d < bestDist) { bestDist = d; best = p; }
    });
    if (!best || bestDist > 30) return hideHistoryHover();

    const cx = L.x(best.ts + half);
    const [line, ...dots] = L.hover.children;
    line.setAttribute('x1', cx); line.setAttribute('x2', cx);
    L.cfg.series.forEach((s, i) => {
      const v = best[s.key];
      dots[i].setAttribute('visibility', v == null ? 'hidden' : 'visible');
      if (v != null) { dots[i].setAttribute('cx', cx); dots[i].setAttribute('cy', L.y(Math.min(v, L.yMax))); }
    });
    L.hover.setAttribute('visibility', 'visible');

    // Tooltip: value first (strong), label second, keyed by a line swatch
    const tt = hEls.tooltip;
    tt.innerHTML = '';
    const time = document.createElement('div');
    time.className = 'tt-time';
    time.textContent = fmtTime(best.ts, historyState.range === '7d');
    tt.appendChild(time);
    const row = (value, label, s) => {
      const r = document.createElement('div');
      r.className = 'tt-row';
      const key = document.createElement('span');
      key.className = `line-key ${s && s.dashed ? 'dashed' : ''}`;
      key.style.setProperty('--key-color', s ? s.color : 'transparent');
      const b = document.createElement('b');
      b.textContent = value;
      r.append(key, b, document.createTextNode(label));
      tt.appendChild(r);
    };
    L.cfg.series.forEach(s => row(fmtValue(historyState.metric, best[s.key]), s.label, s));
    if (L.cfg.peak && best[L.cfg.peak] != null) row(fmtValue(historyState.metric, best[L.cfg.peak]), 'Spitze', null);

    tt.hidden = false;
    const left = cx + 14 + tt.offsetWidth > hEls.chart.clientWidth ? cx - 14 - tt.offsetWidth : cx + 14;
    tt.style.left = `${left}px`;
  }

  function hideHistoryHover() {
    if (historyState.layout) historyState.layout.hover.setAttribute('visibility', 'hidden');
    hEls.tooltip.hidden = true;
  }

  function renderHistoryTable() {
    const data = historyState.data;
    if (!data || !hEls.tableWrap.open) return;
    const cfg = HISTORY_METRICS[historyState.metric];
    const table = hEls.table;
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Zeit', ...cfg.series.map(s => s.label), ...(cfg.peak ? ['Spitze'] : [])].forEach(h => {
      const th = document.createElement('th');
      th.textContent = h;
      head.appendChild(th);
    });
    const body = table.createTBody();
    [...data.points].reverse().forEach(p => {
      const tr = body.insertRow();
      [fmtTime(p.ts, historyState.range === '7d'), ...cfg.series.map(s => fmtValue(historyState.metric, p[s.key])),
       ...(cfg.peak ? [fmtValue(historyState.metric, p[cfg.peak])] : [])].forEach(v => { tr.insertCell().textContent = v; });
    });
  }

  function renderHistory() {
    const cfg = HISTORY_METRICS[historyState.metric];
    const ifaceSelect = document.getElementById('history-iface');
    if (ifaceSelect) ifaceSelect.hidden = historyState.metric !== 'net' || ifaceSelect.options.length < 2;
    const ifaceNote = historyState.metric === 'net' && historyState.iface ? ` (${historyState.iface})` : '';
    hEls.subtitle.textContent = `${cfg.title}${ifaceNote} · ${historyState.range === '7d' ? 'letzte 7 Tage' : 'letzte 24 Stunden'}`;
    hEls.svg.setAttribute('aria-label', `Verlaufsdiagramm ${cfg.title}`);
    renderHistoryChart();
    renderHistoryTable();
  }

  async function loadHistory() {
    hEls.chart.classList.add('loading');  // keep the previous render while fetching
    try {
      const iface = historyState.metric === 'net' ? historyState.iface : '';
      const res = await fetch(`/api/history?range=${historyState.range}&iface=${encodeURIComponent(iface)}`);
      if (res.ok) historyState.data = await res.json();
    } catch (e) {
      console.warn('History fetch failed:', e);
    } finally {
      hEls.chart.classList.remove('loading');
      renderHistory();
    }
  }

  function initHistory() {
    Object.assign(hEls, {
      chart: document.getElementById('history-chart'),
      svg: document.getElementById('history-svg'),
      tooltip: document.getElementById('history-tooltip'),
      empty: document.getElementById('history-empty'),
      stats: document.getElementById('history-stats'),
      subtitle: document.getElementById('history-subtitle'),
      table: document.getElementById('history-table'),
      tableWrap: document.getElementById('history-table-wrap')
    });
    if (!hEls.chart) return;

    try {
      const saved = JSON.parse(localStorage.getItem('historyView') || '{}');
      if (HISTORY_METRICS[saved.metric]) historyState.metric = saved.metric;
      if (saved.range === '24h' || saved.range === '7d') historyState.range = saved.range;
    } catch (e) { /* storage unavailable */ }

    const bindSegmented = (id, attr, onSelect) => {
      const group = document.getElementById(id);
      const buttons = group.querySelectorAll('button');
      const sync = () => buttons.forEach(b => {
        const active = b.dataset[attr] === historyState[attr];
        b.classList.toggle('active', active);
        b.setAttribute('aria-selected', active);
      });
      buttons.forEach(b => b.addEventListener('click', () => {
        historyState[attr] = b.dataset[attr];
        sync();
        try { localStorage.setItem('historyView', JSON.stringify({ metric: historyState.metric, range: historyState.range })); } catch (e) { /* ignore */ }
        onSelect();
      }));
      sync();
    };
    bindSegmented('history-range', 'range', loadHistory);
    // Network data depends on the chosen interface, so switching to/from it reloads
    bindSegmented('history-metric', 'metric', () => (historyState.iface ? loadHistory() : renderHistory()));

    const ifaceSelect = document.getElementById('history-iface');
    fetch('/api/history/interfaces').then(r => (r.ok ? r.json() : { interfaces: [] })).then(({ interfaces }) => {
      interfaces.forEach(name => {
        const o = document.createElement('option');
        o.value = name;
        o.textContent = name;
        ifaceSelect.appendChild(o);
      });
      renderHistory();
    }).catch(() => {});
    ifaceSelect.addEventListener('change', () => { historyState.iface = ifaceSelect.value; loadHistory(); });

    hEls.svg.addEventListener('pointermove', onHistoryHover);
    hEls.svg.addEventListener('pointerleave', hideHistoryHover);
    hEls.tableWrap.addEventListener('toggle', renderHistoryTable);
    new ResizeObserver(() => renderHistoryChart()).observe(hEls.chart);

    loadHistory();
    setInterval(loadHistory, 5 * 60 * 1000);
  }

  // ---------------------------------------------------------------------------
  // NVMe history sheet: one value per day (wear, writes, temperature range)
  // ---------------------------------------------------------------------------
  const nvmeState = { days: 90, data: null };
  const NVME_TEMP_SERIES = [
    { key: 'temp_max', label: 'Höchstwert', color: 'var(--chart-down)' },
    { key: 'temp_avg', label: 'Durchschnitt', color: 'var(--chart-up)', dashed: true }
  ];

  const fmtDay = (day, opts = { day: 'numeric', month: 'numeric' }) =>
    new Date(`${day}T12:00:00`).toLocaleDateString(LOCALE, opts);
  const fmtGb = gb => (gb == null ? '–' : gb >= 1000 ? `${(gb / 1000).toFixed(2)} TB` : `${gb >= 100 ? Math.round(gb) : gb.toFixed(1)} GB`);

  function nvmeStat(label, value, sub) {
    const box = document.createElement('div');
    box.className = 'history-stat';
    const l = document.createElement('span'); l.className = 'label'; l.textContent = label;
    const v = document.createElement('span'); v.className = 'value'; v.textContent = value;
    box.append(l, v);
    if (sub) { const x = document.createElement('span'); x.className = 'sub'; x.textContent = sub; box.appendChild(x); }
    return box;
  }

  function renderNvmeStats(sum, rangeDays) {
    const box = $('nvme-stats');
    box.innerHTML = '';
    if (sum.used != null) {
      box.appendChild(nvmeStat('Verschleiß', `${sum.used}%`,
        sum.used_change > 0 ? `+${sum.used_change}% in ${rangeDays} Tagen` : `unverändert in ${rangeDays} Tagen`));
      let life, lifeSub;
      if (sum.life_status === 'estimate') {
        life = sum.remaining_years >= 20 ? '> 20 Jahre' : `~${sum.remaining_years.toLocaleString(LOCALE)} Jahre`;
        lifeSub = `Hochrechnung: ${sum.wear_per_year.toLocaleString(LOCALE)}% pro Jahr`;
      } else if (sum.life_status === 'no_wear') {
        life = 'Kein Verschleiß'; lifeSub = `seit ${sum.tracked_days} Tagen kein Anstieg`;
      } else {
        life = 'Sammle Daten'; lifeSub = `${sum.tracked_days ?? 0} von 30 Tagen`;
      }
      box.appendChild(nvmeStat('Restlebensdauer', life, lifeSub));
      box.appendChild(nvmeStat('Ø pro Tag geschrieben', fmtGb(sum.avg_written_gb)));
    }
    if (sum.temp_max != null) box.appendChild(nvmeStat('Höchsttemperatur', `${sum.temp_max.toFixed(0)} °C`, `in ${rangeDays} Tagen`));
    if (sum.used != null) {
      box.appendChild(nvmeStat('Unsichere Abschaltungen', sum.unsafe_change > 0 ? `+${sum.unsafe_change}` : '0',
        sum.unsafe_change > 0 ? 'Strom weg ohne Herunterfahren' : `in ${rangeDays} Tagen`));
      box.appendChild(nvmeStat('Medienfehler', String(sum.media_errors ?? 0)));
    }
  }

  // Small daily chart: bars (one series) or lines (several); hover shows the day's values
  function drawDailyChart(container, rows, cfg) {
    const svg = container.querySelector('svg');
    const tip = container.querySelector('.history-tooltip');
    svg.innerHTML = '';
    tip.hidden = true;
    const series = cfg.series;
    const vals = rows.flatMap(r => series.map(s => r[s.key])).filter(v => v != null);
    if (!vals.length) {
      const t = svgEl('text', { x: '50%', y: '50%', 'text-anchor': 'middle', class: 'axis-label' });
      t.textContent = 'Noch keine Werte';
      svg.appendChild(t);
      return;
    }
    const W = container.clientWidth, H = container.clientHeight;
    const pad = { l: 58, r: cfg.kind === 'line' ? 12 : 8, t: 10, b: 24 };
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

    // Fixed day grid for the whole range, so gaps show as gaps
    const n = nvmeState.days;
    const end = new Date(); end.setHours(12, 0, 0, 0);
    const dayIndex = day => n - 1 - Math.round((end - new Date(`${day}T12:00:00`)) / 86400000);
    const slot = iw / n;
    const xc = i => pad.l + (i + 0.5) * slot;

    const lo = cfg.kind === 'line' ? Math.max(0, Math.min(...vals) - 3) : 0;
    const nt = niceTicks(lo, Math.max(...vals, cfg.minMax || 1));
    const y = v => pad.t + ih - (v - nt.min) / (nt.max - nt.min) * ih;
    nt.ticks.forEach(t => {
      svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + iw, y1: y(t), y2: y(t), class: 'grid-line' }));
      const lbl = svgEl('text', { x: pad.l - 8, y: y(t) + 3, 'text-anchor': 'end', class: 'axis-label' });
      lbl.textContent = cfg.tick(t);
      svg.appendChild(lbl);
    });

    // X labels: about 6 dates across the range
    const every = Math.max(1, Math.round(n / 6));
    for (let i = n - 1; i >= 0; i -= every) {
      const d = new Date(end.getTime() - (n - 1 - i) * 86400000);
      const lbl = svgEl('text', { x: xc(i), y: H - 6, 'text-anchor': 'middle', class: 'axis-label' });
      lbl.textContent = d.toLocaleDateString(LOCALE, { day: 'numeric', month: 'numeric' });
      svg.appendChild(lbl);
    }

    const placed = rows.map(r => ({ r, i: dayIndex(r.day) })).filter(p => p.i >= 0 && p.i < n);
    if (cfg.kind === 'bar') {
      const s = series[0];
      const bw = Math.max(1, Math.min(slot - 2, 18));  // 2px surface gap between bars
      placed.forEach(p => {
        const v = p.r[s.key];
        if (v == null) return;
        const h = Math.max(1, y(nt.min) - y(v));
        const x0 = xc(p.i) - bw / 2, y0 = y(nt.min) - h, rad = Math.min(4, bw / 2, h);
        // Rounded top, square base on the axis
        const d = `M${x0},${y0 + h}V${y0 + rad}Q${x0},${y0} ${x0 + rad},${y0}H${x0 + bw - rad}Q${x0 + bw},${y0} ${x0 + bw},${y0 + rad}V${y0 + h}Z`;
        svg.appendChild(svgEl('path', { d, fill: s.color, class: 'bar', 'data-i': p.i }));
      });
    } else {
      series.forEach(s => {
        let seg = [];
        const flush = () => {
          if (!seg.length) return;
          if (seg.length === 1) seg.push([seg[0][0] + 2, seg[0][1]]);
          svg.appendChild(svgEl('path', {
            d: seg.map((q, k) => `${k ? 'L' : 'M'}${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(''),
            fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
            ...(s.dashed ? { 'stroke-dasharray': '5 4' } : {})
          }));
          seg = [];
        };
        let prevI = null;
        placed.forEach(p => {
          const v = p.r[s.key];
          if (v == null || (prevI != null && p.i - prevI > 1)) flush();
          if (v != null) seg.push([xc(p.i), y(v)]);
          prevI = p.i;
        });
        flush();
      });
    }

    // Hover: crosshair + dots (lines) or dimmed neighbours (bars), tooltip with the day's values
    const hover = svgEl('g', { visibility: 'hidden' });
    hover.appendChild(svgEl('line', { y1: pad.t, y2: pad.t + ih, class: 'crosshair' }));
    if (cfg.kind === 'line') series.forEach(s => hover.appendChild(svgEl('circle', { r: 4, fill: s.color, class: 'hover-dot' })));
    svg.appendChild(hover);
    const byIndex = new Map(placed.map(p => [p.i, p.r]));

    svg.onpointermove = evt => {
      const px = evt.clientX - svg.getBoundingClientRect().left;
      const i = Math.floor((px - pad.l) / slot);
      const r = byIndex.get(i);
      if (!r) { svg.onpointerleave(); return; }
      const cx = xc(i);
      const [line, ...dots] = hover.children;
      line.setAttribute('x1', cx); line.setAttribute('x2', cx);
      dots.forEach((dot, k) => {
        const v = r[series[k].key];
        dot.setAttribute('visibility', v == null ? 'hidden' : 'visible');
        if (v != null) { dot.setAttribute('cx', cx); dot.setAttribute('cy', y(v)); }
      });
      hover.setAttribute('visibility', 'visible');
      svg.querySelectorAll('.bar').forEach(b => b.classList.toggle('dim', +b.dataset.i !== i));

      tip.innerHTML = '';
      const t = document.createElement('div');
      t.className = 'tt-time';
      t.textContent = fmtDay(r.day, { weekday: 'short', day: 'numeric', month: 'numeric', year: 'numeric' });
      tip.appendChild(t);
      series.forEach(s => {
        const row = document.createElement('div');
        row.className = 'tt-row';
        const key = document.createElement('span');
        key.className = `line-key ${s.dashed ? 'dashed' : ''}`;
        key.style.setProperty('--key-color', s.color);
        const b = document.createElement('b');
        b.textContent = cfg.fmt(r[s.key]);
        row.append(key, b, document.createTextNode(s.label));
        tip.appendChild(row);
      });
      tip.hidden = false;
      tip.style.left = `${cx + 14 + tip.offsetWidth > W ? cx - 14 - tip.offsetWidth : cx + 14}px`;
    };
    svg.onpointerleave = () => {
      hover.setAttribute('visibility', 'hidden');
      svg.querySelectorAll('.bar.dim').forEach(b => b.classList.remove('dim'));
      tip.hidden = true;
    };
  }

  function renderNvmeHistory() {
    const data = nvmeState.data;
    if (!data) return;
    const rows = data.days;
    const has = rows.length > 0;
    $('nvme-empty').hidden = has;
    ['nvme-chart-written', 'nvme-chart-temp'].forEach(id => { $(id).hidden = !has; });
    document.querySelectorAll('.nvme-chart-title, .nvme-chart-head').forEach(e => { e.hidden = !has; });
    renderNvmeStats(data.summary || {}, nvmeState.days);
    $('nvme-modal-sub').textContent = has
      ? `Ein Messpunkt pro Tag · ${rows.length} Tage erfasst`
      : 'Ein Messpunkt pro Tag';
    if (!has) return;

    const maxGb = Math.max(0, ...rows.map(r => r.written_gb || 0));
    const inTb = maxGb >= 1000;
    drawDailyChart($('nvme-chart-written'), rows, {
      kind: 'bar', series: [{ key: 'written_gb', label: 'Geschrieben', color: 'var(--chart-down)' }],
      tick: t => (t === 0 ? '0' : inTb ? `${+(t / 1000).toFixed(2)} TB` : `${+t.toFixed(1)} GB`), fmt: fmtGb, minMax: 1
    });
    drawDailyChart($('nvme-chart-temp'), rows, {
      kind: 'line', series: NVME_TEMP_SERIES, tick: t => `${t}°`, fmt: v => (v == null ? '–' : `${v.toFixed(1)} °C`), minMax: 10
    });

    const table = $('nvme-table');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Tag', 'Geschrieben', 'Temp. Ø', 'Temp. max', 'Verschleiß', 'Reserve'].forEach(h => {
      const th = document.createElement('th'); th.textContent = h; head.appendChild(th);
    });
    const body = table.createTBody();
    [...rows].reverse().forEach(r => {
      const tr = body.insertRow();
      [fmtDay(r.day, { weekday: 'short', day: 'numeric', month: 'numeric', year: 'numeric' }), fmtGb(r.written_gb),
       r.temp_avg == null ? '–' : `${r.temp_avg.toFixed(1)} °C`, r.temp_max == null ? '–' : `${r.temp_max.toFixed(1)} °C`,
       r.used == null ? '–' : `${r.used}%`, r.spare == null ? '–' : `${r.spare}%`].forEach(v => { tr.insertCell().textContent = v; });
    });
  }

  async function loadNvmeHistory() {
    try {
      const res = await fetch(`/api/nvme/history?days=${nvmeState.days}`);
      if (res.ok) nvmeState.data = await res.json();
    } catch (e) {
      console.warn('NVMe history fetch failed:', e);
    }
    renderNvmeHistory();
  }

  function openNvmeHistory() {
    const modal = $('nvme-modal');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    $('nvme-modal-close').focus();
    loadNvmeHistory();
  }

  function closeNvmeHistory() {
    const modal = $('nvme-modal');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  function initNvmeHistory() {
    if (!$('nvme-modal')) return;
    const legend = $('nvme-temp-legend');
    NVME_TEMP_SERIES.forEach(s => {
      const item = document.createElement('span');
      const key = document.createElement('span');
      key.className = `line-key ${s.dashed ? 'dashed' : ''}`;
      key.style.setProperty('--key-color', s.color);
      item.append(key, document.createTextNode(s.label));
      legend.appendChild(item);
    });
    $('nvme-history-btn').addEventListener('click', openNvmeHistory);
    $('nvme-modal-close').addEventListener('click', closeNvmeHistory);
    $('nvme-modal').addEventListener('click', e => { if (e.target.id === 'nvme-modal') closeNvmeHistory(); });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && $('nvme-modal').classList.contains('open')) closeNvmeHistory();
    });
    document.querySelectorAll('#nvme-range button').forEach(b => b.addEventListener('click', () => {
      nvmeState.days = +b.dataset.days;
      document.querySelectorAll('#nvme-range button').forEach(x => x.classList.toggle('active', x === b));
      loadNvmeHistory();
    }));
    new ResizeObserver(() => { if ($('nvme-modal').classList.contains('open')) renderNvmeHistory(); })
      .observe($('nvme-chart-written'));
  }

  // ---------------------------------------------------------------------------
  // Session expired / not logged in: any 401 sends the user to the login page
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const res = await nativeFetch(...args);
    // The notes card is private even in public view: it shows its own hint instead
    if (res.status === 401 && !/^\/api\/(auth\/|notes|actions|events|disk-forecast)/.test(String(args[0]))) location.href = '/login';
    return res;
  };

  async function initAuth() {
    try {
      const status = await (await nativeFetch('/api/auth/status')).json();
      const chip = document.getElementById('user-chip');
      if (status.user) {
        document.getElementById('user-name').textContent = status.user;
        chip.hidden = false;
      } else {
        document.getElementById('btn-login-link').hidden = false;  // public view, not logged in
      }
    } catch (e) { /* ignore */ }

    document.getElementById('btn-logout').addEventListener('click', async () => {
      await nativeFetch('/api/auth/logout', { method: 'POST' });
      location.href = '/login';
    });
  }

  function init() {
    initAuth();
    startClock();
    initHeaderControls();
    initUpdaterControls();
    initTabs();
    initDiagnostics();
    loadWeather();
    loadDrives();
    initHistory();
    initNvmeHistory();
    initHomelab();
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
