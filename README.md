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
- **Laufwerke & Mount-Punkte:** wie `lsblk` – jedes Laufwerk mit seinen Partitionen (Füllstand, Dateisystem, Mount-Punkt), nicht eingehängte Partitionen, Swap (auch zram) und weitere Geräte (loop). Bind-Mounts von Kubernetes/Docker (z.B. `/var/lib/kubelet/pods/…`) werden unter ihrer Partition zusammengefasst statt als eigene Laufwerke angezeigt.
- **NVMe-Zustand:** Temperatur, verbleibende Lebensdauer, Reserve, geschriebene TB und Betriebsstunden (via `smartctl`; `install.sh` richtet dafür eine eng begrenzte sudo-Regel ein).
- **Laufwerk-Aktivität:** Lese-/Schreibrate je Laufwerk live in der Speicher-Karte und als „Laufwerk“ im Verlauf-Diagramm. Beim Netzwerk-Verlauf lässt sich eine Schnittstelle (LAN, WLAN, NetBird …) wählen.
- **Schnellaktionen:** eigene Skripte als Buttons. Ausführbare Dateien in `~/pi-dashboard-actions/` ablegen (`chmod +x`), optional mit Kopfzeilen `# name: …`, `# description: …`, `# confirm: yes`, `# timeout: 900`. Laufen als Dashboard-Benutzer, Ausgabe live im Fenster, Stoppen möglich; jeder Lauf steht im Aktivitätsprotokoll. Ein Beispiel-Skript wird beim ersten Mal angelegt.
- **Web-Dienste:** eigene Dienste im Heimnetz/VPN (Home Assistant, NAS …) werden alle 1/5/15 Min. per HTTP geprüft; Karte mit Status, Antwortzeit und 24-Std.-Leiste. Verwalten unter Einstellungen → Web-Dienste. Adressen im Internet werden abgelehnt.
- **Helles Design:** Einstellungen → Darstellung (Automatisch / Hell / Dunkel) oder das Sonnen-Symbol oben rechts.
- **Notizen:** Karte auf dem Dashboard, auf dem Pi gespeichert (alle Geräte sehen dasselbe), mit Checklisten (`- [ ]`), Überschriften und Links; speichert beim Tippen und überschreibt nie still eine Änderung von einem anderen Gerät. Nur nach Anmeldung sichtbar.
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

### 🔐 Login, Benutzer & Berechtigungen

Das Dashboard ist komplett passwortgeschützt. Den **ersten Administrator** legst du **nur auf dem Pi** an (nicht über das Web):

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

- **Eigene Gruppen** anlegen (Rechte von einer bestehenden übernehmen) und in einer Matrix ankreuzen – 18 Einzelrechte von „Logs lesen“ bis „Web-Terminal“. Änderungen gelten sofort, ohne neue Anmeldung. Eingebaute Gruppen lassen sich anpassen und auf Standard zurücksetzen.
- Pro Benutzer: Gruppe wechseln, Passwort setzen, auf allen Geräten abmelden, deaktivieren (sofort abgemeldet), löschen. Es bleibt immer mindestens ein aktiver Administrator; die eigenen Admin-Rechte kann man sich nicht selbst nehmen.
- Der Server prüft **jede** Anfrage gegen eine zentrale Rechte-Tabelle (`access.py`); neue ändernde Endpunkte sind ohne Eintrag nur für Administratoren. Die Oberfläche blendet nicht erlaubte Knöpfe und Bereiche aus.
- Passwörter als scrypt-Hash in `auth.json` (nur für deinen Benutzer lesbar, wird nie committed). Eine alte `auth.json` mit einem Benutzer wird automatisch übernommen – dieser wird Administrator.
- Benutzernamen ohne Groß-/Kleinschreibung („Niko“ = „niko“), Passwörter exakt.
- Sitzung per signiertem Cookie (12 h, mit „Angemeldet bleiben“ 30 Tage) – Auto-Updates melden niemanden ab. Neues Passwort oder „Abmelden“ beendet nur die Sitzungen dieses Benutzers.
- Nach 5 Fehlversuchen wird die IP zunehmend lange gesperrt (30 s … 15 min).
- Aktionen werden nur von der eigenen Seite akzeptiert (CSRF-Schutz). Der GitHub-Webhook funktioniert ohne Login nur mit gesetztem `GITHUB_WEBHOOK_SECRET`.

