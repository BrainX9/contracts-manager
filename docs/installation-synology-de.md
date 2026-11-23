# Contracts Manager auf der Synology installieren – Schritt für Schritt

[← Zurück zum README](../README.md) · Kurzfassung auf Englisch: [Installation on a Synology NAS](installation-synology.md)

Diese Anleitung führt dich von einer frischen Synology bis zur laufenden
App mit Anmeldung, HTTPS und täglicher Sicherung. Alles läuft über
**Docker Compose**: Eine einzige Datei beschreibt die Container und enthält
die wenigen Einstellungen. Es wird nichts installiert, gebaut oder geklont,
und für die Installation brauchst du weder SSH noch eine `.env`-Datei.
Jeder Schritt sagt, was du tust, und wie du prüfst, ob er geklappt hat.

Zeitbedarf: etwa 30 Minuten.

---

## Überblick

Am Ende sieht es so aus:

```
/volume1/docker/contracts-manager/
├── docker-compose.yml     beschreibt die drei Container (von GitHub)
├── db/                    PostgreSQL-Datenbank
├── media/                 Logos und Vertragsdokumente
├── config/                erzeugter Schlüssel der App (entsteht von selbst)
└── backups/               tägliche Sicherungen
```

Auf der Synology laufen drei Container:

| Compose-Dienst | Container-Name | Aufgabe |
|---|---|---|
| `db` | `contracts-manager-db` | PostgreSQL 17 |
| `app` | `contracts-manager-app` | die Django-Anwendung, erreichbar auf Port 8010 der Synology |
| `worker` | `contracts-manager-worker` | Hintergrunddienst für Erinnerungen, den KI-Assistenten und Paperless |

In Befehlen mit `docker compose` verwendest du den kurzen Dienstnamen
(z. B. `app`), im Container Manager von DSM und bei `docker exec` siehst
du den langen Container-Namen.

Davor sitzt der Reverse Proxy der Synology, der HTTPS übernimmt.

**Die App kommt als fertiges Docker-Image.** `app` und `worker` laufen mit
dem Image `brainx9/contracts-manager` von Docker Hub, das GitHub bei jeder
neuen Version baut (für Intel/AMD und für ARM). Auf der Synology wird nichts
gebaut und kein Programmcode installiert, es wird nur das Image
heruntergeladen.

| Image | Woher | Inhalt |
|---|---|---|
| `brainx9/contracts-manager` | Docker Hub (zusätzlich `ghcr.io/brainx9/contracts-manager`) | die App, auch für den `worker` |
| `pgvector/pgvector:pg17` | Docker Hub | PostgreSQL 17 |

Das App-Image hat zwei Arten von Namen (Tags): `latest` ist immer die
neueste Version, `0.9.0` o. Ä. eine feste. Standard ist `latest`;
wie du eine Version festhältst, steht unter „Updates einspielen“.

---

## Was du brauchst

- [ ] Synology mit **DSM 7.2** oder neuer (Systemsteuerung › Info-Center)
- [ ] Ein Benutzerkonto in der Gruppe **administrators**
- [ ] Die IP-Adresse der Synology im Heimnetz, z. B. `192.168.1.10`
- [ ] Eine Internetverbindung der Synology (lädt die Images von Docker Hub)
- [ ] Einen Texteditor auf deinem Rechner (z. B. TextEdit, Editor, VS Code)
- [ ] Optional für HTTPS: eine Adresse für die App, z. B. über Synology-DDNS (`deinname.synology.me`)
- [ ] Optional: SSH für die Befehle in den Kästen „Mit SSH“ (Schritt 3)

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

## Schritt 3: SSH (optional)

Für die Installation brauchst du kein SSH. Es ist nur für die Kästen
„Mit SSH“ in dieser Anleitung, für Updates per Befehl und für die
Fehlersuche praktisch.

1. **Systemsteuerung › Terminal & SNMP › Terminal**
2. Haken bei **SSH-Dienst aktivieren**, Port `22` lassen, **Übernehmen**.
3. Auf deinem Rechner das Terminal öffnen (Mac: „Terminal“, Windows:
   „PowerShell“) und verbinden:

```bash
ssh admin@192.168.1.10
```

