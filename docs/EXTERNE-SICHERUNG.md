# Externe Sicherung

Die externe Sicherung kopiert Daten dieses Servers verschlüsselt an einen Ort außerhalb: Google Drive, Dropbox, Nextcloud/WebDAV, S3-Speicher oder ein USB-Laufwerk. Zu finden unter **System → Speicher & Backups → Externe Sicherung**.

Inhalt: [Wie es funktioniert](#wie-es-funktioniert) · [Einrichten](#einrichten) · [Google Drive: eigene Client-ID](#google-drive-eigene-client-id) · [Andere Ziele](#andere-ziele) · [Mehrere Pis](#mehrere-pis) · [Im Alltag](#im-alltag) · [Wiederherstellen](#wiederherstellen) · [Wenn der Server weg ist](#wenn-der-server-weg-ist) · [Fehlerbehebung](#fehlerbehebung) · [Rückbau](#rückbau)

---

## Wie es funktioniert

- **restic** macht die Sicherung: Es verschlüsselt alles auf dem Server, bevor es ihn verlässt. Im Ziel liegen nur unlesbare Datenpakete. Ab dem zweiten Lauf wird nur übertragen, was sich geändert hat.
- **rclone** verbindet restic mit Google Drive, Dropbox und WebDAV. Für S3 und USB-Laufwerke braucht es rclone nicht.
- Die Sicherung läuft als **Aufgabe mit Root-Rechten**, damit sie alle Dateien lesen kann. Welche Ordner das sind, legt der Server selbst fest. Eigene Ordner gehen nur unter `/home`, `/srv`, `/opt`, `/mnt`, `/media`, `/data` und `/var/www`. Passwort-Hashes (`/etc/shadow`) und SSH-Host-Schlüssel werden nie gesichert.
- Jede Sicherung hat ein **eigenes Passwort**. **Ohne dieses Passwort lässt sich nichts wiederherstellen, auch nicht von dir.**

## Einrichten

1. **restic und rclone installieren:** Fehlen sie, steht oben im Abschnitt der Knopf *restic und rclone installieren*. Der Fortschritt erscheint unter *Pakete*.
2. **Sicherung einrichten** klicken und ausfüllen:
   - **Name**, z. B. „Pi nach Google Drive“.
   - **Ziel** und die Angaben dazu (siehe unten).
   - **Was sichern:**

     | Auswahl | Was dazugehört |
     |---|---|
     | Docker-Volumes & Stacks | `/var/lib/docker/volumes` und die Ordner der Compose-Stacks |
     | Kubernetes (k3s) | Volumes der Pods (local-path), Cluster-Datenbank, Token, Manifeste |
     | Dashboard-Daten | Einstellungen, Benutzer, Verlauf, Doku, Wiki und die Datenbank-Dumps |
     | Systemeinstellungen | `/etc`, Cron-Jobs, `/usr/local` |
     | Logs | `/var/log` mit dem Journal |
     | Benutzerordner | `/home` |

     Dazu kommen **eigene Ordner** und **Ausnahmen** (je Zeile eine, z. B. `*.tmp` oder `/var/log/journal`).
   - **Zeitplan:** Wochentage und Stunde. Ohne Zeitplan läuft die Sicherung nur von Hand.
   - **Aufbewahrung:** wie viele tägliche, wöchentliche und monatliche Stände bleiben, z. B. 7 / 4 / 6. Ältere Stände räumt die Sicherung nach jedem Lauf weg.
   - **Passwort:** leer lassen, dann wird eins erzeugt. Ein eigenes Passwort nur eintragen, wenn du ein schon vorhandenes Repository weiterbenutzen willst.
3. **Speichern**, dann sofort **Passwort anzeigen** und das Passwort in einen Passwort-Manager oder auf Papier übertragen, also an einen Ort außerhalb dieses Servers.
4. **Jetzt sichern** für den ersten Lauf.

> Datenbanken in Containern sichert man am zuverlässigsten über ihre Dumps (*Container → Datenbanken*). Die Dumps gehören zu den *Dashboard-Daten*. Eine laufende Datenbank direkt aus dem Volume zu kopieren, kann einen halb geschriebenen Stand erwischen.

## Google Drive: eigene Client-ID

Google Drive funktioniert ohne eigene Client-ID, aber dann nutzt die Sicherung die Kennung von rclone, die sich alle rclone-Nutzer weltweit teilen. Google drosselt sie stark. Die Folge sind Uploads mit wenigen hundert KB/s, Abbrüche und Meldungen wie *„Google Drive drosselt die Anfragen“*. Mit einer eigenen Client-ID hast du ein eigenes Kontingent. Sie kostet nichts und ist in etwa 10 Minuten angelegt.

Alle Schritte mit **dem Google-Konto, in dessen Drive gesichert wird**:

1. **Projekt anlegen:** Auf <https://console.cloud.google.com> oben links die Projektauswahl öffnen, dann **Neues Projekt**, z. B. *HostCore Backup*. Danach prüfen, dass das neue Projekt oben ausgewählt ist.
2. **Drive API einschalten:** *APIs & Dienste → Bibliothek* öffnen, nach **Google Drive API** suchen und **Aktivieren**.
3. **Anmeldeseite einrichten:** *Google Auth Platform* bzw. *OAuth-Zustimmungsbildschirm* öffnen und **Jetzt starten** klicken:
   - App-Name eintragen, z. B. *HostCore Backup*, und die Support-E-Mail-Adresse (deine).
   - Zielgruppe **Extern** wählen.
   - Unter **Kontaktdaten des Entwicklers** noch einmal deine E-Mail-Adresse eintragen.
4. **App veröffentlichen:** Unter **Zielgruppe** auf **App veröffentlichen** klicken, damit der Status **In Produktion** ist.
   - *Der Knopf ist grau?* Dann fehlt unter **Branding** ein Pflichtfeld: App-Name, Support-E-Mail oder Kontaktdaten des Entwicklers.
   - *Warum veröffentlichen?* Im Status „Testen“ dürfen nur eingetragene Testnutzer zugreifen. Sonst kommt *„Zugriff blockiert … Fehler 403: access_denied“*. Außerdem läuft die Anmeldung dann nach 7 Tagen ab. Für den eigenen Gebrauch prüft Google beim Veröffentlichen nichts.
5. **Client-ID erstellen:** *APIs & Dienste → Anmeldedaten → Anmeldedaten erstellen → OAuth-Client-ID*, Typ **Desktop-App**. Du bekommst:
   - die **Client-ID**, endet auf `.apps.googleusercontent.com`
   - das **Client-Secret**, beginnt mit `GOCSPX-`
6. **Im Dashboard eintragen:** Die Sicherung *Bearbeiten*, **Eigene Google-Client-ID (empfohlen)** aufklappen und beide Werte eintragen.
7. **Verbinden:**
   - **Mit Google Drive verbinden** klicken. Es öffnet sich die Google-Anmeldung.
   - Google zeigt *„Google hat diese App nicht überprüft“*. Das ist deine eigene App: **Erweitert → Weiter zu HostCore Backup (unsicher)** und den Zugriff erlauben.
   - Danach landet der Browser auf einer Seite, die nicht lädt (`http://127.0.0.1:53682/?state=…&code=…`). Das ist richtig so. Die **ganze Adresse** aus der Adresszeile kopieren, im Dashboard einfügen und **Anmeldung abschließen** klicken.
8. **Speichern.** Auf der Karte steht bei *Google-Kennung* jetzt „eigene Client-ID“.

Wird die Client-ID später geändert, muss man neu verbinden, weil eine Anmeldung immer zu der Client-ID gehört, mit der sie gemacht wurde.

Im Drive landet die Sicherung im Ordner `HostCore/<servername>`, außer im Formular ist ein anderer eingetragen. Die Dateien darin nicht von Hand ändern oder löschen.

## Andere Ziele

| Ziel | Was man braucht |
|---|---|
| **Dropbox** | *Mit Dropbox verbinden*, sonst wie bei Google Drive (Adresse nach der Anmeldung einfügen). Eine eigene Client-ID ist hier nicht nötig. |
| **Nextcloud / WebDAV** | Adresse `https://<cloud>/remote.php/dav/files/<benutzer>`, Benutzername und ein **App-Passwort**. Das legt man in Nextcloud unter *Einstellungen → Sicherheit* an, nicht das normale Passwort nehmen. |
| **S3-kompatibel** (AWS, Backblaze B2, Hetzner, Wasabi, MinIO …) | Endpunkt `https://…` ohne Pfad, Bucket (muss schon existieren), Key-ID und Secret. Am besten einen Schlüssel anlegen, der nur diesen Bucket darf. |
| **USB-Laufwerk / Ordner** | Ein eingehängter Ordner unter `/mnt`, `/media`, `/srv` oder `/data`, z. B. `/mnt/usb/hostcore`. Gegen Ransomware hilft ein Laufwerk, das nicht ständig angeschlossen ist. |

## Mehrere Pis

Sind weitere Server als Agents verbunden (*Einstellungen → Verbindungen → Remote-Server*), zeigt das Haupt-Dashboard unter *Externe Sicherung* die Sicherungen **aller** Server, eine Gruppe pro Server. Dort sieht man jeweils Status und Fortschritt und kann **Jetzt sichern** und **Prüfen** starten.

**Eine Sicherung auf andere Pis übertragen:**
1. Die Sicherung auf dem Haupt-Dashboard einrichten und einmal testen.
2. Bei ihr auf **Auf andere Server übertragen** klicken, die Server ankreuzen und **Übertragen** klicken.
3. Jeder gewählte Server bekommt dieselbe Sicherung mit demselben Ziel, derselben Anmeldung (bei Google Drive auch derselben Client-ID), derselben Auswahl, demselben Zeitplan und **demselben Passwort**. Du musst dir also nur eins merken.
4. Jeder Server sichert in seinen **eigenen Ordner** `HostCore/<servername>`, die Stände kommen sich nicht in die Quere.
5. Fehlen auf einem Server noch restic oder rclone, sagt die Meldung das. Dann dort über **Dort öffnen** den Knopf *restic und rclone installieren* drücken.

Überträgst du später erneut, wird die Sicherung mit gleichem Namen auf dem anderen Server **aktualisiert**, nicht doppelt angelegt. So kommen z. B. eine neue Client-ID oder ein geänderter Zeitplan auf alle Pis. Bearbeiten, Wiederherstellen und Entfernen macht man auf dem jeweiligen Server über **Dort öffnen**.

Tipp: Die Zeitpläne der Pis um eine Stunde versetzen, z. B. 2:00, 3:00 und 4:00. Dann teilen sie sich nicht gleichzeitig die Leitung und das Google-Kontingent.

## Im Alltag

- **Fortschritt:** Während eine Sicherung läuft, zeigt ihre Karte die Phase, die Prozente, Dateien und Datenmenge, die tatsächlich hochgeladene Menge mit Geschwindigkeit, die Laufzeit und die Restzeit. Das gilt für Sicherungen von Hand und nach Zeitplan. Das Protokoll steht in der Aufgabenleiste unten.
- **Die erste Sicherung** überträgt alles und kann bei vielen GB Stunden dauern. Ein Abbruch schadet nicht, denn schon Hochgeladenes erkennt der nächste Lauf wieder.
- **Warnungen:** Fehlgeschlagene oder überfällige Sicherungen erscheinen in der Statusübersicht und in den E-Mail-Berichten.
- **Prüfen:** Der Knopf *Prüfen* liest die Struktur und 2 % der Daten aus dem Ziel zurück. Sinnvoll etwa einmal im Monat.
- **Stände & Wiederherstellen** listet alle gespeicherten Stände.

## Wiederherstellen

Der Server läuft noch und es fehlen Dateien:

1. **Stände & Wiederherstellen** öffnen, beim gewünschten Stand **Wiederherstellen** klicken.
2. Optional einen Pfad angeben, z. B. `/var/lib/docker/volumes/nextcloud_data`. Leer lassen heißt: alles.
3. Die Dateien landen unter `/var/lib/pi-dashboard/restore/<zeitpunkt>-<stand>/` mit dem vollständigen ursprünglichen Pfad darunter. **Überschrieben wird nichts.**
4. Im Terminal an ihren Platz kopieren, z. B.:
   ```bash
   sudo ls /var/lib/pi-dashboard/restore/
   # Container vorher stoppen, dann:
   sudo cp -a /var/lib/pi-dashboard/restore/<ordner>/var/lib/docker/volumes/nextcloud_data/. /var/lib/docker/volumes/nextcloud_data/
   ```
5. Danach den Ordner wieder löschen: `sudo rm -rf /var/lib/pi-dashboard/restore/<ordner>`

## Wenn der Server weg ist

Ist der Server defekt oder gestohlen, kommst du auch ohne HostCore an die Daten. Du brauchst nur das **Passwort der Sicherung** und den Zugang zum Ziel. Auf einem beliebigen Linux-Rechner (oder einem neuen Pi):

```bash
sudo apt install restic rclone

# Nur für Google Drive / Dropbox / WebDAV: eine Verbindung namens "ziel" anlegen
rclone config
#   n (neue Verbindung) → Name: ziel → Typ: drive (bzw. dropbox, webdav)
#   client_id / client_secret: deine eigene Client-ID (oder leer lassen)
#   scope: 1 (voller Zugriff) → bei "Use web browser" auf einem Rechner ohne Bildschirm n wählen und den
#   angezeigten Befehl  rclone authorize "drive" …  auf einem Rechner mit Browser ausführen

# Stände ansehen (fragt nach dem Passwort der Sicherung)
restic -r rclone:ziel:HostCore/<servername> snapshots
# Alles in einen Ordner zurückholen
restic -r rclone:ziel:HostCore/<servername> restore latest --target ~/wiederherstellung
```

Andere Ziele: `-r s3:https://<endpunkt>/<bucket>/<ordner>` (vorher `export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…`) oder `-r /mnt/usb/hostcore`.

Danach HostCore neu installieren (`install.sh`) und die *Dashboard-Daten* aus der Wiederherstellung zurückkopieren. Darin stecken Einstellungen, Benutzer, Doku und Datenbank-Dumps.

## Fehlerbehebung

| Meldung | Ursache und Abhilfe |
|---|---|
| *Google Drive drosselt die Anfragen … nutzt noch die Kennung von rclone* | Keine eigene Client-ID: [einrichten](#google-drive-eigene-client-id). |
| *Google Drive drosselt die Anfragen deines eigenen Google-Projekts* | Kurzzeitig zu viele Anfragen. Die Sicherung wartet von selbst eine Minute und versucht es noch einmal (insgesamt drei Versuche). Sonst später erneut starten. |
| *Zugriff blockiert … Fehler 403: access_denied* (bei der Anmeldung) | Die Google-App steht noch auf „Testen“: [veröffentlichen](#google-drive-eigene-client-id) (Schritt 4). |
| *Die Anmeldung beim Anbieter ist abgelaufen oder wurde widerrufen* | Neu verbinden. Bei Google Drive prüfen, ob die App „In Produktion“ ist, denn im Status „Testen“ läuft die Anmeldung nach 7 Tagen ab. |
| *rclone hat nicht rechtzeitig geantwortet* | Der Anbieter antwortet nicht oder drosselt. Bei Google Drive eine eigene Client-ID verwenden. |
| *Das Repository gibt es schon, aber das Passwort passt nicht* | Im Zielordner liegt eine Sicherung mit einem anderen Passwort. Das richtige Passwort eintragen oder einen anderen Ordner im Ziel wählen. |
| *Einige Dateien ließen sich nicht lesen* | Dateien, die während der Sicherung verschwanden oder gesperrt waren. Der Stand ist trotzdem gespeichert. |
| Sehr langsam | Auf der Karte *hochgeladen … · … /s* ansehen und im Protokoll nach Zeilen mit ⚠ suchen. Bei Google Drive fast immer die gemeinsame rclone-Kennung. |

## Rückbau

So entfernst du die externe Sicherung vollständig. Die Schritte sind unabhängig voneinander, mach nur die, die du brauchst.

**1. Sicherung im Dashboard entfernen** (bei mehreren Pis auf jedem Server, über *Dort öffnen*)
*Externe Sicherung → Entfernen* (zweimal klicken). Damit sind Zeitplan, Zugangsdaten und Passwort vom Server gelöscht. **Die Daten im Ziel bleiben erhalten.** Willst du sie später noch wiederherstellen, das Passwort vorher notieren.

**2. Daten im Ziel löschen**

| Ziel | So geht's |
|---|---|
| Google Drive | Im Drive den Ordner `HostCore/<servername>` löschen, danach den **Papierkorb leeren**. Sonst zählt er weiter gegen den Speicherplatz. |
| Dropbox | Den Ordner `HostCore/<servername>` löschen. |
| Nextcloud | Den Ordner löschen und den Papierkorb in Nextcloud leeren. |
| S3 | Den Ordner im Bucket löschen (oder den ganzen Bucket) und den Zugangsschlüssel beim Anbieter widerrufen. |
| USB | `sudo rm -rf /mnt/usb/hostcore` (bzw. den eingetragenen Ordner) |

**3. Zugriff beim Anbieter widerrufen**

- **Google:** Auf <https://myaccount.google.com/connections> (*Drittanbieter-Apps und -Dienste*) die App, z. B. *HostCore Backup*, auswählen und den **Zugriff entfernen**. Ohne eigene Client-ID heißt sie *rclone*.
- **Google-Projekt löschen** (falls eine eigene Client-ID angelegt wurde): <https://console.cloud.google.com> → Projekt auswählen → *IAM & Verwaltung → Einstellungen → Herunterfahren*. Google löscht das Projekt nach 30 Tagen endgültig.
- **Dropbox:** <https://www.dropbox.com/account/connected_apps> → *rclone* entfernen.
- **Nextcloud:** *Einstellungen → Sicherheit* → das App-Passwort löschen.

**4. Reste auf dem Server löschen**

```bash
sudo rm -rf /var/lib/pi-dashboard/restic-cache      # Zwischenspeicher von restic (kann einige hundert MB groß sein)
sudo rm -rf /var/lib/pi-dashboard/offsite-progress  # Fortschrittsanzeige
sudo rm -rf /var/lib/pi-dashboard/restore           # wiederhergestellte Dateien, vorher prüfen, ob noch etwas gebraucht wird
rm -rf ~/pi-dashboard/offsite                       # Übergabedateien laufender Aufgaben (meist leer)
```

**5. restic und rclone deinstallieren** (nur wenn sie sonst niemand braucht, z. B. die Backup-Überwachung)

```bash
sudo apt remove restic rclone
```

Die Funktion selbst bleibt im Dashboard sichtbar, tut aber nichts, solange keine Sicherung eingerichtet ist.