### 📱 Handy & Tablet

- Auf dem Handy: **Navigationsleiste unten** (Dashboard, System, Suche, Terminal, Einstellungen), kompakter Kopfbereich, der beim Scrollen verschwindet, und Dialoge als Bottom-Sheets.
- Alle Seiten passen ab 360 px Breite ohne seitliches Scrollen; Tab-Leisten scrollen seitlich, Tabellen zeigen auf kleinen Displays nur die wichtigen Spalten.
- Größere Tippflächen auf Touchscreens, keine Zoom-Sprünge beim Tippen in Eingabefelder (iOS).
- **Zum Home-Bildschirm hinzufügen** (Safari: Teilen → „Zum Home-Bildschirm“, Chrome: Menü → „App installieren“): eigenes Icon, startet im Vollbild wie eine App, berücksichtigt Notch & Home-Indikator.

### 🔎 Befehlspalette (Strg+K / ⌘K)

Auf jeder Seite öffnet **Strg+K** (Mac: **⌘K**) oder die Lupe oben rechts eine Suche über alle Seiten, Bereiche (z.B. „Firewall“, „SSH-Anmeldungen“, „Sprache“) und Aktionen („System-Updates öffnen“, „NVMe-Verlauf“, „Dateien durchsuchen“, „Prozess suchen“, Dienste steuern, Wake-on-LAN-Geräte wecken, Verlauf-Diagramme, Sprache, Abmelden). Pfeiltasten + Enter, tippfehlertolerant, zuletzt verwendete Befehle stehen oben. Im Web-Terminal gehört Strg+K der Shell – dort die Lupe benutzen.

### © Fußzeile

Unter jeder Seite (außer dem Terminal): Copyright, installierte Version (Commit + Datum), Link zum Quellcode sowie Hinweise zu Open-Meteo (Wetterdaten, CC BY 4.0), xterm.js (MIT) und den Schriften (SIL OFL). Der Name steht in `static/footer.js` (`OWNER`).

### 📋 Überblick

- **Statusleiste** oben im Dashboard: „Alles in Ordnung“ – oder genau, was Aufmerksamkeit braucht (Dienst mit Fehler, Web-Dienst nicht erreichbar, Laufwerk fast voll / bald voll, zu heiß, Unterspannung, NVMe-Verschleiß, Sicherheitsupdates, Dashboard-Update). Ein Klick springt an die richtige Stelle.
- **Ereignisse:** Zeitleiste (Karte auf dem Dashboard, komplett unter System-Werkzeuge → Ereignisse) mit Ausfällen und Erholung von Diensten, Drosselung, fehlgeschlagenen Anmeldungen (Dashboard + SSH), Updates, Neustarts und neuen Geräten.
- **Speicherprognose:** „voll in ~N Tagen“ aus dem täglichen Füllstand, dazu die am stärksten gewachsenen Ordner (7 / 30 Tage). Ordnergrößen misst der Pi einmal täglich mit niedrigster Priorität (`du`).
- **Wochenbericht** (`/report`): die letzten 7 Tage auf einer Seite – Kennzahlen, Verfügbarkeit, Drosselung, Speicher, Top-Programme, Änderungen, neue Geräte. „Drucken / PDF“ erzeugt eine saubere Druckansicht.

### 📁 Dateien

Im Datei-Browser (Laufwerk → „Öffnen“): **Hochladen** per Button oder Drag & Drop (nur wo dein Benutzer schreiben darf, max. 4 GB, bestehende Dateien werden nur nach Rückfrage überschrieben), **Vorschau** für Text/Logs/Configs (mit Suche) und Bilder, **Teilen** als Download-Link für 1 Std., 24 Std. oder 7 Tage – funktioniert ohne Anmeldung für alle, die den Pi erreichen. Aktive Links unter Einstellungen → Freigaben. Gesperrte Dateien (Login, Tokens, SSH-Schlüssel, Aktivitätsprotokoll) lassen sich weder ansehen noch teilen.

