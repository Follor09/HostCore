/**
 * PiCore Dashboard - setting up the two-factor login (used on /2fa and in Einstellungen → Konto):
 * QR code / secret for the authenticator app, first code, then the recovery codes (shown once).
 */
(function () {
  'use strict';

  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const raw = (tag, cls, text) => { const e = el(tag, cls, text); e.setAttribute('data-no-i18n', ''); return e; };

  async function post(path, body) {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { const e = new Error(d.detail || `Fehler ${res.status}`); e.status = res.status; throw e; }
    return d;
  }

  function input(type, placeholder, extra = {}) {
    const i = el('input');
    i.type = type;
    i.placeholder = t(placeholder);
    i.setAttribute('aria-label', t(placeholder));
    Object.assign(i, extra);
    return i;
  }

  // Recovery codes: shown once, with copy and download
  function showCodes(box, codes, onDone) {
    box.innerHTML = '';
    box.appendChild(el('p', 'twofa-step', t('Wiederherstellungscodes – jetzt sicher aufbewahren (Passwort-Manager oder ausgedruckt). Jeder Code funktioniert einmal, falls das Handy fehlt. Sie werden nicht wieder angezeigt.')));
    const list = raw('pre', 'twofa-codes', codes.join('\n'));
    box.appendChild(list);
    const row = el('div', 'twofa-row');
    const copy = el('button', 'btn-secondary', t('Kopieren'));
    copy.type = 'button';
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(codes.join('\n')); copy.textContent = t('Kopiert'); } catch (e) { /* select manually */ }
    });
    const dl = el('a', 'btn-secondary', t('Als Datei'));
    dl.href = URL.createObjectURL(new Blob([`PiCore – ${t('Wiederherstellungscodes')} (${location.host})\n\n${codes.join('\n')}\n`], { type: 'text/plain' }));
    dl.download = `picore-recovery-${location.hostname}.txt`;
    row.append(copy, dl);
    box.appendChild(row);
    const confirm = el('label', 'toggle-label');
    const cb = el('input');
    cb.type = 'checkbox';
    confirm.append(cb, el('span', '', t('Ich habe die Codes gespeichert.')));
    box.appendChild(confirm);
    const done = el('button', 'login-submit', t('Fertig'));
    done.type = 'button';
    done.disabled = true;
    cb.addEventListener('change', () => { done.disabled = !cb.checked; });
    done.addEventListener('click', () => onDone && onDone());
    box.appendChild(done);
  }

  function scanStep(box, setup, onDone) {
    box.innerHTML = '';
    box.appendChild(el('p', 'twofa-step', t('1. Den QR-Code mit einer Authenticator-App scannen (z.B. Aegis, 2FAS, Google oder Microsoft Authenticator, 1Password).')));
    if (setup.qr) {
      const qr = el('div', 'twofa-qr');
      // segno output: a plain SVG without scripts - parsed instead of innerHTML for safety
      const doc = new DOMParser().parseFromString(setup.qr, 'image/svg+xml');
      const svg = doc.documentElement;
      if (svg && svg.nodeName.toLowerCase() === 'svg') qr.appendChild(document.importNode(svg, true));
      box.appendChild(qr);
    }
    box.appendChild(el('p', 'field-hint', t('Kein Scannen möglich? Diesen Schlüssel in der App eingeben:')));
    box.appendChild(raw('code', 'twofa-secret', setup.secret.replace(/(.{4})/g, '$1 ').trim()));
    box.appendChild(el('p', 'twofa-step', t('2. Den 6-stelligen Code aus der App eingeben:')));
    const form = el('form', 'twofa-row');
    const code = input('text', '123456', { inputMode: 'numeric', autocomplete: 'one-time-code', maxLength: 8 });
    const go = el('button', 'btn-primary', t('Bestätigen'));
    go.type = 'submit';
    form.append(code, go);
    box.appendChild(form);
    const err = el('p', 'form-msg error');
    box.appendChild(err);
    setTimeout(() => code.focus(), 30);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      go.disabled = true;
      err.textContent = '';
      try {
        const d = await post('/api/auth/2fa/enable', { code: code.value.trim() });
        showCodes(box, d.recovery_codes, onDone);
      } catch (ex) { err.textContent = ex.message; go.disabled = false; code.select(); }
    });
  }

  async function setup(box, opts = {}) {
    box.innerHTML = '';
    const err = el('p', 'form-msg error');
    try {
      scanStep(box, await post('/api/auth/2fa/setup', {}), opts.onDone);
    } catch (ex) {
      if (ex.status !== 403) { box.appendChild(err); err.textContent = ex.message; return; }
      // Some time since the login: confirm with the password first
      box.appendChild(el('p', 'twofa-step', t('Zum Einrichten bitte das Passwort eingeben.')));
      const form = el('form', 'twofa-row');
      const pw = input('password', 'Passwort', { autocomplete: 'current-password' });
      const go = el('button', 'btn-primary', t('Weiter'));
      go.type = 'submit';
      form.append(pw, go);
      box.append(form, err);
      setTimeout(() => pw.focus(), 30);
      form.addEventListener('submit', async e => {
        e.preventDefault();
        go.disabled = true;
        try { scanStep(box, await post('/api/auth/2fa/setup', { password: pw.value }), opts.onDone); }
        catch (ex2) { err.textContent = ex2.message; go.disabled = false; }
      });
    }
  }

  window.PiTwoFA = { setup, showCodes };
})();
