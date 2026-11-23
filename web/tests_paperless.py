"""
Tests für Version 0.9.0, Teil 1: Paperless über die API (Verwaltung,
Anzeige über Contracts Manager, Paperless-ID für Uploads, Übertragung an
Paperless, Übernahme von PAPERLESS_URL). Paperless selbst wird nie
angefragt; die Anfragen sind ersetzt. Die Oberfläche ist standardmäßig deutsch.
"""

import json
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from contracts import paperless
from contracts import permissions as perms
from contracts.models import Document
from contracts.testing import paperless_settings

from . import env_import, paperless_sync
from .models import SiteSettings
from .tests import make_contract

URL = "https://paperless.example.de"


class FakeResponse:
    """Ersatz für die Antwort von urllib (Kontextmanager mit read und headers)."""

    def __init__(self, body=b"", headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = headers or {}
        self.position = 0

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.body) - self.position
        chunk = self.body[self.position:self.position + size]
        self.position += len(chunk)
        return chunk

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def api_settings(**fields):
    return paperless_settings(URL, mode="api", token="geheim", **fields)


class MediaMixin:
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

    def upload_document(self, contract, name="Police", **fields):
        return Document.objects.create(
            contract=contract, name=name, source="upload",
            file=SimpleUploadedFile("police.pdf", b"%PDF-1.4 test", content_type="application/pdf"), **fields,
        )


class ConfigTest(TestCase):
    def test_default_is_off(self):
        current = paperless.config()
        self.assertEqual(current.mode, "off")
        self.assertFalse(current.links)
        self.assertFalse(current.api)
        self.assertIsNone(paperless.document_url(5))

    def test_links_in_links_and_api_mode(self):
        with paperless_settings(URL + "/"):
            self.assertEqual(paperless.document_url(5), f"{URL}/documents/5/details")
        with api_settings():
            self.assertTrue(paperless.config().api)
            self.assertEqual(paperless.correspondent_url(7), f"{URL}/documents?correspondent__id__in=7")
        with paperless_settings(URL, mode="off"):
            self.assertIsNone(paperless.document_url(5))

    def test_api_needs_token(self):
        with paperless_settings(URL, mode="api"):
            self.assertFalse(paperless.config().api)

    def test_tags_and_user_tag(self):
        self.assertEqual(paperless.split_tags(" Vertrag, , vertrag,Contracts Manager "), ("Vertrag", "Contracts Manager"))
        user = User(username="anna")
        with api_settings(user_tag=True, user_tag_prefix="CM-"):
            self.assertEqual(paperless.config().user_tag_for(user), "CM-anna")
        with api_settings(user_tag=False):
            self.assertIsNone(paperless.config().user_tag_for(user))


class EnvImportTest(TestCase):
    @override_settings(PAPERLESS_URL=URL + "/")
    def test_paperless_url_imported_once(self):
        env_import.run()
        site = SiteSettings.load()
        self.assertEqual((site.paperless_mode, site.paperless_url), ("links", URL))
        self.assertIn("paperless", site.env_imported)
        # Später in der Verwaltung ausgeschaltet: bleibt aus
        site.paperless_mode, site.paperless_url = "off", ""
        site.save()
        with self.assertLogs("web.env_import", "WARNING"):
            env_import.run()
        site.refresh_from_db()
        self.assertEqual((site.paperless_mode, site.paperless_url), ("off", ""))

    @override_settings(PAPERLESS_URL="")
    def test_nothing_created_without_variables(self):
        SiteSettings.objects.all().delete()
        env_import.run()
        self.assertFalse(SiteSettings.objects.exists())


class SettingsPageTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "admin@example.com", "x")
        self.url = reverse("manage_paperless")

    def post(self, **data):
        return self.client.post(self.url, {
            "paperless_mode": "off", "paperless_url": "", "token": "", "paperless_tags": "",
            "paperless_user_tag_prefix": "CM-", **data,
        })

    def test_only_superusers(self):
        staff = User.objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.admin)
        page = self.client.get(self.url)
        self.assertContains(page, "Nur Links")
        self.assertContains(page, 'name="token"')

    def test_validation(self):
        self.client.force_login(self.admin)
        response = self.post(paperless_mode="links")
        self.assertEqual(set(response.context["form"].errors), {"paperless_url"})
        response = self.post(paperless_mode="api", paperless_url=URL)
        self.assertEqual(set(response.context["form"].errors), {"token"})

    def test_save_api_and_token_never_shown(self):
        self.client.force_login(self.admin)
        response = self.post(
            paperless_mode="api", paperless_url=URL + "/", token="sehr-geheim", paperless_tags="Vertrag",
            paperless_user_tag="on",
        )
        self.assertRedirects(response, self.url)
        site = SiteSettings.load()
        self.assertEqual(
            (site.paperless_mode, site.paperless_url, site.paperless_token, site.paperless_user_tag),
            ("api", URL, "sehr-geheim", True),
        )
        page = self.client.get(self.url)
        self.assertNotContains(page, "sehr-geheim")
        self.assertContains(page, 'name="clear_token"')
        # Leeres Feld behält das Token
        self.post(paperless_mode="api", paperless_url=URL)
        self.assertEqual(SiteSettings.load().paperless_token, "sehr-geheim")
        self.assertIn("paperless_token", SiteSettings.SECRET_FIELDS)

    def test_test_button_uses_entered_values(self):
        self.client.force_login(self.admin)
        with patch("contracts.paperless.check", return_value=[(True, "Verbunden.")]) as check:
            response = self.post(action="test", paperless_url=URL, token="neu")
        self.assertContains(response, "Verbunden.")
        self.assertEqual(check.call_args.args[0].token, "neu")
        self.assertEqual(SiteSettings.load().paperless_token, "")


