from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contracts import permissions as perms
from contracts.models import Category, Consumer, Contract, CostEntry, Document, Provider

from .models import UserPreferences


def make_contract(owner, name, amount, interval="monthly", category=None):
    provider, _ = Provider.objects.get_or_create(owner=owner, name=f"Partner {owner.username}")
    consumer, _ = Consumer.objects.get_or_create(owner=owner, name=f"Haushalt {owner.username}")
    category = category or Category.objects.get_or_create(owner=owner, name="Sonstiges")[0]
    contract = Contract.objects.create(
        owner=owner, name=name, provider=provider, category=category,
        consumer=consumer, start_date=timezone.localdate(),
    )
    CostEntry.objects.create(
        contract=contract, amount=Decimal(amount), payment_interval=interval,
        valid_from=timezone.localdate(),
    )
    return contract


class LoginTest(TestCase):
    def test_pages_require_login(self):
        for name in ("dashboard", "preferences", "password_change"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, f"{reverse('login')}?next={reverse(name)}")

    def test_login_page_is_german_by_default(self):
        response = self.client.get(reverse("login"), HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(response, "Anmelden")
        self.assertEqual(response.headers["Content-Language"], "de")

    def test_login_and_logout(self):
        User.objects.create_user("anna", password="geheim-123")
        response = self.client.post(reverse("login"), {"username": "anna", "password": "geheim-123"})
        self.assertRedirects(response, reverse("dashboard"))
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        self.assertRedirects(self.client.get(reverse("dashboard")), f"{reverse('login')}?next=/")

    def test_frontend_does_not_need_staff_status(self):
        user = User.objects.create_user("ohne-staff")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_health_stays_public(self):
        self.assertEqual(self.client.get("/health/").status_code, 200)


class DashboardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna")
        cls.ben = User.objects.create_user("ben")
        insurance = Category.objects.create(owner=cls.anna, name="Versicherung")
        car = Category.objects.create(owner=cls.anna, name="KFZ", parent=insurance)
        make_contract(cls.anna, "Hausrat", "120.00", "yearly", category=insurance)   # 10,00 / Monat
        make_contract(cls.anna, "Auto", "300.00", "quarterly", category=car)         # 100,00 / Monat
        shared = make_contract(cls.ben, "Strom", "80.00")                            # 80,00 / Monat
        make_contract(cls.ben, "Geheim", "999.00")
        perms.grant_role(shared, cls.anna, perms.READER)

    def test_totals_only_for_visible_contracts(self):
        self.client.force_login(self.anna)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["totals"]["monthly"], Decimal("190.00"))
        self.assertEqual(response.context["totals"]["yearly"], Decimal("2280.00"))
        self.assertEqual(response.context["contract_count"], 3)
        self.assertNotContains(response, "Geheim")
        self.assertContains(response, "190,00")

    def test_grouped_by_main_category(self):
        self.client.force_login(self.anna)
        categories = self.client.get(reverse("dashboard")).context["categories"]
        self.assertEqual(
            [(c["name"], c["monthly"], c["count"]) for c in categories],
            [("Versicherung", Decimal("110.00"), 2), ("Sonstiges", Decimal("80.00"), 1)],
        )

    def test_top_contracts_sorted_by_monthly_cost(self):
        self.client.force_login(self.anna)
        top = self.client.get(reverse("dashboard")).context["top_contracts"]
        self.assertEqual([item["contract"].name for item in top], ["Auto", "Strom", "Hausrat"])

    def test_empty_dashboard(self):
        self.client.force_login(User.objects.create_user("neu"))
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Es gibt noch keine Verträge")


class LanguageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("anna", password="geheim-123")

    def setUp(self):
        self.client.force_login(self.user)

    def test_default_language_is_german(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Übersicht")
        self.assertContains(response, '<html lang="de">')

    def test_change_language_to_english(self):
        response = self.client.post(reverse("preferences"), {"language": "en"}, follow=True)
        self.assertEqual(UserPreferences.objects.get(user=self.user).language, "en")
        self.assertContains(response, "Your settings have been saved.")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Overview")
        self.assertContains(response, '<html lang="en">')
        self.assertEqual(response.headers["Content-Language"], "en")

    def test_language_applies_to_admin(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        UserPreferences.objects.create(user=self.user, language="en")
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Site administration")

    def test_language_follows_user_not_browser(self):
        UserPreferences.objects.create(user=self.user, language="de")
        response = self.client.get(reverse("dashboard"), HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(response, "Übersicht")

    def test_invalid_language_is_rejected(self):
        response = self.client.post(reverse("preferences"), {"language": "xx"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserPreferences.objects.filter(user=self.user, language="xx").exists())

    def test_model_labels_are_translated(self):
        from contracts.models import Contract as ContractModel
        from django.utils import translation
        field = ContractModel._meta.get_field("provider")
        with translation.override("de"):
            self.assertEqual(str(field.verbose_name), "Vertragspartner")
        with translation.override("en"):
            self.assertEqual(str(field.verbose_name), "contract partner")

    def test_password_change(self):
        response = self.client.post(reverse("password_change"), {
            "old_password": "geheim-123",
            "new_password1": "ein-neues-Passwort-42",
            "new_password2": "ein-neues-Passwort-42",
        })
        self.assertRedirects(response, reverse("preferences"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("ein-neues-Passwort-42"))


class ContractListTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna")
        cls.ben = User.objects.create_user("ben")
        insurance = Category.objects.create(owner=cls.anna, name="Versicherung")
        car = Category.objects.create(owner=cls.anna, name="KFZ", parent=insurance)
        cls.household = make_contract(cls.anna, "Hausrat", "120.00", "yearly", category=insurance)
        cls.car = make_contract(cls.anna, "Auto", "300.00", "quarterly", category=car)
        cls.power = make_contract(cls.ben, "Strom", "80.00")
        cls.secret = make_contract(cls.ben, "Geheim", "999.00")
        # Eigener Partner für den versteckten Vertrag
        cls.secret.provider = Provider.objects.create(owner=cls.ben, name="Geheimer Partner")
        cls.secret.save()
        perms.grant_role(cls.power, cls.anna, perms.READER)
        cls.url = reverse("contract_list")

    def setUp(self):
        self.client.force_login(self.anna)

    def names(self, response):
        return [row["contract"].name for row in response.context["rows"]]

    def test_only_visible_contracts_and_options(self):
        response = self.client.get(self.url)
        self.assertEqual(self.names(response), ["Auto", "Hausrat", "Strom"])
        self.assertEqual(response.context["totals"]["monthly"], Decimal("190.00"))
        self.assertNotContains(response, "Geheim")

    def test_search(self):
        response = self.client.get(self.url, {"q": "haus"})
        self.assertEqual(self.names(response), ["Hausrat"])
        self.assertEqual(response.context["totals"]["monthly"], Decimal("10.00"))
        # Suche nach Partner und Referenz
        self.assertEqual(self.names(self.client.get(self.url, {"q": "Partner ben"})), ["Strom"])
        self.assertEqual(self.names(self.client.get(self.url, {"q": self.car.reference})), ["Auto"])

    def test_search_does_not_find_hidden_contracts(self):
        self.assertEqual(self.names(self.client.get(self.url, {"q": "Geheim"})), [])

    def test_main_category_filter_includes_subcategories(self):
        main = self.household.category
        response = self.client.get(self.url, {"main_category": main.pk})
        self.assertEqual(self.names(response), ["Auto", "Hausrat"])
        self.assertEqual(response.context["totals"]["monthly"], Decimal("110.00"))

    def test_other_filters_and_invalid_values(self):
        response = self.client.get(self.url, {"consumer": self.power.consumer_id})
        self.assertEqual(self.names(response), ["Strom"])
        response = self.client.get(self.url, {"provider": "abc", "category": "-1"})
        self.assertEqual(len(response.context["rows"]), 3)

    def test_filter_for_hidden_object_shows_nothing(self):
        response = self.client.get(self.url, {"provider": self.secret.provider_id})
        self.assertEqual(self.names(response), [])

    def test_sorting(self):
        self.assertEqual(self.names(self.client.get(self.url, {"sort": "-monthly"})), ["Auto", "Strom", "Hausrat"])
        self.assertEqual(self.names(self.client.get(self.url, {"sort": "-name"})), ["Strom", "Hausrat", "Auto"])
        self.assertEqual(self.names(self.client.get(self.url, {"sort": "unbekannt"})), ["Auto", "Hausrat", "Strom"])

    def test_htmx_returns_only_results(self):
        response = self.client.get(self.url, {"q": "haus"}, HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(response, "web/contracts/_results.html")
        self.assertTemplateNotUsed(response, "web/contracts/list.html")
        self.assertNotContains(response, "<html")
        self.assertIn("HX-Request", response.headers["Vary"])

    def test_htmx_history_restore_returns_full_page(self):
        response = self.client.get(
            self.url, HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true",
        )
        self.assertTemplateUsed(response, "web/contracts/list.html")

    def test_sort_is_kept_when_filtering(self):
        response = self.client.get(self.url, {"sort": "-monthly"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, 'name="sort" value="-monthly" form="contract-filters"')


class ContractDetailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna")
        cls.ben = User.objects.create_user("ben")
        cls.carl = User.objects.create_user("carl")
        cls.contract = make_contract(cls.anna, "Strom", "80.00")
        today = timezone.localdate()
        CostEntry.objects.create(
            contract=cls.contract, amount=Decimal("70.00"),
            valid_from=today - timezone.timedelta(days=400), notes="Alter Tarif",
        )
        document = Document.objects.create(
            contract=cls.contract, name="Preiserhöhung", source="paperless", paperless_document_id=42,
        )
        CostEntry.objects.create(
            contract=cls.contract, amount=Decimal("95.00"),
            valid_from=today + timezone.timedelta(days=30), document=document,
        )
        perms.grant_role(cls.contract, cls.ben, perms.EDITOR)
        cls.url = reverse("contract_detail", args=[cls.contract.pk])

    def test_hidden_contract_returns_404(self):
        self.client.force_login(self.carl)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_owner_sees_history_and_roles(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.url)
        costs = response.context["costs"]
        self.assertEqual([c["entry"].amount for c in costs], [Decimal("95.00"), Decimal("80.00"), Decimal("70.00")])
        self.assertEqual([c["is_current"] for c in costs], [False, True, False])
        self.assertEqual([c["is_future"] for c in costs], [True, False, False])
        self.assertContains(response, "Alter Tarif")
        self.assertContains(response, "geplant")
        self.assertEqual(response.context["roles"]["editor"]["users"], {self.ben})
        self.assertContains(response, "Anzeigen, bearbeiten und löschen")

    def test_editor_does_not_see_shares(self):
        self.client.force_login(self.ben)
        response = self.client.get(self.url)
        self.assertIsNone(response.context["roles"])
        self.assertContains(response, "Anzeigen und bearbeiten")
        self.assertNotContains(response, "Leser")

    def test_paperless_link(self):
        self.client.force_login(self.anna)
        with self.settings(PAPERLESS_URL="https://paperless.example.de"):
            response = self.client.get(self.url)
        self.assertContains(response, "https://paperless.example.de/documents/42/details")

    def test_provider_link_only_if_provider_visible(self):
        self.contract.provider.paperless_correspondent_id = 5
        self.contract.provider.save()
        self.client.force_login(self.ben)
        with self.settings(PAPERLESS_URL="https://paperless.example.de"):
            response = self.client.get(self.url)
        self.assertContains(response, self.contract.provider.name)
        self.assertNotContains(response, "correspondent__id__in=5")


class ContractEditTest(TestCase):
    """Vertrag anlegen, bearbeiten und löschen je Rolle"""

    @classmethod
    def setUpTestData(cls):
        users_group = Group.objects.get(name=perms.GROUP_USERS)
        cls.owner = User.objects.create_user("eigentuemer")
        cls.owner.groups.add(users_group)
        cls.editor = User.objects.create_user("bearbeiter")
        cls.reader = User.objects.create_user("leser")
        cls.stranger = User.objects.create_user("fremd")
        cls.stranger.groups.add(users_group)
        cls.contract = make_contract(cls.owner, "Strom", "80.00")
        cls.hidden_provider = Provider.objects.create(owner=cls.stranger, name="Geheimer Partner")
        perms.grant_role(cls.contract, cls.editor, perms.EDITOR)
        perms.grant_role(cls.contract, cls.reader, perms.READER)

    def contract_data(self, **extra):
        data = {
            "name": "Gas",
            "provider": self.contract.provider_id,
            "category": self.contract.category_id,
            "consumer": self.contract.consumer_id,
            "start_date": "2026-01-01",
        }
        data.update(extra)
        return data

    def test_create_sets_owner_and_redirects_to_detail(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("contract_create"), self.contract_data())
        created = Contract.objects.get(name="Gas")
        self.assertRedirects(response, reverse("contract_detail", args=[created.pk]))
        self.assertEqual(created.owner, self.owner)
        self.assertTrue(created.reference)

    def test_create_without_consumer_and_start_date(self):
        """Verbraucher und Vertragsbeginn sind optional (0.9 beta)"""
        self.client.force_login(self.owner)
        response = self.client.post(reverse("contract_create"), self.contract_data(consumer="", start_date=""))
        created = Contract.objects.get(name="Gas")
        self.assertRedirects(response, reverse("contract_detail", args=[created.pk]))
        self.assertIsNone(created.consumer)
        self.assertIsNone(created.start_date)
        # Detail, Liste und die Seiten der Stammdaten kommen damit zurecht
        self.assertEqual(self.client.get(reverse("contract_detail", args=[created.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("contract_list")), "Gas")
        self.assertContains(self.client.get(reverse("provider_detail", args=[created.provider_id])), "Gas")
        self.assertContains(self.client.get(reverse("category_detail", args=[created.category_id])), "Gas")
        self.assertEqual(self.client.get(reverse("contract_edit", args=[created.pk])).status_code, 200)

    def test_create_requires_add_permission(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse("contract_create")).status_code, 403)
        response = self.client.get(reverse("contract_list"))
        self.assertNotContains(response, reverse("contract_create"))

    def test_create_offers_only_visible_master_data(self):
        self.client.force_login(self.owner)
        form = self.client.get(reverse("contract_create")).context["form"]
        self.assertNotIn(self.hidden_provider, form.fields["provider"].queryset)
        response = self.client.post(reverse("contract_create"), self.contract_data(provider=self.hidden_provider.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn("provider", response.context["form"].errors)

    def test_term_validation_shown_in_form(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("contract_create"), self.contract_data(term_value=12))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laufzeit und Laufzeiteinheit")

    def test_editor_can_edit_keeping_hidden_values(self):
        self.client.force_login(self.editor)
        url = reverse("contract_edit", args=[self.contract.pk])
        form = self.client.get(url).context["form"]
        self.assertIn(self.contract.provider, form.fields["provider"].queryset)
        response = self.client.post(url, self.contract_data(name="Strom neu"))
        self.assertRedirects(response, reverse("contract_detail", args=[self.contract.pk]))
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.name, "Strom neu")
        self.assertEqual(self.contract.owner, self.owner)

    def test_reader_cannot_edit_or_delete(self):
        self.client.force_login(self.reader)
        detail = self.client.get(reverse("contract_detail", args=[self.contract.pk]))
        self.assertNotContains(detail, reverse("contract_edit", args=[self.contract.pk]))
        self.assertNotContains(detail, reverse("cost_add", args=[self.contract.pk]))
        self.assertEqual(self.client.get(reverse("contract_edit", args=[self.contract.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("contract_delete", args=[self.contract.pk])).status_code, 403)

    def test_stranger_gets_404(self):
        self.client.force_login(self.stranger)
        for name in ("contract_edit", "contract_delete"):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.contract.pk])).status_code, 404)

    def test_only_owner_can_delete(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.client.post(reverse("contract_delete", args=[self.contract.pk])).status_code, 403)
        self.client.force_login(self.owner)
        response = self.client.post(reverse("contract_delete", args=[self.contract.pk]))
        self.assertRedirects(response, reverse("contract_list"))
        self.assertFalse(Contract.objects.filter(pk=self.contract.pk).exists())
        self.assertFalse(CostEntry.objects.filter(contract_id=self.contract.pk).exists())


class CostEntryEditTest(TestCase):
    """Kosteneinträge per HTMX und ohne JavaScript"""

    def test_cancel_does_not_use_document(self):
        # Fehler aus 0.7: Das Feld "document" verdeckte im onclick das
        # globale "document", Abbrechen tat nichts (Version 0.8a)
        self.client.force_login(self.owner)
        entry = self.contract.costs.get()
        for url in (reverse("cost_edit", args=[entry.pk]), reverse("cost_add", args=[self.contract.pk]),
                    reverse("document_add", args=[self.contract.pk])):
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_HX_REQUEST="true")
                self.assertNotContains(response, 'onclick="document.')
                self.assertContains(response, ".replaceChildren()")

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("eigentuemer")
        cls.editor = User.objects.create_user("bearbeiter")
        cls.reader = User.objects.create_user("leser")
        cls.stranger = User.objects.create_user("fremd")
        cls.contract = make_contract(cls.owner, "Strom", "80.00")
        cls.entry = cls.contract.costs.get()
        perms.grant_role(cls.contract, cls.editor, perms.EDITOR)
        perms.grant_role(cls.contract, cls.reader, perms.READER)
        cls.add_url = reverse("cost_add", args=[cls.contract.pk])

    def cost_data(self, **extra):
        data = {"amount": "95.50", "payment_interval": "monthly", "valid_from": "2020-01-01"}
        data.update(extra)
        return data

    def test_htmx_add_returns_costs_and_current_amount(self):
        self.client.force_login(self.editor)
        response = self.client.get(self.add_url, HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(response, "web/contracts/_cost_form.html")
        self.assertNotContains(response, "<html")

        response = self.client.post(
            self.add_url, self.cost_data(valid_from=str(timezone.localdate())), HTTP_HX_REQUEST="true",
        )
        self.assertTemplateUsed(response, "web/contracts/_costs.html")
        self.assertContains(response, 'id="current-amount" class="text-lg-end" hx-swap-oob="true"')
        self.assertContains(response, "95,50")
        self.assertEqual(self.contract.costs.count(), 2)

    def test_htmx_validation_error_retargets_form(self):
        self.client.force_login(self.editor)
        response = self.client.post(self.add_url, self.cost_data(amount=""), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["HX-Retarget"], "#cost-form")
        self.assertEqual(response.headers["HX-Reswap"], "innerHTML")
        self.assertContains(response, "is-invalid")
        self.assertEqual(self.contract.costs.count(), 1)

    def test_without_javascript(self):
        self.client.force_login(self.editor)
        response = self.client.get(self.add_url)
        self.assertTemplateUsed(response, "web/contracts/cost_form.html")
        self.assertNotContains(response, "hx-post")
        response = self.client.post(self.add_url, self.cost_data())
        self.assertRedirects(response, reverse("contract_detail", args=[self.contract.pk]))

    def test_edit_and_delete(self):
        self.client.force_login(self.owner)
        url = reverse("cost_edit", args=[self.entry.pk])
        response = self.client.post(url, self.cost_data(amount="12.34", notes="Neuer Tarif"), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Neuer Tarif")
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal("12.34"))

        response = self.client.post(reverse("cost_delete", args=[self.entry.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Noch keine Kosteneinträge")
        self.assertFalse(CostEntry.objects.filter(pk=self.entry.pk).exists())

    def test_reader_and_stranger_cannot_change_costs(self):
        for user, status in ((self.reader, 403), (self.stranger, 404)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.add_url).status_code, status)
                self.assertEqual(self.client.post(self.add_url, self.cost_data()).status_code, status)
                self.assertEqual(self.client.post(reverse("cost_edit", args=[self.entry.pk]), self.cost_data()).status_code, status)
                self.assertEqual(self.client.post(reverse("cost_delete", args=[self.entry.pk])).status_code, status)
        self.assertEqual(self.contract.costs.count(), 1)

    def test_delete_requires_post(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("cost_delete", args=[self.entry.pk])).status_code, 405)


class CategoryOrderTest(TestCase):
    def test_main_category_before_its_subcategories(self):
        from contracts.models import category_tree_order
        owner = User.objects.create_user("anna")
        insurance = Category.objects.create(owner=owner, name="Versicherung")
        Category.objects.create(owner=owner, name="KFZ-Versicherung", parent=insurance)
        Category.objects.create(owner=owner, name="Haftpflicht", parent=insurance)
        Category.objects.create(owner=owner, name="Abos")
        self.assertEqual(
            [str(c) for c in category_tree_order(Category.objects.all())],
            ["Abos", "Versicherung", "Versicherung / Haftpflicht", "Versicherung / KFZ-Versicherung"],
        )


class ContractSharingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("eigentuemer")
        cls.editor = User.objects.create_user("bearbeiter")
        cls.reader = User.objects.create_user("leser")
        cls.stranger = User.objects.create_user("fremd")
        cls.admin = User.objects.create_superuser("admin", "admin@example.com", "test")
        cls.family = Group.objects.create(name="Familie")
        cls.contract = make_contract(cls.owner, "Strom", "80.00")
        perms.grant_role(cls.contract, cls.editor, perms.EDITOR)
        perms.grant_role(cls.contract, cls.reader, perms.READER)
        cls.url = reverse("contract_sharing", args=[cls.contract.pk])

    def post_data(self, owner=None, **roles):
        data = {"owner": (owner or self.owner).pk}
        data.update(roles)
        return data

    def test_access_per_role(self):
        for user, status in (
            (self.owner, 200), (self.admin, 200), (self.editor, 403), (self.reader, 403), (self.stranger, 404),
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, status)

    def test_button_only_for_owner(self):
        self.client.force_login(self.editor)
        self.assertNotContains(self.client.get(reverse("contract_detail", args=[self.contract.pk])), self.url)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("contract_detail", args=[self.contract.pk])), self.url)

    def test_form_shows_current_roles(self):
        self.client.force_login(self.owner)
        form = self.client.get(self.url).context["form"]
        self.assertEqual(form[f"user_{self.editor.pk}"].initial, perms.EDITOR)
        self.assertEqual(form[f"user_{self.reader.pk}"].initial, perms.READER)
        self.assertEqual(form[f"user_{self.stranger.pk}"].initial, "")

    def test_save_roles(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.post_data(**{
            f"user_{self.editor.pk}": perms.READER,       # Bearbeiter wird Leser
            f"user_{self.reader.pk}": "",                 # Leser verliert Zugriff
            f"group_{self.family.pk}": perms.EDITOR,      # Gruppe wird Bearbeiter
        }))
        self.assertRedirects(response, reverse("contract_detail", args=[self.contract.pk]))
        roles = perms.get_roles(self.contract)
        self.assertEqual(roles["reader"]["users"], {self.editor})
        self.assertEqual(roles["editor"]["users"], set())
        self.assertEqual(roles["editor"]["groups"], {self.family})

    def test_invalid_role_is_rejected(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.post_data(**{f"user_{self.reader.pk}": "owner"}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(perms.get_roles(self.contract)["reader"]["users"], {self.reader})

    def test_owner_row_shows_full_access_without_choice(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertNotIn(f"user_{self.owner.pk}", response.context["form"].fields)
        self.assertContains(response, "Vollzugriff als Eigentümer")

    def test_hand_over_ownership_previous_owner_becomes_editor(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.post_data(owner=self.reader, **{
            f"user_{self.editor.pk}": perms.EDITOR,
            f"user_{self.reader.pk}": perms.READER,   # neuer Eigentümer braucht keine Rolle
        }))
        self.assertRedirects(response, reverse("contract_detail", args=[self.contract.pk]))
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.owner, self.reader)
        roles = perms.get_roles(self.contract)
        self.assertEqual(roles["editor"]["users"], {self.owner, self.editor})
        self.assertEqual(roles["reader"]["users"], set())
        # Bisheriger Eigentümer darf noch bearbeiten, aber nicht mehr freigeben
        self.assertEqual(self.client.get(reverse("contract_edit", args=[self.contract.pk])).status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 403)


class ProviderPagesTest(TestCase):
    """Vertragspartner im Frontend: Liste, Detail, Anlegen, Bearbeiten, Löschen, Freigaben"""

    @classmethod
    def setUpTestData(cls):
        users_group = Group.objects.get(name=perms.GROUP_USERS)
        cls.owner = User.objects.create_user("eigentuemer")
        cls.owner.groups.add(users_group)
        cls.editor = User.objects.create_user("bearbeiter")
        cls.reader = User.objects.create_user("leser")
        cls.stranger = User.objects.create_user("fremd")
        cls.stranger.groups.add(users_group)
        cls.admin = User.objects.create_superuser("admin", "admin@example.com", "test")

        cls.contract = make_contract(cls.owner, "Strom", "80.00")              # 80,00 / Monat
        cls.provider = cls.contract.provider
        cls.provider.city = "Köln"
        cls.provider.customer_number = "K-4711"
        cls.provider.save()
        make_contract(cls.owner, "Gas", "120.00", "quarterly")                 # 40,00 / Monat
        cls.unused = Provider.objects.create(owner=cls.owner, name="Ohne Verträge")
        cls.hidden = Provider.objects.create(owner=cls.stranger, name="Geheimer Partner")

        for obj in (cls.provider, cls.unused):
            perms.grant_role(obj, cls.editor, perms.EDITOR)
            perms.grant_role(obj, cls.reader, perms.READER)
        # Der Leser sieht nur einen der beiden Verträge
        perms.grant_role(cls.contract, cls.reader, perms.READER)

    def provider_data(self, **extra):
        data = {"name": "Stadtwerke", "country": "DE"}
        data.update(extra)
        return data

    def test_pages_require_login(self):
        url = reverse("provider_list")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_list_only_visible_providers(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("provider_list"))
        names = [row["object"].name for row in response.context["rows"]]
        self.assertEqual(names, ["Ohne Verträge", "Partner eigentuemer"])
        self.assertNotContains(response, "Geheimer Partner")

    def test_counts_and_sums_only_visible_contracts(self):
        self.client.force_login(self.reader)
        rows = {row["object"].pk: row for row in self.client.get(reverse("provider_list")).context["rows"]}
        self.assertEqual(rows[self.provider.pk]["contract_count"], 1)
        self.assertEqual(rows[self.provider.pk]["monthly"], Decimal("80.00"))

        self.client.force_login(self.owner)
        rows = {row["object"].pk: row for row in self.client.get(reverse("provider_list")).context["rows"]}
        self.assertEqual(rows[self.provider.pk]["contract_count"], 2)
        self.assertEqual(rows[self.provider.pk]["monthly"], Decimal("120.00"))
        self.assertEqual(rows[self.unused.pk]["contract_count"], 0)

    def test_search_and_sort(self):
        self.client.force_login(self.owner)
        for q in ("köln", "4711", "partner eig"):
            with self.subTest(q=q):
                rows = self.client.get(reverse("provider_list"), {"q": q}).context["rows"]
                self.assertEqual([row["object"] for row in rows], [self.provider])
        rows = self.client.get(reverse("provider_list"), {"q": "Geheim"}).context["rows"]
        self.assertEqual(rows, [])
        rows = self.client.get(reverse("provider_list"), {"sort": "-monthly"}).context["rows"]
        self.assertEqual(rows[0]["object"], self.provider)
        rows = self.client.get(reverse("provider_list"), {"sort": "unbekannt"}).context["rows"]
        self.assertEqual(rows[0]["object"], self.unused)

    def test_htmx_returns_only_results(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("provider_list"), {"q": "köln"}, headers={"HX-Request": "true"})
        self.assertTemplateUsed(response, "web/providers/_results.html")
        self.assertTemplateNotUsed(response, "web/base.html")
        self.assertIn("HX-Request", response.headers["Vary"])

    def test_detail_shows_visible_contracts(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("provider_detail", args=[self.provider.pk]))
        self.assertEqual([row["contract"] for row in response.context["rows"]], [self.contract])
        self.assertEqual(response.context["totals"]["monthly"], Decimal("80.00"))
        self.assertNotContains(response, "Gas")
        self.assertNotContains(response, reverse("provider_edit", args=[self.provider.pk]))

    def test_detail_access(self):
        url = reverse("provider_detail", args=[self.hidden.pk])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_contract_detail_links_visible_provider(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("contract_detail", args=[self.contract.pk]))
        self.assertContains(response, reverse("provider_detail", args=[self.provider.pk]))

    def test_create_sets_owner(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("provider_create"), self.provider_data(email="info@example.com"))
        created = Provider.objects.get(name="Stadtwerke")
        self.assertRedirects(response, reverse("provider_detail", args=[created.pk]))
        self.assertEqual(created.owner, self.owner)

    def test_create_requires_add_permission(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse("provider_create")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("provider_list")), reverse("provider_create"))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("contract_create")), reverse("provider_create"))

    def test_duplicate_name_per_owner_is_a_form_error(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("provider_create"), self.provider_data(name="Ohne Verträge"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Der Eigentümer hat bereits einen Vertragspartner mit diesem Namen.")
        # Gleicher Name bei einem anderen Eigentümer ist erlaubt
        response = self.client.post(reverse("provider_create"), self.provider_data(name="Geheimer Partner"))
        self.assertEqual(response.status_code, 302)

    def test_editor_duplicate_name_checks_owner(self):
        self.client.force_login(self.editor)
        url = reverse("provider_edit", args=[self.unused.pk])
        response = self.client.post(url, self.provider_data(name="Partner eigentuemer"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())

    def test_paperless_id_must_be_positive(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("provider_create"), self.provider_data(paperless_correspondent_id="0"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("paperless_correspondent_id", response.context["form"].errors)

    def test_edit_per_role(self):
        url = reverse("provider_edit", args=[self.unused.pk])
        for user, status in ((self.editor, 302), (self.reader, 403), (self.stranger, 404)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(url, self.provider_data(name=f"Neu {user.username}"))
                self.assertEqual(response.status_code, status)
        self.unused.refresh_from_db()
        self.assertEqual(self.unused.name, "Neu bearbeiter")
        self.assertEqual(self.unused.owner, self.owner)

    def test_delete_only_owner(self):
        url = reverse("provider_delete", args=[self.unused.pk])
        for user in (self.editor, self.reader):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.post(url), reverse("provider_list"))
        self.assertFalse(Provider.objects.filter(pk=self.unused.pk).exists())

    def test_delete_in_use_is_refused(self):
        self.client.force_login(self.owner)
        url = reverse("provider_delete", args=[self.provider.pk])
        response = self.client.get(url)
        self.assertTrue(response.context["in_use"])
        self.assertNotContains(response, 'class="btn btn-danger"')
        response = self.client.post(url)
        self.assertRedirects(response, reverse("provider_detail", args=[self.provider.pk]))
        self.assertTrue(Provider.objects.filter(pk=self.provider.pk).exists())

    def test_sharing_per_role(self):
        url = reverse("provider_sharing", args=[self.provider.pk])
        for user, status in (
            (self.owner, 200), (self.admin, 200), (self.editor, 403), (self.reader, 403), (self.stranger, 404),
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, status)

    def test_sharing_save_and_hand_over(self):
        self.client.force_login(self.owner)
        url = reverse("provider_sharing", args=[self.unused.pk])
        response = self.client.post(url, {
            "owner": self.editor.pk,
            f"user_{self.reader.pk}": "",
            f"user_{self.stranger.pk}": perms.READER,
        })
        self.assertRedirects(response, reverse("provider_detail", args=[self.unused.pk]))
        self.unused.refresh_from_db()
        self.assertEqual(self.unused.owner, self.editor)
        roles = perms.get_roles(self.unused)
        self.assertEqual(roles["editor"]["users"], {self.owner})
        self.assertEqual(roles["reader"]["users"], {self.stranger})


class ConsumerPagesTest(TestCase):
    """Version 0.3d beta: Verbraucher im Frontend"""

    @classmethod
    def setUpTestData(cls):
        users_group = Group.objects.get(name=perms.GROUP_USERS)
        cls.owner = User.objects.create_user("eigentuemer")
        cls.owner.groups.add(users_group)
        cls.editor = User.objects.create_user("bearbeiter")
        cls.reader = User.objects.create_user("leser")
        cls.stranger = User.objects.create_user("fremd")
        cls.stranger.groups.add(users_group)

        cls.contract = make_contract(cls.owner, "Strom", "80.00")
        cls.consumer = cls.contract.consumer
        cls.consumer.number = "K-AB 123"
        cls.consumer.save()
        make_contract(cls.owner, "Gas", "40.00")
        cls.unused = Consumer.objects.create(owner=cls.owner, name="Auto")
        cls.hidden = Consumer.objects.create(owner=cls.stranger, name="Geheimes Auto")
        for obj in (cls.consumer, cls.unused):
            perms.grant_role(obj, cls.editor, perms.EDITOR)
            perms.grant_role(obj, cls.reader, perms.READER)
        perms.grant_role(cls.contract, cls.reader, perms.READER)

    def test_list_counts_only_visible_contracts(self):
        self.client.force_login(self.reader)
        rows = {row["object"].pk: row for row in self.client.get(reverse("consumer_list")).context["rows"]}
        self.assertEqual(set(rows), {self.consumer.pk, self.unused.pk})
        self.assertEqual(rows[self.consumer.pk]["contract_count"], 1)
        self.assertEqual(rows[self.consumer.pk]["monthly"], Decimal("80.00"))

    def test_search_by_number(self):
        self.client.force_login(self.owner)
        rows = self.client.get(reverse("consumer_list"), {"q": "ab 1"}).context["rows"]
        self.assertEqual([row["object"] for row in rows], [self.consumer])

    def test_detail(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("consumer_detail", args=[self.consumer.pk]))
        self.assertEqual([row["contract"] for row in response.context["rows"]], [self.contract])
        self.assertContains(response, "K-AB 123")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("consumer_detail", args=[self.hidden.pk])).status_code, 404)

    def test_create_edit_delete(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("consumer_create"), {"name": "Haus", "number": "007"})
        created = Consumer.objects.get(name="Haus")
        self.assertRedirects(response, reverse("consumer_detail", args=[created.pk]))
        self.assertEqual((created.owner, created.number), (self.owner, "007"))

        response = self.client.post(reverse("consumer_create"), {"name": "Auto"})
        self.assertContains(response, "Der Eigentümer hat bereits einen Verbraucher mit diesem Namen.")

        self.client.force_login(self.editor)
        url = reverse("consumer_edit", args=[self.unused.pk])
        self.assertEqual(self.client.post(url, {"name": "Neues Auto"}).status_code, 302)
        self.assertEqual(self.client.post(reverse("consumer_delete", args=[self.unused.pk])).status_code, 403)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(url, {"name": "X"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("consumer_create")).status_code, 403)

        self.client.force_login(self.owner)
        self.assertRedirects(self.client.post(reverse("consumer_delete", args=[self.unused.pk])),
                             reverse("consumer_list"))
        self.assertFalse(Consumer.objects.filter(pk=self.unused.pk).exists())

    def test_delete_in_use_is_refused(self):
        self.client.force_login(self.owner)
        url = reverse("consumer_delete", args=[self.consumer.pk])
        self.assertTrue(self.client.get(url).context["in_use"])
        self.assertRedirects(self.client.post(url), reverse("consumer_detail", args=[self.consumer.pk]))
        self.assertTrue(Consumer.objects.filter(pk=self.consumer.pk).exists())

    def test_sharing_per_role(self):
        url = reverse("consumer_sharing", args=[self.consumer.pk])
        for user, status in ((self.owner, 200), (self.editor, 403), (self.reader, 403), (self.stranger, 404)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, status)


class CategoryPagesTest(TestCase):
    """Version 0.3d beta: Kategorien im Frontend"""

    @classmethod
    def setUpTestData(cls):
        users_group = Group.objects.get(name=perms.GROUP_USERS)
        cls.owner = User.objects.create_user("eigentuemer")
        cls.owner.groups.add(users_group)
        cls.reader = User.objects.create_user("leser")
        cls.stranger = User.objects.create_user("fremd")
        cls.stranger.groups.add(users_group)

        cls.insurance = Category.objects.create(owner=cls.owner, name="Versicherung")
        cls.car = Category.objects.create(owner=cls.owner, name="KFZ", parent=cls.insurance)
        cls.home = Category.objects.create(owner=cls.owner, name="Wohnen")
        cls.hidden = Category.objects.create(owner=cls.stranger, name="Geheim")
        cls.house = make_contract(cls.owner, "Hausrat", "10.00", category=cls.insurance)
        cls.auto = make_contract(cls.owner, "Auto", "100.00", category=cls.car)
        for obj in (cls.insurance, cls.car):
            perms.grant_role(obj, cls.reader, perms.READER)
        perms.grant_role(cls.auto, cls.reader, perms.READER)

    def test_list_is_tree_with_totals_including_subcategories(self):
        self.client.force_login(self.owner)
        rows = self.client.get(reverse("category_list")).context["rows"]
        self.assertEqual([row["object"].name for row in rows], ["Versicherung", "KFZ", "Wohnen"])
        self.assertEqual([row["indent"] for row in rows], [False, True, False])
        self.assertEqual(rows[0]["contract_count"], 2)
        self.assertEqual(rows[0]["monthly"], Decimal("110.00"))
        self.assertEqual(rows[1]["monthly"], Decimal("100.00"))

    def test_list_only_visible(self):
        self.client.force_login(self.reader)
        rows = self.client.get(reverse("category_list")).context["rows"]
        self.assertEqual([row["object"] for row in rows], [self.insurance, self.car])
        self.assertEqual(rows[0]["contract_count"], 1)

    def test_search_shows_subcategory_with_main_name(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("category_list"), {"q": "kfz"})
        self.assertEqual([row["object"] for row in response.context["rows"]], [self.car])
        self.assertFalse(response.context["rows"][0]["indent"])
        self.assertContains(response, "Versicherung / KFZ")

    def test_detail_of_main_category(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("category_detail", args=[self.insurance.pk]))
        self.assertEqual({row["contract"] for row in response.context["rows"]}, {self.house, self.auto})
        self.assertEqual(list(response.context["children"]), [self.car])
        self.assertContains(response, f"{reverse('category_create')}?parent={self.insurance.pk}")

    def test_create_subcategory(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("category_create"), {"parent": self.insurance.pk})
        form = response.context["form"]
        self.assertEqual(form.initial["parent"], self.insurance.pk)
        # Nur sichtbare Hauptkategorien, keine Unterkategorien
        self.assertEqual(set(form.fields["parent"].queryset), {self.insurance, self.home})

        response = self.client.post(reverse("category_create"), {"name": "Haftpflicht", "parent": self.insurance.pk})
        created = Category.objects.get(name="Haftpflicht")
        self.assertRedirects(response, reverse("category_detail", args=[created.pk]))
        self.assertEqual((created.owner, created.parent), (self.owner, self.insurance))

    def test_duplicate_name_within_same_main_category(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("category_create"), {"name": "KFZ", "parent": self.insurance.pk})
        self.assertContains(response, "Der Eigentümer hat bereits eine Kategorie mit diesem Namen in dieser Hauptkategorie.")
        response = self.client.post(reverse("category_create"), {"name": "Wohnen"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("category_create"), {"name": "KFZ", "parent": self.home.pk})
        self.assertEqual(response.status_code, 302)

    def test_category_with_children_cannot_become_subcategory(self):
        self.client.force_login(self.owner)
        url = reverse("category_edit", args=[self.insurance.pk])
        form = self.client.get(url).context["form"]
        self.assertNotIn(self.insurance, form.fields["parent"].queryset)
        response = self.client.post(url, {"name": "Versicherung", "parent": self.home.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Eine Kategorie mit Unterkategorien kann nicht selbst Unterkategorie werden.")
        self.insurance.refresh_from_db()
        self.assertIsNone(self.insurance.parent)

    def test_delete_rules(self):
        self.client.force_login(self.owner)
        # Mit Unterkategorie bzw. Verträgen: abgelehnt
        for category in (self.insurance, self.car):
            with self.subTest(category=category.name):
                url = reverse("category_delete", args=[category.pk])
                self.assertTrue(self.client.get(url).context["in_use"])
                self.client.post(url)
                self.assertTrue(Category.objects.filter(pk=category.pk).exists())
        self.assertRedirects(self.client.post(reverse("category_delete", args=[self.home.pk])),
                             reverse("category_list"))
        self.assertFalse(Category.objects.filter(pk=self.home.pk).exists())

    def test_reader_cannot_change(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(reverse("category_edit", args=[self.car.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("category_sharing", args=[self.car.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("category_detail", args=[self.home.pk])).status_code, 404)


class NavigationTest(TestCase):
    """Version 0.3d beta: Menü Stammdaten, Verwaltung nur für Superuser"""

    def test_admin_link_only_for_superuser(self):
        staff = User.objects.create_user("mitarbeiter", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, reverse("admin:index"))
        for name in ("provider_list", "category_list", "consumer_list"):
            self.assertContains(response, reverse(name))

        admin = User.objects.create_superuser("admin", "admin@example.com", "test")
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("dashboard")), reverse("admin:index"))

    def test_contract_page_links_visible_master_data(self):
        owner = User.objects.create_user("anna")
        contract = make_contract(owner, "Strom", "80.00")
        self.client.force_login(owner)
        response = self.client.get(reverse("contract_detail", args=[contract.pk]))
        self.assertContains(response, reverse("category_detail", args=[contract.category_id]))
        self.assertContains(response, reverse("consumer_detail", args=[contract.consumer_id]))

        reader = User.objects.create_user("ben")
        perms.grant_role(contract, reader, perms.READER)
        self.client.force_login(reader)
        response = self.client.get(reverse("contract_detail", args=[contract.pk]))
        self.assertNotContains(response, reverse("consumer_detail", args=[contract.consumer_id]))
        self.assertContains(response, contract.consumer.name)

    def test_contract_form_links_to_create_master_data(self):
        user = User.objects.create_user("anna")
        user.groups.add(Group.objects.get(name=perms.GROUP_USERS))
        self.client.force_login(user)
        response = self.client.get(reverse("contract_create"))
        for name in ("provider_create", "category_create", "consumer_create"):
            self.assertContains(response, reverse(name))
        self.assertNotContains(response, "in der Verwaltung gepflegt")


class ProviderLogoPagesTest(TestCase):
    """Logo im Frontend: Hochladen, Entfernen, Ausliefern nur mit Leserecht (0.4 beta)"""

    def setUp(self):
        import shutil
        import tempfile

        from django.test import override_settings

        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media_root)
        override.enable()
        self.addCleanup(override.disable)

        users_group = Group.objects.get(name=perms.GROUP_USERS)
        self.owner = User.objects.create_user("eigentuemer")
        self.owner.groups.add(users_group)
        self.reader = User.objects.create_user("leser")
        self.stranger = User.objects.create_user("fremd")
        self.contract = make_contract(self.owner, "Strom", "80.00")
        self.provider = self.contract.provider
        perms.grant_role(self.provider, self.reader, perms.READER)

    def image(self, image_format="PNG", name="logo.png"):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        output = BytesIO()
        Image.new("RGB", (400, 200), "blue").save(output, format=image_format)
        return SimpleUploadedFile(name, output.getvalue())

    def post_edit(self, **data):
        form = {"name": self.provider.name, "country": "DE"}
        form.update(data)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("provider_edit", args=[self.provider.pk]), form)

    def upload_logo(self):
        self.client.force_login(self.owner)
        response = self.post_edit(logo=self.image())
        self.assertRedirects(response, reverse("provider_detail", args=[self.provider.pk]))
        self.provider.refresh_from_db()
        self.assertTrue(self.provider.logo)
        return self.provider.logo

    def test_form_is_multipart(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("provider_edit", args=[self.provider.pk]))
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'accept="image/png,image/jpeg,image/webp"')

    def test_upload_shows_logo_on_pages(self):
        logo = self.upload_logo()
        for url in (
            reverse("provider_detail", args=[self.provider.pk]),
            reverse("provider_list"),
            reverse("contract_list"),
            reverse("contract_detail", args=[self.contract.pk]),
        ):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), logo.url)

    def test_invalid_upload_shows_error(self):
        self.client.force_login(self.owner)
        response = self.post_edit(logo=self.image("GIF", "logo.gif"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nur PNG-, JPEG- und WebP-Bilder")
        self.provider.refresh_from_db()
        self.assertFalse(self.provider.logo)

    def test_remove_logo(self):
        import os

        path = self.upload_logo().path
        response = self.post_edit(remove_logo="on")
        self.assertEqual(response.status_code, 302)
        self.provider.refresh_from_db()
        self.assertFalse(self.provider.logo)
        self.assertFalse(os.path.exists(path))

    def test_new_upload_wins_over_remove(self):
        old = self.upload_logo().name
        self.post_edit(remove_logo="on", logo=self.image())
        self.provider.refresh_from_db()
        self.assertTrue(self.provider.logo)
        self.assertNotEqual(self.provider.logo.name, old)

    def test_logo_is_served_only_with_view_permission(self):
        url = self.upload_logo().url
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        for user, status in ((self.owner, 200), (self.reader, 200), (self.stranger, 404)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, status)
                if status == 200:
                    self.assertEqual(response["Content-Type"], "image/png")
                    self.assertIn("private", response["Cache-Control"])
                    b"".join(response.streaming_content)

    def test_unknown_or_other_media_files_are_not_served(self):
        self.client.force_login(self.owner)
        for url in ("/media/logos/unbekannt.png", "/media/anderes/datei.png", "/media/logos/../x.png"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_contract_reader_without_provider_access_sees_no_logo(self):
        url = self.upload_logo().url
        perms.grant_role(self.contract, self.stranger, perms.READER)
        self.client.force_login(self.stranger)
        for page in (reverse("contract_list"), reverse("contract_detail", args=[self.contract.pk])):
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertContains(response, self.provider.name)
                self.assertNotContains(response, url)

    def test_admin_shows_logo(self):
        url = self.upload_logo().url
        admin_user = User.objects.create_superuser("admin", "admin@example.com", "test")
        self.client.force_login(admin_user)
        response = self.client.get(reverse("admin:contracts_provider_change", args=[self.provider.pk]))
        self.assertContains(response, url)
        self.assertContains(self.client.get(reverse("admin:contracts_provider_changelist")), url)


class LayoutTest(TestCase):
    """Gerüst mit Seitenleiste und lokalen Icons (0.4 beta)"""

    def test_sidebar_and_local_assets(self):
        user = User.objects.create_user("anna")
        self.client.force_login(user)
        response = self.client.get(reverse("contract_list"))
        self.assertContains(response, 'id="sidebar"')
        self.assertContains(response, "/static/web/vendor/bootstrap-icons/bootstrap-icons.min.css")
        self.assertContains(response, 'class="app-nav-link active" href="/contracts/"')
        self.assertNotContains(response, "cdn.")

    def test_login_page_without_sidebar(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "auth-card")
        self.assertNotContains(response, 'id="sidebar"')

    def test_dashboard_logo_only_for_visible_provider(self):
        owner = User.objects.create_user("eigentuemer")
        reader = User.objects.create_user("leser")
        contract = make_contract(owner, "Strom", "80.00")
        Provider.objects.filter(pk=contract.provider_id).update(logo="logos/test.png")
        perms.grant_role(contract, reader, perms.READER)

        self.client.force_login(owner)
        self.assertContains(self.client.get(reverse("dashboard")), "/media/logos/test.png")
        self.client.force_login(reader)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Strom")
        self.assertNotContains(response, "/media/logos/test.png")


SSO_SETTINGS = {
    "OIDC_ENABLED": True,
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://nas.example.test/webman/sso/SSOOauth.cgi",
    "OIDC_OP_TOKEN_ENDPOINT": "https://nas.example.test/webman/sso/SSOAccessToken.cgi",
    "OIDC_OP_USER_ENDPOINT": "https://nas.example.test/webman/sso/SSOUserInfo.cgi",
    "OIDC_OP_JWKS_ENDPOINT": "https://nas.example.test/webman/sso/openid-jwks.json",
    "OIDC_RP_CLIENT_ID": "contracts",
    "OIDC_RP_CLIENT_SECRET": "test-secret",
    "AUTHENTICATION_BACKENDS": [
        "django.contrib.auth.backends.ModelBackend",
        "web.oidc.SSOBackend",
        "contracts.backends.OwnerBackend",
        "guardian.backends.ObjectPermissionBackend",
    ],
}


class SSOTest(TestCase):
    """Single Sign-on über OpenID Connect, z. B. Synology SSO Server (0.4 beta)"""

    def backend(self):
        from .oidc import SSOBackend

        return SSOBackend()

    def test_disabled_by_default(self):
        from django.conf import settings

        self.assertFalse(settings.OIDC_ENABLED)
        self.assertNotIn("web.oidc.SSOBackend", settings.AUTHENTICATION_BACKENDS)
        for url in ("/oidc/authenticate/", "/oidc/callback/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("login")), "/oidc/authenticate/")

    def test_login_page_shows_sso_button(self):
        with self.settings(**SSO_SETTINGS):
            response = self.client.get(reverse("login") + "?next=/contracts/")
        self.assertContains(response, "/oidc/authenticate/?next=/contracts/")
        self.assertContains(response, "SSO Login")

    def test_sso_button_label_from_settings(self):
        from .models import SiteSettings

        SiteSettings.objects.create(sso_login_label="Mit Synology anmelden")
        with self.settings(**SSO_SETTINGS):
            response = self.client.get(reverse("login"))
        self.assertContains(response, "Mit Synology anmelden")
        self.assertNotContains(response, "SSO Login")

    def test_authenticate_redirects_to_provider(self):
        with self.settings(**SSO_SETTINGS):
            response = self.client.get("/oidc/authenticate/")
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith(SSO_SETTINGS["OIDC_OP_AUTHORIZATION_ENDPOINT"]))
        self.assertIn("client_id=contracts", location)
        self.assertIn("redirect_uri=http%3A%2F%2Ftestserver%2Foidc%2Fcallback%2F", location)

    def test_matches_username_case_insensitive(self):
        user = User.objects.create_user("Anna")
        with self.settings(**SSO_SETTINGS):
            backend = self.backend()
            self.assertEqual(list(backend.filter_users_by_claims({"username": "anna"})), [user])
            self.assertFalse(backend.filter_users_by_claims({"email": "anna@example.com"}).exists())
            self.assertFalse(backend.verify_claims({"email": "anna@example.com"}))
            self.assertTrue(backend.verify_claims({"username": "anna"}))

    def test_unknown_user_is_not_created_by_default(self):
        from unittest.mock import patch

        with self.settings(**SSO_SETTINGS, OIDC_CREATE_USER=False):
            backend = self.backend()
            with patch.object(backend, "get_userinfo", return_value={"username": "neu"}):
                self.assertIsNone(backend.get_or_create_user("access", "id", {}))
        self.assertFalse(User.objects.filter(username="neu").exists())

    def test_unknown_user_is_created_when_allowed(self):
        from unittest.mock import patch

        with self.settings(**SSO_SETTINGS, OIDC_CREATE_USER=True):
            backend = self.backend()
            claims = {"username": "neu", "email": "neu@example.com"}
            with patch.object(backend, "get_userinfo", return_value=claims):
                user = backend.get_or_create_user("access", "id", {})
        self.assertEqual(user.username, "neu")
        self.assertEqual(user.email, "neu@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(
            set(user.groups.values_list("name", flat=True)), {perms.GROUP_USERS, perms.GROUP_ALL}
        )
        self.assertTrue(user.has_perm("contracts.add_contract"))

    def test_claims_from_id_token_and_userinfo_are_merged(self):
        from unittest.mock import patch

        from mozilla_django_oidc.auth import OIDCAuthenticationBackend

        with self.settings(**SSO_SETTINGS):
            backend = self.backend()
            with patch.object(OIDCAuthenticationBackend, "get_userinfo", return_value={"email": "a@example.com"}):
                claims = backend.get_userinfo("access", "id", {"username": "anna", "sub": "1"})
        self.assertEqual(claims, {"username": "anna", "sub": "1", "email": "a@example.com"})

    def login_via_callback(self, username):
        """
        Kompletter Ablauf: Anmeldung starten, Rückkehr mit Code und State.
        Token und Userinfo des Anbieters werden simuliert.
        """
        from urllib.parse import parse_qs, urlparse
        from unittest.mock import patch

        from mozilla_django_oidc.auth import OIDCAuthenticationBackend

        from .oidc import SSOBackend

        with self.settings(**SSO_SETTINGS):
            start = self.client.get("/oidc/authenticate/?next=/contracts/")
            query = parse_qs(urlparse(start["Location"]).query)
            state, nonce = query["state"][0], query["nonce"][0]
            with patch.object(SSOBackend, "get_token", return_value={"id_token": "id", "access_token": "a"}), \
                    patch.object(SSOBackend, "verify_token", return_value={"username": username, "nonce": nonce}), \
                    patch.object(OIDCAuthenticationBackend, "get_userinfo", return_value={}):
                return self.client.get("/oidc/callback/", {"code": "abc", "state": state}, follow=True)

    def test_callback_logs_in_existing_user(self):
        User.objects.create_user("anna")
        response = self.login_via_callback("anna")
        self.assertRedirects(response, reverse("contract_list"))
        self.assertEqual(response.context["user"].username, "anna")

    def test_callback_rejects_unknown_and_inactive_users(self):
        User.objects.create_user("inaktiv", is_active=False)
        for username in ("unbekannt", "inaktiv"):
            with self.subTest(username=username):
                response = self.login_via_callback(username)
                self.assertRedirects(response, reverse("login"))
                self.assertFalse(response.context["user"].is_authenticated)
                self.assertContains(response, "Die Anmeldung über Single Sign-on ist fehlgeschlagen")

    def test_sso_user_has_no_password_change(self):
        user = User.objects.create_user("sso")
        user.set_unusable_password()
        user.save()
        self.client.force_login(user)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("password_change"))
        self.assertRedirects(self.client.get(reverse("password_change")), reverse("preferences"))


class PasswordResetTest(TestCase):
    """Passwort zurücksetzen per E-Mail (0.5 beta)"""

    def setUp(self):
        self.user = User.objects.create_user("anna", "anna@example.com", "altes-Passwort-1")

    def request_reset(self, email):
        from django.core import mail

        mail.outbox.clear()
        response = self.client.post(reverse("password_reset"), {"email": email})
        self.assertRedirects(response, reverse("password_reset_done"))
        return mail.outbox

    def test_login_page_links_to_reset(self):
        self.assertContains(self.client.get(reverse("login")), reverse("password_reset"))

    def test_reset_mail_in_user_language_with_link(self):
        outbox = self.request_reset("ANNA@example.com")
        self.assertEqual(len(outbox), 1)
        message = outbox[0]
        self.assertEqual(message.to, ["anna@example.com"])
        self.assertIn("Passwort zurücksetzen", message.subject)
        self.assertIn("http://testserver/accounts/password/reset/", message.body)
        html = message.alternatives[0][0]
        self.assertIn("Neues Passwort wählen", html)

        UserPreferences.objects.update_or_create(user=self.user, defaults={"language": "en"})
        outbox = self.request_reset("anna@example.com")
        self.assertIn("Reset your password", outbox[0].subject)

    def test_no_mail_for_unknown_or_sso_users(self):
        sso = User.objects.create_user("sso", "sso@example.com")
        sso.set_unusable_password()
        sso.save()
        self.assertEqual(self.request_reset("unbekannt@example.com"), [])
        self.assertEqual(self.request_reset("sso@example.com"), [])

    def test_set_new_password_with_link(self):
        import re

        message = self.request_reset("anna@example.com")[0]
        path = re.search(r"http://testserver(/accounts/password/reset/\S+/\S+/)", message.body).group(1)
        response = self.client.get(path, follow=True)
        self.assertContains(response, "Neues Passwort wählen")
        self.assertNotContains(response, 'id="sidebar"')
        response = self.client.post(response.request["PATH_INFO"], {
            "new_password1": "Neues-Passwort-42", "new_password2": "Neues-Passwort-42",
        })
        self.assertRedirects(response, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Neues-Passwort-42"))
        # Link ist nur einmal gültig
        self.assertContains(self.client.get(path, follow=True), "Link nicht mehr gültig")


class PreferencesEmailTest(TestCase):
    """Eigene E-Mail-Adresse unter Einstellungen (0.5 beta)"""

    def setUp(self):
        self.user = User.objects.create_user("anna", "anna@example.com")
        self.client.force_login(self.user)

    def test_change_email(self):
        response = self.client.post(reverse("preferences"), {"email": "neu@example.com", "language": "de"})
        self.assertRedirects(response, reverse("preferences"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "neu@example.com")

    def test_email_must_be_unique(self):
        User.objects.create_user("bert", "bert@example.com")
        response = self.client.post(reverse("preferences"), {"email": "BERT@example.com", "language": "de"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Diese E-Mail-Adresse wird bereits von einem anderen Konto verwendet.")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "anna@example.com")


class ReminderPagesTest(TestCase):
    """Erinnerung im Frontend: Rechte, Empfänger, Formular (0.5 beta)"""

    def setUp(self):
        self.owner = User.objects.create_user("eigentuemer", "owner@example.com")
        self.editor = User.objects.create_user("bearbeiter", "editor@example.com")
        self.reader = User.objects.create_user("leser")
        self.stranger = User.objects.create_user("fremd", "fremd@example.com")
        self.contract = make_contract(self.owner, "Strom", "80.00")
        perms.grant_role(self.contract, self.editor, perms.EDITOR)
        perms.grant_role(self.contract, self.reader, perms.READER)
        self.url = reverse("contract_reminder", args=[self.contract.pk])

    def data(self, **extra):
        from datetime import timedelta

        data = {
            "enabled": "on",
            "remind_on": (timezone.localdate() + timedelta(days=30)).isoformat(),
            "lead_days": "14",
            "reason": "cancellation",
            "recipients": [self.owner.pk],
            "note": "",
        }
        data.update(extra)
        return data

    def test_access(self):
        for user, status in ((self.owner, 200), (self.editor, 200), (self.reader, 403), (self.stranger, 404)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, status)

    def test_recipients_only_users_with_view_access_owner_preselected(self):
        self.client.force_login(self.editor)
        form = self.client.get(self.url).context["form"]
        choices = set(form.fields["recipients"].queryset.values_list("username", flat=True))
        self.assertEqual(choices, {"eigentuemer", "bearbeiter", "leser"})
        self.assertEqual(form.fields["recipients"].initial, [self.owner.pk])

    def test_create_and_show_on_contract(self):
        from contracts.models import Reminder

        self.client.force_login(self.editor)
        response = self.client.post(self.url, self.data(note="Kündigungsfrist 3 Monate"))
        self.assertRedirects(response, reverse("contract_detail", args=[self.contract.pk]))
        reminder = Reminder.objects.get(contract=self.contract)
        self.assertTrue(reminder.enabled)
        self.assertEqual(list(reminder.recipients.all()), [self.owner])

        self.client.force_login(self.reader)
        page = self.client.get(reverse("contract_detail", args=[self.contract.pk]))
        self.assertContains(page, "Kündigung")
        self.assertContains(page, "Kündigungsfrist 3 Monate")
        self.assertNotContains(page, self.url)

    def test_stranger_cannot_be_recipient(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.data(recipients=[self.stranger.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["recipients"])

    def test_active_reminder_needs_recipient_with_email(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.data(recipients=[self.reader.pk]))
        self.assertContains(response, "Bitte wähle mindestens einen Empfänger mit E-Mail-Adresse.")

    def test_switched_off_reminder_needs_no_date(self):
        from contracts.models import Reminder

        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.data(enabled="", remind_on="", recipients=[]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Reminder.objects.get(contract=self.contract).enabled)

    def test_date_in_past_is_rejected(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.data(remind_on="2000-01-01"))
        self.assertContains(response, "Das Erinnerungsdatum darf nicht in der Vergangenheit liegen.")


class ManageTestMixin:
    """Benutzer für die Tests der Administration (0.6 beta)"""

    def setUp(self):
        from django.core import mail

        mail.outbox.clear()
        self.admins = Group.objects.get(name=perms.GROUP_ADMINS)
        self.users_group = Group.objects.get(name=perms.GROUP_USERS)
        self.superuser = User.objects.create_superuser("chef", "chef@example.com", "x")
        self.admin = User.objects.create_user("admin", "admin@example.com")
        self.admin.groups.add(self.admins)
        self.normal = User.objects.create_user("normal", "normal@example.com")


class ManageAccessTest(ManageTestMixin, TestCase):
    def test_pages_by_role(self):
        pages = {
            "manage_user_list": (403, 200, 200),
            "manage_user_create": (403, 200, 200),
            "manage_group_list": (403, 200, 200),
            "manage_group_create": (403, 200, 200),
            "manage_system": (403, 200, 200),
            "manage_reminders": (403, 403, 200),
            "manage_backup": (403, 403, 200),
        }
        for name, expected in pages.items():
            for user, status in zip((self.normal, self.admin, self.superuser), expected):
                with self.subTest(page=name, user=user.username):
                    self.client.force_login(user)
                    self.assertEqual(self.client.get(reverse(name)).status_code, status)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("manage_user_transfer", args=[self.normal.pk])).status_code, 403)

    def test_login_required(self):
        url = reverse("manage_user_list")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_sidebar_section(self):
        self.client.force_login(self.normal)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("manage_user_list"))
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, reverse("manage_user_list"))
        self.assertContains(response, reverse("manage_system"))
        self.assertNotContains(response, reverse("manage_reminders"))
        self.assertNotContains(response, reverse("admin:index"))
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, reverse("manage_reminders"))
        self.assertContains(response, "Django-Verwaltung")

    def test_admin_group_has_no_django_backend(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("admin:index")), reverse("admin:login") + "?next=/admin/",
                             fetch_redirect_response=False)


class ManageUserTest(ManageTestMixin, TestCase):
    def create(self, actor, **data):
        self.client.force_login(actor)
        form = {"username": "neu", "first_name": "Nora", "last_name": "Neu", "email": "neu@example.com",
                "send_invitation": "on", "groups": [self.users_group.pk]}
        form.update(data)
        return self.client.post(reverse("manage_user_create"), form)

    def test_create_with_invitation(self):
        import re

        from django.core import mail

        response = self.create(self.admin)
        user = User.objects.get(username="neu")
        self.assertRedirects(response, reverse("manage_user_detail", args=[user.pk]))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {perms.GROUP_USERS, perms.GROUP_ALL})
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["neu@example.com"])
        self.assertIn("Dein Zugang zu Contracts Manager", message.subject)
        self.assertIn("admin hat für dich ein Konto", message.body)

        # Mit dem Link Passwort wählen und anmelden
        self.client.logout()
        path = re.search(r"http://testserver(/accounts/password/reset/\S+/\S+/)", message.body).group(1)
        page = self.client.get(path, follow=True)
        self.client.post(page.request["PATH_INFO"], {"new_password1": "Sicher-Passwort-7", "new_password2": "Sicher-Passwort-7"})
        response = self.client.post(reverse("login"), {"username": "neu", "password": "Sicher-Passwort-7"})
        self.assertRedirects(response, reverse("dashboard"))

    def test_create_without_invitation_needs_no_email(self):
        from django.core import mail

        response = self.create(self.admin, send_invitation="", email="")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox, [])
        response = self.create(self.admin, username="neu2", email="")
        self.assertContains(response, "Für die Einladung ist eine E-Mail-Adresse nötig.")

    def test_admin_cannot_grant_admin_or_superuser(self):
        response = self.create(self.admin, groups=[self.admins.pk], is_superuser="on")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["groups"])
        response = self.create(self.admin, groups=[])
        user = User.objects.get(username="neu")
        self.assertFalse(user.is_superuser)

    def test_superuser_can_grant_admin(self):
        self.create(self.superuser, groups=[self.admins.pk], is_superuser="on")
        user = User.objects.get(username="neu")
        self.assertTrue(user.groups.filter(name=perms.GROUP_ADMINS).exists())
        self.assertTrue(user.is_superuser and user.is_staff)

    def test_admin_cannot_edit_superuser(self):
        self.client.force_login(self.admin)
        for name in ("manage_user_edit", "manage_user_delete"):
            self.assertEqual(self.client.get(reverse(name, args=[self.superuser.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("manage_user_active", args=[self.superuser.pk])).status_code, 403)

    def test_edit_keeps_hidden_groups(self):
        self.normal.groups.add(self.admins)
        self.client.force_login(self.admin)
        # Admin darf normal bearbeiten, "Administratoren" bleibt erhalten
        self.client.post(reverse("manage_user_edit", args=[self.normal.pk]), {
            "username": "normal", "email": "normal@example.com", "is_active": "on", "groups": [self.users_group.pk],
        })
        names = set(self.normal.groups.values_list("name", flat=True))
        self.assertEqual(names, {perms.GROUP_ADMINS, perms.GROUP_USERS, perms.GROUP_ALL})

    def test_no_status_changes_on_own_account(self):
        self.client.force_login(self.admin)
        form = self.client.get(reverse("manage_user_edit", args=[self.admin.pk])).context["form"]
        self.assertNotIn("is_active", form.fields)
        self.assertNotIn("groups", form.fields)
        self.assertEqual(self.client.post(reverse("manage_user_active", args=[self.admin.pk])).status_code, 403)

    def test_deactivate_and_activate(self):
        self.client.force_login(self.admin)
        url = reverse("manage_user_active", args=[self.normal.pk])
        self.client.post(url)
        self.normal.refresh_from_db()
        self.assertFalse(self.normal.is_active)
        self.client.post(url)
        self.normal.refresh_from_db()
        self.assertTrue(self.normal.is_active)

    def test_delete_only_without_objects(self):
        make_contract(self.normal, "Strom", "10.00")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("manage_user_delete", args=[self.normal.pk])).status_code, 403)
        empty = User.objects.create_user("leer")
        response = self.client.post(reverse("manage_user_delete", args=[empty.pk]))
        self.assertRedirects(response, reverse("manage_user_list"))
        self.assertFalse(User.objects.filter(username="leer").exists())

    def test_resend_mail(self):
        from django.core import mail

        self.client.force_login(self.admin)
        self.client.post(reverse("manage_user_mail", args=[self.normal.pk]))
        self.assertIn("Dein Zugang", mail.outbox[0].subject)
        self.normal.set_password("x")
        self.normal.last_login = timezone.now()
        self.normal.save()
        self.client.post(reverse("manage_user_mail", args=[self.normal.pk]))
        self.assertIn("Passwort zurücksetzen", mail.outbox[1].subject)

    def test_user_list_search(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("manage_user_list"), {"q": "norm"})
        usernames = [row["user"].username for row in response.context["rows"]]
        self.assertEqual(usernames, ["normal"])


class ManageTransferTest(ManageTestMixin, TestCase):
    def test_transfer_all_objects(self):
        contract = make_contract(self.normal, "Strom", "10.00")
        self.client.force_login(self.superuser)
        response = self.client.post(reverse("manage_user_transfer", args=[self.normal.pk]), {"new_owner": self.admin.pk})
        self.assertRedirects(response, reverse("manage_user_detail", args=[self.normal.pk]))
        contract.refresh_from_db()
        self.assertEqual(contract.owner, self.admin)
        self.assertEqual(
            perms.owned_object_counts(self.normal),
            {"contract": 0, "provider": 0, "category": 0, "consumer": 0, "paymentmethod": 0},
        )
        self.assertFalse(perms.can_view(self.normal, contract))

    def test_keep_access(self):
        contract = make_contract(self.normal, "Strom", "10.00")
        perms.transfer_all_ownership(self.normal, self.admin, keep_access=True)
        self.assertTrue(perms.can_change(self.normal, contract))

    def test_name_conflict(self):
        make_contract(self.normal, "Strom", "10.00")
        Provider.objects.create(owner=self.admin, name="Partner normal")
        self.client.force_login(self.superuser)
        response = self.client.post(reverse("manage_user_transfer", args=[self.normal.pk]), {"new_owner": self.admin.pk})
        self.assertContains(response, "Partner normal")
        self.assertEqual(perms.owned_object_counts(self.normal)["contract"], 1)


class ManageGroupTest(ManageTestMixin, TestCase):
    def test_create_group_with_members(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("manage_group_create"), {"name": "Familie", "members": [self.normal.pk]})
        self.assertRedirects(response, reverse("manage_group_list"))
        group = Group.objects.get(name="Familie")
        self.assertEqual(list(group.user_set.all()), [self.normal])

    def test_protected_groups(self):
        self.client.force_login(self.admin)
        all_group = Group.objects.get(name=perms.GROUP_ALL)
        self.assertEqual(self.client.get(reverse("manage_group_edit", args=[all_group.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("manage_group_edit", args=[self.admins.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("manage_group_delete", args=[self.users_group.pk])).status_code, 403)
        # Name einer Standardgruppe bleibt
        self.client.post(reverse("manage_group_edit", args=[self.users_group.pk]), {"name": "Anders", "members": []})
        self.assertTrue(Group.objects.filter(name=perms.GROUP_USERS).exists())
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("manage_group_edit", args=[self.admins.pk])).status_code, 200)

    def test_own_membership_and_superusers_are_kept(self):
        group = Group.objects.create(name="Familie")
        group.user_set.add(self.admin, self.superuser)
        self.client.force_login(self.admin)
        self.client.post(reverse("manage_group_edit", args=[group.pk]), {"name": "Familie", "members": [self.normal.pk]})
        self.assertEqual(set(group.user_set.values_list("username", flat=True)), {"admin", "chef", "normal"})

    def test_delete_group_removes_shares(self):
        group = Group.objects.create(name="Familie")
        contract = make_contract(self.normal, "Strom", "10.00")
        perms.grant_role(contract, group, perms.READER)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("manage_group_delete", args=[group.pk]))
        self.assertContains(page, "Zugriff auf 1 Objekt")
        self.client.post(reverse("manage_group_delete", args=[group.pk]))
        self.assertFalse(Group.objects.filter(name="Familie").exists())


class ManageSystemTest(ManageTestMixin, TestCase):
    def test_system_page_and_test_email(self):
        from django.core import mail

        self.client.force_login(self.admin)
        page = self.client.get(reverse("manage_system"))
        self.assertContains(page, settings.APP_VERSION)
        self.assertNotContains(page, "Django-Verwaltung")
        response = self.client.post(reverse("manage_system"), follow=True)
        self.assertContains(response, "Eine Test-E-Mail wurde an admin@example.com geschickt.")
        self.assertIn("Test-E-Mail", mail.outbox[0].subject)

    def test_test_email_needs_own_address(self):
        self.admin.email = ""
        self.admin.save()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("manage_system"), follow=True)
        self.assertContains(response, "Bitte trage zuerst unter Einstellungen deine E-Mail-Adresse ein.")

    def test_reminder_overview(self):
        from contracts.models import Reminder

        contract = make_contract(self.normal, "Strom", "10.00")
        Reminder.objects.create(contract=contract, enabled=True, remind_on=timezone.localdate())
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("manage_reminders")), "Strom")


class SystemWorkerResultTest(ManageTestMixin, TestCase):
    """Ergebnis des letzten Erinnerungsversands übersetzt anzeigen"""

    def test_last_result_in_user_language(self):
        from django_q.models import Success

        from contracts.tasks import send_due_reminders

        result = send_due_reminders()
        self.assertEqual(result, {"sent": 0, "reminders": 0, "failed": 0, "completed": 0})
        now = timezone.now()
        Success.objects.create(
            id="a" * 32, name="test", func="contracts.tasks.send_due_reminders",
            started=now, stopped=now, success=True, result={"sent": 3, "reminders": 2, "failed": 1, "completed": 1},
        )
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("manage_system")), "3 E-Mails zu 2 Erinnerungen verschickt, 1 Fehler, 1 erledigt")
        UserPreferences.objects.update_or_create(user=self.admin, defaults={"language": "en"})
        self.assertContains(self.client.get(reverse("manage_system")), "3 emails for 2 reminders sent, 1 errors, 1 completed")

    def test_old_text_results_are_not_shown(self):
        from django_q.models import Success

        now = timezone.now()
        Success.objects.create(
            id="b" * 32, name="alt", func="contracts.tasks.send_due_reminders",
            started=now, stopped=now, success=True, result="0 E-Mail(s) zu 0 Erinnerung(en) verschickt",
        )
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse("manage_system")), "E-Mail(s)")


