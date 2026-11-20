"""
Tests für Version 0.7.0: Status eines Vertrags (Ampel), "Gekündigt zum",
archivierte Verträge ruhen (keine Summen, keine Erinnerungen).
Die Oberfläche ist standardmäßig deutsch.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from contracts import reminders
from contracts.models import Contract, Reminder, total_current_costs

from .tests import make_contract

ARCHIVED = Contract.Status.ARCHIVED


class StatusModelTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("anna", "anna@example.test", "pw-12345-x")

    def test_default_is_active(self):
        contract = make_contract(self.owner, "Strom", "50.00")
        self.assertEqual(contract.status, Contract.Status.ACTIVE)
        self.assertIsNone(contract.cancelled_as_of)
        self.assertEqual(Contract.Status.values, ["active", "notice_sent", "cancelled", "archived"])

    def test_totals_skip_archived_contracts(self):
        power = make_contract(self.owner, "Strom", "50.00")
        gym = make_contract(self.owner, "Fitness", "30.00")
        old = make_contract(self.owner, "Alt", "20.00")
        old.status = ARCHIVED
        totals = total_current_costs([power, gym, old])
        self.assertEqual((totals["monthly"], totals["count"]), (Decimal("80.00"), 2))
        # Gekündigt, aber nicht archiviert: zählt weiter
        gym.status = Contract.Status.CANCELLED
        self.assertEqual(total_current_costs([power, gym, old])["monthly"], Decimal("80.00"))


class StatusPagesTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("anna", "anna@example.test", "pw-12345-x")
        self.client.force_login(self.owner)
        self.active = make_contract(self.owner, "Strom", "50.00")
        self.notice = make_contract(self.owner, "Mobilfunk", "20.00")
        self.notice.status = Contract.Status.NOTICE_SENT
        self.notice.cancelled_as_of = date(2027, 3, 31)
        self.notice.save()
        self.archived = make_contract(self.owner, "Altes Abo", "9.99")
        self.archived.status = ARCHIVED
        self.archived.save()

    def test_dashboard_ignores_archived_contracts(self):
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.context["contract_count"], 2)
        self.assertEqual(page.context["totals"]["monthly"], Decimal("70.00"))
        self.assertNotContains(page, "Altes Abo")

    def test_list_hides_archived_by_default_and_shows_dots(self):
        page = self.client.get(reverse("contract_list"))
        names = [row["contract"].name for row in page.context["rows"]]
        self.assertEqual(sorted(names), ["Mobilfunk", "Strom"])
        self.assertContains(page, 'class="status-dot status-active"')
        self.assertContains(page, 'class="status-dot status-notice_sent"')
        self.assertContains(page, "Kündigung verschickt")
        self.assertContains(page, "Alle außer archivierten")

    def test_status_filter(self):
        url = reverse("contract_list")
        rows = self.client.get(url, {"status": "archived"}).context["rows"]
        self.assertEqual([row["contract"].name for row in rows], ["Altes Abo"])
        page = self.client.get(url, {"status": "all"})
        self.assertEqual(len(page.context["rows"]), 3)
        # Summe ohne archivierte, auch wenn sie angezeigt werden
        self.assertEqual(page.context["totals"]["monthly"], Decimal("70.00"))
        # Unbekannte Werte gelten als Standard
        self.assertEqual(len(self.client.get(url, {"status": "kaputt"}).context["rows"]), 2)

    def test_sort_by_status(self):
        rows = self.client.get(reverse("contract_list"), {"status": "all", "sort": "status"}).context["rows"]
        self.assertEqual([row["contract"].name for row in rows], ["Strom", "Mobilfunk", "Altes Abo"])

    def test_only_archived_hint(self):
        Contract.objects.update(status=ARCHIVED)
        page = self.client.get(reverse("contract_list"))
        self.assertContains(page, "Alle Verträge, die du sehen darfst, sind archiviert.")

    def test_detail_shows_status_and_date(self):
        page = self.client.get(reverse("contract_detail", args=[self.notice.pk]))
        self.assertContains(page, "Kündigung verschickt")
        self.assertContains(page, "Gekündigt zum")
        self.assertContains(page, "31.03.2027")
        page = self.client.get(reverse("contract_detail", args=[self.archived.pk]))
        self.assertContains(page, "Zählt nicht in Summen, keine Erinnerungen.")

    def test_master_data_sums_skip_archived(self):
        provider = self.active.provider
        page = self.client.get(reverse("provider_detail", args=[provider.pk]))
        self.assertEqual(page.context["totals"]["monthly"], Decimal("70.00"))
        self.assertEqual(len(page.context["rows"]), 3)
        rows = self.client.get(reverse("provider_list")).context["rows"]
        self.assertEqual(rows[0]["monthly"], Decimal("70.00"))
        rows = self.client.get(reverse("category_list")).context["rows"]
        self.assertEqual(rows[0]["monthly"], Decimal("70.00"))

    def test_form_saves_status_and_keeps_it_when_missing(self):
        url = reverse("contract_edit", args=[self.active.pk])
        data = {
            "name": "Strom", "provider": self.active.provider_id, "category": self.active.category_id,
            "status": "cancelled", "cancelled_as_of": "2027-01-31",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.active.refresh_from_db()
        self.assertEqual((self.active.status, self.active.cancelled_as_of), ("cancelled", date(2027, 1, 31)))
        del data["status"]
        self.client.post(url, data)
        self.active.refresh_from_db()
        self.assertEqual(self.active.status, "cancelled")


class ArchivedRemindersTest(TestCase):
    def test_no_reminders_for_archived_contracts(self):
        owner = User.objects.create_user("anna", "anna@example.test", "pw-12345-x")
        contract = make_contract(owner, "Kfz", "40.00")
        reminder = Reminder.objects.create(
            contract=contract, enabled=True, remind_on=date(2026, 12, 31), lead_days=3,
            reason=Reminder.Reason.CANCELLATION,
        )
        reminder.recipients.set([owner])
        contract.status = ARCHIVED
        contract.save()
        mail.outbox.clear()
        result = reminders.send_due_reminders(date(2026, 12, 30))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(result.sent, 0)
        # Wieder aktiv: die Erinnerung geht raus
        contract.status = Contract.Status.ACTIVE
        contract.save()
        reminders.send_due_reminders(date(2026, 12, 30))
        self.assertEqual(len(mail.outbox), 1)


class AssistantToolTest(TestCase):
    def test_contract_summary_has_status(self):
        from assistant.tools import _contract_summary

        owner = User.objects.create_user("anna", "anna@example.test", "pw-12345-x")
        contract = make_contract(owner, "Strom", "50.00")
        contract.status, contract.cancelled_as_of = "notice_sent", date(2027, 3, 31)
        summary = _contract_summary(contract)
        self.assertEqual((summary["status"], summary["cancelled_as_of"]), ("notice_sent", "2027-03-31"))
