from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import ProtectedError
from . import permissions as perms
from .models import (
    Provider, Category, Consumer, Contract, CostEntry, Country, Document, total_current_costs,
    ProviderGroupObjectPermission, ProviderUserObjectPermission, Reminder,
)


def default_owner():
    """
    Eigentümer für Testobjekte (Version 0.3a: jedes Objekt braucht einen).
    """
    user, _ = User.objects.get_or_create(username="owner")
    return user


class CategoryModelTest(TestCase):
    def setUp(self):
        self.parent_category = Category.objects.create(owner=default_owner(), name="Versicherung")
        self.child_category = Category.objects.create(owner=default_owner(),
            name="KFZ-Versicherung", 
            parent=self.parent_category
        )

    def test_category_str_representation(self):
        """Testet die __str__-Methode für Kategorien"""
        self.assertEqual(str(self.parent_category), "Versicherung")
        self.assertEqual(str(self.child_category), "Versicherung / KFZ-Versicherung")

    def test_category_hierarchy_validation(self):
        """Testet, dass nur zwei Ebenen erlaubt sind"""
        # Versuche, eine dritte Ebene zu erstellen (sollte fehlschlagen)
        with self.assertRaises(ValidationError):
            grandchild = Category.objects.create(owner=default_owner(),
                name="Auto-Haftpflicht",
                parent=self.child_category
            )
            grandchild.full_clean()  # Validierung ausführen
            
    def test_unique_constraint_sub_category(self):
        """Testet die UniqueConstraint für Kategorie-Name innerhalb eines Parents - Unter-Kategorie"""
        # Versuche, eine Kategorie mit gleichem Namen und Parent zu erstellen (sollte ValidationError werfen)
        with self.assertRaises(ValidationError):
            category = Category(owner=default_owner(), name="KFZ-Versicherung", parent=self.parent_category)
            category.full_clean()

    def test_unique_constraint_main_category(self):
        """Testet die UniqueConstraint für Kategorie-Name innerhalb eines Parents - Haupt-Kategorie"""
        # Versuche, eine Hauptkategorie mit gleichem Namen zu erstellen (sollte ValidationError werfen)
        with self.assertRaises(ValidationError):
            category = Category(owner=default_owner(), name="Versicherung", parent=None)
            category.full_clean()
            
    def test_unique_constraint_database_level(self):
        """Testet die UniqueConstraint auf Datenbankebene"""
        # Erstelle zwei Kategorien mit gleichem Namen und Parent (durch bulk_create) - sollte IntegrityError werfen
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Category.objects.bulk_create([
                    Category(owner=default_owner(), name="Versicherung", parent=None),
                    Category(owner=default_owner(), name="Versicherung", parent=None)
                ])


