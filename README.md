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
- **Server-Dienste & Homelab-Ports:**
  - Automatische Überwachung von: **SSH** (:22), **Docker**, **Webserver** (:80), **NetBird VPN** (wt0), **Kubernetes** (k3s / k8s / microk8s, :6443) und dem **Auto-Updater** – jeweils mit Status *Aktiv*, *Inaktiv* oder *Fehler*.
- **Laufwerke & Speicher-Explorer:**
  - Übersicht über alle eingehängten Datenträger (SD-Karte, NVMe-SSDs, externe USB-Festplatten unter `/media` und Dateisysteme).
- **System-Journal & Diagnose:**
  - **System-Logs:** Liest `journalctl -n 30` und `dmesg` direkt im Dashboard aus.
  - **Top laufende Prozesse:** PID, CPU%, Memory%, Benutzer.
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
3. startet den Dienst neu (bei geänderten `.service`/`.timer`-Dateien läuft `install.sh` erneut).

Der Timer läuft unabhängig vom Dashboard – auch wenn ein fehlerhafter Commit die App abstürzen lässt, wird der nächste Fix-Commit trotzdem gezogen.

```bash
journalctl -u pi-dashboard-update -f         # Update-Log live ansehen
sudo ~/pi-dashboard/update.sh                # Sofort manuell aktualisieren
systemctl list-timers pi-dashboard-update    # Nächste Prüfung
```

### 🔑 Privates Repo: GitHub-Token hinterlegen

Der Pi braucht Lesezugriff auf das Repo. Token erstellen unter [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new):
*Repository access* → nur dieses Repo, *Permissions* → **Contents: Read-only**.

Dann auf dem Pi:
```bash
~/pi-dashboard/set-token.sh
```

Der Token wird geprüft und in `.git/github-credentials` gespeichert (nur für deinen Benutzer lesbar, wird nie committed). `install.sh` fragt automatisch danach, falls das Repo nicht lesbar ist. Bei abgelaufenem Token einfach `set-token.sh` erneut ausführen.
