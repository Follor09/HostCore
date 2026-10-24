/**
 * HostCore Dashboard - settings section "E-Mail-Benachrichtigungen": SMTP connection, what is sent, test mails.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const WEEKDAYS = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'];

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  function msg(text, level) {
    const m = $('email-msg');
    m.className = `form-msg ${level || ''}`;
    m.textContent = text;
  }

  function options(sel, values) {
    values.forEach(([v, label]) => { const o = el('option', '', label); o.value = String(v); sel.appendChild(o); });
  }

  function show(st) {
    $('em-enabled').checked = st.enabled;
    $('em-host').value = st.host;
    $('em-port').value = st.port;
    $('em-security').value = st.security;
    $('em-insecure').checked = !!st.insecure;
    $('em-insecure').disabled = st.security === 'none';
    $('em-user').value = st.username;
    $('em-pass').value = '';
    $('em-pass').placeholder = st.has_password ? t('gespeichert – leer lassen zum Behalten') : '';
    $('em-sender').value = st.sender;
    $('em-sender-name').value = st.sender_name;
    $('em-to').value = st.recipients.join(', ');
    $('em-url').value = st.dashboard_url;
    $('em-level').value = st.events_level;
    $('em-daily').checked = st.daily;
    $('em-daily-hour').value = String(st.daily_hour);
    $('em-weekly').checked = st.weekly;
    $('em-weekly-day').value = String(st.weekly_day);
    $('em-weekly-hour').value = String(st.weekly_hour);
    $('em-approvals').checked = st.approvals;
    const tag = $('email-state');
    tag.className = `health-tag ${st.enabled ? (st.last_error ? 'warning' : 'good') : ''}`;
    tag.textContent = st.enabled ? (st.last_error ? t('Fehler beim Versand') : t('Aktiv')) : t('Aus');
    const info = $('email-info');
    info.innerHTML = '';
    const add = (k, v) => {
      if (!v) return;
      const dd = el('dd', '', v);
      dd.setAttribute('data-no-i18n', '');
      info.append(el('dt', '', t(k)), dd);
    };
    if (st.last_sent) add('Zuletzt gesendet', `${new Date(st.last_sent * 1000).toLocaleString(LOCALE)} · ${st.last_subject}`);
    if (st.sent_total) add('Gesendet insgesamt', String(st.sent_total));
    if (st.last_error) add('Letzter Fehler', `${new Date(st.last_error_ts * 1000).toLocaleString(LOCALE)} · ${st.last_error}`);
  }

  function payload() {
    return {
      enabled: $('em-enabled').checked, host: $('em-host').value.trim(), port: Number($('em-port').value) || 587,
      security: $('em-security').value, insecure: $('em-insecure').checked, username: $('em-user').value.trim(), password: $('em-pass').value,
      sender: $('em-sender').value.trim(), sender_name: $('em-sender-name').value.trim(), recipients: $('em-to').value,
      dashboard_url: $('em-url').value.trim(), events_level: $('em-level').value,
      daily: $('em-daily').checked, daily_hour: Number($('em-daily-hour').value),
      weekly: $('em-weekly').checked, weekly_day: Number($('em-weekly-day').value), weekly_hour: Number($('em-weekly-hour').value),
      approvals: $('em-approvals').checked
    };
  }

  async function init() {
    if (!$('email-form')) return;
    if (window.PiAuth) await window.PiAuth.ready;
    if (window.PiAuth && !window.PiAuth.can('settings.manage')) return;
    const hours = [...Array(24).keys()].map(h => [h, `${String(h).padStart(2, '0')}:00`]);
    options($('em-daily-hour'), hours);
    options($('em-weekly-hour'), hours);
    options($('em-weekly-day'), WEEKDAYS.map((d, i) => [i, t(d)]));
    $('em-security').addEventListener('change', () => {
      $('em-insecure').disabled = $('em-security').value === 'none';
      const port = $('em-port');
      if (['587', '465', '25'].includes(port.value)) port.value = { starttls: 587, ssl: 465, none: 25 }[$('em-security').value];
    });
    try { show(await api('/api/settings/email')); } catch (e) { msg(e.message, 'error'); }
    $('email-form').addEventListener('submit', async e => {
      e.preventDefault();
      try {
        show(await api('/api/settings/email', payload()));
        msg(t('Gespeichert.'), 'success');
      } catch (err) { msg(err.message, 'error'); }
    });
    $('email-form').querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', async () => {
      b.disabled = true;
      msg(t('Speichere und sende…'));
      try {
        await api('/api/settings/email', payload());
        show(await api('/api/settings/email/test', { kind: b.dataset.test }));
        msg(t('Gesendet – bitte im Postfach nachsehen.'), 'success');
      } catch (err) {
        msg(err.message, 'error');
        try { show(await api('/api/settings/email')); } catch (e2) { /* ignore */ }
      } finally { b.disabled = false; }
    }));
  }

  init();
})();
