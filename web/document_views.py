"""
Dokumente zu Verträgen im Frontend (Version 0.7 beta).

- Rechte folgen dem Vertrag: lesen und herunterladen mit Leserecht,
  anlegen, ändern und löschen mit Bearbeitungsrecht.
- Anlegen und Bearbeiten per HTMX auf der Vertragsseite, ohne JavaScript
  als eigene Seiten (wie bei den Kosteneinträgen).
- Dateien werden nur über ``document_file`` ausgeliefert (Adresse aus
  ``Document.file.url``), nie als öffentliche Mediendatei.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_htmx.http import reswap, retarget

from contracts import documents, paperless
from contracts import permissions as perms
from contracts.models import Contract, Document
from contracts.permissions import visible_to

from .forms import DocumentForm
from .views import cost_context, document_context, get_owned_object


def get_editable_document(user, pk):
    """
    Dokument, dessen Vertrag der Benutzer bearbeiten darf: 404 wenn der
    Vertrag unsichtbar ist, 403 wenn er nur lesbar ist.
    """
    document = get_object_or_404(visible_to(user, Document.objects.select_related("contract")), pk=pk)
    if not perms.can_change(user, document.contract):
        raise PermissionDenied
    return document


def render_sections(request, contract):
    """
    HTMX-Antwort nach dem Speichern oder Löschen: Dokumentenliste und
    Kostenverlauf (zeigt die Namen der verknüpften Dokumente).
    """
    context = {
        **cost_context(contract, request.user),
        **document_context(contract, request.user),
        "oob": True,
    }
    return render(request, "web/contracts/_documents_response.html", context)


class DocumentFormView(LoginRequiredMixin, View):
    """
    Dokument anlegen (contract_pk) oder bearbeiten (pk).
    """
    page_template = "web/contracts/document_form.html"
    form_template = "web/contracts/_document_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.document = None
        self.contract = None

    def load(self):
        user = self.request.user
        if "pk" in self.kwargs:
            self.document = get_editable_document(user, self.kwargs["pk"])
            self.contract = self.document.contract
        else:
            self.contract = get_owned_object(user, Contract.objects.all(), self.kwargs["contract_pk"], "change")

    def get_action_url(self):
        if self.document:
            return reverse("document_edit", args=[self.document.pk])
        return reverse("document_add", args=[self.contract.pk])

    def render_form(self, form):
        context = {
            "form": form,
            "contract": self.contract,
            "document": self.document,
            # Gewählte Quelle: beim Anlegen aus dem Formular, sonst vom Dokument
            "source": form["source"].value() if "source" in form.fields else self.document.source,
            "paperless_enabled": form.paperless.mode != "off",
            "action_url": self.get_action_url(),
            "use_htmx": bool(self.request.htmx),
        }
        template = self.form_template if self.request.htmx else self.page_template
        return render(self.request, template, context)

    def get(self, request, *args, **kwargs):
        self.load()
        initial = {} if self.document else {"source": request.GET.get("source", Document.Source.UPLOAD)}
        if not self.document and initial["source"] != Document.Source.UPLOAD and paperless.config().mode == "off":
            initial["source"] = Document.Source.UPLOAD
        return self.render_form(DocumentForm(instance=self.document, initial=initial, contract=self.contract))

    def post(self, request, *args, **kwargs):
        self.load()
        form = DocumentForm(request.POST, request.FILES, instance=self.document, contract=self.contract)
        if not form.is_valid():
            response = self.render_form(form)
            if request.htmx:
                # Formular mit Fehlern wieder an seine Stelle setzen
                response = reswap(retarget(response, "#document-form"), "innerHTML")
            return response
        document = form.save(commit=False)
        if document.pk is None:
            document.created_by = request.user
        if document.paperless_document_id and document.paperless_sync == Document.SYNC_FAILED:
            # ID von Hand nachgetragen: fehlgeschlagene Übertragung ist erledigt
            document.paperless_sync = ""
            document.paperless_sync_error = ""
        document.save()
        if request.htmx:
            return render_sections(request, self.contract)
        messages.success(request, _("The document has been saved."))
        return redirect("contract_detail", pk=self.contract.pk)


class DocumentDeleteView(LoginRequiredMixin, View):
    """
    Dokument löschen (nur per POST, mit Bearbeitungsrecht am Vertrag).
    Verknüpfte Kosteneinträge bleiben, nur ohne Dokument; eine
    hochgeladene Datei wird mit gelöscht.
    """

    def post(self, request, pk):
        document = get_editable_document(request.user, pk)
        contract = document.contract
        document.delete()
        if request.htmx:
            return render_sections(request, contract)
        messages.success(request, _("The document has been deleted."))
        return redirect("contract_detail", pk=contract.pk)


@login_required
def document_file(request, name):
    """
    Liefert eine hochgeladene Datei aus (Adresse aus ``Document.file.url``),
    nur mit Leserecht am Vertrag, sonst 404. PDFs und Bilder öffnet der
    Browser direkt, alle anderen Typen werden heruntergeladen.
    """
    path = f"{documents.DOCUMENT_DIR}/{name}"
    document = visible_to(request.user, Document.objects.filter(file=path)).first()
    if document is None:
        raise Http404
    try:
        file = document.file.open("rb")
    except FileNotFoundError:
        raise Http404
    inline = documents.is_inline(path)
    response = FileResponse(
        file,
        content_type=documents.content_type(path),
        as_attachment=not inline,
        filename=document.filename,
    )
    if inline and documents.extension(path) != "pdf":
        # Bilder: keine Skripte, kein Zugriff auf die Anwendung. Nicht bei PDF,
        # sonst zeigt der PDF-Betrachter von Chrome die Datei nicht an.
        response["Content-Security-Policy"] = "sandbox"
    patch_cache_control(response, private=True, no_cache=True)
    return response


# --- Paperless über die API (Version 0.9.0) -----------------------------------

@login_required
def document_section(request, pk):
    """
    Dokumentenliste eines Vertrags (HTMX): fragt sie während einer
    Übertragung an Paperless regelmäßig ab. Leserecht genügt.
    """
    contract = get_owned_object(request.user, Contract.objects.all(), pk, "view")
    return render(request, "web/contracts/_documents.html", document_context(contract, request.user))


class DocumentSyncView(LoginRequiredMixin, View):
    """
    Upload an Paperless übertragen (nur POST, Bearbeitungsrecht am Vertrag,
    Betriebsart "API"). Die Arbeit macht der Worker (web.paperless_sync).
    """

    def post(self, request, pk):
        from . import paperless_sync

        document = get_editable_document(request.user, pk)
        if not document.can_sync_to_paperless:
            raise Http404
        paperless_sync.start(document, request.user)
        if request.htmx:
            return render(request, "web/contracts/_documents.html", document_context(document.contract, request.user))
        messages.success(request, _("The document is being transferred to Paperless."))
        return redirect("contract_detail", pk=document.contract.pk)


# Typen, die der Browser direkt anzeigen darf; alles andere wird heruntergeladen
INLINE_TYPES = ("application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif")


@login_required
def paperless_document(request, pk):
    """
    Datei eines Paperless-Dokuments über die API (Betriebsart "API"), nur mit
    Leserecht am Vertrag, sonst 404. Standard ist die von Paperless erzeugte
    PDF-Fassung, ``?original=1`` lädt das Original herunter. Es zählen nur
    die Rechte in Contracts Manager.
    """
    document = get_object_or_404(visible_to(request.user, Document.objects.all()), pk=pk)
    cfg = paperless.config()
    if not cfg.api or not document.paperless_document_id:
        raise Http404
    original = request.GET.get("original") == "1"
    try:
        upstream = paperless.open_document(cfg, document.paperless_document_id, original=original)
    except paperless.PaperlessError as error:
        return render(request, "web/contracts/paperless_error.html", {
            "document": document, "error": error,
        }, status=502)
    content_type = (upstream.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
    inline = not original and content_type in INLINE_TYPES

    def chunks():
        with upstream:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    response = StreamingHttpResponse(chunks(), content_type=content_type)
    filename = paperless_filename(upstream.headers.get("Content-Disposition"), document, content_type)
    response["Content-Disposition"] = content_disposition(filename, inline)
    if upstream.headers.get("Content-Length"):
        response["Content-Length"] = upstream.headers["Content-Length"]
    if inline and content_type != "application/pdf":
        response["Content-Security-Policy"] = "sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    patch_cache_control(response, private=True, no_cache=True)
    return response


def content_disposition(filename, inline):
    from django.utils.http import content_disposition_header

    return content_disposition_header(not inline, filename)


def paperless_filename(header, document, content_type):
    """Dateiname aus der Antwort von Paperless, sonst Name des Dokuments."""
    import mimetypes
    import re
    from urllib.parse import unquote

    if header:
        match = re.search(r"filename\*=UTF-8''([^;]+)", header, re.IGNORECASE)
        if match:
            return unquote(match.group(1))
        match = re.search(r'filename="?([^";]+)"?', header, re.IGNORECASE)
        if match:
            return match.group(1)
    extension = mimetypes.guess_extension(content_type) or ""
    return f"{document.name}{extension}"
