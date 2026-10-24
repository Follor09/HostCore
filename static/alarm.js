/**
 * HostCore Dashboard - page /alarm: acknowledge an alarm with the link from the alarm mail
 * (no login - the link is the permission). The token is removed from the address bar right away.
 * Acknowledging needs a click, so mail scanners that open links do not acknowledge by accident.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const LEVEL = { critical: 'Kritisch', warning: 'Warnung', info: 'Hinweis' };
  const token = new URLSearchParams(location.search).get('token') || '';
  history.replaceState(null, '', '/alarm');
  const fmtTs = ts => new Date(ts * 1000).toLocaleString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

  function error(msg) {
    $('alarm-error').textContent = msg;
    $('alarm-error').hidden = !msg;
  }

  function showAlarm(a) {
    $('alarm-box').hidden = false;
    $('alarm-level').className = `health-tag ${a.level}`;
    $('alarm-level').textContent = t(LEVEL[a.level] || a.level);
    $('alarm-title').textContent = a.title;
    $('alarm-detail').textContent = a.detail || '';
    $('alarm-when').textContent = `${t('Ausgelöst')} ${fmtTs(a.created)}`;
  }

  fetch(`/api/alarm?token=${encodeURIComponent(token)}`).then(r => r.json()).then(d => {
    if (!d.valid) {
      $('alarm-sub').textContent = t('Der Link ist ungültig oder der Alarm ist schon erledigt.');
      return;
    }
    showAlarm(d.alert);
    if (d.alert.status === 'acked') {
      $('alarm-sub').textContent = `${t('Schon quittiert von')} ${d.alert.acked_by} · ${fmtTs(d.alert.acked_at)}`;
      return;
    }
    $('alarm-sub').textContent = `${t('Für')} ${d.recipient}`;
    $('alarm-fields').hidden = false;
  }).catch(() => { $('alarm-sub').textContent = t('Nicht erreichbar.'); });

  $('alarm-form').addEventListener('submit', async e => {
    e.preventDefault();
    error('');
    $('alarm-submit').disabled = true;
    try {
      const res = await fetch('/api/alarm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, note: $('alarm-note').value }) });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.detail || t('Quittieren fehlgeschlagen.'));
      $('alarm-fields').hidden = true;
      $('alarm-sub').textContent = `${t('Quittiert')} · ${fmtTs(d.acked_at)}`;
    } catch (err) {
      error(err.message);
      $('alarm-submit').disabled = false;
    }
  });
})();
