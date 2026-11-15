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
    (SiteSettings.load). Zugangsdaten stehen in der .env; einzige Ausnahme
    ist der API-Schlüssel des KI-Assistenten (Version 0.3.0), der nie
    angezeigt und nie ins Backup geschrieben wird.
    """
    AI_ON = "on"
    AI_OFF = "off"
    AI_ENABLED_CHOICES = [(AI_ON, _("On")), (AI_OFF, _("Off"))]
    AI_PROVIDER_CHOICES = [("openai", _("OpenAI or OpenAI-compatible")), ("anthropic", "Anthropic (Claude)")]

    sso_login_label = models.CharField(
        _("label of the SSO button"),
        max_length=60,
        blank=True,
        help_text=_("Shown on the login page. Leave empty for “SSO Login”."),
    )

    # Version 0.2.0: intern, siehe contracts.payment_defaults
    default_payment_methods_created = models.BooleanField(
        _("default payment methods created"), default=False, editable=False,
    )

    # Version 0.3.0: KI-Assistent. Ein leeres Feld bedeutet "Wert aus der
    # .env"; ausgewertet wird das nur in assistant.config.
    ai_enabled = models.CharField(_("AI assistant"), max_length=3, blank=True, choices=AI_ENABLED_CHOICES)
    ai_provider = models.CharField(_("provider"), max_length=20, blank=True, choices=AI_PROVIDER_CHOICES)
    ai_model = models.CharField(_("model"), max_length=200, blank=True)
    ai_base_url = models.CharField(_("address of the API (base URL)"), max_length=500, blank=True)
    ai_api_key = models.CharField(_("API key"), max_length=500, blank=True)
    ai_system_prompt = models.TextField(_("system prompt"), blank=True)

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