class ContractModelTest(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(owner=default_owner(), name="Test Anbieter")
        self.category = Category.objects.create(owner=default_owner(), name="Test Kategorie")
        self.consumer = Consumer.objects.create(owner=default_owner(), name="Test Verbraucher")

    def test_contract_reference_generation(self):
        """Testet die automatische Generierung der Reference"""
        contract1 = Contract.objects.create(owner=default_owner(),
            name="Vertrag 1",
            provider=self.provider,
            start_date=timezone.now().date(),
            category=self.category,
            consumer=self.consumer
        )
        
        # Prüfe, dass eine Reference generiert wurde
        self.assertIsNotNone(contract1.reference)
        self.assertTrue(contract1.reference.startswith(str(timezone.now().date().year)))
        
        # Erstelle einen zweiten Vertrag im gleichen Jahr - sollte den nächsten Zähler haben
        contract2 = Contract.objects.create(owner=default_owner(),
            name="Vertrag 2",
            provider=self.provider,
            start_date=timezone.now().date(),
            category=self.category,
            consumer=self.consumer
        )

        year = str(timezone.now().date().year)
        self.assertEqual(contract1.reference, f"{year}0001")
        self.assertEqual(contract2.reference, f"{year}0002")

    def test_contract_reference_restarts_each_year(self):
        """Der Zähler beginnt in jedem Jahr wieder bei 0001"""
        def create_in(year):
            fake_now = datetime(year, 6, 1, 12, 0, tzinfo=dt_timezone.utc)
            with patch("contracts.models.timezone.now", return_value=fake_now):
                return Contract.objects.create(owner=default_owner(),
                    name=f"Vertrag {year}",
                    provider=self.provider,
                    start_date=fake_now.date(),
                    category=self.category,
                    consumer=self.consumer,
                )

        self.assertEqual(create_in(2025).reference, "20250001")
        self.assertEqual(create_in(2025).reference, "20250002")
        self.assertEqual(create_in(2026).reference, "20260001")
        self.assertEqual(create_in(2025).reference, "20250003")

    def test_contract_term_validation(self):
        """Testet die Validierung der Laufzeit-Felder"""
        # Test mit nur term_value
        with self.assertRaises(ValidationError):
            contract = Contract.objects.create(owner=default_owner(),
                name="Vertrag",
                provider=self.provider,
                start_date=timezone.now().date(),
                category=self.category,
                consumer=self.consumer,
                term_value=12
            )
            contract.full_clean()

        # Test mit nur term_unit
        with self.assertRaises(ValidationError):
            contract = Contract.objects.create(owner=default_owner(),
                name="Vertrag",
                provider=self.provider,
                start_date=timezone.now().date(),
                category=self.category,
                consumer=self.consumer,
                term_unit='months'
            )
            contract.full_clean()

        # Test mit beiden Feldern (sollte funktionieren)
        contract = Contract.objects.create(owner=default_owner(),
            name="Vertrag",
            provider=self.provider,
            start_date=timezone.now().date(),
            category=self.category,
            consumer=self.consumer,
            term_value=12,
            term_unit='months'
        )
        self.assertEqual(contract.term_value, 12)
        self.assertEqual(contract.term_unit, 'months')


class CostEntryModelTest(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(owner=default_owner(), name="Test Anbieter")
        self.category = Category.objects.create(owner=default_owner(), name="Test Kategorie")
        self.consumer = Consumer.objects.create(owner=default_owner(), name="Test Verbraucher")
        self.contract = Contract.objects.create(owner=default_owner(),
            name="Test Vertrag",
            provider=self.provider,
            start_date=timezone.now().date(),
            category=self.category,
            consumer=self.consumer
        )

    def test_monthly_amount_calculation(self):
        """Testet die Umrechnung auf monatliche Beträge"""
        # Teste mit monatlicher Zahlweise - sollte unverändert sein
        cost_entry = CostEntry.objects.create(
            contract=self.contract,
            amount=100,
            valid_from=timezone.now().date(),
            payment_interval='monthly'
        )
        self.assertEqual(cost_entry.monthly_amount(), 100)
        
        # Teste mit wöchentlicher Zahlweise - sollte auf monatliche Beträge umgerechnet werden
        cost_entry_weekly = CostEntry.objects.create(
            contract=self.contract,
            amount=25,
            valid_from=timezone.now().date(),
            payment_interval='weekly'
        )
        # (25 * 52) / 12 = ca. 108.33
        self.assertAlmostEqual(cost_entry_weekly.monthly_amount(), 108.33, places=2)
        
        # Teste mit quartalsweiser Zahlweise - sollte auf monatliche Beträge umgerechnet werden
        cost_entry_quarterly = CostEntry.objects.create(
            contract=self.contract,
            amount=300,
            valid_from=timezone.now().date(),
            payment_interval='quarterly'
        )
        # 300 / 3 = 100
        self.assertEqual(cost_entry_quarterly.monthly_amount(), 100)
        
        # Teste mit jährlicher Zahlweise - sollte auf monatliche Beträge umgerechnet werden
        cost_entry_yearly = CostEntry.objects.create(
            contract=self.contract,
            amount=1200,
            valid_from=timezone.now().date(),
            payment_interval='yearly'
        )
        # 1200 / 12 = 100
        self.assertEqual(cost_entry_yearly.monthly_amount(), 100)
        

class ProviderModelTest(TestCase):
    def test_provider_fields(self):
        """Testet die neuen Felder im Provider-Modell"""
        provider = Provider.objects.create(owner=default_owner(),
            name="Test-Anbieter",
            street="Teststraße 123",
            postal_code="12345",
            city="Teststadt",
            country=Country.AT,
            email="test@example.com",
            customer_number="CUST001",
            contact_person="Max Mustermann",
            notes="Test-Bemerkung"
        )

        self.assertEqual(provider.street, "Teststraße 123")
        self.assertEqual(provider.postal_code, "12345")
        self.assertEqual(provider.city, "Teststadt")
        self.assertEqual(provider.country, "AT")
        self.assertEqual(provider.get_country_display(), "Österreich")
        self.assertEqual(provider.email, "test@example.com")
        self.assertEqual(provider.customer_number, "CUST001")
        self.assertEqual(provider.contact_person, "Max Mustermann")
        self.assertEqual(provider.notes, "Test-Bemerkung")
    def test_provider_country_default_and_validation(self):
        """Standardland ist Deutschland, unbekannte Codes werden abgelehnt"""
        provider = Provider(owner=default_owner(), name="Ohne Land")
        self.assertEqual(provider.country, Country.DE)

        provider.country = "Deutschland"
        with self.assertRaises(ValidationError):
            provider.full_clean()


class ProtectedMasterDataTest(TestCase):
    """Stammdaten mit Verträgen dürfen nicht gelöscht werden"""

    def setUp(self):
        self.provider = Provider.objects.create(owner=default_owner(), name="Anbieter")
        self.category = Category.objects.create(owner=default_owner(), name="Kategorie")
        self.consumer = Consumer.objects.create(owner=default_owner(), name="Verbraucher")
        self.contract = Contract.objects.create(owner=default_owner(),
            name="Vertrag",
            provider=self.provider,
            start_date=timezone.now().date(),
            category=self.category,
            consumer=self.consumer,
        )

    def test_master_data_with_contracts_cannot_be_deleted(self):
        for obj in (self.provider, self.category, self.consumer):
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ProtectedError):
                    obj.delete()
        self.assertTrue(Contract.objects.filter(pk=self.contract.pk).exists())

    def test_deleting_contract_deletes_its_costs(self):
        CostEntry.objects.create(
            contract=self.contract, amount=10, valid_from=timezone.now().date()
        )
        self.contract.delete()
        self.assertEqual(CostEntry.objects.count(), 0)


class CurrentCostTest(TestCase):
    """Aktueller Betrag eines Vertrags und Summe aller aktuellen Kosten"""

    def setUp(self):
        self.today = timezone.localdate()
        self.provider = Provider.objects.create(owner=default_owner(), name="Anbieter")
        self.category = Category.objects.create(owner=default_owner(), name="Kategorie")
        self.consumer = Consumer.objects.create(owner=default_owner(), name="Verbraucher")

    def make_contract(self, name="Vertrag"):
        return Contract.objects.create(owner=default_owner(),
            name=name,
            provider=self.provider,
            start_date=self.today,
            category=self.category,
            consumer=self.consumer,
        )

    def add_cost(self, contract, amount, days_offset=0, interval="monthly"):
        return CostEntry.objects.create(
            contract=contract,
            amount=Decimal(amount),
            valid_from=self.today + timedelta(days=days_offset),
            payment_interval=interval,
        )

    def test_without_costs_is_none(self):
        contract = self.make_contract()
        self.assertIsNone(contract.current_cost_entry)
        self.assertIsNone(contract.current_cost)
        self.assertIsNone(contract.current_monthly_cost)

    def test_latest_past_entry_wins(self):
        contract = self.make_contract()
        self.add_cost(contract, "10.00", days_offset=-400)
        self.add_cost(contract, "12.50", days_offset=-30)
        self.assertEqual(contract.current_cost, Decimal("12.50"))

    def test_entry_valid_from_today_counts(self):
        contract = self.make_contract()
        self.add_cost(contract, "10.00", days_offset=-30)
        self.add_cost(contract, "11.00", days_offset=0)
        self.assertEqual(contract.current_cost, Decimal("11.00"))

    def test_future_entries_are_ignored(self):
        contract = self.make_contract()
        self.add_cost(contract, "10.00", days_offset=-30)
        self.add_cost(contract, "99.00", days_offset=1)
        self.assertEqual(contract.current_cost, Decimal("10.00"))

    def test_only_future_entries_is_none(self):
        contract = self.make_contract()
        self.add_cost(contract, "99.00", days_offset=10)
        self.assertIsNone(contract.current_cost)

    def test_current_monthly_cost_uses_interval(self):
        contract = self.make_contract()
        self.add_cost(contract, "120.00", days_offset=-1, interval="yearly")
        self.assertEqual(contract.current_cost, Decimal("120.00"))
        self.assertEqual(contract.current_monthly_cost, Decimal("10.00"))

    def test_total_with_mixed_intervals(self):
        monthly = self.make_contract("Monatlich")
        self.add_cost(monthly, "10.00")                        # 10,00 / Monat
        yearly = self.make_contract("Jährlich")
        self.add_cost(yearly, "120.00", interval="yearly")     # 10,00 / Monat
        quarterly = self.make_contract("Quartal")
        self.add_cost(quarterly, "100.00", interval="quarterly")  # 33,33... / Monat
        weekly = self.make_contract("Wöchentlich")
        self.add_cost(weekly, "3.00", interval="weekly")       # 13,00 / Monat
        self.make_contract("Ohne Kosten")
        future = self.make_contract("Zukünftig")
        self.add_cost(future, "50.00", days_offset=5)

        total = total_current_costs(Contract.objects.all())

        # 10 + 10 + 33,333... + 13 = 66,333... -> 66,33 / Monat
        self.assertEqual(total["monthly"], Decimal("66.33"))
        # 66,333... x 12 = 796,00 (erst nach der Hochrechnung gerundet)
        self.assertEqual(total["yearly"], Decimal("796.00"))
        self.assertEqual(total["count"], 4)

    def test_total_without_contracts(self):
        total = total_current_costs(Contract.objects.none())
        self.assertEqual(total, {"monthly": Decimal("0.00"), "yearly": Decimal("0.00"), "count": 0})


class ContractAdminTest(TestCase):
    """Vertragsliste im Admin: Filter nach Hauptkategorie und Kostensumme"""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_superuser("admin", "admin@example.com", "test")
        provider = Provider.objects.create(owner=default_owner(), name="Anbieter")
        consumer = Consumer.objects.create(owner=default_owner(), name="Verbraucher")
        cls.insurance = Category.objects.create(owner=default_owner(), name="Versicherung")
        car = Category.objects.create(owner=default_owner(), name="KFZ-Versicherung", parent=cls.insurance)
        media = Category.objects.create(owner=default_owner(), name="Medien")
        today = timezone.localdate()

        def contract(name, category, amount, interval):
            c = Contract.objects.create(owner=default_owner(),
                name=name, provider=provider, start_date=today,
                category=category, consumer=consumer,
            )
            CostEntry.objects.create(
                contract=c, amount=Decimal(amount), valid_from=today,
                payment_interval=interval,
            )
            return c

        contract("Hausrat", cls.insurance, "120.00", "yearly")   # 10,00 / Monat
        contract("Auto", car, "300.00", "quarterly")             # 100,00 / Monat
        contract("Streaming", media, "1234.50", "monthly")       # 1.234,50 / Monat
        cls.url = reverse("admin:contracts_contract_changelist")

    def setUp(self):
        self.client.force_login(self.admin_user)

    def test_changelist_shows_totals_and_interval(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        totals = response.context["cost_totals"]
        self.assertEqual(totals["monthly"], "1.344,50 €")
        self.assertEqual(totals["yearly"], "16.134,00 €")
        self.assertEqual(totals["count"], 3)
        self.assertContains(response, "Summe pro Monat")
        self.assertContains(response, "120,00 € jährlich")

    def test_main_category_filter_includes_subcategories(self):
        response = self.client.get(self.url, {"main_category": self.insurance.pk})
        names = {c.name for c in response.context["cl"].result_list}
        self.assertEqual(names, {"Hausrat", "Auto"})
        # Die Summe folgt dem Filter: 10 + 100 = 110 pro Monat
        self.assertEqual(response.context["cost_totals"]["monthly"], "110,00 €")

    def test_add_view_loads(self):
        response = self.client.get(reverse("admin:contracts_contract_add"))
        self.assertEqual(response.status_code, 200)


class HealthCheckTest(TestCase):
    """Zustandsprüfung unter /health/"""

    def test_health_ok_without_login(self):
        for url in ("/health/", "/health"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": "ok"})

    def test_health_reports_database_error(self):
        with patch("config.views.connection.cursor", side_effect=DatabaseError("weg")):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error"})

    def test_health_rejects_post(self):
        self.assertEqual(self.client.post("/health/").status_code, 405)


class Version02FieldsTest(TestCase):
    """Version 0.2 beta: neue optionale Felder bei Partner, Kosten und Verbraucher"""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_superuser("admin", "admin@example.com", "test")
        cls.provider = Provider.objects.create(owner=default_owner(), name="Versicherer", paperless_correspondent_id=7)
        cls.consumer = Consumer.objects.create(owner=default_owner(), name="Auto", number="M-AB 123", notes="Kombi")
        category = Category.objects.create(owner=default_owner(), name="Versicherung")
        cls.contract = Contract.objects.create(owner=default_owner(),
            name="KFZ", provider=cls.provider, start_date=timezone.localdate(),
            category=category, consumer=cls.consumer,
        )
        cls.cost = CostEntry.objects.create(
            contract=cls.contract, amount=Decimal("300.00"),
            valid_from=timezone.localdate(), payment_interval="yearly",
            notes="Beitragsanpassung",
        )

    def setUp(self):
        self.client.force_login(self.admin_user)

    def test_new_fields_are_optional(self):
        provider = Provider(owner=default_owner(), name="Ohne Paperless")
        provider.full_clean()
        consumer = Consumer(owner=default_owner(), name="Haushalt")
        consumer.full_clean()
        cost = CostEntry(contract=self.contract, amount=1, valid_from=timezone.localdate())
        cost.full_clean()
        self.assertEqual((consumer.number, consumer.notes, cost.notes), ("", "", ""))
        self.assertIsNone(provider.paperless_correspondent_id)
        self.assertIsNone(cost.document)

    def test_ids_must_be_positive(self):
        for obj, field in (
            (Provider(owner=default_owner(), name="X", paperless_correspondent_id=0), "paperless_correspondent_id"),
            (Document(contract=self.contract, name="X", source="paperless",
                      paperless_document_id=0), "paperless_document_id"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError) as ctx:
                    obj.full_clean()
                self.assertIn(field, ctx.exception.message_dict)

    def test_admin_pages_show_new_fields(self):
        contract_page = self.client.get(
            reverse("admin:contracts_contract_change", args=[self.contract.pk])
        )
        self.assertContains(contract_page, 'name="costs-0-document"')
        self.assertContains(contract_page, 'name="costs-0-notes"')

        provider_page = self.client.get(
            reverse("admin:contracts_provider_change", args=[self.provider.pk])
        )
        self.assertContains(provider_page, 'name="paperless_correspondent_id"')

        consumer_page = self.client.get(
            reverse("admin:contracts_consumer_change", args=[self.consumer.pk])
        )
        self.assertContains(consumer_page, 'name="number"')
        self.assertContains(consumer_page, 'name="notes"')

    def test_consumer_search_by_number(self):
        response = self.client.get(
            reverse("admin:contracts_consumer_changelist"), {"q": "M-AB"}
        )
        self.assertEqual([c.name for c in response.context["cl"].result_list], ["Auto"])


class PaperlessLinkTest(TestCase):
    """Version 0.2 beta: Links nach Paperless über PAPERLESS_URL"""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_superuser("admin", "admin@example.com", "test")
        cls.provider = Provider.objects.create(owner=default_owner(), name="Versicherer", paperless_correspondent_id=7)
        consumer = Consumer.objects.create(owner=default_owner(), name="Auto")
        category = Category.objects.create(owner=default_owner(), name="Versicherung")
        cls.contract = Contract.objects.create(owner=default_owner(),
            name="KFZ", provider=cls.provider, start_date=timezone.localdate(),
            category=category, consumer=consumer,
        )
        cls.document = Document.objects.create(
            contract=cls.contract, name="Rechnung", source="paperless", paperless_document_id=123,
        )

    def setUp(self):
        self.client.force_login(self.admin_user)

    @override_settings(PAPERLESS_URL="https://paperless.example.de/")
    def test_links_with_paperless_url(self):
        self.assertEqual(
            self.document.paperless_url,
            "https://paperless.example.de/documents/123/details",
        )
        self.assertEqual(
            self.provider.paperless_correspondent_url,
            "https://paperless.example.de/documents?correspondent__id__in=7",
        )

    @override_settings(PAPERLESS_URL="")
    def test_no_links_without_paperless_url(self):
        self.assertIsNone(self.document.paperless_url)
        self.assertIsNone(self.provider.paperless_correspondent_url)

    @override_settings(PAPERLESS_URL="https://paperless.example.de")
    def test_no_link_without_id(self):
        document = Document(contract=self.contract, name="Upload", source="upload", file="documents/x.pdf")
        self.assertIsNone(document.paperless_url)

    @override_settings(PAPERLESS_URL="https://paperless.example.de")
    def test_admin_pages_show_links(self):
        contract_page = self.client.get(
            reverse("admin:contracts_contract_change", args=[self.contract.pk])
        )
        self.assertContains(contract_page, "https://paperless.example.de/documents/123/details")
        self.assertContains(contract_page, "Dokumente des Partners öffnen")

        provider_page = self.client.get(
            reverse("admin:contracts_provider_change", args=[self.provider.pk])
        )
        self.assertContains(provider_page, "correspondent__id__in=7")


class PermissionModelTest(TestCase):
    """Version 0.3a beta: Rollen Eigentümer, Bearbeiter, Leser (django-guardian)"""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("eigentuemer")
        cls.editor = User.objects.create_user("bearbeiter")
        cls.reader = User.objects.create_user("leser")
        cls.member = User.objects.create_user("familie")
        cls.stranger = User.objects.create_user("fremd")
        cls.admin = User.objects.create_superuser("admin", "admin@example.com", "test")
        cls.family = Group.objects.create(name="Familie")
        cls.member.groups.add(cls.family)

        cls.provider = Provider.objects.create(owner=cls.owner, name="Versicherer")
        cls.category = Category.objects.create(owner=cls.owner, name="Versicherung")
        cls.consumer = Consumer.objects.create(owner=cls.owner, name="Auto")
        cls.contract = Contract.objects.create(
            owner=cls.owner, name="KFZ", provider=cls.provider,
            start_date=timezone.localdate(), category=cls.category, consumer=cls.consumer,
        )
        cls.cost = CostEntry.objects.create(
            contract=cls.contract, amount=Decimal("50.00"), valid_from=timezone.localdate(),
        )
        perms.grant_role(cls.contract, cls.editor, perms.EDITOR)
        perms.grant_role(cls.contract, cls.reader, perms.READER)
        perms.grant_role(cls.contract, cls.family, perms.READER)

    def rights(self, user, obj):
        return (perms.can_view(user, obj), perms.can_change(user, obj), perms.can_delete(user, obj))

    def test_rights_per_role(self):
        expected = {
            "owner": (True, True, True),
            "editor": (True, True, False),
            "reader": (True, False, False),
            "member": (True, False, False),   # Leser über die Gruppe
            "stranger": (False, False, False),
            "admin": (True, True, True),      # Superuser darf alles
        }
        for name, rights in expected.items():
            with self.subTest(user=name):
                self.assertEqual(self.rights(getattr(self, name), self.contract), rights)

    def test_cost_entries_follow_contract(self):
        expected = {
            "owner": (True, True, True),
            "editor": (True, True, True),     # Bearbeiter pflegt die Kosten
            "reader": (True, False, False),
            "stranger": (False, False, False),
        }
        for name, rights in expected.items():
            with self.subTest(user=name):
                self.assertEqual(self.rights(getattr(self, name), self.cost), rights)
        self.assertEqual(list(perms.visible_to(self.reader, CostEntry.objects.all())), [self.cost])
        self.assertFalse(perms.editable_by(self.reader, CostEntry.objects.all()).exists())
        self.assertFalse(perms.visible_to(self.stranger, CostEntry.objects.all()).exists())

    def test_only_owner_and_superuser_can_share(self):
        self.assertTrue(perms.can_share(self.owner, self.contract))
        self.assertTrue(perms.can_share(self.admin, self.contract))
        self.assertFalse(perms.can_share(self.editor, self.contract))
        self.assertFalse(perms.can_share(self.reader, self.contract))

    def test_inactive_owner_has_no_rights(self):
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(self.rights(self.owner, self.contract), (False, False, False))
        self.assertFalse(perms.visible_to(self.owner, Contract.objects.all()).exists())

    def test_visible_and_editable_lists(self):
        contracts = Contract.objects.all()
        for name, visible, editable in (
            ("owner", True, True), ("editor", True, True), ("reader", True, False),
            ("member", True, False), ("stranger", False, False), ("admin", True, True),
        ):
            with self.subTest(user=name):
                user = getattr(self, name)
                self.assertEqual(perms.visible_to(user, contracts).exists(), visible)
                self.assertEqual(perms.editable_by(user, contracts).exists(), editable)

    def test_owner_rights_only_for_own_objects(self):
        other = Provider.objects.create(owner=self.stranger, name="Fremder Partner")
        self.assertFalse(perms.can_view(self.owner, other))
        self.assertEqual(list(perms.visible_to(self.owner, Provider.objects.all())), [self.provider])

    def test_global_permissions_do_not_open_objects(self):
        """Globale Rechte (z. B. über eine Gruppe) gelten nicht für fremde Objekte"""
        self.stranger.user_permissions.add(
            Permission.objects.get(codename="view_contract"),
            Permission.objects.get(codename="change_contract"),
        )
        stranger = User.objects.get(pk=self.stranger.pk)  # Rechte-Cache leeren
        self.assertEqual(self.rights(stranger, self.contract), (False, False, False))
        self.assertFalse(perms.visible_to(stranger, Contract.objects.all()).exists())

    def test_grant_role_replaces_previous_role(self):
        perms.grant_role(self.contract, self.reader, perms.EDITOR)
        reader = User.objects.get(pk=self.reader.pk)
        self.assertTrue(perms.can_change(reader, self.contract))
        perms.grant_role(self.contract, reader, perms.READER)
        reader = User.objects.get(pk=self.reader.pk)
        self.assertEqual(self.rights(reader, self.contract), (True, False, False))
        perms.revoke_roles(self.contract, reader)
        reader = User.objects.get(pk=self.reader.pk)
        self.assertFalse(perms.can_view(reader, self.contract))

    def test_get_and_set_roles(self):
        roles = perms.get_roles(self.contract)
        self.assertEqual(roles["editor"]["users"], {self.editor})
        self.assertEqual(roles["reader"]["users"], {self.reader})
        self.assertEqual(roles["reader"]["groups"], {self.family})

        # Vollständig neu setzen: Leser fällt weg, Familie wird Bearbeiter,
        # wer in beiden Rollen steht, wird Bearbeiter, Eigentümer wird übersprungen
        perms.set_roles(
            self.contract,
            reader_users=[self.editor, self.owner],
            editor_users=[self.editor],
            editor_groups=[self.family],
        )
        roles = perms.get_roles(self.contract)
        self.assertEqual(roles["editor"]["users"], {self.editor})
        self.assertEqual(roles["editor"]["groups"], {self.family})
        self.assertEqual(roles["reader"]["users"], set())
        self.assertEqual(roles["reader"]["groups"], set())
        member = User.objects.get(pk=self.member.pk)
        self.assertTrue(perms.can_change(member, self.contract))

    def test_shares_are_deleted_with_object(self):
        perms.grant_role(self.provider, self.reader, perms.READER)
        self.assertEqual(ProviderUserObjectPermission.objects.count(), 1)
        Provider.objects.create(owner=self.owner, name="Weg")
        weg = Provider.objects.get(name="Weg")
        perms.grant_role(weg, self.family, perms.EDITOR)
        weg.delete()
        self.assertEqual(ProviderGroupObjectPermission.objects.count(), 0)
        self.assertEqual(ProviderUserObjectPermission.objects.count(), 1)

    def test_user_with_objects_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.owner.delete()
        # Ein Benutzer ohne eigene Objekte lässt sich löschen, seine Freigaben verschwinden
        self.reader.delete()
        self.assertFalse(perms.get_roles(self.contract)["reader"]["users"])

    def test_owner_can_be_changed(self):
        self.contract.owner = self.editor
        self.contract.save()
        editor = User.objects.get(pk=self.editor.pk)
        self.assertTrue(perms.can_delete(editor, self.contract))
        self.assertFalse(perms.can_view(User.objects.get(pk=self.owner.pk), self.contract))

    def test_transfer_ownership_keeps_previous_owner_as_editor(self):
        previous = perms.transfer_ownership(self.contract, self.editor)
        self.assertEqual(previous, self.owner)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.owner, self.editor)
        roles = perms.get_roles(self.contract)
        self.assertEqual(roles["editor"]["users"], {self.owner})  # neuer Eigentümer ohne Rolle
        owner = User.objects.get(pk=self.owner.pk)
        self.assertTrue(perms.can_change(owner, self.contract))
        self.assertFalse(perms.can_delete(owner, self.contract))
        self.assertIsNone(perms.transfer_ownership(self.contract, self.editor))

    def test_unique_names_per_owner(self):
        Provider.objects.create(owner=self.stranger, name="Versicherer")  # anderer Eigentümer: erlaubt
        with self.assertRaises(ValidationError):
            Provider(owner=self.owner, name="Versicherer").full_clean()
        Category.objects.create(owner=self.stranger, name="Versicherung")
        with self.assertRaises(ValidationError):
            Category.objects.create(owner=self.owner, name="Versicherung")

    def test_total_costs_only_for_visible_contracts(self):
        other = Contract.objects.create(
            owner=self.stranger, name="Fremd", provider=self.provider,
            start_date=timezone.localdate(), category=self.category, consumer=self.consumer,
        )
        CostEntry.objects.create(contract=other, amount=Decimal("100.00"), valid_from=timezone.localdate())
        self.assertEqual(
            total_current_costs(perms.visible_to(self.reader, Contract.objects.all()))["monthly"],
            Decimal("50.00"),
        )
        self.assertEqual(total_current_costs(Contract.objects.all())["monthly"], Decimal("150.00"))


class DefaultGroupsTest(TestCase):
    """Gruppen "Benutzer" (darf anlegen) und "Alle" (alle Benutzer)"""

    def test_users_group_may_only_add(self):
        group = Group.objects.get(name=perms.GROUP_USERS)
        codenames = set(group.permissions.values_list("codename", flat=True))
        self.assertEqual(codenames, {
            "add_contract", "add_costentry", "add_provider", "add_category", "add_consumer",
        })

    def test_new_users_join_group_all_and_see_shares(self):
        owner = User.objects.create_user("eigentuemer")
        category = Category.objects.create(owner=owner, name="Allgemein")
        perms.grant_role(category, Group.objects.get(name=perms.GROUP_ALL), perms.READER)

        newcomer = User.objects.create_user("neu")
        self.assertTrue(newcomer.groups.filter(name=perms.GROUP_ALL).exists())
        self.assertTrue(perms.can_view(newcomer, category))
        self.assertFalse(perms.can_change(newcomer, category))


class PermissionAdminTest(TestCase):
    """
    Version 0.3a beta: Berechtigungen im Admin.

    Seit 0.3d ist die Verwaltung nur für Superuser offen. Die Prüfungen je
    Objekt bleiben als zweite Absicherung bestehen und werden hier mit
    Zugang für Mitarbeiter getestet.
    """

    def setUp(self):
        patcher = patch.object(
            admin.site, "has_permission", lambda request: request.user.is_active and request.user.is_staff,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @classmethod
    def setUpTestData(cls):
        def staff(name, *groups):
            user = User.objects.create_user(name, password="test", is_staff=True)
            for group in groups:
                user.groups.add(Group.objects.get(name=group))
            return user

        cls.owner = staff("eigentuemer", perms.GROUP_USERS)
        cls.editor = staff("bearbeiter", perms.GROUP_USERS)
        cls.reader = staff("leser")            # nur Freigaben, darf nichts anlegen
        cls.stranger = staff("fremd", perms.GROUP_USERS)
        cls.admin = User.objects.create_superuser("admin", "admin@example.com", "test")

        cls.provider = Provider.objects.create(owner=cls.owner, name="Versicherer")
        cls.category = Category.objects.create(owner=cls.owner, name="Versicherung")
        cls.consumer = Consumer.objects.create(owner=cls.owner, name="Auto")
        cls.contract = Contract.objects.create(
            owner=cls.owner, name="KFZ", provider=cls.provider,
            start_date=timezone.localdate(), category=cls.category, consumer=cls.consumer,
        )
        CostEntry.objects.create(contract=cls.contract, amount=Decimal("50.00"), valid_from=timezone.localdate())
        cls.hidden_provider = Provider.objects.create(owner=cls.stranger, name="Geheim")
        hidden = Contract.objects.create(
            owner=cls.stranger, name="Fremder Vertrag", provider=cls.hidden_provider,
            start_date=timezone.localdate(), category=cls.category, consumer=cls.consumer,
        )
        CostEntry.objects.create(contract=hidden, amount=Decimal("100.00"), valid_from=timezone.localdate())

        perms.grant_role(cls.contract, cls.editor, perms.EDITOR)
        perms.grant_role(cls.contract, cls.reader, perms.READER)
        perms.grant_role(cls.provider, cls.editor, perms.EDITOR)

    def url(self, name, *args):
        return reverse(f"admin:contracts_{name}", args=args)

    def as_user(self, user):
        self.client.force_login(user)

    def provider_data(self, **extra):
        data = {"name": "Versicherer", "country": "DE"}
        data.update(extra)
        return data

    def test_changelist_shows_only_visible_contracts_and_totals(self):
        for user, names, monthly in (
            (self.owner, {"KFZ"}, "50,00 €"),
            (self.reader, {"KFZ"}, "50,00 €"),
            (self.stranger, {"Fremder Vertrag"}, "100,00 €"),
            (self.admin, {"KFZ", "Fremder Vertrag"}, "150,00 €"),
        ):
            with self.subTest(user=user.username):
                self.as_user(user)
                response = self.client.get(self.url("contract_changelist"))
                self.assertEqual({c.name for c in response.context["cl"].result_list}, names)
                self.assertEqual(response.context["cost_totals"]["monthly"], monthly)

    def test_filters_do_not_leak_hidden_names(self):
        self.as_user(self.reader)
        response = self.client.get(self.url("contract_changelist"))
        self.assertNotContains(response, "Geheim")

    def test_reader_sees_read_only_form(self):
        self.as_user(self.reader)
        response = self.client.get(self.url("contract_change", self.contract.pk))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="name"')
        self.assertNotContains(response, 'name="costs-0-amount"')
        self.assertNotContains(response, 'name="reader_users"')
        response = self.client.post(self.url("contract_change", self.contract.pk), {"name": "Neu"})
        self.assertEqual(response.status_code, 403)

    def test_editor_can_edit_but_not_share_or_delete(self):
        self.as_user(self.editor)
        response = self.client.get(self.url("contract_change", self.contract.pk))
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="costs-0-amount"')
        self.assertNotContains(response, 'name="reader_users"')
        self.assertNotContains(response, 'name="owner"')
        self.assertEqual(self.client.get(self.url("contract_delete", self.contract.pk)).status_code, 403)

        response = self.client.post(
            self.url("provider_change", self.provider.pk), self.provider_data(city="Köln"),
        )
        self.assertEqual(response.status_code, 302)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.city, "Köln")
        self.assertEqual(self.provider.owner, self.owner)

    def test_bulk_delete_checks_each_object(self):
        self.as_user(self.editor)
        response = self.client.post(self.url("contract_changelist"), {
            "action": "delete_selected", "_selected_action": [self.contract.pk], "post": "yes",
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Contract.objects.filter(pk=self.contract.pk).exists())

    def test_stranger_cannot_open_object(self):
        self.as_user(self.stranger)
        response = self.client.get(self.url("contract_change", self.contract.pk))
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(Contract.objects.filter(pk=self.contract.pk, name="Neu").exists())

    def test_owner_shares_and_changes_owner(self):
        self.as_user(self.owner)
        response = self.client.get(self.url("provider_change", self.provider.pk))
        self.assertContains(response, 'name="reader_users"')
        self.assertContains(response, 'name="owner"')

        family = Group.objects.create(name="Familie")
        response = self.client.post(self.url("provider_change", self.provider.pk), self.provider_data(
            owner=self.owner.pk,
            reader_users=[self.reader.pk],
            editor_groups=[family.pk],
        ))
        self.assertEqual(response.status_code, 302)
        roles = perms.get_roles(self.provider)
        self.assertEqual(roles["reader"]["users"], {self.reader})
        self.assertEqual(roles["editor"]["groups"], {family})
        self.assertEqual(roles["editor"]["users"], set())  # Bearbeiter wurde entfernt

        response = self.client.post(self.url("provider_change", self.provider.pk), self.provider_data(
            owner=self.editor.pk,
        ))
        self.assertEqual(response.status_code, 302)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.owner, self.editor)
        # Bisheriger Eigentümer wird automatisch Bearbeiter
        self.assertEqual(perms.get_roles(self.provider)["editor"]["users"], {self.owner})

    def test_add_sets_owner_and_checks_unique_name_per_owner(self):
        self.as_user(self.stranger)
        response = self.client.post(self.url("provider_add"), self.provider_data(
            reader_users=[self.reader.pk],
        ))
        self.assertEqual(response.status_code, 302)
        created = Provider.objects.get(owner=self.stranger, name="Versicherer")
        self.assertEqual(perms.get_roles(created)["reader"]["users"], {self.reader})

        # Gleicher Name beim selben Eigentümer: Formularfehler statt Serverfehler
        response = self.client.post(self.url("provider_add"), self.provider_data())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors)
        self.assertEqual(Provider.objects.filter(owner=self.stranger, name="Versicherer").count(), 1)

    def test_reader_without_group_cannot_add(self):
        self.as_user(self.reader)
        self.assertEqual(self.client.get(self.url("provider_add")).status_code, 403)

    def test_contract_form_offers_only_visible_master_data(self):
        self.as_user(self.owner)
        response = self.client.get(self.url("contract_add"))
        providers = list(response.context["adminform"].form.fields["provider"].queryset)
        self.assertEqual(providers, [self.provider])

    def test_editor_keeps_current_value_not_visible_to_him(self):
        """Ein Bearbeiter kann speichern, auch wenn er den Verbraucher nicht sehen darf"""
        self.as_user(self.editor)
        response = self.client.get(self.url("contract_change", self.contract.pk))
        consumers = list(response.context["adminform"].form.fields["consumer"].queryset)
        self.assertEqual(consumers, [self.consumer])

    def test_admin_index_for_user_with_shares_only(self):
        self.as_user(self.reader)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, self.url("contract_changelist"))

    def test_permission_tables_only_for_superuser(self):
        url = reverse("admin:contracts_contractuserobjectpermission_changelist")
        self.as_user(self.owner)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.as_user(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_user_with_objects_cannot_be_deleted_in_admin(self):
        self.as_user(self.admin)
        response = self.client.get(reverse("admin:auth_user_delete", args=[self.owner.pk]))
        self.assertContains(response, "geschützt")



class AdminSuperuserOnlyTest(TestCase):
    """Version 0.3d beta: Verwaltung nur für Superuser"""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("mitarbeiter", password="test", is_staff=True)
        cls.user = User.objects.create_user("benutzer", password="test")
        cls.admin = User.objects.create_superuser("admin", "admin@example.com", "test")

    def test_superuser_has_access(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_other_users_are_sent_to_frontend(self):
        for user in (self.staff, self.user):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("admin:index"), follow=True)
                self.assertRedirects(response, reverse("dashboard"))
                response = self.client.get(reverse("admin:contracts_contract_changelist"), follow=True)
                self.assertRedirects(response, reverse("dashboard"))

    def test_staff_login_does_not_open_admin(self):
        response = self.client.post(
            reverse("admin:login"), {"username": "mitarbeiter", "password": "test", "next": "/admin/"},
            follow=True,
        )
        self.assertRedirects(response, reverse("dashboard"))

    def test_anonymous_gets_admin_login(self):
        response = self.client.get(reverse("admin:index"))
        self.assertRedirects(response, f"{reverse('admin:login')}?next={reverse('admin:index')}")


class CategoryLevelTest(TestCase):
    """Version 0.3d beta: keine drei Ebenen über eine Kategorie mit Unterkategorien"""

    def test_category_with_children_cannot_get_parent(self):
        owner = User.objects.create_user("anna")
        insurance = Category.objects.create(owner=owner, name="Versicherung")
        Category.objects.create(owner=owner, name="KFZ", parent=insurance)
        home = Category.objects.create(owner=owner, name="Wohnen")
        insurance.parent = home
        with self.assertRaises(ValidationError):
            insurance.save()

    def test_category_cannot_be_its_own_parent(self):
        owner = User.objects.create_user("anna")
        home = Category.objects.create(owner=owner, name="Wohnen")
        home.parent = home
        with self.assertRaises(ValidationError):
            home.full_clean()


def make_image(image_format="PNG", size=(600, 300), mode="RGB", color="red"):
    """
    Erzeugt ein Testbild im Speicher (Pillow).
    """
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new(mode, size, color).save(output, format=image_format)
    return output.getvalue()


class ProviderLogoTest(TestCase):
    """Logo am Vertragspartner: Prüfung, Verkleinerung, Löschen der Dateien (0.4 beta)"""

    def setUp(self):
        import shutil
        import tempfile

        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.owner = User.objects.create_user("anna")

    def upload(self, content, name="logo.png"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, content)

    def make_provider(self, content=None, name="Stadtwerke"):
        provider = Provider(owner=self.owner, name=name)
        if content is not None:
            provider.logo = self.upload(content)
        provider.full_clean()
        with self.captureOnCommitCallbacks(execute=True):
            provider.save()
        return provider

    def test_logo_is_optional(self):
        provider = self.make_provider()
        self.assertFalse(provider.logo)

    def test_logo_is_resized_to_png_with_random_name(self):
        from PIL import Image

        provider = self.make_provider(make_image("JPEG", (1000, 500)), name="Bank")
        self.assertRegex(provider.logo.name, r"^logos/[0-9a-f]{32}\.png$")
        with Image.open(provider.logo.path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (256, 128))
            self.assertNotIn("exif", image.info)

    def test_small_logo_is_not_enlarged(self):
        from PIL import Image

        # Halbtransparent, damit WebP den Alphakanal behält
        provider = self.make_provider(make_image("WEBP", (64, 64), "RGBA", (255, 0, 0, 128)))
        with Image.open(provider.logo.path) as image:
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.mode, "RGBA")

    def test_rejects_other_formats(self):
        for image_format in ("GIF", "BMP"):
            with self.subTest(image_format=image_format):
                with self.assertRaises(ValidationError) as error:
                    self.make_provider(make_image(image_format), name=image_format)
                self.assertIn("logo", error.exception.message_dict)

    def test_rejects_non_images(self):
        with self.assertRaises(ValidationError) as error:
            self.make_provider(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
        self.assertIn("logo", error.exception.message_dict)

    def test_rejects_large_files(self):
        from . import logos

        with patch.object(logos, "MAX_UPLOAD_SIZE", 100):
            with self.assertRaises(ValidationError) as error:
                self.make_provider(make_image())
        self.assertIn("logo", error.exception.message_dict)

    def test_old_file_is_deleted_when_replaced_or_removed(self):
        import os

        provider = self.make_provider(make_image())
        first = provider.logo.path
        provider.logo = self.upload(make_image("PNG", (300, 300)))
        with self.captureOnCommitCallbacks(execute=True):
            provider.save()
        self.assertFalse(os.path.exists(first))
        second = provider.logo.path
        self.assertTrue(os.path.exists(second))

        provider.logo = None
        with self.captureOnCommitCallbacks(execute=True):
            provider.save()
        self.assertFalse(os.path.exists(second))

    def test_unchanged_logo_is_kept_on_save(self):
        import os

        provider = self.make_provider(make_image())
        provider.name = "Neuer Name"
        with self.captureOnCommitCallbacks(execute=True):
            provider.save()
        self.assertTrue(os.path.exists(provider.logo.path))

    def test_file_is_deleted_with_provider(self):
        import os

        provider = self.make_provider(make_image())
        path = provider.logo.path
        with self.captureOnCommitCallbacks(execute=True):
            provider.delete()
        self.assertFalse(os.path.exists(path))


@override_settings(APP_BASE_URL="https://vertraege.example.test")
class ReminderTest(TestCase):
    """Erinnerungen: Zeitraum, täglicher Versand, Empfänger, Abschluss (0.5 beta)"""

    def setUp(self):
        from datetime import date

        from django.core import mail

        mail.outbox.clear()
        self.owner = User.objects.create_user("anna", "anna@example.com")
        self.reader = User.objects.create_user("bert", "bert@example.com")
        self.stranger = User.objects.create_user("fremd", "fremd@example.com")
        self.no_mail = User.objects.create_user("ohne")
        self.contract = Contract.objects.create(
            owner=self.owner, name="Kfz-Versicherung",
            provider=Provider.objects.create(owner=self.owner, name="DEVK"),
            category=Category.objects.create(owner=self.owner, name="Versicherung"),
            consumer=Consumer.objects.create(owner=self.owner, name="Touran"),
            start_date=date(2025, 1, 1), contract_number="K-1",
        )
        perms.grant_role(self.contract, self.reader, perms.READER)
        perms.grant_role(self.contract, self.no_mail, perms.READER)
        self.remind_on = date(2026, 12, 31)
        self.reminder = Reminder.objects.create(
            contract=self.contract, enabled=True, remind_on=self.remind_on, lead_days=3,
            reason=Reminder.Reason.CANCELLATION, note="Schriftlich kündigen",
        )
        self.reminder.recipients.set([self.owner, self.reader, self.stranger, self.no_mail])

    def run_on(self, day):
        from datetime import date

        from django.core import mail

        from . import reminders

        mail.outbox.clear()
        result = reminders.send_due_reminders(date(2026, 12, day))
        self.reminder.refresh_from_db()
        return result, list(mail.outbox)

    def test_send_window(self):
        from datetime import date

        self.assertEqual(self.reminder.send_from, date(2026, 12, 28))
        self.assertFalse(self.reminder.is_due(date(2026, 12, 27)))
        self.assertTrue(self.reminder.is_due(date(2026, 12, 28)))
        self.assertTrue(self.reminder.is_due(date(2026, 12, 31)))
        self.assertFalse(self.reminder.is_due(date(2027, 1, 1)))

    def test_nothing_before_window(self):
        result, outbox = self.run_on(27)
        self.assertEqual(outbox, [])
        self.assertTrue(self.reminder.enabled)

    def test_daily_mail_only_to_allowed_recipients_with_email(self):
        result, outbox = self.run_on(28)
        self.assertEqual(sorted(message.to[0] for message in outbox), ["anna@example.com", "bert@example.com"])
        self.assertEqual(sorted(result.skipped), ["fremd", "ohne"])
        self.assertEqual(self.reminder.last_sent_on.day, 28)
        message = outbox[0]
        self.assertIn("Erinnerung: Kündigung – Kfz-Versicherung am 31.12.2026", message.subject)
        self.assertIn("in 3 Tagen", message.body)
        self.assertIn("Schriftlich kündigen", message.body)
        self.assertIn(f"https://vertraege.example.test/contracts/{self.contract.pk}/", message.body)
        self.assertIn("Vertrag öffnen", message.alternatives[0][0])

        # Am selben Tag nicht noch einmal
        result, outbox = self.run_on(28)
        self.assertEqual(outbox, [])
        # Am nächsten Tag wieder
        result, outbox = self.run_on(29)
        self.assertEqual(len(outbox), 2)

    def test_mail_in_recipient_language(self):
        from web.models import UserPreferences

        UserPreferences.objects.create(user=self.reader, language="en")
        result, outbox = self.run_on(30)
        english = next(message for message in outbox if message.to == ["bert@example.com"])
        self.assertIn("Reminder: Cancellation – Kfz-Versicherung", english.subject)
        self.assertIn("in 1 day", english.body)

    def test_last_mail_on_reminder_date_then_done(self):
        from datetime import date

        result, outbox = self.run_on(31)
        self.assertEqual(len(outbox), 2)
        self.assertIn("heute", outbox[0].body)
        self.assertFalse(self.reminder.enabled)
        self.assertEqual(self.reminder.completed_on, date(2026, 12, 31))
        self.assertEqual(self.reminder.status, "done")
        self.assertEqual(result.completed, 1)

    def test_missed_reminder_is_completed_without_mail(self):
        from datetime import date

        from . import reminders

        from django.core import mail

        mail.outbox.clear()
        reminders.send_due_reminders(date(2027, 1, 5))
        self.reminder.refresh_from_db()
        self.assertEqual(mail.outbox, [])
        self.assertFalse(self.reminder.enabled)

    def test_disabled_reminder_sends_nothing(self):
        Reminder.objects.filter(pk=self.reminder.pk).update(enabled=False)
        result, outbox = self.run_on(30)
        self.assertEqual(outbox, [])

    def test_reenabling_clears_done(self):
        from datetime import date

        self.run_on(31)
        self.reminder.enabled = True
        self.reminder.remind_on = date(2099, 12, 31)
        self.reminder.full_clean()
        self.reminder.save()
        self.assertIsNone(self.reminder.completed_on)
        self.assertEqual(self.reminder.status, "scheduled")

    def test_validation(self):
        from datetime import date

        reminder = Reminder(contract=self.contract, enabled=True)
        with self.assertRaises(ValidationError) as error:
            reminder.clean()
        self.assertIn("remind_on", error.exception.message_dict)
        reminder.remind_on = date(2000, 1, 1)
        with self.assertRaises(ValidationError):
            reminder.clean()
        reminder.lead_days = 61
        with self.assertRaises(ValidationError):
            reminder.clean_fields(exclude=["contract"])

    def test_one_reminder_per_contract(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reminder.objects.create(contract=self.contract)

    def test_smtp_error_does_not_stop_run(self):
        from . import mail as mail_module

        calls = []

        def failing(name, context, user):
            calls.append(user.username)
            if user.username == "anna":
                raise OSError("SMTP nicht erreichbar")
            return True

        with patch.object(mail_module, "send_mail_to_user", failing), \
                patch("contracts.reminders.send_mail_to_user", failing):
            result, outbox = self.run_on(29)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.sent, 1)
        self.assertEqual(self.reminder.last_sent_on.day, 29)

    def test_management_command(self):
        from io import StringIO

        from django.core.management import call_command

        output = StringIO()
        with patch("contracts.reminders.timezone.localdate", return_value=self.remind_on):
            call_command("send_reminders", stdout=output)
        self.assertIn("2 E-Mail(s) zu 1 Erinnerung(en) verschickt", output.getvalue())
        self.assertIn("fremd", output.getvalue())


class ReminderScheduleTest(TestCase):
    """Täglicher Zeitplan im Worker (django-q2)"""

    def test_schedule_exists_after_migrate(self):
        from django_q.models import Schedule

        from .apps import REMINDER_SCHEDULE_NAME

        schedule = Schedule.objects.get(name=REMINDER_SCHEDULE_NAME)
        self.assertEqual(schedule.func, "contracts.tasks.send_due_reminders")
        self.assertEqual(schedule.schedule_type, Schedule.DAILY)
        self.assertEqual(timezone.localtime(schedule.next_run).strftime("%H:%M"), "07:00")

    def test_send_time_change_updates_schedule(self):
        from django_q.models import Schedule

        from .apps import REMINDER_SCHEDULE_NAME, ensure_reminder_schedule

        with self.settings(REMINDER_SEND_TIME="18:30"):
            ensure_reminder_schedule(sender=None)
        schedule = Schedule.objects.get(name=REMINDER_SCHEDULE_NAME)
        self.assertEqual(timezone.localtime(schedule.next_run).strftime("%H:%M"), "18:30")
        self.assertEqual(Schedule.objects.filter(name=REMINDER_SCHEDULE_NAME).count(), 1)

    def test_next_send_time(self):
        from datetime import datetime

        from .apps import next_send_time

        before = timezone.make_aware(datetime(2026, 11, 7, 6, 0))
        after = timezone.make_aware(datetime(2026, 11, 7, 8, 0))
        self.assertEqual(timezone.localtime(next_send_time("07:00", before)).day, 7)
        self.assertEqual(timezone.localtime(next_send_time("07:00", after)).day, 8)


class DocumentModelTest(TestCase):
    """Dokumente zu Verträgen: Ablage, Dateinamen, Prüfung, Löschen (0.7 beta)"""

    def setUp(self):
        import shutil
        import tempfile

        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)
        self.owner = default_owner()
        self.reader = User.objects.create_user("leser")
        self.stranger = User.objects.create_user("fremd")
        self.contract = self.make_contract("Strom")
        perms.grant_role(self.contract, self.reader, perms.READER)

    def make_contract(self, name):
        return Contract.objects.create(
            owner=self.owner, name=name, start_date=timezone.localdate(),
            provider=Provider.objects.get_or_create(owner=self.owner, name="Stadtwerke")[0],
            category=Category.objects.get_or_create(owner=self.owner, name="Energie")[0],
            consumer=Consumer.objects.get_or_create(owner=self.owner, name="Haushalt")[0],
        )

    def upload(self, name="Rechnung 2026.pdf", content=b"%PDF-1.4 test", contract=None):
        from django.core.files.uploadedfile import SimpleUploadedFile

        document = Document(
            contract=contract or self.contract, name="Rechnung", source=Document.Source.UPLOAD,
            file=SimpleUploadedFile(name, content),
        )
        document.full_clean()
        with self.captureOnCommitCallbacks(execute=True):
            document.save()
        return document

    def path(self, document):
        import os

        return os.path.join(self.media_root, document.file.name)

    def test_folder_by_year_and_reference(self):
        document = self.upload()
        reference = self.contract.reference
        self.assertEqual(document.file.name, f"documents/{reference[:4]}/{reference}/Rechnung_2026.pdf")
        self.assertEqual(document.filename, "Rechnung_2026.pdf")

    def test_existing_name_gets_counter(self):
        names = [self.upload("brief.pdf").filename for _ in range(3)]
        self.assertEqual(names, ["brief.pdf", "brief_2.pdf", "brief_3.pdf"])
        # Gleicher Name in einem anderen Vertrag ist frei
        other = self.upload("brief.pdf", contract=self.make_contract("Gas"))
        self.assertEqual(other.filename, "brief.pdf")

    def test_filename_is_cleaned(self):
        from . import documents

        self.assertEqual(documents.clean_filename("../../etc/Passwort Liste.PDF"), "Passwort_Liste.pdf")
        self.assertEqual(documents.clean_filename("C:\\Users\\x\\Scan.jpg"), "Scan.jpg")
        self.assertEqual(documents.clean_filename("???.pdf"), "document.pdf")
        self.assertEqual(len(documents.clean_filename("a" * 300 + ".pdf")), 124)

    def test_file_type_and_size(self):
        from . import documents

        for name in ("seite.html", "bild.svg", "script.js", "ohne_endung"):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as ctx:
                    self.upload(name)
                self.assertIn("file", ctx.exception.message_dict)
        with patch.object(documents, "MAX_UPLOAD_SIZE", 10):
            with self.assertRaises(ValidationError) as ctx:
                self.upload("gross.pdf", content=b"x" * 11)
            self.assertIn("file", ctx.exception.message_dict)
            self.upload("klein.pdf", content=b"x" * 10)
        for name in ("a.pdf", "b.JPG", "c.docx", "d.xlsx", "e.odt", "f.ods", "g.txt", "h.eml", "i.webp", "j.png"):
            with self.subTest(name=name):
                self.upload(name)

    def test_source_rules(self):
        cases = [
            (Document(contract=self.contract, name="X", source=Document.Source.UPLOAD), "file"),
            (Document(contract=self.contract, name="X", source=Document.Source.PAPERLESS), "paperless_document_id"),
            (Document(contract=self.contract, name="X", source=Document.Source.PAPERLESS,
                      paperless_document_id=0), "paperless_document_id"),
        ]
        for document, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError) as ctx:
                    document.full_clean()
                self.assertIn(field, ctx.exception.message_dict)
        paperless = Document(contract=self.contract, name="Police", source=Document.Source.PAPERLESS,
                             paperless_document_id=7)
        paperless.full_clean()
        paperless.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Document.objects.create(contract=self.contract, name="Doppelt", source="paperless", paperless_document_id=7)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Document.objects.create(contract=self.contract, name="Leer", source="upload")

    def test_paperless_url(self):
        document = Document(contract=self.contract, name="Police", source="paperless", paperless_document_id=7)
        with self.settings(PAPERLESS_URL="https://paperless.example.de/"):
            self.assertEqual(document.paperless_url, "https://paperless.example.de/documents/7/details")
        with self.settings(PAPERLESS_URL=""):
            self.assertIsNone(document.paperless_url)

    def test_file_deleted_with_document_and_empty_folders(self):
        import os

        first, second = self.upload("a.pdf"), self.upload("b.pdf")
        with self.captureOnCommitCallbacks(execute=True):
            first.delete()
        self.assertFalse(os.path.exists(self.path(first)))
        self.assertTrue(os.path.exists(self.path(second)))
        with self.captureOnCommitCallbacks(execute=True):
            second.delete()
        # Ordner des Vertrags und des Jahres sind leer und werden entfernt
        self.assertEqual(os.listdir(os.path.join(self.media_root, "documents")), [])

    def test_files_deleted_with_contract(self):
        import os

        document = self.upload()
        with self.captureOnCommitCallbacks(execute=True):
            self.contract.delete()
        self.assertFalse(os.path.exists(self.path(document)))

    def test_cost_entry_only_links_own_documents(self):
        own = self.upload()
        other = self.upload(contract=self.make_contract("Gas"))
        cost = CostEntry(contract=self.contract, amount=1, valid_from=timezone.localdate(), document=own)
        cost.full_clean()
        cost.document = other
        with self.assertRaises(ValidationError) as ctx:
            cost.full_clean()
        self.assertIn("document", ctx.exception.message_dict)

    def test_cost_entry_keeps_amount_when_document_deleted(self):
        document = self.upload()
        cost = CostEntry.objects.create(
            contract=self.contract, amount=1, valid_from=timezone.localdate(), document=document,
        )
        with self.captureOnCommitCallbacks(execute=True):
            document.delete()
        cost.refresh_from_db()
        self.assertIsNone(cost.document)

    def test_permissions_follow_contract(self):
        document = self.upload()
        self.assertTrue(perms.can_view(self.reader, document))
        self.assertFalse(perms.can_change(self.reader, document))
        self.assertFalse(perms.can_delete(self.reader, document))
        self.assertTrue(perms.can_change(self.owner, document))
        self.assertFalse(perms.can_view(self.stranger, document))
        self.assertEqual(list(perms.visible_to(self.reader, Document.objects.all())), [document])
        self.assertEqual(list(perms.visible_to(self.stranger, Document.objects.all())), [])
        self.assertEqual(list(perms.editable_by(self.reader, Document.objects.all())), [])


