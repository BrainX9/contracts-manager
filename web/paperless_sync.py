"""
Uploads an Paperless übertragen (Version 0.9.0, Betriebsart "API").

Paperless antwortet auf einen Upload nur mit der ID einer Aufgabe; die
Dokument-ID entsteht erst, wenn Paperless die Datei verarbeitet hat. Deshalb
läuft alles im Worker (django-q2):

1. ``start`` (Ansicht): Dokument als "wird übertragen" markieren, Aufgabe
   ``transfer`` einreihen.
2. ``transfer``: Liegt die Datei schon in Paperless (gleiche Prüfsumme der
   Originaldatei), wird dieses Dokument verknüpft, ohne neu hochzuladen
   (Version 0.9.1). Sonst Tags auflösen (fehlende anlegen), Datei mit Titel,
   Tags und Korrespondent (vom Vertragspartner) hochladen, dann ``follow``.
3. ``follow``: den Stand der Aufgabe abfragen. Darf der Paperless-Benutzer
   keine Aufgaben sehen (HTTP 403), sucht es das Dokument über die
   Prüfsumme. Fertig: Paperless-ID am Dokument speichern. Noch nicht fertig:
   in zwei Minuten erneut, höchstens eine Stunde lang.
"""

import logging
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from contracts import paperless
from contracts.models import Document

logger = logging.getLogger(__name__)

# Abfragen innerhalb einer Aufgabe (Worker-Timeout 300 s)
POLL_SECONDS = 120
POLL_INTERVAL = 3
FOLLOW_UP_MINUTES = 2
GIVE_UP_AFTER = timedelta(hours=1)


def start(document, user):
    """Übertragung anstoßen (Rechte prüft die Ansicht)."""
    from django_q.tasks import async_task

    Document.objects.filter(pk=document.pk).update(
        paperless_sync=Document.SYNC_PENDING, paperless_sync_error="", paperless_sync_task="",
        paperless_sync_started=timezone.now(),
    )
    async_task("web.paperless_sync.transfer", document.pk, user.pk if user else None)


def fail(document_id, message):
    Document.objects.filter(pk=document_id).update(
        paperless_sync=Document.SYNC_FAILED, paperless_sync_error=str(message)[:2000],
    )


def pending_document(document_id):
    document = Document.objects.select_related("contract__provider").filter(pk=document_id).first()
    if document is None or document.paperless_sync != Document.SYNC_PENDING:
        return None
    return document


def transfer(document_id, user_id=None):
    document = pending_document(document_id)
    if document is None:
        return "skipped"
    cfg = paperless.config()
    if not cfg.api:
        fail(document_id, _("The Paperless API is not configured."))
        return "failed"
    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    try:
        with document.file.open("rb") as file:
            content = file.read()
        existing = paperless.find_by_checksum(cfg, paperless.checksums(content))
        if existing:
            return finish(document, paperless.TaskState(done=True, document_id=existing))
        names = list(cfg.tags)
        user_tag = cfg.user_tag_for(user)
        if user_tag and user_tag.lower() not in (name.lower() for name in names):
            names.append(user_tag)
        tags = paperless.tag_ids(cfg, names)
        provider = document.contract.provider
        task_id = paperless.upload(
            cfg, document.filename, content, document.name, tags=tags,
            correspondent=getattr(provider, "paperless_correspondent_id", None),
        )
    except paperless.PaperlessError as error:
        fail(document_id, error)
        return "failed"
    except Exception as error:
        logger.exception("Übertragung an Paperless fehlgeschlagen (Dokument %s)", document_id)
        fail(document_id, _("The transfer failed: %(error)s") % {"error": error})
        return "failed"
    Document.objects.filter(pk=document_id).update(paperless_sync_task=task_id)
    return follow(document_id)


def follow(document_id, poll_seconds=None):
    """Stand der Aufgabe in Paperless abfragen; bei Bedarf später erneut."""
    document = pending_document(document_id)
    if document is None or not document.paperless_sync_task:
        return "skipped"
    cfg = paperless.config()
    deadline = time.monotonic() + (POLL_SECONDS if poll_seconds is None else poll_seconds)
    file_checksums = None
    while True:
        try:
            try:
                state = paperless.task_state(cfg, document.paperless_sync_task)
            except paperless.PaperlessError as error:
                if error.status != 403:
                    raise
                # Ohne Recht auf Aufgaben: Dokument über die Prüfsumme finden
                if file_checksums is None:
                    with document.file.open("rb") as file:
                        file_checksums = paperless.checksums(file.read())
                found = paperless.find_by_checksum(cfg, file_checksums)
                state = paperless.TaskState(done=bool(found), document_id=found)
        except paperless.PaperlessError as error:
            fail(document_id, error)
            return "failed"
        except OSError as error:
            fail(document_id, _("The transfer failed: %(error)s") % {"error": error})
            return "failed"
        if state.done:
            return finish(document, state)
        if time.monotonic() + POLL_INTERVAL > deadline:
            break
        time.sleep(POLL_INTERVAL)
    started = document.paperless_sync_started or timezone.now()
    if timezone.now() - started > GIVE_UP_AFTER:
        fail(document_id, _("Paperless has not finished processing the file after an hour. If the document "
                            "appears in Paperless, enter its ID here by hand."))
        return "failed"
    schedule_follow_up(document_id)
    return "waiting"


def schedule_follow_up(document_id):
    from django_q.models import Schedule

    Schedule.objects.create(
        name=f"paperless-sync-{document_id}-{timezone.now().timestamp():.0f}",
        func="web.paperless_sync.follow",
        args=repr(document_id),
        schedule_type=Schedule.ONCE,
        repeats=1,
        next_run=timezone.now() + timedelta(minutes=FOLLOW_UP_MINUTES),
    )


def finish(document, state):
    if state.error:
        fail(document.pk, state.error)
        return "failed"
    duplicate = Document.objects.filter(
        contract=document.contract, paperless_document_id=state.document_id,
    ).exclude(pk=document.pk).exists()
    if duplicate:
        fail(document.pk, _("Paperless document #%(id)s is already linked to this contract.") % {
            "id": state.document_id})
        return "failed"
    try:
        with transaction.atomic():
            Document.objects.filter(pk=document.pk).update(
                paperless_document_id=state.document_id, paperless_sync="", paperless_sync_error="",
            )
    except IntegrityError:
        fail(document.pk, _("Paperless document #%(id)s is already linked to this contract.") % {
            "id": state.document_id})
        return "failed"
    return f"document {state.document_id}"
