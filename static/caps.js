/**
 * PiCore Dashboard - what the hardware can do.
 * Elements with data-cap="<feature>" are left out when this Pi (or the selected remote Pi)
 * doesn't have it, e.g. a Pi 4 has no PMIC power readings and no fan curve.
 * Features: fan, fancurve, pmic, throttle, pi5. "a|b" means any of them.
 * Other scripts ask window.PiCaps.has('<feature>') or wait for PiCaps.ready.
 */
(function () {
  'use strict';

  let caps = null;   // null = unknown (older agent, error): show everything
  let resolveReady;
  const ready = new Promise(r => { resolveReady = r; });

  const has = cap => !caps || caps.has(cap);

  function apply(root = document) {
    root.querySelectorAll('[data-cap]').forEach(el => {
      el.classList.toggle('cap-off', !el.dataset.cap.split('|').some(c => has(c.trim())));
    });
  }

  fetch('/api/capabilities')
    .then(r => (r.ok ? r.json() : null))
    .catch(() => null)
    .then(d => {
      if (d && Array.isArray(d.caps)) caps = new Set(d.caps);
      const doIt = () => {
        apply();
        document.documentElement.classList.add('caps-ready');
        resolveReady({ has });
      };
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', doIt);
      else doIt();
    });

  window.PiCaps = { has, apply, ready };
})();
