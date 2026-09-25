/**
 * PiCore Dashboard - light / dark theme.
 * Loaded in <head> (not deferred) so the right theme is set before the first paint.
 * Choice is stored per browser: "auto" follows the device, or "light" / "dark".
 */
(function () {
  'use strict';

  const KEY = 'theme';
  const ACCENT_KEY = 'accent';
  const ACCENTS = ['cyan', 'green', 'violet', 'orange', 'pink', 'blue'];

  function accent() {
    try {
      const v = localStorage.getItem(ACCENT_KEY);
      return ACCENTS.includes(v) ? v : 'cyan';
    } catch (e) {
      return 'cyan';
    }
  }

  function applyAccent() {
    const a = accent();
    if (a === 'cyan') delete document.documentElement.dataset.accent;
    else document.documentElement.dataset.accent = a;
  }

  function setAccent(a) {
    try {
      if (a === 'cyan') localStorage.removeItem(ACCENT_KEY);
      else localStorage.setItem(ACCENT_KEY, a);
    } catch (e) { /* storage blocked: still switch for this page */ }
    applyAccent();
    window.dispatchEvent(new CustomEvent('themechange', { detail: { accent: a } }));
  }
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function choice() {
    try {
      const v = localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : 'auto';
    } catch (e) {
      return 'auto';
    }
  }

  function resolved(c = choice()) {
    return c === 'auto' ? (media && media.matches ? 'light' : 'dark') : c;
  }

  function apply() {
    const theme = resolved();
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === 'light' ? '#eef2f7' : '#07090e';
    window.dispatchEvent(new CustomEvent('themechange', { detail: { theme, choice: choice() } }));
  }

  function set(c) {
    try {
      if (c === 'auto') localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, c);
    } catch (e) { /* storage blocked: still switch for this page */ }
    apply();
  }

  // Header button: always a visible switch to the other look ("auto" is chosen in the settings)
  function cycle() {
    const next = resolved() === 'dark' ? 'light' : 'dark';
    set(next);
    return next;
  }

  if (media && media.addEventListener) media.addEventListener('change', () => { if (choice() === 'auto') apply(); });
  applyAccent();
  apply();
  document.addEventListener('DOMContentLoaded', () => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = resolved() === 'light' ? '#eef2f7' : '#07090e';
  });

  window.PiTheme = { choice, resolved, set, cycle, accent, setAccent, accents: ACCENTS };
})();
