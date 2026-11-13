# Contracts Manager auf der Synology installieren – Schritt für Schritt

[← Zurück zum README](../README.md) · Kurzfassung auf Englisch: [Installation on a Synology NAS](installation-synology.md)

Diese Anleitung führt dich von einer frischen Synology bis zur laufenden
App mit Anmeldung, HTTPS und täglicher Sicherung. Alles läuft über
**Docker Compose**: Eine Datei beschreibt die Container, eine zweite
enthält deine Einstellungen. Es wird nichts installiert, gebaut oder
geklont. Jeder Schritt sagt, was du tust, und wie du prüfst, ob er
geklappt hat.

Zeitbedarf: etwa 40 Minuten.

---

## Überblick

Am Ende sieht es so aus:

```
/volume1/docker/contracts-manager/
├── docker-compose.yml     beschreibt die drei Container (von GitHub)
├── .env                   deine Einstellungen und Passwörter
├── db/                    PostgreSQL-Datenbank
├── media/                 Logos und Vertragsdokumente
└── backups/               tägliche Sicherungen
```

Auf der Synology laufen drei Container:

| Compose-Dienst | Container-Name | Aufgabe |
|---|---|---|
| `db` | `contracts-manager-db` | PostgreSQL 17 |
| `app` | `contracts-manager-app` | die Django-Anwendung, erreichbar auf Port 8000 der Synology |
| `worker` | `contracts-manager-worker` | Hintergrunddienst für Erinnerungen und den KI-Assistenten |

In Befehlen mit `docker compose` verwendest du den kurzen Dienstnamen
(z. B. `app`), im Container Manager von DSM und bei `docker exec` siehst
du den langen Container-Namen.

Davor sitzt der Reverse Proxy der Synology, der HTTPS übernimmt.

**Die App kommt als fertiges Docker-Image.** `app` und `worker` laufen mit
dem Image `ghcr.io/brainx9/contracts-manager`, das GitHub bei jeder neuen
Version baut (für Intel/AMD und für ARM). Auf der Synology wird nichts
gebaut und kein Programmcode installiert, es wird nur das Image
heruntergeladen.

| Image | Woher | Inhalt |
|---|---|---|
| `ghcr.io/brainx9/contracts-manager` | GitHub (`ghcr.io`) | die App, auch für den `worker` |
| `pgvector/pgvector:pg17` | Docker Hub | PostgreSQL 17 |

Das App-Image hat zwei Arten von Namen (Tags): `latest` ist immer die
neueste Version, `0.2.0` o. Ä. eine feste. Standard ist `latest`;
wie du eine Version festhältst, steht unter „Updates einspielen“.

---

## Was du brauchst

- [ ] Synology mit **DSM 7.2** oder neuer (Systemsteuerung › Info-Center)
- [ ] Ein Benutzerkonto in der Gruppe **administrators**
- [ ] Die IP-Adresse der Synology im Heimnetz, z. B. `192.168.1.10`
- [ ] Einen Rechner mit Terminal (Mac: Programm „Terminal“, Windows: „PowerShell“)
- [ ] Eine Internetverbindung der Synology (lädt die Images von GitHub und Docker Hub)
- [ ] Optional für HTTPS: eine Adresse für die App, z. B. über Synology-DDNS (`deinname.synology.me`)

In der Anleitung steht `192.168.1.10` für die IP deiner Synology und
`admin` für deinen Benutzernamen. Ersetze beides durch deine Werte.

---

## Schritt 1: Container Manager installieren

1. DSM im Browser öffnen: `http://192.168.1.10:5000`
2. **Paket-Zentrum** öffnen, nach **Container Manager** suchen, **Installieren**.
3. Beim Installieren das Volume wählen (meist `volume1`).

DSM legt dabei den gemeinsamen Ordner **`docker`** an. Dort liegt
später der Ordner der App.

**Prüfen:** Im Hauptmenü gibt es jetzt „Container Manager“, und unter
Systemsteuerung › Gemeinsamer Ordner steht `docker`.

---

## Schritt 2: Speicherort festlegen (optional verschlüsseln)

Standard ist der Ordner `/volume1/docker/contracts-manager`. Den legst du
in Schritt 4 an, hier ist nichts zu tun.

