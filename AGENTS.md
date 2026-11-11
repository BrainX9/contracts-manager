# Projekt: Contracts Manager

## Ziel
Eine selbst gehostete Webanwendung zur Verwaltung privater Verträge, Abos und Versicherungen.
Betrieb als Docker-Container auf einer Synology (Docker Compose), Datenbank PostgreSQL.

## Arbeitsweise (wichtig)
Mit „Maintainer“ ist die Person gemeint, die das Repository betreut (GitHub: BrainX9).

- Arbeite strikt phasenweise. Beginne erst mit der nächsten Phase, wenn der Maintainer sie freigibt.
- Erstelle vor jeder Phase einen kurzen Plan (Dateien, die du anlegst oder änderst) und warte auf die Bestätigung des Maintainers.
- Ändere pro Schritt nur wenige Dateien. Keine großen Umbauten ohne Rückfrage.
- Füge keine Abhängigkeiten hinzu, die hier nicht genannt sind, ohne vorher zu fragen.
- Halte dich an Django-Konventionen (Apps, Models, Admin, Migrationen). Keine selbstgebauten Ersatzlösungen für Dinge, die Django bereits mitbringt (Auth, Admin, Formulare, ORM).
- Code, Kommentare und Commit-Messages auf Deutsch, Bezeichner (Variablen, Modelle, Funktionen) auf Englisch.
- README und Dokumentation in `docs/` auf Englisch (das Repository ist für die Öffentlichkeit gedacht). Screenshots in `docs/screenshots/` nur mit erfundenen Demodaten.
- Technische Begriffe auf Englisch: Pfade (URLs), URL-Namen, Templates, Dateinamen, CSS-Klassen. Sichtbare Texte im Code als englische Ausgangstexte (`gettext`, `{% translate %}`), deutsche Übersetzung in `locale/de`. Die Oberfläche ist standardmäßig deutsch.
- Keine Secrets im Code. Zugangsdaten nur über Umgebungsvariablen (`.env`, nicht im Repository).

## Tech-Stack
- Python 3.12, Django 5.x (LTS-Version bevorzugen)
- PostgreSQL 17 über das Image `pgvector/pgvector:pg17` (pgvector wird erst für RAG später genutzt)
- `psycopg` (Version 3) als Datenbanktreiber
- `gunicorn` als Server, `whitenoise` für statische Dateien
- `django-guardian` für Berechtigungen pro Objekt (ab Version 0.3a)
- Konfiguration über Umgebungsvariablen (z. B. mit `django-environ`)
- Docker Compose mit den Services `app` und `db`, seit Version 0.5 zusätzlich `worker` (django-q2); `app` und `worker` nutzen das veröffentlichte Image von `ghcr.io`
- Oberfläche: eigene Django-Templates mit HTMX (`django-htmx`) und Bootstrap 5 in der App `web`, inklusive Verwaltung von Benutzern und Gruppen (`/manage/`, seit 0.6); Django-Admin nur für Superuser
- htmx, Bootstrap und Bootstrap Icons liegen lokal in `web/static/web/vendor/` (kein CDN)
- `Pillow` für Logos, `mozilla-django-oidc` für Single Sign-on (ab Version 0.4)
- `django-q2` (ORM-Broker, kein Redis) für Hintergrund-Aufgaben (ab Version 0.5)
- `openai` und `anthropic` (offizielle SDKs) für den KI-Assistenten (ab Version 0.8a); OpenAI-kompatible Anbieter (IONOS, Ollama) über `AI_BASE_URL`
- `django-axes` (mit `ipware`) gegen zu viele Fehlversuche bei der Anmeldung; Container laufen als Benutzer `APP_UID:APP_GID` (Standard 1000), nicht als root

## Datenmodell
Lege eine Django-App `contracts` an.

### Contract (Vertrag)
- `id` (automatisch)
- `name` (Vertragsname)
- `provider` -> ForeignKey auf Provider (Vertragspartner)
- `start_date` (Vertragsbeginn)
- `category` -> ForeignKey auf Category (Kategorie)
- `consumer` -> ForeignKey auf Consumer (Verbraucher)

### CostEntry (Kosten mit Startdatum)
- `contract` -> ForeignKey auf Contract (related_name `costs`)
- `amount` (DecimalField, 2 Nachkommastellen)
- `valid_from` (DateField, ab dem dieser Betrag gilt)
- Ein Vertrag kann mehrere Einträge haben (Preisänderungen). Der aktuell gültige Betrag ist der Eintrag mit dem jüngsten `valid_from`, das nicht in der Zukunft liegt. Implementiere das als Methode oder Property am Contract-Modell.

### Stammdaten-Modelle (jeweils `name`, eindeutig)
- `Provider` (Vertragspartner)
- `Category` (Kategorie)
- `Consumer` (Verbraucher, z. B. Person, Fahrzeug, Haushalt)

## Phasen

### Phase 1: Grundgerüst
- Django-Projekt `config` mit App `contracts`
- `Dockerfile`, `docker-compose.yml`, `.env.example`
- Settings für PostgreSQL über Umgebungsvariablen, `ALLOWED_HOSTS` und `CSRF_TRUSTED_ORIGINS` konfigurierbar
- Alle Modelle oben, erste Migration
- Superuser-Anlage dokumentieren (`createsuperuser` im Container, seit 0.9.1 auch über `ADMIN_*` in der `.env`)

