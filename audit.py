"""
Activity log: who did what in the dashboard, and when.

A middleware in app.py records every changing request (POST) plus file downloads and
terminal sessions automatically. The action label comes from the path (ACTIONS below);
endpoints can add details via `request.state.audit_detail`. Stored in history.db,
kept for 180 days (at most 20 000 entries).
"""

import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

import history

MAX_ROWS = 20000

# (method, path regex) -> (category, label). The first match wins.
ACTIONS: List[Tuple[str, str, str, str]] = [
    ("POST", r"^/api/auth/login$", "login", "Anmeldung"),
    ("POST", r"^/api/auth/logout$", "login", "Abmeldung"),
    ("POST", r"^/api/settings/account$", "settings", "Eigenes Passwort geändert"),
    ("POST", r"^/api/settings/logout-all$", "security", "Alle Benutzer abgemeldet"),
    ("POST", r"^/api/users$", "security", "Benutzer angelegt"),
    ("POST", r"^/api/users/[^/]+/update$", "security", "Benutzer geändert"),
    ("POST", r"^/api/users/[^/]+/delete$", "security", "Benutzer gelöscht"),
    ("POST", r"^/api/groups$", "security", "Gruppe angelegt"),
    ("POST", r"^/api/groups/[^/]+/update$", "security", "Gruppenrechte geändert"),
    ("POST", r"^/api/groups/[^/]+/reset$", "security", "Gruppe zurückgesetzt"),
    ("POST", r"^/api/groups/[^/]+/delete$", "security", "Gruppe gelöscht"),
    ("POST", r"^/api/settings/public-view$", "settings", "Ansehen ohne Login geändert"),
    ("POST", r"^/api/settings/logout-others$", "settings", "Andere Geräte abgemeldet"),
    ("POST", r"^/api/settings/services$", "settings", "Dienst-Kacheln geändert"),
    ("POST", r"^/api/settings/history/clear$", "settings", "Verlauf gelöscht"),
    ("POST", r"^/api/settings/wol/[^/]+/delete$", "settings", "Wake-on-LAN-Gerät entfernt"),
    ("POST", r"^/api/settings/wol$", "settings", "Wake-on-LAN-Gerät hinzugefügt"),
    ("POST", r"^/api/weather/location$", "settings", "Wetter-Ort geändert"),
    ("POST", r"^/api/updates/apt$", "system", "System-Update gestartet"),
    ("POST", r"^/api/updates/check$", "system", "Dashboard-Update gesucht"),
    ("POST", r"^/api/updates/apply$", "system", "Dashboard-Update installiert"),
    ("POST", r"^/api/webhook/github$", "system", "GitHub-Webhook"),
    ("POST", r"^/api/action/reboot$", "system", "Neustart"),
    ("POST", r"^/api/action/shutdown$", "system", "Herunterfahren"),
    ("POST", r"^/api/action/command$", "system", "Diagnose-Befehl"),
    ("POST", r"^/api/services/[^/]+/[^/]+$", "system", "Dienst gesteuert"),
    ("POST", r"^/api/processes/\d+/signal$", "system", "Prozess beendet"),
    ("POST", r"^/api/workloads/docker/", "system", "Docker-Container gesteuert"),
    ("POST", r"^/api/wol/[^/]+/wake$", "network", "Gerät per Wake-on-LAN geweckt"),
    ("POST", r"^/api/wol$", "network", "Gerät per Wake-on-LAN geweckt"),
    ("POST", r"^/api/tools/cpu-mode$", "system", "CPU-Modus geändert"),
    ("POST", r"^/api/tools/benchmark$", "system", "Leistungstest"),
    ("POST", r"^/api/tools/cleanup$", "system", "Speicher aufgeräumt"),
    ("POST", r"^/api/tools/fan-curve/reset$", "system", "Lüfterkurve zurückgesetzt"),
    ("POST", r"^/api/tools/fan-curve$", "system", "Lüfterkurve geändert"),
    ("POST", r"^/api/tools/firewall$", "security", "Firewall geändert"),
    ("POST", r"^/api/tools/fail2ban/unban$", "security", "IP entsperrt (fail2ban)"),
    ("POST", r"^/api/tools/lan/scan$", "network", "Netzwerk gescannt"),
    ("POST", r"^/api/tools/lan/[^/]+/rename$", "network", "Gerät umbenannt"),
    ("POST", r"^/api/tools/lan/[^/]+/forget$", "network", "Gerät vergessen"),
    ("POST", r"^/api/tools/lan/[^/]+/wol$", "network", "Gerät zu Wake-on-LAN hinzugefügt"),
    ("POST", r"^/api/tools/port-test$", "network", "Port-Test"),
    ("POST", r"^/api/tools/dns$", "network", "DNS-Abfrage"),
    ("POST", r"^/api/tools/traceroute$", "network", "Traceroute"),
    ("POST", r"^/api/settings/health/[^/]+/delete$", "settings", "Web-Dienst-Prüfung entfernt"),
    ("POST", r"^/api/settings/health/[^/]+/run$", "settings", "Web-Dienst geprüft"),
    ("POST", r"^/api/settings/health/[^/]+/update$", "settings", "Web-Dienst-Prüfung geändert"),
    ("POST", r"^/api/tools/lan/[^/]+/monitor$", "network", "Gerät wird überwacht"),
    ("POST", r"^/api/settings/health$", "settings", "Web-Dienst-Prüfung hinzugefügt"),
    ("POST", r"^/api/settings/links/[^/]+/delete$", "settings", "Eigener Link entfernt"),
    ("POST", r"^/api/settings/links/[^/]+/move$", "settings", "Eigene Links sortiert"),
    ("POST", r"^/api/settings/links$", "settings", "Eigener Link hinzugefügt"),
    ("POST", r"^/api/terminal/ticket$", "security", "Terminal freigeschaltet"),
    ("POST", r"^/api/actions/[^/]+/run$", "system", "Schnellaktion ausgeführt"),
    ("POST", r"^/api/actions/runs/[^/]+/stop$", "system", "Schnellaktion gestoppt"),
    ("GET", r"^/api/files/download$", "files", "Datei heruntergeladen"),
    ("PUT", r"^/api/files/upload$", "files", "Datei hochgeladen"),
    ("POST", r"^/api/files/share$", "files", "Freigabe-Link erstellt"),
    ("POST", r"^/api/containers/kube/[^/]+/[^/]+/[^/]+/restart$", "system", "Kubernetes-Workload neu gestartet"),
    ("POST", r"^/api/containers/kube/[^/]+/[^/]+/[^/]+/scale$", "system", "Kubernetes-Workload skaliert"),
    ("POST", r"^/api/containers/kube/pod/[^/]+/[^/]+/delete$", "system", "Pod neu erstellt"),
    ("POST", r"^/api/containers/stacks/[^/]+/limits$", "system", "Ressourcen-Limits geändert"),
    ("POST", r"^/api/containers/autoupdate/[^/]+/run$", "system", "Automatisches Update gestartet"),
    ("POST", r"^/api/containers/autoupdate/[^/]+$", "system", "Automatische Updates geändert"),
    ("POST", r"^/api/containers/stacks/[^/]+/[^/]+$", "system", "Compose-Stack gesteuert"),
    ("POST", r"^/api/packages/run$", "system", "Paket installiert/entfernt"),
    ("POST", r"^/api/appstore/[^/]+/install$", "system", "App installiert"),
    ("POST", r"^/api/settings/status-page$", "settings", "Statusseite geändert"),
    ("POST", r"^/api/maintenance$", "system", "Wartungsfenster geplant"),
    ("POST", r"^/api/maintenance/[^/]+/end$", "system", "Wartungsfenster beendet"),
    ("POST", r"^/api/maintenance/[^/]+/delete$", "system", "Wartungsfenster gelöscht"),
    ("POST", r"^/api/remotes$", "settings", "Remote-Pi hinzugefügt"),
    ("POST", r"^/api/settings/netbox$", "settings", "NetBox verbunden"),
    ("POST", r"^/api/boards$", "settings", "Dashboard-Board geändert"),
    ("POST", r"^/api/boards/reset$", "settings", "Dashboard-Boards zurückgesetzt"),
    ("POST", r"^/api/boards/probe$", "network", "Web-Ports eines Geräts geprüft"),
    ("POST", r"^/api/boards/restore$", "settings", "Board-Stand wiederhergestellt"),
    ("POST", r"^/api/exchange$", "files", "Austausch-Link erstellt"),
    ("POST", r"^/api/fleet/apt$", "system", "Updates auf mehreren Pis gestartet"),
    ("POST", r"^/api/fleet/packages$", "system", "Pakete auf mehreren Pis aktualisiert"),
    ("POST", r"^/api/settings/mcp$", "settings", "MCP-Server eingestellt"),
    ("POST", r"^/api/settings/mcp/approval$", "settings", "MCP-Freigaben eingestellt"),
    ("POST", r"^/api/mcp/approvals/[^/]+/approve$", "security", "MCP-Aktion freigegeben"),
    ("POST", r"^/api/mcp/approvals/[^/]+/deny$", "security", "MCP-Aktion abgelehnt"),
    ("POST", r"^/api/settings/email$", "settings", "E-Mail-Benachrichtigungen eingestellt"),
    ("POST", r"^/api/settings/email/test$", "settings", "Test-E-Mail gesendet"),
    ("POST", r"^/api/settings/retention$", "settings", "Datenaufbewahrung geändert"),
    ("POST", r"^/api/settings/retention/cleanup$", "settings", "Alte Daten aufgeräumt"),
    ("POST", r"^/api/backups$", "settings", "Backup-Überwachung angelegt"),
    ("POST", r"^/api/backups/[^/]+/check$", "system", "Backup geprüft"),
    ("POST", r"^/api/backups/[^/]+/delete$", "settings", "Backup-Überwachung entfernt"),
    ("POST", r"^/api/backups/[^/]+$", "settings", "Backup-Überwachung geändert"),
    ("POST", r"^/api/docs/domains/check$", "network", "Domains geprüft"),
    ("POST", r"^/api/docs/domains/[^/]+/delete$", "settings", "Domain entfernt"),
    ("POST", r"^/api/docs/domains(/[^/]+)?$", "settings", "Domain gespeichert"),
    ("POST", r"^/api/containers/images/source$", "settings", "Release-Quelle eines Images gesetzt"),
    ("POST", r"^/api/wiki$", "files", "Wiki-Seite angelegt"),
    ("POST", r"^/api/wiki/[^/]+$", "files", "Wiki-Seite geändert"),
    ("POST", r"^/api/wiki/[^/]+/delete$", "files", "Wiki-Seite gelöscht"),
    ("PUT", r"^/api/docs/attachments/[^/]+/[^/]+$", "files", "Anhang hochgeladen"),
    ("POST", r"^/api/docs/attachments/[^/]+/[^/]+/delete$", "files", "Anhang gelöscht"),
    ("POST", r"^/api/settings/webhooks$", "settings", "Webhook angelegt"),
    ("POST", r"^/api/settings/webhooks/[^/]+/delete$", "settings", "Webhook gelöscht"),
    ("PUT", r"^/api/x/[^/]+/upload$", "files", "Datei über Austausch-Link hochgeladen"),
    ("POST", r"^/api/exchange/[^/]+/delete$", "files", "Austausch-Link beendet"),
    ("POST", r"^/api/settings/netbox/delete$", "settings", "NetBox getrennt"),
    ("POST", r"^/api/netbox/ip$", "network", "IP-Adresse in NetBox angelegt"),
    ("POST", r"^/api/remotes/[^/]+/delete$", "settings", "Remote-Pi entfernt"),
    ("POST", r"^/api/remotes/select$", "settings", "Remote-Pi gewählt"),
    ("POST", r"^/api/tools/geoblock$", "security", "Länder-Sperre geändert"),
    ("POST", r"^/api/tools/geoip/download$", "security", "Länder-Datenbank geladen"),
    ("POST", r"^/api/tools/geoip/remove$", "security", "Länder-Datenbank gelöscht"),
    ("POST", r"^/api/files/rename$", "files", "Umbenannt"),
    ("POST", r"^/api/files/transfer$", "files", "Verschoben/kopiert"),
    ("GET", r"^/api/files/zip/[^/]+$", "files", "ZIP heruntergeladen"),
    ("POST", r"^/api/files/text$", "files", "Datei bearbeitet"),
    ("POST", r"^/api/files/new$", "files", "Datei/Ordner angelegt"),
    ("POST", r"^/api/files/delete$", "files", "In den Papierkorb verschoben"),
    ("POST", r"^/api/files/trash/[^/]+/restore$", "files", "Aus dem Papierkorb wiederhergestellt"),
    ("POST", r"^/api/files/trash/[^/]+/delete$", "files", "Endgültig gelöscht"),
    ("POST", r"^/api/files/trash/empty$", "files", "Papierkorb geleert"),
    ("POST", r"^/api/files/shares/[^/]+/delete$", "files", "Freigabe-Link beendet"),
    ("GET", r"^/s/$", "files", "Download über Freigabe-Link"),
    ("WS", r"^/ws/terminal$", "security", "Terminal-Sitzung"),
]
# Frequent, harmless requests that would flood the log
IGNORE = [r"^/api/diagnostics", r"^/api/processes/search", r"^/api/tools/logs", r"^/api/notes$", r"^/api/appstore/[^/]+/preview$", r"^/api/appstore/refresh$", r"^/api/kanban", r"^/api/reminders", r"^/api/docs/"]

