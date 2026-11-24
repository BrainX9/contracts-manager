"""
Tests für Version 0.11.0: System-E-Mails (Gruppe "Systemmail", neue Version
verfügbar, Neuigkeiten nach einem Update), Backup-Schritt von 0.10.1.
"""

import importlib
from pathlib import Path
from unittest.mock import patch

from django.apps import apps as global_apps
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django_q.models import Schedule

from contracts import backup_upgrades
from contracts import permissions as perms
from contracts.models import Provider
from contracts.testing import site_settings

from . import system_mail, tasks, whats_new
from .apps import ANNOUNCE_SCHEDULE_NAME, ensure_announce_schedule
from .models import SiteSettings, UserPreferences

ENTRIES = (
    whats_new.Entry("0.10.0", "Zehn", (whats_new.Item("zehn"),)),
    whats_new.Entry("0.11.0", "Elf", (whats_new.Item("für alle"), whats_new.Item("nur Chef", superuser_only=True))),
    whats_new.Entry("0.12.0", "Zukunft", (whats_new.Item("später"),)),
)

migration = importlib.import_module("web.migrations.0011_system_mail")


def member(name, superuser=False, email=True, **kwargs):
    if superuser:
        user = User.objects.create_superuser(name, f"{name}@example.com" if email else "", "x", **kwargs)
    else:
        user = User.objects.create_user(name, f"{name}@example.com" if email else "", "x", **kwargs)
    user.groups.add(Group.objects.get(name=perms.GROUP_SYSTEM_MAIL))
    return user


def set_site(**fields):
    SiteSettings.load()
    SiteSettings.objects.filter(pk=1).update(**fields)


