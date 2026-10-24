"""
Baseline check (Compliance -> Grundschutz-Check): a checklist modelled on BSI IT-Grundschutz
modules and CIS controls - not a certification, but a structured view of how well the basics are
covered on this server.

- Requirements grouped by area, each with the Grundschutz module it leans on (e.g. ORP.4,
  OPS.1.1.3, CON.3, DER.4, SYS.1.6). Most are rated automatically from what the dashboard
  already checks (hardening, 2FA policy, access review, backups, patch deadlines, secret scan,
  image policies, activity log integrity, emergency handbook, records of processing ...).
- Every requirement can be assessed by hand (fulfilled / partly / not / not applicable) with
  a note and evidence - that rating wins; a contradicting automatic result is pointed out.
- Fulfilment per area and in total; export as Markdown.
"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import settings_store

KEY = "baseline"
STATES = {"ok": "Erfüllt", "partial": "Teilweise", "fail": "Nicht erfüllt", "na": "Entbehrlich", "open": "Offen"}
VALUE = {"ok": 1.0, "partial": 0.5, "fail": 0.0}
_cache: Dict[str, Any] = {"ts": 0.0, "auto": None}


class BaselineError(Exception):
    pass


def _mod(name: str):
    return __import__(name)


def _hd(cid: str) -> Optional[Tuple[str, str]]:
    r = next((x for x in _mod("hardening").overview()["results"] if x["id"] == cid), None)
    if not r or r["state"] == "skip":
        return None
    return ("ok" if r["state"] == "ok" else "fail"), f"Härtungs-Check: {r['title']} – {r['detail'] or ('in Ordnung' if r['state'] == 'ok' else 'Mangel')}"


# ---------------------------------------------------------------------------
# Automatic ratings: fn() -> (state, evidence) or None (not determinable)
# ---------------------------------------------------------------------------
def a_twofa():
    p = _mod("websec").config()["twofa"]
    return ("ok" if p != "none" else "fail"), f"Zwei-Faktor-Regel: {_mod('websec').TWOFA_POLICIES[p]}"


def a_admins_2fa():
    o = _mod("accessreview").state()
    n = sum(1 for u in o["users"] for f in u["findings"] if f["text"] == "Administrator ohne zweiten Faktor")
    return ("ok" if not n else "fail"), ("Alle Administratoren mit zweitem Faktor" if not n else f"{n} Administrator(en) ohne zweiten Faktor")


def a_review():
    o = _mod("accessreview").overview()
    if not o["last_review"]:
        return "fail", "Noch kein Rechte-Review abgezeichnet"
    return ("partial" if o["overdue"] else "ok"), f"Letztes Rechte-Review {time.strftime('%d.%m.%Y', time.localtime(o['last_review']['ts']))}" + (" – fällig" if o["overdue"] else "")


def a_inactive():
    o = _mod("accessreview").state()
    n = sum(1 for u in o["users"] for f in u["findings"] if "angemeldet" in f["text"])
    return ("ok" if not n else "partial"), ("Keine inaktiven Konten" if not n else f"{n} inaktive oder nie genutzte Konten")


def a_passwords():
    import auth
    return "ok", f"Mindestens {auth.MIN_PASSWORD_LENGTH} Zeichen, bekannte Passwörter gesperrt, scrypt-Hash, Sperre nach Fehlversuchen"


def a_restrict():
    c = _mod("websec").config()
    return ("ok" if c["restrict"] else "partial"), ("Kritische Bereiche nur aus Heimnetz/VPN" if c["restrict"] else "Kritische Bereiche auch aus dem Internet erreichbar")


def a_reauth():
    c = _mod("websec").config()
    return ("ok" if c["reauth"] else "fail"), ("Erneute Bestätigung vor kritischen Änderungen aktiv" if c["reauth"] else "Keine erneute Bestätigung")


def a_vulnscan():
    v = _mod("vulns").overview()
    if not v["tools"].get("debsecan") and not v["tools"].get("trivy"):
        return "fail", "Weder debsecan noch Trivy installiert"
    age = time.time() - (v["checked"] or 0)
    return ("ok" if age < 3 * 86400 else "partial"), (f"Letzter Scan {time.strftime('%d.%m.%Y', time.localtime(v['checked']))}" if v["checked"] else "Noch kein Scan")


def a_patchsla():
    o = _mod("patchsla").overview()
    if not o["scanned"]:
        return None
    n = o["counts"]["overdue"]
    crit = sum(1 for i in o["items"] if i["state"] == "overdue" and i["severity"] == "critical")
    return ("ok" if not n else "fail" if crit else "partial"), ("Keine Lücke über der Frist" if not n else f"{n} {'Lücke' if n == 1 else 'Lücken'} über der Frist") + (
        f", Einhaltung {o['compliance']} %" if o["compliance"] is not None else "")


def a_unattended():
    return _hd("unattended")


def a_changes():
    o = _mod("changes").overview()
    recent = [c for c in o["changes"] if c["created"] > time.time() - 180 * 86400]
    if not recent:
        return "partial", "Keine Änderungsanträge in den letzten 180 Tagen"
    approved = [c for c in recent if c.get("approved_by")]
    return ("ok" if approved else "partial"), f"{len(recent)} {'Änderungsantrag' if len(recent) == 1 else 'Änderungsanträge'} in 180 Tagen, {len(approved)} freigegeben"


def a_eol():
    o = _mod("eol").overview()
    if not o["checked"]:
        return None
    ended = sum(1 for i in o["items"] if i["state"] == "expired")
    return ("ok" if not ended else "fail"), ("Alle Komponenten mit Support" if not ended else f"{ended} Komponenten ohne Support")


def a_backups():
    o = _mod("backups").overview()
    if not o["jobs"]:
        return "fail", "Keine Datensicherung überwacht"
    return ("ok" if not o["problems"] else "partial"), f"{len(o['jobs'])} Sicherungen, {o['problems']} mit Problem"


def a_dumps():
    hb = _mod("drhandbook").build()
    gaps = [g for s in hb["services"] for g in s["gaps"] if "Dump" in g]
    dbs = sum(len(s["databases"]) for s in hb["services"])
    if not dbs:
        return "na", "Keine Datenbank-Container"
    return ("ok" if not gaps else "partial"), (f"{dbs} Datenbanken mit aktuellem Dump" if not gaps else "; ".join(gaps[:3]))


def a_handbook():
    f = _mod("drhandbook").facts()
    if not f["services"]:
        return None
    s = f["score"] or 0
    return ("ok" if s >= 80 else "partial" if s >= 40 else "fail"), f"Notfallhandbuch: {f['complete']} von {f['services']} Diensten vollständig"


def a_handbook_export():
    f = _mod("drhandbook").facts()
    ts = f.get("exported")
    if not ts:
        return "fail", "Notfallhandbuch noch nie exportiert"
    return ("ok" if time.time() - ts < 90 * 86400 else "partial"), f"Zuletzt exportiert {time.strftime('%d.%m.%Y', time.localtime(ts))}"


def a_contacts():
    hb = _mod("drhandbook").build()
    n = len(hb["general"].get("contacts") or [])
    return ("ok" if n else "fail"), (f"{n} Notfallkontakte" if n else "Keine Notfallkontakte hinterlegt")


def a_hardening():
    o = _mod("hardening").overview()
    if o["score"] is None:
        return None
    return ("ok" if o["score"] >= 90 else "partial" if o["score"] >= 70 else "fail"), f"Härtungs-Check: {o['score']} %"


def a_ssh():
    return _hd("ssh-password")


def a_firewall():
    return _hd("ufw")


def a_stackcheck():
    o = _mod("stackcheck").overview()
    if not o["checked"] or o["score"] is None:
        return None
    return ("ok" if not o["critical"] else "fail"), f"Compose-Sicherheitscheck: {o['score']} von 100, {o['critical']} kritische Befunde"


def a_imagepolicy():
    m = _mod("imagepolicy").config()["mode"]
    return {"block": "ok", "warn": "partial", "off": "fail"}[m], f"Image-Richtlinien: {_mod('imagepolicy').MODES[m]}"


def a_secrets():
    o = _mod("secretscan").overview()
    if not o["checked"]:
        return None
    c = o["counts"]
    return ("ok" if not c["critical"] and not c["warning"] else "fail" if c["critical"] else "partial"), f"Geheimnis-Scan: {c['critical']} kritisch, {c['warning']} Warnungen"


def a_auditlog():
    v = _mod("audit").verify()
    return ("ok" if v.get("ok") else "fail"), (f"Aktivitätsprotokoll: {v.get('checked', 0)} Einträge, Kette unversehrt" if v.get("ok") else "Aktivitätsprotokoll: Kette beschädigt")


def a_alerting():
    a = _mod("alerting").public()
    mail = _mod("notify").public()
    if a["enabled"]:
        return "ok", "Alarmierung mit Eskalation eingerichtet"
    return ("partial" if mail["enabled"] else "fail"), ("Nur E-Mail-Benachrichtigung" if mail["enabled"] else "Keine Alarmierung eingerichtet")


def a_postmortems():
    o = _mod("incidents").overview()
    heavy = [i for i in o["incidents"] if not i["open"] and i["severity"] == "schwer"]
    if not heavy:
        return "ok", "Keine schweren Störungen ohne Nachbetrachtung"
    missing = [i for i in heavy if not i.get("postmortem")]
    return ("ok" if not missing else "partial"), f"{len(heavy) - len(missing)} von {len(heavy)} schweren Störungen nachbetrachtet"


def a_problems():
    o = _mod("problems").overview()
    if o["suggestions"]:
        return "partial", f"{len(o['suggestions'])} Dienste mit wiederkehrenden Störungen ohne Problem"
    return "ok", f"{len(o['problems'])} Probleme erfasst, keine offenen Häufungen"


def a_owners():
    hb = _mod("drhandbook").build()
    stacks = [s for s in hb["services"] if s["kind"] == "stack"]
    if not stacks:
        return None
    missing = [s for s in stacks if any(g.startswith(("Kein Steckbrief", "Niemand")) for g in s["gaps"])]
    return ("ok" if not missing else "partial"), f"{len(stacks) - len(missing)} von {len(stacks)} Stacks mit Zuständigem"


def a_vvt():
    o = _mod("privacy").overview()
    if not o["counts"]["activities"]:
        return "fail", "Kein Eintrag im Verzeichnis der Verarbeitungstätigkeiten"
    due = o["counts"]["review_due"]
    return ("ok" if not due else "partial"), f"{o['counts']['activities']} Verarbeitungstätigkeiten, {due} zur Prüfung fällig"


def a_tom():
    o = _mod("privacy").overview()
    miss = o["counts"]["tom_missing"]
    return ("ok" if not miss else "partial" if miss < len(o["tom"]) else "fail"), (
        "Alle TOM-Bereiche beschrieben" if not miss else f"{miss} von {len(o['tom'])} TOM-Bereichen ohne Beschreibung")


# (id, area, module, title, description, auto)
CONTROLS: List[Tuple[str, str, str, str, str, Optional[Callable]]] = [
    ("IAM-1", "Identität & Zugriff", "ORP.4", "Zwei-Faktor-Anmeldung vorgeschrieben", "Mindestens für Administratoren ist ein zweiter Faktor Pflicht.", a_twofa),
    ("IAM-2", "Identität & Zugriff", "ORP.4", "Administratoren mit zweitem Faktor", "Kein Administratorkonto ohne TOTP, Passkey oder SSO mit 2FA.", a_admins_2fa),
    ("IAM-3", "Identität & Zugriff", "ORP.4", "Regelmäßige Rechteprüfung", "Zugänge und Rechte werden regelmäßig geprüft und abgezeichnet.", a_review),
    ("IAM-4", "Identität & Zugriff", "ORP.4", "Nicht benötigte Konten deaktiviert", "Inaktive und nie genutzte Konten werden deaktiviert oder gelöscht.", a_inactive),
    ("IAM-5", "Identität & Zugriff", "ORP.4", "Passwortregeln", "Ausreichend lange, nicht triviale Passwörter; Schutz vor Raten.", a_passwords),
    ("IAM-6", "Identität & Zugriff", "ORP.4", "Administration nur aus vertrauenswürdigen Netzen", "Kritische Funktionen nur aus Heimnetz oder VPN.", a_restrict),
    ("IAM-7", "Identität & Zugriff", "ORP.4", "Bestätigung kritischer Änderungen", "Kritische Änderungen verlangen eine erneute Anmeldung.", a_reauth),
    ("PAT-1", "Patch- & Änderungsmanagement", "OPS.1.1.3", "Schwachstellen werden erkannt", "Pakete und Container-Images werden regelmäßig auf bekannte Lücken geprüft.", a_vulnscan),
    ("PAT-2", "Patch- & Änderungsmanagement", "OPS.1.1.3", "Patch-Fristen eingehalten", "Lücken mit verfügbarem Fix werden innerhalb festgelegter Fristen behoben.", a_patchsla),
    ("PAT-3", "Patch- & Änderungsmanagement", "OPS.1.1.3", "Automatische Sicherheitsupdates", "Sicherheitsupdates des Betriebssystems werden automatisch eingespielt.", a_unattended),
    ("PAT-4", "Patch- & Änderungsmanagement", "OPS.1.1.3", "Geregeltes Änderungsverfahren", "Änderungen werden beantragt, bewertet und freigegeben.", a_changes),
    ("PAT-5", "Patch- & Änderungsmanagement", "OPS.1.1.3", "Keine Software ohne Herstellerunterstützung", "Betriebssystem, Laufzeiten und Images haben noch Support.", a_eol),
    ("BCK-1", "Datensicherung & Notfall", "CON.3", "Datensicherung eingerichtet und aktuell", "Alle wichtigen Daten werden gesichert; Sicherungen werden überwacht.", a_backups),
    ("BCK-2", "Datensicherung & Notfall", "CON.3", "Datenbanken konsistent gesichert", "Datenbanken werden per Dump gesichert, nicht nur als Dateien.", a_dumps),
    ("BCK-3", "Datensicherung & Notfall", "CON.3", "Wiederherstellung getestet", "Die Rücksicherung wurde mindestens einmal im Jahr geprobt (manuell bewerten).", None),
    ("BCK-4", "Datensicherung & Notfall", "DER.4", "Wiederanlauf dokumentiert", "Für jeden Dienst ist beschrieben, wie er nach einem Ausfall zurückkommt.", a_handbook),
    ("BCK-5", "Datensicherung & Notfall", "DER.4", "Notfalldokumentation offline verfügbar", "Eine aktuelle Kopie des Notfallhandbuchs liegt außerhalb des Systems.", a_handbook_export),
    ("BCK-6", "Datensicherung & Notfall", "DER.4", "Notfallkontakte festgelegt", "Wer im Notfall zu informieren ist, ist festgehalten.", a_contacts),
    ("SYS-1", "Server & Container", "SYS.1.3", "Grundhärtung des Servers", "SSH, Konten, Netzdienste und Updates sind sicher eingestellt.", a_hardening),
    ("SYS-2", "Server & Container", "SYS.1.3", "SSH nur mit Schlüssel", "Passwort-Anmeldung per SSH ist abgeschaltet.", a_ssh),
    ("SYS-3", "Server & Container", "NET.3.2", "Paketfilter aktiv", "Eingehende Verbindungen werden durch eine Firewall begrenzt.", a_firewall),
    ("SYS-4", "Server & Container", "SYS.1.6", "Sichere Container-Konfiguration", "Keine privilegierten Container, kein Docker-Socket, keine Klartext-Passwörter in Compose-Dateien.", a_stackcheck),
    ("SYS-5", "Server & Container", "SYS.1.6", "Vertrauenswürdige Images", "Images nur aus freigegebenen Quellen und mit fester Version.", a_imagepolicy),
    ("SYS-6", "Server & Container", "CON.1", "Keine Geheimnisse im Klartext", "Schlüssel und Passwörter liegen nicht in Konfigurationen, Skripten oder Doku.", a_secrets),
    ("SYS-7", "Server & Container", "INF.2", "Physische Sicherheit", "Server steht geschützt, Zugang ist geregelt, Stromversorgung abgesichert (manuell bewerten).", None),
    ("LOG-1", "Protokollierung & Vorfälle", "OPS.1.1.5", "Manipulationssichere Protokollierung", "Administrative Aktionen werden nachvollziehbar und gegen Änderung gesichert protokolliert.", a_auditlog),
    ("LOG-2", "Protokollierung & Vorfälle", "DER.1", "Alarmierung bei Störungen", "Kritische Ereignisse erreichen zuverlässig eine zuständige Person.", a_alerting),
    ("LOG-3", "Protokollierung & Vorfälle", "DER.2.1", "Nachbereitung von Vorfällen", "Schwere Störungen werden nachbetrachtet, Maßnahmen verfolgt.", a_postmortems),
    ("LOG-4", "Protokollierung & Vorfälle", "DER.2.1", "Ursachen wiederkehrender Störungen", "Wiederkehrende Störungen werden als Problem analysiert.", a_problems),
    ("ORG-1", "Organisation & Datenschutz", "ORP.1", "Zuständigkeiten festgelegt", "Für jeden Dienst ist eine zuständige Person benannt.", a_owners),
    ("ORG-2", "Organisation & Datenschutz", "CON.2", "Verzeichnis der Verarbeitungstätigkeiten", "Verarbeitungen personenbezogener Daten sind dokumentiert und aktuell.", a_vvt),
    ("ORG-3", "Organisation & Datenschutz", "CON.2", "Technisch-organisatorische Maßnahmen", "Die TOM sind beschrieben.", a_tom),
    ("ORG-4", "Organisation & Datenschutz", "OPS.2.3", "Verträge zur Auftragsverarbeitung", "Mit Dienstleistern, die personenbezogene Daten verarbeiten, bestehen AV-Verträge (manuell bewerten).", None),
]
AREAS = list(dict.fromkeys(c[1] for c in CONTROLS))


def _auto() -> Dict[str, Optional[Tuple[str, str]]]:
    """Automatic ratings, cached for two minutes (several of them walk logs and scans)."""
    if _cache["auto"] is not None and time.time() - _cache["ts"] < 120:
        return _cache["auto"]
    out: Dict[str, Optional[Tuple[str, str]]] = {}
    for cid, _, _, _, _, fn in CONTROLS:
        if not fn:
            out[cid] = None
            continue
        try:
            out[cid] = fn()
        except Exception as e:
            out[cid] = None
            out[f"{cid}:error"] = ("", str(e)[:120])
    _cache.update(ts=time.time(), auto=out)
    return out


def _manual() -> Dict[str, Dict[str, Any]]:
    c = settings_store.get(KEY) or {}
    return dict((c if isinstance(c, dict) else {}).get("manual") or {})


def assess(cid: str, state: str, note: str, evidence: str, user: str = "") -> Dict[str, Any]:
    if cid not in {c[0] for c in CONTROLS}:
        raise BaselineError("Unbekannte Anforderung.")
    if state not in ("ok", "partial", "fail", "na", ""):
        raise BaselineError("Unbekannte Bewertung.")
    note, evidence = str(note or "").strip(), str(evidence or "").strip()
    if len(note) > 2000 or len(evidence) > 2000:
        raise BaselineError("Notiz und Nachweis: je höchstens 2000 Zeichen.")
    if state == "na" and not note:
        raise BaselineError("Bitte begründen, warum die Anforderung entbehrlich ist.")
    c = settings_store.get(KEY) or {}
    manual = dict(c.get("manual") or {})
    if state:
        manual[cid] = {"state": state, "note": note, "evidence": evidence, "ts": int(time.time()), "user": user}
    else:
        manual.pop(cid, None)
    settings_store.set(KEY, {**c, "manual": manual})
    return overview()


def overview(refresh: bool = False) -> Dict[str, Any]:
    if refresh:
        _cache["auto"] = None
    auto = _auto()
    manual = _manual()
    items = []
    for cid, area, module, title, desc, fn in CONTROLS:
        a = auto.get(cid)
        m = manual.get(cid)
        state = m["state"] if m else (a[0] if a else "open")
        conflict = bool(m and a and m["state"] in ("ok", "partial") and a[0] == "fail")
        err = auto.get(f"{cid}:error")
        items.append({"id": cid, "area": area, "module": module, "title": title, "description": desc, "automatic": fn is not None,
                      "auto_state": a[0] if a else None, "auto_evidence": a[1] if a else (f"Nicht ermittelbar: {err[1]}" if err else None),
                      "manual": m, "state": state, "state_label": STATES[state], "conflict": conflict})
    areas = []
    for name in AREAS:
        rated = [i for i in items if i["area"] == name and i["state"] in VALUE]
        areas.append({"name": name, "total": sum(1 for i in items if i["area"] == name),
                      "score": round(100 * sum(VALUE[i["state"]] for i in rated) / len(rated)) if rated else None,
                      "open": sum(1 for i in items if i["area"] == name and i["state"] == "open"),
                      "failed": sum(1 for i in items if i["area"] == name and i["state"] == "fail")})
    rated = [i for i in items if i["state"] in VALUE]
    return {"items": items, "areas": areas, "states": STATES,
            "score": round(100 * sum(VALUE[i["state"]] for i in rated) / len(rated)) if rated else None,
            "counts": {s: sum(1 for i in items if i["state"] == s) for s in STATES}, "checked": int(_cache["ts"])}


def markdown() -> str:
    o = overview()
    out = ["# Grundschutz-Check", "", f"Stand: {time.strftime('%d.%m.%Y %H:%M')} · Erfüllungsgrad {o['score'] if o['score'] is not None else '–'} %", "",
           "> Angelehnt an Bausteine des BSI IT-Grundschutz-Kompendiums und die CIS Controls. Keine Zertifizierung.", "",
           "| Bereich | Erfüllungsgrad | Nicht erfüllt | Offen |", "|---|---|---|---|"]
    out += [f"| {a['name']} | {a['score'] if a['score'] is not None else '–'} % | {a['failed']} | {a['open']} |" for a in o["areas"]]
    for area in AREAS:
        out += ["", f"## {area}", "", "| Nr. | Baustein | Anforderung | Bewertung | Nachweis |", "|---|---|---|---|---|"]
        for i in [x for x in o["items"] if x["area"] == area]:
            ev = []
            if i["auto_evidence"]:
                ev.append(f"automatisch: {i['auto_evidence']}")
            if i["manual"]:
                m = i["manual"]
                ev.append(f"manuell ({m['user']}, {time.strftime('%d.%m.%Y', time.localtime(m['ts']))}): " + " ".join(x for x in (m["note"], m["evidence"]) if x))
            out.append(f"| {i['id']} | {i['module']} | {i['title']} | {i['state_label']} | {'; '.join(ev).replace('|', '/') or '–'} |")
    return "\n".join(out) + "\n"