**Bearbeiten & Papierkorb:** Textdateien (bis 2 MB, UTF-8) öffnen sich per Stift-Symbol oder „Bearbeiten“ in der Vorschau in einem Editor mit Zeilennummern und Farben für YAML, JSON, INI/`.env`, Shell, Python, Dockerfile, XML, CSS und Markdown (Strg+S speichert, Tab rückt ein, Enter übernimmt die Einrückung). Gespeichert wird atomar (Rechte bleiben erhalten); hat jemand die Datei inzwischen geändert, fragt der Editor nach. JSON wird vor dem Speichern geprüft, Compose-Dateien mit `docker compose config`. „+ Datei“ / „+ Ordner“ legen Neues an. **Löschen** (Papierkorb-Symbol, zweimal klicken) verschiebt in den **Papierkorb** auf demselben Laufwerk (`.picore-trash`, im Home unter `~/.local/share/.picore-trash`) – wiederherstellbar für 30 Tage, danach automatisch weg. Das Dashboard selbst, das Home-Verzeichnis und Laufwerks-Wurzeln lassen sich nicht löschen. Braucht das Recht „Dateien ändern“.

### 🎨 Personalisieren

Akzentfarbe (Cyan, Grün, Violett, Orange, Pink, Blau) unter Einstellungen → Darstellung; Kartenbreite im Anpassen-Modus (Messwert-Karten breit, Verlauf/Container halb); eigene Links (Home Assistant, Router …) unter Einstellungen → Eigene Links – erscheinen oben unter dem Link-Symbol und in der Suche.

### 🧩 Dashboard anpassen

Das Raster-Symbol oben rechts startet den Bearbeiten-Modus: Karten per Pfeil oder Ziehen umsortieren (innerhalb ihres Bereichs – obere Reihe, Messwerte, untere Reihe – und ganze Bereiche wie „Verlauf“ nach oben/unten) und per Auge ausblenden. Gespeichert pro Browser, „Zurücksetzen“ stellt das Standard-Layout wieder her.

### 🧰 System-Werkzeuge (`/system`, Schraubenschlüssel oben rechts)

