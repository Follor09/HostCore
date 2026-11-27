"""Firewall: ports that services listen on and no ufw rule mentions yet are suggested."""

import security

# `ss -H -tunap` of a typical server: SSH, dashboard, nginx, Postgres for everyone, Redis only
# locally, a Docker-published port, DHCP client, a random UDP client port, Grafana on the LAN address
SS = """\
tcp   LISTEN 0      128          0.0.0.0:22         0.0.0.0:*     users:(("sshd",pid=600,fd=3))
tcp   LISTEN 0      128             [::]:22            [::]:*     users:(("sshd",pid=600,fd=4))
tcp   LISTEN 0      2048         0.0.0.0:8000       0.0.0.0:*     users:(("uvicorn",pid=700,fd=7))
tcp   LISTEN 0      511          0.0.0.0:80         0.0.0.0:*     users:(("nginx",pid=800,fd=6))
tcp   LISTEN 0      244          0.0.0.0:5432       0.0.0.0:*     users:(("postgres",pid=900,fd=5))
tcp   LISTEN 0      511        127.0.0.1:6379       0.0.0.0:*     users:(("redis-server",pid=901,fd=6))
tcp   LISTEN 0      4096         0.0.0.0:9443       0.0.0.0:*     users:(("docker-proxy",pid=1000,fd=4))
tcp   LISTEN 0      4096   192.168.1.20:3000        0.0.0.0:*     users:(("grafana",pid=1100,fd=8))
tcp   LISTEN 0      4096      172.17.0.1:7000       0.0.0.0:*     users:(("app",pid=1200,fd=8))
udp   UNCONN 0      0            0.0.0.0:68         0.0.0.0:*     users:(("dhclient",pid=500,fd=7))
udp   UNCONN 0      0            0.0.0.0:41234      0.0.0.0:*     users:(("chrome",pid=1300,fd=9))
udp   UNCONN 0      0            0.0.0.0:53         0.0.0.0:*     users:(("dnsmasq",pid=1400,fd=4))
tcp   ESTAB  0      0      192.168.1.20:22    192.168.1.5:50000   users:(("sshd",pid=1500,fd=4))
"""

RULES = [
    {"to": "22/tcp", "direction": "in", "action": "allow", "from": "Anywhere"},
    {"to": "8000/tcp", "direction": "in", "action": "allow", "from": "Anywhere"},
    {"to": "Nginx Full", "direction": "in", "action": "allow", "from": "Anywhere"},
]
PROFILES = {"Nginx Full": [("tcp", "80"), ("tcp", "443")]}


def _run(rules=RULES, active=True, lan="192.168.1.0/24", defaults=None, kube=None):
    return security.port_suggestions(rules, defaults or {"incoming": "deny"}, active, ss_out=SS,
                                     profiles=PROFILES, lan=lan, ssh=22, kube=kube or {})


def test_only_reachable_unhandled_ports_are_suggested():
    res = _run()
    found = {(p["proto"], p["port"]): p for p in res["ports"]}
    # Handled by rules (also through the app profile), local only, container network, DHCP, random UDP
    assert set(found) == {("tcp", 5432), ("tcp", 3000), ("udp", 53)}
    assert found[("tcp", 5432)]["advice"] == "avoid"
    assert found[("tcp", 3000)]["addrs"] == ["192.168.1.20"]
    assert found[("udp", 53)]["advice"] == "lan"
    assert res["docker"] == [{"proto": "tcp", "port": 9443}]
    assert res["lan"] == "192.168.1.0/24"


def test_sorted_by_advice_and_comment_is_valid():
    res = _run()
    assert [p["advice"] for p in res["ports"]] == sorted((p["advice"] for p in res["ports"]),
                                                         key={"open": 0, "lan": 1, "avoid": 2}.get)
    for p in res["ports"]:
        # The ready rule must pass the form validation
        security._rule_args({"action": "allow", "direction": "in", "proto": p["proto"], "dport": str(p["port"]),
                             "from": res["lan"], "comment": p["comment"]})


def test_any_decision_counts_and_ranges_match():
    rules = RULES + [{"to": "5000:6000/tcp", "direction": "in", "action": "deny", "from": "Anywhere"},
                     {"to": "53", "direction": "in", "action": "allow", "from": "192.168.1.0/24"}]
    ports = {(p["proto"], p["port"]) for p in _run(rules)["ports"]}
    assert ports == {("tcp", 3000)}


