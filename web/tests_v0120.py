"""
Tests für Version 0.12.0: mehrere Werte je Filter in der Vertragsliste,
Build-Argument gegen alte apt-Schichten aus dem Build-Cache.
"""

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from contracts import backup_upgrades
from contracts import permissions as perms
from contracts.models import (
    Category,
    Consumer,
    Contract,
    CostEntry,
    PaymentMethod,
    Provider,
)

from . import whats_new

ROOT = Path(settings.BASE_DIR)


class MultiFilterTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("anna")
        self.other = User.objects.create_user("bert")
        self.me = Consumer.objects.create(owner=self.owner, name="Ich")
        self.household = Consumer.objects.create(owner=self.owner, name="Haushalt")
        self.partner = Consumer.objects.create(owner=self.owner, name="Partner")
        self.hidden = Consumer.objects.create(owner=self.other, name="Fremd")
        self.provider_a = Provider.objects.create(owner=self.owner, name="Stadtwerke")
        self.provider_b = Provider.objects.create(owner=self.owner, name="Telekom")
        self.insurance = Category.objects.create(owner=self.owner, name="Versicherung")
        self.car = Category.objects.create(owner=self.owner, name="KFZ", parent=self.insurance)
        self.energy = Category.objects.create(owner=self.owner, name="Energie")
        self.media = Category.objects.create(owner=self.owner, name="Medien")
        self.paypal = PaymentMethod.objects.create(owner=self.owner, name="PayPal")
        self.debit = PaymentMethod.objects.create(owner=self.owner, name="Lastschrift")
        self.strom = self.make("Strom", self.me, self.provider_a, self.energy, "50.00", self.debit)
        self.gas = self.make("Gas", self.household, self.provider_a, self.energy, "30.00", self.paypal)
        self.handy = self.make("Handy", self.partner, self.provider_b, self.media, "20.00")
        self.kfz = self.make("KFZ-Versicherung", self.me, self.provider_b, self.car, "40.00", self.paypal)
        # Fremder Vertrag, unsichtbar für anna
        self.make("Geheim", self.hidden, Provider.objects.create(owner=self.other, name="X"),
                  Category.objects.create(owner=self.other, name="Y"), "99.00", owner=self.other)
        self.client.force_login(self.owner)

    def make(self, name, consumer, provider, category, amount, payment_method=None, owner=None):
        contract = Contract.objects.create(
            owner=owner or self.owner, name=name, provider=provider, category=category,
            consumer=consumer, payment_method=payment_method, start_date=timezone.localdate(),
        )
        CostEntry.objects.create(contract=contract, amount=Decimal(amount), valid_from=timezone.localdate())
        return contract

    def names(self, response):
        return {row["contract"].name for row in response.context["rows"]}

    def get(self, params):
        return self.client.get(reverse("contract_list"), params)

    def test_several_consumers_are_combined_with_or(self):
        response = self.get({"consumer": [self.me.pk, self.household.pk]})
        self.assertEqual(self.names(response), {"Strom", "Gas", "KFZ-Versicherung"})
        self.assertEqual(response.context["totals"]["monthly"], Decimal("120.00"))

    def test_filters_are_combined_with_and(self):
        response = self.get({"consumer": [self.me.pk, self.partner.pk], "provider": [self.provider_b.pk]})
        self.assertEqual(self.names(response), {"Handy", "KFZ-Versicherung"})
        response = self.get({"consumer": [self.me.pk], "payment_method": [self.paypal.pk, self.debit.pk],
                             "provider": [self.provider_a.pk]})
        self.assertEqual(self.names(response), {"Strom"})

    def test_several_main_categories_include_subcategories(self):
        response = self.get({"main_category": [self.insurance.pk, self.media.pk]})
        self.assertEqual(self.names(response), {"Handy", "KFZ-Versicherung"})
        response = self.get({"category": [self.car.pk, self.energy.pk]})
        self.assertEqual(self.names(response), {"Strom", "Gas", "KFZ-Versicherung"})

    def test_single_value_links_keep_working(self):
        response = self.client.get(f"{reverse('contract_list')}?consumer={self.household.pk}")
        self.assertEqual(self.names(response), {"Gas"})

    def test_invalid_and_hidden_values(self):
        response = self.get({"consumer": ["abc", "", "-1"]})
        self.assertEqual(response.context["filters"]["consumer"], [])
        self.assertEqual(len(response.context["rows"]), 4)
        # Fremde ID ergibt keine Treffer, verrät aber nichts
        response = self.get({"consumer": [self.hidden.pk]})
        self.assertEqual(self.names(response), set())
        self.assertNotContains(response, "Fremd")

    def test_duplicates_are_ignored(self):
        response = self.get({"consumer": [self.me.pk, self.me.pk]})
        self.assertEqual(response.context["filters"]["consumer"], [self.me.pk])

    def test_options_only_from_visible_contracts(self):
        response = self.get({})
        options = response.context["multi_filters"]["consumer"]["options"]
        self.assertEqual([option["text"] for option in options], ["Haushalt", "Ich", "Partner"])
        self.assertNotContains(response, "Fremd")

    def test_summary(self):
        response = self.get({})
        self.assertEqual(str(response.context["multi_filters"]["consumer"]["summary"]), "Alle Verbraucher")
        response = self.get({"consumer": [self.me.pk]})
        self.assertEqual(response.context["multi_filters"]["consumer"]["summary"], "Ich")
        response = self.get({"consumer": [self.me.pk, self.partner.pk]})
        self.assertEqual(response.context["multi_filters"]["consumer"]["summary"], "2 ausgewählt")

    def test_rendered_page_works_without_javascript(self):
        response = self.get({"consumer": [self.me.pk, self.household.pk]})
        content = response.content.decode()
        self.assertIn('<details class="multi-filter" id="filter-consumer"', content)
        self.assertInHTML(
            f'<input class="form-check-input" type="checkbox" name="consumer" value="{self.me.pk}" '
            f'id="filter-consumer-{self.me.pk}" checked>', content,
        )
        self.assertInHTML(
            f'<input class="form-check-input" type="checkbox" name="consumer" value="{self.partner.pk}" '
            f'id="filter-consumer-{self.partner.pk}">', content,
        )
        for name in ("main_category", "category", "consumer", "payment_method", "provider"):
            self.assertIn(f'name="{name}"', content)
        self.assertContains(response, '<noscript><button type="submit"')

    def test_htmx_returns_only_results(self):
        response = self.get({"consumer": [self.me.pk, self.household.pk]})
        response = self.client.get(
            reverse("contract_list"), {"consumer": [self.me.pk, self.household.pk]}, headers={"HX-Request": "true"},
        )
        self.assertNotContains(response, "multi-filter")
        self.assertContains(response, "Gas")

    def test_sort_link_keeps_all_values(self):
        response = self.get({"consumer": [self.me.pk, self.household.pk]})
        self.assertContains(response, f"consumer={self.me.pk}&amp;consumer={self.household.pk}")

    def test_shared_contracts(self):
        # Freigegebener Vertrag von bert: dessen Verbraucher ist wählbar
        shared = Contract.objects.get(name="Geheim")
        perms.grant_role(shared, self.owner, perms.READER)
        response = self.get({"consumer": [self.hidden.pk, self.partner.pk]})
        self.assertEqual(self.names(response), {"Geheim", "Handy"})


