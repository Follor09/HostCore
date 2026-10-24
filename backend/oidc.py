"""
Sign-in with an OpenID Connect provider (Pocket ID, Authentik, Keycloak …) - Einstellungen -> SSO.

- Authorization code flow with PKCE and nonce; the ID token is checked against the provider's
  keys (RS*, PS*, ES*, EdDSA via `cryptography`), issuer, audience, expiry and nonce.
- The provider's subject is linked to a dashboard account on the first sign-in: by an explicit
  assignment, by the same username or e-mail address, or - if allowed - a new account is created.
  Provider groups can set the dashboard group (on every sign-in when "sync" is on).
- Local passwords and passkeys can be limited to administrators (emergency access); the CLI
  ./set-password.sh always works.
- The state of a running sign-in lives in memory, bound to the browser by a Lax cookie (the
  session cookie is SameSite=Strict and does not come back with the provider's redirect).
- "Confirm again" works through the provider too (prompt=login): the session to elevate is
  remembered when the flow starts.
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import httpx

import settings_store

KEY = "oidc"
STATE_COOKIE = "picore_oidc"
FLOW_TTL = 600
REAUTH_MAX_AGE = 300          # the provider must have asked for the login this recently
DEFAULTS: Dict[str, Any] = {
    "enabled": False, "label": "Pocket ID", "issuer": "", "client_id": "", "scopes": "openid profile email groups",
    "groups_claim": "groups", "match_accounts": True, "auto_create": False, "default_group": "",
    "group_map": [], "sync_groups": False, "restrict_to_groups": False, "counts_as_2fa": True, "local_login": "all",
}
LOCAL_LOGIN = {"all": "Für alle Konten", "admins": "Nur Administratoren (Notzugang)"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")

_flows: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
_meta: Dict[str, Any] = {"issuer": "", "ts": 0.0, "doc": None, "jwks": None, "jwks_ts": 0.0}


class OidcError(Exception):
    pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def config() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    return {**DEFAULTS, **(c if isinstance(c, dict) else {})}


def enabled() -> bool:
    c = config()
    return bool(c["enabled"] and c["issuer"] and c["client_id"] and c.get("client_secret"))


def local_login_allowed(group: Optional[str]) -> bool:
    """Password and passkey sign-in: everybody, or only administrators while SSO is on."""
    return not enabled() or config()["local_login"] != "admins" or group == "admin"


def counts_as_2fa() -> bool:
    return enabled() and bool(config()["counts_as_2fa"])


def login_info() -> Dict[str, Any]:
    """What the login page needs (no secrets)."""
    c = config()
    return {"enabled": enabled(), "label": c["label"] or "SSO", "local": c["local_login"] if enabled() else "all"}


def public(callback_url: str = "") -> Dict[str, Any]:
    import auth
    c = config()
    linked = [{"user": u["username"], "subject": u["sso"]["sub"], "issuer": u["sso"]["iss"], "since": u["sso"].get("since"),
               "last": u["sso"].get("last"), "sso_only": u.get("sso_only", False)}
              for u in auth.sso_links()]
    return {**{k: c[k] for k in DEFAULTS}, "has_secret": bool(c.get("client_secret")), "active": enabled(),
            "callback_url": callback_url, "linked": linked, "local_login_options": LOCAL_LOGIN,
            "groups": [{"id": g["id"], "name": g["name"]} for g in auth.list_groups()]}


def _clean_issuer(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    url = re.sub(r"/\.well-known/openid-configuration$", "", url)
    p = urlparse(url)
    if not p.hostname or p.query or p.fragment or p.username:
        raise OidcError("Anbieter-Adresse: ungültige Adresse.")
    if p.scheme != "https" and p.hostname not in ("localhost", "127.0.0.1"):
        raise OidcError("Anbieter-Adresse: nur über https.")
    return url


def save(fields: Dict[str, Any]) -> Dict[str, Any]:
    import auth
    c = config()
    groups = auth.all_groups()
    group_map = []
    for m in fields.get("group_map") or []:
        claim = str((m or {}).get("claim", "")).strip()[:120]
        group = str((m or {}).get("group", "")).strip()
        if not claim:
            continue
        if group not in groups:
            raise OidcError(f"Dashboard-Gruppe für „{claim}“ nicht gefunden.")
        group_map.append({"claim": claim, "group": group})
    if len(group_map) > 30:
        raise OidcError("Höchstens 30 Gruppen-Zuordnungen.")
    default_group = str(fields.get("default_group") or "").strip()
    if default_group and default_group not in groups:
        raise OidcError("Unbekannte Standardgruppe.")
    if default_group == "admin":
        raise OidcError("Neue Konten können nicht automatisch Administratoren werden – dafür eine Gruppen-Zuordnung anlegen.")
    local = str(fields.get("local_login") or "all")
    if local not in LOCAL_LOGIN:
        raise OidcError("Unbekannte Einstellung für die lokale Anmeldung.")
    scopes = " ".join(s for s in re.split(r"[\s,]+", str(fields.get("scopes") or DEFAULTS["scopes"])) if re.fullmatch(r"[\w.:-]{1,60}", s))
    if "openid" not in scopes.split():
        scopes = "openid " + scopes
    claim = str(fields.get("groups_claim") or "groups").strip()
    if not re.fullmatch(r"[\w.:-]{1,60}", claim):
        raise OidcError("Ungültiger Name für das Gruppen-Claim.")
    c.update(enabled=bool(fields.get("enabled")), label=str(fields.get("label") or "").strip()[:40] or "SSO",
             issuer=_clean_issuer(fields.get("issuer", "")), client_id=str(fields.get("client_id") or "").strip()[:200],
             scopes=scopes[:300], groups_claim=claim, match_accounts=bool(fields.get("match_accounts")),
             auto_create=bool(fields.get("auto_create")), default_group=default_group, group_map=group_map,
             sync_groups=bool(fields.get("sync_groups")), restrict_to_groups=bool(fields.get("restrict_to_groups")),
             counts_as_2fa=bool(fields.get("counts_as_2fa")), local_login=local)
    secret = str(fields.get("client_secret") or "").strip()
    if secret:
        c["client_secret"] = secret[:500]
    if c["enabled"]:
        missing = [n for n, v in (("Anbieter-Adresse", c["issuer"]), ("Client-ID", c["client_id"]),
                                  ("Client-Secret", c.get("client_secret"))) if not v]
        if missing:
            raise OidcError(f"Zum Einschalten fehlt: {', '.join(missing)}.")
        if c["restrict_to_groups"] and not group_map:
            raise OidcError("„Nur zugeordnete Gruppen“ braucht mindestens eine Gruppen-Zuordnung.")
    if c["local_login"] == "admins" and not c["enabled"]:
        c["local_login"] = "all"
    settings_store.set(KEY, c)
    _meta.update(ts=0.0, doc=None, jwks=None)
    return c


# ---------------------------------------------------------------------------
# Provider metadata and keys
# ---------------------------------------------------------------------------
async def discovery(issuer: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    issuer = issuer or config()["issuer"]
    if not force and _meta["doc"] and _meta["issuer"] == issuer and time.time() - _meta["ts"] < 3600:
        return _meta["doc"]
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as cl:
            r = await cl.get(f"{issuer}/.well-known/openid-configuration")
        r.raise_for_status()
        doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise OidcError(f"Anbieter nicht erreichbar: {type(e).__name__}") from e
    if not isinstance(doc, dict) or doc.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
        raise OidcError("Der Anbieter meldet eine andere Adresse (issuer) als eingetragen.")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not str(doc.get(k, "")).startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise OidcError(f"Anbieter-Metadaten unvollständig ({k}).")
    _meta.update(issuer=issuer, ts=time.time(), doc=doc, jwks=None)
    return doc


async def _jwks(doc: Dict[str, Any], force: bool = False) -> List[Dict[str, Any]]:
    if _meta["jwks"] is not None and not force and time.time() - _meta["jwks_ts"] < 3600:
        return _meta["jwks"]
    try:
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.get(doc["jwks_uri"])
        r.raise_for_status()
        keys = r.json().get("keys") or []
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        raise OidcError(f"Schlüssel des Anbieters nicht abrufbar: {type(e).__name__}") from e
    _meta.update(jwks=[k for k in keys if isinstance(k, dict)], jwks_ts=time.time())
    return _meta["jwks"]


async def test(fields: Dict[str, Any]) -> Dict[str, Any]:
    issuer = _clean_issuer(fields.get("issuer", "") or config()["issuer"])
    if not issuer:
        raise OidcError("Bitte die Anbieter-Adresse eintragen.")
    doc = await discovery(issuer, force=True)
    keys = await _jwks(doc, force=True)
    return {"issuer": doc["issuer"], "keys": len(keys), "pkce": "S256" in (doc.get("code_challenge_methods_supported") or ["S256"]),
            "scopes": doc.get("scopes_supported") or [], "groups": "groups" in (doc.get("scopes_supported") or [])}


# ---------------------------------------------------------------------------
# JWT (ID token) check
# ---------------------------------------------------------------------------
def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _int(text: str) -> int:
    return int.from_bytes(_unb64(text), "big")


def _verify_sig(alg: str, jwk: Dict[str, Any], signed: bytes, sig: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    hash_alg = {"256": hashes.SHA256(), "384": hashes.SHA384(), "512": hashes.SHA512()}.get(alg[-3:])
    try:
        if alg[:2] in ("RS", "PS") and jwk.get("kty") == "RSA" and hash_alg:
            pub = rsa.RSAPublicNumbers(_int(jwk["e"]), _int(jwk["n"])).public_key()
            pad = padding.PKCS1v15() if alg[0] == "R" else padding.PSS(mgf=padding.MGF1(hash_alg), salt_length=hash_alg.digest_size)
            pub.verify(sig, signed, pad, hash_alg)
        elif alg[:2] == "ES" and jwk.get("kty") == "EC" and hash_alg:
            curve = {"P-256": ec.SECP256R1(), "P-384": ec.SECP384R1(), "P-521": ec.SECP521R1()}.get(jwk.get("crv"))
            if not curve:
                return False
            pub = ec.EllipticCurvePublicNumbers(_int(jwk["x"]), _int(jwk["y"]), curve).public_key()
            half = len(sig) // 2
            pub.verify(encode_dss_signature(int.from_bytes(sig[:half], "big"), int.from_bytes(sig[half:], "big")), signed,
                       ec.ECDSA(hash_alg))
        elif alg == "EdDSA" and jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
            ed25519.Ed25519PublicKey.from_public_bytes(_unb64(jwk["x"])).verify(sig, signed)
        else:
            return False
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False


async def _check_id_token(token: str, doc: Dict[str, Any], client_id: str, nonce: str) -> Dict[str, Any]:
    try:
        h64, p64, s64 = token.split(".")
        header, claims, sig = json.loads(_unb64(h64)), json.loads(_unb64(p64)), _unb64(s64)
    except (ValueError, TypeError):
        raise OidcError("Ungültiges ID-Token.")
    alg = str(header.get("alg", ""))
    if alg in ("", "none") or alg.startswith("HS"):
        raise OidcError(f"Signaturverfahren {alg or 'none'} wird nicht akzeptiert.")
    kid = header.get("kid")
    for force in (False, True):     # unknown key id: the provider may have rotated its keys
        keys = [k for k in await _jwks(doc, force=force) if not kid or k.get("kid") == kid]
        keys = [k for k in keys if k.get("use", "sig") == "sig" and (not k.get("alg") or k["alg"] == alg)]
        if keys or force:
            break
    if not any(_verify_sig(alg, k, f"{h64}.{p64}".encode(), sig) for k in keys):
        raise OidcError("Signatur des ID-Tokens ungültig.")
    now = time.time()
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if claims.get("iss", "").rstrip("/") != doc["issuer"].rstrip("/"):
        raise OidcError("ID-Token von einem anderen Aussteller.")
    if client_id not in auds or (len(auds) > 1 and claims.get("azp") not in (None, client_id)):
        raise OidcError("ID-Token ist nicht für dieses Dashboard ausgestellt.")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 60:
        raise OidcError("ID-Token abgelaufen – Uhrzeit von Server und Anbieter prüfen.")
    if isinstance(claims.get("iat"), (int, float)) and claims["iat"] > now + 120:
        raise OidcError("ID-Token aus der Zukunft – Uhrzeit von Server und Anbieter prüfen.")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("Anmeldung passt nicht zu dieser Anfrage (nonce).")
    if not claims.get("sub"):
        raise OidcError("ID-Token ohne Benutzerkennung.")
    return claims


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------
def _prune() -> None:
    now = time.time()
    for k in [k for k, v in _flows.items() if v["ts"] < now - FLOW_TTL]:
        _flows.pop(k, None)


async def begin(redirect_uri: str, next_path: str, remember: bool, reauth_session: str = "",
                reauth_user: str = "") -> Tuple[str, str]:
    """(URL of the provider's login page, value for the binding cookie)."""
    if not enabled():
        raise OidcError("Anmeldung per SSO ist nicht eingerichtet.")
    c = config()
    doc = await discovery()
    state, nonce, verifier, binding = (secrets.token_urlsafe(24) for _ in range(4))
    with _lock:
        _prune()
        if len(_flows) > 500:
            raise OidcError("Zu viele offene Anmeldungen. Bitte kurz warten.")
        _flows[state] = {"ts": time.time(), "nonce": nonce, "verifier": verifier, "binding": hashlib.sha256(binding.encode()).hexdigest(),
                         "redirect_uri": redirect_uri, "next": next_path, "remember": remember,
                         "reauth_session": reauth_session, "reauth_user": reauth_user}
    params = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": redirect_uri, "scope": c["scopes"],
              "state": state, "nonce": nonce, "code_challenge_method": "S256",
              "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest())}
    if reauth_session:
        params.update(prompt="login", max_age="0")
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{urlencode(params)}", binding


def take_flow(state: str, binding: str) -> Dict[str, Any]:
    """The pending flow for this state - only in the browser that started it, only once."""
    with _lock:
        _prune()
        flow = _flows.pop(state or "", None)
    if not flow:
        raise OidcError("Anmeldung abgelaufen oder schon benutzt – bitte erneut versuchen.")
    if not binding or not secrets.compare_digest(flow["binding"], hashlib.sha256(binding.encode()).hexdigest()):
        raise OidcError("Anmeldung wurde in einem anderen Browser begonnen.")
    return flow


async def finish(flow: Dict[str, Any], code: str) -> Dict[str, Any]:
    """Code -> checked claims (ID token, completed by the userinfo endpoint if groups are missing)."""
    c = config()
    doc = await discovery()
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": flow["redirect_uri"], "code_verifier": flow["verifier"]}
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    auth_arg = None
    if "client_secret_basic" in methods:
        auth_arg = (c["client_id"], c["client_secret"])
    else:
        data.update(client_id=c["client_id"], client_secret=c["client_secret"])
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.post(doc["token_endpoint"], data=data, auth=auth_arg, headers={"Accept": "application/json"})
            tok = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or not tok.get("id_token"):
                raise OidcError(f"Anbieter lehnt die Anmeldung ab: {tok.get('error_description') or tok.get('error') or r.status_code}")
            claims = await _check_id_token(tok["id_token"], doc, c["client_id"], flow["nonce"])
            if c["groups_claim"] not in claims and doc.get("userinfo_endpoint") and tok.get("access_token"):
                u = await cl.get(doc["userinfo_endpoint"], headers={"Authorization": f"Bearer {tok['access_token']}"})
                info = u.json() if u.status_code == 200 else {}
                if isinstance(info, dict) and info.get("sub") == claims["sub"]:
                    claims = {**info, **claims}
    except httpx.HTTPError as e:
        raise OidcError(f"Anbieter nicht erreichbar: {type(e).__name__}") from e
    if flow["reauth_session"]:
        at = claims.get("auth_time")
        if isinstance(at, (int, float)) and at < time.time() - REAUTH_MAX_AGE:
            raise OidcError("Der Anbieter hat nicht erneut nach der Anmeldung gefragt.")
    return claims


# ---------------------------------------------------------------------------
# Provider identity -> dashboard account
# ---------------------------------------------------------------------------
def _groups(claims: Dict[str, Any]) -> List[str]:
    g = claims.get(config()["groups_claim"])
    if isinstance(g, str):
        g = [g]
    return [str(x) for x in g] if isinstance(g, list) else []


def _mapped_group(claims: Dict[str, Any]) -> Optional[str]:
    have = {x.casefold() for x in _groups(claims)}
    return next((m["group"] for m in config()["group_map"] if m["claim"].casefold() in have), None)


def _new_username(claims: Dict[str, Any]) -> str:
    import auth
    base = str(claims.get("preferred_username") or (claims.get("email") or "").split("@")[0] or "sso")
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-.")[:28] or "sso"
    name, n = base, 2
    while auth.canonical_username(name):
        name = f"{base}-{n}"
        n += 1
    return name


def resolve_user(claims: Dict[str, Any]) -> Tuple[str, str]:
    """(dashboard user, what happened) - links or creates the account; raises OidcError if refused."""
    import auth
    c = config()
    iss, sub = claims["iss"].rstrip("/"), str(claims["sub"])
    mapped = _mapped_group(claims)
    if c["restrict_to_groups"] and not mapped:
        raise OidcError("Dein Konto beim Anbieter gehört zu keiner freigegebenen Gruppe.")
    name = auth.find_sso(iss, sub)
    how = "angemeldet"
    if not name and c["match_accounts"]:
        email_ok = claims.get("email_verified") is not False
        name = auth.match_sso_candidate(str(claims.get("preferred_username") or ""),
                                        str(claims.get("email") or "") if email_ok else "")
        if name:
            auth.link_sso(name, iss, sub)
            how = "Konto verknüpft"
    if not name:
        group = mapped or c["default_group"]
        if not c["auto_create"] or not group:
            raise OidcError("Zu diesem Konto gibt es keinen Dashboard-Benutzer. Ein Administrator muss es freischalten.")
        name = auth.create_sso_user(_new_username(claims), group, iss, sub, str(claims.get("email") or ""))
        how = "Konto angelegt"
    if not auth.active_user(name):
        raise OidcError("Das Dashboard-Konto ist deaktiviert oder abgelaufen.")
    if c["sync_groups"] and mapped and auth.user_info(name)["group"] != mapped:
        try:
            auth.update_user(name, group=mapped)
            how += f", Gruppe {mapped}"
        except auth.AuthError:
            pass   # e.g. the last administrator keeps the group
    auth.sso_seen(name)
    return name, how
