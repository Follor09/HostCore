# HostCore – server & homelab dashboard for Debian, Ubuntu and Raspberry Pi

**One self-hosted dashboard for your Linux server or Raspberry Pi: monitoring, Docker & k3s, virtual machines (KVM), GitOps, a web terminal, documentation – and compliance built in.**

Instead of stitching together a start page, a container UI, a VM manager, a server admin panel and a wiki, HostCore does it in one place – and adds what homelab tools usually leave out: change requests with four-eyes approval, an emergency handbook generated from your setup, patch deadlines, access reviews, a BSI IT-Grundschutz–style baseline check, GDPR records of processing and an evidence package for auditors.

> 🇩🇪 Deutsche Fassung mit allen Details: **[README.de.md](README.de.md)** · Getting started: **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)**
> The interface is German by default; an English translation can be switched on (Settings → Language). MIT licensed.

![Compliance – baseline check](docs/screenshots/compliance.png)

## Install

On any 64-bit Debian 12+ or Ubuntu 22.04+ machine – a PC, a mini server, a VM or a Raspberry Pi (Pi 5 recommended, Pi 4 works) with Raspberry Pi OS – and a user with `sudo`:

```bash
git clone https://github.com/Follor09/HostCore.git ~/pi-dashboard
~/pi-dashboard/install.sh
```

`install.sh` sets up the virtualenv, the systemd service, the update timer and narrowly scoped sudo rules, then asks for the first administrator. Open `http://<server-ip>:8000`. Docker, k3s and KVM are optional – their sections appear once they are installed; virtualisation can be set up from the VMs page in two clicks.

**Updates:** a fresh install follows the **stable channel** – only published releases (`vX.Y.Z` tags), never back to an older one. Want every commit? Settings → Auto-Update → *Jeder Commit (main)*. Everything that runs as root comes from a root-owned copy of this repository, see [SECURITY.md](SECURITY.md).

## Why HostCore

| | HostCore | Homarr | Portainer | Cockpit |
|---|:---:|:---:|:---:|:---:|
| Start page with apps & widgets | ✓ | ✓ | – | – |
| Docker containers & compose stacks | ✓ | basic | ✓ | Podman |
| Virtual machines (KVM/libvirt) with console | ✓ | – | – | ✓ |
| Stacks deployed from Git (GitOps) | ✓ | – | ✓ | – |
| Services, packages, firewall, web terminal | ✓ | – | – | ✓ |
| Hardware monitoring incl. Raspberry Pi specifics (throttling, power, fan curve) | ✓ | – | – | basic |
| Runbooks, emergency handbook, change requests | ✓ | – | – | – |
| Baseline check, GDPR records/TOM, evidence export | ✓ | – | – | – |

<sub>Compared by each project's core focus – they are excellent tools; check their docs for details. HostCore is built for one server (or a small fleet via agents), not for large clusters.</sub>

## Highlights

**Run it**
- Live hardware monitoring (CPU, temperature, NVMe health; on the Pi also throttling and Pi 5 power), 24 h / 7 day history, anomaly detection, capacity forecasts
- **Virtual machines** (KVM/libvirt, inspired by Cockpit): create from Debian/Ubuntu cloud images with cloud-init (user, SSH key, guest agent) or from an ISO, start/stop, vCPUs and memory, disks, ISO, network cards, snapshots, clone, virtual networks and storage pools, graphical (noVNC) and serial console in the browser; optional per server (Settings → *Virtuelle Maschinen*), not offered on a Raspberry Pi
- Docker & k3s: containers, compose stacks, live logs, database monitor with dumps, image update checks, app store, stack templates
- **GitOps:** compose stacks from a Git repository – diff of new commits, deploy as a task (databases dumped first, health check last), automatic mode, drift detection, deploy webhook
- Persistent web terminal (tmux) with sharing and recordings, file browser, task log like Proxmox, fleet of servers via agents – a Raspberry Pi, a Debian or Ubuntu machine or VM each run the agent (`./install.sh --agent --from <main-dashboard-ip>`)

**Secure it**
- Users, groups and 24 fine-grained permissions; 2FA (TOTP), passkeys, **single sign-on via OpenID Connect** (Pocket ID, Authentik, Keycloak …)
- Hardening check, vulnerability scan (debsecan, Trivy) with **patch deadlines**, compose security check, **image policies** that can block deployments, **secret scan**
- Tamper-evident activity log (SHA-256 chain), confirm-again for critical changes, areas restricted to home network/VPN, Cloudflare/reverse proxy aware

**Document & prove it**
- Inventory, service profiles, runbooks, operations journal, incidents with post-mortems, **problem management**
- **Change requests** with four-eyes approval, maintenance windows and journal entries
- **Emergency handbook** with restore commands per service, generated from stacks, backups, dumps and GitOps – print or Markdown for offline storage
- **Compliance page:** baseline check modelled on BSI IT-Grundschutz / CIS (mostly rated automatically), GDPR records of processing and TOM, **access review** with sign-off, **evidence package** (ZIP with SHA-256 manifest) for auditors and customers

Plus: Homarr-style boards, command palette (Ctrl+K), alerting with escalation, e-mail reports, public status page, NetBox integration, built-in MCP server for Claude.

## Screenshots

| | |
|---|---|
| ![Containers](docs/screenshots/containers.png) | ![GitOps](docs/screenshots/gitops.png) |
| Containers & stacks | GitOps with diff and drift |
| ![Change requests](docs/screenshots/changes.png) | ![Patch deadlines](docs/screenshots/patch-deadlines.png) |
| Change requests with four-eyes approval | Patch deadlines per severity |
| ![Access review](docs/screenshots/access-review.png) | ![Baseline check](docs/screenshots/compliance.png) |
| Access review | Baseline check |

## Security model in short

HostCore is meant for the home network or behind a VPN / Cloudflare Access – it includes a web terminal and service control. The app runs as a normal user; root actions come only from a root-owned copy of the repo with sudo limited to exactly those scripts. Every request is checked against one permission table. Details and how to report a vulnerability: [SECURITY.md](SECURITY.md).

## Project layout

`app.py` is the entry point, the feature modules live in `backend/`, the web interface in `static/`, guides and screenshots in `docs/`. Install, update and helper scripts stay in the top folder.

## Contributing

Issues and pull requests are welcome – see [CONTRIBUTING.md](CONTRIBUTING.md). Ideas that fit: practical features for running a server, a Pi or a small homelab professionally.

## License

[MIT](LICENSE)
