# ⚡ HostCore Server Dash – Server- & Homelab-Dashboard für Debian, Ubuntu und Raspberry Pi

> 🇬🇧 English: [README.md](README.md) · Einstieg: [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)

Ein schlankes, hochmodernes **Server- & Homelab-Dashboard** für Linux-Server (Debian, Ubuntu) und den Raspberry Pi – mit automatischem **GitHub Auto-Update**, Hardware-Monitoring, Docker & Diensten, **virtuellen Maschinen (KVM)**, Speicher-Explorer und System-Journal.

---

## 🌟 Features

- **GitHub Auto-Update System (Vollautomatisch):**
  - **systemd-Timer (jede Minute):** Prüft GitHub auf neue Commits. Sobald du einen Commit pushst, zieht der Server das Update automatisch, aktualisiert bei Bedarf Abhängigkeiten und startet den Dienst neu – unabhängig von der App, also auch nach einem Absturz.
  - **Keine Portweiterleitung nötig:** Funktioniert hinter jedem Heim-Router / Fritz!Box ohne offene Ports oder statische IPs.
  - **GitHub Webhook Support:** Wer einen Tunnel (Cloudflare/Tailscale) oder DynDNS nutzt, kann den Webhook `http://<IP>:8000/api/webhook/github` einrichten für sekundenschnelle Push-Updates bei jedem Commit.
  - **Live-UI Integration:** Header-Badge mit aktuellem Commit-Hash, leuchtender *"Update verfügbar!"*-Hinweis und eigener Reiter *"Git & Updates"* mit Ein-Klick-Update und Logbuch.
- **Echtzeit-Hardware-Monitoring (WebSocket Stream @ 1.5s):**
  - **CPU:** Gesamtauslastung %, Taktfrequenz (MHz), Auslastung jedes einzelnen Kerns + dynamischer Canvas-Sparkline-Chart.
  - **Temperatur & Throttling-Diagnose:** CPU-Temperatur in °C (`vcgencmd` / `thermal_zone0`), automatische Statuserkennung (Optimal / Warm / Kritisch) & Raspberry Pi Firmware-Flags (Erkennung von Unterspannung, Taktbegrenzung und Hitzedrosselung).
  - **Arbeitsspeicher (RAM) & SWAP:** Belegt / Frei / Gesamt mit MB-Genauigkeit.
  - **Netzwerk & Durchsatz:** Live Download- und Upload-Rate (KB/s bzw. MB/s) mit Datentraffic.
- **Verlauf (24h / 7 Tage):** CPU, Temperatur, RAM, Netzwerk und Lüfterstufe werden jede Minute in `history.db` (SQLite, ~0,5 MB) gespeichert und 7 Tage aufbewahrt – als Diagramm mit Hover-Details und Tabellenansicht.
- **System-Updates:** Anzahl ausstehender apt-Pakete (Sicherheitsupdates hervorgehoben) und Hinweis, wenn ein Neustart nötig ist. Ein Klick auf die Kachel öffnet die Detailansicht mit allen Paketen und den Buttons *Paketlisten aktualisieren* und *Update & Upgrade installieren* inkl. Live-Log. apt läuft dabei als eigener systemd-Dienst (`pi-dashboard-apt@.service`) und wird durch einen Dashboard-Neustart nicht unterbrochen.
- **Änderungen vor dem Update:** im Fenster *System-Updates* zeigt „Änderungen“ pro Paket die Changelog-Einträge seit der installierten Version (`apt-get changelog`), die genannten CVEs, die Dringlichkeit, welche Dienste das Update neu startet und ob danach ein Systemneustart nötig ist. „Alle Änderungen prüfen“ fasst das für alle ausstehenden Pakete zusammen.
- **Laufwerke & Mount-Punkte:** wie `lsblk` – jedes Laufwerk mit seinen Partitionen (Füllstand, Dateisystem, Mount-Punkt), nicht eingehängte Partitionen, Swap (auch zram) und weitere Geräte (loop). Bind-Mounts von Kubernetes/Docker (z.B. `/var/lib/kubelet/pods/…`) werden unter ihrer Partition zusammengefasst statt als eigene Laufwerke angezeigt.
- **NVMe-Zustand:** Temperatur, verbleibende Lebensdauer, Reserve, geschriebene TB und Betriebsstunden (via `smartctl`; `install.sh` richtet dafür eine eng begrenzte sudo-Regel ein).
- **Laufwerk-Aktivität:** Lese-/Schreibrate je Laufwerk live in der Speicher-Karte und als „Laufwerk“ im Verlauf-Diagramm. Beim Netzwerk-Verlauf lässt sich eine Schnittstelle (LAN, WLAN, NetBird …) wählen.
- **Schnellaktionen:** eigene Skripte als Buttons. Ausführbare Dateien in `~/pi-dashboard-actions/` ablegen (`chmod +x`), optional mit Kopfzeilen `# name: …`, `# description: …`, `# confirm: yes`, `# timeout: 900`. Laufen als Dashboard-Benutzer, Ausgabe live im Fenster, Stoppen möglich; jeder Lauf steht im Aktivitätsprotokoll. Ein Beispiel-Skript wird beim ersten Mal angelegt.
- **Web-Dienste:** eigene Dienste im Heimnetz/VPN (Home Assistant, NAS …) werden alle 1/5/15 Min. per HTTP geprüft; Karte mit Status, Antwortzeit und 24-Std.-Leiste. Verwalten unter Einstellungen → Web-Dienste. Adressen im Internet werden abgelehnt.
- **Helles Design:** Einstellungen → Darstellung (Automatisch / Hell / Dunkel) oder „Design-Schema umschalten“ in der Seitenleiste.
- **Notizen:** Karte auf dem Dashboard, auf dem Server gespeichert (alle Geräte sehen dasselbe), mit Checklisten (`- [ ]`), Überschriften und Links; speichert beim Tippen und überschreibt nie still eine Änderung von einem anderen Gerät. Nur nach Anmeldung sichtbar.
- **NVMe-Verlauf:** „Verlauf“ in der NVMe-Karte zeigt Verschleiß, pro Tag geschriebene Datenmenge und Temperatur (Tages-Ø und -Max.) über 30 Tage, 90 Tage oder 1 Jahr, dazu unsichere Abschaltungen, Medienfehler und eine hochgerechnete Restlebensdauer (ab 30 Tagen Daten). Ein Wert pro Tag, 3 Jahre aufbewahrt. Die NVMe-Temperatur gibt es zusätzlich im Verlauf-Diagramm (24 h / 7 Tage).
- **Notfallhandbuch (Dokumentation → Notfallhandbuch):** wie jeder Dienst nach einem Ausfall zurückkommt – automatisch aus Stacks, Steckbriefen, Sicherungen, Datenbank-Dumps, GitOps, Inventar und Verträgen zusammengestellt, mit fertigen Befehlen (Ordner, Variablen, Volumes, Datenbank-Restore, Start, Prüfung). Pro Dienst Priorität, zugeordnete Sicherungen, eigene Schritte, Runbook und Prüfung; Notfallkontakte und der Hinweis, wo Zugangsdaten liegen (nie die Passwörter selbst). Lücken ergeben eine Bereitschaft in Prozent; Export als Druckansicht/PDF oder Markdown für die Ablage offline.
- **Container & Pods:** Docker-Container und Kubernetes-Pods (k3s / kubectl) mit Status, Neustarts und Fehler-Hervorhebung.
  - Docker braucht Zugriff auf den Socket: `sudo usermod -aG docker $USER`
  - k3s braucht eine lesbare kubeconfig: `mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config`
- **Stromversorgung (Pi 5):** Board-Leistung in Watt aus dem PMIC (`vcgencmd pmic_read_adc`), Eingangsspannung, vom Netzteil gemeldete Stromstärke, Unterspannung (live / seit Boot). Auch im Verlauf unter „Leistung“.
- **Datei-Explorer & Speicher-Analyse:** Über „Öffnen“ bei jedem Laufwerk: Ordner durchsuchen, Dateien herunterladen (nur lesen), Ordnergrößen berechnen und die größten Dateien finden. Sensible Pfade (Dashboard-Login, GitHub-Token, `~/.ssh`, `~/.kube` …) sowie `/proc`, `/sys`, `/dev` sind gesperrt; immer nur mit Login.
- **Web-Terminal:** Echte Shell im Browser („Terminal“ in der Seitenleiste), läuft als dein Benutzer (`sudo` wie gewohnt). Öffnen erfordert das Passwort erneut (Einmal-Ticket, 60 s), nur von der Dashboard-Seite selbst, max. 5 gleichzeitig. **Dauerhafte Sitzungen:** die Shell läuft in tmux (eigener Server, als systemd-Benutzerdienst) – bricht die Verbindung ab, wird die Seite neu geladen oder startet das Dashboard neu, läuft sie weiter und die Seite verbindet sich von selbst wieder (mit einem Token nur für diesen Tab, ohne Passwort). Laufende Sitzungen lassen sich von einem anderen Gerät übernehmen (Passwort) oder beenden; nach 24 Std. ohne Nutzung enden sie. Nach 30 Min. ohne Eingabe wird nur die Verbindung getrennt. Ohne tmux endet die Shell mit der Verbindung. xterm.js liegt im Repo (`static/vendor/xterm`, MIT) statt aus einem CDN.
- **Terminal teilen:** der Besitzer gibt eine laufende Sitzung für einen anderen Benutzer mit dem Recht „Web-Terminal“ frei – zum **Zuschauen** (tmux-Client nur lesend, Eingaben kommen nicht an) oder zum **Mitschreiben**. Die Person öffnet das Terminal und wählt die Sitzung unter „Laufende Sitzungen“ (Passwort wie beim Öffnen). Neben dem Status steht, wer gerade verbunden ist; „Freigabe beenden“ trennt sofort.
- **Terminal-Mitschnitte** (Einstellungen → Terminal-Mitschnitte, nur Administratoren): jede dauerhafte Sitzung wird als asciicast aufgezeichnet (tmux `pipe-pane`, auch ohne verbundenen Browser und über Neustarts) und lässt sich mit Pause, Springen, 1–8-facher Geschwindigkeit und gekürzten Pausen abspielen. Passwörter an einer sudo-Abfrage sind nicht enthalten, weil das Terminal sie nicht anzeigt. Abspielen steht im Aktivitätsprotokoll; Aufbewahrung einstellbar (Standard 30 Tage), abschaltbar. Laufende Sitzungen tragen den Hinweis „Aufzeichnung“.
- **Aufgaben (wie in Proxmox):** Compose-Aktionen, App-Installationen, automatische Stack-Updates, Schnellaktionen, Datenbank-Dumps und Systemupdates laufen als Aufgabe mit eigenem Protokoll. Jede läuft als eigener systemd-Benutzerdienst (`picore-task-<id>`) – unabhängig vom Browser und auch über einen Neustart des Dashboards (Auto-Update) hinweg. Die Leiste unten rechts auf jeder Seite zeigt laufende Aufgaben; die Liste (Start, Dauer, Benutzer, Beschreibung, Status) öffnet das Protokoll, live solange die Aufgabe läuft, mit „Abbrechen“. Fehlgeschlagene Aufgaben werden zu Ereignissen (und damit zu Alarmen). Protokolle liegen in `tasks/` (die letzten 300, höchstens 60 Tage). `install.sh` installiert tmux und schaltet Lingering für den Dashboard-Benutzer ein.
- **GitOps (Container → GitOps):** Compose-Stacks direkt aus einem Git-Repository (Branch, Unterordner, Compose-Datei; https mit optionalem Token oder SSH mit den Schlüsseln des Dashboard-Benutzers). Geklont wird nach `~/picore-apps/<name>`; der ausgecheckte Commit ist der ausgerollte Stand. Neue Commits, die den Ordner des Stacks betreffen, erscheinen mit Diff; „Ausrollen“ läuft als Aufgabe wie das sichere Stack-Update (Datenbanken sichern, `up -d --remove-orphans`, Gesundheitsprüfung) – auf Wunsch automatisch. Abweichungen (auf dem Server geänderte Dateien, fehlende oder gestoppte Dienste, andere Images) werden gemeldet. Lokale Variablen liegen nur auf dem Server (`gitops-env/`, per `--env-file`). Ein Webhook pro Stack (`/hook/gitops/<token>`) lässt GitHub/Gitea/GitLab sofort prüfen.
- **Image-Richtlinien (Container → Sicherheitscheck):** erlaubte Registries, feste Versionen statt `latest`, Fixierung per Digest, gesperrte Images (Muster) und Höchstalter, mit Ausnahmen pro Stack. „Warnen“ meldet Verstöße der laufenden Container; „Blockieren“ stoppt zusätzlich Stack-Updates, App-Installationen, Vorlagen und GitOps-Ausrollen mit verbotenen Images (Prüfschritt vor dem `pull`, Grund im Aufgaben-Protokoll).
- **Änderungsanträge (Dokumentation → Betrieb):** geplante Änderungen mit Risiko, betroffenen Diensten, Ablauf, Rückfallplan und Prüfung. Freigabe nach dem Vier-Augen-Prinzip (Recht „Änderungen freigeben“; ohne zweiten Freigeber als Selbstfreigabe vermerkt), Standardänderungen mit geringem Risiko vorab genehmigt. Die Freigabe legt auf Wunsch das Wartungsfenster an, Überschneidungen werden angezeigt, das Ergebnis landet im Betriebstagebuch; Export als Markdown.
- **Geheimnis-Scan (System-Werkzeuge → Sicherheit):** sucht Schlüssel, Tokens und Klartext-Passwörter in Stack-Ordnern, GitOps-Klonen, Schnellaktionen, eigenen Ordnern und der Dokumentation; `.env`- und Schlüsseldateien werden auf zu offene Rechte geprüft. Gespeichert werden nur maskierte Auszüge und Fingerabdrücke. Täglich und auf Knopfdruck; Befunde lassen sich akzeptieren, neue werden zu Ereignissen.
- **Patch-Fristen (System-Werkzeuge → Schwachstellen & Support):** jede Lücke mit verfügbarem Fix bekommt eine Frist nach Schwere (Standard: kritisch 7, hoch 30, mittel 90, niedrig 180 Tage) ab dem Tag, an dem der Fix zum ersten Mal auftauchte. Ampel pro Lücke, überfällige werden zu Ereignissen; Ausnahmen mit Begründung und Ablaufdatum. Behobene Lücken bleiben ein Jahr als Nachweis (Tage bis zur Behebung, Einhaltung, Durchschnitt pro Schwere); Bericht als Markdown/PDF.
- **Probleme (Dokumentation → Betrieb):** wiederkehrende Störungen zu einem Problem bündeln, Ursache analysieren, als bekannten Fehler mit Workaround festhalten und über einen Änderungsantrag lösen (Entwurf direkt aus dem Problem). Dienste mit mehreren Störungen in 90 Tagen werden vorgeschlagen, offene Störungen auf einem Dienst mit bekanntem Fehler zeigen den Workaround.
- **Compliance (eigene Seite):**
  - *Grundschutz-Check:* 33 Anforderungen angelehnt an BSI IT-Grundschutz (ORP.4, OPS.1.1.3, CON.3, DER.4, SYS.1.6 …) und CIS, die meisten automatisch aus den vorhandenen Prüfungen bewertet; jede auch von Hand mit Notiz und Nachweis. Erfüllungsgrad pro Bereich, Bericht als Markdown/PDF. Keine Zertifizierung.
  - *Datenschutz:* Verzeichnis der Verarbeitungstätigkeiten (Art. 30 DSGVO) mit Rechtsgrundlage, Betroffenen, Datenkategorien, Empfängern, Löschfristen und beteiligten Systemen; TOM (Art. 32) nach Kontrollbereichen, jeweils mit dem, was das Dashboard selbst feststellt. Druckansicht, keine Rechtsberatung.
  - *Nachweis-Paket:* ein ZIP für einen Zeitraum mit Grundschutz-Check, Patch-Fristen, Rechte-Reviews, Änderungen, Störungen und Problemen, Aktivitätsprotokoll mit Integritätsprüfung, Datensicherung, technischen Prüfungen, Notfallhandbuch, VVT und TOM – jede Datei mit SHA-256 im Manifest, ohne Passwörter oder Tokens.
