/**
 * PiCore Dashboard - notes card
 * Shared notes stored on the Pi (settings.json), so every device sees the same text.
 * View mode renders a small subset of Markdown: "# heading", "- item", "- [ ] / - [x]"
 * checklists (clickable) and links. Edit mode saves automatically while typing.
 * If another device saved in between, the server answers 409 and nothing is overwritten.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const state = { notes: null, editing: false, timer: null, saving: false };

  function setStatus(text, cls = '') {
    const el = $('notes-status');
    el.textContent = text;
    el.className = `card-subtitle notes-status ${cls}`;
  }

  function savedLabel(n) {
    if (!n || !n.updated) return 'Noch leer';
    const when = new Date(n.updated).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    return `${t('Gespeichert')} ${when}${n.user ? ` · ${n.user}` : ''}`;
  }

  // Text with clickable http(s) links; everything else stays plain text
  function appendInline(parent, text) {
    const re = /https?:\/\/[^\s<>"')]+/g;
    let pos = 0, m;
    while ((m = re.exec(text))) {
      parent.appendChild(document.createTextNode(text.slice(pos, m.index)));
      const a = document.createElement('a');
      a.href = m[0];
      a.textContent = m[0];
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      parent.appendChild(a);
      pos = m.index + m[0].length;
    }
    parent.appendChild(document.createTextNode(text.slice(pos)));
  }

  function render() {
    const view = $('notes-view');
    view.innerHTML = '';
    const text = (state.notes && state.notes.text) || '';
    if (!text.trim()) {
      const p = document.createElement('p');
      p.className = 'notes-empty';
      p.textContent = t('Noch keine Notizen. „Bearbeiten“ drücken – z.B. für To-dos im Homelab: - [ ] Backup testen');
      view.appendChild(p);
      return;
    }
    const lines = text.split('\n');
    lines.forEach((line, i) => {
      let m;
      if ((m = line.match(/^\s*[-*] \[( |x|X)\] (.*)$/))) {
        const label = document.createElement('label');
        label.className = `notes-check${m[1] !== ' ' ? ' done' : ''}`;
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = m[1] !== ' ';
        box.addEventListener('change', () => toggleLine(i, box.checked));
        const span = document.createElement('span');
        appendInline(span, m[2]);
        label.append(box, span);
        view.appendChild(label);
      } else if ((m = line.match(/^#{1,3} (.*)$/))) {
        const h = document.createElement('h4');
        h.className = 'notes-h';
        appendInline(h, m[1]);
        view.appendChild(h);
      } else if ((m = line.match(/^\s*[-*] (.*)$/))) {
        const li = document.createElement('div');
        li.className = 'notes-li';
        appendInline(li, m[1]);
        view.appendChild(li);
      } else if (!line.trim()) {
        const gap = document.createElement('div');
        gap.className = 'notes-gap';
        view.appendChild(gap);
      } else {
        const p = document.createElement('p');
        p.className = 'notes-p';
        appendInline(p, line);
        view.appendChild(p);
      }
    });
  }

  function toggleLine(index, checked) {
    const lines = state.notes.text.split('\n');
    lines[index] = lines[index].replace(/\[( |x|X)\]/, checked ? '[x]' : '[ ]');
    save(lines.join('\n'));
  }

  async function load() {
    if (state.editing || state.saving) return;
    try {
      const res = await fetch('/api/notes');
      if (res.status === 401) {
        $('notes-edit').hidden = true;
        $('notes-view').innerHTML = '';
        const p = document.createElement('p');
        p.className = 'notes-empty';
        p.textContent = t('Notizen sind nur nach der Anmeldung sichtbar.');
        $('notes-view').appendChild(p);
        setStatus('');
        return;
      }
      state.notes = await res.json();
      render();
      setStatus(savedLabel(state.notes));
    } catch (e) { /* offline: keep what is shown */ }
  }

  async function save(text) {
    state.saving = true;
    setStatus(t('Speichert…'));
    try {
      const res = await fetch('/api/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, base: (state.notes && state.notes.updated) || 0 })
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 409) {
        conflict(data.notes, text);
        return;
      }
      if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
      state.notes = data;
      setStatus(savedLabel(data), 'ok');
      if (!state.editing) render();
    } catch (err) {
      setStatus(err.message, 'error');
    } finally {
      state.saving = false;
    }
  }

  // Another device saved in between: let the user pick, never overwrite silently
  function conflict(theirs, mine) {
    const box = $('notes-conflict');
    box.hidden = false;
    setStatus(t('Woanders geändert'), 'error');
    $('notes-take-theirs').onclick = () => {
      state.notes = theirs;
      box.hidden = true;
      if (state.editing) $('notes-text').value = theirs.text;
      render();
      setStatus(savedLabel(theirs));
    };
    $('notes-keep-mine').onclick = () => {
      state.notes = theirs;  // base on their version, then write ours on top
      box.hidden = true;
      save(mine);
    };
  }

  function setEditing(on) {
    state.editing = on;
    const area = $('notes-text');
    area.hidden = !on;
    $('notes-view').hidden = on;
    $('notes-edit').textContent = t(on ? 'Fertig' : 'Bearbeiten');
    $('notes-edit').setAttribute('aria-pressed', String(on));
    $('notes-help').hidden = !on;
    if (on) {
      area.value = (state.notes && state.notes.text) || '';
      area.focus();
    } else {
      clearTimeout(state.timer);
      if (state.notes && area.value !== state.notes.text) save(area.value);
      else render();
    }
  }

  function init() {
    if (!$('notes-card')) return;
    $('notes-edit').addEventListener('click', () => setEditing(!state.editing));
    $('notes-text').addEventListener('input', () => {
      clearTimeout(state.timer);
      setStatus(t('Nicht gespeichert'));
      state.timer = setTimeout(() => save($('notes-text').value), 1000);
    });
    load();
    setInterval(() => { if (!document.hidden) load(); }, 60 * 1000);
  }

  init();
})();