class LoginLockoutTest(TestCase):
    """Sperre nach zu vielen Fehlversuchen (django-axes)"""

    def setUp(self):
        self.user = User.objects.create_user("anna", password="Richtig-Passwort-1")

    def attempt(self, password, username="anna"):
        return self.client.post(reverse("login"), {"username": username, "password": password})

    def test_locked_after_five_failures(self):
        for _ in range(4):
            self.assertEqual(self.attempt("falsch").status_code, 200)
        response = self.attempt("falsch")
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Anmeldung vorübergehend gesperrt", status_code=429)
        # Auch das richtige Passwort hilft während der Sperre nicht
        self.assertEqual(self.attempt("Richtig-Passwort-1").status_code, 429)
        # Andere Benutzernamen sind nicht betroffen
        User.objects.create_user("bert", password="Bert-Passwort-1")
        self.assertRedirects(self.attempt("Bert-Passwort-1", username="bert"), reverse("dashboard"))

    def test_success_resets_counter(self):
        for _ in range(4):
            self.attempt("falsch")
        self.assertRedirects(self.attempt("Richtig-Passwort-1"), reverse("dashboard"))
        self.client.logout()
        for _ in range(4):
            self.assertEqual(self.attempt("falsch").status_code, 200)


class QueryCountTest(TestCase):
    """Anzahl der Datenbankabfragen wächst nicht mit der Zahl der Verträge"""

    def count_queries(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(queries)

    def test_lists_use_constant_queries(self):
        owner = User.objects.create_user("anna")
        self.client.force_login(owner)
        for i in range(3):
            make_contract(owner, f"Vertrag {i}", "10.00")
        urls = [reverse("dashboard"), reverse("contract_list"), reverse("provider_list"),
                reverse("category_list"), reverse("consumer_list")]
        # Erster Aufruf legt Sitzung und Einstellungen an, daher vorab
        for url in urls:
            self.count_queries(url)
        before = {url: self.count_queries(url) for url in urls}
        for i in range(3, 15):
            make_contract(owner, f"Vertrag {i}", "10.00")
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.count_queries(url), before[url])