class VersionTest(SimpleTestCase):
    def test_version_entries(self):
        self.assertEqual(settings.APP_VERSION, "0.12.0")
        self.assertEqual(backup_upgrades.CURRENT.version, "0.12.0")
        self.assertIs(backup_upgrades.find("0.11.0").upgrade, backup_upgrades.no_changes)
        self.assertIn("0.12.0", [entry.version for entry in whats_new.ENTRIES])


class AptRefreshTest(SimpleTestCase):
    """Die apt-Schicht darf nicht aus dem Build-Cache kommen (Docker Scout)."""

    def setUp(self):
        workflow = ROOT / ".github" / "workflows" / "docker-image.yml"
        if not workflow.exists():
            self.skipTest("Workflow nicht vorhanden (Image)")
        self.workflow = workflow.read_text()
        self.dockerfile = (ROOT / "Dockerfile").read_text()

    def test_dockerfile_uses_build_arg_before_apt(self):
        arg = self.dockerfile.index("ARG APT_REFRESH")
        self.assertLess(arg, self.dockerfile.index("&& apt-get upgrade -y"))
        self.assertIn("${APT_REFRESH}", self.dockerfile)

    def test_workflow_sets_build_arg_and_pulls(self):
        self.assertEqual(self.workflow.count("build-args: APT_REFRESH=${{ github.run_id }}"), 2)
        self.assertEqual(self.workflow.count("pull: true"), 2)
