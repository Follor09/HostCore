/**
 * PiCore Dashboard - combined live logs of several Docker containers on the containers
 * page: pick containers, lines are merged by time and colored per container, with text
 * filter, "errors only" and live follow (polling with since=last timestamp).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('live-logs')) return;
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const KEY = 'liveLogContainers';
  const MAX_LINES = 3000;
  const ERROR_RE = /\b(err(or)?|warn(ing)?|fail(ed|ure)?|fatal|crit(ical)?|exception|panic|traceback)\b/i;
  const state = { containers: [], selected: new Set(), lines: [], until: 0, timer: 0, busy: false, follow: true };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function hue(name) {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return h;
  }

  function saveSelection() {
    try { localStorage.setItem(KEY, JSON.stringify([...state.selected])); } catch (e) { /* per page only */ }
  }

  function renderChips() {
    const box = $('ll-chips');
    box.innerHTML = '';
    if (!state.containers.length) { box.appendChild(el('span', 'field-hint', t('Keine laufenden Container.'))); return; }
    state.containers.forEach(name => {
      const chip = el('button', `ll-chip${state.selected.has(name) ? ' on' : ''}`, name);
      chip.type = 'button';
      chip.setAttribute('data-no-i18n', '');
      chip.setAttribute('aria-pressed', String(state.selected.has(name)));
      chip.style.setProperty('--ll-h', hue(name));
      chip.addEventListener('click', () => {
        if (state.selected.has(name)) state.selected.delete(name); else state.selected.add(name);
        saveSelection();
        renderChips();
        restart();
      });
      box.appendChild(chip);
    });
    const all = el('button', 'text-btn ll-all', state.selected.size === state.containers.length ? t('Keine') : t('Alle'));
    all.type = 'button';
    all.addEventListener('click', () => {
      if (state.selected.size === state.containers.length) state.selected.clear();
      else state.containers.forEach(n => state.selected.add(n));
      saveSelection();
      renderChips();
      restart();
    });
    box.appendChild(all);
  }

  function visible(line) {
    const q = $('ll-q').value.trim().toLowerCase();
    if ($('ll-errors').checked && !ERROR_RE.test(line.text)) return false;
    return !q || line.text.toLowerCase().includes(q) || line.container.toLowerCase().includes(q);
  }

  function lineEl(line) {
    const row = el('div', `ll-line${ERROR_RE.test(line.text) ? ' ll-alert' : ''}`);
    const time = el('span', 'll-time', new Date(line.ts * 1000).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    const who = el('span', 'll-who', line.container);
    who.style.setProperty('--ll-h', hue(line.container));
    row.append(time, who, el('span', 'll-text', line.text));
    return row;
  }

  function render(appendOnly) {
    const out = $('ll-output');
    const atBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
    if (!appendOnly) out.innerHTML = '';
    const lines = appendOnly || state.lines;
    const frag = document.createDocumentFragment();
    lines.filter(visible).forEach(l => frag.appendChild(lineEl(l)));
    out.appendChild(frag);
    while (out.childElementCount > MAX_LINES) out.firstChild.remove();
    if (!out.childElementCount) out.appendChild(el('div', 'll-empty', state.selected.size ? t('Keine passenden Zeilen.') : t('Oben Container auswählen.')));
    else out.querySelector('.ll-empty')?.remove();
    if (atBottom || !appendOnly) out.scrollTop = out.scrollHeight;
    $('ll-state').textContent = state.selected.size ? `${state.selected.size} × Docker · ${state.lines.length} ${t('Zeilen')}${$('ll-live').checked ? ` · ${t('live')}` : ''}` : '';
  }

  async function fetchLines(initial) {
    if (!state.selected.size || state.busy) return;
    state.busy = true;
    const qs = new URLSearchParams({ names: [...state.selected].join(','), tail: $('ll-tail').value, since: initial ? 0 : state.until });
    try {
      const res = await fetch(`/api/containers/logs/multi?${qs}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
      if (initial) {
        state.lines = data.lines;
        render();
      } else if (data.lines.length) {
        state.lines = state.lines.concat(data.lines).slice(-MAX_LINES);
        render(data.lines);
      }
      if (data.until) state.until = data.until;
      $('ll-hint').textContent = '';
    } catch (err) {
      $('ll-hint').textContent = err.message;
    } finally {
      state.busy = false;
    }
  }

  function restart() {
    clearInterval(state.timer);
    state.lines = [];
    state.until = 0;
    render();
    if (!state.selected.size) return;
    fetchLines(true);
    if ($('ll-live').checked) state.timer = setInterval(() => { if (!document.hidden) fetchLines(false); }, 2000);
  }

  async function loadContainers() {
    try {
      const res = await fetch('/api/containers/overview');
      if (!res.ok) return;
      const d = await res.json();
      state.containers = (d.docker.containers || []).filter(c => c.state === 'running').map(c => c.name).sort();
    } catch (e) { return; }
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { /* none */ }
    state.selected = new Set(saved.filter(n => state.containers.includes(n)));
    renderChips();
    restart();
  }

  $('ll-q').addEventListener('input', () => render());
  $('ll-errors').addEventListener('change', () => render());
  $('ll-tail').addEventListener('change', restart);
  $('ll-live').addEventListener('change', restart);
  $('ll-clear').addEventListener('click', () => { state.lines = []; render(); });
  $('ll-form').addEventListener('submit', e => e.preventDefault());

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (!window.PiAuth || window.PiAuth.can('logs.view')) loadContainers();
  })();
})();
