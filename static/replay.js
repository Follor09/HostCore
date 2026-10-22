/**
 * PiCore Dashboard - day replay
 * Play back a stored day like a video: drag the time slider (or press play) and the CPU,
 * temperature, RAM, network, fan, power and 3D-Pi cards show the values of that minute.
 * Events of the day (service down, throttling, logins …) are marks on the slider and pop
 * up while playing. Live updates keep arriving in the background; closing goes back to them.
 */
(function () {
  'use strict';

  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const SPEEDS = [[60, '1 Min/s'], [300, '5 Min/s'], [900, '15 Min/s'], [3600, '1 Std/s']];
  const GAP = 180;          // no sample for 3 min = dashboard was off
  const SPARK = 25;         // points in the card sparklines (as live)

  const R = {
    active: false, playing: false, speed: 900, start: 0, end: 0, t: 0, samples: [], metrics: [],
    events: [], shown: -1, raf: 0, lastFrame: 0, tickerTimer: 0, bar: null, window: '24h'
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const fmtClock = ts => new Date(ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
  const fmtDay = ts => new Date(ts * 1000).toLocaleDateString(LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit' });
  const midnight = (d = new Date()) => Math.floor(new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000);

  // ---------------------------------------------------------------------------
  // Bar
  // ---------------------------------------------------------------------------
  const ICON = {
    play: '<polygon points="7 4 20 12 7 20 7 4"></polygon>',
    pause: '<rect x="6" y="4" width="4" height="16" rx="1"></rect><rect x="14" y="4" width="4" height="16" rx="1"></rect>',
    back: '<polygon points="11 19 2 12 11 5 11 19"></polygon><polygon points="22 19 13 12 22 5 22 19"></polygon>',
    fwd: '<polygon points="13 19 22 12 13 5 13 19"></polygon><polygon points="2 19 11 12 2 5 2 19"></polygon>',
    close: '<path d="M18 6L6 18M6 6l12 12"></path>'
  };
  const svg = name => `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[name]}</svg>`;

  function iconButton(name, title, onClick, cls = '') {
    const b = el('button', `replay-btn ${cls}`);
    b.type = 'button';
    b.innerHTML = svg(name);
    b.title = t(title);
    b.setAttribute('aria-label', t(title));
    b.addEventListener('click', onClick);
    return b;
  }

  function buildBar() {
    const bar = el('div', 'replay-bar');
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', t('Tages-Replay'));

    const top = el('div', 'replay-top');
    const badge = el('span', 'replay-badge', 'REPLAY');
    badge.setAttribute('data-no-i18n', '');
    const when = el('div', 'replay-when');
    R.dayEl = el('span', 'replay-day');
    R.clockEl = el('span', 'replay-clock', '--:--');
    R.clockEl.setAttribute('data-no-i18n', '');
    when.append(R.dayEl, R.clockEl);

    const controls = el('div', 'replay-controls');
    controls.append(
      iconButton('back', '15 Minuten zurück (←)', () => seek(R.t - 900)),
      R.playBtn = iconButton('play', 'Abspielen (Leertaste)', togglePlay, 'replay-play'),
      iconButton('fwd', '15 Minuten vor (→)', () => seek(R.t + 900))
    );

    const speed = el('div', 'segmented replay-speed');
    speed.setAttribute('role', 'radiogroup');
    speed.setAttribute('aria-label', t('Geschwindigkeit'));
    SPEEDS.forEach(([v, label]) => {
      const b = el('button', v === R.speed ? 'active' : '', label);
      b.type = 'button';
      b.addEventListener('click', () => {
        R.speed = v;
        [...speed.children].forEach(x => x.classList.toggle('active', x === b));
      });
      speed.appendChild(b);
    });

    R.daySelect = el('select', 'inline-select replay-select');
    R.daySelect.setAttribute('aria-label', t('Zeitraum'));
    R.daySelect.appendChild(new Option(t('Letzte 24 Std.'), '24h'));
    const today = midnight();
    for (let i = 0; i < 7; i++) {
      const d = new Date((today - i * 86400 + 7200) * 1000);
      const label = i === 0 ? t('Heute') : i === 1 ? t('Gestern') : d.toLocaleDateString(LOCALE, { weekday: 'long', day: '2-digit', month: '2-digit' });
      R.daySelect.appendChild(new Option(label, String(midnight(d))));
    }
    R.daySelect.addEventListener('change', () => load(R.daySelect.value));

    const close = iconButton('close', 'Replay beenden (Esc)', closeReplay, 'replay-close');
    top.append(badge, when, controls, speed, R.daySelect, close);

    const track = el('div', 'replay-track');
    R.slider = el('input', 'replay-slider');
    R.slider.type = 'range';
    R.slider.step = '60';
    R.slider.setAttribute('aria-label', t('Zeitpunkt'));
    R.slider.addEventListener('input', () => { pause(); seek(+R.slider.value); });
    R.marks = el('div', 'replay-marks');
    R.gaps = el('div', 'replay-gaps');
    R.hours = el('div', 'replay-hours');
    track.append(R.gaps, R.slider, R.marks);

    R.ticker = el('div', 'replay-ticker');
    R.ticker.setAttribute('aria-live', 'polite');
    R.status = el('div', 'replay-status');

    bar.append(top, track, R.hours, R.ticker, R.status);
    document.body.appendChild(bar);
    return bar;
  }

  // ---------------------------------------------------------------------------
  // Data
  // ---------------------------------------------------------------------------
  async function load(which) {
    pause();
    R.window = which;
    const now = Math.floor(Date.now() / 1000);
    if (which === '24h') { R.start = now - 86400; R.end = now; } else { R.start = +which; R.end = Math.min(now, R.start + 86400); }
    R.samples = [];
    R.events = [];
    R.shown = -1;
    R.status.textContent = t('Lade…');
    try {
      const res = await fetch(`/api/replay?start=${R.start}&end=${R.end}`);
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      R.metrics = data.metrics;
      R.samples = data.samples;
    } catch (e) {
      R.status.textContent = t('Fehler beim Laden.');
      return;
    }
    try {
      const days = Math.min(8, Math.ceil((now - R.start) / 86400) + 1);
      const res = await fetch(`/api/events?days=${days}`);
      if (res.ok) R.events = ((await res.json()).events || []).filter(e => e.ts >= R.start && e.ts <= R.end).sort((a, b) => a.ts - b.ts);
    } catch (e) { /* events need "System ansehen": replay works without them */ }

    R.slider.min = String(R.start);
    R.slider.max = String(R.end);
    R.dayEl.textContent = which === '24h' ? t('Letzte 24 Std.') : fmtDay(R.start);
    R.status.textContent = R.samples.length ? '' : t('Für diesen Zeitraum gibt es keine Messwerte.');
    renderMarks();
    renderGaps();
    renderHours();
    seek(R.samples.length ? R.samples[0][0] : R.start, true);
    if (R.samples.length) play();
  }

  const val = (s, name) => s[R.metrics.indexOf(name) + 1];

  function renderMarks() {
    R.marks.innerHTML = '';
    const span = Math.max(1, R.end - R.start);
    R.events.forEach(e => {
      const m = el('button', `replay-mark ${e.level}`);
      m.type = 'button';
      m.style.left = `${((e.ts - R.start) / span) * 100}%`;
      m.title = `${fmtClock(e.ts)} · ${t(e.title)}${e.detail ? ` – ${e.detail}` : ''}`;
      m.setAttribute('aria-label', m.title);
      m.addEventListener('click', () => { pause(); seek(e.ts); showTicker(e); });
      R.marks.appendChild(m);
    });
  }

  // Stretches without samples (dashboard or Pi off) are hatched on the track
  function renderGaps() {
    R.gaps.innerHTML = '';
    const span = Math.max(1, R.end - R.start);
    const add = (a, b) => {
      if (b - a < GAP) return;
      const g = el('span', 'replay-gap');
      g.style.left = `${((a - R.start) / span) * 100}%`;
      g.style.width = `${((b - a) / span) * 100}%`;
      R.gaps.appendChild(g);
    };
    let prev = R.start;
    R.samples.forEach(s => { add(prev, s[0]); prev = s[0]; });
    add(prev, R.end);
  }

  function renderHours() {
    R.hours.innerHTML = '';
    const span = Math.max(1, R.end - R.start);
    const first = Math.ceil(R.start / 10800) * 10800;
    for (let ts = first; ts <= R.end; ts += 10800) {
      const h = el('span', '', fmtClock(ts));
      h.style.left = `${((ts - R.start) / span) * 100}%`;
      R.hours.appendChild(h);
    }
  }

  // ---------------------------------------------------------------------------
  // Playback
  // ---------------------------------------------------------------------------
  function indexAt(ts) {
    let lo = 0, hi = R.samples.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (R.samples[mid][0] <= ts) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  function seek(ts, silent) {
    const prev = R.t;
    R.t = Math.max(R.start, Math.min(R.end, ts));
    R.slider.value = String(R.t);
    R.slider.style.setProperty('--pos', `${((R.t - R.start) / Math.max(1, R.end - R.start)) * 100}%`);
    R.clockEl.textContent = fmtClock(R.t);
    if (R.window === '24h') R.dayEl.textContent = fmtDay(R.t);

    const idx = indexAt(R.t);
    const gap = idx < 0 || R.t - R.samples[idx][0] > GAP;
    R.bar.classList.toggle('no-data', gap && R.samples.length > 0);
    if (!gap && idx !== R.shown) {
      R.shown = idx;
      paint(idx);
    }
    // Events passed while playing forward
    if (!silent && R.playing && R.t > prev) {
      const passed = R.events.filter(e => e.ts > prev && e.ts <= R.t);
      if (passed.length) showTicker(passed[passed.length - 1]);
    }
  }

  function showTicker(e) {
    R.ticker.textContent = `${fmtClock(e.ts)} · ${t(e.title)}${e.detail ? ` – ${e.detail}` : ''}`;
    R.ticker.className = `replay-ticker show ${e.level}`;
    clearTimeout(R.tickerTimer);
    R.tickerTimer = setTimeout(() => R.ticker.classList.remove('show'), 3500);
  }

  function frame(now) {
    if (!R.playing) return;
    const dt = Math.min(0.25, (now - R.lastFrame) / 1000);
    R.lastFrame = now;
    seek(R.t + dt * R.speed);
    if (R.t >= R.end) { pause(); return; }
    R.raf = requestAnimationFrame(frame);
  }

  function play() {
    if (!R.samples.length) return;
    if (R.t >= R.end - 60) seek(R.samples[0][0], true);
    R.playing = true;
    R.playBtn.innerHTML = svg('pause');
    R.playBtn.title = t('Pause (Leertaste)');
    R.lastFrame = performance.now();
    cancelAnimationFrame(R.raf);
    R.raf = requestAnimationFrame(frame);
  }

  function pause() {
    R.playing = false;
    cancelAnimationFrame(R.raf);
    if (R.playBtn) {
      R.playBtn.innerHTML = svg('play');
      R.playBtn.title = t('Abspielen (Leertaste)');
    }
  }

  function togglePlay() { (R.playing ? pause : play)(); }

  // Build a system snapshot like the live one, with the values of sample idx
  function paint(idx) {
    const s = R.samples[idx];
    const base = (window.PiDash && window.PiDash.lastSystem()) || {};
    const sys = JSON.parse(JSON.stringify(base));
    const round = (v, d = 1) => v == null ? v : Math.round(v * 10 ** d) / 10 ** d;
    sys.replay = true;
    ['uptime', 'swap', 'throttled', 'disk'].forEach(k => delete sys[k]);

    if (val(s, 'cpu') != null) sys.cpu = { ...(base.cpu || {}), percent: round(val(s, 'cpu')), cores: undefined };
    else delete sys.cpu;
    if (val(s, 'temp') != null) sys.temperature = { ...(base.temperature || {}), celsius: round(val(s, 'temp')), source: 'Replay', is_simulated: false };
    else delete sys.temperature;
    if (val(s, 'ram') != null && base.memory) {
      const used = Math.round(base.memory.total_mb * val(s, 'ram') / 100);
      sys.memory = { ...base.memory, percent: round(val(s, 'ram')), used_mb: used, available_mb: base.memory.total_mb - used };
    } else delete sys.memory;
    if (val(s, 'net_down') != null && base.network) {
      sys.network = { ...base.network, download_kbps: round(val(s, 'net_down')), upload_kbps: round(val(s, 'net_up') || 0) };
    } else delete sys.network;
    if (base.fan && base.fan.available && val(s, 'fan') != null) {
      const level = Math.round(val(s, 'fan'));
      sys.fan = { ...base.fan, level, level_name: level === 0 ? 'Aus' : `Stufe ${level}`, rpm: null, pwm_percent: null };
    } else delete sys.fan;
    if (base.power && base.power.available && val(s, 'power') != null) sys.power = { ...base.power, board_watt: val(s, 'power') };
    else delete sys.power;
    sys.disk_io = val(s, 'disk_read') != null ? [{ name: t('Alle'), read_kbs: round(val(s, 'disk_read')), write_kbs: round(val(s, 'disk_write')) }] : [];

    // Sparklines: the minutes before this one
    const before = R.samples.slice(Math.max(0, idx - SPARK + 1), idx);
    const series = name => before.map(x => val(x, name)).filter(v => v != null);
    window.PiDash.showSystem(sys, { cpu: series('cpu'), temp: series('temp'), netDown: series('net_down'), netUp: series('net_up'), power: series('power') });
  }

  // ---------------------------------------------------------------------------
  // Open / close
  // ---------------------------------------------------------------------------
  function open(which = '24h') {
    if (!window.PiDash) return;
    if (!R.active) {
      R.active = true;
      document.body.classList.add('replaying');
      R.bar = buildBar();
      R.daySelect.value = which;
    }
    load(which);
  }

  function closeReplay() {
    if (!R.active) return;
    pause();
    clearTimeout(R.tickerTimer);
    R.active = false;
    document.body.classList.remove('replaying');
    if (R.bar) R.bar.remove();
    R.bar = null;
    window.PiDash.resetSparklines();
  }

  document.addEventListener('keydown', e => {
    if (!R.active) return;
    const tag = (e.target && e.target.tagName) || '';
    if (document.querySelector('.modal-backdrop.open, .palette-backdrop:not([hidden])')) return;
    if (e.key === 'Escape') { closeReplay(); return; }
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) && e.target !== R.slider) return;
    if (e.key === ' ') { e.preventDefault(); togglePlay(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      pause();
      seek(R.t + (e.key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 3600 : 900));
    }
  });

  document.getElementById('btn-replay')?.addEventListener('click', () => (R.active ? closeReplay() : open()));
  document.getElementById('history-replay')?.addEventListener('click', () => open());

  window.PiReplay = {
    get active() { return R.active; },
    open, close: closeReplay
  };
})();
