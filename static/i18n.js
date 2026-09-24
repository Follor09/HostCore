/**
 * PiCore Dashboard - language layer (Deutsch / English)
 *
 * The UI is written in German. With English selected, this script translates every
 * text node and the title / placeholder / aria-label attributes - including text that
 * is added or changed later (live values, toasts, server error messages) - via a
 * MutationObserver. Exact strings come from DICT, strings with numbers or names from
 * PATTERNS. Log output, the terminal and file names are never touched.
 *
 * Load it before the page's own script: it also sets I18N.locale for date/number formats.
 */
(function () {
  'use strict';

  const KEY = 'lang';
  let lang = 'de';
  try { lang = localStorage.getItem(KEY) || ''; } catch (e) { /* storage blocked */ }
  if (lang !== 'de' && lang !== 'en') {
    lang = (navigator.language || 'de').toLowerCase().startsWith('de') ? 'de' : 'en';
  }

  const I18N = window.I18N = {
    lang,
    locale: lang === 'en' ? 'en-GB' : 'de-DE',
    misses: new Set(),  // German-looking strings without a translation (for testing)
    setLang(next) {
      try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
      location.reload();
    },
    t: s => s
  };
  document.documentElement.lang = lang;
  if (lang === 'de') return;

  // ---------------------------------------------------------------------------
  // Exact strings
  // ---------------------------------------------------------------------------
  const DICT = {
    // --- Common
    'Dashboard': 'Dashboard', 'Einstellungen': 'Settings', 'Abmelden': 'Log out', 'Anmelden': 'Log in',
    'Speichern': 'Save', 'Abbrechen': 'Cancel', 'Bestätigen': 'Confirm', 'Schließen (Esc)': 'Close (Esc)',
    'Neu laden': 'Reload', 'Lade…': 'Loading…', 'Läuft…': 'Running…', 'Suche…': 'Searching…', 'Sicher?': 'Sure?',
    'Aktiv': 'Active', 'Inaktiv': 'Inactive', 'Fehler': 'Error', 'Verbunden': 'Connected', 'Getrennt': 'Disconnected',
    'Nicht verbunden': 'Not connected', 'Nicht verfügbar': 'Not available', 'Unbekannt': 'Unknown', 'unbekannt': 'unknown',
    'Keine Daten.': 'No data.', 'Keine Ausgabe.': 'No output.', '(keine Ausgabe)': '(no output)', '(keine Einträge)': '(no entries)',
    'Fehler beim Laden.': 'Failed to load.', 'Nicht angemeldet.': 'Not logged in.', 'Zurück zum Dashboard': 'Back to dashboard',
    'Bereiche': 'Sections', 'Aktion': 'Action', 'Name': 'Name', 'Pfad': 'Path', 'Test': 'Test', 'Hinzufügen': 'Add',
    'Entfernen': 'Remove', 'Aufräumen': 'Clean up', 'Beenden': 'End', 'Erzwingen': 'Force', 'Erzwingen?': 'Force?',
    'Starten': 'Start', 'Stoppen': 'Stop', 'Neu starten': 'Restart', 'Suchen': 'Search', 'Öffnen': 'Open',
    'Wirklich löschen?': 'Really delete?', 'Wirklich alles?': 'Really everything?', 'Wirklich alle abmelden?': 'Really log out everyone?',
    'Wirklich entfernen?': 'Really remove?', 'Wirklich neu starten?': 'Really restart?', 'Wirklich stoppen?': 'Really stop?',
    'Keine Berechtigung.': 'Permission denied.', 'Unbekannte Aktion.': 'Unknown action.',
    'Keine Berechtigung – install.sh auf dem Pi erneut ausführen.': 'Permission denied – run install.sh on the Pi again.',
    'Ungültige Herkunft der Anfrage.': 'Invalid request origin.',
    'Nur auf dem Raspberry Pi verfügbar.': 'Only available on the Raspberry Pi.',
    'Linux': 'Linux', 'System': 'System', 'Hardware': 'Hardware', 'Kernel': 'Kernel', 'Docker': 'Docker', 'Kubernetes': 'Kubernetes',

    // --- Titles
    'Raspberry Pi Dashboard | Server & Homelab Monitor': 'Raspberry Pi Dashboard | Server & Homelab Monitor',
    'Anmelden | PiCore Server': 'Log in | PiCore Server', 'Einstellungen | PiCore Server': 'Settings | PiCore Server',
    'System-Werkzeuge | PiCore Server': 'System tools | PiCore Server', 'Terminal | PiCore Server': 'Terminal | PiCore Server',

    // --- Login
    'Anmelden, um das Dashboard zu verwenden': 'Log in to use the dashboard', 'Benutzername': 'Username', 'Passwort': 'Password',
    'Angemeldet bleiben (30 Tage)': 'Stay logged in (30 days)', 'Prüfe…': 'Checking…',
    'Nur ansehen, ohne Anmeldung →': 'View only, without login →',
    'Bitte Benutzername und Passwort eingeben.': 'Please enter username and password.',
    'Benutzername oder Passwort falsch.': 'Wrong username or password.', 'Anmeldung fehlgeschlagen.': 'Login failed.',
    'Noch kein Passwort gesetzt. Auf dem Pi per SSH ausführen:': 'No password set yet. Run on the Pi via SSH:',
    'Noch kein Passwort gesetzt. Auf dem Pi ausführen: ~/pi-dashboard/set-password.sh': 'No password set yet. Run on the Pi: ~/pi-dashboard/set-password.sh',

    // --- Header
    'Live WebSocket': 'Live WebSocket', 'Verbindung getrennt': 'Connection lost', 'Neustart': 'Reboot', 'Herunterfahren': 'Shut down',
    'Raspberry Pi neu starten': 'Reboot Raspberry Pi', 'Raspberry Pi ausschalten': 'Shut down Raspberry Pi',
    'Vollbild Kiosk-Modus': 'Fullscreen kiosk mode', 'Design-Schema umschalten': 'Switch colour scheme',
    'System-Werkzeuge': 'System tools', 'Web-Terminal': 'Web terminal', 'Einstellungen öffnen': 'Open settings',
    'Update verfügbar!': 'Update available!', 'Neuer GitHub Commit verfügbar': 'New GitHub commit available',
    'Vollbildmodus konnte nicht aktiviert werden': 'Could not enter fullscreen',
    'Design: Solar Cyber aktiviert': 'Theme: Solar Cyber enabled', 'Design: Neon Cyber aktiviert': 'Theme: Neon Cyber enabled',

    // --- Clock / weather
    'SYSTEMZEIT & BETRIEBSZEIT': 'SYSTEM TIME & UPTIME', 'Lade Datum...': 'Loading date...', 'Uptime': 'Uptime', 'Boot': 'Boot',
    'WETTER & KLIMA': 'WEATHER', 'Ort ändern': 'Change location', 'Ort eingeben…': 'Enter location…', 'Wetter aktualisieren': 'Refresh weather',
    'Wetterdaten werden aktualisiert...': 'Refreshing weather...', 'Jetzt': 'Now', 'Stunden': 'Hourly', 'Morgen': 'Tomorrow',
    '7 Tage': '7 days', 'Heute': 'Today', 'Luftfeuchte': 'Humidity', 'Wind': 'Wind', 'Regen': 'Rain', 'Wind max.': 'Max wind',
    'Sonne': 'Sun', 'UV-Index': 'UV index', 'Keine Stundenvorhersage verfügbar': 'No hourly forecast available',
    'Keine Vorhersage für morgen verfügbar': 'No forecast for tomorrow available', 'Keine Wochenvorhersage verfügbar': 'No weekly forecast available',
    'Bitte einen Ort eingeben.': 'Please enter a location.', 'Ort konnte nicht gesetzt werden.': 'Could not set the location.',
    'Ortssuche fehlgeschlagen. Internetverbindung prüfen.': 'Location lookup failed. Check the internet connection.',
    'Klarer Himmel': 'Clear sky', 'Klar': 'Clear', 'Überwiegend klar': 'Mostly clear', 'Teilweise bewölkt': 'Partly cloudy',
    'Bedeckt': 'Overcast', 'Neblig': 'Foggy', 'Raureifnebel': 'Rime fog', 'Leichter Nieselregen': 'Light drizzle',
    'Nieselregen': 'Drizzle', 'Starker Nieselregen': 'Heavy drizzle', 'Leichter Regen': 'Light rain', 'Mäßiger Regen': 'Moderate rain',
    'Starker Regen': 'Heavy rain', 'Leichter Schneefall': 'Light snow', 'Schneefall': 'Snow', 'Starker Schneefall': 'Heavy snow',
    'Regenschauer': 'Rain showers', 'Starke Schauer': 'Heavy showers', 'Heftige Schauer': 'Violent showers',
    'Gewitter': 'Thunderstorm', 'Gewitter mit Hagel': 'Thunderstorm with hail', 'Heiter': 'Fair',

    // --- Services
    'SERVER-DIENSTE & PORTS': 'SERVER SERVICES & PORTS', 'SSH Server': 'SSH server', 'Docker Engine': 'Docker Engine',
    'Webserver (HTTP)': 'Web server (HTTP)', 'NetBird VPN': 'NetBird VPN', 'Auto-Updater': 'Auto-updater',
    'Timer aus': 'Timer off', 'Letztes Update fehlgeschlagen': 'Last update failed', 'GitHub · jede Minute': 'GitHub · every minute',
    'Journal (letzte 150 Zeilen)': 'Journal (last 150 lines)', 'Journal konnte nicht geladen werden.': 'Could not load the journal.',
    'Update jetzt ausführen': 'Run update now', 'Aktion fehlgeschlagen.': 'Action failed.',
    'Achtung: Wenn du SSH stoppst, kannst du dich nicht mehr per SSH verbinden – nur noch über dieses Dashboard.':
      'Careful: if you stop SSH you can no longer connect via SSH – only through this dashboard.',
    'Stoppen oder Neustarten von Docker hält alle Container an.': 'Stopping or restarting Docker stops all containers.',
    'Wenn du über NetBird verbunden bist, verlierst du beim Stoppen die Verbindung zum Dashboard.':
      'If you are connected via NetBird, stopping it cuts your connection to the dashboard.',
    'Stoppen hält alle Pods auf diesem Node an.': 'Stopping halts all pods on this node.',
    'Dienst ist auf diesem System nicht installiert.': 'Service is not installed on this system.',

    // --- Metric cards
    'CPU Auslastung': 'CPU usage', 'Kerne Details': 'Core details', 'CPU Temperatur': 'CPU temperature',
    'Sensor: Thermal Zone': 'Sensor: thermal zone', 'Raspberry Pi Throttling-Status': 'Raspberry Pi throttling status',
    'Normal': 'Normal', 'Optimal': 'Optimal', 'Warnung': 'Warning',
    'Keine thermische Drosselung oder Unterspannung erkannt.': 'No thermal throttling or undervoltage detected.',
    'Normal (Nicht auf Pi)': 'Normal (not a Pi)', 'OK (Keine Drosselung)': 'OK (no throttling)',
    '⚠️ Unterspannung (Netzteil zu schwach!)': '⚠️ Undervoltage (power supply too weak!)', '⚠️ Taktung begrenzt': '⚠️ Clock capped',
    '🔥 Thermisches Throttling aktiv': '🔥 Thermal throttling active', '🌡️ Soft Temp Limit erreicht': '🌡️ Soft temperature limit reached',
    'Arbeitsspeicher': 'Memory', 'Belegt:': 'Used:', 'Frei:': 'Free:', 'Verfügbar:': 'Available:', 'Gesamt:': 'Total:',
    'SWAP SPEICHER': 'SWAP', 'Systemlaufwerk': 'System drive', 'NVMe SSD': 'NVMe SSD', 'SD-Karte': 'SD card', 'USB-SSD': 'USB SSD',
    'Festplatte (HDD)': 'Hard disk (HDD)', 'eMMC': 'eMMC', 'Root-Partition (/)': 'Root partition (/)',
    'Gesamtkapazität': 'Capacity', 'Architektur': 'Architecture', 'NVMe-Zustand': 'NVMe health', 'Lebensdauer:': 'Life left:',
    'Reserve:': 'Spare:', 'Temperatur': 'Temperature', 'Geschrieben': 'Written', 'Betriebszeit': 'Power-on time',
    'Gesund': 'Healthy', 'Verschleiß hoch': 'Heavily worn', 'Keine SMART-Daten': 'No SMART data',
    'SMART nicht lesbar (install.sh erneut ausführen)': 'SMART not readable (run install.sh again)',
    'smartmontools nicht installiert (install.sh erneut ausführen)': 'smartmontools not installed (run install.sh again)',
    'Netzwerk-Traffic': 'Network traffic', 'Echtzeit Datendurchsatz': 'Real-time throughput', 'Download': 'Download', 'Upload': 'Upload',
    'Empfangen gesamt': 'Received total', 'Gesendet gesamt': 'Sent total', 'Lüfter': 'Fan', 'Active Cooler': 'Active Cooler',
    'Active Cooler (PWM)': 'Active Cooler (PWM)', 'Kein Lüfter erkannt': 'No fan detected', 'Drehzahl': 'Speed', 'PWM': 'PWM',
    'Stufen ab': 'Steps at', 'Stufe – / –': 'Level – / –', 'Aus': 'Off', 'Niedrig': 'Low', 'Mittel': 'Medium', 'Hoch': 'High', 'Maximal': 'Max',
    'Stromversorgung': 'Power supply', 'Board, ohne USB-Geräte': 'Board, without USB devices', 'Nur auf Raspberry Pi 5': 'Raspberry Pi 5 only',
    'Eingang': 'Input', 'Netzteil': 'Power supply', 'CPU-Kern': 'CPU core', 'Spannung OK': 'Voltage OK', 'Unterspannung!': 'Undervoltage!',
    'Unterspannung seit Boot': 'Undervoltage since boot', 'Netzteil < 5 A': 'PSU < 5 A',
    'Unter 5 A: der Pi begrenzt USB-Geräte auf 600 mA': 'Below 5 A the Pi limits USB devices to 600 mA',

    // --- Updates card / sheet
    'System-Updates': 'System updates', 'apt-Pakete': 'apt packages', 'Details & Installation öffnen': 'Open details & install',
    'Aktuell': 'Up to date', 'Neustart nötig': 'Reboot required', 'Sicherheitsupdate': 'Security update',
    'Alle Pakete sind auf dem neuesten Stand.': 'All packages are up to date.',
    'Nur auf Debian / Raspberry Pi OS verfügbar.': 'Only available on Debian / Raspberry Pi OS.',
    'Klicken für alle Pakete & Installation': 'Click for all packages & install', 'Paket': 'Package', 'Installiert': 'Installed', 'Neu': 'New',
    'Paketlisten aktualisieren': 'Refresh package lists', 'Update & Upgrade installieren': 'Install update & upgrade',
    'Wirklich installieren? Nochmal klicken': 'Really install? Click again', 'Ausgabe': 'Output', 'Ausgabe: apt update': 'Output: apt update',
    'Ausgabe: apt update & full-upgrade': 'Output: apt update & full-upgrade', 'Fertig': 'Done', 'Update läuft…': 'Update running…',
    'Upgrade läuft…': 'Upgrade running…', 'Starte…': 'Starting…', 'apt läuft bereits.': 'apt is already running.',
    'Paketlisten werden aktualisiert…': 'Refreshing package lists…', 'Update & Upgrade gestartet…': 'Update & upgrade started…',
    'apt ist fertig. Paketliste wird aktualisiert.': 'apt finished. Refreshing the package list.',
    'apt ist mit einem Fehler beendet.': 'apt finished with an error.', 'Start fehlgeschlagen.': 'Start failed.',
    'apt läuft als eigener Systemdienst weiter – auch wenn du dieses Fenster schließt oder das Dashboard neu startet.':
      'apt keeps running as its own system service – even if you close this window or the dashboard restarts.',

    // --- Wake-on-LAN
    'Wake-on-LAN': 'Wake-on-LAN', 'Geräte im Netzwerk wecken': 'Wake devices on the network', 'Wecken': 'Wake',
    'Geräte verwalten →': 'Manage devices →', 'Noch keine Geräte. Füge PC, NAS & Co. in den Einstellungen hinzu.': 'No devices yet. Add your PC, NAS & co. in the settings.',
    'Senden fehlgeschlagen.': 'Sending failed.', 'Gerät nicht gefunden.': 'Device not found.', 'Maximal 30 Geräte.': 'At most 30 devices.',
    'Bitte einen Namen angeben.': 'Please enter a name.',
    'Ungültige MAC-Adresse (Format: AA:BB:CC:DD:EE:FF).': 'Invalid MAC address (format: AA:BB:CC:DD:EE:FF).',
    'Ungültige Broadcast-Adresse (z.B. 192.168.178.255).': 'Invalid broadcast address (e.g. 192.168.178.255).',

    // --- History
    'Verlauf': 'History', 'Messwerte der letzten 24 Stunden': 'Readings of the last 24 hours', 'Zeitraum': 'Time range', 'Messwert': 'Metric',
    'Temperatur ': 'Temperature', 'Leistung': 'Power', 'Als Tabelle anzeigen': 'Show as table', 'Verlaufsdiagramm': 'History chart',
    'Noch keine Daten – der erste Messpunkt wird nach einer Minute gespeichert.': 'No data yet – the first reading is stored after one minute.',
    'Ø': 'Avg', 'Spitze': 'Peak', 'Minimum': 'Minimum', 'Zeit': 'Time', 'CPU-Auslastung': 'CPU usage', 'CPU-Temperatur': 'CPU temperature',
    'Lüfterstufe': 'Fan level', 'Board-Leistung': 'Board power', 'Netzwerk': 'Network', 'RAM': 'RAM', 'CPU': 'CPU',

    // --- Workloads
    'Container & Pods': 'Containers & pods', 'Docker & Kubernetes': 'Docker & Kubernetes', 'System-Pods ausblenden': 'Hide system pods',
    'Docker ist nicht installiert.': 'Docker is not installed.', 'kubectl / k3s ist nicht installiert.': 'kubectl / k3s is not installed.',
    'Keine Container vorhanden.': 'No containers.', 'Keine Pods (System-Pods ausgeblendet).': 'No pods (system pods hidden).',
    'Keine Berechtigung für Docker.': 'No permission for Docker.', 'Keine lesbare kubeconfig gefunden.': 'No readable kubeconfig found.',
    'Log anzeigen': 'Show log', 'Container-Log': 'Container log', 'Letzte 300 Zeilen': 'Last 300 lines',
    'Log konnte nicht geladen werden.': 'Could not load the log.',

    // --- Bottom tools
    'Laufwerke & Mount-Punkte': 'Drives & mount points', 'SD-Karte, NVMe-SSDs & externe USB-Speicher': 'SD card, NVMe SSDs & external USB storage',
    'Laufwerke neu einlesen': 'Rescan drives', 'Laufwerke aktualisiert': 'Drives refreshed', 'Keine Partitionen gefunden.': 'No partitions found.',
    'Root System (/)': 'Root system (/)', 'Boot Partition (/boot)': 'Boot partition (/boot)',
    'Dateien durchsuchen & Speicher analysieren': 'Browse files & analyse storage',
    'Top Prozesse': 'Top processes', 'Diagnose & Server-Tools': 'Diagnostics & server tools', 'System-Logs': 'System logs', 'Git & Updates': 'Git & updates',
    'PID': 'PID', 'CPU %': 'CPU %', 'MEM %': 'MEM %', 'Benutzer': 'User', 'Top 6 nach CPU': 'Top 6 by CPU', 'Suchergebnis': 'Search result',
    'Prozess suchen (Name oder PID)…': 'Search process (name or PID)…', 'Prozess suchen': 'Search process',
    'Lade aktive Prozesse...': 'Loading processes...', 'Keine Prozesse gefunden': 'No processes found', 'Kein passender Prozess': 'No matching process',
    'Gehört einem anderen Benutzer oder ist geschützt – nur über das Web-Terminal mit sudo beendbar.': 'Owned by another user or protected – end it from the web terminal with sudo.',
    'Beenden fehlgeschlagen.': 'Could not end the process.', 'Dieser Prozess ist geschützt.': 'This process is protected.',
    'Prozess ist bereits beendet.': 'Process has already ended.', 'Prozess existiert nicht mehr (PID wurde neu vergeben).': 'Process no longer exists (PID was reused).',
    'Klicke oben auf einen Diagnose-Befehl, um die Ausgabe hier in Echtzeit anzuzeigen.': 'Click a diagnostic command above to show its output here.',
    'Docker PS': 'Docker PS', 'Ping Test (1.1.1.1)': 'Ping test (1.1.1.1)', 'Speicher (free -h)': 'Memory (free -h)', 'Festplatte (df -h)': 'Disk (df -h)',
    'vcgencmd temp': 'vcgencmd temp', 'vcgencmd throttled': 'vcgencmd throttled', 'bash — output': 'bash — output',
    'Journalctl & Kernel-Ereignisse': 'Journalctl & kernel events', 'Logs aktualisieren': 'Refresh logs', 'Logs aktualisiert': 'Logs refreshed',
    'Lade System-Logs...': 'Loading system logs...', 'Keine Logs vorhanden.': 'No logs.', 'Fehler beim Abrufen der Logs.': 'Failed to fetch logs.',
    'Aktueller Git Commit': 'Current git commit', 'Installierter Commit': 'Installed commit', 'Nach Updates suchen': 'Check for updates',
    'Jetzt Aktualisieren': 'Update now', 'Auto-Update Poller': 'Auto-update poller', 'Aktiv (alle 5 Min)': 'Active (every 5 min)',
    'Regelmäßiger Check aktiv': 'Periodic check active', 'GitHub Webhook URL': 'GitHub webhook URL', 'Update Protokoll / Git Output': 'Update log / git output',
    'Bereit. Änderungen auf GitHub werden alle 5 Minuten automatisch erkannt.': 'Ready. Changes on GitHub are detected automatically.',
    '🚀 Neues Update auf GitHub verfügbar!': '🚀 New update available on GitHub!', 'System ist auf dem neuesten Stand': 'System is up to date',
    'Neues Update vom GitHub Repo installieren': 'Install the new update from the GitHub repo', 'Suche nach Updates auf GitHub...': 'Checking GitHub for updates...',
    'Suche nach neuen Commits auf GitHub (git fetch origin)...': 'Looking for new commits on GitHub (git fetch origin)...',
    'Update-Prüfung fehlgeschlagen': 'Update check failed', 'Installiere Update vom GitHub Repo...': 'Installing update from the GitHub repo...',
    'Update installiert! Server wird neu gestartet...': 'Update installed! Restarting server...', 'Fehler beim Update.': 'Update failed.',
    'Update läuft bereits...': 'Update already running...', 'Das Verzeichnis ist kein Git-Repository.': 'The folder is not a git repository.',
    'Update erfolgreich installiert! Der Server wird jetzt neu geladen...': 'Update installed! The server is reloading...',
    'Kein Git-Repo': 'No git repo', 'Starte git pull und Abhängigkeiten-Prüfung...': 'Running git pull and checking dependencies...',
    'GitHub push event empfangen. Auto-Update gestartet!': 'GitHub push event received. Auto-update started!', 'Letzter Check: gerade eben': 'Last check: just now', 'Lese System-Journal / dmesg...': 'Reading system journal / dmesg...',
    'Unbekannter Fehler': 'Unknown error', 'Befehl ausgeführt': 'Command executed',

    // --- Reboot modal
    'System-Aktion bestätigen': 'Confirm system action', 'Raspberry Pi Neustart': 'Reboot Raspberry Pi', 'Raspberry Pi Herunterfahren': 'Shut down Raspberry Pi',
    'Möchtest du den Raspberry Pi wirklich neu starten?': 'Do you really want to reboot the Raspberry Pi?',
    'Möchtest du den Raspberry Pi jetzt neu starten? Laufende Serverdienste werden beendet.': 'Reboot the Raspberry Pi now? Running services will be stopped.',
    'Möchtest du das System sicher ausschalten? Danach muss die Stromversorgung getrennt bzw. wiederhergestellt werden.': 'Shut the system down safely? Afterwards the power has to be disconnected and reconnected.',
    'Jetzt Neustarten': 'Reboot now', 'Jetzt Ausschalten': 'Shut down now',
    'Raspberry Pi wird jetzt neu gestartet...': 'Raspberry Pi is rebooting...', 'Raspberry Pi wird heruntergefahren...': 'Raspberry Pi is shutting down...',
    "[Simulation] Neustart-Befehl erfolgreich empfangen. Auf einem echten Pi wird 'sudo reboot' ausgeführt.": "[Simulation] Reboot command received. On a real Pi 'sudo reboot' runs.",
    "[Simulation] Herunterfahren-Befehl erfolgreich empfangen. Auf einem echten Pi wird 'sudo poweroff' ausgeführt.": "[Simulation] Shutdown command received. On a real Pi 'sudo poweroff' runs.",

    // --- File explorer
    'Dateien': 'Files', 'Nur lesen · Klick auf eine Datei lädt sie herunter': 'Read-only · click a file to download it', 'Übergeordneter Ordner': 'Parent folder',
    '↑ Hoch': '↑ Up', 'Ordnergrößen berechnen': 'Calculate folder sizes', 'Größte Dateien': 'Largest files', 'Ordner öffnen': 'Open folder',
    'Ordner ist leer.': 'Folder is empty.', 'Keine lesbaren Dateien gefunden.': 'No readable files found.',
    'Aus Sicherheitsgründen gesperrt': 'Blocked for security reasons', 'Dieser Pfad ist aus Sicherheitsgründen gesperrt.': 'This path is blocked for security reasons.',
    'Berechne Ordnergrößen… (kann bei großen Laufwerken dauern)': 'Calculating folder sizes… (can take a while on large drives)',
    'Suche größte Dateien…': 'Looking for the largest files…', 'Nur die ersten 2000 Einträge werden angezeigt.': 'Only the first 2000 entries are shown.',
    'Ungültiger Pfad.': 'Invalid path.', 'Pfad liegt auf keinem eingehängten Laufwerk.': 'Path is not on a mounted drive.', 'Kein Ordner.': 'Not a folder.',
    'Keine Leserechte für diesen Ordner.': 'No read permission for this folder.', 'Keine Datei.': 'Not a file.',
    'Keine Leserechte für diese Datei.': 'No read permission for this file.', 'Berechnung dauerte zu lange (> 2 Min.).': 'Calculation took too long (> 2 min).',

    // --- Service / updates misc from the server
    'Keine Logs verfügbar': 'No logs available',

    // --- Settings
    'Konto': 'Account', 'Wetter': 'Weather', 'Dienste': 'Services', 'Auto-Update': 'Auto-update', 'Sprache': 'Language',
    'Zugangsdaten für das Dashboard. Beim Speichern werden alle anderen Geräte abgemeldet.': 'Credentials for the dashboard. Saving logs out all other devices.',
    'Neues Passwort': 'New password', '(leer lassen = unverändert)': '(leave empty = unchanged)', 'Neues Passwort wiederholen': 'Repeat new password',
    'Aktuelles Passwort': 'Current password', '(zur Bestätigung)': '(to confirm)', '(optional)': '(optional)',
    'Ansehen ohne Login': 'View without login',
    'Für ein Wanddisplay: Jeder im Netzwerk sieht das Dashboard. Aktionen, Updates und diese Seite brauchen weiterhin ein Login.': 'For a wall display: everyone on the network can see the dashboard. Actions, updates and this page still require a login.',
    'Alle anderen Geräte abmelden': 'Log out all other devices', 'Beendet jede Sitzung außer dieser – z.B. wenn ein Gerät verloren ging.': 'Ends every session except this one – e.g. if a device got lost.',
    'Die neuen Passwörter stimmen nicht überein.': 'The new passwords do not match.', 'Bitte das aktuelle Passwort eingeben.': 'Please enter the current password.',
    'Speichere…': 'Saving…', 'Gespeichert.': 'Saved.', 'Gespeichert. Alle anderen Geräte wurden abgemeldet.': 'Saved. All other devices have been logged out.',
    'Aktuelles Passwort ist falsch.': 'Current password is wrong.', 'Alle anderen Geräte wurden abgemeldet.': 'All other devices have been logged out.',
    'Benutzername: 1–32 Zeichen aus Buchstaben, Ziffern, . _ -': 'Username: 1–32 characters from letters, digits, . _ -',
    'Ansehen ohne Login ist jetzt an.': 'View without login is now on.', 'Ansehen ohne Login ist jetzt aus.': 'View without login is now off.',
    'Ort': 'Location', 'Ort für das Wetter-Widget. Koordinaten werden über Open-Meteo ermittelt.': 'Location for the weather widget. Coordinates are looked up via Open-Meteo.',
    'Welche Kacheln im Widget „Server-Dienste“ angezeigt werden. Änderungen werden sofort gespeichert.': 'Which tiles the "Server services" widget shows. Changes are saved immediately.',
    'Dienste gespeichert.': 'Services saved.',
    'Geräte, die im Dashboard per Klick geweckt werden können. Wake-on-LAN muss im BIOS / in der Netzwerkkarte des Geräts aktiviert sein.': 'Devices you can wake from the dashboard with a click. Wake-on-LAN has to be enabled in the device\'s BIOS / network card.',
    'MAC-Adresse': 'MAC address', 'Broadcast': 'Broadcast', 'Noch keine Geräte.': 'No devices yet.',
    'Der Pi prüft jede Minute GitHub und installiert neue Commits automatisch.': 'The Pi checks GitHub every minute and installs new commits automatically.',
    'Status': 'Status', 'Repository': 'Repository', 'Branch': 'Branch', 'Commit': 'Commit', 'GitHub-Token': 'GitHub token', 'Hinterlegt': 'Saved',
    'Nicht hinterlegt (nur für private Repos nötig)': 'Not saved (only needed for private repos)', 'Letzte Prüfung': 'Last check',
    'Jetzt prüfen': 'Check now', 'Prüfe GitHub…': 'Checking GitHub…', 'Alles aktuell.': 'Everything up to date.',
    'Messwerte für das Verlaufsdiagramm (CPU, Temperatur, RAM, Netzwerk, Lüfter).': 'Readings for the history chart (CPU, temperature, RAM, network, fan).',
    'Speicherplatz': 'Disk space', 'Messpunkte': 'Data points', 'Ältester Wert': 'Oldest value', 'Aufbewahrung': 'Retention',
    'Verlauf löschen': 'Clear history', 'Verlauf gelöscht.': 'History cleared.',
    'Hostname': 'Hostname', 'Modell': 'Model', 'Betriebssystem': 'Operating system', 'Python': 'Python', 'Laufzeit': 'Uptime',
    'Einstellungen konnten nicht geladen werden': 'Could not load settings',
    'Sprache der Oberfläche. Wird in diesem Browser gespeichert.': 'Interface language. Stored in this browser.',

    // --- Terminal
    'Terminal': 'Terminal', 'Terminal öffnen': 'Open terminal', 'Zur Sicherheit bitte das Passwort erneut eingeben.': 'For security, please enter your password again.',
    'Neu verbinden': 'Reconnect', 'Verbinde…': 'Connecting…', 'Passwort ist falsch.': 'Password is wrong.',
    'Terminal konnte nicht geöffnet werden.': 'Could not open the terminal.', 'xterm.js konnte nicht geladen werden.': 'Could not load xterm.js.',
    'Sitzung abgelaufen oder Ticket ungültig – bitte erneut anmelden.': 'Session expired or ticket invalid – please log in again.',
    'Die Shell läuft als dein Benutzer – für Root-Befehle': 'The shell runs as your user – for root commands use',
    'verwenden. Nach 30 Min. ohne Eingabe wird sie geschlossen.': '. It closes after 30 minutes without input.',

    // --- System tools
    'Netzwerk ': 'Network', 'Schnittstellen': 'Interfaces', 'Container- & Loopback-Schnittstellen anzeigen': 'Show container & loopback interfaces',
    'Offene Ports': 'Open ports', 'Port': 'Port', 'Protokoll': 'Protocol', 'Adresse': 'Address', 'Dienst': 'Service', 'Prozess': 'Process',
    'Erreichbar': 'Reachable', 'Nur lokal': 'Local only', 'Gateway': 'Gateway', 'DNS-Server': 'DNS servers', 'keine IP-Adresse': 'no IP address',
    'LAN': 'LAN', 'WLAN': 'Wi-Fi', 'VPN': 'VPN', 'Container': 'Container', 'Loopback': 'Loopback', 'Sonstige': 'Other',
    'WLAN-Signalstärke': 'Wi-Fi signal strength', 'Sehr gut': 'Very good', 'Gut': 'Good', 'Mäßig': 'Fair', 'Schwach': 'Weak',
    'Keine Schnittstellen gefunden.': 'No interfaces found.',
    'Offene Ports konnten ohne Root-Rechte nicht gelesen werden.': 'Open ports could not be read without root rights.',
    'Prozessnamen anderer Benutzer (z.B. root) sind ohne Root-Rechte nicht sichtbar.': 'Process names of other users (e.g. root) are not visible without root rights.',
    'Prozessor': 'Processor', 'Revision': 'Revision', 'Seriennummer': 'Serial number', 'Firmware': 'Firmware', 'Bootloader': 'Bootloader',
    'Laufwerke': 'Drives', 'USB-Geräte': 'USB devices', 'Keine USB-Geräte angeschlossen.': 'No USB devices connected.',
    'Keine Laufwerksdetails verfügbar (nur auf Linux).': 'No drive details available (Linux only).', 'Unbekanntes Gerät': 'Unknown device',
    'Tipp: Die SSD kann PCIe Gen 3 (~doppelt so schnell). Aktivieren mit „dtparam=pciex1_gen=3“ in /boot/firmware/config.txt und Neustart. Offiziell nicht zertifiziert, läuft aber meist stabil.': 'Tip: the SSD supports PCIe Gen 3 (~twice as fast). Enable it with "dtparam=pciex1_gen=3" in /boot/firmware/config.txt and reboot. Not officially certified, but usually stable.',
    'CPU-Modus': 'CPU mode', 'Legt fest, wie schnell die CPU hochtaktet. Wird gespeichert und nach einem Neustart automatisch wieder gesetzt.': 'Sets how quickly the CPU clocks up. It is saved and applied again after a reboot.',
    'Sparsam': 'Power saving', 'Sanft': 'Gentle', 'Ausgewogen': 'Balanced', 'Ausgewogen (schedutil)': 'Balanced (schedutil)',
    'Immer niedrigster Takt. Kühl und leise, aber spürbar langsamer.': 'Always the lowest clock. Cool and quiet, but noticeably slower.',
    'Taktet langsam und schrittweise hoch. Spart etwas Strom.': 'Clocks up slowly, step by step. Saves a little power.',
    'Standard: taktet bei Last sofort hoch, im Leerlauf runter.': 'Default: clocks up immediately under load, down when idle.',
    'Moderne Variante von „Ausgewogen“, gesteuert vom Scheduler.': 'Modern variant of "Balanced", driven by the scheduler.',
    'Immer maximaler Takt. Schnellste Reaktion, mehr Wärme und Verbrauch.': 'Always the maximum clock. Fastest response, more heat and power.',
    'Auf diesem System nicht verfügbar (nur Linux mit cpufreq).': 'Not available on this system (Linux with cpufreq only).',
    'CPU-Modus wird auf diesem System nicht unterstützt.': 'CPU mode is not supported on this system.', 'Unbekannter CPU-Modus.': 'Unknown CPU mode.',
    'Umschalten fehlgeschlagen.': 'Switching failed.',
    'Leistungstest': 'Speed test', 'Misst Laufwerk und CPU. Ergebnisse werden gespeichert – praktisch zum Vergleichen, z.B. vor und nach PCIe Gen 3 oder einem anderen CPU-Modus.': 'Measures drive and CPU. Results are saved – handy for comparing, e.g. before and after PCIe Gen 3 or another CPU mode.',
    'Laufwerk': 'Drive', 'Test starten (~15 s)': 'Start test (~15 s)', 'Test starten (~6 s)': 'Start test (~6 s)',
    'Schreibt eine 512-MB-Testdatei und löscht sie danach wieder.': 'Writes a 512 MB test file and deletes it afterwards.',
    'SHA-256-Durchsatz auf einem und auf allen Kernen.': 'SHA-256 throughput on one core and on all cores.',
    'Bisherige Ergebnisse': 'Previous results', 'Datum': 'Date', 'Ergebnis': 'Result', 'Schreiben': 'Write', 'Lesen': 'Read', '4K zufällig': '4K random',
    '1 Kern': '1 core', 'Skalierung': 'Scaling', 'Noch kein Ergebnis': 'No result yet', 'Noch keine Tests.': 'No tests yet.', 'Test abgeschlossen.': 'Test finished.',
    'Es läuft bereits ein Test.': 'A test is already running.', 'Unbekannter Test.': 'Unknown test.',
    'Kein Schreibzugriff auf diesem Laufwerk.': 'No write access on this drive.',
    'Zu wenig freier Speicher für den Test (mind. 2 GB frei nötig).': 'Not enough free space for the test (at least 2 GB needed).',
    'Logs': 'Logs', 'Log-Suche': 'Log search', 'Durchsucht das System-Journal. Neueste Einträge zuerst, maximal 300 Treffer.': 'Searches the system journal. Newest entries first, at most 300 hits.',
    'Alle Dienste': 'All services', 'Alle Meldungen': 'All messages', 'Warnungen & Fehler': 'Warnings & errors', 'Nur Fehler': 'Errors only',
    'Letzte 15 Min.': 'Last 15 min', 'Letzte Stunde': 'Last hour', 'Letzte 24 Std.': 'Last 24 h', 'Letzte 7 Tage': 'Last 7 days',
    'Suchbegriff…': 'Search term…', 'Suchbegriff': 'Search term', 'Schweregrad': 'Severity',
    'Kein systemd-Journal auf diesem System.': 'No systemd journal on this system.', 'Unbekannter Dienst.': 'Unknown service.',
    'Suche dauerte zu lange – Zeitraum verkleinern.': 'Search took too long – narrow the time range.',
    'Speicher aufräumen': 'Clean up storage', 'Gibt Speicherplatz frei, ohne Programme oder deine Daten anzufassen. Größen werden beim Öffnen neu berechnet.': 'Frees up space without touching programs or your data. Sizes are recalculated when the page opens.',
    'Alles aufräumen': 'Clean up everything', 'Nichts zum Aufräumen gefunden.': 'Nothing to clean up.', 'Unbekannter Eintrag.': 'Unknown entry.',
    'System-Logs (Journal)': 'System logs (journal)', 'Paket-Cache (apt)': 'Package cache (apt)', 'Benutzer-Cache (~/.cache)': 'User cache (~/.cache)', 'Papierkorb': 'Trash',
    'Heruntergeladene Installationsdateien. Werden bei Bedarf neu geladen.': 'Downloaded installation files. Downloaded again when needed.',
    'Ungenutzte Images und Build-Cache. Laufende Container bleiben unberührt.': 'Unused images and build cache. Running containers are left alone.',
    'Zwischenspeicher von Programmen (pip, Thumbnails …). Wird automatisch neu aufgebaut.': 'Program caches (pip, thumbnails …). Rebuilt automatically.',
    'Gelöschte Dateien im Papierkorb.': 'Deleted files in the trash.', 'Löscht Einträge älter als 14 Tage.': 'Deletes entries older than 14 days.',
    'docker prune fehlgeschlagen.': 'docker prune failed.', 'Fehlgeschlagen.': 'Failed.',
    'Wegen Inaktivität geschlossen (30 Min.).': 'Closed after 30 minutes of inactivity.', 'Shell beendet.': 'Shell ended.', 'Verbindung getrennt.': 'Connection closed.'
  };

  // ---------------------------------------------------------------------------
  // Strings with numbers / names: [regex, replacement string or function]
  // ---------------------------------------------------------------------------
  const tr = s => (DICT[s] !== undefined ? DICT[s] : translateDynamic(s) ?? s);
  const plural = (n, one, many) => (Number(n) === 1 ? one : many);
  const PATTERNS = [
    [/^vor (\d+) Min\.?$/, (m, n) => `${n} min ago`],
    [/^vor (\d+) Std\.$/, (m, n) => `${n} h ago`],
    [/^vor (\d+) Tagen$/, (m, n) => `${n} days ago`],
    [/^Paketlisten (vor .+)$/, (m, when) => `Package lists ${tr(when)}`],
    [/^(\d+) \/ (\d+) Aktiv$/, '$1 / $2 active'],
    [/^(\d+) \/ (\d+) laufen( · (\d+) Fehler)?$/, (m, a, b, _, e) => `${a} / ${b} running${e ? ` · ${e} ${plural(e, 'error', 'errors')}` : ''}`],
    [/^(\d+) Neustarts · (.+)$/, (m, n, age) => `${n} ${plural(n, 'restart', 'restarts')} · ${age}`],
    [/^(\d+) Kerne • (.+)$/, '$1 cores • $2'],
    [/^(\d+) Kerne · max\. (.+) GHz$/, '$1 cores · max. $2 GHz'],
    [/^(\d+) Kerne$/, '$1 cores'],
    [/^(\d+) Tage$/, '$1 days'],
    [/^(\d+) Std\.$/, '$1 h'],
    [/^(\d+) Tage, Messung jede Minute$/, '$1 days, one reading per minute'],
    [/^(.+) MB von (.+) MB$/, '$1 MB of $2 MB'],
    [/^(.+) · letzte 24 Stunden$/, (m, t) => `${tr(t)} · last 24 hours`],
    [/^(.+) · letzte 7 Tage$/, (m, t) => `${tr(t)} · last 7 days`],
    [/^Stufe (\S+) \/ (\S+)$/, 'Level $1 / $2'],
    [/^Stufe ([\d.]+)$/, 'Level $1'],
    [/^Sensor: (.+)$/, (m, s) => `Sensor: ${tr(s)}`],
    [/^(Download|Upload) (Ø|Spitze)$/, (m, a, b) => `${a} ${b === 'Ø' ? 'avg' : 'peak'}`],
    [/^Wetter-Ort: (.+)$/, 'Weather location: $1'],
    [/^Ort "(.+)" nicht gefunden\.$/, 'Location "$1" not found.'],
    [/^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag), (.+)$/, (m, d, rest) => `${d}, ${rest}`],
    [/^Fehler: ([\s\S]+)$/, (m, e) => `Error: ${tr(e)}`],
    [/^Fehler (\d+)$/, 'Error $1'],
    [/^Fehler \(Code (\d+)\)$/, 'Error (code $1)'],
    [/^Verbindungsfehler: (.+)$/, 'Connection error: $1'],
    [/^Netzwerkfehler: (.+)$/, 'Network error: $1'],
    [/^Update verfügbar: (\d+) neuer Commit$/, (m, n) => `Update available: ${n} new ${plural(n, 'commit', 'commits')}`],
    [/^Update verfügbar \((\d+)\)$/, 'Update available ($1)'],
    [/^Update verfügbar \((.+)\)$/, (m, v) => `Update available (${v === 'neuer' ? 'newer' : v})`],
    [/^Aktuell \((.+)\)$/, 'Up to date ($1)'],
    [/^(\d+) neuer Commit im Repository\.$/, (m, n) => `${n} new ${plural(n, 'commit', 'commits')} in the repository.`],
    [/^(\d+) neuer? Commits? – wird innerhalb einer Minute installiert\.$/, (m, n) => `${n} new ${plural(n, 'commit', 'commits')} – installed within a minute.`],
    [/^Update fehlgeschlagen: (.+)$/, (m, e) => `Update failed: ${tr(e)}`],
    [/^Letzter Check: vor (\d+) Min$/, 'Last check: $1 min ago'],
    [/^Führe '(.+)' aus\.\.\.$/, "Running '$1'..."],
    [/^Befehl '(.+)' abgeschlossen \(Exit: (.+)\)$/, "Command '$1' finished (exit: $2)"],
    [/^Commit (\S+)$/, 'Commit $1'],
    [/^Extern: (.+)$/, 'External: $1'],
    [/^(\d+) Pakete? aktualisierbar( · Paketlisten (vor .+))?$/, (m, n, _, when) => `${n} ${plural(n, 'package', 'packages')} upgradable${when ? ` · package lists ${tr(when)}` : ''}`],
    [/^(\d+) Sicherheit$/, '$1 security'],
    [/^(\d+) weitere$/, '$1 more'],
    [/^\+ (\d+) weitere Pakete$/, '+ $1 more packages'],
    [/^Update & Upgrade installieren \((\d+)\)$/, 'Install update & upgrade ($1)'],
    [/^Log: (.+)$/, 'Log: $1'],
    [/^(.+) (hinzugefügt|entfernt)\.$/, (m, n, v) => `${n} ${v === 'hinzugefügt' ? 'added' : 'removed'}.`],
    [/^Weckruf an (.+) gesendet\.$/, 'Wake-up sent to $1.'],
    [/^(.+) (gestartet|gestoppt|neu gestartet)\.$/, (m, n, v) => `${n} ${{ gestartet: 'started', gestoppt: 'stopped', 'neu gestartet': 'restarted' }[v]}.`],
    [/^(.+) (wird gestartet|wird gestoppt|wird neu gestartet)…$/, (m, n, v) => `${n} ${{ 'wird gestartet': 'is starting', 'wird gestoppt': 'is stopping', 'wird neu gestartet': 'is restarting' }[v]}…`],
    [/^Container '(.+)' nicht gefunden\.$/, "Container '$1' not found."],
    [/^(.+) \((\d+)\) beendet\.$/, '$1 ($2) ended.'],
    [/^(.+) \((\d+)\) reagiert nicht auf SIGTERM\.$/, '$1 ($2) does not respond to SIGTERM.'],
    [/^(.+) \((\d+)\) läuft noch\.$/, '$1 ($2) is still running.'],
    [/^(.+) \((\d+)\) beenden$/, 'End $1 ($2)'],
    [/^Gehört Benutzer '(.+)' – dafür das Web-Terminal mit sudo nutzen\.$/, "Owned by user '$1' – use the web terminal with sudo."],
    [/^(Log anzeigen|Starten|Stoppen|Neu starten): (.+)$/, (m, a, n) => `${tr(a)}: ${n}`],
    [/^(Starten|Stoppen|Neu starten)\? Nochmal klicken$/, (m, a) => `${tr(a)}? Click again`],
    [/^(.+) per Wake-on-LAN starten$/, 'Start $1 via Wake-on-LAN'],
    [/^(.+): steuern & Log ansehen$/, (m, n) => `${tr(n)}: control & view log`],
    [/^(.+) herunterladen$/, 'Download $1'],
    [/^Zu viele Fehlversuche\. Bitte (\d+) s warten\.$/, 'Too many failed attempts. Please wait $1 s.'],
    [/^Neues Passwort: mindestens (\d+) Zeichen\.$/, 'New password: at least $1 characters.'],
    [/^Maximal (\d+) Terminals gleichzeitig\.$/, 'At most $1 terminals at the same time.'],
    [/^Für alle Log-Zeilen: (.+)$/, 'For all log lines: $1'],
    [/^Nur eigene Logs sichtbar\. Für alle: (.+)$/, 'Only your own logs are visible. For all: $1'],
    [/^Gesamt (.+?)( · (\d+) Ordner nicht lesbar)?$/, (m, total, _, n) => `Total ${total}${n ? ` · ${n} ${plural(n, 'folder', 'folders')} not readable` : ''}`],
    [/^([\d.,]+) Dateien durchsucht( \(nach 60 s abgebrochen\))?$/, (m, n, cut) => `${n} files scanned${cut ? ' (stopped after 60 s)' : ''}`],
    [/^(\d+\+?) Einträge( · ([\s\S]+))?$/, (m, n, _, hint) => `${n} entries${hint ? ` · ${tr(hint)}` : ''}`],
    [/^Keine Einträge gefunden\.( ([\s\S]+))?$/, (m, _, hint) => `No entries found.${hint ? ` ${tr(hint)}` : ''}`],
    [/^Laufwerk (.+)$/, 'Drive $1'],
    [/^(.+) MB\/s \(1 Kern\) · (.+) MB\/s \((\d+)\)$/, '$1 MB/s (1 core) · $2 MB/s ($3)'],
    [/^(.+) freigegeben\.$/, '$1 freed.'],
    [/^Räume auf: (.+)…$/, 'Cleaning up: $1…'],
    [/^(.+) frei$/, '$1 free'],
    [/^gesamt (.+)$/, 'total $1'],
    [/^Takt: (.+)$/, 'Clock: $1'],
    [/^CPU-Modus: (.+)$/, (m, g) => `CPU mode: ${tr(g)}`],
    [/^(Sehr gut|Gut|Mäßig|Schwach) \((-?\d+) dBm\)$/, (m, q, d) => `${tr(q)} (${d} dBm)`],
    [/^USB 3 · (.+)$/, 'USB 3 · $1'],
    [/^(.+) · Port (.+)$/, '$1 · port $2'],
    [/^(.+) · Firmware (.+)$/, '$1 · firmware $2'],
    [/^(.+) · Broadcast (.+)$/, '$1 · broadcast $2'],
    [/^Löscht Einträge älter als (\d+) Tage\.$/, 'Deletes entries older than $1 days.'],
    [/^(\d+) aktive Warnungen?$/, (m, n) => `${n} active ${plural(n, 'warning', 'warnings')}`],
    [/^(.+) · (Nur lesen)$/, '$1 · read-only'],
    [/^(.+) Terminals gleichzeitig\.$/, '$1 terminals at once.'],
    [/^Aktuell: (.+) \((.+)\)$/, 'Current: $1 ($2)'],
    [/^Kerne •$/, 'cores •'],
    [/^keine IP-Adresse( · [\s\S]+)$/, 'no IP address$1'],
    [/^(Aktiv|Inaktiv|Fehler) · (.+)$/, (m, st, d) => `${tr(st)} · ${tr(d)}`],
    [/^Verlaufsdiagramm (.+)$/, (m, t) => `History chart ${tr(t)}`]
  ];

  function translateDynamic(s) {
    for (const [re, rep] of PATTERNS) {
      if (re.test(s)) return typeof rep === 'function' ? s.replace(re, rep) : s.replace(re, rep);
    }
    return undefined;
  }

  // Heuristic for the misses list: umlauts or typical German words
  const GERMAN = /[äöüÄÖÜß]|\b(und|der|die|das|nicht|kein|keine|wird|werden|bitte|oder|für|mit|nach|vor|Uhr|jetzt|läuft|gespeichert)\b/i;
  function translateText(raw) {
    const s = raw.trim();
    if (!s || !/[A-Za-zÄÖÜäöüß]{2}/.test(s)) return null;
    const out = DICT[s] !== undefined ? DICT[s] : translateDynamic(s);
    if (out === undefined) {
      if (GERMAN.test(s)) I18N.misses.add(s);
      return null;
    }
    return out === s ? null : raw.replace(s, out);
  }
  I18N.t = s => translateText(s) ?? s;

  // ---------------------------------------------------------------------------
  // DOM walking
  // ---------------------------------------------------------------------------
  // Output that must stay as it is: logs, terminal, file names, user data
  const SKIP = 'script, style, code, .xterm, .terminal-box, .log-results, .file-row, .apt-log, #logs-output, [data-no-i18n]';
  const ATTRS = ['title', 'placeholder', 'aria-label'];

  function translateAttrs(el) {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v) {
        const t = translateText(v);
        if (t !== null) el.setAttribute(a, t);
      }
    }
  }

  function translateNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (!parent || parent.closest(SKIP)) return;
      const t = translateText(node.nodeValue);
      if (t !== null) node.nodeValue = t;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || node.closest(SKIP)) return;
    translateAttrs(node);
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: n => (n.nodeType === Node.ELEMENT_NODE && n.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT)
    });
    let n = walker.nextNode();
    while (n) {
      if (n.nodeType === Node.TEXT_NODE) {
        const t = translateText(n.nodeValue);
        if (t !== null) n.nodeValue = t;
      } else {
        translateAttrs(n);
      }
      n = walker.nextNode();
    }
  }

  function start() {
    const t = translateText(document.title);
    if (t !== null) document.title = t;
    translateNode(document.body);
    new MutationObserver(mutations => {
      for (const m of mutations) {
        if (m.type === 'characterData') translateNode(m.target);
        else if (m.type === 'attributes') { if (!m.target.closest(SKIP)) translateAttrs(m.target); }
        else m.addedNodes.forEach(translateNode);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