### Phase 1b: Erweiterung des Datenmodells (Version 0.1 beta)
Nur Modelle, Migration, Admin und Tests. Keine neuen Abhängigkeiten ohne Rückfrage.

**Category (Haupt- und Unterkategorie)**
- Neues Feld `parent` (ForeignKey auf sich selbst, `null=True`, `blank=True`, `on_delete=PROTECT`, `related_name="children"`).
- Hauptkategorie: `parent` leer (z. B. "Versicherung"). Unterkategorie: `parent` zeigt auf eine Hauptkategorie (z. B. "KFZ-Versicherung"). Nur zwei Ebenen, per `clean()` prüfen.
- Name nur innerhalb desselben `parent` eindeutig, auch bei Hauptkategorien (`UniqueConstraint(fields=["parent", "name"], nulls_distinct=False)`).
- `__str__` zeigt "Versicherung / KFZ-Versicherung".
- `Contract.category` darf auf eine Unter- oder Hauptkategorie zeigen.

**Contract (Vertrag), neue Felder, alle optional außer `reference`**
- `reference`: CharField, eindeutig, `editable=False`. Wird automatisch vergeben im Format Jahr (YYYY) + 4-stelliger Zähler, z. B. `20260001`. Der Zähler beginnt jedes Jahr bei `0001`. Die Vergabe muss auch bei gleichzeitigem Speichern eindeutig bleiben (Transaktion mit Sperre). Der automatische Primärschlüssel `id` bleibt unverändert.
- `contract_number`: CharField (Vertragsnummer des Anbieters), `blank=True`.
- `short_description`: CharField (max. 255), `blank=True`.
- `notes`: TextField (Informationen), `blank=True`.
- `term_value`: PositiveIntegerField, `null=True`, `blank=True`. `term_unit`: CharField mit Auswahl Tage, Wochen, Monate, Quartale, Jahre. Beide Felder müssen zusammen gesetzt oder leer sein (Validierung).

**CostEntry**
- Neues Feld `payment_interval` mit Auswahl: wöchentlich, monatlich, quartalsweise, jährlich. Standard: monatlich.
- Methode am CostEntry, die den Betrag auf einen Monat umrechnet (wöchentlich x 52 / 12, quartalsweise / 3, jährlich / 12).

**Provider (Vertragspartner), neue Felder, alle optional**
- `street`, `postal_code`, `city`, `country` (Auswahlfeld, Standard Deutschland), `email` (EmailField), `customer_number`, `contact_person`, `notes` (TextField).

**Admin**
- `reference` im Vertrag schreibgeschützt anzeigen und in der Liste als erste Spalte.
- Felder in Gruppen (fieldsets) ordnen: Stammdaten, Laufzeit, Informationen.
- Kategorie-Auswahl zeigt "Haupt / Unter". Filter nach Hauptkategorie ergänzen.
- Die Summe aller aktuellen Kosten (Phase 2) wird auf Basis des monatlichen Betrags berechnet und pro Monat und pro Jahr ausgewiesen.

**Migration und Tests**
- Neue Migration `0002`. Falls schon Verträge existieren, vergibt eine Datenmigration `reference` nachträglich.
- Tests für: Format und Jahreszähler von `reference`, Zwei-Ebenen-Regel der Kategorie, Umrechnung der Zahlweise, Validierung der Laufzeit.

### Phase 2: Kern-Funktionen (MVP)
- Admin-Konfiguration für alle Modelle:
  - Verträge mit CostEntry als Inline
  - Spalten in der Liste: Name, Partner, Kategorie, Verbraucher, aktueller Betrag
  - Filter nach Kategorie, Verbraucher und Partner, Suche nach Name
- Admin-Ansicht oder Auswertung mit der Summe aller aktuellen Kosten

### Phase 3: Betrieb
- Volume-Pfade für Synology (Datenbank und Uploads außerhalb des Containers, auf verschlüsseltem Volume)
- Backup-Hinweis (`pg_dump`)
- README mit Installations- und Update-Anleitung

### Version 0.2 beta: Paperless-Verknüpfung und Erweiterungen
Nur Modelle, Migration, Admin, Tests und Doku. Keine neuen Abhängigkeiten.

**Provider (Vertragspartner)**
- `paperless_correspondent_id`: PositiveIntegerField (ab 1), `null=True`, `blank=True`. ID des Korrespondenten in Paperless-ngx. Liegt beim Partner, nicht beim Vertrag, da ein Korrespondent in Paperless der Firma entspricht.

**CostEntry**
- `notes`: TextField, `blank=True`.
- `paperless_document_id`: PositiveIntegerField (ab 1), `null=True`, `blank=True`. ID des Dokuments in Paperless-ngx (z. B. Rechnung, Preisanpassung).

**Consumer (Verbraucher)**
- `number`: CharField (max. 100), `blank=True`. Text statt Zahl, damit auch Kennzeichen oder Nummern mit führender Null passen.
- `notes`: TextField, `blank=True`.

