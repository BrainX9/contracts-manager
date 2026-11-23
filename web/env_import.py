"""
Einstellungen aus der .env einmalig in die Verwaltung übernehmen
(Version 0.9.0). Läuft nach jeder Migration, also bei jedem Start des
Containers; was schon übernommen wurde, steht in
``SiteSettings.env_imported`` (z. B. "paperless") und wird nie wiederholt.
Danach gilt nur noch die Verwaltung.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def imported_groups(site):
    return {name for name in site.env_imported.split(",") if name}


def mark_imported(site, group):
    groups = imported_groups(site) | {group}
    site.env_imported = ",".join(sorted(groups))


def import_paperless(site):
    """``PAPERLESS_URL`` wird zur Betriebsart "Nur Links" mit dieser Adresse."""
    url = (getattr(settings, "PAPERLESS_URL", "") or "").strip().rstrip("/")
    if url and not site.paperless_url:
        site.paperless_url = url
        if site.paperless_mode == site.PAPERLESS_OFF:
            site.paperless_mode = site.PAPERLESS_LINKS
        logger.warning("PAPERLESS_URL aus der .env in Verwaltung › Paperless übernommen.")
    return bool(url)


IMPORTERS = {"paperless": import_paperless}


def run(sender=None, **kwargs):
    """Alle noch nicht übernommenen Gruppen übernehmen; wiederholbar."""
    from web.models import SiteSettings

    site = SiteSettings.objects.filter(pk=1).first()
    if site is None:
        # Neue Installation ohne alte Variablen: nichts anlegen
        if not getattr(settings, "PAPERLESS_URL", ""):
            return
        site = SiteSettings.load()
    done = imported_groups(site)
    changed = False
    for group, importer in IMPORTERS.items():
        if group in done:
            if group == "paperless" and getattr(settings, "PAPERLESS_URL", ""):
                logger.warning("PAPERLESS_URL wird seit Version 0.9.0 nicht mehr ausgewertet; "
                               "Paperless stellt man unter Verwaltung › Paperless ein.")
            continue
        importer(site)
        mark_imported(site, group)
        changed = True
    if changed:
        site.save()
