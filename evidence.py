"""
Evidence package for auditors (Compliance -> Nachweis-Paket): one ZIP with what an auditor or
customer asks for, for a chosen period.

Contents: overview (README), baseline check, access reviews of the period plus the current
state, change requests, incidents with post-mortems and problems, the activity log of the period
with the integrity check of the whole hash chain, backups, vulnerabilities and patch deadlines,
hardening / container / secret findings (masked), emergency handbook, records of processing and
TOM. Every file is listed with its SHA-256 in MANIFEST.sha256; each export is logged.

Never contained: passwords, tokens, the secret values themselves, session data.
"""

import csv
import hashlib
import io
import json
import platform
import time
import zipfile
from typing import Any, Callable, Dict, List, Tuple

import settings_store

KEY = "evidence_exports"
MAX_DAYS = 3 * 366


class EvidenceError(Exception):
    pass


def _fmt(ts: Any, with_time: bool = False) -> str:
    return time.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y", time.localtime(ts)) if ts else "–"


def _activity_csv(since: int, until: int) -> Tuple[str, int]:
    import audit
    with audit._conn() as conn:
        rows = conn.execute("SELECT id, ts, user, ip, category, action, path, detail, status, hash FROM audit WHERE ts >= ? AND ts < ? ORDER BY id",
                            (since, until)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["ID", "Zeit", "Benutzer", "Adresse", "Bereich", "Aktion", "Pfad", "Detail", "Status", "Hash"])
    for r in rows:
        w.writerow([r[0], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[1])), *r[2:]])
    return buf.getvalue(), len(rows)


def _changes_md(since: int, until: int) -> str:
    import changes
    items = [c for c in changes.overview()["changes"] if since <= c["created"] < until or any(since <= t["ts"] < until for t in c.get("timeline", []))]
    out = [f"# Änderungsanträge {_fmt(since)} – {_fmt(until)}", "", f"{len(items)} Anträge im Zeitraum.", "",
           "| Titel | Risiko | Status | Ergebnis | Beantragt | Genehmigt von |", "|---|---|---|---|---|---|"]
    out += [f"| {c['title']} | {c['risk_label']} | {c['status_label']} | {c['result_label'] or '–'} | {c['user']} ({_fmt(c['created'])}) | {c.get('approved_by') or '–'} |"
            for c in items]
    for c in items:
        out += ["", "---", "", changes.markdown(c["id"])]
    return "\n".join(out) + "\n"


def _incidents_md(since: int, until: int) -> str:
    import incidents
    import problems
    items = [i for i in incidents.overview()["incidents"] if since <= i["started"] < until]
    out = [f"# Störungen {_fmt(since)} – {_fmt(until)}", "", f"{len(items)} Störungen im Zeitraum.", "",
           "| Störung | Schwere | Beginn | Dauer | Nachbetrachtung |", "|---|---|---|---|---|"]
    out += [f"| {i['title']} | {i['severity_label']} | {_fmt(i['started'], True)} | {round(i['duration'] / 3600, 1)} Std. | {'ja' if i.get('postmortem') else 'nein'} |"
            for i in items]
    for i in items:
        try:
            out += ["", "---", "", incidents.markdown(i["id"])]
        except Exception:
            pass
    out += ["", "---", "", problems.markdown()]
    return "\n".join(out) + "\n"


def _backups_md() -> str:
    import backups
    o = backups.overview()
    out = ["# Datensicherung", "", f"Stand: {_fmt(time.time(), True)}", "", "| Sicherung | Art | Ziel | Letzte Sicherung | Höchstalter | Zustand |", "|---|---|---|---|---|---|"]
    out += [f"| {j['name']} | {j['kind']} | {j['target']} | {_fmt(j.get('last_backup'), True)} | {j.get('max_age_hours', 26)} Std. | {j['state']} |" for j in o["jobs"]]
    if not o["jobs"]:
        out.append("| – | – | – | – | – | keine Sicherung überwacht |")
    return "\n".join(out) + "\n"


def _security_md() -> str:
    import hardening
    import imagepolicy
    import secretscan
    import stackcheck
    hd = hardening.overview()
    out = ["# Technische Prüfungen", "", f"Stand: {_fmt(time.time(), True)}", "", f"## Härtung ({hd['score'] if hd['score'] is not None else '–'} %)", "",
           f"Geprüft: {_fmt(hd['checked'], True)}", "", "| Bereich | Prüfung | Ergebnis | Detail |", "|---|---|---|---|"]
    out += [f"| {r['group']} | {r['title']} | {r['state']} | {(r['detail'] or '').replace('|', '/')} |" for r in hd["results"]]
    sc = stackcheck.overview()
    out += ["", f"## Compose-Sicherheitscheck ({sc['score'] if sc['score'] is not None else '–'} von 100)", "", "| Stack | Bewertung | Kritisch | Warnungen |", "|---|---|---|---|"]
    out += [f"| {s['name']} | {s['score'] if s['score'] is not None else '–'} | {s.get('counts', {}).get('critical', 0)} | {s.get('counts', {}).get('warning', 0)} |" for s in sc["stacks"]]
    ip = imagepolicy.audit()
    out += ["", f"## Image-Richtlinien (Modus: {imagepolicy.MODES[ip['config']['mode']]})", "",
            f"{ip['counts']['containers']} Container geprüft, {ip['counts']['violating']} mit Verstoß, {ip['counts']['excepted']} ausgenommen."]
    ss = secretscan.overview()
    out += ["", "## Geheimnis-Scan", "", f"Gescannt: {_fmt(ss['checked'], True)} · kritisch {ss['counts']['critical']}, Warnungen {ss['counts']['warning']}, "
            f"akzeptiert {ss['ignored']}. Auszüge maskiert.", "", "| Stufe | Art | Fundstelle | Auszug | Akzeptiert |", "|---|---|---|---|---|"]
    out += [f"| {f['level']} | {f['label']} | {f['location']}{':' + str(f['line']) if f['line'] else ''} | {f['excerpt']} | {'ja' if f['ignored'] else 'nein'} |"
            for f in ss["findings"][:300]]
    return "\n".join(out) + "\n"