- **Dienste steuern:** Klick auf eine Dienst-Kachel öffnet Start / Stopp / Neustart (mit Bestätigung) und das Journal des Dienstes. `install.sh` erlaubt per sudo genau diese Units – sonst nichts. Beim Auto-Updater: „Update jetzt ausführen“.
- **Container-Steuerung:** Docker-Container starten, stoppen, neu starten (mit Bestätigung) und Logs ansehen.
- **Wake-on-LAN:** PCs, NAS & Co. per Klick wecken; Geräte unter *Einstellungen → Wake-on-LAN* verwalten.
- **NetBox:** Unter *Einstellungen → NetBox* Adresse und API-Token eintragen (NetBox 3.x/4.x, auch `nbt_`-Tokens ab 4.5). *System-Werkzeuge → NetBox* zeigt Präfixe mit Auslastung, IP-Adressen, Geräte und VMs und gleicht sie mit dem Geräte-Scan ab: nicht dokumentierte Geräte, abweichende IP oder MAC, reservierte Adressen in Benutzung, dokumentierte Adressen ohne Antwort. Dazu Adressen, die in NetBox *und* im Netz frei sind (ohne IP-Bereiche wie den DHCP-Pool). Mit der Berechtigung „NetBox bearbeiten“ und einem Token mit Schreibrechten lassen sich Geräte und freie Adressen direkt in NetBox eintragen. Der Token bleibt auf dem Server.
- **Server-Dienste & Homelab-Ports:**
  - Automatische Überwachung von: **SSH** (:22), **Docker**, **Webserver** (:80), **NetBird VPN** (wt0), **Kubernetes** (k3s / k8s / microk8s, :6443) und dem **Auto-Updater** – jeweils mit Status *Aktiv*, *Inaktiv* oder *Fehler*.
- **Laufwerke & Speicher-Explorer:**
  - Übersicht über alle eingehängten Datenträger (SD-Karte, NVMe-SSDs, externe USB-Festplatten unter `/media` und Dateisysteme).
- **System-Journal & Diagnose:**
  - **System-Logs:** Liest `journalctl -n 30` und `dmesg` direkt im Dashboard aus.
  - **Prozesse:** Top-Prozesse live, Suche nach Name oder PID, eigene Prozesse beenden (SIGTERM, bei Bedarf erzwingen mit SIGKILL). Root-Prozesse nur über das Web-Terminal.
  - **Diagnose-Terminal:** Schnell-Befehle (`docker ps`, `ping 1.1.1.1`, `free -h`, `df -h`, `vcgencmd temp`, `vcgencmd get_throttled`).
  - **Geführter Neustart / Herunterfahren:** Mit Bestätigungs-Modal.
  - **Smart-Display & Wetter:** Uhrzeit, Uptime und Open-Meteo Wetter.

---

## 🍓 Installation

**Voraussetzungen:** ein 64-bit-System mit Debian 12 oder neuer bzw. Ubuntu 22.04 oder neuer – PC, Mini-Server, VM oder Raspberry Pi (am besten Pi 5, Pi 4 geht auch) mit Raspberry Pi OS – und ein Benutzer mit `sudo`. Docker, k3s und KVM sind optional – die Bereiche erscheinen, sobald sie installiert sind; die Virtualisierung lässt sich auf der Seite *VMs* mit zwei Klicks einrichten. Pi-spezifische Anzeigen (Drosselung, PMIC-Leistung, Lüfterkurve, 3D-Modell) erscheinen nur auf einem Raspberry Pi; auf anderen Rechnern kommen Modell und CPU-Temperatur aus DMI bzw. coretemp/k10temp.

### Schnellstart – direkt auf dem Server (per SSH)

```bash
git clone https://github.com/Follor09/RaspberryPi-Dash.git ~/pi-dashboard
~/pi-dashboard/install.sh
```

`install.sh` richtet venv & Pakete, den Dienst `raspberry-dashboard`, den Auto-Update-Timer und die eng begrenzten sudo-Regeln ein und fragt am Ende nach dem ersten Administrator. Danach ist das Dashboard unter `http://<IP-des-Servers>:8000` erreichbar. Das Skript kann jederzeit erneut ausgeführt werden.

