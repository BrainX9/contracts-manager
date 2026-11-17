"""
Tests für Version 0.5.0 (die Oberfläche ist standardmäßig deutsch): Versionsanzeige und Hinweis auf neue Versionen
(web.update_check), Zeitplan im Worker.
"""

import io
import json
from unittest.mock import patch
from urllib.error import URLError

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from . import update_check
from .context_processors import version_notice
from .models import SiteSettings


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def github(tag="v0.6.0", **extra):
    return FakeResponse(json.dumps({"tag_name": tag, **extra}).encode())


class VersionTest(TestCase):
    def test_parse(self):
        self.assertEqual(update_check.parse_version("v0.5.0"), (0, 5, 0))
        self.assertEqual(update_check.parse_version("0.10.2"), (0, 10, 2))
        for text in ("0.9.1-beta", "v1.2", "latest", "", None):
            self.assertIsNone(update_check.parse_version(text))

    @override_settings(APP_VERSION="0.5.0")
    def test_comparison_is_numeric(self):
        self.assertTrue(update_check.newer_than_installed("0.10.0"))
        self.assertTrue(update_check.newer_than_installed("0.5.1"))
        self.assertFalse(update_check.newer_than_installed("0.5.0"))
        self.assertFalse(update_check.newer_than_installed("0.4.9"))
        self.assertFalse(update_check.newer_than_installed(""))
        self.assertFalse(update_check.newer_than_installed("nonsense"))


class CheckTest(TestCase):
    def test_stores_the_result(self):
        with patch("urllib.request.urlopen", return_value=github("v0.6.0")) as urlopen:
            self.assertEqual(update_check.check(), "0.6.0")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, update_check.LATEST_URL)
        # Anonym: ein allgemeiner Name, keine Angaben zur Installation
        self.assertEqual(request.get_header("User-agent"), "contracts-manager-update-check")
        site = SiteSettings.load()
        self.assertEqual(site.latest_version, "0.6.0")
        self.assertIsNotNone(site.update_checked_at)

    def test_network_error_keeps_the_last_result(self):
        SiteSettings.load()
        SiteSettings.objects.filter(pk=1).update(latest_version="0.5.5")
        with patch("urllib.request.urlopen", side_effect=URLError("offline")):
            self.assertIsNone(update_check.check())
        self.assertEqual(SiteSettings.load().latest_version, "0.5.5")

    def test_unexpected_answers_are_ignored(self):
        for body in (github("nightly"), FakeResponse(b"not json"), FakeResponse(b"[]")):
            with patch("urllib.request.urlopen", return_value=body):
                self.assertIsNone(update_check.check())
        self.assertEqual(SiteSettings.load().latest_version, "")

    def test_switched_off_makes_no_request(self):
        SiteSettings.load()
        SiteSettings.objects.filter(pk=1).update(update_check=False)
        with patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(update_check.check())
        urlopen.assert_not_called()

    def test_task(self):
        from .tasks import check_for_updates

        with patch("urllib.request.urlopen", return_value=github("v0.7.0")):
            self.assertEqual(check_for_updates(), "0.7.0")
        with patch("urllib.request.urlopen", side_effect=OSError):
            self.assertEqual(check_for_updates(), "")

    @override_settings(APP_VERSION="0.5.0")
    def test_available_update(self):
        self.assertIsNone(update_check.available_update())
        # Die Abfrage legt keinen Datensatz an
        self.assertFalse(SiteSettings.objects.exists())
        SiteSettings.load()
        SiteSettings.objects.filter(pk=1).update(latest_version="0.6.0")
        self.assertEqual(update_check.available_update(), "0.6.0")
        SiteSettings.objects.filter(pk=1).update(update_check=False)
        self.assertIsNone(update_check.available_update())
        SiteSettings.objects.filter(pk=1).update(update_check=True, latest_version="0.5.0")
        self.assertIsNone(update_check.available_update())

    def test_schedule_is_created_once(self):
        from django_q.models import Schedule

        from .apps import UPDATE_SCHEDULE_NAME, ensure_update_schedule

        ensure_update_schedule()
        ensure_update_schedule()
        schedule = Schedule.objects.get(name=UPDATE_SCHEDULE_NAME)
        self.assertEqual((schedule.func, schedule.schedule_type), ("web.tasks.check_for_updates", Schedule.DAILY))