**Links nach Paperless**
- Optionale Einstellung `PAPERLESS_URL` (Umgebungsvariable). Ist sie gesetzt, zeigt das Admin die IDs als Links (neuer Tab): Dokument unter `/documents/<id>/details`, Korrespondent als gefilterte Dokumentenliste `/documents?correspondent__id__in=<id>`.
- Contracts Manager greift nicht auf Paperless zu, sondern erzeugt nur Links.

**Migration und Tests**
- Neue Migration `0004`, nur neue Felder.
- Tests für: optionale Felder, IDs ab 1, Links mit und ohne `PAPERLESS_URL`, Admin-Seiten.

### Version 0.3a beta: Berechtigungen
Umsetzung mit `django-guardian` (3.5). Gilt für Vertrag, Vertragspartner, Kategorie und Verbraucher. Kosteneinträge haben keine eigenen Rechte, sie folgen immer ihrem Vertrag (lesen mit Leserecht, anlegen/ändern/löschen mit Bearbeitungsrecht am Vertrag).

**Rollen**
| Rolle | Umsetzung | Rechte |
|---|---|---|
| Eigentümer | Feld `owner` (ForeignKey auf User, `on_delete=PROTECT`), genau einer pro Objekt | lesen, ändern, löschen, Freigaben und Eigentümer ändern |
| Bearbeiter | guardian-Rechte `view_<modell>` + `change_<modell>` für beliebig viele Benutzer und Gruppen | lesen, ändern |
| Leser | guardian-Recht `view_<modell>` für beliebig viele Benutzer und Gruppen | lesen |

- Der Eigentümer wird beim Anlegen auf den anlegenden Benutzer gesetzt und ist danach änderbar. Ein Benutzer, dem Objekte gehören, kann nicht gelöscht werden (`PROTECT`).
- `contracts.backends.OwnerBackend` gibt dem Eigentümer alle Rechte am eigenen Objekt. Dadurch gilt überall `user.has_perm("contracts.change_contract", obj)`.
- `contracts.permissions` ist die einzige Stelle für Rechte: `can_view`, `can_change`, `can_delete`, `can_share`, `visible_to`, `editable_by`, `grant_role`, `revoke_roles`, `get_roles`, `set_roles`. Rollen werden immer als vollständiges Bündel gesetzt. Admin, Frontend, API und MCP-Server nutzen diese Funktionen.
- guardian mit direkten Fremdschlüssel-Tabellen je Modell (`<Modell>UserObjectPermission`, `<Modell>GroupObjectPermission`), damit Freigaben mit dem Objekt gelöscht werden. `ANONYMOUS_USER_NAME = None`.
- Superuser darf alles. Eigentümer und Freigaben ändern nur Eigentümer und Superuser.
- Anlegen erfordert das globale Django-Recht `add_<modell>`, vergeben über die Gruppe **„Benutzer“**. Globale `view`/`change`-Rechte werden nicht vergeben und gelten nicht für Objekte.
- Gruppe **„Alle“**: Jeder neue Benutzer kommt automatisch hinein. Freigabe an „Alle“ macht ein Objekt für alle sichtbar.
- Namen von Vertragspartner und Verbraucher sind pro Eigentümer eindeutig, Kategorien pro Eigentümer und Hauptkategorie.
- Verweise auf Stammdaten, die man nicht lesen darf, werden als Name angezeigt. Auswahllisten zeigen nur lesbare Objekte (plus den aktuellen Wert).
- Admin: Listen, Filter und Kostensumme nur über sichtbare Objekte; Leser sehen Formulare schreibgeschützt; Freigaben als Formularfelder „Leser/Bearbeiter (Benutzer/Gruppen)“, kein `GuardedModelAdmin`.

**Migrationen und Tests**
- `0005` Eigentümer (vorerst optional) und Rechte-Tabellen, `0006` Datenmigration (vorhandene Objekte gehören dem ersten Superuser, Gruppen „Benutzer“ und „Alle“), `0007` Eigentümer verpflichtend.
- Tests pro Rolle und Aktion, für Gruppen, Kosteneinträge, Löschschutz, Summen und das Admin.

### Version 0.3b beta: Oberfläche (Frontend)
Neue App `web`, Fachlogik und Berechtigungen bleiben in `contracts` (`contracts.permissions`).

**Technik**
- Django-Templates + HTMX 2 (`django-htmx`) + Bootstrap 5.3, lokal abgelegt, kein Node-Build.
- Englische Pfade, Oberfläche auf Deutsch über Django i18n. Sprache pro Benutzer (`web.UserPreferences.language`, Deutsch oder Englisch), aktiviert durch `web.middleware.UserLanguageMiddleware`, gilt auch im Admin; die Browsersprache wird nicht ausgewertet. `gettext` im Image, Übersetzungen werden beim Start kompiliert.
- Nutzbar für jeden aktiven Benutzer, auch ohne Mitarbeiter-Status. Link zur Verwaltung nur mit Mitarbeiter-Status (seit 0.3d: nur Superuser).
- Helles und dunkles Design nach Systemeinstellung, responsiv.