Beim ersten Mal die Frage nach dem Fingerabdruck mit `yes` bestätigen,
dann dein DSM-Passwort eingeben (beim Tippen erscheinen keine Zeichen, das ist normal).
Docker-Befehle brauchen auf der Synology `sudo`. Falls `sudo docker compose version`
einen Fehler meldet, verwende überall `docker-compose` statt `docker compose`.

---

## Schritt 4: Ordner anlegen

1. **File Station** öffnen, den gemeinsamen Ordner `docker` wählen.
2. **Erstellen › Ordner erstellen**: `contracts-manager`.
3. In `contracts-manager` drei Ordner erstellen: `db`, `media` und `backups`.

Den Ordner `config` legt Docker beim ersten Start selbst an. Besitzer und
Rechte musst du nicht ändern: Der App-Container gibt `media` und `config`
bei jedem Start dem Benutzer, als der die App läuft (Standard `1000:1000`),
und arbeitet danach nicht mehr als root.

> **Mit SSH:**
> `sudo mkdir -p /volume1/docker/contracts-manager/db /volume1/docker/contracts-manager/media /volume1/docker/contracts-manager/backups`

**Prüfen:** In File Station siehst du unter `docker/contracts-manager` die
Ordner `db`, `media` und `backups`.

---

## Schritt 5: Compose-Datei holen und Passwort eintragen

1. Die Datei herunterladen:
   <https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml>
   (im Browser öffnen und mit „Speichern unter …“ als `docker-compose.yml` sichern).
2. Die Datei im Texteditor öffnen und in der Zeile

   ```yaml
     POSTGRES_PASSWORD: &db-password "CHANGE_ME"
   ```

   `CHANGE_ME` durch ein eigenes Passwort ersetzen, **nur Buchstaben und
   Ziffern**, z. B. `Vertraege2026Sicher`. Die Anführungszeichen bleiben.
   Das ist die einzige Pflichtänderung.
3. Speichern und die Datei mit File Station in den Ordner
   `docker/contracts-manager` hochladen.

Das Passwort gilt ab dem ersten Start. Später ändern geht nur mit
zusätzlichem Aufwand in der Datenbank – also gleich ein gutes wählen.

**Optionale Einstellungen** stehen schon in der Datei, mit `#` davor
(auskommentiert). Um eine zu nutzen, entfernst du das `#` und trägst deinen
Wert ein:

| Einstellung | Wofür |
|---|---|
| `ALLOWED_HOSTS` | empfohlen: Adresse(n), unter der die App erreichbar ist, ohne `http://`, z. B. `vertraege.deinname.synology.me` |
| `CSRF_TRUSTED_ORIGINS` | nur nötig, wenn der Reverse Proxy Adresse oder Schema ändert; mit `https://`, z. B. `https://vertraege.deinname.synology.me` |
| `USE_HTTPS` | `"True"`, sobald die App über den Reverse Proxy mit HTTPS läuft (Schritt 9) |
| `APP_UID`, `APP_GID` | wenn `media` und `config` einem anderen Benutzer gehören sollen, z. B. deinem DSM-Benutzer |
| `SECRET_KEY` | nur, wenn du den Schlüssel selbst festlegen willst; sonst erzeugt die App einen im Ordner `config` |

Für den ersten Test im Heimnetz musst du nichts davon ändern. Die App ist
auf Port **8010** erreichbar (`"8010:8000"` unter `ports:`); ist der Port
auf deiner Synology belegt, ändere die linke Zahl. Alle weiteren
Einstellungen: [Umgebungsvariablen](variables.md) (englisch).

In der Datei steht das Datenbank-Passwort – gib sie nicht weiter. Ein `$`
in einem Wert schreibst du als `$$`.

E-Mail (für Erinnerungen und Passwort-Reset), Single Sign-on, den
KI-Assistenten und Paperless richtest du später in der App ein, unter
**Verwaltung** (siehe [Konfiguration](configuration.md)). Für die
Installation sind sie nicht nötig.

> **Mit SSH:** `cd /volume1/docker/contracts-manager`, dann
> `curl -fsSLO https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml`
> und mit `vi docker-compose.yml` bearbeiten (`i` zum Bearbeiten, `Esc` und
> `:wq` zum Speichern). `main` lädt die aktuelle Fassung; für die Datei einer
> bestimmten Version schreibst du ihren Tag in die Adresse, z. B. `v0.9.0`.

