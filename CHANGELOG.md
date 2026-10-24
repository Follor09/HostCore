# Changelog

Releases are tags `vX.Y.Z`; installs on the stable update channel only follow these.

## Unreleased

### New
- **Virtual machines (KVM/libvirt)**, inspired by Cockpit: create VMs from Debian/Ubuntu cloud images with cloud-init (user, SSH key, guest agent – checksum verified), from an ISO, an existing disk or PXE; start/stop/pause/reset, vCPUs and memory, disks, ISO, network cards, snapshots, clone, rename, delete; virtual networks and storage pools; graphical (noVNC) and serial console in the browser; setup in two clicks. New permissions *VMs steuern* and *VMs verwalten* (the latter asks for the password again). Optional: Settings → *Virtuelle Maschinen* switches the feature off per server; on a Raspberry Pi it does not exist.
- **Any Debian/Ubuntu host:** model from DMI, CPU temperature from coretemp/k10temp, no made-up temperature on VMs without sensors; Raspberry Pi–only cards (3D model, throttling, power, fan curve) appear on a Pi only.

- **Agent on any Debian/Ubuntu host:** `install.sh` installs missing base packages on fresh VMs and cloud images and checks the Python version; `--from <ip>` / `./agent.sh allow` lets the agent accept only the main dashboard's address (on top of the token) and opens port 8000 in an active ufw; the remote list shows OS, kind (Raspberry Pi, VM, server) and architecture.

### Changed
- The repository moved to **github.com/Follor09/HostCore**. `update.sh` notices a renamed or moved repository once a day and switches the update address of every install by itself.
- New name: **HostCore** (was PiCore), with a new icon. Internal names (services, folders, the agent header) stay, so existing installs and agents keep working.
- Texts say *server* instead of *Pi* where they mean the machine the dashboard runs on (*Remote-Server*, *Alle Server* …). Service and file names stay the same, so existing installs update as usual.

## v1.3.0 – 2026-10-24

First tagged release.

### New
- **Single sign-on (OpenID Connect):** Pocket ID, Authentik, Keycloak …; account linking, group mapping, SSO as second factor, local sign-in limited to administrators as emergency access.
- **GitOps:** compose stacks from a Git repository with diff, deploy as a task, automatic mode, drift detection and deploy webhook.
- **Emergency handbook:** restore steps per service generated from stacks, backups, dumps and GitOps; print view and Markdown.
- **Change requests** with four-eyes approval, maintenance windows and journal entries.
- **Access review** with sign-off and fingerprinted evidence.
- **Image policies** (allowed registries, fixed versions, block list) that warn or block deployments.
- **Secret scan** for keys, tokens and passwords in stack folders, scripts and documentation (masked).
- **Patch deadlines** per severity with exceptions and time-to-fix statistics.
- **Problem management** with known errors and solution via change request.
- **Compliance page:** baseline check modelled on BSI IT-Grundschutz / CIS, GDPR records of processing and TOM, evidence package for auditors.
- **Update channels:** *stable* (releases only, default for new installs) or *main* (every commit).

Everything before this release: see the [commit history](https://github.com/Follor09/HostCore/commits/main) and [README.de.md](README.de.md).
