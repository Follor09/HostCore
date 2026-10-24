"""
Passkeys (WebAuthn): registration and sign-in with Face ID, Touch ID, Windows Hello, Android or
a security key. Only what the dashboard needs, on top of `cryptography`:

- no attestation check (we ask for "none" - the dashboard trusts the user, not the device maker)
- user verification required (PIN / biometrics), so a passkey replaces password + 2FA code
- algorithms ES256 (almost all authenticators), EdDSA and RS256 (Windows Hello)
- signature counter checked when the authenticator keeps one (cloned keys)

Browsers only allow passkeys on HTTPS (or http://localhost); the relying party ID is the host name
the dashboard is opened with, so passkeys made under one address only work there.
"""

import base64
import hashlib
import json
import os
import secrets
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

CHALLENGE_TTL = 300
ALGS = [-7, -8, -257]
_challenges: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


class WebAuthnError(Exception):
    pass


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64u(text: str) -> bytes:
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------------------
# Minimal CBOR decoder (RFC 8949): enough for attestation objects and COSE keys
# ---------------------------------------------------------------------------
def cbor_decode(data: bytes, pos: int = 0) -> Tuple[Any, int]:
    if pos >= len(data):
        raise WebAuthnError("CBOR: unerwartetes Ende.")
    ib = data[pos]
    major, info = ib >> 5, ib & 0x1F
    pos += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if pos + size > len(data):
            raise WebAuthnError("CBOR: unerwartetes Ende.")
        arg = int.from_bytes(data[pos:pos + size], "big")
        if major == 7 and info in (25, 26, 27):
            fmt = {25: ">e", 26: ">f", 27: ">d"}[info]
            return struct.unpack(fmt, data[pos:pos + size])[0], pos + size
        pos += size
    else:
        raise WebAuthnError("CBOR: Länge ohne Angabe wird nicht unterstützt.")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        chunk = data[pos:pos + arg]
        if len(chunk) != arg:
            raise WebAuthnError("CBOR: unerwartetes Ende.")
        return (chunk if major == 2 else chunk.decode("utf-8")), pos + arg
    if major == 4:
        out = []
        for _ in range(arg):
            item, pos = cbor_decode(data, pos)
            out.append(item)
        return out, pos
    if major == 5:
        obj = {}
        for _ in range(arg):
            k, pos = cbor_decode(data, pos)
            v, pos = cbor_decode(data, pos)
            obj[k] = v
        return obj, pos
    if major == 6:
        return cbor_decode(data, pos)     # tag: take the value
    if major == 7:
        return {20: False, 21: True, 22: None, 23: None}.get(arg), pos
    raise WebAuthnError("CBOR: unbekannter Typ.")


# ---------------------------------------------------------------------------
# Challenges
# ---------------------------------------------------------------------------
def new_challenge(purpose: str, user: str = "", rp_id: str = "", origin: str = "") -> str:
    ch = b64u(secrets.token_bytes(32))
    now = time.time()
    with _lock:
        for k in [k for k, v in _challenges.items() if v["expires"] < now]:
            _challenges.pop(k, None)
        _challenges[ch] = {"purpose": purpose, "user": user, "rp_id": rp_id, "origin": origin, "expires": now + CHALLENGE_TTL}
    return ch


def _take_challenge(ch: str, purpose: str) -> Dict[str, Any]:
    with _lock:
        c = _challenges.pop(ch, None)
    if not c or c["purpose"] != purpose or c["expires"] < time.time():
        raise WebAuthnError("Anfrage abgelaufen – bitte noch einmal versuchen.")
    return c


# ---------------------------------------------------------------------------
# Parsing and checks
# ---------------------------------------------------------------------------
def _client_data(b64: str, kind: str, challenge_purpose: str) -> Tuple[Dict[str, Any], bytes, Dict[str, Any]]:
    raw = unb64u(b64)
    try:
        cd = json.loads(raw)
    except ValueError:
        raise WebAuthnError("Ungültige Antwort des Browsers.")
    if cd.get("type") != kind:
        raise WebAuthnError("Ungültige Antwort des Browsers.")
    ch = _take_challenge(cd.get("challenge", ""), challenge_purpose)
    if cd.get("origin") != ch["origin"]:
        raise WebAuthnError("Passkey gehört zu einer anderen Adresse.")
    return cd, raw, ch


def _auth_data(data: bytes, rp_id: str) -> Dict[str, Any]:
    if len(data) < 37:
        raise WebAuthnError("Ungültige Authenticator-Daten.")
    if data[:32] != hashlib.sha256(rp_id.encode()).digest():
        raise WebAuthnError("Passkey gehört zu einer anderen Adresse.")
    flags = data[32]
    if not flags & 0x01:
        raise WebAuthnError("Keine Bestätigung am Gerät.")
    if not flags & 0x04:
        raise WebAuthnError("Das Gerät hat die Person nicht geprüft (PIN oder Biometrie nötig).")
    out: Dict[str, Any] = {"flags": flags, "sign_count": int.from_bytes(data[33:37], "big")}
    if flags & 0x40:
        if len(data) < 55:
            raise WebAuthnError("Ungültige Authenticator-Daten.")
        cred_len = int.from_bytes(data[53:55], "big")
        cred_id = data[55:55 + cred_len]
        key, _ = cbor_decode(data, 55 + cred_len)
        out.update(aaguid=data[37:53].hex(), credential_id=cred_id, public_key=key)
    return out