class DocumentPagesTest(TestCase):
    """Dokumente auf der Vertragsseite: Upload, Paperless, Rechte, Download (0.7 beta)"""

    def setUp(self):
        import shutil
        import tempfile

        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.anna = User.objects.create_user("anna")
        self.ben = User.objects.create_user("ben")
        self.carl = User.objects.create_user("carl")
        self.contract = make_contract(self.anna, "Strom", "80.00")
        perms.grant_role(self.contract, self.ben, perms.READER)
        self.add_url = reverse("document_add", args=[self.contract.pk])
        self.detail_url = reverse("contract_detail", args=[self.contract.pk])

    def pdf(self, name="Vertrag.pdf", content=b"%PDF-1.4 test"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def upload(self, name="Vertrag.pdf", **extra):
        self.client.force_login(self.anna)
        data = {"source": "upload", "name": "", "notes": "", "file": self.pdf(name)}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.add_url, data, **extra)

    def test_upload_without_javascript(self):
        response = self.upload()
        self.assertRedirects(response, self.detail_url)
        document = self.contract.documents.get()
        self.assertEqual((document.name, document.source, document.created_by), ("Vertrag", "upload", self.anna))
        page = self.client.get(self.detail_url)
        self.assertContains(page, document.file.url)
        self.assertContains(page, "bi-hdd")

    def test_upload_with_htmx_updates_sections(self):
        response = self.upload(HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="documents-section"')
        self.assertContains(response, 'id="costs-section" class="card" hx-swap-oob="true"')

    def test_invalid_upload_shows_error(self):
        self.client.force_login(self.anna)
        from django.core.files.uploadedfile import SimpleUploadedFile

        data = {"source": "upload", "file": SimpleUploadedFile("seite.html", b"<script>")}
        response = self.client.post(self.add_url, data, HTTP_HX_REQUEST="true")
        self.assertEqual(response["HX-Retarget"], "#document-form")
        self.assertContains(response, "Dieser Dateityp ist nicht erlaubt")
        self.assertFalse(self.contract.documents.exists())

    def test_paperless_link(self):
        self.client.force_login(self.anna)
        data = {"source": "paperless", "name": "Police", "paperless_document_id": "42"}
        with self.settings(PAPERLESS_URL="https://paperless.example.de"):
            self.assertRedirects(self.client.post(self.add_url, data), self.detail_url)
            page = self.client.get(self.detail_url)
            duplicate = self.client.post(self.add_url, {**data, "name": "Nochmal"})
        self.assertContains(page, "https://paperless.example.de/documents/42/details")
        self.assertContains(page, "bi-archive")
        self.assertContains(duplicate, "bereits mit diesem Vertrag verknüpft")
        self.assertEqual(self.contract.documents.count(), 1)

    def test_paperless_needs_name_and_id(self):
        self.client.force_login(self.anna)
        response = self.client.post(self.add_url, {"source": "paperless", "name": "", "paperless_document_id": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["form"].errors), {"paperless_document_id"})
        response = self.client.post(self.add_url, {"source": "paperless", "name": "", "paperless_document_id": "5"})
        self.assertEqual(set(response.context["form"].errors), {"name"})

    def test_source_tabs(self):
        self.client.force_login(self.anna)
        upload = self.client.get(self.add_url)
        paperless = self.client.get(self.add_url + "?source=paperless", HTTP_HX_REQUEST="true")
        self.assertContains(upload, 'type="file"')
        self.assertNotContains(upload, 'name="paperless_document_id"')
        self.assertContains(paperless, 'name="paperless_document_id"')
        self.assertNotContains(paperless, 'type="file"')

    def test_edit_keeps_file(self):
        self.upload()
        document = self.contract.documents.get()
        url = reverse("document_edit", args=[document.pk])
        page = self.client.get(url)
        self.assertNotContains(page, 'type="file"')
        response = self.client.post(url, {"name": "Neuer Name", "notes": "Seite 2 fehlt"})
        self.assertRedirects(response, self.detail_url)
        document.refresh_from_db()
        self.assertEqual((document.name, document.notes, document.source), ("Neuer Name", "Seite 2 fehlt", "upload"))
        self.assertTrue(document.file)

    def test_download_by_role(self):
        self.upload()
        document = self.contract.documents.get()
        url = document.file.url
        self.assertTrue(url.startswith("/media/documents/"))
        for user, status in ((self.anna, 200), (self.ben, 200), (self.carl, 404)):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, status)
                if status == 200:
                    self.assertEqual(response["Content-Type"], "application/pdf")
                    self.assertTrue(response["Content-Disposition"].startswith("inline"))
                    self.assertIn("private", response["Cache-Control"])
                    self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get("/media/documents/../../config/settings.py").status_code, 404)

    def test_office_files_are_downloaded(self):
        self.upload("Tabelle.xlsx")
        document = self.contract.documents.get()
        response = self.client.get(document.file.url)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertIn("spreadsheetml", response["Content-Type"])

    def test_reader_and_stranger_cannot_change(self):
        self.upload()
        document = self.contract.documents.get()
        cases = [
            ("get", self.add_url), ("post", self.add_url),
            ("get", reverse("document_edit", args=[document.pk])),
            ("post", reverse("document_delete", args=[document.pk])),
        ]
        for user, status in ((self.ben, 403), (self.carl, 404)):
            self.client.force_login(user)
            for method, url in cases:
                with self.subTest(user=user.username, url=url, method=method):
                    self.assertEqual(getattr(self.client, method)(url).status_code, status)
        self.client.force_login(self.ben)
        page = self.client.get(self.detail_url)
        self.assertContains(page, document.name)
        self.assertNotContains(page, self.add_url)

    def test_delete_removes_file_and_link(self):
        import os

        self.upload()
        document = self.contract.documents.get()
        cost = self.contract.costs.get()
        cost.document = document
        cost.save()
        path = os.path.join(self.media_root, document.file.name)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("document_delete", args=[document.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, 'id="documents-section"')
        self.assertFalse(os.path.exists(path))
        cost.refresh_from_db()
        self.assertIsNone(cost.document)

    def test_cost_form_offers_only_own_documents(self):
        self.upload()
        own = self.contract.documents.get()
        other_contract = make_contract(self.anna, "Gas", "20.00")
        other = Document.objects.create(contract=other_contract, name="Fremd", source="paperless",
                                        paperless_document_id=9)
        cost = self.contract.costs.get()
        url = reverse("cost_edit", args=[cost.pk])
        page = self.client.get(url)
        self.assertEqual(list(page.context["form"].fields["document"].queryset), [own])
        data = {"amount": "80.00", "payment_interval": "monthly", "valid_from": cost.valid_from.isoformat(),
                "notes": ""}
        response = self.client.post(url, {**data, "document": other.pk})
        self.assertIn("document", response.context["form"].errors)
        self.assertRedirects(self.client.post(url, {**data, "document": own.pk}), self.detail_url)
        cost.refresh_from_db()
        self.assertEqual(cost.document, own)
        self.assertContains(self.client.get(self.detail_url), own.file.url, count=2)


class SiteSettingsTest(ManageTestMixin, TestCase):
    """Beschriftung der SSO-Schaltfläche unter Verwaltung › System (0.7 beta)"""

    def test_admin_changes_label(self):
        from .models import SiteSettings

        self.client.force_login(self.admin)
        url = reverse("manage_system")
        self.assertContains(self.client.get(url), 'name="sso_login_label"')
        response = self.client.post(url, {"action": "settings", "sso_login_label": "  Synology  "})
        self.assertRedirects(response, url)
        self.assertEqual(SiteSettings.load().sso_button_label, "Synology")
        self.client.post(url, {"action": "settings", "sso_login_label": ""})
        self.assertEqual(SiteSettings.load().sso_button_label, "SSO Login")
        self.assertEqual(SiteSettings.objects.count(), 1)

    def test_normal_user_cannot_change_label(self):
        self.client.force_login(self.normal)
        response = self.client.post(reverse("manage_system"), {"action": "settings", "sso_login_label": "X"})
        self.assertEqual(response.status_code, 403)


class ManageBackupTest(ManageTestMixin, TestCase):
    """Backup und Import in der Oberfläche (0.9 beta), nur Superuser"""

    def setUp(self):
        import shutil
        import tempfile

        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.contract = make_contract(self.superuser, "Strom", "50.00")

    def download(self):
        import io

        response = self.client.post(reverse("manage_backup_download"))
        self.assertEqual(response.status_code, 200)
        return io.BytesIO(b"".join(response.streaming_content))

    def upload(self, content, name="backup.zip"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.client.post(reverse("manage_backup"), {"file": SimpleUploadedFile(name, content)})

    def test_only_superuser(self):
        for user in (self.normal, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("manage_backup_download")).status_code, 403)
            self.assertEqual(self.client.post(reverse("manage_backup_import"), {"token": "ab"}).status_code, 403)
            self.assertNotContains(self.client.get(reverse("dashboard")), reverse("manage_backup"))
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("dashboard")), reverse("manage_backup"))
        self.assertEqual(self.client.get(reverse("manage_backup_download")).status_code, 405)

    def test_download_is_backup_without_passwords(self):
        import json
        import zipfile

        self.client.force_login(self.superuser)
        response = self.client.post(reverse("manage_backup_download"))
        self.assertIn("attachment; filename=\"contracts-manager-backup-", response["Content-Disposition"])
        import io
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        rows = json.loads(archive.read("data.json"))
        users = {row["fields"]["username"]: row["fields"]["password"] for row in rows if row["model"] == "auth.user"}
        # "" = hatte ein Passwort, "!" = hatte keines; nie ein Hash
        self.assertEqual(users, {"chef": "", "admin": "!", "normal": "!"})

    def test_upload_shows_summary_and_changes_nothing(self):
        self.client.force_login(self.superuser)
        buffer = self.download()
        Contract.objects.create(
            owner=self.superuser, name="Gas", provider=self.contract.provider, category=self.contract.category,
            consumer=self.contract.consumer, start_date=timezone.localdate(),
        )
        response = self.upload(buffer.getvalue())
        self.assertContains(response, "Alles ersetzen und importieren")
        self.assertContains(response, 'name="token"')
        self.assertEqual(Contract.objects.count(), 2)

    def test_import_replaces_data_and_signs_out(self):
        from django.core import mail

        self.client.force_login(self.superuser)
        buffer = self.download()
        self.contract.delete()
        User.objects.create_user("spaeter")
        token = self.upload(buffer.getvalue()).context["token"]
        response = self.client.post(reverse("manage_backup_import"), {"token": token, "confirm": "1"})
        self.assertRedirects(response, reverse("login"))
        self.assertTrue(Contract.objects.filter(name="Strom").exists())
        self.assertFalse(User.objects.filter(username="spaeter").exists())
        # Abgemeldet, kein Passwort mehr
        url = reverse("manage_backup")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
        self.assertFalse(User.objects.get(username="chef").has_usable_password())
        self.assertFalse(User.objects.get(username="chef").check_password("x"))
        # Die hochgeladene Datei ist weg
        self.client.force_login(User.objects.get(username="chef"))
        response = self.client.post(reverse("manage_backup_import"), {"token": token, "confirm": "1"})
        self.assertRedirects(response, url)

        # "Passwort vergessen?" geht für alle, die vorher ein Passwort hatten
        self.client.logout()
        mail.outbox.clear()
        self.client.post(reverse("password_reset"), {"email": "chef@example.com"})
        self.assertEqual([message.to for message in mail.outbox], [["chef@example.com"]])
        # ... aber nicht für Benutzer, die nie eines hatten (nur SSO)
        mail.outbox.clear()
        self.client.post(reverse("password_reset"), {"email": "normal@example.com"})
        self.assertEqual(mail.outbox, [])

    def test_cancel_discards_upload(self):
        from contracts import backup

        self.client.force_login(self.superuser)
        token = self.upload(self.download().getvalue()).context["token"]
        self.assertIsNotNone(backup.upload_path(token))
        response = self.client.post(reverse("manage_backup_import"), {"token": token, "cancel": "1"})
        self.assertRedirects(response, reverse("manage_backup"))
        self.assertIsNone(backup.upload_path(token))
        self.assertTrue(User.objects.get(username="chef").has_usable_password())

    def test_invalid_file_rejected(self):
        self.client.force_login(self.superuser)
        response = self.upload(b"kein zip", name="notizen.zip")
        self.assertContains(response, "Diese Datei ist kein Backup von Contracts Manager.")
        self.assertNotContains(response, 'name="token"')
        response = self.client.post(reverse("manage_backup_import"), {"token": "../etc", "confirm": "1"})
        self.assertRedirects(response, reverse("manage_backup"))
