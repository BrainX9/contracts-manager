"""Tests für den KI-Assistenten (Version 0.8a), ohne Aufrufe beim Anbieter."""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contracts import permissions as perms
from contracts.models import Category, Consumer, Contract, CostEntry, Provider

from . import agent, tools
from .models import Conversation, Message, Proposal
from .providers import AnthropicProvider, OpenAIProvider, ProviderError, RefusalError, Reply

# Unerreichbare Adresse: Tests dürfen nie echte Anfragen senden
AI_SETTINGS = {
    "AI_ENABLED": True, "AI_PROVIDER": "openai", "AI_MODEL": "test-model", "AI_API_KEY": "sk-test",
    "AI_BASE_URL": "http://127.0.0.1:9",
}


def make_contract(owner, name, amount, interval="monthly", category="Versicherung", provider="Allianz"):
    contract = Contract.objects.create(
        owner=owner, name=name, start_date=timezone.localdate(),
        provider=Provider.objects.get_or_create(owner=owner, name=provider)[0],
        category=Category.objects.get_or_create(owner=owner, name=category, parent=None)[0],
        consumer=Consumer.objects.get_or_create(owner=owner, name="Haushalt")[0],
    )
    CostEntry.objects.create(contract=contract, amount=Decimal(amount), payment_interval=interval,
                             valid_from=timezone.localdate())
    return contract


