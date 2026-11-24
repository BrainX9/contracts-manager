from django.db import migrations, models

# Version vor den System-E-Mails: bestehende Installationen kündigen damit die
# Neuigkeiten ab 0.11.0 per E-Mail an
PREVIOUS_VERSION = "0.10.1"
GROUP_SYSTEM_MAIL = "Systemmail"
GROUP_ADMINS = "Administratoren"


def existing_installation(apps, schema_editor):
    """
    Bestehende Installation (es gibt schon Benutzer): Gruppe "Systemmail" mit
    allen Superusern und Administratoren; die Neuigkeiten von 0.11.0 werden
    per E-Mail angekündigt. Eine neue Installation bleibt leer.
    """
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")
    SiteSettings = apps.get_model("web", "SiteSettings")
    if not User.objects.exists():
        return
    group, _created = Group.objects.get_or_create(name=GROUP_SYSTEM_MAIL)
    members = User.objects.filter(models.Q(is_superuser=True) | models.Q(groups__name=GROUP_ADMINS)).distinct()
    group.user_set.add(*members)
    site, _created = SiteSettings.objects.get_or_create(pk=1)
    if not site.announced_version:
        site.announced_version = PREVIOUS_VERSION
        site.save(update_fields=["announced_version"])


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('web', '0010_whats_new'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='announced_version',
            field=models.CharField(blank=True, editable=False, max_length=20,
                                   verbose_name="what's new email sent for version"),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='update_notified_version',
            field=models.CharField(blank=True, editable=False, max_length=20,
                                   verbose_name='update email sent for version'),
        ),
        migrations.RunPython(existing_installation, migrations.RunPython.noop),
    ]
