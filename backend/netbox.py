"""
NetBox integration (section "NetBox" on the system page):

- prefixes with utilization, IP addresses, devices and virtual machines read from the NetBox API
- comparison with the LAN scan (netscan): undocumented devices, IP/MAC differences,
  documented addresses that did not answer, reserved addresses in use
- addresses that are free in NetBox *and* in the network, and creating an IP address
  in NetBox for an undocumented device or a free address

Connection (URL, API token, TLS check) lives in settings.json under "netbox"; the token is
never sent back to the browser. Works with NetBox 3.x and 4.x (v1 "Token" and v2 "Bearer nbt_" tokens).
"""

import asyncio
import ipaddress
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

import netscan
import settings_store

CACHE_SECONDS = 120
PAGE_SIZE = 1000
MAX_PAGES = 10           # never more than 10 000 objects per type
FREE_SHOWN = 12          # free addresses listed per network
_cache: Dict[str, Any] = {"ts": 0, "data": None}
_fetch_lock = asyncio.Lock()
DNS_RE = re.compile(r"^[0-9A-Za-z*._-]{0,255}$")


class NetBoxError(Exception):
    pass


# ---------------------------------------------------------------------------
# Connection settings
# ---------------------------------------------------------------------------
def _cfg() -> Optional[Dict[str, Any]]:
    c = settings_store.get("netbox")
    return c if isinstance(c, dict) and c.get("url") and c.get("token") else None


def public() -> Dict[str, Any]:
    c = _cfg()
    if not c:
        return {"configured": False}
    return {"configured": True, "url": c["url"], "token_hint": f"…{c['token'][-4:]}", "verify": c.get("verify", True),
            "version": c.get("version", ""), "added": c.get("added", 0)}


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    url = re.sub(r"/api$", "", url)
    p = urlparse(url)
    if not p.hostname or p.query or p.fragment or p.username or p.password:
        raise NetBoxError("Adresse wie https://netbox.local oder http://192.168.178.20:8000 angeben.")
    return url


def _headers(token: str) -> Dict[str, str]:
    scheme = "Bearer" if token.startswith("nbt_") else "Token"
    return {"Authorization": f"{scheme} {token}", "Accept": "application/json"}


def _client(url: str, token: str, verify: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=url, headers=_headers(token), verify=verify, timeout=httpx.Timeout(15.0),
                             follow_redirects=False)


def _http_error(e: Exception) -> NetBoxError:
    if isinstance(e, httpx.ConnectError) and "CERTIFICATE" in str(e).upper():
        return NetBoxError("TLS-Zertifikat ungültig – bei eigenem Zertifikat „Zertifikat nicht prüfen“ wählen.")
    return NetBoxError(f"NetBox nicht erreichbar ({type(e).__name__}) – Adresse, Port und Firewall prüfen.")


def _check(res: httpx.Response) -> None:
    if res.status_code in (401, 403):
        raise NetBoxError("NetBox lehnt den Token ab (falsch, abgelaufen oder ohne Leserechte).")
    if res.status_code == 404:
        raise NetBoxError("Dort antwortet keine NetBox-API (Adresse ohne /api angeben).")
    if res.is_redirect:
        raise NetBoxError(f"NetBox leitet um auf {res.headers.get('location', '?')} – diese Adresse eintragen.")
    if res.status_code >= 400:
        raise NetBoxError(f"NetBox antwortet mit {res.status_code}.")


async def probe(url: str, token: str, verify: bool) -> Dict[str, Any]:
    if len(token) < 20:
        raise NetBoxError("API-Token fehlt oder ist zu kurz (NetBox → Profil → API-Tokens).")
    try:
        async with _client(url, token, verify) as client:
            status = await client.get("/api/status/")
            _check(status)
            # /api/status/ may be readable without login: make sure the token can read IPAM
            check = await client.get("/api/ipam/prefixes/", params={"limit": 1})
            _check(check)
    except httpx.HTTPError as e:
        raise _http_error(e)
    try:
        info = status.json()
    except ValueError:
        raise NetBoxError("Antwort ist kein JSON – ist das wirklich NetBox?")
    if "netbox-version" not in info:
        raise NetBoxError("Antwort sieht nicht nach NetBox aus.")
    return {"version": str(info["netbox-version"]), "prefixes": check.json().get("count", 0)}


