# ⚡ PiCore Server Dash - Raspberry Pi Server & Homelab Dashboard

Ein schlankes, hochmodernes **Server- & Homelab-Dashboard** für den Raspberry Pi – mit automatischem **GitHub Auto-Update**, Hardware-Monitoring, Docker & Diensten, Speicher-Explorer und System-Journal.

---

## 🌟 Features

- **GitHub Auto-Update System (Vollautomatisch):**
  - **systemd-Timer (jede Minute):** Prüft GitHub auf neue Commits. Sobald du einen Commit pushst, zieht der Pi das Update automatisch, aktualisiert bei Bedarf Abhängigkeiten und startet den Dienst neu – unabhängig von der App, also auch nach einem Absturz.
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
- **NVMe-Zustand:** Temperatur, verbleibende Lebensdauer, Reserve, geschriebene TB und Betriebsstunden (via `smartctl`; `install.sh` richtet dafür eine eng begrenzte sudo-Regel ein).
- **NVMe-Verlauf:** „Verlauf“ in der NVMe-Karte zeigt Verschleiß, pro Tag geschriebene Datenmenge und Temperatur (Tages-Ø und -Max.) über 30 Tage, 90 Tage oder 1 Jahr, dazu unsichere Abschaltungen, Medienfehler und eine hochgerechnete Restlebensdauer (ab 30 Tagen Daten). Ein Wert pro Tag, 3 Jahre aufbewahrt. Die NVMe-Temperatur gibt es zusätzlich im Verlauf-Diagramm (24 h / 7 Tage).
- **Container & Pods:** Docker-Container und Kubernetes-Pods (k3s / kubectl) mit Status, Neustarts und Fehler-Hervorhebung.
  - Docker braucht Zugriff auf den Socket: `sudo usermod -aG docker $USER`
  - k3s braucht eine lesbare kubeconfig: `mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config`
- **Stromversorgung (Pi 5):** Board-Leistung in Watt aus dem PMIC (`vcgencmd pmic_read_adc`), Eingangsspannung, vom Netzteil gemeldete Stromstärke, Unterspannung (live / seit Boot). Auch im Verlauf unter „Leistung“.
- **Datei-Explorer & Speicher-Analyse:** Über „Öffnen“ bei jedem Laufwerk: Ordner durchsuchen, Dateien herunterladen (nur lesen), Ordnergrößen berechnen und die größten Dateien finden. Sensible Pfade (Dashboard-Login, GitHub-Token, `~/.ssh`, `~/.kube` …) sowie `/proc`, `/sys`, `/dev` sind gesperrt; immer nur mit Login.
- **Web-Terminal:** Echte Shell im Browser (Terminal-Symbol oben rechts), läuft als dein Benutzer (`sudo` wie gewohnt). Öffnen erfordert das Passwort erneut (Einmal-Ticket, 60 s), nur von der Dashboard-Seite selbst, max. 3 gleichzeitig, schließt nach 30 Min. ohne Eingabe. xterm.js liegt im Repo (`static/vendor/xterm`, MIT) statt aus einem CDN.
- **Dienste steuern:** Klick auf eine Dienst-Kachel öffnet Start / Stopp / Neustart (mit Bestätigung) und das Journal des Dienstes. `install.sh` erlaubt per sudo genau diese Units – sonst nichts. Beim Auto-Updater: „Update jetzt ausführen“.
- **Container-Steuerung:** Docker-Container starten, stoppen, neu starten (mit Bestätigung) und Logs ansehen.
- **Wake-on-LAN:** PCs, NAS & Co. per Klick wecken; Geräte unter *Einstellungen → Wake-on-LAN* verwalten.
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

## 🍓 Erst-Installation auf dem Raspberry Pi

### 1. Repo auf GitHub anlegen & pushen (einmalig, auf dem Mac)

```bash
git add . && git commit -m "Initial commit"
git remote add origin https://github.com/DEIN-BENUTZERNAME/pi-dashboard.git
git push -u origin main
```

### 2. Auf den Pi deployen

**Option A – vom Mac aus:**
```bash
./deploy.sh pi@<IP-DEINES-PI>
```

**Option B – direkt auf dem Pi (per SSH):**
```bash
git clone https://github.com/DEIN-BENUTZERNAME/pi-dashboard.git ~/pi-dashboard
~/pi-dashboard/install.sh
```

`install.sh` richtet venv & Pakete, den Dienst `raspberry-dashboard`, den Auto-Update-Timer und die sudo-Regel für den Neustart ein. Es kann jederzeit erneut ausgeführt werden.

---

## 🔄 Auto-Update bei jedem Commit

Ab jetzt reicht auf dem Mac:

```bash
git commit -am "Neue Funktion" && git push
```

Der systemd-Timer `pi-dashboard-update.timer` prüft **jede Minute** GitHub. Bei einem neuen Commit:
1. installiert er geänderte `requirements.txt` (vor dem Code-Wechsel – schlägt pip fehl, läuft die alte Version weiter),
2. setzt den Code auf `origin/<branch>` (`git reset --hard` – lokale Änderungen auf dem Pi werden verworfen),
3. startet den Dienst neu (bei geänderten `.service`/`.timer`-Dateien, `install.sh` oder `services_manager.py` läuft `install.sh` erneut).

