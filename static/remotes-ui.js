/**
 * HostCore Dashboard - settings section "Remote-Server": add, test, rename, remove agents.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Fehler ${res.status}`);
    return data;
  }

  function msg(text, level) {
    const m = $('remote-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  function render(items) {
    const box = $('remote-list');
    box.innerHTML = '';
    if (!items.length) {
      box.appendChild(el('p', 'field-hint', t('Noch keine Remote-Server.')));
      return;
    }
    const current = window.PiHosts && window.PiHosts.current && window.PiHosts.current();
    items.forEach(r => {
      const row = el('div', 'iface-row remote-row');
      const main = el('div', 'workload-main');
      const name = el('span', 'workload-name', r.name);
      name.setAttribute('data-no-i18n', '');
      const info = r.info || {};
      // e.g. "Debian GNU/Linux 13 (trixie) · VM x86_64 (QEMU Standard PC …)" or "… · Raspberry Pi 5 Model B"
      const kind = info.kind === 'pi' ? info.model : [{ vm: t('VM'), server: t('Server') }[info.kind], info.arch, info.model ? `(${info.model})` : ''].filter(Boolean).join(' ');
      const sub = el('span', 'workload-sub', [r.url, info.hostname, info.os, kind || info.model, info.commit ? `Version ${info.commit.slice(0, 7)}` : ''].filter(Boolean).join(' · '));
      sub.setAttribute('data-no-i18n', '');
      main.append(name, sub);
      if (r.online === false && r.error) main.appendChild(el('span', 'workload-sub remote-err', r.error));
      const side = el('div', 'iface-tags');
      side.appendChild(el('span', `health-tag ${r.online ? 'good' : r.online === false ? 'critical' : ''}`, r.online ? t('Erreichbar') : r.online === false ? t('Nicht erreichbar') : '–'));
      const open = el('button', 'btn-secondary', current && current.id === r.id ? t('Wird angezeigt') : t('Anzeigen'));
      open.type = 'button';
      open.disabled = !!(current && current.id === r.id);
      open.addEventListener('click', async () => {
        await api('/api/remotes/select', { id: r.id });
        location.href = '/';
      });
      const rename = el('button', 'btn-secondary', t('Umbenennen'));
      rename.type = 'button';
      rename.addEventListener('click', async () => {
        const n = window.prompt(t('Neuer Name'), r.name);
        if (!n || !n.trim()) return;
        try { await api(`/api/remotes/${r.id}/update`, { name: n.trim() }); load(); } catch (e) { msg(e.message, 'error'); }
      });
      const token = el('button', 'btn-secondary', t('Neuer Token'));
      token.type = 'button';
      token.addEventListener('click', async () => {
        const tk = window.prompt(t('Neuer Token (./agent.sh token auf dem Remote-Server)'), '');
        if (!tk || !tk.trim()) return;
        try { await api(`/api/remotes/${r.id}/update`, { token: tk.trim() }); msg(t('Token gespeichert.'), 'success'); load(); } catch (e) { msg(e.message, 'error'); }
      });
      const del = el('button', 'btn-danger', t('Entfernen'));
      del.type = 'button';
      del.addEventListener('click', async () => {
        if (!del.classList.contains('confirming')) {
          del.classList.add('confirming');
          del.textContent = t('Sicher?');
          setTimeout(() => { del.classList.remove('confirming'); del.textContent = t('Entfernen'); }, 4000);
          return;
        }
        try { render((await api(`/api/remotes/${r.id}/delete`, {})).remotes); } catch (e) { msg(e.message, 'error'); }
      });
      side.append(open, rename, token, del);
      row.append(main, side);
      box.appendChild(row);
    });
  }

  async function load() {
    try { render((await api('/api/remotes')).remotes); } catch (e) { msg(e.message, 'error'); }
  }

  const form = () => ({ name: $('remote-name').value.trim(), url: $('remote-url').value.trim(), token: $('remote-token').value.trim() });

  async function init() {
    if (!$('remote-list')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('users.manage')) return;
    load();
    $('remote-test').addEventListener('click', async () => {
      msg(t('Teste…'));
      try {
        const info = await api('/api/remotes/test', form());
        msg(`${t('Verbunden mit')} ${info.hostname}${info.model ? ` (${info.model})` : ''}.`, 'success');
      } catch (e) { msg(e.message, 'error'); }
    });
    $('remote-form').addEventListener('submit', async e => {
      e.preventDefault();
      msg(t('Verbinde…'));
      try {
        const r = await api('/api/remotes', form());
        msg(`${r.name} ${t('hinzugefügt – oben rechts lässt sich jetzt zwischen den Servern wechseln.')}`, 'success');
        $('remote-form').reset();
        load();
      } catch (err) { msg(err.message, 'error'); }
    });
  }

  init();
})();