async def save(url: str, token: str, verify: bool) -> Dict[str, Any]:
    url = normalize_url(url)
    old = _cfg()
    token = (token or "").strip()
    if not token:
        # Keeping the stored token is only allowed for the same server - it never goes to a new address
        if not old or old["url"] != url:
            raise NetBoxError("API-Token angeben.")
        token = old["token"]
    info = await probe(url, token, verify)
    settings_store.set("netbox", {"url": url, "token": token, "verify": bool(verify), "version": info["version"],
                                  "added": (old or {}).get("added") or int(time.time())})
    _cache.update(ts=0, data=None)
    return {**public(), "test": info}


def remove() -> None:
    settings_store.set("netbox", None)
    _cache.update(ts=0, data=None)


# ---------------------------------------------------------------------------
# Reading NetBox
# ---------------------------------------------------------------------------
async def _list(client: httpx.AsyncClient, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    query = {"limit": PAGE_SIZE, "offset": 0, **(params or {})}
    for _ in range(MAX_PAGES):
        res = await client.get(path, params=query)
        _check(res)
        page = res.json()
        items.extend(page.get("results", []))
        if not page.get("next"):
            break
        query["offset"] += PAGE_SIZE
    return items


def _version_tuple(v: str) -> Tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _name(obj: Optional[Dict[str, Any]], key: str = "name") -> str:
    return (obj or {}).get(key) or (obj or {}).get("display") or ""


def _status(obj: Dict[str, Any]) -> str:
    st = obj.get("status")
    return st.get("value", "") if isinstance(st, dict) else (st or "")


def _owner(assigned: Optional[Dict[str, Any]], obj_type: str) -> Optional[Dict[str, Any]]:
    """Device / VM an interface belongs to: {kind, id, name, iface}."""
    if not assigned:
        return None
    if obj_type == "dcim.interface" and assigned.get("device"):
        dev = assigned["device"]
        return {"kind": "device", "id": dev.get("id"), "name": _name(dev), "iface": assigned.get("name", "")}
    if obj_type == "virtualization.vminterface" and assigned.get("virtual_machine"):
        vm = assigned["virtual_machine"]
        return {"kind": "vm", "id": vm.get("id"), "name": _name(vm), "iface": assigned.get("name", "")}
    return None


async def _fetch(cfg: Dict[str, Any]) -> Dict[str, Any]:
    async with _client(cfg["url"], cfg["token"], cfg.get("verify", True)) as client:
        status = await client.get("/api/status/")
        _check(status)
        version = str(status.json().get("netbox-version", ""))
        slim = {"exclude": "config_context"}
        jobs = [
            _list(client, "/api/ipam/prefixes/"),
            _list(client, "/api/ipam/ip-addresses/"),
            _list(client, "/api/ipam/ip-ranges/"),
            _list(client, "/api/dcim/devices/", slim),
            _list(client, "/api/virtualization/virtual-machines/", slim),
        ]
        if _version_tuple(version) >= (4, 2):
            jobs.append(_list(client, "/api/dcim/mac-addresses/"))
        else:  # before 4.2 the MAC is a field of the interface
            jobs += [_list(client, "/api/dcim/interfaces/"), _list(client, "/api/virtualization/interfaces/")]
        results = await asyncio.gather(*jobs)
    prefixes, ips, ranges, devices, vms = results[:5]

    macs: List[Dict[str, Any]] = []
    if len(results) == 6:
        for m in results[5]:
            owner = _owner(m.get("assigned_object"), m.get("assigned_object_type") or "")
            if m.get("mac_address") and owner:
                macs.append({"mac": m["mac_address"].lower(), **owner})
    else:
        for kind_type, rows in (("dcim.interface", results[5]), ("virtualization.vminterface", results[6])):
            for i in rows:
                if not i.get("mac_address"):
                    continue
                parent = {"device": i["device"]} if i.get("device") else {"virtual_machine": i.get("virtual_machine")}
                owner = _owner({**parent, "name": i.get("name", "")}, kind_type)
                if owner:
                    macs.append({"mac": i["mac_address"].lower(), **owner})
    return {"version": version, "prefixes": prefixes, "ips": ips, "ranges": ranges, "devices": devices, "vms": vms,
            "macs": macs}


# ---------------------------------------------------------------------------
# Building the view
# ---------------------------------------------------------------------------
def _link(base: str, path: str, obj_id: Any) -> str:
    return f"{base}/{path}/{obj_id}/" if obj_id is not None else ""


def _primary(obj: Dict[str, Any]) -> str:
    p = obj.get("primary_ip4") or obj.get("primary_ip") or obj.get("primary_ip6")
    return (p or {}).get("address", "").split("/")[0]


def _ip_sort(ip: str):
    try:
        a = ipaddress.ip_address(ip)
        return (a.version, int(a))
    except ValueError:
        return (9, 0)


def _build(raw: Dict[str, Any], base: str) -> Dict[str, Any]:
    lan = netscan.devices()
    last_scan = lan.get("last_scan")
    subnets = [ipaddress.ip_network(s) for s in lan.get("subnets") or []]
    lan_devs = [d for d in lan["devices"] if d.get("ip")]
    lan_by_ip: Dict[str, Dict[str, Any]] = {}
    for d in lan_devs:  # online first (netscan's order): a remembered old device never hides the current one
        lan_by_ip.setdefault(d["ip"], d)
    online_ips = {d["ip"] for d in lan_devs if d.get("online")}

    # --- IP addresses
    ips: List[Dict[str, Any]] = []
    ip_index: Dict[str, Dict[str, Any]] = {}
    owner_ips: Dict[Tuple[str, Any], set] = {}
    for x in raw["ips"]:
        try:
            iface = ipaddress.ip_interface(x["address"])
        except (ValueError, KeyError):
            continue
        owner = _owner(x.get("assigned_object"), x.get("assigned_object_type") or "")
        ip = str(iface.ip)
        item = {"id": x.get("id"), "ip": ip, "address": x["address"], "status": _status(x),
                "dns_name": x.get("dns_name") or "", "description": x.get("description") or "",
                "role": _status({"status": x.get("role")}), "tenant": _name(x.get("tenant")),
                "owner": owner, "link": _link(base, "ipam/ip-addresses", x.get("id")),
                "in_lan": any(iface.ip in n for n in subnets)}
        if item["in_lan"]:
            d = lan_by_ip.get(ip)
            item["lan"] = {"online": bool(d and d.get("online")), "mac": d["mac"] if d else "",
                           "name": (d.get("alias") or d.get("hostname") or "") if d else ""}
        ips.append(item)
        ip_index.setdefault(ip, item)
        if owner:
            owner_ips.setdefault((owner["kind"], owner["id"]), set()).add(ip)
    ips.sort(key=lambda i: _ip_sort(i["ip"]))

    mac_index: Dict[str, Dict[str, Any]] = {}
    owner_macs: Dict[Tuple[str, Any], set] = {}
    for m in raw["macs"]:
        mac_index.setdefault(m["mac"], m)
        owner_macs.setdefault((m["kind"], m["id"]), set()).add(m["mac"])

    # --- IP ranges (DHCP pools etc.) - addresses inside never count as free
    ranges = []
    for r in raw["ranges"]:
        try:
            start = ipaddress.ip_interface(r["start_address"]).ip
            end = ipaddress.ip_interface(r["end_address"]).ip
        except (ValueError, KeyError):
            continue
        ranges.append({"start": start, "end": end, "status": _status(r), "description": r.get("description") or "",
                       "mark_utilized": bool(r.get("mark_utilized")), "size": int(end) - int(start) + 1})

    # --- Prefixes with utilization
    nets = []
    for p in raw["prefixes"]:
        try:
            nets.append((ipaddress.ip_network(p["prefix"], strict=False), p))
        except (ValueError, KeyError):
            pass
    prefixes = []
    for net, p in nets:
        status = _status(p)
        size = net.num_addresses
        if net.version == 4 and net.prefixlen < 31 and not p.get("is_pool"):
            size -= 2
        children = [n for n, _ in nets if n != net and n.version == net.version and n.subnet_of(net)]
        if status == "container":
            direct = [c for c in children if not any(o != c and c.subnet_of(o) for o in children)]
            used = sum(c.num_addresses for c in direct)
            size = net.num_addresses
        elif p.get("mark_utilized"):
            used = size
        else:
            used = sum(1 for i in ips if i["ip"] and ipaddress.ip_address(i["ip"]).version == net.version
                       and ipaddress.ip_address(i["ip"]) in net)
            used += sum(r["size"] for r in ranges if r["mark_utilized"] and r["start"].version == net.version
                        and r["start"] in net and r["end"] in net)
        pct = round(min(used / size * 100, 100), 1) if size > 0 else 0
        prefixes.append({"id": p.get("id"), "prefix": str(net), "status": status, "description": p.get("description") or "",
                         "vlan": _name(p.get("vlan"), "display"), "scope": _name(p.get("scope"), "display") or _name(p.get("site")),
                         "vrf": _name(p.get("vrf")), "is_pool": bool(p.get("is_pool")), "used": used, "size": size,
                         "pct": pct if net.version == 4 or status == "container" else None,
                         "children": len(children), "in_lan": any(net.overlaps(n) for n in subnets),
                         "link": _link(base, "ipam/prefixes", p.get("id"))})
    prefixes.sort(key=lambda p: (ipaddress.ip_network(p["prefix"]).version, ipaddress.ip_network(p["prefix"])))

    # --- Devices and VMs
    objects = []
    for kind, rows, path in (("device", raw["devices"], "dcim/devices"), ("vm", raw["vms"], "virtualization/virtual-machines")):
        for o in rows:
            key = (kind, o.get("id"))
            primary = _primary(o)
            mine = owner_ips.get(key, set()) | ({primary} if primary else set())
            seen = [ip for ip in mine if ip in online_ips] or \
                   [d["ip"] for d in lan_devs if d.get("online") and d["mac"] in owner_macs.get(key, set())]
            in_lan = any(_in(ip, subnets) for ip in mine) or bool(owner_macs.get(key, set()) & {d["mac"] for d in lan_devs})
            dtype = o.get("device_type") or {}
            objects.append({
                "id": o.get("id"), "kind": kind, "name": o.get("name") or o.get("display") or f"#{o.get('id')}",
                "status": _status(o), "role": _name(o.get("role") or o.get("device_role")),
                "site": _name(o.get("site")), "cluster": _name(o.get("cluster")),
                "model": " ".join(filter(None, [_name(dtype.get("manufacturer")), dtype.get("model", "")])),
                "platform": _name(o.get("platform")), "primary_ip": primary, "serial": o.get("serial") or "",
                "tenant": _name(o.get("tenant")), "ips": sorted(mine, key=_ip_sort),
                "lan": ("online" if seen else "offline") if in_lan and last_scan else None,
                "link": _link(base, path, o.get("id"))})
    objects.sort(key=lambda o: o["name"].lower())

    # --- Comparison with the LAN scan
    issues: List[Dict[str, Any]] = []
    moved: set = set()  # devices found online under another IP: their documented IP is not reported as offline

    def lan_label(d):
        return d.get("alias") or _short(d.get("hostname", "")) or d.get("vendor") or d["mac"]

    for d in lan_devs:
        if not d.get("online") and d["ip"] in online_ips:
            continue  # address now used by another device
        nb_ip = ip_index.get(d["ip"])
        nb_mac = mac_index.get(d["mac"])
        base_issue = {"ip": d["ip"], "mac": d["mac"], "name": lan_label(d), "vendor": d.get("vendor", ""),
                      "hostname": d.get("hostname", ""), "online": bool(d.get("online")), "self": bool(d.get("self"))}
        if nb_mac:
            key = (nb_mac["kind"], nb_mac["id"])
            documented = owner_ips.get(key, set())
            if d["ip"] in documented:
                continue
            moved.add(key)
            path = "dcim/devices" if nb_mac["kind"] == "device" else "virtualization/virtual-machines"
            issues.append({**base_issue, "kind": "ip_missing" if not documented else "ip_mismatch",
                           "netbox": {"name": nb_mac["name"], "iface": nb_mac["iface"], "ips": sorted(documented, key=_ip_sort),
                                      "link": _link(base, path, nb_mac["id"])}})
        elif nb_ip:
            owner = nb_ip["owner"]
            known_macs = owner_macs.get((owner["kind"], owner["id"]), set()) if owner else set()
            if known_macs and d["mac"] not in known_macs:
                issues.append({**base_issue, "kind": "mac_mismatch",
                               "netbox": {"name": owner["name"], "macs": sorted(known_macs), "link": nb_ip["link"]}})
            elif nb_ip["status"] in ("reserved", "deprecated") and d.get("online"):
                issues.append({**base_issue, "kind": "status", "netbox": {"status": nb_ip["status"], "link": nb_ip["link"],
                                                                         "name": owner["name"] if owner else nb_ip["dns_name"]}})
        else:
            issues.append({**base_issue, "kind": "undocumented",
                           "suggest": {"dns_name": _dns(d.get("hostname", "")), "description": _describe(d)}})

    if last_scan:
        for i in ips:
            if i["in_lan"] and i["status"] == "active" and i["ip"] not in online_ips \
                    and not (i["owner"] and (i["owner"]["kind"], i["owner"]["id"]) in moved):
                issues.append({"kind": "offline", "ip": i["ip"], "mac": (lan_by_ip.get(i["ip"]) or {}).get("mac", ""),
                               "name": (i["owner"] or {}).get("name") or i["dns_name"] or i["description"],
                               "online": False, "netbox": {"link": i["link"], "status": i["status"],
                                                            "iface": (i["owner"] or {}).get("iface", "")}})
    order = {"ip_mismatch": 0, "mac_mismatch": 1, "status": 2, "ip_missing": 3, "undocumented": 4, "offline": 5}
    issues.sort(key=lambda x: (order[x["kind"]], not x.get("online"), _ip_sort(x["ip"])))

    # --- Free addresses per LAN network: not in NetBox, not in an IP range, not seen in the last 7 days
    recent = {d["ip"] for d in lan_devs if d.get("last_seen", 0) > time.time() - 7 * 86400}
    taken = set(ip_index) | recent
    free = []
    for net in subnets:
        if net.version != 4 or net.num_addresses > 1024:
            continue
        nb_prefix = min((p for p in prefixes if ipaddress.ip_network(p["prefix"]).version == 4
                         and net.subnet_of(ipaddress.ip_network(p["prefix"]))),
                        key=lambda p: ipaddress.ip_network(p["prefix"]).num_addresses, default=None)
        in_range = [r for r in ranges if r["start"].version == 4 and (r["start"] in net or r["end"] in net)]
        addrs = []
        for h in net.hosts():
            s = str(h)
            if s in taken or any(r["start"] <= h <= r["end"] for r in in_range):
                continue
            addrs.append(s)
        free.append({"network": str(net), "prefix": nb_prefix["prefix"] if nb_prefix else "",
                     "prefix_link": nb_prefix["link"] if nb_prefix else "", "count": len(addrs),
                     "first": addrs[:FREE_SHOWN], "excluded_ranges": [f"{r['start']}–{r['end']}" for r in in_range]})

    counts: Dict[str, int] = {}
    for x in issues:
        counts[x["kind"]] = counts.get(x["kind"], 0) + 1
    return {"version": raw["version"], "url": base, "prefixes": prefixes, "ips": ips, "objects": objects,
            "issues": issues, "issue_counts": counts, "free": free, "last_scan": last_scan,
            "subnets": [str(n) for n in subnets],
            "summary": {"prefixes": len(prefixes), "ips": len(ips), "devices": len(raw["devices"]), "vms": len(raw["vms"]),
                        "macs": len(raw["macs"]), "ranges": len(ranges)}}


def _in(ip: str, nets) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in nets)


