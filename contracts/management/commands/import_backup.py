import shutil
import sys
import tempfile

from django.core.management.base import BaseCommand, CommandError

from contracts import backup


class Command(BaseCommand):
    help = (
        "Ersetzt ALLE Daten und Dateien durch ein Backup aus export_backup. "
        "Danach hat kein Benutzer ein Passwort: neu setzen mit 'manage.py changepassword <Benutzer>' "
        "oder über 'Passwort vergessen?'. Mit '-' von der Standardeingabe (dann immer mit --yes), z. B. "
        "docker compose exec -T app python manage.py import_backup --yes - < backup.zip"
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="ZIP-Datei aus export_backup, '-' für die Standardeingabe")
        parser.add_argument("--yes", action="store_true", help="Nicht nachfragen")

    def handle(self, *args, **options):
        if options["path"] != "-":
            return self.run_import(options["path"], options)
        if not options["yes"]:
            raise CommandError("Beim Import von der Standardeingabe ist --yes nötig (keine Rückfrage möglich).")
        # ZIP braucht eine Datei zum Zurückspringen, daher über eine Zwischendatei
        with tempfile.NamedTemporaryFile(suffix=".zip") as buffer:
            shutil.copyfileobj(sys.stdin.buffer, buffer)
            buffer.flush()
            self.run_import(buffer.name, options)

    def run_import(self, path, options):
        try:
            info = backup.inspect_backup(path)
        except backup.BackupError as error:
            raise CommandError(str(error))
        self.stdout.write(
            f"Backup vom {info.created_at}, Version {info.app_version}: "
            f"{info.total_objects} Datensätze, {info.media_files} Dateien"
        )
        if not options["yes"]:
            answer = input("Alle vorhandenen Daten und Dateien werden ersetzt. Fortfahren? [ja/NEIN] ")
            if answer.strip().lower() not in ("ja", "j", "yes", "y"):
                raise CommandError("Abgebrochen, nichts wurde geändert.")
        try:
            backup.import_backup(path)
        except backup.BackupError as error:
            raise CommandError(str(error))
        self.stdout.write(self.style.SUCCESS("Import abgeschlossen."))
        self.stdout.write(
            "Kein Benutzer hat jetzt ein Passwort. Neu wählen über 'Passwort vergessen?' auf der "
            "Anmeldeseite (wer vorher eines hatte) oder setzen mit: python manage.py changepassword <Benutzer>"
        )
