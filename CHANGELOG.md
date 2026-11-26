# Changelog

Releases are tags `vX.Y.Z`; installs on the stable update channel only follow these.

## Unreleased

## v1.9.0 – 2026-11-26

### New
- **Terminal recordings easier to find and searchable:** a *Mitschnitte* button in the terminal header and an entry in the search (Ctrl+K) lead to the recordings (admins only). The list filters by user, target and date as you type; *Im Inhalt suchen* searches what all recordings showed – commands and output, without colour codes – and lists the matching places with their time; clicking one plays the recording from just before it. Searches are recorded in the activity log. Works for the selected server, also on agents.

## v1.8.0 – 2026-11-26

### New
- **Off-site backup by generations (grandfather-father-son):** retention now has a fourth generation – yearly states – next to daily, weekly and monthly, and the form calls them sons, fathers, grandfathers and great-grandfathers with an explanation; new backups keep 7 / 4 / 6 / 2. No weekly full backup is needed: every run transfers only changes and every state can be restored on its own.
- **Automatic check of the off-site backup:** weekly or monthly (first chosen weekday of the month) at a chosen hour – on by default for new backups. Each check reads the structure and another twelfth of the data back from the target, so all of it is read within 12 checks; the manual *Prüfen* does the same. A failed check shows up as a warning; the card shows the plan and the last result with its part. A planned backup or check that finds the other one running waits and tries again for up to four hours.

## v1.7.1 – 2026-11-26

### Changed
- **"Datenbank … läuft nicht" leads to the container:** the hint in the status bar and the task bar, the event and the alarm open the Docker page with that container marked and scrolled into view (stopped containers shown), where it can be started. The other database hints still lead to the database overview. Modules can now give each hint its own link.

## v1.7.0 – 2026-11-26

### New
- **Set warnings to green:** every hint of the status bar on the start page has a check mark – *Auf grün setzen* for *until something changes*, *24 hours* or *7 days*; *Alle auf grün* does all at once. A hint set to green no longer counts, so the bar turns green. It shows again when it changes (e.g. "2 Lücken" becomes "3 Lücken", or a warning becomes critical), when its time is up, or when the problem was gone for a day and comes back. Hints set to green stay visible under *auf grün gesetzt* with who and until when, and can be shown again. Recorded in the activity log.
- **Hints and alarms in the task bar:** the bar at the bottom of every page shows how many hints are open (or *alles grün*); a click or the new filter *Hinweise* lists them together with the open alarms – open where a hint comes from, set it to green, or acknowledge and close an alarm, from any page. `#hinweise` opens it directly.
- **Alarms: *Alle erledigt*** sets every open and acknowledged alarm to done at once (System → Alarme).

## v1.6.0 – 2026-11-26

### New
- **Off-site backup for several servers:** the main dashboard lists the backups of all connected servers, one group per server, with state, progress, *Jetzt sichern* and *Prüfen*, and a link to open the server. *Auf andere Server übertragen* sets up a backup of this server on the chosen others – same target, sign-in (and Google client ID), selection, schedule and password, each server in its own folder `HostCore/<server name>`; copying again updates the backup of the same name instead of adding another. Servers without the feature yet or without restic/rclone say so. The guide has a new section *Mehrere Pis*.

## v1.5.7 – 2026-11-26

### New
- **Guide for the off-site backup** ([docs/EXTERNE-SICHERUNG.md](docs/EXTERNE-SICHERUNG.md), German): how it works, setting up each target, an own Google client ID step by step (including the greyed-out *Publish* button and "Fehler 403: access_denied"), everyday use, restoring, getting the data back without HostCore when the server is gone, a table of error messages, and how to remove everything again – the backup, the data in the target, the access granted to Google/Dropbox/Nextcloud, the Google project, leftovers on the server and the packages. Linked from the section, the client ID form and the READMEs.

## v1.5.6 – 2026-11-26