class GroupTest(TestCase):
    def test_protected_described_and_not_shareable(self):
        self.assertTrue(Group.objects.filter(name=perms.GROUP_SYSTEM_MAIL).exists())
        self.assertIn(perms.GROUP_SYSTEM_MAIL, perms.PROTECTED_GROUPS)
        self.assertIn("System-E-Mails", str(perms.group_description(perms.GROUP_SYSTEM_MAIL)))
        self.assertNotIn(perms.GROUP_SYSTEM_MAIL, [group.name for group in perms.shareable_groups()])
        anna = User.objects.create_user("anna")
        self.assertIn(perms.GROUP_SYSTEM_MAIL, [group.name for group in perms.assignable_groups(anna)])
        # Eine ältere Freigabe bleibt sichtbar, damit man sie entfernen kann
        provider = Provider.objects.create(owner=anna, name="Telekom")
        group = Group.objects.get(name=perms.GROUP_SYSTEM_MAIL)
        perms.grant_role(provider, group, perms.READER)
        self.assertIn(group, perms.shareable_groups(provider))

    def test_first_admin_joins_later_users_do_not(self):
        chef = User.objects.create_superuser("chef", "chef@example.com", "x")
        second = User.objects.create_superuser("zweiter", "zweiter@example.com", "x")
        anna = User.objects.create_user("anna")
        members = set(Group.objects.get(name=perms.GROUP_SYSTEM_MAIL).user_set.values_list("username", flat=True))
        self.assertEqual(members, {chef.username})
        self.assertNotIn(second.username, members)
        self.assertNotIn(anna.username, members)

    def test_first_admin_in_browser(self):
        response = self.client.post(reverse("first_admin"), {
            "username": "chef", "email": "chef@example.com",
            "password1": "Sehr-geheim-2026", "password2": "Sehr-geheim-2026",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(username="chef").groups.filter(name=perms.GROUP_SYSTEM_MAIL).exists())

    def test_first_ordinary_user_does_not_join(self):
        anna = User.objects.create_user("anna")
        self.assertFalse(anna.groups.filter(name=perms.GROUP_SYSTEM_MAIL).exists())


class MigrationTest(TestCase):
    def run_migration(self):
        migration.existing_installation(global_apps, None)

    def test_existing_installation(self):
        chef = User.objects.create_superuser("chef", "chef@example.com", "x")
        chef.groups.clear()
        admin = User.objects.create_user("admin2")
        admin.groups.add(Group.objects.get(name=perms.GROUP_ADMINS))
        User.objects.create_user("anna")
        set_site(announced_version="")
        self.run_migration()
        members = set(Group.objects.get(name=perms.GROUP_SYSTEM_MAIL).user_set.values_list("username", flat=True))
        self.assertEqual(members, {"chef", "admin2"})
        self.assertEqual(SiteSettings.load().announced_version, "0.10.1")

    def test_new_installation(self):
        set_site(announced_version="")
        self.run_migration()
        self.assertEqual(SiteSettings.load().announced_version, "")


@override_settings(APP_VERSION="0.11.0")
class UpdateMailTest(TestCase):
    def setUp(self):
        self.chef = member("chef", superuser=True)
        self.anna = member("anna")
        member("ohne", superuser=True, email=False)
        member("weg", superuser=True, is_active=False)
        User.objects.create_superuser("fremd", "fremd@example.com", "x").groups.clear()

    def test_once_per_version_superusers_of_the_group_only(self):
        self.assertEqual(system_mail.notify_update("0.12.0"), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["chef@example.com"])
        self.assertIn("0.12.0", message.subject)
        self.assertIn("releases/tag/v0.12.0", message.body)
        self.assertIn("Systemmail", message.body)
        self.assertEqual(system_mail.notify_update("0.12.0"), 0)
        self.assertEqual(system_mail.notify_update("0.12.1"), 1)
        self.assertEqual(SiteSettings.load().update_notified_version, "0.12.1")

    def test_not_newer_nothing(self):
        for version in ("0.11.0", "0.10.1", "", None, "kaputt"):
            with self.subTest(version=version):
                self.assertEqual(system_mail.notify_update(version), 0)
        self.assertEqual(mail.outbox, [])

    def test_language_of_the_recipient(self):
        UserPreferences.objects.update_or_create(user=self.chef, defaults={"language": "de"})
        system_mail.notify_update("0.12.0")
        self.assertIn("verfügbar", mail.outbox[0].subject)

    def test_daily_task_mails_only_when_check_finds_a_version(self):
        with patch("web.update_check.fetch_latest", return_value="0.12.0"):
            self.assertEqual(tasks.check_for_updates(), "0.12.0")
            self.assertEqual(tasks.check_for_updates(), "0.12.0")
        self.assertEqual(len(mail.outbox), 1)
        set_site(update_check=False, update_notified_version="")
        with patch("web.update_check.fetch_latest", return_value="0.13.0"):
            self.assertEqual(tasks.check_for_updates(), "")
        self.assertEqual(len(mail.outbox), 1)

    def test_smtp_error_does_not_stop_the_others(self):
        member("zweiter", superuser=True)
        with patch("web.system_mail.send_mail_to_user", side_effect=[OSError("SMTP"), True]), \
                self.assertLogs("web.system_mail", "WARNING"):
            self.assertEqual(system_mail.notify_update("0.12.0"), 1)


@override_settings(APP_VERSION="0.11.0")
@patch.object(whats_new, "ENTRIES", ENTRIES)
class WhatsNewMailTest(TestCase):
    def setUp(self):
        self.chef = member("chef", superuser=True)
        self.anna = member("anna")
        User.objects.create_user("ben", "ben@example.com", "x")

    def test_after_update_to_all_members(self):
        set_site(announced_version="0.10.1")
        with site_settings(app_base_url="https://contracts.example.com"):
            self.assertEqual(tasks.announce_release(), 2)
        by_recipient = {message.to[0]: message for message in mail.outbox}
        self.assertEqual(set(by_recipient), {"chef@example.com", "anna@example.com"})
        anna = by_recipient["anna@example.com"]
        self.assertIn("0.11.0", anna.subject)
        self.assertIn("Elf", anna.body)
        self.assertIn("für alle", anna.body)
        self.assertNotIn("nur Chef", anna.body)
        self.assertNotIn("Zehn", anna.body)
        self.assertNotIn("Zukunft", anna.body)
        self.assertIn("https://contracts.example.com" + reverse("whats_new"), anna.body)
        self.assertIn("nur Chef", by_recipient["chef@example.com"].body)
        self.assertIn("nur Chef", by_recipient["chef@example.com"].alternatives[0][0])
        # Nur einmal
        self.assertEqual(tasks.announce_release(), 0)
        self.assertEqual(SiteSettings.load().announced_version, "0.11.0")

    def test_several_versions_since_last_announcement(self):
        set_site(announced_version="0.9.2")
        system_mail.announce_release()
        self.assertIn("Zehn", mail.outbox[0].body)
        self.assertIn("Elf", mail.outbox[0].body)

    def test_new_installation_or_downgrade_only_remembers(self):
        for announced in ("", "0.12.0"):
            with self.subTest(announced=announced):
                set_site(announced_version=announced)
                self.assertEqual(system_mail.announce_release(), 0)
                self.assertEqual(SiteSettings.load().announced_version, "0.11.0")
        self.assertEqual(mail.outbox, [])

    def test_version_without_entry_sends_nothing(self):
        set_site(announced_version="0.11.0")
        with override_settings(APP_VERSION="0.11.1"):
            self.assertEqual(system_mail.announce_release(), 0)
            self.assertEqual(SiteSettings.load().announced_version, "0.11.1")
        self.assertEqual(mail.outbox, [])

    def test_schedule_after_each_migration(self):
        Schedule.objects.filter(name=ANNOUNCE_SCHEDULE_NAME).update(repeats=0)
        ensure_announce_schedule()
        schedule = Schedule.objects.get(name=ANNOUNCE_SCHEDULE_NAME)
        self.assertEqual((schedule.func, schedule.schedule_type, schedule.repeats),
                         ("web.tasks.announce_release", Schedule.ONCE, 1))


class RealEntryTest(SimpleTestCase):
    def test_entry_for_current_version(self):
        self.assertIn(settings.APP_VERSION, [entry.version for entry in whats_new.ENTRIES])


class BackupStepTest(SimpleTestCase):
    def objects(self):
        return [
            {"model": "auth.group", "pk": 3, "fields": {"name": "Administratoren", "permissions": []}},
            {"model": "auth.group", "pk": 7, "fields": {"name": "Familie", "permissions": []}},
            {"model": "auth.user", "pk": 1, "fields": {"username": "chef", "is_superuser": True, "groups": []}},
            {"model": "auth.user", "pk": 2, "fields": {"username": "admin2", "is_superuser": False,
                                                       "groups": [["Administratoren"]]}},
            {"model": "auth.user", "pk": 3, "fields": {"username": "anna", "is_superuser": False,
                                                       "groups": [["Familie"]]}},
            {"model": "web.sitesettings", "pk": 1, "fields": {}},
        ]

    def test_group_members_and_announcement(self):
        objects = backup_upgrades.upgrade(self.objects(), "0.10.1")
        self.assertEqual(objects[2], {"model": "auth.group", "pk": 8,
                                      "fields": {"name": "Systemmail", "permissions": []}})
        groups = {row["fields"]["username"]: row["fields"]["groups"] for row in objects if row["model"] == "auth.user"}
        self.assertIn(["Systemmail"], groups["chef"])
        self.assertIn(["Systemmail"], groups["admin2"])
        self.assertNotIn(["Systemmail"], groups["anna"])
        self.assertEqual(objects[-1]["fields"]["announced_version"], "0.11.0")

    def test_existing_group_is_reused(self):
        objects = self.objects()
        objects.insert(0, {"model": "auth.group", "pk": 9, "fields": {"name": "Systemmail", "permissions": []}})
        objects = backup_upgrades.upgrade(objects, "0.10.1")
        self.assertEqual(sum(row["fields"].get("name") == "Systemmail" for row in objects), 1)


class IssueFormsTest(SimpleTestCase):
    folder = Path(settings.BASE_DIR) / ".github" / "ISSUE_TEMPLATE"

    def test_one_required_field(self):
        for name in ("feature_request.yml", "bug_report.yml"):
            path = self.folder / name
            if not path.exists():  # im Image fehlt .github
                self.skipTest("Formulare nicht vorhanden")
            with self.subTest(name=name):
                text = path.read_text()
                # Ohne YAML-Bibliothek: genau ein Pflichtfeld
                self.assertEqual(text.count("required: true"), 1)
                self.assertRegex(text, r'title: "\[(Bug|Feature)\]: "')
