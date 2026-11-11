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
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_htmx.http import reswap, retarget

from contracts import documents
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
            "action_url": self.get_action_url(),
            "use_htmx": bool(self.request.htmx),
        }
        template = self.form_template if self.request.htmx else self.page_template
        return render(self.request, template, context)

    def get(self, request, *args, **kwargs):
        self.load()
        initial = {} if self.document else {"source": request.GET.get("source", Document.Source.UPLOAD)}
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