### Fixed
- **Off-site backup to Google Drive failed with "500 Internal Server Error":** one failed upload of a data pack failed the whole backup, because a plain upload can't be repeated once its data is on the way – rclone then answered restic with an error. Packs from 1 MB up now go to Drive as resumable uploads in 8 MB pieces, and a failed piece is sent again (up to 20 times).
- **Error messages of the off-site backup lost the actual reason:** rclone's lines start with a long Google address and the pack's name, so the cut-off kept only those. Addresses, timestamps and pack names are shortened now and the end of the line – the reason – is kept.
- The backup's card says for Google Drive whether an own client ID or rclone's shared, often throttled one is in use.

## v1.5.5 – 2026-11-26

### New
- **Real upload speed of an off-site backup:** for Google Drive, Dropbox and WebDAV the progress shows how much rclone has really uploaded and how fast (from rclone's own statistics every 30 s), next to restic's files and data read. Throttling by the provider, failed uploads and retries reach the task log while the backup runs – once a minute at most – instead of only at the end.

## v1.5.4 – 2026-11-26

### Fixed
- **Clearer Google Drive throttling message:** it now tells from Google's answer whose quota ran out – rclone's shared client (then: set up an own client ID) or the own Google project (then: try again later) – instead of quoting a link from Google's error text. A short throttling of an own project no longer fails the backup at once: the check waits a minute and tries again, up to three times.

## v1.5.3 – 2026-11-26

### Fixed
- **Off-site backup to Google Drive stopped or failed with "context deadline exceeded":** the sign-in used rclone's own Google client, which every rclone user shares and Google throttles; rclone then waits and retries for minutes, the upload seems to stand still, and the next start ran into restic's one-minute limit for rclone's first answer. Now:
  - an own Google client ID and secret can be entered with the backup (*Eigene Google-Client-ID*, with a link to rclone's five-minute guide); the sign-in is then made for it and the backup gets its own quota;
  - before each run a quick connection check says in plain words what is wrong – throttled, sign-in expired or revoked, no answer within 3 minutes – instead of restic's timeout;
  - restic gives rclone 5 minutes for its first answer, rclone's own error messages reach the log, and on Google Drive old data removed by the retention bypasses the trash, which would otherwise keep counting against the storage quota.

## v1.5.2 – 2026-11-26

### New
- **Progress of a running off-site backup:** whether started by hand or by its schedule, the backup's card shows the phase (connecting, backing up, pruning old states), a meter with the percentage, files and data done out of the total, how long it has been running and how long it still takes – restic's own estimate once it has scanned the folders, before that an estimate from the last run. Updated every few seconds; the overview of *Speicher & Backups* names running backups with their percentage too. The last run shows its total duration.

### Fixed
- **Off-site backup could stall with many unreadable files:** restic's error output was only read at the end, so a run with lots of warnings could fill the pipe and stop. It is read alongside now.

## v1.5.1 – 2026-11-26

### New
- **Update button on the start page:** the arrow icon in the navigation bar looks for an update, says what would be installed, asks once and installs it the same way as the update timer (`update.sh`, which also updates root's copy of the helpers); the page waits for the restart and reloads by itself. A lit dot on the icon shows that an update is waiting. Needs the permission *Updates installieren*.

### Fixed
- **Installing an update from the dashboard restarted nothing:** the button in the update tab (and the GitHub webhook) ran the update in a worker thread, where scheduling the restart failed after the new code was already in place – the dashboard kept running the old version and reported the update as failed. It now goes through the update unit like the timer; only where that unit doesn't exist (development) does it update in place, and the restart works from any thread.

## v1.5.0 – 2026-11-26

### New
- **Off-site backup** (System → Speicher & Backups → *Externe Sicherung*): back up this server to Google Drive, Dropbox, Nextcloud/WebDAV, S3-compatible storage (AWS, Backblaze B2, Hetzner, Wasabi, MinIO …) or a USB drive. Each backup picks what to include – Docker volumes and compose stack folders, Kubernetes (k3s) volumes with the cluster database, the dashboard's own data with its database dumps, system settings (`/etc`, cron, `/usr/local`), logs (`/var/log`), home folders or own folders – plus exclusions, a schedule by weekday and hour, and how many daily, weekly and monthly states to keep. restic encrypts everything on the server before it leaves, transfers only changes and prunes old states; the password is generated and can be shown to keep it somewhere safe. Google Drive and Dropbox are connected in the dashboard: sign in with the provider, paste the address the browser ends on, done – no rclone needed on your own computer. States can be listed, checked (structure plus 2 % of the data) and restored into a root-only folder below `/var/lib/pi-dashboard/restore/`; nothing is overwritten in place. Runs as a task as root through the helper, which builds the folder list itself and accepts own folders only below /home, /srv, /opt, /mnt, /media, /data and /var/www; password hashes and SSH host keys are never backed up. Failed and overdue backups appear in the status overview and the e-mail reports. Setting up, credentials and restores need *Benutzer & Berechtigungen*, starting a backup *Einstellungen ändern*. restic and rclone can be installed with one click.

## v1.4.1 – 2026-11-26

### Fixed
- **Uploads land on the server the page shows:** file uploads went past the server check added in v1.4.0, so a file list opened for Pi 2 uploaded to the main server once another tab had switched back. Uploads now say which server they are meant for and are refused with a note and a reload otherwise.
- **File integrity explains only the updated package itself:** an update of `python3` also marked changed files of `python3-requests` and every other `python3-*` package as explained and closed them; `libc6` did the same for `libc6-dev`. Now only the package's own files (and its `:arch` variant) count.
- **Two snapshot schedules on one VM keep their own snapshots:** retention counted all automatic snapshots of the VM, so a daily schedule keeping 7 and a weekly one keeping 4 left only 4. Automatic snapshots now carry their schedule's id and each schedule removes only its own; snapshots from before stay untouched while a VM has more than one snapshot schedule.
- **A failing VM schedule is not repeated every minute:** an unexpected error (e.g. a virsh timeout) was not recorded, so the snapshot or backup was tried again every minute for 30 minutes and the schedules after it waited. Every failure now counts as that slot's run and shows up as a warning.
- **Deleting a VM backup no longer leaves disks behind unseen:** when a backup disk couldn't be removed, the backup vanished from the list anyway and the disk kept taking space in the pool. Now the backup stays in the list marked *Unvollständig* with the disks that are left, deleting it again retries them, and it can't be restored any more. Retention of scheduled backups does the same.
- The VM page names the real reason when VMs aren't available (Raspberry Pi, not Linux, or an unsupported platform) instead of always saying Raspberry Pi.
- The operating system name in the settings is cut at 80 characters like everywhere else (one function instead of two).

## v1.4.0 – 2026-11-26

Virtual machines, a new look and navigation, more compliance tools, and a fix for the baseline check that could stop the dashboard from answering.

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

### Fixed
- **Baseline check, privacy page and weekly report no longer hang the dashboard:** the baseline check asked the privacy overview for its records, the privacy overview collected its evidence by running the baseline check, and so on until Python's recursion limit - every level running all checks again. On a Pi with real data that took so long that the dashboard stopped answering and had to be restarted; the baseline report (print view and Markdown), the evidence package, the weekly report and the status overview (privacy warnings) ran into the same loop. In the print view of reports short codes such as IAM-1 or OPS.1.1.3 no longer break in the middle. The checks now only read the privacy counts, the privacy evidence takes the last baseline result, and a nested run is cut off.
- **Long computations can't block the rest any more:** baseline check, evidence package, log verification and export, Compose and secret scans, SBOM export, weekly report and Markdown exports run in a pool of two workers of their own; everything else (live data, logins, the activity log of each request) gets a larger shared pool. System, processes, services, drives, logs, update check and update installation no longer run in the event loop. Under constant load (200,000 log entries, verification, baseline check and report in parallel) ordinary requests answered in 22 ms on average instead of timing out.
- **Activity log verification in chunks:** the hash chain is read a few thousand entries at a time instead of all at once, so other requests keep logging meanwhile; a finding is confirmed with one consistent read. The baseline check uses a verification result up to an hour old, and two baseline runs at the same time share one.
- **Actions always reach the server the page shows:** the remote selection is one cookie for the whole browser, so a tab still showing Pi 2 sent its restart, terminal or console to the main server after another tab had switched back (and the other way round). Pages now say which server they show with every action, terminal ticket and live connection (WebSocket); when that is no longer the selected server the request is refused, a note explains it and the page reloads. The restart and shutdown dialogs name the server and say when it is the one the dashboard itself runs on.

### Changed
- **README and screenshots:** all screenshots retaken in the new look from a demo server with sample data – new overview as the lead image, containers, VMs and VM details, baseline check, change requests, patch deadlines, access review and the light theme; new social preview image. The English README describes the interface and the responsiveness work, the German one the navigation bar and the instrument-panel look instead of the old sidebar; the baseline check is listed with its 39 requirements.
- **Baseline check page:** the fulfilment as a large reading with an LED meter, the counts as filters (*Handlungsbedarf* by default, remembered per browser), areas with meter and a short tally, requirements as quiet rows with a status LED and one line of evidence instead of coloured pills and borders; a click opens the description and the assessment form. The introduction folds away, and groups with a single section no longer repeat their title above it.
- **Navigation at the top:** the sidebar is replaced by a bar across the top. Hovering a page opens a menu with its groups (System, Container, Einstellungen) or, on pages without groups, its sections; Dokumentation, Compliance, Wissen, Wochenbericht and IT-Toolbox share the menu *Wissen & Nachweise*, own links get a *Links* menu, several boards are listed under *Übersicht*. Search, the small actions and Einstellungen sit at the right end. Inside a group the sections are a second tab row instead of a list on the left, and the IT-Toolbox and VM pages show their sections as chips on top. On phones the bar is a drawer behind the menu button.
- **Instrument-panel look:** the design now takes its cues from the hardware it runs: graphite (dark) or brushed aluminium (light) panels without outlines, titles in IBM Plex Sans Condensed like the printing on a rack unit, readings in large light figures, meters as lit LED segments, chart wells with a fine dot raster, status as LEDs. Buttons and tabs are pills, the open tab and primary buttons in ink; colour is used only as a signal, green by default (the other signal colours stay selectable). IBM Plex replaces Inter and JetBrains Mono, also in the terminal; icon boxes in front of card titles are gone.
- **Less clutter:** pages with groups (System-Werkzeuge, Container, Einstellungen, Dokumentation) show one section at a time with a list of the group's sections on the left (a row of pills on phones); old links to a section still open it. The overview tiles drop the repeated section chips. The sidebar no longer repeats the tabs, sorts its pages into *Betrieb* and *Wissen & Nachweise*, has search on top and settings plus the small actions at the bottom. The header keeps restart and shutdown as icons and drops the Git chip. The default start board shows load, services, network, events and history; clock, weather, notes, Wake-on-LAN, tasks and the other cards wait in a folded section *Mehr*. Boards still in the old default layout move over by themselves; boards changed by hand stay as they are.
- **New look:** calm, flat design in the style of current admin dashboards: neutral zinc greys instead of neon, 1px borders instead of glows and glass blur, smaller radii, one typeface (Inter) with aligned numbers, labels in normal case, underline tabs, a quieter sidebar and top bar, status dots in the services widget. Blue is the new default accent (cyan and the others stay selectable); light and dark theme both follow. One web font fewer to load (Outfit), and no more blur over moving content.
- **Lighter live view:** all open dashboards share one measurement instead of each tab running its own every 1.5 s; processes refresh every 3 s, services every 10 s, storage every 15 s, and only parts that changed are sent. A tab in the background closes its live connection after 15 s and pauses its polling; the 3D model turns at about 15 frames a second.
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
