"""
KI-Assistent (Version 0.8a): Unterhaltungen und Nachrichten.

Nachrichten werden anbieterneutral gespeichert (Rolle, Text, Werkzeug-
aufrufe), damit derselbe Verlauf mit OpenAI-kompatiblen Anbietern und mit
Claude funktioniert (assistant.providers). Alles bleibt in der eigenen
Datenbank; an den Anbieter geht nur, was für eine Antwort nötig ist.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Conversation(models.Model):
    """
    Unterhaltung eines Benutzers mit dem Assistenten. Nur der Benutzer
    selbst sieht seine Unterhaltungen.
    """

    class Status(models.TextChoices):
        IDLE = "idle", _("ready")
        RUNNING = "running", _("answering")
        ERROR = "error", _("error")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_conversations",
        verbose_name=_("user"),
    )
    title = models.CharField(_("title"), max_length=200, blank=True)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.IDLE)
    error = models.TextField(_("error"), blank=True)
    # Beim Start festgelegt und danach unverändert: Claude bindet seine
    # Denkschritte an den bisherigen Verlauf samt System-Prompt
    system_prompt = models.TextField(_("system prompt"), blank=True, editable=False)
    # Werkzeuge der Unterhaltung, beim Anlegen festgelegt: Claude erlaubt
    # keine Änderung der Werkzeugliste innerhalb einer Unterhaltung.
    # "read" (0.8a, nur lesen), "documents" (0.8b, Dokumente und Vorschläge)
    toolset = models.CharField(_("toolset"), max_length=20, default="read", editable=False)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("conversation")
        verbose_name_plural = _("conversations")
        ordering = ["-updated_at", "-id"]
        permissions = [("use_assistant", _("Can use the AI assistant"))]

    def __str__(self):
        return self.title or str(_("New conversation"))

    @property
    def is_running(self):
        return self.status == self.Status.RUNNING


class Message(models.Model):
    """
    Eine Nachricht im Verlauf.

    - ``user``: Eingabe des Benutzers
    - ``assistant``: Antwort des Modells, ggf. mit ``tool_calls``
      ([{"id", "name", "arguments"}])
    - ``tool``: Ergebnis eines Werkzeugs (``tool_call_id``, Inhalt als JSON-Text)
    """

    class Role(models.TextChoices):
        USER = "user", _("user")
        ASSISTANT = "assistant", _("assistant")
        TOOL = "tool", _("tool")
        # Ereignis, z. B. Freigabe eines Vorschlags; geht als Benutzer-
        # Nachricht an das Modell, im Chat als Hinweis (Version 0.8b)
        EVENT = "event", _("event")

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages", verbose_name=_("conversation"),
    )
    role = models.CharField(_("role"), max_length=20, choices=Role.choices)
    content = models.TextField(_("content"), blank=True)
    tool_calls = models.JSONField(_("tool calls"), default=list, blank=True)
    tool_call_id = models.CharField(_("tool call ID"), max_length=200, blank=True)
    tool_name = models.CharField(_("tool"), max_length=100, blank=True)
    # Antwort des Modells im Originalformat des Anbieters (z. B. Claude mit
    # Denkschritten), wird unverändert zurückgegeben (assistant.providers)
    provider = models.CharField(_("provider"), max_length=20, blank=True)
    provider_content = models.JSONField(_("provider content"), default=list, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_role_display()}: {self.content[:50]}"


def attachment_upload_to(instance, filename):
    from contracts.documents import available_name, clean_filename

    field = instance._meta.get_field("file")
    return available_name(field.storage, f"assistant/{instance.conversation_id}", clean_filename(filename))


class Attachment(models.Model):
    """
    Datei, die der Benutzer im Chat hochlädt (Version 0.8b). Sie gehört
    zunächst nur zur Unterhaltung; erst nach Freigabe wird sie als Dokument
    an einen Vertrag gehängt (``document``). Erkannter Text und die vom
    Assistenten festgestellten Daten bleiben lokal gespeichert.
    """

    class Method(models.TextChoices):
        PENDING = "pending", _("not yet read")
        TEXT = "text", _("text")
        PDF_TEXT = "pdf_text", _("PDF text")
        OCR = "ocr", _("text recognition")
        EMAIL = "email", _("email")
        NONE = "none", _("no text found")

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="attachments", verbose_name=_("conversation"),
    )
    message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="attachments",
        verbose_name=_("message"),
    )
    file = models.FileField(_("file"), upload_to=attachment_upload_to, max_length=255)
    original_name = models.CharField(_("file name"), max_length=255)
    size = models.PositiveIntegerField(_("size"), default=0)
    method = models.CharField(_("text recognition"), max_length=20, choices=Method.choices, default=Method.PENDING)
    text = models.TextField(_("recognised text"), blank=True)
    facts = models.JSONField(_("recognised data"), default=dict, blank=True)
    document = models.ForeignKey(
        "contracts.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name=_("document"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("attachment")
        verbose_name_plural = _("attachments")
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.original_name


class Proposal(models.Model):
    """
    Vorschlag des Assistenten für eine Änderung (Version 0.8b). Ausgeführt
    wird er erst, wenn der Benutzer ihn freigibt (assistant.proposals).
    Vorschläge zum Löschen gibt es nicht.
    """

    class Kind(models.TextChoices):
        CREATE_PROVIDER = "create_provider", _("Create contract partner")
        CREATE_CATEGORY = "create_category", _("Create category")
        CREATE_CONSUMER = "create_consumer", _("Create consumer")
        CREATE_CONTRACT = "create_contract", _("Create contract")
        ATTACH_DOCUMENT = "attach_document", _("Attach document to contract")

    class Status(models.TextChoices):
        PENDING = "pending", _("waiting for approval")
        APPROVED = "approved", _("approved")
        REJECTED = "rejected", _("rejected")
        FAILED = "failed", _("failed")

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="proposals", verbose_name=_("conversation"),
    )
    tool_call_id = models.CharField(_("tool call ID"), max_length=200, blank=True)
    kind = models.CharField(_("kind"), max_length=30, choices=Kind.choices)
    data = models.JSONField(_("data"), default=dict)
    # Angezeigte Werte: [[Bezeichnung, Wert], ...]
    summary = models.JSONField(_("summary"), default=list)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    result = models.JSONField(_("result"), default=dict, blank=True)
    error = models.TextField(_("error"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)

    class Meta:
        verbose_name = _("proposal")
        verbose_name_plural = _("proposals")
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.get_kind_display()

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING
