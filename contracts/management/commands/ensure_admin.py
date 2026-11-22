import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Seit Version 0.8.0 nur noch ein Hinweis: ADMIN_USERNAME, ADMIN_PASSWORD und ADMIN_EMAIL "
        "legen keinen Administrator mehr an. Den ersten Administrator legt man im Browser an "
        "(beim ersten Aufruf einer neuen Installation). Wird beim Start des Containers "
        "aufgerufen (docker/entrypoint.sh)."
    )

    VARIABLES = ("ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_EMAIL")

    def handle(self, *args, **options):
        # Bestehende Administratoren bleiben unberührt: sie sind normale Benutzer
        # in der Datenbank. Die Variablen werden nur noch erkannt, nie ausgewertet.
        found = [name for name in self.VARIABLES if os.environ.get(name, "").strip()]
        if found:
            self.stdout.write(self.style.WARNING(
                f"{', '.join(found)} wird seit Version 0.8.0 nicht mehr ausgewertet und kann aus der "
                ".env bzw. der Compose-Datei entfernt werden. Bestehende Administratoren funktionieren "
                "weiter; bei einer neuen Installation legt man den Administrator beim ersten Aufruf "
                "im Browser an."
            ))