> **Hinweis zu Updates:** Eine neue Installation folgt nur veröffentlichten Releases (stabiler Kanal, siehe [Auto-Update](#-auto-update)). Wer jede Änderung sofort haben will, stellt unter *Einstellungen → Auto-Update* auf „Jeder Commit (main)“; wer Updates selbst freigibt, nutzt einen eigenen Fork oder schaltet den Timer ab: `sudo systemctl disable --now pi-dashboard-update.timer`.

### Eigene Version (Fork) mit Auto-Update

1. Repository auf GitHub **forken** und den Fork auf den Rechner klonen.
2. Auf den Server bringen – entweder vom Rechner aus:
   ```bash
   ./deploy.sh pi@<IP-DEINES-PI>
   ```
   oder direkt auf dem Server:
   ```bash
   git clone https://github.com/<DEIN-BENUTZERNAME>/RaspberryPi-Dash.git ~/pi-dashboard
   ~/pi-dashboard/install.sh
   ```
3. Ab dann landet jeder Push auf deinen Fork innerhalb einer Minute auf dem Server. Ist der Fork privat, braucht der Server einen Lese-Token (siehe [Privates Repo](#-privates-repo-github-token-hinterlegen)).

---

## 🔄 Auto-Update

**Update-Kanal** (*Einstellungen → Auto-Update*): **Nur Releases (stabil)** folgt nur veröffentlichten Versionen (Tags `vX.Y.Z`) und geht nie auf ein älteres Release zurück – das ist die Voreinstellung bei einer neuen Installation. **Jeder Commit (main)** übernimmt jede Änderung des Branches, wie unten beschrieben (bestehende Installationen bleiben auf diesem Kanal). Der Kanal steht in der Datei `.update-channel` im Dashboard-Ordner.

### Jeder Commit (Kanal „main“)

Ab jetzt reicht auf dem Mac:

```bash
git commit -am "Neue Funktion" && git push
```

Der systemd-Timer `pi-dashboard-update.timer` prüft **jede Minute** GitHub. Bei einem neuen Commit:
1. installiert er geänderte `requirements.txt` (vor dem Code-Wechsel – schlägt pip fehl, läuft die alte Version weiter),
2. setzt den Code auf `origin/<branch>` (`git reset --hard` – lokale Änderungen auf dem Server werden verworfen),
3. startet den Dienst neu (bei geänderten `.service`/`.timer`-Dateien, `install.sh` oder `services_manager.py` läuft `install.sh` erneut).

**Sicherheit:** Alles, was als root läuft (`update.sh`, `apt-run.sh`, `install.sh`, die Unit-Dateien und die sudo-Regeln), kommt aus einer **eigenen Kopie des Repos direkt von GitHub** unter `/var/lib/pi-dashboard/src` (gehört root). Der Dashboard-Ordner gehört deinem Benutzer – root führt daraus nie etwas aus. So kann niemand, der nur deinen Benutzer (oder die Dashboard-App) kontrolliert, über geänderte Skripte oder eine umgebogene `origin`-URL root werden. Ein neuer Token aus `set-token.sh` wird beim nächsten Durchlauf automatisch übernommen. Firewall und fail2ban laufen über `pi-helper.sh` aus dieser root-Kopie, das jedes Argument streng prüft – sudo erlaubt nur dieses eine Skript.

Der Timer läuft unabhängig vom Dashboard – auch wenn ein fehlerhafter Commit die App abstürzen lässt, wird der nächste Fix-Commit trotzdem gezogen.

```bash
journalctl -u pi-dashboard-update -f         # Update-Log live ansehen
sudo systemctl start pi-dashboard-update     # Sofort manuell aktualisieren
systemctl list-timers pi-dashboard-update    # Nächste Prüfung
```

### 🔐 Login, Benutzer & Berechtigungen

Das Dashboard ist komplett passwortgeschützt. Den **ersten Administrator** legst du **nur auf dem Server** an (nicht über das Web):

```bash
~/pi-dashboard/set-password.sh                  # Passwort setzen / Benutzer anlegen (der erste wird Administrator)
~/pi-dashboard/set-password.sh users            # Benutzer und Gruppen anzeigen
~/pi-dashboard/set-password.sh public-view on   # Ansehen ohne Login (z.B. Wanddisplay) – Aktionen bleiben geschützt
~/pi-dashboard/set-password.sh public-view off
```

Weitere Benutzer verwaltet ein Administrator im Dashboard unter **Einstellungen → Benutzer** und **→ Berechtigungen**:

| Gruppe | Darf |
|---|---|
| **Administrator** | alles, auch Benutzer & Rechte (nicht änderbar) |
| **Bearbeiter** | System-Werkzeuge & Logs ansehen, Dateien hochladen/teilen, Notizen, Schnellaktionen, Dienste steuern, Netzwerk-Werkzeuge, Einstellungen – ohne Terminal, Neustart, Updates, Firewall, Benutzerverwaltung |
| **Benutzer** | System-Werkzeuge ansehen, Dateien ansehen, Notizen |
| **Betrachter** | nur das Dashboard |

- **Eigene Gruppen** anlegen (Rechte von einer bestehenden übernehmen) und in einer Matrix ankreuzen – 24 Einzelrechte von „Logs lesen“ bis „Web-Terminal“. Änderungen gelten sofort, ohne neue Anmeldung. Eingebaute Gruppen lassen sich anpassen und auf Standard zurücksetzen.
- Pro Benutzer: Gruppe wechseln, Passwort setzen, auf allen Geräten abmelden, deaktivieren (sofort abgemeldet), löschen. Es bleibt immer mindestens ein aktiver Administrator; die eigenen Admin-Rechte kann man sich nicht selbst nehmen.
- Der Server prüft **jede** Anfrage gegen eine zentrale Rechte-Tabelle (`backend/access.py`); neue ändernde Endpunkte sind ohne Eintrag nur für Administratoren. Die Oberfläche blendet nicht erlaubte Knöpfe und Bereiche aus.
- Passwörter als scrypt-Hash in `auth.json` (nur für deinen Benutzer lesbar, wird nie committed). Eine alte `auth.json` mit einem Benutzer wird automatisch übernommen – dieser wird Administrator.
- Benutzernamen ohne Groß-/Kleinschreibung („Anna“ = „anna“), Passwörter exakt.
- Sitzung per signiertem Cookie (12 h, mit „Angemeldet bleiben“ 30 Tage) – Auto-Updates melden niemanden ab. Neues Passwort oder „Abmelden“ beendet nur die Sitzungen dieses Benutzers.
- Nach 5 Fehlversuchen wird die IP zunehmend lange gesperrt (30 s … 15 min), dazu pro Konto ab 10 Fehlversuchen (höchstens 5 min). Hinter Cloudflare oder einem Reverse-Proxy zählt die echte Besucher-IP (siehe *Öffentlicher Betrieb*).
- Aktionen werden nur von der eigenen Seite akzeptiert (CSRF-Schutz). Der GitHub-Webhook funktioniert ohne Login nur mit gesetztem `GITHUB_WEBHOOK_SECRET`.

### 🛡️ Öffentlicher Betrieb (Cloudflare, Reverse-Proxy, Internet)

Unter **Einstellungen → Sicherheit** (Administratoren) mit einer Checkliste, was für einen öffentlichen Zugang noch fehlt:

- **Echte Besucher-Adresse:** „Cloudflare (Tunnel oder Proxy)“ oder „Eigener Reverse-Proxy“ wählen. `CF-Connecting-IP` bzw. `X-Forwarded-For` gelten nur von vertrauenswürdigen Proxy-Adressen (Standard: Server selbst, Docker, k3s). Login-Sperren, Rate-Limit, Aktivitätsprotokoll und die Netz-Beschränkung sehen so den echten Besucher statt des Tunnels – sonst würden 5 Fehlversuche irgendwo alle aussperren.
- **Zwei-Faktor-Anmeldung (TOTP):** pro Konto unter *Einstellungen → Zwei-Faktor-Anmeldung* mit QR-Code für jede Authenticator-App und 10 einmaligen Wiederherstellungscodes. Als Regel „Pflicht für Administratoren“ oder „für alle“ – Betroffene richten sie bei der nächsten Anmeldung ein (`/2fa`), vorher geht nichts anderes. Handy verloren: Administrator → Benutzer → „Zwei-Faktor zurücksetzen“ (entfernt auch die Passkeys), oder auf dem Server `./set-password.sh 2fa-reset <name>`.
- **Erneute Bestätigung:** ausgewählte Rechte (Standard: Benutzer, Firewall, Neustart, Updates, Apps installieren) verlangen vor einer Änderung Passwort und Code erneut – gilt 15 Minuten pro Sitzung. Ein Dialog fragt danach und wiederholt die Aktion.
- **Nur aus Heimnetz / VPN:** ausgewählte Bereiche (Standard: Terminal, Benutzer, Firewall, Neustart) sind aus dem Internet gesperrt – auch für Administratoren. Private Netze und VPN-Adressen (NetBird, Tailscale) gelten als intern, weitere lassen sich eintragen. Aus dem Internet lässt sich die Sperre nicht einschalten (Schutz vor Selbst-Aussperrung).
- **Passkeys:** unter *Einstellungen → Passkeys* Face ID, Touch ID, Windows Hello, Handy oder Sicherheitsschlüssel hinterlegen (bis zu 10 pro Konto); auf der Anmeldeseite „Mit Passkey anmelden“ ohne Benutzername, Passwort und Code – sicher gegen gefälschte Anmeldeseiten. Ein Passkey zählt als Zwei-Faktor-Anmeldung und taugt auch für die erneute Bestätigung. Nur über HTTPS (z.B. Cloudflare) und unter der Adresse, unter der er angelegt wurde. Prüfung ohne Zusatzpaket (eigener CBOR-Leser, Signaturen über `cryptography`): Herkunft, Gerätebestätigung mit PIN/Biometrie und Signaturzähler gegen kopierte Schlüssel.
- **Passwort vergessen:** auf der Anmeldeseite mit Benutzername oder E-Mail-Adresse – ein Link (30 Minuten, einmal benutzbar) geht an die hinterlegte Adresse (*Einstellungen → Konto* bzw. für Administratoren unter *Benutzer → E-Mail*). Die Antwort ist immer gleich, ob es das Konto gibt oder nicht; höchstens 5 Anfragen pro Stunde und Adresse, eine Mail pro Konto alle 5 Minuten. Danach sind alle Geräte abgemeldet, eine Zwei-Faktor-Anmeldung bleibt bestehen; der Benutzer bekommt eine Bestätigungsmail. Braucht unter *Einstellungen → E-Mail* Mailserver und Dashboard-Adresse; abschaltbar unter *Sicherheit*.
- **Anmeldung per SSO (OpenID Connect):** unter *Einstellungen → Anmeldung per SSO* Pocket ID, Authentik, Keycloak o.ä. eintragen (Authorization-Code-Flow mit PKCE, ID-Token gegen die Schlüssel des Anbieters geprüft). Beim ersten Mal wird das Konto beim Anbieter mit einem Dashboard-Benutzer verknüpft – über gleichen Benutzernamen oder bestätigte E-Mail-Adresse, oder es wird neu angelegt; Gruppen des Anbieters bestimmen auf Wunsch die Dashboard-Gruppe. Die SSO-Anmeldung kann als Zwei-Faktor gelten, und Passwort/Passkey lassen sich auf Administratoren als Notzugang beschränken (`./set-password.sh` geht immer). „Bestätigen“ vor kritischen Änderungen funktioniert auch über den Anbieter.
- **Rechte-Review:** unter *Einstellungen → Rechte-Review* alle Konten mit kritischen Rechten, zweitem Faktor und letzter Aktivität, dazu Gruppen, Links ohne Anmeldung und MCP-Identitäten. Befunde (inaktive oder nie genutzte Konten, Administratoren bzw. kritische Rechte ohne zweiten Faktor, abgelaufene Gäste, alte Passwörter) lassen sich direkt beheben. Ein Administrator zeichnet das Review mit Notiz ab (Standard: alle 90 Tage); der Stand wird mit SHA-256-Fingerabdruck gespeichert und als Markdown bzw. CSV exportiert. Ein fälliges Review steht in der Statusübersicht.
- **Sitzungen:** jede Anmeldung mit Gerät, Adresse (mit Land) und letzter Nutzung; einzeln beenden (eigene unter *Sitzungen*, alle Benutzer unter *Sicherheit*).
- **Neue Adresse:** Anmeldung von einer bisher unbekannten Adresse außerhalb des Heimnetzes wird ein Sicherheitsereignis (und je nach E-Mail-Einstellung eine Mail).
- **Header & Härtung:** Content-Security-Policy, Schutz vor Einbetten, `nosniff`, Referrer-Policy, Permissions-Policy, HSTS über HTTPS, `Secure`-Cookie hinter HTTPS-Proxys, kein Caching von API-Antworten, keine FastAPI-Seiten `/docs` und `/openapi.json`, ohne Login keine Versionsnummer.
- **Begrenzungen:** höchstens 120 Anfragen pro Minute und Adresse aus dem Internet ohne Login (einstellbar; Heimnetz und VPN ausgenommen); Login-Sperre zusätzlich pro Konto (ab 10 Fehlversuchen, höchstens 5 Minuten).
- **Passwörter:** mindestens 10 Zeichen, keine bekannten Standardpasswörter, nicht der Benutzername (gilt für neue Passwörter).

Empfehlung für den Betrieb hinter einem Cloudflare-Tunnel: Proxy-Art „Cloudflare“, Zwei-Faktor „Pflicht für Administratoren“, „Nur aus Heimnetz / VPN“ für Terminal und Benutzerverwaltung einschalten (aus dem Heimnetz), Port 8000 in der Firewall nur fürs LAN öffnen.

### 📱 Handy & Tablet

- Auf dem Handy: **Navigationsleiste unten** (Dashboard, System, Suche, Terminal, Einstellungen), kompakter Kopfbereich, der beim Scrollen verschwindet, und Dialoge als Bottom-Sheets.
- Alle Seiten passen ab 360 px Breite ohne seitliches Scrollen; Tab-Leisten scrollen seitlich, Tabellen zeigen auf kleinen Displays nur die wichtigen Spalten.
- Größere Tippflächen auf Touchscreens, keine Zoom-Sprünge beim Tippen in Eingabefelder (iOS).
- **Zum Home-Bildschirm hinzufügen** (Safari: Teilen → „Zum Home-Bildschirm“, Chrome: Menü → „App installieren“): eigenes Icon, startet im Vollbild wie eine App, berücksichtigt Notch & Home-Indikator.

### 🔎 Befehlspalette (Strg+K / ⌘K)

Auf jeder Seite öffnet **Strg+K** (Mac: **⌘K**) oder „Suchen & Befehle“ in der Seitenleiste eine Suche über alle Seiten, Bereiche (z.B. „Firewall“, „SSH-Anmeldungen“, „Sprache“) und Aktionen („System-Updates öffnen“, „NVMe-Verlauf“, „Dateien durchsuchen“, „Prozess suchen“, Dienste steuern, Wake-on-LAN-Geräte wecken, Verlauf-Diagramme, Sprache, Abmelden). Pfeiltasten + Enter, tippfehlertolerant, zuletzt verwendete Befehle stehen oben. Ab zwei Zeichen durchsucht sie zusätzlich die **Inhalte**: Inventar, Betriebstagebuch, Runbooks, Steckbriefe, Verträge, Befehle, Notizen, Aufgaben, Erinnerungen, Textblöcke und Links der Boards sowie alle Apps – ein Treffer springt an die passende Stelle. Im Web-Terminal gehört Strg+K der Shell – dort den Eintrag in der Seitenleiste benutzen.

### © Fußzeile

**Was ist neu:** Nach einem Auto-Update zeigt das Dashboard beim nächsten Öffnen einmal, welche Änderungen dazugekommen sind (aus dem Git-Verlauf, also auf Englisch). Später jederzeit über „Was ist neu“ in der Fußzeile oder die Befehlspalette.

Unter jeder Seite (außer dem Terminal): Copyright, installierte Version (Commit + Datum), Link zum Quellcode sowie Hinweise zu Open-Meteo (Wetterdaten, CC BY 4.0), xterm.js (MIT) und den Schriften (SIL OFL). Der Name steht in `static/footer.js` (`OWNER`).

### 📋 Überblick

- **Statusleiste** oben im Dashboard: „Alles in Ordnung“ – oder genau, was Aufmerksamkeit braucht (Dienst mit Fehler, Web-Dienst nicht erreichbar, Laufwerk fast voll / bald voll, zu heiß, Unterspannung, NVMe-Verschleiß, Sicherheitsupdates, Dashboard-Update). Ein Klick springt an die richtige Stelle.
- **Ereignisse:** Zeitleiste (Karte auf dem Dashboard, komplett unter System-Werkzeuge → Ereignisse) mit Ausfällen und Erholung von Diensten, Drosselung, fehlgeschlagenen Anmeldungen (Dashboard + SSH), Updates, Neustarts und neuen Geräten.
- **Auffälligkeiten:** alle 10 Minuten werden CPU, Temperatur, RAM und Netzwerk der letzten 15 Minuten mit dem verglichen, was zu dieser Tageszeit üblich ist; deutliche Abweichungen werden zum Ereignis (und zur E-Mail). Empfindlichkeit und Messwerte einstellbar; die Erkennung lernt in den ersten Tagen.
- **Kapazitätsplanung:** Tageswerte von CPU und RAM (Durchschnitt, Spitze) bleiben so lange wie der Speicherverlauf; aus dem Trend der letzten 30 Tage wird geschätzt, wann es eng wird.
- **DNS-Überwachung:** eigene DNS-Einträge alle 15 Minuten abfragen – Änderung, Fehler oder Abweichung vom erwarteten Wert wird zum Ereignis.
- **IP-Konflikte & Adresswechsel:** alle 5 Minuten wird geprüft, welches Gerät auf eine Adresse antwortet; wechselt das, oder antworten zwei Geräte abwechselnd (IP-Konflikt), steht es unter „Geräte im Netzwerk“ und in den Ereignissen.
- **Speicherprognose:** „voll in ~N Tagen“ aus dem täglichen Füllstand, dazu die am stärksten gewachsenen Ordner (7 / 30 Tage). Ordnergrößen misst der Server einmal täglich mit niedrigster Priorität (`du`).
- **Backups** (System-Werkzeuge): restic- und Borg-Repositorys (lokal, SFTP, REST, S3/B2 über Umgebungsvariablen) sowie Backup-Ordner (neueste Datei, optional nur `*.sql.gz` o. ä.) werden stündlich geprüft – letztes Backup, Anzahl, Größe. Ist das letzte älter als das eingestellte Höchstalter oder scheitert die Prüfung, gibt es ein Ereignis, einen Eintrag in der Statusübersicht und eine E-Mail. Das Repository muss für den Dashboard-Benutzer lesbar sein; Passwörter bleiben in `settings.json`.
- **Alarme mit Quittierung & Eskalation** (Einstellungen → Alarmierung, System → Alarme): neue kritische Ereignisse (auf Wunsch auch Warnungen) werden zum Alarm, der quittiert werden muss. Stufe 1 bekommt sofort eine Mail, weitere Stufen (bis zu vier, Benutzer mit hinterlegter E-Mail-Adresse oder Adressen) nach ihrer Wartezeit, solange niemand quittiert; die letzte Stufe lässt sich wiederholen. Quittiert wird im Dashboard oder mit dem Link in der Mail ohne Anmeldung (ein Link pro Empfänger, erst ein Klick auf der Seite quittiert), die anderen Empfänger erfahren es per Mail. Kommt die Entwarnung („wieder aktiv“, „wieder erreichbar“ …), schließt sich der Alarm von selbst; Wiederholungen derselben Störung werden zusammengefasst. Unquittierte Alarme stehen in der Statusübersicht; die normale Ereignis-Mail lässt weg, was als Alarm kommt.
- **Wochenbericht** (`/report`): die letzten 7 Tage auf einer Seite – Kennzahlen, Verfügbarkeit, Drosselung, Speicher, Top-Programme, Änderungen, neue Geräte. „Drucken / PDF“ erzeugt eine saubere Druckansicht.

### 🗂️ Planung & Auswertung

- **Verlauf abspielen** („Verlauf abspielen“ in der Seitenleiste, „Abspielen“ im Verlauf oder Befehlspalette): gibt die letzten 24 Stunden oder einen der letzten 7 Tage minutengenau wieder. CPU, Temperatur, RAM, Netzwerk, Lüfter, Leistung und der 3D-Pi zeigen die Werte der jeweiligen Minute (mit Sparklines davor), alle anderen Karten werden ausgegraut. Zeitregler, Wiedergabe/Pause (Leertaste), ±15 Min. (Pfeiltasten, mit Umschalt ±1 Std.), Tempo von 1 Min./s bis 1 Std./s. Ereignisse des Tages (Ausfälle, Drosselung, Anmeldungen …) sind farbige Marken auf dem Regler; Zeiten ohne Messwerte sind schraffiert. Esc kehrt zu den Live-Werten zurück.
- **Erinnerungen:** wiederkehrende Aufgaben („monatlich: Backup prüfen“) – einmalig, täglich, wöchentlich, monatlich oder jährlich, auch mit Intervall („alle 3 Monate“). Fällige stehen auf der Karte und in der Statusleiste, bis sie abgehakt sind; abhaken setzt wiederkehrende auf den nächsten Termin (der 31. bleibt in kurzen Monaten der letzte Tag), „Morgen“ verschiebt um einen Tag. Für alle Geräte gleich, Bearbeiten mit dem Recht „Notizen bearbeiten“.
- **Aufgaben (Kanban):** Board mit *Offen*, *In Arbeit* und *Erledigt*. Karten per Drag & Drop oder Pfeil-Schaltflächen (auch auf Touch-Geräten) verschieben, mit Beschreibung, Labels und Fälligkeitsdatum; überfällige und heute fällige Karten sind markiert. Die Dashboard-Karte zeigt Zähler und anstehende Aufgaben und nimmt neue per Enter an.
- **Live-3D-Pi:** Karte mit einem drehbaren 3D-Modell des Pi 5 (reines CSS, keine Bibliothek). Der Kühler glüht je nach CPU-Temperatur von Blau bis Rot, der Lüfter dreht mit der echten Lüfterstufe, die grüne LED blinkt schneller bei CPU-Last, die Ethernet-LEDs flackern mit dem Netzwerkverkehr, die rote LED blinkt bei Unterspannung. Ziehen oder Pfeiltasten drehen, Doppelklick setzt die Ansicht zurück.
- **Erweiterte Prüfungen** (Einstellungen → Web-Dienste): neben Webseiten auch „Webseite enthält Text“ (oder darf ihn nicht enthalten), JSON-Wert (Pfad wie `data.0.state`, optional mit erwartetem Wert), TCP-Port, Ping und DNS-Server (Name abfragen, optional mit erwarteter Antwort) – alles nur im Heimnetz/VPN. Ein Klick auf einen Dienst in der Web-Dienste-Karte zeigt Antwortzeiten (24 Std., 7 oder 30 Tage) mit Ausfällen im Diagramm, Kennzahlen und die Liste der Vorfälle mit Dauer und Fehler; Vorfälle während einer Wartung sind markiert. Prüfungen lassen sich bearbeiten.
- **Geräte-Verfügbarkeit:** in System-Werkzeuge → Geräte im Netzwerk „Überwachen“ – das Gerät wird alle 5 Minuten angepingt, über seine MAC-Adresse (auch nach einem IP-Wechsel per DHCP). Status und Verfügbarkeit stehen in der Geräteliste, Verlauf und Vorfälle in der Web-Dienste-Karte.
- **Öffentliche Statusseite** (`/status`): eine schlichte Seite ohne Anmeldung für Familie und Freunde. Unter Einstellungen → Statusseite einschalten, Titel setzen und Dienste, Web-Dienste und Compose-Stacks auswählen – jeweils mit eigenem Anzeigenamen. Zeigt „Alle Systeme laufen“ / „Teilweise Störung“, den Zustand jedes Eintrags und optional die Verfügbarkeit der letzten 30 Tage als Balken. Es verlassen nur diese Namen und Zustände den Server – keine Adressen, IPs oder Fehlermeldungen. Ausgeschaltet antwortet `/api/public/status` mit 404.

### 📚 Dokumentation (`/doku`)

- **Inventar:** Hardware mit Kategorie, Hersteller, Modell, Seriennummer, Standort, Kaufdatum, Preis und Garantie. Optional mit einem Gerät aus dem Netzwerk-Scan verknüpft – dann steht dabei, ob es gerade online ist und unter welcher IP. Gesamtwert oben; endet eine Garantie in den nächsten 30 Tagen, erscheint ein Hinweis in der Statusleiste.
- **Betriebstagebuch:** eigene Einträge („Was wurde wann geändert und warum“) mit Kategorie (Änderung, Wartung, Störung, Installation, Notiz), Zeitpunkt und Bezug. Durchsuchbar; Einträge erscheinen in den Ereignissen und im Wochenbericht.
- **Runbooks:** Schritt-für-Schritt-Anleitungen (ein Schritt pro Zeile). Ein Durchlauf wird als Checkliste abgehakt – wer welchen Schritt wann erledigt hat, wird gespeichert, auch über mehrere Geräte. Beim Abschluss landet ein Eintrag im Betriebstagebuch (mit übersprungenen Schritten und Notiz); die letzten 30 Durchläufe bleiben sichtbar.
- **Verträge & Abos:** Domains, Server, Lizenzen, Abos mit Anbieter, Kosten, Zahlungsrhythmus, Laufzeitende, Kündigungsfrist und Link zum Kundenkonto. Das Dashboard rechnet aus, bis wann gekündigt werden muss (bei automatisch verlängernden Verträgen für die nächste Verlängerung), markiert Fristen in den nächsten 30 Tagen und verpasste Fristen und zeigt die Kosten pro Monat und Jahr. Fristen und Verlängerungen erscheinen im Kalender-Widget.
- **Kosten:** Strom (vom Server gemessen, hochgerechnet), Verträge & Abos und Hardware-Käufe aus dem Inventar – pro Monat und Jahr, Hardware zusätzlich auf drei Jahre verteilt, dazu ein Diagramm der letzten 12 Monate.
- **Anhänge:** PDFs, Bilder, Text- und ZIP-Dateien (bis 25 MB) an Inventar, Verträge, Steckbriefe und Wiki-Seiten hängen – Rechnungen, Handbücher, Fotos. Gespeichert unter `attachments/` auf dem Server; Bilder und PDFs öffnen sich in einer abgeschotteten Vorschau.
- **Domains:** eigene Domains mit Ablaufdatum, Registrar, Nameservern und DNSSEC – einmal täglich per RDAP (Nachfolger von WHOIS) abgefragt, auch für .de, .ch und .io. Registries ohne öffentliches Ablaufdatum (z. B. DENIC) bekommen das Verlängerungsdatum von Hand. Vor Ablauf (Vorwarnzeit pro Domain) erscheint eine Warnung in der Statusübersicht und in der täglichen E-Mail.
- **Änderungen (Versionsverlauf):** jede Änderung an Inventar, Verträgen, Befehlen, Betriebstagebuch, Runbooks, Steckbriefen und Wissensseiten wird als Version gespeichert – wer, wann, Vergleich mit der Fassung davor (Feld für Feld, längere Texte zeilenweise) und Wiederherstellen; gelöschte Einträge lassen sich zurückholen. Im Wiki zeigt „Unterschiede“ den Vergleich einer früheren Fassung mit der aktuellen. Aufbewahrung unter *Einstellungen → Datenaufbewahrung* (Standard 1 Jahr, höchstens 100 Versionen pro Eintrag).
- **Störungen:** mit Schwere, betroffenen Diensten, Status (untersucht → Ursache gefunden → beobachtet → behoben) und Zeitlinie. Beginn und Ende erscheinen in den Ereignissen; als öffentlich markierte Störungen zeigt die öffentliche Statusseite.
- **Nachbetrachtungen (Post-Mortems):** zu einer Störung Zusammenfassung, Auswirkung, Ursache, was gut und schlecht lief, Erkenntnisse und Maßnahmen mit Verantwortlichem und Termin. Maßnahmen werden auf Wunsch Karten im Aufgaben-Board (Label „Post-Mortem“) und gelten als erledigt, sobald die Karte in *Erledigt* liegt; überfällige stehen in der täglichen E-Mail. Export als Markdown.
- **Befehle:** oft gebrauchte Befehle mit Titel, Beschreibung und Tags – mit einem Klick kopiert; im Web-Terminal fügt der Knopf „Befehle“ sie an der Eingabe ein (ohne sie abzuschicken).
- **Steckbriefe:** pro Dienst, Web-Dienst oder Compose-Stack (oder als eigener Eintrag) Zweck, Adresse, Ports, Speicherort der Daten, Sicherung, Doku-Link, Zuständige und Notizen. Auf der Container-Seite öffnet „Steckbrief“ den passenden Eintrag oder legt ihn an.

Ansehen darf jeder angemeldete Benutzer, Ändern braucht „Notizen & Doku bearbeiten“. Gespeichert in `settings.json`, für alle Geräte gleich.

### 📖 Wissensdatenbank (`/wissen`)

Freie Markdown-Seiten in Ordnern für alles, was nicht in Runbooks oder Steckbriefe passt: Überschriften, Listen, Aufgaben, Tabellen, Code-Blöcke, Zitate; `[[Seitentitel]]` verlinkt auf eine andere Seite. Editor mit Vorschau (Strg+S speichert), Anhänge pro Seite, **Verlauf** mit den letzten 20 Versionen (ansehen, wiederherstellen; Anzahl unter Datenaufbewahrung einstellbar) und Schutz vor gleichzeitigem Überschreiben. **Vorlagen** für neue Seiten: Gerät, Störungsbericht, Anleitung, Dienst, Entscheidung, Netzwerk – dazu jede eigene Seite im Ordner „Vorlagen“; `{{titel}}`, `{{datum}}` und `{{benutzer}}` werden beim Anlegen ausgefüllt. Die Seiten liegen in `wiki.json` und sind über die Volltextsuche (Strg+K) und den MCP erreichbar.

### 🛠️ Wartungsfenster & Gastzugänge

- **Wartungsfenster** (Einstellungen → Wartungsfenster): geplante Arbeiten für alle oder ausgewählte Dienste, Web-Dienste und Stacks – mit Beginn, Ende und Notiz, oder „Jetzt starten (1 Std.)“. Während eines Fensters zählen Ausfälle der betroffenen Dienste nicht gegen die Verfügbarkeit (Verfügbarkeit, Wochenbericht, Statusseite), Ausfall-Ereignisse werden als Hinweis statt Warnung geführt, und die Statusleiste zeigt nur „Wartung: … (bis …)“. Die öffentliche Statusseite zeigt „Wartungsarbeiten“ und kündigt geplante Fenster der nächsten 14 Tage an. Fenster lassen sich nicht rückwirkend anlegen, laufende vorzeitig beenden.
- **Gastzugänge** (Einstellungen → Benutzer → „Zugang“): Konten mit Ablauf nach 1, 7 oder 30 Tagen oder bis zu einem Datum (höchstens 365 Tage). Danach ist keine Anmeldung mehr möglich und laufende Sitzungen enden; „+7 Tage“ verlängert. Gäste können keine Administratoren sein.

### 📁 Dateien

Im Datei-Browser (Laufwerk → „Öffnen“): **Hochladen** per Button oder Drag & Drop (nur wo dein Benutzer schreiben darf, max. 4 GB, bestehende Dateien werden nur nach Rückfrage überschrieben), **Vorschau** für Text/Logs/Configs (mit Suche) und Bilder, **Teilen** als Download-Link für 1 Std., 24 Std. oder 7 Tage – funktioniert ohne Anmeldung für alle, die den Server erreichen. Aktive Links unter Einstellungen → Freigaben.

**Webhooks** (Einstellungen → Webhooks): eigene Adressen `/hook/<token>`, an die Uptime Kuma, Watchtower/Shoutrrr, GitHub oder Skripte (`curl -d "Backup fertig" …`) Meldungen schicken. Sie erscheinen in den Ereignissen und im Wochenbericht; die Stufe wird aus der Meldung erkannt (z.B. Uptime-Kuma „down“ = kritisch) oder fest vorgegeben. Höchstens 60 Meldungen pro Minute und Webhook.

**Austausch-Links** (Einstellungen → Freigaben): ein öffentlicher Link für einen ganzen Ordner – **nur hochladen** (z.B. Logs oder Fotos von anderen einsammeln), **nur herunterladen** (alle Dateien direkt im Ordner) oder **beides**. Gültig 1 Std. bis 30 Tage, mit Größenlimit für Uploads und optionalem Hinweis für Besucher. Die Seite `/x/…` braucht keine Anmeldung; hochgeladene Dateien überschreiben nie etwas („Name (2)“), Dateinamen werden bereinigt, Unterordner und versteckte Dateien werden nie gezeigt. Jeder Up- und Download steht im Aktivitätsprotokoll. Erstellen braucht „Dateien teilen“, Upload-Links zusätzlich „Dateien hochladen“. Gesperrte Dateien (Login, Tokens, SSH-Schlüssel, Aktivitätsprotokoll) lassen sich weder ansehen noch teilen.

**Bearbeiten & Papierkorb:** Textdateien (bis 2 MB, UTF-8) öffnen sich per Stift-Symbol oder „Bearbeiten“ in der Vorschau in einem Editor mit Zeilennummern und Farben für YAML, JSON, INI/`.env`, Shell, Python, Dockerfile, XML, CSS und Markdown (Strg+S speichert, Tab rückt ein, Enter übernimmt die Einrückung). Gespeichert wird atomar (Rechte bleiben erhalten); hat jemand die Datei inzwischen geändert, fragt der Editor nach. JSON wird vor dem Speichern geprüft, Compose-Dateien mit `docker compose config`. „+ Datei“ / „+ Ordner“ legen Neues an. **Löschen** (Papierkorb-Symbol, zweimal klicken) verschiebt in den **Papierkorb** auf demselben Laufwerk (`.picore-trash`, im Home unter `~/.local/share/.picore-trash`) – wiederherstellbar für 30 Tage, danach automatisch weg. Das Dashboard selbst, das Home-Verzeichnis und Laufwerks-Wurzeln lassen sich nicht löschen. Braucht das Recht „Dateien ändern“.

**Suchen, Auswählen, ZIP, Duplikate:** Die Suchzeile durchsucht den geöffneten Ordner samt Unterordnern (Name, auch mit `*`/`?`; Filter nach Typ, Größe, Änderungsdatum; max. 500 Treffer, 25 s). Häkchen vor den Einträgen wählen mehrere aus: **Als ZIP** (wird direkt gestreamt, ohne Zwischendatei), **Ausschneiden/Kopieren** → Zielordner öffnen → **Hier einfügen** (läuft im Hintergrund mit Fortschritt, auch zwischen Laufwerken; vorhandene Namen werden nie überschrieben, sondern „Name (2)“), **In den Papierkorb**. Das Symbol „T“ benennt um. „Als ZIP“ in der Werkzeugleiste lädt den ganzen Ordner. **Duplikate** findet inhaltsgleiche Dateien ab 1 MB (erst Größe, dann Prüfsumme) und zeigt, wie viel Platz sie belegen – „Nur die älteste behalten“ schiebt die übrigen Kopien in den Papierkorb.

### 🎨 Personalisieren

Akzentfarbe (Cyan, Grün, Violett, Orange, Pink, Blau) unter Einstellungen → Darstellung; Größe und Platz jeder Karte im Board-Bearbeiten-Modus; eigene Links (Home Assistant, Router …) unter Einstellungen → Eigene Links – erscheinen oben unter dem Link-Symbol und in der Suche.

### 🧭 Seitenleiste

Links auf jeder Seite: **Boards**, alle **Seiten** mit Namen (System-Werkzeuge, Container, Flotte, Dokumentation, Wissen, Wochenbericht, IT-Toolbox, Terminal, Einstellungen) – die Bereiche der aktuellen Seite (z.B. „Firewall“, „Logs“) stehen direkt darunter und zeigen, wo man gerade ist –, deine **eigenen Links** und die **Aktionen** (Suchen & Befehle, Verlauf abspielen, Board bearbeiten, Vollbild, Design). Der Pfeil oben klappt sie auf reine Symbole ein (gemerkt pro Browser); auf dem Handy öffnet sie das Menü-Symbol oben links.


### 🗂️ Gruppen auf langen Seiten

System-Werkzeuge, Einstellungen, Container und Dokumentation beginnen mit einer **Übersicht aus Kacheln** – eine pro Gruppe (z. B. Zustand, Netzwerk, Sicherheit, Hardware & Strom, Speicher & Backups, Logs, Pakete & Wartung) mit ihrem Zustand: Warnungen aus der Statusübersicht und ein paar Kennzahlen (Geräte online, Firewall, Temperatur, Belegung, Backups, Updates …). Ein Klick öffnet die Gruppe; oben wechseln fixierte Reiter zwischen den Gruppen, die Seitenleiste listet die Gruppen statt aller Bereiche. Die Adresse merkt sich, was offen ist (`#g-netzwerk`), und alte Direktlinks auf einen Bereich (`/system#firewall`) öffnen die passende Gruppe.
### 🧩 Boards (Startseite im Homarr-Stil)

Die Startseite ist ein frei anordenbares Raster aus **Apps**, **Widgets** und den **Karten** des Dashboards, gegliedert in **Kategorien** – und es kann mehrere **Boards** geben (z.B. „Übersicht“, „Medien“, „Netzwerk“). Gespeichert wird auf dem Server, alle Geräte sehen dasselbe.

- **Apps:** Kachel mit Icon, Name und optionaler Beschreibung, die deinen Dienst öffnet. Icons kommen aus der Sammlung [Dashboard Icons](https://github.com/homarr-labs/dashboard-icons) (Suche nach Name, passendes Icon wird automatisch vorgeschlagen) oder von einer eigenen Bild-Adresse; der Server lädt sie einmal und hält sie im Cache (`icon_cache/`). Ein Punkt oben rechts zeigt, ob die App erreichbar ist – der Server prüft jede Minute, auf Wunsch über eine eigene Status-Adresse.
- **Widgets:**
  - **Suchleiste** (DuckDuckGo, Google, Startpage, Bing, Ecosia oder eigene Adresse; findet auch deine Apps) und **eingebettete Webseite** (z.B. ein Grafana-Dashboard).
  - **Kalender:** Monatsansicht mit Erinnerungen (inkl. Wiederholungen), fälligen Aufgaben, Wartungsfenstern und Vertragsfristen, darunter die nächsten Termine; in niedrigen Kacheln nur die Terminliste. Nur nach Anmeldung sichtbar.
  - **RSS-Feed:** bis zu 5 RSS-/Atom-Feeds (z.B. `…/releases.atom` eines GitHub-Projekts), vom Server alle 15 Minuten abgerufen.
  - **Textblock** mit Markdown (Überschriften, fett/kursiv, Code, Links, Listen) und **Linkliste** für viele Links platzsparend in einem Widget.
  - **Remote-Server:** CPU, Temperatur, RAM, Speicher und Laufzeit eines Remote-Servers direkt auf dem Board; „Anzeigen“ schaltet das Dashboard auf ihn um.
- **Erkannt:** Im Hinzufügen-Dialog schlägt das Dashboard Apps aus laufenden Docker-Containern (veröffentlichte Ports, Icon aus dem Image-Namen) und Kubernetes (Ingresses, NodePorts) vor – per Klick aufs Board. Darunter die Geräte aus dem Geräte-Scan und aus NetBox: „Web-Ports prüfen“ sucht auf den üblichen Ports nach Weboberflächen (mit Seitentitel), ein Klick übernimmt sie als App. Geprüft werden nur Adressen im Heimnetz oder VPN.
- **Karten:** alle bisherigen Karten (CPU, Temperatur, Verlauf, Container, Notizen, Aufgaben …), jede einmal pro Board.
- **Bearbeiten** („Board bearbeiten“ in der Seitenleiste, Berechtigung „Dashboard gestalten“, ab 1100 Pixel Fensterbreite): Elemente ziehen, an der Ecke unten rechts in der Größe ändern, auch zwischen Kategorien verschieben; „+ Hinzufügen“ für Apps, Widgets, Karten und Kategorien; Board-Einstellungen mit Name, Spaltenzahl, Hintergrundbild und Abdunkeln. Änderungen werden sofort gespeichert; wurde das Board inzwischen auf einem anderen Gerät geändert, lädt das Dashboard den neuen Stand, statt ihn zu überschreiben.
- Boards wechseln per Klick, **Alt+1…9**, Befehlspalette oder Link (`/#board-medien`). Apps aller Boards lassen sich auch über die Befehlspalette (Strg+K) öffnen. Kategorien lassen sich zuklappen (pro Browser).
- Auf schmalen Bildschirmen (Handy, Tablet) stehen die Apps als Kachelraster oben, die Karten darunter untereinander.
- **Verlauf** (Board-Einstellungen → „Verlauf“): jede gespeicherte Änderung wird als Version aufbewahrt (die letzten 60; schnelle Änderungen hintereinander zählen als eine), mit Zeit, Benutzer und Umfang. „Wiederherstellen“ macht einen früheren Stand wieder aktuell – als neue Version, es geht also nichts verloren.
- Beim ersten Start entsteht ein Standard-Board: Suchleiste, deine eigenen Links als Apps und alle Karten in der Kategorie „System“. „Alle Boards zurücksetzen“ in den Board-Einstellungen stellt es wieder her.

### 🤖 MCP-Server (Claude)

Das Dashboard ist zugleich ein **MCP-Server** (Streamable HTTP unter `/mcp`), mit dem Claude (claude.ai, Claude Desktop, Claude Code) es bedienen kann – über 70 Werkzeuge:

| Bereich | Werkzeuge |
| --- | --- |
| Überblick | `get_overview` (guter Einstieg), `list_hosts`, `fleet_overview`, `get_metrics_history`, `get_events`, `weekly_report`, `search_content` |
| Logs & Diagnose | `get_logs`, `get_error_digest`, `service_journal`, `container_logs`, `list_processes`, `get_storage`, `get_network`, `get_security`, `dns_lookup`, `port_test` |
| Dienste & Container | `list_services`, `service_action`, `list_containers`, `container_action`, `stack_action`, `kube_restart`, `kube_scale` |
| Updates & System | `apt_status`, `apt_run`, `fleet_apt`, `packages`, `system_power`, `list_quick_actions`, `run_quick_action` |
| Netzwerk | `list_lan_devices`, `scan_lan`, `netbox_comparison`, `wake_on_lan`, `firewall`, `web_services`, `maintenance_window` |
| Doku & Planung | Notizen, Aufgaben, Erinnerungen, Betriebstagebuch, Runbooks, Steckbriefe, Inventar, Verträge, Befehle, Wissensdatenbank, Kosten, Boards (`add_app_to_board`) |
| Dateien | `list_directory`, `read_file`, `write_file` (mit Konfliktschutz), `search_files`, `move_to_trash` |
| Backups, Domains, Images | `list_backups`, `check_backup`, `list_domains`, `image_updates`, `image_release_notes` |

**Prompts** (fertige Abläufe, in Claude über das Plus-Menü): `morgen_check`, `stoerung_analysieren`, `wochenbericht`, `wartung_planen`, `update_runde`. **Ressourcen:** Wiki-Seiten (`picore://wiki/<id>`, Übersicht `picore://wiki`), Runbooks (`picore://runbook/<id>`) und Steckbriefe (`picore://servicedoc/<ziel>`) lassen sich als Kontext anhängen.

**Freigaben:** unter **Einstellungen → MCP-Aktivität** wird festgelegt, welche Aktionen (z. B. apt, Neustart, Firewall, Dateien schreiben/löschen) erst nach einer Bestätigung laufen. Claude wartet bis zu einer einstellbaren Zeit; währenddessen erscheint auf jeder Dashboard-Seite eine Leiste mit „Freigeben“ / „Ablehnen“ (und auf Wunsch eine E-Mail). Kommt die Freigabe später, ruft Claude das Werkzeug mit denselben Parametern erneut auf – eine Freigabe gilt 15 Minuten und einmal. Freigeben darf der zugeordnete Benutzer oder ein Administrator, nie der MCP selbst. Darunter steht das **Protokoll aller Werkzeug-Aufrufe** (Zeit, Werkzeug, Benutzer, Parameter, Ergebnis, Dauer) mit Filtern; gespeichert in `mcp_calls.json`.

Fast alle Werkzeuge nehmen `host` – den Namen eines Remote-Servers –, sonst gilt der Haupt-Server. **Anmeldung per OAuth über Pocket ID** (FastMCP OIDCProxy, wie bei eigenen *-no-kari-Servern); jede Pocket-ID-Identität (E-Mail, Benutzername oder Subject) wird unter **Einstellungen → MCP-Server** einem Dashboard-Benutzer zugeordnet. Jedes Werkzeug ruft intern die Dashboard-API als dieser Benutzer auf: es gelten genau dessen **Berechtigungen**, alles steht mit „(MCP)“ im **Aktivitätsprotokoll**, und Remote-Server werden wie im Browser über ihren Agent erreicht.

Einrichtung: in Pocket ID einen OIDC-Client mit der angezeigten Callback-URL (`https://<domain>/auth/callback`) anlegen, eine eigene https-Domain per Cloudflare Tunnel auf `http://<Server>:8000` zeigen lassen, Adresse, Pocket-ID-URL, Client-ID/-Secret und die Zuordnung eintragen, „Speichern & neu starten“. Unter dieser Domain liefert das Dashboard **nur** den MCP und die OAuth-Endpunkte aus, die Oberfläche bleibt im Heimnetz. In Claude: Connector mit `https://<domain>/mcp`; Claude Code: `claude mcp add --transport http picore https://<domain>/mcp`.

### 🛰️ Flotte (`/flotte`)

Dieser Server und alle Remote-Server in einer Tabelle: erreichbar oder nicht, Dashboard-Version (und ob ein Update wartet), ausstehende Paket-Updates mit Sicherheits-Updates und „Neustart nötig“, Temperatur, CPU, RAM, Speicher und Laufzeit; aktualisiert sich alle 30 Sekunden. Mehrere Server auswählen und **Paketlisten aktualisieren** oder **Update & Upgrade installieren** für alle gleichzeitig starten – jeder Server führt es in seinem eigenen apt-Dienst aus (Berechtigung „Updates installieren“).


**Paket-Updates im Detail:** Ein Klick auf die Update-Zahl eines Servers zeigt seine ausstehenden Pakete (installierte → neue Version, Sicherheitsupdates markiert); einzelne Pakete lassen sich gezielt aktualisieren (`apt-get install --only-upgrade`, es kommt nichts Neues dazu) oder alles auf einmal. Sind mehrere Server ausgewählt, fasst **„Ausstehende Updates der Auswahl“** alle Pakete zusammen – mit den Servern, die sie brauchen, Filter und „Nur Sicherheitsupdates“; ausgewählte Pakete werden auf jedem betroffenen Server aktualisiert. „Protokoll“ zeigt die Ausgabe des laufenden bzw. letzten apt-Laufs jedes Servers live.

**Latenz zwischen den Servern:** alle 5 Minuten pingt jeder Server jeden anderen (3 Pakete); eine Matrix zeigt Antwortzeit, Durchschnitt und Paketverlust (24 Std. / 7 Tage), ein Klick den Verlauf. **Dashboard-Version:** die Spalte Version zeigt, welcher Server hinter GitHub zurückliegt oder eine andere Version als der Haupt-Server hat; „Dashboard aktualisieren“ lässt die ausgewählten Server sofort aktualisieren statt beim nächsten Timer-Lauf. **Kapazität:** RAM- und CPU-Trend pro Server und wann Arbeitsspeicher, CPU oder ein Laufwerk knapp werden.
### 🧮 IT-Toolbox (`/werkzeuge`)

Alltagshelfer, die nur im Browser rechnen (nichts geht an den Server oder ins Internet): **Netzwerk-Rechner** für IPv4 und IPv6 (Netz, Maske, Broadcast, Host-Bereich, Anzahl, Art der Adresse; in Teilnetze aufteilen; prüfen, ob eine IP im Netz liegt), **Base64** (auch URL-sicher), **URL-Kodierung**, **JSON** formatieren/verkleinern/prüfen mit Zeile und Spalte, **Hashes** (MD5, SHA-1, SHA-256), **JWT lesen** mit Ablaufzeit, **Passwort- und UUID-Generator**, **Zeitstempel** umrechnen und **Cron-Ausdrücke** in Worten erklärt mit den nächsten Ausführungen.

### 🧰 System-Werkzeuge (`/system`)

- **Netzwerk:** Schnittstellen mit IPs, MAC, Traffic, Link-Speed und WLAN-Signal (SSID, dBm, Band); Gateway & DNS; offene Ports mit Dienst, Prozess und „nur lokal / im Netzwerk erreichbar“.
- **Sensoren:** alle Temperaturen (CPU, RP1-Chip, PMIC, NVMe …), Spannungen und Ströme der Pi-5-Stromschienen, Lüfterdrehzahl und Unterspannungs-Alarm – live, mit 24-Stunden-Kurven.
- **Stromverbrauch:** Leistung des Pi 5 (PMIC) als kWh und Kosten – heute, dieser Monat, Hochrechnung für Monat und Jahr, Balken pro Tag (30 Tage; Tage mit Lücken sind heller). Strompreis (ct/kWh), Währung und ein fester Zuschlag in Watt (Netzteil-Verluste, NVMe, USB – die misst der PMIC nicht vollständig) lassen sich einstellen. Tageswerte werden stündlich zusammengefasst und ein gutes Jahr aufbewahrt.
- **Ressourcen-Verbrauch:** welche Programme über 24 Std. / 7 Tage am meisten CPU und RAM gebraucht haben (pro Programm zusammengefasst, bei Python/Node mit Skriptname), mit Verlauf je Programm – findet z.B. nächtliche Last.
- **Datenverbrauch:** empfangen/gesendet heute, pro Tag (30 Tage) und pro Monat, je Schnittstelle wählbar. 2 Jahre gespeichert.
- **DNS & Traceroute:** DNS-Abfragen (A, AAAA, CNAME, MX, TXT, NS, PTR) gegen System-DNS, Cloudflare, Google, Quad9 oder einen eigenen Server; Traceroute mit Namen und Zeiten (`sudo apt install traceroute`, falls nicht vorhanden).
- **Geräte im Netzwerk:** scannt das Heimnetz (Ping-Sweep + Nachbartabelle, ohne Root), zeigt IP, MAC, Hersteller und Router-Namen, merkt sich Geräte (auch offline), markiert neue, Umbenennen, „Zu Wake-on-LAN“ mit einem Klick. Hersteller-Liste für alle Geräte: `sudo apt install ieee-data`.
- **Port-Check:** jeder offene Port mit Erreichbarkeit aus LAN und VPN (aus Bindung + Firewall-Regeln berechnet, Docker-Ports markiert) und ein TCP-Test zu beliebigen Hosts/Ports.
- **Verfügbarkeit:** Uptime in % je Dienst über 24 Std. / 7 / 30 Tage mit Zeitleiste und Liste der Ausfälle (auch im Dienst-Fenster auf dem Dashboard).
- **Drosselung:** Protokoll von Unterspannung, Hitze-Drosselung, Taktbegrenzung und Temperaturgrenze – inkl. kurzer Ereignisse zwischen zwei Messungen (90 Tage).
- **Lüfterkurve:** vier Stufen (Temperatur + Drehzahl) mit Vorlagen Standard / Leise / Kühl und Live-Vorschau; wird mit Backup in `config.txt` geschrieben und gilt nach dem Neustart.
- **Aktivität:** wer wann was gemacht hat (Anmeldungen inkl. Fehlversuche, Updates, Dienste, Firewall, Prozesse, Downloads, Terminal …), durchsuchbar, 180 Tage. **Manipulationssicher:** jeder Eintrag ist per SHA-256 mit dem vorherigen verkettet; „Integrität prüfen“ zeigt, ob ein Eintrag nachträglich geändert oder gelöscht wurde, „Export (CSV)“ liefert alle Einträge mit Prüfsummen. Einmal täglich wird der aktuelle Stand notiert und steht in der täglichen E-Mail – so fällt auch das Abschneiden der neuesten Einträge auf.
- **Firewall (ufw):** ein-/ausschalten, Regeln ansehen, hinzufügen und löschen. Regeln mit Aktion (Erlauben / Blockieren / Ablehnen / Begrenzen), Richtung (ein-/ausgehend), Schnittstelle, Protokoll, **Quelle + Quellport** und **Ziel + Zielport** (einzeln, Bereich `6000:6010` oder Liste `80,443`), Position (am Ende oder ganz oben mit Vorrang), optional „Treffer protokollieren“ und Kommentar – mit Live-Vorschau des ufw-Befehls. Schutz gegen Aussperren: keine Sperre der eigenen IP fürs Dashboard, von SSH/Dashboard für alle oder von allem ausgehenden Verkehr; beim Einschalten werden SSH und das Dashboard (Port 8000) immer zuerst freigegeben, die letzte Freigabe lässt sich nicht löschen. Vorschläge für k3s, NetBird (wt0) und Webserver, wenn diese laufen. ufw installieren: `sudo apt install ufw`.
- **Verbindungen:** alle offenen TCP/UDP-Verbindungen mit Programm (per `ss` über den Helfer, damit auch Dienste anderer Benutzer benannt sind), Richtung, Gegenstelle mit Land, Filter nach Richtung/Gegenstelle/Text, Programm-Übersicht, „Live“ alle 5 s; interne Verbindungen (localhost, k3s-/Docker-Netze) ausgeblendet; eingehende aus dem Internet lassen sich direkt sperren.
- **Firewall-Traffic:** was die Firewall blockiert hat (aus den `[UFW BLOCK]`-Kernelmeldungen im Journal, alle Boots; Fallback `/var/log/ufw.log`) für 1 Std. / 24 Std. / 7 Tage: Kennzahlen, Diagramm Internet vs. LAN/VPN, häufigste Absender (mit Gerätenamen aus dem LAN-Scan, **„IP sperren“** mit einem Klick), häufigste Ziel-Ports (markiert, wenn auf dem Port ein Programm lauscht; **„Freigeben…“** füllt das Regel-Formular vor) und die letzten Pakete. Filter nach Blockiert/Erlaubt, Richtung, IP, Schnittstelle oder Port. Ist die Protokollierung aus, lässt sie sich direkt einschalten (Stufe „low“). Braucht Lesezugriff aufs Journal (`systemd-journal`-Gruppe, wie bei den SSH-Anmeldungen).
- **Länder (GeoIP):** nach einmaligem Laden der kostenlosen Länder-Datenbank von [DB-IP](https://db-ip.com) (CC BY 4.0, ~5 MB Download, lokal in `geoip.db`, monatlich automatisch aktualisiert) stehen Flagge und Land bei allen Internet-IPs: Firewall-Traffic (plus Übersicht „Herkunftsländer“), SSH-Anmeldungen, fail2ban-Sperren und Aktivität. Alles offline, keine IP verlässt den Server.
- **Länder sperren:** Länder suchen oder aus den Vorschlägen im Firewall-Traffic übernehmen → „Übernehmen“. Die Netze landen in `ipset`-Listen, die eine eigene Regel in ufws `before.rules` vor allen anderen Regeln verwirft (Treffer erscheinen im Firewall-Traffic). Antworten auf eigene Verbindungen, LAN und VPN bleiben unberührt; das Land, aus dem du gerade verbunden bist, wird abgelehnt. `pi-dashboard-geoblock.service` lädt die Listen beim Booten vor ufw; nach jedem Datenbank-Update werden sie erneuert. `ipset` installiert `install.sh` automatisch.
- **SSH-Anmeldungen:** erfolgreiche und fehlgeschlagene Logins der letzten 7 Tage aus dem Journal (pro Verbindung), IPs mit den meisten Fehlversuchen (LAN / VPN / Internet), wer gerade angemeldet ist, und **fail2ban** mit Jails, gesperrten IPs und „Entsperren“. Für alle Logs: `sudo usermod -aG systemd-journal $USER`.
- **CPU-Modus:** Sparsam / Sanft / Ausgewogen / Leistung (cpufreq-Governor) mit Live-Takt; wird gespeichert und nach Neustart wieder gesetzt. sudo erlaubt nur das Schreiben der Governor-Datei.
- **Leistungstest:** Laufwerk (512-MB-Testdatei: sequentiell schreiben/lesen ohne Cache, 4K-IOPS) und CPU (SHA-256 auf 1 / allen Kernen). Ergebnisse mit Temperatur und CPU-Modus gespeichert zum Vergleichen.
- **Fehler-Übersicht:** Fehler (Priorität „err“ und schlimmer) aus dem Journal der letzten 24 Std./7/30 Tage, gruppiert nach Quelle und Meldung – Zahlen, IDs, IPs und Zeiten werden dabei ignoriert. Pro Gruppe Anzahl, Balken pro Tag, erstes/letztes Auftreten, Markierung „neu“ und „gerade eben“; „Im Log suchen“ springt mit passendem Filter zur Log-Suche.
- **Paketverwaltung:** Pakete suchen (apt-cache) mit Details, installierte Pakete nach Größe (nur selbst installierte oder alle). Vor jedem Installieren/Entfernen zeigt ein Probelauf (`apt-get -s`), was dazukommt oder wegfällt und wie viel Platz es braucht. apt läuft dann als eigene systemd-Unit (übersteht einen Dashboard-Neustart) mit Live-Protokoll. Basis-Pakete und alles, was System oder Dashboard brauchen (SSH, sudo, systemd, Kernel, Docker, Python, ufw …), lassen sich nicht entfernen – auch nicht als Abhängigkeit. Braucht das Recht „Updates installieren“.
- **Log-Suche:** System-Journal filtern nach Dienst oder Kernel, Schweregrad, Zeitraum und Suchbegriff (Suche über `journalctl -g`), Treffer hervorgehoben.
- **Speicher aufräumen:** System-Logs älter als 14 Tage, apt-Paket-Cache, ungenutzte Docker-Images & Build-Cache, `~/.cache` und Papierkorb – mit Größenvorschau, einzeln oder alles auf einmal. sudo nur für `journalctl --vacuum-time=14d` und `apt-get clean`.
- **Hardware:** Modell, SoC, RAM, Revision, Seriennummer, Kernel, Firmware, Bootloader mit Update-Hinweis, NVMe mit PCIe-Generation (Tipp für Gen 3), Laufwerke, USB-Geräte mit Geschwindigkeit.
- **Schwachstellen:** bekannte Sicherheitslücken (CVE) in den installierten Paketen (über `debsecan`, `sudo apt install debsecan`) und in den Images laufender Container (über [Trivy](https://trivy.dev), falls installiert) – einmal täglich mit niedriger Priorität, ohne root. Kennzahlen, Pakete gefiltert nach „mit Update“ / „schwer“ / alle, Suche nach Paket oder CVE, je Image die schweren Lücken mit installierter und behobener Version. Einzelne Lücken lassen sich mit Begründung akzeptieren (zählen dann nicht mehr für Warnungen). Neue schwere Lücken mit Fix werden zum Ereignis; offene stehen in der Statusübersicht und der täglichen E-Mail.
- **Lebenszyklus:** wie lange Betriebssystem (Debian / Raspberry Pi OS), Kernel, Python, Docker Engine, Kubernetes (k3s), Node.js, das Pi-Modell (Produktionszusage) und die Images laufender Container (postgres, mariadb, redis, nginx, node, python …) noch Sicherheitsupdates bekommen – Daten von [endoflife.date](https://endoflife.date), einmal täglich. Warnung 90 Tage, kritisch 30 Tage vor dem Ende; Hinweis, wenn nur noch Langzeit-Support (z.B. Debian LTS) läuft.
- **Software-Inventar (SBOM):** welche Software in welcher Version läuft – Systempakete (dpkg, mit Lizenzen aus maschinenlesbaren copyright-Dateien), die Python-Pakete des Dashboards und die Pakete in den Container-Images (aus der täglichen Trivy-Prüfung). Suche nach Name, „Name Version“ (z.B. `openssl 3.0`) oder Lizenz, mit „Alle Server“ über die ganze Flotte. Lizenzübersicht; Export als CycloneDX 1.5 (JSON) oder CSV.
- **Härtung:** die Sicherheits-Grundeinstellungen mit Bewertung in Prozent – SSH (Root-Login, Passwort-Login mit/ohne hinterlegten Schlüssel, leere Passwörter, X11), fail2ban, ufw und Standard-Policy, aus dem LAN erreichbare Dienste, Docker-Ports, die ufw umgehen, sudo ohne Passwort, automatische Anmeldung, Benutzer „pi“, ausstehende Sicherheitsupdates und Neustart, unattended-upgrades. Jeder Mangel mit Befehl zum Kopieren; ohne root, täglich und auf Knopfdruck. Schwere Mängel erscheinen in der Statusübersicht.

### 🐳 Container (`/containers`)

- **Übersicht:** laufende Pods, bereite Workloads, Docker-Container, Stacks, verfügbare Image-Updates und Kubernetes-Warnungen der letzten 24 Std. – plus eine Liste mit allem, was gerade kaputt ist (CrashLoop, OOMKilled, beendete Container …).
- **Kubernetes (k3s):** Workloads (Deployments, StatefulSets, DaemonSets) mit Bereit-Status, Images und Aktionen **Neu starten** (`rollout restart`), **Stoppen** (auf 0 skalieren – „Starten“ holt die alte Anzahl zurück) und **Skalieren**; Pods mit Status, Neustarts, OOMKilled, CPU/RAM (metrics-server), **Logs** (Container-Auswahl, vorheriger Start, Filter, Live) und **Neu erstellen**; Services mit Ports/NodePorts/LoadBalancer-IP; Nodes mit Auslastung und Version; Warnungen (Events). Filter nach Namespace und Text, kube-system ausblendbar. Braucht eine lesbare kubeconfig (`~/.kube/config`, siehe Hinweis auf der Seite).
- **Docker:** alle Container mit Status/Health, CPU (inkl. 24-Std.-Sparkline), RAM, Ports; Starten/Stoppen/Neu starten, Logs, Verlauf.
- **Compose-Stacks:** aus `docker compose ls` und den Container-Labels; **Starten, Neu starten, Aktualisieren** (`pull` + `up -d`), **Stoppen, Entfernen** (`down`, Volumes bleiben) mit Live-Ausgabe; **Compose-Datei** öffnet den Editor – beim Speichern prüft `docker compose config` die Datei, kaputte YAML wird nicht gespeichert.
- **Versionshinweise:** „Was ist neu?“ bei einem Image zeigt die GitHub-Releases seines Projekts (Quelle aus den Image-Labels, sonst die üblichen Orte bei GHCR und LinuxServer.io, sonst selbst eintragen), neuere Versionen als das lokale Image zuerst und mit Hinweis auf Breaking Changes.
- **Image-Updates:** alle 12 Std. (oder per „Jetzt prüfen“) wird der Digest jedes laufenden Images (Docker + k3s) mit der Registry verglichen – Docker Hub, GHCR, Quay, lscr.io & Co., anonym per `HEAD` (zählt nicht gegen das Docker-Hub-Limit). Bei Updates: Stack direkt aktualisieren oder Deployment neu starten. Nach einem Stack-Update wird automatisch neu geprüft.
- **Datenbanken:** PostgreSQL- (auch PostGIS, TimescaleDB, pgvector), MySQL-, Percona- und MariaDB-Container werden erkannt und alle 5 Minuten geprüft – Version, Laufzeit, Verbindungen gegen das Maximum, Größe pro Datenbank mit Wachstum über 7 Tage, Abfragen über 30 Sek., größte Tabellen (bei PostgreSQL mit toten Zeilen und VACUUM-Hinweis), Cache-Trefferquote. Abgefragt wird per `docker exec` mit den Zugangsdaten aus der Umgebung des Containers (`POSTGRES_USER`, `MYSQL_ROOT_PASSWORD` …) – kein gespeichertes Passwort, kein freigegebener Port. Fast volle Verbindungen, Abfragen über 5 Min., Transaktionen über 10 Min. ohne Aktivität, nicht abfragbare oder gestoppte Datenbanken und überfällige oder fehlgeschlagene Dumps werden zu Ereignissen (und damit zu Alarmen). **Dumps** (`pg_dumpall` bzw. `mysqldump`/`mariadb-dump`, gzip) per Klick oder täglich zu einer Uhrzeit nach `db-dumps/<container>/`, die neuesten N bleiben.
- **Sicheres Aktualisieren:** „Aktualisieren“ eines Stacks sichert zuerst dessen Datenbank-Container (Dump nach `db-dumps/`), lädt dann die Images, startet neu und prüft zum Schluss, ob alle Container laufen, Healthchecks grün sind und keiner ständig neu startet (`stackhealth.py`). Schlägt ein Schritt fehl, endet die Aufgabe mit Fehler – ein kritisches Ereignis und damit ein Alarm, der Dump liegt bereit. Gilt auch für automatische Updates.
- **Sicherheitscheck:** jeder Compose-Stack wird täglich (und auf Knopfdruck) auf riskante Einstellungen geprüft – privilegierte Container, eingebundener Docker-Socket, Host-Netzwerk/-PID/-IPC, weitreichende Capabilities, abgeschaltetes seccomp/AppArmor, Host-Ordner wie `/` oder `/etc`, Ports auf allen Schnittstellen, Images ohne feste Version, Passwörter im Klartext in der Compose-Datei, fehlende Restart-Policy. Grundlage ist `docker compose config` (alle Dateien zusammengeführt). Bewertung 0–100 pro Stack, zu jedem Befund der konkrete Fix; bewusst gewollte Befunde lassen sich akzeptieren. Kritische Befunde stehen in der Statusübersicht.
- **Volumes & Netzwerke:** alle Docker-Volumes mit Größe, Stack und nutzenden Containern; Netzwerke mit Subnetz und Containern.
- **Docker aufräumen:** Belegung und was frei werden kann – unbenannte und ungenutzte Images, ungenutzte Netzwerke, Build-Cache, Volumes ohne Container (nur einzeln). Mit Vorschau und Bestätigung.
- **Stack-Vorlagen:** eigene Compose-Dateien (auch aus einem bestehenden Stack übernommen) auf einem oder mehreren Servern als neuen Stack anlegen; Zugangsdaten kommen beim Anlegen in eine `.env` auf dem Ziel-Server, nie in die Vorlage. Braucht „Apps installieren“.
- **Statistik:** CPU, RAM und Netzwerk pro Container (Pods eines Deployments zusammengefasst) für 24 Std. / 7 Tage, einmal pro Minute gemessen (`docker stats`, `kubectl top`), 7 Tage aufbewahrt.
- **App-Store:** zehn empfohlene Apps (Jellyfin, Pi-hole, AdGuard Home, Uptime Kuma, Vaultwarden, Home Assistant, Node-RED, Grafana, Syncthing, Gitea) plus der Katalog von [LinuxServer.io](https://www.linuxserver.io) über deren API – rund 160 Apps, gefiltert auf Images für die Architektur des Servers. Suche über Name, Beschreibung und Kategorie, Filter nach Kategorie und Quelle (auch „Installiert“), Sortierung nach Beliebtheit (monatliche Downloads), Name oder Aktualität. Der Katalog wird höchstens einmal am Tag geladen und in `appstore_cache.json` gespeichert (funktioniert danach offline); Logos lädt der Server selbst und legt sie in `appstore_logos/` ab, der Browser fragt nie bei GitHub an. Das Formular entsteht aus der Konfiguration der App: Ports (freie werden vorgeschlagen, Web-Ports unter 1024 als 8080/8443 …), Ordner (`./name` im App-Verzeichnis oder ein vorhandener Pfad), Umgebungsvariablen (PUID/PGID/TZ automatisch), optionale Ports, Ordner und Geräte zum Anhaken. Apps mit privilegiertem Modus, Host-Netzwerk, Zusatzrechten oder Geräten sind als „Erweiterte Rechte“ markiert und zeigen vor der Installation, was sie brauchen; der Docker-Socket wird nie eingebunden. „Compose-Datei ansehen“ zeigt vorher, was geschrieben wird. Jede App landet in `~/picore-apps/<name>/docker-compose.yml` und wird mit `pull` + `up -d` gestartet, mit Live-Ausgabe; danach ist sie ein normaler Compose-Stack. Systemordner (`/etc`, `/root`, `/dev`, `~/.ssh`, das Dashboard …) lassen sich nicht einbinden.

- **Container-Konsole:** „Konsole“ in der Docker-Liste öffnet eine Shell im laufenden Container (`docker exec`, bash falls vorhanden, sonst sh) – im Web-Terminal mit denselben Regeln: Passwort erneut eingeben, Einmal-Ticket, max. 3 Sitzungen, 30 Min. Leerlauf. Braucht das Recht „Web-Terminal“; jede Sitzung steht im Aktivitätsprotokoll.
- **Live-Logs:** die Logs mehrerer Container gleichzeitig, nach Zeit zusammengeführt und pro Container eingefärbt; Filter, „Nur Fehler & Warnungen“, Live-Modus (alle 2 s neue Zeilen, nach oben scrollen hält an). Die Auswahl merkt sich der Browser.
- **Automatische Updates:** pro Stack „Automatisch aktualisieren“ täglich oder an einem Wochentag zu einer Uhrzeit – derselbe Ablauf wie „Aktualisieren“ (`pull` + `up -d`). Verpasste Läufe (Server aus) werden bis zu 3 Stunden nachgeholt; „Jetzt ausführen“ startet sofort. Jeder Lauf wird protokolliert, Fehlschläge und echte Aktualisierungen erscheinen als Ereignis.
- **Ressourcen-Limits:** CPU-Kerne und RAM pro Dienst eines Stacks. Gilt sofort (`docker update`) und bleibt über `docker-compose.picore.yml` im Stack-Ordner auch nach Updates und Neuerstellen erhalten. Braucht der Kernel RAM-Limits (cgroup memory), sagt das Dashboard Bescheid.

Ansehen braucht „System ansehen“, Logs „Logs lesen“, alle Aktionen „Dienste steuern“, das Bearbeiten der Compose-Datei „Dateien ändern“, Apps installieren das neue Recht „Apps installieren“ (nur Administratoren, kann anderen Gruppen gegeben werden).

### 🖥️ Virtuelle Maschinen (`/vms`)

KVM-Gäste über libvirt – angelehnt an Cockpit, auf jedem Debian-/Ubuntu-Server mit Intel VT-x / AMD-V. **Optional:** unter *Einstellungen → Virtuelle Maschinen* pro Server abschaltbar (Seite, Menüpunkt und alle VM-Befehle verschwinden; laufende VMs bleiben unberührt). Auf einem Raspberry Pi gibt es die Funktion nicht.

- **Einrichtung:** „Pakete installieren“ (QEMU, libvirt, virt-install, OVMF bzw. AAVMF) und „Zugriff einrichten“: der Dashboard-Benutzer kommt in die Gruppe `libvirt`, libvirt läuft, das NAT-Netzwerk und der Speicher-Pool `default` (`/var/lib/libvirt/images`) werden angelegt, danach startet das Dashboard neu. Die Seite zeigt, was fehlt – auch, wenn `/dev/kvm` fehlt (BIOS-Einstellung, oder der Host ist selbst eine VM ohne verschachtelte Virtualisierung). Achtung: die Gruppe `libvirt` hat praktisch Root-Rechte (wie `docker`).
- **Übersicht:** laufende VMs, zugewiesene vCPUs und Arbeitsspeicher gegenüber dem Host (Überbuchung wird markiert), Hypervisor und libvirt-Version. Pro VM Status, CPU-Last, Arbeitsspeicher (mit Gast-Agent der echte Verbrauch), IP-Adresse (DHCP-Lease, Gast-Agent oder ARP), Laufwerksgröße und Autostart.
- **VM erstellen** (als Aufgabe): aus einem **Cloud-Image** (Debian 12/13, Ubuntu 22.04/24.04/26.04 – offiziell, Prüfsumme SHA-512/SHA-256 wird kontrolliert, einmal heruntergeladen und danach für jede VM kopiert) mit **cloud-init** (Benutzer, Passwort als SHA-512-Hash, SSH-Schlüssel, Hostname, Zeitzone, QEMU-Gast-Agent, optional alle Updates beim ersten Start), aus einer **ISO**, einem vorhandenen Laufwerk oder per **PXE**. vCPUs, Arbeitsspeicher, Laufwerk, Speicher-Pool, Netzwerk (NAT, Bridge des Hosts oder direkt an die Netzwerkkarte per macvtap), BIOS oder UEFI, Autostart, Beschreibung.
- **Steuern:** Starten, Herunterfahren, Neu starten, Anhalten/Fortsetzen, Zurücksetzen und Ausschalten (erzwingen) mit Bestätigung.
- **Details:** vCPUs und Arbeitsspeicher (bei ausgeschalteter VM), Titel und Beschreibung, Autostart, Umbenennen, **Klonen** (virt-clone), Löschen (auf Wunsch samt Laufwerken – ISOs bleiben); **Laufwerke** hinzufügen (VirtIO, SCSI, SATA), entfernen oder löschen, **ISO einlegen/auswerfen**; **Netzwerkkarten** hinzufügen, entfernen, „Kabel ziehen“; **Snapshots** erstellen (bei laufender VM mit Arbeitsspeicher), zurücksetzen, löschen.
- **Konsole** in einem eigenen Tab: **grafisch** per noVNC (der VNC-Port lauscht nur auf 127.0.0.1, das Dashboard reicht ihn über eine WebSocket-Verbindung durch) mit Strg+Alt+Entf und „Text senden“, oder **seriell** (virsh console, xterm.js). Jede Verbindung braucht ein Einmal-Ticket und steht im Aktivitätsprotokoll.
- **Images & ISOs, Netzwerke, Speicher-Pools:** Cloud-Images und beliebige ISOs per https (mit Prüfsumme) in einen Pool laden; virtuelle Netzwerke anlegen (NAT, isoliert, geroutet – Überschneidungen mit Host-Netzen werden abgelehnt), starten, stoppen, Autostart; Ordner-Pools anlegen (keine Systemordner), Volumes ansehen und unbenutzte löschen.

Ansehen braucht „System-Werkzeuge ansehen“, Starten/Herunterfahren/Neu starten/Anhalten das Recht **„VMs steuern“** (auch Bearbeiter), alles andere **„VMs verwalten“** – mit erneuter Passwort-Abfrage und, wenn eingeschaltet, nur aus Heimnetz/VPN. Die Einrichtung ist Administratoren vorbehalten. Auf Remote-Servern funktioniert alles genauso (über den Agent).

### 🛰️ Remote-Server (Agent)

Ein Dashboard kann weitere Server (Debian, Ubuntu, Raspberry Pi OS) steuern. Auf dem anderen Server läuft dafür der **HostCore-Agent** – dasselbe Dashboard ohne eigene Oberfläche und ohne Login, das nur mit einem Token auf das Haupt-Dashboard hört:

Läuft auf einem **Raspberry Pi** (Raspberry Pi OS 64-bit), auf **Debian 12/13** und **Ubuntu 22.04/24.04/26.04** – als echter Rechner oder als VM (x86_64 und arm64). Fehlende Grundpakete (git, Python-venv, curl …) installiert `install.sh` selbst, auch auf einem frischen Cloud-Image.

```bash
# auf dem Remote-Server (Pi, Debian-VM, Ubuntu-VM …):
sudo apt update && sudo apt install -y git
git clone https://github.com/Follor09/RaspberryPi-Dash.git ~/pi-dashboard
~/pi-dashboard/install.sh --agent --from 192.168.1.10   # IP des Haupt-Dashboards; zeigt am Ende Adresse + Token

./agent.sh token                 # Token später wieder anzeigen
./agent.sh allow 192.168.1.10    # nur das Haupt-Dashboard zulassen (mehrere: "IP1,IP2" oder ein Netz /24; "any" = alle)
./agent.sh rotate                # neuer Token
./agent.sh status                # an/aus, Adresse, erlaubte Adressen
./agent.sh disable               # wieder normales Dashboard
```

Mit `--from` nimmt der Agent nur Anfragen von dieser Adresse an – zusätzlich zum Token, ein abgegriffener Token allein reicht dann nicht. Ist auf dem Remote-Server ufw aktiv, gibt `install.sh` Port 8000 frei (mit `--from` nur für diese Adresse). Ohne `--from` gilt nur der Token.

Im Haupt-Dashboard unter **Einstellungen → Remote-Server** Name, Adresse (`http://IP:8000`) und Token eintragen („Verbindung testen“ prüft vorher). Danach wechselt der Schalter oben rechts zwischen „Dieser Server“ und den Remote-Servern. Das Haupt-Dashboard leitet dann alle API-Aufrufe, die Live-Werte und das Web-Terminal an den Agent weiter – **alle Funktionen** (Dienste, Prozesse, Container & k3s, Dateien, Firewall, Updates, Pakete, Logs, Verlauf …) zeigen und steuern den gewählten Server. Eine gelbe Leiste zeigt, welcher Server gerade dran ist (rot, wenn er nicht erreichbar ist).

- **Rechte:** Anmeldung, Benutzer und Berechtigungen kommen vom Haupt-Dashboard und gelten auch für die Remote-Server; im Aktivitätsprotokoll des Agents steht, wer (`name@remote`) etwas getan hat. Das Terminal-Passwort wird im Haupt-Dashboard geprüft.
- **Nur lokal:** Login, Benutzer/Rechte, eigene Links, Notizen, Freigabe-Links und „Was ist neu“ gehören immer zum Haupt-Dashboard.
- **Sicherheit:** Der Token wird bei jeder Anfrage mitgeschickt – über `http` im Klartext. Remote-Server darum nur im Heimnetz oder über das VPN (NetBird) ansprechen; `https://` funktioniert, wenn davor ein Reverse-Proxy mit Zertifikat steht. Tokens sind nach dem Eintragen nicht mehr abrufbar. Remote-Server verwalten dürfen nur Administratoren.
- **Unterschiedliche Hardware:** Das Dashboard erkennt, was der gewählte Server hat, und blendet den Rest aus – auf einer VM oder einem x86-Server die Pi-Anzeigen (Drosselung, PMIC, Lüfterkurve, 3D-Modell), auf einem Pi 4 Lüfterkurve und Stromverbrauch, auf einem Pi die virtuellen Maschinen. Die Liste unter *Einstellungen → Remote-Server* zeigt pro Server System, Art (Raspberry Pi, VM, Server) und Architektur.
- Der Agent aktualisiert sich wie jedes Dashboard selbst von GitHub; `agent.json` (Token) bleibt dabei erhalten.

### 🌐 Sprache / Language

Deutsch oder Englisch – umschaltbar unter *Einstellungen → Sprache* oder unten auf der Login-Seite, pro Browser gespeichert (ohne Auswahl nach Browsersprache). `static/i18n.js` übersetzt alle Texte inkl. Live-Werten und Server-Meldungen; Logs, Terminal und Dateinamen bleiben unverändert. Neue deutsche Texte: im Browser `I18N.misses` (Konsole, bei aktivem Englisch) zeigt, was im Wörterbuch noch fehlt.

### ⚙️ Einstellungen (`/settings`, Zahnrad oben rechts)

- **Sprache:** Deutsch / English
- **Konto:** Benutzername/Passwort ändern, „Ansehen ohne Login“ umschalten, alle anderen Geräte abmelden
- **Wetter:** Ort festlegen
- **Dienste:** auswählen, welche Kacheln im Dienste-Widget erscheinen
- **Wake-on-LAN:** Geräte hinzufügen / entfernen
- **Auto-Update:** Repo, Branch, Commit, Timer-Status, Token-Status, „Jetzt prüfen“
- **Verlauf:** Größe & Zeitraum der Messdaten, „Verlauf löschen“
- **Datenaufbewahrung:** wie lange Messwerte (bis 90 Tage – ab 30 Tagen zeigt das Verlaufsdiagramm auch „30 Tage“), Verfügbarkeit, Web-Dienst-Prüfungen, Aktivitätsprotokoll, Datenverbrauch, Speicherverlauf und Papierkorb bleiben und wie viele Wiki-Versionen, Board-Stände, Webhook-Meldungen und MCP-Aufrufe behalten werden; dazu der Speicherbedarf jeder Ablage und „Jetzt aufräumen“
- **E-Mail-Benachrichtigungen:** SMTP (STARTTLS, SSL oder ohne; „Zertifikat nicht prüfen“ für selbst signierte Mailserver), Empfänger und Dashboard-Adresse für Links. Verschickt werden neue Ereignisse ab Warnung oder nur kritische (gesammelt alle 5 Minuten), eine Tagesübersicht zur gewählten Uhrzeit (fällige Erinnerungen, Kündigungsfristen, Garantien, ablaufende Domains, überfällige Backups – nur wenn es etwas gibt), der Wochenbericht an einem Wochentag und MCP-Freigaben sofort. Test-Mail und Vorschau von Tagesübersicht und Wochenbericht per Knopf
- **Selbstdiagnose:** prüft die eigene Installation – systemd-Dienst, Update-Timer und letzter Lauf, Root-Kopie und sudo-Regeln, lokale Änderungen, die das nächste Update verwerfen würde, GitHub-Zugriff (Token), Rechte der Dateien mit Zugangsdaten, Dateibesitzer, freier Platz, Integrität von `history.db` und ob noch gemessen wird, Python-Pakete, NTP, Docker-/Kubernetes-Zugriff, Administratoren, Remote-Server, E-Mail, MCP und GeoIP. Zu jedem Problem der Befehl zum Kopieren; Dateirechte setzen und Datenbank verdichten gehen per Knopf. Läuft zusätzlich täglich, Probleme erscheinen in der Statusübersicht.
- **API:** Verweis auf `/api-doku` – alle Schnittstellen mit Parametern, Berechtigung und „Ausprobieren“, erzeugt aus dem Dashboard selbst
- **MCP-Aktivität:** offene Freigaben, welche Werkzeuge eine Freigabe brauchen, Protokoll der Aufrufe
- **System:** Hostname, Modell, OS, Kernel, Python, Laufzeit, IP-Adressen

Die Einstellungsseite braucht immer ein Login – auch wenn „Ansehen ohne Login“ aktiv ist.

### 🔑 Privates Repo: GitHub-Token hinterlegen

Der Server braucht Lesezugriff auf das Repo. Token erstellen unter [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new):
*Repository access* → nur dieses Repo, *Permissions* → **Contents: Read-only**.

Dann auf dem Server:
```bash
~/pi-dashboard/set-token.sh
```

Der Token wird geprüft und in `.git/github-credentials` gespeichert (nur für deinen Benutzer lesbar, wird nie committed). `install.sh` fragt automatisch danach, falls das Repo nicht lesbar ist. Bei abgelaufenem Token einfach `set-token.sh` erneut ausführen.

---

## 📄 Lizenz & Sicherheit

- Lizenz: [MIT](LICENSE) – frei nutzbar, veränderbar und weitergebbar, solange der Copyright-Hinweis erhalten bleibt.
- Mitgelieferte Fremdsoftware: xterm.js (MIT, `static/vendor/xterm`); FastMCP (Apache-2.0, als Python-Paket); gridstack.js (MIT, `static/vendor/gridstack`); App-Icons aus Dashboard Icons von homarr-labs (über jsDelivr, werden erst bei Bedarf geladen); Wetterdaten von Open-Meteo (CC BY 4.0); Länder-Datenbank von DB-IP (CC BY 4.0, wird erst bei Bedarf geladen); App-Katalog von LinuxServer.io (über deren öffentliche API).
- Sicherheitsmodell und wie man eine Lücke meldet: [SECURITY.md](SECURITY.md). Das Dashboard ist für das Heimnetz bzw. ein VPN gedacht, nicht für den direkten Betrieb im Internet.
