"""
Backup und Import des gesamten Datenbestands (Version 0.9 beta).

Ein Backup ist eine ZIP-Datei:

    manifest.json   Format, App-Version, Stand der Migrationen, Anzahl je Modell
    data.json       alle Datensätze im Django-Format (Python-Serializer)
    media/…         Logos, Vertragsdokumente und Anhänge des Assistenten

Regeln:
- Passwörter werden nicht exportiert, nur ob ein Benutzer eines hatte.
  Nach einem Import hat kein Benutzer ein lokales Passwort. Wer vorher
  eines hatte, wählt über "Passwort vergessen?" ein neues
  (``PASSWORD_RESET_MARKER``, siehe ``web.forms.WebPasswordResetForm``);
  sonst ``manage.py changepassword``.
- Der Import ersetzt den gesamten Bestand (kein Zusammenführen) und nimmt
  nur Backups derselben App-Version und desselben Migrationsstands an.
- Nicht enthalten: Sitzungen, Login-Sperren (axes), Aufgaben des Workers
  (django-q2), das Protokoll des Django-Admin und die .env.

Oberfläche (web.manage_views) und Kommandozeile (export_backup,
import_backup) nutzen dieselben Funktionen.
"""

import json
import posixpath
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth.hashers import is_password_usable
from django.contrib.sessions.models import Session
from django.core import serializers
from django.core.files.storage import default_storage
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone
from django.utils.translation import gettext as _

FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
DATA_NAME = "data.json"
MEDIA_PREFIX = "media/"

# Steht nach einem Import im Feld ``password`` der Benutzer, die im Backup ein
# lokales Passwort hatten. Beginnt mit "!", gilt für Django also als "kein
# Passwort": Anmelden ist damit nicht möglich, "Passwort vergessen?" schon.
PASSWORD_RESET_MARKER = "!password-reset-after-import"

# Modelle in der Reihenfolge, in der sie beim Import geladen werden.
# Der Import nimmt ausschließlich diese Modelle an.
BACKUP_MODELS = (
    "auth.group",
    "auth.user",
    "web.userpreferences",
    "web.sitesettings",
    "contracts.provider",
    "contracts.category",
    "contracts.consumer",
    "contracts.contract",
    "contracts.document",
    "contracts.costentry",
    "contracts.reminder",
    "contracts.provideruserobjectpermission",
    "contracts.providergroupobjectpermission",
    "contracts.categoryuserobjectpermission",
    "contracts.categorygroupobjectpermission",
    "contracts.consumeruserobjectpermission",
    "contracts.consumergroupobjectpermission",
    "contracts.contractuserobjectpermission",
    "contracts.contractgroupobjectpermission",
    "assistant.conversation",
    "assistant.message",
    "assistant.attachment",
    "assistant.proposal",
)

# Dateifelder, deren Dateien ins Backup gehören: (Modell, Feld)
FILE_FIELDS = (
    ("contracts.provider", "logo"),
    ("contracts.document", "file"),
    ("assistant.attachment", "file"),
)

# Ordner unter MEDIA_ROOT, die der Import ersetzt. Dateien im Backup müssen
# in einem dieser Ordner liegen.
MEDIA_DIRS = ("logos", "documents", "assistant")

# Apps, deren Migrationsstand beim Import übereinstimmen muss
MIGRATION_APPS = ("auth", "contenttypes", "guardian", "contracts", "web", "assistant")

# Obergrenze für data.json (entpackt), als Schutz vor defekten Archiven
MAX_DATA_BYTES = 1024 * 1024 * 1024


class BackupError(Exception):
    """Das Backup kann nicht gelesen oder nicht importiert werden."""


@dataclass
class BackupInfo:
    """Inhalt eines Backups laut manifest.json, nach der Prüfung."""

    app_version: str
    created_at: str
    counts: dict
    media_files: int
    media_bytes: int
    missing_files: list = field(default_factory=list)

    @property
    def total_objects(self):
        return sum(self.counts.values())


