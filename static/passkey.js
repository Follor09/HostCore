/**
 * HostCore Dashboard - passkeys in the browser (WebAuthn): sign in, confirm again and add a passkey.
 * The server sends options with base64url strings; the browser API wants ArrayBuffers.
 * Passkeys need HTTPS (or http://localhost) - window.PiPasskey.supported() says whether they work here.
 */
(function () {
  'use strict';

  const toBuf = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - s.length % 4) % 4)), c => c.charCodeAt(0)).buffer;
  const toB64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  async function post(path, body) {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
    return d;
  }

  function supported() {
    return !!(window.PublicKeyCredential && window.isSecureContext && navigator.credentials);
  }

  function friendly(err) {
    if (err && err.name === 'NotAllowedError') return new Error('Abgebrochen oder Zeit abgelaufen.');
    if (err && err.name === 'InvalidStateError') return new Error('Dieser Passkey ist auf dem Gerät schon für dieses Konto angelegt.');
    if (err && err.name === 'SecurityError') return new Error('Passkeys gehen nur über HTTPS und die eingetragene Adresse.');
    return err;
  }

  async function get(options) {
    const pk = {
      challenge: toBuf(options.challenge), rpId: options.rpId, timeout: options.timeout,
      userVerification: options.userVerification,
      allowCredentials: (options.allowCredentials || []).map(c => ({ type: 'public-key', id: toBuf(c.id) }))
    };
    let cred;
    try { cred = await navigator.credentials.get({ publicKey: pk }); } catch (e) { throw friendly(e); }
    const r = cred.response;
    return {
      id: cred.id, type: cred.type,
      response: { clientDataJSON: toB64(r.clientDataJSON), authenticatorData: toB64(r.authenticatorData),
        signature: toB64(r.signature), userHandle: r.userHandle ? toB64(r.userHandle) : '' }
    };
  }

  // Sign in on the login page (discoverable passkey: no username needed)
  async function login(remember) {
    const options = await post('/api/auth/passkey/login/options');
    return post('/api/auth/passkey/login', { credential: await get(options), remember: !!remember });
  }

  // Confirm again while logged in (only this user's passkeys are offered)
  async function confirm() {
    const options = await post('/api/auth/passkey/login/options');
    return post('/api/auth/passkey/login', { credential: await get(options) });
  }

  async function register(name) {
    const o = await post('/api/auth/passkey/register/options');
    const pk = {
      challenge: toBuf(o.challenge), rp: o.rp, timeout: o.timeout, attestation: o.attestation,
      user: { id: toBuf(o.user.id), name: o.user.name, displayName: o.user.displayName },
      pubKeyCredParams: o.pubKeyCredParams, authenticatorSelection: o.authenticatorSelection,
      excludeCredentials: (o.excludeCredentials || []).map(c => ({ type: 'public-key', id: toBuf(c.id) }))
    };
    let cred;
    try { cred = await navigator.credentials.create({ publicKey: pk }); } catch (e) { throw friendly(e); }
    const r = cred.response;
    return post('/api/auth/passkey/register', {
      name,
      credential: { id: cred.id, type: cred.type,
        response: { clientDataJSON: toB64(r.clientDataJSON), attestationObject: toB64(r.attestationObject),
          transports: r.getTransports ? r.getTransports() : [] } }
    });
  }

  window.PiPasskey = { supported, login, confirm, register };
})();
