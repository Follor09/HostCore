/**
 * HostCore Dashboard - API documentation (/api-doku): every endpoint grouped, with parameters,
 * permission and "Ausprobieren" for reading requests (GET, with the own session).
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  let data = null;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  function tryPanel(ep) {
    const box = el('div', 'apidoc-try');
    const inputs = {};
    ep.params.forEach(p => {
      const l = el('label', 'apidoc-param');
      l.appendChild(raw('span', 'mono', `${p.name}${p.required ? ' *' : ''}`));
      const i = el('input');
      i.type = 'text';
      i.placeholder = p.default != null ? String(p.default) : p.type;
      i.spellcheck = false;
      inputs[p.name] = { input: i, where: p.in };
      l.appendChild(i);
      box.appendChild(l);
    });
    const go = el('button', 'btn-secondary', t('Ausprobieren'));
    go.type = 'button';
    const out = raw('pre', 'fleet-log-pre apidoc-out');
    out.hidden = true;
    go.addEventListener('click', async () => {
      let path = ep.path;
      const q = new URLSearchParams();
      for (const [name, { input, where }] of Object.entries(inputs)) {
        const v = input.value.trim();
        if (where === 'path') path = path.replace(`{${name}}`, encodeURIComponent(v));
        else if (v) q.set(name, v);
      }
      if (/\{[^}]+\}/.test(path)) { out.hidden = false; out.textContent = t('Bitte alle Pfad-Parameter ausfüllen.'); return; }
      go.disabled = true;
      out.hidden = false;
      out.textContent = t('Lade…');
      const url = path + (q.toString() ? `?${q}` : '');
      try {
        const res = await fetch(url);
        const text = await res.text();
        let shown = text;
        try { shown = JSON.stringify(JSON.parse(text), null, 2); } catch (e) { /* not JSON */ }
        out.textContent = `GET ${url}\n${res.status} ${res.statusText}\n\n${shown.slice(0, 60000)}${shown.length > 60000 ? '\n…' : ''}`;
      } catch (err) { out.textContent = err.message; }
      finally { go.disabled = false; }
    });
    box.append(go, out);
    return box;
  }

  function entry(ep) {
    const det = el('details', 'apidoc-entry');
    const sum = el('summary');
    sum.append(el('span', `apidoc-method ${ep.method.toLowerCase()}`, ep.method), raw('code', 'apidoc-path', ep.path));
    if (ep.summary) sum.appendChild(raw('span', 'apidoc-summary', ep.summary));
    det.appendChild(sum);
    const meta = el('div', 'apidoc-meta');
    meta.appendChild(el('span', 'health-tag', t(ep.permission_label || 'Angemeldet')));
    meta.appendChild(el('span', 'field-hint', t(ep.local_only ? 'immer dieser Server' : 'wird an den gewählten Remote-Server weitergeleitet')));
    det.appendChild(meta);
    if (ep.params.length) {
      const tb = el('table', 'data-table apidoc-table');
      const h = tb.createTHead().insertRow();
      ['Parameter', 'Ort', 'Typ', 'Standard'].forEach(x => h.appendChild(el('th', '', t(x))));
      const b = tb.createTBody();
      ep.params.forEach(p => {
        const r = b.insertRow();
        r.insertCell().appendChild(raw('code', '', `${p.name}${p.required ? ' *' : ''}`));
        r.insertCell().textContent = t(p.in === 'path' ? 'Pfad' : p.in === 'query' ? 'Adresse (?…)' : p.in);
        r.insertCell().appendChild(raw('span', 'mono', p.type));
        r.insertCell().appendChild(raw('span', 'mono', p.default != null ? String(p.default) : '–'));
      });
      det.appendChild(tb);
    }
    if (ep.body) {
      det.appendChild(el('p', 'field-hint', t('JSON im Body:')));
      det.appendChild(raw('code', 'apidoc-body', ep.body.length
        ? `{ ${ep.body.map(b => `"${b.name}": ${b.type}${b.required ? '' : '?'}`).join(', ')} }` : '{ … }'));
    }
    if (ep.method === 'GET') {
      let built = false;
      det.addEventListener('toggle', () => { if (det.open && !built) { built = true; det.appendChild(tryPanel(ep)); } });
    }
    return det;
  }

  function render() {
    const q = $('apidoc-q').value.trim().toLowerCase();
    const g = $('apidoc-group').value, m = $('apidoc-method').value;
    const list = data.endpoints.filter(e => (!g || e.group === g) && (!m || e.method === m)
      && (!q || [e.path, e.summary, e.permission_label, e.group].some(v => (v || '').toLowerCase().includes(q))));
    $('apidoc-count').textContent = t(`${list.length} von ${data.count} Schnittstellen`);
    const box = $('apidoc-groups');
    box.innerHTML = '';
    data.groups.forEach(group => {
      const items = list.filter(e => e.group === group);
      if (!items.length) return;
      const sec = el('section', 'glass-card settings-section apidoc-group');
      sec.appendChild(el('h2', '', t(group)));
      items.forEach(e => sec.appendChild(entry(e)));
      box.appendChild(sec);
    });
  }

  async function init() {
    if (window.PiAuth) await window.PiAuth.ready;
    try {
      const status = await (await fetch('/api/auth/status')).json();
      if (!status.user) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}${location.hash}`; return; }
      $('user-name').textContent = status.user;
      $('user-chip').hidden = false;
    } catch (e) { /* ignore */ }
    const res = await fetch('/api/api-docs');
    data = await res.json();
    data.groups.forEach(g => { const o = el('option', '', t(g)); o.value = g; $('apidoc-group').appendChild(o); });
    let timer;
    $('apidoc-q').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(render, 150); });
    ['apidoc-group', 'apidoc-method'].forEach(id => $(id).addEventListener('change', render));
    $('apidoc-filters').addEventListener('submit', e => e.preventDefault());
    render();
  }

  init();
})();
