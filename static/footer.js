/**
 * PiCore Dashboard - footer with copyright, version and credits (every page but the terminal).
 */
(function () {
  'use strict';

  const OWNER = 'Follor09';   // copyright holder shown in the footer
  const CREDITS = [
    ['Wetterdaten: Open-Meteo.com (CC BY 4.0)', 'https://open-meteo.com/'],
    ['Web-Terminal: xterm.js (MIT)', 'https://xtermjs.org/'],
    ['Schriften: Inter, Outfit, JetBrains Mono (SIL OFL)', 'https://fonts.google.com/'],
    ['Länderdaten: DB-IP.com (CC BY 4.0)', 'https://db-ip.com/']
  ];

  function link(text, href) {
    const a = document.createElement('a');
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = text;
    return a;
  }

  function sep() {
    const s = document.createElement('span');
    s.className = 'footer-sep';
    s.setAttribute('aria-hidden', 'true');
    s.textContent = '·';
    return s;
  }

  function build(about) {
    const footer = document.createElement('footer');
    footer.className = 'site-footer';

    const main = document.createElement('div');
    main.className = 'footer-main';
    const copy = document.createElement('span');
    copy.textContent = `© ${about.year || new Date().getFullYear()} ${OWNER}`;
    copy.setAttribute('data-no-i18n', '');
    main.append(copy, sep(), document.createTextNode(about.name || 'PiCore Server'));
    if (about.commit) {
      const v = document.createElement('span');
      v.className = 'footer-version';
      v.textContent = `Version ${about.version ? `${about.version} · ` : ""}${about.commit}${about.date ? ` (${new Date(`${about.date}T12:00:00`).toLocaleDateString((window.I18N && window.I18N.locale) || 'de-DE')})` : ''}`;
      main.append(sep(), v);
    }
    if (window.PiWhatsNew && !document.body.classList.contains('login-page')) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'footer-link';
      b.textContent = 'Was ist neu';
      b.addEventListener('click', () => window.PiWhatsNew.open());
      main.append(sep(), b);
    }
    if (about.repo) main.append(sep(), link('Quellcode auf GitHub', about.repo));
    main.append(sep(), document.createTextNode('Alle Rechte vorbehalten.'));

    const credits = document.createElement('div');
    credits.className = 'footer-credits';
    CREDITS.forEach(([text, href], i) => {
      if (i) credits.appendChild(sep());
      credits.appendChild(link(text, href));
    });

    footer.append(main, credits);
    document.body.appendChild(footer);
  }

  fetch('/api/about')
    .then(r => (r.ok ? r.json() : {}))
    .catch(() => ({}))
    .then(build);
})();
