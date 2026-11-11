"""
Ablauf einer Antwort des KI-Assistenten (Version 0.8a, Dokumente 0.8b).

``answer(conversation)`` läuft im Worker (assistant.tasks): Verlauf an das
Modell, angeforderte Werkzeuge mit den Rechten des Benutzers ausführen,
Ergebnisse zurückgeben, bis eine Antwort ohne Werkzeugaufruf kommt oder
AI_MAX_TOOL_ROUNDS erreicht ist. Jeder Schritt wird gespeichert.
"""

import json
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone, translation

from contracts.documents import extension
from contracts.mail import user_language

from . import extraction, tools
from .models import Attachment, Conversation, Message
from .providers import ProviderError, RefusalError, get_provider

logger = logging.getLogger(__name__)

# Länge des automatisch vergebenen Titels einer Unterhaltung
TITLE_LENGTH = 60

LANGUAGE_NAMES = {"de": "German", "en": "English"}

SYSTEM_PROMPT = """You are the assistant of “Contracts Manager”, a self-hosted app for private contracts, \
subscriptions and insurance policies.

Rules:
- Answer in {language}. Amounts are in euros. Each user message starts with its date in brackets, \
e.g. [2026-01-31]; use it as “today”.
- Use the tools to look up contracts, costs and master data. Never invent contracts, amounts or dates; \
if the tools return nothing, say so.
{changes}
- You only see contracts the user is allowed to view.
- Mention the contract reference (e.g. 20260001) when you refer to a contract.
- Keep answers short and clear. Write plain text without Markdown tables or headings; simple lists with \
“- ” are fine."""


READ_ONLY_RULE = """- You can only read data. You cannot create, change or delete anything, and you never delete data. \
If the user asks for a change, explain that this is not possible yet and how to do it in the app."""

DOCUMENTS_RULES = """- You never change anything yourself and you can never delete anything. You can only create \
proposals (propose_* tools). Each proposal is shown to the user as a card with all values and is carried out \
only when the user approves it. Never claim that something was saved before an [Approval] message confirms it.
- When the user uploads a file (shown as [Attachment #id: name]): read it with read_attachment, store the \
relevant facts with save_document_facts, then check with search_contracts and search_master_data whether a \
matching contract or contract partner already exists, and tell the user what you found.
- If a matching contract exists, ask whether the document should be attached and propose it \
(propose_attach_document).
- If no matching contract exists, ask whether a new one should be created. Propose missing contract \
partners, categories or consumers first, one proposal at a time, and wait for the approval before proposing \
the contract (with the first cost entry and the document). Before proposing, list exactly what will be stored.
- If data such as the partner's address or logo is missing from the document, say so. You cannot search the \
internet; the user can add it later."""


def system_prompt(user, toolset="read"):
    language = LANGUAGE_NAMES.get(user_language(user), "German")
    changes = DOCUMENTS_RULES if toolset == "documents" else READ_ONLY_RULE
    return SYSTEM_PROMPT.format(language=language, changes=changes)


def conversation_system_prompt(conversation):
    """
    System-Prompt der Unterhaltung: beim ersten Mal festgelegt, danach
    unverändert (Claude lehnt einen geänderten Verlauf sonst ab). Das Datum
    steht deshalb an jeder Benutzer-Nachricht statt im System-Prompt.
    """
    if not conversation.system_prompt:
        conversation.system_prompt = system_prompt(conversation.user, conversation.toolset)
        conversation.save(update_fields=["system_prompt"])
    return conversation.system_prompt


def user_text(message, attachments=()):
    """
    Benutzer-Nachricht mit Datum und Anhängen, abgeleitet aus gespeicherten
    Werten und damit bei jeder Anfrage gleich.
    """
    text = f"[{timezone.localdate(message.created_at).isoformat()}] {message.content}"
    for attachment in attachments:
        text += f"\n[Attachment #{attachment.pk}: {attachment.original_name}]"
    return text


# Datei direkt an das Modell (nur Claude, nur wenn lokal nichts erkannt wurde)
DIRECT_FILE_TYPES = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "png": "image/png", "webp": "image/webp"}
MAX_DIRECT_FILE_SIZE = 10 * 1024 * 1024


def tool_entry(message, attachments):
    """
    Werkzeug-Ergebnis für den Verlauf. Verweist es auf eine Datei, die
    lokal nicht lesbar war, wird sie (falls möglich) mitgegeben.
    """
    entry = {"role": "tool", "tool_call_id": message.tool_call_id, "name": message.tool_name,
             "content": message.content}
    if '"_attach_file"' not in message.content:
        return entry
    try:
        data = json.loads(message.content)
    except ValueError:
        return entry
    attachment = attachments.get(data.pop("_attach_file", None))
    entry["content"] = json.dumps(data, ensure_ascii=False)
    # Fehlt die Datei (z. B. Worker ohne media-Ordner), ohne Datei weiter
    if attachment and attachment.size <= MAX_DIRECT_FILE_SIZE and attachment.file.storage.exists(attachment.file.name):
        media_type = DIRECT_FILE_TYPES.get(extension(attachment.original_name))
        if media_type:
            entry["file"] = {"path": attachment.file.path, "media_type": media_type}
    return entry