Die App enthält private Vertragsdaten. Wenn du sie gegen Diebstahl der
Synology schützen willst, lege stattdessen einen **verschlüsselten
gemeinsamen Ordner** an und verwende ihn statt `docker`:

1. **Systemsteuerung › Gemeinsamer Ordner › Erstellen › Gemeinsamen Ordner erstellen**
2. Name: `contracts`, Ort: `Volume 1`
3. „Papierkorb aktivieren“ darf aus bleiben. „Im Netzwerk verbergen“ empfehlenswert an.
4. Auf der nächsten Seite **„Diesen gemeinsamen Ordner verschlüsseln“** aktivieren
   und ein langes Passwort vergeben.
5. DSM bietet am Ende an, eine **Schlüsseldatei** (`.key`) herunterzuladen:
   unbedingt herunterladen und sicher aufbewahren (z. B. im Passwortmanager).
   Ohne Passwort oder Schlüsseldatei sind alle Verträge verloren.
6. Berechtigungen: deinem Admin-Benutzer **Lesen/Schreiben** geben, allen anderen **Kein Zugriff**.

Der Projektordner heißt dann `/volume1/contracts/contracts-manager`.
**Ersetze in der ganzen Anleitung `/volume1/docker` durch `/volume1/contracts`.**
Beachte außerdem Schritt 11: Nach einem Neustart der Synology ist ein
verschlüsselter Ordner gesperrt.

---

## Schritt 3: SSH einschalten und verbinden

1. **Systemsteuerung › Terminal & SNMP › Terminal**
2. Haken bei **SSH-Dienst aktivieren**, Port `22` lassen, **Übernehmen**.
3. Auf deinem Rechner das Terminal öffnen und verbinden:

```bash
ssh admin@192.168.1.10
```

Beim ersten Mal die Frage nach dem Fingerabdruck mit `yes` bestätigen,
dann dein DSM-Passwort eingeben (beim Tippen erscheinen keine Zeichen, das ist normal).

4. Prüfen, ob Docker da ist:

```bash
sudo docker --version
```

```bash
sudo docker compose version
```

`sudo` fragt einmal nach deinem DSM-Passwort. Beide Befehle sollten
eine Versionsnummer ausgeben. Falls der zweite Befehl einen Fehler meldet,
probiere `sudo docker-compose version` und verwende in dieser Anleitung
überall `docker-compose` statt `docker compose`.

> Alle folgenden Befehle tippst du in diesem SSH-Fenster.

---

## Schritt 4: Ordner anlegen und Dateien holen

Zuerst den Projektordner mit den drei Unterordnern anlegen:

```bash
sudo mkdir -p /volume1/docker/contracts-manager/db /volume1/docker/contracts-manager/media /volume1/docker/contracts-manager/backups
```

Den Projektordner dir geben, damit du die Dateien darin ohne `sudo`
anlegen und ändern kannst (ersetze `admin` durch deinen Benutzernamen):

```bash
sudo chown admin /volume1/docker/contracts-manager
```

```bash
cd /volume1/docker/contracts-manager
```

Jetzt die zwei Dateien von GitHub holen:

```bash
curl -fsSLO https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml
```

```bash
curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/.env.example -o .env
```

```bash
chmod 600 .env
```

Die zweite Datei ist die Vorlage für deine Einstellungen und heißt
gleich `.env`.

> **Hinweis:** `main` lädt die aktuelle Fassung der Dateien. Willst du genau
> die Dateien einer bestimmten Version, schreibe statt `main` ihren Tag in
> die Adresse, z. B. `v0.2.0`.

Die Container `app` und `worker` laufen nicht als root, sondern als
Benutzer und Gruppe `1000`. Dieser Benutzer muss in `media` schreiben
dürfen (dort landen Logos und Dokumente):

```bash
sudo chown -R 1000:1000 /volume1/docker/contracts-manager/media
```

(Andere Werte lassen sich in `.env` mit `APP_UID` und `APP_GID` einstellen.
Dann den `chown`-Befehl entsprechend anpassen.)

**Prüfen:**

```bash
ls -la /volume1/docker/contracts-manager
```

Du solltest `docker-compose.yml`, `.env` und die Ordner `db`, `media` und
`backups` sehen.

