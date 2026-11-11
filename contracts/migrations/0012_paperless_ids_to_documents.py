"""
Version 0.7 beta: Paperless-IDs der Kosteneinträge werden zu Dokumenten
des Vertrags (Quelle Paperless) und mit dem Kosteneintrag verknüpft.
Gleiche IDs im selben Vertrag ergeben ein gemeinsames Dokument.
"""

from django.db import migrations


def paperless_ids_to_documents(apps, schema_editor):
    CostEntry = apps.get_model("contracts", "CostEntry")
    Document = apps.get_model("contracts", "Document")
    entries = CostEntry.objects.filter(paperless_document_id__isnull=False).order_by("contract_id", "valid_from")
    for entry in entries:
        document, _created = Document.objects.get_or_create(
            contract_id=entry.contract_id,
            paperless_document_id=entry.paperless_document_id,
            defaults={"source": "paperless", "name": f"Paperless #{entry.paperless_document_id}"},
        )
        entry.document = document
        entry.save(update_fields=["document"])


def documents_to_paperless_ids(apps, schema_editor):
    CostEntry = apps.get_model("contracts", "CostEntry")
    for entry in CostEntry.objects.filter(document__source="paperless").select_related("document"):
        entry.paperless_document_id = entry.document.paperless_document_id
        entry.save(update_fields=["paperless_document_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0011_document"),
    ]

    operations = [
        migrations.RunPython(paperless_ids_to_documents, documents_to_paperless_ids),
    ]
