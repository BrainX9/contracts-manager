from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Q
from django.utils.formats import number_format
from django.utils.html import format_html
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from . import permissions as perms
from .models import (
    Category,
    CategoryGroupObjectPermission,
    CategoryUserObjectPermission,
    Consumer,
    ConsumerGroupObjectPermission,
    ConsumerUserObjectPermission,
    PaymentMethod,
    PaymentMethodGroupObjectPermission,
    PaymentMethodUserObjectPermission,
    Contract,
    ContractGroupObjectPermission,
    ContractUserObjectPermission,
    CostEntry,
    Document,
    Provider,
    ProviderGroupObjectPermission,
    Reminder,
    ProviderUserObjectPermission,
    total_current_costs,
)


def format_euro(amount):
    """
    Formatiert einen Betrag mit zwei Nachkommastellen im Format der aktiven
    Sprache, z. B. "1.234,50 €" (Deutsch) oder "1,234.50 €" (Englisch).
    """
    return f"{number_format(amount, decimal_pos=2, force_grouping=True)} €"


def paperless_link(url, label=None):
    """
    Link nach Paperless in neuem Tab, "–" wenn keine Adresse vorhanden ist.
    """
    if not url:
        return "–"
    label = label or gettext("Open in Paperless")
    return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', url, label)


# --- Berechtigungen im Admin (Version 0.3a beta) -----------------------------

# Formularfelder für die Freigaben: Feldname -> (Rolle, Benutzer oder Gruppe)
SHARING_FIELDS = {
    'reader_users': (perms.READER, 'users'),
    'reader_groups': (perms.READER, 'groups'),
    'editor_users': (perms.EDITOR, 'users'),
    'editor_groups': (perms.EDITOR, 'groups'),
}


def _user_choices():
    return get_user_model().objects.filter(is_active=True).order_by('username')


class SharingForm(forms.ModelForm):
    """
    Formular mit den Freigaben (Leser und Bearbeiter, Benutzer und Gruppen).
    Die Felder erscheinen nur für Eigentümer und Superuser (siehe OwnedAdmin).
    """
    reader_users = forms.ModelMultipleChoiceField(
        queryset=_user_choices(), required=False, label=_('Readers (users)'),
        widget=FilteredSelectMultiple(_('users'), is_stacked=False),
        help_text=_('May view the object.'),
    )
    reader_groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by('name'), required=False, label=_('Readers (groups)'),
        widget=FilteredSelectMultiple(_('groups'), is_stacked=False),
    )
    editor_users = forms.ModelMultipleChoiceField(
        queryset=_user_choices(), required=False, label=_('Editors (users)'),
        widget=FilteredSelectMultiple(_('users'), is_stacked=False),
        help_text=_('May view and edit the object, but not delete it.'),
    )
    editor_groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by('name'), required=False, label=_('Editors (groups)'),
        widget=FilteredSelectMultiple(_('groups'), is_stacked=False),
    )

    # Wird von OwnedAdmin.get_form gesetzt (angemeldeter Benutzer)
    request_user = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Neue Objekte gehören dem Anlegenden. Schon hier setzen, damit die
        # Eindeutigkeit pro Eigentümer im Formular geprüft wird.
        if self.instance.pk is None and self.instance.owner_id is None and self.request_user:
            self.instance.owner = self.request_user
        present = [name for name in SHARING_FIELDS if name in self.fields]
        if self.instance.pk and present:
            roles = perms.get_roles(self.instance)
            for name in present:
                role, kind = SHARING_FIELDS[name]
                self.fields[name].initial = [obj.pk for obj in roles[role][kind]]

    def _get_validation_exclusions(self):
        # Django lässt Felder, die nicht im Formular stehen (hier oft "owner"),
        # bei der Prüfung von UniqueConstraints weg. Der Eigentümer steht aber
        # immer fest, daher Constraints wie (owner, name) im Formular prüfen,
        # statt beim Speichern einen Fehler zu bekommen.
        exclude = super()._get_validation_exclusions()
        if self.instance.owner_id is not None:
            exclude.discard('owner')
        return exclude