CATEGORIES = {"login": "Anmeldung", "settings": "Einstellungen", "system": "System",
              "security": "Sicherheit", "network": "Netzwerk", "files": "Dateien"}


def _conn() -> sqlite3.Connection:
    conn = history._connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, user TEXT, ip TEXT,
        category TEXT, action TEXT, path TEXT, detail TEXT, status INTEGER)""")
    conn.execute("CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts)")
    return conn


def classify(method: str, path: str) -> Optional[Tuple[str, str]]:
    if any(re.match(p, path) for p in IGNORE):
        return None
    for m, rx, cat, label in ACTIONS:
        if m == method and re.match(rx, path):
            return cat, label
    if method == "POST" and path.startswith("/api/"):
        return "system", f"Aktion {path}"  # unknown, but still worth recording
    return None


def record(user: str, ip: str, method: str, path: str, status: int, detail: str = "") -> None:
    kind = classify(method, path)
    if not kind:
        return
    now = int(time.time())
    with _conn() as conn:
        conn.execute("INSERT INTO audit (ts, user, ip, category, action, path, detail, status) VALUES (?,?,?,?,?,?,?,?)",
                      (now, user or "", ip or "", kind[0], kind[1], path, (detail or "")[:300], status))
        if now % 50 == 0:  # occasional cleanup
            cleanup(conn)


def cleanup(conn=None) -> None:
    """Entries older than the retention "audit" (Einstellungen -> Datenaufbewahrung)."""
    import retention
    if conn is None:
        with _conn() as c:
            return cleanup(c)
    conn.execute("DELETE FROM audit WHERE ts < ?", (int(time.time()) - retention.seconds("audit"),))
    conn.execute("DELETE FROM audit WHERE id <= (SELECT MAX(id) FROM audit) - ?", (MAX_ROWS,))


def entries(query: str = "", category: str = "", limit: int = 200, before: int = 0) -> Dict[str, Any]:
    sql = "SELECT id, ts, user, ip, category, action, detail, status FROM audit WHERE 1=1"
    args: List[Any] = []
    if category in CATEGORIES:
        sql += " AND category = ?"
        args.append(category)
    if before:
        sql += " AND id < ?"
        args.append(before)
    q = query.strip()[:100]
    if q:
        sql += " AND (action LIKE ? OR detail LIKE ? OR user LIKE ? OR ip LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    with _conn() as conn:
        rows = conn.execute(sql, args).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    import geoip
    items = [{"id": i, "ts": ts, "user": u, "ip": ip, "category": c, "action": a, "detail": d,
              "ok": 200 <= (s or 0) < 400, "status": s, "country": geoip.lookup(ip or "")}
             for i, ts, u, ip, c, a, d, s in rows]
    return {"entries": items, "total": total, "categories": CATEGORIES,
            "more": len(items) == max(1, min(limit, 500))}
