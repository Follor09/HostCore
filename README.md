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
- **Helles Design:** Einstellungen → Darstellung (Automatisch / Hell / Dunkel) oder „Design-Schema umschalten“ in der Seitenleiste.
- **Notizen:** Karte auf dem Dashboard, auf dem Pi gespeichert (alle Geräte sehen dasselbe), mit Checklisten (`- [ ]`), Überschriften und Links; speichert beim Tippen und überschreibt nie still eine Änderung von einem anderen Gerät. Nur nach Anmeldung sichtbar.
- **NVMe-Verlauf:** „Verlauf“ in der NVMe-Karte zeigt Verschleiß, pro Tag geschriebene Datenmenge und Temperatur (Tages-Ø und -Max.) über 30 Tage, 90 Tage oder 1 Jahr, dazu unsichere Abschaltungen, Medienfehler und eine hochgerechnete Restlebensdauer (ab 30 Tagen Daten). Ein Wert pro Tag, 3 Jahre aufbewahrt. Die NVMe-Temperatur gibt es zusätzlich im Verlauf-Diagramm (24 h / 7 Tage).
- **Container & Pods:** Docker-Container und Kubernetes-Pods (k3s / kubectl) mit Status, Neustarts und Fehler-Hervorhebung.
  - Docker braucht Zugriff auf den Socket: `sudo usermod -aG docker $USER`
  - k3s braucht eine lesbare kubeconfig: `mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config`
- **Stromversorgung (Pi 5):** Board-Leistung in Watt aus dem PMIC (`vcgencmd pmic_read_adc`), Eingangsspannung, vom Netzteil gemeldete Stromstärke, Unterspannung (live / seit Boot). Auch im Verlauf unter „Leistung“.
- **Datei-Explorer & Speicher-Analyse:** Über „Öffnen“ bei jedem Laufwerk: Ordner durchsuchen, Dateien herunterladen (nur lesen), Ordnergrößen berechnen und die größten Dateien finden. Sensible Pfade (Dashboard-Login, GitHub-Token, `~/.ssh`, `~/.kube` …) sowie `/proc`, `/sys`, `/dev` sind gesperrt; immer nur mit Login.
- **Web-Terminal:** Echte Shell im Browser („Terminal“ in der Seitenleiste), läuft als dein Benutzer (`sudo` wie gewohnt). Öffnen erfordert das Passwort erneut (Einmal-Ticket, 60 s), nur von der Dashboard-Seite selbst, max. 3 gleichzeitig, schließt nach 30 Min. ohne Eingabe. xterm.js liegt im Repo (`static/vendor/xterm`, MIT) statt aus einem CDN.
- **Dienste steuern:** Klick auf eine Dienst-Kachel öffnet Start / Stopp / Neustart (mit Bestätigung) und das Journal des Dienstes. `install.sh` erlaubt per sudo genau diese Units – sonst nichts. Beim Auto-Updater: „Update jetzt ausführen“.
- **Container-Steuerung:** Docker-Container starten, stoppen, neu starten (mit Bestätigung) und Logs ansehen.
- **Wake-on-LAN:** PCs, NAS & Co. per Klick wecken; Geräte unter *Einstellungen → Wake-on-LAN* verwalten.
- **NetBox:** Unter *Einstellungen → NetBox* Adresse und API-Token eintragen (NetBox 3.x/4.x, auch `nbt_`-Tokens ab 4.5). *System-Werkzeuge → NetBox* zeigt Präfixe mit Auslastung, IP-Adressen, Geräte und VMs und gleicht sie mit dem Geräte-Scan ab: nicht dokumentierte Geräte, abweichende IP oder MAC, reservierte Adressen in Benutzung, dokumentierte Adressen ohne Antwort. Dazu Adressen, die in NetBox *und* im Netz frei sind (ohne IP-Bereiche wie den DHCP-Pool). Mit der Berechtigung „NetBox bearbeiten“ und einem Token mit Schreibrechten lassen sich Geräte und freie Adressen direkt in NetBox eintragen. Der Token bleibt auf dem Pi.
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

## 🍓 Installation auf dem Raspberry Pi

**Voraussetzungen:** Raspberry Pi (am besten Pi 5, Pi 4 geht auch) mit Raspberry Pi OS oder Debian (64-bit, Bookworm oder neuer), ein Benutzer mit `sudo`. Docker und k3s sind optional – die Container-Bereiche erscheinen, sobald sie installiert sind.

### Schnellstart – direkt auf dem Pi (per SSH)

```bash
git clone https://github.com/Follor09/RaspberryPi-Dash.git ~/pi-dashboard
~/pi-dashboard/install.sh
```

`install.sh` richtet venv & Pakete, den Dienst `raspberry-dashboard`, den Auto-Update-Timer und die eng begrenzten sudo-Regeln ein und fragt am Ende nach dem ersten Administrator. Danach ist das Dashboard unter `http://<IP-des-Pi>:8000` erreichbar. Das Skript kann jederzeit erneut ausgeführt werden.

