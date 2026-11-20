import re
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)
README = BASE_DIR / "README.md"

# Setzt docker-compose.yml selbst; stehen im README, aber nicht in .env.example
SET_BY_COMPOSE = {"DATABASE_URL", "MEDIA_ROOT"}

# Variablen, die nicht in config/settings.py oder docker-compose.yml gelesen
# werden: ensure_admin, Dockerfile (gunicorn) und scripts/backup.sh
OTHER_VARIABLES = {
    "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_EMAIL",
    "GUNICORN_CMD_ARGS",
    "BACKUP_DIR", "BACKUP_RETENTION_DAYS",
}


# Version 0.4.0: Diese Einstellungen stehen in der Verwaltung (E-Mail, Single
# Sign-on, KI). Die Variablen wirken weiter, sollen aber nicht mehr genutzt
# werden: Das README nennt sie in einer eigenen Tabelle, .env.example nicht.
MOVED_TO_APP = {
    "EMAIL_HOST", "EMAIL_PORT", "EMAIL_USE_TLS", "EMAIL_USE_SSL",
    "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL",
    "APP_BASE_URL", "REMINDER_SEND_TIME",
    "OIDC_ENABLED", "OIDC_OP_BASE_URL", "OIDC_RP_CLIENT_ID", "OIDC_RP_CLIENT_SECRET",
    "OIDC_CREATE_USERS", "OIDC_USERNAME_CLAIM", "OIDC_RP_SCOPES", "OIDC_RP_SIGN_ALGO",
    "OIDC_OP_AUTHORIZATION_ENDPOINT", "OIDC_OP_TOKEN_ENDPOINT",
    "OIDC_OP_USER_ENDPOINT", "OIDC_OP_JWKS_ENDPOINT",
    "AI_ENABLED", "AI_PROVIDER", "AI_API_KEY", "AI_MODEL", "AI_BASE_URL",
}


def settings_variables():
    """Namen aller Variablen, die config/settings.py über django-environ liest."""
    source = (BASE_DIR / "config" / "settings.py").read_text(encoding="utf-8")
    return set(re.findall(r'\benv(?:\.[a-z_]+)?\(\s*"([A-Z][A-Z0-9_]*)"', source))


def compose_variables():
    """Namen aller Variablen, die docker-compose.yml aus der .env einsetzt."""
    source = (BASE_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", source))


@skipUnless(README.exists(), "README.md fehlt (im Docker-Image nicht enthalten)")
class ConfigurationDocsTest(SimpleTestCase):
    """
    Version 0.2.0: Der Abschnitt "Configuration" im README nennt jede
    Variable, .env.example jede, die nicht in die Verwaltung gewandert ist
    (MOVED_TO_APP). Schlägt der Test fehl, die neue Variable an beiden
    Stellen eintragen.
    """

    def documented(self, path):
        text = path.read_text(encoding="utf-8")
        return set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\bDEBUG\b", text))

    def test_settings_are_read_from_known_variables(self):
        # Sicherung gegen einen stillen Fehler im Suchmuster
        found = settings_variables()
        for name in ("SECRET_KEY", "DEBUG", "EMAIL_USE_SSL", "OIDC_OP_JWKS_ENDPOINT", "DJANGO_LOG_LEVEL"):
            self.assertIn(name, found)
        self.assertIn("APP_IMAGE_TAG", compose_variables())

    def test_readme_lists_every_variable(self):
        expected = settings_variables() | compose_variables() | OTHER_VARIABLES | SET_BY_COMPOSE
        self.assertEqual(sorted(expected - self.documented(README)), [])

    def test_env_example_lists_every_variable(self):
        expected = (settings_variables() | compose_variables() | OTHER_VARIABLES) - SET_BY_COMPOSE
        documented = self.documented(BASE_DIR / ".env.example")
        self.assertEqual(sorted(expected - MOVED_TO_APP - documented), [])

    def test_env_example_omits_settings_of_the_app(self):
        # Jede dieser Variablen wird noch gelesen, steht aber nicht mehr in der Vorlage
        self.assertEqual(sorted(MOVED_TO_APP - settings_variables()), [])
        self.assertEqual(sorted(MOVED_TO_APP & self.documented(BASE_DIR / ".env.example")), [])


class NoEnvComposeTest(SimpleTestCase):
    """
    Version 0.7.0: Compose-Dateien ohne .env enthalten alle Pflichtwerte
    selbst und lesen nichts aus einer .env.
    """
    REQUIRED = ("SECRET_KEY", "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")

    def check(self, name):
        path = BASE_DIR / name
        if not path.exists():
            self.skipTest(f"{name} fehlt (im Docker-Image nicht enthalten)")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("env_file", text)
        # Kein Ersetzen durch Compose: nur $$ (an den Container weitergereicht)
        self.assertEqual(re.findall(r"(?<!\$)\$\{", text), [])
        for variable in self.REQUIRED:
            self.assertRegex(text, rf"\n  {variable}: ")
        return text

    def test_no_env(self):
        text = self.check("docker-compose.no-env.yml")
        # Die optionalen Variablen der .env stehen auskommentiert darin
        for variable in ("PAPERLESS_URL", "AI_TIMEOUT", "AXES_FAILURE_LIMIT", "DJANGO_LOG_LEVEL", "ADMIN_PASSWORD"):
            self.assertIn(variable, text)

    def test_no_env_minimal(self):
        text = self.check("docker-compose.no-env-minimal.yml")
        self.assertIn("ADMIN_PASSWORD", text)