class PaperlessIdMigrationTest(TransactionTestCase):
    """0012: Paperless-IDs der Kosteneinträge werden zu Dokumenten (0.7 beta)"""

    def test_ids_become_documents(self):
        from django.apps import apps as global_apps
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        before = [("contracts", "0011_document")]
        after = [("contracts", "0012_paperless_ids_to_documents")]
        latest = executor.loader.graph.leaf_nodes("contracts")
        executor.migrate(before)
        try:
            old_apps = executor.loader.project_state(before).apps
            UserModel = old_apps.get_model("auth", "User")
            owner = UserModel.objects.create(username="migration")
            common = {"owner_id": owner.pk}
            OldContract = old_apps.get_model("contracts", "Contract")
            contract = OldContract.objects.create(
                name="KFZ", start_date=timezone.localdate(), reference="20260099",
                provider=old_apps.get_model("contracts", "Provider").objects.create(name="P", **common),
                category=old_apps.get_model("contracts", "Category").objects.create(name="C", **common),
                consumer=old_apps.get_model("contracts", "Consumer").objects.create(name="V", **common),
                **common,
            )
            OldCost = old_apps.get_model("contracts", "CostEntry")
            for day, document_id in ((1, 55), (2, 55), (3, None)):
                OldCost.objects.create(
                    contract=contract, amount=1, valid_from=timezone.localdate() - timedelta(days=day),
                    paperless_document_id=document_id,
                )
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            new_apps = executor.loader.project_state(after).apps
            documents = list(new_apps.get_model("contracts", "Document").objects.filter(contract_id=contract.pk))
            self.assertEqual([(d.source, d.paperless_document_id, d.name) for d in documents],
                             [("paperless", 55, "Paperless #55")])
            linked = new_apps.get_model("contracts", "CostEntry").objects.filter(document=documents[0]).count()
            self.assertEqual(linked, 2)
        finally:
            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate(latest)
            global_apps.clear_cache()


