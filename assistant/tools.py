"""
Werkzeuge des KI-Assistenten (Version 0.8a).

Alle Werkzeuge in dieser Version lesen nur. Sie sehen ausschließlich
Objekte, die der angemeldete Benutzer lesen darf (contracts.permissions),
und geben einfache JSON-Daten zurück. Werkzeuge zum Löschen gibt es nicht.
Aktionen mit Änderungen kommen später nur als Vorschlag, den der Benutzer
freigibt.
"""

from decimal import Decimal

from django.db.models import Q

from contracts import permissions as perms
from contracts.models import CENT, Category, Consumer, Contract, Provider, total_current_costs

# Höchstzahl der Treffer je Suche, damit Antworten klein bleiben
MAX_RESULTS = 25


def _money(value):
    return None if value is None else str(Decimal(value).quantize(CENT))


def _visible_contracts(user):
    return (
        perms.visible_to(user, Contract.objects.all())
        .select_related("provider", "category__parent", "consumer")
        .prefetch_related("costs")
    )


def _contract_summary(contract):
    entry = contract.current_cost_entry
    return {
        "id": contract.pk,
        "reference": contract.reference,
        "name": contract.name,
        "provider": contract.provider.name,
        "category": str(contract.category),
        "consumer": contract.consumer.name if contract.consumer_id else None,
        "current_amount": _money(entry.amount) if entry else None,
        "payment_interval": entry.payment_interval if entry else None,
        "monthly_amount": _money(entry.monthly_amount()) if entry else None,
    }


def search_contracts(user, query="", limit=MAX_RESULTS):
    contracts = _visible_contracts(user)
    query = (query or "").strip()
    if query:
        contracts = contracts.filter(
            Q(name__icontains=query) | Q(reference__icontains=query) | Q(contract_number__icontains=query)
            | Q(short_description__icontains=query) | Q(notes__icontains=query)
            | Q(provider__name__icontains=query) | Q(category__name__icontains=query)
            | Q(category__parent__name__icontains=query) | Q(consumer__name__icontains=query)
        )
    contracts = list(contracts.order_by("name"))
    limit = max(1, min(int(limit or MAX_RESULTS), MAX_RESULTS))
    return {
        "total": len(contracts),
        "contracts": [_contract_summary(contract) for contract in contracts[:limit]],
        "truncated": len(contracts) > limit,
    }


def get_contract(user, contract_id=None, reference=None):
    contracts = _visible_contracts(user).prefetch_related("documents")
    if contract_id:
        contract = contracts.filter(pk=contract_id).first()
    elif reference:
        contract = contracts.filter(reference=str(reference).strip()).first()
    else:
        return {"error": "Pass contract_id or reference."}
    if contract is None:
        return {"error": "No visible contract found."}
    # Fehlt die Erinnerung, löst der Zugriff RelatedObjectDoesNotExist aus (AttributeError)
    reminder = getattr(contract, "reminder", None)
    data = _contract_summary(contract)
    data.update({
        "contract_number": contract.contract_number,
        "short_description": contract.short_description,
        "notes": contract.notes,
        "start_date": contract.start_date.isoformat() if contract.start_date else None,
        "term": f"{contract.term_value} {contract.term_unit}" if contract.term_value else None,
        "cost_history": [
            {
                "valid_from": entry.valid_from.isoformat(),
                "amount": _money(entry.amount),
                "payment_interval": entry.payment_interval,
                "monthly_amount": _money(entry.monthly_amount()),
                "notes": entry.notes,
                "document": entry.document.name if entry.document_id else None,
            }
            for entry in contract.costs.select_related("document").order_by("-valid_from", "-id")
        ],
        "documents": [
            {"name": document.name, "source": document.source, "created_at": document.created_at.date().isoformat()}
            for document in contract.documents.all()
        ],
        "reminder": {
            "enabled": reminder.enabled,
            "remind_on": reminder.remind_on.isoformat() if reminder.remind_on else None,
            "reason": reminder.reason,
            "note": reminder.note,
        } if reminder else None,
    })
    return data


def cost_overview(user, group_by="none"):
    contracts = list(_visible_contracts(user))
    result = {"total": _totals(contracts)}
    keys = {
        "category": lambda c: str(c.category.parent or c.category),
        "provider": lambda c: c.provider.name,
        "consumer": lambda c: c.consumer.name if c.consumer_id else "–",
    }
    if group_by in keys:
        groups = {}
        for contract in contracts:
            groups.setdefault(keys[group_by](contract), []).append(contract)
        result["groups"] = sorted(
            ({"name": name, **_totals(items)} for name, items in groups.items()),
            key=lambda group: Decimal(group["monthly"]), reverse=True,
        )
    return result


def _totals(contracts):
    totals = total_current_costs(contracts)
    return {"monthly": _money(totals["monthly"]), "yearly": _money(totals["yearly"]), "contracts": totals["count"]}