class OwnedAdmin(admin.ModelAdmin):
    """
    Admin für Objekte mit Eigentümer, Lesern und Bearbeitern.

    - Listen zeigen nur sichtbare Objekte.
    - Lesen, Ändern und Löschen werden je Objekt geprüft. Leser sehen das
      Formular schreibgeschützt (Ansicht von Django ohne Änderungsrecht).
    - Eigentümer wird beim Anlegen automatisch gesetzt. Eigentümer und
      Freigaben ändern nur Eigentümer und Superuser.
    - Auswahllisten für verknüpfte Objekte zeigen nur lesbare Objekte
      (plus den aktuell gewählten Wert).
    """
    form = SharingForm
    # Inhalt des Formulars, die Gruppe "Freigaben" wird automatisch ergänzt
    content_fieldsets = ()
    # Fremdschlüssel, deren Auswahl auf lesbare Objekte beschränkt wird
    visible_fk_fields = ()

    # Zugriff

    def has_module_permission(self, request):
        # Auch Benutzer mit nur freigegebenen Objekten sollen die App sehen
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return request.user.is_active and request.user.is_staff
        return perms.can_view(request.user, obj)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return request.user.is_active and request.user.is_staff
        return perms.can_change(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            # Für die Sammelaktion; Django prüft danach jedes Objekt einzeln
            return request.user.is_active and request.user.is_staff
        return perms.can_delete(request.user, obj)

    def get_queryset(self, request):
        return perms.visible_to(request.user, super().get_queryset(request))

    # Liste

    def get_list_display(self, request):
        return [*super().get_list_display(request), 'owner']

    # Formular

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            sharing = tuple(SHARING_FIELDS)  # Anlegender wird Eigentümer
        elif perms.can_share(request.user, obj):
            sharing = ('owner', *SHARING_FIELDS)
        else:
            sharing = ('owner',)
        return (*self.content_fieldsets, (_('Owner and sharing'), {'fields': sharing}))

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and not perms.can_share(request.user, obj):
            readonly.append('owner')
        return readonly

    def get_form(self, request, obj=None, **kwargs):
        # Aktuelles Objekt für formfield_for_foreignkey merken
        request._owned_admin_obj = obj
        form = super().get_form(request, obj, **kwargs)
        form.request_user = request.user
        return form

    def limit_to_visible(self, request, db_field, queryset):
        """
        Beschränkt eine Auswahl auf lesbare Objekte und behält den aktuellen Wert.
        """
        obj = getattr(request, '_owned_admin_obj', None)
        current = getattr(obj, db_field.attname, None) if obj is not None else None
        return perms.visible_or_current(request.user, queryset, current)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'owner':
            kwargs['queryset'] = _user_choices()
        elif db_field.name in self.visible_fk_fields:
            base = kwargs.get('queryset')
            if base is None:
                base = db_field.related_model._default_manager.all()
            kwargs['queryset'] = self.limit_to_visible(request, db_field, base)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # Speichern

    def save_model(self, request, obj, form, change):
        request._owned_admin_new_owner = None
        if not change:
            obj.owner = request.user
        elif 'owner' in form.changed_data:
            # Wechsel erst nach den Freigaben ausführen (save_related), damit
            # der bisherige Eigentümer sie noch setzen darf und automatisch
            # Bearbeiter wird (perms.transfer_ownership)
            request._owned_admin_new_owner = obj.owner
            obj.owner_id = form.initial['owner']
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        data = form.cleaned_data
        if all(name in data for name in SHARING_FIELDS) and perms.can_share(request.user, obj):
            perms.set_roles(
                obj,
                reader_users=data['reader_users'],
                reader_groups=data['reader_groups'],
                editor_users=data['editor_users'],
                editor_groups=data['editor_groups'],
            )
        new_owner = getattr(request, '_owned_admin_new_owner', None)
        if new_owner is not None:
            perms.transfer_ownership(obj, new_owner)


# --- Filter --------------------------------------------------------------------

class MainCategoryFilter(admin.SimpleListFilter):
    """
    Filter nach Hauptkategorie. Findet Verträge, die direkt der
    Hauptkategorie oder einer ihrer Unterkategorien zugeordnet sind.
    Bietet nur Hauptkategorien der sichtbaren Verträge an.
    """
    title = _('main category')
    parameter_name = 'main_category'

    def lookups(self, request, model_admin):
        contracts = model_admin.get_queryset(request)
        main_categories = Category.objects.filter(
            Q(parent__isnull=True, pk__in=contracts.values('category'))
            | Q(pk__in=contracts.values('category__parent'))
        ).order_by('name')
        return [(str(category.pk), category.name) for category in main_categories]

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(Q(category_id=value) | Q(category__parent_id=value))
        return queryset


# --- Kosteneinträge ------------------------------------------------------------

class CostEntryInline(admin.TabularInline):
    """
    CostEntry als Inline im Contract-Admin.
    Rechte folgen dem Vertrag: lesen mit Leserecht, pflegen mit Bearbeitungsrecht.
    """
    model = CostEntry
    extra = 1
    fields = [
        'amount', 'valid_from', 'payment_interval',
        'document', 'notes',
    ]
    # Kleines Notizfeld, damit die Tabelle übersichtlich bleibt
    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(attrs={'rows': 2, 'cols': 30})},
    }

    # obj ist hier der Vertrag (None beim Anlegen eines neuen Vertrags)
    def has_view_permission(self, request, obj=None):
        return obj is None or perms.can_view(request.user, obj)

    def has_change_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    def has_add_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    def get_formset(self, request, obj=None, **kwargs):
        request._inline_contract = obj
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'document':
            # Nur Dokumente dieses Vertrags
            contract = getattr(request, '_inline_contract', None)
            kwargs['queryset'] = Document.objects.filter(contract=contract) if contract else Document.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class DocumentInline(admin.TabularInline):
    """
    Dokumente im Contract-Admin (Version 0.7 beta). Rechte wie bei den
    Kosteneinträgen.
    """
    model = Document
    extra = 0
    fields = ['name', 'source', 'file', 'paperless_document_id', 'paperless_document_link', 'notes', 'created_at']
    readonly_fields = ['paperless_document_link', 'created_at']
    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(attrs={'rows': 2, 'cols': 30})},
    }

    def has_view_permission(self, request, obj=None):
        return obj is None or perms.can_view(request.user, obj)

    def has_change_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    def has_add_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    @admin.display(description=_('Paperless'))
    def paperless_document_link(self, obj):
        return paperless_link(obj.paperless_url, gettext("Open document"))