- **Netzwerk:** Schnittstellen mit IPs, MAC, Traffic, Link-Speed und WLAN-Signal (SSID, dBm, Band); Gateway & DNS; offene Ports mit Dienst, Prozess und „nur lokal / im Netzwerk erreichbar“.
- **Sensoren:** alle Temperaturen (CPU, RP1-Chip, PMIC, NVMe …), Spannungen und Ströme der Pi-5-Stromschienen, Lüfterdrehzahl und Unterspannungs-Alarm – live, mit 24-Stunden-Kurven.
- **Ressourcen-Verbrauch:** welche Programme über 24 Std. / 7 Tage am meisten CPU und RAM gebraucht haben (pro Programm zusammengefasst, bei Python/Node mit Skriptname), mit Verlauf je Programm – findet z.B. nächtliche Last.
- **Datenverbrauch:** empfangen/gesendet heute, pro Tag (30 Tage) und pro Monat, je Schnittstelle wählbar. 2 Jahre gespeichert.
- **DNS & Traceroute:** DNS-Abfragen (A, AAAA, CNAME, MX, TXT, NS, PTR) gegen System-DNS, Cloudflare, Google, Quad9 oder einen eigenen Server; Traceroute mit Namen und Zeiten (`sudo apt install traceroute`, falls nicht vorhanden).
- **Geräte im Netzwerk:** scannt das Heimnetz (Ping-Sweep + Nachbartabelle, ohne Root), zeigt IP, MAC, Hersteller und Router-Namen, merkt sich Geräte (auch offline), markiert neue, Umbenennen, „Zu Wake-on-LAN“ mit einem Klick. Hersteller-Liste für alle Geräte: `sudo apt install ieee-data`.
- **Port-Check:** jeder offene Port mit Erreichbarkeit aus LAN und VPN (aus Bindung + Firewall-Regeln berechnet, Docker-Ports markiert) und ein TCP-Test zu beliebigen Hosts/Ports.
- **Verfügbarkeit:** Uptime in % je Dienst über 24 Std. / 7 / 30 Tage mit Zeitleiste und Liste der Ausfälle (auch im Dienst-Fenster auf dem Dashboard).
- **Drosselung:** Protokoll von Unterspannung, Hitze-Drosselung, Taktbegrenzung und Temperaturgrenze – inkl. kurzer Ereignisse zwischen zwei Messungen (90 Tage).
- **Lüfterkurve:** vier Stufen (Temperatur + Drehzahl) mit Vorlagen Standard / Leise / Kühl und Live-Vorschau; wird mit Backup in `config.txt` geschrieben und gilt nach dem Neustart.
- **Aktivität:** wer wann was gemacht hat (Anmeldungen inkl. Fehlversuche, Updates, Dienste, Firewall, Prozesse, Downloads, Terminal …), durchsuchbar, 180 Tage.
- **Firewall (ufw):** ein-/ausschalten, Regeln ansehen, hinzufügen und löschen. Regeln mit Aktion (Erlauben / Blockieren / Ablehnen / Begrenzen), Richtung (ein-/ausgehend), Schnittstelle, Protokoll, **Quelle + Quellport** und **Ziel + Zielport** (einzeln, Bereich `6000:6010` oder Liste `80,443`), Position (am Ende oder ganz oben mit Vorrang), optional „Treffer protokollieren“ und Kommentar – mit Live-Vorschau des ufw-Befehls. Schutz gegen Aussperren: keine Sperre der eigenen IP fürs Dashboard, von SSH/Dashboard für alle oder von allem ausgehenden Verkehr; beim Einschalten werden SSH und das Dashboard (Port 8000) immer zuerst freigegeben, die letzte Freigabe lässt sich nicht löschen. Vorschläge für k3s, NetBird (wt0) und Webserver, wenn diese laufen. ufw installieren: `sudo apt install ufw`.
- **Firewall-Traffic:** was die Firewall blockiert hat (aus den `[UFW BLOCK]`-Kernelmeldungen im Journal, alle Boots; Fallback `/var/log/ufw.log`) für 1 Std. / 24 Std. / 7 Tage: Kennzahlen, Diagramm Internet vs. LAN/VPN, häufigste Absender (mit Gerätenamen aus dem LAN-Scan, **„IP sperren“** mit einem Klick), häufigste Ziel-Ports (markiert, wenn auf dem Port ein Programm lauscht; **„Freigeben…“** füllt das Regel-Formular vor) und die letzten Pakete. Filter nach Blockiert/Erlaubt, Richtung, IP, Schnittstelle oder Port. Ist die Protokollierung aus, lässt sie sich direkt einschalten (Stufe „low“). Braucht Lesezugriff aufs Journal (`systemd-journal`-Gruppe, wie bei den SSH-Anmeldungen).
- **Länder (GeoIP):** nach einmaligem Laden der kostenlosen Länder-Datenbank von [DB-IP](https://db-ip.com) (CC BY 4.0, ~5 MB Download, lokal in `geoip.db`, monatlich automatisch aktualisiert) stehen Flagge und Land bei allen Internet-IPs: Firewall-Traffic (plus Übersicht „Herkunftsländer“), SSH-Anmeldungen, fail2ban-Sperren und Aktivität. Alles offline, keine IP verlässt den Pi.
- **Länder sperren:** Länder suchen oder aus den Vorschlägen im Firewall-Traffic übernehmen → „Übernehmen“. Die Netze landen in `ipset`-Listen, die eine eigene Regel in ufws `before.rules` vor allen anderen Regeln verwirft (Treffer erscheinen im Firewall-Traffic). Antworten auf eigene Verbindungen, LAN und VPN bleiben unberührt; das Land, aus dem du gerade verbunden bist, wird abgelehnt. `pi-dashboard-geoblock.service` lädt die Listen beim Booten vor ufw; nach jedem Datenbank-Update werden sie erneuert. `ipset` installiert `install.sh` automatisch.
- **SSH-Anmeldungen:** erfolgreiche und fehlgeschlagene Logins der letzten 7 Tage aus dem Journal (pro Verbindung), IPs mit den meisten Fehlversuchen (LAN / VPN / Internet), wer gerade angemeldet ist, und **fail2ban** mit Jails, gesperrten IPs und „Entsperren“. Für alle Logs: `sudo usermod -aG systemd-journal $USER`.
- **CPU-Modus:** Sparsam / Sanft / Ausgewogen / Leistung (cpufreq-Governor) mit Live-Takt; wird gespeichert und nach Neustart wieder gesetzt. sudo erlaubt nur das Schreiben der Governor-Datei.
- **Leistungstest:** Laufwerk (512-MB-Testdatei: sequentiell schreiben/lesen ohne Cache, 4K-IOPS) und CPU (SHA-256 auf 1 / allen Kernen). Ergebnisse mit Temperatur und CPU-Modus gespeichert zum Vergleichen.
- **Log-Suche:** System-Journal filtern nach Dienst oder Kernel, Schweregrad, Zeitraum und Suchbegriff (Suche über `journalctl -g`), Treffer hervorgehoben.
- **Speicher aufräumen:** System-Logs älter als 14 Tage, apt-Paket-Cache, ungenutzte Docker-Images & Build-Cache, `~/.cache` und Papierkorb – mit Größenvorschau, einzeln oder alles auf einmal. sudo nur für `journalctl --vacuum-time=14d` und `apt-get clean`.
- **Hardware:** Modell, SoC, RAM, Revision, Seriennummer, Kernel, Firmware, Bootloader mit Update-Hinweis, NVMe mit PCIe-Generation (Tipp für Gen 3), Laufwerke, USB-Geräte mit Geschwindigkeit.

### 🐳 Container (`/containers`, Würfel oben rechts)

- **Übersicht:** laufende Pods, bereite Workloads, Docker-Container, Stacks, verfügbare Image-Updates und Kubernetes-Warnungen der letzten 24 Std. – plus eine Liste mit allem, was gerade kaputt ist (CrashLoop, OOMKilled, beendete Container …).
- **Kubernetes (k3s):** Workloads (Deployments, StatefulSets, DaemonSets) mit Bereit-Status, Images und Aktionen **Neu starten** (`rollout restart`), **Stoppen** (auf 0 skalieren – „Starten“ holt die alte Anzahl zurück) und **Skalieren**; Pods mit Status, Neustarts, OOMKilled, CPU/RAM (metrics-server), **Logs** (Container-Auswahl, vorheriger Start, Filter, Live) und **Neu erstellen**; Services mit Ports/NodePorts/LoadBalancer-IP; Nodes mit Auslastung und Version; Warnungen (Events). Filter nach Namespace und Text, kube-system ausblendbar. Braucht eine lesbare kubeconfig (`~/.kube/config`, siehe Hinweis auf der Seite).
- **Docker:** alle Container mit Status/Health, CPU (inkl. 24-Std.-Sparkline), RAM, Ports; Starten/Stoppen/Neu starten, Logs, Verlauf.
- **Compose-Stacks:** aus `docker compose ls` und den Container-Labels; **Starten, Neu starten, Aktualisieren** (`pull` + `up -d`), **Stoppen, Entfernen** (`down`, Volumes bleiben) mit Live-Ausgabe; **Compose-Datei** öffnet den Editor – beim Speichern prüft `docker compose config` die Datei, kaputte YAML wird nicht gespeichert.
- **Image-Updates:** alle 12 Std. (oder per „Jetzt prüfen“) wird der Digest jedes laufenden Images (Docker + k3s) mit der Registry verglichen – Docker Hub, GHCR, Quay, lscr.io & Co., anonym per `HEAD` (zählt nicht gegen das Docker-Hub-Limit). Bei Updates: Stack direkt aktualisieren oder Deployment neu starten. Nach einem Stack-Update wird automatisch neu geprüft.
- **Statistik:** CPU, RAM und Netzwerk pro Container (Pods eines Deployments zusammengefasst) für 24 Std. / 7 Tage, einmal pro Minute gemessen (`docker stats`, `kubectl top`), 7 Tage aufbewahrt.

Ansehen braucht „System ansehen“, Logs „Logs lesen“, alle Aktionen „Dienste steuern“, das Bearbeiten der Compose-Datei „Dateien ändern“.

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