def _cose_raw(key: Dict[int, Any]) -> bytes:
    """COSE key -> stored form (CBOR bytes are not re-encoded: keep the needed numbers as JSON)."""
    kty, alg = key.get(1), key.get(3)
    if kty == 2:
        return json.dumps({"kty": 2, "alg": alg, "crv": key.get(-1), "x": b64u(key[-2]), "y": b64u(key[-3])}).encode()
    if kty == 1:
        return json.dumps({"kty": 1, "alg": alg, "crv": key.get(-1), "x": b64u(key[-2])}).encode()
    if kty == 3:
        return json.dumps({"kty": 3, "alg": alg, "n": b64u(key[-1]), "e": b64u(key[-2])}).encode()
    raise WebAuthnError("Schlüsseltyp wird nicht unterstützt.")


def _verify_signature(stored: str, signed: bytes, signature: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    k = json.loads(unb64u(stored))
    try:
        if k["kty"] == 2 and k.get("crv") == 1:
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64u(k["x"]), "big"), int.from_bytes(unb64u(k["y"]), "big"),
                                                ec.SECP256R1()).public_key()
            pub.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif k["kty"] == 1 and k.get("crv") == 6:
            ed25519.Ed25519PublicKey.from_public_bytes(unb64u(k["x"])).verify(signature, signed)
        elif k["kty"] == 3:
            pub = rsa.RSAPublicNumbers(int.from_bytes(unb64u(k["e"]), "big"), int.from_bytes(unb64u(k["n"]), "big")).public_key()
            pub.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False
        return True
    except (InvalidSignature, ValueError):
        return False


# ---------------------------------------------------------------------------
# Ceremonies
# ---------------------------------------------------------------------------
def registration_options(username: str, user_handle: str, rp_id: str, origin: str, existing: List[str]) -> Dict[str, Any]:
    return {"challenge": new_challenge("register", username, rp_id, origin),
            "rp": {"name": "HostCore", "id": rp_id},
            "user": {"id": user_handle, "name": username, "displayName": username},
            "pubKeyCredParams": [{"type": "public-key", "alg": a} for a in ALGS],
            "timeout": 120000, "attestation": "none",
            "authenticatorSelection": {"residentKey": "required", "requireResidentKey": True, "userVerification": "required"},
            "excludeCredentials": [{"type": "public-key", "id": c} for c in existing]}


def verify_registration(username: str, cred: Dict[str, Any]) -> Dict[str, Any]:
    resp = cred.get("response") or {}
    _, _, ch = _client_data(resp.get("clientDataJSON", ""), "webauthn.create", "register")
    if ch["user"] != username:
        raise WebAuthnError("Anfrage gehört zu einem anderen Benutzer.")
    att, _ = cbor_decode(unb64u(resp.get("attestationObject", "")))
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise WebAuthnError("Ungültige Antwort des Browsers.")
    ad = _auth_data(att["authData"], ch["rp_id"])
    if "credential_id" not in ad:
        raise WebAuthnError("Der Browser hat keinen Schlüssel geliefert.")
    if ad["public_key"].get(3) not in ALGS:
        raise WebAuthnError("Verfahren des Passkeys wird nicht unterstützt.")
    transports = [t for t in (resp.get("transports") or []) if isinstance(t, str)][:6]
    return {"id": b64u(ad["credential_id"]), "public_key": b64u(_cose_raw(ad["public_key"])), "sign_count": ad["sign_count"],
            "aaguid": ad.get("aaguid", ""), "rp_id": ch["rp_id"], "transports": transports, "created": int(time.time())}


def authentication_options(rp_id: str, origin: str, allow: Optional[List[str]] = None, user: str = "") -> Dict[str, Any]:
    return {"challenge": new_challenge("login", user, rp_id, origin), "rpId": rp_id, "timeout": 120000,
            "userVerification": "required", "allowCredentials": [{"type": "public-key", "id": c} for c in (allow or [])]}


def verify_authentication(cred: Dict[str, Any], lookup) -> Tuple[str, Dict[str, Any], int]:
    """lookup(credential id) -> (username, stored credential, user handle) or None.
    Returns (username, credential, new sign count)."""
    resp = cred.get("response") or {}
    _, raw_cd, ch = _client_data(resp.get("clientDataJSON", ""), "webauthn.get", "login")
    found = lookup(str(cred.get("id") or ""))
    if not found:
        raise WebAuthnError("Dieser Passkey ist hier nicht (mehr) hinterlegt.")
    username, stored, handle = found
    if ch["user"] and ch["user"] != username:
        raise WebAuthnError("Passkey gehört zu einem anderen Benutzer.")
    if resp.get("userHandle") and handle and resp["userHandle"] != handle:
        raise WebAuthnError("Passkey gehört zu einem anderen Benutzer.")
    auth_data = unb64u(resp.get("authenticatorData", ""))
    ad = _auth_data(auth_data, ch["rp_id"])
    if not _verify_signature(stored["public_key"], auth_data + hashlib.sha256(raw_cd).digest(), unb64u(resp.get("signature", ""))):
        raise WebAuthnError("Signatur ungültig.")
    old = int(stored.get("sign_count") or 0)
    if ad["sign_count"] and old and ad["sign_count"] <= old:
        raise WebAuthnError("Passkey-Zähler passt nicht – möglicherweise kopiert. Passkey entfernen und neu anlegen.")
    return username, stored, ad["sign_count"]


def user_handle() -> str:
    return b64u(os.urandom(16))
