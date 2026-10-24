# Sicherheit

## Sicherheitslücke melden

Bitte **nicht** als öffentliches Issue. Stattdessen über GitHub: **Security → Report a vulnerability** in diesem Repository (private Meldung). Hilfreich sind betroffene Version (Commit), Schritte zum Nachvollziehen und die mögliche Auswirkung.

## Wofür das Dashboard gedacht ist

HostCore läuft im **Heimnetz oder hinter einem VPN** (z. B. NetBird, WireGuard, Tailscale). Es ist nicht dafür gebaut, direkt ins Internet gestellt zu werden – es bietet unter anderem ein Web-Terminal, Dienst- und Container-Steuerung und Datei-Zugriff. Wer es von außen erreichen will, sollte ein VPN oder einen Reverse-Proxy mit zusätzlicher Anmeldung und HTTPS davorsetzen.

## Sicherheitsmodell in Kürze

- **Anmeldung:** Passwörter als scrypt-Hash in `auth.json` (nur für den Dashboard-Benutzer lesbar, nie im Repo), signierte Sitzungs-Cookies, Sperre nach Fehlversuchen, CSRF-Schutz für alle ändernden Anfragen.
- **Rechte:** Gruppen mit Einzelrechten; der Server prüft jede Anfrage gegen eine zentrale Tabelle (`backend/access.py`). Neue ändernde Endpunkte sind ohne Eintrag nur für Administratoren.
- **Root:** Die App läuft als normaler Benutzer. Was als root laufen muss (Updates, apt, Firewall), kommt aus einer root-eigenen Kopie des Repos unter `/var/lib/pi-dashboard/src`; sudo erlaubt nur genau diese Skripte und Units.
- **Web-Terminal / Container-Konsole:** nur mit dem Recht „Web-Terminal“, Passwort wird erneut abgefragt (Einmal-Ticket), höchstens 3 Sitzungen, Abbruch nach 30 Minuten Leerlauf, jede Sitzung im Aktivitätsprotokoll.
- **Netzwerk-Prüfungen** (Web-Dienste, Ping, DNS …) sind auf Heimnetz und VPN beschränkt; das Dashboard prüft keine Adressen im Internet.
- **App-Store:** schreibt nur Compose-Dateien in `~/picore-apps`, bindet nie den Docker-Socket oder Systemordner ein und weist auf erweiterte Rechte (privileged, Host-Netzwerk, Capabilities, Geräte) vor der Installation hin.
- **Öffentliche Statusseite:** standardmäßig aus; zeigt nur selbst vergebene Namen und Zustände – keine Adressen, IPs oder Fehlermeldungen.

## Automatische Updates

Der Auto-Updater prüft **jede Minute** GitHub und führt dabei auch Skripte als root aus. Neue Installationen folgen dem **stabilen Kanal**: nur veröffentlichte Releases (Tags `vX.Y.Z`), nie zurück auf ein älteres. Auf dem Kanal „main“ (Einstellungen → Auto-Update) kommt jeder Commit des Branches automatisch. Wer Updates lieber selbst freigibt, arbeitet mit einem **eigenen Fork** (siehe README) oder schaltet den Timer ab:

```bash
sudo systemctl disable --now pi-dashboard-update.timer
```
