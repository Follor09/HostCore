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
    ("GET", r"^/terminal$", "terminal"),
    ("GET", r"^/settings$", "login"),

    # --- Own account (every logged-in user)
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
    ("POST", r"^/api/settings/(services|history/clear|wol|wol/[^/]+/delete|health|health/[^/]+/(delete|run)|links|links/[^/]+/(delete|move))$", "settings.manage"),
    ("POST", r"^/api/weather/location$", "settings.manage"),

    # --- Views
    ("GET", r"^/api/tools/(logs|log-units)$", "logs.view"),
    ("GET", r"^/api/logs$", "logs.view"),
    ("GET", r"^/api/services/[^/]+/journal$", "logs.view"),
    ("GET", r"^/api/workloads/docker/[^/]+/logs$", "logs.view"),
    ("GET", r"^/api/containers/kube/logs$", "logs.view"),
    ("GET", r"^/api/containers/.*$", "system.view"),
    ("GET", r"^/api/tools/(ssh|activity|firewall|firewall/traffic|geoip|geoblock)$", "security.view"),
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

    # --- Dashboard cards
    ("POST", r"^/api/notes$", "notes.edit"),
    ("GET", r"^/api/notes$", "login"),
    ("GET", r"^/api/actions(/.*)?$", "actions.run"),
    ("POST", r"^/api/actions/.*$", "actions.run"),
    ("GET", r"^/api/links$", "login"),

    # --- System control
    ("POST", r"^/api/services/[^/]+/[^/]+$", "services.control"),
    ("POST", r"^/api/workloads/docker/[^/]+/[^/]+$", "services.control"),
    ("POST", r"^/api/containers/(kube/.+|stacks/[^/]+/[^/]+|images/check)$", "services.control"),
    ("POST", r"^/api/processes/\d+/signal$", "processes.kill"),
    ("POST", r"^/api/tools/(cpu-mode|fan-curve|fan-curve/reset|cleanup|benchmark)$", "system.config"),
    ("POST", r"^/api/(updates/apt|updates/apply|updates/check)$", "updates.install"),
    ("POST", r"^/api/action/(reboot|shutdown)$", "system.power"),

    # --- Network
    ("POST", r"^/api/tools/(lan/scan|lan/[^/]+/(rename|forget|wol)|port-test|dns|traceroute)$", "network.tools"),
    ("POST", r"^/api/wol(/[^/]+/wake)?$", "network.tools"),
    ("POST", r"^/api/tools/(firewall|fail2ban/unban|geoblock|geoip/download|geoip/remove)$", "firewall.manage"),

    # --- Terminal
    ("*", r"^/api/terminal.*$", "terminal"),
]
_COMPILED = [(m, re.compile(rx), perm) for m, rx, perm in RULES]

# Reads that stay open in "view without login" mode (the dashboard itself)
PUBLIC_READ_PREFIXES = ("/api/stats", "/api/system", "/api/processes", "/api/services", "/api/drives", "/api/weather",
                        "/api/history", "/api/homelab", "/api/updates", "/api/nvme", "/api/status-summary", "/api/health",
                        "/api/wol", "/api/workloads", "/api/diagnostics", "/api/about")


def required_permission(method: str, path: str) -> Optional[str]:
    for m, rx, perm in _COMPILED:
        if (m == "*" or m == method) and rx.match(path):
            return perm
    if method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/"):
        return "users.manage"   # not classified yet: admins only
    if method == "GET" and path.startswith("/api/") and not path.startswith(PUBLIC_READ_PREFIXES):
        return "login"
    return None
