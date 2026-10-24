/**
 * HostCore Dashboard - documentation section "Änderungen": recent changes and deleted entries of
 * inventory, contracts, commands, journal, runbooks, service docs and wiki pages, with all
 * versions of an entry, comparison with the version before and restore.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const ACTION = { create: ['good', 'Neu'], update: ['', 'Geändert'], delete: ['critical', 'Gelöscht'], original: ['', 'Ursprung'],
    restore: ['warning', 'Wiederhergestellt'] };
  const KINDS = { inventory: 'Inventar', contracts: 'Verträge & Abos', snippets: 'Befehle', journal: 'Betriebstagebuch',
    runbooks: 'Runbooks', services: 'Steckbriefe', wiki: 'Wissen' };
  const open = new Set();

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

  function button(label, cls, fn) {
    const b = el('button', cls, t(label));
    b.type = 'button';
    b.addEventListener('click', () => fn(b));
    return b;
  }

  function restoreButton(versionId, label, after) {
    return button(label, 'text-btn', async b => {
      if (!b.classList.contains('confirming')) {
        b.classList.add('confirming');
        b.textContent = t('Sicher?');
        setTimeout(() => { b.classList.remove('confirming'); b.textContent = t(label); }, 4000);
        return;
      }
      b.disabled = true;
      try { await api(`/api/versions/${versionId}/restore`, {}); toast(t('Wiederhergestellt.'), 'success'); after(); }
      catch (err) { toast(err.message, 'error'); b.disabled = false; }
    });
  }

  // All versions of one entry, each compared with the one before
  async function versionsPanel(kind, itemId) {
    const box = el('div', 'ver-panel');
    box.appendChild(el('p', 'field-hint', t('Lade…')));
    try {
      const d = await api(`/api/versions?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(itemId)}`);
      box.innerHTML = '';
      if (!d.versions.length) { box.appendChild(el('p', 'field-hint', t('Keine Versionen.'))); return box; }
      d.versions.forEach((v, i) => {
        const older = d.versions[i + 1];
        const row = el('div', 'ver-version');
        const head = el('div', 'ver-head');
        const [cls, label] = ACTION[v.action] || ACTION.update;
        head.append(el('span', `health-tag ${cls}`, t(label)), raw('span', 'ver-when', `${fmtTs(v.ts)}${v.user ? ` · ${v.user}` : ''}`));
        const diffBox = el('div', 'ver-diff');
        diffBox.hidden = true;
        if (v.action !== 'original') {
          head.appendChild(button(older ? 'Vergleichen' : 'Inhalt', 'text-btn', () => {
            if (diffBox.hidden && !diffBox.childNodes.length) {
              diffBox.appendChild(v.action === 'delete' ? window.PiDiff.renderFields(v.data, {}) : window.PiDiff.renderFields(older ? older.data : {}, v.data));
            }
            diffBox.hidden = !diffBox.hidden;
          }));
        }
        const isCurrent = i === 0 && v.action !== 'delete' && d.current;
        if (can('notes.edit') && !isCurrent) head.appendChild(restoreButton(v.id, 'Diese Fassung wiederherstellen', reloadAll));
        row.append(head, diffBox);
        box.appendChild(row);
      });
    } catch (err) {
      box.innerHTML = '';
      box.appendChild(el('p', 'form-msg error', err.message));
    }
    return box;
  }

  function itemRow(item, extra) {
    const key = `${item.kind}|${item.item_id}`;
    const row = el('div', 'ver-row');
    const head = el('div', 'ver-head');
    const [cls, label] = ACTION[item.action || 'delete'] || ACTION.update;
    head.append(el('span', `health-tag ${cls}`, t(label)), el('span', 'ver-kind', t(KINDS[item.kind] || item.kind)),
      raw('strong', 'ver-title', item.title || '–'), raw('span', 'ver-when', `${fmtTs(item.ts)}${item.user ? ` · ${item.user}` : ''}`));
    const panel = el('div');
    head.appendChild(button(open.has(key) ? 'Zuklappen' : 'Versionen', 'text-btn', async b => {
      if (open.has(key)) { open.delete(key); panel.innerHTML = ''; b.textContent = t('Versionen'); return; }
      open.add(key);
      b.textContent = t('Zuklappen');
      panel.appendChild(await versionsPanel(item.kind, item.item_id));
    }));
    if (extra) head.appendChild(extra);
    row.append(head, panel);
    if (open.has(key)) versionsPanel(item.kind, item.item_id).then(p => panel.appendChild(p));
    return row;
  }

  async function reloadAll() {
    const kind = $('ver-kind').value;
    const [rc, dl] = await Promise.allSettled([api('/api/versions/recent?limit=100'), api(`/api/versions/deleted?kind=${encodeURIComponent(kind)}`)]);
    const del = $('ver-deleted');
    del.innerHTML = '';
    const deleted = dl.status === 'fulfilled' ? dl.value.items : [];
    if (!deleted.length) del.appendChild(el('p', 'field-hint', t('Nichts gelöscht.')));
    deleted.forEach(d => del.appendChild(itemRow({ ...d, action: 'delete' }, can('notes.edit') ? restoreButton(d.version, 'Wiederherstellen', reloadAll) : null)));
    const rec = $('ver-recent');
    rec.innerHTML = '';
    let items = rc.status === 'fulfilled' ? rc.value.items : [];
    if (kind) items = items.filter(i => i.kind === kind);
    // one row per entry: its newest change
    const seen = new Set();
    items = items.filter(i => { const k = `${i.kind}|${i.item_id}`; if (seen.has(k)) return false; seen.add(k); return true; });
    if (!items.length) rec.appendChild(el('p', 'field-hint', t('Noch keine Änderungen aufgezeichnet.')));
    items.forEach(i => rec.appendChild(itemRow(i)));
  }

  async function init() {
    if (!$('aenderungen')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    const sel = $('ver-kind');
    Object.entries(KINDS).forEach(([k, v]) => { const o = el('option', '', t(v)); o.value = k; sel.appendChild(o); });
    sel.addEventListener('change', reloadAll);
    reloadAll();
  }

  init();
})();
