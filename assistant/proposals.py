"""
Vorschläge des KI-Assistenten (Version 0.8b).

Der Assistent ändert nie selbst etwas. Er erstellt einen Vorschlag, der
im Chat mit allen Werten angezeigt wird; ausgeführt wird er erst, wenn der
Benutzer ihn freigibt. Geprüft wird zweimal: beim Erstellen (damit das
Modell Fehler sofort sieht) und bei der Freigabe (die Daten können sich
inzwischen geändert haben). Ausgeführt wird mit den Rechten des Benutzers
(contracts.permissions); der Benutzer wird Eigentümer neuer Objekte.

Vorschläge zum Löschen gibt es nicht.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from contracts import permissions as perms
from contracts.models import Category, Consumer, Contract, CostEntry, Country, Document, Provider

from .models import Proposal


class ProposalError(Exception):
    """
    Ungültiger Vorschlag; die Meldung geht an das Modell bzw. den Benutzer.
    """


def _text(data, key, max_length=None, required=False):
    value = str(data.get(key) or "").strip()
    if required and not value:
        raise ProposalError(f"“{key}” is required.")
    if max_length and len(value) > max_length:
        raise ProposalError(f"“{key}” is longer than {max_length} characters.")
    return value


def _date(data, key, required=False):
    value = data.get(key)
    if not value:
        if required:
            raise ProposalError(f"“{key}” is required (YYYY-MM-DD).")
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ProposalError(f"“{key}” must be a date in the format YYYY-MM-DD.")


def _int(data, key):
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ProposalError(f"“{key}” must be a number.")


def _visible(user, model, pk, label):
    obj = perms.visible_to(user, model.objects.all()).filter(pk=pk).first() if pk else None
    if obj is None:
        raise ProposalError(f"{label} {pk} does not exist or is not visible to the user.")
    return obj


def _attachment(conversation, pk):
    attachment = conversation.attachments.filter(pk=pk).first() if pk else None
    if attachment is None:
        raise ProposalError(f"Attachment {pk} does not exist in this conversation.")
    return attachment


def _check(obj, exclude=None):
    try:
        obj.full_clean(exclude=exclude)
    except ValidationError as error:
        messages = []
        for field, errors in error.message_dict.items():
            messages += [f"{field}: {message}" for message in errors]
        raise ProposalError("; ".join(messages))


def _require(user, permission):
    if not user.has_perm(f"contracts.{permission}"):
        raise ProposalError("The user may not create this kind of object.")


# --- Prüfung je Art: gibt das ungespeicherte Objekt und die Anzeige zurück --------

def _build_provider(user, data, conversation):
    _require(user, "add_provider")
    country = _text(data, "country", 2).upper() or Country.DE
    provider = Provider(
        owner=user, name=_text(data, "name", 200, required=True),
        street=_text(data, "street", 255), postal_code=_text(data, "postal_code", 20),
        city=_text(data, "city", 100), country=country, email=_text(data, "email", 254),
        customer_number=_text(data, "customer_number", 100), contact_person=_text(data, "contact_person", 200),
        notes=_text(data, "notes"),
    )
    _check(provider, exclude=["logo"])
    summary = [
        (_("Name"), provider.name),
        (_("Address"), ", ".join(part for part in (
            provider.street, f"{provider.postal_code} {provider.city}".strip(), provider.get_country_display(),
        ) if part)),
        (_("Email"), provider.email), (_("Customer number"), provider.customer_number),
        (_("Contact person"), provider.contact_person), (_("Notes"), provider.notes),
    ]
    return provider, summary


def _build_category(user, data, conversation):
    _require(user, "add_category")
    parent_id = _int(data, "parent_id")
    parent = _visible(user, Category, parent_id, "Category") if parent_id else None
    if parent and parent.parent_id:
        raise ProposalError("parent_id must be a main category.")
    category = Category(owner=user, name=_text(data, "name", 200, required=True), parent=parent)
    _check(category)
    return category, [(_("Name"), category.name), (_("Main category"), parent.name if parent else "–")]


def _build_consumer(user, data, conversation):
    _require(user, "add_consumer")
    consumer = Consumer(
        owner=user, name=_text(data, "name", 200, required=True),
        number=_text(data, "number", 100), notes=_text(data, "notes"),
    )
    _check(consumer)
    return consumer, [(_("Name"), consumer.name), (_("Number"), consumer.number), (_("Notes"), consumer.notes)]


INTERVALS = {"weekly", "monthly", "quarterly", "yearly"}
TERM_UNITS = {"days", "weeks", "months", "quarters", "years"}


def _cost(data, start_date):
    if data.get("amount") in (None, ""):
        return None
    try:
        amount = Decimal(str(data["amount"])).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ProposalError("“amount” must be a number, e.g. 49.90.")
    if amount < 0:
        raise ProposalError("“amount” must not be negative.")
    interval = _text(data, "payment_interval") or "monthly"
    if interval not in INTERVALS:
        raise ProposalError(f"“payment_interval” must be one of {sorted(INTERVALS)}.")
    return {"amount": amount, "payment_interval": interval,
            "valid_from": _date(data, "valid_from") or start_date or timezone.localdate()}


def _build_contract(user, data, conversation):
    _require(user, "add_contract")
    term_unit = _text(data, "term_unit") or None
    if term_unit and term_unit not in TERM_UNITS:
        raise ProposalError(f"“term_unit” must be one of {sorted(TERM_UNITS)}.")
    contract = Contract(
        owner=user, name=_text(data, "name", 200, required=True),
        provider=_visible(user, Provider, _int(data, "provider_id"), "Contract partner"),
        category=_visible(user, Category, _int(data, "category_id"), "Category"),
        # Verbraucher und Vertragsbeginn sind optional (0.9 beta)
        consumer=_visible(user, Consumer, _int(data, "consumer_id"), "Consumer") if data.get("consumer_id") else None,
        start_date=_date(data, "start_date"),
        contract_number=_text(data, "contract_number", 200),
        short_description=_text(data, "short_description", 255), notes=_text(data, "notes"),
        term_value=_int(data, "term_value"), term_unit=term_unit,
    )
    _check(contract, exclude=["reference"])
    cost = _cost(data, contract.start_date)
    attachment = _attachment(conversation, _int(data, "attachment_id")) if data.get("attachment_id") else None
    summary = [
        (_("Name"), contract.name), (_("Contract partner"), contract.provider.name),
        (_("Category"), str(contract.category)), (_("Consumer"), contract.consumer.name if contract.consumer else ""),
        (_("Start date"), contract.start_date.isoformat() if contract.start_date else ""), (_("Contract number"), contract.contract_number),
        (_("Short description"), contract.short_description),
        (_("Term"), f"{contract.term_value} {contract.get_term_unit_display()}" if contract.term_value else ""),
        (_("Notes"), contract.notes),
    ]
    if cost:
        summary.append((_("Costs"), _("%(amount)s € %(interval)s from %(date)s") % {
            "amount": cost["amount"], "interval": dict(CostEntry._meta.get_field("payment_interval").choices)[
                cost["payment_interval"]], "date": cost["valid_from"].isoformat(),
        }))
    if attachment:
        summary.append((_("Document"), attachment.original_name))
    return (contract, cost, attachment), summary


def _build_attach(user, data, conversation):
    contract = _visible(user, Contract, _int(data, "contract_id"), "Contract")
    if not perms.can_change(user, contract):
        raise ProposalError("The user may not edit this contract.")
    attachment = _attachment(conversation, _int(data, "attachment_id"))
    name = _text(data, "name", 200) or attachment.original_name.rsplit(".", 1)[0][:200]
    summary = [
        (_("Contract"), f"{contract.reference} – {contract.name}"),
        (_("Document"), attachment.original_name), (_("Name"), name),
    ]
    return (contract, attachment, name), summary


BUILDERS = {
    Proposal.Kind.CREATE_PROVIDER: _build_provider,
    Proposal.Kind.CREATE_CATEGORY: _build_category,
    Proposal.Kind.CREATE_CONSUMER: _build_consumer,
    Proposal.Kind.CREATE_CONTRACT: _build_contract,
    Proposal.Kind.ATTACH_DOCUMENT: _build_attach,
}


def create(conversation, kind, data, tool_call_id=""):
    """
    Prüft den Vorschlag und speichert ihn (Status "wartet auf Freigabe").
    Leere Werte erscheinen nicht in der Anzeige.
    """
    _built, summary = BUILDERS[kind](conversation.user, data, conversation)
    return Proposal.objects.create(
        conversation=conversation, kind=kind, data=data, tool_call_id=tool_call_id,
        summary=[[label, value] for label, value in summary if value],
    )


# --- Ausführung nach Freigabe ---------------------------------------------------------

def _attach_file(contract, attachment, name, user):
    with attachment.file.open("rb") as source:
        document = Document(contract=contract, name=name, source=Document.Source.UPLOAD, created_by=user)
        document.file.save(attachment.original_name, File(source), save=False)
        document.full_clean()
        document.save()
    attachment.document = document
    attachment.save(update_fields=["document"])
    return document


def _result(obj, label, url):
    return {"model": obj._meta.model_name, "id": obj.pk, "label": label, "url": url}


@transaction.atomic
def _execute(proposal):
    user = proposal.conversation.user
    built, _summary = BUILDERS[proposal.kind](user, proposal.data, proposal.conversation)
    kind = proposal.kind
    if kind == Proposal.Kind.CREATE_PROVIDER:
        built.save()
        return _result(built, built.name, reverse("provider_detail", args=[built.pk]))
    if kind == Proposal.Kind.CREATE_CATEGORY:
        built.save()
        return _result(built, str(built), reverse("category_detail", args=[built.pk]))
    if kind == Proposal.Kind.CREATE_CONSUMER:
        built.save()
        return _result(built, built.name, reverse("consumer_detail", args=[built.pk]))
    if kind == Proposal.Kind.CREATE_CONTRACT:
        contract, cost, attachment = built
        contract.save()
        document = None
        if attachment:
            document = _attach_file(contract, attachment, attachment.original_name.rsplit(".", 1)[0][:200], user)
        if cost:
            CostEntry.objects.create(contract=contract, document=document, **cost)
        return _result(contract, f"{contract.reference} – {contract.name}",
                       reverse("contract_detail", args=[contract.pk]))
    contract, attachment, name = built
    _attach_file(contract, attachment, name, user)
    return _result(contract, f"{contract.reference} – {contract.name}", reverse("contract_detail", args=[contract.pk]))


def approve(proposal):
    """
    Führt den Vorschlag aus. Bei einem Fehler bleibt nichts gespeichert und
    der Vorschlag gilt als fehlgeschlagen.
    """
    try:
        proposal.result = _execute(proposal)
        proposal.status = Proposal.Status.APPROVED
    except (ProposalError, ValidationError) as error:
        proposal.status = Proposal.Status.FAILED
        proposal.error = str(error)
    proposal.decided_at = timezone.now()
    proposal.save()
    return proposal


def reject(proposal):
    proposal.status = Proposal.Status.REJECTED
    proposal.decided_at = timezone.now()
    proposal.save()
    return proposal


def outcome_text(proposal):
    """
    Nachricht für das Modell nach der Entscheidung (anbieterneutral).
    """
    if proposal.status == Proposal.Status.APPROVED:
        return (f"[Approval] The user approved proposal {proposal.pk} ({proposal.kind}). "
                f"Created/updated: {proposal.result.get('model')} id {proposal.result.get('id')} "
                f"({proposal.result.get('label')}).")
    if proposal.status == Proposal.Status.FAILED:
        return f"[Approval] The user approved proposal {proposal.pk} ({proposal.kind}), but it failed: {proposal.error}"
    return f"[Approval] The user rejected proposal {proposal.pk} ({proposal.kind}). Do not repeat it unasked."
