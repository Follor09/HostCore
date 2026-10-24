# Getting started with HostCore

HostCore has a lot of features. You don't need them all on day one – this is a sensible order for the first hour.
(The interface is German by default; switch to English under **Einstellungen → Sprache**.)

## 1. Install and sign in

```bash
git clone https://github.com/Follor09/HostCore.git ~/pi-dashboard
~/pi-dashboard/install.sh
```

The script asks for the first administrator at the end. Then open `http://<server-ip>:8000` and sign in.
Forgot the password? On the server: `~/pi-dashboard/set-password.sh` always works.

Optional, so the container pages have something to show:

```bash
sudo usermod -aG docker $USER                       # Docker (log in again afterwards)
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config   # k3s
sudo apt install debsecan                           # vulnerability scan of packages (Trivy for images is optional)
```

## 2. Secure the account (5 minutes)

1. **Settings → Two-factor sign-in** (*Zwei-Faktor-Anmeldung*): set up TOTP or add a passkey.
2. **Settings → Security** (*Sicherheit*): if you reach the server through Cloudflare or a reverse proxy, choose it there, so lockouts and logs see the real client address. Make two-factor sign-in mandatory for administrators. The checklist shows what is still missing for access from outside.
3. Optional: **Settings → Single sign-on** for Pocket ID, Authentik or Keycloak.

## 3. Tell HostCore what matters

- **Settings → Web services** (*Web-Dienste*): the URLs you want watched (Home Assistant, NAS …).
- **System tools → Backups:** the restic/Borg repositories or folders your backups write to – HostCore warns when one is overdue.
- **Settings → E-mail** and **Alerting** (*Alarmierung*): who gets notified, with escalation.
- **Settings → Auto-update:** new installs follow stable releases; switch to *every commit* if you like living on the edge.

## 4. Virtual machines (optional)

On a Debian/Ubuntu host with hardware virtualisation (Intel VT-x / AMD-V) – not on a Raspberry Pi. Switch it off under **Settings → Virtuelle Maschinen** if you do not need it.

1. **VMs → Setup** (*Einrichtung*): *Pakete installieren* (QEMU, libvirt, virt-install), then *Zugriff einrichten* – the dashboard user joins the `libvirt` group (as powerful as root, like `docker`), the NAT network and the storage pool `default` are created and the dashboard restarts.
2. **VMs → Images & ISOs:** download a Debian or Ubuntu cloud image once (checksum verified).
3. **VM erstellen:** name, vCPUs, memory, disk, network, user and SSH key – the VM is up in about a minute and shows its IP address. Console, snapshots, disks and network cards are in the VM's details.

## 5. Containers

- **Containers → Docker / Compose stacks:** start, stop, update (databases are dumped before an update), logs, resource limits.
- **Containers → GitOps:** point a stack at a Git repository; new commits show up with a diff and can be deployed – or deployed automatically.
- **Containers → Security check / Image policies:** risky compose settings and rules for allowed images.

## 6. Document as you go

- **Documentation → Service profiles** (*Steckbriefe*): purpose, address, owner per service – they feed the emergency handbook.
- **Documentation → Emergency handbook** (*Notfallhandbuch*): restore steps per service are generated; add contacts, then print or export it and keep a copy offline.
- **Documentation → Operations:** incidents, problems, change requests, runbooks.

## 7. Compliance (if you run IT for others)

- **Compliance → Baseline check:** most requirements are rated automatically from what HostCore already checks; assess the rest by hand with evidence.
- **Compliance → Data protection:** records of processing (Art. 30 GDPR) and TOM (Art. 32 GDPR) – the TOM page lists what the system itself can confirm.
- **Settings → Access review** (*Rechte-Review*): sign off who has which rights every 90 days.
- **Compliance → Evidence package:** one ZIP for a period, for auditors or customers.

## More servers

Install the agent on further servers – a Raspberry Pi, a Debian or Ubuntu machine or VM – with `./install.sh --agent --from <IP of the main dashboard>` (fresh cloud images are fine, missing base packages are installed) and add them under **Settings → Remote servers** – switch between them at the top right. Details in [README.de.md](../README.de.md) (section „Remote-Server“).

## Help

Command palette: **Ctrl+K / ⌘K** finds every page and setting. Self-test with copyable fixes: **Settings → Self-test** (*Selbstdiagnose*). Questions and bugs: [GitHub issues](https://github.com/Follor09/HostCore/issues).
