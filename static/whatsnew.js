/**
 * HostCore Dashboard - "What's new": after an auto-update the dashboard shows once
 * which changes it brought (from the Git history). Also reachable from the footer
 * and the command palette.
 */
(function () {
  'use strict';

  const KEY = 'picore-seen-commit';
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  let modal = null;
  let data = null;

  const store = {
    get() { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* private mode */ } }
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  const fmtDate = ts => new Date(ts * 1000).toLocaleDateString(LOCALE, { day: 'numeric', month: 'long', year: 'numeric' });

  function build() {
    modal = el('div', 'modal-backdrop whatsnew-modal');
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = `
      <div class="modal-card sheet-card whatsnew-sheet" role="dialog" aria-modal="true" aria-labelledby="whatsnew-title">
        <div class="sheet-head">
          <div>
            <h3 id="whatsnew-title">Was ist neu</h3>
            <p class="card-subtitle" id="whatsnew-sub"></p>
          </div>
          <button class="mini-icon-btn" id="whatsnew-close" title="Schließen (Esc)">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"></path></svg>
          </button>
        </div>
        <div class="whatsnew-body" id="whatsnew-body"></div>
        <div class="form-actions">
          <button class="btn-primary" id="whatsnew-ok">Verstanden</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    const close = () => { modal.classList.remove('open'); modal.setAttribute('aria-hidden', 'true'); if (data) store.set(data.head); };
    modal.querySelector('#whatsnew-close').addEventListener('click', close);
    modal.querySelector('#whatsnew-ok').addEventListener('click', close);
    modal.addEventListener('click', e => { if (e.target === modal) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal.classList.contains('open')) close(); });
  }

  function commitCard(c) {
    const card = el('article', 'whatsnew-item');
    const head = el('div', 'whatsnew-item-head');
    const title = el('h4', '', c.title || c.subject);
    title.setAttribute('data-no-i18n', '');
    head.append(title, el('span', 'whatsnew-date', fmtDate(c.ts)));
    card.appendChild(head);
    if (c.points.length) {
      const ul = el('ul');
      ul.setAttribute('data-no-i18n', '');
      c.points.forEach(p => ul.appendChild(el('li', '', p)));
      card.appendChild(ul);
    } else if (c.title && c.title !== c.subject) {
      const p = el('p', '', c.subject);
      p.setAttribute('data-no-i18n', '');
      card.appendChild(p);
    }
    return card;
  }

  // Commits the viewer hasn't seen: everything newer than the stored commit
  function unseen(d, seen) {
    if (!seen) return [];
    const i = d.commits.findIndex(c => c.hash.startsWith(seen) || seen.startsWith(c.short));
    if (i === 0) return [];
    if (i > 0) return d.commits.slice(0, i);
    const last = d.last_update;
    return last ? d.commits.filter(c => last.commits.includes(c.hash)) : d.commits.slice(0, 1);
  }

  function render(fresh) {
    const body = modal.querySelector('#whatsnew-body');
    body.innerHTML = '';
    const last = data.last_update;
    modal.querySelector('#whatsnew-sub').textContent = last
      ? `${t('Letztes Update')}: ${fmtDate(last.ts)} · Version ${data.short}`
      : `Version ${data.short}`;
    if (fresh.length) {
      body.appendChild(el('h5', 'whatsnew-group', t('Seit deinem letzten Besuch')));
      fresh.forEach(c => body.appendChild(commitCard(c)));
    }
    const older = data.commits.filter(c => !fresh.includes(c)).slice(0, 40);
    if (older.length) {
      const det = el('details', 'whatsnew-older');
      if (!fresh.length) det.open = true;
      det.appendChild(el('summary', '', fresh.length ? t('Frühere Änderungen') : t('Letzte Änderungen')));
      older.forEach(c => det.appendChild(commitCard(c)));
      body.appendChild(det);
    }
    body.appendChild(el('p', 'field-hint', t('Die Beschreibungen stammen aus dem Git-Verlauf des Dashboards und sind auf Englisch.')));
  }

  async function load() {
    if (data) return data;
    const res = await fetch('/api/changelog');
    if (!res.ok) throw new Error(`Fehler ${res.status}`);
    data = await res.json();
    return data;
  }

  async function open(auto = false) {
    try {
      await load();
    } catch (e) { return; }
    if (!data.available) return;
    const seen = store.get();
    const fresh = unseen(data, seen);
    if (auto && !fresh.length) {
      if (!seen) store.set(data.head);   // first visit: nothing to announce
      return;
    }
    if (!modal) build();
    render(fresh);
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    modal.querySelector('#whatsnew-ok').focus();
  }

  window.PiWhatsNew = { open: () => open(false) };

  // Only the dashboard announces updates by itself
  if (location.pathname === '/' && !document.body.classList.contains('login-page')) {
    const start = () => setTimeout(() => open(true), 1500);
    if (window.PiAuth && window.PiAuth.ready) window.PiAuth.ready.then(start); else start();
  }
})();