class BackupTest(TestCase):
    """Backup und Import des gesamten Bestands (0.9 beta)"""

    def setUp(self):
        import shutil
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile

        from assistant.models import Attachment, Conversation, Message

        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.anna = User.objects.create_user("anna", email="anna@example.de", password="geheim-12345")
        self.ben = User.objects.create_user("ben")
        self.family = Group.objects.create(name="Familie")
        self.ben.groups.add(self.family)
        self.insurance = Category.objects.create(owner=self.anna, name="Versicherung")
        self.car = Category.objects.create(owner=self.anna, name="KFZ", parent=self.insurance)
        self.provider = Provider.objects.create(owner=self.anna, name="Allianz", city="München")
        self.consumer = Consumer.objects.create(owner=self.anna, name="Auto", number="M-AB 123")
        self.contract = Contract.objects.create(
            owner=self.anna, name="KFZ-Haftpflicht", start_date=timezone.localdate(),
            provider=self.provider, category=self.car, consumer=self.consumer,
        )
        self.document = Document.objects.create(
            contract=self.contract, name="Police", source=Document.Source.UPLOAD,
            file=SimpleUploadedFile("Police.pdf", b"%PDF-1.4 police"),
        )
        CostEntry.objects.create(
            contract=self.contract, amount=Decimal("120.50"), valid_from=timezone.localdate(),
            payment_interval="yearly", document=self.document,
        )
        reminder = Reminder.objects.create(
            contract=self.contract, remind_on=timezone.localdate() + timedelta(days=30), lead_days=7,
        )
        reminder.recipients.add(self.anna)
        perms.grant_role(self.contract, self.family, perms.READER)
        perms.grant_role(self.provider, self.ben, perms.EDITOR)
        conversation = Conversation.objects.create(user=self.anna, title="Frage")
        message = Message.objects.create(conversation=conversation, role="user", content="Was zahle ich?")
        self.attachment = Attachment.objects.create(
            conversation=conversation, message=message, original_name="Brief.txt", size=5,
            file=SimpleUploadedFile("Brief.txt", b"Hallo"),
        )

    def export(self):
        import io

        from . import backup

        buffer = io.BytesIO()
        info = backup.write_backup(buffer)
        buffer.seek(0)
        return buffer, info

    def read_json(self, buffer, name):
        import json
        import zipfile

        with zipfile.ZipFile(buffer) as archive:
            return json.loads(archive.read(name))

    def rewrite(self, buffer, change):
        """Kopie des Archivs; ``change(name, data)`` liefert den neuen Inhalt oder None (weglassen)."""
        import io
        import zipfile

        result = io.BytesIO()
        with zipfile.ZipFile(buffer) as source, zipfile.ZipFile(result, "w") as target:
            for name in source.namelist():
                data = change(name, source.read(name))
                if data is not None:
                    target.writestr(name, data)
        result.seek(0)
        return result

    def file_content(self, name):
        import os

        with open(os.path.join(self.media_root, name), "rb") as handle:
            return handle.read()

    def test_export_content(self):
        import zipfile

        buffer, info = self.export()
        self.assertEqual(info.counts["contracts.contract"], 1)
        self.assertEqual(info.counts["auth.user"], 2)
        self.assertEqual(info.media_files, 2)
        self.assertEqual(info.missing_files, [])
        with zipfile.ZipFile(buffer) as archive:
            names = archive.namelist()
            self.assertIn("media/" + self.document.file.name, names)
            self.assertIn("media/" + self.attachment.file.name, names)
            self.assertEqual(archive.read("media/" + self.document.file.name), b"%PDF-1.4 police")
        manifest = self.read_json(buffer, "manifest.json")
        from django.conf import settings

        self.assertEqual(manifest["app_version"], settings.APP_VERSION)
        self.assertEqual(manifest["format"], 1)

    def test_export_has_no_passwords(self):
        buffer, _info = self.export()
        users = [row for row in self.read_json(buffer, "data.json") if row["model"] == "auth.user"]
        self.assertEqual(len(users), 2)
        # Nur, ob es ein Passwort gab: "" = ja, "!" = nein
        self.assertTrue(all(row["fields"]["password"] in ("", "!") for row in users))
        buffer.seek(0)
        self.assertNotIn(self.anna.password.encode(), buffer.read())

    def test_export_reports_missing_file(self):
        import os

        os.remove(os.path.join(self.media_root, self.document.file.name))
        _buffer, info = self.export()
        self.assertEqual(info.missing_files, [self.document.file.name])
        self.assertEqual(info.media_files, 1)

    def test_import_replaces_everything(self):
        from django.contrib.sessions.models import Session

        from assistant.models import Attachment, Conversation

        from . import backup

        buffer, _info = self.export()
        document_name = self.document.file.name
        contract_pk = self.contract.pk

        # Bestand verändern: Vertrag samt Datei löschen, Neues anlegen
        with self.captureOnCommitCallbacks(execute=True):
            self.contract.delete()
        Contract.objects.create(
            owner=self.anna, name="Neu", start_date=timezone.localdate(),
            provider=self.provider, category=self.insurance, consumer=self.consumer,
        )
        User.objects.create_user("carla")
        self.client.force_login(self.anna)
        self.assertTrue(Session.objects.exists())

        info = backup.import_backup(buffer)
        self.assertEqual(info.counts["contracts.contract"], 1)

        # Daten wie beim Export
        self.assertEqual(list(User.objects.order_by("username").values_list("username", flat=True)), ["anna", "ben"])
        contract = Contract.objects.get()
        self.assertEqual((contract.pk, contract.name), (contract_pk, "KFZ-Haftpflicht"))
        self.assertEqual(contract.category.parent.name, "Versicherung")
        self.assertEqual(contract.reference, self.contract.reference)
        cost = contract.costs.get()
        self.assertEqual((cost.amount, cost.document.name), (Decimal("120.50"), "Police"))
        self.assertEqual(list(contract.reminder.recipients.all()), [User.objects.get(username="anna")])
        self.assertEqual(Conversation.objects.get().messages.get().content, "Was zahle ich?")

        # Gruppen und Freigaben
        ben = User.objects.get(username="ben")
        self.assertTrue(ben.groups.filter(name="Familie").exists())
        self.assertTrue(perms.can_view(ben, contract))
        self.assertFalse(perms.can_change(ben, contract))
        self.assertTrue(perms.can_change(ben, contract.provider))
        # Globale Rechte der Standardgruppen (über Namen statt IDs gesichert)
        self.assertTrue(Group.objects.get(name=perms.GROUP_USERS).permissions.filter(codename="add_contract").exists())
        self.assertTrue(User.objects.get(username="anna").groups.filter(name=perms.GROUP_ALL).exists())

        # Dateien wie beim Export
        self.assertEqual(self.file_content(document_name), b"%PDF-1.4 police")
        self.assertEqual(self.file_content(Attachment.objects.get().file.name), b"Hallo")

        # Kein Passwort, keine Sitzungen
        self.assertFalse(User.objects.get(username="anna").has_usable_password())
        self.assertFalse(Session.objects.exists())

        # Zähler stehen hinter den importierten Datensätzen
        new_user = User.objects.create_user("dora")
        self.assertGreater(new_user.pk, ben.pk)
        Provider.objects.create(owner=new_user, name="Neu")

    def test_import_ignores_password_hash_in_file(self):
        import json

        from . import backup

        buffer, _info = self.export()

        def change(name, data):
            if name != "data.json":
                return data
            rows = json.loads(data)
            for row in rows:
                if row["model"] == "auth.user":
                    row["fields"]["password"] = self.anna.password
            return json.dumps(rows)

        backup.import_backup(self.rewrite(buffer, change))
        self.assertFalse(User.objects.get(username="anna").has_usable_password())

    def assert_rejected(self, buffer):
        from . import backup

        with self.assertRaises(backup.BackupError):
            backup.inspect_backup(buffer)
        buffer.seek(0)
        with self.assertRaises(backup.BackupError):
            backup.import_backup(buffer)
        # Nichts wurde geändert
        self.assertTrue(Contract.objects.filter(pk=self.contract.pk).exists())
        self.assertTrue(User.objects.get(username="anna").has_usable_password())
        self.assertEqual(self.file_content(self.document.file.name), b"%PDF-1.4 police")
        import os

        self.assertFalse([name for name in os.listdir(self.media_root) if name.startswith(".import-")])

    def assert_rejected_on_import(self, buffer):
        """Fehler, der erst beim Laden auffällt: Import bricht ab, nichts ändert sich."""
        from . import backup

        with self.assertRaises(backup.BackupError):
            backup.import_backup(buffer)
        self.assertTrue(Contract.objects.filter(pk=self.contract.pk).exists())
        self.assertTrue(User.objects.get(username="anna").has_usable_password())
        self.assertEqual(self.file_content(self.document.file.name), b"%PDF-1.4 police")

    def change_manifest(self, buffer, **values):
        import json

        def change(name, data):
            if name != "manifest.json":
                return data
            manifest = json.loads(data)
            manifest.update(values)
            return json.dumps(manifest)

        return self.rewrite(buffer, change)

    def test_other_version_rejected(self):
        buffer, _info = self.export()
        self.assert_rejected(self.change_manifest(buffer, app_version="0.1"))

    def test_other_migration_state_rejected(self):
        buffer, _info = self.export()
        self.assert_rejected(self.change_manifest(buffer, migrations={"contracts": ["0001_initial"]}))

    def test_unknown_format_rejected(self):
        buffer, _info = self.export()
        self.assert_rejected(self.change_manifest(buffer, format=99))

    def test_not_a_backup_rejected(self):
        import io

        self.assert_rejected(io.BytesIO(b"kein zip"))
        buffer, _info = self.export()
        self.assert_rejected(self.rewrite(buffer, lambda name, data: None if name == "manifest.json" else data))

    def test_unsafe_path_rejected(self):
        import io
        import zipfile

        for name in ("media/../evil.txt", "media/other/x.txt", "media/logos/../../x", "evil.txt", "/etc/passwd"):
            buffer, _info = self.export()
            raw = io.BytesIO(buffer.read())
            with zipfile.ZipFile(raw, "a") as archive:
                archive.writestr(name, b"x")
            raw.seek(0)
            self.assert_rejected(raw)

    def test_unknown_model_rejected(self):
        import json

        buffer, _info = self.export()

        def change(name, data):
            if name != "data.json":
                return data
            rows = json.loads(data)
            rows.append({"model": "sessions.session", "pk": "x", "fields": {}})
            return json.dumps(rows)

        self.assert_rejected(self.rewrite(buffer, change))

    def test_broken_reference_rolls_back(self):
        import json

        buffer, _info = self.export()

        def change(name, data):
            if name != "data.json":
                return data
            rows = json.loads(data)
            # Vertrag verweist auf einen Partner, den es nicht gibt
            for row in rows:
                if row["model"] == "contracts.contract":
                    row["fields"]["provider"] = 99999
            return json.dumps(rows)

        self.assert_rejected_on_import(self.rewrite(buffer, change))

    def test_commands(self):
        import os

        from django.core.management import call_command
        from django.core.management.base import CommandError

        path = os.path.join(self.media_root, "backup.zip")
        call_command("export_backup", output=path, stdout=open(os.devnull, "w"))
        User.objects.create_user("carla")
        call_command("import_backup", path, yes=True, stdout=open(os.devnull, "w"))
        self.assertFalse(User.objects.filter(username="carla").exists())
        with self.assertRaises(CommandError):
            call_command("import_backup", os.path.join(self.media_root, "fehlt.zip"), yes=True)