@override_settings(APP_VERSION="0.5.0")
class DisplayTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser("root", "root@example.test", "pw-12345-x")
        cls.user = User.objects.create_user("jo", "jo@example.test", "pw-12345-x")

    def set_latest(self, version):
        SiteSettings.load()
        SiteSettings.objects.filter(pk=1).update(latest_version=version)

    def test_sidebar_shows_version_to_superusers_only(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Contracts Manager 0.5.0")
        self.assertNotContains(page, "app-version-update")
        self.client.force_login(self.user)
        page = self.client.get(reverse("dashboard"))
        self.assertNotContains(page, "Contracts Manager 0.5.0")

    def test_sidebar_shows_the_update_hint(self):
        self.set_latest("0.6.0")
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Update 0.6.0")
        self.assertContains(page, update_check.release_url("0.6.0"))
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("dashboard")), "0.6.0")

    def test_no_hint_when_up_to_date_or_off(self):
        self.set_latest("0.5.0")
        self.client.force_login(self.superuser)
        self.assertNotContains(self.client.get(reverse("dashboard")), "app-version-update")
        self.set_latest("0.6.0")
        SiteSettings.objects.filter(pk=1).update(update_check=False)
        self.assertNotContains(self.client.get(reverse("dashboard")), "app-version-update")

    def test_context_processor_for_anonymous(self):
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        self.assertEqual(version_notice(request), {})


@override_settings(APP_VERSION="0.5.0")
class SystemPageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser("root", "root@example.test", "pw-12345-x")
        cls.url = reverse("manage_system")

    def test_superuser_sees_the_card(self):
        self.client.force_login(self.superuser)
        page = self.client.get(self.url)
        self.assertContains(page, "Installierte Version")
        self.assertContains(page, "Noch nicht geprüft")
        SiteSettings.objects.filter(pk=1).update(latest_version="0.6.0")
        page = self.client.get(self.url)
        self.assertContains(page, "Update verfügbar: 0.6.0")

    def test_administrator_does_not_see_the_card(self):
        from contracts import permissions as perms

        admin = User.objects.create_user("adm", "adm@example.test", "pw-12345-x")
        admin.groups.add(Group.objects.get(name=perms.GROUP_ADMINS))
        self.client.force_login(admin)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Installierte Version")
        # Auch ohne Karte darf er die Prüfung nicht verstellen
        for action in ("update_check_off", "update_check_now", "update_check_on"):
            self.assertEqual(self.client.post(self.url, {"action": action}).status_code, 403)
        self.assertTrue(SiteSettings.load().update_check)

    def test_switch_off_and_on(self):
        self.client.force_login(self.superuser)
        self.client.post(self.url, {"action": "update_check_off"})
        self.assertFalse(SiteSettings.load().update_check)
        self.assertContains(self.client.get(self.url), "Die Update-Prüfung ist aus.")
        self.client.post(self.url, {"action": "update_check_on"})
        self.assertTrue(SiteSettings.load().update_check)

    def test_check_now(self):
        self.client.force_login(self.superuser)
        with patch("urllib.request.urlopen", return_value=github("v0.6.0")):
            response = self.client.post(self.url, {"action": "update_check_now"}, follow=True)
        self.assertEqual(SiteSettings.load().latest_version, "0.6.0")
        self.assertContains(response, "Update verfügbar: 0.6.0")

    def test_check_now_offline_shows_an_error(self):
        self.client.force_login(self.superuser)
        with patch("urllib.request.urlopen", side_effect=URLError("offline")):
            response = self.client.post(self.url, {"action": "update_check_now"}, follow=True)
        self.assertContains(response, "GitHub ist nicht erreichbar")