def search_master_data(user, kind, query=""):
    models = {"provider": Provider, "category": Category, "consumer": Consumer}
    model = models.get(kind)
    if model is None:
        return {"error": "kind must be provider, category or consumer."}
    objects = perms.visible_to(user, model.objects.all())
    query = (query or "").strip()
    if query:
        objects = objects.filter(name__icontains=query)
    objects = list(objects.order_by("name")[:MAX_RESULTS])
    items = []
    for obj in objects:
        item = {"id": obj.pk, "name": str(obj)}
        if kind == "provider":
            item.update({
                "street": obj.street, "postal_code": obj.postal_code, "city": obj.city,
                "country": obj.country, "email": obj.email, "customer_number": obj.customer_number,
                "contact_person": obj.contact_person,
            })
        elif kind == "consumer":
            item["number"] = obj.number
        items.append(item)
    return {"results": items}


# Beschreibung für das Modell (JSON-Schema) und Funktion je Werkzeug
TOOLS = {
    "search_contracts": {
        "function": search_contracts,
        "description": (
            "Search the user's contracts by text (name, reference, contract number, partner, category, "
            "consumer, notes). Empty query lists all contracts. Returns current amounts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text, may be empty"},
                "limit": {"type": "integer", "description": f"Maximum results (1-{MAX_RESULTS})"},
            },
        },
    },
    "get_contract": {
        "function": get_contract,
        "description": (
            "Get full details of one contract: term, notes, cost history, documents and reminder. "
            "Use contract_id from search results or the reference (e.g. 20260001)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contract_id": {"type": "integer"},
                "reference": {"type": "string"},
            },
        },
    },
    "cost_overview": {
        "function": cost_overview,
        "description": (
            "Totals of all current costs per month and per year, optionally grouped by main category, "
            "partner or consumer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": ["none", "category", "provider", "consumer"]},
            },
        },
    },
    "search_master_data": {
        "function": search_master_data,
        "description": "Search contract partners (with address and contact), categories or consumers by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["provider", "category", "consumer"]},
                "query": {"type": "string"},
            },
            "required": ["kind"],
        },
    },
}


# --- Dokumente und Vorschläge (Version 0.8b) ------------------------------------
# Diese Werkzeuge brauchen die Unterhaltung (Anhänge, Vorschläge) und
# bekommen sie als ersten Parameter.

# Höchstlänge eines Textauszugs je Aufruf (Zeichen)
TEXT_CHUNK = 12_000


def list_attachments(conversation):
    return {"attachments": [
        {
            "attachment_id": attachment.pk, "file_name": attachment.original_name,
            "text_recognition": attachment.method, "text_length": len(attachment.text),
            "saved_facts": attachment.facts,
            "attached_to_document": attachment.document_id,
        }
        for attachment in conversation.attachments.all()
    ]}


def read_attachment(conversation, attachment_id, offset=0):
    attachment = conversation.attachments.filter(pk=attachment_id).first()
    if attachment is None:
        return {"error": "Attachment not found in this conversation."}
    offset = max(0, int(offset or 0))
    chunk = attachment.text[offset:offset + TEXT_CHUNK]
    result = {
        "attachment_id": attachment.pk, "file_name": attachment.original_name,
        "text_recognition": attachment.method, "text": chunk,
        "next_offset": offset + TEXT_CHUNK if offset + TEXT_CHUNK < len(attachment.text) else None,
        "saved_facts": attachment.facts,
    }
    if not attachment.text:
        # Lokal nichts erkannt: Datei geht (bei Claude) direkt an das Modell
        result["note"] = "No text was recognised locally. The file itself is attached to this result if possible."
        result["_attach_file"] = attachment.pk
    return result


def save_document_facts(conversation, attachment_id, facts):
    attachment = conversation.attachments.filter(pk=attachment_id).first()
    if attachment is None:
        return {"error": "Attachment not found in this conversation."}
    if not isinstance(facts, dict):
        return {"error": "facts must be an object."}
    attachment.facts = {**attachment.facts, **facts}
    attachment.save(update_fields=["facts"])
    return {"saved": True, "facts": attachment.facts}


def _propose(kind):
    def function(conversation, tool_call_id="", **data):
        from .proposals import ProposalError, create

        try:
            proposal = create(conversation, kind, data, tool_call_id=tool_call_id)
        except ProposalError as error:
            return {"error": str(error)}
        return {
            "proposal_id": proposal.pk, "status": "waiting for the user's approval",
            "shown_to_user": proposal.summary,
            "instruction": "Stop here. Tell the user to approve or reject the card. Do not assume it was carried out.",
        }
    return function


_TEXT = {"type": "string"}
_ID = {"type": "integer"}

