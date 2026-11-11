from django.apps import AppConfig
from django.db.models.signals import post_delete, post_migrate
from django.utils.translation import gettext_lazy as _


def ensure_ai_group(sender, **kwargs):
    """
    Legt die Gruppe "KI" mit dem Recht assistant.use_assistant an. Läuft
    nach jeder Migration und ist wiederholbar.
    """
    from django.contrib.auth.models import Group, Permission

    from contracts.permissions import GROUP_AI

    group, _created = Group.objects.get_or_create(name=GROUP_AI)
    group.permissions.add(*Permission.objects.filter(
        content_type__app_label="assistant", codename="use_assistant",
    ))


class AssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assistant"
    verbose_name = _("AI assistant")

    def ready(self):
        post_migrate.connect(ensure_ai_group, sender=self)

        # Datei eines Anhangs mit der Unterhaltung löschen (Version 0.8b); die
        # an einen Vertrag gehängte Kopie (Dokument) bleibt
        from contracts.documents import delete_file_on_commit

        from .models import Attachment

        post_delete.connect(
            lambda sender, instance, **kwargs: delete_file_on_commit(instance.file),
            sender=Attachment, weak=False, dispatch_uid="assistant_delete_attachment_file",
        )
