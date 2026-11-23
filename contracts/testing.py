"""Hilfen für Tests (nur in Tests verwenden)."""

from django.test.utils import TestContextDecorator, override_settings


class site_settings(TestContextDecorator):
    """
    Felder von ``web.SiteSettings`` für die Dauer eines Tests setzen; danach
    gelten wieder die vorherigen Werte. Als ``with``, als Dekorator für
    Methoden und für Testklassen nutzbar (wie ``override_settings``).
    """

    def __init__(self, **fields):
        self.fields = fields
        super().__init__()

    def enable(self):
        from web.models import SiteSettings

        site = SiteSettings.load()
        self.previous = {name: getattr(site, name) for name in self.fields}
        for name, value in self.fields.items():
            setattr(site, name, value)
        site.save()
        return site

    def disable(self):
        from web.models import SiteSettings

        site = SiteSettings.load()
        for name, value in self.previous.items():
            setattr(site, name, value)
        site.save()


class paperless_settings(site_settings):
    """
    Paperless unter Verwaltung › Paperless einstellen (seit Version 0.9.0
    statt PAPERLESS_URL). Leere Adresse = ausgeschaltet.
    """

    def __init__(self, url="", mode=None, **fields):
        super().__init__(
            paperless_url=url,
            paperless_mode=mode or ("links" if url else "off"),
            **{f"paperless_{name}": value for name, value in fields.items()},
        )


class env_settings(TestContextDecorator):
    """
    Frühere Variablen der .env (``AI_*``, ``EMAIL_*``, ``OIDC_*`` …) für einen
    Test: setzt sie wie ``override_settings`` und schreibt dieselben Werte in
    die Verwaltung, so wie es ``web.env_import`` beim Start einmalig tut. Seit
    0.9.0 liest die App diese Einstellungen nur noch aus der Verwaltung.
    """

    def __init__(self, **values):
        self.values = values
        super().__init__()

    def enable(self):
        from web import env_import

        self.override = override_settings(**self.values)
        self.override.enable()
        fields = env_import.site_values(self.values)
        # Ausdrücklich leere Werte leeren auch das Feld
        for group in env_import.FIELDS.values():
            for setting, _variable, field, _convert in group:
                if setting in self.values and field not in fields and self.values[setting] in ("", None):
                    fields[field] = ""
        self.site = site_settings(**fields)
        return self.site.enable()

    def disable(self):
        self.site.disable()
        self.override.disable()