def backup_models():
    return [apps.get_model(label) for label in BACKUP_MODELS]


def migration_state():
    """Letzte Migration je App laut Code: {"contracts": ["0013_…"], …}."""
    graph = MigrationLoader(connection).graph
    return {app: sorted(name for _app, name in graph.leaf_nodes(app)) for app in MIGRATION_APPS}


def default_filename(now=None):
    now = timezone.localtime(now)
    return f"contracts-manager-backup-{now:%Y-%m-%d_%H%M%S}.zip"


# --- Export -----------------------------------------------------------------


def serialize_objects():
    """
    Alle Datensätze als Liste im Format des Python-Serializers. Verweise auf
    Benutzer, Gruppen und Rechte als natürliche Schlüssel (Name statt ID),
    Passwörter werden entfernt: "" = hatte ein Passwort, "!" = hatte keines
    (z. B. nur Single Sign-on).
    """
    objects = []
    counts = {}
    for model in backup_models():
        rows = serializers.serialize(
            "python", model._default_manager.order_by("pk"), use_natural_foreign_keys=True,
        )
        if model._meta.label_lower == "auth.user":
            for row in rows:
                row["fields"]["password"] = "" if is_password_usable(row["fields"]["password"]) else "!"
        counts[model._meta.label_lower] = len(rows)
        objects.extend(rows)
    return objects, counts


def referenced_files():
    """Namen aller Dateien, auf die ein Datensatz verweist (ohne Duplikate)."""
    names = []
    for label, field_name in FILE_FIELDS:
        model = apps.get_model(label)
        names.extend(
            model._default_manager.exclude(**{field_name: ""})
            .exclude(**{f"{field_name}__isnull": True})
            .values_list(field_name, flat=True)
        )
    return sorted(set(names))


def write_backup(target):
    """
    Schreibt ein Backup nach ``target`` (Pfad oder zum Schreiben geöffnete
    Binärdatei) und gibt die BackupInfo zurück. Dateien, die in der Datenbank
    stehen, aber fehlen, werden übersprungen und in ``missing_files`` genannt.
    """
    # Eine Transaktion, damit alle Tabellen denselben Stand zeigen
    with transaction.atomic():
        objects, counts = serialize_objects()
        file_names = referenced_files()

    media_files = 0
    media_bytes = 0
    missing = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(DATA_NAME, json.dumps(objects, cls=DjangoJSONEncoder, ensure_ascii=False))
        for name in file_names:
            if not is_safe_media_name(name) or not default_storage.exists(name):
                missing.append(name)
                continue
            # Unkomprimiert: PDFs und Bilder sind es bereits
            with default_storage.open(name, "rb") as source, archive.open(
                zipfile.ZipInfo(MEDIA_PREFIX + name, date_time=timezone.localtime().timetuple()[:6]), "w",
                force_zip64=True,
            ) as destination:
                shutil.copyfileobj(source, destination)
            media_files += 1
            media_bytes += default_storage.size(name)
        manifest = {
            "format": FORMAT_VERSION,
            "app": "contracts-manager",
            "app_version": settings.APP_VERSION,
            "created_at": timezone.now().isoformat(),
            "migrations": migration_state(),
            "counts": counts,
            "media_files": media_files,
            "media_bytes": media_bytes,
            "missing_files": missing,
        }
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
    return info_from_manifest(manifest)


# --- Prüfung ----------------------------------------------------------------


def is_safe_media_name(name):
    """
    Nur relative Pfade innerhalb der bekannten Ordner, ohne "..", ohne
    Backslash und ohne leere Teile.
    """
    if not name or "\\" in name or name.startswith("/") or posixpath.normpath(name) != name:
        return False
    parts = name.split("/")
    return len(parts) >= 2 and parts[0] in MEDIA_DIRS and ".." not in parts