class FakeProvider:
    """Gibt vorbereitete Antworten zurück und merkt sich die Aufrufe."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, messages, tools, allow_tools=True):
        self.calls.append({"system": system, "messages": messages, "tools": tools, "allow_tools": allow_tools})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class DefaultGroupTest(TestCase):
    def test_ai_group_with_permission(self):
        group = Group.objects.get(name=perms.GROUP_AI)
        self.assertEqual(list(group.permissions.values_list("codename", flat=True)), ["use_assistant"])
        self.assertIn(perms.GROUP_AI, perms.PROTECTED_GROUPS)

    def test_can_use_assistant(self):
        member = User.objects.create_user("anna")
        member.groups.add(Group.objects.get(name=perms.GROUP_AI))
        other = User.objects.create_user("ben")
        admin = User.objects.create_superuser("chef", "chef@example.com", "x")
        with self.settings(AI_ENABLED=True):
            self.assertTrue(perms.can_use_assistant(User.objects.get(pk=member.pk)))
            self.assertFalse(perms.can_use_assistant(other))
            self.assertTrue(perms.can_use_assistant(admin))
        with self.settings(AI_ENABLED=False):
            self.assertFalse(perms.can_use_assistant(admin))


class ToolsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna")
        cls.ben = User.objects.create_user("ben")
        cls.kfz = make_contract(cls.anna, "KFZ-Versicherung", "120.00", "quarterly")
        cls.strom = make_contract(cls.anna, "Strom", "80.00", category="Energie", provider="Stadtwerke")
        cls.fremd = make_contract(cls.ben, "Handy", "20.00", category="Telefon", provider="Telekom")

    def test_search_only_visible(self):
        result = tools.search_contracts(self.anna, "")
        self.assertEqual([c["name"] for c in result["contracts"]], ["KFZ-Versicherung", "Strom"])
        self.assertEqual(tools.search_contracts(self.ben, "Strom")["total"], 0)
        perms.grant_role(self.strom, self.ben, perms.READER)
        self.assertEqual(tools.search_contracts(self.ben, "stadtwerke")["total"], 1)

    def test_search_by_partner_and_amounts(self):
        result = tools.search_contracts(self.anna, "allianz")
        self.assertEqual(result["contracts"][0]["current_amount"], "120.00")
        self.assertEqual(result["contracts"][0]["monthly_amount"], "40.00")
        self.assertEqual(result["contracts"][0]["reference"], self.kfz.reference)

    def test_get_contract(self):
        data = tools.get_contract(self.anna, reference=self.kfz.reference)
        self.assertEqual(data["name"], "KFZ-Versicherung")
        self.assertEqual(len(data["cost_history"]), 1)
        self.assertIsNone(data["reminder"])
        self.assertEqual(tools.get_contract(self.anna, contract_id=self.fremd.pk), {"error": "No visible contract found."})
        self.assertIn("error", tools.get_contract(self.anna))

    def test_cost_overview(self):
        overview = tools.cost_overview(self.anna, "category")
        self.assertEqual(overview["total"], {"monthly": "120.00", "yearly": "1440.00", "contracts": 2})
        self.assertEqual([(g["name"], g["monthly"]) for g in overview["groups"]],
                         [("Energie", "80.00"), ("Versicherung", "40.00")])

    def test_master_data(self):
        result = tools.search_master_data(self.anna, "provider", "stadt")
        self.assertEqual([p["name"] for p in result["results"]], ["Stadtwerke"])
        self.assertEqual(tools.search_master_data(self.anna, "provider", "telekom")["results"], [])
        self.assertIn("error", tools.search_master_data(self.anna, "user"))

    def test_run_tool_errors(self):
        self.assertIn("error", tools.run_tool(self.anna, "delete_contract", {}))
        self.assertIn("error", tools.run_tool(self.anna, "get_contract", {"unknown": 1}))

    def test_no_writing_tools(self):
        names = {tool["name"] for tool in tools.tool_definitions()}
        self.assertEqual(names, {"search_contracts", "get_contract", "cost_overview", "search_master_data"})


class ProviderConversionTest(TestCase):
    history = [
        {"role": "user", "content": "Was zahle ich?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "cost_overview", "arguments": {}},
            {"id": "c2", "name": "search_contracts", "arguments": {"query": "x"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "cost_overview", "content": "{}"},
        {"role": "tool", "tool_call_id": "c2", "name": "search_contracts", "content": "{}"},
    ]

    def test_openai_format(self):
        messages = OpenAIProvider.convert_messages("System", self.history)
        self.assertEqual(messages[0], {"role": "system", "content": "System"})
        self.assertEqual(messages[2]["tool_calls"][1]["function"],
                         {"name": "search_contracts", "arguments": json.dumps({"query": "x"})})
        self.assertIsNone(messages[2]["content"])
        self.assertEqual(messages[3], {"role": "tool", "tool_call_id": "c1", "content": "{}"})

    def test_anthropic_merges_tool_results(self):
        messages = AnthropicProvider.convert_messages(self.history)
        self.assertEqual(len(messages), 3)
        self.assertEqual([b["type"] for b in messages[1]["content"]], ["tool_use", "tool_use"])
        self.assertEqual([b["tool_use_id"] for b in messages[2]["content"]], ["c1", "c2"])

    @override_settings(**AI_SETTINGS)
    def test_openai_reply(self):
        provider = OpenAIProvider()
        call = SimpleNamespace(id="c9", function=SimpleNamespace(name="get_contract", arguments='{"contract_id": 3}'))
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))])
        with patch.object(provider.client.chat.completions, "create", return_value=response) as create:
            reply = provider.complete("System", [{"role": "user", "content": "Hi"}], tools.tool_definitions())
        self.assertEqual(reply.tool_calls, [{"id": "c9", "name": "get_contract", "arguments": {"contract_id": 3}}])
        self.assertEqual(create.call_args.kwargs["model"], "test-model")
        self.assertEqual(create.call_args.kwargs["tools"][0]["type"], "function")

    @override_settings(**{**AI_SETTINGS, "AI_PROVIDER": "anthropic"})
    def test_anthropic_reply(self):
        provider = AnthropicProvider()
        blocks = [
            {"type": "text", "text": "Moment"},
            {"type": "tool_use", "id": "t1", "name": "cost_overview", "input": {"group_by": "none"}},
        ]
        response = SimpleNamespace(stop_reason="tool_use", content=[
            SimpleNamespace(**block, model_dump=lambda block=block, **kwargs: dict(block)) for block in blocks
        ])
        with patch.object(provider.client.messages, "create", return_value=response) as create:
            reply = provider.complete("System", [{"role": "user", "content": "Hi"}], tools.tool_definitions())
        self.assertEqual(reply.text, "Moment")
        self.assertEqual(reply.tool_calls[0]["arguments"], {"group_by": "none"})
        self.assertEqual(create.call_args.kwargs["system"], "System")
        self.assertIn("input_schema", create.call_args.kwargs["tools"][0])


@override_settings(**AI_SETTINGS)
class AgentTest(TestCase):
    def setUp(self):
        self.anna = User.objects.create_user("anna")
        self.contract = make_contract(self.anna, "Strom", "80.00")
        self.conversation = Conversation.objects.create(user=self.anna)
        agent.add_user_message(self.conversation, "Was kostet Strom?\nDanke")

    def run_agent(self, replies):
        fake = FakeProvider(replies)
        with patch("assistant.agent.get_provider", return_value=fake):
            agent.answer(self.conversation)
        self.conversation.refresh_from_db()
        return fake

    def test_title_and_running(self):
        self.assertEqual(self.conversation.title, "Was kostet Strom?")
        self.assertTrue(self.conversation.is_running)

    def test_tool_round_and_answer(self):
        fake = self.run_agent([
            Reply(tool_calls=[{"id": "c1", "name": "search_contracts", "arguments": {"query": "Strom"}}]),
            Reply(text=f"Strom ({self.contract.reference}) kostet 80,00 € im Monat."),
        ])
        self.assertEqual(self.conversation.status, "idle")
        roles = list(self.conversation.messages.values_list("role", flat=True))
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        tool_result = json.loads(self.conversation.messages.get(role="tool").content)
        self.assertEqual(tool_result["contracts"][0]["name"], "Strom")
        # Zweiter Aufruf enthält das Werkzeug-Ergebnis
        self.assertEqual(fake.calls[1]["messages"][-1]["role"], "tool")
        self.assertIn("German", fake.calls[0]["system"])

    def test_provider_error(self):
        self.run_agent([ProviderError("401: invalid key")])
        self.assertEqual((self.conversation.status, self.conversation.error), ("error", "401: invalid key"))

    @override_settings(AI_MAX_TOOL_ROUNDS=2)
    def test_stops_after_max_rounds(self):
        loop = Reply(tool_calls=[{"id": "x", "name": "cost_overview", "arguments": {}}])
        fake = self.run_agent([loop, loop, Reply(text="Fertig")])
        # Gleiche Werkzeuge (unveränderter Verlauf), aber nicht mehr nutzbar
        self.assertEqual(fake.calls[-1]["tools"], fake.calls[0]["tools"])
        self.assertFalse(fake.calls[-1]["allow_tools"])
        self.assertEqual(self.conversation.messages.last().content, "Fertig")
        self.assertEqual(self.conversation.status, "idle")

    def test_refusal(self):
        self.run_agent([RefusalError("cyber")])
        self.assertEqual((self.conversation.status, self.conversation.error), ("error", "refusal: cyber"))

    def test_system_prompt_frozen_and_dated_messages(self):
        fake = self.run_agent([Reply(text="Erste Antwort")])
        first_system = fake.calls[0]["system"]
        self.assertEqual(self.conversation.system_prompt, first_system)
        self.assertNotIn(timezone.localdate().isoformat(), first_system)
        today = timezone.localdate().isoformat()
        self.assertEqual(fake.calls[0]["messages"][0]["content"], f"[{today}] Was kostet Strom?\nDanke")
        # Spätere Änderungen (z. B. Sprache) ändern den Prompt der Unterhaltung nicht
        agent.add_user_message(self.conversation, "Und Gas?")
        with patch("assistant.agent.system_prompt", return_value="ANDERS"):
            fake = self.run_agent([Reply(text="Zweite Antwort")])
        self.assertEqual(fake.calls[0]["system"], first_system)

    def test_raw_content_stored_and_replayed(self):
        raw = [{"type": "thinking", "thinking": "", "signature": "sig"},
               {"type": "tool_use", "id": "c1", "name": "cost_overview", "input": {}}]
        fake = self.run_agent([
            Reply(tool_calls=[{"id": "c1", "name": "cost_overview", "arguments": {}}], provider="anthropic", raw=raw),
            Reply(text="Fertig", provider="anthropic", raw=[{"type": "text", "text": "Fertig"}]),
        ])
        stored = self.conversation.messages.filter(role="assistant").first()
        self.assertEqual((stored.provider, stored.provider_content), ("anthropic", raw))
        second = fake.calls[1]["messages"]
        self.assertEqual(second[1]["raw"], raw)
        converted = AnthropicProvider.convert_messages(second)
        self.assertEqual(converted[1], {"role": "assistant", "content": raw})
        # Andere Anbieter bekommen das neutrale Format
        self.assertNotIn("thinking", json.dumps(OpenAIProvider.convert_messages("S", second)))

    def test_history_skips_unanswered_tool_calls(self):
        Message.objects.create(conversation=self.conversation, role="assistant",
                               tool_calls=[{"id": "lost", "name": "cost_overview", "arguments": {}}])
        self.assertEqual([m["role"] for m in agent.history(self.conversation)], ["user"])


@override_settings(**AI_SETTINGS)
class AssistantPagesTest(TestCase):
    def setUp(self):
        self.anna = User.objects.create_user("anna")
        self.anna.groups.add(Group.objects.get(name=perms.GROUP_AI))
        self.ben = User.objects.create_user("ben")
        self.url = reverse("assistant_home")

    def test_access(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.ben)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotContains(self.client.get(reverse("dashboard")), self.url)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertContains(self.client.get(reverse("dashboard")), self.url)
        with self.settings(AI_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertNotContains(self.client.get(reverse("dashboard")), self.url)

    def test_new_conversation_queues_answer(self):
        self.client.force_login(self.anna)
        with patch("assistant.views.queue_answer") as queue:
            response = self.client.post(self.url, {"text": "Welche Verträge habe ich?"})
        conversation = Conversation.objects.get()
        self.assertRedirects(response, reverse("assistant_conversation", args=[conversation.pk]))
        queue.assert_called_once_with(conversation)
        page = self.client.get(reverse("assistant_conversation", args=[conversation.pk]))
        self.assertContains(page, "Welche Verträge habe ich?")
        self.assertContains(page, 'hx-trigger="every 2s"')

    def test_reply_shown_and_polling_stops(self):
        conversation = Conversation.objects.create(user=self.anna, title="Test")
        Message.objects.create(conversation=conversation, role="user", content="Frage")
        Message.objects.create(conversation=conversation, role="assistant",
                               tool_calls=[{"id": "1", "name": "cost_overview", "arguments": {}}])
        Message.objects.create(conversation=conversation, role="tool", tool_call_id="1", content="{}")
        Message.objects.create(conversation=conversation, role="assistant", content="Antwort mit <b>HTML</b>")
        self.client.force_login(self.anna)
        response = self.client.get(reverse("assistant_messages", args=[conversation.pk]))
        self.assertContains(response, "Antwort mit &lt;b&gt;HTML&lt;/b&gt;")
        self.assertContains(response, "Kosten berechnet")
        self.assertNotContains(response, "every 2s")

    def test_chat_scroll_script_only_in_conversation(self):
        # Das Skript hält die Nachrichtenliste am Ende (Version 0.9)
        conversation = Conversation.objects.create(user=self.anna, title="Test")
        self.client.force_login(self.anna)
        response = self.client.get(reverse("assistant_conversation", args=[conversation.pk]))
        self.assertContains(response, "htmx:afterSwap")
        self.assertNotContains(self.client.get(reverse("assistant_home")), "htmx:afterSwap")
        # Die per HTMX nachgeladene Liste enthält das Skript nicht erneut
        self.assertNotContains(self.client.get(reverse("assistant_messages", args=[conversation.pk])), "<script")

    def test_second_message_while_running(self):
        conversation = Conversation.objects.create(user=self.anna, status="running")
        self.client.force_login(self.anna)
        with patch("assistant.views.queue_answer") as queue:
            response = self.client.post(reverse("assistant_conversation", args=[conversation.pk]),
                                        {"text": "Noch was"}, HTTP_HX_REQUEST="true")
        queue.assert_not_called()
        self.assertContains(response, "Bitte warte")

    def test_htmx_send(self):
        conversation = Conversation.objects.create(user=self.anna)
        self.client.force_login(self.anna)
        with patch("assistant.views.queue_answer") as queue:
            response = self.client.post(reverse("assistant_conversation", args=[conversation.pk]),
                                        {"text": "Hallo"}, HTTP_HX_REQUEST="true")
        queue.assert_called_once()
        self.assertContains(response, 'id="chat-messages"')
        self.assertContains(response, "Hallo")

    def test_foreign_conversation_404_and_delete(self):
        conversation = Conversation.objects.create(user=self.anna)
        admin = User.objects.create_superuser("chef", "chef@example.com", "x")
        self.client.force_login(admin)
        for name in ("assistant_conversation", "assistant_messages"):
            self.assertEqual(self.client.get(reverse(name, args=[conversation.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("assistant_delete", args=[conversation.pk])).status_code, 404)
        self.client.force_login(self.anna)
        self.assertRedirects(self.client.post(reverse("assistant_delete", args=[conversation.pk])), self.url)
        self.assertFalse(Conversation.objects.exists())

    def test_task_skips_finished_conversation(self):
        from .tasks import answer_conversation

        conversation = Conversation.objects.create(user=self.anna, status="idle")
        with patch("assistant.agent.answer") as answer:
            answer_conversation(conversation.pk)
        answer.assert_not_called()


@override_settings(**{**AI_SETTINGS, "AI_PROVIDER": "anthropic", "AI_MODEL": "claude-sonnet-5-5", "AI_BASE_URL": ""})
class AnthropicDetailsTest(TestCase):
    """Claude: Denkschritte, Denktiefe, Ablehnung, Fallback (0.8a)"""

    def response(self, content, stop_reason="end_turn", stop_details=None):
        return SimpleNamespace(content=content, stop_reason=stop_reason, stop_details=stop_details)

    def block(self, **data):
        return SimpleNamespace(**data, model_dump=lambda **kwargs: dict(data))

    def test_thinking_blocks_kept_fallback_and_effort(self):
        provider = AnthropicProvider()
        response = self.response([
            self.block(type="fallback"),
            self.block(type="thinking", thinking="", signature="sig"),
            self.block(type="text", text="Antwort"),
        ])
        with self.settings(AI_EFFORT="low"), \
                patch.object(provider.client.beta.messages, "create", return_value=response) as create:
            reply = provider.complete("System", [{"role": "user", "content": "Hi"}], tools.tool_definitions())
        self.assertEqual(reply.text, "Antwort")
        self.assertEqual([b["type"] for b in reply.raw], ["thinking", "text"])
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs["fallbacks"], kwargs["betas"]), ("default", ["server-side-fallback-2026-07-01"]))
        self.assertEqual(kwargs["output_config"], {"effort": "low"})
        self.assertEqual(kwargs["max_tokens"], 16000)
        self.assertNotIn("thinking", kwargs)

    def test_no_fallback_with_base_url_and_no_tools_round(self):
        with self.settings(AI_BASE_URL="https://proxy.example.com"):
            provider = AnthropicProvider()
        response = self.response([self.block(type="text", text="Ok")])
        with patch.object(provider.client.messages, "create", return_value=response) as create:
            provider.complete("System", [{"role": "user", "content": "Hi"}], tools.tool_definitions(), allow_tools=False)
        kwargs = create.call_args.kwargs
        self.assertNotIn("fallbacks", kwargs)
        self.assertNotIn("output_config", kwargs)
        self.assertEqual(kwargs["tool_choice"], {"type": "none"})

    def test_refusal_and_max_tokens(self):
        provider = AnthropicProvider()
        refusal = self.response([], "refusal", SimpleNamespace(category="cyber"))
        with patch.object(provider.client.beta.messages, "create", return_value=refusal):
            with self.assertRaises(RefusalError) as ctx:
                provider.complete("S", [{"role": "user", "content": "Hi"}], [])
        self.assertEqual(ctx.exception.category, "cyber")
        cut = self.response([self.block(type="tool_use", id="1", name="x", input={})], "max_tokens")
        with patch.object(provider.client.beta.messages, "create", return_value=cut):
            with self.assertRaises(ProviderError):
                provider.complete("S", [{"role": "user", "content": "Hi"}], [])

    def test_refusal_shown_in_chat(self):
        anna = User.objects.create_user("anna")
        anna.groups.add(Group.objects.get(name=perms.GROUP_AI))
        conversation = Conversation.objects.create(user=anna, status="error", error="refusal: cyber")
        self.client.force_login(anna)
        response = self.client.get(reverse("assistant_messages", args=[conversation.pk]))
        self.assertContains(response, "alert-warning")


# --- Version 0.8b: Dokumente im Chat und Vorschläge ---------------------------------

class MediaRootMixin:
    def setUp(self):
        import shutil
        import tempfile

        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

    def upload(self, name="vertrag.txt", content="Stadtwerke Musterstadt\nKundennummer 4711".encode()):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, content)


class ExtractionTest(TestCase):
    def test_validate_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from . import extraction

        for name in ("a.pdf", "b.JPG", "c.png", "d.webp", "e.txt", "f.eml"):
            self.assertIsNone(extraction.validate_upload(SimpleUploadedFile(name, b"x")), name)
        for name in ("a.docx", "b.html", "c.svg", "noext"):
            self.assertIsNotNone(extraction.validate_upload(SimpleUploadedFile(name, b"x")), name)

    def test_text_email_and_ocr(self):
        import os
        import tempfile
        from email.message import EmailMessage

        from PIL import Image, ImageDraw, ImageFont

        from . import extraction

        folder = tempfile.mkdtemp()
        txt = os.path.join(folder, "notiz.txt")
        with open(txt, "wb") as file:
            file.write("Grüße aus Köln".encode("latin-1"))
        self.assertEqual(extraction.extract(txt, "notiz.txt"), ("Grüße aus Köln", "text"))

        message = EmailMessage()
        message["Subject"], message["From"] = "Preisanpassung", "info@stadtwerke.example"
        message.set_content("Ab 1. März kostet Ihr Tarif 89,00 EUR.")
        eml = os.path.join(folder, "mail.eml")
        with open(eml, "wb") as file:
            file.write(bytes(message))
        text, method = extraction.extract(eml, "mail.eml")
        self.assertEqual(method, "email")
        self.assertIn("Subject: Preisanpassung", text)
        self.assertIn("89,00 EUR", text)

        image = Image.new("RGB", (1200, 220), "white")
        ImageDraw.Draw(image).text((40, 70), "Kundennummer 4711", fill="black", font=ImageFont.load_default(size=48))
        png = os.path.join(folder, "foto.png")
        image.save(png)
        text, method = extraction.extract(png, "foto.png")
        self.assertEqual(method, "ocr")
        self.assertIn("4711", text)

        broken = os.path.join(folder, "kaputt.pdf")
        with open(broken, "wb") as file:
            file.write(b"kein pdf")
        self.assertEqual(extraction.extract(broken, "kaputt.pdf"), ("", "none"))


@override_settings(**AI_SETTINGS)
class DocumentToolsTest(MediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.anna = User.objects.create_user("anna")
        self.anna.groups.add(Group.objects.get(name=perms.GROUP_USERS))
        self.anna = User.objects.get(pk=self.anna.pk)
        self.conversation = Conversation.objects.create(user=self.anna, toolset="documents")
        agent.add_user_message(self.conversation, "Bitte prüfen", self.upload())
        agent.read_pending_attachments(self.conversation)
        self.attachment = self.conversation.attachments.get()

    def call(self, tool, **arguments):
        return tools.run_tool(self.anna, tool, arguments, conversation=self.conversation, tool_call_id="call-1")

    def test_read_and_save_facts(self):
        self.assertEqual(self.attachment.method, "text")
        self.assertEqual(self.call("list_attachments")["attachments"][0]["file_name"], "vertrag.txt")
        result = self.call("read_attachment", attachment_id=self.attachment.pk)
        self.assertIn("Kundennummer 4711", result["text"])
        self.assertIsNone(result["next_offset"])
        self.call("save_document_facts", attachment_id=self.attachment.pk, facts={"customer_number": "4711"})
        self.call("save_document_facts", attachment_id=self.attachment.pk, facts={"provider": "Stadtwerke"})
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.facts, {"customer_number": "4711", "provider": "Stadtwerke"})
        self.assertIn("error", self.call("read_attachment", attachment_id=999))

    def test_user_text_marks_attachment(self):
        messages = agent.history(self.conversation)
        self.assertIn(f"[Attachment #{self.attachment.pk}: vertrag.txt]", messages[0]["content"])

    def test_proposal_created_but_nothing_saved(self):
        result = self.call("propose_create_provider", name="Stadtwerke Musterstadt", city="Musterstadt",
                          postal_code="12345", customer_number="4711")
        proposal = Proposal.objects.get()
        self.assertEqual(result["proposal_id"], proposal.pk)
        self.assertEqual((proposal.kind, proposal.status, proposal.tool_call_id),
                         ("create_provider", "pending", "call-1"))
        self.assertIn(["Kundennummer", "4711"], proposal.summary)
        self.assertFalse(Provider.objects.exists())

    def test_invalid_proposal_returns_error(self):
        self.assertIn("error", self.call("propose_create_provider", name=""))
        self.assertIn("error", self.call("propose_create_contract", name="X", provider_id=999, category_id=1,
                                        consumer_id=1, start_date="2026-01-01"))
        self.assertFalse(Proposal.objects.exists())

    def test_read_toolset_cannot_propose(self):
        conversation = Conversation.objects.create(user=self.anna, toolset="read")
        result = tools.run_tool(self.anna, "propose_create_provider", {"name": "X"}, conversation=conversation)
        self.assertEqual(result, {"error": "Unknown tool propose_create_provider."})
        names = {tool["name"] for tool in tools.tool_definitions("documents")}
        self.assertFalse(any("delete" in name for name in names))

    def test_unreadable_file_sent_to_claude(self):
        from .providers import AnthropicProvider

        self.attachment.text, self.attachment.method = "", "none"
        self.attachment.original_name = "scan.pdf"
        self.attachment.save()
        result = self.call("read_attachment", attachment_id=self.attachment.pk)
        Message.objects.create(conversation=self.conversation, role="assistant",
                               tool_calls=[{"id": "r1", "name": "read_attachment", "arguments": {}}])
        Message.objects.create(conversation=self.conversation, role="tool", tool_call_id="r1",
                               tool_name="read_attachment", content=json.dumps(result))
        entry = agent.history(self.conversation)[-1]
        self.assertNotIn("_attach_file", entry["content"])
        self.assertEqual(entry["file"]["media_type"], "application/pdf")
        content = AnthropicProvider.tool_content(entry)
        self.assertEqual([block["type"] for block in content], ["text", "document"])
        # OpenAI bekommt nur den Text
        self.assertEqual(OpenAIProvider.convert_messages("S", [entry])[1]["content"], entry["content"])
        # Fehlt die Datei, geht es ohne sie weiter (kein FileNotFoundError)
        self.attachment.file.storage.delete(self.attachment.file.name)
        entry = agent.history(self.conversation)[-1]
        self.assertNotIn("file", entry)
        self.assertEqual(AnthropicProvider.tool_content(entry), entry["content"])


@override_settings(**AI_SETTINGS)
class ProposalTest(MediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.anna = User.objects.create_user("anna")
        self.anna.groups.add(Group.objects.get(name=perms.GROUP_USERS))
        self.anna = User.objects.get(pk=self.anna.pk)
        self.ben = User.objects.create_user("ben")
        self.conversation = Conversation.objects.create(user=self.anna, toolset="documents")
        agent.add_user_message(self.conversation, "Vertrag", self.upload("Vertrag Strom.txt"))
        self.attachment = self.conversation.attachments.get()
        self.category = Category.objects.create(owner=self.anna, name="Energie")
        self.consumer = Consumer.objects.create(owner=self.anna, name="Haushalt")

    def propose(self, kind, **data):
        from . import proposals

        return proposals.create(self.conversation, kind, data)

    def test_create_provider_then_contract_with_document_and_cost(self):
        from . import proposals

        provider_proposal = proposals.approve(self.propose("create_provider", name="Stadtwerke", city="Köln"))
        self.assertEqual(provider_proposal.status, "approved")
        provider = Provider.objects.get(name="Stadtwerke")
        self.assertEqual((provider.owner, provider.city), (self.anna, "Köln"))
        self.assertEqual(provider_proposal.result["url"], reverse("provider_detail", args=[provider.pk]))

        proposal = self.propose(
            "create_contract", name="Strom Basis", provider_id=provider.pk, category_id=self.category.pk,
            consumer_id=self.consumer.pk, start_date="2026-02-01", contract_number="99-123",
            term_value=12, term_unit="months", amount="84.50", payment_interval="monthly",
            attachment_id=self.attachment.pk,
        )
        self.assertIn(["Dokument", "Vertrag Strom.txt"], proposal.summary)
        with self.captureOnCommitCallbacks(execute=True):
            proposals.approve(proposal)
        self.assertEqual(proposal.status, "approved", proposal.error)
        contract = Contract.objects.get(name="Strom Basis")
        self.assertEqual((contract.owner, contract.contract_number, contract.term_value), (self.anna, "99-123", 12))
        document = contract.documents.get()
        self.assertTrue(document.file.name.startswith(f"documents/{contract.reference[:4]}/{contract.reference}/"))
        cost = contract.costs.get()
        self.assertEqual((cost.amount, cost.document), (Decimal("84.50"), document))
        self.attachment.refresh_from_db()
        self.assertEqual(self.attachment.document, document)

    def test_contract_without_consumer_and_start_date(self):
        """Verbraucher und Vertragsbeginn sind optional (0.9 beta)"""
        from . import proposals, tools

        provider = Provider.objects.create(owner=self.anna, name="Stadtwerke")
        proposal = self.propose(
            "create_contract", name="Gas", provider_id=provider.pk, category_id=self.category.pk, amount="30",
        )
        with self.captureOnCommitCallbacks(execute=True):
            proposals.approve(proposal)
        self.assertEqual(proposal.status, "approved", proposal.error)
        contract = Contract.objects.get(name="Gas")
        self.assertEqual((contract.consumer, contract.start_date), (None, None))
        # Ohne Vertragsbeginn gilt der erste Betrag ab heute
        self.assertEqual(contract.costs.get().valid_from, timezone.localdate())
        # Die lesenden Werkzeuge kommen mit den leeren Feldern zurecht
        self.assertIsNone(tools.search_contracts(self.anna, "Gas")["contracts"][0]["consumer"])
        self.assertIsNone(tools.get_contract(self.anna, contract_id=contract.pk)["start_date"])
        groups = tools.cost_overview(self.anna, group_by="consumer")["groups"]
        self.assertEqual([group["name"] for group in groups], ["–"])

    def test_attach_needs_edit_rights(self):
        from . import proposals

        contract = make_contract(self.ben, "Fremd", "10.00")
        with self.assertRaises(proposals.ProposalError):
            self.propose("attach_document", contract_id=contract.pk, attachment_id=self.attachment.pk)
        perms.grant_role(contract, self.anna, perms.READER)
        with self.assertRaises(proposals.ProposalError):
            self.propose("attach_document", contract_id=contract.pk, attachment_id=self.attachment.pk)
        perms.grant_role(contract, self.anna, perms.EDITOR)
        proposal = proposals.approve(self.propose("attach_document", contract_id=contract.pk,
                                                  attachment_id=self.attachment.pk, name="Police"))
        self.assertEqual(proposal.status, "approved")
        self.assertEqual(contract.documents.get().name, "Police")

    def test_checked_again_on_approval(self):
        from . import proposals

        proposal = self.propose("create_provider", name="Doppelt")
        Provider.objects.create(owner=self.anna, name="Doppelt")
        proposals.approve(proposal)
        self.assertEqual(proposal.status, "failed")
        self.assertTrue(proposal.error)
        self.assertEqual(Provider.objects.filter(name="Doppelt").count(), 1)

    def test_reject_and_outcome_texts(self):
        from . import proposals

        proposal = proposals.reject(self.propose("create_consumer", name="Auto"))
        self.assertEqual(proposal.status, "rejected")
        self.assertFalse(Consumer.objects.filter(name="Auto").exists())
        self.assertIn("rejected", proposals.outcome_text(proposal))

    def test_needs_add_permission(self):
        from . import proposals

        self.anna.groups.clear()
        self.anna = User.objects.get(pk=self.anna.pk)
        self.conversation.user = self.anna
        with self.assertRaises(proposals.ProposalError):
            self.propose("create_provider", name="X")


@override_settings(**AI_SETTINGS)
class DocumentPagesTest(MediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.anna = User.objects.create_user("anna")
        self.anna.groups.add(Group.objects.get(name=perms.GROUP_AI), Group.objects.get(name=perms.GROUP_USERS))
        self.client.force_login(self.anna)

    def test_upload_starts_conversation(self):
        with patch("assistant.views.queue_answer") as queue:
            response = self.client.post(reverse("assistant_home"), {"text": "", "file": self.upload()})
        conversation = Conversation.objects.get()
        self.assertRedirects(response, reverse("assistant_conversation", args=[conversation.pk]))
        queue.assert_called_once()
        self.assertEqual(conversation.toolset, "documents")
        attachment = conversation.attachments.get()
        self.assertEqual((attachment.original_name, attachment.method), ("vertrag.txt", "pending"))
        self.assertTrue(attachment.file.name.startswith(f"assistant/{conversation.pk}/"))
        self.assertEqual(conversation.messages.get().content, "Bitte prüfe dieses Dokument.")
        self.assertContains(self.client.get(reverse("assistant_conversation", args=[conversation.pk])), "vertrag.txt")

    def test_invalid_upload_and_empty_message(self):
        response = self.client.post(reverse("assistant_home"), {"file": self.upload("seite.html")})
        self.assertContains(response, "Nur PDF")
        response = self.client.post(reverse("assistant_home"), {"text": ""})
        self.assertContains(response, "Bitte gib eine Nachricht ein")
        self.assertFalse(Conversation.objects.exists())

    def test_old_conversation_has_no_upload(self):
        conversation = Conversation.objects.create(user=self.anna, toolset="read")
        page = self.client.get(reverse("assistant_conversation", args=[conversation.pk]))
        self.assertNotContains(page, 'type="file"')
        self.assertContains(self.client.get(reverse("assistant_home")), 'type="file"')

    def test_approve_and_reject_views(self):
        from . import proposals

        conversation = Conversation.objects.create(user=self.anna, toolset="documents")
        proposal = proposals.create(conversation, "create_consumer", {"name": "Auto", "number": "K-AB 1"},
                                    tool_call_id="c1")
        Message.objects.create(conversation=conversation, role="assistant",
                               tool_calls=[{"id": "c1", "name": "propose_create_consumer", "arguments": {}}])
        Message.objects.create(conversation=conversation, role="tool", tool_call_id="c1", content="{}")
        Message.objects.create(conversation=conversation, role="assistant", content="Bitte freigeben.")
        page = self.client.get(reverse("assistant_messages", args=[conversation.pk]))
        self.assertContains(page, "Verbraucher anlegen")
        self.assertContains(page, "K-AB 1")
        self.assertContains(page, reverse("assistant_proposal", args=[proposal.pk, "approve"]))

        other = User.objects.create_superuser("chef", "chef@example.com", "x")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("assistant_proposal", args=[proposal.pk, "approve"])).status_code, 404)

        self.client.force_login(self.anna)
        with patch("assistant.views.queue_answer") as queue:
            response = self.client.post(reverse("assistant_proposal", args=[proposal.pk, "approve"]),
                                        HTTP_HX_REQUEST="true")
        queue.assert_called_once()
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "approved")
        self.assertTrue(Consumer.objects.filter(owner=self.anna, name="Auto").exists())
        conversation.refresh_from_db()
        self.assertTrue(conversation.is_running)
        self.assertEqual(conversation.messages.last().role, "event")
        self.assertNotIn("[Approval]", response.content.decode())

        # Zweite Entscheidung wird abgewiesen
        conversation.status = "idle"
        conversation.save()
        with patch("assistant.views.queue_answer") as queue:
            response = self.client.post(reverse("assistant_proposal", args=[proposal.pk, "reject"]),
                                        HTTP_HX_REQUEST="true")
        queue.assert_not_called()
        self.assertContains(response, "bereits entschieden")
        self.assertEqual(self.client.post(reverse("assistant_proposal", args=[proposal.pk, "delete"])).status_code, 404)

    def test_conversation_delete_removes_attachment_file(self):
        import os

        with patch("assistant.views.queue_answer"):
            self.client.post(reverse("assistant_home"), {"text": "x", "file": self.upload()})
        conversation = Conversation.objects.get()
        path = conversation.attachments.get().file.path
        self.assertTrue(os.path.exists(path))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("assistant_delete", args=[conversation.pk]))
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(os.path.dirname(path)))