DOCUMENT_TOOLS = {
    "list_attachments": {
        "function": list_attachments,
        "description": "List the files the user uploaded in this conversation, with saved facts.",
        "parameters": {"type": "object", "properties": {}},
    },
    "read_attachment": {
        "function": read_attachment,
        "description": (
            "Read the recognised text of an uploaded file (in chunks; continue with next_offset). "
            "If no text was recognised locally, the file itself is provided where the model supports it."
        ),
        "parameters": {
            "type": "object",
            "properties": {"attachment_id": _ID, "offset": _ID},
            "required": ["attachment_id"],
        },
    },
    "save_document_facts": {
        "function": save_document_facts,
        "description": (
            "Store the relevant facts you found in an uploaded document locally, e.g. provider, address, "
            "customer_number, contract_number, start_date, amount, payment_interval, term, notice_period, "
            "end_date, consumer. Call this after reading a document. Does not change any contract."
        ),
        "parameters": {
            "type": "object",
            "properties": {"attachment_id": _ID, "facts": {"type": "object"}},
            "required": ["attachment_id", "facts"],
        },
    },
    "propose_create_provider": {
        "function": _propose("create_provider"),
        "description": "Propose a new contract partner. Shown to the user for approval; nothing is saved before.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": _TEXT, "street": _TEXT, "postal_code": _TEXT, "city": _TEXT,
                "country": {"type": "string", "description": "ISO code, e.g. DE"},
                "email": _TEXT, "customer_number": _TEXT, "contact_person": _TEXT, "notes": _TEXT,
            },
            "required": ["name"],
        },
    },
    "propose_create_category": {
        "function": _propose("create_category"),
        "description": "Propose a new category (optionally below a main category). Needs approval.",
        "parameters": {
            "type": "object",
            "properties": {"name": _TEXT, "parent_id": _ID},
            "required": ["name"],
        },
    },
    "propose_create_consumer": {
        "function": _propose("create_consumer"),
        "description": "Propose a new consumer (person, car, household …). Needs approval.",
        "parameters": {
            "type": "object",
            "properties": {"name": _TEXT, "number": _TEXT, "notes": _TEXT},
            "required": ["name"],
        },
    },
    "propose_create_contract": {
        "function": _propose("create_contract"),
        "description": (
            "Propose a new contract with existing partner and category, optionally a consumer (IDs from the "
            "search tools), the start date, the first cost entry and the uploaded document. Needs approval. "
            "Propose missing partners, categories or consumers first and wait for their approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": _TEXT, "provider_id": _ID, "category_id": _ID, "consumer_id": _ID,
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "contract_number": _TEXT, "short_description": _TEXT, "notes": _TEXT,
                "term_value": _ID,
                "term_unit": {"type": "string", "enum": ["days", "weeks", "months", "quarters", "years"]},
                "amount": {"type": "number"},
                "payment_interval": {"type": "string", "enum": ["weekly", "monthly", "quarterly", "yearly"]},
                "valid_from": {"type": "string", "description": "YYYY-MM-DD, default start_date, else today"},
                "attachment_id": _ID,
            },
            "required": ["name", "provider_id", "category_id"],
        },
    },
    "propose_attach_document": {
        "function": _propose("attach_document"),
        "description": "Propose attaching an uploaded file to an existing contract as a document. Needs approval.",
        "parameters": {
            "type": "object",
            "properties": {"contract_id": _ID, "attachment_id": _ID, "name": _TEXT},
            "required": ["contract_id", "attachment_id"],
        },
    },
}

TOOLSETS = {
    "read": dict(TOOLS),
    "documents": {**TOOLS, **DOCUMENT_TOOLS},
}
# Neue Unterhaltungen bekommen diese Werkzeuge
DEFAULT_TOOLSET = "documents"


def tool_definitions(toolset="read"):
    return [
        {"name": name, "description": tool["description"], "parameters": tool["parameters"]}
        for name, tool in TOOLSETS.get(toolset, TOOLS).items()
    ]


def run_tool(user, name, arguments, conversation=None, tool_call_id=""):
    """
    Führt ein Werkzeug mit den Rechten des Benutzers aus. Nur Werkzeuge der
    Unterhaltung sind erlaubt. Unbekannte Werkzeuge und falsche Argumente
    ergeben eine Fehlermeldung für das Modell, keine Ausnahme.
    """
    toolset = TOOLSETS.get(conversation.toolset if conversation else "read", TOOLS)
    tool = toolset.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name}."}
    arguments = dict(arguments or {})
    try:
        if name in DOCUMENT_TOOLS:
            if name.startswith("propose_"):
                arguments["tool_call_id"] = tool_call_id
            return tool["function"](conversation, **arguments)
        return tool["function"](user, **arguments)
    except (TypeError, ValueError) as error:
        return {"error": f"Invalid arguments: {error}"}
