"""
Tests für Version 0.9.2: Antworten mit Denkschritten über die OpenAI-
Schnittstelle (Mistral), ähnliche Stammdaten, kurze Fehlermeldung von
Paperless.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from contracts import paperless
from contracts import permissions as perms
from contracts.models import Provider
from contracts.testing import env_settings, site_settings
from web.tests_paperless import URL, FakeResponse

from . import agent, tools
from .providers import OpenAIProvider, message_text
from .tests import AI_SETTINGS

MISTRAL_REPLY = [
    {"type": "thinking", "thinking": [{"type": "text", "text": "The user sent test."}], "closed": True},
    {"type": "text", "text": "Hallo! "},
    {"type": "text", "text": "Ich bin da."},
]


class MessageTextTest(TestCase):
    def test_text_blocks_without_thinking(self):
        self.assertEqual(message_text(MISTRAL_REPLY), "Hallo! Ich bin da.")

    def test_objects_strings_and_empty(self):
        blocks = [SimpleNamespace(type="thinking", thinking=[]), SimpleNamespace(type="text", text="Ja")]
        self.assertEqual(message_text(blocks), "Ja")
        self.assertEqual(message_text("Nur Text"), "Nur Text")
        self.assertEqual(message_text(None), "")
        self.assertEqual(message_text([]), "")

    @env_settings(**{**AI_SETTINGS, "AI_PROVIDER": "mistral"})
    def test_reply_with_thinking_and_tool_call(self):
        provider = OpenAIProvider()
        call = SimpleNamespace(id="c1", function=SimpleNamespace(name="search_contracts", arguments="{}"))
        message = SimpleNamespace(content=MISTRAL_REPLY[:2], tool_calls=[call])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        with patch.object(provider.client.chat.completions, "create", return_value=response):
            reply = provider.complete("System", [{"role": "user", "content": "test"}], tools.tool_definitions())
        self.assertEqual(reply.text, "Hallo! ")
        self.assertEqual(reply.tool_calls, [{"id": "c1", "name": "search_contracts", "arguments": {}}])


class SimilarMasterDataTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna")
        cls.ben = User.objects.create_user("ben")
        for name in ("DEVK Allgemeine Versicherungs-AG", "DEVK Versicherungen", "HUK-COBURG", "Vodafone GmbH"):
            Provider.objects.create(owner=cls.anna, name=name)
        Provider.objects.create(owner=cls.ben, name="DEVK Lebensversicherung")

    def names(self, user, query):
        return [item["name"] for item in tools.search_master_data(user, "provider", query)["results"]]

    def test_words_find_similar_names_best_first(self):
        self.assertEqual(self.names(self.anna, "DEVK Versicherungen"),
                         ["DEVK Versicherungen", "DEVK Allgemeine Versicherungs-AG"])
        self.assertEqual(self.names(self.anna, "DEVK Allgemeine Versicherungs-AG, Köln")[0],
                         "DEVK Allgemeine Versicherungs-AG")

    def test_single_word_and_filler_words(self):
        self.assertEqual(self.names(self.anna, "devk"), ["DEVK Allgemeine Versicherungs-AG", "DEVK Versicherungen"])
        # "GmbH" allein findet nicht jede GmbH
        self.assertEqual(self.names(self.anna, "Telekom GmbH"), [])
        self.assertEqual(len(self.names(self.anna, "")), 4)

    def test_only_visible_entries(self):
        self.assertEqual(self.names(self.ben, "DEVK Versicherungen"), ["DEVK Lebensversicherung"])
        devk = Provider.objects.get(name="DEVK Lebensversicherung")
        perms.grant_role(devk, self.anna, perms.READER)
        self.assertIn("DEVK Lebensversicherung", self.names(self.anna, "DEVK Versicherungen"))

    def test_rule_in_fixed_part_of_the_prompt(self):
        with site_settings(ai_system_prompt="Eigener Prompt"):
            for toolset in ("read", "documents"):
                with self.subTest(toolset=toolset):
                    prompt = agent.system_prompt(self.anna, toolset)
                    self.assertTrue(prompt.startswith("Eigener Prompt"))
                    self.assertIn("probably the same", prompt)
                    self.assertIn("then delete the other", prompt)
        self.assertIn("look for similar existing entries", agent.system_prompt(self.anna, "documents"))


TRACEBACK = (
    'File "/usr/local/lib/python3.14/site-packages/celery/app/trace.py", line 585, in trace_task R = retval = '
    'fun(*args, **kwargs) ~~~^^^^^ File "/usr/src/paperless/src/documents/consumer.py", line 240, in _fail '
    'raise ConsumerError(f"{self.filename}: {log_message or message}") from exception ConsumerError '
    "Abo.eml: Error occurred while consuming document Abo.eml: Could not parse content with tika server at "
    "http://tika:9998:"
)


class ShortPaperlessErrorTest(SimpleTestCase):
    def test_one_line_traceback(self):
        self.assertEqual(paperless.short_error(TRACEBACK),
                         "Abo.eml: Error occurred while consuming document Abo.eml: Could not parse content "
                         "with tika server at http://tika:9998:")

    def test_multi_line_traceback(self):
        message = ('Traceback (most recent call last):\n  File "/x.py", line 3, in run\n    parse()\n'
                   "documents.parsers.ParseError: Could not parse content\n")
        self.assertEqual(paperless.short_error(message), "Could not parse content")

    def test_plain_messages_stay(self):
        for message in ("kaputt", "police.pdf: Not consuming police.pdf: It is a duplicate of Police (#17).", ""):
            self.assertEqual(paperless.short_error(message), message)

    def test_task_state_shortens_and_logs(self):
        cfg = paperless.PaperlessConfig(mode="api", base_url=URL, token="geheim")
        with patch("urllib.request.urlopen", return_value=FakeResponse([{"status": "FAILURE", "result": TRACEBACK}])), \
                self.assertLogs("contracts.paperless", "WARNING") as logs:
            state = paperless.task_state(cfg, "abc")
        self.assertTrue(state.error.startswith("Abo.eml: Error occurred"))
        self.assertNotIn("File ", state.error)
        self.assertIn("trace.py", logs.output[0])
