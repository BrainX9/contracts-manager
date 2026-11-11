import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Legt den ersten Administrator (Superuser) aus ADMIN_USERNAME, ADMIN_PASSWORD und "
        "optional ADMIN_EMAIL an, aber nur, wenn noch kein Superuser existiert. "
        "Wird beim Start des Containers aufgerufen (docker/entrypoint.sh). "
        "Ohne diese Variablen geschieht nichts."
    )

    def handle(self, *args, **options):
        username = os.environ.get("ADMIN_USERNAME", "").strip()
        password = os.environ.get("ADMIN_PASSWORD", "")
        email = os.environ.get("ADMIN_EMAIL", "").strip()

        if not username and not password:
            return
        if not username or not password:
            self.fail("ADMIN_USERNAME und ADMIN_PASSWORD müssen zusammen gesetzt sein.")
            return

        User = get_user_model()
        # Nur beim ersten Start. Spätere Änderungen der Variablen bleiben wirkungslos,
        # damit kein Passwort versehentlich überschrieben wird.
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Es gibt schon einen Administrator, ADMIN_USERNAME und ADMIN_PASSWORD werden ignoriert.")
            return
        if User.objects.filter(username__iexact=username).exists():
            self.fail(f"Der Benutzername '{username}' ist schon an einen Benutzer ohne Administratorrechte vergeben.")
            return
        if email and User.objects.filter(email__iexact=email).exists():
            self.fail("ADMIN_EMAIL gehört schon einem anderen Benutzer.")
            return

        user = User(username=username, email=email)
        try:
            user.full_clean(exclude=["password"])
            validate_password(password, user)
        except ValidationError as error:
            # Das Passwort selbst kommt nie in die Ausgabe
            self.fail("ADMIN_USERNAME oder ADMIN_PASSWORD ist ungültig: " + " ".join(error.messages))
            return

        User.objects.create_superuser(username, email, password)
        self.stdout.write(self.style.SUCCESS(f"Administrator '{username}' wurde angelegt."))
        self.stdout.write(
            "Bitte nach der ersten Anmeldung das Passwort in der App ändern "
            "und ADMIN_PASSWORD aus der .env entfernen."
        )

    def fail(self, message):
        """Meldet den Fehler, bricht den Start aber nicht ab."""
        self.stderr.write(self.style.ERROR("Administrator wurde nicht angelegt: " + message))