**Sicherheit:** Alles, was als root läuft (`update.sh`, `apt-run.sh`, `install.sh`, die Unit-Dateien und die sudo-Regeln), kommt aus einer **eigenen Kopie des Repos direkt von GitHub** unter `/var/lib/pi-dashboard/src` (gehört root). Der Dashboard-Ordner gehört deinem Benutzer – root führt daraus nie etwas aus. So kann niemand, der nur deinen Benutzer (oder die Dashboard-App) kontrolliert, über geänderte Skripte oder eine umgebogene `origin`-URL root werden. Ein neuer Token aus `set-token.sh` wird beim nächsten Durchlauf automatisch übernommen. Firewall und fail2ban laufen über `pi-helper.sh` aus dieser root-Kopie, das jedes Argument streng prüft – sudo erlaubt nur dieses eine Skript.

Der Timer läuft unabhängig vom Dashboard – auch wenn ein fehlerhafter Commit die App abstürzen lässt, wird der nächste Fix-Commit trotzdem gezogen.

```bash
journalctl -u pi-dashboard-update -f         # Update-Log live ansehen
sudo systemctl start pi-dashboard-update     # Sofort manuell aktualisieren
systemctl list-timers pi-dashboard-update    # Nächste Prüfung
```

### 🔐 Login

Das Dashboard ist komplett passwortgeschützt. Das Passwort wird **nur auf dem Pi** gesetzt (nicht über das Web):

```bash
~/pi-dashboard/set-password.sh                  # Benutzer & Passwort setzen (meldet alle Sitzungen ab)
~/pi-dashboard/set-password.sh public-view on   # Ansehen ohne Login (z.B. Wanddisplay) – Aktionen bleiben geschützt
~/pi-dashboard/set-password.sh public-view off
~/pi-dashboard/set-password.sh status
```

- Passwort als scrypt-Hash in `auth.json` (nur für deinen Benutzer lesbar, wird nie committed).
- Sitzung per signiertem Cookie (12 h, mit „Angemeldet bleiben“ 30 Tage) – Auto-Updates melden niemanden ab.
- Nach 5 Fehlversuchen wird die IP zunehmend lange gesperrt (30 s … 15 min).
- Aktionen (Updates, Neustart, Befehle …) werden nur von der eigenen Seite akzeptiert (CSRF-Schutz).
- Der GitHub-Webhook funktioniert ohne Login nur mit gesetztem `GITHUB_WEBHOOK_SECRET`.

### 🧰 System-Werkzeuge (`/system`, Schraubenschlüssel oben rechts)

- **Netzwerk:** Schnittstellen mit IPs, MAC, Traffic, Link-Speed und WLAN-Signal (SSID, dBm, Band); Gateway & DNS; offene Ports mit Dienst, Prozess und „nur lokal / im Netzwerk erreichbar“.
- **Firewall (ufw):** ein-/ausschalten, Regeln ansehen, hinzufügen (Erlauben / Blockieren / Begrenzen, Port oder Bereich, TCP/UDP, Quelle, Kommentar) und löschen. Beim Einschalten werden SSH und das Dashboard (Port 8000) immer zuerst freigegeben; die letzte Freigabe für SSH/Dashboard lässt sich nicht löschen. Vorschläge für k3s, NetBird (wt0) und Webserver, wenn diese laufen. ufw installieren: `sudo apt install ufw`.
- **SSH-Anmeldungen:** erfolgreiche und fehlgeschlagene Logins der letzten 7 Tage aus dem Journal (pro Verbindung), IPs mit den meisten Fehlversuchen (LAN / VPN / Internet), wer gerade angemeldet ist, und **fail2ban** mit Jails, gesperrten IPs und „Entsperren“. Für alle Logs: `sudo usermod -aG systemd-journal $USER`.
- **CPU-Modus:** Sparsam / Sanft / Ausgewogen / Leistung (cpufreq-Governor) mit Live-Takt; wird gespeichert und nach Neustart wieder gesetzt. sudo erlaubt nur das Schreiben der Governor-Datei.
- **Leistungstest:** Laufwerk (512-MB-Testdatei: sequentiell schreiben/lesen ohne Cache, 4K-IOPS) und CPU (SHA-256 auf 1 / allen Kernen). Ergebnisse mit Temperatur und CPU-Modus gespeichert zum Vergleichen.
- **Log-Suche:** System-Journal filtern nach Dienst oder Kernel, Schweregrad, Zeitraum und Suchbegriff (Suche über `journalctl -g`), Treffer hervorgehoben.
- **Speicher aufräumen:** System-Logs älter als 14 Tage, apt-Paket-Cache, ungenutzte Docker-Images & Build-Cache, `~/.cache` und Papierkorb – mit Größenvorschau, einzeln oder alles auf einmal. sudo nur für `journalctl --vacuum-time=14d` und `apt-get clean`.
- **Hardware:** Modell, SoC, RAM, Revision, Seriennummer, Kernel, Firmware, Bootloader mit Update-Hinweis, NVMe mit PCIe-Generation (Tipp für Gen 3), Laufwerke, USB-Geräte mit Geschwindigkeit.

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
- **System:** Hostname, Modell, OS, Kernel, Python, Laufzeit, IP-Adressen

Die Einstellungsseite braucht immer ein Login – auch wenn „Ansehen ohne Login“ aktiv ist.

### 🔑 Privates Repo: GitHub-Token hinterlegen

Der Pi braucht Lesezugriff auf das Repo. Token erstellen unter [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new):
*Repository access* → nur dieses Repo, *Permissions* → **Contents: Read-only**.

Dann auf dem Pi:
```bash
~/pi-dashboard/set-token.sh
```

Der Token wird geprüft und in `.git/github-credentials` gespeichert (nur für deinen Benutzer lesbar, wird nie committed). `install.sh` fragt automatisch danach, falls das Repo nicht lesbar ist. Bei abgelaufenem Token einfach `set-token.sh` erneut ausführen.