**Seiten**
| Pfad | Inhalt |
|---|---|
| `/` | Übersicht: Summen pro Monat/Jahr, nach Hauptkategorie, teuerste Verträge |
| `/contracts/` | Liste mit Live-Suche, Filtern und Sortierung (HTMX), Summe passend zum Filter |
| `/contracts/<id>/` | Detail mit Kostenverlauf, Paperless-Links, Zugriff |
| `/contracts/new/`, `/<id>/edit/`, `/<id>/delete/` | Vertrag anlegen (Recht `add_contract`), bearbeiten (Bearbeiter), löschen (Eigentümer) |
| `/contracts/<id>/costs/new/`, `/costs/<id>/edit/`, `/costs/<id>/delete/` | Kosteneinträge per HTMX (Bearbeitungsrecht am Vertrag), ohne JavaScript als eigene Seiten |
| `/contracts/<id>/sharing/` | Eigentümer und Freigaben (Eigentümer, Superuser), je Benutzer/Gruppe genau eine Rolle |
| `/accounts/login/`, `/accounts/logout/`, `/accounts/settings/`, `/accounts/password/` | Anmeldung, Sprache, Passwort |

**Regeln**
- Nicht sichtbare Objekte liefern 404, sichtbare ohne Recht 403.
- Filter- und Auswahllisten zeigen nur lesbare Objekte (Auswahl plus aktueller Wert: `perms.visible_or_current`). Kategorien als Baum sortiert (`category_tree_order`).
- Bei einer Übergabe des Eigentums wird der bisherige Eigentümer automatisch Bearbeiter (`perms.transfer_ownership`), auch im Admin.
- Stammdaten (Partner, Kategorien, Verbraucher) werden vorerst im Admin gepflegt (seit 0.3c/0.3d im Frontend).

### Version 0.3c beta: Vertragspartner im Frontend
Keine neuen Abhängigkeiten. Gleiche Regeln wie in 0.3b.

| Pfad | Inhalt |
|---|---|
| `/providers/` | Liste mit Live-Suche (Name, Ort, Kundennummer, Ansprechpartner, E-Mail) und Sortierung, Anzahl und Monatssumme der sichtbaren Verträge |
| `/providers/<id>/` | Detail mit Adresse, Kontakt, Paperless-Link, sichtbaren Verträgen samt Summe, Zugriff |
| `/providers/new/`, `/<id>/edit/`, `/<id>/delete/` | Anlegen (Recht `add_provider`), bearbeiten (Bearbeiter), löschen (Eigentümer) |
| `/providers/<id>/sharing/` | Eigentümer und Freigaben (Eigentümer, Superuser) |

- Löschen eines Partners, der noch in Verträgen verwendet wird, wird abgelehnt (`PROTECT`), ohne die Anzahl der Verträge zu nennen.
- Gleicher Name beim selben Eigentümer ergibt eine Formularmeldung.
- Vertragsseite verlinkt den Partner, wenn er lesbar ist; das Vertragsformular verlinkt „Neuer Vertragspartner“.
- Gemeinsame Bausteine in `web` für Kategorien und Verbraucher: `get_owned_object`/`OwnedObjectMixin` (404/403), `HtmxResultsMixin`, `OwnedSharingView` mit `web/sharing.html`, `OwnedModelFormMixin` (Eigentümer setzen, Eindeutigkeit pro Eigentümer, Auswahllisten), `web/includes/access_card.html`, `web/includes/sort_link.html`.

### Version 0.3d beta: Kategorien und Verbraucher im Frontend
Keine neuen Abhängigkeiten. Gleiche Regeln wie in 0.3b/0.3c.

| Pfad | Inhalt |
|---|---|
| `/consumers/`, `/consumers/<id>/`, `new/`, `edit/`, `delete/`, `sharing/` | Verbraucher wie Vertragspartner; Suche über Name und Nummer |
| `/categories/`, `/categories/<id>/`, `new/` (`?parent=<id>` für Unterkategorie), `edit/`, `delete/`, `sharing/` | Kategorien als Baum; Hauptkategorien zählen Verträge ihrer Unterkategorien mit |

- Kategorie: Als Hauptkategorie sind nur lesbare Hauptkategorien wählbar, nie die Kategorie selbst. `Category.clean()` verhindert, dass eine Kategorie mit Unterkategorien selbst Unterkategorie wird. Löschen nur ohne Verträge und Unterkategorien.
- Menü „Stammdaten“ (Vertragspartner, Kategorien, Verbraucher). Vertragsseite verlinkt lesbare Stammdaten, das Vertragsformular hat Links zum Anlegen.
- Verwaltung (`/admin/`) nur für Superuser: `config.admin.AdminSite` (`has_permission`), eingebunden über `config.apps.AdminConfig`. Andere angemeldete Benutzer werden zur Übersicht weitergeleitet. Link „Verwaltung“ im Benutzermenü nur für Superuser. Die Rechteprüfung je Objekt im Admin bleibt als zweite Absicherung.
- Gemeinsame Ansichten für Stammdaten in `web.views`: `MasterDataListView`, `MasterDataDetailView`, `MasterDataCreateView`, `MasterDataUpdateView`, `MasterDataDeleteView`; Templates `web/includes/object_actions.html`, `web/includes/contract_table.html`.

