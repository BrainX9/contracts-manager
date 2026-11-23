"""
Test-Runner (Version 0.9.0): Tests laufen unabhängig von den früheren
Variablen der .env (KI, E-Mail, SSO, Paperless). Sonst übernähme
``web.env_import`` sie beim Anlegen der Testdatenbank in die Verwaltung,
und die Ergebnisse hingen von der Entwicklungsumgebung ab. Tests, die die
Übernahme prüfen, setzen die Variablen selbst (``patch.dict(os.environ)``).
"""

import os

from django.test.runner import DiscoverRunner


class TestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        from web import env_import

        for name in env_import.VARIABLES:
            os.environ.pop(name, None)
        super().setup_test_environment(**kwargs)
