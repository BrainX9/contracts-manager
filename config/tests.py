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
    Version 0.2.0: Die Tabelle "Configuration" im README und .env.example
    nennen jede Variable. Schlägt der Test fehl, die neue Variable an beiden
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
        self.assertEqual(sorted(expected - self.documented(BASE_DIR / ".env.example")), [])
