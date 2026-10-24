"""
Data protection documentation (Compliance -> Datenschutz): the records of processing activities
(Verzeichnis von Verarbeitungstätigkeiten, Art. 30 DSGVO) and the technical and organisational
measures (TOM, Art. 32 DSGVO).

- Controller and data protection officer once; then one entry per processing activity: purpose,
  legal basis, data subjects, data categories (special categories flagged), recipients,
  processors, third-country transfers, deletion periods, the systems involved (services/stacks
  of the dashboard), owner and review date.
- TOM by the usual control areas. Each area has its own text plus evidence the dashboard
  determines itself (2FA policy, password rules, access review, activity log, backups, firewall,
  encryption in transit, patch deadlines ...), so the document stays close to reality.
- Export as Markdown / print view. Not legal advice - a structure to fill in.
"""

import datetime
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

import settings_store

KEY = "privacy"
LEGAL_BASES = {
    "a": "Art. 6 Abs. 1 lit. a – Einwilligung", "b": "Art. 6 Abs. 1 lit. b – Vertrag", "c": "Art. 6 Abs. 1 lit. c – rechtliche Verpflichtung",
    "d": "Art. 6 Abs. 1 lit. d – lebenswichtige Interessen", "e": "Art. 6 Abs. 1 lit. e – öffentliche Aufgabe",
    "f": "Art. 6 Abs. 1 lit. f – berechtigtes Interesse", "bdsg26": "§ 26 BDSG – Beschäftigungsverhältnis",
}
TOM_AREAS = [
    ("zutritt", "Zutrittskontrolle", "Kein unbefugter Zutritt zu den Räumen und Geräten (Serverraum, Schrank, Standort des Servers)."),
    ("zugang", "Zugangskontrolle", "Keine unbefugte Nutzung der Systeme (Anmeldung, Passwörter, zweiter Faktor)."),
    ("zugriff", "Zugriffskontrolle", "Zugriff nur auf die Daten, die für die Aufgabe nötig sind (Rechte, Rollen, Prüfung)."),
    ("weitergabe", "Weitergabekontrolle", "Schutz bei Übertragung und Transport (Verschlüsselung, VPN, Freigabe-Links)."),
    ("eingabe", "Eingabekontrolle", "Nachvollziehbar, wer was geändert hat (Protokollierung)."),
    ("auftrag", "Auftragskontrolle", "Verarbeitung im Auftrag nur nach Weisung (AV-Verträge, Auswahl der Dienstleister)."),
    ("verfuegbarkeit", "Verfügbarkeitskontrolle", "Schutz vor Verlust (Datensicherung, Notfallplanung, Überwachung)."),
    ("trennung", "Trennungsgebot", "Daten unterschiedlicher Zwecke getrennt verarbeiten (Container, Mandanten, Datenbanken)."),
    ("verschluesselung", "Pseudonymisierung & Verschlüsselung", "Verschlüsselung gespeicherter und übertragener Daten, Hashing von Passwörtern."),
    ("ueberpruefung", "Überprüfung & Bewertung", "Regelmäßige Überprüfung der Wirksamkeit (Scans, Reviews, Änderungsverfahren)."),
]
REVIEW_DAYS = 365
_lock = threading.Lock()


class PrivacyError(Exception):
    pass


def _cfg() -> Dict[str, Any]:
    c = settings_store.get(KEY) or {}
    c = c if isinstance(c, dict) else {}
    c.setdefault("controller", {})
    c.setdefault("activities", [])
    c.setdefault("tom", {})
    return c


