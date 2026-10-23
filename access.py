"""
Which permission each request needs - one table for the whole dashboard.

The middleware in app.py asks required_permission(method, path):
  None           -> no extra permission (any logged-in user; public view for plain reads)
  "login"        -> must be logged in, but no special permission (e.g. own password)
  "<permission>" -> the user's group must have it (see auth.PERMISSIONS)
Changing requests (POST/PUT/DELETE) that are not listed need "users.manage", so a new
endpoint is admin-only until it is added here on purpose.
"""

import re
from typing import List, Optional, Tuple

RULES: List[Tuple[str, str, Optional[str]]] = [
    # --- Pages
    ("GET", r"^/system$", "system.view"),
    ("GET", r"^/containers$", "system.view"),
    ("GET", r"^/report$", "system.view"),
    ("GET", r"^/doku$", "login"),
    ("GET", r"^/wissen$", "login"),
    ("GET", r"^/api/wiki(/.*)?$", "login"),
    ("POST", r"^/api/wiki(/.*)?$", "notes.edit"),
    ("GET", r"^/flotte$", "system.view"),
    ("GET", r"^/werkzeuge$", "login"),
    ("GET", r"^/api/fleet$", "system.view"),
    ("POST", r"^/api/fleet/(apt|packages)$", "updates.install"),
    ("GET", r"^/api/fleet/job$", "system.view"),
    ("GET", r"^/api/fleet/(latency|capacity)$", "system.view"),
    ("POST", r"^/api/fleet/latency/measure$", "network.tools"),
    ("POST", r"^/api/fleet/dashboard-update$", "updates.install"),
    ("POST", r"^/api/updates/run-now$", "updates.install"),
    ("GET", r"^/api/(capacity|capacity/summary|anomalies|dns-watch)$", "system.view"),
    ("POST", r"^/api/anomalies/settings$", "settings.manage"),
    ("POST", r"^/api/dns-watch(/.*)?$", "settings.manage"),
    ("GET", r"^/api/incidents$", "login"),
    ("POST", r"^/api/incidents(/.*)?$", "notes.edit"),
    ("GET", r"^/(api-doku|api/api-docs)$", "login"),
    ("POST", r"^/api/containers/docker/cleanup$", "services.control"),
    ("POST", r"^/api/containers/stacks/create$", "apps.install"),
    ("GET", r"^/api/stack-templates$", "system.view"),
    ("POST", r"^/api/stack-templates(/.*)?$", "apps.install"),
    ("GET", r"^/terminal$", "terminal"),
    ("GET", r"^/settings$", "login"),

    # --- Own account (every logged-in user)
    ("POST", r"^/api/auth/(reauth|2fa/(setup|enable|disable|recovery)|sessions/[0-9a-f]{16}/revoke)$", "login"),
    ("GET", r"^/api/auth/(2fa|sessions)$", "login"),
    ("GET", r"^/2fa$", "login"),
    ("*", r"^/api/security(/.*)?$", "users.manage"),
    ("POST", r"^/api/settings/account$", "login"),
    ("POST", r"^/api/settings/logout-others$", "login"),
    ("GET", r"^/api/settings$", "login"),
    ("GET", r"^/api/me$", "login"),

    # --- Users & permissions
    ("*", r"^/api/users(/.*)?$", "users.manage"),
    ("*", r"^/api/groups(/.*)?$", "users.manage"),
    ("POST", r"^/api/settings/logout-all$", "users.manage"),
    ("POST", r"^/api/settings/public-view$", "users.manage"),   # decides who sees the dashboard at all

    # --- Dashboard settings
    ("POST", r"^/api/settings/(services|history/clear|wol|wol/[^/]+/delete|health|health/[^/]+/(delete|run|update)|links|links/[^/]+/(delete|move))$", "settings.manage"),
    ("POST", r"^/api/weather/location$", "settings.manage"),
    ("*", r"^/api/settings/status-page$", "settings.manage"),

    # --- Boards (start page): everyone who sees the dashboard sees them, editing needs its own permission
    ("POST", r"^/api/boards(/reset|/probe|/restore)?$", "board.edit"),
    ("GET", r"^/api/boards/(discover|history)$", "board.edit"),

    # --- NetBox: reading for system viewers, writing IP addresses with its own permission
    ("GET", r"^/api/netbox/(status|data)$", "system.view"),
    ("POST", r"^/api/netbox/ip$", "netbox.write"),
    ("POST", r"^/api/settings/netbox(/test|/delete)?$", "settings.manage"),

    # --- MCP server (OAuth via Pocket ID; the connection settings are admin-only)
    ("*", r"^/api/settings/mcp(/approval|/calls)?$", "users.manage"),
    ("GET", r"^/api/mcp/approvals$", "login"),
    ("POST", r"^/api/mcp/approvals/[^/]+/(approve|deny)$", "login"),   # own requests; others need users.manage (checked there)
    ("*", r"^/api/settings/(email|email/test|retention|retention/cleanup)$", "settings.manage"),
    ("GET", r"^/api/backups$", "system.view"),
    ("POST", r"^/api/backups(/.*)?$", "settings.manage"),
    ("GET", r"^/api/(vulns|eol)$", "system.view"),
    ("POST", r"^/api/(vulns/(scan|ignore)|eol/check)$", "settings.manage"),
    ("GET", r"^/api/selftest$", "settings.manage"),
    ("GET", r"^/api/hardening$", "security.view"),
    ("POST", r"^/api/hardening/run$", "security.view"),
    ("GET", r"^/api/updates/changelog$", "system.view"),
    ("GET", r"^/api/(sbom|sbom/search|sbom/export|fleet/sbom)$", "system.view"),
    ("POST", r"^/api/selftest/(run|repair)$", "settings.manage"),
    ("POST", r"^/api/containers/images/source$", "services.control"),
    ("*", r"^/api/settings/webhooks(/.*)?$", "settings.manage"),

    # --- Remote Pis: switching is for everyone logged in, managing them for admins
    ("GET", r"^/api/remotes/current$", "login"),
    ("POST", r"^/api/remotes/select$", "login"),
    ("GET", r"^/api/remotes$", "users.manage"),
    ("POST", r"^/api/remotes(/.*)?$", "users.manage"),

    # --- Views
    ("GET", r"^/api/tools/(logs|log-units)$", "logs.view"),
    ("GET", r"^/api/logs$", "logs.view"),
    ("GET", r"^/api/services/[^/]+/journal$", "logs.view"),
    ("GET", r"^/api/workloads/docker/[^/]+/logs$", "logs.view"),
    ("GET", r"^/api/containers/kube/logs$", "logs.view"),
    ("GET", r"^/api/containers/logs/multi$", "logs.view"),
    ("GET", r"^/api/containers/.*$", "system.view"),
    ("GET", r"^/api/power$", "system.view"),
    ("GET", r"^/api/tools/(ssh|activity|firewall|firewall/traffic|geoip|geoblock|connections)$", "security.view"),
    ("GET", r"^/api/tools/errors$", "logs.view"),
    ("GET", r"^/api/packages(/.*)?$", "system.view"),
    ("POST", r"^/api/packages/simulate$", "system.view"),
    ("POST", r"^/api/packages/run$", "updates.install"),
    ("GET", r"^/api/(tools/.*|events|report|disk-forecast|monitor/.*|sensors.*)$", "system.view"),
    ("POST", r"^/api/action/command$", "system.view"),        # read-only diagnostics (uptime, df ...)

    # --- Files
    ("GET", r"^/api/files/shares$", "files.share"),
    ("GET", r"^/api/files/trash$", "files.write"),
    ("GET", r"^/api/files/.*$", "files.read"),
    ("POST", r"^/api/files/(text|new|delete|trash/.+|rename|transfer|jobs/[^/]+/cancel)$", "files.write"),
    ("POST", r"^/api/files/(zip|duplicates)$", "files.read"),
    ("PUT", r"^/api/files/upload$", "files.write"),
    ("POST", r"^/api/files/(share|shares/[^/]+/delete)$", "files.share"),
    ("*", r"^/api/exchange(/.*)?$", "files.share"),

    # --- Dashboard cards
    ("POST", r"^/api/notes$", "notes.edit"),
    ("POST", r"^/api/(kanban|reminders)(/.*)?$", "notes.edit"),
    ("POST", r"^/api/docs/.*$", "notes.edit"),
    ("PUT", r"^/api/docs/attachments/[^/]+/[^/]+$", "notes.edit"),
    ("GET", r"^/api/docs/.*$", "login"),
    ("POST", r"^/api/maintenance(/.*)?$", "settings.manage"),
    ("GET", r"^/api/maintenance$", "login"),
    ("GET", r"^/api/(kanban|reminders)$", "login"),
    ("GET", r"^/api/notes$", "login"),
    ("GET", r"^/api/actions(/.*)?$", "actions.run"),
    ("POST", r"^/api/actions/.*$", "actions.run"),
    ("GET", r"^/api/links$", "login"),

    # --- System control
    ("POST", r"^/api/services/[^/]+/[^/]+$", "services.control"),
    ("POST", r"^/api/workloads/docker/[^/]+/[^/]+$", "services.control"),
    ("POST", r"^/api/containers/(kube/.+|stacks/[^/]+/[^/]+|images/check|autoupdate/[^/]+(/run)?)$", "services.control"),
    ("GET", r"^/api/appstore$", "system.view"),
    ("*", r"^/api/appstore/[^/]+/(form|preview|install)$", "apps.install"),
    ("POST", r"^/api/appstore/refresh$", "apps.install"),
    ("GET", r"^/api/appstore/logo/[^/]+$", "system.view"),
    ("POST", r"^/api/power/settings$", "settings.manage"),
    ("POST", r"^/api/processes/\d+/signal$", "processes.kill"),
    ("POST", r"^/api/tools/(cpu-mode|fan-curve|fan-curve/reset|cleanup|benchmark)$", "system.config"),
    ("POST", r"^/api/(updates/apt|updates/apply|updates/check)$", "updates.install"),
    ("POST", r"^/api/action/(reboot|shutdown)$", "system.power"),

    # --- Network
    ("POST", r"^/api/tools/(lan/scan|lan/[^/]+/(rename|forget|wol|monitor)|port-test|dns|traceroute)$", "network.tools"),
    ("POST", r"^/api/wol(/[^/]+/wake)?$", "network.tools"),
    ("POST", r"^/api/tools/(firewall|fail2ban/unban|geoblock|geoip/download|geoip/remove)$", "firewall.manage"),

    # --- Terminal
    ("*", r"^/api/terminal.*$", "terminal"),
]
_COMPILED = [(m, re.compile(rx), perm) for m, rx, perm in RULES]

# Reads that stay open in "view without login" mode (the dashboard itself)
PUBLIC_READ_PREFIXES = ("/api/stats", "/api/system", "/api/processes", "/api/services", "/api/drives", "/api/weather",
                        "/api/history", "/api/homelab", "/api/updates", "/api/nvme", "/api/status-summary", "/api/health",
                        "/api/wol", "/api/workloads", "/api/diagnostics", "/api/about", "/api/capabilities",
                        "/api/replay", "/api/boards")


def required_permission(method: str, path: str) -> Optional[str]:
    for m, rx, perm in _COMPILED:
        if (m == "*" or m == method) and rx.match(path):
            return perm
    if method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/"):
        return "users.manage"   # not classified yet: admins only
    if method == "GET" and path.startswith("/api/") and not path.startswith(PUBLIC_READ_PREFIXES):
        return "login"
    return None