def info_from_manifest(manifest):
    return BackupInfo(
        app_version=str(manifest.get("app_version", "")),
        created_at=str(manifest.get("created_at", "")),
        counts={label: int(manifest.get("counts", {}).get(label, 0)) for label in BACKUP_MODELS},
        media_files=int(manifest.get("media_files", 0)),
        media_bytes=int(manifest.get("media_bytes", 0)),
        missing_files=list(manifest.get("missing_files", [])),
    )


def read_manifest(archive):
    try:
        manifest = json.loads(archive.read(MANIFEST_NAME))
    except (KeyError, ValueError):
        raise BackupError(_("This file is not a Contracts Manager backup."))
    if not isinstance(manifest, dict) or manifest.get("app") != "contracts-manager":
        raise BackupError(_("This file is not a Contracts Manager backup."))
    if manifest.get("format") != FORMAT_VERSION:
        raise BackupError(_("This backup has a format that this version cannot read."))
    if manifest.get("app_version") != settings.APP_VERSION or manifest.get("migrations") != migration_state():
        raise BackupError(
            _("This backup was created with version %(backup)s. It can only be imported into the same version "
              "(installed: %(installed)s).")
            % {"backup": manifest.get("app_version") or "?", "installed": settings.APP_VERSION}
        )
    return manifest


def media_members(archive):
    """Dateien unter media/ im Archiv; bricht bei unzulässigen Pfaden ab."""
    members = []
    for member in archive.infolist():
        if member.is_dir() or member.filename in (MANIFEST_NAME, DATA_NAME):
            continue
        name = member.filename[len(MEDIA_PREFIX):] if member.filename.startswith(MEDIA_PREFIX) else ""
        if not is_safe_media_name(name):
            raise BackupError(_("The backup contains a file with an invalid path: %(name)s") % {"name": member.filename})
        members.append((name, member))
    return members


def open_archive(source):
    try:
        return zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError):
        raise BackupError(_("This file is not a Contracts Manager backup."))


def inspect_backup(source):
    """
    Prüft ein Backup, ohne etwas zu ändern: Format, Version, Pfade und ob
    data.json lesbar ist und nur bekannte Modelle enthält. Gibt die
    BackupInfo zurück oder löst BackupError aus.
    """
    with open_archive(source) as archive:
        manifest = read_manifest(archive)
        media_members(archive)
        load_objects(archive)
    return info_from_manifest(manifest)


def load_objects(archive):
    try:
        if archive.getinfo(DATA_NAME).file_size > MAX_DATA_BYTES:
            raise BackupError(_("The backup is damaged."))
        objects = json.loads(archive.read(DATA_NAME))
    except (KeyError, ValueError, zipfile.BadZipFile):
        raise BackupError(_("The backup is damaged."))
    if not isinstance(objects, list):
        raise BackupError(_("The backup is damaged."))
    for row in objects:
        if not isinstance(row, dict) or row.get("model") not in BACKUP_MODELS:
            raise BackupError(_("The backup is damaged."))
    return objects


# --- Import -----------------------------------------------------------------


def flush_tables():
    """
    Leert alle Tabellen der Backup-Modelle samt ihrer Zwischentabellen und
    setzt die Zähler zurück. TRUNCATE löst keine Signale aus, die Dateien
    ersetzt der Import danach selbst. CASCADE leert auch abhängige Tabellen
    außerhalb des Backups (Protokoll des Django-Admin).
    """
    tables = []
    for model in backup_models():
        tables.append(model._meta.db_table)
        tables.extend(
            m2m.remote_field.through._meta.db_table
            for m2m in model._meta.local_many_to_many
            if m2m.remote_field.through._meta.auto_created
        )
    statements = connection.ops.sql_flush(no_style(), tables, reset_sequences=True, allow_cascade=True)
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            # TRUNCATE scheitert, solange in der Transaktion noch Prüfungen von
            # Fremdschlüsseln ausstehen: erst abarbeiten, danach wieder bis zum
            # Ende aufschieben (Datensätze verweisen beim Laden aufeinander).
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for statement in statements:
            cursor.execute(statement)
        if connection.vendor == "postgresql":
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")


