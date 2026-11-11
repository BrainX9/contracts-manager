"""
Anbindung der Sprachmodelle (Version 0.8a).

Eine gemeinsame Schnittstelle für zwei Arten von Anbietern:

- ``OpenAIProvider``: OpenAI und alle OpenAI-kompatiblen Anbieter (IONOS AI
  Model Hub, Mistral, lokales Ollama) über ``AI_BASE_URL``
- ``AnthropicProvider``: Claude

Nachrichten kommen anbieterneutral herein::

    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}

Werkzeuge werden als ``{"name", "description", "parameters"}`` (JSON-Schema)
übergeben. Zugangsdaten kommen nur aus den Settings (.env).
"""

import json
from dataclasses import dataclass, field

from django.conf import settings


class ProviderError(Exception):
    """
    Fehler beim Anbieter (Verbindung, Zugangsdaten, Kontingent). Die
    Meldung wird dem Benutzer angezeigt und enthält keine Zugangsdaten.
    """


class RefusalError(ProviderError):
    """
    Das Modell hat die Anfrage aus Sicherheitsgründen abgelehnt
    (stop_reason "refusal"); ``category`` nennt den Grund, falls bekannt.
    """

    def __init__(self, category=None):
        self.category = category
        super().__init__(f"refusal: {category}" if category else "refusal")


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    # Antwort im Originalformat des Anbieters, unverändert zurückzugeben
    provider: str = ""
    raw: list = field(default_factory=list)


