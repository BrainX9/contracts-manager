from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_delete, post_migrate, post_save, pre_save
from django.utils.translation import gettext_lazy as _


# Globale Rechte der Gruppe "Benutzer": nur Anlegen. Globale Rechte zum
# Lesen oder Ändern werden bewusst nicht vergeben, sie würden für alle
# Objekte gelten und die Freigaben pro Objekt aushebeln.
USERS_GROUP_PERMISSIONS = [
    "add_contract",
    "add_costentry",
    "add_provider",
    "add_category",
    "add_consumer",
    "add_paymentmethod",
]


# Globale Rechte der Gruppe "Administratoren" (Version 0.6 beta): Benutzer
# und Gruppen im Frontend pflegen. Das Django-Backend bleibt Superusern
# vorbehalten (config.admin.AdminSite).
ADMINS_GROUP_PERMISSIONS = [
    f"{action}_{model}" for model in ("user", "group") for action in ("view", "add", "change", "delete")
]


def ensure_default_groups(sender, **kwargs):
    """
    Legt die Gruppen "Benutzer", "Alle" und "Administratoren" an und setzt
    ihre Rechte. Läuft nach jeder Migration und ist wiederholbar.
    """
    from django.contrib.auth.models import Group, Permission

    from .permissions import GROUP_ADMINS, GROUP_ALL, GROUP_USERS

    Group.objects.get_or_create(name=GROUP_ALL)
    users_group, _ = Group.objects.get_or_create(name=GROUP_USERS)
    permissions = Permission.objects.filter(
        content_type__app_label="contracts",
        codename__in=USERS_GROUP_PERMISSIONS,
    )
    users_group.permissions.add(*permissions)
    admins_group, _ = Group.objects.get_or_create(name=GROUP_ADMINS)
    admins_group.permissions.add(*Permission.objects.filter(
        content_type__app_label="auth", codename__in=ADMINS_GROUP_PERMISSIONS,
    ))


def add_new_user_to_all(sender, instance, created, raw=False, **kwargs):
    """
    Neue Benutzer kommen automatisch in die Gruppe "Alle".
    """
    if not created or raw:
        return
    from django.contrib.auth.models import Group

    from .permissions import GROUP_ALL

    group, _ = Group.objects.get_or_create(name=GROUP_ALL)
    instance.groups.add(group)


REMINDER_SCHEDULE_NAME = "contracts.send_due_reminders"


def next_send_time(send_time, now=None):
    """
    Nächster Zeitpunkt (lokale Zeit) für "HH:MM": heute, falls noch nicht
    vorbei, sonst morgen.
    """
    from datetime import datetime, time, timedelta

    from django.utils import timezone

    hour, minute = (int(part) for part in send_time.split(":"))
    now = timezone.localtime(now)
    run = timezone.make_aware(datetime.combine(now.date(), time(hour, minute)))
    return run if run > now else run + timedelta(days=1)


def ensure_reminder_schedule(sender, **kwargs):
    """
    Legt den täglichen Versand der Erinnerungen im Worker an (django-q2)
    bzw. passt die Uhrzeit an REMINDER_SEND_TIME an. Läuft nach jeder
    Migration und ist wiederholbar.
    """
    from django.utils import timezone
    from django_q.models import Schedule

    send_time = settings.REMINDER_SEND_TIME
    schedule, created = Schedule.objects.get_or_create(
        name=REMINDER_SCHEDULE_NAME,
        defaults={
            "func": "contracts.tasks.send_due_reminders",
            "schedule_type": Schedule.DAILY,
            "next_run": next_send_time(send_time),
        },
    )
    if not created:
        planned = timezone.localtime(schedule.next_run).strftime("%H:%M") if schedule.next_run else None
        if planned != send_time or schedule.schedule_type != Schedule.DAILY:
            schedule.func = "contracts.tasks.send_due_reminders"
            schedule.schedule_type = Schedule.DAILY
            schedule.next_run = next_send_time(send_time)
            schedule.save()


class ContractsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contracts'
    verbose_name = _("Contracts")

    def ready(self):
        post_migrate.connect(ensure_default_groups, sender=self)
        post_migrate.connect(ensure_reminder_schedule, sender=self)
        post_save.connect(
            add_new_user_to_all,
            sender=settings.AUTH_USER_MODEL,
            dispatch_uid="contracts_add_new_user_to_all",
        )

        # Logos der Vertragspartner und Icons der Zahlungsmethoden:
        # ersetzte und verwaiste Dateien löschen
        from . import logos
        from .models import PaymentMethod, Provider

        logos.connect_file_cleanup(Provider, "logo")
        logos.connect_file_cleanup(PaymentMethod, "icon")

        # Standard-Zahlungsmethoden (Version 0.2.0), siehe contracts.payment_defaults
        from . import payment_defaults

        post_migrate.connect(payment_defaults.after_migrate, sender=self)
        post_save.connect(
            payment_defaults.after_superuser_saved,
            sender=settings.AUTH_USER_MODEL,
            dispatch_uid="contracts_default_payment_methods",
        )

        # Dokumente (Version 0.7 beta): Datei mit dem Dokument löschen
        from . import documents
        from .models import Document

        post_delete.connect(
            documents.delete_file_with_document, sender=Document, dispatch_uid="contracts_delete_document_file",
        )