class EnsureAdminTest(TestCase):
    """Erster Administrator aus der .env (0.9.1 beta)"""

    ENV = {"ADMIN_USERNAME": "chef", "ADMIN_PASSWORD": "Sehr-gutes-Passwort-7391", "ADMIN_EMAIL": "chef@example.com"}

    def run_command(self, **env):
        """Führt den Befehl mit genau diesen ADMIN_*-Variablen aus; gibt (stdout, stderr) zurück."""
        import io
        import os

        from django.core.management import call_command

        out, err = io.StringIO(), io.StringIO()
        clean = {k: v for k, v in os.environ.items() if not k.startswith("ADMIN_")}
        with patch.dict(os.environ, {**clean, **env}, clear=True):
            call_command("ensure_admin", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_creates_superuser_when_none_exists(self):
        out, err = self.run_command(**self.ENV)
        user = User.objects.get(username="chef")
        self.assertTrue(user.is_superuser and user.is_staff and user.is_active)
        self.assertEqual(user.email, "chef@example.com")
        self.assertTrue(user.check_password("Sehr-gutes-Passwort-7391"))
        self.assertTrue(user.groups.filter(name=perms.GROUP_ALL).exists())
        self.assertIn("wurde angelegt", out)
        self.assertEqual(err, "")

    def test_email_is_optional(self):
        self.run_command(ADMIN_USERNAME="chef", ADMIN_PASSWORD="Sehr-gutes-Passwort-7391")
        self.assertEqual(User.objects.get(username="chef").email, "")

    def test_password_never_in_output(self):
        out, err = self.run_command(**self.ENV)
        self.assertNotIn("Sehr-gutes-Passwort-7391", out + err)
        out, err = self.run_command(ADMIN_USERNAME="x y", ADMIN_PASSWORD="Sehr-gutes-Passwort-7391")
        self.assertNotIn("Sehr-gutes-Passwort-7391", out + err)

    def test_nothing_without_variables(self):
        out, err = self.run_command()
        self.assertFalse(User.objects.exists())
        self.assertEqual((out, err), ("", ""))

    def test_only_one_of_username_and_password_is_an_error(self):
        for env in ({"ADMIN_USERNAME": "chef"}, {"ADMIN_PASSWORD": "Sehr-gutes-Passwort-7391"}):
            with self.subTest(env=list(env)):
                out, err = self.run_command(**env)
                self.assertFalse(User.objects.exists())
                self.assertIn("zusammen", err)

    def test_ignored_when_a_superuser_exists(self):
        User.objects.create_superuser("alt", "alt@example.com", "Das-alte-Passwort-5512")
        out, err = self.run_command(**self.ENV)
        self.assertEqual(User.objects.count(), 1)
        self.assertIn("ignoriert", out)
        self.assertEqual(err, "")

    def test_second_run_does_not_reset_password(self):
        self.run_command(**self.ENV)
        user = User.objects.get(username="chef")
        user.set_password("Neues-Passwort-in-der-App-2048")
        user.save()
        self.run_command(**self.ENV)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Neues-Passwort-in-der-App-2048"))

    def test_existing_normal_user_is_not_promoted(self):
        User.objects.create_user("Chef", "anderer@example.com", "Etwas-anderes-4410")
        out, err = self.run_command(**self.ENV)
        self.assertFalse(User.objects.get(username="Chef").is_superuser)
        self.assertEqual(User.objects.count(), 1)
        self.assertIn("vergeben", err)

    def test_email_of_another_user_is_rejected(self):
        User.objects.create_user("anna", "CHEF@example.com")
        out, err = self.run_command(**self.ENV)
        self.assertFalse(User.objects.filter(username="chef").exists())
        self.assertIn("ADMIN_EMAIL", err)

    def test_weak_password_is_rejected_without_failing(self):
        for password in ("12345678", "password", "a1b2"):
            with self.subTest(password=password):
                out, err = self.run_command(ADMIN_USERNAME="chef", ADMIN_PASSWORD=password)
                self.assertFalse(User.objects.exists())
                self.assertIn("ungültig", err)
                self.assertNotIn(password, out + err)

    def test_invalid_username_is_rejected(self):
        out, err = self.run_command(ADMIN_USERNAME="mit leerzeichen", ADMIN_PASSWORD="Sehr-gutes-Passwort-7391")
        self.assertFalse(User.objects.exists())
        self.assertIn("ungültig", err)