def _text(value: Any, label: str, max_len: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise PrivacyError(f"{label} fehlt.")
    if len(text) > max_len:
        raise PrivacyError(f"{label}: höchstens {max_len} Zeichen.")
    return text


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def save_controller(fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    ctl = {k: _text(fields.get(k), label, n) for k, label, n in (
        ("name", "Verantwortlicher", 200), ("address", "Anschrift", 500), ("contact", "Kontakt", 200),
        ("representative", "Vertreter", 200), ("dpo", "Datenschutzbeauftragte(r)", 300))}
    with _lock:
        c = _cfg()
        c["controller"] = {**ctl, "updated": int(time.time()), "updated_by": user}
        settings_store.set(KEY, c)
    return overview()


def save_activity(aid: Optional[str], fields: Dict[str, Any], user: str = "") -> Dict[str, Any]:
    import changes
    bases = [b for b in (str(x) for x in fields.get("legal_bases") or []) if b in LEGAL_BASES]
    if not bases:
        raise PrivacyError("Bitte mindestens eine Rechtsgrundlage wählen.")
    names = changes._names()
    review = _text(fields.get("review"), "Nächste Prüfung", 10)
    if review:
        try:
            datetime.date.fromisoformat(review)
        except ValueError:
            raise PrivacyError("Nächste Prüfung: Datum im Format JJJJ-MM-TT.")
    clean = {"name": _text(fields.get("name"), "Bezeichnung", 120, True), "purpose": _text(fields.get("purpose"), "Zweck", 2000, True),
             "legal_bases": bases, "legal_note": _text(fields.get("legal_note"), "Erläuterung", 500),
             "subjects": _text(fields.get("subjects"), "Betroffene", 500, True), "categories": _text(fields.get("categories"), "Datenkategorien", 1000, True),
             "special": bool(fields.get("special")), "recipients": _text(fields.get("recipients"), "Empfänger", 1000),
             "processors": _text(fields.get("processors"), "Auftragsverarbeiter", 1000), "third_country": _text(fields.get("third_country"), "Drittland", 500),
             "retention": _text(fields.get("retention"), "Löschfrist", 500, True), "owner": _text(fields.get("owner"), "Verantwortlich intern", 120),
             "systems": [s for s in (str(x) for x in fields.get("systems") or []) if s in names][:30], "review": review}
    with _lock:
        c = _cfg()
        if aid:
            a = next((x for x in c["activities"] if x["id"] == aid), None)
            if not a:
                raise PrivacyError("Eintrag nicht gefunden.")
            a.update(clean, updated=int(time.time()), updated_by=user)
        else:
            if len(c["activities"]) >= 200:
                raise PrivacyError("Höchstens 200 Verarbeitungstätigkeiten.")
            c["activities"].append({"id": secrets.token_hex(5), **clean, "created": int(time.time()), "updated": int(time.time()), "updated_by": user})
        settings_store.set(KEY, c)
    return overview()


def delete_activity(aid: str) -> Dict[str, Any]:
    with _lock:
        c = _cfg()
        c["activities"] = [x for x in c["activities"] if x["id"] != aid]
        settings_store.set(KEY, c)
    return overview()


def save_tom(area: str, text: str, user: str = "") -> Dict[str, Any]:
    if area not in dict((a, l) for a, l, _ in TOM_AREAS):
        raise PrivacyError("Unbekannter Bereich.")
    with _lock:
        c = _cfg()
        c["tom"][area] = {"text": _text(text, "Maßnahmen", 4000), "updated": int(time.time()), "updated_by": user}
        settings_store.set(KEY, c)
    return overview()


# ---------------------------------------------------------------------------
# Evidence from the dashboard
# ---------------------------------------------------------------------------
def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def evidence() -> Dict[str, List[str]]:
    import auth
    import websec
    ev: Dict[str, List[str]] = {a: [] for a, _, _ in TOM_AREAS}
    ws = websec.config()
    policy = {"none": "freiwillig", "admins": "Pflicht für Administratoren", "all": "Pflicht für alle"}[ws["twofa"]]
    ev["zugang"] += [f"Passwörter mit mindestens {auth.MIN_PASSWORD_LENGTH} Zeichen, bekannte Passwörter gesperrt, scrypt-Hash",
                     f"Zwei-Faktor-Anmeldung (TOTP/Passkey): {policy}",
                     "Sperre nach wiederholten Fehlversuchen (pro Adresse und Konto)",
                     f"Sitzungen enden nach {auth.SESSION_TTL // 3600} Std. (mit „Angemeldet bleiben“ {auth.REMEMBER_TTL // 86400} Tage), einzeln beendbar"]
    import oidc
    if oidc.enabled():
        ev["zugang"].append(f"Anmeldung über Identitätsanbieter (OpenID Connect, {oidc.config()['label']})")
    groups = _safe(auth.list_groups, []) or []
    ev["zugriff"].append(f"Rollenmodell mit {len(groups)} Gruppen und {len(auth.ALL_PERMISSIONS)} Einzelrechten; jede Anfrage wird gegen eine zentrale Rechtetabelle geprüft")
    ar = _safe(lambda: __import__("accessreview").overview())
    if ar:
        ev["zugriff"].append(f"Rechte-Review: zuletzt {time.strftime('%d.%m.%Y', time.localtime(ar['last_review']['ts']))}, Turnus {ar['settings']['interval_days']} Tage"
                             if ar["last_review"] else "Rechte-Review eingerichtet, noch nicht abgezeichnet")
    if ws["restrict"]:
        ev["zugriff"].append("Kritische Bereiche (u. a. " + ", ".join(ws["restrict_perms"][:4]) + ") nur aus dem internen Netz/VPN")
    if ws["reauth"]:
        ev["zugriff"].append(f"Erneute Bestätigung vor kritischen Änderungen ({ws['reauth_minutes']} Min. gültig)")
    if ws["proxy"] == "cloudflare":
        ev["weitergabe"].append("Zugriff von außen nur über Cloudflare (TLS-verschlüsselt)")
    if ws["hsts"]:
        ev["weitergabe"].append("HTTPS erzwungen (HSTS) bei Zugriff über HTTPS")
    hd = _safe(lambda: __import__("hardening").overview(), {}) or {}
    for r in hd.get("results", []):
        if r["id"] == "ufw" and r["state"] == "ok":
            ev["weitergabe"].append("Paketfilter (ufw) aktiv")
        if r["id"] == "ssh-password" and r["state"] == "ok":
            ev["zugang"].append("SSH nur mit Schlüssel, Passwort-Anmeldung aus")
        if r["id"] == "unattended" and r["state"] == "ok":
            ev["ueberpruefung"].append("Automatische Sicherheitsupdates aktiv")
    ev["weitergabe"].append("Freigabe- und Austausch-Links mit Ablaufzeit, jederzeit widerrufbar")
    ev["eingabe"].append("Aktivitätsprotokoll aller ändernden Aktionen mit Benutzer, Adresse und Zeit, als SHA-256-Kette gegen nachträgliche Änderung gesichert")
    import recordings
    if _safe(lambda: recordings.config().get("enabled", True), False):
        ev["eingabe"].append("Web-Terminal-Sitzungen werden aufgezeichnet")
    bk = _safe(lambda: __import__("backups").overview(), {}) or {}
    if bk.get("jobs"):
        ev["verfuegbarkeit"].append(f"{len(bk['jobs'])} überwachte Datensicherungen, davon {bk['problems']} mit Problem")
    hb = _safe(lambda: __import__("drhandbook").facts(), {}) or {}
    if hb.get("services"):
        ev["verfuegbarkeit"].append(f"Notfallhandbuch für {hb['services']} Dienste, Bereitschaft {hb['score']} %")
    ev["verfuegbarkeit"].append("Überwachung von Diensten, Web-Diensten und Hardware mit Alarmierung")
    ev["trennung"].append("Dienste als getrennte Container/Compose-Stacks mit eigenen Netzwerken und Datenbanken")
    ev["verschluesselung"] += ["Passwörter nur als scrypt-Hash gespeichert", "Zugangsdaten des Dashboards in Dateien nur für den Besitzer lesbar (0600)"]
    ss = _safe(lambda: __import__("secretscan").overview(), {}) or {}
    if ss.get("checked"):
        c = ss["counts"]
        ev["verschluesselung"].append(f"Geheimnis-Scan: {c['critical'] + c['warning']} offene Fundstellen")
    ps = _safe(lambda: __import__("patchsla").overview(), {}) or {}
    if ps.get("scanned"):
        ev["ueberpruefung"].append(f"Täglicher Schwachstellen-Scan mit Patch-Fristen, {ps['counts']['overdue']} überfällig"
                                   + (f", Einhaltung {ps['compliance']} %" if ps.get("compliance") is not None else ""))
    if hd.get("score") is not None:
        ev["ueberpruefung"].append(f"Härtungs-Check: {hd['score']} %")
    ch = _safe(lambda: __import__("changes").overview(), {}) or {}
    if ch.get("changes"):
        ev["ueberpruefung"].append(f"Änderungsverfahren mit Vier-Augen-Freigabe ({len(ch['changes'])} Anträge)")
    gs = _safe(lambda: __import__("baseline").overview(), {}) or {}
    if gs.get("score") is not None:
        ev["ueberpruefung"].append(f"Grundschutz-Check: Erfüllungsgrad {gs['score']} %")
    return ev


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def overview() -> Dict[str, Any]:
    import changes
    c = _cfg()
    names = changes._names()
    today = datetime.date.today().isoformat()
    acts = []
    for a in sorted(c["activities"], key=lambda x: x["name"].lower()):
        due = a.get("review") or ""
        stale = (not due and time.time() - a.get("updated", 0) > REVIEW_DAYS * 86400) or (due and due < today)
        acts.append({**a, "system_names": [names.get(s, s.split(":", 1)[-1]) for s in a.get("systems", [])],
                     "legal_labels": [LEGAL_BASES[b] for b in a["legal_bases"]], "review_due": bool(stale)})
    ev = evidence()
    tom = [{"id": a, "label": l, "hint": h, "text": (c["tom"].get(a) or {}).get("text", ""), "updated": (c["tom"].get(a) or {}).get("updated"),
            "evidence": ev.get(a, [])} for a, l, h in TOM_AREAS]
    return {"controller": c["controller"], "activities": acts, "tom": tom, "legal_bases": LEGAL_BASES,
            "targets": [{"target": k, "name": v} for k, v in names.items()],
            "counts": {"activities": len(acts), "review_due": sum(1 for a in acts if a["review_due"]), "special": sum(1 for a in acts if a["special"]),
                       "tom_missing": sum(1 for t in tom if not t["text"])}}


def markdown(part: str = "all") -> str:
    o = overview()
    ctl = o["controller"]
    fmt = lambda ts: time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "–"
    out: List[str] = []
    if part in ("all", "vvt"):
        out += ["# Verzeichnis von Verarbeitungstätigkeiten (Art. 30 DSGVO)", "", f"Stand: {time.strftime('%d.%m.%Y')}", "",
                "## Verantwortlicher", ""]
        for k, label in (("name", "Name"), ("address", "Anschrift"), ("contact", "Kontakt"), ("representative", "Vertreter"), ("dpo", "Datenschutzbeauftragte(r)")):
            out.append(f"- **{label}:** {ctl.get(k) or '–'}")
        for a in o["activities"]:
            out += ["", f"## {a['name']}", ""]
            for k, label in (("purpose", "Zweck"), ("subjects", "Betroffene Personen"), ("categories", "Datenkategorien"), ("recipients", "Empfänger"),
                             ("processors", "Auftragsverarbeiter"), ("third_country", "Übermittlung in Drittländer"), ("retention", "Löschfristen"),
                             ("owner", "Verantwortlich intern")):
                out.append(f"- **{label}:** {a.get(k) or '–'}")
            out.append(f"- **Rechtsgrundlage:** {'; '.join(a['legal_labels'])}" + (f" ({a['legal_note']})" if a.get("legal_note") else ""))
            if a["special"]:
                out.append("- **Besondere Kategorien (Art. 9 DSGVO):** ja")
            out.append(f"- **Systeme:** {', '.join(a['system_names']) or '–'}")
            out.append("- **TOM:** siehe technische und organisatorische Maßnahmen")
            out.append(f"- **Zuletzt geändert:** {fmt(a.get('updated'))}" + (f" · nächste Prüfung {a['review']}" if a.get("review") else ""))
        out.append("")
    if part in ("all", "tom"):
        out += ["# Technische und organisatorische Maßnahmen (Art. 32 DSGVO)", "", f"Stand: {time.strftime('%d.%m.%Y')}" + (f" · {ctl['name']}" if ctl.get("name") else ""), ""]
        for t in o["tom"]:
            out += [f"## {t['label']}", "", t["text"] or "_Noch nicht beschrieben._", ""]
            if t["evidence"]:
                out += ["Vom System festgestellt:", ""] + [f"- {e}" for e in t["evidence"]] + [""]
    return "\n".join(out) + "\n"


def alerts() -> List[Dict[str, Any]]:
    c = _safe(lambda: overview()["counts"], {}) or {}
    if c.get("review_due"):
        return [{"level": "info", "text": f"Datenschutz: {c['review_due']} {'Verarbeitungstätigkeit' if c['review_due'] == 1 else 'Verarbeitungstätigkeiten'} zur Prüfung fällig"}]
    return []
