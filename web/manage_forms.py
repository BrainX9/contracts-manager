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

    class Meta:
        model = SiteSettings
        fields = ["ai_enabled", "ai_provider", "ai_model", "ai_base_url", "ai_system_prompt"]
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
            "Only for OpenAI-compatible providers or a proxy, e.g. https://example.com/v1. "
            "Empty: value from the .env file."
        )
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

    def candidate(self):
        """Konfiguration aus den eingegebenen Werten (für den Test, ungespeichert)."""
        from types import SimpleNamespace

        from assistant import config as ai_config

        data = self.cleaned_data
        return ai_config.merge(SimpleNamespace(
            ai_enabled=data["ai_enabled"], ai_provider=data["ai_provider"], ai_model=data["ai_model"],
            ai_base_url=data["ai_base_url"], ai_api_key=self.chosen_api_key(),
            ai_system_prompt=data["ai_system_prompt"],
        ), self.env)

    def save(self, commit=True):
        self.instance.ai_api_key = self.chosen_api_key()
        return super().save(commit)


class BackupUploadForm(BootstrapFormMixin, forms.Form):
    """Backup zum Import hochladen (0.9 beta); geprüft wird in contracts.backup."""

    file = forms.FileField(label=_("Backup file (ZIP)"), widget=forms.ClearableFileInput(attrs={"accept": ".zip"}))
