"""
Tests für Version 0.9.0, Teil 2: Einstellungen aus der .env einmalig in die
Verwaltung übernehmen, danach nur noch die Verwaltung; manage.py wechselt im
Container zum App-Benutzer. Die Oberfläche ist standardmäßig deutsch.
"""

import os
import sys
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import env_import, site_config
from .models import SiteSettings

ENVIRONMENT = {
    "AI_ENABLED": "True", "AI_PROVIDER": "anthropic", "AI_MODEL": "claude-sonnet-5-5", "AI_API_KEY": "sk-env",
    "EMAIL_HOST": "smtp.env.test", "EMAIL_PORT": "465", "EMAIL_USE_SSL": "True",
    "EMAIL_HOST_USER": "env@example.test", "EMAIL_HOST_PASSWORD": "env-passwort",
    "APP_BASE_URL": "https://env.example.test/", "REMINDER_SEND_TIME": "06:30",
    "OIDC_ENABLED": "True", "OIDC_OP_BASE_URL": "https://nas.env.test", "OIDC_RP_CLIENT_ID": "env-client",
    "OIDC_RP_CLIENT_SECRET": "env-secret", "OIDC_CREATE_USERS": "False",
}
# Dieselben Werte, wie config/settings.py sie aus der Umgebung liest
SETTINGS = {
    "AI_ENABLED": True, "AI_PROVIDER": "anthropic", "AI_MODEL": "claude-sonnet-5-5", "AI_API_KEY": "sk-env",
    "EMAIL_HOST": "smtp.env.test", "EMAIL_PORT": 465, "EMAIL_USE_SSL": True, "EMAIL_USE_TLS": True,
    "EMAIL_HOST_USER": "env@example.test", "EMAIL_HOST_PASSWORD": "env-passwort",
    "APP_BASE_URL": "https://env.example.test", "REMINDER_SEND_TIME": "06:30",
    "OIDC_ENABLED": True, "OIDC_OP_BASE_URL": "https://nas.env.test", "OIDC_RP_CLIENT_ID": "env-client",
    "OIDC_RP_CLIENT_SECRET": "env-secret", "OIDC_CREATE_USER": False,
}


@override_settings(**SETTINGS)
@patch.dict(os.environ, ENVIRONMENT)
class EnvImportTest(TestCase):
    def test_imported_once_into_empty_fields(self):
        site = SiteSettings.load()
        site.email_sender = "eigener@example.test"
        site.save()
        with self.assertLogs("web.env_import", "WARNING") as logs:
            env_import.run()
        site.refresh_from_db()
        self.assertEqual(
            (site.ai_enabled, site.ai_provider, site.ai_model, site.ai_api_key),
            ("on", "anthropic", "claude-sonnet-5-5", "sk-env"),
        )
        self.assertEqual(
            (site.email_host, site.email_port, site.email_security, site.email_user, site.email_password,
             site.app_base_url, site.reminder_send_time),
            ("smtp.env.test", 465, "ssl", "env@example.test", "env-passwort", "https://env.example.test", "06:30"),
        )
        # Vorhandene Werte der Verwaltung bleiben
        self.assertEqual(site.email_sender, "eigener@example.test")
        self.assertEqual(
            (site.sso_enabled, site.sso_base_url, site.sso_client_id, site.sso_client_secret, site.sso_create_users),
            ("on", "https://nas.env.test", "env-client", "env-secret", "off"),
        )
        # Nicht gesetzte Variablen werden nicht übernommen (Standardwerte bleiben leer)
        self.assertEqual((site.ai_base_url, site.sso_scopes, site.sso_token_endpoint), ("", "", ""))
        self.assertEqual(set(site.env_imported.split(",")), {"ai", "email", "paperless", "sso"})
        self.assertIn("ai_api_key", logs.output[0])
        self.assertNotIn("sk-env", " ".join(logs.output))

    def test_reminder_schedule_follows_imported_time(self):
        from django.utils import timezone
        from django_q.models import Schedule

        from contracts.apps import REMINDER_SCHEDULE_NAME

        with self.assertLogs("web.env_import", "WARNING"):
            env_import.run()
        schedule = Schedule.objects.get(name=REMINDER_SCHEDULE_NAME)
        self.assertEqual(timezone.localtime(schedule.next_run).strftime("%H:%M"), "06:30")

    def test_later_changes_in_env_are_ignored(self):
        with self.assertLogs("web.env_import", "WARNING"):
            env_import.run()
        site = SiteSettings.load()
        site.ai_enabled, site.email_host = "off", ""
        site.save()
        with self.assertLogs("web.env_import", "WARNING") as logs:
            env_import.run()
        site.refresh_from_db()
        self.assertEqual((site.ai_enabled, site.email_host), ("off", ""))
        self.assertIn("nicht mehr ausgewertet", logs.output[0])
        self.assertIn("EMAIL_HOST", logs.output[0])