### Version 0.4 beta: Logos, Design, Single Sign-on
Neue Abhängigkeiten (freigegeben): `Pillow`, `mozilla-django-oidc` (5.x); Bootstrap Icons 1.11.3 lokal in `web/static/web/vendor/bootstrap-icons/`.

**Logo beim Vertragspartner**
- `Provider.logo` (ImageField, optional, Migration `0009`). Logik in `contracts.logos`: PNG, JPEG, WebP bis 2 MB, kein SVG; beim Speichern auf 256 px verkleinert und als PNG ohne Metadaten mit zufälligem Namen unter `logos/` abgelegt. Alte Dateien werden beim Ersetzen, Entfernen und Löschen gelöscht (Signale, nach erfolgreicher Transaktion).
- Ausgabe über `web.views.provider_logo` unter `/media/logos/<datei>` (= `Provider.logo.url`), nur mit Leserecht am Partner, sonst 404. Andere Mediendateien werden nicht ausgeliefert.
- Anzeige über `web/includes/provider_logo.html` (Platzhalter: Anfangsbuchstabe). In Vertragslisten nur, wenn der Partner lesbar ist (`contract_rows(..., user)` liefert `provider_visible`).

**Design**
- Gerüst mit Seitenleiste (`offcanvas-lg`, auf dem Handy ausklappbar), Benutzermenü unten in der Seitenleiste; eigenes Farbschema über CSS-Variablen in `web/static/web/app.css`, hell und dunkel; Tabellen als Karten (`card-table`); Anmeldeseite ohne Seitenleiste (Block `auth_content`).

**Single Sign-on (OpenID Connect, Synology SSO Server)**
- `web.oidc`: `SSOBackend`, `SSORequestView`, `SSOCallbackView`; URLs unter `/oidc/` (404, solange `OIDC_ENABLED` aus ist). Konfiguration nur über Umgebungsvariablen (`OIDC_*`), Endpunkte aus `OIDC_OP_BASE_URL` (Synology `/webman/sso/…`).
- Zuordnung über den Claim `OIDC_USERNAME_CLAIM` (Standard `username`), ohne Groß-/Kleinschreibung, nicht über die E-Mail. Unbekannte Benutzer nur mit `OIDC_CREATE_USERS=True` anlegen (Gruppen „Benutzer“ und „Alle“, kein lokales Passwort, nie Superuser/Mitarbeiter). Deaktivierte Benutzer werden abgewiesen. Lokale Anmeldung bleibt möglich.
- Benutzer ohne lokales Passwort sehen „Passwort ändern“ nicht.

### Version 0.5 beta: E-Mail, Passwort-Reset, Erinnerungen
Neue Abhängigkeit (freigegeben): `django-q2` 1.11. Neuer Service `worker` (`python manage.py qcluster`, gleiches Image, startet nach `app`).

**E-Mail**
- SMTP nur über Umgebungsvariablen (`EMAIL_*`, `DEFAULT_FROM_EMAIL`, `APP_BASE_URL`), erster Anbieter IONOS. Ohne `EMAIL_HOST` Console-Backend.
- `contracts.mail`: `build_mail(name, context, to, language)` und `send_mail_to_user(name, context, user)`; Vorlagen `contracts/templates/emails/<name>_subject.txt`, `<name>.txt`, `<name>.html` (gemeinsames Layout `emails/base.html`), Sprache des Empfängers (`UserPreferences.language`).

**Passwort-Reset und E-Mail-Adresse**
- Djangos Passwort-Reset unter `/accounts/password/reset/…` mit eigenen Templates (`web/password_reset/`, ohne Seitenleiste über `auth_layout`). Link „Passwort vergessen?“ auf der Anmeldeseite.
- E-Mail-Adresse pflegt jeder Benutzer unter Einstellungen (eindeutig, ohne Groß-/Kleinschreibung).

**Erinnerung (`contracts.Reminder`, Migration `0010`)**
- Genau eine pro Vertrag (OneToOne `contract.reminder`). Felder: `enabled`, `remind_on`, `lead_days` (0–60), `reason` (Kündigung, Vertragsüberprüfung, Sonstiges), `note`, `recipients` (Benutzer), intern `last_sent_on`, `completed_on`.
- Rechte folgen dem Vertrag (ändern mit Bearbeitungsrecht). Empfänger nur aus `perms.users_who_can_view(contract)`; beim Versand erneut geprüft (aktiv, E-Mail, Leserecht).
- Versand (`contracts.reminders.send_due_reminders`): täglich von `remind_on - lead_days` bis einschließlich `remind_on`, höchstens einmal pro Tag; ab `remind_on` aus und erledigt; verpasste Tage nicht nachholen. Zeitplan in django-q2 (`ensure_reminder_schedule`, täglich `REMINDER_SEND_TIME`), manuell `manage.py send_reminders`.
- Frontend: Karte auf der Vertragsseite, Formular `/contracts/<id>/reminder/`. Admin: Inline am Vertrag.

### Version 0.6 beta: Verwaltung im Frontend
Keine neuen Abhängigkeiten. Bereich unter `/manage/` (`web.manage_views`, `web.manage_forms`, Templates `web/manage/`), Abschnitt „Verwaltung“ in der Seitenleiste.

