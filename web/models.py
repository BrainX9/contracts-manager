from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserPreferences(models.Model):
    """
    Persönliche Einstellungen eines Benutzers, z. B. die Sprache der Oberfläche.
    Wird bei Bedarf angelegt (siehe UserPreferences.for_user).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preferences",
        verbose_name=_("user"),
    )
    language = models.CharField(
        _("language"),
        max_length=10,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
    )

    class Meta:
        verbose_name = _("user preferences")
        verbose_name_plural = _("user preferences")

    def __str__(self):
        return str(self.user)

    @classmethod
    def for_user(cls, user):
        preferences, _created = cls.objects.get_or_create(user=user)
        return preferences


class SiteSettings(models.Model):
    """
    Einstellungen der Anwendung, die Administratoren unter Verwaltung ›
    System ändern (Version 0.7 beta). Es gibt genau einen Datensatz
    (SiteSettings.load). Zugangsdaten gehören nicht hierher, sondern in .env.
    """
    sso_login_label = models.CharField(
        _("label of the SSO button"),
        max_length=60,
        blank=True,
        help_text=_("Shown on the login page. Leave empty for “SSO Login”."),
    )

    class Meta:
        verbose_name = _("application settings")
        verbose_name_plural = _("application settings")

    def __str__(self):
        return str(_("Application settings"))

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        settings_obj, _created = cls.objects.get_or_create(pk=1)
        return settings_obj

    @property
    def sso_button_label(self):
        return self.sso_login_label.strip() or _("SSO Login")