class NoEnvFallbackTest(TestCase):
    """Seit 0.9.0 gelten nur noch die Werte der Verwaltung, leere Felder = Standard."""

    @override_settings(**SETTINGS)
    def test_variables_have_no_effect(self):
        from assistant import config as ai_config

        mail = site_config.mail()
        self.assertEqual((mail.host, mail.port, mail.security, mail.password, mail.base_url),
                         ("", 587, "starttls", "", ""))
        self.assertFalse(mail.smtp)
        sso = site_config.sso()
        self.assertEqual((sso.enabled, sso.client_id, sso.username_claim, sso.scopes, sso.sign_algo),
                         (False, "", "username", "openid email", "RS256"))
        self.assertEqual(site_config.reminder_time(), "07:00")
        ai = ai_config.current()
        self.assertEqual((ai.enabled, ai.provider, ai.api_key), (False, "openai", ""))

    def test_settings_pages_offer_no_env_choice(self):
        admin = User.objects.create_superuser("chef", "chef@example.com", "x")
        self.client.force_login(admin)
        for name in ("manage_ai", "manage_email", "manage_sso"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name))
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, ".env")
        # Ohne Wert vorausgewählt: aus bzw. STARTTLS
        ai = self.client.get(reverse("manage_ai"))
        self.assertContains(ai, '<option value="off" selected>')
        email = self.client.get(reverse("manage_email"))
        self.assertContains(email, '<option value="starttls" selected>')


class DropRootTest(SimpleTestCase):
    """manage.py wechselt im Container als root zum App-Benutzer"""

    def drop_root(self):
        import importlib.util

        from django.conf import settings

        spec = importlib.util.spec_from_file_location("manage_module", settings.BASE_DIR / "manage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.drop_root

    def test_switches_user_as_root_in_the_image(self):
        drop_root = self.drop_root()
        environment = {"CONTRACTS_MANAGER_IMAGE": "1", "APP_UID": "1026", "APP_GID": "100"}
        with patch.dict(os.environ, environment), patch("os.getuid", return_value=0), \
                patch("os.execv") as execv, patch.object(sys, "argv", ["manage.py", "import_backup", "x.zip"]):
            drop_root()
        program, arguments = execv.call_args.args
        self.assertEqual(program, "/usr/bin/setpriv")
        self.assertEqual(arguments[1:5], ["--reuid=1026", "--regid=100", "--clear-groups", "--inh-caps=-all"])
        self.assertEqual(arguments[-3:], ["manage.py", "import_backup", "x.zip"])

    def test_nothing_happens_outside_the_image_or_as_user(self):
        drop_root = self.drop_root()
        with patch.dict(os.environ, {"CONTRACTS_MANAGER_IMAGE": ""}), patch("os.getuid", return_value=0), \
                patch("os.execv") as execv:
            drop_root()
        execv.assert_not_called()
        with patch.dict(os.environ, {"CONTRACTS_MANAGER_IMAGE": "1"}), patch("os.getuid", return_value=1000), \
                patch("os.execv") as execv:
            drop_root()
        execv.assert_not_called()

    def test_refuses_root_as_app_user(self):
        drop_root = self.drop_root()
        with patch.dict(os.environ, {"CONTRACTS_MANAGER_IMAGE": "1", "APP_UID": "0"}), \
                patch("os.getuid", return_value=0), patch("os.execv") as execv:
            with self.assertRaises(SystemExit):
                drop_root()
        execv.assert_not_called()