def _parse_arguments(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class OpenAIProvider:
    def __init__(self):
        from openai import OpenAI

        self.client = OpenAI(
            api_key=settings.AI_API_KEY or "none",
            base_url=settings.AI_BASE_URL or None,
            timeout=settings.AI_TIMEOUT,
            max_retries=2,
        )
        self.model = settings.AI_MODEL

    @staticmethod
    def convert_messages(system, messages):
        result = [{"role": "system", "content": system}]
        for message in messages:
            if message["role"] == "assistant":
                entry = {"role": "assistant", "content": message.get("content") or None}
                if message.get("tool_calls"):
                    entry["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                        }
                        for call in message["tool_calls"]
                    ]
                result.append(entry)
            elif message["role"] == "tool":
                result.append({"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]})
            else:
                result.append({"role": "user", "content": message["content"]})
        return result

    @staticmethod
    def convert_tools(tools):
        return [{"type": "function", "function": tool} for tool in tools]

    def complete(self, system, messages, tools, allow_tools=True):
        import openai

        kwargs = {"model": self.model, "messages": self.convert_messages(system, messages)}
        if tools:
            kwargs["tools"] = self.convert_tools(tools)
            if not allow_tools:
                kwargs["tool_choice"] = "none"
        try:
            response = self.client.chat.completions.create(**kwargs)
        except openai.APIError as error:
            raise ProviderError(_error_text(error)) from error
        message = response.choices[0].message
        calls = [
            {"id": call.id, "name": call.function.name, "arguments": _parse_arguments(call.function.arguments)}
            for call in (message.tool_calls or [])
            if getattr(call, "function", None)
        ]
        return Reply(text=message.content or "", tool_calls=calls)


class AnthropicProvider:
    """
    Claude über die Messages API. Aktuelle Modelle (z. B. claude-sonnet-5-5)
    denken immer mit; ihre Denkschritte (``thinking``-Blöcke) werden
    gespeichert und unverändert zurückgegeben, sonst fehlt dem Modell der
    Gedankengang zwischen den Werkzeugaufrufen. Direkt bei Anthropic (ohne
    AI_BASE_URL) springt bei einer Ablehnung serverseitig ein anderes Modell
    ein (``fallbacks: "default"``).
    """
    NAME = "anthropic"
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self):
        from anthropic import Anthropic

        self.client = Anthropic(
            api_key=settings.AI_API_KEY,
            base_url=settings.AI_BASE_URL or None,
            timeout=settings.AI_TIMEOUT,
            max_retries=2,
        )
        self.model = settings.AI_MODEL
        self.use_fallback = not settings.AI_BASE_URL

    @classmethod
    def convert_messages(cls, messages):
        """
        Claude erwartet Werkzeug-Ergebnisse als Benutzer-Nachricht; mehrere
        Ergebnisse hintereinander kommen in dieselbe Nachricht. Eigene
        Antworten gehen im Originalformat zurück (mit Denkschritten).
        """
        result = []
        for message in messages:
            if message["role"] == "assistant" and message.get("provider") == cls.NAME and message.get("raw"):
                result.append({"role": "assistant", "content": message["raw"]})
            elif message["role"] == "assistant":
                blocks = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                                   "input": call["arguments"]})
                result.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif message["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": message["tool_call_id"],
                         "content": cls.tool_content(message)}
                previous = result[-1] if result else None
                if previous and previous["role"] == "user" and isinstance(previous["content"], list) \
                        and previous["content"] and previous["content"][0].get("type") == "tool_result":
                    previous["content"].append(block)
                else:
                    result.append({"role": "user", "content": [block]})
            else:
                result.append({"role": "user", "content": message["content"]})
        return result

    @staticmethod
    def tool_content(message):
        """
        Inhalt eines Werkzeug-Ergebnisses; eine lokal nicht lesbare Datei
        (PDF oder Bild) wird als Dokument bzw. Bild mitgegeben.
        """
        file = message.get("file")
        if not file:
            return message["content"]
        import base64

        with open(file["path"], "rb") as handle:
            data = base64.standard_b64encode(handle.read()).decode("ascii")
        kind = "document" if file["media_type"] == "application/pdf" else "image"
        return [
            {"type": "text", "text": message["content"]},
            {"type": kind, "source": {"type": "base64", "media_type": file["media_type"], "data": data}},
        ]

    @staticmethod
    def convert_tools(tools):
        return [
            {"name": tool["name"], "description": tool["description"], "input_schema": tool["parameters"]}
            for tool in tools
        ]

    def complete(self, system, messages, tools, allow_tools=True):
        import anthropic

        kwargs = {
            "model": self.model,
            "max_tokens": settings.AI_MAX_TOKENS,
            "system": system,
            "messages": self.convert_messages(messages),
        }
        if settings.AI_EFFORT:
            kwargs["output_config"] = {"effort": settings.AI_EFFORT}
        if tools:
            kwargs["tools"] = self.convert_tools(tools)
            if not allow_tools:
                kwargs["tool_choice"] = {"type": "none"}
        try:
            if self.use_fallback:
                response = self.client.beta.messages.create(
                    **kwargs, betas=[self.FALLBACK_BETA], fallbacks="default",
                )
            else:
                response = self.client.messages.create(**kwargs)
        except anthropic.APIError as error:
            raise ProviderError(_error_text(error)) from error
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise RefusalError(getattr(details, "category", None))
        texts, calls, raw = [], [], []
        for block in response.content:
            if block.type == "fallback":
                # Nur Hinweis auf den Modellwechsel, nicht zurückgeben
                continue
            raw.append(block.model_dump(mode="json", exclude_none=True))
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append({"id": block.id, "name": block.name, "arguments": _parse_arguments(block.input)})
        if response.stop_reason == "max_tokens":
            # Abgeschnitten: unvollständige Werkzeugaufrufe nicht ausführen
            raise ProviderError("The answer was cut off (AI_MAX_TOKENS reached).")
        return Reply(
            text="\n\n".join(text for text in texts if text), tool_calls=calls, provider=self.NAME, raw=raw,
        )


PROVIDERS = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}


def get_provider():
    """
    Anbieter laut AI_PROVIDER. Fehlt das Modell, gibt es einen ProviderError.
    """
    provider_class = PROVIDERS.get(settings.AI_PROVIDER)
    if provider_class is None:
        raise ProviderError(f"Unknown AI_PROVIDER “{settings.AI_PROVIDER}”.")
    if not settings.AI_MODEL:
        raise ProviderError("AI_MODEL is not set.")
    return provider_class()


def _error_text(error):
    """
    Kurze Fehlermeldung ohne Zugangsdaten (Status und Text des Anbieters).
    """
    status = getattr(error, "status_code", None)
    message = getattr(error, "message", None) or str(error)
    message = message.replace(settings.AI_API_KEY, "***") if settings.AI_API_KEY else message
    return f"{status}: {message}" if status else message
