from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

UPDATE_SCHEDULE_NAME = "web.check_for_updates"


def ensure_update_schedule(sender=None, **kwargs):
    """
    Legt die tägliche Prüfung auf neue Versionen im Worker an (django-q2,
    Version 0.5.0). Läuft nach jeder Migration und ist wiederholbar; die
    erste Prüfung folgt kurz nach dem Start.
    """
    from datetime import timedelta

    from django.utils import timezone
    from django_q.models import Schedule

    Schedule.objects.get_or_create(
        name=UPDATE_SCHEDULE_NAME,
        defaults={
            "func": "web.tasks.check_for_updates",
            "schedule_type": Schedule.DAILY,
            "next_run": timezone.now() + timedelta(minutes=5),
        },
    )


class WebConfig(AppConfig):
    """
    Oberfläche (Frontend) mit Django-Templates und HTMX.
    Die Fachlogik und alle Berechtigungen liegen in der App "contracts".
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "web"
    verbose_name = _("Web interface")

    def ready(self):
        from django.conf import settings
        from django.db.models.signals import post_migrate, post_save

        from . import env_import, whats_new

        post_migrate.connect(ensure_update_schedule, sender=self)
        # Version 0.10.0: neue Benutzer sehen keine Neuigkeiten älterer Versionen
        post_save.connect(whats_new.set_for_new_user, sender=settings.AUTH_USER_MODEL,
                          dispatch_uid="web_whats_new_new_user")
        # Version 0.9.0: Werte aus der .env einmalig in die Verwaltung übernehmen
        post_migrate.connect(env_import.run, sender=self)
