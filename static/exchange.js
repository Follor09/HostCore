/**
 * HostCore Dashboard - public exchange page (/x/<token>): upload into and/or download from a folder.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const token = location.pathname.split('/').pop();
  const base = `/api/x/${encodeURIComponent(token)}`;
  let info = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const fmtSize = b => (b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GB` : b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.ceil(b / 1024))} KB`);
  const fmtTime = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

  function fail(message) {
    $('ex-error').textContent = message;
    $('ex-error').hidden = false;
    $('ex-upload').hidden = true;
    $('ex-download').hidden = true;
  }

  function renderFiles() {
    const list = $('ex-list');
    list.innerHTML = '';
    if (!info.files.length) list.appendChild(el('p', 'field-hint', t('Der Ordner ist leer.')));
    info.files.forEach(f => {
      const a = el('a', 'exchange-file');
      a.href = `${base}/file?name=${encodeURIComponent(f.name)}`;
      a.setAttribute('download', f.name);
      const name = el('span', 'exchange-file-name', f.name);
      name.setAttribute('data-no-i18n', '');
      a.append(name, el('span', 'exchange-file-meta', `${fmtSize(f.size)} · ${fmtTime(f.mtime)}`));
      list.appendChild(a);
    });
  }

  async function load() {
    let res;
    try { res = await fetch(base); } catch (e) { fail(t('Der Server ist gerade nicht erreichbar.')); return; }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { fail(data.detail || t('Link ungültig.')); return; }
    info = data;
    $('ex-title').textContent = info.name;
    $('ex-title').setAttribute('data-no-i18n', '');
    $('ex-sub').textContent = `${t('Gültig bis')} ${fmtTime(info.expires)}`;
    if (info.note) { $('ex-note').textContent = info.note; $('ex-note').setAttribute('data-no-i18n', ''); $('ex-note').hidden = false; }
    $('ex-upload').hidden = !info.upload;
    $('ex-download').hidden = !info.download;
    $('ex-limit').textContent = `${t('Noch frei:')} ${fmtSize(info.left_bytes)}`;
    if (info.download) renderFiles();
  }

  // One request per file (PUT with the raw body), so large files never sit in memory
  function uploadOne(file) {
    const row = el('div', 'exchange-up');
    const name = el('span', 'exchange-file-name', file.name);
    name.setAttribute('data-no-i18n', '');
    const state = el('span', 'exchange-file-meta', '0 %');
    const bar = el('div', 'progress-bar-bg mini');
    const fill = el('div', 'progress-bar-fill fill-emerald');
    fill.style.width = '0%';
    bar.appendChild(fill);
    row.append(name, state, bar);
    $('ex-progress').prepend(row);
    return new Promise(resolve => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', `${base}/upload?name=${encodeURIComponent(file.name)}`);
      xhr.upload.onprogress = e => {
        if (!e.lengthComputable) return;
        const p = Math.round((e.loaded / e.total) * 100);
        fill.style.width = `${p}%`;
        state.textContent = `${p} %`;
      };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
        if (xhr.status === 200) {
          state.textContent = data.name && data.name !== file.name ? `${t('Fertig – gespeichert als')} ${data.name}` : t('Fertig');
          row.classList.add('done');
        } else {
          state.textContent = data.detail || `${t('Fehler')} ${xhr.status}`;
          row.classList.add('failed');
          fill.className = 'progress-bar-fill danger';
        }
        resolve();
      };
      xhr.onerror = () => { state.textContent = t('Verbindung unterbrochen.'); row.classList.add('failed'); resolve(); };
      xhr.send(file);
    });
  }

  async function uploadAll(files) {
    for (const f of files) await uploadOne(f);  // one after another: the size limit stays exact
    load();
  }

  function init() {
    const drop = $('ex-drop');
    $('ex-files').addEventListener('change', e => { uploadAll([...e.target.files]); e.target.value = ''; });
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => uploadAll([...e.dataTransfer.files]));
    load();
  }

  init();
})();