def reset_sequences():
    """Zähler der IDs hinter die importierten Datensätze setzen."""
    statements = connection.ops.sequence_reset_sql(no_style(), backup_models())
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def load_into_database(objects):
    try:
        deferred = []
        # Einzeln laden und sofort speichern: Verweise über natürliche
        # Schlüssel (Benutzer, Gruppen) finden nur gespeicherte Datensätze.
        for item in serializers.deserialize("python", objects, handle_forward_references=True):
            if item.object._meta.label_lower == "auth.user":
                # Auch ein von Hand eingetragener Hash wird nicht übernommen
                if (item.object.password or "").startswith("!"):
                    item.object.set_unusable_password()
                else:
                    item.object.password = PASSWORD_RESET_MARKER
            item.save()
            if item.deferred_fields:
                deferred.append(item)
        for item in deferred:
            item.save_deferred_fields()
        # Verweise jetzt prüfen statt erst am Ende der Transaktion, damit ein
        # fehlerhaftes Backup als solches gemeldet wird
        connection.check_constraints()
    except BackupError:
        raise
    except Exception as error:  # Serializer melden Fehler mit vielen Typen
        raise BackupError(_("The backup is damaged.")) from error


def extract_media(archive, members, directory):
    for name, member in members:
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, open(destination, "wb") as output:
            shutil.copyfileobj(source, output)


def replace_media(directory):
    """Ersetzt die Ordner unter MEDIA_ROOT durch die entpackten Dateien."""
    media_root = Path(settings.MEDIA_ROOT)
    for name in MEDIA_DIRS:
        shutil.rmtree(media_root / name, ignore_errors=True)
        if (directory / name).exists():
            (directory / name).rename(media_root / name)


def import_backup(source):
    """
    Ersetzt den gesamten Bestand durch das Backup ``source`` (Pfad oder
    Binärdatei) und gibt die BackupInfo zurück.

    Ablauf: Archiv prüfen und Dateien in einen Zwischenordner unter
    MEDIA_ROOT entpacken; Datenbank in einer Transaktion leeren und füllen;
    erst nach deren Erfolg die Dateien austauschen. Schlägt etwas vor dem
    Austausch fehl, bleibt alles unverändert. Alle Sitzungen enden.
    """
    media_root = Path(settings.MEDIA_ROOT)
    staging = media_root / f".import-{uuid.uuid4().hex}"
    with open_archive(source) as archive:
        manifest = read_manifest(archive)
        members = media_members(archive)
        objects = load_objects(archive)
        try:
            staging.mkdir(parents=True)
            try:
                extract_media(archive, members, staging)
            except (OSError, zipfile.BadZipFile) as error:
                raise BackupError(_("The backup is damaged.")) from error
            with transaction.atomic():
                flush_tables()
                load_into_database(objects)
                reset_sequences()
                Session.objects.all().delete()
            replace_media(staging)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return info_from_manifest(manifest)


# --- Zwischenablage für den Import in der Oberfläche ---------------------------


def upload_directory():
    directory = Path(tempfile.gettempdir()) / "contracts-manager-import"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def store_upload(uploaded_file):
    """Legt eine hochgeladene Datei bis zur Bestätigung ab; gibt die Kennung zurück."""
    token = uuid.uuid4().hex
    with open(upload_directory() / f"{token}.zip", "wb") as output:
        for chunk in uploaded_file.chunks():
            output.write(chunk)
    return token


def upload_path(token):
    """Pfad zu einer abgelegten Datei oder None (unbekannte oder ungültige Kennung)."""
    if not token or not all(char in "0123456789abcdef" for char in token):
        return None
    path = upload_directory() / f"{token}.zip"
    return path if path.is_file() else None


def discard_upload(token):
    path = upload_path(token)
    if path:
        path.unlink(missing_ok=True)