def _access_files(since: int, until: int) -> List[Tuple[str, str]]:
    import accessreview
    files = []
    for r in accessreview._cfg()["reviews"]:
        if since <= r["ts"] < until:
            files.append((f"03-rechte-reviews/review-{time.strftime('%Y-%m-%d', time.localtime(r['ts']))}-{r['id']}.md", accessreview.review_markdown(r["id"])))
    files.append(("03-rechte-reviews/aktueller-stand.csv", accessreview.csv_export()))
    return files


def build(since: int, until: int, user: str = "") -> Tuple[bytes, str]:
    if until <= since:
        raise EvidenceError("Das Ende muss nach dem Beginn liegen.")
    if until - since > MAX_DAYS * 86400:
        raise EvidenceError("Höchstens drei Jahre auf einmal.")
    import audit
    import baseline
    import drhandbook
    import patchsla
    import privacy
    files: List[Tuple[str, str]] = []
    problems_log: List[str] = []

    def add(name: str, fn: Callable[[], str]) -> None:
        try:
            files.append((name, fn()))
        except Exception as e:
            problems_log.append(f"{name}: {e}")

    add("01-grundschutz-check.md", baseline.markdown)
    add("02-patch-fristen.md", patchsla.markdown)
    try:
        files += _access_files(since, until)
    except Exception as e:
        problems_log.append(f"Rechte-Reviews: {e}")
    add("04-aenderungsantraege.md", lambda: _changes_md(since, until))
    add("05-stoerungen-und-probleme.md", lambda: _incidents_md(since, until))
    activity_rows = 0
    try:
        text, activity_rows = _activity_csv(since, until)
        files.append(("06-aktivitaetsprotokoll.csv", "﻿" + text))
        v = audit.verify()
        files.append(("06-aktivitaetsprotokoll-integritaet.json", json.dumps(v, ensure_ascii=False, indent=2)))
    except Exception as e:
        problems_log.append(f"Aktivitätsprotokoll: {e}")
    add("07-datensicherung.md", _backups_md)
    add("08-technische-pruefungen.md", _security_md)
    add("09-notfallhandbuch.md", drhandbook.markdown)
    add("10-verzeichnis-verarbeitungstaetigkeiten.md", lambda: privacy.markdown("vvt"))
    add("11-technisch-organisatorische-massnahmen.md", lambda: privacy.markdown("tom"))

    host = platform.node()
    readme = [f"# Nachweis-Paket – {host}", "", f"- Zeitraum: {_fmt(since)} bis {_fmt(until - 1)}", f"- Erstellt: {_fmt(time.time(), True)} von {user or '–'}",
              "- Erzeugt vom PiCore Dashboard. Enthält keine Passwörter, Tokens oder Sitzungsdaten; Fundstellen des Geheimnis-Scans sind maskiert.",
              f"- Einträge im Aktivitätsprotokoll im Zeitraum: {activity_rows}", "", "## Inhalt", ""]
    readme += [f"- `{name}`" for name, _ in files]
    readme += ["", "Die SHA-256-Prüfsummen aller Dateien stehen in `MANIFEST.sha256` (prüfbar mit `sha256sum -c MANIFEST.sha256`)."]
    if problems_log:
        readme += ["", "## Nicht enthalten (Fehler beim Erzeugen)", ""] + [f"- {p}" for p in problems_log]
    files.insert(0, ("README.md", "\n".join(readme) + "\n"))

    buf = io.BytesIO()
    manifest = []
    stamp = time.localtime()[:6]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files:
            data = text.encode("utf-8")
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
        z.writestr(zipfile.ZipInfo("MANIFEST.sha256", date_time=stamp), ("\n".join(manifest) + "\n").encode())
    content = buf.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    log = (settings_store.get(KEY, []) or [])[-99:]
    log.append({"ts": int(time.time()), "user": user, "since": since, "until": until, "files": len(files) + 1, "size": len(content),
                "sha256": digest, "problems": problems_log[:10]})
    settings_store.set(KEY, log)
    name = f"nachweise-{host}-{time.strftime('%Y%m%d', time.localtime(since))}-{time.strftime('%Y%m%d', time.localtime(until - 1))}.zip"
    return content, name


def history() -> Dict[str, Any]:
    return {"exports": list(reversed(settings_store.get(KEY, []) or []))[:30]}
