"""
Tests für Version 0.10.0: Neuigkeiten nach einem Update, Pfadprüfung beim
Backup (CodeQL py/path-injection).
"""

import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from contracts import backup, backup_upgrades

from . import update_check, whats_new
from .models import UserPreferences

ENTRIES = (
    whats_new.Entry("0.8.0", "Acht", (whats_new.Item("alt"),)),
    whats_new.Entry("0.9.0", "Neun", (whats_new.Item("für alle"), whats_new.Item("nur Chef", superuser_only=True))),
    whats_new.Entry("0.9.2", "Neun-Zwei", (whats_new.Item("nur Chef", superuser_only=True),)),
    whats_new.Entry("0.10.0", "Zehn", (whats_new.Item("zehn"),)),
    whats_new.Entry("0.11.0", "Zukunft", (whats_new.Item("später"),)),
)


def old_user(name="anna", **kwargs):
    """Benutzer wie vor dem Update: Neuigkeiten noch nicht gesehen."""
    user = User.objects.create_user(name, password="x", **kwargs)
    UserPreferences.objects.filter(user=user).update(seen_version="")
    return user


@override_settings(APP_VERSION="0.10.0")
@patch.object(whats_new, "ENTRIES", ENTRIES)
class WhatsNewTest(TestCase):
    def titles(self, entries):
        return [entry.title for entry, _items in entries]

    def test_versions_compared_as_numbers(self):
        self.assertGreater(whats_new.version_key("0.10.0"), whats_new.version_key("0.9.2"))
        self.assertEqual(whats_new.version_key(""), (0, 0, 0))
        self.assertEqual(update_check.parse_version("v0.10.0"), (0, 10, 0))
        self.assertEqual(backup_upgrades.parse("0.10.0"), (0, 10, 0))

    def test_pending_since_last_seen_newest_first_at_most_three(self):
        user = old_user()
        self.assertEqual(self.titles(whats_new.pending(user, "")), ["Zehn", "Neun", "Acht"])
        self.assertEqual(self.titles(whats_new.pending(user, "0.9.0")), ["Zehn"])
        self.assertEqual(whats_new.pending(user, "0.10.0"), [])

    def test_superuser_only_items(self):
        user = old_user()
        chef = User.objects.create_superuser("chef", "chef@example.com", "x")
        entries = {entry.version: items for entry, items in whats_new.entries_for(user)}
        self.assertNotIn("0.9.2", entries)
        self.assertEqual([item.text for item in entries["0.9.0"]], ["für alle"])
        entries = {entry.version: items for entry, items in whats_new.entries_for(chef)}
        self.assertEqual(len(entries["0.9.0"]), 2)
        self.assertIn("0.9.2", entries)

    def test_new_users_have_seen_current_version(self):
        user = User.objects.create_user("neu", password="x")
        self.assertEqual(UserPreferences.objects.get(user=user).seen_version, "0.10.0")
        self.client.force_login(user)
        self.assertNotContains(self.client.get(reverse("dashboard")), 'id="whats-new"')

    def test_window_once_after_update(self):
        user = old_user()
        self.client.force_login(user)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, 'id="whats-new"')
        self.assertContains(page, "Zehn")
        self.assertContains(page, "https://github.com/BrainX9/contracts-manager/releases/tag/v0.10.0")
        self.assertNotContains(page, "Zukunft")
        self.assertNotContains(page, "nur Chef")
        response = self.client.post(reverse("whats_new_seen"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(UserPreferences.objects.get(user=user).seen_version, "0.10.0")
        self.assertNotContains(self.client.get(reverse("dashboard")), 'id="whats-new"')

    def test_without_preferences_and_without_javascript(self):
        user = User.objects.create_user("ohne", password="x")
        UserPreferences.objects.filter(user=user).delete()
        self.client.force_login(user)
        page = self.client.get(reverse("contract_list"))
        self.assertContains(page, "<noscript>")
        response = self.client.post(reverse("whats_new_seen"), {"next": reverse("contract_list")})
        self.assertRedirects(response, reverse("contract_list"))
        # Fremde Adressen werden nicht angesprungen
        old = old_user("ben")
        self.client.force_login(old)
        response = self.client.post(reverse("whats_new_seen"), {"next": "https://example.com/"})
        self.assertRedirects(response, reverse("dashboard"))

    def test_page_lists_everything_and_marks_seen(self):
        user = old_user()
        self.client.force_login(user)
        page = self.client.get(reverse("whats_new"))
        self.assertContains(page, "Acht")
        self.assertContains(page, "Zehn")
        self.assertNotContains(page, 'id="whats-new"')
        self.assertContains(page, reverse("whats_new"))  # Eintrag im Benutzermenü
        self.assertEqual(UserPreferences.objects.get(user=user).seen_version, "0.10.0")

    def test_login_required_and_post_only(self):
        self.assertEqual(self.client.get(reverse("whats_new")).status_code, 302)
        self.assertEqual(self.client.post(reverse("whats_new_seen")).status_code, 302)
        self.client.force_login(old_user())
        self.assertEqual(self.client.get(reverse("whats_new_seen")).status_code, 405)


class RealEntriesTest(SimpleTestCase):
    def test_entries_are_valid(self):
        versions = [entry.version for entry in whats_new.ENTRIES]
        self.assertEqual(len(versions), len(set(versions)))
        for entry in whats_new.ENTRIES:
            self.assertIsNotNone(update_check.parse_version(entry.version), entry.version)
            self.assertTrue(entry.items)
        self.assertIn("0.10.0", versions)


class SafePathTest(SimpleTestCase):
    def test_inside_and_outside(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(backup.safe_path(root, "documents/a.pdf"),
                             Path(os.path.realpath(root)) / "documents" / "a.pdf")
            for name in ("../x", "documents/../../x", "/etc/passwd", ""):
                with self.subTest(name=name), self.assertRaises(backup.BackupError):
                    backup.safe_path(root, name)

    def test_symlink_out_of_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, os.path.join(root, "documents"))
            with self.assertRaises(backup.BackupError):
                backup.safe_path(root, "documents/a.pdf")

    def test_upload_token(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(backup, "upload_directory", return_value=Path(directory)):
            token = "a" * 32
            (Path(directory) / f"{token}.zip").write_bytes(b"x")
            self.assertEqual(backup.upload_path(token), Path(os.path.realpath(directory)) / f"{token}.zip")
            for value in ("../" + token, token[:-1], token + "0", token.upper(), None, 7, ""):
                with self.subTest(value=value):
                    self.assertIsNone(backup.upload_path(value))
            backup.discard_upload("../../etc/passwd")
            backup.discard_upload(token)
            self.assertFalse((Path(directory) / f"{token}.zip").exists())

    def test_extract_media_stays_inside(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("media/documents/a.txt", "ok")
        with zipfile.ZipFile(buffer) as archive, tempfile.TemporaryDirectory() as directory:
            members = backup.media_members(archive)
            backup.extract_media(archive, members, Path(directory))
            self.assertEqual((Path(directory) / "documents" / "a.txt").read_text(), "ok")
            os.symlink(tempfile.gettempdir(), Path(directory) / "logos")
            member = archive.getinfo("media/documents/a.txt")
            with self.assertRaises(backup.BackupError):
                backup.extract_media(archive, [("logos/x.txt", member)], Path(directory))