> **Hinweis zu Updates:** Der Pi holt sich automatisch jeden neuen Stand des Branches, von dem er geklont wurde (siehe [Auto-Update](#-auto-update-bei-jedem-commit)). Wer Änderungen lieber selbst prüft, nutzt einen eigenen Fork – oder schaltet den Timer ab: `sudo systemctl disable --now pi-dashboard-update.timer`.

### Eigene Version (Fork) mit Auto-Update

1. Repository auf GitHub **forken** und den Fork auf den Rechner klonen.
2. Auf den Pi bringen – entweder vom Rechner aus:
   ```bash
   ./deploy.sh pi@<IP-DEINES-PI>
   ```
   oder direkt auf dem Pi:
   ```bash
   git clone https://github.com/<DEIN-BENUTZERNAME>/RaspberryPi-Dash.git ~/pi-dashboard
   ~/pi-dashboard/install.sh
   ```
3. Ab dann landet jeder Push auf deinen Fork innerhalb einer Minute auf dem Pi. Ist der Fork privat, braucht der Pi einen Lese-Token (siehe [Privates Repo](#-privates-repo-github-token-hinterlegen)).

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

- **Eigene Gruppen** anlegen (Rechte von einer bestehenden übernehmen) und in einer Matrix ankreuzen – 21 Einzelrechte von „Logs lesen“ bis „Web-Terminal“. Änderungen gelten sofort, ohne neue Anmeldung. Eingebaute Gruppen lassen sich anpassen und auf Standard zurücksetzen.
- Pro Benutzer: Gruppe wechseln, Passwort setzen, auf allen Geräten abmelden, deaktivieren (sofort abgemeldet), löschen. Es bleibt immer mindestens ein aktiver Administrator; die eigenen Admin-Rechte kann man sich nicht selbst nehmen.
- Der Server prüft **jede** Anfrage gegen eine zentrale Rechte-Tabelle (`access.py`); neue ändernde Endpunkte sind ohne Eintrag nur für Administratoren. Die Oberfläche blendet nicht erlaubte Knöpfe und Bereiche aus.
- Passwörter als scrypt-Hash in `auth.json` (nur für deinen Benutzer lesbar, wird nie committed). Eine alte `auth.json` mit einem Benutzer wird automatisch übernommen – dieser wird Administrator.
- Benutzernamen ohne Groß-/Kleinschreibung („Anna“ = „anna“), Passwörter exakt.
- Sitzung per signiertem Cookie (12 h, mit „Angemeldet bleiben“ 30 Tage) – Auto-Updates melden niemanden ab. Neues Passwort oder „Abmelden“ beendet nur die Sitzungen dieses Benutzers.
- Nach 5 Fehlversuchen wird die IP zunehmend lange gesperrt (30 s … 15 min).
- Aktionen werden nur von der eigenen Seite akzeptiert (CSRF-Schutz). Der GitHub-Webhook funktioniert ohne Login nur mit gesetztem `GITHUB_WEBHOOK_SECRET`.

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
- **Speicherprognose:** „voll in ~N Tagen“ aus dem täglichen Füllstand, dazu die am stärksten gewachsenen Ordner (7 / 30 Tage). Ordnergrößen misst der Pi einmal täglich mit niedrigster Priorität (`du`).
- **Backups** (System-Werkzeuge): restic- und Borg-Repositorys (lokal, SFTP, REST, S3/B2 über Umgebungsvariablen) sowie Backup-Ordner (neueste Datei, optional nur `*.sql.gz` o. ä.) werden stündlich geprüft – letztes Backup, Anzahl, Größe. Ist das letzte älter als das eingestellte Höchstalter oder scheitert die Prüfung, gibt es ein Ereignis, einen Eintrag in der Statusübersicht und eine E-Mail. Das Repository muss für den Dashboard-Benutzer lesbar sein; Passwörter bleiben in `settings.json`.
- **Wochenbericht** (`/report`): die letzten 7 Tage auf einer Seite – Kennzahlen, Verfügbarkeit, Drosselung, Speicher, Top-Programme, Änderungen, neue Geräte. „Drucken / PDF“ erzeugt eine saubere Druckansicht.

### 🗂️ Planung & Auswertung

- **Verlauf abspielen** („Verlauf abspielen“ in der Seitenleiste, „Abspielen“ im Verlauf oder Befehlspalette): gibt die letzten 24 Stunden oder einen der letzten 7 Tage minutengenau wieder. CPU, Temperatur, RAM, Netzwerk, Lüfter, Leistung und der 3D-Pi zeigen die Werte der jeweiligen Minute (mit Sparklines davor), alle anderen Karten werden ausgegraut. Zeitregler, Wiedergabe/Pause (Leertaste), ±15 Min. (Pfeiltasten, mit Umschalt ±1 Std.), Tempo von 1 Min./s bis 1 Std./s. Ereignisse des Tages (Ausfälle, Drosselung, Anmeldungen …) sind farbige Marken auf dem Regler; Zeiten ohne Messwerte sind schraffiert. Esc kehrt zu den Live-Werten zurück.
- **Erinnerungen:** wiederkehrende Aufgaben („monatlich: Backup prüfen“) – einmalig, täglich, wöchentlich, monatlich oder jährlich, auch mit Intervall („alle 3 Monate“). Fällige stehen auf der Karte und in der Statusleiste, bis sie abgehakt sind; abhaken setzt wiederkehrende auf den nächsten Termin (der 31. bleibt in kurzen Monaten der letzte Tag), „Morgen“ verschiebt um einen Tag. Für alle Geräte gleich, Bearbeiten mit dem Recht „Notizen bearbeiten“.
- **Aufgaben (Kanban):** Board mit *Offen*, *In Arbeit* und *Erledigt*. Karten per Drag & Drop oder Pfeil-Schaltflächen (auch auf Touch-Geräten) verschieben, mit Beschreibung, Labels und Fälligkeitsdatum; überfällige und heute fällige Karten sind markiert. Die Dashboard-Karte zeigt Zähler und anstehende Aufgaben und nimmt neue per Enter an.
- **Live-3D-Pi:** Karte mit einem drehbaren 3D-Modell des Pi 5 (reines CSS, keine Bibliothek). Der Kühler glüht je nach CPU-Temperatur von Blau bis Rot, der Lüfter dreht mit der echten Lüfterstufe, die grüne LED blinkt schneller bei CPU-Last, die Ethernet-LEDs flackern mit dem Netzwerkverkehr, die rote LED blinkt bei Unterspannung. Ziehen oder Pfeiltasten drehen, Doppelklick setzt die Ansicht zurück.
- **Erweiterte Prüfungen** (Einstellungen → Web-Dienste): neben Webseiten auch „Webseite enthält Text“ (oder darf ihn nicht enthalten), JSON-Wert (Pfad wie `data.0.state`, optional mit erwartetem Wert), TCP-Port, Ping und DNS-Server (Name abfragen, optional mit erwarteter Antwort) – alles nur im Heimnetz/VPN. Ein Klick auf einen Dienst in der Web-Dienste-Karte zeigt Antwortzeiten (24 Std., 7 oder 30 Tage) mit Ausfällen im Diagramm, Kennzahlen und die Liste der Vorfälle mit Dauer und Fehler; Vorfälle während einer Wartung sind markiert. Prüfungen lassen sich bearbeiten.
- **Geräte-Verfügbarkeit:** in System-Werkzeuge → Geräte im Netzwerk „Überwachen“ – das Gerät wird alle 5 Minuten angepingt, über seine MAC-Adresse (auch nach einem IP-Wechsel per DHCP). Status und Verfügbarkeit stehen in der Geräteliste, Verlauf und Vorfälle in der Web-Dienste-Karte.
- **Öffentliche Statusseite** (`/status`): eine schlichte Seite ohne Anmeldung für Familie und Freunde. Unter Einstellungen → Statusseite einschalten, Titel setzen und Dienste, Web-Dienste und Compose-Stacks auswählen – jeweils mit eigenem Anzeigenamen. Zeigt „Alle Systeme laufen“ / „Teilweise Störung“, den Zustand jedes Eintrags und optional die Verfügbarkeit der letzten 30 Tage als Balken. Es verlassen nur diese Namen und Zustände den Pi – keine Adressen, IPs oder Fehlermeldungen. Ausgeschaltet antwortet `/api/public/status` mit 404.

### 📚 Dokumentation (`/doku`)

- **Inventar:** Hardware mit Kategorie, Hersteller, Modell, Seriennummer, Standort, Kaufdatum, Preis und Garantie. Optional mit einem Gerät aus dem Netzwerk-Scan verknüpft – dann steht dabei, ob es gerade online ist und unter welcher IP. Gesamtwert oben; endet eine Garantie in den nächsten 30 Tagen, erscheint ein Hinweis in der Statusleiste.
- **Betriebstagebuch:** eigene Einträge („Was wurde wann geändert und warum“) mit Kategorie (Änderung, Wartung, Störung, Installation, Notiz), Zeitpunkt und Bezug. Durchsuchbar; Einträge erscheinen in den Ereignissen und im Wochenbericht.
- **Runbooks:** Schritt-für-Schritt-Anleitungen (ein Schritt pro Zeile). Ein Durchlauf wird als Checkliste abgehakt – wer welchen Schritt wann erledigt hat, wird gespeichert, auch über mehrere Geräte. Beim Abschluss landet ein Eintrag im Betriebstagebuch (mit übersprungenen Schritten und Notiz); die letzten 30 Durchläufe bleiben sichtbar.
- **Verträge & Abos:** Domains, Server, Lizenzen, Abos mit Anbieter, Kosten, Zahlungsrhythmus, Laufzeitende, Kündigungsfrist und Link zum Kundenkonto. Das Dashboard rechnet aus, bis wann gekündigt werden muss (bei automatisch verlängernden Verträgen für die nächste Verlängerung), markiert Fristen in den nächsten 30 Tagen und verpasste Fristen und zeigt die Kosten pro Monat und Jahr. Fristen und Verlängerungen erscheinen im Kalender-Widget.
- **Kosten:** Strom (vom Pi gemessen, hochgerechnet), Verträge & Abos und Hardware-Käufe aus dem Inventar – pro Monat und Jahr, Hardware zusätzlich auf drei Jahre verteilt, dazu ein Diagramm der letzten 12 Monate.
- **Anhänge:** PDFs, Bilder, Text- und ZIP-Dateien (bis 25 MB) an Inventar, Verträge, Steckbriefe und Wiki-Seiten hängen – Rechnungen, Handbücher, Fotos. Gespeichert unter `attachments/` auf dem Pi; Bilder und PDFs öffnen sich in einer abgeschotteten Vorschau.
- **Domains:** eigene Domains mit Ablaufdatum, Registrar, Nameservern und DNSSEC – einmal täglich per RDAP (Nachfolger von WHOIS) abgefragt, auch für .de, .ch und .io. Registries ohne öffentliches Ablaufdatum (z. B. DENIC) bekommen das Verlängerungsdatum von Hand. Vor Ablauf (Vorwarnzeit pro Domain) erscheint eine Warnung in der Statusübersicht und in der täglichen E-Mail.
- **Befehle:** oft gebrauchte Befehle mit Titel, Beschreibung und Tags – mit einem Klick kopiert; im Web-Terminal fügt der Knopf „Befehle“ sie an der Eingabe ein (ohne sie abzuschicken).
- **Steckbriefe:** pro Dienst, Web-Dienst oder Compose-Stack (oder als eigener Eintrag) Zweck, Adresse, Ports, Speicherort der Daten, Sicherung, Doku-Link, Zuständige und Notizen. Auf der Container-Seite öffnet „Steckbrief“ den passenden Eintrag oder legt ihn an.

Ansehen darf jeder angemeldete Benutzer, Ändern braucht „Notizen & Doku bearbeiten“. Gespeichert in `settings.json`, für alle Geräte gleich.

### 📖 Wissensdatenbank (`/wissen`)

Freie Markdown-Seiten in Ordnern für alles, was nicht in Runbooks oder Steckbriefe passt: Überschriften, Listen, Aufgaben, Tabellen, Code-Blöcke, Zitate; `[[Seitentitel]]` verlinkt auf eine andere Seite. Editor mit Vorschau (Strg+S speichert), Anhänge pro Seite, **Verlauf** mit den letzten 20 Versionen (ansehen, wiederherstellen; Anzahl unter Datenaufbewahrung einstellbar) und Schutz vor gleichzeitigem Überschreiben. **Vorlagen** für neue Seiten: Gerät, Störungsbericht, Anleitung, Dienst, Entscheidung, Netzwerk – dazu jede eigene Seite im Ordner „Vorlagen“; `{{titel}}`, `{{datum}}` und `{{benutzer}}` werden beim Anlegen ausgefüllt. Die Seiten liegen in `wiki.json` und sind über die Volltextsuche (Strg+K) und den MCP erreichbar.

### 🛠️ Wartungsfenster & Gastzugänge

- **Wartungsfenster** (Einstellungen → Wartungsfenster): geplante Arbeiten für alle oder ausgewählte Dienste, Web-Dienste und Stacks – mit Beginn, Ende und Notiz, oder „Jetzt starten (1 Std.)“. Während eines Fensters zählen Ausfälle der betroffenen Dienste nicht gegen die Verfügbarkeit (Verfügbarkeit, Wochenbericht, Statusseite), Ausfall-Ereignisse werden als Hinweis statt Warnung geführt, und die Statusleiste zeigt nur „Wartung: … (bis …)“. Die öffentliche Statusseite zeigt „Wartungsarbeiten“ und kündigt geplante Fenster der nächsten 14 Tage an. Fenster lassen sich nicht rückwirkend anlegen, laufende vorzeitig beenden.
- **Gastzugänge** (Einstellungen → Benutzer → „Zugang“): Konten mit Ablauf nach 1, 7 oder 30 Tagen oder bis zu einem Datum (höchstens 365 Tage). Danach ist keine Anmeldung mehr möglich und laufende Sitzungen enden; „+7 Tage“ verlängert. Gäste können keine Administratoren sein.

### 📁 Dateien

Im Datei-Browser (Laufwerk → „Öffnen“): **Hochladen** per Button oder Drag & Drop (nur wo dein Benutzer schreiben darf, max. 4 GB, bestehende Dateien werden nur nach Rückfrage überschrieben), **Vorschau** für Text/Logs/Configs (mit Suche) und Bilder, **Teilen** als Download-Link für 1 Std., 24 Std. oder 7 Tage – funktioniert ohne Anmeldung für alle, die den Pi erreichen. Aktive Links unter Einstellungen → Freigaben.

**Webhooks** (Einstellungen → Webhooks): eigene Adressen `/hook/<token>`, an die Uptime Kuma, Watchtower/Shoutrrr, GitHub oder Skripte (`curl -d "Backup fertig" …`) Meldungen schicken. Sie erscheinen in den Ereignissen und im Wochenbericht; die Stufe wird aus der Meldung erkannt (z.B. Uptime-Kuma „down“ = kritisch) oder fest vorgegeben. Höchstens 60 Meldungen pro Minute und Webhook.

**Austausch-Links** (Einstellungen → Freigaben): ein öffentlicher Link für einen ganzen Ordner – **nur hochladen** (z.B. Logs oder Fotos von anderen einsammeln), **nur herunterladen** (alle Dateien direkt im Ordner) oder **beides**. Gültig 1 Std. bis 30 Tage, mit Größenlimit für Uploads und optionalem Hinweis für Besucher. Die Seite `/x/…` braucht keine Anmeldung; hochgeladene Dateien überschreiben nie etwas („Name (2)“), Dateinamen werden bereinigt, Unterordner und versteckte Dateien werden nie gezeigt. Jeder Up- und Download steht im Aktivitätsprotokoll. Erstellen braucht „Dateien teilen“, Upload-Links zusätzlich „Dateien hochladen“. Gesperrte Dateien (Login, Tokens, SSH-Schlüssel, Aktivitätsprotokoll) lassen sich weder ansehen noch teilen.

**Bearbeiten & Papierkorb:** Textdateien (bis 2 MB, UTF-8) öffnen sich per Stift-Symbol oder „Bearbeiten“ in der Vorschau in einem Editor mit Zeilennummern und Farben für YAML, JSON, INI/`.env`, Shell, Python, Dockerfile, XML, CSS und Markdown (Strg+S speichert, Tab rückt ein, Enter übernimmt die Einrückung). Gespeichert wird atomar (Rechte bleiben erhalten); hat jemand die Datei inzwischen geändert, fragt der Editor nach. JSON wird vor dem Speichern geprüft, Compose-Dateien mit `docker compose config`. „+ Datei“ / „+ Ordner“ legen Neues an. **Löschen** (Papierkorb-Symbol, zweimal klicken) verschiebt in den **Papierkorb** auf demselben Laufwerk (`.picore-trash`, im Home unter `~/.local/share/.picore-trash`) – wiederherstellbar für 30 Tage, danach automatisch weg. Das Dashboard selbst, das Home-Verzeichnis und Laufwerks-Wurzeln lassen sich nicht löschen. Braucht das Recht „Dateien ändern“.

**Suchen, Auswählen, ZIP, Duplikate:** Die Suchzeile durchsucht den geöffneten Ordner samt Unterordnern (Name, auch mit `*`/`?`; Filter nach Typ, Größe, Änderungsdatum; max. 500 Treffer, 25 s). Häkchen vor den Einträgen wählen mehrere aus: **Als ZIP** (wird direkt gestreamt, ohne Zwischendatei), **Ausschneiden/Kopieren** → Zielordner öffnen → **Hier einfügen** (läuft im Hintergrund mit Fortschritt, auch zwischen Laufwerken; vorhandene Namen werden nie überschrieben, sondern „Name (2)“), **In den Papierkorb**. Das Symbol „T“ benennt um. „Als ZIP“ in der Werkzeugleiste lädt den ganzen Ordner. **Duplikate** findet inhaltsgleiche Dateien ab 1 MB (erst Größe, dann Prüfsumme) und zeigt, wie viel Platz sie belegen – „Nur die älteste behalten“ schiebt die übrigen Kopien in den Papierkorb.

### 🎨 Personalisieren

Akzentfarbe (Cyan, Grün, Violett, Orange, Pink, Blau) unter Einstellungen → Darstellung; Größe und Platz jeder Karte im Board-Bearbeiten-Modus; eigene Links (Home Assistant, Router …) unter Einstellungen → Eigene Links – erscheinen oben unter dem Link-Symbol und in der Suche.

### 🧭 Seitenleiste

Links auf jeder Seite: **Boards**, alle **Seiten** mit Namen (System-Werkzeuge, Container, Flotte, Dokumentation, Wissen, Wochenbericht, IT-Toolbox, Terminal, Einstellungen) – die Bereiche der aktuellen Seite (z.B. „Firewall“, „Logs“) stehen direkt darunter und zeigen, wo man gerade ist –, deine **eigenen Links** und die **Aktionen** (Suchen & Befehle, Verlauf abspielen, Board bearbeiten, Vollbild, Design). Der Pfeil oben klappt sie auf reine Symbole ein (gemerkt pro Browser); auf dem Handy öffnet sie das Menü-Symbol oben links.

### 🧩 Boards (Startseite im Homarr-Stil)

Die Startseite ist ein frei anordenbares Raster aus **Apps**, **Widgets** und den **Karten** des Dashboards, gegliedert in **Kategorien** – und es kann mehrere **Boards** geben (z.B. „Übersicht“, „Medien“, „Netzwerk“). Gespeichert wird auf dem Pi, alle Geräte sehen dasselbe.

- **Apps:** Kachel mit Icon, Name und optionaler Beschreibung, die deinen Dienst öffnet. Icons kommen aus der Sammlung [Dashboard Icons](https://github.com/homarr-labs/dashboard-icons) (Suche nach Name, passendes Icon wird automatisch vorgeschlagen) oder von einer eigenen Bild-Adresse; der Pi lädt sie einmal und hält sie im Cache (`icon_cache/`). Ein Punkt oben rechts zeigt, ob die App erreichbar ist – der Pi prüft jede Minute, auf Wunsch über eine eigene Status-Adresse.
- **Widgets:**
  - **Suchleiste** (DuckDuckGo, Google, Startpage, Bing, Ecosia oder eigene Adresse; findet auch deine Apps) und **eingebettete Webseite** (z.B. ein Grafana-Dashboard).
  - **Kalender:** Monatsansicht mit Erinnerungen (inkl. Wiederholungen), fälligen Aufgaben, Wartungsfenstern und Vertragsfristen, darunter die nächsten Termine; in niedrigen Kacheln nur die Terminliste. Nur nach Anmeldung sichtbar.
  - **RSS-Feed:** bis zu 5 RSS-/Atom-Feeds (z.B. `…/releases.atom` eines GitHub-Projekts), vom Pi alle 15 Minuten abgerufen.
  - **Textblock** mit Markdown (Überschriften, fett/kursiv, Code, Links, Listen) und **Linkliste** für viele Links platzsparend in einem Widget.
  - **Remote-Pi:** CPU, Temperatur, RAM, Speicher und Laufzeit eines Remote-Pis direkt auf dem Board; „Anzeigen“ schaltet das Dashboard auf ihn um.
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

Fast alle Werkzeuge nehmen `host` – den Namen eines Remote-Pis –, sonst gilt der Haupt-Pi. **Anmeldung per OAuth über Pocket ID** (FastMCP OIDCProxy, wie bei eigenen *-no-kari-Servern); jede Pocket-ID-Identität (E-Mail, Benutzername oder Subject) wird unter **Einstellungen → MCP-Server** einem Dashboard-Benutzer zugeordnet. Jedes Werkzeug ruft intern die Dashboard-API als dieser Benutzer auf: es gelten genau dessen **Berechtigungen**, alles steht mit „(MCP)“ im **Aktivitätsprotokoll**, und Remote-Pis werden wie im Browser über ihren Agent erreicht.

Einrichtung: in Pocket ID einen OIDC-Client mit der angezeigten Callback-URL (`https://<domain>/auth/callback`) anlegen, eine eigene https-Domain per Cloudflare Tunnel auf `http://<Pi>:8000` zeigen lassen, Adresse, Pocket-ID-URL, Client-ID/-Secret und die Zuordnung eintragen, „Speichern & neu starten“. Unter dieser Domain liefert das Dashboard **nur** den MCP und die OAuth-Endpunkte aus, die Oberfläche bleibt im Heimnetz. In Claude: Connector mit `https://<domain>/mcp`; Claude Code: `claude mcp add --transport http picore https://<domain>/mcp`.

### 🛰️ Flotte (`/flotte`)

Dieser Pi und alle Remote-Pis in einer Tabelle: erreichbar oder nicht, Dashboard-Version (und ob ein Update wartet), ausstehende Paket-Updates mit Sicherheits-Updates und „Neustart nötig“, Temperatur, CPU, RAM, Speicher und Laufzeit; aktualisiert sich alle 30 Sekunden. Mehrere Pis auswählen und **Paketlisten aktualisieren** oder **Update & Upgrade installieren** für alle gleichzeitig starten – jeder Pi führt es in seinem eigenen apt-Dienst aus (Berechtigung „Updates installieren“).

### 🧮 IT-Toolbox (`/werkzeuge`)

Alltagshelfer, die nur im Browser rechnen (nichts geht an den Pi oder ins Internet): **Netzwerk-Rechner** für IPv4 und IPv6 (Netz, Maske, Broadcast, Host-Bereich, Anzahl, Art der Adresse; in Teilnetze aufteilen; prüfen, ob eine IP im Netz liegt), **Base64** (auch URL-sicher), **URL-Kodierung**, **JSON** formatieren/verkleinern/prüfen mit Zeile und Spalte, **Hashes** (MD5, SHA-1, SHA-256), **JWT lesen** mit Ablaufzeit, **Passwort- und UUID-Generator**, **Zeitstempel** umrechnen und **Cron-Ausdrücke** in Worten erklärt mit den nächsten Ausführungen.

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
- **Aktivität:** wer wann was gemacht hat (Anmeldungen inkl. Fehlversuche, Updates, Dienste, Firewall, Prozesse, Downloads, Terminal …), durchsuchbar, 180 Tage.
- **Firewall (ufw):** ein-/ausschalten, Regeln ansehen, hinzufügen und löschen. Regeln mit Aktion (Erlauben / Blockieren / Ablehnen / Begrenzen), Richtung (ein-/ausgehend), Schnittstelle, Protokoll, **Quelle + Quellport** und **Ziel + Zielport** (einzeln, Bereich `6000:6010` oder Liste `80,443`), Position (am Ende oder ganz oben mit Vorrang), optional „Treffer protokollieren“ und Kommentar – mit Live-Vorschau des ufw-Befehls. Schutz gegen Aussperren: keine Sperre der eigenen IP fürs Dashboard, von SSH/Dashboard für alle oder von allem ausgehenden Verkehr; beim Einschalten werden SSH und das Dashboard (Port 8000) immer zuerst freigegeben, die letzte Freigabe lässt sich nicht löschen. Vorschläge für k3s, NetBird (wt0) und Webserver, wenn diese laufen. ufw installieren: `sudo apt install ufw`.
- **Verbindungen:** alle offenen TCP/UDP-Verbindungen mit Programm (per `ss` über den Helfer, damit auch Dienste anderer Benutzer benannt sind), Richtung, Gegenstelle mit Land, Filter nach Richtung/Gegenstelle/Text, Programm-Übersicht, „Live“ alle 5 s; interne Verbindungen (localhost, k3s-/Docker-Netze) ausgeblendet; eingehende aus dem Internet lassen sich direkt sperren.
- **Firewall-Traffic:** was die Firewall blockiert hat (aus den `[UFW BLOCK]`-Kernelmeldungen im Journal, alle Boots; Fallback `/var/log/ufw.log`) für 1 Std. / 24 Std. / 7 Tage: Kennzahlen, Diagramm Internet vs. LAN/VPN, häufigste Absender (mit Gerätenamen aus dem LAN-Scan, **„IP sperren“** mit einem Klick), häufigste Ziel-Ports (markiert, wenn auf dem Port ein Programm lauscht; **„Freigeben…“** füllt das Regel-Formular vor) und die letzten Pakete. Filter nach Blockiert/Erlaubt, Richtung, IP, Schnittstelle oder Port. Ist die Protokollierung aus, lässt sie sich direkt einschalten (Stufe „low“). Braucht Lesezugriff aufs Journal (`systemd-journal`-Gruppe, wie bei den SSH-Anmeldungen).
- **Länder (GeoIP):** nach einmaligem Laden der kostenlosen Länder-Datenbank von [DB-IP](https://db-ip.com) (CC BY 4.0, ~5 MB Download, lokal in `geoip.db`, monatlich automatisch aktualisiert) stehen Flagge und Land bei allen Internet-IPs: Firewall-Traffic (plus Übersicht „Herkunftsländer“), SSH-Anmeldungen, fail2ban-Sperren und Aktivität. Alles offline, keine IP verlässt den Pi.
- **Länder sperren:** Länder suchen oder aus den Vorschlägen im Firewall-Traffic übernehmen → „Übernehmen“. Die Netze landen in `ipset`-Listen, die eine eigene Regel in ufws `before.rules` vor allen anderen Regeln verwirft (Treffer erscheinen im Firewall-Traffic). Antworten auf eigene Verbindungen, LAN und VPN bleiben unberührt; das Land, aus dem du gerade verbunden bist, wird abgelehnt. `pi-dashboard-geoblock.service` lädt die Listen beim Booten vor ufw; nach jedem Datenbank-Update werden sie erneuert. `ipset` installiert `install.sh` automatisch.
- **SSH-Anmeldungen:** erfolgreiche und fehlgeschlagene Logins der letzten 7 Tage aus dem Journal (pro Verbindung), IPs mit den meisten Fehlversuchen (LAN / VPN / Internet), wer gerade angemeldet ist, und **fail2ban** mit Jails, gesperrten IPs und „Entsperren“. Für alle Logs: `sudo usermod -aG systemd-journal $USER`.
- **CPU-Modus:** Sparsam / Sanft / Ausgewogen / Leistung (cpufreq-Governor) mit Live-Takt; wird gespeichert und nach Neustart wieder gesetzt. sudo erlaubt nur das Schreiben der Governor-Datei.
- **Leistungstest:** Laufwerk (512-MB-Testdatei: sequentiell schreiben/lesen ohne Cache, 4K-IOPS) und CPU (SHA-256 auf 1 / allen Kernen). Ergebnisse mit Temperatur und CPU-Modus gespeichert zum Vergleichen.
- **Fehler-Übersicht:** Fehler (Priorität „err“ und schlimmer) aus dem Journal der letzten 24 Std./7/30 Tage, gruppiert nach Quelle und Meldung – Zahlen, IDs, IPs und Zeiten werden dabei ignoriert. Pro Gruppe Anzahl, Balken pro Tag, erstes/letztes Auftreten, Markierung „neu“ und „gerade eben“; „Im Log suchen“ springt mit passendem Filter zur Log-Suche.
- **Paketverwaltung:** Pakete suchen (apt-cache) mit Details, installierte Pakete nach Größe (nur selbst installierte oder alle). Vor jedem Installieren/Entfernen zeigt ein Probelauf (`apt-get -s`), was dazukommt oder wegfällt und wie viel Platz es braucht. apt läuft dann als eigene systemd-Unit (übersteht einen Dashboard-Neustart) mit Live-Protokoll. Basis-Pakete und alles, was System oder Dashboard brauchen (SSH, sudo, systemd, Kernel, Docker, Python, ufw …), lassen sich nicht entfernen – auch nicht als Abhängigkeit. Braucht das Recht „Updates installieren“.
- **Log-Suche:** System-Journal filtern nach Dienst oder Kernel, Schweregrad, Zeitraum und Suchbegriff (Suche über `journalctl -g`), Treffer hervorgehoben.
- **Speicher aufräumen:** System-Logs älter als 14 Tage, apt-Paket-Cache, ungenutzte Docker-Images & Build-Cache, `~/.cache` und Papierkorb – mit Größenvorschau, einzeln oder alles auf einmal. sudo nur für `journalctl --vacuum-time=14d` und `apt-get clean`.
- **Hardware:** Modell, SoC, RAM, Revision, Seriennummer, Kernel, Firmware, Bootloader mit Update-Hinweis, NVMe mit PCIe-Generation (Tipp für Gen 3), Laufwerke, USB-Geräte mit Geschwindigkeit.

### 🐳 Container (`/containers`)

- **Übersicht:** laufende Pods, bereite Workloads, Docker-Container, Stacks, verfügbare Image-Updates und Kubernetes-Warnungen der letzten 24 Std. – plus eine Liste mit allem, was gerade kaputt ist (CrashLoop, OOMKilled, beendete Container …).
- **Kubernetes (k3s):** Workloads (Deployments, StatefulSets, DaemonSets) mit Bereit-Status, Images und Aktionen **Neu starten** (`rollout restart`), **Stoppen** (auf 0 skalieren – „Starten“ holt die alte Anzahl zurück) und **Skalieren**; Pods mit Status, Neustarts, OOMKilled, CPU/RAM (metrics-server), **Logs** (Container-Auswahl, vorheriger Start, Filter, Live) und **Neu erstellen**; Services mit Ports/NodePorts/LoadBalancer-IP; Nodes mit Auslastung und Version; Warnungen (Events). Filter nach Namespace und Text, kube-system ausblendbar. Braucht eine lesbare kubeconfig (`~/.kube/config`, siehe Hinweis auf der Seite).
- **Docker:** alle Container mit Status/Health, CPU (inkl. 24-Std.-Sparkline), RAM, Ports; Starten/Stoppen/Neu starten, Logs, Verlauf.
- **Compose-Stacks:** aus `docker compose ls` und den Container-Labels; **Starten, Neu starten, Aktualisieren** (`pull` + `up -d`), **Stoppen, Entfernen** (`down`, Volumes bleiben) mit Live-Ausgabe; **Compose-Datei** öffnet den Editor – beim Speichern prüft `docker compose config` die Datei, kaputte YAML wird nicht gespeichert.
- **Versionshinweise:** „Was ist neu?“ bei einem Image zeigt die GitHub-Releases seines Projekts (Quelle aus den Image-Labels, sonst die üblichen Orte bei GHCR und LinuxServer.io, sonst selbst eintragen), neuere Versionen als das lokale Image zuerst und mit Hinweis auf Breaking Changes.
- **Image-Updates:** alle 12 Std. (oder per „Jetzt prüfen“) wird der Digest jedes laufenden Images (Docker + k3s) mit der Registry verglichen – Docker Hub, GHCR, Quay, lscr.io & Co., anonym per `HEAD` (zählt nicht gegen das Docker-Hub-Limit). Bei Updates: Stack direkt aktualisieren oder Deployment neu starten. Nach einem Stack-Update wird automatisch neu geprüft.
- **Statistik:** CPU, RAM und Netzwerk pro Container (Pods eines Deployments zusammengefasst) für 24 Std. / 7 Tage, einmal pro Minute gemessen (`docker stats`, `kubectl top`), 7 Tage aufbewahrt.
- **App-Store:** zehn empfohlene Apps (Jellyfin, Pi-hole, AdGuard Home, Uptime Kuma, Vaultwarden, Home Assistant, Node-RED, Grafana, Syncthing, Gitea) plus der Katalog von [LinuxServer.io](https://www.linuxserver.io) über deren API – rund 160 Apps, gefiltert auf Images für die Architektur des Pi. Suche über Name, Beschreibung und Kategorie, Filter nach Kategorie und Quelle (auch „Installiert“), Sortierung nach Beliebtheit (monatliche Downloads), Name oder Aktualität. Der Katalog wird höchstens einmal am Tag geladen und in `appstore_cache.json` gespeichert (funktioniert danach offline); Logos lädt der Pi selbst und legt sie in `appstore_logos/` ab, der Browser fragt nie bei GitHub an. Das Formular entsteht aus der Konfiguration der App: Ports (freie werden vorgeschlagen, Web-Ports unter 1024 als 8080/8443 …), Ordner (`./name` im App-Verzeichnis oder ein vorhandener Pfad), Umgebungsvariablen (PUID/PGID/TZ automatisch), optionale Ports, Ordner und Geräte zum Anhaken. Apps mit privilegiertem Modus, Host-Netzwerk, Zusatzrechten oder Geräten sind als „Erweiterte Rechte“ markiert und zeigen vor der Installation, was sie brauchen; der Docker-Socket wird nie eingebunden. „Compose-Datei ansehen“ zeigt vorher, was geschrieben wird. Jede App landet in `~/picore-apps/<name>/docker-compose.yml` und wird mit `pull` + `up -d` gestartet, mit Live-Ausgabe; danach ist sie ein normaler Compose-Stack. Systemordner (`/etc`, `/root`, `/dev`, `~/.ssh`, das Dashboard …) lassen sich nicht einbinden.

- **Container-Konsole:** „Konsole“ in der Docker-Liste öffnet eine Shell im laufenden Container (`docker exec`, bash falls vorhanden, sonst sh) – im Web-Terminal mit denselben Regeln: Passwort erneut eingeben, Einmal-Ticket, max. 3 Sitzungen, 30 Min. Leerlauf. Braucht das Recht „Web-Terminal“; jede Sitzung steht im Aktivitätsprotokoll.
- **Live-Logs:** die Logs mehrerer Container gleichzeitig, nach Zeit zusammengeführt und pro Container eingefärbt; Filter, „Nur Fehler & Warnungen“, Live-Modus (alle 2 s neue Zeilen, nach oben scrollen hält an). Die Auswahl merkt sich der Browser.
- **Automatische Updates:** pro Stack „Automatisch aktualisieren“ täglich oder an einem Wochentag zu einer Uhrzeit – derselbe Ablauf wie „Aktualisieren“ (`pull` + `up -d`). Verpasste Läufe (Pi aus) werden bis zu 3 Stunden nachgeholt; „Jetzt ausführen“ startet sofort. Jeder Lauf wird protokolliert, Fehlschläge und echte Aktualisierungen erscheinen als Ereignis.
- **Ressourcen-Limits:** CPU-Kerne und RAM pro Dienst eines Stacks. Gilt sofort (`docker update`) und bleibt über `docker-compose.picore.yml` im Stack-Ordner auch nach Updates und Neuerstellen erhalten. Braucht der Kernel RAM-Limits (cgroup memory), sagt das Dashboard Bescheid.

Ansehen braucht „System ansehen“, Logs „Logs lesen“, alle Aktionen „Dienste steuern“, das Bearbeiten der Compose-Datei „Dateien ändern“, Apps installieren das neue Recht „Apps installieren“ (nur Administratoren, kann anderen Gruppen gegeben werden).

### 🛰️ Remote-Pis (Agent)

Ein Dashboard kann weitere Raspberry Pis steuern. Auf dem anderen Pi läuft dafür der **PiCore-Agent** – dasselbe Dashboard ohne eigene Oberfläche und ohne Login, das nur mit einem Token auf das Haupt-Dashboard hört:

```bash
# auf dem Remote-Pi (Repo klonen wie beim ersten Pi):
cd pi-dashboard && ./install.sh --agent     # zeigt am Ende Adresse + Token
./agent.sh token                            # Token später wieder anzeigen
./agent.sh rotate                           # neuer Token
./agent.sh disable                          # wieder normales Dashboard
```

Im Haupt-Dashboard unter **Einstellungen → Remote-Pis** Name, Adresse (`http://IP:8000`) und Token eintragen („Verbindung testen“ prüft vorher). Danach wechselt der Schalter oben rechts zwischen „Dieser Pi“ und den Remote-Pis. Das Haupt-Dashboard leitet dann alle API-Aufrufe, die Live-Werte und das Web-Terminal an den Agent weiter – **alle Funktionen** (Dienste, Prozesse, Container & k3s, Dateien, Firewall, Updates, Pakete, Logs, Verlauf …) zeigen und steuern den gewählten Pi. Eine gelbe Leiste zeigt, welcher Pi gerade dran ist (rot, wenn er nicht erreichbar ist).

- **Rechte:** Anmeldung, Benutzer und Berechtigungen kommen vom Haupt-Dashboard und gelten auch für die Remote-Pis; im Aktivitätsprotokoll des Agents steht, wer (`name@remote`) etwas getan hat. Das Terminal-Passwort wird im Haupt-Dashboard geprüft.
- **Nur lokal:** Login, Benutzer/Rechte, eigene Links, Notizen, Freigabe-Links und „Was ist neu“ gehören immer zum Haupt-Dashboard.
- **Sicherheit:** Der Token wird bei jeder Anfrage mitgeschickt – über `http` im Klartext. Remote-Pis darum nur im Heimnetz oder über das VPN (NetBird) ansprechen; `https://` funktioniert, wenn davor ein Reverse-Proxy mit Zertifikat steht. Tokens sind nach dem Eintragen nicht mehr abrufbar. Remote-Pis verwalten dürfen nur Administratoren.
- **Ältere Pis:** Das Dashboard erkennt die Hardware und blendet aus, was es dort nicht gibt – auf einem Pi 4 z.B. Lüfterkurve, Stromversorgung/Stromverbrauch (PMIC) und ohne Lüfter die Lüfter-Karte. Das gilt auch für das Dashboard selbst, wenn es auf einem Pi 4 läuft.
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
- **E-Mail-Benachrichtigungen:** SMTP (STARTTLS, SSL oder ohne), Empfänger und Dashboard-Adresse für Links. Verschickt werden neue Ereignisse ab Warnung oder nur kritische (gesammelt alle 5 Minuten), eine Tagesübersicht zur gewählten Uhrzeit (fällige Erinnerungen, Kündigungsfristen, Garantien, ablaufende Domains, überfällige Backups – nur wenn es etwas gibt), der Wochenbericht an einem Wochentag und MCP-Freigaben sofort. Test-Mail und Vorschau von Tagesübersicht und Wochenbericht per Knopf
- **MCP-Aktivität:** offene Freigaben, welche Werkzeuge eine Freigabe brauchen, Protokoll der Aufrufe
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

---

## 📄 Lizenz & Sicherheit

- Lizenz: [MIT](LICENSE) – frei nutzbar, veränderbar und weitergebbar, solange der Copyright-Hinweis erhalten bleibt.
- Mitgelieferte Fremdsoftware: xterm.js (MIT, `static/vendor/xterm`); FastMCP (Apache-2.0, als Python-Paket); gridstack.js (MIT, `static/vendor/gridstack`); App-Icons aus Dashboard Icons von homarr-labs (über jsDelivr, werden erst bei Bedarf geladen); Wetterdaten von Open-Meteo (CC BY 4.0); Länder-Datenbank von DB-IP (CC BY 4.0, wird erst bei Bedarf geladen); App-Katalog von LinuxServer.io (über deren öffentliche API).
- Sicherheitsmodell und wie man eine Lücke meldet: [SECURITY.md](SECURITY.md). Das Dashboard ist für das Heimnetz bzw. ein VPN gedacht, nicht für den direkten Betrieb im Internet.
