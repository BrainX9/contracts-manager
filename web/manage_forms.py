"""
Formulare für den Bereich Administration (Version 0.6 beta): Benutzer,
Gruppen, Eigentum übertragen. Grenzen für Administratoren stehen in
contracts.permissions (can_edit_user, can_change_status, assignable_groups).
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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


class BackupUploadForm(BootstrapFormMixin, forms.Form):
    """Backup zum Import hochladen (0.9 beta); geprüft wird in contracts.backup."""

    file = forms.FileField(label=_("Backup file (ZIP)"), widget=forms.ClearableFileInput(attrs={"accept": ".zip"}))
