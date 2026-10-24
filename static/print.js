/**
 * HostCore Dashboard - print view for Markdown reports (/druck?src=...): renders the report with the
 * safe Markdown renderer, laid out for paper. Only the known report sources are accepted.
 */
(function () {
  'use strict';

  const SOURCES = {
    baseline: ['/api/compliance/baseline/markdown', '/nachweise#grundschutz'],
    vvt: ['/api/compliance/privacy/markdown?part=vvt', '/nachweise#datenschutz-vvt'],
    tom: ['/api/compliance/privacy/markdown?part=tom', '/nachweise#datenschutz-tom'],
    patch: ['/api/patch-sla/markdown', '/system#patch-fristen'],
    problems: ['/api/problems/markdown', '/doku#probleme']
  };
  const doc = document.getElementById('pr-doc');
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const src = SOURCES[new URLSearchParams(location.search).get('src')];
  if (!src) { doc.textContent = t('Unbekannter Bericht.'); return; }
  document.getElementById('pr-back').href = src[1];
  const md = document.getElementById('pr-md');
  md.href = src[0];
  md.download = '';
  document.getElementById('pr-print').addEventListener('click', () => window.print());
  fetch(src[0]).then(r => { if (!r.ok) throw new Error(String(r.status)); return r.text(); }).then(text => {
    doc.innerHTML = '';
    doc.appendChild(window.PiMarkdown.render(text));
    const h1 = doc.querySelector('h1');
    if (h1) document.title = `${h1.textContent} | HostCore Server`;
  }).catch(() => { doc.textContent = t('Laden fehlgeschlagen.'); });
})();
