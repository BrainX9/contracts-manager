from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
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
    (SiteSettings.load). Zugangsdaten stehen in der .env oder hier
    (``SECRET_FIELDS``: API-Schlüssel des KI-Assistenten seit 0.3.0,
    SMTP-Passwort und Client-Secret des SSO seit 0.4.0); sie werden nie
    angezeigt und nie ins Backup geschrieben.
    """
    # Zugangsdaten: nie anzeigen, nie exportieren, bei einem Import behalten
    SECRET_FIELDS = ("ai_api_key", "email_password", "sso_client_secret")
    ON = "on"
    OFF = "off"
    SWITCH_CHOICES = [(ON, _("On")), (OFF, _("Off"))]
    EMAIL_SECURITY_CHOICES = [
        ("starttls", _("STARTTLS (usually port 587)")),
        ("ssl", _("SSL/TLS (usually port 465)")),
        ("none", _("None (unencrypted)")),
    ]
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

    # Version 0.4.0: E-Mail, Erinnerungsversand und Single Sign-on. Auch hier
    # bedeutet ein leeres Feld "Wert aus der .env"; ausgewertet wird das nur
    # in web.site_config.
    email_host = models.CharField(_("SMTP server"), max_length=255, blank=True)
    email_port = models.PositiveIntegerField(
        _("port"), null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(65535)],
    )
    email_security = models.CharField(_("encryption"), max_length=10, blank=True, choices=EMAIL_SECURITY_CHOICES)
    email_user = models.CharField(_("SMTP user"), max_length=255, blank=True)
    email_password = models.CharField(_("SMTP password"), max_length=500, blank=True)
    email_sender = models.CharField(_("sender address"), max_length=255, blank=True)
    app_base_url = models.CharField(_("address of the app"), max_length=500, blank=True)
    reminder_send_time = models.CharField(_("send reminders daily at"), max_length=5, blank=True)

    sso_enabled = models.CharField(_("single sign-on"), max_length=3, blank=True, choices=SWITCH_CHOICES)
    sso_base_url = models.CharField(_("address of the SSO server"), max_length=500, blank=True)
    sso_client_id = models.CharField(_("client ID"), max_length=255, blank=True)
    sso_client_secret = models.CharField(_("client secret"), max_length=500, blank=True)
    sso_create_users = models.CharField(_("create unknown users"), max_length=3, blank=True, choices=SWITCH_CHOICES)
    sso_username_claim = models.CharField(_("claim with the user name"), max_length=100, blank=True)
    sso_scopes = models.CharField(_("scopes"), max_length=255, blank=True)
    sso_sign_algo = models.CharField(_("signing algorithm"), max_length=10, blank=True)
    sso_authorization_endpoint = models.CharField(_("authorization endpoint"), max_length=500, blank=True)
    sso_token_endpoint = models.CharField(_("token endpoint"), max_length=500, blank=True)
    sso_user_endpoint = models.CharField(_("user info endpoint"), max_length=500, blank=True)
    sso_jwks_endpoint = models.CharField(_("JWKS endpoint"), max_length=500, blank=True)

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
