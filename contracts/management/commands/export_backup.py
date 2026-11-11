import shutil
import sys
import tempfile

from django.core.management.base import BaseCommand
from django.template.defaultfilters import filesizeformat

from contracts import backup


class Command(BaseCommand):
    help = (
        "Schreibt ein Backup aller Daten und Dateien als ZIP (ohne Passwörter). "
        "Mit '--output -' auf die Standardausgabe, z. B. "
        "docker compose exec -T app python manage.py export_backup --output - > backup.zip"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output", "-o", default=None,
            help="Zieldatei, '-' für die Standardausgabe (Standard: contracts-manager-backup-<Datum>.zip)",
        )

    def handle(self, *args, **options):
        output = options["output"] or backup.default_filename()
        if output == "-":
            # ZIP braucht eine Datei zum Zurückspringen, daher über eine Zwischendatei
            with tempfile.TemporaryFile() as buffer:
                info = backup.write_backup(buffer)
                buffer.seek(0)
                shutil.copyfileobj(buffer, sys.stdout.buffer)
            report = self.stderr
        else:
            info = backup.write_backup(output)
            report = self.stdout
            report.write(f"Backup geschrieben: {output}")
        report.write(
            f"{info.total_objects} Datensätze, {info.media_files} Dateien ({filesizeformat(info.media_bytes)})"
        )
        for name in info.missing_files:
            report.write(f"Datei fehlt und wurde übersprungen: {name}")
