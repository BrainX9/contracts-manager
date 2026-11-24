"""
System-E-Mails (Version 0.11.0).

Empfänger sind die aktiven Mitglieder der Standardgruppe "Systemmail" mit
E-Mail-Adresse; die Gruppe pflegen Administratoren. Bisher zwei E-Mails:

- ``system_update``: eine neue Version ist verfügbar. Nur an Superuser
  (nur sie können updaten), einmal je Version, nach der täglichen
  Update-Prüfung (``web.tasks.check_for_updates``).
- ``system_whats_new``: nach einem Update die Neuigkeiten wie im Fenster
  (``web.whats_new``), einmal je Version, kurz nach dem Start
  (``web.tasks.announce_release``, Zeitplan aus ``ensure_announce_schedule``).

Welche Version schon gemeldet ist, steht in ``SiteSettings`` und wird vor
dem Versand per bedingtem Update gesetzt, damit nie doppelt verschickt
wird. Ein Fehler beim Versand an einen Empfänger hält die anderen nicht auf.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from contracts.mail import absolute_url, send_mail_to_user
from contracts.permissions import GROUP_SYSTEM_MAIL

from . import update_check, whats_new

logger = logging.getLogger(__name__)

OPERATIONS_URL = "https://github.com/BrainX9/contracts-manager/blob/main/docs/operations.md#update"


def recipients(superusers_only=False):
    users = (
        get_user_model().objects.filter(is_active=True, groups__name=GROUP_SYSTEM_MAIL)
        .exclude(email="").select_related("preferences").order_by("pk").distinct()
    )
    return users.filter(is_superuser=True) if superusers_only else users


def claim(field, expected, value):
    """
    Setzt ``SiteSettings.<field>`` von ``expected`` auf ``value``. False,
    wenn ein anderer Prozess schneller war (dann nichts verschicken).
    """
    from .models import SiteSettings

    SiteSettings.load()
    return SiteSettings.objects.filter(pk=1, **{field: expected}).update(**{field: value}) == 1


def send_each(name, users, context_for):
    """Sendet an jeden Benutzer; Ergebnis: Anzahl der verschickten E-Mails."""
    sent = 0
    for user in users:
        context = context_for(user)
        if context is None:
            continue
        try:
            send_mail_to_user(name, context, user)
        except Exception as error:  # noqa: BLE001 (SMTP-Fehler: die anderen trotzdem versuchen)
            logger.warning("System-E-Mail %s an %s fehlgeschlagen: %s", name, user.get_username(), error)
            continue
        sent += 1
    return sent


def notify_update(latest):
    """
    E-Mail "Neue Version verfügbar" an die Superuser der Gruppe, einmal je
    Version. Ergebnis: Anzahl der E-Mails.
    """
    from .models import SiteSettings

    if not update_check.newer_than_installed(latest):
        return 0
    notified = SiteSettings.load().update_notified_version
    if notified == latest or not claim("update_notified_version", notified, latest):
        return 0
    context = {
        "version": latest,
        "installed_version": settings.APP_VERSION,
        "release_url": update_check.release_url(latest),
        "operations_url": OPERATIONS_URL,
    }
    return send_each("system_update", recipients(superusers_only=True), lambda user: context)


def announce_release():
    """
    E-Mail mit den Neuigkeiten seit der zuletzt angekündigten Version (wie
    das Fenster, höchstens ``whats_new.MAX_PENDING`` Versionen). Neue
    Installation (nichts angekündigt) oder ältere Version: nur merken.
    Ergebnis: Anzahl der E-Mails.
    """
    from .models import SiteSettings

    current = settings.APP_VERSION
    announced = SiteSettings.load().announced_version
    if announced == current or not claim("announced_version", announced, current):
        return 0
    if not announced or whats_new.version_key(announced) > whats_new.version_key(current):
        return 0
    page_url = absolute_url(reverse("whats_new"))

    def context_for(user):
        entries = whats_new.entries_for(user, announced, limit=whats_new.MAX_PENDING)
        if not entries:
            return None
        return {"entries": entries, "version": current, "whats_new_url": page_url}

    return send_each("system_whats_new", recipients(), context_for)


def add_first_admin(sender, instance, created, raw=False, **kwargs):
    """
    Der erste Benutzer einer neuen Installation (Superuser über die
    Einrichtungsseite oder createsuperuser) kommt in "Systemmail".
    """
    if not created or raw or not instance.is_superuser:
        return
    if get_user_model().objects.exclude(pk=instance.pk).exists():
        return
    from django.contrib.auth.models import Group

    group, _created = Group.objects.get_or_create(name=GROUP_SYSTEM_MAIL)
    instance.groups.add(group)
