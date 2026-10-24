/**
 * HostCore Dashboard - quick actions card and web services (health checks) card.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function post(url) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    return data;
  }

  // ---------------------------------------------------------------------------
  // Quick actions
  // ---------------------------------------------------------------------------
  const STATE = {
    running: ['warning', 'Läuft…'], done: ['good', 'Fertig'], failed: ['critical', 'Fehler'],
    timeout: ['critical', 'Zeit abgelaufen'], stopped: ['', 'Gestoppt']
  };
  const run = { id: null, action: null, offset: 0, timer: null };
  let actionList = [];

  function renderActions(data) {
    actionList = data.actions;
    const list = $('actions-list');
    list.innerHTML = '';
    data.actions.forEach(a => {
      const b = el('button', 'action-btn');
      b.dataset.id = a.id;
      const name = el('span', 'action-name', a.name);
      name.setAttribute('data-no-i18n', '');
      b.appendChild(name);
      if (a.last) {
        const [level, text] = STATE[a.last.state] || ['', a.last.state];
        const when = new Date(a.last.started * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        b.appendChild(el('span', `action-last ${level}`, `${t(text)} · ${when}`));
      }
      if (a.description) b.title = a.description;
      b.addEventListener('click', () => startAction(a, b));
      list.appendChild(b);
    });
    const hint = $('actions-hint');
    hint.textContent = data.actions.length
      ? (data.skipped.length ? `${t('Nicht ausführbar (chmod +x fehlt)')}: ${data.skipped.join(', ')}` : '')
      : `${t('Noch keine Skripte. Lege ausführbare Skripte in diesen Ordner:')} ${data.dir}`;
    $('actions-sub').textContent = data.actions.length ? `${data.actions.length} · ${data.dir}` : t('Eigene Skripte per Klick');
  }

  async function loadActions() {
    try {
      const res = await fetch('/api/actions');
      if (res.status === 401) {
        $('actions-list').innerHTML = '';
        $('actions-hint').textContent = t('Schnellaktionen sind nur nach der Anmeldung verfügbar.');
        return;
      }
      renderActions(await res.json());
    } catch (e) { /* offline */ }
  }

  function startAction(a, btn) {
    if (a.confirm && !btn.classList.contains('confirming')) {
      btn.classList.add('confirming');
      const name = btn.querySelector('.action-name');
      const original = name.textContent;
      name.textContent = `${original} – ${t('wirklich?')}`;
      setTimeout(() => { btn.classList.remove('confirming'); name.textContent = original; }, 4000);
      return;
    }
    btn.classList.remove('confirming');
    execute(a);
  }

  async function execute(a) {
    openSheet(a);
    try {
      const r = await post(`/api/actions/${encodeURIComponent(a.id)}/run`);
      run.id = r.id;
      run.offset = 0;
      $('action-run-output').textContent = '';
      showRun(r);
      poll();
    } catch (err) {
      $('action-run-output').textContent = err.message;
      setTag('failed');
    }
  }

  function setTag(state) {
    const [level, text] = STATE[state] || ['', state];
    const tag = $('action-run-tag');
    tag.className = `health-tag ${level}`;
    tag.textContent = t(text);
    $('action-run-stop').hidden = state !== 'running';
    $('action-run-again').hidden = state === 'running';
  }

  function showRun(r) {
    const out = $('action-run-output');
    const atBottom = out.scrollTop + out.clientHeight >= out.scrollHeight - 20;
    if (r.output) out.textContent += r.output;
    run.offset = r.offset;
    if (atBottom) out.scrollTop = out.scrollHeight;
    setTag(r.state);
    const secs = Math.round(((r.finished || Date.now() / 1000) - r.started));
    // One German sentence, translated as a whole (the i18n patterns know it)
    $('action-run-sub').textContent = `${r.state === 'running' ? 'läuft seit' : 'Dauer'} ${secs} s`
      + (r.exit_code != null ? ` · Exit-Code ${r.exit_code}` : '');
  }

  async function poll() {
    clearTimeout(run.timer);
    if (!run.id) return;
    try {
      const res = await fetch(`/api/actions/runs/${run.id}?offset=${run.offset}`);
      if (!res.ok) return;
      const r = await res.json();
      showRun(r);
      if (r.state === 'running') run.timer = setTimeout(poll, 700);
      else loadActions();
    } catch (e) {
      run.timer = setTimeout(poll, 2000);
    }
  }

  function openSheet(a) {
    run.action = a;
    $('action-run-title').textContent = a.name;
    $('action-run-sub').textContent = '';
    $('action-run-output').textContent = t('Starte…');
    setTag('running');
    const modal = $('action-run-modal');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    $('action-run-close').focus();
  }

  function closeSheet() {
    clearTimeout(run.timer);
    run.id = null;
    const modal = $('action-run-modal');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  function initActions() {
    if (!$('actions-card')) return;
    $('action-run-close').addEventListener('click', closeSheet);
    $('action-run-modal').addEventListener('click', e => { if (e.target.id === 'action-run-modal') closeSheet(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('action-run-modal').classList.contains('open')) closeSheet(); });
    $('action-run-stop').addEventListener('click', async () => {
      if (!run.id) return;
      try { showRun(await post(`/api/actions/runs/${run.id}/stop`)); } catch (err) { toast(err.message, 'error'); }
    });
    $('action-run-again').addEventListener('click', () => run.action && execute(run.action));
    loadActions();
    window.PiActions = { list: () => actionList, run: id => { const a = actionList.find(x => x.id === id); if (a) execute(a); } };
  }

  // ---------------------------------------------------------------------------
  // Web services (health checks)
  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Detail sheet: response time chart + incidents
  // ---------------------------------------------------------------------------
  const detail = { check: null, hours: 24 };
  const fmtDur = s => s >= 86400 ? `${Math.floor(s / 86400)} T. ${Math.round((s % 86400) / 3600)} Std.` : s >= 3600 ? `${Math.floor(s / 3600)} Std. ${Math.round((s % 3600) / 60)} Min.` : `${Math.max(1, Math.round(s / 60))} Min.`;
  const fmtWhen = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

  function drawResponseChart(svg, series, hours) {
    const box = svg.parentElement;
    const W = Math.max(300, box.clientWidth), H = 180, P = { l: 44, r: 10, t: 10, b: 22 };
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = '';
    const NS = 'http://www.w3.org/2000/svg';
    const add = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); if (text) n.textContent = text; svg.appendChild(n); return n; };
    if (!series.length) { add('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'hd-empty' }, t('Noch keine Messwerte')); return; }
    const t0 = Math.floor(Date.now() / 1000) - hours * 3600, t1 = Math.floor(Date.now() / 1000);
    const vals = series.map(p => p.avg_ms).filter(v => v != null);
    const top = Math.max(10, ...vals) * 1.15;
    const x = ts => P.l + ((ts - t0) / (t1 - t0)) * (W - P.l - P.r);
    const y = v => H - P.b - (v / top) * (H - P.t - P.b);
    [0, 0.5, 1].forEach(f => {
      const v = top * f;
      add('line', { x1: P.l, x2: W - P.r, y1: y(v), y2: y(v), class: 'hd-grid' });
      add('text', { x: P.l - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'hd-axis' }, `${Math.round(v)} ms`);
    });
    // failed buckets as red columns
    const bw = Math.max(2, (W - P.l - P.r) / Math.max(1, (t1 - t0) / (series[1] ? series[1].ts - series[0].ts : 3600)));
    series.filter(p => p.up < 1).forEach(p => add('rect', { x: x(p.ts), y: P.t, width: bw, height: H - P.t - P.b, class: p.up === 0 ? 'hd-down' : 'hd-partial' }));
    let d = '';
    series.forEach(p => { if (p.avg_ms == null) return; d += `${d ? 'L' : 'M'}${x(p.ts).toFixed(1)},${y(p.avg_ms).toFixed(1)}`; });
    if (d) add('path', { d, class: 'hd-line' });
    // Few values (fresh check, long interval): show them as dots too
    if (vals.length <= 60) series.forEach(p => { if (p.avg_ms != null) add('circle', { cx: x(p.ts), cy: y(p.avg_ms), r: 2.5, class: 'hd-dot' }); });
    [t0, (t0 + t1) / 2, t1].forEach((ts, i) => add('text', { x: x(ts), y: H - 6, 'text-anchor': ['start', 'middle', 'end'][i], class: 'hd-axis' },
      new Date(ts * 1000).toLocaleString(LOCALE, hours > 48 ? { day: '2-digit', month: '2-digit' } : { hour: '2-digit', minute: '2-digit' })));
  }

  async function loadHealthDetail() {
    let d;
    try {
      const res = await fetch(`/api/health/${encodeURIComponent(detail.check.id)}/history?hours=${detail.hours}`);
      if (!res.ok) throw new Error();
      d = await res.json();
    } catch (e) { toast(t('Fehler beim Laden.'), 'error'); return; }
    const st = d.stats;
    const tiles = $('hd-stats');
    tiles.innerHTML = '';
    [['Verfügbarkeit', st.uptime != null ? `${st.uptime.toLocaleString(LOCALE)} %` : '–'], ['Ø Antwortzeit', st.avg_ms != null ? `${Math.round(st.avg_ms)} ms` : '–'],
     ['Schnellste', st.min_ms != null ? `${Math.round(st.min_ms)} ms` : '–'], ['Langsamste', st.max_ms != null ? `${Math.round(st.max_ms)} ms` : '–'],
     ['Vorfälle', String(d.incidents.length)]].forEach(([label, value]) => {
      const tile = el('div', 'bench-value');
      tile.append(el('span', 'label', t(label)), el('span', 'value', value));
      tiles.appendChild(tile);
    });
    drawResponseChart($('hd-chart').querySelector('svg'), d.series, d.hours);
    const table = $('hd-incidents');
    table.innerHTML = '';
    const head = table.createTHead().insertRow();
    ['Beginn', 'Dauer', 'Fehler', ''].forEach(h => head.appendChild(el('th', '', t(h))));
    const body = table.createTBody();
    if (!d.incidents.length) {
      const c = body.insertRow().insertCell();
      c.colSpan = 4;
      c.textContent = t('Keine Vorfälle im Zeitraum.');
    }
    d.incidents.forEach(i => {
      const row = body.insertRow();
      row.insertCell().textContent = fmtWhen(i.start);
      row.insertCell().textContent = i.ongoing ? `${t('seit')} ${fmtDur(i.duration)}` : fmtDur(i.duration);
      const err = row.insertCell();
      err.className = 'wrap';
      err.textContent = i.error || '–';
      const tags = row.insertCell();
      if (i.ongoing) tags.appendChild(el('span', 'health-tag critical', t('Läuft noch')));
      if (i.maintenance) tags.appendChild(el('span', 'health-tag', t('Wartung')));
    });
  }

  function openHealthDetail(c) {
    detail.check = c;
    $('hd-title').textContent = c.name;
    $('hd-sub').textContent = c.target || c.url || '';
    const m = $('health-modal');
    m.classList.add('open');
    m.setAttribute('aria-hidden', 'false');
    loadHealthDetail();
  }

  function closeHealthDetail() {
    const m = $('health-modal');
    m.classList.remove('open');
    m.setAttribute('aria-hidden', 'true');
  }

  function initHealthDetail() {
    if (!$('health-modal')) return;
    document.querySelectorAll('#hd-range button').forEach(b => b.addEventListener('click', () => {
      detail.hours = +b.dataset.hours;
      document.querySelectorAll('#hd-range button').forEach(x => x.classList.toggle('active', x === b));
      loadHealthDetail();
    }));
    $('hd-close').addEventListener('click', closeHealthDetail);
    $('health-modal').addEventListener('click', e => { if (e.target.id === 'health-modal') closeHealthDetail(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('health-modal').classList.contains('open')) closeHealthDetail(); });
  }

  function renderHealth(data) {
    const list = $('health-list');
    list.innerHTML = '';
    const checks = data.checks;
    const up = checks.filter(c => c.last && c.last.ok).length;
    $('health-count').textContent = checks.length ? `${up}/${checks.length}` : '0';
    $('health-count').classList.toggle('warn', checks.length > 0 && up < checks.length);
    checks.forEach(c => {
      const row = el('div', 'health-row');
      const head = el('div', 'health-row-head');
      const name = el(c.url ? 'a' : 'span', 'health-name', c.name);
      if (c.url) {
        name.href = c.url;
        name.target = '_blank';
        name.rel = 'noopener noreferrer';
      }
      name.title = c.target || c.url || '';
      name.setAttribute('data-no-i18n', '');
      row.classList.add('clickable');
      row.tabIndex = 0;
      row.title = t('Antwortzeiten & Vorfälle');
      row.addEventListener('click', e => { if (!e.target.closest('a')) openHealthDetail(c); });
      row.addEventListener('keydown', e => { if (e.key === 'Enter') openHealthDetail(c); });
      const ok = c.last && c.last.ok;
      const tag = el('span', `health-tag ${!c.last ? '' : ok ? 'good' : 'critical'}`,
        !c.last ? t('Noch nicht geprüft') : ok ? `${t('Erreichbar')}${c.last.ms != null ? ` · ${Math.round(c.last.ms)} ms` : ''}` : t('Fehler'));
      head.append(name, tag);
      row.appendChild(head);

      // 24h strip: one bar per 30 minutes
      const strip = el('div', 'health-strip');
      strip.setAttribute('role', 'img');
      strip.setAttribute('aria-label', `${c.name}: ${c.uptime ?? '–'}% (24 Std.)`);
      c.bars.forEach(v => strip.appendChild(el('span', `health-bar ${v == null ? 'none' : v >= 1 ? 'up' : v > 0 ? 'partial' : 'down'}`)));
      row.appendChild(strip);

      const sub = [];
      if (c.uptime != null) sub.push(`${String(c.uptime).replace('.', LOCALE.startsWith('de') ? ',' : '.')}% in 24 Std.`);
      if (c.avg_ms != null) sub.push(`Ø ${Math.round(c.avg_ms)} ms`);
      if (c.last && !c.last.ok && c.last.error) sub.push(c.last.error);
      else if (c.last_failure) sub.push(`Letzter Fehler ${new Date(c.last_failure.ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`);
      row.appendChild(el('span', 'health-sub', sub.join(' · ')));
      list.appendChild(row);
    });
    if (!checks.length) list.appendChild(el('p', 'notes-empty', t('Noch keine Dienste. Unter „Dienste verwalten“ z.B. Home Assistant oder das NAS hinzufügen.')));
  }

  async function loadHealth() {
    try {
      const res = await fetch('/api/health');
      if (res.ok) renderHealth(await res.json());
    } catch (e) { /* offline */ }
  }

  function initHealth() {
    if (!$('health-card')) return;
    loadHealth();
    setInterval(() => { if (!document.hidden) loadHealth(); }, 60 * 1000);
  }

  initActions();
  initHealth();
  initHealthDetail();
})();
