import os
import re
import stat
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from config import secret_key

BASE_DIR = Path(settings.BASE_DIR)
README = BASE_DIR / "README.md"
VARIABLES_DOC = BASE_DIR / "docs" / "variables.md"

# Version 0.9.0: Jede Variable gehört zu genau einer von vier Gruppen.
# Neue Variablen hier einordnen und dokumentieren (docs/variables.md; README
# nur Pflicht und optional; .env.example alle außer den archivierten;
# docker-compose.yml Pflicht und optional).
REQUIRED = {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"}
OPTIONAL = {"ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "USE_HTTPS", "APP_UID", "APP_GID", "SECRET_KEY"}
ADVANCED = {
    "DEBUG", "APP_PORT", "APP_IMAGE_TAG", "DATA_PATH",
    "AI_EFFORT", "AI_MAX_TOKENS", "AI_MAX_TOOL_ROUNDS", "AI_TIMEOUT",
    "AXES_FAILURE_LIMIT", "AXES_COOLOFF_MINUTES", "SECURE_HSTS_SECONDS", "DJANGO_LOG_LEVEL",
    "GUNICORN_CMD_ARGS", "BACKUP_DIR", "BACKUP_RETENTION_DAYS",
}
# Seit 0.9.0 nicht mehr ausgewertet: einmalig in die Verwaltung übernommen
# (web.env_import); ADMIN_* seit 0.8.0 ohne Wirkung
ARCHIVED = {
    "EMAIL_HOST", "EMAIL_PORT", "EMAIL_USE_TLS", "EMAIL_USE_SSL",
    "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL",
    "APP_BASE_URL", "REMINDER_SEND_TIME",
    "OIDC_ENABLED", "OIDC_OP_BASE_URL", "OIDC_RP_CLIENT_ID", "OIDC_RP_CLIENT_SECRET",
    "OIDC_CREATE_USERS", "OIDC_USERNAME_CLAIM", "OIDC_RP_SCOPES", "OIDC_RP_SIGN_ALGO",
    "OIDC_OP_AUTHORIZATION_ENDPOINT", "OIDC_OP_TOKEN_ENDPOINT",
    "OIDC_OP_USER_ENDPOINT", "OIDC_OP_JWKS_ENDPOINT",
    "AI_ENABLED", "AI_PROVIDER", "AI_API_KEY", "AI_MODEL", "AI_BASE_URL",
    "PAPERLESS_URL",
    "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_EMAIL",
}
# Setzen Compose-Datei bzw. Image selbst
INTERNAL = {"DATABASE_URL", "MEDIA_ROOT", "CONFIG_DIR"}
# Nicht in config/settings.py gelesen: Dockerfile (gunicorn), scripts/backup.sh,
# Startskript (APP_UID/APP_GID)
OTHER_VARIABLES = {"GUNICORN_CMD_ARGS", "BACKUP_DIR", "BACKUP_RETENTION_DAYS", "APP_UID", "APP_GID"}


def settings_variables():
    """Namen aller Variablen, die config/settings.py über django-environ liest."""
    source = (BASE_DIR / "config" / "settings.py").read_text(encoding="utf-8")
    return set(re.findall(r'\benv(?:\.[a-z_]+)?\(\s*"([A-Z][A-Z0-9_]*)"', source))


def compose_variables():
    """Namen aller Variablen, die docker-compose.env.yml aus der .env einsetzt."""
    source = (BASE_DIR / "docker-compose.env.yml").read_text(encoding="utf-8")
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", source))


def documented(path):
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\bDEBUG\b", text))


@skipUnless(README.exists(), "README.md fehlt (im Docker-Image nicht enthalten)")
class ConfigurationDocsTest(SimpleTestCase):
    """
    Version 0.9.0: Variablen in vier Gruppen. Schlägt ein Test fehl, die
    Variable oben einer Gruppe zuordnen und an den genannten Stellen
    dokumentieren.
    """

    def test_settings_are_read_from_known_variables(self):
        # Sicherung gegen einen stillen Fehler im Suchmuster
        found = settings_variables()
        for name in ("SECRET_KEY", "DEBUG", "EMAIL_USE_SSL", "OIDC_OP_JWKS_ENDPOINT", "DJANGO_LOG_LEVEL"):
            self.assertIn(name, found)
        self.assertIn("APP_IMAGE_TAG", compose_variables())

    def test_every_variable_has_a_group(self):
        groups = [REQUIRED, OPTIONAL, ADVANCED, ARCHIVED, INTERNAL]
        used = settings_variables() | compose_variables() | OTHER_VARIABLES
        self.assertEqual(sorted(used - set().union(*groups)), [])
        # Keine Variable in zwei Gruppen
        for index, group in enumerate(groups):
            for other in groups[index + 1:]:
                self.assertEqual(group & other, set())

    def test_variables_page_lists_every_variable(self):
        expected = REQUIRED | OPTIONAL | ADVANCED | ARCHIVED | INTERNAL
        self.assertEqual(sorted(expected - documented(VARIABLES_DOC)), [])

    def test_readme_lists_required_and_optional(self):
        self.assertEqual(sorted((REQUIRED | OPTIONAL) - documented(README)), [])
        # Erweiterte und archivierte stehen auf der eigenen Seite
        self.assertEqual(sorted(ARCHIVED & documented(README)), [])

    def test_env_example(self):
        text = documented(BASE_DIR / ".env.example")
        self.assertEqual(sorted((REQUIRED | OPTIONAL | ADVANCED) - text), [])
        self.assertEqual(sorted(ARCHIVED & text), [])

    def test_archived_variables_match_the_import(self):
        from web import env_import

        self.assertEqual(sorted(ARCHIVED - {"ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_EMAIL"}),
                         env_import.VARIABLES)


class DefaultComposeTest(SimpleTestCase):
    """
    Version 0.9.0: docker-compose.yml braucht keine .env, enthält Pflicht- und
    optionale Werte (optionale auskommentiert) und sonst keine Variablen.
    """

    def setUp(self):
        path = BASE_DIR / "docker-compose.yml"
        if not path.exists():
            self.skipTest("docker-compose.yml fehlt (im Docker-Image nicht enthalten)")
        self.text = path.read_text(encoding="utf-8")

    def test_no_env_file(self):
        self.assertNotIn("env_file", self.text)
        # Kein Ersetzen durch Compose: nur $$ (an den Container weitergereicht)
        self.assertEqual(re.findall(r"(?<!\$)\$\{", self.text), [])
        self.assertNotIn("user:", self.text)

    def test_required_and_optional_only(self):
        for variable in REQUIRED:
            self.assertRegex(self.text, rf"\n  {variable}: ")
        for variable in OPTIONAL:
            self.assertRegex(self.text, rf"\n  # {variable}: ")
        self.assertEqual(sorted((ADVANCED | ARCHIVED) & documented(BASE_DIR / "docker-compose.yml")), [])

    def test_port_and_folders(self):
        self.assertIn('"8010:8000"', self.text)
        self.assertIn("./config:/config", self.text)
        self.assertIn("source: ./media", self.text)


class SecretKeyTest(SimpleTestCase):
    """Version 0.9.0: SECRET_KEY ohne .env im Ordner config"""

    def test_created_once_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            first = secret_key.load_or_create(directory)
            path = Path(directory) / secret_key.FILE_NAME
            self.assertGreaterEqual(len(first), 50)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(secret_key.load_or_create(directory), first)
            self.assertEqual(os.listdir(directory), [secret_key.FILE_NAME])

    def test_missing_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(secret_key.SecretKeyError) as error:
                secret_key.load_or_create(Path(directory) / "fehlt")
        self.assertIn("SECRET_KEY", str(error.exception))

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / secret_key.FILE_NAME).write_text("")
            with self.assertRaises(secret_key.SecretKeyError):
                secret_key.load_or_create(directory)
