"""
WSGI-Konfiguration für config Projekt.

Dieser Datei wird als WSGI-Callable direkt von Django aufgerufen. Für weitere Informationen siehe
https://docs.djangoproject.com/en/5.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()