def _short(hostname: str) -> str:
    return re.sub(r"\.(fritz\.box|local|lan|home|localdomain|speedport\.ip)$", "", hostname or "", flags=re.I)


def _dns(hostname: str) -> str:
    h = (hostname or "").strip().rstrip(".")
    return h if h and DNS_RE.match(h) else ""


def _describe(d: Dict[str, Any]) -> str:
    parts = [d.get("alias") or _short(d.get("hostname", "")), d.get("vendor", ""), d.get("mac", "")]
    return " · ".join(p for p in parts if p and not p.startswith("Private Adresse"))[:200]


async def data(refresh: bool = False) -> Dict[str, Any]:
    cfg = _cfg()
    if not cfg:
        raise NetBoxError("NetBox ist nicht verbunden (Einstellungen → NetBox).")
    async with _fetch_lock:
        if refresh or not _cache["data"] or time.time() - _cache["ts"] > CACHE_SECONDS:
            try:
                _cache["data"] = await _fetch(cfg)
            except httpx.HTTPError as e:
                raise _http_error(e)
            _cache["ts"] = time.time()
    # The comparison is rebuilt every time, so a fresh LAN scan shows up without re-reading NetBox
    view = await asyncio.to_thread(_build, _cache["data"], cfg["url"])
    return {**view, "fetched": int(_cache["ts"])}


