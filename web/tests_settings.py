"""
Tests für Version 0.4.0: E-Mail und Single Sign-on in der Verwaltung
(web.site_config), Markdown in Notizen.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from contracts import permissions as perms
from contracts.models import Category, Consumer, Contract, Provider

from . import site_config
from .markdown import render
from .models import SiteSettings

ENV_MAIL = {
    "EMAIL_HOST": "smtp.env.test", "EMAIL_PORT": 587, "EMAIL_HOST_USER": "env@example.test",
    "EMAIL_HOST_PASSWORD": "env-passwort", "EMAIL_USE_TLS": True, "EMAIL_USE_SSL": False,
    "DEFAULT_FROM_EMAIL": "env@example.test", "APP_BASE_URL": "https://env.example.test",
}
ENV_SSO = {
    "OIDC_ENABLED": False, "OIDC_OP_BASE_URL": "https://env.example.test",
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://env.example.test/webman/sso/SSOOauth.cgi",
    "OIDC_OP_TOKEN_ENDPOINT": "https://env.example.test/webman/sso/SSOAccessToken.cgi",
    "OIDC_OP_USER_ENDPOINT": "https://env.example.test/webman/sso/SSOUserInfo.cgi",
    "OIDC_OP_JWKS_ENDPOINT": "https://env.example.test/webman/sso/openid-jwks.json",
    "OIDC_RP_CLIENT_ID": "env-client", "OIDC_RP_CLIENT_SECRET": "env-secret",
    "OIDC_CREATE_USER": False, "OIDC_USERNAME_CLAIM": "username",
    "OIDC_RP_SCOPES": "openid email", "OIDC_RP_SIGN_ALGO": "RS256",
}


def site(**values):
    settings_obj = SiteSettings.load()
    for name, value in values.items():
        setattr(settings_obj, name, value)
    settings_obj.save()
    return settings_obj


class SuperuserMixin:
    def setUp(self):
        mail.outbox.clear()
        self.superuser = User.objects.create_superuser("chef", "chef@example.com", "x")
        self.admin = User.objects.create_user("admin", "admin@example.com")
        self.admin.groups.add(Group.objects.get(name=perms.GROUP_ADMINS))


@override_settings(**ENV_MAIL)
class MailConfigTest(TestCase):
    """Verwaltung vor .env; ein leeres Feld bedeutet "Wert aus der .env"."""

    def test_env_only(self):
        config = site_config.mail()
        self.assertEqual(
            (config.host, config.port, config.security, config.user, config.password, config.sender, config.base_url),
            ("smtp.env.test", 587, "starttls", "env@example.test", "env-passwort", "env@example.test",
             "https://env.example.test"),
        )
        self.assertTrue(config.smtp)

    def test_site_values_take_precedence(self):
        site(email_host="smtp.db.test", email_port=465, email_security="ssl", email_user="db@example.test",
             email_password="db-passwort", app_base_url="https://db.example.test/")
        config = site_config.mail()
        self.assertEqual(
            (config.host, config.port, config.security, config.user, config.password, config.base_url),
            ("smtp.db.test", 465, "ssl", "db@example.test", "db-passwort", "https://db.example.test"),
        )
        # Ohne eigenen Absender die Adresse des SMTP-Benutzers aus der Verwaltung
        self.assertEqual(config.sender, "db@example.test")
        site(email_sender="absender@example.test")
        self.assertEqual(site_config.mail().sender, "absender@example.test")

    def test_partial_override(self):
        site(email_password="db-passwort")
        config = site_config.mail()
        self.assertEqual((config.host, config.user, config.password), ("smtp.env.test", "env@example.test", "db-passwort"))

    @override_settings(EMAIL_HOST="", DEFAULT_FROM_EMAIL="contracts@localhost")
    def test_without_server_console(self):
        from django.core.mail.backends.console import EmailBackend as ConsoleBackend

        from .mail_backend import SiteEmailBackend

        self.assertFalse(site_config.mail().smtp)
        self.assertIsInstance(SiteEmailBackend().backend, ConsoleBackend)

    def test_backend_uses_current_settings(self):
        from .mail_backend import SiteEmailBackend

        site(email_host="smtp.db.test", email_port=465, email_security="ssl", email_password="db-passwort")
        backend = SiteEmailBackend().backend
        self.assertEqual(
            (backend.host, backend.port, backend.username, backend.password, backend.use_ssl, backend.use_tls),
            ("smtp.db.test", 465, "env@example.test", "db-passwort", True, False),
        )

    def test_mail_uses_sender_and_base_url_from_settings(self):
        from contracts.mail import absolute_url, build_mail

        site(email_sender="absender@example.test", app_base_url="https://db.example.test")
        message = build_mail("test", {"user": User(username="x")}, ["an@example.test"])
        self.assertEqual(message.from_email, "absender@example.test")
        self.assertEqual(absolute_url("/contracts/1/"), "https://db.example.test/contracts/1/")

    @override_settings(REMINDER_SEND_TIME="07:00")
    def test_reminder_time(self):
        self.assertEqual(site_config.reminder_time(), "07:00")
        site(reminder_send_time="18:45")
        self.assertEqual(site_config.reminder_time(), "18:45")


@override_settings(**ENV_MAIL, REMINDER_SEND_TIME="07:00")
class ManageEmailSettingsTest(SuperuserMixin, TestCase):
    url = reverse_lazy("manage_email")

    def data(self, **values):
        data = {"email_host": "", "email_port": "", "email_security": "", "email_user": "", "password": "",
                "email_sender": "", "app_base_url": "", "reminder_send_time": "", "action": "save"}
        data.update(values)
        return data

    def test_only_superuser(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.data(email_host="smtp.x.test")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("manage_system")), 'href="/manage/email/"')
        self.client.force_login(self.superuser)
        page = self.client.get(self.url)
        self.assertContains(page, "smtp.env.test:587")
        self.assertNotContains(page, "env-passwort")
        self.assertContains(self.client.get(reverse("manage_system")), 'href="/manage/email/"')

    def test_save_and_password_is_never_shown(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, self.data(
            email_host="smtp.db.test", email_port="465", email_security="ssl", email_user="db@example.test",
            password="db-geheim-1", email_sender="db@example.test", app_base_url="https://db.example.test/",
        ))
        self.assertRedirects(response, self.url)
        settings_obj = SiteSettings.load()
        self.assertEqual(
            (settings_obj.email_host, settings_obj.email_port, settings_obj.email_security,
             settings_obj.email_password, settings_obj.app_base_url),
            ("smtp.db.test", 465, "ssl", "db-geheim-1", "https://db.example.test"),
        )
        page = self.client.get(self.url)
        self.assertNotContains(page, "db-geheim-1")
        self.assertContains(page, "smtp.db.test:465")
        # Leer lassen behält das Passwort, das Kästchen entfernt es
        self.client.post(self.url, self.data(email_host="smtp.db.test"))
        self.assertEqual(SiteSettings.load().email_password, "db-geheim-1")
        self.client.post(self.url, self.data(email_host="smtp.db.test", clear_password="on"))
        self.assertEqual(SiteSettings.load().email_password, "")

    def test_validation(self):
        self.client.force_login(self.superuser)
        for values in (
            {"email_host": "https://smtp.db.test/"}, {"email_port": "70000"}, {"email_sender": "kein-absender"},
            {"app_base_url": "ftp://x.test"}, {"reminder_send_time": "25:00"},
        ):
            with self.subTest(values=values):
                response = self.client.post(self.url, self.data(**values))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
        self.assertEqual(SiteSettings.load().email_host, "")
        # Absender mit Anzeigename ist erlaubt
        self.client.post(self.url, self.data(email_sender="Contracts Manager <vertraege@example.test>"))
        self.assertEqual(SiteSettings.load().email_sender, "Contracts Manager <vertraege@example.test>")

    def test_reminder_time_updates_schedule(self):
        from django_q.models import Schedule

        from contracts.apps import REMINDER_SCHEDULE_NAME

        self.client.force_login(self.superuser)
        self.client.post(self.url, self.data(reminder_send_time="18:45"))
        schedule = Schedule.objects.get(name=REMINDER_SCHEDULE_NAME)
        self.assertEqual(timezone.localtime(schedule.next_run).strftime("%H:%M"), "18:45")
        self.assertContains(self.client.get(reverse("manage_system")), "18:45")
        # Leer: wieder die Uhrzeit aus der .env
        self.client.post(self.url, self.data())
        schedule.refresh_from_db()
        self.assertEqual(timezone.localtime(schedule.next_run).strftime("%H:%M"), "07:00")

    def test_test_sends_with_entered_values_without_saving(self):
        self.client.force_login(self.superuser)
        connection = MagicMock()
        connection.send_messages.return_value = 1
        with patch("web.manage_views.smtp_backend", return_value=connection) as smtp_backend:
            response = self.client.post(self.url, self.data(
                email_host="smtp.test.test", email_port="2525", email_security="none", password="test-geheim",
                email_sender="test@example.test", action="test",
            ))
        self.assertEqual(response.status_code, 200)
        candidate = smtp_backend.call_args.args[0]
        self.assertEqual(
            (candidate.host, candidate.port, candidate.security, candidate.user, candidate.password, candidate.sender),
            ("smtp.test.test", 2525, "none", "env@example.test", "test-geheim", "test@example.test"),
        )
        message = connection.send_messages.call_args.args[0][0]
        self.assertEqual((message.to, message.from_email), (["chef@example.com"], "test@example.test"))
        self.assertTrue(response.context["test_result"][0])
        self.assertNotContains(response, "test-geheim")
        # Nichts gespeichert
        settings_obj = SiteSettings.load()
        self.assertEqual((settings_obj.email_host, settings_obj.email_password), ("", ""))

    def test_test_reports_error(self):
        self.client.force_login(self.superuser)
        connection = MagicMock()
        connection.send_messages.side_effect = OSError("Connection refused")
        with patch("web.manage_views.smtp_backend", return_value=connection):
            response = self.client.post(self.url, self.data(email_host="smtp.test.test", action="test"))
        self.assertFalse(response.context["test_result"][0])
        self.assertContains(response, "Connection refused")

    @override_settings(EMAIL_HOST="")
    def test_test_without_server(self):
        self.client.force_login(self.superuser)
        with patch("web.manage_views.smtp_backend") as smtp_backend:
            response = self.client.post(self.url, self.data(action="test"))
        smtp_backend.assert_not_called()
        self.assertFalse(response.context["test_result"][0])


@override_settings(**ENV_SSO)
class SSOConfigTest(TestCase):
    def test_env_only(self):
        config = site_config.sso()
        self.assertFalse(config.enabled)
        self.assertEqual((config.client_id, config.client_secret), ("env-client", "env-secret"))
        self.assertEqual(config.token_endpoint, "https://env.example.test/webman/sso/SSOAccessToken.cgi")

    def test_site_values_and_endpoints(self):
        site(sso_enabled="on", sso_base_url="https://nas.db.test/", sso_client_id="db-client",
             sso_client_secret="db-secret", sso_create_users="on", sso_username_claim="preferred_username",
             sso_scopes="openid  profile email", sso_sign_algo="ES256")
        config = site_config.sso()
        self.assertTrue(config.enabled and config.create_users)
        self.assertEqual(
            (config.base_url, config.client_id, config.client_secret, config.username_claim, config.scopes,
             config.sign_algo),
            ("https://nas.db.test", "db-client", "db-secret", "preferred_username", "openid profile email", "ES256"),
        )
        # Endpunkte aus der Adresse der Verwaltung (Synology), einzelne eigene haben Vorrang
        self.assertEqual(config.jwks_endpoint, "https://nas.db.test/webman/sso/openid-jwks.json")
        site(sso_token_endpoint="https://idp.db.test/token")
        config = site_config.sso()
        self.assertEqual(config.token_endpoint, "https://idp.db.test/token")
        self.assertEqual(config.user_endpoint, "https://nas.db.test/webman/sso/SSOUserInfo.cgi")
        self.assertEqual(config.oidc_settings()["OIDC_OP_TOKEN_ENDPOINT"], "https://idp.db.test/token")

    def test_switch_off_overrides_env(self):
        with self.settings(OIDC_ENABLED=True):
            self.assertTrue(site_config.sso().enabled)
            site(sso_enabled="off")
            self.assertFalse(site_config.sso().enabled)


@override_settings(**ENV_SSO)
class SSOFromSettingsTest(TestCase):
    """Anmeldung mit Werten aus der Verwaltung, ohne Neustart"""

    def setUp(self):
        # Seit 0.8.0 leitet die Anmeldeseite ohne einen einzigen Benutzer zur Erstanlage
        User.objects.get_or_create(username="vorhanden")

    def enable(self, **values):
        site(sso_enabled="on", sso_base_url="https://nas.db.test", sso_client_id="db-client",
             sso_client_secret="db-secret", **values)

    def test_switch_on_in_settings(self):
        self.assertEqual(self.client.get("/oidc/authenticate/").status_code, 404)
        self.assertNotContains(self.client.get(reverse("login")), "/oidc/authenticate/")
        self.enable()
        self.assertContains(self.client.get(reverse("login")), "/oidc/authenticate/")
        response = self.client.get("/oidc/authenticate/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://nas.db.test/webman/sso/SSOOauth.cgi"))
        self.assertIn("client_id=db-client", response["Location"])

    def test_backend_reads_settings(self):
        from .oidc import SSOBackend

        self.enable(sso_username_claim="preferred_username", sso_create_users="on")
        backend = SSOBackend()
        self.assertEqual(
            (backend.OIDC_RP_CLIENT_ID, backend.OIDC_RP_CLIENT_SECRET, backend.OIDC_OP_TOKEN_ENDPOINT,
             backend.OIDC_OP_JWKS_ENDPOINT, backend.OIDC_RP_SIGN_ALGO),
            ("db-client", "db-secret", "https://nas.db.test/webman/sso/SSOAccessToken.cgi",
             "https://nas.db.test/webman/sso/openid-jwks.json", "RS256"),
        )
        self.assertEqual(backend.get_username({"preferred_username": " Anna "}), "Anna")
        self.assertTrue(backend.get_settings("OIDC_CREATE_USER", True))
        from mozilla_django_oidc.auth import OIDCAuthenticationBackend

        with patch.object(OIDCAuthenticationBackend, "get_userinfo", return_value={}):
            user = backend.get_or_create_user("token", "id-token", {"preferred_username": "anna"})
        self.assertEqual(user.username, "anna")
        self.assertFalse(user.has_usable_password())

    def test_backend_creation_needs_no_query(self):
        # Django erzeugt die Backends bei jeder Rechteprüfung
        from .oidc import SSOBackend

        user = User.objects.create_user("anna")
        with self.assertNumQueries(0):
            SSOBackend()
        request = RequestFactory().get("/")
        with self.assertNumQueries(0):
            self.assertIsNone(SSOBackend().authenticate(request, username="anna", password="x"))
        self.assertFalse(user.has_perm("contracts.change_contract"))


@override_settings(**ENV_SSO)
class ManageSSOSettingsTest(SuperuserMixin, TestCase):
    url = reverse_lazy("manage_sso")

    def data(self, **values):
        data = {name: "" for name in (
            "sso_enabled", "sso_base_url", "sso_client_id", "client_secret", "sso_create_users", "sso_login_label",
            "sso_username_claim", "sso_scopes", "sso_sign_algo", "sso_authorization_endpoint",
            "sso_token_endpoint", "sso_user_endpoint", "sso_jwks_endpoint",
        )}
        data["action"] = "save"
        data.update(values)
        return data

    def test_only_superuser(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.data(sso_enabled="on")).status_code, 403)
        self.client.force_login(self.superuser)
        page = self.client.get(self.url)
        self.assertContains(page, "http://testserver/oidc/callback/")
        self.assertNotContains(page, "env-secret")

    def test_save_and_secret_is_never_shown(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, self.data(
            sso_enabled="on", sso_base_url="https://nas.db.test/", sso_client_id="db-client",
            client_secret="db-geheim-2", sso_login_label="Mit Synology anmelden",
        ))
        self.assertRedirects(response, self.url)
        settings_obj = SiteSettings.load()
        self.assertEqual(
            (settings_obj.sso_enabled, settings_obj.sso_base_url, settings_obj.sso_client_secret),
            ("on", "https://nas.db.test", "db-geheim-2"),
        )
        self.assertNotContains(self.client.get(self.url), "db-geheim-2")
        self.client.logout()
        self.assertContains(self.client.get(reverse("login")), "Mit Synology anmelden")

    def test_cannot_switch_on_incomplete(self):
        self.client.force_login(self.superuser)
        with self.settings(OIDC_RP_CLIENT_SECRET="", OIDC_RP_CLIENT_ID=""):
            response = self.client.post(self.url, self.data(sso_enabled="on", sso_base_url="https://nas.db.test"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(SiteSettings.load().sso_enabled, "")

    def test_validation(self):
        self.client.force_login(self.superuser)
        for values in ({"sso_base_url": "nas.db.test"}, {"sso_sign_algo": "none"},
                       {"sso_token_endpoint": "javascript:alert(1)"}):
            with self.subTest(values=values):
                response = self.client.post(self.url, self.data(**values))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)

    def test_test_checks_entered_values_without_saving(self):
        self.client.force_login(self.superuser)
        jwks = MagicMock(status_code=200)
        jwks.json.return_value = {"keys": [{"kid": "1"}]}
        with patch("web.sso_check.requests.get", return_value=jwks) as get, \
                patch("web.sso_check.requests.request", return_value=MagicMock(status_code=400)) as request:
            response = self.client.post(self.url, self.data(
                sso_base_url="https://nas.test.test", sso_client_id="c", client_secret="test-geheim", action="test",
            ))
        self.assertEqual(get.call_args.args[0], "https://nas.test.test/webman/sso/openid-jwks.json")
        self.assertEqual(
            [call.args[:2] for call in request.call_args_list],
            [("POST", "https://nas.test.test/webman/sso/SSOAccessToken.cgi"),
             ("GET", "https://nas.test.test/webman/sso/SSOUserInfo.cgi")],
        )
        self.assertEqual([result.status for result in response.context["results"]], ["ok"] * 4)
        self.assertNotContains(response, "test-geheim")
        self.assertEqual(SiteSettings.load().sso_base_url, "")

    def test_test_reports_unreachable_server(self):
        import requests

        self.client.force_login(self.superuser)
        with patch("web.sso_check.requests.get", side_effect=requests.ConnectionError("no route")), \
                patch("web.sso_check.requests.request", return_value=MagicMock(status_code=404)):
            response = self.client.post(self.url, self.data(sso_base_url="https://nas.test.test", action="test"))
        self.assertEqual([result.status for result in response.context["results"]], ["ok", "failed", "failed", "failed"])
        self.assertContains(response, "no route")


class MarkdownTest(TestCase):
    """Notizen mit Markdown: Formatierung ja, HTML und Bilder nein"""

    def test_formatting(self):
        html = render("# Titel\n\n**fett** und *kursiv*\n\n- eins\n- zwei\n\n| A | B |\n|---|---|\n| 1 | 2 |")
        for part in ("<h1>Titel</h1>", "<strong>fett</strong>", "<em>kursiv</em>", "<li>eins</li>", "<table>", "<td>1</td>"):
            self.assertIn(part, html)

    def test_plain_text_keeps_line_breaks(self):
        self.assertEqual(render("Zeile eins\nZeile zwei").strip(), "<p>Zeile eins<br>\nZeile zwei</p>")
        self.assertEqual(render(""), "")

    def test_html_is_not_executed(self):
        html = render('<script>alert(1)</script> <img src=x onerror="alert(2)"> <b>x</b>')
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_links_and_images(self):
        html = render("[Seite](https://example.com/a) [böse](javascript:alert(1)) ![Bild](https://example.com/x.png)")
        self.assertIn('<a href="https://example.com/a" target="_blank" rel="noopener noreferrer nofollow">Seite</a>', html)
        self.assertNotIn("javascript:", html.replace("[böse](javascript:alert(1))", ""))
        self.assertNotIn('href="javascript', html)
        self.assertNotIn("<img", html)

    def test_detail_pages_render_notes(self):
        user = User.objects.create_user("anna")
        user.groups.add(Group.objects.get(name=perms.GROUP_USERS))
        notes = "**wichtig** <script>alert(1)</script>"
        provider = Provider.objects.create(name="Stadtwerke", owner=user, notes=notes)
        consumer = Consumer.objects.create(name="Haushalt", owner=user, notes=notes)
        category = Category.objects.create(name="Energie", owner=user)
        contract = Contract.objects.create(
            name="Strom", provider=provider, consumer=consumer, category=category, owner=user, notes=notes,
        )
        self.client.force_login(user)
        for url in (reverse("contract_detail", args=[contract.pk]), reverse("provider_detail", args=[provider.pk]),
                    reverse("consumer_detail", args=[consumer.pk])):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertContains(page, "<strong>wichtig</strong>")
                self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertContains(self.client.get(reverse("contract_edit", args=[contract.pk])), "Markdown")


class ManageDocumentAITest(SuperuserMixin, TestCase):
    """Dokument-KI unter Verwaltung › KI-Einstellungen (Version 0.6.0)"""

    url = reverse_lazy("manage_ai")

    def data(self, **values):
        from assistant.agent import DEFAULT_PROMPT

        data = {"ai_enabled": "", "ai_provider": "", "ai_model": "", "ai_base_url": "", "api_key": "",
                "ai_system_prompt": DEFAULT_PROMPT, "reader_provider": "", "reader_model": "",
                "reader_base_url": "", "reader_kind": "", "reader_key": "", "action": "save"}
        data.update(values)
        return data

    def test_save_and_key_is_never_shown(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, self.data(
            reader_model="lightonai/LightOnOCR-2-1B", reader_kind="ocr", reader_key="sk-reader-geheim",
        ))
        self.assertRedirects(response, self.url)
        stored = SiteSettings.load()
        self.assertEqual((stored.reader_model, stored.reader_kind, stored.reader_api_key),
                         ("lightonai/LightOnOCR-2-1B", "ocr", "sk-reader-geheim"))
        page = self.client.get(self.url)
        self.assertNotContains(page, "sk-reader-geheim")
        self.assertContains(page, "lightonai/LightOnOCR-2-1B")
        # Leer lassen behält den Schlüssel, das Kästchen entfernt ihn
        self.client.post(self.url, self.data(reader_model="anderes-modell"))
        self.assertEqual(SiteSettings.load().reader_api_key, "sk-reader-geheim")
        self.client.post(self.url, self.data(reader_model="anderes-modell", clear_reader_key="on"))
        self.assertEqual(SiteSettings.load().reader_api_key, "")
        # Ohne Angabe gilt das Vision-Modell
        self.assertEqual(SiteSettings.load().reader_kind, "vision")

    def test_invalid_address(self):
        self.client.force_login(self.superuser)
        response = self.client.post(self.url, self.data(reader_model="m", reader_base_url="ftp://example.test"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SiteSettings.load().reader_model, "")

    def test_key_is_a_secret(self):
        self.assertIn("reader_api_key", SiteSettings.SECRET_FIELDS)

    def test_test_uses_the_entered_document_ai(self):
        from assistant import selftest

        self.client.force_login(self.superuser)
        seen = []

        def fake_run_step(step, config, timeout=None):
            seen.append((step, config.reader and (config.reader.model, config.reader.api_key)))
            return selftest.StepResult(step, selftest.OK, "")

        with patch("assistant.selftest.run_step", side_effect=fake_run_step):
            self.client.post(self.url, self.data(
                action="test", step="reader", ai_provider="openai", ai_model="m", api_key="sk-main",
                reader_model="vision-model",
            ), HTTP_HX_REQUEST="true")
        self.assertEqual(seen, [("reader", ("vision-model", "sk-main"))])
        self.assertEqual(SiteSettings.load().reader_model, "")
