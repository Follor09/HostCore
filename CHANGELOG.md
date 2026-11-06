# Changelog

Releases are tags `vX.Y.Z`; installs on the stable update channel only follow these.

## Unreleased

### New
- **VM templates:** tested base configurations (size, network, cloud-init without a password) as the starting point for new VMs, also from the service catalog.
- **VM backups:** full, self-contained copies of a VM's disks in a storage pool, while it runs (`virsh backup-begin`) or switched off, with retention. A restore creates a new VM with new MAC addresses and leaves it switched off.
- **VM schedules:** snapshots, backups, start and shutdown by weekday and time; old automatic snapshots and backups are removed; missed snapshots and backups catch up within 30 minutes.
- **Live migration** between HostCore servers: checks architecture, memory, network, storage, ISOs and the SSH connection first. Each source gets its own SSH key, limited on the target to `virt-ssh-helper`, and the target's host keys come over the authenticated agent connection. Disks are copied along unless storage is shared. Migration data is not encrypted: use it in your own network or VPN and allow ports 49152–49215 on the target.
- **Service catalog:** standard requests as forms with four-eyes approval; fulfilment as a task on the board, a change request, a VM from a template or a quick action.
- **File integrity:** hashes of system folders as a root task, daily if wanted. Changes from package updates and approved change requests are explained and closed automatically, the rest stays open until closed with a reason.
- **sudo log:** who ran which command as root, per user and server, kept for 180 days, with built-in and own warning rules.
- **Malware scan:** ClamAV and rkhunter on a schedule, also in container volumes. Findings are reported, nothing is deleted.
- **Container runtime protection:** Falco from its signed package source (modern eBPF), events with grouping and muting. Falco only observes.
- **NIS-2 deadlines:** classify a security incident, track the stages of § 32 BSIG (24 hours, 72 hours, final report) and Art. 33 GDPR, prepare the texts, record the submission.
- **Policies:** versions with fingerprint, four-eyes approval, review interval and acknowledgement per user and version.
- **Supplier register:** criticality, data processing agreement, certificates, review dates with history, exit plan and dependent services.
- **Internal audits:** checklists (also from the baseline check), findings with corrective action and deadline, effectiveness confirmed by someone other than the owner.
- The baseline check rates six more requirements automatically; the evidence package also contains policies, suppliers, internal audits and NIS-2 cases.

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