# ---------------------------------------------------------------------------
# Writing: create an IP address
# ---------------------------------------------------------------------------
async def create_ip(ip: str, status: str, dns_name: str, description: str) -> Dict[str, Any]:
    cfg = _cfg()
    if not cfg:
        raise NetBoxError("NetBox ist nicht verbunden.")
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        raise NetBoxError("Ungültige IP-Adresse.")
    if status not in ("active", "reserved", "dhcp"):
        raise NetBoxError("Ungültiger Status.")
    dns_name = (dns_name or "").strip()
    if not DNS_RE.match(dns_name):
        raise NetBoxError("DNS-Name darf nur Buchstaben, Ziffern, Punkt, Binde- und Unterstrich enthalten.")
    description = (description or "").strip()[:200]

    # Prefix length: smallest NetBox prefix containing the address, else the server's own network
    length = None
    raw = _cache["data"]
    if raw:
        best = None
        for p in raw["prefixes"]:
            try:
                net = ipaddress.ip_network(p["prefix"], strict=False)
            except (ValueError, KeyError):
                continue
            if net.version == addr.version and addr in net and (best is None or net.prefixlen > best.prefixlen):
                best = net
        length = best.prefixlen if best else None
    if length is None:
        net = next((ipaddress.ip_network(s) for s in netscan.devices().get("subnets") or []
                    if addr in ipaddress.ip_network(s)), None)
        if net is None:
            raise NetBoxError("Zu dieser Adresse gibt es weder einen NetBox-Präfix noch ein Netz dieses Servers.")
        length = net.prefixlen

    try:
        async with _client(cfg["url"], cfg["token"], cfg.get("verify", True)) as client:
            existing = await client.get("/api/ipam/ip-addresses/", params={"address": str(addr), "limit": 1})
            _check(existing)
            if existing.json().get("count"):
                raise NetBoxError(f"{addr} steht schon in NetBox.")
            body = {"address": f"{addr}/{length}", "status": status, "dns_name": dns_name, "description": description}
            res = await client.post("/api/ipam/ip-addresses/", json=body)
    except httpx.HTTPError as e:
        raise _http_error(e)
    if res.status_code == 403:
        raise NetBoxError("Der Token darf in NetBox nichts anlegen (Schreibrechte für IP-Adressen fehlen).")
    if res.status_code == 400:
        try:
            errors = "; ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in res.json().items())
        except (ValueError, AttributeError):
            errors = res.text[:200]
        raise NetBoxError(f"NetBox: {errors}")
    _check(res)
    created = res.json()
    _cache["ts"] = 0  # re-read on the next view
    return {"id": created.get("id"), "address": created.get("address"),
            "link": _link(cfg["url"], "ipam/ip-addresses", created.get("id"))}
