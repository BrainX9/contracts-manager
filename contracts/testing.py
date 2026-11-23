"""Hilfen für Tests (nur in Tests verwenden)."""

from contextlib import ContextDecorator


class paperless_settings(ContextDecorator):
    """
    Paperless unter Verwaltung › Paperless für die Dauer eines Tests
    einstellen (seit Version 0.9.0 statt PAPERLESS_URL). Leere Adresse =
    ausgeschaltet. Als ``with`` oder als Dekorator nutzbar.
    """

    def __init__(self, url="", mode=None, **fields):
        self.values = {
            "paperless_url": url,
            "paperless_mode": mode or ("links" if url else "off"),
            **{f"paperless_{name}": value for name, value in fields.items()},
        }

    def __enter__(self):
        from web.models import SiteSettings

        site = SiteSettings.load()
        self.previous = {name: getattr(site, name) for name in self.values}
        for name, value in self.values.items():
            setattr(site, name, value)
        site.save()
        return site

    def __exit__(self, *exc):
        from web.models import SiteSettings

        site = SiteSettings.load()
        for name, value in self.previous.items():
            setattr(site, name, value)
        site.save()
        return False
