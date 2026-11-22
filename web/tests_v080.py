"""
Tests für Version 0.8.0: Status im Formular (Fehler aus 0.7.0), Vorgänger und
Nachfolger, ALLOWED_HOSTS optional, Links mit der Adresse der App, erster
Administrator im Browser, Mistral als Anbieter und Mistral OCR.
Die Oberfläche ist standardmäßig deutsch.
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from contracts import permissions as perms
from contracts.models import Contract

from .models import SiteSettings
from .tests import make_contract


class ContractFormFieldsTest(TestCase):
    """Fehler aus 0.7.0: Status und "Gekündigt zum" fehlten im Formular"""

    def test_form_page_shows_status_and_cancelled_as_of(self):
        from django.contrib.auth.models import Group

        owner = User.objects.create_user("anna")
        owner.groups.add(Group.objects.get(name=perms.GROUP_USERS))
        contract = make_contract(owner, "Strom", "50.00")
        self.client.force_login(owner)
        page = self.client.get(reverse("contract_edit", args=[contract.pk]))
        self.assertContains(page, 'name="status"')
        self.assertContains(page, 'name="cancelled_as_of"')
        self.assertContains(page, "Kündigung verschickt")
        # Vorgänger und Nachfolger mit Suchfeld
        self.assertContains(page, 'name="predecessor"')
        self.assertContains(page, 'name="successor"')
        self.assertContains(page, f'hx-get="{reverse("contract_link_options")}"', count=2)
        page = self.client.get(reverse("contract_create"))
        self.assertContains(page, 'name="status"')


class ContractLinkTest(TestCase):
    """Vorgänger- und Nachfolgevertrag mit Gegeneintrag"""

    def setUp(self):
        self.owner = User.objects.create_user("anna")
        self.other = User.objects.create_user("bert")
        self.old = make_contract(self.owner, "Strom 2025", "50.00")
        self.new = make_contract(self.owner, "Strom 2026", "55.00")
        self.client.force_login(self.owner)

    def data(self, contract, **values):
        data = {"name": contract.name, "provider": contract.provider_id, "category": contract.category_id,
                "predecessor": "", "successor": ""}
        data.update(values)
        return data

    def post(self, contract, **values):
        return self.client.post(reverse("contract_edit", args=[contract.pk]), self.data(contract, **values))

    def test_successor_creates_the_counter_entry(self):
        self.assertEqual(self.post(self.old, successor=self.new.pk).status_code, 302)
        self.new.refresh_from_db()
        self.assertEqual(self.new.predecessor, self.old)
        self.assertEqual(Contract.objects.get(pk=self.old.pk).successor_contract, self.new)
        # Auf beiden Vertragsseiten sichtbar
        page = self.client.get(reverse("contract_detail", args=[self.old.pk]))
        self.assertContains(page, "Nachfolgevertrag")
        self.assertContains(page, "Strom 2026")
        page = self.client.get(reverse("contract_detail", args=[self.new.pk]))
        self.assertContains(page, "Vorgängervertrag")
        self.assertContains(page, "Strom 2025")

    def test_predecessor_and_removing_the_link(self):
        self.post(self.new, predecessor=self.old.pk)
        self.assertEqual(Contract.objects.get(pk=self.old.pk).successor_contract, self.new)
        # Leeren beim alten Vertrag löst die Verknüpfung auch beim neuen
        self.post(self.old, successor="")
        self.new.refresh_from_db()
        self.assertIsNone(self.new.predecessor)

    def test_no_loops_and_not_both_sides(self):
        self.post(self.new, predecessor=self.old.pk)
        response = self.post(self.new, predecessor=self.old.pk, successor=self.old.pk)
        self.assertEqual(response.status_code, 200)
        third = make_contract(self.owner, "Strom 2027", "60.00")
        self.post(third, predecessor=self.new.pk)
        # old -> new -> third: third als Vorgänger von old wäre ein Kreis
        response = self.post(self.old, predecessor=third.pk)
        self.assertEqual(response.status_code, 200)
        self.old.refresh_from_db()
        self.assertIsNone(self.old.predecessor)

    def test_only_free_contracts_can_be_chosen(self):
        from .forms import link_candidates

        self.post(self.new, predecessor=self.old.pk)
        third = make_contract(self.owner, "Strom 2027", "60.00")
        # old hat schon einen Nachfolger: als Vorgänger von third nicht mehr frei
        self.assertNotIn(self.old, link_candidates(self.owner, third, "predecessor"))
        self.assertIn(self.new, link_candidates(self.owner, third, "predecessor"))
        # new hat schon einen Vorgänger: als Nachfolger nicht mehr frei
        self.assertNotIn(self.new, link_candidates(self.owner, third, "successor"))
        # Der Vertrag selbst nie
        self.assertNotIn(third, link_candidates(self.owner, third, "successor"))

    def test_needs_edit_rights_on_both_contracts(self):
        from .forms import link_candidates

        foreign = make_contract(self.other, "Fremder Vertrag", "10.00")
        perms.grant_role(foreign, self.owner, perms.READER)
        self.assertNotIn(foreign, link_candidates(self.owner, self.old, "successor"))
        response = self.post(self.old, successor=foreign.pk)
        self.assertEqual(response.status_code, 200)
        foreign.refresh_from_db()
        self.assertIsNone(foreign.predecessor)
        # Schon verknüpft mit einem nur lesbaren Vertrag: das Feld bleibt gesperrt
        foreign.predecessor = self.old
        foreign.save()
        page = self.client.get(reverse("contract_edit", args=[self.old.pk]))
        self.assertTrue(page.context["form"].fields["successor"].disabled)
        self.post(self.old)
        foreign.refresh_from_db()
        self.assertEqual(foreign.predecessor, self.old)

    def test_hidden_link_is_not_shown(self):
        foreign = make_contract(self.other, "Geheim", "10.00")
        foreign.predecessor = self.old
        foreign.save()
        page = self.client.get(reverse("contract_detail", args=[self.old.pk]))
        self.assertNotContains(page, "Geheim")

    def test_search_options(self):
        url = reverse("contract_link_options")
        page = self.client.get(url, {"field": "successor", "contract": self.old.pk, "q": "2026"})
        self.assertContains(page, "Strom 2026")
        self.assertNotContains(page, "Strom 2025")
        page = self.client.get(url, {"field": "successor", "contract": self.old.pk, "q": "nichts"})
        self.assertContains(page, "Kein passender Vertrag")
        self.assertEqual(self.client.get(url, {"field": "owner"}).status_code, 404)

    def test_model_rejects_itself(self):
        from django.core.exceptions import ValidationError

        self.old.predecessor = self.old
        with self.assertRaises(ValidationError):
            self.old.save()

    def test_assistant_sees_the_link(self):
        from assistant.tools import get_contract

        self.new.predecessor = self.old
        self.new.save()
        data = get_contract(self.owner, contract_id=self.new.pk)
        self.assertEqual(data["predecessor"]["name"], "Strom 2025")
        self.assertIsNone(data["successor"])


class AllowedHostsTest(TestCase):
    def test_flag_and_hint(self):
        admin = User.objects.create_superuser("chef", "chef@example.test", "x")
        self.client.force_login(admin)
        with override_settings(ALLOWED_HOSTS_SET=False, ALLOWED_HOSTS=["*"]):
            self.assertContains(self.client.get(reverse("manage_system")), "ALLOWED_HOSTS ist nicht gesetzt")
        with override_settings(ALLOWED_HOSTS_SET=True):
            self.assertNotContains(self.client.get(reverse("manage_system")), "ALLOWED_HOSTS ist nicht gesetzt")

    def test_links_use_the_address_of_the_app(self):
        from contracts.mail import link_url

        SiteSettings.load()
        SiteSettings.objects.filter(pk=1).update(app_base_url="https://contracts.example.test")
        self.assertEqual(link_url("/x/", "https://evil.example"), "https://contracts.example.test/x/")
        SiteSettings.objects.filter(pk=1).update(app_base_url="")
        with override_settings(APP_BASE_URL=""):
            self.assertEqual(link_url("/x/", "https://host.example/"), "https://host.example/x/")


class FirstAdminTest(TestCase):
    url = reverse("first_admin")

    def test_new_installation_goes_to_setup(self):
        self.assertRedirects(self.client.get(reverse("login")), self.url)
        self.assertRedirects(self.client.get(reverse("dashboard")), f"{reverse('login')}?next=/",
                             target_status_code=302)
        page = self.client.get(self.url)
        self.assertContains(page, "Administrator anlegen")

    def test_create_and_log_in(self):
        response = self.client.post(self.url, {
            "username": "chef", "email": "chef@example.test",
            "password1": "Sehr-gutes-Passwort-7391", "password2": "Sehr-gutes-Passwort-7391",
        })
        self.assertRedirects(response, reverse("dashboard"))
        user = User.objects.get(username="chef")
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        # Danach gibt es die Seite nicht mehr, und die Anmeldung ist normal
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"username": "zwei"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_password_rules_apply(self):
        response = self.client.post(self.url, {"username": "chef", "password1": "123", "password2": "123"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_existing_installation_never_sees_setup(self):
        User.objects.create_user("anna")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)


class MistralTest(TestCase):
    def site(self, **values):
        site = SiteSettings.load()
        for name, value in values.items():
            setattr(site, name, value)
        site.save()

    @override_settings(AI_ENABLED=True, AI_PROVIDER="openai", AI_MODEL="m", AI_API_KEY="sk-env",
                       AI_BASE_URL="http://127.0.0.1:9")
    def test_default_address_and_shared_key(self):
        from assistant.config import MISTRAL_DEFAULT_URL, current
        from assistant.providers import OpenAIProvider, get_provider

        self.site(ai_provider="mistral", ai_model="mistral-medium-latest", ai_api_key="sk-mistral",
                  reader_model="mistral-ocr-latest", reader_kind="mistral_ocr")
        config = current()
        self.assertEqual((config.provider, config.base_url), ("mistral", MISTRAL_DEFAULT_URL))
        self.assertEqual(MISTRAL_DEFAULT_URL, "https://api.mistral.ai/v1")
        self.assertEqual((config.reader.provider, config.reader.base_url, config.reader.api_key, config.reader.kind),
                         ("mistral", MISTRAL_DEFAULT_URL, "sk-mistral", "mistral_ocr"))
        self.assertIsInstance(get_provider(), OpenAIProvider)

    def test_message_format_for_mistral(self):
        from assistant.providers import OpenAIProvider

        messages = [
            {"role": "user", "content": "Frage"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "abc123def", "name": "t", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "abc123def", "name": "t", "content": "{}"},
        ]
        mistral = OpenAIProvider.convert_messages("S", messages, mistral=True)
        self.assertEqual(mistral[2]["content"], "")
        self.assertEqual(mistral[3]["name"], "t")
        openai_format = OpenAIProvider.convert_messages("S", messages)
        self.assertIsNone(openai_format[2]["content"])
        self.assertNotIn("name", openai_format[3])

    def reader(self):
        from assistant.config import ReaderConfig

        return ReaderConfig(provider="mistral", model="mistral-ocr-latest", base_url="https://api.mistral.ai/v1",
                            api_key="sk-geheim", kind="mistral_ocr")

    def fake_response(self, pages):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"pages": pages}).encode()
        return response

    def test_ocr_reads_the_pdf_directly(self):
        from assistant import vision

        response = self.fake_response([{"index": 0, "markdown": "Seite eins"}, {"index": 1, "markdown": "Seite zwei"}])
        with patch("urllib.request.urlopen", return_value=response) as urlopen, \
                patch("assistant.vision.pdf_page_images") as render:
            text = vision.read_pdf(b"%PDF-1.4", self.reader())
        render.assert_not_called()
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.mistral.ai/v1/ocr")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-geheim")
        self.assertEqual(body["model"], "mistral-ocr-latest")
        self.assertEqual(body["document"]["type"], "document_url")
        self.assertTrue(body["document"]["document_url"].startswith("data:application/pdf;base64,"))
        self.assertEqual(body["pages"], list(range(vision.MAX_PAGES)))
        self.assertEqual(text, "--- Page 1 ---\nSeite eins\n\n--- Page 2 ---\nSeite zwei")

    def test_ocr_reads_photos_as_image(self):
        from assistant import vision

        response = self.fake_response([{"index": 0, "markdown": "Rechnung"}])
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(vision.transcribe(b"jpeg", self.reader()), "Rechnung")
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["document"]["type"], "image_url")
        self.assertTrue(body["document"]["image_url"].startswith("data:image/jpeg;base64,"))

    def test_ocr_error_hides_the_key(self):
        import urllib.error

        from assistant import vision
        from assistant.providers import ProviderError

        error = urllib.error.HTTPError("https://api.mistral.ai/v1/ocr", 401, "Unauthorized", {},
                                       io.BytesIO(b'{"message": "invalid key sk-geheim"}'))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ProviderError) as raised:
                vision.read_pdf(b"%PDF-1.4", self.reader())
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("sk-geheim", str(raised.exception))

    def test_ocr_kind_needs_mistral(self):
        from assistant.agent import DEFAULT_PROMPT

        admin = User.objects.create_superuser("chef", "chef@example.test", "x")
        self.client.force_login(admin)
        data = {"ai_enabled": "", "ai_provider": "openai", "ai_model": "m", "ai_base_url": "", "api_key": "",
                "ai_system_prompt": DEFAULT_PROMPT, "reader_provider": "", "reader_model": "mistral-ocr-latest",
                "reader_base_url": "", "reader_kind": "mistral_ocr", "reader_key": "", "action": "save"}
        self.assertEqual(self.client.post(reverse("manage_ai"), data).status_code, 200)
        self.assertEqual(SiteSettings.load().reader_kind, "vision")
        data["ai_provider"] = "mistral"
        self.assertEqual(self.client.post(reverse("manage_ai"), data).status_code, 302)
        self.assertEqual(SiteSettings.load().reader_kind, "mistral_ocr")
