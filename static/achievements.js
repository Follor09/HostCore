/**
 * PiCore Dashboard - achievements: dashboard card, trophy sheet and the pop-up
 * (with confetti) when a new badge was unlocked since this browser last looked.
 */
(function () {
  'use strict';

  const card = document.getElementById('achievements-card');
  if (!card) return;
  const $ = id => document.getElementById(id);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const SEEN_KEY = 'achievementsSeen';
  const TIER = { bronze: 'Bronze', silver: 'Silber', gold: 'Gold' };
  let data = null;
  const queue = [];

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function medal(b, size = '') {
    const m = el('span', `ach-medal tier-${b.tier}${b.unlocked ? '' : ' locked'} ${size}`, b.icon);
    m.setAttribute('aria-hidden', 'true');
    m.setAttribute('data-no-i18n', '');
    return m;
  }

  const fmtDate = ts => new Date(ts * 1000).toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
  const fmtNum = v => Number(v).toLocaleString(LOCALE, { maximumFractionDigits: 1 });

  // ---------------------------------------------------------------------------
  // Card
  // ---------------------------------------------------------------------------
  function renderCard() {
    $('ach-sub').textContent = `${data.unlocked} ${t('von')} ${data.total} ${t('freigeschaltet')}`;
    $('ach-score').textContent = `${data.score} P`;
    $('ach-fill').style.width = `${(data.unlocked / data.total) * 100}%`;
    const recent = $('ach-recent');
    recent.innerHTML = '';
    const byId = Object.fromEntries(data.badges.map(b => [b.id, b]));
    const list = data.recent.map(id => byId[id]);
    // Fill up with the badges closest to being unlocked
    const next = data.badges.filter(b => !b.unlocked && b.progress != null).sort((a, b) => b.progress - a.progress);
    list.concat(next).slice(0, 3).forEach(b => {
      const row = el('button', `ach-row${b.unlocked ? '' : ' next'}`);
      row.type = 'button';
      row.appendChild(medal(b));
      const text = el('span', 'ach-row-text');
      text.append(el('b', '', t(b.title)), el('small', '', b.unlocked ? `${t(TIER[b.tier])} · ${fmtDate(b.unlocked)}` : `${t('Als Nächstes')}: ${t(b.description)}`));
      row.appendChild(text);
      if (!b.unlocked) row.appendChild(progressBar(b));
      row.addEventListener('click', openSheet);
      recent.appendChild(row);
    });
  }

  function progressBar(b) {
    const wrap = el('span', 'ach-bar');
    const fill = el('i');
    fill.style.width = `${Math.round((b.progress || 0) * 100)}%`;
    wrap.appendChild(fill);
    wrap.title = b.target > 1 && b.value != null ? `${fmtNum(Math.min(b.value, b.target))} / ${fmtNum(b.target)}` : `${Math.round((b.progress || 0) * 100)} %`;
    return wrap;
  }

  // ---------------------------------------------------------------------------
  // Trophy sheet
  // ---------------------------------------------------------------------------
  function renderSheet() {
    const body = $('ach-grid');
    body.innerHTML = '';
    $('ach-sheet-sub').textContent = `${data.unlocked} / ${data.total} · ${data.score} ${t('von')} ${data.max_score} ${t('Punkten')}`;
    const cats = [...new Set(data.badges.map(b => b.category))];
    cats.forEach(cat => {
      const sec = el('section', 'ach-cat');
      sec.appendChild(el('h4', 'ach-cat-title', t(cat)));
      const grid = el('div', 'ach-cat-grid');
      data.badges.filter(b => b.category === cat).forEach(b => {
        const tile = el('div', `ach-tile${b.unlocked ? ' unlocked' : ''}`);
        tile.appendChild(medal(b, 'big'));
        tile.appendChild(el('b', 'ach-tile-title', t(b.title)));
        tile.appendChild(el('span', 'ach-tile-desc', t(b.description)));
        if (b.unlocked) tile.appendChild(el('span', `ach-tile-meta tier-${b.tier}`, `${t(TIER[b.tier])} · ${fmtDate(b.unlocked)}`));
        else tile.appendChild(progressBar(b));
        grid.appendChild(tile);
      });
      sec.appendChild(grid);
      body.appendChild(sec);
    });
  }

  function openSheet() {
    if (!data) return;
    renderSheet();
    const m = $('achievements-modal');
    m.classList.add('open');
    m.setAttribute('aria-hidden', 'false');
  }

  function closeSheet() {
    const m = $('achievements-modal');
    m.classList.remove('open');
    m.setAttribute('aria-hidden', 'true');
  }

  // ---------------------------------------------------------------------------
  // Pop-up for new badges
  // ---------------------------------------------------------------------------
  function seen() {
    try { return JSON.parse(localStorage.getItem(SEEN_KEY) || 'null'); } catch (e) { return null; }
  }

  function markSeen(ids) {
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(ids)); } catch (e) { /* storage blocked: pop-ups may repeat */ }
  }

  function confetti(box) {
    const colors = ['#00f0ff', '#00ffa3', '#ffb703', '#ff2a5f', '#a855f7', '#ffffff'];
    for (let i = 0; i < 40; i++) {
      const c = el('i', 'ach-confetti');
      c.style.setProperty('--x', `${Math.random() * 100}%`);
      c.style.setProperty('--dx', `${(Math.random() - 0.5) * 160}px`);
      c.style.setProperty('--r', `${Math.random() * 720 - 360}deg`);
      c.style.setProperty('--d', `${1.4 + Math.random() * 1.2}s`);
      c.style.setProperty('--delay', `${Math.random() * 0.3}s`);
      c.style.background = colors[i % colors.length];
      box.appendChild(c);
    }
  }

  function showNext() {
    if (document.querySelector('.ach-popup') || !queue.length) return;
    const item = queue.shift();
    const pop = el('div', 'ach-popup');
    pop.setAttribute('role', 'status');
    const inner = el('div', 'ach-popup-card');
    confetti(inner);
    if (item.summary) {
      inner.appendChild(el('span', 'ach-medal tier-gold big', '🏆'));
      inner.appendChild(el('span', 'ach-popup-kicker', t('Willkommen bei den Erfolgen!')));
      inner.appendChild(el('b', 'ach-popup-title', `${item.count} ${item.count === 1 ? t('Erfolg') : t('Erfolge')} ${t('schon freigeschaltet')}`));
      inner.appendChild(el('span', 'ach-popup-desc', t('Dein Homelab sammelt ab jetzt Trophäen.')));
    } else {
      const b = item.badge;
      inner.appendChild(medal(b, 'big'));
      inner.appendChild(el('span', 'ach-popup-kicker', `${t('Erfolg freigeschaltet')} · ${t(TIER[b.tier])}`));
      inner.appendChild(el('b', 'ach-popup-title', t(b.title)));
      inner.appendChild(el('span', 'ach-popup-desc', t(b.description)));
    }
    const btns = el('div', 'ach-popup-actions');
    const more = el('button', 'btn-secondary', t('Alle Trophäen'));
    const ok = el('button', 'btn-primary', t('Super!'));
    btns.append(more, ok);
    inner.appendChild(btns);
    pop.appendChild(inner);
    document.body.appendChild(pop);
    requestAnimationFrame(() => pop.classList.add('show'));
    const close = () => {
      pop.classList.remove('show');
      setTimeout(() => { pop.remove(); showNext(); }, 250);
    };
    ok.addEventListener('click', close);
    more.addEventListener('click', () => { close(); openSheet(); });
    pop.addEventListener('click', e => { if (e.target === pop) close(); });
    setTimeout(() => ok.focus(), 50);
  }

  function announce() {
    const unlocked = data.badges.filter(b => b.unlocked);
    const ids = unlocked.map(b => b.id);
    const before = seen();
    if (before === null) {
      if (unlocked.length) queue.push({ summary: true, count: unlocked.length });
    } else {
      unlocked.filter(b => !before.includes(b.id)).sort((a, b) => a.unlocked - b.unlocked).forEach(b => queue.push({ badge: b }));
    }
    markSeen(ids);
    showNext();
  }

  // ---------------------------------------------------------------------------
  async function load() {
    try {
      const res = await fetch('/api/achievements');
      if (res.status === 401) {
        card.classList.add('ach-locked');
        $('ach-sub').textContent = t('Nur nach der Anmeldung');
        return;
      }
      if (!res.ok) return;
      data = await res.json();
      card.classList.remove('ach-locked');
      renderCard();
      if ($('achievements-modal').classList.contains('open')) renderSheet();
      // Badges of a remote Pi belong to that Pi: no pop-ups for them
      if (!document.body.classList.contains('remote-host')) announce();
    } catch (e) { /* offline */ }
  }

  $('ach-open').addEventListener('click', openSheet);
  $('ach-close').addEventListener('click', closeSheet);
  $('achievements-modal').addEventListener('click', e => { if (e.target.id === 'achievements-modal') closeSheet(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('achievements-modal').classList.contains('open')) closeSheet(); });

  window.PiAchievements = { open: openSheet };
  load();
  setInterval(() => { if (!document.hidden) load(); }, 5 * 60 * 1000);
})();
