"""
Version 0.3a beta: Datenmigration für Berechtigungen.

- Vorhandene Objekte ohne Eigentümer bekommen den ersten Superuser
  (oder, falls es keinen gibt, den ersten Benutzer) als Eigentümer.
- Legt die Gruppen "Benutzer" und "Alle" an. Vorhandene Benutzer kommen
  in "Alle". Die Rechte der Gruppe "Benutzer" setzt contracts.apps nach
  jeder Migration (post_migrate), da Rechte erst danach existieren.
"""

from django.conf import settings
from django.db import migrations

OWNED_MODELS = ["Provider", "Category", "Consumer", "Contract"]

# Feste Namen in der Migration (unabhängig von späteren Änderungen am Code)
GROUP_USERS = "Benutzer"
GROUP_ALL = "Alle"


def assign_owner_and_groups(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    Group = apps.get_model("auth", "Group")

    without_owner = [
        apps.get_model("contracts", name).objects.filter(owner__isnull=True)
        for name in OWNED_MODELS
    ]
    if any(qs.exists() for qs in without_owner):
        owner = (
            User.objects.filter(is_superuser=True).order_by("pk").first()
            or User.objects.order_by("pk").first()
        )
        if owner is None:
            raise RuntimeError(
                "Es gibt Objekte, aber keinen Benutzer, der Eigentümer werden "
                "kann. Bitte zuerst mit 'createsuperuser' einen Benutzer anlegen."
            )
        for qs in without_owner:
            qs.update(owner=owner)

    Group.objects.get_or_create(name=GROUP_USERS)
    group_all, _ = Group.objects.get_or_create(name=GROUP_ALL)
    group_all.user_set.add(*User.objects.all())


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0005_owner_and_object_permissions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Rückweg: nichts zu tun, die Felder entfernt die Migration 0005
        migrations.RunPython(assign_owner_and_groups, migrations.RunPython.noop),
    ]