**Rolle Administrator**
- Neue Standardgruppe **„Administratoren“** (angelegt in `ensure_default_groups`) mit den globalen Rechten `auth.{view,add,change,delete}_{user,group}`. Prüfung über `perms.can_manage(user, action, model)`.
- Grenzen in `contracts.permissions`: keine Superuser bearbeiten (`can_edit_user`), eigene Gruppen/Status nie ändern (`can_change_status`), „Administratoren“ und Superuser-Status nur durch Superuser (`assignable_groups`), Löschen nur ohne eigene Objekte (`can_delete_user`). „Alle“ automatisch, Standardgruppen (`PROTECTED_GROUPS`) nicht umbenennen oder löschen.
- Django-Backend (`/admin/`) bleibt nur für Superuser; Link „Django-Verwaltung“ im Benutzermenü.

**Seiten**
| Pfad | Inhalt | Wer |
|---|---|---|
| `/manage/users/`, `new/`, `<id>/`, `<id>/edit/`, `<id>/mail/`, `<id>/active/`, `<id>/delete/` | Benutzer: Liste, anlegen mit Einladung, bearbeiten, Einladung/Reset-Link, deaktivieren, löschen | Administratoren, Superuser |
| `/manage/users/<id>/transfer/` | Alle Objekte übertragen (`perms.transfer_all_ownership`, Namenskonflikte vorher prüfen) | nur Superuser |
| `/manage/groups/`, `new/`, `<id>/`, `<id>/delete/` | Gruppen und Mitglieder | Administratoren, Superuser |
| `/manage/reminders/` | Erinnerungen aller Verträge | nur Superuser |
| `/manage/system/` | Version (`APP_VERSION`), Worker, E-Mail, SSO, Test-E-Mail | Administratoren, Superuser |

- Neue Benutzer ohne lokales Passwort; Einladung (`emails/invitation*`) mit Link zum Passwort wählen. `PASSWORD_RESET_TIMEOUT` 7 Tage für Einladung und Reset.

### Version 0.7 beta: Dokumente, SSO-Beschriftung
Keine neuen Abhängigkeiten.

**Dokumente (`contracts.Document`, Migrationen `0011`–`0013`)**
- Pro Vertrag beliebig viele (`contract.documents`), Quelle `upload` (Datei) oder `paperless` (Name und Dokument-ID, eindeutig pro Vertrag); genau eine Quelle (CheckConstraint und `clean()`).
- Logik in `contracts.documents`: PDF, JPG, PNG, WebP, DOCX, XLSX, ODT, ODS, TXT, EML bis 20 MB. Ablage `documents/<Jahr>/<Referenz>/<Dateiname>` (Jahr aus der Referenz), Originalname bereinigt, bei Kollision `_2`, `_3` … Datei und leere Ordner werden mit dem Dokument gelöscht (Signal, nach erfolgreicher Transaktion).
- Rechte folgen dem Vertrag wie bei Kosteneinträgen (`perms.CONTRACT_CHILD_MODELS`). Ausgabe über `web.document_views.document_file` unter `/media/documents/<pfad>` (= `Document.file.url`), nur mit Leserecht, sonst 404; PDF und Bilder inline, sonst Download.
- `CostEntry.document` (ForeignKey, `SET_NULL`, nur Dokumente desselben Vertrags) ersetzt `CostEntry.paperless_document_id`; `0012` wandelt vorhandene IDs in Paperless-Dokumente um.
- Frontend: Karte „Dokumente“ auf der Vertragsseite mit Symbol für die Quelle, `/contracts/<id>/documents/new/` (`?source=upload|paperless`), `/documents/<id>/edit/`, `/documents/<id>/delete/` per HTMX (ohne JavaScript als eigene Seiten). Admin: Inline am Vertrag.

**SSO-Beschriftung**
- `web.SiteSettings` (genau ein Datensatz, `SiteSettings.load()`, Migration `web/0002`): `sso_login_label`, leer = „SSO Login“. Pflege unter Verwaltung › System (Administratoren, Superuser). `OIDC_LOGIN_LABEL` entfällt.

**Betrieb**
- `LOGGING`: Fehler (500) mit Traceback in `docker compose logs`; gunicorn mit `--no-control-socket`.

### Version 0.8a beta: KI-Assistent (Grundlage, Fragen zu Verträgen)
Neue Abhängigkeiten (freigegeben): `openai`, `anthropic`. Neue App `assistant`.