class ApiClientTest(TestCase):
    cfg = paperless.PaperlessConfig(mode="api", base_url=URL, token="geheim")

    def test_check(self):
        responses = [FakeResponse({"count": 12, "results": []}), FakeResponse({"results": []})]
        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            results = paperless.check(paperless.PaperlessConfig(mode="api", base_url=URL, token="geheim",
                                                                tags=("Vertrag",)))
        self.assertTrue(all(ok for ok, _text in results))
        self.assertIn("12", results[0][1])
        self.assertIn("Vertrag", results[1][1])
        request = urlopen.call_args_list[0].args[0]
        self.assertEqual(request.get_header("Authorization"), "Token geheim")

    def test_http_error_without_token(self):
        from urllib.error import HTTPError

        error = HTTPError(f"{URL}/api/documents/", 401, "Unauthorized", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            results = paperless.check(self.cfg)
        self.assertFalse(results[0][0])
        self.assertNotIn("geheim", results[0][1])
        self.assertIn("401", results[0][1])

    def test_redirect_means_wrong_address(self):
        response = FakeResponse(b"<html>")
        response.geturl = lambda: f"{URL}/accounts/login/?next=/nix/api/documents/"
        with patch("urllib.request.urlopen", return_value=response):
            results = paperless.check(self.cfg)
        self.assertFalse(results[0][0])
        self.assertIn("weitergeleitet", results[0][1])

    def test_tag_ids_creates_missing(self):
        responses = [
            FakeResponse({"results": [{"id": 3, "name": "Vertrag"}]}),
            FakeResponse({"results": []}),
            FakeResponse({"id": 9, "name": "CM-anna"}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            self.assertEqual(paperless.tag_ids(self.cfg, ["Vertrag", "CM-anna"]), [3, 9])
        created = urlopen.call_args_list[2].args[0]
        self.assertEqual(json.loads(created.data), {"name": "CM-anna", "matching_algorithm": 0})

    def test_upload_sends_title_tags_and_correspondent(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse('"abc-123"'.encode())) as urlopen:
            task = paperless.upload(self.cfg, "police.pdf", b"%PDF", "Police", tags=[3, 9], correspondent=7)
        self.assertEqual(task, "abc-123")
        body = urlopen.call_args.args[0].data
        for part in (b'name="title"\r\n\r\nPolice', b'name="tags"\r\n\r\n3', b'name="tags"\r\n\r\n9',
                     b'name="correspondent"\r\n\r\n7', b'filename="police.pdf"', b"%PDF"):
            self.assertIn(part, body)

    def test_task_state(self):
        cases = [
            ([], paperless.TaskState(done=False)),
            ([{"status": "STARTED"}], paperless.TaskState(done=False)),
            ([{"status": "SUCCESS", "related_document": "42"}], paperless.TaskState(done=True, document_id=42)),
            ([{"status": "FAILURE", "result": "police.pdf: Not consuming police.pdf: It is a duplicate of "
                                               "Police (#17)."}], paperless.TaskState(done=True, document_id=17)),
            ([{"status": "FAILURE", "result": "kaputt"}], paperless.TaskState(done=True, error="kaputt")),
        ]
        for data, expected in cases:
            with patch("urllib.request.urlopen", return_value=FakeResponse(data)):
                self.assertEqual(paperless.task_state(self.cfg, "abc"), expected)


class DocumentApiTest(MediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.anna = User.objects.create_user("anna")
        self.ben = User.objects.create_user("ben")
        self.carl = User.objects.create_user("carl")
        self.contract = make_contract(self.anna, "Strom", "80.00")
        perms.grant_role(self.contract, self.ben, perms.READER)
        self.linked = Document.objects.create(
            contract=self.contract, name="Rechnung", source="paperless", paperless_document_id=42,
        )
        self.proxy_url = reverse("document_paperless", args=[self.linked.pk])
        self.detail_url = reverse("contract_detail", args=[self.contract.pk])

    def test_paperless_url_in_api_mode_points_to_contracts_manager(self):
        with api_settings():
            self.assertEqual(self.linked.paperless_url, self.proxy_url)
            self.client.force_login(self.ben)
            page = self.client.get(self.detail_url)
        self.assertContains(page, self.proxy_url)
        self.assertContains(page, f"{self.proxy_url}?original=1")
        self.assertContains(page, "In Paperless öffnen")

    def test_proxy_streams_pdf_for_readers(self):
        upstream = FakeResponse(b"%PDF-1.7 archiv", {
            "Content-Type": "application/pdf", "Content-Disposition": 'inline; filename="rechnung.pdf"',
        })
        self.client.force_login(self.ben)
        with api_settings(), patch("contracts.paperless.open_document", return_value=upstream) as fetch:
            response = self.client.get(self.proxy_url)
            content = b"".join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(content, b"%PDF-1.7 archiv")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(fetch.call_args.kwargs, {"original": False})

    def test_original_is_a_download(self):
        upstream = FakeResponse(b"original", {"Content-Type": "image/jpeg"})
        self.client.force_login(self.anna)
        with api_settings(), patch("contracts.paperless.open_document", return_value=upstream) as fetch:
            response = self.client.get(self.proxy_url + "?original=1")
            b"".join(response.streaming_content)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertEqual(fetch.call_args.kwargs, {"original": True})

    def test_proxy_404_for_strangers_and_without_api(self):
        self.client.force_login(self.carl)
        with api_settings():
            self.assertEqual(self.client.get(self.proxy_url).status_code, 404)
        self.client.force_login(self.anna)
        with paperless_settings(URL):
            self.assertEqual(self.client.get(self.proxy_url).status_code, 404)

    def test_proxy_error_page(self):
        self.client.force_login(self.anna)
        error = paperless.PaperlessError("Nicht gefunden in Paperless (HTTP 404).")
        with api_settings(), patch("contracts.paperless.open_document", side_effect=error):
            response = self.client.get(self.proxy_url)
        self.assertEqual(response.status_code, 502)
        self.assertContains(response, "Nicht gefunden", status_code=502)

    def test_upload_may_have_paperless_id(self):
        document = self.upload_document(self.contract, paperless_document_id=50)
        self.assertEqual(document.paperless_document_id, 50)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.upload_document(self.contract, name="Doppelt", paperless_document_id=42)
        # Paperless-Dokumente brauchen weiter eine ID und keine Datei
        with self.assertRaises(IntegrityError), transaction.atomic():
            Document.objects.create(contract=self.contract, name="Leer", source="paperless")

    def test_edit_upload_adds_paperless_id(self):
        document = self.upload_document(self.contract, paperless_sync=Document.SYNC_FAILED,
                                        paperless_sync_error="kaputt")
        url = reverse("document_edit", args=[document.pk])
        self.client.force_login(self.anna)
        with paperless_settings(URL):
            page = self.client.get(url)
            self.assertContains(page, 'name="paperless_document_id"')
            duplicate = self.client.post(url, {"name": "Police", "notes": "", "paperless_document_id": "42"})
            self.assertContains(duplicate, "bereits mit diesem Vertrag verknüpft")
            self.assertRedirects(
                self.client.post(url, {"name": "Police", "notes": "", "paperless_document_id": "77"}),
                self.detail_url,
            )
            page = self.client.get(self.detail_url)
        document.refresh_from_db()
        self.assertEqual((document.paperless_document_id, document.paperless_sync), (77, ""))
        self.assertContains(page, f"{URL}/documents/77/details")

    def test_paperless_off_offers_only_uploads(self):
        self.client.force_login(self.anna)
        add_url = reverse("document_add", args=[self.contract.pk])
        page = self.client.get(add_url + "?source=paperless")
        self.assertNotContains(page, "Paperless-Dokument verknüpfen")
        self.assertContains(page, 'type="file"')
        response = self.client.post(add_url, {"source": "paperless", "name": "X", "paperless_document_id": "5"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.contract.documents.count(), 1)
        document = self.upload_document(self.contract)
        page = self.client.get(reverse("document_edit", args=[document.pk]))
        self.assertNotContains(page, 'name="paperless_document_id"')


class SyncViewTest(MediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.anna = User.objects.create_user("anna")
        self.ben = User.objects.create_user("ben")
        self.contract = make_contract(self.anna, "Strom", "80.00")
        perms.grant_role(self.contract, self.ben, perms.READER)
        self.document = self.upload_document(self.contract)
        self.url = reverse("document_sync", args=[self.document.pk])

    def test_button_only_with_api_and_edit_right(self):
        detail = reverse("contract_detail", args=[self.contract.pk])
        self.client.force_login(self.anna)
        self.assertNotContains(self.client.get(detail), self.url)
        with api_settings():
            self.assertContains(self.client.get(detail), self.url)
            self.client.force_login(self.ben)
            self.assertNotContains(self.client.get(detail), self.url)
            self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_start_marks_pending_and_queues_task(self):
        self.client.force_login(self.anna)
        with api_settings(), patch("django_q.tasks.async_task") as queue:
            response = self.client.post(self.url, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Wird an Paperless übertragen")
        self.assertContains(response, 'hx-trigger="every 3s"')
        self.document.refresh_from_db()
        self.assertEqual(self.document.paperless_sync, Document.SYNC_PENDING)
        queue.assert_called_once_with("web.paperless_sync.transfer", self.document.pk, self.anna.pk)
        # Während der Übertragung kein zweiter Start
        with api_settings():
            self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_no_sync_without_api_or_with_id(self):
        self.client.force_login(self.anna)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        Document.objects.filter(pk=self.document.pk).update(paperless_document_id=5)
        with api_settings():
            self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_section_endpoint_needs_read_right(self):
        url = reverse("document_section", args=[self.contract.pk])
        self.client.force_login(self.ben)
        self.assertContains(self.client.get(url), 'id="document-list"')
        self.client.force_login(User.objects.create_user("carl"))
        self.assertEqual(self.client.get(url).status_code, 404)


class SyncTaskTest(MediaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.anna = User.objects.create_user("anna")
        self.contract = make_contract(self.anna, "Strom", "80.00")
        self.contract.provider.paperless_correspondent_id = 7
        self.contract.provider.save()
        self.document = self.upload_document(self.contract, paperless_sync=Document.SYNC_PENDING)

    def run_transfer(self, state, **settings_fields):
        with api_settings(tags="Vertrag", **settings_fields), \
                patch("contracts.paperless.tag_ids", return_value=[3, 9]) as tag_ids, \
                patch("contracts.paperless.upload", return_value="task-1") as upload, \
                patch("contracts.paperless.task_state", return_value=state):
            result = paperless_sync.transfer(self.document.pk, self.anna.pk)
        self.document.refresh_from_db()
        return result, tag_ids, upload

    def test_success_stores_id(self):
        result, tag_ids, upload = self.run_transfer(
            paperless.TaskState(done=True, document_id=99), user_tag=True, user_tag_prefix="CM-",
        )
        self.assertEqual(result, "document 99")
        self.assertEqual((self.document.paperless_document_id, self.document.paperless_sync), (99, ""))
        self.assertEqual(tag_ids.call_args.args[1], ["Vertrag", "CM-anna"])
        self.assertEqual(upload.call_args.kwargs, {"tags": [3, 9], "correspondent": 7})
        self.assertEqual(upload.call_args.args[1:4], ("police.pdf", b"%PDF-1.4 test", "Police"))

    def test_failure_is_shown(self):
        self.run_transfer(paperless.TaskState(done=True, error="Datei kaputt"))
        self.assertEqual(self.document.paperless_sync, Document.SYNC_FAILED)
        self.assertEqual(self.document.paperless_sync_error, "Datei kaputt")
        self.assertIsNone(self.document.paperless_document_id)

    def test_upload_error(self):
        with api_settings(), patch("contracts.paperless.tag_ids", side_effect=paperless.PaperlessError("weg")):
            self.assertEqual(paperless_sync.transfer(self.document.pk, self.anna.pk), "failed")
        self.document.refresh_from_db()
        self.assertEqual((self.document.paperless_sync, self.document.paperless_sync_error), ("failed", "weg"))

    def test_still_processing_schedules_follow_up(self):
        from django_q.models import Schedule

        with patch.object(paperless_sync, "POLL_SECONDS", 0):
            result, _tags, _upload = self.run_transfer(paperless.TaskState(done=False))
        self.assertEqual(result, "waiting")
        self.assertEqual(self.document.paperless_sync_task, "task-1")
        schedule = Schedule.objects.get(func="web.paperless_sync.follow")
        self.assertEqual(schedule.args, repr(self.document.pk))
        with api_settings(), patch("contracts.paperless.task_state",
                                   return_value=paperless.TaskState(done=True, document_id=5)):
            self.assertEqual(paperless_sync.follow(self.document.pk), "document 5")

    def test_duplicate_in_contract_fails(self):
        Document.objects.create(contract=self.contract, name="Alt", source="paperless", paperless_document_id=99)
        self.run_transfer(paperless.TaskState(done=True, document_id=99))
        self.assertEqual(self.document.paperless_sync, Document.SYNC_FAILED)
        self.assertIn("#99", self.document.paperless_sync_error)
