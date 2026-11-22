"""
Formulare für den Bereich Administration (Version 0.6 beta): Benutzer,
Gruppen, Eigentum übertragen. Grenzen für Administratoren stehen in
contracts.permissions (can_edit_user, can_change_status, assignable_groups).
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _

from contracts import permissions as perms

from .forms import BootstrapFormMixin
from .models import SiteSettings

User = get_user_model()


class UserForm(BootstrapFormMixin, forms.ModelForm):
    """
    Benutzer anlegen oder bearbeiten (Aufruf mit ``actor=...``).

    - Gruppen: nur die, die der Bearbeiter vergeben darf; andere bleiben
      erhalten ("Alle" automatisch, "Administratoren" nur durch Superuser).
    - Aktiv und Gruppen nicht am eigenen Konto, Superuser-Status nur durch
      Superuser.
    - Neue Benutzer haben kein lokales Passwort; mit Einladung wählen sie
      es selbst, sonst melden sie sich nur über SSO an.
    """
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
        label=_("Groups"),
    )
    send_invitation = forms.BooleanField(
        label=_("Send invitation by email"), required=False, initial=True,
        help_text=_("The user receives a link to choose a password (valid for 7 days). "
                    "Without an invitation the user can only sign in via single sign-on."),
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active", "is_superuser"]
        labels = {"is_superuser": _("Superuser (all rights, also in the Django backend)")}

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        target = self.instance
        is_new = target.pk is None
        self.fields["email"].required = False
        self.fields["groups"].queryset = perms.assignable_groups(actor)
        if not is_new:
            self.fields["groups"].initial = list(target.groups.values_list("pk", flat=True))
            del self.fields["send_invitation"]
        if is_new or not perms.can_change_status(actor, target):
            del self.fields["is_active"]
        if not is_new and not perms.can_change_status(actor, target):
            del self.fields["groups"]
        if not actor.is_superuser or (not is_new and target.pk == actor.pk):
            del self.fields["is_superuser"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("This email address is already used by another account."))
        return email

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("send_invitation") and not cleaned_data.get("email"):
            self.add_error("email", _("An email address is required for the invitation."))
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.pk is None:
            user.set_unusable_password()
        if "is_superuser" in self.fields:
            # Für das Django-Backend (Anmeldeformular dort prüft is_staff)
            user.is_staff = user.is_superuser
        user.save()
        if "groups" in self.fields:
            assignable = set(self.fields["groups"].queryset)
            kept = [group for group in user.groups.all() if group not in assignable]
            user.groups.set(kept + list(self.cleaned_data["groups"]))
        return user


class GroupForm(BootstrapFormMixin, forms.ModelForm):
    """
    Gruppe anlegen oder bearbeiten (Aufruf mit ``actor=...``). Standard-
    gruppen behalten ihren Namen. Mitglieder: nur Benutzer, deren Gruppen
    der Bearbeiter ändern darf; alle anderen Mitgliedschaften bleiben.
    """
    members = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
        label=_("Members"),
    )

    class Meta:
        model = Group
        fields = ["name"]

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        group = self.instance
        if group.name in perms.PROTECTED_GROUPS:
            self.fields["name"].disabled = True
            self.fields["name"].help_text = _("Default groups cannot be renamed.")
        self.fields["members"].queryset = self.editable_members()
        self.fields["members"].label_from_instance = lambda user: (
            f"{user.get_username()} ({user.get_full_name()})" if user.get_full_name() else user.get_username()
        )
        if group.pk:
            self.fields["members"].initial = list(group.user_set.values_list("pk", flat=True))

    def editable_members(self):
        candidates = User.objects.filter(is_active=True).exclude(pk=self.actor.pk).order_by("username")
        if not self.actor.is_superuser:
            candidates = candidates.exclude(is_superuser=True)
        return candidates

    def save(self, commit=True):
        group = super().save(commit=True)
        editable = set(self.fields["members"].queryset)
        kept = [user for user in group.user_set.all() if user not in editable]
        group.user_set.set(kept + list(self.cleaned_data["members"]))
        return group


class TransferOwnershipForm(BootstrapFormMixin, forms.Form):
    """
    Alle Objekte eines Benutzers an einen anderen übertragen (nur Superuser).
    """
    new_owner = forms.ModelChoiceField(queryset=User.objects.none(), label=_("New owner"))
    keep_access = forms.BooleanField(
        label=_("Previous owner becomes editor"), required=False,
        help_text=_("Leave empty if the user is to be deleted afterwards."),
    )

    def __init__(self, *args, from_user, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_user = from_user
        self.fields["new_owner"].queryset = (
            User.objects.filter(is_active=True).exclude(pk=from_user.pk).order_by("username")
        )

    def clean(self):
        cleaned_data = super().clean()
        new_owner = cleaned_data.get("new_owner")
        if new_owner:
            conflicts = perms.ownership_conflicts(self.from_user, new_owner)
            if conflicts:
                names = ", ".join(str(obj) for obj in conflicts)
                raise forms.ValidationError(
                    _("The new owner already has objects with these names: %(names)s. "
                      "Rename them first.") % {"names": names}
                )
        return cleaned_data


class SiteSettingsForm(BootstrapFormMixin, forms.ModelForm):
    """
    Einstellungen der Anwendung unter Verwaltung › System (Version 0.7 beta).
    """

    class Meta:
        model = SiteSettings
        fields = ["sso_login_label"]
        widgets = {"sso_login_label": forms.TextInput(attrs={"placeholder": _("SSO Login")})}


class AISettingsForm(BootstrapFormMixin, forms.ModelForm):
    """
    KI-Assistent unter Verwaltung › KI-Einstellungen (Version 0.3.0, nur
    Superuser). Ein leeres Feld bedeutet "Wert aus der .env"
    (assistant.config). Der API-Schlüssel wird gespeichert, aber nie wieder
    angezeigt: leer lassen = unverändert.
    """
    MAX_PROMPT_LENGTH = 20_000

    api_key = forms.CharField(
        label=_("API key"), required=False, max_length=500,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    clear_api_key = forms.BooleanField(label=_("Remove the stored API key"), required=False)
    # Dokument-KI (Version 0.6.0): eigener Schlüssel, ebenfalls nie angezeigt
    reader_key = forms.CharField(
        label=_("API key"), required=False, max_length=500,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    clear_reader_key = forms.BooleanField(label=_("Remove the stored API key"), required=False)

    class Meta:
        model = SiteSettings
        fields = [
            "ai_enabled", "ai_provider", "ai_model", "ai_base_url", "ai_system_prompt",
            "reader_provider", "reader_model", "reader_base_url", "reader_kind",
        ]
        widgets = {"ai_system_prompt": forms.Textarea(attrs={"rows": 20, "spellcheck": "false"})}

    def __init__(self, *args, **kwargs):
        from assistant import config as ai_config
        from assistant.agent import DEFAULT_PROMPT

        super().__init__(*args, **kwargs)
        self.env = env = ai_config.from_env()
        self.default_prompt = DEFAULT_PROMPT
        as_env = _("As in the .env file (%(value)s)")
        self.fields["ai_enabled"].choices = [
            ("", as_env % {"value": _("on") if env.enabled else _("off")}),
            *SiteSettings.AI_ENABLED_CHOICES,
        ]
        self.fields["ai_provider"].choices = [
            ("", as_env % {"value": env.provider}), *SiteSettings.AI_PROVIDER_CHOICES,
        ]
        self.fields["ai_model"].widget.attrs["placeholder"] = env.model or "claude-sonnet-5-5"
        self.fields["ai_base_url"].widget.attrs["placeholder"] = env.base_url or _("Default address of the provider")
        self.fields["ai_model"].help_text = _("Empty: value from the .env file.")
        self.fields["ai_base_url"].help_text = _(
            "Only for OpenAI-compatible providers, Ollama or a proxy, e.g. https://example.com/v1 or "
            "http://192.168.1.20:11434/v1 for Ollama on another computer. Empty: for Mistral AI "
            "https://api.mistral.ai/v1, for Ollama the Docker host (host.docker.internal), otherwise "
            "the value from the .env file."
        )
        # Dokument-KI: leere Felder gelten wie beim Hauptmodell
        self.fields["reader_kind"].required = False
        self.fields["reader_provider"].choices = [("", _("Same as the main model")), *SiteSettings.READER_PROVIDER_CHOICES]
        self.fields["reader_model"].help_text = _(
            "Empty: no document AI, scans and photos are read with the local text recognition. "
            "Example Mistral: mistral-ocr-latest (type Mistral OCR). Example IONOS: lightonai/LightOnOCR-2-1B "
            "(OCR model)."
        )
        self.fields["reader_base_url"].help_text = _(
            "Empty: the address of the main model (Ollama: the Docker host)."
        )
        if self.instance.reader_api_key:
            self.fields["reader_key"].help_text = _("A key is stored. Leave empty to keep it.")
        else:
            del self.fields["clear_reader_key"]
            self.fields["reader_key"].help_text = _("Empty: the key of the main model (same provider).")
        if self.instance.ai_api_key:
            self.fields["api_key"].help_text = _("A key is stored. Leave empty to keep it.")
        else:
            del self.fields["clear_api_key"]
            self.fields["api_key"].help_text = (
                _("No key is stored here; the key from the .env file is used.") if env.api_key
                else _("No key is set yet.")
            )
        # Das Fenster zeigt immer einen Text: den eigenen oder den Standard
        if not self.instance.ai_system_prompt:
            self.initial["ai_system_prompt"] = DEFAULT_PROMPT

    def clean_ai_base_url(self):
        value = self.cleaned_data["ai_base_url"].strip()
        if value:
            URLValidator(schemes=["http", "https"])(value)
        return value

    def clean_reader_kind(self):
        return self.cleaned_data.get("reader_kind") or "vision"

    def clean(self):
        cleaned = super().clean()
        # Mistral OCR ist ein Dienst von Mistral: nur mit dem Anbieter Mistral
        if cleaned.get("reader_kind") == "mistral_ocr" and cleaned.get("reader_model"):
            provider = cleaned.get("reader_provider") or cleaned.get("ai_provider") or self.env.provider
            if provider != "mistral":
                self.add_error("reader_kind", _("Mistral OCR only works with the provider Mistral AI."))
        return cleaned

    def clean_reader_base_url(self):
        value = self.cleaned_data["reader_base_url"].strip()
        if value:
            URLValidator(schemes=["http", "https"])(value)
        return value

    def clean_ai_system_prompt(self):
        value = self.cleaned_data["ai_system_prompt"].replace("\r\n", "\n").strip()
        if len(value) > self.MAX_PROMPT_LENGTH:
            raise forms.ValidationError(_("The system prompt may have at most 20,000 characters."))
        # Unveränderter Standard wird nicht gespeichert, damit spätere
        # Verbesserungen des Standards ankommen
        return "" if value == self.default_prompt else value

    def chosen_api_key(self):
        """Schlüssel nach dem Speichern: neu eingegeben, entfernt oder wie bisher."""
        if self.cleaned_data.get("api_key"):
            return self.cleaned_data["api_key"]
        if self.cleaned_data.get("clear_api_key"):
            return ""
        return self.instance.ai_api_key

    def chosen_reader_key(self):
        """Schlüssel der Dokument-KI nach dem Speichern: neu, entfernt oder wie bisher."""
        if self.cleaned_data.get("reader_key"):
            return self.cleaned_data["reader_key"]
        if self.cleaned_data.get("clear_reader_key"):
            return ""
        return self.instance.reader_api_key

    def candidate(self):
        """Konfiguration aus den eingegebenen Werten (für den Test, ungespeichert)."""
        from types import SimpleNamespace

        from assistant import config as ai_config

        data = self.cleaned_data
        return ai_config.merge(SimpleNamespace(
            ai_enabled=data["ai_enabled"], ai_provider=data["ai_provider"], ai_model=data["ai_model"],
            ai_base_url=data["ai_base_url"], ai_api_key=self.chosen_api_key(),
            ai_system_prompt=data["ai_system_prompt"],
            reader_provider=data["reader_provider"], reader_model=data["reader_model"],
            reader_base_url=data["reader_base_url"], reader_api_key=self.chosen_reader_key(),
            reader_kind=data["reader_kind"],
        ), self.env)

    def save(self, commit=True):
        self.instance.ai_api_key = self.chosen_api_key()
        self.instance.reader_api_key = self.chosen_reader_key()
        return super().save(commit)


class SecretFieldMixin:
    """
    Zugangsdaten in den Einstellungen (Version 0.4.0): gespeichert, aber nie
    wieder angezeigt. Das Formularfeld ``secret_field`` bleibt leer = Wert
    unverändert; ``clear_field`` (nur wenn etwas gespeichert ist) entfernt
    ihn. ``secret_model_field`` ist das Feld in SiteSettings.
    """
    secret_field = ""
    clear_field = ""
    secret_model_field = ""

    def setup_secret(self, env_value, stored_text, env_text, missing_text):
        if getattr(self.instance, self.secret_model_field):
            self.fields[self.secret_field].help_text = stored_text
        else:
            del self.fields[self.clear_field]
            self.fields[self.secret_field].help_text = env_text if env_value else missing_text

    def chosen_secret(self):
        """Wert nach dem Speichern: neu eingegeben, entfernt oder wie bisher."""
        if self.cleaned_data.get(self.secret_field):
            return self.cleaned_data[self.secret_field]
        if self.cleaned_data.get(self.clear_field):
            return ""
        return getattr(self.instance, self.secret_model_field)

    def save(self, commit=True):
        setattr(self.instance, self.secret_model_field, self.chosen_secret())
        return super().save(commit)


def clean_http_url(value):
    """Adresse mit http oder https, ohne Schrägstrich am Ende; leer bleibt leer."""
    value = (value or "").strip().rstrip("/")
    if value:
        URLValidator(schemes=["http", "https"])(value)
    return value


class EmailSettingsForm(SecretFieldMixin, BootstrapFormMixin, forms.ModelForm):
    """
    SMTP, Absender, Adresse der App und Uhrzeit der Erinnerungen unter
    Verwaltung › E-Mail (Version 0.4.0, nur Superuser). Ein leeres Feld
    bedeutet "Wert aus der .env" (web.site_config).
    """
    secret_field = "password"
    clear_field = "clear_password"
    secret_model_field = "email_password"

    password = forms.CharField(
        label=_("SMTP password"), required=False, max_length=500,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    clear_password = forms.BooleanField(label=_("Remove the stored password"), required=False)

    class Meta:
        model = SiteSettings
        fields = [
            "email_host", "email_port", "email_security", "email_user", "email_sender",
            "app_base_url", "reminder_send_time",
        ]
        widgets = {"reminder_send_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}

    def __init__(self, *args, **kwargs):
        from django.conf import settings

        from . import site_config

        super().__init__(*args, **kwargs)
        self.env = env = site_config.mail_from_env()
        empty = _("Empty: value from the .env file.")
        placeholders = {
            "email_host": env.host or "smtp.example.com",
            "email_port": env.port,
            "email_user": env.user,
            "email_sender": env.sender,
            "app_base_url": env.base_url or "https://contracts.example.com",
        }
        for name, placeholder in placeholders.items():
            self.fields[name].widget.attrs["placeholder"] = placeholder
            self.fields[name].help_text = empty
        self.fields["email_security"].choices = [
            ("", _("As in the .env file (%(value)s)") % {"value": env.security_label}),
            *SiteSettings.EMAIL_SECURITY_CHOICES,
        ]
        self.fields["email_sender"].help_text = _(
            "Address the emails come from, often the same as the SMTP user. Empty: value from the .env file."
        )
        self.fields["app_base_url"].help_text = _(
            "Public address of the app, used for links in emails. Empty: value from the .env file."
        )
        self.fields["reminder_send_time"].help_text = _(
            "Empty: value from the .env file (%(value)s)."
        ) % {"value": settings.REMINDER_SEND_TIME}
        self.setup_secret(
            env.password,
            _("A password is stored. Leave empty to keep it."),
            _("No password is stored here; the password from the .env file is used."),
            _("No password is set yet."),
        )

    def clean_email_host(self):
        value = self.cleaned_data["email_host"].strip()
        if value and (" " in value or "/" in value):
            raise forms.ValidationError(_("Enter the host name only, e.g. smtp.example.com."))
        return value

    def clean_email_sender(self):
        # Auch mit Anzeigename: Contracts Manager <contracts@example.com>
        from email.utils import parseaddr

        value = self.cleaned_data["email_sender"].strip()
        if value:
            forms.EmailField().clean(parseaddr(value)[1])
        return value

    def clean_app_base_url(self):
        return clean_http_url(self.cleaned_data["app_base_url"])

    def clean_reminder_send_time(self):
        value = (self.cleaned_data["reminder_send_time"] or "").strip()
        if not value:
            return ""
        try:
            hour, minute = (int(part) for part in value.split(":")[:2])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            raise forms.ValidationError(_("Enter a time such as 07:00."))
        return f"{hour:02d}:{minute:02d}"

    def candidate(self):
        """Konfiguration aus den eingegebenen Werten (für den Test, ungespeichert)."""
        from types import SimpleNamespace

        from . import site_config

        data = self.cleaned_data
        return site_config.merge_mail(SimpleNamespace(
            email_host=data["email_host"], email_port=data["email_port"],
            email_security=data["email_security"], email_user=data["email_user"],
            email_password=self.chosen_secret(), email_sender=data["email_sender"],
            app_base_url=data["app_base_url"],
        ), self.env)


class SSOSettingsForm(SecretFieldMixin, BootstrapFormMixin, forms.ModelForm):
    """
    Single Sign-on unter Verwaltung › Single Sign-on (Version 0.4.0, nur
    Superuser). Ein leeres Feld bedeutet "Wert aus der .env"
    (web.site_config).
    """
    secret_field = "client_secret"
    clear_field = "clear_client_secret"
    secret_model_field = "sso_client_secret"
    SIGN_ALGOS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "HS256")
    ENDPOINTS = ("authorization_endpoint", "token_endpoint", "user_endpoint", "jwks_endpoint")

    client_secret = forms.CharField(
        label=_("Client secret"), required=False, max_length=500,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    clear_client_secret = forms.BooleanField(label=_("Remove the stored client secret"), required=False)

    class Meta:
        model = SiteSettings
        fields = [
            "sso_enabled", "sso_base_url", "sso_client_id", "sso_create_users", "sso_login_label",
            "sso_username_claim", "sso_scopes", "sso_sign_algo",
            "sso_authorization_endpoint", "sso_token_endpoint", "sso_user_endpoint", "sso_jwks_endpoint",
        ]
        widgets = {"sso_login_label": forms.TextInput(attrs={"placeholder": _("SSO Login")})}

    def __init__(self, *args, **kwargs):
        from . import site_config

        super().__init__(*args, **kwargs)
        self.env = env = site_config.sso_from_env()
        as_env = _("As in the .env file (%(value)s)")
        empty = _("Empty: value from the .env file.")
        for name, value in (("sso_enabled", env.enabled), ("sso_create_users", env.create_users)):
            self.fields[name].choices = [
                ("", as_env % {"value": _("on") if value else _("off")}), *SiteSettings.SWITCH_CHOICES,
            ]
        self.fields["sso_create_users"].help_text = _(
            "On: a user who signs in for the first time is created automatically, "
            "without a local password and never as an administrator."
        )
        placeholders = {
            "sso_base_url": env.base_url or "https://nas.example.com",
            "sso_client_id": env.client_id,
            "sso_username_claim": env.username_claim,
            "sso_scopes": env.scopes,
            "sso_sign_algo": env.sign_algo,
        }
        for name, placeholder in placeholders.items():
            self.fields[name].widget.attrs["placeholder"] = placeholder
            self.fields[name].help_text = empty
        self.fields["sso_base_url"].help_text = _(
            "For the Synology SSO Server the address of the NAS, e.g. https://nas.example.com; "
            "the endpoints are derived from it. Empty: value from the .env file."
        )
        self.fields["sso_username_claim"].help_text = _(
            "Synology: “username”, other providers often “preferred_username”. Empty: value from the .env file."
        )
        for name in self.ENDPOINTS:
            field = self.fields[f"sso_{name}"]
            field.widget.attrs["placeholder"] = getattr(env, name)
            field.help_text = ""
        self.setup_secret(
            env.client_secret,
            _("A client secret is stored. Leave empty to keep it."),
            _("No client secret is stored here; the one from the .env file is used."),
            _("No client secret is set yet."),
        )

    def clean_sso_base_url(self):
        return clean_http_url(self.cleaned_data["sso_base_url"])

    def clean_sso_sign_algo(self):
        value = self.cleaned_data["sso_sign_algo"].strip().upper()
        if value and value not in self.SIGN_ALGOS:
            raise forms.ValidationError(
                _("Supported: %(values)s.") % {"values": ", ".join(self.SIGN_ALGOS)}
            )
        return value

    def clean(self):
        cleaned_data = super().clean()
        for name in self.ENDPOINTS:
            key = f"sso_{name}"
            if cleaned_data.get(key):
                try:
                    cleaned_data[key] = clean_http_url(cleaned_data[key])
                except forms.ValidationError as error:
                    self.add_error(key, error)
        if self.errors:
            return cleaned_data
        # Eingeschaltet nur mit vollständigen Angaben, sonst führt die
        # Schaltfläche auf der Anmeldeseite ins Leere
        candidate = self.candidate(cleaned_data)
        if candidate.enabled:
            missing = [
                label for label, value in (
                    (_("address of the SSO server or the endpoints"), candidate.authorization_endpoint.startswith("http")
                     and candidate.token_endpoint.startswith("http") and candidate.user_endpoint.startswith("http")
                     and candidate.jwks_endpoint.startswith("http")),
                    (_("client ID"), candidate.client_id),
                    (_("client secret"), candidate.client_secret),
                ) if not value
            ]
            if missing:
                raise forms.ValidationError(
                    _("Single sign-on can only be switched on with complete settings. Missing: %(missing)s.")
                    % {"missing": ", ".join(str(item) for item in missing)}
                )
        return cleaned_data

    def candidate(self, data=None):
        """Konfiguration aus den eingegebenen Werten (für den Test, ungespeichert)."""
        from types import SimpleNamespace

        from . import site_config

        data = data if data is not None else self.cleaned_data
        values = {name: data.get(name) or "" for name in self.Meta.fields if name != "sso_login_label"}
        return site_config.merge_sso(SimpleNamespace(**values, sso_client_secret=self.chosen_secret()), self.env)


class BackupUploadForm(BootstrapFormMixin, forms.Form):
    """Backup zum Import hochladen (0.9 beta); geprüft wird in contracts.backup."""

    file = forms.FileField(label=_("Backup file (ZIP)"), widget=forms.ClearableFileInput(attrs={"accept": ".zip"}))
