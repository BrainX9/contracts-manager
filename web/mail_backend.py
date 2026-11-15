"""
E-Mail-Backend mit Einstellungen aus der Verwaltung (Version 0.4.0).

Als ``EMAIL_BACKEND`` eingetragen. Bei jedem Versand gelten die aktuellen
Werte aus ``web.site_config`` (Verwaltung vor .env), ohne Neustart. Ohne
SMTP-Server werden E-Mails wie bisher nur ins Log geschrieben.
"""

from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

from . import site_config


def smtp_backend(config, fail_silently=False):
    """SMTP-Verbindung mit genau diesen Einstellungen."""
    return SMTPBackend(
        host=config.host,
        port=config.port,
        username=config.user,
        password=config.password,
        use_tls=config.security == site_config.SECURITY_STARTTLS,
        use_ssl=config.security == site_config.SECURITY_SSL,
        timeout=config.timeout,
        fail_silently=fail_silently,
    )


class SiteEmailBackend(BaseEmailBackend):
    """
    Reicht an SMTP oder an die Konsole weiter. ``config`` nur für den Test
    ungespeicherter Einstellungen, sonst die gültige Konfiguration.
    """

    def __init__(self, fail_silently=False, config=None, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.config = config or site_config.mail()
        if self.config.smtp:
            self.backend = smtp_backend(self.config, fail_silently)
        else:
            self.backend = ConsoleBackend(fail_silently=fail_silently)

    def open(self):
        return self.backend.open()

    def close(self):
        return self.backend.close()

    def send_messages(self, email_messages):
        return self.backend.send_messages(email_messages)
