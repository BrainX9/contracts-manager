from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView
from django_htmx.http import reswap, retarget

from contracts import logos
from contracts import permissions as perms
from contracts.models import (
    CENT, Category, Consumer, Contract, CostEntry, Provider, Reminder, category_tree_order, total_current_costs,
)
from contracts.permissions import visible_to

from .forms import (
    CategoryForm, ConsumerForm, ContractForm, CostEntryForm, LoginForm, PreferencesForm, ProviderForm, ReminderForm,
    SharingForm, WebPasswordChangeForm, WebPasswordResetForm, WebSetPasswordForm,
)
from .models import SiteSettings, UserPreferences

# Anzahl der teuersten Verträge auf der Übersicht
TOP_CONTRACTS = 5


class DashboardView(LoginRequiredMixin, TemplateView):
    """
    Übersicht: Summen aller sichtbaren Verträge, aufgeteilt nach
    Hauptkategorie, und die teuersten Verträge.
    """
    template_name = "web/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contracts = list(
            visible_to(self.request.user, Contract.objects.all())
            .select_related("category__parent", "provider", "consumer")
            .prefetch_related("costs")
        )

        # Monatsbetrag je Vertrag nur einmal berechnen (eine Abfrage je Vertrag)
        with_costs = []
        for contract in contracts:
            monthly = contract.current_monthly_cost
            if monthly is not None:
                with_costs.append((contract, monthly))

        totals = total_current_costs(contract for contract, _monthly in with_costs)

        by_category = {}
        for contract, monthly in with_costs:
            main = contract.category.parent or contract.category
            entry = by_category.setdefault(main.pk, {"name": main.name, "monthly": Decimal("0"), "count": 0})
            entry["monthly"] += monthly
            entry["count"] += 1
        categories = sorted(by_category.values(), key=lambda e: e["monthly"], reverse=True)
        for entry in categories:
            entry["share"] = (
                round(entry["monthly"] / totals["monthly"] * 100) if totals["monthly"] else 0
            )
            entry["monthly"] = entry["monthly"].quantize(CENT)

        top = sorted(with_costs, key=lambda item: item[1], reverse=True)[:TOP_CONTRACTS]

        context.update({
            "totals": totals,
            "contract_count": len(contracts),
            "categories": categories,
            # Mit Logo und Link zum Partner, wenn er lesbar ist
            "top_contracts": contract_rows([contract for contract, _monthly in top], self.request.user),
        })
        return context


class WebLoginView(auth_views.LoginView):
    """
    Lokale Anmeldung; ist SSO eingeschaltet, zusätzlich die Schaltfläche
    zur Anmeldung über den SSO-Anbieter (web.oidc).
    """
    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sso_enabled"] = settings.OIDC_ENABLED
        context["sso_label"] = SiteSettings.load().sso_button_label
        return context