def test_ssh_and_dashboard_while_firewall_off():
    ports = {(p["proto"], p["port"]) for p in _run(rules=[], active=False)["ports"]}
    assert ("tcp", 22) not in ports and ("tcp", 8000) not in ports    # "Firewall einschalten" allows them
    ports = {(p["proto"], p["port"]) for p in _run(rules=[], active=True)["ports"]}
    assert ("tcp", 22) in ports and ("tcp", 8000) in ports            # active without them: would lock out


def test_incoming_allowed_by_default_needs_nothing():
    res = _run(defaults={"incoming": "allow"})
    assert res["ports"] == [] and res["all_open"]


def test_vps_without_home_network():
    found = {(p["proto"], p["port"]): p["advice"] for p in _run(lan="")["ports"]}
    assert found[("tcp", 3000)] == "lan"         # known as LAN-only, stays a warning
    assert found[("tcp", 5432)] == "avoid"


# `kubectl get services,pods -A -o json` of a k3s cluster: Traefik through ServiceLB, a NodePort
# app, a ClusterIP service (internal), ServiceLB's own svclb pod and a pod with a hostPort
KUBE = {"items": [
    {"kind": "Service", "metadata": {"namespace": "kube-system", "name": "traefik"},
     "spec": {"type": "LoadBalancer", "ports": [{"port": 80, "nodePort": 31080, "protocol": "TCP"},
                                                 {"port": 443, "nodePort": 31443, "protocol": "TCP"}]},
     "status": {"loadBalancer": {"ingress": [{"ip": "192.168.1.20"}]}}},
    {"kind": "Service", "metadata": {"namespace": "apps", "name": "grafana"},
     "spec": {"type": "NodePort", "ports": [{"port": 3000, "nodePort": 30300, "protocol": "TCP"}]}},
    {"kind": "Service", "metadata": {"namespace": "apps", "name": "db"},
     "spec": {"type": "ClusterIP", "ports": [{"port": 5432, "protocol": "TCP"}]}},
    {"kind": "Pod", "metadata": {"namespace": "kube-system", "name": "svclb-traefik-abcde",
                                 "labels": {"svccontroller.k3s.cattle.io/svcname": "traefik"}},
     "spec": {"containers": [{"ports": [{"hostPort": 80, "containerPort": 80}]}]}, "status": {"phase": "Running"}},
    {"kind": "Pod", "metadata": {"namespace": "iot", "name": "mosquitto-0"},
     "spec": {"containers": [{"ports": [{"hostPort": 1883, "containerPort": 1883, "protocol": "TCP"}]}]},
     "status": {"phase": "Running"}},
]}


def test_kubernetes_ports_without_listener():
    rules = [r for r in RULES if r["to"] != "Nginx Full"]
    found = {(p["proto"], p["port"]): p for p in _run(rules=rules, kube=KUBE)["ports"]}
    # LoadBalancer ports and the NodePorts behind them, the NodePort app, the hostPort; not ClusterIP / svclb
    assert {("tcp", 80), ("tcp", 443), ("tcp", 31080), ("tcp", 31443), ("tcp", 30300), ("tcp", 1883)} <= set(found)
    assert ("tcp", 5432) in found and found[("tcp", 5432)]["program"] == "postgres"   # the listener, not the ClusterIP
    assert found[("tcp", 443)]["advice"] == "open"
    assert found[("tcp", 443)]["kube"] == ["LoadBalancer kube-system/traefik"]
    assert found[("tcp", 30300)]["service"] == "grafana" and found[("tcp", 30300)]["advice"] == "lan"
    assert found[("tcp", 1883)]["service"] == "MQTT"
    for p in found.values():
        security._rule_args({"action": "allow", "direction": "in", "proto": p["proto"], "dport": str(p["port"]),
                             "comment": p["comment"]})


def test_kubernetes_ports_with_rules_and_listener():
    # nginx listens on 80 as well (hostNetwork): one entry; the profile covers 80/443 anyway
    found = {(p["proto"], p["port"]): p for p in _run(kube=KUBE)["ports"]}
    assert ("tcp", 80) not in found and ("tcp", 443) not in found
    rules = RULES + [{"to": "30000:32767/tcp", "direction": "in", "action": "allow", "from": "192.168.1.0/24"}]
    found = {(p["proto"], p["port"]) for p in _run(rules=rules, kube=KUBE)["ports"]}
    assert not any(30000 <= port <= 32767 for _, port in found)
