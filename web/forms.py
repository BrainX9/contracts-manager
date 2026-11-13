import os
import unicodedata

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import Group
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from contracts import documents
from contracts.backup import PASSWORD_RESET_MARKER
from contracts import permissions as perms
from contracts.mail import build_mail, user_language
from contracts.models import (
    Category, Consumer, Contract, CostEntry, Document, PaymentMethod, Provider, Reminder,
    category_tree_order,
)

from .models import UserPreferences


class BootstrapFormMixin:
    """
    Setzt die CSS-Klassen von Bootstrap an allen Eingabefeldern.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                css = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = "form-select"
            else:
                css = "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css}".strip()

    def full_clean(self):
        super().full_clean()
        # Felder mit Fehlern rot markieren
        for name in self.errors:
            if name in self.fields:
                widget = self.fields[name].widget
                widget.attrs["class"] = f"{widget.attrs.get('class', '')} is-invalid".strip()


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    pass


class WebPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class WebPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    """
    Passwort zurücksetzen. Django sendet nur an aktive Benutzer mit lokalem
    Passwort und verrät nicht, ob es die Adresse gibt. Die E-Mail kommt aus
    den Vorlagen ``emails/password_reset*`` in der Sprache des Benutzers.

    Seit 0.9 beta auch an Benutzer, deren Passwort ein Import entfernt hat
    (``contracts.backup.PASSWORD_RESET_MARKER``). Benutzer, die nie ein
    lokales Passwort hatten (nur Single Sign-on), bleiben ausgenommen.
    """

    def get_users(self, email):
        yield from super().get_users(email)
        wanted = unicodedata.normalize("NFKC", email).casefold()
        candidates = get_user_model().objects.filter(
            email__iexact=email, is_active=True, password=PASSWORD_RESET_MARKER,
        )
        for user in candidates:
            if unicodedata.normalize("NFKC", user.email).casefold() == wanted:
                yield user

    def send_mail(self, subject_template_name, email_template_name, context, from_email,
                  to_email, html_email_template_name=None):
        reset_path = reverse("password_reset_confirm", kwargs={"uidb64": context["uid"], "token": context["token"]})
        context = {**context, "reset_url": f"{context['protocol']}://{context['domain']}{reset_path}"}
        build_mail("password_reset", context, [to_email], user_language(context["user"])).send()


class WebSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class PreferencesForm(BootstrapFormMixin, forms.ModelForm):
    """
    Persönliche Einstellungen: Sprache (UserPreferences) und die eigene
    E-Mail-Adresse (am Benutzer), z. B. für Erinnerungen und das
    Zurücksetzen des Passworts.
    """
    email = forms.EmailField(
        label=_("Email address"),
        required=False,
        help_text=_("Used for reminders and to reset your password."),
    )

    class Meta:
        model = UserPreferences
        fields = ["email", "language"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].initial = self.instance.user.email

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        user_model = get_user_model()
        if email and user_model.objects.filter(email__iexact=email).exclude(pk=self.instance.user_id).exists():
            raise forms.ValidationError(_("This email address is already used by another account."))
        return email

    def save(self, commit=True):
        preferences = super().save(commit=commit)
        user = preferences.user
        if user.email != self.cleaned_data["email"]:
            user.email = self.cleaned_data["email"]
            user.save(update_fields=["email"])
        return preferences


class DateInput(forms.DateInput):
    """
    Datumsfeld mit dem Datumswähler des Browsers (ISO-Format).
    """
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


class OwnedModelFormMixin:
    """
    Formulare für Objekte mit Eigentümer (Aufruf mit ``user=...``).

    - Neue Objekte gehören dem angemeldeten Benutzer.
    - Eindeutigkeit pro Eigentümer (z. B. owner + name) wird schon im
      Formular geprüft, obwohl ``owner`` kein Formularfeld ist.
    - ``limit_to_visible`` beschränkt Auswahllisten auf lesbare Objekte
      (plus den aktuellen Wert).
    """

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if self.instance.pk is None:
            self.instance.owner = user

    def limit_to_visible(self, name, model):
        current = getattr(self.instance, f"{name}_id", None)
        queryset = perms.visible_or_current(self.user, model.objects.all(), current)
        if model is Category:
            queryset = category_tree_order(queryset)
        else:
            queryset = queryset.order_by("name")
        self.fields[name].queryset = queryset

    def _get_validation_exclusions(self):
        # Django lässt Felder, die nicht im Formular stehen, bei der Prüfung
        # von UniqueConstraints weg. Der Eigentümer steht aber immer fest.
        exclude = super()._get_validation_exclusions()
        if self.instance.owner_id is not None:
            exclude.discard("owner")
        return exclude


class ContractForm(OwnedModelFormMixin, BootstrapFormMixin, forms.ModelForm):
    """
    Vertrag anlegen und bearbeiten. Auswahllisten zeigen nur lesbare
    Stammdaten (plus den aktuellen Wert). Neue Verträge gehören dem
    angemeldeten Benutzer.
    """

    class Meta:
        model = Contract
        fields = [
            "name", "short_description", "provider", "category", "consumer", "payment_method",
            "start_date", "term_value", "term_unit", "contract_number", "notes",
        ]
        widgets = {
            "start_date": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, model in (
            ("provider", Provider), ("category", Category), ("consumer", Consumer),
            ("payment_method", PaymentMethod),
        ):
            self.limit_to_visible(name, model)


class ProviderForm(OwnedModelFormMixin, BootstrapFormMixin, forms.ModelForm):
    """
    Vertragspartner anlegen und bearbeiten. Der Name ist pro Eigentümer
    eindeutig. Das Logo lässt sich hochladen, ersetzen oder entfernen.
    """
    remove_logo = forms.BooleanField(label=_("Remove logo"), required=False)

    class Meta:
        model = Provider
        fields = [
            "name", "customer_number", "contact_person", "email",
            "street", "postal_code", "city", "country",
            "paperless_correspondent_id", "logo", "notes",
        ]
        widgets = {
            "email": forms.EmailInput(attrs={"autocomplete": "off"}),
            "logo": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _("The owner already has a contract partner with this name."),
            },
        }

    def clean(self):
        cleaned_data = super().clean()
        # "Entfernen" gilt nur, wenn nicht gleichzeitig ein neues Logo kommt.
        # False leert das Feld (FileField.save_form_data).
        if cleaned_data.get("remove_logo") and "logo" not in self.files:
            cleaned_data["logo"] = False
        return cleaned_data


class ConsumerForm(OwnedModelFormMixin, BootstrapFormMixin, forms.ModelForm):
    """
    Verbraucher anlegen und bearbeiten. Der Name ist pro Eigentümer
    eindeutig.
    """

    class Meta:
        model = Consumer
        fields = ["name", "number", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _("The owner already has a consumer with this name."),
            },
        }


class PaymentMethodForm(OwnedModelFormMixin, BootstrapFormMixin, forms.ModelForm):
    """
    Zahlungsmethode anlegen und bearbeiten (Version 0.2.0). Der Name ist pro
    Eigentümer eindeutig. Das Icon lässt sich hochladen, ersetzen oder
    entfernen.
    """
    remove_icon = forms.BooleanField(label=_("Remove icon"), required=False)

    class Meta:
        model = PaymentMethod
        fields = ["name", "icon"]
        widgets = {
            "icon": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        }
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _("The owner already has a payment method with this name."),
            },
        }

    def clean(self):
        cleaned_data = super().clean()
        # "Entfernen" gilt nur, wenn nicht gleichzeitig ein neues Icon kommt
        if cleaned_data.get("remove_icon") and "icon" not in self.files:
            cleaned_data["icon"] = False
        return cleaned_data


class CategoryForm(OwnedModelFormMixin, BootstrapFormMixin, forms.ModelForm):
    """
    Kategorie anlegen und bearbeiten. Als Hauptkategorie stehen nur lesbare
    Hauptkategorien zur Wahl (nie die Kategorie selbst). Leer bedeutet:
    die Kategorie ist selbst eine Hauptkategorie.
    """

    class Meta:
        model = Category
        fields = ["name", "parent"]
        error_messages = {
            NON_FIELD_ERRORS: {
                "unique_together": _("The owner already has a category with this name in this main category."),
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mains = Category.objects.filter(parent__isnull=True)
        if self.instance.pk is not None:
            mains = mains.exclude(pk=self.instance.pk)
        parent = self.fields["parent"]
        parent.queryset = perms.visible_or_current(self.user, mains, self.instance.parent_id).order_by("name")
        parent.empty_label = _("– none (this is a main category) –")
        parent.help_text = _("Leave empty for a main category.")


class CostEntryForm(BootstrapFormMixin, forms.ModelForm):
    """
    Kosteneintrag eines Vertrags (der Vertrag kommt aus der Adresse).
    """

    class Meta:
        model = CostEntry
        fields = ["amount", "payment_interval", "valid_from", "document", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
            "valid_from": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, contract, **kwargs):
        super().__init__(*args, **kwargs)
        # Version 0.7 beta: Auswahl aus den Dokumenten dieses Vertrags
        self.instance.contract = contract
        field = self.fields["document"]
        field.queryset = contract.documents.order_by("name", "id")
        field.label_from_instance = document_label
        field.empty_label = _("No document")


def document_label(document):
    """
    Auswahltext eines Dokuments mit Quelle, z. B. "Rechnung 2026 (Upload)".
    """
    return f"{document.name} ({document.get_source_display()})"


class DocumentForm(BootstrapFormMixin, forms.ModelForm):
    """
    Dokument eines Vertrags (Version 0.7 beta). Beim Anlegen wird die
    Quelle gewählt (Upload oder Paperless), danach bleibt sie fest; eine
    hochgeladene Datei wird nicht ersetzt, sondern bei Bedarf neu angelegt.
    Ohne Namen gilt bei Uploads der Dateiname.
    """

    class Meta:
        model = Document
        fields = ["source", "name", "file", "paperless_document_id", "notes"]
        widgets = {
            "source": forms.HiddenInput,
            "notes": forms.Textarea(attrs={"rows": 2}),
            "file": forms.FileInput(attrs={"accept": ",".join(f".{ext}" for ext in documents.ALLOWED_EXTENSIONS)}),
        }

    def __init__(self, *args, contract, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.contract = contract
        self.fields["name"].required = False
        self.fields["name"].help_text = _("Leave empty to use the file name.")
        self.fields["file"].help_text = _("PDF, images, Office documents, text or emails, up to %(size)s.") % {
            "size": filesizeformat(documents.MAX_UPLOAD_SIZE),
        }
        if self.instance.pk:
            # Quelle und Datei nach dem Anlegen nicht mehr änderbar
            del self.fields["source"]
            del self.fields["file"]
            if self.instance.is_upload:
                del self.fields["paperless_document_id"]
            self.fields["name"].required = True
            self.fields["name"].help_text = ""
        elif self.data:
            # Felder der nicht gewählten Quelle ignorieren
            source = self.data.get(self.add_prefix("source"))
            if source == Document.Source.PAPERLESS:
                self.fields["file"].disabled = True
            elif source == Document.Source.UPLOAD:
                self.fields["paperless_document_id"].disabled = True

    def clean(self):
        cleaned_data = super().clean()
        source = cleaned_data.get("source", self.instance.source)
        if not cleaned_data.get("name") and "name" in self.fields:
            upload = cleaned_data.get("file")
            if source == Document.Source.UPLOAD and upload:
                stem = os.path.splitext(os.path.basename(upload.name))[0]
                cleaned_data["name"] = stem[: Document._meta.get_field("name").max_length]
            elif source == Document.Source.PAPERLESS and cleaned_data.get("paperless_document_id"):
                self.add_error("name", _("Please enter a name."))
        document_id = cleaned_data.get("paperless_document_id")
        if source == Document.Source.PAPERLESS and document_id:
            duplicates = self.instance.contract.documents.filter(paperless_document_id=document_id)
            if duplicates.exclude(pk=self.instance.pk).exists():
                self.add_error(
                    "paperless_document_id", _("This Paperless document is already linked to this contract."),
                )
        return cleaned_data



class ReminderForm(BootstrapFormMixin, forms.ModelForm):
    """
    Erinnerung eines Vertrags. Als Empfänger stehen nur Benutzer zur Wahl,
    die den Vertrag lesen dürfen; beim ersten Einrichten ist der Eigentümer
    vorausgewählt.
    """

    class Meta:
        model = Reminder
        fields = ["enabled", "remind_on", "lead_days", "reason", "recipients", "note"]
        widgets = {
            "remind_on": DateInput(),
            "recipients": forms.CheckboxSelectMultiple,
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        contract = self.instance.contract
        recipients = self.fields["recipients"]
        recipients.queryset = perms.users_who_can_view(contract)
        recipients.label_from_instance = self.recipient_label
        if self.instance.pk is None:
            recipients.initial = [contract.owner_id]
        self.fields["lead_days"].choices = [
            (days, ngettext("%(days)s day", "%(days)s days", days) % {"days": days})
            for days in range(Reminder.MAX_LEAD_DAYS + 1)
        ]

    @staticmethod
    def recipient_label(user):
        if user.email:
            return f"{user.get_username()} ({user.email})"
        return f"{user.get_username()} – {_('no email address')}"

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("enabled"):
            recipients = cleaned_data.get("recipients")
            if recipients is not None and not any(user.email for user in recipients):
                self.add_error("recipients", _("Please select at least one recipient with an email address."))
        return cleaned_data


ROLE_NONE = ""
ROLE_CHOICES = [
    (ROLE_NONE, _("No access")),
    (perms.READER, _("Reader")),
    (perms.EDITOR, _("Editor")),
]


class SharingForm(BootstrapFormMixin, forms.Form):
    """
    Eigentümer und Freigaben eines Objekts. Pro Benutzer und Gruppe genau
    eine Rolle (kein Zugriff, Leser, Bearbeiter), damit keine
    widersprüchlichen Kombinationen entstehen.
    """
    owner = forms.ModelChoiceField(queryset=None, label=_("Owner"), empty_label=None)

    def __init__(self, *args, obj, **kwargs):
        super().__init__(*args, **kwargs)
        self.obj = obj
        users = get_user_model().objects.filter(is_active=True).order_by("username")
        groups = Group.objects.order_by("name")
        self.fields["owner"].queryset = users
        self.fields["owner"].initial = obj.owner_id

        roles = perms.get_roles(obj)
        current = {}
        for role, entries in roles.items():
            for user in entries["users"]:
                current[("user", user.pk)] = role
            for group in entries["groups"]:
                current[("group", group.pk)] = role

        self.user_rows = []
        # Der Eigentümer hat immer vollen Zugriff und bekommt keine Auswahl
        for user in users.exclude(pk=obj.owner_id):
            name = f"user_{user.pk}"
            self.fields[name] = forms.ChoiceField(
                choices=ROLE_CHOICES, required=False, widget=forms.RadioSelect,
                label=user.get_username(), initial=current.get(("user", user.pk), ROLE_NONE),
            )
            self.user_rows.append((user, name))
        self.group_rows = []
        for group in groups:
            name = f"group_{group.pk}"
            self.fields[name] = forms.ChoiceField(
                choices=ROLE_CHOICES, required=False, widget=forms.RadioSelect,
                label=group.name, initial=current.get(("group", group.pk), ROLE_NONE),
            )
            self.group_rows.append((group, name))

    def user_fields(self):
        return [(user, self[name]) for user, name in self.user_rows]

    def group_fields(self):
        return [(group, self[name]) for group, name in self.group_rows]

    def save(self):
        """
        Setzt Rollen und ggf. den neuen Eigentümer. Bei einer Übergabe wird
        der bisherige Eigentümer automatisch Bearbeiter.
        """
        obj = self.obj
        targets = {perms.READER: {"users": [], "groups": []}, perms.EDITOR: {"users": [], "groups": []}}
        for user, name in self.user_rows:
            role = self.cleaned_data.get(name)
            if role in targets:
                targets[role]["users"].append(user)
        for group, name in self.group_rows:
            role = self.cleaned_data.get(name)
            if role in targets:
                targets[role]["groups"].append(group)
        perms.set_roles(
            obj,
            reader_users=targets[perms.READER]["users"],
            reader_groups=targets[perms.READER]["groups"],
            editor_users=targets[perms.EDITOR]["users"],
            editor_groups=targets[perms.EDITOR]["groups"],
        )
        perms.transfer_ownership(obj, self.cleaned_data["owner"])
        return obj
