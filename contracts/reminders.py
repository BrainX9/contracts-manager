"""
Versand der Erinnerungen (Version 0.5 beta).

Läuft täglich als Aufgabe im Worker (contracts.tasks, django-q2) und lässt
sich von Hand starten: ``python manage.py send_reminders``.

- Fällig ist eine Erinnerung jeden Tag ab ``remind_on - lead_days`` bis
  einschließlich ``remind_on``, höchstens einmal pro Tag (``last_sent_on``).
- Empfänger bekommen die E-Mail nur, wenn sie aktiv sind, eine
  E-Mail-Adresse haben und den Vertrag (noch) lesen dürfen.
- Ab dem Erinnerungsdatum wird die Erinnerung ausgeschaltet und als
  erledigt markiert. Verpasste Tage werden nicht nachgeholt.
"""

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import permissions as perms
from .mail import absolute_url, send_mail_to_user
from .models import Reminder

logger = logging.getLogger(__name__)


@dataclass
class Result:
    sent: int = 0          # verschickte E-Mails
    reminders: int = 0     # Erinnerungen mit mindestens einer E-Mail
    completed: int = 0     # erledigte (ausgeschaltete) Erinnerungen
    failed: int = 0        # E-Mails mit Fehler beim Versand
    skipped: list = field(default_factory=list)  # Empfänger ohne Recht oder Adresse

    def as_dict(self):
        """
        Zähler ohne Text, damit die Anzeige (Verwaltung › System) sie in der
        Sprache des Benutzers formulieren kann.
        """
        return {"sent": self.sent, "reminders": self.reminders, "failed": self.failed, "completed": self.completed}

    def __str__(self):
        return (
            f"{self.sent} E-Mail(s) zu {self.reminders} Erinnerung(en) verschickt, "
            f"{self.failed} Fehler, {self.completed} erledigt"
        )


def reminder_context(reminder, today):
    contract = reminder.contract
    return {
        "reminder": reminder,
        "contract": contract,
        "days_left": (reminder.remind_on - today).days,
        "contract_url": absolute_url(reverse("contract_detail", args=[contract.pk])),
    }


def send_reminder(reminder, today, result):
    """
    Schickt die Erinnerung an alle berechtigten Empfänger. Gibt True zurück,
    wenn mindestens eine E-Mail verschickt wurde.
    """
    contract = reminder.contract
    context = reminder_context(reminder, today)
    any_sent = False
    for user in reminder.recipients.all():
        if not (user.is_active and user.email and perms.can_view(user, contract)):
            result.skipped.append(user.get_username())
            continue
        try:
            send_mail_to_user("reminder", context, user)
        except Exception:
            # Ein fehlerhafter Empfänger oder Server soll den Lauf nicht abbrechen
            logger.exception("Erinnerung %s an %s konnte nicht verschickt werden", reminder.pk, user)
            result.failed += 1
            continue
        result.sent += 1
        any_sent = True
    return any_sent


def send_due_reminders(today=None):
    """
    Verschickt alle heute fälligen Erinnerungen und schaltet abgelaufene aus.
    Gibt ein Result mit Zählern zurück.
    """
    today = today or timezone.localdate()
    result = Result()
    candidates = Reminder.objects.filter(enabled=True, remind_on__isnull=False).values_list("pk", flat=True)
    for pk in list(candidates):
        # Jede Erinnerung einzeln sperren, damit ein paralleler Lauf
        # (Worker und Befehl) nicht doppelt verschickt
        with transaction.atomic():
            reminder = (
                Reminder.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("contract__provider")
                .filter(pk=pk, enabled=True)
                .first()
            )
            if reminder is None:
                continue
            changed = []
            if reminder.is_due(today) and send_reminder(reminder, today, result):
                reminder.last_sent_on = today
                changed.append("last_sent_on")
                result.reminders += 1
            if reminder.remind_on <= today:
                reminder.enabled = False
                reminder.completed_on = today
                changed += ["enabled", "completed_on"]
                result.completed += 1
            if changed:
                # save() ohne Validierung, nur die geänderten Felder
                Reminder.objects.filter(pk=reminder.pk).update(**{name: getattr(reminder, name) for name in changed})
    logger.info("Erinnerungen: %s", result)
    return result
