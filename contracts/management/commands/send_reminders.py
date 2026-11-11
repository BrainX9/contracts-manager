from django.core.management.base import BaseCommand

from contracts import reminders


class Command(BaseCommand):
    help = "Verschickt die heute fälligen Erinnerungen (läuft sonst täglich im Worker)."

    def handle(self, *args, **options):
        result = reminders.send_due_reminders()
        self.stdout.write(str(result))
        if result.skipped:
            self.stdout.write("Übersprungen (kein Recht oder keine E-Mail-Adresse): " + ", ".join(result.skipped))