def history(conversation):
    """
    Verlauf im anbieterneutralen Format (assistant.providers).
    """
    stored = list(conversation.messages.all())
    attachments = {attachment.pk: attachment for attachment in conversation.attachments.all()}
    by_message = {}
    for attachment in attachments.values():
        by_message.setdefault(attachment.message_id, []).append(attachment)
    # Werkzeugaufrufe ohne Ergebnis (z. B. nach einem Abbruch) weglassen,
    # sonst lehnt der Anbieter den Verlauf ab
    answered = {message.tool_call_id for message in stored if message.role == Message.Role.TOOL}
    messages = []
    for message in stored:
        if message.role == Message.Role.TOOL:
            messages.append(tool_entry(message, attachments))
        elif message.role == Message.Role.ASSISTANT:
            calls = [call for call in message.tool_calls if call.get("id") in answered]
            if message.content or calls:
                entry = {"role": "assistant", "content": message.content, "tool_calls": calls}
                # Originalformat nur, wenn alle Werkzeugaufrufe ein Ergebnis haben
                if message.provider_content and len(calls) == len(message.tool_calls):
                    entry.update(provider=message.provider, raw=message.provider_content)
                messages.append(entry)
        elif message.role == Message.Role.EVENT:
            messages.append({"role": "user", "content": message.content})
        else:
            messages.append({"role": "user", "content": user_text(message, by_message.get(message.pk, ()))})
    return messages


def read_pending_attachments(conversation):
    """
    Text neuer Anhänge lokal erkennen (vor der Antwort, im Worker).
    """
    for attachment in conversation.attachments.filter(method=Attachment.Method.PENDING):
        attachment.text, attachment.method = extraction.extract(attachment.file.path, attachment.original_name)
        attachment.save(update_fields=["text", "method"])


def answer(conversation):
    """
    Erzeugt die Antwort auf die letzte Benutzer-Nachricht. Fehler des
    Anbieters werden an der Unterhaltung gespeichert (Status "error").
    """
    with translation.override(user_language(conversation.user)):
        _answer(conversation)


def _answer(conversation):
    user = conversation.user
    try:
        read_pending_attachments(conversation)
        provider = get_provider()
        definitions = tools.tool_definitions(conversation.toolset)
        system = conversation_system_prompt(conversation)
        for _round in range(settings.AI_MAX_TOOL_ROUNDS):
            reply = provider.complete(system, history(conversation), definitions)
            Message.objects.create(
                conversation=conversation, role=Message.Role.ASSISTANT,
                content=reply.text, tool_calls=reply.tool_calls,
                provider=reply.provider, provider_content=reply.raw,
            )
            if not reply.tool_calls:
                break
            for call in reply.tool_calls:
                result = tools.run_tool(user, call["name"], call["arguments"],
                                        conversation=conversation, tool_call_id=call["id"])
                Message.objects.create(
                    conversation=conversation, role=Message.Role.TOOL, tool_call_id=call["id"],
                    tool_name=call["name"], content=json.dumps(result, ensure_ascii=False, default=str),
                )
        else:
            # Zu viele Werkzeugaufrufe: mit Antwort abschließen. Die Werkzeuge
            # bleiben im Aufruf (gleicher Verlauf), dürfen aber nicht genutzt werden.
            reply = provider.complete(system, history(conversation), definitions, allow_tools=False)
            Message.objects.create(conversation=conversation, role=Message.Role.ASSISTANT, content=reply.text,
                                   provider=reply.provider, provider_content=reply.raw)
    except RefusalError as error:
        logger.info("KI-Anbieter hat abgelehnt: %s", error)
        finish(conversation, Conversation.Status.ERROR, str(error))
        return
    except ProviderError as error:
        logger.warning("KI-Anbieter: %s", error)
        finish(conversation, Conversation.Status.ERROR, str(error))
        return
    except Exception as error:  # Unerwartete Fehler nicht im Worker verlieren
        logger.exception("KI-Assistent fehlgeschlagen")
        finish(conversation, Conversation.Status.ERROR, type(error).__name__)
        return
    finish(conversation, Conversation.Status.IDLE, "")


def finish(conversation, status, error):
    with transaction.atomic():
        conversation.status = status
        conversation.error = error
        conversation.save(update_fields=["status", "error", "updated_at"])


def add_user_message(conversation, text, upload=None):
    """
    Speichert die Eingabe (optional mit Datei), setzt bei der ersten
    Nachricht den Titel und markiert die Unterhaltung als "antwortet".
    Der Text der Datei wird erst im Worker erkannt.
    """
    message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=text)
    if upload is not None:
        Attachment.objects.create(
            conversation=conversation, message=message, file=upload,
            original_name=upload.name[:255], size=upload.size,
        )
    if not conversation.title:
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        conversation.title = (first_line or (upload.name if upload else ""))[:TITLE_LENGTH]
    conversation.status = Conversation.Status.RUNNING
    conversation.error = ""
    conversation.save()
    return message


def add_event(conversation, text):
    """
    Ereignis (z. B. Freigabe) als Nachricht an das Modell; danach antwortet
    der Assistent erneut.
    """
    Message.objects.create(conversation=conversation, role=Message.Role.EVENT, content=text)
    conversation.status = Conversation.Status.RUNNING
    conversation.error = ""
    conversation.save()