> Alle `docker compose`-Befehle führst du im Projektordner aus. Wenn du
> in einem neuen SSH-Fenster bist: `cd /volume1/docker/contracts-manager`.

---

## Schritt 5: Einstellungen in `.env` eintragen

### 5.1 Geheimnisse automatisch eintragen

Diese Befehle erzeugen einen zufälligen `SECRET_KEY` und ein zufälliges
Datenbank-Passwort (nur Buchstaben und Ziffern). Im Projektordner einfach
nacheinander kopieren:

```bash
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env
```

```bash
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
```

Auch dein **erster Administrator** kommt aus der `.env`. Name und E-Mail
trägst du selbst ein (ersetze die Werte), das Passwort erzeugt der dritte
Befehl zufällig:

```bash
sed -i "s|^# ADMIN_USERNAME=.*|ADMIN_USERNAME=admin|; s|^# ADMIN_EMAIL=.*|ADMIN_EMAIL=du@example.com|" .env
```

```bash
sed -i "s|^# ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')|" .env
```

### 5.2 Adresse der App eintragen

Jetzt entscheidest du, wie du die App aufrufst. Öffne die Datei:

```bash
vi .env
```

Kurzanleitung für `vi`: Mit den Pfeiltasten zur Zeile gehen, `i` drücken
(Bearbeiten), Text ändern, dann `Esc` drücken und `:wq` + Enter tippen
(speichern und beenden). Ohne Speichern beenden: `Esc`, `:q!` + Enter.

Ändere die Zeilen `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `USE_HTTPS`
und `APP_BASE_URL` (Adresse für Links in E-Mails) je nach Variante:

**Variante A – nur zum ersten Test im Heimnetz, ohne HTTPS**

```
ALLOWED_HOSTS=192.168.1.10
CSRF_TRUSTED_ORIGINS=http://192.168.1.10:8000
USE_HTTPS=False
APP_BASE_URL=http://192.168.1.10:8000
```

**Variante B – dauerhaft über den Reverse Proxy mit HTTPS (empfohlen)**

```
ALLOWED_HOSTS=vertraege.deinname.synology.me
CSRF_TRUSTED_ORIGINS=https://vertraege.deinname.synology.me
USE_HTTPS=True
APP_BASE_URL=https://vertraege.deinname.synology.me
```

Regeln, die oft schiefgehen:

- `ALLOWED_HOSTS`: nur der Hostname, **ohne** `http://`, ohne Port, ohne `/`
- `CSRF_TRUSTED_ORIGINS`: **mit** `http://` bzw. `https://`, ohne `/` am Ende
- Mehrere Einträge mit Komma trennen, ohne Leerzeichen
- Bei Variante A darf `USE_HTTPS` nicht `True` sein, sonst klappt die Anmeldung nicht

Du kannst mit Variante A anfangen und später auf B umstellen (siehe Schritt 9).

`DEBUG=False` und `APP_PORT=8000` bleiben so. Nur wenn Port 8000 auf der
Synology schon belegt ist, `APP_PORT` auf einen anderen Wert setzen
(z. B. `8010`) und unten überall `8000` entsprechend ersetzen.

Die Zeile `DATA_PATH` bleibt auskommentiert: Datenbank und Uploads liegen
in den Ordnern `db` und `media` neben der `docker-compose.yml`.

E-Mail (für Erinnerungen und Passwort-Reset), Single Sign-on, Paperless
und den KI-Assistenten kannst du später ergänzen, siehe
[Konfiguration](configuration.md). Für die Installation sind sie nicht nötig.

### 5.3 Kontrolle

```bash
grep -v '^#' .env | grep -v '^$'
```

Es dürfen **kein** `CHANGE_ME` und **keine** Zeile `COMPOSE_FILE=` oder
`DATA_PATH=` vorkommen. Die Zeilen `ADMIN_USERNAME`, `ADMIN_PASSWORD` und
`ADMIN_EMAIL` müssen dagegen vorkommen. Das Passwort steht hier im
Klartext: Merke es dir für die erste Anmeldung (`grep ADMIN_PASSWORD .env`).

---

## Schritt 6: Starten

```bash
cd /volume1/docker/contracts-manager && sudo docker compose up -d
```

Beim ersten Mal lädt die Synology die fertigen Images herunter (Datenbank
und App), gebaut wird nichts. Das dauert ein paar Minuten. Am Ende steht
`Started` bzw. `Healthy`.

**Prüfen** (nach ca. einer Minute):

```bash
sudo docker compose ps
```

`db` und `app` müssen in der Spalte STATUS `(healthy)` zeigen, `worker`
startet danach und zeigt `Up`. In DSM siehst du dieselben drei Container
im **Container Manager** unter „Container“ (`contracts-manager-…`).

```bash
curl http://localhost:8000/health/
```

Antwort: `{"status": "ok"}`

Wenn nicht: Logs ansehen und im Abschnitt [Fehlerbehebung](#fehlerbehebung) nachschlagen.

```bash
sudo docker compose logs app
```

```bash
sudo docker compose logs db
```

```bash
sudo docker compose logs worker
```

**Welche Version läuft?** Die geladenen Images und ihre Tags zeigt:

```bash
sudo docker compose images
```

In der Spalte `TAG` steht bei `app` und `worker` `latest` (oder die
Version, die du in `.env` festgelegt hast). Die Versionsnummer der App
selbst siehst du nach der Anmeldung unter **Verwaltung › System**.

---

## Schritt 7: Als Administrator anmelden

Die App hat kein voreingestelltes Konto. Beim ersten Start legt sie den
Administrator aus `ADMIN_USERNAME` und `ADMIN_PASSWORD` in der `.env` an
(Schritt 5), aber nur, wenn noch keiner existiert. Prüfen:

```bash
sudo docker compose logs app | grep -i administrator
```

Die Ausgabe `Administrator '…' wurde angelegt.` zeigt, dass es geklappt
hat. Das ist dein Login für die App, unabhängig von DSM. Das Passwort
liest du so aus:

```bash
grep ADMIN_PASSWORD .env
```

Nach der ersten Anmeldung:

1. Passwort in der App ändern (Benutzermenü › Einstellungen › Passwort).
2. Die Zeile mit dem Passwort aus der `.env` löschen:

```bash
sed -i '/^ADMIN_PASSWORD=/d' .env
```

Weitere Benutzer legst du später in der App unter **Verwaltung** an.

**Steht stattdessen ein Hinweis, dass das Passwort ungültig ist** (zu kurz,
zu häufig, nur Ziffern), korrigiere es in der `.env` und starte mit
`sudo docker compose up -d` neu. Ohne die Variablen legst du das Konto von
Hand an:

```bash
sudo docker compose exec app python manage.py createsuperuser
```

---

## Schritt 8: Erster Test im Browser

- Variante A: `http://192.168.1.10:8000/`
- Variante B: erst Schritt 9 erledigen, dann `https://vertraege.deinname.synology.me/`

Mit dem Benutzer aus Schritt 7 anmelden. Du landest auf der Übersicht mit
den Summen pro Monat und Jahr. Das Django-Admin erreichst du als Superuser
über das Benutzermenü oder unter `/admin/`.

---

## Schritt 9: HTTPS über den Reverse Proxy

### 9.1 Adresse besorgen (falls noch nicht vorhanden)

Einfachster Weg mit Synology-DDNS:

1. **Systemsteuerung › Externer Zugriff › DDNS › Hinzufügen**
2. Dienstanbieter: **Synology**, Hostname z. B. `deinname.synology.me`
3. Haken bei **„Zertifikat von Let's Encrypt holen und als Standard festlegen“**
   (dafür muss Port 80 aus dem Internet erreichbar sein, siehe 9.4)

Für die App nimmst du dann `deinname.synology.me` selbst oder eine
Unteradresse wie `vertraege.deinname.synology.me`. Für eine Unteradresse
muss das Zertifikat diese Adresse enthalten (in DSM unter
Systemsteuerung › Sicherheit › Zertifikat › Hinzufügen › Let's Encrypt,
bei synology.me-Adressen ist ein Wildcard-Zertifikat möglich).

### 9.2 Reverse-Proxy-Regel anlegen

**Systemsteuerung › Anmeldeportal › Erweitert › Reverse Proxy › Erstellen**

Reiter **Allgemein**:

| | Protokoll | Hostname | Port |
|---|---|---|---|
| Quelle | HTTPS | `vertraege.deinname.synology.me` | `443` |
| Ziel | HTTP | `localhost` | `8000` |

Beschreibung: `contracts-manager`. HSTS darf an.

Reiter **Benutzerdefinierte Kopfzeile › Erstellen › Benutzerdefiniert**:

| Kopfzeilenname | Wert |
|---|---|
| `X-Forwarded-Proto` | `$scheme` |

Speichern.

### 9.3 Zertifikat zuweisen

**Systemsteuerung › Sicherheit › Zertifikat › Einstellungen**: beim
Dienst `vertraege.deinname.synology.me` das passende Zertifikat auswählen.

### 9.4 Zugriff von unterwegs (optional)

- **Nur im Heimnetz:** Im Heimnetz-DNS (Router) oder in der `hosts`-Datei
  deines Rechners die Adresse auf `192.168.1.10` zeigen lassen. Nichts am Router freigeben.
- **Von unterwegs, sicher:** VPN zur Synology (Paket **VPN Server**) oder
  Tailscale verwenden und nichts nach außen freigeben.
- **Direkt aus dem Internet:** Im Router Port `443` (und für Let's
  Encrypt `80`) auf die Synology weiterleiten. Nur mit starkem Passwort –
  die App enthält private Vertragsdaten.

### 9.5 `.env` auf Variante B umstellen

Falls du mit Variante A begonnen hast:

```bash
cd /volume1/docker/contracts-manager && vi .env
```

Werte wie in Schritt 5.2, Variante B eintragen, dann die App neu starten,
damit sie die Änderung liest:

```bash
sudo docker compose up -d
```

### 9.6 Port 8000 abschotten

Damit niemand den Proxy umgeht: **Systemsteuerung › Sicherheit ›
Firewall** aktivieren, Firewall-Profil bearbeiten und für Port `8000`
eine Regel **Ablehnen** für alle Quellen anlegen (die Regel muss über
einer allgemeinen „Zulassen“-Regel stehen). Der Reverse Proxy greift
über `localhost` zu und ist davon nicht betroffen.

**Prüfen:** `https://vertraege.deinname.synology.me/` funktioniert,
`http://192.168.1.10:8000/` von deinem Rechner aus nicht mehr.

---

## Schritt 10: Tägliche Sicherung einrichten

Die Sicherung ist ein kurzes Skript in der Aufgabenplanung von DSM. Es
nutzt den festen Container-Namen `contracts-manager-db`, braucht also
nichts aus dem Repository.

### 10.1 Aufgabe anlegen

**Systemsteuerung › Aufgabenplanung › Erstellen › Geplante Aufgabe ›
Benutzerdefiniertes Script**

- Reiter **Allgemein**: Aufgabe `contracts-backup`, Benutzer **root**
- Reiter **Zeitplan**: täglich, 03:00
- Reiter **Aufgabeneinstellungen**:
  - „Ausführungsdetails per E-Mail senden“ und „nur wenn das Skript nicht normal beendet wird“ aktivieren
    (setzt eingerichtete E-Mail-Benachrichtigungen in DSM voraus)
  - Benutzerdefiniertes Script:

```
PATH=/usr/local/bin:/usr/bin:/bin:$PATH
DIR=/volume1/docker/contracts-manager/backups
FILE="$DIR/contracts-$(date +%Y-%m-%d_%H%M%S).dump"
if docker exec contracts-manager-db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$FILE.partial" && [ -s "$FILE.partial" ]; then
  mv "$FILE.partial" "$FILE" && chmod 600 "$FILE"
  find "$DIR" -maxdepth 1 -name 'contracts-*.dump' -mtime +30 -delete
else
  rm -f "$FILE.partial"
  echo "Sicherung fehlgeschlagen" >&2
  exit 1
fi
```

Das Skript schreibt eine komprimierte Sicherung nach
`backups/contracts-JJJJ-MM-TT_HHMMSS.dump`, legt sie erst nach Erfolg
an (keine halben Dateien) und löscht Sicherungen, die älter als 30 Tage
sind. Die Zahl `30` in der `find`-Zeile ist die Aufbewahrungszeit.

### 10.2 Einmal ausführen und prüfen

Aufgabe markieren › **Ausführen**. Danach liegt eine neue Datei im Ordner:

```bash
ls -lh /volume1/docker/contracts-manager/backups
```

### 10.3 Kopie außerhalb der Synology

Die Sicherungen liegen auf derselben Synology wie die Datenbank. Gegen
Defekt oder Diebstahl zusätzlich mit **Hyper Backup** (Paket-Zentrum)
die Ordner `contracts-manager/backups` und `contracts-manager/media` auf
eine USB-Platte oder in die Cloud sichern. Nicht `db` direkt sichern –
die laufende Datenbank ist als Dateikopie nicht zuverlässig.

Zusätzlich kann die App selbst alle Daten und Dateien in einer ZIP-Datei
sichern (Verwaltung › Backup), siehe [Betrieb](operations.md#backup-file-of-the-app-export-and-import).

---

## Schritt 11: Verhalten nach einem Neustart der Synology

Liegt der Projektordner im Standardordner `docker`, starten die Container
nach einem Neustart von selbst wieder.

Hast du einen **verschlüsselten Ordner** gewählt (Schritt 2), ist er nach
dem Neustart **gesperrt**. Die Container starten dann absichtlich nicht,
damit nicht an einem unverschlüsselten Ort eine neue, leere Datenbank
entsteht.

So bringst du alles wieder hoch:

1. **Systemsteuerung › Gemeinsamer Ordner** › `contracts` markieren ›
   **Verschlüsselung › Einhängen** › Passwort eingeben
2. Per SSH:

```bash
cd /volume1/contracts/contracts-manager && sudo docker compose up -d
```

Alternativ in DSM die Schlüsselverwaltung nutzen, damit der Ordner beim
Start automatisch eingehängt wird. Bequemer, schützt aber nicht mehr,
wenn die ganze Synology gestohlen wird.

---

## Updates einspielen

Wenn es eine neue Version gibt (Tag `v0.2.0` o. Ä.):

1. **Sicherung:** In DSM Aufgabenplanung die Aufgabe `contracts-backup`
   markieren › **Ausführen** (Schritt 10).
2. **Neues Image laden und starten:**

```bash
cd /volume1/docker/contracts-manager && sudo docker compose pull
```

```bash
sudo docker compose up -d
```

```bash
sudo docker compose ps
```

`docker compose pull` lädt das neue Image, `up -d` startet die Container
damit neu. Migrationen laufen beim Start automatisch. Deine Daten bleiben
erhalten.

**Die Compose-Datei** wird beim Update nicht automatisch erneuert. Meist
bleibt sie gleich. Braucht eine Version eine geänderte Datei, steht das in
den Hinweisen der Version auf GitHub. Dann holst du sie wie in Schritt 4
neu (am besten unter anderem Namen) und vergleichst sie:

```bash
curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml -o docker-compose.neu.yml
```

```bash
diff docker-compose.yml docker-compose.neu.yml
```

Gibt `diff` nichts aus, bist du aktuell (`rm docker-compose.neu.yml`).
Sonst `mv docker-compose.neu.yml docker-compose.yml` und
`sudo docker compose up -d`.

### Eine Version festhalten

Mit `latest` bekommst du bei jedem `docker compose pull` die neueste
Version. Willst du selbst bestimmen, wann es ein Update gibt, trage in
`.env` eine feste Version ein:

```bash
vi /volume1/docker/contracts-manager/.env
```

```
APP_IMAGE_TAG=0.2.0
```

Dann ändert `docker compose pull` nichts, bis du die Zeile auf die nächste
Version setzt. Welche Versionen es gibt, steht auf GitHub auf der Seite des
Repositorys unter **Tags** bzw. **Packages**. Danach wie oben:
`pull`, `up -d`.

### Zurück auf eine ältere Version

1. In `.env` `APP_IMAGE_TAG` auf die ältere Version setzen.
2. `sudo docker compose pull` und `sudo docker compose up -d`.

**Achtung:** Die Datenbank wird dabei nicht zurückgedreht. Hat die neuere
Version die Datenbank verändert (neue Migrationen), startet die ältere
unter Umständen nicht. Dann zuerst die Sicherung aus der Zeit vor dem
Update einspielen (siehe [Betrieb](operations.md#restore)).

### Bestehende Installation mit den Ordnern `app/` und `data/`

Frühere Fassungen dieser Anleitung haben das Repository mit `git clone`
nach `/volume1/contracts/app` geholt und Datenbank und Uploads nach
`/volume1/contracts/data` gelegt. Solche Installationen laufen
**weiter**: Ihre `.env` enthält `DATA_PATH`, und die Container nutzen die
Ordner, die sie schon nutzen.

Das öffentliche Repository hat mit Version 0.1.0 eine **neue
Git-Historie**. Deshalb funktioniert `git pull` in einem alten Klon nicht
mehr. Es gibt zwei Wege:

- Die Compose-Datei per `curl` holen wie in Schritt 4, ganz ohne Git.
- Den Klon auf die neue Historie stellen: `git fetch origin`, dann
  `git reset --hard origin/main` (deine `.env` ist nicht versioniert und
  bleibt erhalten).

Danach `sudo docker compose pull` und `sudo docker compose up -d`. Dabei
werden die Container nur umbenannt (`contracts-manager-app` usw.), die
Daten bleiben, wo sie sind. Das alte, selbst gebaute Image
`contracts-manager-app` kannst du mit
`sudo docker image rm contracts-manager-app` löschen.

**Umzug auf die neue Ordnerstruktur (optional):**

1. **Sicherung** erstellen und aufbewahren: im alten Ordner
   `sudo sh scripts/backup.sh`.
2. **Alte Container stoppen und entfernen** (sie belegen sonst Port und
   Namen):

```bash
cd /volume1/contracts/app && sudo docker compose down
```

3. **Neuen Ordner anlegen** (Schritt 4: Ordner, `docker-compose.yml`
   holen, `chown`). Die `.env` **nicht** neu anlegen, sondern die alte
   übernehmen:

```bash
sudo cp /volume1/contracts/app/.env /volume1/docker/contracts-manager/.env
```

   In der neuen `.env` die Zeile `DATA_PATH=…` (und `BACKUP_DIR=…`, falls
   vorhanden) **löschen** oder auskommentieren.
4. **Daten kopieren** (mit `-a`, damit Besitzer und Rechte erhalten bleiben):

```bash
sudo cp -a /volume1/contracts/data/db/. /volume1/docker/contracts-manager/db/
```

```bash
sudo cp -a /volume1/contracts/data/media/. /volume1/docker/contracts-manager/media/
```

```bash
sudo cp -a /volume1/contracts/data/backups/. /volume1/docker/contracts-manager/backups/
```

5. **Starten und prüfen:** `cd /volume1/docker/contracts-manager && sudo docker compose up -d`,
   dann `sudo docker compose ps` und in der App anmelden: Verträge und
   Dokumente müssen da sein.
6. **Aufgabenplanung** auf das Skript aus Schritt 10 umstellen, bestehende
   Hyper-Backup-Aufgaben auf die neuen Ordner zeigen lassen. Reverse Proxy
   und Firewall bleiben unverändert (gleicher Port).
7. Erst wenn alles läuft, den alten Ordner `/volume1/contracts` löschen.

Liegt der alte Ordner in einem verschlüsselten Ordner und der neue im
unverschlüsselten `docker`, liegen die Daten danach **unverschlüsselt**.
Dann den neuen Ordner ebenfalls in einem verschlüsselten Ordner anlegen
(Schritt 2).

---

## Fehlerbehebung

| Symptom | Ursache | Lösung |
|---|---|---|
| `permission denied ... docker.sock` | `sudo` vergessen | Befehl mit `sudo` wiederholen |
| `docker: 'compose' is not a docker command` | ältere Container-Manager-Version | `docker-compose` statt `docker compose` |
| `no configuration file provided: not found` | Befehl nicht im Projektordner ausgeführt | `cd /volume1/docker/contracts-manager` |
| `bind source path does not exist` | Ordner `db` oder `media` fehlt neben der `docker-compose.yml`, oder der verschlüsselte Ordner ist gesperrt | Befehl im Projektordner ausführen, `mkdir` aus Schritt 4, Ordner einhängen (Schritt 11) |
| `Conflict. The container name "/contracts-manager-app" is already in use` | ein alter Stack mit denselben Containern läuft noch | im alten Ordner `sudo docker compose down` ausführen |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Port 8000 belegt | in `.env` `APP_PORT=8010`, Reverse Proxy auf 8010 ändern |
| `db` startet nicht, Log: `password authentication failed` | `POSTGRES_PASSWORD` nachträglich geändert | Das Passwort gilt nur beim allerersten Start. Altes Passwort zurück in `.env` – oder bei leerer Installation den Ordner `db` leeren und neu starten |
| `db`-Log: `data directory ... has invalid permissions` / `Operation not permitted` | Rechte des Synology-Ordners | `sudo docker compose down`, `sudo chown -R 999:999 /volume1/docker/contracts-manager/db`, `sudo chmod 700 /volume1/docker/contracts-manager/db`, neu starten |
| Browser: **Bad Request (400)** | Adresse fehlt in `ALLOWED_HOSTS` | Hostname eintragen (ohne `http://`), `sudo docker compose up -d` |
| Beim Anmelden: **CSRF-Überprüfung fehlgeschlagen** | `CSRF_TRUSTED_ORIGINS` passt nicht oder Header fehlt | exakte Adresse mit `https://` eintragen, Kopfzeile `X-Forwarded-Proto` im Proxy (9.2) |
| Anmeldung über `http://IP:8000` springt immer zurück zum Login | `USE_HTTPS=True` ohne HTTPS | Variante A: `USE_HTTPS=False` |
| Endlose Weiterleitung / „zu viele Umleitungen“ | Proxy-Ziel auf HTTPS gestellt | Ziel im Reverse Proxy muss **HTTP** `localhost:8000` sein |
| `app` oder `worker`-Log: `Permission denied: '/app/media/...'` | `media` gehört nicht dem Benutzer `1000` | `chown` aus Schritt 4 ausführen |
| `app` bleibt `unhealthy` | Datenbank nicht erreichbar oder Fehler beim Start | `sudo docker compose logs app` lesen |
| `curl: (22) ... 404` oder `Not Found` beim Holen der Dateien | Die Adresse ist falsch geschrieben, oder der Tag darin existiert nicht | Adresse aus Schritt 4 neu kopieren |
| `manifest unknown` / `not found` beim Laden des Images | die Version in `APP_IMAGE_TAG` gibt es nicht (Tippfehler, ohne `v`) | Schreibweise prüfen, z. B. `0.2.0`, oder die Zeile entfernen für `latest` |
| Anmeldung nicht möglich, kein Benutzer vorhanden | `ADMIN_USERNAME`/`ADMIN_PASSWORD` fehlen in der `.env`, oder es gab schon einen Administrator (dann werden sie ignoriert), oder das Passwort war ungültig | `sudo docker compose logs app \| grep -i administrator` lesen; Werte korrigieren und `sudo docker compose up -d`, oder `createsuperuser` (Schritt 7) |
| Aufgabe `contracts-backup` meldet „Sicherung fehlgeschlagen“ | Container `contracts-manager-db` läuft nicht oder heißt anders (alte Compose-Datei) | `sudo docker ps` prüfen; ggf. Compose-Datei erneuern (siehe „Updates einspielen“) |

Änderungen an `.env` wirken erst nach `sudo docker compose up -d`.

---

## Nützliche Befehle (immer in `/volume1/docker/contracts-manager`)

| Zweck | Befehl |
|---|---|
| Status | `sudo docker compose ps` |
| Logs live verfolgen | `sudo docker compose logs -f app` (bzw. `worker`) |
| Neustart | `sudo docker compose restart` |
| Anhalten | `sudo docker compose stop` |
| Wieder starten | `sudo docker compose up -d` |
| Neues Image laden (Update) | `sudo docker compose pull`, dann `sudo docker compose up -d` |
| Laufende Image-Versionen anzeigen | `sudo docker compose images` |
| Passwort eines App-Benutzers ändern | `sudo docker compose exec app python manage.py changepassword BENUTZER` |
| Tests im Container | `sudo docker compose exec app python manage.py test` |

Die Wiederherstellung einer Sicherung, die Login-Sperre nach Fehlversuchen
und weitere Betriebsthemen beschreibt [Betrieb](operations.md) (englisch).