---

## Schritt 6: Projekt im Container Manager anlegen und starten

1. **Container Manager › Projekt › Erstellen**
2. Projektname: `contracts-manager`
3. Pfad: **Festlegen** › `docker/contracts-manager` (der Ordner aus Schritt 4)
4. Quelle: **Vorhandene docker-compose.yml verwenden** (DSM findet die
   hochgeladene Datei). Alternativ „docker-compose.yml erstellen“ und den
   Inhalt einfügen.
5. **Weiter**. Die Seite „Web-Portal-Einstellungen“ überspringst du mit
   **Weiter**, dann **Fertig**.

Beim ersten Mal lädt die Synology die fertigen Images herunter (Datenbank
und App), gebaut wird nichts. Das dauert ein paar Minuten.

> **Mit SSH:** `cd /volume1/docker/contracts-manager && sudo docker compose up -d`

**Prüfen** (nach ca. einer Minute): Unter **Container Manager › Projekt**
zeigt `contracts-manager` den Status „Wird ausgeführt“, unter **Container**
laufen `contracts-manager-db`, `contracts-manager-app` und
`contracts-manager-worker`. Im Browser antwortet
`http://192.168.1.10:8010/health/` mit `{"status": "ok"}`.

Wenn nicht: **Container › contracts-manager-app › Details › Protokoll**
ansehen und im Abschnitt [Fehlerbehebung](#fehlerbehebung) nachschlagen.

> **Mit SSH:** `sudo docker compose ps` zeigt den Status (`db` und `app`
> `(healthy)`, `worker` `Up`), `sudo docker compose logs app` das Protokoll.

**Welche Version läuft?** Nach der Anmeldung unter **Verwaltung › System**.

---

## Schritt 7: Ersten Administrator anlegen

Die App hat kein voreingestelltes Konto. Öffne sie direkt nach dem ersten
Start im Browser:

```
http://192.168.1.10:8010/
```

(statt `192.168.1.10` die IP-Adresse deiner Synology). Eine neue
Installation zeigt die Seite **Administrator anlegen**. Wähle Benutzername,
E-Mail-Adresse und ein starkes Passwort (z. B. aus deinem Passwortmanager).

Danach bist du angemeldet, und die Seite verschwindet für immer. Das ist
dein Login für die App, unabhängig von DSM.

> **Wichtig:** Lege den Administrator an, bevor du die App über den Reverse
> Proxy aus dem Internet erreichbar machst (Schritt 9). Bis dahin wird
> Administrator, wer die Seite zuerst öffnet.

Weitere Benutzer legst du später in der App unter **Verwaltung** an. Über
die Kommandozeile geht es auch:

```bash
sudo docker compose exec app python manage.py createsuperuser
```

---

## Schritt 8: Erster Test im Browser

Unter `http://192.168.1.10:8010/` mit dem Benutzer aus Schritt 7 anmelden.
Du landest auf der Übersicht mit den Summen pro Monat und Jahr. Das
Django-Admin erreichst du als Superuser über das Benutzermenü oder unter
`/admin/`.

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
| Ziel | HTTP | `localhost` | `8010` |

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

### 9.5 Compose-Datei auf HTTPS umstellen

In der `docker-compose.yml` (mit File Station herunterladen, bearbeiten,
wieder hochladen) bei diesen Zeilen das `#` entfernen und deine Adresse
eintragen:

```yaml
  ALLOWED_HOSTS: "vertraege.deinname.synology.me"
  USE_HTTPS: "True"
```

Regeln, die oft schiefgehen:

- `ALLOWED_HOSTS`: nur der Hostname, **ohne** `http://`, ohne Port, ohne `/`;
  mehrere mit Komma trennen, ohne Leerzeichen
- `CSRF_TRUSTED_ORIGINS` brauchst du nur, wenn die Anmeldung mit
  „CSRF-Überprüfung fehlgeschlagen“ scheitert: dann mit `https://` und ohne
  `/` am Ende
- Mit `USE_HTTPS: "True"` klappt die Anmeldung über `http://192.168.1.10:8010`
  nicht mehr – das ist gewollt

Dann die Änderung übernehmen: **Container Manager › Projekt ›
contracts-manager › Aktion › Erstellen** (baut die Container mit der neuen
Datei neu auf, die Daten bleiben).

> **Mit SSH:** `cd /volume1/docker/contracts-manager && sudo docker compose up -d`

### 9.6 Port 8010 abschotten

Damit niemand den Proxy umgeht: **Systemsteuerung › Sicherheit ›
Firewall** aktivieren, Firewall-Profil bearbeiten und für Port `8010`
eine Regel **Ablehnen** für alle Quellen anlegen (die Regel muss über
einer allgemeinen „Zulassen“-Regel stehen). Der Reverse Proxy greift
über `localhost` zu und ist davon nicht betroffen.

**Prüfen:** `https://vertraege.deinname.synology.me/` funktioniert,
`http://192.168.1.10:8010/` von deinem Rechner aus nicht mehr.

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
2. **Container Manager › Projekt › contracts-manager › Aktion › Starten**
   (mit SSH: `cd /volume1/contracts/contracts-manager && sudo docker compose up -d`)

Alternativ in DSM die Schlüsselverwaltung nutzen, damit der Ordner beim
Start automatisch eingehängt wird. Bequemer, schützt aber nicht mehr,
wenn die ganze Synology gestohlen wird.

---

## Updates einspielen

Wenn es eine neue Version gibt (Tag `v0.9.0` o. Ä.; als Administrator
siehst du in der Seitenleiste einen Hinweis). Updates gehen am einfachsten
per SSH (Schritt 3):

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
den Hinweisen der Version auf GitHub. Dann holst du sie wie in Schritt 5
neu (am besten unter anderem Namen), trägst dein Passwort ein und
vergleichst sie:

```bash
curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml -o docker-compose.neu.yml
```

```bash
diff docker-compose.yml docker-compose.neu.yml
```

Gibt `diff` nichts aus, bist du aktuell (`rm docker-compose.neu.yml`).
Sonst `mv docker-compose.neu.yml docker-compose.yml` und
`sudo docker compose up -d`.

**Installation von vor 0.9.0 mit `.env`:** Deine `docker-compose.yml` und
`.env` laufen unverändert weiter. Beim ersten Start von 0.9.0 übernimmt die
App die Einstellungen für E-Mail, Single Sign-on, KI und Paperless einmalig
aus der `.env` in die Verwaltung; das Protokoll nennt danach die Zeilen, die
du löschen kannst. Die Compose-Datei mit `.env` heißt auf GitHub jetzt
`docker-compose.env.yml` – zum Vergleichen also diese holen:
`curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.env.yml -o docker-compose.neu.yml`.

### Eine Version festhalten

Mit `latest` bekommst du bei jedem `docker compose pull` die neueste
Version. Willst du selbst bestimmen, wann es ein Update gibt, schreibe in
der `docker-compose.yml` bei `app` und `worker` eine feste Version statt
`latest`:

```yaml
    image: brainx9/contracts-manager:0.9.0
```

(Mit `.env`: `APP_IMAGE_TAG=0.9.0`.) Dann ändert `docker compose pull`
nichts, bis du die Zeilen auf die nächste Version setzt. Welche Versionen es gibt, steht auf GitHub auf der Seite des
Repositorys unter **Tags** bzw. **Packages**. Danach wie oben:
`pull`, `up -d`.

### Zurück auf eine ältere Version

1. Unter `image:` (bzw. in `.env` bei `APP_IMAGE_TAG`) die ältere Version eintragen.
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

- Die Compose-Datei mit `.env` per `curl` holen, ganz ohne Git:
  `curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.env.yml -o docker-compose.yml`
- Den Klon auf die neue Historie stellen: `git fetch origin`, dann
  `git reset --hard origin/main` (deine `.env` ist nicht versioniert und
  bleibt erhalten). Seit 0.9.0 ist `docker-compose.yml` im Repository die
  Datei **ohne** `.env`; trage deshalb in deine `.env` die Zeile
  `COMPOSE_FILE=docker-compose.env.yml` ein, dann nutzt `docker compose`
  weiter deine `.env`.

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

3. **Neuen Ordner anlegen** (Schritt 4) und die Compose-Datei mit `.env`
   holen: `curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.env.yml -o docker-compose.yml`.
   Die `.env` **nicht** neu anlegen, sondern die alte übernehmen:

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
| `bind source path does not exist` / `Bind mount failed` | Ordner `db` oder `media` fehlt neben der `docker-compose.yml`, oder der verschlüsselte Ordner ist gesperrt | Ordner anlegen (Schritt 4), Ordner einhängen (Schritt 11) |
| `Conflict. The container name "/contracts-manager-app" is already in use` | ein alter Stack mit denselben Containern läuft noch | im alten Ordner `sudo docker compose down` ausführen |
| `Bind for 0.0.0.0:8010 failed: port is already allocated` | Port 8010 belegt | in der `docker-compose.yml` die linke Zahl bei `"8010:8000"` ändern (z. B. `"8020:8000"`), Reverse Proxy anpassen |
| `db` startet nicht, Log: `password authentication failed` | `POSTGRES_PASSWORD` nachträglich geändert | Das Passwort gilt nur beim allerersten Start. Altes Passwort zurück in die Compose-Datei – oder bei leerer Installation den Ordner `db` leeren und neu starten |
| `db`-Log: `data directory ... has invalid permissions` / `Operation not permitted` | Rechte des Synology-Ordners | `sudo docker compose down`, `sudo chown -R 999:999 /volume1/docker/contracts-manager/db`, `sudo chmod 700 /volume1/docker/contracts-manager/db`, neu starten |
| Browser: **Bad Request (400)** | Adresse fehlt in `ALLOWED_HOSTS`, oder im Hostnamen steht ein Unterstrich (`_`) | Hostname eintragen (ohne `http://`) bzw. einen Namen ohne Unterstrich verwenden, Projekt neu erstellen (9.5) |
| Beim Anmelden: **CSRF-Überprüfung fehlgeschlagen** | `CSRF_TRUSTED_ORIGINS` passt nicht oder Header fehlt | exakte Adresse mit `https://` eintragen, Kopfzeile `X-Forwarded-Proto` im Proxy (9.2) |
| Anmeldung über `http://IP:8010` springt immer zurück zum Login | `USE_HTTPS` ist `True`, aber ohne HTTPS aufgerufen | über die HTTPS-Adresse aufrufen oder `USE_HTTPS` wieder auskommentieren |
| Endlose Weiterleitung / „zu viele Umleitungen“ | Proxy-Ziel auf HTTPS gestellt | Ziel im Reverse Proxy muss **HTTP** `localhost:8010` sein |
| `app` oder `worker`-Log: `Permission denied: '/app/media/...'` oder `/config` | alte Compose-Datei mit `user:`, oder die Freigabe erlaubt kein Ändern des Besitzers | `sudo chown -R 1000:1000` für `media` und `config` (bzw. `APP_UID:APP_GID`) |
| `app` bleibt `unhealthy` | Datenbank nicht erreichbar oder Fehler beim Start | `sudo docker compose logs app` lesen |
| `curl: (22) ... 404` oder `Not Found` beim Holen der Dateien | Die Adresse ist falsch geschrieben, oder der Tag darin existiert nicht | Adresse aus Schritt 4 neu kopieren |
| `manifest unknown` / `not found` beim Laden des Images | die Version unter `image:` (bzw. in `APP_IMAGE_TAG`) gibt es nicht (Tippfehler, ohne `v`) | Schreibweise prüfen, z. B. `0.9.0`, oder `latest` |
| Kein Administrator, die Seite „Administrator anlegen“ erscheint nicht | Es gibt schon einen Benutzer (z. B. nach einem Import) | `sudo docker compose exec app python manage.py createsuperuser` (Schritt 7) |
| Aufgabe `contracts-backup` meldet „Sicherung fehlgeschlagen“ | Container `contracts-manager-db` läuft nicht oder heißt anders (alte Compose-Datei) | `sudo docker ps` prüfen; ggf. Compose-Datei erneuern (siehe „Updates einspielen“) |
| Alle sind plötzlich abgemeldet | Der Ordner `config` wurde gelöscht oder geleert; die App hat einen neuen Schlüssel erzeugt | Nichts zu tun: neu anmelden. Daten gehen dabei nicht verloren |

Änderungen an der `docker-compose.yml` (oder `.env`) wirken erst, wenn das
Projekt neu erstellt wird (9.5) bzw. nach `sudo docker compose up -d`.

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