class ReminderInline(admin.StackedInline):
    """
    Erinnerung (höchstens eine) im Contract-Admin. Rechte wie bei den
    Kosteneinträgen: lesen mit Leserecht, pflegen mit Bearbeitungsrecht.
    """
    model = Reminder
    extra = 0
    max_num = 1
    can_delete = True
    fields = ['enabled', 'remind_on', 'lead_days', 'reason', 'note', 'recipients', 'last_sent_on', 'completed_on']
    readonly_fields = ['last_sent_on', 'completed_on']
    filter_horizontal = ['recipients']
    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(attrs={'rows': 2, 'cols': 40})},
    }

    def has_view_permission(self, request, obj=None):
        return obj is None or perms.can_view(request.user, obj)

    def has_change_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    def has_add_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        return obj is None or perms.can_change(request.user, obj)


@admin.register(CostEntry)
class CostEntryAdmin(admin.ModelAdmin):
    list_display = ['contract', 'amount', 'valid_from', 'payment_interval', 'document']
    list_filter = ['payment_interval']
    list_select_related = ['contract', 'document']

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        if obj is None:
            return request.user.is_active and request.user.is_staff
        return perms.can_view(request.user, obj)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return request.user.is_active and request.user.is_staff
        return perms.can_change(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return request.user.is_active and request.user.is_staff
        return perms.can_delete(request.user, obj)

    def get_queryset(self, request):
        return perms.visible_to(request.user, super().get_queryset(request))

    def get_form(self, request, obj=None, **kwargs):
        request._owned_admin_obj = obj
        return super().get_form(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'contract':
            # Nur Verträge, die der Benutzer bearbeiten darf
            editable = perms.editable_by(request.user, Contract.objects.all())
            obj = getattr(request, '_owned_admin_obj', None)
            if obj is not None:
                editable = Contract.objects.filter(
                    Q(pk__in=editable.values('pk')) | Q(pk=obj.contract_id)
                )
            kwargs['queryset'] = editable
        if db_field.name == 'document':
            # Nur Dokumente des eigenen Vertrags (Prüfung zusätzlich in CostEntry.clean)
            obj = getattr(request, '_owned_admin_obj', None)
            kwargs['queryset'] = (
                Document.objects.filter(contract_id=obj.contract_id) if obj else Document.objects.none()
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# --- Stammdaten und Verträge ---------------------------------------------------

@admin.register(Provider)
class ProviderAdmin(OwnedAdmin):
    list_display = ['logo_preview', 'name', 'customer_number', 'paperless_correspondent_link']
    list_display_links = ['name']
    list_filter = [('owner', admin.RelatedOnlyFieldListFilter)]
    search_fields = ['name', 'customer_number']
    readonly_fields = ['paperless_correspondent_link', 'logo_preview']
    content_fieldsets = (
        (_('Master data'), {
            'fields': ('name', 'street', 'postal_code', 'city', 'country')
        }),
        (_('Logo'), {
            'fields': ('logo_preview', 'logo')
        }),
        (_('Contact details'), {
            'fields': ('email', 'customer_number', 'contact_person')
        }),
        (_('Paperless'), {
            'fields': ('paperless_correspondent_id', 'paperless_correspondent_link')
        }),
        (_('Other'), {
            'fields': ('notes',)
        }),
    )

    @admin.display(description=_('Paperless'))
    def paperless_correspondent_link(self, obj):
        return paperless_link(obj.paperless_correspondent_url, gettext("Open documents"))

    @admin.display(description=_('Logo'))
    def logo_preview(self, obj):
        # Adresse zeigt auf web.views.provider_logo (nur mit Leserecht)
        if not obj.logo:
            return "–"
        return format_html(
            '<img src="{}" alt="" style="max-width: 4rem; max-height: 4rem; background: #fff">', obj.logo.url
        )


@admin.register(Category)
class CategoryAdmin(OwnedAdmin):
    list_display = ['name', 'parent']
    list_filter = [('parent', admin.RelatedOnlyFieldListFilter), ('owner', admin.RelatedOnlyFieldListFilter)]
    list_select_related = ['parent', 'owner']
    search_fields = ['name']
    content_fieldsets = ((None, {'fields': ('name', 'parent')}),)
    visible_fk_fields = ('parent',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'parent':
            # Nur Hauptkategorien können Oberkategorie sein
            kwargs['queryset'] = Category.objects.filter(parent__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Consumer)
class ConsumerAdmin(OwnedAdmin):
    list_display = ['name', 'number']
    list_filter = [('owner', admin.RelatedOnlyFieldListFilter)]
    search_fields = ['name', 'number']
    content_fieldsets = ((None, {'fields': ('name', 'number', 'notes')}),)


@admin.register(PaymentMethod)
class PaymentMethodAdmin(OwnedAdmin):
    list_display = ['icon_preview', 'name']
    list_display_links = ['name']
    list_filter = [('owner', admin.RelatedOnlyFieldListFilter)]
    search_fields = ['name']
    readonly_fields = ['icon_preview']
    content_fieldsets = ((None, {'fields': ('name', 'icon_preview', 'icon')}),)

    @admin.display(description=_('Icon'))
    def icon_preview(self, obj):
        # Adresse wird nur mit Leserecht ausgeliefert (Ansicht in web)
        if not obj.icon:
            return "–"
        return format_html(
            '<img src="{}" alt="" style="max-width: 4rem; max-height: 4rem; background: #fff">', obj.icon.url
        )


@admin.register(Contract)
class ContractAdmin(OwnedAdmin):
    list_display = ['reference', 'name', 'provider', 'category', 'consumer', 'current_cost_display']
    # RelatedOnly: Filter zeigen nur Werte der sichtbaren Verträge
    list_filter = [
        MainCategoryFilter,
        ('category', admin.RelatedOnlyFieldListFilter),
        ('consumer', admin.RelatedOnlyFieldListFilter),
        ('provider', admin.RelatedOnlyFieldListFilter),
        ('payment_method', admin.RelatedOnlyFieldListFilter),
        ('owner', admin.RelatedOnlyFieldListFilter),
    ]
    list_select_related = ['provider', 'category__parent', 'consumer', 'owner']
    search_fields = ['name', 'reference']
    inlines = [DocumentInline, CostEntryInline, ReminderInline]
    # reference ist editable=False und darf nur schreibgeschützt angezeigt werden
    readonly_fields = ['reference', 'provider_paperless_link']
    visible_fk_fields = ('provider', 'category', 'consumer', 'payment_method')
    content_fieldsets = (
        (_('Master data'), {
            'fields': ('reference', 'name', 'provider', 'category', 'consumer', 'payment_method')
        }),
        (_('Term'), {
            'fields': ('start_date', 'term_value', 'term_unit')
        }),
        (_('Information'), {
            'fields': ('contract_number', 'short_description', 'notes', 'provider_paperless_link')
        }),
    )

    @admin.display(description=_('Paperless (partner)'))
    def provider_paperless_link(self, obj):
        """
        Link zu den Dokumenten des Vertragspartners in Paperless.
        """
        if obj.pk is None:
            return "–"
        return paperless_link(obj.provider.paperless_correspondent_url, gettext("Open partner documents"))

    @admin.display(description=_('Current amount'))
    def current_cost_display(self, obj):
        """
        Zeigt den aktuellen Betrag mit Zahlweise an, z. B. "120,00 € jährlich".
        """
        entry = obj.current_cost_entry
        if entry is None:
            return gettext("No costs")
        return f"{format_euro(entry.amount)} {entry.get_payment_interval_display()}"

    def changelist_view(self, request, extra_context=None):
        """
        Ergänzt die Vertragsliste um die Summe aller aktuellen Kosten.
        Berücksichtigt Suche, Filter und Berechtigungen, nicht nur die
        angezeigte Seite.
        """
        response = super().changelist_view(request, extra_context)
        # Bei Weiterleitungen (z. B. nach Sammelaktionen) gibt es keinen Kontext
        context = getattr(response, 'context_data', None)
        if not context or 'cl' not in context:
            return response

        totals = total_current_costs(context['cl'].queryset)
        context['cost_totals'] = {
            'monthly': format_euro(totals['monthly']),
            'yearly': format_euro(totals['yearly']),
            'count': totals['count'],
            'total_count': context['cl'].result_count,
        }
        return response


# --- Freigaben (nur Superuser, schreibgeschützt, zur Fehlersuche) --------------

class ObjectPermissionAdmin(admin.ModelAdmin):
    """
    Rohdaten von django-guardian. Freigaben werden im jeweiligen Objekt
    gepflegt, damit die Rollen vollständig bleiben.
    """
    list_select_related = True

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for _model in (
    ProviderUserObjectPermission, CategoryUserObjectPermission,
    ConsumerUserObjectPermission, PaymentMethodUserObjectPermission, ContractUserObjectPermission,
):
    admin.site.register(_model, ObjectPermissionAdmin, list_display=['content_object', 'user', 'permission'])

for _model in (
    ProviderGroupObjectPermission, CategoryGroupObjectPermission,
    ConsumerGroupObjectPermission, PaymentMethodGroupObjectPermission, ContractGroupObjectPermission,
):
    admin.site.register(_model, ObjectPermissionAdmin, list_display=['content_object', 'group', 'permission'])
