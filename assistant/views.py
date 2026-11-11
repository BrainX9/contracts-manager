"""
Chat mit dem KI-Assistenten (Version 0.8a).

- Nur mit AI_ENABLED und Recht assistant.use_assistant (Gruppe "KI") oder
  als Superuser; ist der Assistent aus, liefern die Seiten 404.
- Jeder sieht nur seine eigenen Unterhaltungen (fremde: 404).
- Die Antwort entsteht im Worker; die Seite fragt per HTMX alle zwei
  Sekunden nach, solange sie läuft. Ohne JavaScript lädt man neu.
- Seit 0.8b: Dateien im Chat hochladen; Vorschläge des Assistenten werden
  erst nach Freigabe ausgeführt (assistant.proposals).
"""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from contracts import permissions as perms

from . import extraction, proposals
from .agent import add_event, add_user_message
from .models import Conversation, Proposal
from .tasks import queue_answer
from .tools import DEFAULT_TOOLSET

# Höchstlänge einer Eingabe
MAX_MESSAGE_LENGTH = 4000
# Angezeigte Unterhaltungen in der Liste
MAX_CONVERSATIONS = 30


class MessageForm(forms.Form):
    text = forms.CharField(
        label=_("Message"), max_length=MAX_MESSAGE_LENGTH, strip=True, required=False,
        widget=forms.Textarea(attrs={
            "rows": 2, "class": "form-control", "placeholder": _("Ask about your contracts …"),
        }),
    )
    file = forms.FileField(
        label=_("Attach document"), required=False,
        widget=forms.ClearableFileInput(attrs={
            "class": "visually-hidden", "accept": ",".join(f".{ext}" for ext in extraction.ALLOWED_EXTENSIONS),
        }),
    )

    def __init__(self, *args, allow_files=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_files:
            del self.fields["file"]

    def clean_file(self):
        upload = self.cleaned_data.get("file")
        if upload:
            error = extraction.validate_upload(upload)
            if error:
                raise forms.ValidationError(error)
        return upload

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("text") and not cleaned_data.get("file") and not self.errors:
            raise forms.ValidationError(_("Please enter a message or attach a document."))
        return cleaned_data

    def message_text(self):
        return self.cleaned_data.get("text") or str(_("Please check this document."))


class AssistantMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not settings.AI_ENABLED:
            raise Http404
        if not perms.can_use_assistant(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_conversation(self, pk):
        return get_object_or_404(Conversation, pk=pk, user=self.request.user)

    @staticmethod
    def allow_files(conversation):
        # Ältere Unterhaltungen (0.8a) haben keine Werkzeuge für Dokumente
        return conversation is None or conversation.toolset == "documents"

    def message_form(self, conversation=None, data=None, files=None):
        return MessageForm(data, files, allow_files=self.allow_files(conversation))

    def page_context(self, conversation=None, form=None):
        return {
            "conversation": conversation,
            "conversations": self.request.user.assistant_conversations.all()[:MAX_CONVERSATIONS],
            "chat_messages": visible_messages(conversation) if conversation else [],
            "form": form or self.message_form(conversation),
            "allow_files": self.allow_files(conversation),
            "provider": settings.AI_PROVIDER,
            "model": settings.AI_MODEL,
        }


# Sichtbare Bezeichnung der Werkzeuge im Verlauf
TOOL_LABELS = {
    "search_contracts": _("searched contracts"),
    "get_contract": _("read contract details"),
    "cost_overview": _("calculated costs"),
    "search_master_data": _("searched master data"),
    "list_attachments": _("listed documents"),
    "read_attachment": _("read document"),
    "save_document_facts": _("saved document data"),
    "propose_create_provider": _("proposed a change"),
    "propose_create_category": _("proposed a change"),
    "propose_create_consumer": _("proposed a change"),
    "propose_create_contract": _("proposed a change"),
    "propose_attach_document": _("proposed a change"),
}


def visible_messages(conversation):
    """
    Nachrichten für die Anzeige: Eingaben (mit Anhängen) und Antworten mit
    Text. Genutzte Werkzeuge und Vorschläge erscheinen an der nächsten
    Antwort; Ereignisse (Freigaben) zeigt die Karte des Vorschlags selbst.
    """
    proposals_by_call = {proposal.tool_call_id: proposal for proposal in conversation.proposals.all()}
    attachments = {}
    for attachment in conversation.attachments.all():
        attachments.setdefault(attachment.message_id, []).append(attachment)
    items, used, pending = [], [], []
    for message in conversation.messages.all():
        if message.role == "assistant":
            used += [str(TOOL_LABELS.get(call.get("name"), call.get("name"))) for call in message.tool_calls]
            pending += [proposals_by_call[call["id"]] for call in message.tool_calls
                        if call.get("id") in proposals_by_call]
            if message.content:
                items.append({"role": "assistant", "content": message.content,
                              "tools": list(dict.fromkeys(used)), "proposals": pending})
                used, pending = [], []
        elif message.role == "user":
            items.append({"role": "user", "content": message.content, "tools": [], "proposals": [],
                          "attachments": attachments.get(message.pk, [])})
            used = []
    if pending:
        items.append({"role": "assistant", "content": "", "tools": [], "proposals": pending})
    return items


class AssistantHomeView(AssistantMixin, View):
    """
    Liste der Unterhaltungen und Eingabe für eine neue.
    """

    def get(self, request):
        return render(request, "assistant/chat.html", self.page_context())

    def post(self, request):
        form = self.message_form(data=request.POST, files=request.FILES)
        if not form.is_valid():
            return render(request, "assistant/chat.html", self.page_context(form=form))
        conversation = Conversation.objects.create(user=request.user, toolset=DEFAULT_TOOLSET)
        add_user_message(conversation, form.message_text(), form.cleaned_data.get("file"))
        queue_answer(conversation)
        return redirect("assistant_conversation", pk=conversation.pk)


class ConversationView(AssistantMixin, View):
    """
    Eine Unterhaltung: anzeigen und weitere Nachricht senden.
    """

    def get(self, request, pk):
        conversation = self.get_conversation(pk)
        return render(request, "assistant/chat.html", self.page_context(conversation))

    def post(self, request, pk):
        conversation = self.get_conversation(pk)
        form = self.message_form(conversation, request.POST, request.FILES)
        if conversation.is_running:
            form.add_error(None, _("Please wait until the current answer is complete."))
        if form.is_valid():
            add_user_message(conversation, form.message_text(), form.cleaned_data.get("file"))
            queue_answer(conversation)
            form = self.message_form(conversation)
        if request.htmx:
            return render(request, "assistant/_messages.html", self.page_context(conversation, form))
        if form.errors:
            return render(request, "assistant/chat.html", self.page_context(conversation, form))
        return redirect("assistant_conversation", pk=conversation.pk)


class ConversationMessagesView(AssistantMixin, View):
    """
    Verlauf einer Unterhaltung (HTMX-Abfrage, solange die Antwort läuft).
    """

    def get(self, request, pk):
        conversation = self.get_conversation(pk)
        return render(request, "assistant/_messages.html", self.page_context(conversation))


class ConversationDeleteView(AssistantMixin, View):
    """
    Eigene Unterhaltung löschen (nur per POST). Der Assistent selbst kann
    nichts löschen; das darf nur der Benutzer mit seinen Unterhaltungen.
    """

    def post(self, request, pk):
        self.get_conversation(pk).delete()
        return redirect("assistant_home")


class ProposalDecisionView(AssistantMixin, View):
    """
    Vorschlag freigeben oder ablehnen (nur per POST, nur der Benutzer der
    Unterhaltung). Danach erfährt der Assistent das Ergebnis und antwortet
    erneut, z. B. mit dem nächsten Schritt.
    """

    def post(self, request, pk, decision):
        proposal = get_object_or_404(
            Proposal.objects.select_related("conversation"), pk=pk, conversation__user=request.user,
        )
        conversation = proposal.conversation
        if decision not in ("approve", "reject"):
            raise Http404
        error = None
        if not proposal.is_pending:
            error = _("This proposal has already been decided.")
        elif conversation.is_running:
            error = _("Please wait until the current answer is complete.")
        else:
            if decision == "approve":
                proposals.approve(proposal)
            else:
                proposals.reject(proposal)
            add_event(conversation, proposals.outcome_text(proposal))
            queue_answer(conversation)
        if request.htmx:
            return render(request, "assistant/_messages.html", {
                **self.page_context(conversation), "notice": error,
            })
        if error:
            messages.warning(request, error)
        return redirect("assistant_conversation", pk=conversation.pk)