- Einstellungen nur über `.env`: `AI_ENABLED`, `AI_PROVIDER` (`openai` | `anthropic`), `AI_API_KEY`, `AI_MODEL`, `AI_BASE_URL`, `AI_MAX_TOOL_ROUNDS`, `AI_TIMEOUT`. Ohne `AI_ENABLED` liefern die Seiten 404 und der Link fehlt.
- Nutzung nur mit Recht `assistant.use_assistant` über die Standardgruppe **„KI“** (`perms.GROUP_AI`, geschützt) oder als Superuser: `perms.can_use_assistant`.
- `assistant.providers`: eine Schnittstelle (`complete(system, messages, tools, allow_tools)`), anbieterneutrales Nachrichtenformat; `OpenAIProvider` (Chat Completions mit Werkzeugen), `AnthropicProvider` (Messages API, Standardmodell `claude-sonnet-5-5`).
- Claude: Antworten im Originalformat speichern (`Message.provider_content`, mit Denkschritten) und unverändert zurückgeben; System-Prompt pro Unterhaltung festschreiben (`Conversation.system_prompt`, Datum an der Benutzer-Nachricht); Werkzeugliste nie ändern (letzte Runde mit `tool_choice: none`); `AI_EFFORT`, `AI_MAX_TOKENS` (16000); Ablehnungen (`stop_reason: refusal`) als Hinweis im Chat; ohne `AI_BASE_URL` serverseitiger Fallback (`fallbacks: "default"`).
- `assistant.tools`: nur lesende Werkzeuge mit den Rechten des Benutzers (`search_contracts`, `get_contract`, `cost_overview`, `search_master_data`). **Keine Werkzeuge zum Löschen**, auch später nicht; Änderungen später nur als Vorschlag mit Freigabe durch den Benutzer.
- `assistant.agent.answer` läuft im Worker (`assistant.tasks`), Verlauf in `Conversation`/`Message`; Chat unter `/assistant/` mit HTMX-Abfrage alle 2 Sekunden. Jeder sieht nur seine Unterhaltungen.
- Worker: `poll` 2 s, `workers` 2.
- Fehler aus 0.7 behoben: Abbrechen im Kostenformular (Feld `document` verdeckte im `onclick` das globale `document`).

### Version 0.8b beta: KI-Assistent – neue Verträge aus Dokumenten
Neue Abhängigkeiten (freigegeben): `pypdf`; im Image `tesseract-ocr` (`deu`, `eng`) und `poppler-utils`. Keine Internetsuche (Kosten).

- Upload im Chat (PDF, JPG, PNG, WebP, TXT, EML bis 20 MB) als `assistant.Attachment` unter `assistant/<Unterhaltung>/`; Text wird im Worker lokal erkannt (`assistant.extraction`: pypdf, sonst `pdftoppm` + `tesseract`; EML mit angehängten PDFs). Erkannte Daten speichert der Assistent mit `save_document_facts` am Anhang. Nur wenn lokal nichts erkannt wird, geht die Datei an das Modell (nur Claude, PDF/Bild bis 10 MB).
- Änderungen nur als Vorschlag (`assistant.Proposal`, `assistant.proposals`): Vertragspartner, Kategorie, Verbraucher, Vertrag (mit erstem Kosteneintrag und Dokument) anlegen, Dokument einem Vertrag zuordnen. Karte mit allen Werten, „Freigeben“/„Ablehnen“ (`/assistant/proposals/<id>/<approve|reject>/`). Prüfung beim Erstellen und erneut bei der Freigabe, mit den Rechten des Benutzers; danach Ereignis (`Message.Role.EVENT`) an das Modell und neue Antwort. **Keine Vorschläge zum Löschen.**
- Werkzeuge pro Unterhaltung festgelegt (`Conversation.toolset`: `read` aus 0.8a, `documents` ab 0.8b), weil Claude keine Änderung der Werkzeugliste erlaubt.

### Version 0.9 beta: Backup und Import, optionale Vertragsfelder
Keine neuen Abhängigkeiten. Letzte Beta vor der öffentlichen Version.

- `contracts.backup`: ein Backup ist eine ZIP-Datei mit `manifest.json` (Format, Version, Migrationsstand, Anzahl je Modell), `data.json` (alle Modelle aus `BACKUP_MODELS`, Verweise auf Benutzer und Gruppen als Namen) und `media/…` (Logos, Dokumente, Anhänge des Assistenten). Nicht enthalten: Sitzungen, Login-Sperren, Aufgaben des Workers, `.env`.
- **Passwörter werden nie exportiert**, nur ob ein Benutzer eines hatte (`""` oder `"!"`). Nach dem Import hat kein Benutzer ein Passwort; wer eines hatte, bekommt `PASSWORD_RESET_MARKER` und wählt über „Passwort vergessen?“ ein neues (`WebPasswordResetForm.get_users`). Benutzer nur mit SSO bleiben ohne lokales Passwort.
- Der Import ersetzt den gesamten Bestand (kein Zusammenführen), nur bei gleicher `APP_VERSION` und gleichem Migrationsstand; Datenbank in einer Transaktion, Dateien erst danach; alle Sitzungen enden.
- Oberfläche nur für Superuser: `/manage/backup/` (herunterladen, hochladen und prüfen), `/manage/backup/download/`, `/manage/backup/import/` (bestätigen oder abbrechen). Der Upload wird bis zur Bestätigung abgelegt (`store_upload`).
- Kommandozeile: `manage.py export_backup [--output Datei|-]`, `manage.py import_backup Datei [--yes]`.
- Neue Modelle müssen in `BACKUP_MODELS` (und Dateifelder in `FILE_FIELDS`) eingetragen werden.
- Fehler behoben: Chat des Assistenten folgt neuen Nachrichten.
- `Contract.consumer` und `Contract.start_date` sind optional (Migration `0014`). Anzeige „–“; im Assistenten sind `consumer_id` und `start_date` keine Pflichtangaben mehr, ohne Vertragsbeginn gilt der erste Kosteneintrag ab heute.

