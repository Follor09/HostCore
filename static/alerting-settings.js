/**
 * PiCore Dashboard - settings section "Alarmierung": which events become alarms, the escalation
 * chain (who is told after how many minutes without acknowledgement), repeating, test alarm.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  if (!$('ar-form')) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const can = p => !window.PiAuth || window.PiAuth.can(p);
  const MAX_STEPS = 4;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function toast(message, type = 'info') {
    const box = $('toast-container');
    if (!box) return;
    const n = el('div', `toast ${type}`, message);
    box.appendChild(n);
    setTimeout(() => n.remove(), 4000);
  }

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  function stepRow(step, index) {
    const row = el('div', 'alarm-step');
    row.appendChild(el('span', 'alarm-step-no', t(`Stufe ${index + 1}`)));
    const when = el('label', `login-field alarm-step-after${index ? '' : ' alarm-step-now'}`);
    when.appendChild(el('span', '', t(index ? 'Nach Minuten ohne Quittung' : 'Sofort')));
    const after = el('input');
    after.type = 'number';
    after.min = 1;
    after.max = 1440;
    after.value = index ? step.after : 0;
    after.hidden = !index;   // the first step always goes out right away
    after.className = 'ar-after';
    when.appendChild(after);
    const who = el('label', 'login-field alarm-step-targets');
    who.appendChild(el('span', '', t('Empfänger')));
    const targets = el('input');
    targets.type = 'text';
    targets.className = 'ar-targets';
    targets.setAttribute('list', 'ar-users');
    targets.setAttribute('data-no-i18n', '');
    targets.spellcheck = false;
    targets.placeholder = index ? t('z. B. admin@example.com') : t('z. B. admin');
    targets.value = (step.targets || []).join(', ');
    who.appendChild(targets);
    row.append(when, who);
    if (index) {
      const del = el('button', 'text-btn', t('Entfernen'));
      del.type = 'button';
      del.addEventListener('click', () => { row.remove(); renumber(); });
      row.appendChild(del);
    }
    return row;
  }

  function renumber() {
    const rows = [...$('ar-steps').children];
    rows.forEach((r, i) => { r.querySelector('.alarm-step-no').textContent = t(`Stufe ${i + 1}`); });
    $('ar-add-step').hidden = rows.length >= MAX_STEPS;
  }

  function show(cfg) {
    $('ar-enabled').checked = cfg.enabled;
    $('ar-level').value = cfg.level;
    $('ar-repeat').value = String(cfg.repeat || 0);
    if (![...$('ar-repeat').options].some(o => o.value === String(cfg.repeat || 0))) {
      const o = el('option', '', t(`Alle ${cfg.repeat} Minuten`));
      o.value = String(cfg.repeat);
      $('ar-repeat').appendChild(o);
      $('ar-repeat').value = String(cfg.repeat);
    }
    const steps = $('ar-steps');
    steps.innerHTML = '';
    cfg.steps.forEach((s, i) => steps.appendChild(stepRow(s, i)));
    renumber();
    const users = $('ar-users');
    users.innerHTML = '';
    cfg.users.filter(u => u.email).forEach(u => { const o = el('option'); o.value = u.username; o.label = u.email; users.appendChild(o); });
    const tag = $('ar-state');
    const warn = cfg.enabled && (!cfg.email_ready || cfg.problems.length);
    tag.className = `health-tag ${cfg.enabled ? (warn ? 'warning' : 'good') : ''}`;
    tag.textContent = cfg.enabled ? (!cfg.email_ready ? t('E-Mail fehlt') : cfg.problems.length ? t('Prüfen') : t('Aktiv')) : t('Aus');
    const probs = [...(cfg.email_ready ? [] : [t('Der E-Mail-Versand ist nicht eingerichtet (Abschnitt E-Mail) – ohne ihn erscheinen Alarme nur im Dashboard.')]),
      ...cfg.problems.map(p => t(p))];
    $('ar-problems').hidden = !probs.length;
    $('ar-problems').textContent = probs.join(' ');
  }

  function collect() {
    return {
      enabled: $('ar-enabled').checked,
      level: $('ar-level').value,
      repeat: Number($('ar-repeat').value),
      steps: [...$('ar-steps').children].map(r => ({
        after: Number(r.querySelector('.ar-after').value),
        targets: r.querySelector('.ar-targets').value
      }))
    };
  }

  $('ar-add-step').addEventListener('click', () => {
    const rows = $('ar-steps').children;
    const lastAfter = rows.length > 1 ? Number(rows[rows.length - 1].querySelector('.ar-after').value) : 0;
    $('ar-steps').appendChild(stepRow({ after: lastAfter ? lastAfter * 2 : 15, targets: [] }, rows.length));
    renumber();
  });

  $('ar-form').addEventListener('submit', async e => {
    e.preventDefault();
    try { show(await api('/api/settings/alerting', collect())); toast(t('Alarmierung gespeichert.'), 'success'); } catch (err) { toast(err.message, 'error'); }
  });

  $('ar-test').addEventListener('click', async e => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      const r = await api('/api/settings/alerting/test', {});
      toast(`${t('Test-Alarm gesendet an')} ${r.sent.join(', ')}`, 'success');
    } catch (err) { toast(err.message, 'error'); }
    b.disabled = false;
  });

  (async () => {
    if (window.PiAuth) await window.PiAuth.ready;
    if (!can('settings.manage')) return;
    try { show(await api('/api/settings/alerting')); } catch (e) { /* offline */ }
  })();
})();
