/**
 * PiCore Dashboard - knowledge base (/wissen): page tree with folders, Markdown view and editor,
 * version history and attachments. The open page is in the URL (#<id>).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  let index = { pages: [], folders: [] };
  let current = null;     // open page (with content)
  let editing = null;     // page being edited, {} for a new one

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const n = el('div', `toast ${type}`, message);
    $('toast-container').appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (res.status === 401) { location.href = '/login'; throw new Error('Nicht angemeldet.'); }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.detail || `Fehler ${res.status}`);
    return json;
  }

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const wikiLink = title => {
    const p = index.pages.find(x => x.title.toLowerCase() === title.trim().toLowerCase());
    return p ? `#${p.id}` : null;
  };
  const md = text => window.PiMarkdown.render(text, { wikiLink });

  // ---------------------------------------------------------------------------
  // Tree
  // ---------------------------------------------------------------------------
  function renderTree() {
    const tree = $('wiki-tree');
    tree.innerHTML = '';
    const q = $('wiki-q').value.trim().toLowerCase();
    const list = index.pages.filter(p => !q || [p.title, p.folder].some(v => (v || '').toLowerCase().includes(q)));
    const groups = new Map();
    list.forEach(p => { const f = p.folder || ''; if (!groups.has(f)) groups.set(f, []); groups.get(f).push(p); });
    [...groups.keys()].sort((a, b) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b))).forEach(folder => {
      if (folder) {
        const h = el('div', 'wiki-folder', folder);
        h.setAttribute('data-no-i18n', '');
        tree.appendChild(h);
      }
      groups.get(folder).forEach(p => {
        const a = el('a', `wiki-link${current && current.id === p.id ? ' active' : ''}${folder ? ' nested' : ''}`, p.title);
        a.href = `#${p.id}`;
        a.setAttribute('data-no-i18n', '');
        tree.appendChild(a);
      });
    });
    if (!list.length) tree.appendChild(el('p', 'field-hint', index.pages.length ? t('Keine Treffer.') : t('Noch keine Seiten.')));
    $('wiki-folders').innerHTML = '';
    index.folders.forEach(f => { const o = el('option'); o.value = f; $('wiki-folders').appendChild(o); });
  }

  async function loadIndex() {
    index = await api('/api/wiki');
    renderTree();
  }

  // ---------------------------------------------------------------------------
  // View
  // ---------------------------------------------------------------------------
  function showView() {
    $('wiki-templates').hidden = true;
    $('wiki-empty').hidden = !!current;
    $('wiki-view').hidden = !current;
    $('wiki-form').hidden = true;
    if (!current) return;
    $('wiki-folder').textContent = current.folder || '';
    $('wiki-folder').setAttribute('data-no-i18n', '');
    $('wiki-title').textContent = current.title;
    $('wiki-title').setAttribute('data-no-i18n', '');
    $('wiki-meta').textContent = `${t('Geändert')} ${fmtTs(current.updated)}${current.user ? ` · ${current.user}` : ''}${current.versions.length ? ` · ${current.versions.length} ${t('frühere Versionen')}` : ''}`;
    const body = $('wiki-body');
    body.innerHTML = '';
    body.appendChild(current.content.trim() ? md(current.content) : el('p', 'field-hint', t('Diese Seite ist noch leer.')));
    $('wiki-panel').hidden = true;
    $('wiki-panel').innerHTML = '';
    document.title = `${current.title} | ${t('Wissen')} | PiCore Server`;
  }

  async function open(id) {
    if (!id) { current = null; showView(); renderTree(); return; }
    try {
      current = await api(`/api/wiki/${encodeURIComponent(id)}`);
    } catch (err) {
      toast(err.message, 'error');
      current = null;
    }
    showView();
    renderTree();
  }

  function panel(build) {
    const p = $('wiki-panel');
    if (!p.hidden && p.dataset.kind === build.name) { p.hidden = true; return; }
    p.innerHTML = '';
    p.dataset.kind = build.name;
    p.hidden = false;
    build(p);
  }

  function historyPanel(p) {
    if (!current.versions.length) { p.appendChild(el('p', 'field-hint', t('Noch keine früheren Versionen.'))); return; }
    current.versions.forEach(v => {
      const row = el('div', 'wiki-version');
      const label = el('span', '', `${fmtTs(v.ts)}${v.user ? ` · ${v.user}` : ''}${v.title !== current.title ? ` · „${v.title}“` : ''}`);
      label.setAttribute('data-no-i18n', '');
      const show = el('button', 'text-btn', t('Ansehen'));
      show.type = 'button';
      const restore = el('button', 'text-btn', t('Wiederherstellen'));
      restore.type = 'button';
      restore.hidden = !can('notes.edit');
      const box = el('div', 'wiki-body wiki-old');
      box.hidden = true;
      let old = null;
      const fetchOld = async () => old || (old = await api(`/api/wiki/${current.id}/version?ts=${v.ts}`));
      show.addEventListener('click', async () => {
        try { const o = await fetchOld(); box.innerHTML = ''; box.appendChild(md(o.content)); box.hidden = !box.hidden; } catch (err) { toast(err.message, 'error'); }
      });
      restore.addEventListener('click', async () => {
        try { const o = await fetchOld(); startEdit({ ...current, title: o.title, content: o.content }); toast(t('Alte Version im Editor – zum Übernehmen speichern.')); } catch (err) { toast(err.message, 'error'); }
      });
      row.append(label, show, restore);
      p.append(row, box);
    });
  }

  function attachPanel(p) {
    const base = `/api/docs/attachments/wiki/${encodeURIComponent(current.id)}`;
    const draw = files => {
      p.innerHTML = '';
      if (!files.length) p.appendChild(el('p', 'field-hint', t('Noch keine Anhänge.')));
      files.forEach(f => {
        const row = el('div', 'att-row');
        const a = el('a', 'att-name', f.name);
        a.href = `${base}/file?name=${encodeURIComponent(f.name)}`;
        a.target = '_blank';
        a.rel = 'noopener';
        a.setAttribute('data-no-i18n', '');
        const copy = el('button', 'text-btn', t('Als Link einfügen'));
        copy.type = 'button';
        copy.hidden = !can('notes.edit');
        copy.addEventListener('click', () => { startEdit(current); insertAtCursor(`[${f.name}](${a.getAttribute('href')})`); });
        const del = el('button', 'text-btn danger', t('Löschen'));
        del.type = 'button';
        del.hidden = !can('notes.edit');
        del.addEventListener('click', async () => {
          try { draw((await api(`${base}/delete?name=${encodeURIComponent(f.name)}`, {})).files); } catch (err) { toast(err.message, 'error'); }
        });
        row.append(a, el('span', 'att-meta', fmtTs(f.mtime)), copy, del);
        p.appendChild(row);
      });
      if (can('notes.edit')) {
        const label = el('label', 'att-upload');
        const input = el('input');
        input.type = 'file';
        input.multiple = true;
        input.hidden = true;
        label.append(input, el('span', 'btn-secondary', t('Dateien anhängen')), el('span', 'field-hint', t('PDF, Bilder, Text, ZIP – höchstens 25 MB')));
        input.addEventListener('change', async () => {
          for (const file of input.files) {
            try {
              const res = await fetch(`${base}?name=${encodeURIComponent(file.name)}`, { method: 'PUT', body: file });
              const data = await res.json().catch(() => ({}));
              if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
              draw(data.files);
            } catch (err) { toast(`${file.name}: ${err.message}`, 'error'); }
          }
          input.value = '';
        });
        p.appendChild(label);
      }
    };
    api(base).then(r => draw(r.files)).catch(err => p.appendChild(el('p', 'field-hint', err.message)));
  }

  // ---------------------------------------------------------------------------
  // Editor
  // ---------------------------------------------------------------------------
  function setMode(mode) {
    document.querySelectorAll('.wiki-mode button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
    $('wiki-f-content').hidden = mode !== 'write';
    $('wiki-preview').hidden = mode !== 'preview';
    if (mode === 'preview') { $('wiki-preview').innerHTML = ''; $('wiki-preview').appendChild(md($('wiki-f-content').value)); }
  }

  // ---------------------------------------------------------------------------
  // Templates for new pages
  // ---------------------------------------------------------------------------
  const fill = (text, vars) => text.replace(/\{\{\s*(titel|datum|benutzer)\s*\}\}/gi, (m, k) => (vars[k.toLowerCase()] ?? m));

  async function chooseTemplate() {
    let list = [];
    try { list = (await api('/api/wiki/templates')).templates; } catch (err) { toast(err.message, 'error'); }
    $('wiki-empty').hidden = true;
    $('wiki-view').hidden = true;
    $('wiki-form').hidden = true;
    $('wiki-templates').hidden = false;
    const grid = $('wiki-template-grid');
    grid.innerHTML = '';
    const vars = { datum: new Date().toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' }),
      benutzer: (window.PiAuth && window.PiAuth.user) || '' };
    [{ id: '', title: 'Leere Seite', description: 'Ohne Gliederung beginnen', content: '', folder: '' }, ...list].forEach(tpl => {
      const card = el('button', `wiki-template${tpl.own ? ' own' : ''}`);
      card.type = 'button';
      const name = el('span', 'wiki-template-name', tpl.own ? tpl.title : t(tpl.title));
      if (tpl.own) name.setAttribute('data-no-i18n', '');
      card.append(name, el('span', 'field-hint', t(tpl.description)));
      if (tpl.folder) {
        const f = el('span', 'wiki-template-folder', `${t('Ordner')}: ${tpl.folder}`);
        card.appendChild(f);
      }
      card.addEventListener('click', () => {
        startEdit({ title: '', folder: tpl.own ? '' : tpl.folder, content: fill(tpl.content || '', vars), template: true });
      });
      grid.appendChild(card);
    });
  }

  function startEdit(page) {
    editing = page || {};
    $('wiki-templates').hidden = true;
    $('wiki-empty').hidden = true;
    $('wiki-view').hidden = true;
    $('wiki-form').hidden = false;
    $('wiki-f-title').value = editing.title || '';
    $('wiki-f-folder').value = editing.folder || '';
    $('wiki-f-content').value = editing.content || '';
    $('wiki-msg').textContent = '';
    setMode('write');
    (editing.id ? $('wiki-f-content') : $('wiki-f-title')).focus();
  }

  function insertAtCursor(text) {
    const ta = $('wiki-f-content');
    const pos = ta.selectionStart || ta.value.length;
    ta.value = `${ta.value.slice(0, pos)}${text}${ta.value.slice(pos)}`;
    ta.focus();
  }

  async function save(e) {
    e.preventDefault();
    const body = { title: $('wiki-f-title').value.trim(), folder: $('wiki-f-folder').value.trim(), content: $('wiki-f-content').value };
    if (!editing.id && body.folder.split('/')[0] !== 'Vorlagen') body.content = fill(body.content, { titel: body.title });
    try {
      const page = editing.id ? await api(`/api/wiki/${editing.id}`, { ...body, base: editing.updated }) : await api('/api/wiki', body);
      editing = null;
      await loadIndex();
      if (location.hash !== `#${page.id}`) location.hash = page.id;
      else { current = page; showView(); renderTree(); }
      toast(t('Gespeichert.'), 'success');
    } catch (err) {
      $('wiki-msg').className = 'form-msg error';
      $('wiki-msg').textContent = err.message;
    }
  }

  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = '/login'; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }
    $('wiki-q').addEventListener('input', renderTree);
    $('wiki-new').addEventListener('click', chooseTemplate);
    $('wiki-tpl-cancel').addEventListener('click', showView);
    $('wiki-edit').addEventListener('click', () => startEdit(current));
    $('wiki-history').addEventListener('click', () => panel(historyPanel));
    $('wiki-attach').addEventListener('click', () => panel(attachPanel));
    $('wiki-cancel').addEventListener('click', () => { editing = null; showView(); });
    $('wiki-form').addEventListener('submit', save);
    document.querySelectorAll('.wiki-mode button').forEach(b => b.addEventListener('click', () => setMode(b.dataset.mode)));
    $('wiki-f-content').addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); $('wiki-form').requestSubmit(); }
      if (e.key === 'Tab') { e.preventDefault(); insertAtCursor('  '); }
    });
    const del = $('wiki-delete');
    del.addEventListener('click', async () => {
      if (!del.classList.contains('confirming')) {
        del.classList.add('confirming');
        del.textContent = t('Sicher?');
        setTimeout(() => { del.classList.remove('confirming'); del.textContent = t('Löschen'); }, 4000);
        return;
      }
      try {
        index = await api(`/api/wiki/${current.id}/delete`, {});
        current = null;
        history.replaceState(null, '', location.pathname);
        showView();
        renderTree();
        toast(t('Seite gelöscht.'), 'success');
      } catch (err) { toast(err.message, 'error'); }
    });
    window.addEventListener('hashchange', () => open(location.hash.slice(1)));
    try { await loadIndex(); } catch (err) { toast(err.message, 'error'); }
    open(location.hash.slice(1) || (index.pages[0] || {}).id);
  }

  init();
})();