### Docker-Image (nach 0.9 beta)
- `.github/workflows/docker-image.yml`: bei einem Tag `v…` Image bauen, Tests im Image gegen PostgreSQL ausführen, dann für `amd64` und `arm64` nach `ghcr.io/brainx9/contracts-manager` schieben (`<Version>` und `latest`). Manuell gestartet: nur Bauen und Tests. Docker Hub erst, wenn das Repository öffentlich ist (im Workflow vorbereitet).
- `docker-compose.yml` lädt das fertige Image (`APP_IMAGE_TAG`, Standard `latest`), gebaut wird nur noch in der Entwicklung (`docker-compose.dev.yml`: `build: .`, Image `contracts-manager-app`).
- Betrieb nur mit Docker Compose: Ordner `/volume1/docker/contracts-manager` mit `docker-compose.yml`, `.env`, `db/`, `media/` und `backups/` (relative Pfade `${DATA_PATH:-.}/…`, `DATA_PATH` nur noch optional für bestehende Installationen mit `app/` und `data/`). Dateien per `curl` von GitHub (raw-Adresse), kein git auf der Synology.
- Container heißen `contracts-manager-app`, `contracts-manager-db` und `contracts-manager-worker` (`container_name`); die Compose-Dienste bleiben `app`, `db`, `worker`.
- Backup auf der Synology als Skript in der Aufgabenplanung mit `docker exec contracts-manager-db pg_dump …`; `scripts/backup.sh` nur noch für Installationen mit Repository.
- Veröffentlichung: `APP_VERSION` setzen, nach `main` mergen, Tag `vX.Y.Z` setzen und pushen (nur auf Anweisung).

### Version 0.9.1 beta: Administrator aus der .env
Keine neuen Abhängigkeiten, keine Migration. Änderung steckt im Image (`docker/entrypoint.sh`), daher neuer Tag `v0.9.1-beta`; Backups aus 0.9 beta lassen sich wegen der Versionsregel nicht importieren.

- `manage.py ensure_admin` (aufgerufen im Startskript nach den Migrationen): legt aus `ADMIN_USERNAME`, `ADMIN_PASSWORD` und optional `ADMIN_EMAIL` einen Superuser an, **nur wenn noch keiner existiert**. Spätere Änderungen der Variablen bleiben wirkungslos, es wird nie ein Passwort überschrieben. Prüfung mit Djangos Passwortregeln und Feldvalidierung; Fehler stehen im Log (nie das Passwort) und brechen den Start nicht ab. `createsuperuser` bleibt als Alternative.
- `.env.example` ist seit 0.9.1 englisch und für die Veröffentlichung bereinigt (keine Hinweise auf einzelne Entwicklungsumgebungen, neutrale Beispieladressen). Neue Variablen dort englisch dokumentieren.

### Version 0.1.0: erste öffentliche Version
- Fasst die Beta-Versionen 0.2 bis 0.9.1 der privaten Entwicklung zusammen. Das Repository ist öffentlich, mit frischer Historie (ein Anfangs-Commit). Das bisherige private Repository liegt als Archiv (`contracts-manager-archive`).
- `APP_VERSION` ist `0.1.0`, Tag `v0.1.0`, Image `ghcr.io/brainx9/contracts-manager:0.1.0` und `latest`. Für Compose-Datei und Image ist kein Token mehr nötig (Paket auf `ghcr.io` öffentlich). Backups aus den Beta-Versionen lassen sich wegen der Versionsregel nicht importieren.
- Die Abschnitte „Version 0.x beta“ oben beschreiben die Entstehung und bleiben als Spezifikation erhalten.

### Spätere Phasen (noch nicht umsetzen, nur Schnittstellen nicht verbauen)
- REST-API mit `django-ninja` (automatische OpenAPI/Swagger-Doku)
- MCP-Server auf Basis derselben Modelle
- E-Mail-Client (IMAP, über den vorhandenen Worker `django-q2`)
- RAG über Vertragsdokumente mit pgvector
- KI-Funktionen (z. B. Zusammenfassungen, Kündigungsfristen erkennen)

## Qualität
- Jede Phase endet mit lauffähigem `docker compose up` und einer kurzen Anleitung, wie das Ergebnis geprüft wird.
- Schreibe für die Kernlogik (aktueller Betrag, Summenbildung) einfache Tests mit `pytest-django` oder dem Django-Testrunner.
- Wenn Anforderungen unklar sind, frage nach, statt zu raten.

## Git
- Das Repository ist initialisiert (Branch `main`), Remote `origin` ist `https://github.com/BrainX9/contracts-manager.git` (GitHub).
- Arbeite pro Phase auf einem eigenen Branch (`phase-1`, `phase-2`, ...).
- Committe nur nach Freigabe des Maintainers, in kleinen Schritten, Commit-Messages auf Deutsch im Format "Phase 1: ...".
- Committe niemals `.env`, Datenbankdateien oder Secrets. Prüfe vor jedem Commit `git status`.
- Kein `git push --force` und kein `git reset --hard` ohne Rückfrage.
- Pushe nur auf ausdrückliche Anweisung des Maintainers.