class PreferencesView(LoginRequiredMixin, UpdateView):
    """
    Persönliche Einstellungen, z. B. Sprache der Oberfläche.
    """
    form_class = PreferencesForm
    template_name = "web/preferences.html"
    success_url = reverse_lazy("preferences")

    def get_object(self, queryset=None):
        return UserPreferences.for_user(self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        # Meldung in der neu gewählten Sprache
        from django.utils import translation
        with translation.override(form.instance.language):
            messages.success(self.request, str(_("Your settings have been saved.")))
        return response


class WebPasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    form_class = WebPasswordChangeForm
    template_name = "web/password_change.html"
    success_url = reverse_lazy("preferences")

    def dispatch(self, request, *args, **kwargs):
        # Über SSO angelegte Benutzer haben kein lokales Passwort
        if request.user.is_authenticated and not request.user.has_usable_password():
            messages.info(request, _("You sign in via single sign-on and have no local password."))
            return redirect("preferences")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Your password has been changed."))
        return response


# --- Passwort zurücksetzen (Version 0.5 beta) --------------------------------

class WebPasswordResetView(auth_views.PasswordResetView):
    extra_context = {"auth_layout": True}
    form_class = WebPasswordResetForm
    template_name = "web/password_reset/form.html"
    success_url = reverse_lazy("password_reset_done")


class WebPasswordResetDoneView(auth_views.PasswordResetDoneView):
    extra_context = {"auth_layout": True}
    template_name = "web/password_reset/done.html"


class WebPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    extra_context = {"auth_layout": True}
    form_class = WebSetPasswordForm
    template_name = "web/password_reset/confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class WebPasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    extra_context = {"auth_layout": True}
    template_name = "web/password_reset/complete.html"


# --- Gemeinsame Bausteine für Objekte mit Eigentümer --------------------------

def get_owned_object(user, queryset, pk, action=None):
    """
    Lädt ein Objekt mit Eigentümer und prüft die Rechte.

    Nicht sichtbare Objekte liefern 404, damit sich nicht erraten lässt,
    welche es gibt. Sichtbare Objekte ohne das Recht für ``action``
    ("change", "delete" oder "share") liefern 403.
    """
    obj = get_object_or_404(visible_to(user, queryset), pk=pk)
    if action is not None and not getattr(perms, f"can_{action}")(user, obj):
        raise PermissionDenied
    return obj


class OwnedObjectMixin:
    """
    Für Detail-, Bearbeitungs-, Lösch- und Freigabe-Ansichten: lädt das
    Objekt über ``get_owned_object`` mit dem Recht ``required_action``.
    """
    model = None
    required_action = None

    def get_queryset(self):
        return self.model.objects.all()

    def get_object(self, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        return get_owned_object(self.request.user, queryset, self.kwargs["pk"], self.required_action)


class HtmxResultsMixin:
    """
    Listen mit Live-Suche: Bei HTMX-Anfragen wird nur das Ergebnis
    (``results_template_name``) neu geladen.
    """
    results_template_name = None

    def is_partial(self):
        # Nur das Ergebnis neu laden, außer htmx stellt beim Zurück-Navigieren
        # eine ganze Seite wieder her
        htmx = self.request.htmx
        return bool(htmx) and not htmx.boosted and not htmx.history_restore_request

    def get_template_names(self):
        return [self.results_template_name if self.is_partial() else self.template_name]

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        # Gleiche Adresse, unterschiedliche Antwort: Caches müssen das unterscheiden
        patch_vary_headers(response, ("HX-Request",))
        return response


def get_sort(request, sorts, default="name"):
    """
    Sortierung aus dem URL-Parameter ``sort``, z. B. "name" oder "-name".
    Unbekannte Werte ergeben die Standardsortierung.
    """
    sort = request.GET.get("sort", default)
    return sort if sort.lstrip("-") in sorts else default


class OwnedSharingView(LoginRequiredMixin, OwnedObjectMixin, View):
    """
    Eigentümer und Freigaben eines Objekts (nur Eigentümer und Superuser).
    Unterklassen setzen ``model``, die URL-Namen und den Hilfetext.
    """
    required_action = "share"
    template_name = "web/sharing.html"
    list_url_name = None
    detail_url_name = None
    list_label = None
    help_text = None

    def render_form(self, obj, form):
        return render(self.request, self.template_name, {
            "object": obj,
            "form": form,
            "list_url": reverse(self.list_url_name),
            "list_label": self.list_label,
            "detail_url": reverse(self.detail_url_name, args=[obj.pk]),
            "help_text": self.help_text,
        })

    def get(self, request, pk):
        obj = self.get_object()
        return self.render_form(obj, SharingForm(obj=obj))

    def post(self, request, pk):
        obj = self.get_object()
        form = SharingForm(request.POST, obj=obj)
        if not form.is_valid():
            return self.render_form(obj, form)
        form.save()
        messages.success(request, _("The sharing settings have been saved."))
        # Nach einem Wechsel des Eigentümers hat der bisherige ggf. keinen Zugriff mehr
        if perms.can_view(request.user, obj):
            return redirect(self.detail_url_name, pk=obj.pk)
        return redirect(self.list_url_name)


# --- Verträge ----------------------------------------------------------------

def cost_context(contract, user):
    """
    Kostenverlauf eines Vertrags für Detailseite und HTMX-Antworten.
    """
    current = contract.current_cost_entry
    today = timezone.localdate()
    costs = [
        {
            "entry": entry,
            "monthly": entry.monthly_amount().quantize(CENT),
            "is_current": current is not None and entry.pk == current.pk,
            "is_future": entry.valid_from > today,
        }
        for entry in contract.costs.select_related("document").order_by("-valid_from", "-id")
    ]
    return {
        "contract": contract,
        "costs": costs,
        "current": current,
        "current_monthly": current.monthly_amount().quantize(CENT) if current else None,
        "can_change": perms.can_change(user, contract),
    }


def document_context(contract, user):
    """
    Dokumente eines Vertrags für Detailseite und HTMX-Antworten (Version 0.7 beta).
    """
    return {
        "contract": contract,
        "documents": list(contract.documents.order_by("-created_at", "-id")),
        "can_change": perms.can_change(user, contract),
    }


def get_editable_cost_entry(user, pk):
    """
    Kosteneintrag, dessen Vertrag der Benutzer bearbeiten darf: 404 wenn der
    Vertrag unsichtbar ist, 403 wenn er nur lesbar ist.
    """
    entry = get_object_or_404(
        CostEntry.objects.select_related("contract").filter(
            contract__in=visible_to(user, Contract.objects.all())
        ),
        pk=pk,
    )
    if not perms.can_change(user, entry.contract):
        raise PermissionDenied
    return entry

# Sortierungen der Vertragsliste: Schlüssel im URL-Parameter -> Sortierfunktion
CONTRACT_SORTS = {
    "name": lambda row: row["contract"].name.lower(),
    "reference": lambda row: row["contract"].reference or "",
    "provider": lambda row: row["contract"].provider.name.lower(),
    "monthly": lambda row: row["monthly"] if row["monthly"] is not None else Decimal("-1"),
}


def _selected_id(request, name):
    """
    Liest eine ID aus den URL-Parametern, ungültige Werte werden ignoriert.
    """
    value = request.GET.get(name, "")
    return int(value) if value.isdigit() else None


class ContractListView(LoginRequiredMixin, HtmxResultsMixin, TemplateView):
    """
    Liste der sichtbaren Verträge mit Suche, Filtern, Sortierung und der
    Summe passend zum Filter. Bei HTMX-Anfragen wird nur das Ergebnis
    (Tabelle und Summe) neu geladen.
    """
    template_name = "web/contracts/list.html"
    results_template_name = "web/contracts/_results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        visible = visible_to(request.user, Contract.objects.all())

        filters = {
            "q": request.GET.get("q", "").strip(),
            "main_category": _selected_id(request, "main_category"),
            "category": _selected_id(request, "category"),
            "consumer": _selected_id(request, "consumer"),
            "provider": _selected_id(request, "provider"),
        }
        sort = get_sort(request, CONTRACT_SORTS)

        contracts = visible.select_related("provider", "category__parent", "consumer", "owner").prefetch_related("costs")
        if filters["q"]:
            q = filters["q"]
            contracts = contracts.filter(
                Q(name__icontains=q) | Q(reference__icontains=q)
                | Q(contract_number__icontains=q) | Q(short_description__icontains=q)
                | Q(provider__name__icontains=q)
            )
        if filters["main_category"]:
            contracts = contracts.filter(
                Q(category_id=filters["main_category"]) | Q(category__parent_id=filters["main_category"])
            )
        for name in ("category", "consumer", "provider"):
            if filters[name]:
                contracts = contracts.filter(**{f"{name}_id": filters[name]})

        rows = contract_rows(contracts, self.request.user)
        rows.sort(key=CONTRACT_SORTS[sort.lstrip("-")], reverse=sort.startswith("-"))

        totals = total_current_costs(row["contract"] for row in rows)

        # Filteroptionen nur aus sichtbaren Verträgen, damit keine fremden
        # Namen erscheinen
        context.update({
            "rows": rows,
            "totals": totals,
            "filters": filters,
            "sort": sort,
            "list_url": reverse("contract_list"),
            "is_filtered": any(filters.values()),
            "visible_count": visible.count(),
            "main_categories": Category.objects.filter(
                Q(parent__isnull=True, pk__in=visible.values("category"))
                | Q(pk__in=visible.values("category__parent"))
            ).order_by("name"),
            "categories": category_tree_order(Category.objects.filter(pk__in=visible.values("category"))),
            "consumers": Consumer.objects.filter(pk__in=visible.values("consumer")).order_by("name"),
            "providers": Provider.objects.filter(pk__in=visible.values("provider")).order_by("name"),
        })
        return context


class ContractDetailView(LoginRequiredMixin, OwnedObjectMixin, DetailView):
    """
    Vertrag mit Stammdaten, Laufzeit, Kostenverlauf, Paperless-Links und
    Rollen. Nicht sichtbare Verträge liefern 404, damit sich nicht erraten
    lässt, welche es gibt.
    """
    model = Contract
    template_name = "web/contracts/detail.html"
    context_object_name = "contract"

    def get_queryset(self):
        return Contract.objects.select_related("provider", "category__parent", "consumer", "owner")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contract = self.object
        user = self.request.user
        can_share = perms.can_share(user, contract)
        context.update(cost_context(contract, user))
        context.update(document_context(contract, user))
        context.update({
            "can_delete": perms.can_delete(user, contract),
            "can_share": can_share,
            # Verknüpfte Stammdaten nur mit Details, wenn sie lesbar sind
            "provider_visible": perms.can_view(user, contract.provider),
            "category_visible": perms.can_view(user, contract.category),
            "consumer_visible": contract.consumer_id is not None and perms.can_view(user, contract.consumer),
            "roles": perms.get_roles(contract) if can_share else None,
            "reminder": Reminder.objects.filter(contract=contract).prefetch_related("recipients").first(),
        })
        return context


class ReminderView(LoginRequiredMixin, View):
    """
    Erinnerung eines Vertrags einrichten oder ändern (Bearbeitungsrecht am
    Vertrag). Es gibt höchstens eine Erinnerung; ausschalten statt löschen.
    """
    template_name = "web/contracts/reminder_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.contract = None

    def get_form(self):
        contract = get_owned_object(self.request.user, Contract.objects.all(), self.kwargs["pk"], action="change")
        self.contract = contract
        instance = Reminder.objects.filter(contract=contract).first() or Reminder(contract=contract)
        data = self.request.POST if self.request.method == "POST" else None
        return ReminderForm(data, instance=instance)

    def render_form(self, form):
        return render(self.request, self.template_name, {"form": form, "contract": self.contract})

    def get(self, request, pk):
        return self.render_form(self.get_form())

    def post(self, request, pk):
        form = self.get_form()
        if not form.is_valid():
            return self.render_form(form)
        form.save()
        messages.success(request, _("The reminder has been saved."))
        return redirect("contract_detail", pk=self.contract.pk)


class ContractFormLinksMixin:
    """
    Im Vertragsformular neben Partner, Kategorie und Verbraucher einen Link
    zum Anlegen, wenn der Benutzer das darf.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        user = self.request.user
        context["new_links"] = [
            (form["provider"], user.has_perm("contracts.add_provider"), reverse("provider_create"),
             _("New contract partner")),
            (form["category"], user.has_perm("contracts.add_category"), reverse("category_create"),
             _("New category")),
            (form["consumer"], user.has_perm("contracts.add_consumer"), reverse("consumer_create"),
             _("New consumer")),
        ]
        return context


class ContractCreateView(LoginRequiredMixin, PermissionRequiredMixin, ContractFormLinksMixin, CreateView):
    """
    Neuen Vertrag anlegen. Erfordert das globale Recht "Vertrag anlegen"
    (Gruppe "Benutzer"). Der Anlegende wird Eigentümer.
    """
    form_class = ContractForm
    template_name = "web/contracts/form.html"
    permission_required = "contracts.add_contract"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("The contract has been created. You can now add its costs."))
        return response

    def get_success_url(self):
        return reverse("contract_detail", args=[self.object.pk])


class ContractUpdateView(LoginRequiredMixin, OwnedObjectMixin, ContractFormLinksMixin, UpdateView):
    """
    Vertrag bearbeiten (Eigentümer, Bearbeiter, Superuser).
    """
    model = Contract
    required_action = "change"
    form_class = ContractForm
    template_name = "web/contracts/form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("The contract has been saved."))
        return response

    def get_success_url(self):
        return reverse("contract_detail", args=[self.object.pk])


class ContractDeleteView(LoginRequiredMixin, OwnedObjectMixin, DeleteView):
    """
    Vertrag mit allen Kosteneinträgen löschen (nur Eigentümer und Superuser).
    """
    model = Contract
    required_action = "delete"
    template_name = "web/contracts/confirm_delete.html"
    context_object_name = "contract"
    success_url = reverse_lazy("contract_list")

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, _("The contract “%(name)s” has been deleted.") % {"name": name})
        return response


# --- Kosteneinträge (HTMX, mit Rückfall auf normale Seiten) ------------------

class CostEntryFormView(LoginRequiredMixin, View):
    """
    Kosteneintrag anlegen (contract_pk) oder bearbeiten (pk).

    Mit HTMX wird das Formular in die Detailseite geladen und nach dem
    Speichern der Kostenverlauf ersetzt. Ohne JavaScript gibt es eine
    eigene Seite und danach eine Weiterleitung zur Detailseite.
    """
    page_template = "web/contracts/cost_form.html"
    form_template = "web/contracts/_cost_form.html"
    costs_template = "web/contracts/_costs.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.entry = None
        self.contract = None

    def load(self):
        user = self.request.user
        if "pk" in self.kwargs:
            self.entry = get_editable_cost_entry(user, self.kwargs["pk"])
            self.contract = self.entry.contract
        else:
            self.contract = get_owned_object(user, Contract.objects.all(), self.kwargs["contract_pk"], "change")

    def get_action_url(self):
        if self.entry:
            return reverse("cost_edit", args=[self.entry.pk])
        return reverse("cost_add", args=[self.contract.pk])

    def render_form(self, form):
        context = {
            "form": form,
            "contract": self.contract,
            "entry": self.entry,
            "action_url": self.get_action_url(),
            "use_htmx": bool(self.request.htmx),
        }
        template = self.form_template if self.request.htmx else self.page_template
        return render(self.request, template, context)

    def get(self, request, *args, **kwargs):
        self.load()
        initial = {} if self.entry else {"valid_from": timezone.localdate()}
        return self.render_form(CostEntryForm(instance=self.entry, initial=initial, contract=self.contract))

    def post(self, request, *args, **kwargs):
        self.load()
        form = CostEntryForm(request.POST, instance=self.entry, contract=self.contract)
        if not form.is_valid():
            response = self.render_form(form)
            if request.htmx:
                # Formular mit Fehlern wieder an seine Stelle setzen
                response = reswap(retarget(response, "#cost-form"), "innerHTML")
            return response
        entry = form.save(commit=False)
        entry.contract = self.contract
        entry.save()
        if request.htmx:
            return render(request, self.costs_template, {**cost_context(self.contract, request.user), "oob": True})
        messages.success(request, _("The cost entry has been saved."))
        return redirect("contract_detail", pk=self.contract.pk)


class CostEntryDeleteView(LoginRequiredMixin, View):
    """
    Kosteneintrag löschen (nur per POST, mit Bearbeitungsrecht am Vertrag).
    """

    def post(self, request, pk):
        entry = get_editable_cost_entry(request.user, pk)
        contract = entry.contract
        entry.delete()
        if request.htmx:
            return render(request, "web/contracts/_costs.html", {**cost_context(contract, request.user), "oob": True})
        messages.success(request, _("The cost entry has been deleted."))
        return redirect("contract_detail", pk=contract.pk)


class ContractSharingView(OwnedSharingView):
    """
    Eigentümer und Freigaben eines Vertrags.
    """
    model = Contract
    list_url_name = "contract_list"
    detail_url_name = "contract_detail"
    list_label = _("Contracts")
    help_text = _(
        "Readers may view the contract and its costs. Editors may also edit the contract "
        "and its costs, but not delete it or change the sharing. The owner has full access."
    )


# --- Stammdaten: gemeinsame Ansichten ----------------------------------------

def contract_rows(contracts, user):
    """
    Zeilen für Vertragstabellen: Vertrag, aktueller Eintrag, Monatsbetrag und
    ob der Benutzer den Vertragspartner lesen darf (dann mit Logo und Link).
    """
    contracts = list(contracts)
    provider_ids = {contract.provider_id for contract in contracts}
    visible_providers = set(
        visible_to(user, Provider.objects.filter(pk__in=provider_ids)).values_list("pk", flat=True)
    )
    rows = []
    for contract in contracts:
        entry = contract.current_cost_entry
        rows.append({
            "contract": contract,
            "entry": entry,
            "monthly": entry.monthly_amount().quantize(CENT) if entry else None,
            "provider_visible": contract.provider_id in visible_providers,
        })
    return rows


class MasterDataMixin:
    """
    Gemeinsame Angaben der Stammdaten-Ansichten (Vertragspartner,
    Kategorien, Verbraucher).

    - ``model``: das Modell
    - ``url_prefix``: Anfang der URL-Namen, z. B. "provider" für
      provider_list, provider_detail, ...
    - ``contract_field``: Feld am Vertrag, das auf das Modell verweist
    """
    model = None
    url_prefix = None
    contract_field = None

    def url_name(self, action):
        return f"{self.url_prefix}_{action}"

    def visible_contracts(self):
        # Kosten vorab laden, damit der aktuelle Betrag keine Abfrage je Vertrag braucht
        return visible_to(self.request.user, Contract.objects.prefetch_related("costs"))


class MasterDataListView(LoginRequiredMixin, MasterDataMixin, HtmxResultsMixin, TemplateView):
    """
    Liste sichtbarer Stammdaten mit Live-Suche (``search_fields``) und
    Sortierung (``sorts``). Anzahl und Monatssumme zählen nur Verträge,
    die der Benutzer sehen darf.
    """
    search_fields = ("name",)
    sorts = {
        "name": lambda row: row["object"].name.lower(),
        "contracts": lambda row: row["contract_count"],
        "monthly": lambda row: row["monthly"],
    }

    def get_queryset(self):
        return visible_to(self.request.user, self.model.objects.select_related("owner"))

    def search(self, queryset, q):
        condition = Q()
        for field in self.search_fields:
            condition |= Q(**{f"{field}__icontains": q})
        return queryset.filter(condition)

    def contract_stats(self, objects):
        """
        Anzahl und Monatssumme der sichtbaren Verträge je Objekt.
        """
        stats = {obj.pk: {"count": 0, "monthly": Decimal("0")} for obj in objects}
        field_id = f"{self.contract_field}_id"
        contracts = self.visible_contracts().filter(**{f"{self.contract_field}__in": objects})
        for contract in contracts:
            entry = stats[getattr(contract, field_id)]
            entry["count"] += 1
            entry["monthly"] += contract.current_monthly_cost or Decimal("0")
        return stats

    def get_rows(self, objects):
        stats = self.contract_stats(objects)
        return [
            {
                "object": obj,
                "contract_count": stats[obj.pk]["count"],
                "monthly": stats[obj.pk]["monthly"].quantize(CENT),
            }
            for obj in objects
        ]

    def sort_rows(self, rows, sort):
        rows.sort(key=self.sorts[sort.lstrip("-")], reverse=sort.startswith("-"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        visible = self.get_queryset()
        q = request.GET.get("q", "").strip()
        sort = get_sort(request, self.sorts)
        objects = list(self.search(visible, q) if q else visible)
        rows = self.get_rows(objects)
        self.sort_rows(rows, sort)
        context.update({
            "rows": rows,
            "q": q,
            "sort": sort,
            "list_url": reverse(self.url_name("list")),
            "visible_count": visible.count(),
        })
        return context


class MasterDataDetailView(LoginRequiredMixin, MasterDataMixin, OwnedObjectMixin, DetailView):
    """
    Stammdaten-Objekt mit den sichtbaren Verträgen, die darauf verweisen,
    und dem Zugriff.
    """

    def get_queryset(self):
        return self.model.objects.select_related("owner")

    def get_contracts(self):
        return self.visible_contracts().filter(**{self.contract_field: self.object})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.object
        user = self.request.user
        contracts = (
            self.get_contracts()
            .select_related("provider", "category__parent", "consumer")
            .order_by("name")
        )
        rows = contract_rows(contracts, self.request.user)
        can_share = perms.can_share(user, obj)
        context.update({
            "rows": rows,
            "totals": total_current_costs(row["contract"] for row in rows),
            "can_change": perms.can_change(user, obj),
            "can_delete": perms.can_delete(user, obj),
            "can_share": can_share,
            "roles": perms.get_roles(obj) if can_share else None,
        })
        return context


class MasterDataFormMixin(MasterDataMixin):
    """
    Anlegen und Bearbeiten: Formular mit ``user``, Meldung und Weiterleitung
    zur Detailseite.
    """
    success_message = None

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, self.success_message)
        return response

    def get_success_url(self):
        return reverse(self.url_name("detail"), args=[self.object.pk])


class MasterDataCreateView(LoginRequiredMixin, PermissionRequiredMixin, MasterDataFormMixin, CreateView):
    """
    Anlegen erfordert das globale Recht "add_<modell>" (Gruppe "Benutzer").
    Der Anlegende wird Eigentümer.
    """

    def get_permission_required(self):
        return [f"contracts.add_{self.model._meta.model_name}"]


class MasterDataUpdateView(LoginRequiredMixin, MasterDataFormMixin, OwnedObjectMixin, UpdateView):
    """
    Bearbeiten (Eigentümer, Bearbeiter, Superuser).
    """
    required_action = "change"


class MasterDataDeleteView(LoginRequiredMixin, MasterDataMixin, OwnedObjectMixin, DeleteView):
    """
    Löschen (nur Eigentümer und Superuser). Solange das Objekt noch
    verwendet wird (PROTECT), wird das abgelehnt. Wie oft, wird nicht
    genannt, da der Benutzer manche Verträge evtl. nicht sehen darf.
    """
    required_action = "delete"
    protected_message = None
    deleted_message = None

    def get_success_url(self):
        return reverse(self.url_name("list"))

    def is_in_use(self):
        return Contract.objects.filter(**{self.contract_field: self.object}).exists()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["in_use"] = self.is_in_use()
        return context

    def form_valid(self, form):
        name = self.object.name
        try:
            response = super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, self.protected_message)
            return redirect(self.url_name("detail"), pk=self.object.pk)
        messages.success(self.request, self.deleted_message % {"name": name})
        return response


# --- Vertragspartner ---------------------------------------------------------

class ProviderMixin:
    model = Provider
    url_prefix = "provider"
    contract_field = "provider"


class ProviderListView(ProviderMixin, MasterDataListView):
    template_name = "web/providers/list.html"
    results_template_name = "web/providers/_results.html"
    search_fields = ("name", "city", "customer_number", "contact_person", "email")
    sorts = {
        **MasterDataListView.sorts,
        "city": lambda row: row["object"].city.lower(),
    }


class ProviderDetailView(ProviderMixin, MasterDataDetailView):
    template_name = "web/providers/detail.html"
    context_object_name = "provider"


class ProviderCreateView(ProviderMixin, MasterDataCreateView):
    form_class = ProviderForm
    template_name = "web/providers/form.html"
    success_message = _("The contract partner has been created.")


class ProviderUpdateView(ProviderMixin, MasterDataUpdateView):
    form_class = ProviderForm
    template_name = "web/providers/form.html"
    success_message = _("The contract partner has been saved.")


class ProviderDeleteView(ProviderMixin, MasterDataDeleteView):
    template_name = "web/providers/confirm_delete.html"
    context_object_name = "provider"
    protected_message = _("The contract partner cannot be deleted because it is still used in contracts.")
    deleted_message = _("The contract partner “%(name)s” has been deleted.")


@login_required
def provider_logo(request, filename):
    """
    Liefert das Logo eines Vertragspartners aus (Adresse aus
    ``Provider.logo.url``). Nur mit Leserecht am Partner, sonst 404, damit
    sich nicht erkennen lässt, ob es das Logo gibt.
    """
    name = f"{logos.LOGO_DIR}/{filename}"
    provider = visible_to(request.user, Provider.objects.filter(logo=name)).first()
    if provider is None:
        raise Http404
    try:
        file = provider.logo.open("rb")
    except FileNotFoundError:
        raise Http404
    response = FileResponse(file, content_type="image/png")
    # Der Dateiname ändert sich bei jedem neuen Logo, daher lange zwischenspeichern,
    # aber nur im Browser (private), nicht in gemeinsamen Caches
    patch_cache_control(response, private=True, max_age=7 * 24 * 3600)
    return response


class ProviderSharingView(OwnedSharingView):
    model = Provider
    list_url_name = "provider_list"
    detail_url_name = "provider_detail"
    list_label = _("Contract partners")
    help_text = _(
        "Readers may view the contract partner and use it in their contracts. Editors may also "
        "edit it, but not delete it or change the sharing. The owner has full access."
    )


# --- Verbraucher -------------------------------------------------------------

class ConsumerMixin:
    model = Consumer
    url_prefix = "consumer"
    contract_field = "consumer"


class ConsumerListView(ConsumerMixin, MasterDataListView):
    template_name = "web/consumers/list.html"
    results_template_name = "web/consumers/_results.html"
    search_fields = ("name", "number")
    sorts = {
        **MasterDataListView.sorts,
        "number": lambda row: row["object"].number.lower(),
    }


class ConsumerDetailView(ConsumerMixin, MasterDataDetailView):
    template_name = "web/consumers/detail.html"
    context_object_name = "consumer"


class ConsumerCreateView(ConsumerMixin, MasterDataCreateView):
    form_class = ConsumerForm
    template_name = "web/consumers/form.html"
    success_message = _("The consumer has been created.")


class ConsumerUpdateView(ConsumerMixin, MasterDataUpdateView):
    form_class = ConsumerForm
    template_name = "web/consumers/form.html"
    success_message = _("The consumer has been saved.")


class ConsumerDeleteView(ConsumerMixin, MasterDataDeleteView):
    template_name = "web/consumers/confirm_delete.html"
    context_object_name = "consumer"
    protected_message = _("The consumer cannot be deleted because it is still used in contracts.")
    deleted_message = _("The consumer “%(name)s” has been deleted.")


class ConsumerSharingView(OwnedSharingView):
    model = Consumer
    list_url_name = "consumer_list"
    detail_url_name = "consumer_detail"
    list_label = _("Consumers")
    help_text = _(
        "Readers may view the consumer and use it in their contracts. Editors may also "
        "edit it, but not delete it or change the sharing. The owner has full access."
    )


# --- Kategorien --------------------------------------------------------------

class CategoryMixin:
    model = Category
    url_prefix = "category"
    contract_field = "category"


class CategoryListView(CategoryMixin, MasterDataListView):
    """
    Kategorien als Baum: Hauptkategorie, darunter ihre Unterkategorien.
    Bei Hauptkategorien zählen die Verträge der Unterkategorien mit.
    """
    template_name = "web/categories/list.html"
    results_template_name = "web/categories/_results.html"
    sorts = {"name": None}  # immer Baum-Reihenfolge

    def get_queryset(self):
        return category_tree_order(super().get_queryset().select_related("parent"))

    def contract_stats(self, objects):
        stats = {obj.pk: {"count": 0, "monthly": Decimal("0")} for obj in objects}
        contracts = self.visible_contracts().filter(
            Q(category__in=objects) | Q(category__parent__in=objects)
        ).select_related("category")
        for contract in contracts:
            monthly = contract.current_monthly_cost or Decimal("0")
            for pk in (contract.category_id, contract.category.parent_id):
                if pk in stats:
                    stats[pk]["count"] += 1
                    stats[pk]["monthly"] += monthly
        return stats

    def get_rows(self, objects):
        rows = super().get_rows(objects)
        shown = {obj.pk for obj in objects}
        for row in rows:
            # Unterkategorien eingerückt, wenn ihre Hauptkategorie darüber steht
            row["indent"] = row["object"].parent_id in shown
        return rows

    def sort_rows(self, rows, sort):
        pass


class CategoryDetailView(CategoryMixin, MasterDataDetailView):
    template_name = "web/categories/detail.html"
    context_object_name = "category"

    def get_queryset(self):
        return Category.objects.select_related("owner", "parent")

    def get_contracts(self):
        return self.visible_contracts().filter(Q(category=self.object) | Q(category__parent=self.object))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        category = self.object
        context.update({
            "children": visible_to(user, category.children.all()).order_by("name"),
            "parent_visible": category.parent is not None and perms.can_view(user, category.parent),
        })
        return context


class CategoryCreateView(CategoryMixin, MasterDataCreateView):
    form_class = CategoryForm
    template_name = "web/categories/form.html"
    success_message = _("The category has been created.")

    def get_initial(self):
        # "Neue Unterkategorie" von der Seite einer Hauptkategorie
        parent = _selected_id(self.request, "parent")
        return {"parent": parent} if parent else {}


class CategoryUpdateView(CategoryMixin, MasterDataUpdateView):
    form_class = CategoryForm
    template_name = "web/categories/form.html"
    success_message = _("The category has been saved.")


class CategoryDeleteView(CategoryMixin, MasterDataDeleteView):
    template_name = "web/categories/confirm_delete.html"
    context_object_name = "category"
    protected_message = _(
        "The category cannot be deleted because it is still used in contracts or has subcategories."
    )
    deleted_message = _("The category “%(name)s” has been deleted.")

    def is_in_use(self):
        return super().is_in_use() or self.object.children.exists()


class CategorySharingView(OwnedSharingView):
    model = Category
    list_url_name = "category_list"
    detail_url_name = "category_detail"
    list_label = _("Categories")
    help_text = _(
        "Readers may view the category and use it in their contracts. Editors may also "
        "edit it, but not delete it or change the sharing. The owner has full access. "
        "Sharing a main category does not share its subcategories."
    )
